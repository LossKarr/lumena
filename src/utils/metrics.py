"""P10 — Observabilité CodeAgent.

Écrit des métriques structurées (JSONL) à chaque tâche CodeAgent :
- task_id, model_name, attempt, iterations, success, status_code, duration_s
- Path : `<LOGS_DIR>/codeagent/metrics.jsonl`

Gardé par flag LUMENA_CODING_METRICS. Best-effort, fail-safe.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


def record_task_metrics(
    *,
    task_id: str,
    model_name: str,
    attempt: int,
    iterations: int,
    success: bool,
    status_code: str,
    duration_s: float,
    extra: dict[str, Any] | None = None,
) -> None:
    """Ajoute une ligne JSON au fichier metrics.jsonl (+ snapshot métriques gate).

    No-op si le flag CODING_METRICS est désactivé ou en cas d'erreur I/O.
    """
    try:
        from src.config.codeagent_flags import CODING_METRICS
        if not CODING_METRICS:
            return
        # Enrichir avec les métriques gate (P7)
        if extra is None:
            extra = {}
        try:
            from src.utils.gate_metrics import get_summary
            gate_summary = get_summary()
            extra.setdefault("gate_pass_rate", gate_summary.get("gate_pass_rate"))
            extra.setdefault("gate_retry_count", gate_summary.get("gate_retry_count"))
            extra.setdefault("wrong_workspace_count", gate_summary.get("wrong_workspace_context_count"))
            extra.setdefault("lsp_fail_open_count", gate_summary.get("lsp_fail_open_count"))
        except Exception:
            pass
        from src.utils.paths import LOGS_DIR
        metrics_dir = LOGS_DIR / "codeagent"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_file = metrics_dir / "metrics.jsonl"

        entry: dict[str, Any] = {
            "ts": time.time(),
            "task_id": str(task_id)[:120],
            "model": str(model_name)[:80],
            "attempt": int(attempt),
            "iterations": int(iterations),
            "success": bool(success),
            "status": str(status_code),
            "duration_s": round(float(duration_s), 3),
        }
        if extra:
            for k, v in extra.items():
                if isinstance(v, (str, int, float, bool)) and k not in entry:
                    entry[k] = v

        with metrics_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("[metrics] record failed: {}", exc)


def read_recent_metrics(limit: int = 100) -> list[dict[str, Any]]:
    """Lit les N dernières entrées (best-effort, pour UI/debug)."""
    try:
        from src.utils.paths import LOGS_DIR
        metrics_file = LOGS_DIR / "codeagent" / "metrics.jsonl"
        if not metrics_file.exists():
            return []
        lines = metrics_file.read_text(encoding="utf-8", errors="ignore").splitlines()
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


__all__ = ["record_task_metrics", "read_recent_metrics"]
