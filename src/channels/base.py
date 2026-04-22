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


import re as _re

# Mots-clés indiquant une intention de MODIFICATION d'image (pas description)
_IMAGE_EDIT_KEYWORDS = _re.compile(
    r"\b("
    r"modifi|enlève|enl[eè]ve|supprime|ajoute|remplace|change|transform"
    r"|agrandi|r[eé]dui|upscale|recadr|retouche|am[eé]lior|corrig"
    r"|flout|nettoi|d[eé]tour|d[eé]coup|fond|background|filtr"
    r"|stylis|converti|redimensionn|rogn|crop|rotat|pivote"
    r"|inpaint|outpaint|extend|img2img|rempla|efface|gomm"
    r"|colori|d[eé]satur|noir.et.blanc|s[eé]pia|contrast"
    r"|luminosit|saturat|teint|exposur|sharpen|nettet"
    r"|superpos|overlay|fusion|blend|merg|combin"
    r"|remove.background|remove.bg|sans.le.fond|sans.fond"
    r"|mets|rajoute|colle|ins[eè]re|place|pose"
    r")\b",
    _re.IGNORECASE,
)

# Mots-clés indiquant une intention de DESCRIPTION / QUESTION sur l'image
_IMAGE_DESCRIBE_KEYWORDS = _re.compile(
    r"\b("
    r"d[eé]cris|qu.est.ce|c.est.quoi|identifi|reconna[iî]"
    r"|analys|expliqu|lis|tradui|transcri|ocr|texte"
    r"|combien|o[uù].est|qui.est|quel|quelle"
    r")\b",
    _re.IGNORECASE,
)


def _is_image_edit_request(caption: str) -> bool:
    """Détermine si la légende d'une image reçue indique une intention de modification.

    Returns True si l'utilisateur veut modifier/transformer l'image,
    False si c'est une question / demande de description / ambigu.
    En cas de conflit (mots des deux catégories), on privilégie l'édition
    car l'utilisateur peut toujours re-demander une description.
    """
    if not caption or not caption.strip():
        return False
    c = caption.strip()
    has_edit = bool(_IMAGE_EDIT_KEYWORDS.search(c))
    has_describe = bool(_IMAGE_DESCRIBE_KEYWORDS.search(c))
    # Édition explicite → True, même si describe détecté aussi
    if has_edit:
        return True
    return False


def build_image_combined_message(
    caption: str,
    image_description: str,
    save_path: str,
) -> str:
    """Construit le message unifié envoyé au cerveau pour une image reçue.

    Gère 3 cas:
    1. Pas de caption → décrire l'image (comportement existant)
    2. Caption avec intent édition → inclure chemin + instruction outils
    3. Caption sans intent édition → répondre à la demande (describe/question)

    Le chemin est TOUJOURS inclus pour que le ReAct puisse y accéder si besoin.
    """
    path_line = f"📁 Chemin du fichier image reçu: {save_path}"

    if not caption or not caption.strip():
        # Pas de caption → décrire
        return (
            f"[📷 L'utilisateur a envoyé une image sans message]\n\n"
            f"📸 Description de l'image:\n{image_description}\n\n"
            f"{path_line}\n\n"
            f"Décris ce que tu vois et si tu as des observations ou commentaires utiles, partage-les."
        )

    if _is_image_edit_request(caption):
        # Intent édition détecté → instruire le ReAct d'utiliser les outils image
        return (
            f'[📷 L\'utilisateur a envoyé une image avec une demande de MODIFICATION: "{caption}"]\n\n'
            f"📸 Description de l'image (pour contexte):\n{image_description}\n\n"
            f"{path_line}\n\n"
            f"⚠️ L'utilisateur veut MODIFIER cette image, PAS juste la décrire.\n"
            f"Utilise les outils d'édition/génération d'image disponibles (edit_image, "
            f"remove_background, upscale_image, etc.) avec le fichier ci-dessus.\n"
            f"NE TE CONTENTE PAS de décrire l'image."
        )

    # Caption présent mais pas d'intent édition → répondre normalement
    return (
        f'[📷 L\'utilisateur a envoyé une image avec ce message: "{caption}"]\n\n'
        f"📸 Description de l'image:\n{image_description}\n\n"
        f"{path_line}\n\n"
        f"Réponds à la demande de l'utilisateur en tenant compte de l'image ci-dessus."
    )


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
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
