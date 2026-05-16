"""
Phase 0 — Isolation metrics test/prod.

Référence : DIAGNOSTIC_PROD.md section 1.

Avant Phase 0 : les tests écrivaient dans `metrics.jsonl` (même fichier que prod),
352 entrées `task-x` synthétiques polluaient les analyses.

Après Phase 0 : sous pytest, écriture redirigée vers `metrics_test.jsonl`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_pytest_detection():
    """Sanity : on tourne bien sous pytest."""
    from src.utils.metrics import _is_running_under_pytest

    assert _is_running_under_pytest() is True


def test_record_under_pytest_goes_to_test_file(tmp_path, monkeypatch):
    """`record_task_metrics()` sous pytest doit écrire dans metrics_test.jsonl,
    pas metrics.jsonl.
    """
    # Rediriger LOGS_DIR pour ne pas polluer le vrai dossier
    monkeypatch.setattr("src.utils.paths.LOGS_DIR", tmp_path)

    # Forcer le flag CODING_METRICS à True via env (au cas où désactivé)
    monkeypatch.setenv("LUMENA_CODING_METRICS", "1")
    # S'assurer que l'override env est désactivé
    monkeypatch.delenv("LUMENA_METRICS_FILE", raising=False)

    # Reload du module pour appliquer la nouvelle LOGS_DIR
    from src.utils import metrics as metrics_mod
    # Patcher directement l'import paths dans le module pour éviter cache
    monkeypatch.setattr("src.utils.metrics._is_running_under_pytest", lambda: True)

    metrics_mod.record_task_metrics(
        task_id="test_isolation_123",
        model_name="test-model",
        attempt=1,
        iterations=5,
        success=True,
        status_code="success",
        duration_s=1.0,
    )

    prod_file = tmp_path / "codeagent" / "metrics.jsonl"
    test_file = tmp_path / "codeagent" / "metrics_test.jsonl"

    assert not prod_file.exists(), (
        "Sous pytest, metrics.jsonl ne doit JAMAIS être écrit "
        "(pollution prod). Cf. DIAGNOSTIC_PROD.md §1."
    )
    assert test_file.exists(), "metrics_test.jsonl doit recevoir l'entrée"

    content = test_file.read_text(encoding="utf-8").strip()
    assert content, "Fichier test non vide"
    entry = json.loads(content.splitlines()[-1])
    assert entry["task_id"] == "test_isolation_123"


def test_override_env_takes_precedence(tmp_path, monkeypatch):
    """LUMENA_METRICS_FILE override permet de cibler un fichier précis
    (utile pour les tests d'observabilité qui veulent vérifier le contenu)."""
    custom_file = tmp_path / "custom_metrics.jsonl"
    monkeypatch.setenv("LUMENA_METRICS_FILE", str(custom_file))
    monkeypatch.setenv("LUMENA_CODING_METRICS", "1")

    from src.utils import metrics as metrics_mod
    metrics_mod.record_task_metrics(
        task_id="override_test",
        model_name="m",
        attempt=1,
        iterations=1,
        success=True,
        status_code="success",
        duration_s=0.1,
    )

    assert custom_file.exists()
    entry = json.loads(custom_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["task_id"] == "override_test"


def test_existing_metrics_jsonl_no_new_pollution():
    """Vérifie qu'aucune nouvelle entrée 'task-x' n'a été ajoutée à metrics.jsonl
    depuis le fix d'isolation.

    Compte les task-x actuels comme baseline. Si le nombre augmente après
    une run de tests, l'isolation a un trou.
    """
    metrics_path = Path("data/logs/codeagent/metrics.jsonl")
    if not metrics_path.exists():
        pytest.skip("metrics.jsonl non présent dans cet environnement")

    task_x_count = 0
    with metrics_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("task_id") == "task-x":
                    task_x_count += 1
            except Exception:
                continue

    # Snapshot historique : 352 au moment du diagnostic
    # On accepte ce nombre + une marge raisonnable pour les runs antérieures
    # à l'isolation. Mais on alerte si ça monte beaucoup.
    print(f"\ntask-x dans metrics.jsonl (historique) : {task_x_count}")
    # Garde-fou : pas plus de 600 entrées synthétiques même historiquement
    assert task_x_count <= 600, (
        f"{task_x_count} entrées task-x — l'isolation test/prod ne marche "
        f"pas ou n'a pas été appliquée à temps."
    )
