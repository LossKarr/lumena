"""
🤖 LUMENA - Session

Représente une session de conversation avec un agent.
Gère l'historique des messages et le contexte.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
from loguru import logger

try:
    from ..tools.compaction import ContextCompactor as _ContextCompactor
    _SESSION_COMPACTOR = _ContextCompactor()
except Exception:
    _SESSION_COMPACTOR = None


class SessionState(Enum):
    """État d'une session."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class Message:
    """Un message dans une session."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """
    Session de conversation avec un agent.
    
    Maintient l'historique des messages et le contexte.
    """
    
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    user_id: str = ""
    agent_type: str = "lumena"  # Type d'agent (lumena, coder, researcher, etc.)
    state: SessionState = SessionState.ACTIVE
    
    # Historique
    messages: List[Message] = field(default_factory=list)
    
    # Contexte
    context: Dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    
    # Métadonnées
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    title: str = ""
    
    # Limites (Phase 4.11)
    max_messages: int = 500  # Limite d'historique bornée
    
    def add_message(self, role: str, content: str, **metadata) -> Message:
        """
        Ajoute un message à la session.
        
        Args:
            role: Rôle (user, assistant, system, tool)
            content: Contenu du message
            
        Returns:
            Le message créé
        """
        msg = Message(
            role=role,
            content=content,
            metadata=metadata
        )
        self.messages.append(msg)
        self.updated_at = datetime.now()
        
        # Truncate si trop de messages
        if len(self.messages) > self.max_messages:
            # Garder le system prompt et les derniers messages
            system_msgs = [m for m in self.messages if m.role == "system"]
            other_msgs = [m for m in self.messages if m.role != "system"]
            keep = self.max_messages - len(system_msgs)
            dropped = other_msgs[:-keep]
            kept = other_msgs[-keep:]

            # Résumé extractif des messages supprimés (au lieu de les perdre silencieusement)
            summary_msg = None
            if dropped and _SESSION_COMPACTOR is not None:
                try:
                    summary_text = _SESSION_COMPACTOR._extractive_summary(
                        [{"role": m.role, "content": m.content} for m in dropped]
                    )
                    summary_msg = Message(
                        role="system",
                        content=f"📋 Résumé de la conversation précédente ({len(dropped)} messages compressés):\n{summary_text}",
                    )
                    logger.debug(f"Session: {len(dropped)} messages compressés en résumé extractif")
                except Exception as exc:
                    logger.warning(f"[Session] Compaction summary failed: {exc}")

            if summary_msg:
                self.messages = system_msgs + [summary_msg] + kept
            else:
                self.messages = system_msgs + kept
        
        return msg
    
    def add_user_message(self, content: str) -> Message:
        """Ajoute un message utilisateur."""
        return self.add_message("user", content)
    
    def add_assistant_message(self, content: str) -> Message:
        """Ajoute un message assistant."""
        return self.add_message("assistant", content)
    
    def add_tool_result(self, tool_name: str, result: str) -> Message:
        """Ajoute un résultat d'outil."""
        return self.add_message("tool", result, tool_name=tool_name)
    
    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """
        Retourne les messages formatés pour le LLM.
        
        Returns:
            Liste de dicts {role, content}
        """
        result = []
        
        # Ajouter le system prompt si présent
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        
        # Ajouter les autres messages
        for msg in self.messages:
            if msg.role == "tool":
                # Formater les résultats d'outil
                result.append({
                    "role": "user",
                    "content": f"[Résultat outil {msg.metadata.get('tool_name', 'unknown')}]: {msg.content}"
                })
            else:
                result.append({"role": msg.role, "content": msg.content})
        
        return result
    
    def get_last_message(self) -> Optional[Message]:
        """Retourne le dernier message."""
        return self.messages[-1] if self.messages else None
    
    def get_message_count(self) -> int:
        """Retourne le nombre de messages."""
        return len(self.messages)
    
    def set_context(self, key: str, value: Any):
        """Définit une valeur de contexte."""
        self.context[key] = value
        self.updated_at = datetime.now()
    
    def get_context(self, key: str, default: Any = None) -> Any:
        """Récupère une valeur de contexte."""
        return self.context.get(key, default)
    
    def clear_messages(self):
        """Efface l'historique des messages."""
        self.messages.clear()
        self.updated_at = datetime.now()
    
    def pause(self):
        """Met la session en pause."""
        self.state = SessionState.PAUSED
        self.updated_at = datetime.now()
    
    def resume(self):
        """Reprend une session en pause."""
        if self.state == SessionState.PAUSED:
            self.state = SessionState.ACTIVE
            self.updated_at = datetime.now()
    
    def complete(self):
        """Marque la session comme terminée."""
        self.state = SessionState.COMPLETED
        self.updated_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit la session en dictionnaire."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "agent_type": self.agent_type,
            "state": self.state.value,
            "message_count": len(self.messages),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "title": self.title or f"Session {self.id}"
        }
    
    def __str__(self) -> str:
        return f"Session({self.id}, {self.agent_type}, {len(self.messages)} msgs)"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
