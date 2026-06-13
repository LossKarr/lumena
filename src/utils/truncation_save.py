"""P4 — Truncation save.

Quand une observation dépasse un seuil, on sauvegarde l'intégralité dans
`<LOGS_DIR>/codeagent/<task_id>/obs_<iter>.txt` et on ne réinjecte qu'un
résumé compact (head + tail + chemin complet) dans le contexte LLM.

Objectif: économiser les tokens tout en permettant au LLM de read_file le
log complet si besoin.

Gardé par flag LUMENA_TRUNCATION_SAVE (opt-OUT).
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.utils.paths import LOGS_DIR


def _safe_task_id(task_id: str) -> str:
    """Nettoie un task_id pour usage filesystem (alnum + - + _)."""
    if not task_id:
        return "unknown"
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(task_id))
    return cleaned[:80] or "unknown"


def save_and_truncate(
    text: str,
    *,
    task_id: str,
    iteration: int,
    threshold: int = 8000,
    head_chars: int = 3000,
    tail_chars: int = 1500,
) -> str:
    """Sauvegarde `text` complet sur disque si > threshold, renvoie un résumé.

    Si flag off, ou texte trop court, ou échec I/O, renvoie `text` tel quel
    (best-effort, fail-safe).
    """
    from src.config.codeagent_flags import TRUNCATION_SAVE
    if not TRUNCATION_SAVE:
        return text
    if not isinstance(text, str) or len(text) <= threshold:
        return text

    try:
        target_dir = LOGS_DIR / "codeagent" / _safe_task_id(task_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"obs_{int(iteration):04d}.txt"
        target.write_text(text, encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — fail-safe
        logger.debug("[truncation_save] écriture échouée: {}", exc)
        return text

    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    total = len(text)
    dropped = total - len(head) - len(tail)
    try:
        rel = target.as_posix()
    except Exception:  # noqa: BLE001
        rel = str(target)

    marker = (
        f"\n\n[... {dropped} chars tronqués / chars omis ...]\n"
        f"OBS_FULL_PATH={rel}\n"
        f"OBS_FULL_CHARS={total}\n"
        f"Pour consulter l'integralite si necessaire: read_file(path=\"{rel}\").\n\n"
    )
    return head + marker + tail


__all__ = ["save_and_truncate"]
