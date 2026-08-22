"""C3-shadow — Mémoire courte des suggestions de délégation (observabilité).

En mode `shadow`, Lumena PROPOSE des délégations sans jamais agir. Ces
propositions sont poussées en temps réel (SSE), donc invisibles si elles ont été
émises AVANT l'ouverture du panneau. Pour que l'utilisateur puisse réellement
OBSERVER ce que Lumena déciderait, on conserve les N dernières dans un fichier
dédié `data/peer_suggestions.json` (ring buffer borné).

Lecture seule pour l'UI — ce store n'influence JAMAIS une décision ou une
exécution. C'est un journal d'observation, pas un substrat de décision.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import List

from src.utils.paths import DATA_DIR

_FILE = DATA_DIR / "peer_suggestions.json"
_LOCK = threading.Lock()
_MAX = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> List[dict]:
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save(rows: List[dict]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)
    except Exception:
        pass


def record(prop: dict) -> None:
    """Ajoute une suggestion shadow en tête (ring buffer borné à `_MAX`)."""
    if not isinstance(prop, dict):
        return
    row = {
        "at": _now(),
        "objective": str(prop.get("objective", ""))[:300],
        "peer_id": str(prop.get("peer_id", "")),
        "peer_name": str(prop.get("peer_name", "")),
        "reason": str(prop.get("reason", ""))[:300],
        "score": prop.get("score", 0),
    }
    with _LOCK:
        rows = _load()
        rows.insert(0, row)
        _save(rows[:_MAX])


def recent(limit: int = 20) -> List[dict]:
    """Les `limit` suggestions les plus récentes (plus récente en tête)."""
    limit = max(1, min(_MAX, int(limit)))
    with _LOCK:
        return _load()[:limit]


def clear_for_tests() -> None:
    with _LOCK:
        _save([])
