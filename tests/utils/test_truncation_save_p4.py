"""Tests P4 — truncation_save."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_flag_cache(monkeypatch):
    # Force re-import of FLAGS with current env
    import src.config.codeagent_flags as cf
    import importlib
    importlib.reload(cf)
    yield
    importlib.reload(cf)


def test_save_and_truncate_noop_when_under_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    small = "x" * 1000
    out = ts.save_and_truncate(small, task_id="t1", iteration=1, threshold=8000)
    assert out == small
    # Pas de fichier créé
    assert not (tmp_path / "codeagent").exists()


def test_save_and_truncate_writes_file_and_returns_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    big = "HEAD_START" + ("a" * 20000) + "TAIL_END"
    out = ts.save_and_truncate(
        big, task_id="task-42", iteration=3,
        threshold=8000, head_chars=100, tail_chars=50,
    )

    # Résumé contient head + marker + tail
    assert out.startswith("HEAD_START")
    assert out.endswith("TAIL_END")
    assert "tronqués" in out
    assert "read_file(" in out

    # Fichier sauvegardé
    target = tmp_path / "codeagent" / "task-42" / "obs_0003.txt"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == big


def test_save_and_truncate_sanitizes_task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    big = "x" * 20000
    ts.save_and_truncate(
        big, task_id="../../evil/path", iteration=1, threshold=100,
    )
    # Pas de traversée
    unsafe = tmp_path.parent.parent / "evil"
    assert not unsafe.exists()
    # Le fichier a bien été créé sous LOGS_DIR/codeagent/
    codeagent_dir = tmp_path / "codeagent"
    assert codeagent_dir.exists()
    # Un sous-dossier au nom cleané existe
    subdirs = [d for d in codeagent_dir.iterdir() if d.is_dir()]
    assert len(subdirs) >= 1


def test_save_and_truncate_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("LUMENA_TRUNCATION_SAVE", "false")
    import importlib
    import src.utils.paths as paths
    import src.config.codeagent_flags as cf
    importlib.reload(paths)
    importlib.reload(cf)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    big = "x" * 20000
    out = ts.save_and_truncate(big, task_id="t", iteration=1, threshold=8000)
    assert out == big
    assert not (tmp_path / "codeagent").exists()


def test_save_and_truncate_failsafe_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    # Non-str input → return as-is
    out = ts.save_and_truncate(None, task_id="t", iteration=1, threshold=100)  # type: ignore[arg-type]
    assert out is None


def test_save_and_truncate_empty_task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    big = "x" * 20000
    ts.save_and_truncate(big, task_id="", iteration=0, threshold=100)
    assert (tmp_path / "codeagent" / "unknown" / "obs_0000.txt").exists()


def test_sub_agent_imports_save_and_truncate():
    """Le wire-up doit être présent dans sub_agent.py."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "from src.utils.truncation_save import save_and_truncate" in content
    assert "save_and_truncate(" in content


def test_preserves_head_and_tail_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    import importlib
    import src.utils.paths as paths
    importlib.reload(paths)
    import src.utils.truncation_save as ts
    importlib.reload(ts)

    text = "HEADX" + ("m" * 10000) + "TAILY"
    out = ts.save_and_truncate(
        text, task_id="t", iteration=1,
        threshold=100, head_chars=5, tail_chars=5,
    )
    assert out.startswith("HEADX")
    assert out.endswith("TAILY")
    assert "HEADX" in out[:10]
    assert "TAILY" in out[-10:]
