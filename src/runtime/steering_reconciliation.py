"""Build bounded work briefs and reconcile steering lifecycle outcomes."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from loguru import logger

from .task_steering_store import TaskSteeringStore

# Etats dans lesquels une tache est consideree en cours de travail.
_ETATS_ACTIFS = {"queued", "running", "waiting_io", "checkpointed"}
# Etats dans lesquels une orientation attend encore quelque chose.
_COMMANDES_ACTIVES = {"pending", "delivered", "incorporated", "partially_applied"}


def clore_travaux_interrompus(orchestrator: Any) -> Dict[str, List[str]]:
    """LOT ORI-3 : au demarrage, un tour Agent ou vocal actif est un tour MORT.

    Le cycle normal est `queued -> running -> checkpointed(xN) -> done`. Si le
    processus meurt entre un checkpoint et sa cloture, la tache reste
    `checkpointed` pour toujours : aucun code ne peut garantir qu'un processus
    tue appelle `mark_done`.

    On ne DEDUIT donc pas qu'un travail est mort (un filtre fonde sur
    l'horodatage se fait contourner des qu'une ecriture rafraichit la tache,
    `task_orchestrator.py:399`) : on le MARQUE mort. L'etat devient la verite, et
    le registre de travail n'a plus rien a interpreter.

    Les MISSIONS sont epargnees : elles reprennent reellement au boot (lot 0.d,
    `reconcile_on_boot`), et leurs workers portent le meme `kind`.
    """
    vide: Dict[str, List[str]] = {"taches": [], "orientations": []}
    if orchestrator is None:
        return vide
    try:
        taches = orchestrator.list_all_tasks(limit=500)
    except Exception as exc:
        logger.debug("[ORI-3] taches illisibles : {}", type(exc).__name__)
        return vide

    store = TaskSteeringStore(orchestrator)
    taches_closes: List[str] = []
    orientations_closes: List[str] = []

    for tache in taches:
        meta: Dict[str, Any] = tache.get("metadata") or {}
        if tache.get("state") not in _ETATS_ACTIFS:
            continue
        if str(meta.get("kind") or "") == "mission":
            continue
        task_id = str(tache.get("task_id") or "")
        if not task_id:
            continue

        # Les orientations d'abord : elles n'ont plus de destinataire.
        for commande in store.list(task_id):
            if commande.get("status") not in _COMMANDES_ACTIVES:
                continue
            try:
                store.transition(
                    task_id, str(commande.get("command_id")), "cancelled",
                    {"reason": "travail interrompu par un redemarrage"},
                )
            except Exception as exc:
                logger.debug("[ORI-3] orientation ignoree : {}", type(exc).__name__)
                continue
            orientations_closes.append(str(commande.get("command_id")))

        # Puis l'etat de la tache : c'est LUI qui fait foi pour le registre.
        try:
            orchestrator.update_state(
                task_id, "cancelled",
                result_summary="travail interrompu par un redemarrage",
            )
        except Exception as exc:
            logger.debug("[ORI-3] etat inchange : {}", type(exc).__name__)
            continue
        taches_closes.append(task_id)

    if taches_closes or orientations_closes:
        logger.info(
            "[ORI-3] {} tour(s) et {} orientation(s) clos : leur travail n'a pas "
            "survecu au redemarrage",
            len(taches_closes), len(orientations_closes),
        )
    return {"taches": taches_closes, "orientations": orientations_closes}


def build_effective_work_brief(orchestrator: Any, task_id: str, command_ids: List[str]) -> str:
    record = orchestrator.get_task(task_id) or {}
    metadata = record.get("metadata") or {}
    objective = str(
        metadata.get("initial_objective")
        or metadata.get("objective")
        or record.get("message_preview")
        or ""
    ).strip()
    commands = {
        item.get("command_id"): item for item in TaskSteeringStore(orchestrator).list(task_id)
    }
    active = [commands[item] for item in command_ids if item in commands]
    lines = [
        "BRIEF DE CONTINUITE DU TRAVAIL",
        f"OBJECTIF INITIAL (immuable): {objective}",
        "REGLE: conserve le plan, les observations, les preuves et tous les acquis conformes.",
        "REGLE: poursuis tout ce que l'utilisateur n'a pas explicitement retire.",
        "ORIENTATIONS A INCORPORER:",
    ]
    for command in active:
        lines.append(f"- [{command.get('sequence')}] {command.get('text') or ''}")
    return "\n".join(lines)


def incorporate_delivered(orchestrator: Any, task_id: str, command_ids: List[str]) -> None:
    store = TaskSteeringStore(orchestrator)
    record = orchestrator.get_task(task_id) or {}
    metadata = record.get("metadata") or {}
    revision = int(metadata.get("objective_revision", 0)) + (1 if command_ids else 0)
    active_constraints = list(metadata.get("active_constraints") or [])
    for command_id in command_ids:
        command = store.get(task_id, command_id)
        if not command or command.get("status") != "delivered":
            continue
        store.transition(task_id, command_id, "incorporated", {"objective_revision": revision})
        if command.get("kind") in {"add_constraint", "reprioritize", "replace_constraint"}:
            active_constraints.append({
                "command_id": command_id,
                "sequence": command.get("sequence"),
                "text": command.get("text"),
            })
    if command_ids:
        orchestrator.set_task_metadata(
            task_id,
            objective_revision=revision,
            active_constraints=active_constraints[-100:],
        )


def reconcile_final_outcomes(orchestrator: Any, task_id: str, answer: str) -> None:
    """Close incorporated items conservatively without claiming proof of effect."""
    store = TaskSteeringStore(orchestrator)
    answer_hash = hashlib.sha256(str(answer or "").encode("utf-8")).hexdigest()[:16]
    for command in store.list(task_id):
        if command.get("status") != "incorporated":
            continue
        store.record_outcome(
            task_id,
            str(command.get("command_id") or ""),
            "partially_applied",
            {"evidence_kind": "final_reconciliation", "answer_hash": answer_hash},
        )
