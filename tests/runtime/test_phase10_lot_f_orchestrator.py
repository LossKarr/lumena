"""Lot F Phase 10 - peer orchestrator V1."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from src.runtime.peer_orchestrator import (
    build_peer_candidates,
    choose_best_peer,
    register_trace_or_raise,
    reset_seen_traces,
)


def _peer(
    instance_id: str,
    *,
    trust: str = "trusted",
    token: str = "tok",
    scopes=None,
    caps=None,
    latency: float = 100.0,
) -> dict:
    return {
        "instance_id": instance_id,
        "instance_name": instance_id,
        "host": "192.168.1.10",
        "port": 8081,
        "trust": trust,
        "peer_token_outbound": token,
        "allowed_scopes": scopes if scopes is not None else ["chat", "task.delegate"],
        "capabilities": caps if caps is not None else ["chat"],
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "last_latency_ms": latency,
    }


@pytest.fixture(autouse=True)
def _clean_traces():
    reset_seen_traces()
    yield
    reset_seen_traces()


class TestPeerOrchestratorRuntime:

    def test_filters_unusable_peers(self):
        peers = {
            "ok": _peer("ok", caps=["browser"]),
            "blocked": _peer("blocked", trust="blocked", caps=["browser"]),
            "no-token": _peer("no-token", token="", caps=["browser"]),
            "no-scope": _peer("no-scope", scopes=["chat"], caps=["browser"]),
            "no-cap": _peer("no-cap", caps=["documents"]),
        }
        candidates = build_peer_candidates(peers, scope="task.delegate", capability="browser")
        assert [c.instance_id for c in candidates] == ["ok"]

    def test_choose_best_peer_by_latency(self):
        peers = {
            "slow": _peer("slow", latency=900),
            "fast": _peer("fast", latency=20),
        }
        chosen = choose_best_peer(peers, scope="chat")
        assert chosen is not None
        assert chosen.instance_id == "fast"

    def test_trace_reuse_refused(self):
        register_trace_or_raise("trace-1", hop_count=0)
        with pytest.raises(ValueError, match="already seen"):
            register_trace_or_raise("trace-1", hop_count=0)

    def test_hop_count_limit_refused(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "2")
        with pytest.raises(ValueError, match="Max peer hop_count"):
            register_trace_or_raise("trace-hop", hop_count=2)


class _FakeResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    calls: list[dict[str, Any]] = []
    responses: list[_FakeResponse] = []

    def __init__(self, timeout: float):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url: str, json: dict = None, headers: dict = None, content=None, **kw):
        if json is None and content is not None:
            import json as _json
            raw = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
            try:
                json = _json.loads(raw)
            except Exception:
                json = None
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
        if self.responses:
            return self.responses.pop(0)
        return _FakeResponse(200, {"status": "completed", "response": "ok"})


def _write_registry(path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


class TestPeerOrchestratorHandler:

    @pytest.mark.asyncio
    async def test_handler_fallbacks_to_second_peer(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        peers = {
            "first": _peer("first", latency=1),
            "second": _peer("second", latency=2),
        }
        _write_registry(reg, peers)
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import src.utils.paths as paths
        monkeypatch.setattr(paths, "INSTANCE_ID", "self-test")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [
            _FakeResponse(500, {"detail": "down"}),
            _FakeResponse(200, {"status": "completed", "response": "fallback ok"}),
        ]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = await mod.orchestrate_peer_request_handler(None, prompt="hello", scope="chat")

        assert result.success is True
        assert "second" in result.output
        assert "fallback ok" in result.output
        assert len(_FakeAsyncClient.calls) == 2

    @pytest.mark.asyncio
    async def test_handler_single_best_does_not_fallback(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        _write_registry(reg, {"first": _peer("first", latency=1), "second": _peer("second", latency=2)})
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import src.utils.paths as paths
        monkeypatch.setattr(paths, "INSTANCE_ID", "self-test")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [_FakeResponse(500, {"detail": "down"})]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = await mod.orchestrate_peer_request_handler(
            None,
            prompt="hello",
            scope="chat",
            strategy="single_best",
        )

        assert result.success is False
        assert len(_FakeAsyncClient.calls) == 1

    @pytest.mark.asyncio
    async def test_handler_multi_best_synthesizes_successful_peers(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        _write_registry(reg, {
            "first": _peer("first", latency=1),
            "second": _peer("second", latency=2),
            "third": _peer("third", latency=3),
        })
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import src.utils.paths as paths
        monkeypatch.setattr(paths, "INSTANCE_ID", "self-test")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [
            _FakeResponse(200, {"status": "completed", "response": "avis first"}),
            _FakeResponse(200, {"status": "completed", "response": "avis second"}),
            _FakeResponse(200, {"status": "completed", "response": "avis third"}),
        ]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = await mod.orchestrate_peer_request_handler(
            None,
            prompt="compare ce plan",
            scope="chat",
            strategy="multi_best",
            max_peers=2,
        )

        assert result.success is True
        assert "Pairs consultes: 2/2" in result.output
        assert "Synthese equipe Lumena" in result.output
        assert "avis first" in result.output
        assert "avis second" in result.output
        assert "avis third" not in result.output
        assert len(_FakeAsyncClient.calls) == 2

    @pytest.mark.asyncio
    async def test_handler_multi_best_keeps_partial_successes(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        _write_registry(reg, {"first": _peer("first", latency=1), "second": _peer("second", latency=2)})
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import src.utils.paths as paths
        monkeypatch.setattr(paths, "INSTANCE_ID", "self-test")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [
            _FakeResponse(500, {"detail": "down"}),
            _FakeResponse(200, {"status": "completed", "response": "second ok"}),
        ]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = await mod.orchestrate_peer_request_handler(
            None,
            prompt="double avis",
            scope="chat",
            strategy="multi_best",
        )

        assert result.success is True
        assert "second ok" in result.output
        assert "Echecs" in result.output
        assert len(_FakeAsyncClient.calls) == 2

    @pytest.mark.asyncio
    async def test_handler_filters_by_capability(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        peers = {
            "docs": _peer("docs", caps=["documents"], latency=1),
            "browser": _peer("browser", caps=["browser"], latency=50),
        }
        _write_registry(reg, peers)
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import src.utils.paths as paths
        monkeypatch.setattr(paths, "INSTANCE_ID", "self-test")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [_FakeResponse(200, {"status": "completed", "response": "browser ok"})]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = await mod.orchestrate_peer_request_handler(
            None,
            prompt="open page",
            scope="chat",
            capability="browser",
        )

        assert result.success is True
        assert "browser" in result.output
        assert _FakeAsyncClient.calls[0]["url"].startswith("http://192.168.1.10:8081")

    @pytest.mark.asyncio
    async def test_handler_refuses_unsupported_v1_scope(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        _write_registry(reg, {"p": _peer("p", scopes=["artifact.share"])})
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        result = await mod.orchestrate_peer_request_handler(
            None,
            prompt="share",
            scope="artifact.share",
        )

        assert result.success is False
        assert "Lot F V1" in result.output

    @pytest.mark.asyncio
    async def test_handler_refuses_repeated_trace(self, tmp_path, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        reg = tmp_path / "peer_registry.json"
        _write_registry(reg, {"p": _peer("p")})
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        import httpx
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [
            _FakeResponse(200, {"status": "completed", "response": "ok"}),
        ]
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        first = await mod.orchestrate_peer_request_handler(None, prompt="x", trace_id="trace-repeat")
        second = await mod.orchestrate_peer_request_handler(None, prompt="x", trace_id="trace-repeat")

        assert first.success is True
        assert second.success is False
        assert "Trace already seen" in second.output

    def test_handler_registered_when_flag_enabled(self, monkeypatch):
        from src.reasoning.handlers.peer_orchestrator import get_peer_orchestrator_handler_defs

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        defs = get_peer_orchestrator_handler_defs()
        assert [d.name for d in defs] == ["peer_team_request", "orchestrate_peer_request"]
