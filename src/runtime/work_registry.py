"""Read-only work snapshots and deterministic routing for every Lumena surface."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.task_steering import queue_steering


_ACTIVE = {"queued", "running", "waiting_io", "checkpointed"}
# Instant de demarrage du processus. Rien ne reconcilie les taches au
# chargement : un tour coupe par un arret du serveur reste actif pour
# toujours. Ce registre projette ce qui travaille MAINTENANT, donc une tache
# vue pour la derniere fois avant ce demarrage en est exclue. La persistance
# n'est pas reecrite : les missions gardent leur propre reprise au boot.
_PROCESS_START = datetime.now(timezone.utc)
_WORK_KINDS = {"agent_turn", "mission", "voice_turn"}


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()



def _vue_avant_ce_demarrage(task: Dict[str, Any]) -> bool:
    """Vrai si cette tache n'a plus bouge depuis le demarrage du processus.

    Un horodatage absent ou illisible ne prouve rien : on ne suppose alors pas
    la mort du travail et la tache reste visible.
    """
    brut = str(task.get("updated_at") or task.get("created_at") or "")
    if not brut:
        return False
    try:
        vu = datetime.fromisoformat(brut)
    except ValueError:
        return False
    if vu.tzinfo is None:
        vu = vu.replace(tzinfo=timezone.utc)
    return vu < _PROCESS_START


def classify_work_turn(text: str) -> str:
    n = _norm(text)
    if any(x in n for x in ("annule la tache", "annule ce travail", "laisse tomber")):
        return "cancel"
    if any(x in n for x in (
        "mets en pause", "met en pause", "mets la tache en pause",
        "met la tache en pause", "pause la tache", "attends",
    )):
        return "pause"
    if any(x in n for x in ("reprends", "continue la tache", "continue le travail")):
        return "resume"
    if any(x in n for x in (
        "tu en es ou", "ou en est", "est ce fini", "est ce que c est fini",
        "qu est ce qui bloque", "combien de workers", "resultat partiel",
        "les tests passent", "quand penses tu finir", "montre moi ce que tu as",
    )):
        return "status"
    if any(x in n for x in (
        "change plutot", "ajoute aussi", "ne touche plus", "privilegie",
        "priorite a", "fais plutot",
    )):
        return "steer"
    return "conversation"


@dataclass(frozen=True)
class WorkSnapshot:
    task_id: str
    objective: str
    state: str
    updated_at: str
    completed_workers: int
    active_workers: int
    failed_workers: int
    last_phase: str
    artifacts: Tuple[str, ...]
    last_error: str
    result_summary: str
    paused: bool


class ActiveWorkRegistry:
    """A view over TaskOrchestrator; it owns no mutable business state."""

    def __init__(
        self,
        orchestrator: Any,
        conversation_id: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        include_workers: bool = False,
    ):
        self.orchestrator = orchestrator
        self.conversation_id = conversation_id
        self.owner_user_id = owner_user_id
        self.include_workers = include_workers

    def active_ids(self) -> List[str]:
        if self.orchestrator is None:
            return []
        ids: List[str] = []
        for task in self.orchestrator.list_all_tasks(limit=500):
            meta = task.get("metadata") or {}
            if task.get("state") not in _ACTIVE or meta.get("kind") not in _WORK_KINDS:
                continue
            if _vue_avant_ce_demarrage(task):
                continue
            if meta.get("parent_id") and not self.include_workers:
                continue
            requester = meta.get("requester") if isinstance(meta.get("requester"), dict) else {}
            task_owner = str(meta.get("owner_user_id") or requester.get("owner_user_id") or "")
            if self.owner_user_id and task_owner != self.owner_user_id:
                continue
            if self.conversation_id:
                source = str(meta.get("source_conversation_id") or task.get("conversation_id") or "")
                if source and source != self.conversation_id:
                    continue
            ids.append(str(task.get("task_id")))
        return ids

    def resolve(self, preferred_id: Optional[str] = None) -> Tuple[Optional[WorkSnapshot], List[str]]:
        if preferred_id:
            snap = self.snapshot(preferred_id)
            if snap and snap.state in _ACTIVE:
                return snap, []
        ids = self.active_ids()
        if len(ids) == 1:
            return self.snapshot(ids[0]), []
        return None, ids

    def snapshot(self, task_id: str) -> Optional[WorkSnapshot]:
        if self.orchestrator is None:
            return None
        task = self.orchestrator.get_task(task_id)
        if not task:
            return None
        metadata = task.get("metadata") or {}
        children = self.orchestrator.get_children(task_id) if hasattr(self.orchestrator, "get_children") else []
        states = [str(c.get("state") or "") for c in children]
        checkpoint = task.get("last_checkpoint") or {}
        artifacts = metadata.get("artifacts") or []
        return WorkSnapshot(
            task_id=task_id,
            objective=str(metadata.get("objective") or task.get("message_preview") or ""),
            state=str(task.get("state") or "unknown"),
            updated_at=str(task.get("updated_at") or ""),
            completed_workers=sum(s == "done" for s in states),
            active_workers=sum(s in _ACTIVE for s in states),
            failed_workers=sum(s in {"failed", "cancelled"} for s in states),
            last_phase=str(checkpoint.get("phase") or ""),
            artifacts=tuple(str(x) for x in artifacts),
            last_error=str(task.get("last_error") or ""),
            result_summary=str(task.get("result_summary") or ""),
            paused=bool(metadata.get("paused") or metadata.get("pause_requested")),
        )

    def status_text(self, preferred_id: Optional[str] = None) -> str:
        snap, ambiguous = self.resolve(preferred_id)
        if ambiguous:
            return f"J'ai {len(ambiguous)} travaux actifs. Dis-moi lequel tu veux suivre."
        if snap is None:
            return "Je n'ai aucun travail actif pour le moment."
        if snap.paused:
            return "Le travail est en pause au prochain point sur."
        parts = [f"Le travail est {snap.state}."]
        if snap.completed_workers or snap.active_workers or snap.failed_workers:
            parts.append(
                f"Workers: {snap.completed_workers} termines, {snap.active_workers} actifs"
                + (f", {snap.failed_workers} en echec." if snap.failed_workers else ".")
            )
        if snap.last_phase:
            parts.append(f"Derniere phase prouvee: {snap.last_phase}.")
        if snap.last_error:
            parts.append("Un blocage est enregistre a l'ecran.")
        elif snap.artifacts:
            parts.append(f"{len(snap.artifacts)} livrable(s) deja present(s).")
        return " ".join(parts)

    def steer(self, task_id: str, text: str) -> Dict[str, Any]:
        return queue_steering(self.orchestrator, task_id, "add_constraint", {"text": text})

    def pause(self, task_id: str) -> Dict[str, Any]:
        return queue_steering(self.orchestrator, task_id, "pause")

    def resume(self, task_id: str) -> Dict[str, Any]:
        return queue_steering(self.orchestrator, task_id, "resume")


class WorkNotificationTracker:
    """Deduplicates meaningful mission transitions; no periodic narration."""

    def __init__(
        self,
        orchestrator: Any,
        conversation_id: Optional[str] = None,
        source_channel: Optional[str] = "voice",
    ):
        self.orchestrator = orchestrator
        self.conversation_id = conversation_id
        self.source_channel = source_channel
        self._states: Dict[str, str] = {}
        self._prime()

    def _eligible(self, task: Dict[str, Any]) -> bool:
        meta = task.get("metadata") or {}
        if meta.get("kind") != "mission":
            return False
        if self.source_channel and meta.get("source_channel") != self.source_channel:
            return False
        source = meta.get("source_conversation_id")
        return not self.conversation_id or not source or source == self.conversation_id

    def _prime(self) -> None:
        if self.orchestrator is None:
            return
        for task in self.orchestrator.list_all_tasks(limit=500):
            if self._eligible(task):
                self._states[str(task.get("task_id"))] = str(task.get("state") or "")

    def collect(self) -> List[str]:
        if self.orchestrator is None:
            return []
        notices: List[str] = []
        for task in self.orchestrator.list_all_tasks(limit=500):
            if not self._eligible(task):
                continue
            task_id = str(task.get("task_id"))
            state = str(task.get("state") or "")
            previous = self._states.get(task_id)
            self._states[task_id] = state
            if previous is None or previous == state:
                continue
            if state == "done":
                notices.append("La mission est terminée. Son résultat est disponible à l'écran.")
            elif state == "failed":
                notices.append("La mission a rencontré une erreur. Le détail est affiché à l'écran.")
            elif state == "waiting_io":
                notices.append("La mission est bloquée et demande une vérification à l'écran.")
            elif state == "cancelled":
                notices.append("La mission a été annulée.")
        return notices
