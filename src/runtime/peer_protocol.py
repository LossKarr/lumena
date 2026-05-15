"""Phase 5/6 — Schémas de délégation + audit log inter-instances."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.utils.paths import DATA_DIR

PEER_AUDIT_LOG = DATA_DIR / "peer_audit.jsonl"
_AUDIT_LOCK = threading.Lock()


@dataclass
class DelegateRequest:
    task_id: str
    from_instance_id: str
    from_user_id: str
    actor_id: str
    scope: str
    prompt: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegateResult:
    task_id: str
    status: str          # "completed" | "refused" | "error"
    response: str
    evidence: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)


def write_audit_log(
    *,
    event: str,
    from_instance_id: str,
    task_id: str,
    scope: str,
    status: str,
    detail: str = "",
) -> None:
    """Ajoute une entrée dans data/peer_audit.jsonl.

    Toutes les requêtes inter-Lumena (acceptées ou refusées) sont tracées ici.
    Thread-safe via _AUDIT_LOCK.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "from_instance_id": from_instance_id,
        "task_id": task_id,
        "scope": scope,
        "status": status,
        "detail": detail,
    }
    line = json.dumps(entry, ensure_ascii=False)
    with _AUDIT_LOCK:
        try:
            PEER_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(PEER_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def read_audit_log(limit: int = 200) -> List[dict]:
    """Lit les N dernières entrées de l'audit log."""
    try:
        if not PEER_AUDIT_LOG.exists():
            return []
        lines = PEER_AUDIT_LOG.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines[-limit:] if l.strip()]
    except Exception:
        return []
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
