"""
task_envelope.py — Contrat d'exécution minimal pour toute tâche autonome.

Garantit qu'aucune mutation autonome ne peut partir sans :
  - origin    : source de la tâche (user | daemon | scheduler | heartbeat | goals)
  - intent    : what the task intends to do
  - workspace : workspace_path résolu (obligatoire si risk_level > low)
  - risk_level: low | medium | high
  - tool_category: catégorie d'outil principale prévue
  - requires_verification: True si confirmation utilisateur requise
  - budget_seconds: durée maximale autorisée

Usage :
    envelope = TaskEnvelope.for_autonomous(
        origin="scheduler",
        intent="archive old projects",
        workspace="/lumena/workspace",
        tool_category="files",
        budget_seconds=300,
    )
    envelope.validate()  # lève EnvelopeViolation si incomplet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

VALID_ORIGINS = frozenset({"user", "daemon", "scheduler", "heartbeat", "goals", "test"})
VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})
# Catégories dont les mutations exigent un workspace résolu
_MUTATING_CATEGORIES = frozenset({
    "files", "project", "git", "github", "agents", "codebase", "documents",
})


# ──────────────────────────────────────────────────────────────────────────────
# Exception
# ──────────────────────────────────────────────────────────────────────────────

class EnvelopeViolation(Exception):
    """Le contrat d'enveloppe n'est pas respecté — tâche bloquée."""


# ──────────────────────────────────────────────────────────────────────────────
# TaskEnvelope
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TaskEnvelope:
    """
    Contrat d'exécution d'une tâche autonome.

    Tous les champs ont un défaut sûr. validate() lève EnvelopeViolation
    si les invariants ne sont pas respectés pour le risk_level donné.
    """

    origin: str = "unknown"
    intent: str = ""
    workspace: Optional[str] = None
    risk_level: str = "low"
    tool_category: str = ""
    requires_verification: bool = False
    budget_seconds: int = 300

    def validate(self) -> None:
        """Vérifie les invariants. Lève EnvelopeViolation si invalide."""
        if self.origin not in VALID_ORIGINS:
            raise EnvelopeViolation(
                f"origin '{self.origin}' invalide. Valeurs autorisées : {sorted(VALID_ORIGINS)}"
            )
        if self.risk_level not in VALID_RISK_LEVELS:
            raise EnvelopeViolation(
                f"risk_level '{self.risk_level}' invalide. Valeurs : {sorted(VALID_RISK_LEVELS)}"
            )
        if not self.intent.strip():
            raise EnvelopeViolation("intent manquant — décrivez ce que la tâche doit faire.")
        if self.budget_seconds <= 0:
            raise EnvelopeViolation(f"budget_seconds doit être > 0, got {self.budget_seconds}")
        # Workspace obligatoire si catégorie mutante et risk > low
        if (
            self.tool_category in _MUTATING_CATEGORIES
            and self.risk_level != "low"
            and not self.workspace
        ):
            raise EnvelopeViolation(
                f"workspace requis pour category='{self.tool_category}' "
                f"avec risk_level='{self.risk_level}'"
            )

    def is_valid(self) -> bool:
        """True si l'enveloppe satisfait tous ses invariants."""
        try:
            self.validate()
            return True
        except EnvelopeViolation:
            return False

    # ── Constructeurs nommés ──────────────────────────────────────────────────

    @classmethod
    def for_user(
        cls,
        intent: str,
        workspace: Optional[str] = None,
        tool_category: str = "",
        budget_seconds: int = 120,
    ) -> "TaskEnvelope":
        """Enveloppe pour une tâche déclenchée par l'utilisateur."""
        return cls(
            origin="user",
            intent=intent,
            workspace=workspace,
            risk_level="medium",
            tool_category=tool_category,
            requires_verification=False,
            budget_seconds=budget_seconds,
        )

    @classmethod
    def for_autonomous(
        cls,
        origin: str,
        intent: str,
        workspace: Optional[str] = None,
        tool_category: str = "",
        budget_seconds: int = 300,
        risk_level: str = "low",
        requires_verification: bool = False,
    ) -> "TaskEnvelope":
        """Enveloppe pour une tâche autonome (daemon, scheduler, goals)."""
        if origin not in VALID_ORIGINS:
            origin = "daemon"
        return cls(
            origin=origin,
            intent=intent,
            workspace=workspace,
            risk_level=risk_level,
            tool_category=tool_category,
            requires_verification=requires_verification,
            budget_seconds=budget_seconds,
        )

    @classmethod
    def from_scheduled_task(cls, task: object) -> "TaskEnvelope":
        """
        Construit l'enveloppe depuis un ScheduledTask existant.
        Lit les champs 'envelope_*' du metadata si présents,
        sinon produit une enveloppe low-risk par défaut.
        """
        meta: dict = getattr(task, "metadata", {}) or {}
        intent = (
            meta.get("envelope_intent")
            or getattr(task, "description", "")
            or getattr(task, "name", "")
            or "scheduled_task"
        )
        return cls(
            origin=meta.get("envelope_origin", "scheduler"),
            intent=str(intent),
            workspace=meta.get("envelope_workspace"),
            risk_level=meta.get("envelope_risk_level", "low"),
            tool_category=meta.get("envelope_tool_category", "system"),
            requires_verification=bool(meta.get("envelope_requires_verification", False)),
            budget_seconds=int(meta.get("envelope_budget_seconds", getattr(task, "timeout_seconds", 300))),
        )

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "intent": self.intent,
            "workspace": self.workspace,
            "risk_level": self.risk_level,
            "tool_category": self.tool_category,
            "requires_verification": self.requires_verification,
            "budget_seconds": self.budget_seconds,
        }

    def __repr__(self) -> str:
        return (
            f"TaskEnvelope(origin={self.origin!r}, intent={self.intent!r}, "
            f"risk={self.risk_level}, category={self.tool_category!r}, "
            f"workspace={'set' if self.workspace else 'None'}, "
            f"verification={self.requires_verification}, budget={self.budget_seconds}s)"
        )
