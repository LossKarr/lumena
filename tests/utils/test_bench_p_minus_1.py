"""P-1 — Tests du script de benchmark baseline."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH_SCRIPT = ROOT / "scripts" / "bench_codeagent.py"


def test_bench_script_exists():
    assert BENCH_SCRIPT.exists(), "scripts/bench_codeagent.py manquant"


def test_bench_runs_minimal(tmp_path, monkeypatch):
    """Run bench avec 10 itérations pour valider qu'il tourne end-to-end."""
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    result = subprocess.run(
        [sys.executable, str(BENCH_SCRIPT), "--iter", "10"],
        capture_output=True, text=True, timeout=30, cwd=str(ROOT),
    )
    assert result.returncode == 0, f"Bench a échoué: {result.stderr[:500]}"
    assert "fuzzy_replace" in result.stdout
    assert "truncation_save" in result.stdout


def test_bench_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    subprocess.run(
        [sys.executable, str(BENCH_SCRIPT), "--iter", "10"],
        capture_output=True, text=True, timeout=30, cwd=str(ROOT),
    )
    out_dir = tmp_path / "codeagent"
    reports = list(out_dir.glob("benchmark_*.json"))
    assert reports, "aucun rapport généré"
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert "benches" in data
    for name in ("fuzzy_replace", "env_context", "french_errors",
                 "model_temperature", "action_parsing", "truncation_save"):
        assert name in data["benches"]
        b = data["benches"][name]
        assert "mean_ms" in b and "p95_ms" in b and "median_ms" in b


def test_bench_baseline_created(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_LOGS_DIR", str(tmp_path))
    subprocess.run(
        [sys.executable, str(BENCH_SCRIPT), "--iter", "10"],
        capture_output=True, text=True, timeout=30, cwd=str(ROOT),
    )
    baseline = tmp_path / "codeagent" / "benchmark_baseline.json"
    assert baseline.exists(), "benchmark_baseline.json non créé au premier run"
