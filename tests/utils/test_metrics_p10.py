"""Tests P10 — metrics observability."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def _reset_logs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    monkeypatch.delenv("LUMENA_CODING_METRICS", raising=False)
    # FIX Phase 0 : depuis l'isolation test/prod (src/utils/metrics.py),
    # sous pytest les métriques vont par défaut dans metrics_test.jsonl.
    # Ces tests historiques attendent metrics.jsonl — on utilise l'override
    # explicite LUMENA_METRICS_FILE pour cibler le fichier voulu.
    monkeypatch.setenv("LUMENA_METRICS_FILE", str(tmp_path / "codeagent" / "metrics.jsonl"))
    import src.config.codeagent_flags as cf
    import src.utils.paths as paths
    importlib.reload(cf)
    importlib.reload(paths)
    import src.utils.metrics as m
    importlib.reload(m)
    yield tmp_path
    importlib.reload(cf)
    importlib.reload(paths)
    importlib.reload(m)


def test_record_task_metrics_writes_jsonl(_reset_logs_dir):
    from src.utils.metrics import record_task_metrics, read_recent_metrics

    record_task_metrics(
        task_id="task-001", model_name="deepseek-v3",
        attempt=1, iterations=5, success=True,
        status_code="SUCCESS", duration_s=12.34,
    )
    metrics_file = _reset_logs_dir / "codeagent" / "metrics.jsonl"
    assert metrics_file.exists()

    content = metrics_file.read_text(encoding="utf-8").strip()
    entry = json.loads(content)
    assert entry["task_id"] == "task-001"
    assert entry["model"] == "deepseek-v3"
    assert entry["iterations"] == 5
    assert entry["success"] is True
    assert entry["status"] == "SUCCESS"
    assert entry["duration_s"] == 12.34


def test_record_metrics_appends(_reset_logs_dir):
    from src.utils.metrics import record_task_metrics

    for i in range(3):
        record_task_metrics(
            task_id=f"t{i}", model_name="claude", attempt=1,
            iterations=i, success=(i % 2 == 0),
            status_code="OK", duration_s=1.0,
        )
    metrics_file = _reset_logs_dir / "codeagent" / "metrics.jsonl"
    lines = metrics_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_read_recent_metrics(_reset_logs_dir):
    from src.utils.metrics import record_task_metrics, read_recent_metrics

    for i in range(5):
        record_task_metrics(
            task_id=f"t{i}", model_name="gpt-5", attempt=1,
            iterations=i, success=True, status_code="OK", duration_s=0.5,
        )
    recent = read_recent_metrics(limit=3)
    assert len(recent) == 3
    # Les 3 derniers
    assert [r["task_id"] for r in recent] == ["t2", "t3", "t4"]


def test_flag_off_skips_write(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("LUMENA_CODING_METRICS", "false")
    import src.config.codeagent_flags as cf
    import src.utils.paths as paths
    importlib.reload(cf)
    importlib.reload(paths)
    import src.utils.metrics as m
    importlib.reload(m)

    m.record_task_metrics(
        task_id="t", model_name="x", attempt=1, iterations=1,
        success=True, status_code="OK", duration_s=1.0,
    )
    assert not (tmp_path / "codeagent" / "metrics.jsonl").exists()
    importlib.reload(cf)


def test_record_metrics_with_extra(_reset_logs_dir):
    from src.utils.metrics import record_task_metrics

    record_task_metrics(
        task_id="tx", model_name="x", attempt=2, iterations=10,
        success=False, status_code="PARTIAL", duration_s=9.9,
        extra={"stuck": True, "custom_field": "value"},
    )
    metrics_file = _reset_logs_dir / "codeagent" / "metrics.jsonl"
    entry = json.loads(metrics_file.read_text(encoding="utf-8").strip())
    assert entry["stuck"] is True
    assert entry["custom_field"] == "value"


def test_finalize_metrics_hook_present():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "_finalize_metrics" in content
    assert "from src.utils.metrics import record_task_metrics" in content
