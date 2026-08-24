from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import lumena_updater


def _argv(tmp_path: Path) -> list[str]:
    return [
        "lumena_updater.py", "--root", str(tmp_path), "--state", str(tmp_path / "state.json"),
        "--parent-pid", "123", "--mode", "portable", "--transaction", str(tmp_path / "transaction.json"),
        "--version", "1.0.48", "--commit", "a" * 40, "--python", sys.executable,
        "--restart-json", json.dumps([sys.executable, "run_desktop.py"]),
        "--health-url", "http://127.0.0.1:8080/api/health",
    ]


def test_helper_applies_smokes_restarts_and_requires_exact_health(tmp_path: Path, monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(sys, "argv", _argv(tmp_path))
    monkeypatch.setattr(lumena_updater.time, "sleep", lambda _: None)
    monkeypatch.setattr(lumena_updater, "_terminate_parent", lambda pid: events.append("terminate"))
    monkeypatch.setattr(lumena_updater, "_wait_stopped", lambda pid: events.append("stopped"))
    monkeypatch.setattr(lumena_updater, "apply_transaction", lambda path: events.append("applied"))
    monkeypatch.setattr(lumena_updater, "_run_smoke", lambda *args: events.append("smoke"))
    monkeypatch.setattr(lumena_updater, "_launch", lambda *args: events.append("restart"))
    monkeypatch.setattr(
        lumena_updater, "_wait_health",
        lambda *args, **kwargs: {"status": "ok", "version": "1.0.48"},
    )

    assert lumena_updater.main() == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert events == ["terminate", "stopped", "applied", "smoke", "restart"]
    assert state["state"] == "healthy" and state["installed_version"] == "1.0.48"


def test_helper_rolls_back_when_post_restart_health_is_wrong(tmp_path: Path, monkeypatch) -> None:
    transaction = tmp_path / "transaction.json"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "build-info.json").write_text(
        '{"version":"1.0.47","commit":"old-commit"}', encoding="utf-8"
    )
    transaction.write_text(json.dumps({"snapshot_dir": str(snapshot)}), encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(sys, "argv", _argv(tmp_path))
    monkeypatch.setattr(lumena_updater.time, "sleep", lambda _: None)
    monkeypatch.setattr(lumena_updater, "_terminate_parent", lambda pid: None)
    monkeypatch.setattr(lumena_updater, "_wait_stopped", lambda pid: None)
    monkeypatch.setattr(lumena_updater, "apply_transaction", lambda path: events.append("applied"))
    monkeypatch.setattr(lumena_updater, "rollback_transaction", lambda path: events.append("rolled-back"))
    monkeypatch.setattr(lumena_updater, "_run_smoke", lambda *args: None)
    monkeypatch.setattr(lumena_updater, "_launch", lambda *args: events.append("restart"))
    health_calls = iter([RuntimeError("wrong build"), {"status": "ok", "version": "1.0.47"}])

    def health(*args, **kwargs):
        result = next(health_calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(lumena_updater, "_wait_health", health)

    assert lumena_updater.main() == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert events == ["applied", "restart", "rolled-back", "restart"]
    assert state["state"] == "failed"
    assert state["rollback_health"]["version"] == "1.0.47"


def test_git_helper_does_not_require_managed_manifest_in_health(tmp_path: Path, monkeypatch) -> None:
    args = _argv(tmp_path)
    args[args.index("portable")] = "git"
    events: list[str] = []
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(lumena_updater.time, "sleep", lambda _: None)
    monkeypatch.setattr(lumena_updater, "_terminate_parent", lambda pid: None)
    monkeypatch.setattr(lumena_updater, "_wait_stopped", lambda pid: None)
    monkeypatch.setattr(lumena_updater, "git_fast_forward_to", lambda *args: events.append("ff"))
    monkeypatch.setattr(lumena_updater, "_run_smoke", lambda *args: events.append("smoke"))
    monkeypatch.setattr(lumena_updater, "_launch", lambda *args: events.append("restart"))
    required: list[bool] = []

    def health(*args, **kwargs):
        required.append(kwargs["require_manifest"])
        return {"status": "ok", "version": "1.0.48", "commit": "a" * 40}

    monkeypatch.setattr(lumena_updater, "_wait_health", health)

    assert lumena_updater.main() == 0
    assert events == ["ff", "smoke", "restart"]
    assert required == [False]
