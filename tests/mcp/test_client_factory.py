"""
Tests Phase 19.5 v2 — client_factory.create_mcp_client_from_runner.

Sections :
  1. Factory happy path
  2. Factory refuse (codes whitelist)
  3. Factory defensive poll()
  4. Factory wrap MCPClientError → ClientFactoryError
  5. Factory does not mutate runner
  6. Factory no direct subprocess.Popen
  7. Factory integration avec vrai MCPSandboxRunner mock
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest import mock

import pytest

from src.mcp.client import MCPClient, MCPClientError
from src.mcp.client_factory import (
    ClientFactoryError,
    create_mcp_client_from_runner,
)
from src.mcp.sandbox_runner import (
    MCPInstallSpec,
    MCPSandboxRunner,
    ProcessState,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fakes / helpers
# ──────────────────────────────────────────────────────────────────────────────


class _FakeSpec:
    def __init__(self, name: str):
        self.name = name


class _FakePopen:
    """Popen-like minimal : stdin/stdout/stderr PIPE + poll()."""

    def __init__(
        self,
        *,
        stdin: Any = "PIPE",
        stdout: Any = "PIPE",
        stderr: Any = "PIPE",
        poll_value: Optional[int] = None,
        poll_raises: bool = False,
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._poll_value = poll_value
        self._poll_raises = poll_raises
        self.poll_calls = 0
        # Tracking : ces méthodes ne doivent JAMAIS être appelées par la factory
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> Optional[int]:
        self.poll_calls += 1
        if self._poll_raises:
            raise RuntimeError("POLL_FAIL")
        return self._poll_value

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        self.wait_calls += 1


class _FakeRunner:
    """Runner fake exposant .process (property-like) et .spec."""

    def __init__(
        self,
        *,
        popen: Optional[_FakePopen] = None,
        spec_name: Optional[str] = "alice",
        no_spec: bool = False,
        no_process_property: bool = False,
    ):
        self._popen = popen
        if no_spec:
            self.spec = None
        else:
            self.spec = _FakeSpec(spec_name) if spec_name is not None else _FakeSpec("")
        self._no_process_property = no_process_property
        # Tracking : ne doivent JAMAIS être appelés par la factory
        self.start_calls = 0
        self.stop_calls = 0
        self.stdout_reads = 0

    @property
    def process(self):
        if self._no_process_property:
            raise AttributeError("No process attribute")
        return self._popen

    def start(self, runtime_env_secrets=None):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


class _RunnerWithoutProcessAttr:
    """Runner sans propriété process du tout."""

    def __init__(self):
        self.spec = _FakeSpec("alice")


def _make_alive_popen() -> _FakePopen:
    return _FakePopen()


def _make_dead_popen(exit_code: int = 0) -> _FakePopen:
    return _FakePopen(poll_value=exit_code)


# ──────────────────────────────────────────────────────────────────────────────
# Section 1 — Factory happy path
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryHappyPath:
    def test_returns_mcp_client(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        client = create_mcp_client_from_runner(runner)
        assert isinstance(client, MCPClient)

    def test_passes_process_to_client(self):
        popen = _make_alive_popen()
        runner = _FakeRunner(popen=popen, spec_name="alice")
        client = create_mcp_client_from_runner(runner)
        # MCPClient garde la référence sur _process
        assert client._process is popen

    def test_passes_server_name_to_client(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="my-server")
        client = create_mcp_client_from_runner(runner)
        assert client._server_name == "my-server"

    def test_default_timeout_passed(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        client = create_mcp_client_from_runner(runner)
        assert client._default_timeout_s == 30.0

    def test_custom_timeout_passed(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        client = create_mcp_client_from_runner(runner, default_timeout_s=60.0)
        assert client._default_timeout_s == 60.0

    def test_client_not_initialized(self):
        """Factory ne doit pas appeler client.initialize()."""
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        client = create_mcp_client_from_runner(runner)
        assert client.is_initialized is False


# ──────────────────────────────────────────────────────────────────────────────
# Section 2 — Factory refuse (codes whitelist)
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryRefuses:
    def test_runner_none_raises_invalid_none(self):
        with pytest.raises(ClientFactoryError, match="runner_invalid:none"):
            create_mcp_client_from_runner(None)

    def test_runner_without_process_property_raises(self):
        runner = _RunnerWithoutProcessAttr()
        with pytest.raises(ClientFactoryError, match="no_process_property"):
            create_mcp_client_from_runner(runner)

    def test_runner_with_process_none_raises_not_started(self):
        runner = _FakeRunner(popen=None, spec_name="alice")
        with pytest.raises(ClientFactoryError, match="runner_not_started"):
            create_mcp_client_from_runner(runner)

    def test_runner_without_spec_raises_no_spec(self):
        runner = _FakeRunner(popen=_make_alive_popen(), no_spec=True)
        with pytest.raises(ClientFactoryError, match="no_spec"):
            create_mcp_client_from_runner(runner)

    def test_runner_spec_name_empty_raises(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="")
        with pytest.raises(ClientFactoryError, match="no_server_name"):
            create_mcp_client_from_runner(runner)

    def test_runner_spec_name_not_str_raises(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        runner.spec.name = 123  # type: ignore
        with pytest.raises(ClientFactoryError, match="no_server_name"):
            create_mcp_client_from_runner(runner)

    def test_runner_spec_name_none_raises(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        runner.spec.name = None  # type: ignore
        with pytest.raises(ClientFactoryError, match="no_server_name"):
            create_mcp_client_from_runner(runner)


# ──────────────────────────────────────────────────────────────────────────────
# Section 3 — Factory defensive poll()
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryDefensivePoll:
    def test_factory_rejects_exited_process(self):
        runner = _FakeRunner(popen=_make_dead_popen(exit_code=0), spec_name="alice")
        with pytest.raises(ClientFactoryError, match="runner_not_alive"):
            create_mcp_client_from_runner(runner)

    def test_factory_rejects_nonzero_exit_process(self):
        runner = _FakeRunner(popen=_make_dead_popen(exit_code=42), spec_name="alice")
        with pytest.raises(ClientFactoryError, match="runner_not_alive"):
            create_mcp_client_from_runner(runner)

    def test_factory_poll_raises_wrapped_as_not_alive(self):
        popen = _FakePopen(poll_raises=True)
        runner = _FakeRunner(popen=popen, spec_name="alice")
        with pytest.raises(ClientFactoryError, match="runner_not_alive"):
            create_mcp_client_from_runner(runner)


# ──────────────────────────────────────────────────────────────────────────────
# Section 4 — Factory wrap MCPClientError → ClientFactoryError
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryWrapsMCPClientError:
    def test_factory_wraps_mcpclient_error_for_missing_stderr(self):
        """stderr=None → MCPClient raise MCPClientError → factory wrap."""
        popen = _FakePopen(stderr=None)
        runner = _FakeRunner(popen=popen, spec_name="alice")
        with pytest.raises(ClientFactoryError, match="client_create_failed") as exc_info:
            create_mcp_client_from_runner(runner)
        # __cause__ préservé
        assert isinstance(exc_info.value.__cause__, MCPClientError)

    def test_factory_wraps_mcpclient_error_for_missing_stdout(self):
        popen = _FakePopen(stdout=None)
        runner = _FakeRunner(popen=popen, spec_name="alice")
        with pytest.raises(ClientFactoryError, match="client_create_failed") as exc_info:
            create_mcp_client_from_runner(runner)
        assert isinstance(exc_info.value.__cause__, MCPClientError)

    def test_factory_wraps_mcpclient_error_for_missing_stdin(self):
        popen = _FakePopen(stdin=None)
        runner = _FakeRunner(popen=popen, spec_name="alice")
        with pytest.raises(ClientFactoryError, match="client_create_failed") as exc_info:
            create_mcp_client_from_runner(runner)
        assert isinstance(exc_info.value.__cause__, MCPClientError)


# ──────────────────────────────────────────────────────────────────────────────
# Section 5 — Factory does not mutate runner
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryDoesNotMutateRunner:
    def test_factory_does_not_call_runner_start(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        create_mcp_client_from_runner(runner)
        assert runner.start_calls == 0

    def test_factory_does_not_call_runner_stop(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        create_mcp_client_from_runner(runner)
        assert runner.stop_calls == 0

    def test_factory_does_not_call_popen_terminate(self):
        popen = _make_alive_popen()
        runner = _FakeRunner(popen=popen, spec_name="alice")
        create_mcp_client_from_runner(runner)
        assert popen.terminate_calls == 0

    def test_factory_does_not_call_popen_kill(self):
        popen = _make_alive_popen()
        runner = _FakeRunner(popen=popen, spec_name="alice")
        create_mcp_client_from_runner(runner)
        assert popen.kill_calls == 0

    def test_factory_does_not_call_popen_wait(self):
        popen = _make_alive_popen()
        runner = _FakeRunner(popen=popen, spec_name="alice")
        create_mcp_client_from_runner(runner)
        assert popen.wait_calls == 0


# ──────────────────────────────────────────────────────────────────────────────
# Section 6 — Factory no direct subprocess.Popen
# ──────────────────────────────────────────────────────────────────────────────


class TestFactoryNoDirectSubprocess:
    def test_no_subprocess_popen_in_client_factory_module(self):
        """Grep statique : aucun appel direct subprocess.Popen ou Popen()."""
        source = (
            Path(__file__).resolve().parent.parent.parent
            / "src" / "mcp" / "client_factory.py"
        )
        text = source.read_text(encoding="utf-8")
        assert "subprocess.Popen" not in text
        # Refuse aussi un éventuel Popen importé directement
        assert "from subprocess import Popen" not in text

    def test_e2e_no_subprocess_popen_during_call(self):
        runner = _FakeRunner(popen=_make_alive_popen(), spec_name="alice")
        with mock.patch("subprocess.Popen") as popen_patch:
            create_mcp_client_from_runner(runner)
            assert popen_patch.call_count == 0


# ──────────────────────────────────────────────────────────────────────────────
# Section 7 — Factory integration avec vrai MCPSandboxRunner
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def real_npm_spec() -> MCPInstallSpec:
    return MCPInstallSpec(
        name="brave-search",
        transport="npm",
        package="@modelcontextprotocol/server-brave-search",
        env_keys_allowlist=["BRAVE_API_KEY"],
        package_version="0.1.0",
        args=["./node_modules/.bin/brave-search-stub"],
    )


@pytest.fixture
def real_runner(tmp_path: Path, real_npm_spec: MCPInstallSpec) -> MCPSandboxRunner:
    mcp_root = tmp_path / "mcp_root"
    logs_dir = tmp_path / "logs"
    return MCPSandboxRunner(
        spec=real_npm_spec,
        mcp_root=mcp_root,
        logs_dir=logs_dir,
        stdout_mode="capture",
    )


class TestFactoryWithRealRunner:
    def test_factory_rejects_real_runner_before_start(self, real_runner):
        """Runner réel non démarré → process is None → runner_not_started."""
        # State = NOT_INSTALLED ; process property doit retourner None
        with pytest.raises(ClientFactoryError, match="runner_not_started"):
            create_mcp_client_from_runner(real_runner)

    def test_factory_rejects_real_runner_after_force_stopped(self, real_runner):
        """État STOPPED → process is None → runner_not_started."""
        # Forcer un état STOPPED côté state machine (sans subprocess réel)
        with real_runner._state_lock:
            real_runner._state = ProcessState.STOPPED
            real_runner._process = None
        with pytest.raises(ClientFactoryError, match="runner_not_started"):
            create_mcp_client_from_runner(real_runner)

    def test_factory_works_with_real_runner_in_running_state(self, real_runner):
        """Runner réel forcé en état RUNNING avec Popen mocké → factory OK."""
        popen = _make_alive_popen()
        with real_runner._state_lock:
            real_runner._state = ProcessState.RUNNING
            real_runner._process = popen  # type: ignore
        client = create_mcp_client_from_runner(real_runner)
        assert isinstance(client, MCPClient)
        assert client._process is popen
        assert client._server_name == "brave-search"

    def test_factory_uses_property_not_underscore_process(self, real_runner):
        """Vérifie que la factory utilise runner.process (pas runner._process)."""
        popen = _make_alive_popen()
        with real_runner._state_lock:
            real_runner._state = ProcessState.RUNNING
            real_runner._process = popen  # type: ignore
        # Spy sur la property pour vérifier l'accès
        access_count = {"v": 0}
        original_get = type(real_runner).process.fget

        def spy_get(self):
            access_count["v"] += 1
            return original_get(self)

        # Hack : on remplace temporairement la property
        with mock.patch.object(
            type(real_runner), "process",
            property(spy_get),
        ):
            create_mcp_client_from_runner(real_runner)
        assert access_count["v"] >= 1
