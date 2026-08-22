from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.llm.codex_app_server import (
    CodexAppServerConfig,
    CodexAppServerProcessError,
    CodexAppServerProtocolError,
    CodexAppServerRPCError,
    CodexAppServerState,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    build_codex_app_server_environment,
    codex_compatibility_config_overrides,
    detach_shared_codex_app_server,
    get_shared_codex_app_server,
    redact_codex_diagnostic,
    stop_attached_codex_app_server,
)


FAKE_SERVER = r'''
import json
import os
import sys
import threading
import time

lock = threading.Lock()

def send(payload):
    with lock:
        print(json.dumps(payload, ensure_ascii=False), flush=True)

def respond_later(message):
    params = message.get("params") or {}
    time.sleep(float(params.get("delay", 0)))
    send({"id": message["id"], "result": params.get("value")})

log_path = os.environ.get("FAKE_CODEX_LOG")
for line in sys.stdin:
    message = json.loads(line)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(message) + "\n")
    method = message.get("method")
    if "id" not in message:
        continue
    if method == "initialize":
        send({"id": message["id"], "result": {"server": "fake"}})
    elif method == "account/read":
        send({"id": message["id"], "result": {
            "account": {"type": "chatgpt", "plan": "test"},
            "api_key_seen": bool(os.environ.get("OPENAI_API_KEY")),
        }})
    elif method == "echo":
        threading.Thread(target=respond_later, args=(message,), daemon=True).start()
    elif method == "turn/start":
        send({"id": message["id"], "result": {"turn": {"id": "turn-test"}}})
    elif method == "notify":
        params = message.get("params") or {}
        for index in range(int(params.get("count", 1))):
            send({"method": "turn/progress", "params": {"index": index}})
        send({"id": message["id"], "result": True})
    elif method == "rpc_error":
        send({"id": message["id"], "error": {"code": 99, "message": "nope"}})
    elif method == "server_request":
        send({"id": "server-1", "method": message["params"]["method"], "params": {"ok": 1}})
        send({"id": message["id"], "result": True})
    elif method == "hang":
        pass
    elif method == "stderr":
        print(message["params"]["value"], file=sys.stderr, flush=True)
        send({"id": message["id"], "result": True})
    elif method == "crash":
        os._exit(17)
'''


def _config(tmp_path: Path, **kwargs) -> CodexAppServerConfig:
    script = tmp_path / "fake_codex_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "sk-this-must-never-reach-the-child"
    return CodexAppServerConfig(
        command=(sys.executable, "-u", str(script)),
        cwd=str(tmp_path),
        environ=env,
        request_timeout_s=1.0,
        handshake_timeout_s=1.0,
        shutdown_timeout_s=1.0,
        **kwargs,
    )


def test_environment_removes_paid_api_credentials():
    env = build_codex_app_server_environment(
        {
            "PATH": "ok",
            "openai_api_key": "secret",
            "OPENAI_PROJECT": "paid",
        }
    )
    assert env["PATH"] == "ok"
    assert "openai_api_key" not in env
    assert "OPENAI_PROJECT" not in env


def test_compatibility_override_only_replaces_obsolete_service_tier(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config_path = codex_home / "config.toml"
    environment = {"CODEX_HOME": str(codex_home)}

    config_path.write_text('service_tier = "default"\n', encoding="utf-8")
    assert codex_compatibility_config_overrides(environment) == {
        "service_tier": "flex"
    }

    for valid in ("fast", "flex"):
        config_path.write_text(f'service_tier = "{valid}"\n', encoding="utf-8")
        assert codex_compatibility_config_overrides(environment) == {}

    config_path.write_text('service_tier = "future-tier"\n', encoding="utf-8")
    assert codex_compatibility_config_overrides(environment) == {}


def test_from_executable_applies_ephemeral_config_without_editing_files(tmp_path):
    config = CodexAppServerConfig.from_executable(
        "codex.exe",
        cwd=tmp_path,
        config_overrides={"service_tier": "flex"},
    )
    assert config.command == (
        "codex.exe",
        "--config",
        'service_tier="flex"',
        "app-server",
    )
    assert config.cwd == str(tmp_path)


def test_redaction_is_bounded_and_hides_secrets():
    value = 'prefix Bearer abcdefghijklmnop {"access_token":"secret-value"}'
    redacted = redact_codex_diagnostic(value, limit=48)
    assert "abcdefghijklmnop" not in redacted
    assert "secret-value" not in redacted
    assert len(redacted) <= 48


@pytest.mark.asyncio
async def test_lifespan_helper_is_dormant_or_stops_attached_supervisor():
    detach_shared_codex_app_server()
    empty_app = SimpleNamespace(state=SimpleNamespace())
    assert await stop_attached_codex_app_server(empty_app) is False

    supervisor = SimpleNamespace(stop=AsyncMock())
    lease = SimpleNamespace(release=MagicMock())
    app = SimpleNamespace(
        state=SimpleNamespace(
            codex_app_server=supervisor,
            codex_collaboration_leases={"thr:turn": lease},
        )
    )
    assert await stop_attached_codex_app_server(app) is True
    supervisor.stop.assert_awaited_once_with()
    lease.release.assert_called_once_with()
    assert app.state.codex_collaboration_leases == {}
    assert app.state.codex_app_server is None
    assert get_shared_codex_app_server() is None


@pytest.mark.asyncio
async def test_handshake_order_and_account_read(tmp_path):
    log_path = tmp_path / "wire.jsonl"
    config = _config(tmp_path)
    config = CodexAppServerConfig(
        **{**config.__dict__, "environ": {**config.environ, "FAKE_CODEX_LOG": str(log_path)}}
    )
    supervisor = CodexAppServerSupervisor(config)
    try:
        await supervisor.start()
        result = await supervisor.request("account/read")
        assert supervisor.is_running
        assert supervisor.initialized_result == {"server": "fake"}
        assert result["account"]["type"] == "chatgpt"
        assert result["api_key_seen"] is False
        messages = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert [message["method"] for message in messages[:2]] == [
            "initialize",
            "initialized",
        ]
        assert "jsonrpc" not in messages[0]
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(tmp_path):
    supervisor = CodexAppServerSupervisor(_config(tmp_path))
    await supervisor.start()
    pid = supervisor.snapshot().pid
    await supervisor.start()
    assert supervisor.snapshot().pid == pid
    await supervisor.stop()
    await supervisor.stop()
    assert supervisor.state is CodexAppServerState.STOPPED
    assert supervisor.snapshot().pid is None


@pytest.mark.asyncio
async def test_concurrent_requests_are_multiplexed_out_of_order(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        slow = asyncio.create_task(
            supervisor.request("echo", {"value": "slow", "delay": 0.08})
        )
        fast = asyncio.create_task(
            supervisor.request("echo", {"value": "fast", "delay": 0.01})
        )
        done_order = []
        for task in asyncio.as_completed((slow, fast)):
            done_order.append(await task)
        assert done_order == ["fast", "slow"]


@pytest.mark.asyncio
async def test_notifications_can_be_waited_and_consumed(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        waiter = asyncio.create_task(
            supervisor.wait_for_notification(
                "turn/progress",
                predicate=lambda item: item.params["index"] == 1,
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        await supervisor.request("notify", {"count": 2})
        matched = await waiter
        first = await supervisor.next_notification(timeout=1)
        assert matched.params == {"index": 1}
        assert first.params == {"index": 0}


@pytest.mark.asyncio
async def test_notification_queue_is_bounded_and_drops_oldest(tmp_path):
    async with CodexAppServerSupervisor(
        _config(tmp_path, notification_queue_size=2)
    ) as supervisor:
        await supervisor.request("notify", {"count": 5})
        assert supervisor.snapshot().queued_notifications == 2
        assert supervisor.snapshot().dropped_notifications == 3
        assert (await supervisor.next_notification()).params["index"] == 3


@pytest.mark.asyncio
async def test_server_request_handler_and_unknown_method(tmp_path):
    log_path = tmp_path / "wire.jsonl"
    config = _config(tmp_path)
    config = CodexAppServerConfig(
        **{**config.__dict__, "environ": {**config.environ, "FAKE_CODEX_LOG": str(log_path)}}
    )
    async with CodexAppServerSupervisor(config) as supervisor:
        supervisor.register_server_request_handler(
            "approval/request", lambda params: {"approved": params["ok"] == 1}
        )
        await supervisor.request("server_request", {"method": "approval/request"})
        await supervisor.request("server_request", {"method": "unknown/request"})
        await asyncio.sleep(0.05)
    messages = [json.loads(line) for line in log_path.read_text().splitlines()]
    approval = next(item for item in messages if item.get("id") == "server-1" and "result" in item)
    rejection = next(item for item in messages if item.get("id") == "server-1" and "error" in item)
    assert approval["result"] == {"approved": True}
    assert rejection["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_rpc_errors_keep_code_and_data(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        with pytest.raises(CodexAppServerRPCError) as caught:
            await supervisor.request("rpc_error")
        assert caught.value.code == 99
        assert caught.value.message == "nope"


@pytest.mark.asyncio
async def test_request_timeout_cleans_pending_request(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        with pytest.raises(CodexAppServerTimeout):
            await supervisor.request("hang", timeout=0.03)
        assert supervisor.snapshot().pending_requests == 0


@pytest.mark.asyncio
async def test_snapshot_exposes_bounded_request_metrics(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        await supervisor.request("echo", {"value": "ok"})
        await supervisor.request("turn/start", {})
        with pytest.raises(CodexAppServerRPCError):
            await supervisor.request("rpc_error")
        with pytest.raises(CodexAppServerTimeout):
            await supervisor.request("hang", timeout=0.02)
        snapshot = supervisor.snapshot()
        assert snapshot.request_count == 5  # initialize + four explicit requests
        assert snapshot.turn_count == 1
        assert snapshot.request_error_count == 2
        assert snapshot.request_timeout_count == 1
        assert snapshot.last_latency_ms >= 0
        assert snapshot.average_latency_ms >= 0


@pytest.mark.asyncio
async def test_outgoing_message_limit_is_enforced_without_killing_server(tmp_path):
    async with CodexAppServerSupervisor(
        _config(tmp_path, max_message_bytes=1024)
    ) as supervisor:
        with pytest.raises(CodexAppServerProtocolError):
            await supervisor.request("echo", {"value": "x" * 2000})
        assert supervisor.is_running
        assert await supervisor.request("echo", {"value": "still-alive"}) == "still-alive"


@pytest.mark.asyncio
async def test_caller_cancellation_cleans_pending_request(tmp_path):
    async with CodexAppServerSupervisor(_config(tmp_path)) as supervisor:
        task = asyncio.create_task(supervisor.request("hang", timeout=10))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.snapshot().pending_requests == 0


@pytest.mark.asyncio
async def test_crash_fails_pending_and_restarts_only_once(tmp_path):
    supervisor = CodexAppServerSupervisor(_config(tmp_path, max_auto_restarts=1))
    try:
        await supervisor.start()
        with pytest.raises(CodexAppServerProcessError):
            await supervisor.request("crash", timeout=1)
        await supervisor.wait_until_running(timeout=2)
        assert supervisor.snapshot().restart_count == 1
        with pytest.raises(CodexAppServerProcessError):
            await supervisor.request("crash", timeout=1)
        await asyncio.sleep(0.1)
        assert supervisor.state is CodexAppServerState.FAILED
        assert supervisor.snapshot().restart_count == 1
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_stderr_snapshot_is_bounded_and_redacted(tmp_path):
    async with CodexAppServerSupervisor(
        _config(tmp_path, max_stderr_bytes=64)
    ) as supervisor:
        await supervisor.request(
            "stderr", {"value": "Bearer abcdefghijklmnopqrstuvwxyz"}
        )
        await asyncio.sleep(0.03)
        snapshot = supervisor.snapshot()
        assert "abcdefghijklmnopqrstuvwxyz" not in snapshot.stderr_tail
        assert "[REDACTED]" in snapshot.stderr_tail
        assert len(snapshot.stderr_tail.encode()) <= 64
