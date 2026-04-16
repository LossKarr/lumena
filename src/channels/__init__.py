"""
🌐 LUMENA - Système Multi-Canal

Architecture pour gérer plusieurs canaux de communication:
- Discord
- Telegram
- CLI (existant)
- Web (futur)

"""

import os

from .base import BaseChannel, ChannelMessage, ChannelType
from .manager import ChannelManager


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# Imports optionnels (dépendances peuvent manquer)
# Par défaut on expose les canaux unifiés (*_channel.py).
# Le chemin legacy reste disponible via LUMENA_CHANNELS_LEGACY_IMPORTS=1.
USE_LEGACY_CHANNEL_IMPORTS = _env_flag("LUMENA_CHANNELS_LEGACY_IMPORTS", False)

if USE_LEGACY_CHANNEL_IMPORTS:
    try:
        from .telegram import TelegramChannel, get_telegram_channel
    except Exception:
        TelegramChannel = None  # module telegram non disponible
        get_telegram_channel = None

    try:
        from .discord import DiscordChannel, get_discord_channel
    except Exception:
        DiscordChannel = None  # module discord non disponible
        get_discord_channel = None
else:
    try:
        from .telegram_channel import TelegramChannel
    except Exception:
        TelegramChannel = None  # module telegram non disponible

    try:
        from .discord_channel import DiscordChannel
    except Exception:
        DiscordChannel = None  # module discord non disponible

    # Les helpers singleton legacy ne s'appliquent qu'aux modules legacy.
    get_telegram_channel = None
    get_discord_channel = None

try:
    from .twitter_channel import TwitterChannel, get_twitter_channel
except Exception:
    TwitterChannel = None  # module twitter non disponible
    get_twitter_channel = None

try:
    from .whatsapp_channel import WhatsAppChannel
except Exception:
    WhatsAppChannel = None

__all__ = [
    'BaseChannel', 'ChannelMessage', 'ChannelType', 'ChannelManager',
    'USE_LEGACY_CHANNEL_IMPORTS',
    'TelegramChannel', 'get_telegram_channel',
    'DiscordChannel', 'get_discord_channel',
    'TwitterChannel', 'get_twitter_channel',
    'WhatsAppChannel',
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
