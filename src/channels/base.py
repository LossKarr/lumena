"""
🌐 LUMENA - Classe de Base pour les Canaux

Définit l'interface commune que tous les canaux doivent implémenter.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable, Awaitable
from loguru import logger


class ChannelType(Enum):
    """Types de canaux supportés."""
    CLI = "cli"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    TWITTER = "twitter"
    WEB = "web"
    API = "api"
    WHATSAPP = "whatsapp"


@dataclass
class ChannelMessage:
    """
    Message unifié entre tous les canaux.
    
    Permet de normaliser les messages de différentes sources.
    """
    content: str
    channel_type: ChannelType
    user_id: str
    username: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Métadonnées spécifiques au canal
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None  # Discord server
    chat_id: Optional[str] = None   # Telegram chat
    reply_to: Optional[str] = None  # ID du message auquel on répond
    
    # Attachements
    attachments: list = field(default_factory=list)
    
    # Métadonnées additionnelles
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"[{self.channel_type.value}] {self.username}: {self.content[:50]}..."


class BaseChannel(ABC):
    """
    Classe abstraite de base pour tous les canaux.
    
    Chaque canal (Discord, Telegram, etc.) doit hériter de cette classe
    et implémenter les méthodes abstraites.
    """
    
    def __init__(self, channel_type: ChannelType):
        self.channel_type = channel_type
        self.is_running = False
        self._message_callback: Optional[Callable[[ChannelMessage], Awaitable[str]]] = None
    
    @abstractmethod
    async def start(self) -> bool:
        """
        Démarre le canal.
        
        Returns:
            True si le démarrage a réussi
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Arrête le canal proprement."""
        pass
    
    @abstractmethod
    async def send_message(self, content: str, target_id: str, **kwargs) -> bool:
        """
        Envoie un message via ce canal.
        
        Args:
            content: Contenu du message
            target_id: ID de la destination (chat_id, channel_id, etc.)
            
        Returns:
            True si envoyé avec succès
        """
        pass
    
    def set_message_callback(self, callback: Callable[[ChannelMessage], Awaitable[str]]):
        """
        Définit le callback appelé quand un message est reçu.
        
        Le callback reçoit un ChannelMessage et doit retourner la réponse.
        """
        self._message_callback = callback
    
    async def _on_message_received(self, message: ChannelMessage) -> Optional[str]:
        """
        Appelé quand un message est reçu.
        
        Exécute le callback et retourne la réponse.
        """
        if self._message_callback:
            try:
                return await self._message_callback(message)
            except Exception as e:
                logger.error(f"Erreur callback message {self.channel_type.value}: {e}")
                return f"❌ Erreur: {e}"
        return None
    
    @property
    def name(self) -> str:
        """Nom du canal."""
        return self.channel_type.value
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
