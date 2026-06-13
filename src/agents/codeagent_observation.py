"""
Observations compactes et classees pour CodeAgent.

Objectif: le modele recoit une observation courte et fiable, tandis que les
sorties longues restent disponibles dans les logs via `truncation_save`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ObservationStatus = Literal["success", "warning", "blocked", "error", "timeout"]

BLOCKED_MARKERS = (
    "sous-commande bloquee",
    "sous-commande bloquée",
    "commande bloquee",
    "commande bloquée",
    "bloque par la whitelist",
    "bloqué par la whitelist",
    "non autorise",
    "non autorisé",
    "interdit",
    "policy denied",
    "permission denied",
    "access denied",
)

ERROR_MARKERS = (
    "traceback",
    "syntaxerror",
    "nameerror",
    "typeerror",
    "attributeerror",
    "keyerror",
    "module not found",
    "no module named",
    "failed",
    "failure",
    "error:",
    "erreur",
    "❌",
)

TIMEOUT_MARKERS = ("timeout", "timed out", "delai depasse", "délai dépassé")

WARNING_MARKERS = ("warning", "⚠", "deprecated", "déprécié", "deprecie")


@dataclass(frozen=True)
class ObservationView:
    status: ObservationStatus
    text: str

    @property
    def failed(self) -> bool:
        return self.status in ("blocked", "error", "timeout")


def classify_observation(text: str) -> ObservationStatus:
    lower = (text or "").lower()
    if any(marker in lower for marker in TIMEOUT_MARKERS):
        return "timeout"
    if any(marker in lower for marker in BLOCKED_MARKERS):
        return "blocked"
    if any(marker in lower for marker in ERROR_MARKERS):
        return "error"
    if any(marker in lower for marker in WARNING_MARKERS):
        return "warning"
    return "success"


def compact_observation(
    text: str,
    *,
    task_id: str = "",
    iteration: int = 0,
    action_type: str = "",
    threshold: int | None = None,
) -> ObservationView:
    """Classe et tronque une observation avant reinjection dans le prompt."""
    original = text or ""
    status = classify_observation(original)
    limit = threshold if threshold is not None else _threshold_for(action_type, status)

    compact = original
    try:
        from src.config.codeagent_flags import CODEAGENT_OBSERVATION_COMPACT
        if CODEAGENT_OBSERVATION_COMPACT:
            from src.utils.truncation_save import save_and_truncate
            compact = save_and_truncate(
                original,
                task_id=task_id or "codeagent",
                iteration=max(int(iteration or 0), 0),
                threshold=limit,
                head_chars=max(900, limit * 3 // 5),
                tail_chars=max(500, limit // 4),
            )
    except Exception:
        compact = _head_tail(original, limit)

    if len(compact) > limit * 2:
        compact = _head_tail(compact, limit)

    if status in ("blocked", "error", "timeout"):
        prefix = f"STATUT_OUTIL={status.upper()} — traite cette observation comme un echec a corriger.\n"
        if not compact.startswith("STATUT_OUTIL="):
            compact = prefix + compact

    return ObservationView(status=status, text=compact)


def _threshold_for(action_type: str, status: ObservationStatus) -> int:
    if status in ("blocked", "error", "timeout"):
        return 9000
    if action_type in ("read_file", "read_files_batch"):
        return 14000
    if action_type in ("grep", "run_command", "run_tests", "lint"):
        return 7000
    return 10000


def _head_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: max(1, limit * 3 // 5)]
    tail = text[-max(1, limit // 4):]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n[... {omitted} chars omis ...]\n\n{tail}"


__all__ = ["ObservationView", "classify_observation", "compact_observation"]
