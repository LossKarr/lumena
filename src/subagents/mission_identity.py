"""CONN-5D-1 - identite d'execution des missions.

Les missions tournent dans `mission_worker_loop`, demarre au `lifespan` : aucune
requete, donc aucun `RuntimeContext`. Les gardes qui exigent un contexte (provider
IDE : proprietaire) refusaient alors tout appel d'une vraie mission.

L'identite du DEMANDEUR est relevee a la creation depuis le contexte courant (jamais
depuis les arguments du modele), heritee par les workers, puis `run_mission` pose un
contexte construit uniquement depuis elle. Sans demandeur valide : aucun contexte,
comme avant. Un role inconnu ne devient jamais proprietaire.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from src.runtime.context import (
    RuntimeContext, _VALID_ROLES, pop_runtime_context, push_runtime_context,
)

REQUESTER_FIELDS = ("user_role", "user_id", "owner_user_id", "channel")
MISSIONS_CONVERSATION = "__missions__"


def validated_requester(value: Any) -> Optional[dict]:
    """Forme fermee : exactement les quatre champs, chaines non vides, role connu."""
    if type(value) is not dict or set(value) != set(REQUESTER_FIELDS):
        return None
    if any(type(value[key]) is not str or not value[key].strip() for key in REQUESTER_FIELDS):
        return None
    if value["user_role"] not in _VALID_ROLES:
        return None
    return {key: value[key] for key in REQUESTER_FIELDS}


def capture_requester(runtime_context: Any) -> Optional[dict]:
    """Identite du demandeur, relevee du contexte d'execution courant."""
    if runtime_context is None:
        return None
    return validated_requester({key: getattr(runtime_context, key, None) for key in REQUESTER_FIELDS})


def requester_for_worker(orchestrator: Any, lead_id: Any) -> Optional[dict]:
    """Un worker agit pour le demandeur de son lead, et pour personne d'autre."""
    if orchestrator is None or not lead_id:
        return None
    try:
        lead = orchestrator.get_task(lead_id) or {}
    except Exception:
        return None
    return validated_requester((lead.get("metadata") or {}).get("requester"))


def mission_runtime_context(mission_id: str, requester: Any) -> Optional[RuntimeContext]:
    """Contexte d'une mission, sans dossier IDE ni fichier actif."""
    identity = validated_requester(requester)
    if identity is None or not mission_id:
        return None
    return RuntimeContext(
        channel="mission", client="mission-runner", request_id=f"mission-{mission_id}",
        conversation_id=MISSIONS_CONVERSATION, message_id=str(mission_id), mode="agent",
        user_id=identity["user_id"], owner_user_id=identity["owner_user_id"],
        user_role=identity["user_role"], task_id=str(mission_id),
    )


@contextmanager
def mission_runtime_scope(orchestrator: Any, mission_id: str) -> Iterator[Optional[RuntimeContext]]:
    """Pose le contexte de la mission pour la duree du run, puis restaure le precedent."""
    requester = None
    if orchestrator is not None:
        try:
            requester = ((orchestrator.get_task(mission_id) or {}).get("metadata") or {}).get("requester")
        except Exception:
            requester = None
    context = mission_runtime_context(mission_id, requester)
    if context is None:
        yield None
        return
    token = push_runtime_context(context)
    try:
        yield context
    finally:
        pop_runtime_context(token)
