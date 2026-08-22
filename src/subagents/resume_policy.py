"""Reprise sûre des missions au démarrage (zéro rejeu aveugle).

Principe Lumena 24/7 : on gate le FUTUR, jamais le PRÉSENT. Une mission interrompue
par un crash après une action non-idempotente (mail envoyé, écriture, action distante)
**ne doit JAMAIS être rejouée à l'aveugle**.

Une mission lead interrompue est remise en file dans un mode de récupération borné.
Ce mode inspecte les artefacts/checkpoints existants, répare et vérifie localement,
mais ne peut pas rejouer d'effet externe ambigu. Les workers interrompus restent en
revue : le lead récupéré réconcilie leur travail depuis le disque et leurs résultats.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from loguru import logger

_IN_FLIGHT = {"running", "waiting_io", "checkpointed"}

# Filtre DUR du run de récupération. Il couvre inspection, réparation locale, tests,
# preview locale et publication dans workspace. Aucun mail/paiement/social/P2P/GitHub,
# aucune suppression distante et aucun contrôle d'application n'est exposé.
RECOVERY_ALLOWED_TOOLS = frozenset({
    "read_file", "read_files_batch", "list_directory", "find_files", "grep_search",
    "grep_batch", "open_file", "analyze_document", "read_document",
    "write_file", "edit_file", "apply_patch", "apply_patches", "multi_edit_file",
    "insert_at_anchor", "create_directory",
    "run_command", "run_tests", "test_and_fix", "get_last_test_failure",
    "lint_and_fix", "lsp_check", "lsp_diagnostics",
    "check_web_project", "browser_verify_local_project", "serve_website",
    "start_preview_server", "stop_website_server",
    "mission_status", "mission_result", "list_missions", "publish_mission_workspace",
    "get_time", "parallel_tools", "screenshot",
})


def _recovery_limit() -> int:
    try:
        return max(1, min(5, int(os.getenv("LUMENA_MISSION_AUTO_RECOVERY_MAX", "2"))))
    except (TypeError, ValueError):
        return 2


def is_top_level_mission(record: Dict[str, Any]) -> bool:
    """True pour un lead de mission, jamais pour un worker enfant."""
    meta = (record or {}).get("metadata") or {}
    if meta.get("kind") != "mission" or meta.get("parent_id"):
        return False
    try:
        return int(meta.get("depth") or 1) <= 1
    except (TypeError, ValueError):
        return True


def build_recovery_objective(
    record: Dict[str, Any], *, children: List[Dict[str, Any]] | None = None,
) -> str:
    """Construit le mandat de récupération à partir des preuves persistées."""
    meta = (record or {}).get("metadata") or {}
    workspace = str(meta.get("mission_workspace") or "").strip() or "(non enregistré)"
    objective = str(meta.get("objective") or record.get("message_preview") or "").strip()
    checkpoint = record.get("last_checkpoint") or {}
    artifacts = meta.get("artifacts") or []
    child_rows = []
    for child in children or []:
        child_meta = child.get("metadata") or {}
        child_rows.append({
            "task_id": child.get("task_id"),
            "state": child.get("state"),
            "result": str(child.get("result_summary") or "")[:500],
            "artifacts": list(child_meta.get("artifacts") or [])[:20],
        })
    evidence = {
        "mission_workspace": workspace,
        "artifacts": list(artifacts)[:30],
        "last_checkpoint": checkpoint,
        "children": child_rows,
    }
    return (
        "[RECUPERATION SURE APRES INTERRUPTION]\n"
        "Cette mission a été interrompue par l'arrêt du runtime. Ne recommence PAS "
        "l'objectif depuis zéro et ne répète AUCUNE action externe antérieure. Tout "
        "envoi, paiement, publication distante, suppression distante ou contrôle de "
        "processus a un résultat INCONNU et doit rester non rejoué.\n"
        "Travaille uniquement depuis les preuves persistées : inspecte le dossier de "
        "mission, le contrat, les artefacts, les résultats des workers et les tests. "
        "Répare seulement ce qui manque dans le workspace local, exécute les tests "
        "d'intégration, vérifie localement le web si nécessaire, publie le workspace "
        "local si la preuve est suffisante, puis conclus honnêtement. Ne redélègue pas. "
        "Si une action externe reste ambiguë, signale-la comme non prouvée au lieu de "
        "la rejouer.\n\n"
        f"OBJECTIF D'ORIGINE (contexte uniquement) :\n{objective}\n\n"
        "ETAT PERSISTE :\n"
        f"{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}"
    )


def reconcile_on_boot(orchestrator: Any) -> Dict[str, List[str]]:
    """Applique la règle de reprise sûre sur toutes les tâches au démarrage.

    Retourne `{"requeued": [...], "needs_review": [...]}` (task_ids).
    Une tâche n'est jamais enfilée deux fois pendant ce passage. Jamais fatal.
    """
    requeued: List[str] = []
    flagged: List[str] = []
    recovered: List[str] = []
    try:
        # API existante : renvoie des DICTS (to_dict). `limit` large pour tout balayer.
        tasks = orchestrator.list_all_tasks(limit=1_000_000)
    except Exception as exc:
        logger.debug("[resume] list_all_tasks indisponible: {}", exc)
        return {"requeued": requeued, "needs_review": flagged}

    for rec in tasks:
        state = rec.get("state") if isinstance(rec, dict) else getattr(rec, "state", None)
        metadata = (rec.get("metadata") if isinstance(rec, dict) else getattr(rec, "metadata", {})) or {}
        task_id = (rec.get("task_id") if isinstance(rec, dict) else getattr(rec, "task_id", "")) or ""
        if not task_id:
            continue
        if state == "queued":
            requeued.append(task_id)  # laissé tel quel — la file le reprendra (Lot 2)
        elif state in _IN_FLIGHT and is_top_level_mission(rec):
            try:
                attempts = max(0, int(metadata.get("recovery_attempts") or 0))
            except (TypeError, ValueError):
                attempts = 0
            if attempts >= _recovery_limit():
                if not metadata.get("needs_review"):
                    try:
                        orchestrator.set_task_metadata(
                            task_id,
                            needs_review=True,
                            needs_review_reason=(
                                "reprise automatique épuisée — aucune action ambiguë rejouée"
                            ),
                        )
                        flagged.append(task_id)
                    except Exception as exc:
                        logger.debug("[resume] set_task_metadata({}) échec: {}", task_id, exc)
                continue
            try:
                recovery_meta = {
                    "recovery_required": True,
                    "recovery_attempts": attempts + 1,
                    "recovery_original_state": state,
                    "needs_review": False,
                    "needs_review_reason": "",
                }
                if metadata.get("deadline_ts"):
                    recovery_meta["recovery_original_deadline_ts"] = metadata.get("deadline_ts")
                    # La deadline utilisateur est conservée ci-dessus. La récupération a
                    # son propre timeout borné et ne doit pas être annulée immédiatement
                    # parce que le runtime était éteint au passage de l'échéance.
                    recovery_meta["deadline_ts"] = None
                orchestrator.set_task_metadata(task_id, **recovery_meta)
                orchestrator.update_state(task_id, "queued")
                requeued.append(task_id)
                recovered.append(task_id)
            except Exception as exc:
                logger.debug("[resume] préparation récupération {} échec: {}", task_id, exc)
        elif state in _IN_FLIGHT and not metadata.get("needs_review"):
            try:
                orchestrator.set_task_metadata(
                    task_id,
                    needs_review=True,
                    needs_review_reason=(
                        f"worker/tâche interrompu au reboot (état={state}) — le lead "
                        "réconciliera les preuves, aucun rejeu automatique"
                    ),
                )
                flagged.append(task_id)
            except Exception as exc:
                logger.debug("[resume] set_task_metadata({}) échec: {}", task_id, exc)

    if recovered:
        logger.warning(
            "[resume] {} mission(s) lead préparée(s) pour récupération sûre",
            len(recovered),
        )
    if flagged:
        logger.warning(
            "[resume] {} tâche(s) interrompue(s) → needs_review (zéro rejeu automatique)",
            len(flagged),
        )
    return {"requeued": requeued, "needs_review": flagged}
