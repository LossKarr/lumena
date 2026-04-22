"""
🎮 LUMENA - Canal Discord

Bot Discord pour LUMENA.
Permet d'interagir avec Lumena via Discord.
"""

import os
import re as _re_discord
import time as _time_global
from pathlib import Path
from typing import Optional, Any, List
import asyncio
from datetime import datetime
from loguru import logger

# Marqueur interne pour transmettre des chemins de fichiers via le flux streaming
_FILES_MARKER = "__DISCORD_FILES__"


def _split_smart(text: str, max_len: int = 1900) -> List[str]:
    """Découpe un texte en chunks de max_len caractères, en respectant les mots et les blocs code."""
    if len(text) <= max_len:
        return [text]
    
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        # Cherche une frontière propre (saut de ligne, puis espace)
        cut = max_len
        for sep in ('\n', ' '):
            pos = remaining.rfind(sep, 0, max_len)
            if pos > max_len // 2:  # coupure pas trop courte
                cut = pos + 1
                break
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return [c for c in chunks if c]

from .base import BaseChannel, ChannelMessage, ChannelType

try:
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    logger.warning("discord.py non installé. Installez avec: pip install discord.py")


class DiscordChannel(BaseChannel):
    """
    Canal Discord pour LUMENA.
    
    Utilise discord.py pour créer un bot Discord.
    
    Configuration requise:
    - DISCORD_TOKEN dans .env
    - DISCORD_PREFIX (optionnel, défaut: "!")
    """
    
    def __init__(self, token: Optional[str] = None, prefix: str = "!"):
        """
        Initialise le canal Discord.
        
        Args:
            token: Token du bot Discord (ou depuis .env)
            prefix: Préfixe des commandes (défaut: "!")
        """
        super().__init__(ChannelType.DISCORD)
        
        self.token = token or os.getenv("DISCORD_TOKEN")
        self.prefix = prefix or os.getenv("DISCORD_PREFIX", "!")
        
        self._bot: Optional[Any] = None
        self._ready_event = asyncio.Event()
        # Callback de streaming (async generator) — si défini, remplace _message_callback
        self._stream_callback = None
        # Cooldown pour les mentions passives (channel_id → timestamp dernière réponse)
        self._passive_mention_cooldown: dict = {}
        # Fils de conversation actifs : {channel_id: {user_id: last_response_monotonic}}
        # Si Lumena a répondu à user X dans channel Y il y a moins de CONVO_WINDOW_SEC, 
        # les messages suivants de X dans Y lui sont automatiquement adressés.
        self._active_convos: dict = {}
        self.CONVO_WINDOW_SEC = 120  # 2 minutes d'inactivité = fin du fil
        # Cache nom→id des salons (rempli à on_ready)
        self._channel_cache: dict = {}  # {name: id}
        # Redirections en attente : {user_id: {"from_ch": id, "to_ch": id, "to_name": str}}
        self._pending_redirect: dict = {}
    
    def set_stream_callback(self, callback) -> None:
        """Définit un callback de streaming (async generator) pour les réponses Discord."""
        self._stream_callback = callback

    @property
    def is_available(self) -> bool:
        """Vérifie si Discord est disponible."""
        return DISCORD_AVAILABLE and bool(self.token)
    
    async def start(self) -> bool:
        """Démarre le bot Discord."""
        if not DISCORD_AVAILABLE:
            logger.error("discord.py non installé")
            return False
        
        if not self.token:
            logger.error("DISCORD_TOKEN non configuré")
            return False
        
        try:
            # Créer le bot avec les intents nécessaires
            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True
            intents.members = True
            
            self._bot = commands.Bot(command_prefix=self.prefix, intents=intents)
            
            # Événements
            @self._bot.event
            async def on_ready():
                logger.info(f"🎮 Discord connecté: {self._bot.user}")
                self.is_running = True
                self._ready_event.set()
                # Cache tous les salons texte (nom → id)
                for guild in self._bot.guilds:
                    for ch in guild.channels:
                        if hasattr(ch, 'send'):  # salons texte + forum
                            self._channel_cache[ch.name.lower()] = str(ch.id)
            
            @self._bot.event
            async def on_message(message: discord.Message):
                if message.author.bot:
                    return
                
                import re as _re
                import time as _time

                is_mention = self._bot.user in message.mentions
                is_dm = isinstance(message.channel, discord.DMChannel)
                has_prefix = message.content.startswith(self.prefix)

                # ── Mention passive : nom du bot dans le texte sans @mention ──
                bot_display_name = self._bot.user.display_name.lower()
                bot_name_clean = _re.sub(r'[^\w]', '', bot_display_name)
                msg_lower = message.content.lower()
                msg_clean = _re.sub(r'[^\w\s]', '', msg_lower)
                is_name_mention = (
                    not is_mention and not is_dm and not has_prefix
                    and (bot_name_clean in msg_clean or "lumena" in msg_clean)
                )
                # Cooldown mention passive (20s par salon)
                if is_name_mention:
                    _ck = str(message.channel.id)
                    _last_passive_info = self._passive_mention_cooldown.get(_ck, 0)
                    _last_passive_ts = _last_passive_info.get("ts", 0) if isinstance(_last_passive_info, dict) else _last_passive_info
                    if _time.monotonic() - _last_passive_ts < 20:
                        is_name_mention = False

                # ── Fil de conversation actif ──────────────────────────────────
                # Si Lumena a répondu à cet utilisateur dans ce salon récemment,
                # son prochain message lui est automatiquement adressé — sans @mention ni nom.
                _chan_key = str(message.channel.id)
                _user_key = str(message.author.id)
                _now = _time.monotonic()
                # Également corriger la référence au fil actif pour tester le timestamp
                _last_ts = (self._active_convos.get(_chan_key, {}).get(_user_key) or {})
                if isinstance(_last_ts, dict):
                    _last_ts = _last_ts.get("ts", 0)
                _in_active_convo = (
                    not is_mention and not is_dm and not has_prefix and not is_name_mention
                    and (_now - _last_ts) < self.CONVO_WINDOW_SEC
                )
                # Gardes : ne pas intercepter si le message mentionne quelqu'un d'autre
                if _in_active_convo:
                    # Le message contient un @ vers un autre utilisateur → pas pour Lumena
                    if message.mentions and self._bot.user not in message.mentions:
                        _in_active_convo = False
                    # Message très court qui ressemble à une réaction générique (ok, haha, non…)
                    # → on répond quand même, c'est de la conversation naturelle
                    # Seule exception : message vide ou purement emoji
                    if not message.content.strip() or _re.fullmatch(r'[\U00010000-\U0010ffff\s]+', message.content.strip()):
                        _in_active_convo = False

                should_respond = is_mention or is_dm or has_prefix or is_name_mention or _in_active_convo

                if not should_respond:
                    return

                # ── Réaction "je lis" sur le message reçu ─────────────────────
                try:
                    await message.add_reaction("👀")
                except Exception:
                    pass  # réaction émoji best-effort

                # ── Traitement des pièces jointes (audio/images/fichiers) ──────
                attachment_prefix, image_paths = await self._process_discord_attachments(message)

                # ── Nettoyage du contenu ───────────────────────────────────────
                content = message.content
                if is_mention:
                    content = content.replace(f"<@{self._bot.user.id}>", "").strip()
                if has_prefix:
                    content = content[len(self.prefix):].strip()

                # Mention passive : injecter le contexte de la conversation
                if is_name_mention:
                    self._passive_mention_cooldown[_chan_key] = _time.monotonic()
                    try:
                        recent = []
                        async for prev in message.channel.history(limit=5, before=message):
                            if not prev.author.bot:
                                recent.append(f"{prev.author.display_name}: {prev.content}")
                        recent.reverse()
                        recent.append(f"{message.author.display_name}: {message.content}")
                        conversation_snippet = "\n".join(recent)
                    except Exception:
                        conversation_snippet = f"{message.author.display_name}: {message.content}"  # fallback historique
                    content = (
                        f"[Tu as entendu ton prénom mentionné dans la conversation suivante — "
                        f"ce message ne t'était pas directement adressé, mais tu as décidé d'intervenir naturellement :]\n"
                        f"{conversation_snippet}\n\n"
                        f"[Réagis avec naturel, humour si approprié, comme si tu avais surpris la conversation. "
                        f"Ne fais pas semblant de ne pas avoir entendu. Sois spontanée et courte.]"
                    )

                # Fil actif : indiquer à Lumena qu'elle est en continuation de conversation
                if _in_active_convo and not content.strip() and not attachment_prefix:
                    return  # message vide, on ignore

                # Injecter les infos des pièces jointes dans le contenu
                if attachment_prefix:
                    content = f"{attachment_prefix}\n{content}".strip() if content else attachment_prefix

                # Si des images sont reçues, ajouter l'instruction d'édition si pertinent
                if image_paths:
                    from .base import _is_image_edit_request
                    _user_text = message.content or ""
                    if is_mention:
                        _user_text = _user_text.replace(f"<@{self._bot.user.id}>", "").strip()
                    if has_prefix:
                        _user_text = _user_text[len(self.prefix):].strip()
                    if _is_image_edit_request(_user_text):
                        _paths_str = ", ".join(image_paths)
                        content += (
                            f"\n\n⚠️ L'utilisateur veut MODIFIER cette image, PAS juste la décrire.\n"
                            f"Utilise les outils d'édition/génération d'image disponibles "
                            f"(edit_image, remove_background, upscale_image, etc.) avec le(s) fichier(s): {_paths_str}\n"
                            f"NE TE CONTENTE PAS de décrire l'image."
                        )

                # ── Vérification admin Lumena ──────────────────────────────────
                is_admin = await self._is_discord_admin(message)

                # ── Message unifié ────────────────────────────────────────────
                # Calculer les utilisateurs actifs dans ce salon (pour le contexte multi-user)
                _now2 = _time.monotonic()
                _active_in_chan = [
                    {
                        "user_id": uid,
                        "username": info.get("username", "") if isinstance(info, dict) else "",
                    }
                    for uid, info in self._active_convos.get(_chan_key, {}).items()
                    if uid != _user_key
                    and (_now2 - (info.get("ts", 0) if isinstance(info, dict) else info)) < self.CONVO_WINDOW_SEC
                ]
                _multi_user = len(_active_in_chan) > 0

                # Topic et nom du salon courant
                _ch_name = getattr(message.channel, 'name', None) or ""
                _ch_topic = getattr(message.channel, 'topic', None) or ""

                # ── Détection migration en attente (user dit "oui") ───────────
                _uid_str = str(message.author.id)
                _pending = self._pending_redirect.get(_uid_str)
                if _pending and _pending.get("from_ch") == str(message.channel.id):
                    _yes_words = ("oui", "ok", "ouais", "yes", "yep", "d'acc", "daccord",
                                  "allons", "vas-y", "go", "parfait", "super")
                    if any(w in content.lower() for w in _yes_words):
                        asyncio.create_task(
                            self._migrate_conversation(
                                message, _pending["to_ch"], _pending["to_name"]
                            )
                        )
                        del self._pending_redirect[_uid_str]
                        return

                channel_msg = ChannelMessage(
                    content=content,
                    channel_type=ChannelType.DISCORD,
                    user_id=str(message.author.id),
                    username=message.author.display_name,
                    timestamp=datetime.now(),
                    channel_id=str(message.channel.id),
                    guild_id=str(message.guild.id) if message.guild else None,
                    attachments=[a.url for a in message.attachments],
                    metadata={
                        "message_id": str(message.id),
                        "is_dm": is_dm,
                        "is_mention": is_mention,
                        "is_passive_mention": is_name_mention,
                        "is_active_convo": _in_active_convo,
                        "active_users_in_channel": _active_in_chan,
                        "multi_user_channel": _multi_user,
                        "discord_image_paths": image_paths,
                        "is_discord_admin": is_admin,
                        "channel_name": _ch_name,
                        "channel_topic": _ch_topic,
                        "available_channels": list(self._channel_cache.keys()),
                    }
                )
                
                if self._stream_callback:
                    # Mode streaming : envoyer un placeholder puis éditer au fil des tokens
                    import time
                    import re as _re_stream
                    sent_msg = await message.reply("💭")
                    collected = ""
                    last_edit_time = time.monotonic()
                    try:
                        async for token in self._stream_callback(channel_msg):
                            collected += token
                            now = time.monotonic()
                            if now - last_edit_time >= 0.8 and collected:
                                try:
                                    # Ne pas afficher le marqueur REDIRECT pendant le streaming
                                    _display = _re_stream.sub(r'\[REDIRECT:#[^\]\s]+\]', '', collected).rstrip()
                                    await sent_msg.edit(content=_display[:2000])
                                    last_edit_time = now
                                except Exception:
                                    pass  # edit streaming best-effort
                    except Exception as e:
                        logger.error(f"Erreur streaming Discord: {e}")

                    # Préfixer la réponse avec la @mention si plusieurs personnes actives dans le salon
                    if collected and _multi_user and not is_dm:
                        collected = f"<@{message.author.id}> {collected}"

                    # ── Détecter marqueur de redirection [REDIRECT:#salon] ──
                    import re as _re_redir
                    _redir_m = _re_redir.search(r'\[REDIRECT:#([^\]\s]+)\]', collected)
                    if _redir_m:
                        _raw_target = _redir_m.group(1)
                        # Chercher dans le cache par nom exact, puis sans emojis
                        _target_name = _raw_target.lower()
                        _target_id = self._channel_cache.get(_target_name)
                        if not _target_id:
                            # Fallback : chercher en ignorant les emojis dans le nom du cache
                            import unicodedata as _ud
                            def _strip_emoji(s):
                                return "".join(c for c in s if _ud.category(c) not in ("So", "Sm") and not (0x1F000 <= ord(c) <= 0x1FFFF))
                            _clean_target = _strip_emoji(_target_name).lstrip("-").strip()
                            for _cached_name, _cached_id in self._channel_cache.items():
                                if _strip_emoji(_cached_name).lstrip("-").strip() == _clean_target:
                                    _target_id = _cached_id
                                    _target_name = _cached_name
                                    break
                        collected = collected[:_redir_m.start()].rstrip()
                        if _target_id and _target_id != str(message.channel.id):
                            self._pending_redirect[str(message.author.id)] = {
                                "from_ch": str(message.channel.id),
                                "to_ch": _target_id,
                                "to_name": _target_name,
                            }

                    # Extraire les fichiers à envoyer (marqueur __DISCORD_FILES__:...)
                    file_paths: List[str] = []
                    _marker_match = _re_discord.search(
                        r'\[__DISCORD_FILES__:([^\]]+)\]', collected
                    )
                    if _marker_match:
                        _raw = _marker_match.group(1)
                        file_paths = [p.strip() for p in _raw.split("|") if p.strip()]
                        collected = collected[:_marker_match.start()].rstrip()

                    # Édition finale avec la réponse texte
                    if collected:
                        if len(collected) > 2000:
                            await sent_msg.delete()
                            # Découpage propre aux frontières de mots
                            chunks = _split_smart(collected, 1900)
                            for chunk in chunks:
                                await message.reply(chunk)
                        else:
                            try:
                                await sent_msg.edit(content=collected or "✅")
                            except Exception:
                                await message.reply(collected)  # fallback si edit échoue
                    elif not file_paths:
                        await sent_msg.delete()

                    # Supprimer la réaction 👀 et ajouter ✅ une fois terminé
                    try:
                        await message.remove_reaction("👀", self._bot.user)
                    except Exception:
                        pass  # remove réaction best-effort

                    # Envoyer les fichiers en pièces jointes
                    for fp in file_paths:
                        await self._send_file_to_message(message, fp)

                    # Mettre à jour le fil de conversation actif
                    import time as _time2
                    self._active_convos.setdefault(_chan_key, {})[_user_key] = {
                        "ts": _time2.monotonic(), "username": message.author.display_name
                    }

                else:
                    # Fallback sans streaming
                    async with message.channel.typing():
                        response = await self._on_message_received(channel_msg)
                        if response:
                            if len(response) > 2000:
                                chunks = _split_smart(response, 1900)
                                for chunk in chunks:
                                    await message.reply(chunk)
                            else:
                                await message.reply(response)
                    try:
                        await message.remove_reaction("👀", self._bot.user)
                    except Exception:
                        pass  # remove réaction best-effort
                    import time as _time3
                    self._active_convos.setdefault(_chan_key, {})[_user_key] = {
                        "ts": _time3.monotonic(), "username": message.author.display_name
                    }
            
            # Lancer le bot en background avec gestion d'erreur
            async def _safe_bot_start():
                try:
                    await self._bot.start(self.token)
                except Exception as exc:
                    logger.warning(f"Discord bot arrêté: {exc}")
                    self.is_running = False
                    self._ready_event.set()  # débloquer le wait_for

            self._bot_task = asyncio.create_task(_safe_bot_start())
            
            # Attendre que le bot soit prêt
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=30)
                if not self.is_running:
                    logger.error("Discord: connexion échouée (rate-limit ou auth)")
                    return False
                return True
            except asyncio.TimeoutError:
                logger.error("Timeout connexion Discord")
                return False
            
        except Exception as e:
            logger.error(f"Erreur démarrage Discord: {e}")
            return False
    
    async def _migrate_conversation(self, message: Any, target_channel_id: str, target_channel_name: str) -> None:
        """Transfère la conversation vers un autre salon Discord.

        - Confirme dans le salon d'origine
        - Envoie un message de suite dans le salon cible avec le contexte
        """
        try:
            target_ch = self._bot.get_channel(int(target_channel_id))
            if not target_ch:
                await message.reply(f"❌ Je n'arrive pas à accéder au salon #{target_channel_name}.")
                return
            # Confirmation dans le salon d'origine
            await message.reply(
                f"✅ On continue dans <#{target_channel_id}> ! Je t'y retrouve."
            )
            # Message d'introduction dans le salon cible
            mention = f"<@{message.author.id}>"
            origin = f"<#{message.channel.id}>"
            await target_ch.send(
                f"{mention} — Suite de notre conversation depuis {origin} :\n"
                f"> Dis-moi, tu souhaitais parler de quoi exactement ? Je t'écoute ici ! 😊"
            )
            logger.info(f"🎮 Conversation migrée: {message.author.display_name} → #{target_channel_name}")
        except Exception as e:
            logger.error(f"Erreur migration conversation Discord: {e}")
            await message.reply(f"❌ Impossible de migrer la conversation : {e}")

    async def _send_file_to_message(self, message: Any, file_path: str) -> None:
        """Envoie un fichier en réponse à un message Discord."""
        if not DISCORD_AVAILABLE:
            return
        try:
            p = Path(file_path)
            if not p.exists() or not p.is_file():
                logger.warning(f"🎮 Fichier introuvable: {file_path}")
                return
            size_mb = p.stat().st_size / (1024 * 1024)
            if size_mb > 25:
                await message.reply(
                    f"⚠️ Fichier trop volumineux pour Discord ({size_mb:.1f} Mo > 25 Mo) : `{p.name}`"
                )
                return
            await message.reply(
                content=f"📎 `{p.name}`",
                file=discord.File(str(p), filename=p.name),
            )
            logger.info(f"🎮 Fichier envoyé sur Discord: {p.name}")
        except Exception as e:
            logger.error(f"Erreur envoi fichier Discord {file_path}: {e}")
            await message.reply(f"❌ Impossible d'envoyer le fichier `{Path(file_path).name}` : {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Gestion des droits admin Lumena
    # ──────────────────────────────────────────────────────────────────────────

    async def _is_discord_admin(self, message: Any) -> bool:
        """Vérifie si l'auteur du message est admin Lumena sur Discord.

        Ordre de priorité :
        1. ID dans DISCORD_ADMIN_IDS (env, comma-séparé)
        2. Propriétaire du serveur (guild.owner_id)
        3. ID de rôle dans DISCORD_ADMIN_ROLE_IDS (env, comma-séparé)
        4. Nom de rôle dans DISCORD_ADMIN_ROLES (env, défaut: admin,administrateur,moderateur,lumena-admin)
        5. Permission administrateur Discord native
        """
        user_id_str = str(message.author.id)

        # 1. Whitelist explicite user ID
        admin_ids_raw = os.getenv("DISCORD_ADMIN_IDS", "")
        if admin_ids_raw:
            if user_id_str in {x.strip() for x in admin_ids_raw.split(",") if x.strip()}:
                return True

        # 2. Propriétaire du serveur = toujours admin
        if message.guild and message.guild.owner_id == message.author.id:
            return True

        # 3 & 4 & 5. Vérification par rôle (nécessite un guild)
        if not message.guild:
            return False

        admin_role_ids_raw = os.getenv("DISCORD_ADMIN_ROLE_IDS", "")
        admin_role_ids = {x.strip() for x in admin_role_ids_raw.split(",") if x.strip()}

        admin_roles_raw = os.getenv("DISCORD_ADMIN_ROLES", "admin,administrateur,moderateur,lumena-admin")
        admin_role_names = {x.strip().lower() for x in admin_roles_raw.split(",") if x.strip()}

        for role in getattr(message.author, 'roles', []):
            if str(role.id) in admin_role_ids:
                return True
            if role.name.lower() in admin_role_names:
                return True

        # 5. Permission administrateur Discord native
        try:
            perms = message.channel.permissions_for(message.author)
            if getattr(perms, 'administrator', False):
                return True
        except Exception as exc:
            logger.debug(f"[Discord] Permission check fallback: {exc}")

        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Traitement des pièces jointes Discord
    # ──────────────────────────────────────────────────────────────────────────

    async def _process_discord_attachments(self, message: Any) -> tuple:
        """Télécharge et traite les pièces jointes Discord.
        
        - Audio (.ogg, .mp3, .wav, .m4a, .opus, .flac) → transcription Whisper
        - Images (.jpg, .png, .gif, .webp, .bmp) → sauvegarde sur disque
        - Autres fichiers → info textuelle
        
        Retourne (enrichissement_texte: str, image_paths: list[str])
        """
        if not message.attachments:
            return "", []
        
        text_parts: List[str] = []
        image_paths: List[str] = []
        
        AUDIO_EXTS = ('.ogg', '.mp3', '.wav', '.m4a', '.opus', '.flac', '.webm')
        IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.avif')
        
        try:
            import aiohttp as _aiohttp
        except ImportError:
            logger.warning("🎮 aiohttp non disponible, pièces jointes non traitées")
            for a in message.attachments:
                text_parts.append(f"[📎 Fichier joint: {a.filename}]")
            return "\n".join(text_parts), []
        
        import tempfile
        import os as _os
        
        for attachment in message.attachments:
            fname = attachment.filename
            fname_lower = fname.lower()
            ct = getattr(attachment, 'content_type', '') or ''
            
            is_audio = fname_lower.endswith(AUDIO_EXTS) or ct.startswith('audio/')
            is_image = fname_lower.endswith(IMAGE_EXTS) or ct.startswith('image/')
            
            # ── Audio → transcription ──────────────────────────────────────
            if is_audio:
                transcript = ""
                try:
                    async with _aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            audio_bytes = await resp.read()
                    
                    ext = Path(fname).suffix.lower() or '.ogg'
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
                        tmp_path = fh.name
                        fh.write(audio_bytes)
                    
                    try:
                        from faster_whisper import WhisperModel as _WM
                        _wm = _WM("tiny", device="cpu", compute_type="int8")
                        segs, _ = _wm.transcribe(tmp_path, language="fr")
                        transcript = " ".join(s.text for s in segs).strip()
                    except Exception as e_wm:
                        logger.warning(f"🎤 Whisper Discord: {e_wm}")
                    finally:
                        try:
                            _os.unlink(tmp_path)
                        except Exception:
                            pass  # fichier temp cleanup best-effort
                    
                    if transcript:
                        text_parts.append(f"[🎤 Message vocal transcrit: « {transcript} »]")
                        logger.info(f"🎤 Transcription Discord: {transcript[:60]}…")
                    else:
                        text_parts.append(f"[🎤 Message vocal reçu (transcription indisponible): {fname}]")
                except Exception as e_audio:
                    logger.warning(f"🎤 Erreur audio Discord: {e_audio}")
                    text_parts.append(f"[🎤 Message vocal reçu: {fname}]")
            
            # ── Image → sauvegarde + info ──────────────────────────────────
            elif is_image:
                try:
                    ts = int(_time_global.time())
                    from ..utils.paths import RECEIVED_IMAGES_DIR
                    save_dir = RECEIVED_IMAGES_DIR
                    save_dir.mkdir(parents=True, exist_ok=True)
                    save_path = save_dir / f"discord_{ts}_{fname}"
                    
                    async with _aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            save_path.write_bytes(await resp.read())
                    
                    image_paths.append(str(save_path))
                    w = getattr(attachment, 'width', None)
                    h = getattr(attachment, 'height', None)
                    dims = f" ({w}×{h})" if w and h else ""
                    text_parts.append(
                        f"[📷 Image reçue: {fname}{dims} | chemin local: {save_path}]"
                    )
                    logger.info(f"🖼️ Image Discord sauvegardée: {save_path.name}")
                except Exception as e_img:
                    logger.warning(f"🖼️ Erreur image Discord: {e_img}")
                    text_parts.append(f"[📷 Image reçue: {fname} (non sauvegardée)]")
            
            # ── Autre fichier ──────────────────────────────────────────────
            else:
                size_str = ""
                if hasattr(attachment, 'size') and attachment.size:
                    kb = attachment.size / 1024
                    size_str = f" ({kb:.0f} Ko)" if kb < 1024 else f" ({kb/1024:.1f} Mo)"
                text_parts.append(f"[📎 Fichier joint: {fname}{size_str}]")
        
        return "\n".join(text_parts), image_paths

    async def stop(self) -> None:
        """Arrête le bot Discord."""
        if self._bot:
            await self._bot.close()
            self._bot = None
        self.is_running = False
        self._ready_event.clear()
        logger.info("🎮 Discord déconnecté")
    
    async def send_message(self, content: str, target_id: str, **kwargs) -> bool:
        """
        Envoie un message sur Discord.
        
        Args:
            content: Contenu du message
            target_id: ID du channel Discord
        """
        if not self._bot or not self.is_running:
            return False
        
        try:
            channel = self._bot.get_channel(int(target_id))
            if not channel:
                channel = await self._bot.fetch_channel(int(target_id))
            
            if channel:
                await channel.send(content)
                return True
            return False
            
        except Exception as e:
            logger.error(f"Erreur envoi Discord: {e}")
            return False

    async def send_file(self, file_path: str, target_id: str, caption: str = "") -> bool:
        """Envoie un fichier (document, image, PDF…) dans un salon Discord.

        Args:
            file_path: Chemin absolu du fichier à envoyer.
            target_id: ID du salon Discord (str ou int).
            caption:   Texte optionnel accompagnant le fichier.
        """
        if not DISCORD_AVAILABLE or not self._bot or not self.is_running:
            return False
        try:
            p = Path(file_path)
            if not p.exists() or not p.is_file():
                logger.warning(f"🎮 send_file: fichier introuvable: {file_path}")
                return False
            size_mb = p.stat().st_size / (1024 * 1024)
            if size_mb > 25:
                logger.warning(f"🎮 send_file: fichier trop lourd ({size_mb:.1f} Mo)")
                return False
            channel = self._bot.get_channel(int(target_id))
            if not channel:
                channel = await self._bot.fetch_channel(int(target_id))
            if channel:
                await channel.send(
                    content=caption or f"📎 `{p.name}`",
                    file=discord.File(str(p), filename=p.name),
                )
                logger.info(f"🎮 Fichier envoyé: {p.name} → #{target_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Erreur send_file Discord: {e}")
            return False

    @property 
    def bot(self) -> Optional[Any]:
        """Accès au bot Discord."""
        return self._bot
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
