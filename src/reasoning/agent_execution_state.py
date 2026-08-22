"""
AgentExecutionState — État d'exécution structuré de la boucle ReAct.

Regroupe les ~30 attributs privés dispersés dans ReActLoop en dataclasses
catégorisées, avec reset() et snapshot().

V1 : extraction d'état, pas de changement fonctionnel.
Les compteurs/streaks sont mutable (pas frozen) car incrémentés en boucle.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Guards / Streaks ─────────────────────────────────────────────────────────

@dataclass
class LoopGuards:
    """Compteurs de détection de boucles, stagnation, et répétitions."""

    # Détection d'actions identiques consécutives
    consecutive_same_action: int = 0
    last_action_signature: Optional[Any] = None

    # Guidance injectée dans la prochaine observation
    pending_loop_guidance: Optional[str] = None

    # Max 1 auto-avancement par itération
    last_auto_advance_iter: int = -1

    # Anti-aveuglement browser
    last_browser_visual_iter: int = -1
    browser_blind_streak: int = 0
    last_browser_surface: Optional[str] = None
    last_browser_surface_reason: str = ""
    browser_surface_streak: int = 0
    last_browser_progress_sig: Optional[tuple] = None
    browser_no_progress_streak: int = 0

    # LOT 2.11.C/D — preview LOCALE servie par Lumena : inspection visuelle
    # répétée (screenshot/dom_state) sans progrès. Les outils VISUELS ne comptent
    # pas dans browser_no_progress_streak (réservé aux vraies actions) → sur un
    # jeu/preview local, screenshot en boucle ne déclenchait AUCUN stop (run memo).
    local_preview_blind_streak: int = 0
    local_preview_evaluate_asked: bool = False
    # LOT 2.12.D — une assertion `browser_evaluate` a-t-elle PROUVÉ l'interactif
    # (état JS réel : compteur/score/DOM) sur la preview locale ? Sans cette preuve,
    # un FINAL qui affirme « jeu démarré / serpent redirigé » (run snake) fabrique.
    local_preview_interaction_proven: bool = False
    # M106: a click alone is not proof. Keep the last DOM-read fingerprint and
    # require a successful user action followed by a different local DOM read.
    local_preview_last_read_fingerprint: str = ""
    local_preview_mutation_since_read: bool = False
    # LOT Z23 — l'interactif a-t-il été jugé NON PROUVABLE sur cette preview ?
    # Constat acquis et définitif : il ferme la boucle d'inspection (sans quoi on
    # retombe sur le rebouclage infini du run memo) SANS terminer la mission.
    # Avant Z23, ce constat faisait `return` : le run entier mourait avec lui.
    local_preview_interaction_unprovable: bool = False

    # Stagnation par thoughts répétés
    stagnation_streak: int = 0
    exploratory_since_productive: int = 0

    # Post-édition : read-only loop après des écritures
    post_edit_read_streak: int = 0
    redundant_read_streak: int = 0
    last_read_sig: Optional[tuple] = None
    has_done_edits: bool = False

    # Pré-édition : boucles de lecture avant le premier edit
    pre_edit_redundant_streak: int = 0
    pre_edit_last_sig: Optional[tuple] = None

    # Browser fail streak (local à _run_internal, mais logique de guard)
    browser_fail_streak: int = 0
    web_fetch_fail_streak: int = 0

    # Read file tracking
    read_file_path_counter: Dict[str, int] = field(default_factory=dict)
    read_file_ranges_seen: Dict[str, set] = field(default_factory=dict)
    read_file_reread_counter: Dict[str, int] = field(default_factory=dict)

    # Listed dirs tracking
    listed_dirs: Set[str] = field(default_factory=set)

    # Repeated read detection
    last_read_signature: Optional[tuple] = None
    repeated_read_count: int = 0

    # Thoughts history for stagnation detection
    previous_thoughts: List[str] = field(default_factory=list)

    # Web writes counter
    web_writes_count: int = 0


# ── Repair Tracking ──────────────────────────────────────────────────────────

@dataclass
class RepairTracking:
    """Compteurs de tentatives de réparation."""

    final_repair_attempts: int = 0
    hallucination_repair_attempts: int = 0
    thought_leak_repairs: int = 0
    premature_final_retries: int = 0
    plan_guard_retries: int = 0
    verbalization_redirects: int = 0
    action_inline_count: int = 0

    # Ledger FINAL guard (V1)
    ledger_final_guard_used: bool = False

    # Réponse sauvegardée pour rollback après repair
    pre_repair_answer: Optional[str] = None

    # Post-delegate : skip repairs
    after_delegate_success: bool = False


# ── Budget / Category Tracking ───────────────────────────────────────────────

@dataclass
class CategoryBudget:
    """Budgets d'itérations par catégorie d'outil."""

    iter_counts: Dict[str, int] = field(default_factory=dict)


# ── Run Meta ─────────────────────────────────────────────────────────────────

@dataclass
class RunMeta:
    """Métadonnées de sortie du run (exposées via get_run_meta)."""

    agent_output_incomplete: bool = False
    agent_output_warning: Optional[str] = None
    agent_repair_attempts: int = 0
    agent_final_finish_reason: Optional[str] = None

    _FIELDS = ("agent_output_incomplete", "agent_output_warning",
                "agent_repair_attempts", "agent_final_finish_reason")

    def to_dict(self) -> Dict[str, Any]:
        return {f: getattr(self, f) for f in self._FIELDS}


class RunMetaProxy(dict):
    """Proxy dict qui lit/écrit directement les champs de RunMeta.

    Permet au code existant de faire :
        self._run_meta["agent_output_incomplete"] = True
    tout en stockant l'état dans la dataclass structurée.

    À retirer quand tous les consommateurs seront migrés vers exec_state.run_meta.
    """

    def __init__(self, run_meta: RunMeta):
        super().__init__()
        self._rm = run_meta

    def __getitem__(self, key: str) -> Any:
        if key in RunMeta._FIELDS:
            return getattr(self._rm, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in RunMeta._FIELDS:
            setattr(self._rm, key, value)
        else:
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return key in RunMeta._FIELDS

    def get(self, key: str, default: Any = None) -> Any:
        if key in RunMeta._FIELDS:
            return getattr(self._rm, key)
        return default

    def __repr__(self) -> str:
        return repr(self._rm.to_dict())

    def items(self):
        return self._rm.to_dict().items()

    def keys(self):
        return self._rm.to_dict().keys()

    def values(self):
        return self._rm.to_dict().values()

    def __iter__(self):
        return iter(RunMeta._FIELDS)

    def __len__(self):
        return len(RunMeta._FIELDS)


# ── Composite ────────────────────────────────────────────────────────────────

@dataclass
class AgentExecutionState:
    """État d'exécution complet de la boucle ReAct.

    Regroupe guards, repairs, budgets, run_meta, et outils utilisés.
    """

    guards: LoopGuards = field(default_factory=LoopGuards)
    repairs: RepairTracking = field(default_factory=RepairTracking)
    budget: CategoryBudget = field(default_factory=CategoryBudget)
    run_meta: RunMeta = field(default_factory=RunMeta)

    # Accumule TOUS les outils appelés dans la session (survit aux compactions)
    all_session_tools: Set[str] = field(default_factory=set)

    # Accumule uniquement les outils dont l'observation.success est True
    # Utilisé par le guard anti-hallucination comme preuve réelle d'exécution
    successful_session_tools: Set[str] = field(default_factory=set)

    # Dernière meta LLM
    last_llm_meta: Dict[str, Any] = field(default_factory=dict)

    def reset(self) -> None:
        """Réinitialise l'état pour un nouveau run.

        Appelé au début de _run_internal pour éviter la contamination
        entre runs successifs.
        """
        self.guards = LoopGuards()
        self.repairs = RepairTracking()
        self.budget = CategoryBudget()
        self.run_meta = RunMeta()
        self.last_llm_meta = {}
        # NOTE: all_session_tools et successful_session_tools ne sont PAS reset ici
        # car ils survivent aux compactions (comportement existant).

    def snapshot(self) -> Dict[str, Any]:
        """Vue sérialisable complète pour debug / télémétrie / tests."""
        return {
            "guards": asdict(self.guards),
            "repairs": asdict(self.repairs),
            "budget": asdict(self.budget),
            "run_meta": self.run_meta.to_dict(),
            "all_session_tools": sorted(self.all_session_tools),
            "successful_session_tools": sorted(self.successful_session_tools),
            "last_llm_meta": dict(self.last_llm_meta),
        }


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
