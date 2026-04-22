"""
🧠 LUMENA - Session Memory (Phase 3: Performance)

Mémoire de session avancée pour améliorer la pertinence des réponses.
Garde trace du contexte de la conversation avec decay temporel.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger("lumena.session_memory")


@dataclass
class SessionTurn:
    """Un tour de conversation."""
    role: str  # "user" | "assistant" | "tool"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    importance: float = 1.0  # 0.0 - 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class KeyDecision:
    """Une décision importante prise pendant la session."""
    description: str
    context: str
    timestamp: datetime = field(default_factory=datetime.now)
    related_turns: List[int] = field(default_factory=list)  # Indices des turns


class SessionMemory:
    """
    🚀 Mémoire de session avec decay temporel.
    
    Fonctionnalités:
    - Context window des N derniers messages
    - Tracking des décisions importantes
    - Préférences utilisateur apprises
    - Decay temporel pour oublier les vieux détails
    """
    
    def __init__(self, max_turns: int = 50, decay_hours: float = 1.0):
        self.turns: List[SessionTurn] = []
        self.key_decisions: List[KeyDecision] = []
        self.user_preferences: Dict[str, Any] = {}
        self.max_turns = max_turns
        self.decay_hours = decay_hours
        self.session_start = datetime.now()
    
    def add_turn(
        self, 
        role: str, 
        content: str, 
        importance: float = 1.0,
        metadata: Optional[Dict] = None
    ) -> int:
        """
        Ajoute un tour de conversation.
        
        Returns:
            Index du turn ajouté
        """
        turn = SessionTurn(
            role=role,
            content=content,
            importance=importance,
            metadata=metadata or {}
        )
        self.turns.append(turn)
        
        # Nettoyer les vieux turns si nécessaire
        if len(self.turns) > self.max_turns * 2:
            self._compact()
        
        return len(self.turns) - 1
    
    def _compact(self):
        """Compacte la mémoire en gardant les turns importants."""
        if len(self.turns) <= self.max_turns:
            return
        
        # Appliquer decay et garder les plus importants
        now = datetime.now()
        scored_turns = []
        
        for i, turn in enumerate(self.turns):
            age_hours = (now - turn.timestamp).total_seconds() / 3600
            decay = max(0.1, 1.0 - (age_hours / self.decay_hours * 0.1))
            score = turn.importance * decay
            
            # Les derniers turns ont toujours un boost
            if i >= len(self.turns) - 10:
                score += 0.5
            
            scored_turns.append((score, i, turn))
        
        # Trier par score et garder max_turns
        scored_turns.sort(reverse=True, key=lambda x: x[0])
        kept_indices = sorted([x[1] for x in scored_turns[:self.max_turns]])
        self.turns = [self.turns[i] for i in kept_indices]
        
        logger.info(f"Session compactée: {len(self.turns)} turns gardés")
    
    def add_decision(self, description: str, context: str = ""):
        """Enregistre une décision importante."""
        decision = KeyDecision(
            description=description,
            context=context,
            related_turns=[len(self.turns) - 1] if self.turns else []
        )
        self.key_decisions.append(decision)
    
    def learn_preference(self, key: str, value: Any):
        """Apprend une préférence utilisateur."""
        self.user_preferences[key] = value
        logger.debug(f"Préférence apprise: {key} = {value}")
    
    def get_context(self, last_n: int = 20) -> Dict[str, Any]:
        """
        Récupère le contexte de session.
        
        Returns:
            Dict avec:
            - recent_turns: List des derniers turns
            - decisions: List des décisions
            - preferences: Dict des préférences
            - session_duration: Durée de la session
        """
        return {
            "recent_turns": [
                {
                    "role": t.role,
                    "content": t.content[:500],  # Tronquer pour économiser
                    "importance": t.importance
                }
                for t in self.turns[-last_n:]
            ],
            "decisions": [
                {"description": d.description, "context": d.context}
                for d in self.key_decisions[-10:]
            ],
            "preferences": self.user_preferences,
            "session_duration_minutes": (
                datetime.now() - self.session_start
            ).total_seconds() / 60,
            "total_turns": len(self.turns)
        }
    
    def get_context_summary(self) -> str:
        """Génère un résumé textuel du contexte."""
        ctx = self.get_context()
        parts = []
        
        if ctx["preferences"]:
            prefs = ", ".join(f"{k}={v}" for k, v in ctx["preferences"].items())
            parts.append(f"📋 Préférences: {prefs}")
        
        if ctx["decisions"]:
            decisions = "; ".join(d["description"] for d in ctx["decisions"][-5:])
            parts.append(f"🎯 Décisions récentes: {decisions}")
        
        parts.append(f"⏱️ Session: {ctx['session_duration_minutes']:.0f}min, {ctx['total_turns']} tours")
        
        return "\n".join(parts)
    
    def to_json(self) -> str:
        """Sérialise en JSON pour persistance."""
        return json.dumps({
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": t.timestamp.isoformat(),
                    "importance": t.importance,
                    "metadata": t.metadata
                }
                for t in self.turns
            ],
            "decisions": [
                {
                    "description": d.description,
                    "context": d.context,
                    "timestamp": d.timestamp.isoformat()
                }
                for d in self.key_decisions
            ],
            "preferences": self.user_preferences,
            "session_start": self.session_start.isoformat()
        }, indent=2, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> "SessionMemory":
        """Restaure depuis JSON."""
        data = json.loads(json_str)
        memory = cls()
        
        for t in data.get("turns", []):
            memory.turns.append(SessionTurn(
                role=t["role"],
                content=t["content"],
                timestamp=datetime.fromisoformat(t["timestamp"]),
                importance=t.get("importance", 1.0),
                metadata=t.get("metadata", {})
            ))
        
        for d in data.get("decisions", []):
            memory.key_decisions.append(KeyDecision(
                description=d["description"],
                context=d["context"],
                timestamp=datetime.fromisoformat(d["timestamp"])
            ))
        
        memory.user_preferences = data.get("preferences", {})
        memory.session_start = datetime.fromisoformat(data.get("session_start", datetime.now().isoformat()))
        
        return memory

# Singleton global avec lock thread-safe (Phase 2.1)
import threading
_session_memory: Optional[SessionMemory] = None
_session_memory_lock = threading.Lock()


def get_session_memory() -> SessionMemory:
    """Retourne l'instance singleton de SessionMemory (thread-safe)."""
    global _session_memory
    
    # Double-check locking pattern
    if _session_memory is None:
        with _session_memory_lock:
            if _session_memory is None:
                _session_memory = SessionMemory()
    return _session_memory


def reset_session_memory():
    """Réinitialise la mémoire de session (thread-safe)."""
    global _session_memory
    with _session_memory_lock:
        _session_memory = SessionMemory()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
