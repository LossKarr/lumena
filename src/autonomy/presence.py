"""Présence utilisateur partagée (process-agnostique).

Le daemon d'autonomie garde la présence en mémoire (`user_present`), mais d'autres
boucles (ex. l'autonomie réseau P2P, qui tourne dans SA propre boucle) en ont aussi
besoin. On la matérialise donc par un horodatage fichier `data/last_user_activity` :

- `mark_user_activity()` est appelé à CHAQUE interaction (chat web, daemon, Telegram…) ;
- `is_user_present(idle_min)` répond True si l'utilisateur a agi depuis < idle_min.

Best-effort, jamais bloquant ni fatal. Si l'horodatage est absent (aucune
interaction depuis le boot) → l'utilisateur est considéré ABSENT.
"""
from __future__ import annotations

import time

from src.utils.paths import DATA_DIR

_FILE = DATA_DIR / "last_user_activity"
_DEFAULT_IDLE_MIN = 10


def mark_user_activity() -> None:
    """Stampe l'instant de la dernière interaction utilisateur (epoch secondes)."""
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def seconds_since_activity() -> float | None:
    """Secondes écoulées depuis la dernière activité, ou None si inconnu."""
    try:
        ts = float(_FILE.read_text(encoding="utf-8").strip())
        return max(0.0, time.time() - ts)
    except Exception:
        return None


def is_user_present(idle_min: float = _DEFAULT_IDLE_MIN) -> bool:
    """True si l'utilisateur a interagi depuis moins de `idle_min` minutes.

    Absence d'horodatage = ABSENT (False) — choix conservateur pour l'autonomie.
    """
    since = seconds_since_activity()
    if since is None:
        return False
    return since < (max(0.5, float(idle_min)) * 60.0)
