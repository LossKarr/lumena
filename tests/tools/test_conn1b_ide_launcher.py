from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import pytest

from src.tools.ide_discovery import (
    IDEDiscoveryReport,
    IDEInstallation,
)


class _Discovery:
    def __init__(self, installation: IDEInstallation | None) -> None:
        self.installation = installation
        self.calls = 0

    def discover(self) -> IDEDiscoveryReport:
        self.calls += 1
        return IDEDiscoveryReport(self.installation, ())


class _Process:
    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class _Starter:
    def __init__(self, process: _Process | None = None) -> None:
        self.process = process or _Process()
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def __call__(self, command: tuple[str, ...], cwd: Path, environment: dict[str, str]) -> _Process:
        self.calls.append((command, cwd, environment))
        return self.process


class _Probe:
    def __init__(self, *states) -> None:
        self.states = deque(states)
        self.last = states[-1]
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.states:
            self.last = self.states.popleft()
        return self.last


def _installation(root: Path, *, mode: str = "packaged") -> IDEInstallation:
    executable = root / "Lumena IDE.exe" if mode == "packaged" else None
    command = (str(executable),) if executable else ("npm.cmd", "run", "electron:dev", "--")
    return IDEInstallation(
        mode=mode,
        source="test",
        root=root,
        manifest_path=root / "lumena-extension.json",
        version="1.0.0",
        platform="windows-x64",
        executable=executable,
        command=command,
        manifest_sha256="a" * 64,
        artifact_sha256="b" * 64,
    )


def _readiness(
    *,
    connected: bool = False,
    handshake: bool = False,
    authenticated: bool = False,
    workspace: Path | None = None,
):
    from src.tools.ide_launcher import IDEReadiness

    return IDEReadiness(
        transport_connected=connected,
        handshake_received=handshake,
        authenticated=authenticated,
        workspace=workspace,
    )


@pytest.mark.asyncio
async def test_launch_waits_for_handshake_and_never_equates_popen_with_ready(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    workspace = tmp_path / "project"
    workspace.mkdir()
    install = _installation(tmp_path / "ide")
    starter = _Starter()
    probe = _Probe(
        _readiness(),
        _readiness(connected=True),
        _readiness(connected=True, handshake=True, workspace=workspace),
    )
    launcher = IDELauncherService(
        discovery=_Discovery(install),
        readiness_probe=probe,
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready(workspace)

    assert result.available is True
    assert result.ready is False
    assert result.state == "transport_ready"
    assert result.process_started is True
    assert result.handshake_received is True
    assert result.authenticated is False
    assert len(starter.calls) == 1
    command, cwd, environment = starter.calls[0]
    assert command == (str(install.executable), f"--workspace={workspace.resolve()}")
    assert cwd == install.root
    assert environment["LUMENA_IDE_WORKSPACE"] == str(workspace.resolve())
    assert probe.calls == 3


@pytest.mark.asyncio
async def test_authenticated_handshake_is_the_only_final_ready_state(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    workspace = tmp_path / "project"
    workspace.mkdir()
    starter = _Starter()
    launcher = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(
            _readiness(),
            _readiness(connected=True, handshake=True, authenticated=True, workspace=workspace),
        ),
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready(workspace)

    assert result.state == "authenticated_ready"
    assert result.available is True
    assert result.ready is True
    assert result.authenticated is True


@pytest.mark.asyncio
async def test_two_concurrent_requests_start_only_one_process(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    workspace = tmp_path / "project"
    workspace.mkdir()
    starter = _Starter()
    probe = _Probe(
        _readiness(),
        _readiness(),
        _readiness(connected=True, handshake=True, workspace=workspace),
    )
    launcher = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=probe,
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    first, second = await asyncio.gather(
        launcher.ensure_ready(workspace),
        launcher.ensure_ready(workspace),
    )

    assert len(starter.calls) == 1
    assert first.available and second.available
    assert first.process_started is True
    assert second.process_started is False
    assert second.reused is True


@pytest.mark.asyncio
async def test_connected_instance_reuses_transport_and_switches_workspace(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    old_workspace = tmp_path / "old"
    new_workspace = tmp_path / "new"
    old_workspace.mkdir()
    new_workspace.mkdir()
    starter = _Starter()
    routed: list[Path] = []

    async def route(path: Path) -> bool:
        routed.append(path)
        return True

    launcher = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(
            _readiness(connected=True, handshake=True, workspace=old_workspace),
            _readiness(connected=True, handshake=True, workspace=new_workspace),
        ),
        workspace_router=route,
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready(new_workspace)

    assert result.available is True
    assert result.reused is True
    assert result.process_started is False
    assert routed == [new_workspace.resolve()]
    assert starter.calls == []


@pytest.mark.asyncio
async def test_invalid_workspace_fails_before_discovery_or_process_start(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    discovery = _Discovery(_installation(tmp_path / "ide"))
    starter = _Starter()
    launcher = IDELauncherService(
        discovery=discovery,
        readiness_probe=_Probe(_readiness()),
        process_starter=starter,
    )

    result = await launcher.ensure_ready(tmp_path / "missing")

    assert result.state == "invalid_workspace"
    assert result.available is False
    assert discovery.calls == 0
    assert starter.calls == []


@pytest.mark.asyncio
async def test_missing_runtime_is_explicit_and_never_starts_a_process(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    starter = _Starter()
    launcher = IDELauncherService(
        discovery=_Discovery(None),
        readiness_probe=_Probe(_readiness()),
        process_starter=starter,
    )

    result = await launcher.ensure_ready()

    assert result.state == "unavailable"
    assert result.available is False
    assert "runtime" in result.error.lower()
    assert starter.calls == []


@pytest.mark.asyncio
async def test_process_crash_before_handshake_fails_closed(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    launcher = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(_readiness()),
        process_starter=_Starter(_Process(exit_code=17)),
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready()

    assert result.state == "crashed"
    assert result.available is False
    assert result.exit_code == 17
    assert result.handshake_received is False


@pytest.mark.asyncio
async def test_timeout_after_process_start_is_not_success(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    now = iter((0.0, 0.0, 2.0))
    launcher = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(_readiness()),
        process_starter=_Starter(),
        monotonic=lambda: next(now),
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready()

    assert result.state == "timeout"
    assert result.process_started is True
    assert result.available is False
    assert result.ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_wrong_workspace_never_becomes_success_even_on_authenticated_transport(
    tmp_path: Path, accepted: bool,
) -> None:
    from src.tools.ide_launcher import IDELauncherService

    old = tmp_path / "old"
    requested = tmp_path / "requested"
    old.mkdir()
    requested.mkdir()

    async def route(_workspace: Path) -> bool:
        return accepted

    now = iter((0.0, 0.0, 2.0))
    launcher = IDELauncherService(
        discovery=_Discovery(None),
        readiness_probe=_Probe(_readiness(
            connected=True, handshake=True, authenticated=True, workspace=old,
        )),
        workspace_router=route,
        monotonic=lambda: next(now),
        timeout=1,
    )

    result = await launcher.ensure_ready(requested)

    assert result.state == ("timeout" if accepted else "workspace_rejected")
    assert result.workspace == old
    assert result.transport_connected and result.handshake_received and result.authenticated
    assert result.available is False
    assert result.ready is False


@pytest.mark.asyncio
async def test_launch_deadline_includes_a_stalled_bridge_probe() -> None:
    from src.tools.ide_launcher import IDELauncherService

    cancelled = asyncio.Event()

    async def stalled_probe():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    launcher = IDELauncherService(
        discovery=_Discovery(None), readiness_probe=stalled_probe, timeout=0.02,
    )
    result = await asyncio.wait_for(launcher.ensure_ready(), timeout=0.5)
    assert result.state == "timeout"
    assert not result.available
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_converted_to_launch_failure() -> None:
    from src.tools.ide_launcher import IDELauncherService

    entered = asyncio.Event()

    async def stalled_probe():
        entered.set()
        await asyncio.Event().wait()

    launcher = IDELauncherService(discovery=_Discovery(None), readiness_probe=stalled_probe)
    task = asyncio.create_task(launcher.ensure_ready())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_source_runtime_uses_complete_start_script_and_workspace_environment(tmp_path: Path) -> None:
    from src.tools.ide_launcher import IDELauncherService

    workspace = tmp_path / "project"
    workspace.mkdir()
    install = _installation(tmp_path / "ide", mode="source")
    install = IDEInstallation(
        **{
            **install.__dict__,
            "command": ("npm.cmd", "run", "start", "--"),
        }
    )
    starter = _Starter()
    launcher = IDELauncherService(
        discovery=_Discovery(install),
        readiness_probe=_Probe(
            _readiness(),
            _readiness(connected=True, handshake=True, workspace=workspace),
        ),
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )

    result = await launcher.ensure_ready(workspace)

    assert result.available is True
    command, _cwd, environment = starter.calls[0]
    assert command == ("npm.cmd", "run", "start", "--")
    assert environment["LUMENA_IDE_WORKSPACE"] == str(workspace.resolve())


def test_launcher_owner_stays_outside_react_and_handlers_do_not_spawn() -> None:
    launcher = Path("src/tools/ide_launcher.py").read_text(encoding="utf-8")
    handler = Path("src/reasoning/handlers/ide.py").read_text(encoding="utf-8")
    compatibility_handler = Path("src/reasoning/handlers/computer_use.py").read_text(encoding="utf-8")
    react = Path("src/reasoning/react.py").read_text(encoding="utf-8")

    assert "class IDELauncherService" in launcher
    assert "reasoning.react" not in launcher
    assert "IDELauncherService" not in react
    assert "subprocess.Popen" not in handler
    assert "get_ide_launcher" in handler
    assert "def _launch_cursor_ide_process" not in compatibility_handler
    assert "_get_cursor_ide_launcher" in compatibility_handler


@pytest.mark.asyncio
async def test_legacy_cursor_tool_delegates_opening_to_the_single_launcher(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.reasoning.handlers import computer_use
    from src.tools.ide_launcher import IDELaunchResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class _Launcher:
        def __init__(self) -> None:
            self.calls: list[Path] = []

        async def ensure_ready(self, value: Path, *, dedicated: bool = False) -> IDELaunchResult:
            self.calls.append(value)
            return IDELaunchResult(
                state="transport_ready",
                transport_connected=True,
                handshake_received=True,
                workspace=value,
            )

    launcher = _Launcher()
    monkeypatch.setattr(computer_use, "_get_cursor_ide_launcher", lambda: launcher)

    class _Context:
        runtime_root = workspace
        lumena_root = tmp_path

    result = await computer_use.lumena_ide(
        _Context(),
        action="ensure_workspace",
        workspace_path=str(workspace),
        create_if_missing=False,
    )

    assert result.success is True
    assert launcher.calls == [workspace.resolve()]
    assert "handshake" in result.output.lower()


@pytest.mark.asyncio
async def test_primary_ide_launch_facade_reports_observed_transport_not_popen(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.reasoning.handlers import ide
    from src.tools.ide_launcher import IDELaunchResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class _Launcher:
        async def ensure_ready(self, value: str, *, dedicated: bool = False) -> IDELaunchResult:
            assert Path(value) == workspace
            return IDELaunchResult(
                state="transport_ready",
                process_started=True,
                transport_connected=True,
                handshake_received=True,
                workspace=workspace,
            )

    monkeypatch.setattr(ide, "_get_launcher", lambda: _Launcher())

    result = await ide._handle_ide_launch(None, workspace=str(workspace))

    assert result.success is True
    assert "handshake confirme" in result.output
    assert "non authentifie" in result.output


@pytest.mark.asyncio
async def test_primary_ide_launch_facade_propagates_timeout_as_failure(monkeypatch) -> None:
    from src.reasoning.handlers import ide
    from src.tools.ide_launcher import IDELaunchResult

    class _Launcher:
        async def ensure_ready(self, _value, *, dedicated: bool = False) -> IDELaunchResult:
            return IDELaunchResult(
                state="timeout",
                process_started=True,
                error="handshake timeout",
            )

    monkeypatch.setattr(ide, "_get_launcher", lambda: _Launcher())

    result = await ide._handle_ide_launch(None)

    assert result.success is False
    assert result.error == "handshake timeout"
