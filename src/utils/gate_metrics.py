"""
Métriques de la Verification Gate et du workspace isolation.

Compteurs thread-safe persistés en JSONL (même fichier que metrics.py).
Cinq compteurs P0 :
  gate_pass_rate          — ratio passes/(passes+fails)
  gate_retry_count        — nombre total de retries déclenchés par la gate
  wrong_workspace_count   — tentatives bloquées (mauvais workspace)
  rollback_count          — rollbacks effectués après échec gate
  lsp_fail_open_count     — fois où LSP a échoué et qu'on a continué sans lui
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()

# Compteurs en mémoire (reset à chaque démarrage)
_counters: dict[str, int | float] = {
    "gate_pass": 0,
    "gate_fail": 0,
    "gate_retry": 0,
    "wrong_workspace": 0,
    "rollback": 0,
    "lsp_fail_open": 0,
}


def _is_test_env() -> bool:
    """Détecte si on tourne sous pytest (PYTEST_CURRENT_TEST ou LUMENA_ENV=test)."""
    import os
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("LUMENA_ENV", "").lower() == "test"
    )


def _metrics_file() -> Path | None:
    try:
        from src.utils.paths import LOGS_DIR
        # Fichier séparé en environnement de test — évite de polluer les métriques prod.
        suffix = "_test" if _is_test_env() else ""
        p = LOGS_DIR / "codeagent" / f"gate_metrics{suffix}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _append(entry: dict[str, Any]) -> None:
    f = _metrics_file()
    if f is None:
        return
    try:
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── API publique ──────────────────────────────────────────────────────────────

def record_gate_pass(task_id: str = "") -> None:
    with _lock:
        _counters["gate_pass"] += 1
    _append({"ts": time.time(), "event": "gate_pass", "task_id": task_id})


def record_gate_fail(task_id: str = "", reason: str = "") -> None:
    with _lock:
        _counters["gate_fail"] += 1
    _append({"ts": time.time(), "event": "gate_fail", "task_id": task_id, "reason": reason})


def record_gate_retry(task_id: str = "") -> None:
    with _lock:
        _counters["gate_retry"] += 1
    _append({"ts": time.time(), "event": "gate_retry", "task_id": task_id})


def record_wrong_workspace(task_id: str = "", attempted: str = "") -> None:
    with _lock:
        _counters["wrong_workspace"] += 1
    _append({"ts": time.time(), "event": "wrong_workspace", "task_id": task_id, "attempted": attempted})


def record_rollback(task_id: str = "") -> None:
    with _lock:
        _counters["rollback"] += 1
    _append({"ts": time.time(), "event": "rollback", "task_id": task_id})


def record_lsp_fail_open(task_id: str = "", error: str = "") -> None:
    with _lock:
        _counters["lsp_fail_open"] += 1
    _append({"ts": time.time(), "event": "lsp_fail_open", "task_id": task_id, "error": error})


def get_summary() -> dict[str, Any]:
    with _lock:
        passes = _counters["gate_pass"]
        fails = _counters["gate_fail"]
        total = passes + fails
        rate = round(passes / total, 4) if total > 0 else None
        return {
            "gate_pass_rate": rate,
            "gate_pass": passes,
            "gate_fail": fails,
            "gate_retry_count": _counters["gate_retry"],
            "wrong_workspace_context_count": _counters["wrong_workspace"],
            "rollback_count": _counters["rollback"],
            "lsp_fail_open_count": _counters["lsp_fail_open"],
        }


def read_recent_gate_events(limit: int = 200) -> list[dict[str, Any]]:
    f = _metrics_file()
    if f is None or not f.exists():
        return []
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


__all__ = [
    "record_gate_pass",
    "record_gate_fail",
    "record_gate_retry",
    "record_wrong_workspace",
    "record_rollback",
    "record_lsp_fail_open",
    "get_summary",
    "read_recent_gate_events",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# ──────────────────────────────────────────────────────────────────────────────
