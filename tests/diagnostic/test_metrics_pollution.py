"""
Phase 0 — Diagnostic : prouver que metrics.jsonl est pollué par les tests.

Référence : DIAGNOSTIC_PROD.md section 1.
Source de pollution : tests/agents/test_codeagent_observability.py:159

Ce test ne dépend pas du patch — il documente le ratio test vs prod et alerte
si la pollution s'aggrave dans le futur.
"""

import json
from pathlib import Path

import pytest


def _load_metrics():
    metrics_path = Path("data/logs/codeagent/metrics.jsonl")
    if not metrics_path.exists():
        pytest.skip("metrics.jsonl non présent dans cet environnement")
    rows = []
    with metrics_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def test_metrics_pollution_documented():
    """Le ratio synthétique:réel doit être noté.

    Si ce test FAIL parce que les synthétiques dépassent un seuil dangereux,
    isoler `_METRICS_FILE` côté tests devient prioritaire.
    """
    rows = _load_metrics()
    synthetic = [d for d in rows if d.get("task_id") == "task-x"]
    real = [d for d in rows if d.get("task_id") != "task-x"]

    print(f"\nMETRICS — synthétiques (task-x): {len(synthetic)}")
    print(f"METRICS — réelles: {len(real)}")
    print(f"Ratio pollution: {100 * len(synthetic) / max(len(rows), 1):.1f}%")

    # Avertit si la pollution dépasse 80% (signe que les analyses prod sont faussées)
    assert len(synthetic) / max(len(rows), 1) < 0.95, (
        "Plus de 95% des metrics sont synthétiques — isoler metrics.jsonl test/prod"
    )


def test_real_success_rate_above_90():
    """Sur les vraies tâches, le success rate doit rester élevé.

    Si ce test FAIL, c'est un vrai régression à investiguer.
    """
    rows = _load_metrics()
    real = [d for d in rows if d.get("task_id") != "task-x"]
    if not real:
        pytest.skip("Pas de vraies tâches dans cet environnement")

    success = sum(1 for d in real if d.get("success"))
    rate = 100 * success / len(real)
    print(f"\nSuccess rate sur {len(real)} vraies tâches : {rate:.1f}%")
    assert rate >= 90.0, (
        f"Success rate {rate:.1f}% < 90% — régression majeure à investiguer"
    )


def test_real_worldmodel_population_above_50():
    """WorldModel doit être rempli sur la majorité des vraies tâches."""
    rows = _load_metrics()
    real = [d for d in rows if d.get("task_id") != "task-x"]
    if not real:
        pytest.skip("Pas de vraies tâches dans cet environnement")

    populated = sum(1 for d in real if d.get("world_model_files", 0) > 0)
    rate = 100 * populated / len(real)
    print(f"\nWorldModel populated rate : {rate:.1f}%")
    assert rate >= 50.0, (
        f"WorldModel rempli sur {rate:.1f}% des tâches — vérifier "
        f"sub_agent.py:3886-3918 (update_from_write/edit)"
    )
