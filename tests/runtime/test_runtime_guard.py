from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext

import pytest

from scripts.runtime_guard import (
    RuntimeGuardError,
    compare_versions,
    parse_exact_lock,
    resolve_wheelhouse,
    verify_wheelhouse,
)
import scripts.runtime_guard as runtime_guard


ROOT = Path(__file__).resolve().parents[2]


def test_exact_lock_accepts_bom_and_normalises_distribution_names(tmp_path):
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text("\ufeffFoo_Bar==1.2.3\nhttpx==0.28.1\n", encoding="utf-8")

    assert parse_exact_lock(lock) == {"foo-bar": "1.2.3", "httpx": "0.28.1"}


def test_non_exact_lock_is_never_used_for_automatic_repair(tmp_path):
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text("httpx>=0.28\n", encoding="utf-8")

    with pytest.raises(RuntimeGuardError, match="Lock non exact"):
        parse_exact_lock(lock)


def test_drift_reports_missing_and_wrong_versions_without_caring_about_extras():
    missing, mismatched = compare_versions(
        {"alpha": "1.0", "beta": "2.0"},
        {"alpha": "0.9", "extra": "9.9"},
    )

    assert missing == ["beta==2.0"]
    assert mismatched == ["alpha: 0.9 != 1.0"]


def test_wheelhouse_must_be_bound_to_the_exact_lock(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / ".requirements.sha256").write_text("abc\n", encoding="ascii")

    verify_wheelhouse(wheelhouse, "abc")
    with pytest.raises(RuntimeGuardError, match="différent du lock"):
        verify_wheelhouse(wheelhouse, "def")


def test_wheelhouse_resolution_supports_installed_and_repository_layouts(tmp_path):
    repository = tmp_path / "installer" / "wheelhouse"
    repository.mkdir(parents=True)
    assert resolve_wheelhouse(tmp_path) == repository

    installed = tmp_path / "wheelhouse"
    installed.mkdir()
    assert resolve_wheelhouse(tmp_path) == installed


def test_launchers_run_offline_guard_before_application_start():
    for name in ("START.bat", "START_DESKTOP.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        guard = text.index("scripts\\runtime_guard.py")
        assert "--repair" in text[guard: guard + 180]
        assert guard < text.index("run_desktop.py") if name == "START_DESKTOP.bat" else guard < text.index("web/server.py")

    desktop = (ROOT / "START_DESKTOP.bat").read_text(encoding="utf-8")
    assert "pip install pywebview" not in desktop


def test_guard_is_offline_and_never_handles_user_configuration():
    source = (ROOT / "scripts" / "runtime_guard.py").read_text(encoding="utf-8")
    assert '"--no-index"' in source
    assert '"--find-links"' in source
    assert "requests." not in source
    assert "urllib" not in source
    assert '".env"' not in source


def test_pip_check_blocks_conflict_owned_by_main_runtime(monkeypatch, tmp_path):
    (tmp_path / "requirements-lock.txt").write_text(
        "fsspec==2026.3.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        runtime_guard,
        "_run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "fsspec 2026.3.0 has requirement alpha==1, but you have alpha 2.\n",
                "stderr": "",
            },
        )(),
    )

    with pytest.raises(RuntimeGuardError, match="fsspec 2026.3.0"):
        runtime_guard._pip_check(tmp_path)


def test_pip_check_logs_but_does_not_block_optional_extension_conflict(
    monkeypatch, tmp_path
):
    (tmp_path / "requirements-lock.txt").write_text(
        "fsspec==2026.3.0\n", encoding="utf-8"
    )
    logs = []
    monkeypatch.setattr(
        runtime_guard,
        "_run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": (
                    "datasets 4.3.0 has requirement fsspec<=2025.9.0, "
                    "but you have fsspec 2026.3.0.\n"
                ),
                "stderr": "",
            },
        )(),
    )
    monkeypatch.setattr(
        runtime_guard, "_append_log", lambda _root, message: logs.append(message)
    )

    runtime_guard._pip_check(tmp_path)

    assert len(logs) == 1
    assert "extension optionnelle" in logs[0]
    assert "datasets 4.3.0" in logs[0]


def test_fast_path_never_invokes_repair(monkeypatch, tmp_path):
    (tmp_path / "venv").mkdir()
    calls = []
    monkeypatch.setattr(runtime_guard, "verify_runtime", lambda _root: {
        "ready": True, "lock_sha256": "abc", "locked_count": 2,
        "missing": [], "mismatched": [], "missing_imports": [],
    })
    monkeypatch.setattr(runtime_guard, "_pip_check", lambda _root: calls.append("check"))
    monkeypatch.setattr(runtime_guard, "_native_import_probe", lambda _root: calls.append("native"))
    monkeypatch.setattr(
        runtime_guard, "_repair_from_wheelhouse",
        lambda *_args, **_kwargs: calls.append("repair"),
    )

    report = runtime_guard.ensure_runtime(tmp_path, repair=True)

    assert report["ready"] is True
    assert calls == ["check", "native"]
    assert (tmp_path / "venv" / runtime_guard.STATE_FILENAME).is_file()


def test_repair_path_is_serialised_and_uses_only_resolved_wheelhouse(monkeypatch, tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (tmp_path / "requirements-lock.txt").write_text("alpha==1\n", encoding="utf-8")
    reports = iter([
        {"ready": False, "lock_sha256": "abc", "locked_count": 1,
         "missing": ["alpha==1"], "mismatched": [], "missing_imports": []},
        {"ready": False, "lock_sha256": "abc", "locked_count": 1,
         "missing": ["alpha==1"], "mismatched": [], "missing_imports": []},
        {"ready": True, "lock_sha256": "abc", "locked_count": 1,
         "missing": [], "mismatched": [], "missing_imports": []},
    ])
    calls = []
    monkeypatch.setattr(runtime_guard, "verify_runtime", lambda _root: next(reports))
    monkeypatch.setattr(runtime_guard, "resolve_wheelhouse", lambda _root: wheelhouse)
    monkeypatch.setattr(runtime_guard, "verify_wheelhouse", lambda path, digest: calls.append(("wheelhouse", path, digest)))
    monkeypatch.setattr(runtime_guard, "repair_lock", lambda _root: nullcontext())
    monkeypatch.setattr(runtime_guard, "_repair_from_wheelhouse", lambda root, wheels, lock: calls.append(("repair", root, wheels, lock)))
    monkeypatch.setattr(runtime_guard, "_pip_check", lambda _root: calls.append(("check",)))
    monkeypatch.setattr(runtime_guard, "_native_import_probe", lambda _root: calls.append(("native",)))
    monkeypatch.setattr(runtime_guard, "_write_json_atomic", lambda *_args: None)
    monkeypatch.setattr(runtime_guard, "_append_log", lambda *_args: None)

    report = runtime_guard.ensure_runtime(tmp_path, repair=True)

    assert report["ready"] is True and report["repaired"] is True
    assert calls[0] == ("wheelhouse", wheelhouse, "abc")
    assert calls[1][0] == "repair"
    assert calls[1][2] == wheelhouse
    assert calls[1][3] == tmp_path / "requirements-lock.txt"
    assert calls[2] == ("check",)
    assert calls[3] == ("native",)


def test_native_probe_loads_real_extensions_in_a_disposable_process(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        runtime_guard,
        "_run",
        lambda command, **kwargs: seen.append((list(command), kwargs)) or type(
            "Result", (), {"returncode": 0, "stderr": "", "stdout": ""}
        )(),
    )

    runtime_guard._native_import_probe(tmp_path)

    command, kwargs = seen[0]
    assert command[:2] == [runtime_guard.sys.executable, "-c"]
    assert "chromadb_rust_bindings" in command[2]
    assert "ctranslate2" in command[2]
    assert kwargs["cwd"] == tmp_path
