"""
🌐 LUMENA - Gestionnaire de Canaux

Gère plusieurs canaux de communication en parallèle.
Centralise l'envoi et la réception de messages.
"""

from typing import Dict, Optional, Callable, Awaitable, List, Any
import asyncio
from loguru import logger

from .base import BaseChannel, ChannelMessage, ChannelType


class ChannelManager:
    """
    Gestionnaire centralisé pour tous les canaux de communication.
    
    Permet de :
    - Démarrer/arrêter plusieurs canaux
    - Router les messages vers le bon canal
    - Centraliser le callback de traitement
    """
    
    def __init__(self):
        self.channels: Dict[ChannelType, BaseChannel] = {}
        self._message_handler: Optional[Callable[[ChannelMessage], Awaitable[str]]] = None
        self._is_running = False
    
    def register_channel(self, channel: BaseChannel) -> None:
        """
        Enregistre un canal.
        
        Args:
            channel: Instance du canal à enregistrer
        """
        if channel.channel_type in self.channels:
            logger.warning(f"Canal {channel.channel_type.value} déjà enregistré, remplacement...")
        
        self.channels[channel.channel_type] = channel
        
        # Connecter le callback si défini
        if self._message_handler:
            channel.set_message_callback(self._message_handler)
        
        logger.info(f"📡 Canal enregistré: {channel.channel_type.value}")
    
    def set_message_handler(self, handler: Callable[[ChannelMessage], Awaitable[str]]) -> None:
        """
        Définit le handler global pour les messages.
        
        Ce handler sera appelé pour chaque message reçu sur n'importe quel canal.
        
        Args:
            handler: Async fonction qui prend un ChannelMessage et retourne une réponse
        """
        self._message_handler = handler
        
        # Mettre à jour les canaux existants
        for channel in self.channels.values():
            channel.set_message_callback(handler)
    
    async def start_all(self) -> Dict[ChannelType, bool]:
        """
        Démarre tous les canaux enregistrés.
        
        Returns:
            Dict mapping chaque type de canal à son statut de démarrage
        """
        results = {}
        
        for channel_type, channel in self.channels.items():
            try:
                success = await channel.start()
                results[channel_type] = success
                if success:
                    logger.info(f"✅ Canal démarré: {channel_type.value}")
                else:
                    logger.warning(f"⚠️ Canal non démarré: {channel_type.value}")
            except Exception as e:
                logger.error(f"❌ Erreur démarrage {channel_type.value}: {e}")
                results[channel_type] = False
        
        self._is_running = True
        return results
    
    async def stop_all(self) -> None:
        """Arrête tous les canaux."""
        for channel_type, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info(f"🛑 Canal arrêté: {channel_type.value}")
            except Exception as e:
                logger.error(f"Erreur arrêt {channel_type.value}: {e}")
        
        self._is_running = False
    
    async def start_channel(self, channel_type: ChannelType) -> bool:
        """Démarre un canal spécifique."""
        if channel_type not in self.channels:
            logger.error(f"Canal non trouvé: {channel_type.value}")
            return False
        
        return await self.channels[channel_type].start()
    
    async def stop_channel(self, channel_type: ChannelType) -> None:
        """Arrête un canal spécifique."""
        if channel_type in self.channels:
            await self.channels[channel_type].stop()
    
    async def send_message(
        self, 
        channel_type: ChannelType, 
        content: str, 
        target_id: str,
        **kwargs
    ) -> bool:
        """
        Envoie un message via un canal spécifique.
        
        Args:
            channel_type: Type de canal à utiliser
            content: Contenu du message
            target_id: Destination (channel_id, chat_id, etc.)
        """
        if channel_type not in self.channels:
            logger.error(f"Canal non disponible: {channel_type.value}")
            return False
        
        return await self.channels[channel_type].send_message(content, target_id, **kwargs)
    
    async def broadcast(self, content: str, target_ids: Dict[ChannelType, str]) -> Dict[ChannelType, bool]:
        """
        Envoie un message sur plusieurs canaux.
        
        Args:
            content: Contenu du message
            target_ids: Dict {ChannelType: target_id}
        
        Returns:
            Dict des résultats par canal
        """
        results = {}
        
        for channel_type, target_id in target_ids.items():
            results[channel_type] = await self.send_message(channel_type, content, target_id)
        
        return results
    
    def get_channel(self, channel_type: ChannelType) -> Optional[BaseChannel]:
        """Récupère un canal par son type."""
        return self.channels.get(channel_type)
    
    def list_channels(self) -> List[ChannelType]:
        """Liste les types de canaux enregistrés."""
        return list(self.channels.keys())
    
    @property
    def is_running(self) -> bool:
        """Vérifie si le manager est en cours d'exécution."""
        return self._is_running

    def get_runtime_snapshot(self) -> Dict[str, Any]:
        """Retourne un snapshot normalisé des canaux enregistrés."""
        channels: Dict[str, Dict[str, Any]] = {}

        for channel_type, channel in self.channels.items():
            channel_key = channel_type.value
            entry: Dict[str, Any] = {
                "registered": True,
                "enabled": bool(getattr(channel, "is_available", True)),
                "running": bool(getattr(channel, "is_running", False)),
                "state": "running" if bool(getattr(channel, "is_running", False)) else "stopped",
                "error": None,
            }

            runtime_status = getattr(channel, "get_runtime_status", None)
            if callable(runtime_status):
                try:
                    raw = runtime_status() or {}
                    if isinstance(raw, dict):
                        entry["enabled"] = bool(raw.get("enabled", entry["enabled"]))
                        entry["running"] = bool(raw.get("running", entry["running"]))
                        entry["state"] = str(raw.get("state", entry["state"]))
                        entry["error"] = raw.get("last_error")
                        for key, value in raw.items():
                            if key not in {"enabled", "running", "state", "last_error"}:
                                entry[key] = value
                except Exception as e:
                    entry["state"] = "error"
                    entry["error"] = f"runtime_status_error: {e}"

            channels[channel_key] = entry

        return {
            "running": self._is_running,
            "registered_count": len(channels),
            "channels": channels,
        }

# Singleton global avec lock thread-safe (Phase 2.1)
import threading
_channel_manager: Optional[ChannelManager] = None
_channel_manager_lock = threading.Lock()


def get_channel_manager() -> ChannelManager:
    """Retourne l'instance globale du gestionnaire de canaux (thread-safe)."""
    global _channel_manager
    
    # Double-check locking pattern
    if _channel_manager is None:
        with _channel_manager_lock:
            if _channel_manager is None:
                _channel_manager = ChannelManager()
    return _channel_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
