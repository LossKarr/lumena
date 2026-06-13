"""
Tests MCPSandboxRunner (Phase 5).

Stratégie :
  - Tests par défaut MOCKENT subprocess.Popen / subprocess.run (offline, déterministe)
  - Tests live (vrais npm/uvx) marqués @pytest.mark.skipif → opt-in
  - Toujours via tmp_path / monkeypatch LUMENA_MCP_ROOT pour isolation
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.mcp.sandbox_runner import (
    MCPInstallSpec,
    MCPSandboxError,
    MCPSandboxRunner,
    ProcessState,
    _system_env_allowlist,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mcp_root(tmp_path) -> Path:
    return tmp_path / "mcp"


@pytest.fixture
def logs_dir(tmp_path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def npm_spec() -> MCPInstallSpec:
    """Spec npm par défaut AVEC args explicites (pas de fallback npx).

    Pour tester le refus de start sans args, utiliser npm_spec_no_args.
    """
    return MCPInstallSpec(
        name="brave-search",
        transport="npm",
        package="@modelcontextprotocol/server-brave-search",
        env_keys_allowlist=["BRAVE_API_KEY"],
        package_version="0.1.0",
        args=["./node_modules/.bin/brave-search-stub"],
    )


@pytest.fixture
def npm_spec_no_args() -> MCPInstallSpec:
    """Spec npm SANS args : utilisée pour vérifier le refus de fallback npx."""
    return MCPInstallSpec(
        name="brave-search",
        transport="npm",
        package="@modelcontextprotocol/server-brave-search",
        env_keys_allowlist=["BRAVE_API_KEY"],
        package_version="0.1.0",
    )


@pytest.fixture
def npm_runner_no_args(npm_spec_no_args, mcp_root, logs_dir) -> MCPSandboxRunner:
    # Phase 5 legacy → stdout_mode="capture" pour rétrocompat tests existants
    return MCPSandboxRunner(
        spec=npm_spec_no_args, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="capture",
    )


@pytest.fixture
def uv_spec() -> MCPInstallSpec:
    return MCPInstallSpec(
        name="mcp-pyserver",
        transport="uv",
        package="mcp-server-example",
        env_keys_allowlist=["EXAMPLE_API_KEY"],
        require_wheels_only=True,
    )


@pytest.fixture
def npm_runner(npm_spec, mcp_root, logs_dir) -> MCPSandboxRunner:
    # Phase 5 legacy → stdout_mode="capture" pour rétrocompat tests existants
    # qui s'attendent à ce que stdout soit lu par le runner.
    return MCPSandboxRunner(
        spec=npm_spec,
        mcp_root=mcp_root,
        logs_dir=logs_dir,
        stdout_mode="capture",
    )


@pytest.fixture
def uv_runner(uv_spec, mcp_root, logs_dir) -> MCPSandboxRunner:
    # Idem rétrocompat
    return MCPSandboxRunner(
        spec=uv_spec,
        mcp_root=mcp_root,
        logs_dir=logs_dir,
        stdout_mode="capture",
    )


def _make_success_run(stdout: str = "", stderr: str = ""):
    """Crée un MagicMock subprocess.CompletedProcess(returncode=0)."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = stdout
    result.stderr = stderr
    return result


def _make_fake_popen(
    stdout_lines: Optional[List[str]] = None,
    stderr_lines: Optional[List[str]] = None,
    returncode: int = 0,
):
    """Construit un MagicMock subprocess.Popen jouant des flux stdout/stderr.

    Phase 5.1 : stdout et stderr sont SÉPARÉS. Le runner attend les 2.
    Par défaut stderr retourne EOF immédiatement (vide).
    """
    stdout_lines = stdout_lines or []
    stderr_lines = stderr_lines or []

    fake_proc = MagicMock()
    fake_proc.pid = 12345

    # stdout readline
    out_iter = iter(stdout_lines + [""])

    def _stdout_readline():
        try:
            return next(out_iter)
        except StopIteration:
            return ""

    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline = _stdout_readline

    # stderr readline (séparé)
    err_iter = iter(stderr_lines + [""])

    def _stderr_readline():
        try:
            return next(err_iter)
        except StopIteration:
            return ""

    fake_proc.stderr = MagicMock()
    fake_proc.stderr.readline = _stderr_readline

    fake_proc.returncode = returncode

    poll_count = {"n": 0}

    def _poll():
        # Retourne None tant que le monitor n'a pas terminé
        poll_count["n"] += 1
        if poll_count["n"] < 3:
            return None
        return returncode

    fake_proc.poll = _poll
    fake_proc.wait = MagicMock(return_value=returncode)
    fake_proc.terminate = MagicMock()
    fake_proc.kill = MagicMock()
    return fake_proc


# ──────────────────────────────────────────────────────────────────────────────
# Validation spec / construction
# ──────────────────────────────────────────────────────────────────────────────


def test_invalid_name_rejected(mcp_root, logs_dir):
    bad = MCPInstallSpec(name="", transport="npm", package="x")
    with pytest.raises(MCPSandboxError):
        MCPSandboxRunner(spec=bad, mcp_root=mcp_root, logs_dir=logs_dir)


def test_name_with_path_traversal_rejected(mcp_root, logs_dir):
    bad = MCPInstallSpec(name="../etc/passwd", transport="npm", package="x")
    with pytest.raises(MCPSandboxError):
        MCPSandboxRunner(spec=bad, mcp_root=mcp_root, logs_dir=logs_dir)


def test_name_too_long_rejected(mcp_root, logs_dir):
    bad = MCPInstallSpec(name="a" * 100, transport="npm", package="x")
    with pytest.raises(MCPSandboxError):
        MCPSandboxRunner(spec=bad, mcp_root=mcp_root, logs_dir=logs_dir)


def test_unknown_transport_rejected(mcp_root, logs_dir):
    bad = MCPInstallSpec(name="ok", transport="apt", package="x")  # type: ignore
    with pytest.raises(MCPSandboxError):
        MCPSandboxRunner(spec=bad, mcp_root=mcp_root, logs_dir=logs_dir)


def test_initial_state_not_installed(npm_runner):
    assert npm_runner.state() == ProcessState.NOT_INSTALLED
    assert npm_runner.is_installed() is False


# ──────────────────────────────────────────────────────────────────────────────
# Env allowlist
# ──────────────────────────────────────────────────────────────────────────────


def test_env_allowlist_posix_contains_home_path():
    if sys.platform == "win32":
        pytest.skip("POSIX-only")
    allowlist = _system_env_allowlist()
    assert "HOME" in allowlist
    assert "PATH" in allowlist


def test_env_allowlist_windows_contains_required_vars():
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    allowlist = _system_env_allowlist()
    assert "USERPROFILE" in allowlist
    assert "PATH" in allowlist
    assert "TEMP" in allowlist
    assert "APPDATA" in allowlist
    assert "LOCALAPPDATA" in allowlist


def test_install_env_has_no_secrets(npm_runner, monkeypatch):
    """L'env d'install ne doit jamais contenir un secret connu."""
    # Pollue l'env du test avec un faux secret
    monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_secret")
    monkeypatch.setenv("MY_CUSTOM_SECRET", "leak_me_please")

    install_env = npm_runner._build_install_env()
    assert "OPENAI_API_KEY" not in install_env
    assert "STRIPE_API_KEY" not in install_env
    assert "MY_CUSTOM_SECRET" not in install_env
    # Le PATH système doit être préservé pour faire fonctionner npm/uv
    assert "PATH" in install_env


def test_install_env_redirects_caches_to_isolated_dir(npm_runner):
    env = npm_runner._build_install_env()
    assert "NPM_CONFIG_CACHE" in env
    assert "UV_CACHE_DIR" in env
    # Les caches doivent être sous le dossier serveur
    server_dir_str = str(npm_runner.server_dir)
    assert env["NPM_CONFIG_CACHE"].startswith(server_dir_str)
    assert env["UV_CACHE_DIR"].startswith(server_dir_str)


def test_install_env_disables_npm_prompts(npm_runner):
    env = npm_runner._build_install_env()
    assert env.get("CI") == "1"
    assert env.get("NPM_CONFIG_FUND") == "false"
    assert env.get("NPM_CONFIG_AUDIT") == "false"


def test_runtime_env_rejects_keys_outside_allowlist(npm_runner):
    """runtime_env_secrets doit ⊆ spec.env_keys_allowlist."""
    with pytest.raises(MCPSandboxError, match="env_keys_allowlist"):
        npm_runner._build_runtime_env({"NOT_ALLOWED": "value"})


def test_runtime_env_accepts_allowlisted_keys(npm_runner):
    env = npm_runner._build_runtime_env({"BRAVE_API_KEY": "bsa_abc"})
    assert env["BRAVE_API_KEY"] == "bsa_abc"


def test_runtime_env_rejects_non_string_value(npm_runner):
    with pytest.raises(MCPSandboxError):
        npm_runner._build_runtime_env({"BRAVE_API_KEY": 123})  # type: ignore


def test_runtime_env_rejects_non_dict(npm_runner):
    with pytest.raises(MCPSandboxError):
        npm_runner._build_runtime_env("not_a_dict")  # type: ignore


def test_runtime_env_none_returns_install_env(npm_runner):
    env = npm_runner._build_runtime_env(None)
    assert "BRAVE_API_KEY" not in env
    assert "PATH" in env


# ──────────────────────────────────────────────────────────────────────────────
# Install (mockée)
# ──────────────────────────────────────────────────────────────────────────────


def test_npm_install_uses_ignore_scripts_and_prefix(npm_runner):
    captured: Dict[str, Any] = {}

    def fake_run(cmd, env=None, cwd=None, capture_output=None,
                 text=None, timeout=None, check=None, **kwargs):
        # Fix Y : accepte encoding/errors (UTF-8 explicite)
        captured["cmd"] = cmd
        captured["env"] = env
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        npm_runner.install()

    cmd = captured["cmd"]
    # Phase I-7 : sur Windows, cmd[0] résout vers chemin absolu (npm.cmd).
    # Sur Unix, peut rester "npm" ou résoudre vers /usr/bin/npm.
    assert cmd[0] == "npm" or cmd[0].lower().endswith(("npm", "npm.cmd", "npm.exe", "npm.bat"))
    assert cmd[1] == "install"
    assert "--ignore-scripts" in cmd
    assert "--prefix" in cmd
    prefix_idx = cmd.index("--prefix")
    assert cmd[prefix_idx + 1] == str(npm_runner.server_dir)


def test_npm_install_uses_versioned_package(npm_runner):
    captured: Dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        npm_runner.install()

    cmd = captured["cmd"]
    # Le dernier élément doit être package@version
    assert cmd[-1] == "@modelcontextprotocol/server-brave-search@0.1.0"


def test_install_env_passed_has_no_secrets(npm_runner, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-no-leak")
    captured: Dict[str, Any] = {}

    def fake_run(cmd, env=None, **kwargs):
        captured["env"] = env
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        npm_runner.install()

    assert "OPENAI_API_KEY" not in (captured["env"] or {})


def test_install_marks_done_and_state(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    assert npm_runner.is_installed() is True
    assert npm_runner.state() == ProcessState.INSTALLED
    assert (npm_runner.server_dir / ".install_done").exists()


def test_install_idempotent(npm_runner):
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        npm_runner.install()
        first = call_count["n"]
        npm_runner.install()  # 2e appel
        second = call_count["n"]

    assert second == first, "Install doit être idempotent (skip si déjà fait)"


def test_install_failure_raises(npm_runner):
    fail = MagicMock()
    fail.returncode = 1
    fail.stdout = ""
    fail.stderr = "npm error"

    with patch("subprocess.run", return_value=fail):
        with pytest.raises(MCPSandboxError, match="exit 1"):
            npm_runner.install()
    # Et le sentinelle ne doit PAS exister
    assert not (npm_runner.server_dir / ".install_done").exists()


def test_install_missing_binary_raises(npm_runner):
    with patch("subprocess.run", side_effect=FileNotFoundError("npm not found")):
        with pytest.raises(MCPSandboxError, match="not found"):
            npm_runner.install()


def test_install_timeout_raises(npm_runner):
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["npm"], timeout=600),
    ):
        with pytest.raises(MCPSandboxError, match="timed out"):
            npm_runner.install()


def test_uv_install_creates_venv_then_pip(uv_runner):
    calls: List[List[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        uv_runner.install()

    # Au moins 2 appels : uv venv + uv pip install
    assert len(calls) >= 2
    venv_cmd = calls[0]
    install_cmd = calls[1]
    # Phase I-7 : cmd[0] peut être résolu vers un chemin absolu sur Windows.
    assert venv_cmd[0] == "uv" or venv_cmd[0].lower().endswith(("uv", "uv.exe"))
    assert venv_cmd[1] == "venv"
    assert install_cmd[0] == "uv" or install_cmd[0].lower().endswith(("uv", "uv.exe"))
    assert install_cmd[1:3] == ["pip", "install"]
    # --no-build car require_wheels_only=True
    assert "--no-build" in install_cmd
    # --python pointe vers le venv créé
    assert "--python" in install_cmd
    py_idx = install_cmd.index("--python")
    assert ".venv" in install_cmd[py_idx + 1]


def test_uv_install_skips_no_build_when_disabled(mcp_root, logs_dir):
    spec = MCPInstallSpec(
        name="allow-source",
        transport="uv",
        package="example",
        require_wheels_only=False,
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    calls: List[List[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        runner.install()

    install_cmd = calls[1]
    assert "--no-build" not in install_cmd


def test_uv_install_with_lock_file_uses_require_hashes(mcp_root, logs_dir, tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("# fake lock\n", encoding="utf-8")
    spec = MCPInstallSpec(
        name="locked",
        transport="uv",
        package="example",
        lock_file=lock,
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    calls: List[List[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        runner.install()

    install_cmd = calls[1]
    assert "--require-hashes" in install_cmd
    assert str(lock) in install_cmd


def test_uv_install_missing_lock_file_raises(mcp_root, logs_dir, tmp_path):
    lock = tmp_path / "does_not_exist.lock"
    spec = MCPInstallSpec(
        name="badlock",
        transport="uv",
        package="example",
        lock_file=lock,
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)

    def fake_run(cmd, **kwargs):
        # uv venv passe, mais uv pip install ne sera pas atteint (raise avant)
        return _make_success_run()

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(MCPSandboxError, match="lock_file not found"):
            runner.install()


# ──────────────────────────────────────────────────────────────────────────────
# Uninstall
# ──────────────────────────────────────────────────────────────────────────────


def test_uninstall_removes_server_dir(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    assert npm_runner.server_dir.exists()
    npm_runner.uninstall()
    assert not npm_runner.server_dir.exists()
    assert npm_runner.state() == ProcessState.NOT_INSTALLED


# ──────────────────────────────────────────────────────────────────────────────
# Lifecycle start/stop
# ──────────────────────────────────────────────────────────────────────────────


def test_start_refuses_if_not_installed(npm_runner):
    with pytest.raises(MCPSandboxError, match="not installed"):
        npm_runner.start()


def test_start_then_stop_state_transitions(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()

    fake = _make_fake_popen(stdout_lines=["line 1\n", "line 2\n"])

    with patch("subprocess.Popen", return_value=fake):
        npm_runner.start(runtime_env_secrets={"BRAVE_API_KEY": "bsa_test"})
        # Attendre que le monitor thread consomme les lignes
        if npm_runner._monitor_thread:
            npm_runner._monitor_thread.join(timeout=2.0)
        assert npm_runner.state() in (
            ProcessState.RUNNING,
            ProcessState.STOPPED,
        )
        assert npm_runner.get_pid() == 12345

    npm_runner.stop()
    assert npm_runner.state() == ProcessState.STOPPED


def test_start_with_secret_outside_allowlist_refused(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    with pytest.raises(MCPSandboxError, match="env_keys_allowlist"):
        npm_runner.start(runtime_env_secrets={"OPENAI_API_KEY": "leak"})


def test_start_idempotent_no_op_if_running(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()

    # readline doit BLOQUER (simule un process vivant qui n'a rien envoyé).
    # Sinon le monitor _stdout_reader_loop sort vite et met state=STOPPED
    # dans son finally, faisant passer le 2e start() à un nouveau Popen.
    release_event = threading.Event()

    def blocking_readline():
        release_event.wait(timeout=10.0)
        return ""

    fake = MagicMock()
    fake.pid = 12345
    fake.stdout = MagicMock()
    fake.stdout.readline = blocking_readline
    fake.stderr = MagicMock()
    fake.stderr.readline = blocking_readline
    fake.poll = MagicMock(return_value=None)
    fake.terminate = MagicMock()
    fake.kill = MagicMock()
    # wait discriminé par timeout (race-safe)
    def fake_wait(timeout=None):
        if timeout == 1.0:
            raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
        return 0
    fake.wait = fake_wait

    try:
        with patch("subprocess.Popen", return_value=fake) as popen_mock:
            npm_runner.start()
            # 2e appel : doit être no-op (state=RUNNING, monitor bloqué sur readline)
            npm_runner.start()
            assert popen_mock.call_count == 1
    finally:
        release_event.set()  # débloquer les readers pour cleanup
        npm_runner.stop()


def test_stop_when_not_running_no_op(npm_runner):
    # Aucune exception
    npm_runner.stop()
    assert npm_runner.state() == ProcessState.NOT_INSTALLED


def test_stop_sends_terminate_then_kill_on_timeout(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()

    # readline doit BLOQUER (simule process vivant) jusqu'à ce que stop() ait
    # fini. Sinon le monitor sort de iter() et met state=STOPPED dans son
    # finally, ce qui ferait stop() retourner avant terminate().
    release_event = threading.Event()

    def blocking_readline():
        release_event.wait(timeout=10.0)
        return ""

    fake = MagicMock()
    fake.pid = 12345
    fake.stdout = MagicMock()
    fake.stdout.readline = blocking_readline
    fake.stderr = MagicMock()
    fake.stderr.readline = blocking_readline
    fake.poll = MagicMock(return_value=None)
    # terminate déclenche aussi la libération des readers (simule SIGTERM)
    fake.terminate = MagicMock(side_effect=lambda: release_event.set())
    fake.kill = MagicMock()

    # wait discriminé par timeout (race-safe) :
    #   - timeout=1.0                       : monitor finally → TimeoutExpired
    #   - timeout=npm_runner._stop_timeout_s: stop après terminate → TimeoutExpired
    #   - timeout=2.0                       : stop après kill / monitor join → 0
    stop_timeout = npm_runner._stop_timeout_s

    def fake_wait(timeout=None):
        if timeout == 1.0:
            raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
        if timeout == stop_timeout:
            raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
        return 0

    fake.wait = fake_wait

    with patch("subprocess.Popen", return_value=fake):
        npm_runner.start()
        npm_runner.stop()

    fake.terminate.assert_called_once()
    fake.kill.assert_called_once()


def test_start_binary_missing_raises_and_crashed(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    # Pour passer la résolution _build_start_command, on fournit spec.args
    # explicitement via un runner dédié
    spec = MCPInstallSpec(
        name="missing-bin",
        transport="npm",
        package="example",
        args=["/nonexistent/path/to/binary"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=npm_runner._mcp_root, logs_dir=npm_runner._logs_dir,
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()
    with patch("subprocess.Popen", side_effect=FileNotFoundError("bin")):
        with pytest.raises(MCPSandboxError, match="not found"):
            runner.start()
    assert runner.state() == ProcessState.CRASHED


# ──────────────────────────────────────────────────────────────────────────────
# Sécurité start command : interdiction npx --yes
# ──────────────────────────────────────────────────────────────────────────────


def test_npm_start_without_args_does_not_use_npx_and_raises(npm_runner_no_args):
    """Sans spec.args ET sans binaire local résolvable → MCPSandboxError.

    Garde-fou clé : Lumena ne doit JAMAIS lancer `npx --yes <package>`
    car cela téléchargerait/exécuterait du code au runtime, contournant
    l'install contrôlée `npm install --ignore-scripts`.
    """
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner_no_args.install()
    # Le node_modules/.bin n'existe pas (install est mocké),
    # et spec.args est vide → start() doit refuser
    with pytest.raises(MCPSandboxError, match="no spec.args"):
        npm_runner_no_args.start()


def test_npm_start_uses_explicit_args_only(mcp_root, logs_dir):
    """spec.args explicite → start() utilise EXACTEMENT cette commande."""
    explicit_cmd = ["/path/to/local/server-binary", "--stdio"]
    spec = MCPInstallSpec(
        name="explicit-args",
        transport="npm",
        package="example",
        args=explicit_cmd,
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    captured: Dict[str, Any] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _make_fake_popen()

    with patch("subprocess.Popen", side_effect=fake_popen):
        runner.start()
        if runner._monitor_thread:
            runner._monitor_thread.join(timeout=2.0)
        runner.stop()

    assert captured["cmd"] == explicit_cmd


def test_npm_start_uses_resolved_local_bin(mcp_root, logs_dir):
    """Sans spec.args mais avec un binaire local installé → utilise ce binaire."""
    spec = MCPInstallSpec(
        name="with-local-bin",
        transport="npm",
        package="@modelcontextprotocol/server-fake",
        # pas d'args explicites
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    # Simule la présence du binaire local après install
    bin_dir = runner.server_dir / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    # Nom déduit du basename : "server-fake"
    if sys.platform == "win32":
        local_bin = bin_dir / "server-fake.cmd"
    else:
        local_bin = bin_dir / "server-fake"
    local_bin.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")

    captured: Dict[str, Any] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _make_fake_popen()

    with patch("subprocess.Popen", side_effect=fake_popen):
        runner.start()
        if runner._monitor_thread:
            runner._monitor_thread.join(timeout=2.0)
        runner.stop()

    assert captured["cmd"] == [str(local_bin)]


def test_start_command_never_contains_npx_yes(npm_runner):
    """Garantie forte : aucune valeur 'npx', '--yes', ou pattern de
    téléchargement runtime ne doit apparaître dans la commande de start.

    Vérifie via inspection des branches possibles de _build_start_command.
    """
    # Branche 1 : spec.args fourni
    spec = MCPInstallSpec(
        name="with-args",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/my-server"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=npm_runner._mcp_root, logs_dir=npm_runner._logs_dir,
    )
    cmd = runner._build_start_command()
    assert "npx" not in cmd
    assert "--yes" not in cmd

    # Branche 2 : pas d'args, binaire local résolu
    spec2 = MCPInstallSpec(
        name="local-resolved",
        transport="npm",
        package="@scope/local-name",
    )
    runner2 = MCPSandboxRunner(
        spec=spec2, mcp_root=npm_runner._mcp_root, logs_dir=npm_runner._logs_dir,
    )
    bin_dir = runner2.server_dir / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    bin_path = bin_dir / ("local-name.cmd" if sys.platform == "win32" else "local-name")
    bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd2 = runner2._build_start_command()
    assert "npx" not in cmd2
    assert "--yes" not in cmd2

    # Branche 3 : pas d'args, pas de binaire local → raise (pas de commande npx)
    spec3 = MCPInstallSpec(
        name="no-fallback",
        transport="npm",
        package="example",
    )
    runner3 = MCPSandboxRunner(
        spec=spec3, mcp_root=npm_runner._mcp_root, logs_dir=npm_runner._logs_dir,
    )
    with pytest.raises(MCPSandboxError) as exc_info:
        runner3._build_start_command()
    # Le message d'erreur mentionne explicitement que npx --yes est interdit
    assert "npx" in str(exc_info.value).lower() or "no spec.args" in str(exc_info.value).lower()


# ──────────────────────────────────────────────────────────────────────────────
# Fix O (Phase I-7) — résolution binaire mcp-<basename> + scan opportuniste
# ──────────────────────────────────────────────────────────────────────────────


class TestFixOBinaryResolution:
    """Fix O : les serveurs Anthropic officiels exposent leur binaire sous
    `mcp-<basename>` (ex: `mcp-server-slack`), pas sous le basename brut du
    package (`server-slack`). L'heuristique naïve ratait ce cas et levait
    MCPSandboxError au boot/activation.
    """

    def _make_runner(self, mcp_root, logs_dir, package: str) -> MCPSandboxRunner:
        spec = MCPInstallSpec(
            name="fix-o-test",
            transport="npm",
            package=package,
        )
        return MCPSandboxRunner(
            spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        )

    def test_resolves_mcp_prefixed_binary(self, mcp_root, logs_dir):
        """Cas Slack/GitHub/etc. : `@modelcontextprotocol/server-slack`
        → basename `server-slack` → binaire réel `mcp-server-slack`."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@modelcontextprotocol/server-slack",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        bin_name = (
            "mcp-server-slack.cmd" if sys.platform == "win32"
            else "mcp-server-slack"
        )
        bin_path = bin_dir / bin_name
        bin_path.write_text("#!/bin/sh\n", encoding="utf-8")

        resolved = runner._resolve_local_npm_bin()
        assert resolved is not None
        assert resolved.name == bin_name

    def test_prefers_basename_when_both_exist(self, mcp_root, logs_dir):
        """Si basename direct existe ET mcp-basename existe, prendre basename
        (cas package conforme au pattern noyau npm)."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@scope/my-tool",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        ext = ".cmd" if sys.platform == "win32" else ""
        (bin_dir / f"my-tool{ext}").write_text("#!/bin/sh\n", encoding="utf-8")
        (bin_dir / f"mcp-my-tool{ext}").write_text("#!/bin/sh\n", encoding="utf-8")

        resolved = runner._resolve_local_npm_bin()
        assert resolved is not None
        assert resolved.stem == "my-tool"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ext priority")
    def test_windows_prefers_cmd_over_ps1(self, mcp_root, logs_dir):
        """Sur Windows, .cmd doit être priorisé sur .ps1 pour Popen."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@modelcontextprotocol/server-slack",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "mcp-server-slack.ps1").write_text("", encoding="utf-8")
        (bin_dir / "mcp-server-slack.cmd").write_text("", encoding="utf-8")

        resolved = runner._resolve_local_npm_bin()
        assert resolved is not None
        assert resolved.suffix == ".cmd"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only Fix P regression")
    def test_fix_p_windows_skips_unix_shell_when_cmd_exists(
        self, mcp_root, logs_dir,
    ):
        """Fix P regression : npm crée TROIS variantes du binaire sur Windows
        (sans-ext = script Unix, .cmd = batch Windows, .ps1 = PowerShell).
        L'ordre naïf (sans-ext en premier) faisait choisir le script Unix
        → Popen Windows → WinError 193 "%1 n'est pas une application Win32".
        Fix P doit prioriser .cmd même si le sans-extension existe aussi."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@modelcontextprotocol/server-slack",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Reproduit exactement ce que `npm install` fait sur Windows
        (bin_dir / "mcp-server-slack").write_text(
            "#!/usr/bin/env node\n", encoding="utf-8"
        )
        (bin_dir / "mcp-server-slack.cmd").write_text(
            "@node mcp-server-slack %*\n", encoding="utf-8"
        )
        (bin_dir / "mcp-server-slack.ps1").write_text("", encoding="utf-8")

        resolved = runner._resolve_local_npm_bin()
        assert resolved is not None
        assert resolved.suffix == ".cmd", (
            f"Sur Windows, .cmd doit gagner sur sans-extension. Got: {resolved}"
        )

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only Fix P scan")
    def test_fix_p_windows_fallback_scan_refuses_unix_shell(
        self, mcp_root, logs_dir,
    ):
        """Fix P fallback scan : si seul un script Unix sans extension existe
        (pas de .cmd ni .ps1) sur Windows, refuser → return None plutôt que
        de retourner un binaire qui plantera Popen avec WinError 193."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@weird/exotic-tool",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Seulement le script Unix, pas de wrapper Windows
        (bin_dir / "exotic-tool-stub").write_text(
            "#!/usr/bin/env node\n", encoding="utf-8"
        )

        # Le pattern match (`exotic-tool` ⊂ `exotic-tool-stub`) mais sur
        # Windows on doit refuser car ça plantera Popen.
        resolved = runner._resolve_local_npm_bin()
        assert resolved is None or resolved.suffix != "", (
            f"Sur Windows, ne JAMAIS retourner un sans-extension. Got: {resolved}"
        )

    def test_fallback_scan_for_exotic_package(self, mcp_root, logs_dir):
        """Fallback scan : package sans pattern standard, mais binaire
        existe sous un nom qui contient le basename."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@weird/chess",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        ext = ".cmd" if sys.platform == "win32" else ""
        # Ni "chess" ni "mcp-chess" — un nom exotique qui contient "chess"
        (bin_dir / f"play-chess-bot{ext}").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )

        resolved = runner._resolve_local_npm_bin()
        assert resolved is not None
        assert "chess" in resolved.stem.lower()

    def test_returns_none_when_truly_missing(self, mcp_root, logs_dir):
        """Aucun binaire ne matche → None (laisse start() lever proprement)."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@scope/totally-absent",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Aucun fichier dans le bin_dir
        assert runner._resolve_local_npm_bin() is None

    def test_build_start_command_uses_resolved_binary(self, mcp_root, logs_dir):
        """End-to-end : _build_start_command renvoie [binaire mcp-prefixed]
        sans lever, sans spec.args, pour package Anthropic standard."""
        runner = self._make_runner(
            mcp_root, logs_dir, "@modelcontextprotocol/server-slack",
        )
        bin_dir = runner.server_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        bin_name = (
            "mcp-server-slack.cmd" if sys.platform == "win32"
            else "mcp-server-slack"
        )
        bin_path = bin_dir / bin_name
        bin_path.write_text("#!/bin/sh\n", encoding="utf-8")

        cmd = runner._build_start_command()
        assert len(cmd) == 1
        assert cmd[0].endswith(bin_name)
        # Garantie sécurité maintenue : pas de npx dans la commande
        assert "npx" not in cmd[0].lower()


# ──────────────────────────────────────────────────────────────────────────────
# Healthcheck
# ──────────────────────────────────────────────────────────────────────────────


def test_is_alive_false_when_not_started(npm_runner):
    assert npm_runner.is_alive() is False


def test_is_alive_uses_process_poll(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    fake = _make_fake_popen()
    fake.poll = MagicMock(return_value=None)  # toujours running
    with patch("subprocess.Popen", return_value=fake):
        npm_runner.start()
        assert npm_runner.is_alive() is True
        fake.poll = MagicMock(return_value=0)
        assert npm_runner.is_alive() is False


# ──────────────────────────────────────────────────────────────────────────────
# Logs
# ──────────────────────────────────────────────────────────────────────────────


def test_logs_capture_subprocess_stdout(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    fake = _make_fake_popen(stdout_lines=["MCP started\n", "Listening...\n"])
    with patch("subprocess.Popen", return_value=fake):
        npm_runner.start()
        if npm_runner._monitor_thread:
            npm_runner._monitor_thread.join(timeout=2.0)

    logs = npm_runner.get_logs(lines=100)
    assert any("MCP started" in line for line in logs)


def test_logs_rolling_max_size(npm_runner, mcp_root, logs_dir):
    # Reconstruit avec max_log_lines petit
    spec = MCPInstallSpec(
        name="rolling",
        transport="npm",
        package="example",
        env_keys_allowlist=[],
        args=["./node_modules/.bin/rolling-stub"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir, max_log_lines=5,
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    fake = _make_fake_popen(
        stdout_lines=[f"line {i}\n" for i in range(20)]
    )
    with patch("subprocess.Popen", return_value=fake):
        runner.start()
        if runner._monitor_thread:
            runner._monitor_thread.join(timeout=2.0)

    logs = runner.get_logs(lines=100)
    assert len(logs) <= 5


def test_logs_scrub_runtime_secrets(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()

    secret_value = "bsa_super_secret_token_xyz"
    fake = _make_fake_popen(
        stdout_lines=[
            f"Connecting with key={secret_value}\n",
            "OK\n",
        ]
    )
    with patch("subprocess.Popen", return_value=fake):
        npm_runner.start(runtime_env_secrets={"BRAVE_API_KEY": secret_value})
        if npm_runner._monitor_thread:
            npm_runner._monitor_thread.join(timeout=2.0)

    logs = npm_runner.get_logs(lines=10)
    full = "\n".join(logs)
    assert secret_value not in full, "Le secret runtime doit être scrubbed des logs"
    assert "****" in full


def test_get_logs_with_zero_returns_empty(npm_runner):
    assert npm_runner.get_logs(lines=0) == []


# ──────────────────────────────────────────────────────────────────────────────
# Quarantine
# ──────────────────────────────────────────────────────────────────────────────


def test_quarantine_after_3_crashes(npm_runner):
    # Simule 3 crashes
    for _ in range(3):
        npm_runner._record_crash()
    assert npm_runner.is_quarantined() is True
    assert npm_runner.crash_count_last_5min() == 3


def test_start_refused_when_quarantined(npm_runner):
    with patch("subprocess.run", return_value=_make_success_run()):
        npm_runner.install()
    for _ in range(3):
        npm_runner._record_crash()
    assert npm_runner.is_quarantined()
    with pytest.raises(MCPSandboxError, match="quarantined"):
        npm_runner.start()


def test_clear_quarantine_resets_state(npm_runner):
    for _ in range(3):
        npm_runner._record_crash()
    assert npm_runner.is_quarantined()
    npm_runner.clear_quarantine()
    assert not npm_runner.is_quarantined()
    assert npm_runner.crash_count_last_5min() == 0


def test_crash_window_garbage_collection(npm_runner, monkeypatch):
    # Anciens crashes hors fenêtre 5min → ignorés
    npm_runner._crash_timestamps = [
        1.0, 2.0, 3.0,  # vieux, > 5min en arrière
    ]
    assert npm_runner.crash_count_last_5min() == 0
    npm_runner._record_crash()  # nouveau → comptera 1
    assert npm_runner.crash_count_last_5min() == 1
    assert not npm_runner.is_quarantined()  # 1 < 3


# ──────────────────────────────────────────────────────────────────────────────
# Lock concurrent install
# ──────────────────────────────────────────────────────────────────────────────


def test_install_lock_serializes_concurrent(mcp_root, logs_dir, npm_spec):
    """Deux threads appelant install() en parallèle → l'install ne se fait qu'une fois (idempotence + lock).

    IMPORTANT : le `with patch(...)` est au niveau du test, PAS dans les threads.
    `unittest.mock.patch` n'est pas thread-safe — un thread qui sort restaure
    l'attribut global même si un autre thread est encore actif. Patcher une
    seule fois au niveau du test garantit qu'aucun mock ne leak au-delà.
    """
    runner_a = MCPSandboxRunner(spec=npm_spec, mcp_root=mcp_root, logs_dir=logs_dir)
    runner_b = MCPSandboxRunner(spec=npm_spec, mcp_root=mcp_root, logs_dir=logs_dir)

    call_count = {"n": 0}
    call_lock = threading.Lock()

    def fake_run(cmd, **kwargs):
        with call_lock:
            call_count["n"] += 1
        return _make_success_run()

    errors: List[Exception] = []

    def installer(runner):
        try:
            runner.install()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    # Patch global au niveau du test (thread-safe car restauré APRÈS les joins)
    with patch("subprocess.run", side_effect=fake_run):
        t_a = threading.Thread(target=installer, args=(runner_a,))
        t_b = threading.Thread(target=installer, args=(runner_b,))
        t_a.start()
        t_b.start()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

    assert errors == [], f"Erreurs concurrent install: {errors}"
    # L'install ne doit avoir été lancée qu'une fois (idempotence)
    assert call_count["n"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Smoke tests live (opt-in)
# ──────────────────────────────────────────────────────────────────────────────


def _have_binary(name: str) -> bool:
    return shutil.which(name) is not None


@pytest.mark.skipif(
    not _have_binary("npm"),
    reason="npm not installed (live test, opt-in)",
)
def test_live_npm_binary_is_on_path():
    """Smoke test : npm est sur le PATH (utilisable par runner.install())."""
    path = shutil.which("npm")
    assert path is not None
    assert Path(path).exists()


@pytest.mark.skipif(
    not _have_binary("uv"),
    reason="uv not installed (live test, opt-in)",
)
def test_live_uv_binary_is_on_path():
    """Smoke test : uv est sur le PATH."""
    path = shutil.which("uv")
    assert path is not None
    assert Path(path).exists()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5.1 v2 : stdout ownership client (stderr capturé, stdout pas lu)
# ──────────────────────────────────────────────────────────────────────────────
#
# Discipline thread-safe stricte :
#   - 1 runner instance par test (max)
#   - join explicite avec timeout 2s pour chaque thread créé
#   - try/finally avec stop() + joins pour cleanup garanti
#   - pas de MCPClient.initialize() réel (juste construction)


def _safe_join_runner(runner):
    """Cleanup helper : join tous les threads d'un runner, timeout court."""
    if runner._monitor_thread is not None:
        runner._monitor_thread.join(timeout=2.0)
    if runner._watcher_thread is not None:
        runner._watcher_thread.join(timeout=2.0)
    if runner._stderr_thread is not None:
        runner._stderr_thread.join(timeout=2.0)


# ─── Tests sans start() (0 thread créé) ─────────────────────────────────────


def test_phase51_default_stdout_mode_is_client(mcp_root, logs_dir):
    """Le mode par défaut DOIT être 'client' (stdout appartient MCPClient)."""
    spec = MCPInstallSpec(
        name="default-mode",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    assert runner._stdout_mode == "client"


def test_phase51_invalid_stdout_mode_rejected(mcp_root, logs_dir):
    spec = MCPInstallSpec(
        name="bad-mode",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    with pytest.raises(MCPSandboxError, match="Invalid stdout_mode"):
        MCPSandboxRunner(
            spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
            stdout_mode="invalid_xxx",
        )


def test_phase51_get_logs_stdout_empty_in_client_mode(mcp_root, logs_dir):
    """En mode 'client', get_logs(stream='stdout') retourne [] (état attendu)."""
    spec = MCPInstallSpec(
        name="client-empty",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    # Même si on push manuellement (impossible normalement) :
    runner._stdout_buffer.append("should-not-appear")
    assert runner.get_logs(lines=10, stream="stdout") == []


def test_phase51_get_logs_invalid_stream_raises(mcp_root, logs_dir):
    spec = MCPInstallSpec(
        name="bad-stream",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    runner = MCPSandboxRunner(spec=spec, mcp_root=mcp_root, logs_dir=logs_dir)
    with pytest.raises(MCPSandboxError, match="Invalid stream"):
        runner.get_logs(lines=10, stream="invalid_xxx")


def test_phase51_get_logs_stream_all_skips_stdout_in_client_mode(mcp_root, logs_dir):
    """En mode client, stream='all' ne contient pas de [stdout]."""
    spec = MCPInstallSpec(
        name="all-client",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    runner._stdout_buffer.append("rpc-msg")  # forcé manuellement
    runner._stderr_buffer.append("server-log")
    all_logs = runner.get_logs(lines=10, stream="all")
    joined = "\n".join(all_logs)
    assert "[stdout]" not in joined
    assert "[stderr]" in joined
    assert "server-log" in joined


# ─── Tests avec start() (max 2 threads, cleanup garanti) ─────────────────────


def test_phase51_popen_uses_stderr_pipe(mcp_root, logs_dir):
    """En mode client, Popen est appelé avec stderr=PIPE (pas STDOUT)."""
    spec = MCPInstallSpec(
        name="stderr-pipe-test",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    captured: Dict[str, Any] = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _make_fake_popen()

    try:
        with patch("subprocess.Popen", side_effect=fake_popen):
            runner.start()
            assert captured.get("stderr") == subprocess.PIPE
            assert captured.get("stderr") != subprocess.STDOUT
    finally:
        runner.stop()
        _safe_join_runner(runner)


def test_phase51_client_mode_does_not_read_stdout(mcp_root, logs_dir):
    """PREUVE CRITIQUE : en mode client, le runner ne consomme PAS stdout.

    On push des lignes stdout via le fake Popen, on vérifie qu'aucune n'arrive
    dans _stdout_buffer après start/stop.
    """
    spec = MCPInstallSpec(
        name="no-stdout-read",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
        env_keys_allowlist=[],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    fake = _make_fake_popen(
        stdout_lines=[
            '{"jsonrpc":"2.0","id":1}\n',
            '{"jsonrpc":"2.0","id":2}\n',
        ],
        stderr_lines=["[INFO] ready\n"],
    )

    try:
        with patch("subprocess.Popen", return_value=fake):
            runner.start()
            # Pas de _monitor_thread en mode client
            assert runner._monitor_thread is None
            # _watcher_thread existe et est joinable
            assert runner._watcher_thread is not None
    finally:
        runner.stop()
        _safe_join_runner(runner)

    # Buffer stdout doit rester vide même si stdout_lines a été poussé
    assert list(runner._stdout_buffer) == []


def test_phase51_stderr_captured_in_client_mode(mcp_root, logs_dir):
    """Stderr est bien capturé même en mode client."""
    spec = MCPInstallSpec(
        name="stderr-captured",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
        env_keys_allowlist=[],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    fake = _make_fake_popen(
        stdout_lines=['{"jsonrpc":"2.0","id":1}\n'],
        stderr_lines=[
            "[INFO] Server up\n",
            "[INFO] Tool registered\n",
        ],
    )

    try:
        with patch("subprocess.Popen", return_value=fake):
            runner.start()
            # Laisser le stderr reader thread consommer
            if runner._stderr_thread:
                runner._stderr_thread.join(timeout=2.0)
    finally:
        runner.stop()
        _safe_join_runner(runner)

    stderr_logs = runner.get_logs(lines=100, stream="stderr")
    assert any("Server up" in line for line in stderr_logs)
    assert any("Tool registered" in line for line in stderr_logs)


def test_phase51_capture_mode_still_reads_stdout(mcp_root, logs_dir):
    """Rétrocompat : mode 'capture' lit stdout comme avant Phase 5.1."""
    spec = MCPInstallSpec(
        name="capture-mode",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
        env_keys_allowlist=[],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="capture",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    fake = _make_fake_popen(
        stdout_lines=["line A\n", "line B\n"],
        stderr_lines=[],
    )

    try:
        with patch("subprocess.Popen", return_value=fake):
            runner.start()
            # Mode capture : _monitor_thread existe, _watcher_thread est None
            assert runner._monitor_thread is not None
            assert runner._watcher_thread is None
            if runner._monitor_thread:
                runner._monitor_thread.join(timeout=2.0)
    finally:
        runner.stop()
        _safe_join_runner(runner)

    stdout_logs = runner.get_logs(lines=100, stream="stdout")
    assert any("line A" in line for line in stdout_logs)


def test_phase51_process_exits_immediately_not_marked_running(mcp_root, logs_dir):
    """Race condition : un process qui sort immédiatement avec rc != 0
    NE DOIT PAS finir avec state=RUNNING.

    Avant correction : start() marquait RUNNING APRÈS le démarrage des threads,
    écrasant un éventuel CRASHED déjà posé par le watcher.
    Après correction : RUNNING posé AVANT les threads, puis le watcher peut
    transitionner vers CRASHED sans être écrasé.
    """
    spec = MCPInstallSpec(
        name="immediate-exit",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
        env_keys_allowlist=[],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    # Fake Popen avec wait() qui retourne 1 immédiatement (crash)
    fake = _make_fake_popen()
    fake.wait = MagicMock(return_value=1)  # exit code 1 = crash
    fake.poll = MagicMock(return_value=1)

    try:
        with patch("subprocess.Popen", return_value=fake):
            runner.start()
            # Attendre que le watcher consomme proc.wait() et transitionne
            if runner._watcher_thread:
                runner._watcher_thread.join(timeout=2.0)
            # Capture l'état AVANT stop() : sinon le test dépend du
            # comportement futur de stop() qui pourrait remettre STOPPED.
            final_state = runner.state()
            assert final_state == ProcessState.CRASHED, (
                f"Expected CRASHED after immediate exit with rc=1, "
                f"got {final_state}"
            )
            assert final_state != ProcessState.RUNNING
    finally:
        runner.stop()
        _safe_join_runner(runner)


def test_phase51_mcpclient_accepts_popen_from_runner(mcp_root, logs_dir):
    """MCPClient peut être construit avec le Popen fourni par le runner en
    mode client. Pas d'initialize réel (phase d'intégration future)."""
    from src.mcp.client import MCPClient

    spec = MCPInstallSpec(
        name="mcpclient-construct",
        transport="npm",
        package="example",
        args=["./node_modules/.bin/stub"],
        env_keys_allowlist=[],
    )
    runner = MCPSandboxRunner(
        spec=spec, mcp_root=mcp_root, logs_dir=logs_dir,
        stdout_mode="client",
    )
    with patch("subprocess.run", return_value=_make_success_run()):
        runner.install()

    fake = _make_fake_popen()
    try:
        with patch("subprocess.Popen", return_value=fake):
            runner.start()
            # CRITIQUE : MCPClient accepte le Popen (stdin/stdout/stderr présents)
            # PAS d'initialize() réel : juste construction.
            client = MCPClient(fake, server_name="mcpclient-construct")
            assert client.server_name == "mcpclient-construct"
            assert client.is_initialized is False
            client.close()
    finally:
        runner.stop()
        _safe_join_runner(runner)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 19.5 — Propriété read-only `process`
# ══════════════════════════════════════════════════════════════════════════════


class TestProcessProperty:
    """Phase 19.5 : propriété read-only `process` defensive (lock + state + poll)."""

    def test_process_is_none_before_start(self, npm_runner):
        """État initial NOT_INSTALLED → process is None."""
        assert npm_runner.process is None

    def test_process_is_none_when_state_installed_but_no_process(self, npm_runner):
        """État INSTALLED sans _process → None."""
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.INSTALLED
            npm_runner._process = None
        assert npm_runner.process is None

    def test_process_returns_popen_when_state_running_and_alive(self, npm_runner):
        """State=RUNNING + _process non-None + poll()=None → retourne Popen."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen
        assert npm_runner.process is fake_popen

    def test_process_returns_popen_when_state_starting_and_alive(self, npm_runner):
        """State=STARTING également valide."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.STARTING
            npm_runner._process = fake_popen
        assert npm_runner.process is fake_popen

    def test_process_none_when_state_stopped(self, npm_runner):
        """State=STOPPED → None même si _process présent (defensive)."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.STOPPED
            npm_runner._process = fake_popen
        assert npm_runner.process is None

    def test_process_none_when_state_crashed(self, npm_runner):
        """State=CRASHED → None."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.CRASHED
            npm_runner._process = fake_popen
        assert npm_runner.process is None

    def test_process_none_when_poll_nonzero_even_if_state_running(self, npm_runner):
        """Defensive : state=RUNNING mais poll()!=None → None.

        Évite d'attacher MCPClient à un Popen mort en cas de
        désynchronisation transitoire entre poll() et _state.
        """
        fake_popen = MagicMock()
        fake_popen.poll.return_value = 42  # subprocess terminé
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen
        assert npm_runner.process is None

    def test_process_none_when_poll_zero_even_if_state_running(self, npm_runner):
        """Defensive : exit code 0 aussi → None."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = 0
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen
        assert npm_runner.process is None

    def test_process_none_when_poll_raises(self, npm_runner):
        """Defensive : poll() lève une exception → None (pas de fuite)."""
        fake_popen = MagicMock()
        fake_popen.poll.side_effect = RuntimeError("poll error")
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen
        assert npm_runner.process is None

    def test_process_read_only_no_setter(self, npm_runner):
        """Pas de setter → AttributeError natif Python."""
        with pytest.raises(AttributeError):
            npm_runner.process = MagicMock()  # type: ignore

    def test_process_returns_same_popen_across_reads(self, npm_runner):
        """Idempotence : 2 reads consécutifs → même Popen."""
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen
        p1 = npm_runner.process
        p2 = npm_runner.process
        assert p1 is p2

    def test_process_uses_state_lock(self, npm_runner):
        """Sanity : la propriété acquiert _state_lock pendant l'accès.

        Vérifié indirectement en spy sur __enter__ / __exit__ du lock.
        """
        fake_popen = MagicMock()
        fake_popen.poll.return_value = None
        with npm_runner._state_lock:
            npm_runner._state = ProcessState.RUNNING
            npm_runner._process = fake_popen

        # Wrap le lock pour compter les acquisitions
        original_lock = npm_runner._state_lock
        enter_count = {"v": 0}
        exit_count = {"v": 0}

        class _SpyLock:
            def __enter__(self):
                enter_count["v"] += 1
                return original_lock.__enter__()

            def __exit__(self, exc_type, exc_val, exc_tb):
                exit_count["v"] += 1
                return original_lock.__exit__(exc_type, exc_val, exc_tb)

        npm_runner._state_lock = _SpyLock()
        try:
            _ = npm_runner.process
        finally:
            npm_runner._state_lock = original_lock
        assert enter_count["v"] >= 1
        assert exit_count["v"] >= 1
