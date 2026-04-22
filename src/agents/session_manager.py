"""
🤖 LUMENA - Session Manager

Gère les sessions de conversation pour plusieurs utilisateurs.
Supporte le multi-agent avec différents types d'agents spécialisés.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import threading
from loguru import logger

from .session import Session, SessionState


class SessionManager:
    """
    Gestionnaire centralisé des sessions.
    
    Permet de:
    - Créer et gérer des sessions par utilisateur
    - Basculer entre différents agents
    - Persister et restaurer des sessions
    """
    
    # Types d'agents disponibles
    AGENT_TYPES = {
        "lumena": "Agent principal LUMENA",
        "coder": "Agent spécialisé en code",
        "researcher": "Agent de recherche web",
        "writer": "Agent de rédaction",
        "analyst": "Agent d'analyse de données"
    }
    
    def __init__(self, max_sessions_per_user: int = 10):
        """
        Initialise le gestionnaire.
        
        Args:
            max_sessions_per_user: Nombre max de sessions par utilisateur
        """
        self.sessions: Dict[str, Session] = {}  # session_id -> Session
        self.user_sessions: Dict[str, List[str]] = {}  # user_id -> [session_ids]
        self.active_session: Dict[str, str] = {}  # user_id -> active_session_id
        self.max_sessions_per_user = max_sessions_per_user
    
    def create_session(
        self, 
        user_id: str, 
        agent_type: str = "lumena",
        system_prompt: str = "",
        title: str = ""
    ) -> Session:
        """
        Crée une nouvelle session.
        
        Args:
            user_id: ID de l'utilisateur
            agent_type: Type d'agent à utiliser
            system_prompt: Prompt système initial
            title: Titre de la session
            
        Returns:
            La session créée
        """
        session = Session(
            user_id=user_id,
            agent_type=agent_type,
            system_prompt=system_prompt,
            title=title or f"Session {agent_type}"
        )
        
        # Enregistrer
        self.sessions[session.id] = session
        
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = []
        
        self.user_sessions[user_id].append(session.id)
        self.active_session[user_id] = session.id
        
        # Limiter le nombre de sessions
        self._cleanup_old_sessions(user_id)
        
        logger.info(f"📝 Session créée: {session.id} ({agent_type}) pour {user_id}")
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Récupère une session par son ID."""
        return self.sessions.get(session_id)
    
    def get_active_session(self, user_id: str) -> Optional[Session]:
        """Récupère la session active d'un utilisateur."""
        session_id = self.active_session.get(user_id)
        if session_id:
            return self.sessions.get(session_id)
        return None
    
    def get_or_create_session(self, user_id: str, agent_type: str = "lumena") -> Session:
        """Récupère la session active ou en crée une nouvelle."""
        session = self.get_active_session(user_id)
        if session and session.state == SessionState.ACTIVE:
            return session
        return self.create_session(user_id, agent_type)
    
    def switch_session(self, user_id: str, session_id: str) -> Optional[Session]:
        """
        Change la session active d'un utilisateur.
        
        Returns:
            La nouvelle session active ou None
        """
        if session_id in self.sessions:
            session = self.sessions[session_id]
            if session.user_id == user_id:
                self.active_session[user_id] = session_id
                session.resume()  # Réactiver si en pause
                logger.debug(f"🔄 Session changée: {session_id}")
                return session
        return None
    
    def switch_agent(self, user_id: str, agent_type: str) -> Session:
        """
        Change l'agent pour l'utilisateur (crée une nouvelle session).
        
        Args:
            user_id: ID utilisateur
            agent_type: Type d'agent
            
        Returns:
            Nouvelle session avec l'agent
        """
        if agent_type not in self.AGENT_TYPES:
            logger.warning(f"Type d'agent inconnu: {agent_type}, utilisation de 'lumena'")
            agent_type = "lumena"
        
        # Mettre la session actuelle en pause
        current = self.get_active_session(user_id)
        if current:
            current.pause()
        
        return self.create_session(user_id, agent_type)
    
    def list_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Liste les sessions d'un utilisateur."""
        session_ids = self.user_sessions.get(user_id, [])
        return [
            self.sessions[sid].to_dict() 
            for sid in session_ids 
            if sid in self.sessions
        ]
    
    def delete_session(self, session_id: str) -> bool:
        """Supprime une session."""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            user_id = session.user_id
            
            del self.sessions[session_id]
            
            if user_id in self.user_sessions:
                if session_id in self.user_sessions[user_id]:
                    self.user_sessions[user_id].remove(session_id)
            
            if self.active_session.get(user_id) == session_id:
                del self.active_session[user_id]
            
            logger.info(f"🗑️ Session supprimée: {session_id}")
            return True
        return False
    
    def _cleanup_old_sessions(self, user_id: str):
        """Nettoie les anciennes sessions si limite dépassée."""
        # Phase 2.8: Copier la liste avant itération+suppression pour éviter RuntimeError
        session_ids = list(self.user_sessions.get(user_id, []))
        
        if len(session_ids) > self.max_sessions_per_user:
            # Trier par date de mise à jour
            sorted_ids = sorted(
                session_ids,
                key=lambda sid: self.sessions[sid].updated_at if sid in self.sessions else datetime.min
            )
            
            # Supprimer les plus anciennes (itérer sur une copie)
            to_remove = sorted_ids[:len(sorted_ids) - self.max_sessions_per_user]
            for sid in list(to_remove):  # Copie pour itération sûre
                if sid != self.active_session.get(user_id):
                    self.delete_session(sid)
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques."""
        active_count = sum(1 for s in self.sessions.values() if s.state == SessionState.ACTIVE)
        
        return {
            "total_sessions": len(self.sessions),
            "active_sessions": active_count,
            "total_users": len(self.user_sessions),
            "agent_types": list(self.AGENT_TYPES.keys())
        }


# Singleton global avec lock thread-safe (Phase 2.1)
_session_manager: Optional[SessionManager] = None
_session_manager_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    """Retourne l'instance globale du gestionnaire de sessions (thread-safe)."""
    global _session_manager
    
    # Double-check locking pattern
    if _session_manager is None:
        with _session_manager_lock:
            if _session_manager is None:
                _session_manager = SessionManager()
    return _session_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
