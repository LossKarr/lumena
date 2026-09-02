"""Lot 1.3 — MissionManager : crée, lance (en fond) et suit les missions.

Sépare **décision** (Lumena décide via les outils, Lot 3) et **exécution** (le manager
crée la mission dans `TaskOrchestrator` puis lance le runner en arrière-plan). Lumena
ne crée JAMAIS un `asyncio.create_task` directement — elle passe par le manager.

⚠️ Une mission = un sous-agent « Lumena complète » (`runner.run_mission` →
`think_and_act_silent`). Aucun lien avec le **CodeAgent** (`src/agents/sub_agent.py`).

Lot 1 : lancement direct (1 à la fois en pratique). La **file + plafond** = Lot 2.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from loguru import logger

_MISSIONS_CONV = "__missions__"


class MissionManager:
    """Gestionnaire des missions de Lumena (adossé à `TaskOrchestrator`).

    `launch` **met en file** (`asyncio.Queue`) ; l'exécution est faite par le worker
    app-lifetime (`src/subagents/worker.py`), pas par une tâche liée à la requête →
    une mission survit à la fin du tour de chat (correctif Lot 4).
    """

    def __init__(self, core: Any) -> None:
        self.core = core
        self._queue: Optional["asyncio.Queue"] = None
        self._inflight: set = set()   # mission_ids en file ou en cours (idempotence)

    def queue(self) -> "asyncio.Queue":
        """File des missions à exécuter (créée à la demande, sur la boucle courante)."""
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    # ── orchestrateur (source unique de vérité) ────────────────────────────────
    @property
    def _orch(self) -> Any:
        orch = getattr(self.core, "task_orchestrator", None)
        if orch is None:
            raise RuntimeError("MissionManager: TaskOrchestrator indisponible (cf. Lot 0.a).")
        return orch

    # ── création (pur, ne lance pas) ───────────────────────────────────────────
    def create_mission(
        self,
        objective: str,
        *,
        deadline: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Crée une mission persistante (`queued`) et retourne son `mission_id`."""
        meta: Dict[str, Any] = {"kind": "mission", "objective": str(objective)[:2000]}
        if deadline:
            meta["deadline"] = str(deadline)
            # Lot 5.7.1 — point central : toute création de mission normalise l'échéance.
            # On garde le texte brut + un timestamp ISO si parsable (sinon pas d'échéance
            # imposée → comportement identique).
            try:
                from src.subagents.mission_budget import normalize_deadline
                _dts = normalize_deadline(deadline)
                if _dts:
                    meta["deadline_ts"] = _dts
                    # LOT Z32 phase 2 — `deadline_ts` est un ISO NAÏF LOCAL (voir
                    # mission_budget._iso) tandis que `created_at` est en UTC avec
                    # offset. Deux champs du même enregistrement, deux conventions :
                    # les comparer donne 2 heures d'écart. C'est ce qui m'a fait
                    # conclure « 121 minutes » là où le runtime voyait 90 secondes,
                    # et ce qui a masqué le vrai défaut pendant deux runs.
                    #
                    # On AJOUTE un champ sans ambiguïté au lieu de convertir
                    # `deadline_ts` : il est lu par les workers (héritage) et par le
                    # préambule de contrat, et la convention naïve-locale y est
                    # correcte. Le changer casserait le runtime pour réparer un
                    # problème de lecture.
                    try:
                        from datetime import datetime as _dtz32
                        meta["deadline_utc"] = (
                            _dtz32.fromisoformat(_dts).astimezone().isoformat()
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        if allowed_tools:
            meta["allowed_tools"] = list(allowed_tools)
        if metadata:
            meta.update(metadata)
        record = self._orch.start_task(
            conversation_id=_MISSIONS_CONV,
            channel="mission",
            message_preview=str(objective),
            metadata=meta,
        )
        logger.info("[mission] créée {} — {}", record.task_id, str(objective)[:80])
        return record.task_id

    # ── lancement (MISE EN FILE — exécuté par le worker app-lifetime) ──────────
    def launch(
        self,
        mission_id: str,
        objective: str,
        *,
        timeout: float = 600.0,
        allowed_tools: Optional[list] = None,
    ) -> bool:
        """Met la mission EN FILE. Idempotent : ignore si déjà en file/en cours.

        N'exécute PAS ici (plus de `create_task` lié à la requête). Le worker
        (`worker.mission_worker_loop`, app-lifetime) consomme la file → la mission
        survit à la fin du tour de chat. Retourne True si enfilée.
        """
        if mission_id in self._inflight:
            return False
        self._inflight.add(mission_id)
        self.queue().put_nowait((mission_id, objective, timeout, allowed_tools))
        return True

    def create_and_launch(self, objective: str, **kw) -> str:
        """Commodité : crée puis lance. Retourne le `mission_id`."""
        timeout = kw.pop("timeout", 600.0)
        mission_id = self.create_mission(objective, **kw)
        self.launch(mission_id, objective, timeout=timeout,
                    allowed_tools=kw.get("allowed_tools"))
        return mission_id

    # ── lecture / contrôle ─────────────────────────────────────────────────────
    def _runtime_projection(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Ajoute l'activité mémoire sans modifier l'état persistant.

        Un run actif peut être momentanément ``checkpointed`` entre deux
        itérations. Le stockage garde ce checkpoint pour la reprise, tandis que
        le panneau doit savoir que le coroutine tourne encore.
        """
        row = dict(record or {})
        task_id = str(row.get("task_id") or "")
        active = task_id in self._inflight
        if task_id and not active:
            try:
                from src.agents.sub_agent import is_bg_agent_active, is_delegate_active
                active = is_delegate_active(task_id) or is_bg_agent_active(task_id)
            except Exception:
                active = False
        row["runtime_active"] = bool(active)
        return row

    def list_missions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [
            self._runtime_projection(item)
            for item in self._orch.get_conversation_tasks(_MISSIONS_CONV, limit=limit)
        ]

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        item = self._orch.get_task(mission_id)
        return self._runtime_projection(item) if item else None

    def cancel_mission(self, mission_id: str) -> Dict[str, Any]:
        """Annulation coopérative : la mission s'arrête au prochain checkpoint."""
        return self._orch.cancel_task(mission_id)

    def running_count(self) -> int:
        """Missions en file ou en cours (le détail running/queued est dans le registre)."""
        return len(self._inflight)


# ── Singleton + reprise au boot (Lot 2.3) ──────────────────────────────────────
import threading

_manager: Optional[MissionManager] = None
_manager_lock = threading.Lock()


def get_mission_manager(core: Any) -> MissionManager:
    """Singleton du MissionManager, lié au `core` courant (thread-safe)."""
    global _manager
    if _manager is None or _manager.core is not core:
        with _manager_lock:
            if _manager is None or _manager.core is not core:
                _manager = MissionManager(core)
    return _manager


def relaunch_queued(manager: MissionManager) -> List[str]:
    """Au démarrage : relance les missions `queued`.

    Les missions interrompues préparées par `resume_policy` reçoivent un objectif de
    récupération fondé sur les preuves persistées et un filtre d'outils DUR local.
    Les tâches `needs_review` et terminales ne sont jamais relancées.
    Nécessite une boucle asyncio active (appelé au boot async). Jamais fatal.
    """
    relaunched: List[str] = []
    try:
        missions = manager.list_missions(limit=10000)
    except Exception as exc:
        logger.debug("[mission] relaunch_queued: list indisponible: {}", exc)
        return relaunched
    for m in missions:
        if m.get("state") != "queued":
            continue
        meta = m.get("metadata") or {}
        if meta.get("needs_review"):
            continue  # ex-running interrompue → on ne rejoue pas
        objective = meta.get("objective") or m.get("message_preview") or ""
        if not objective:
            continue
        allowed_tools = meta.get("allowed_tools")
        timeout = 600.0
        if meta.get("recovery_required"):
            from src.subagents.resume_policy import (
                RECOVERY_ALLOWED_TOOLS,
                build_recovery_objective,
            )
            children = [
                item for item in missions
                if ((item.get("metadata") or {}).get("parent_id") == m.get("task_id"))
            ]
            objective = build_recovery_objective(m, children=children)
            allowed_tools = sorted(RECOVERY_ALLOWED_TOOLS)
            try:
                timeout = max(120.0, min(
                    1800.0,
                    float(os.getenv("LUMENA_MISSION_RECOVERY_TIMEOUT_S", "900")),
                ))
            except (TypeError, ValueError):
                timeout = 900.0
        try:
            manager.launch(
                m["task_id"], objective, timeout=timeout,
                allowed_tools=allowed_tools,
            )
            relaunched.append(m["task_id"])
        except Exception as exc:
            logger.debug("[mission] relaunch {} échec: {}", m.get("task_id"), exc)
    if relaunched:
        logger.info("[mission] {} mission(s) `queued` relancée(s) au démarrage", len(relaunched))
    return relaunched
