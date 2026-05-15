"""
StructuredState — État conversationnel structuré parallèle.

Enrichit le ConversationContext sans remplacer la façade messages existante.
Chaque ConversationContext peut porter un StructuredState optionnel.

V1 : dernier intent, outils récents, faits établis, questions en attente.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class StructuredState:
    """État structuré parallèle au ConversationContext.

    Champs :
    - last_intent        : dernier intent résolu (ex: "code_edit", "question")
    - recent_tools       : outils récemment utilisés (borné à max_recent_tools)
    - established_facts  : assertions stables (clé → valeur str)
    - pending_questions  : questions non résolues émises par Lumena
    """

    last_intent: Optional[str] = None
    recent_tools: Deque[str] = field(default_factory=deque)
    established_facts: Dict[str, str] = field(default_factory=dict)
    pending_questions: List[str] = field(default_factory=list)

    # Borne pour recent_tools
    max_recent_tools: int = field(default=20, repr=False)

    # ── Mutations utilitaires ────────────────────────────────────────────

    def record_tool(self, tool_name: str) -> None:
        """Ajoute un outil aux outils récents (FIFO borné)."""
        self.recent_tools.append(tool_name)
        while len(self.recent_tools) > self.max_recent_tools:
            self.recent_tools.popleft()

    def set_fact(self, key: str, value: str) -> None:
        """Enregistre ou met à jour un fait établi."""
        self.established_facts[str(key)] = str(value)

    def remove_fact(self, key: str) -> None:
        """Retire un fait établi."""
        self.established_facts.pop(str(key), None)

    def add_pending_question(self, question: str) -> None:
        """Ajoute une question en attente."""
        q = str(question).strip()
        if q and q not in self.pending_questions:
            self.pending_questions.append(q)

    def resolve_pending_question(self, question: str) -> bool:
        """Retire une question en attente. Retourne True si elle existait."""
        q = str(question).strip()
        if q in self.pending_questions:
            self.pending_questions.remove(q)
            return True
        return False

    def clear_pending_questions(self) -> None:
        """Efface toutes les questions en attente."""
        self.pending_questions.clear()

    # ── Sérialisation ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Retourne un dict sérialisable (JSON-safe)."""
        return {
            "last_intent": self.last_intent,
            "recent_tools": list(self.recent_tools),
            "established_facts": dict(self.established_facts),
            "pending_questions": list(self.pending_questions),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> "StructuredState":
        """Reconstruit un StructuredState depuis un dict (chargement disque).

        Tolerant : les clés manquantes prennent la valeur par défaut.
        """
        if not isinstance(data, dict):
            return cls(**kwargs)
        recent = data.get("recent_tools")
        max_rt = kwargs.get("max_recent_tools", 20)
        return cls(
            last_intent=data.get("last_intent"),
            recent_tools=deque(recent if isinstance(recent, list) else [], maxlen=max_rt),
            established_facts=dict(data.get("established_facts") or {}),
            pending_questions=list(data.get("pending_questions") or []),
            max_recent_tools=max_rt,
        )

    def is_empty(self) -> bool:
        """True si aucun état structuré n'a été enregistré."""
        return (
            self.last_intent is None
            and len(self.recent_tools) == 0
            and len(self.established_facts) == 0
            and len(self.pending_questions) == 0
        )


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
