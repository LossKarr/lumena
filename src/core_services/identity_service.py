"""
IdentityService — Résolution d'identité Telegram / Discord.

Migré depuis LumenaCore (11 méthodes, dépendances data_dir + memory).
"""

import json
import re
import threading
from typing import Any, Dict, Optional

from loguru import logger

from .base_service import BaseService
from ..utils.persistence import atomic_write_json
from ..structured_state import StructuredState


class IdentityService(BaseService):
    """Gestion de l'identité utilisateur (Telegram, Discord)."""

    def __init__(self, ctx, *, tg_contexts=None, discord_contexts=None, discord_users=None, max_contexts=500):
        super().__init__(ctx)
        # Contextes Telegram par tg_id → ConversationContext
        self._tg_contexts: Dict[str, Any] = tg_contexts if tg_contexts is not None else {}
        # Contextes WhatsApp par phone → ConversationContext
        self._wa_contexts: Dict[str, Any] = {}
        # Contextes Discord par channel_user key
        self._discord_contexts: Dict[str, Any] = discord_contexts if discord_contexts is not None else {}
        self._discord_users: Dict[str, dict] = discord_users if discord_users is not None else {}
        self._max_contexts = max_contexts
        self._identity_lock = threading.Lock()
        # Phase 2 fiabilisation : contexte code récent par canal pour maintenir
        # le projet actif à travers plusieurs tours conversationnels.
        # Clé = channel_key unifié (ex: "telegram:12345", "web:session:xxx").
        # Valeur = {"workspace_path": str, "project_slug": Optional[str], "ts": float}
        self._last_code_context: Dict[str, Dict[str, Any]] = {}
        self._code_context_ttl: float = 1800.0  # 30 min

    # ── Stickiness code context (Phase 2 fiabilisation) ─────────────────

    def remember_code_context(
        self,
        channel_key: str,
        workspace_path: str,
        project_slug: Optional[str] = None,
    ) -> None:
        """Mémorise le dernier contexte code actif pour un canal.

        Permet à ReAct de re-router les messages ambigus ("ça marche pas",
        "t'as fini ?") vers le bon projet pendant `_code_context_ttl` secondes.
        """
        if not channel_key or not workspace_path:
            return
        import time
        with self._identity_lock:
            self._last_code_context[channel_key] = {
                "workspace_path": str(workspace_path),
                "project_slug": project_slug,
                "ts": time.time(),
            }

    def get_recent_code_context(
        self,
        channel_key: str,
        ttl: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retourne le contexte code récent pour un canal, ou None si expiré/absent."""
        if not channel_key:
            return None
        import time
        _ttl = ttl if ttl is not None else self._code_context_ttl
        with self._identity_lock:
            entry = self._last_code_context.get(channel_key)
            if not entry:
                return None
            if (time.time() - float(entry.get("ts", 0))) > _ttl:
                self._last_code_context.pop(channel_key, None)
                return None
            return dict(entry)

    @staticmethod
    def resolve_channel_key(runtime_context: Any, sender: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Construit une clé unifiée (channel:identifier) depuis le RuntimeContext.

        Priorité pour le web/ide : user_id + conversation_id > user_id + client > user_id.
        session_id n'existe plus dans RuntimeContext (supprimé Phase 0).
        """
        if runtime_context is None and not sender:
            return None
        channel = getattr(runtime_context, "channel", None) or "unknown"
        # Priorités par canal externe (sender fourni par bot)
        if sender:
            if channel == "telegram" and sender.get("id"):
                return f"telegram:{sender['id']}"
            if channel == "whatsapp" and sender.get("phone"):
                return f"whatsapp:{sender['phone']}"
            if channel == "discord" and sender.get("id"):
                return f"discord:{sender['id']}"
        # Pour web/ide : utiliser user_id + conversation_id ou client
        if runtime_context is not None:
            uid = getattr(runtime_context, "user_id", None) or "local:owner"
            conv = getattr(runtime_context, "conversation_id", None) or ""
            client = getattr(runtime_context, "client", None) or ""
            if conv:
                return f"{channel}:{uid}:{conv}"
            if client and client != "unknown":
                return f"{channel}:{uid}:{client}"
            return f"{channel}:{uid}"
        return f"{channel}:default"

    def _resolve_sender_identity(
        self,
        sender: Optional[Dict[str, Any]],
        source_channel: str,
    ) -> Optional[Dict[str, Any]]:
        """Résout l'identité d'un expéditeur Telegram."""
        if not sender or source_channel != "telegram":
            return None
        if not self.memory:
            return None

        tg_id = str(sender.get("id", "")).strip()
        tg_name = (sender.get("name") or sender.get("first_name") or "Inconnu").strip()
        if not tg_id:
            return None

        owner_tg_id = self.memory.get_fact("telegram_owner_id")

        if not owner_tg_id:
            self.memory.learn_fact("telegram_owner_id", tg_id)
            logger.info(f"📱 Propriétaire Telegram enregistré automatiquement: ID={tg_id} nom={tg_name}")
            owner_tg_id = tg_id

        is_owner = (tg_id == owner_tg_id)

        name_key = f"telegram_{tg_id}_name"
        known_name = self.memory.get_fact(name_key)
        if not known_name:
            self.memory.learn_fact(name_key, tg_name)
            known_name = tg_name
            if not is_owner:
                logger.info(f"📱 Nouvel ami Telegram mémorisé: {tg_name} (ID={tg_id})")
            else:
                logger.info(f"📱 Propriétaire reconnu: {tg_name} (ID={tg_id})")

        known_raw = self.memory.get_fact("telegram_known_ids") or ""
        known_ids_list = [i for i in known_raw.split(",") if i]
        if tg_id not in known_ids_list:
            known_ids_list.append(tg_id)
            self.memory.learn_fact("telegram_known_ids", ",".join(known_ids_list))

        return {"name": known_name, "is_owner": is_owner, "tg_id": tg_id}

    def _resolve_whatsapp_identity(
        self,
        sender: Optional[Dict[str, Any]],
        source_channel: str,
    ) -> Optional[Dict[str, Any]]:
        """Résout l'identité d'un expéditeur WhatsApp."""
        if not sender or source_channel != "whatsapp":
            return None
        if not self.memory:
            return None

        phone = str(sender.get("phone", "")).strip()
        wa_name = (sender.get("profile_name") or sender.get("name") or "").strip()
        if not phone:
            return None

        owner_wa_phone = self.memory.get_fact("whatsapp_owner_phone")
        if not owner_wa_phone:
            self.memory.learn_fact("whatsapp_owner_phone", phone)
            logger.info(f"📱 Propriétaire WhatsApp enregistré automatiquement: phone={phone} nom={wa_name}")
            owner_wa_phone = phone

        is_owner = (phone == owner_wa_phone)

        name_key = f"whatsapp_{phone}_name"
        stored_name = self.memory.get_fact(name_key)
        if wa_name and wa_name != stored_name:
            self.memory.learn_fact(name_key, wa_name)
            if not stored_name:
                if not is_owner:
                    logger.info(f"📱 Nouveau contact WhatsApp mémorisé: {wa_name} (phone={phone})")
                else:
                    logger.info(f"📱 Propriétaire WhatsApp reconnu: {wa_name} (phone={phone})")

        known_raw = self.memory.get_fact("whatsapp_known_phones") or ""
        known_set = set(known_raw.split(",")) if known_raw else set()
        if phone not in known_set:
            known_set.add(phone)
            self.memory.learn_fact("whatsapp_known_phones", ",".join(sorted(known_set)))

        return {"name": wa_name or phone, "is_owner": is_owner, "phone": phone}

    def _resolve_channel_and_ide_context(
        self,
        source_channel: str,
        ide_context: Optional[Dict[str, Any]],
    ):
        """Résout le canal actif et le contexte IDE à partir du RuntimeContext."""
        try:
            from src.runtime.context import get_current_runtime_context
            ctx = get_current_runtime_context()
        except Exception:
            ctx = None

        if ctx is not None and ctx.channel == "ide" and source_channel == "web":
            resolved_channel = "ide"
            resolved_ide_ctx = {
                "workspace_path": ctx.workspace_path or "",
                "active_file_path": ctx.active_file_path or "",
                "open_files": list(ctx.open_files or []),
                "resolved_workspace": ctx.resolved_workspace or "",
            }
            return resolved_channel, resolved_ide_ctx

        return source_channel, (ide_context or {})

    def _detect_friend_rename(
        self,
        user_message: str,
        sender_info: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Détecte si le propriétaire veut renommer un ami Telegram."""
        if sender_info and not sender_info["is_owner"]:
            return None
        if not self.memory:
            return None

        rename_patterns = [
            re.compile(r"appelle[- ](?:mon )?(?:ami |contact )?(.+?)\s+(?:comme|par)\s+(.+)", re.I),
            re.compile(r"renomme[- ](?:l'ami |le contact )?(.+?)\s+en\s+(.+)", re.I),
            re.compile(r"(?:mon ami|le contact)\s+(.+?)\s+(?:s'appelle|se nomme)(?:\s+en fait)?\s+(.+)", re.I),
            re.compile(r"le nom de\s+(.+?)\s+c'est\s+(.+)", re.I),
        ]

        for pattern in rename_patterns:
            match = pattern.search(user_message)
            if match:
                old_query = match.group(1).strip().lower()
                new_name = match.group(2).strip().rstrip(".!?,")
                if not new_name:
                    continue
                result = self._apply_friend_rename(old_query, new_name)
                if result:
                    return f"✅ J'ai renommé **{result['old']}** en **{result['new']}** dans ma mémoire !"
        return None

    def _detect_self_introduction(
        self,
        user_message: str,
        sender_info: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Détecte si un interlocuteur Telegram se présente lui-même."""
        if not sender_info or sender_info["is_owner"]:
            return None
        if not self.memory:
            return None

        intro_patterns = [
            re.compile(r"(?:je\s+suis|je\s+m[' ]appelle|mon\s+(?:nom|pr[eé]nom)\s+c[' ]est)\s+(.+)", re.I),
            re.compile(r"(?:appelle[- ]moi|dis[- ]moi)\s+(.+)", re.I),
            re.compile(r"(?:moi\s+c[' ]est|c[' ]est)\s+(.+?)(?:\s*[.!,]|$)", re.I),
        ]

        claimed_name: Optional[str] = None
        for pattern in intro_patterns:
            match = pattern.search(user_message)
            if match:
                raw = match.group(1).strip().rstrip(".!?,;:")
                if raw and len(raw) <= 30 and " " not in raw or len(raw.split()) <= 3:
                    claimed_name = raw
                    break

        if not claimed_name:
            return None

        owner_name = (self.memory.get_fact("user_name") or "").strip().lower()
        owner_prenom = (self.memory.get_fact("prénom_utilisateur") or "").strip().lower()
        owner_aliases = {n for n in [owner_name, owner_prenom] if n}

        if claimed_name.lower() in owner_aliases:
            logger.warning(
                f"📱 Tentative d'usurpation: ID={sender_info['tg_id']} prétend être '{claimed_name}' (propriétaire)"
            )
            return (
                f"⚠️ Désolée, je sais que tu n'es pas {claimed_name.title()} — "
                f"c'est mon créateur et je le reconnais par son compte Telegram. "
                f"Dis-moi ton vrai prénom et je m'en souviendrai ! 😊"
            )

        tg_id = sender_info["tg_id"]
        current_name = self.memory.get_fact(f"telegram_{tg_id}_name") or ""
        if current_name.lower() == claimed_name.lower():
            return None

        old_name = current_name or "Inconnu"
        self.memory.learn_fact(f"telegram_{tg_id}_name", claimed_name)
        logger.info(f"📱 Auto-présentation Telegram: {old_name} → {claimed_name} (ID={tg_id})")

        sender_info["name"] = claimed_name

        return (
            f"✅ Enchanté(e) **{claimed_name}** ! Je me souviendrai de toi. "
            f"Tu es un(e) ami(e) de {owner_name.title() or 'mon utilisateur'}, bienvenue ! 😊"
        )

    def _apply_friend_rename(self, old_name_query: str, new_name: str) -> Optional[Dict[str, str]]:
        """Cherche un ami Telegram par nom partiel et le renomme en mémoire."""
        if not self.memory:
            return None
        known_raw = self.memory.get_fact("telegram_known_ids") or ""
        known_ids = [i for i in known_raw.split(",") if i]
        for tid in known_ids:
            current_name = self.memory.get_fact(f"telegram_{tid}_name")
            if current_name and old_name_query in current_name.lower():
                self.memory.learn_fact(f"telegram_{tid}_name", new_name)
                logger.info(f"📱 Ami Telegram renommé: {current_name} → {new_name} (ID={tid})")
                return {"old": current_name, "new": new_name, "tg_id": tid}
        return None

    def _load_discord_user_context(self, user_id: str, channel_id: str, username: str = ""):
        """Charge (ou crée) le contexte de conversation d'un utilisateur Discord."""
        ctx_key = f"{channel_id}_{user_id}"
        if ctx_key in self._discord_contexts:
            self._discord_contexts.move_to_end(ctx_key)
            return self._discord_contexts[ctx_key]

        from src.core import ConversationContext
        ctx = ConversationContext(max_messages=20)
        ctx_file = self.data_dir / "discord_contexts" / f"{ctx_key}.json"
        if ctx_file.exists():
            try:
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                for msg in data:
                    if msg.get("role") and msg.get("content"):
                        ctx.add_message(msg["role"], msg["content"])
                logger.debug(f"🎮 Contexte Discord chargé: {username or user_id} dans #{channel_id} ({len(ctx.messages)} msgs)")
            except Exception as e:
                logger.warning(f"Erreur chargement contexte Discord {ctx_key}: {e}")

        # ── structured_state parallèle (V1) ──
        state_file = self.data_dir / "discord_contexts" / f"{ctx_key}.state.json"
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                ctx.structured_state = StructuredState.from_dict(state_data)
            except Exception as e:
                logger.debug(f"structured_state Discord non chargé {ctx_key}: {e}")

        self._discord_contexts[ctx_key] = ctx
        # LRU eviction (P1)
        _max = getattr(self, '_max_contexts', 500)
        while len(self._discord_contexts) > _max:
            self._discord_contexts.popitem(last=False)

        if user_id not in self._discord_users:
            profile_file = self.data_dir / "discord_users" / f"{user_id}.json"
            if profile_file.exists():
                try:
                    self._discord_users[user_id] = json.loads(profile_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"Lecture profil Discord {user_id}: {e}")
            if user_id not in self._discord_users:
                from datetime import datetime
                self._discord_users[user_id] = {
                    "username": username,
                    "user_id": user_id,
                    "first_seen": datetime.now().strftime("%d/%m/%Y"),
                    "last_seen": datetime.now().strftime("%d/%m/%Y"),
                    "message_count": 0,
                }
                logger.info(f"🎮 Nouvel utilisateur Discord: {username} ({user_id})")

        if username and self._discord_users[user_id].get("username") != username:
            self._discord_users[user_id]["username"] = username

        return ctx

    def _save_discord_user_context(self, user_id: str, channel_id: str):
        """Sauvegarde le contexte Discord sur disque."""
        ctx_key = f"{channel_id}_{user_id}"
        with self._identity_lock:
            try:
                ctx = self._discord_contexts.get(ctx_key)
                if ctx:
                    ctx_dir = self.data_dir / "discord_contexts"
                    ctx_dir.mkdir(parents=True, exist_ok=True)
                    data = [{"role": m.role, "content": m.content} for m in ctx.messages]
                    atomic_write_json(ctx_dir / f"{ctx_key}.json", data)
                    # ── structured_state parallèle (V1) ──
                    if hasattr(ctx, 'structured_state') and not ctx.structured_state.is_empty():
                        atomic_write_json(ctx_dir / f"{ctx_key}.state.json", ctx.structured_state.to_dict())
            except Exception as e:
                logger.warning(f"Erreur sauvegarde contexte Discord {ctx_key}: {e}")

            try:
                profile = self._discord_users.get(user_id)
                if profile:
                    from datetime import datetime
                    profile["last_seen"] = datetime.now().strftime("%d/%m/%Y")
                    prof_dir = self.data_dir / "discord_users"
                    prof_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(prof_dir / f"{user_id}.json", profile)
            except Exception as e:
                logger.warning(f"Erreur sauvegarde profil Discord {user_id}: {e}")

    def _get_discord_user_context_block(self, user_id: str) -> str:
        """Retourne un bloc texte décrivant l'utilisateur Discord."""
        profile = self._discord_users.get(user_id)
        if not profile:
            return ""
        name = profile.get("username") or f"utilisateur {user_id}"
        count = profile.get("message_count", 0)
        first = profile.get("first_seen", "?")
        if count == 0:
            relation = "C'est la première fois que tu parles avec cette personne. Accueille-la chaleureusement."
        elif count < 10:
            relation = f"Tu as échangé {count} message(s) avec cette personne depuis le {first}. Tu commences à la connaître."
        else:
            relation = f"Tu connais bien cette personne : {count} messages échangés depuis le {first}. Adapte ton ton à votre relation."
        return f"""[Contexte Discord]
Tu parles avec : {name}
{relation}"""

    def _load_tg_context(self, tg_id: str):
        """Charge (ou crée) le contexte de conversation d'un ami Telegram."""
        if tg_id in self._tg_contexts:
            self._tg_contexts.move_to_end(tg_id)
            return self._tg_contexts[tg_id]

        from src.core import ConversationContext
        ctx = ConversationContext(max_messages=10)
        ctx_file = self.data_dir / "tg_contexts" / f"{tg_id}.json"
        if ctx_file.exists():
            try:
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                for msg in data:
                    if msg.get("role") and msg.get("content"):
                        ctx.add_message(msg["role"], msg["content"])
                logger.debug(f"📱 Contexte Telegram chargé: {tg_id} ({len(ctx.messages)} msgs)")
            except Exception as e:
                logger.warning(f"Erreur chargement contexte Telegram {tg_id}: {e}")

        # ── structured_state parallèle (V1) ──
        state_file = self.data_dir / "tg_contexts" / f"{tg_id}.state.json"
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                ctx.structured_state = StructuredState.from_dict(state_data)
            except Exception as e:
                logger.debug(f"structured_state Telegram non chargé {tg_id}: {e}")

        self._tg_contexts[tg_id] = ctx
        # LRU eviction (P1)
        _max = getattr(self, '_max_contexts', 500)
        while len(self._tg_contexts) > _max:
            self._tg_contexts.popitem(last=False)
        return ctx

    def _save_tg_context(self, tg_id: str, context):
        """Sauvegarde le contexte d'un ami Telegram sur disque (écriture atomique)."""
        with self._identity_lock:
            try:
                ctx_dir = self.data_dir / "tg_contexts"
                ctx_dir.mkdir(parents=True, exist_ok=True)
                data = [{"role": m.role, "content": m.content} for m in context.messages]
                atomic_write_json(ctx_dir / f"{tg_id}.json", data)
                # ── structured_state parallèle (V1) ──
                if hasattr(context, 'structured_state') and not context.structured_state.is_empty():
                    atomic_write_json(ctx_dir / f"{tg_id}.state.json", context.structured_state.to_dict())
            except Exception as e:
                logger.warning(f"Erreur sauvegarde contexte Telegram {tg_id}: {e}")

    def clear_tg_context(self, tg_id: str):
        """Efface le contexte de conversation d'un ami Telegram (RAM + disque)."""
        if tg_id in self._tg_contexts:
            self._tg_contexts[tg_id].clear()
            del self._tg_contexts[tg_id]
        ctx_file = self.data_dir / "tg_contexts" / f"{tg_id}.json"
        try:
            if ctx_file.exists():
                ctx_file.unlink()
        except Exception as e:
            logger.warning(f"Erreur suppression contexte Telegram {tg_id}: {e}")
        logger.info(f"📱 Contexte Telegram effacé: {tg_id}")

    def _load_wa_context(self, phone: str):
        """Charge (ou crée) le contexte de conversation d'un contact WhatsApp."""
        if phone in self._wa_contexts:
            return self._wa_contexts[phone]

        from src.core import ConversationContext
        ctx = ConversationContext(max_messages=10)
        ctx_file = self.data_dir / "wa_contexts" / f"{phone}.json"
        if ctx_file.exists():
            try:
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                for msg in data:
                    if msg.get("role") and msg.get("content"):
                        ctx.add_message(msg["role"], msg["content"])
                logger.debug(f"📱 Contexte WhatsApp chargé: {phone} ({len(ctx.messages)} msgs)")
            except Exception as e:
                logger.warning(f"Erreur chargement contexte WhatsApp {phone}: {e}")

        # ── structured_state parallèle (V1) ──
        state_file = self.data_dir / "wa_contexts" / f"{phone}.state.json"
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                ctx.structured_state = StructuredState.from_dict(state_data)
            except Exception as e:
                logger.debug(f"structured_state WhatsApp non chargé {phone}: {e}")

        self._wa_contexts[phone] = ctx
        _max = getattr(self, '_max_contexts', 500)
        while len(self._wa_contexts) > _max:
            oldest = next(iter(self._wa_contexts))
            del self._wa_contexts[oldest]
        return ctx

    def _save_wa_context(self, phone: str, context):
        """Sauvegarde le contexte d'un contact WhatsApp sur disque (écriture atomique)."""
        with self._identity_lock:
            try:
                ctx_dir = self.data_dir / "wa_contexts"
                ctx_dir.mkdir(parents=True, exist_ok=True)
                data = [{"role": m.role, "content": m.content} for m in context.messages]
                atomic_write_json(ctx_dir / f"{phone}.json", data)
                # ── structured_state parallèle (V1) ──
                if hasattr(context, 'structured_state') and not context.structured_state.is_empty():
                    atomic_write_json(ctx_dir / f"{phone}.state.json", context.structured_state.to_dict())
            except Exception as e:
                logger.warning(f"Erreur sauvegarde contexte WhatsApp {phone}: {e}")

    def clear_wa_context(self, phone: str):
        """Efface le contexte de conversation d'un contact WhatsApp (RAM + disque)."""
        if phone in self._wa_contexts:
            self._wa_contexts[phone].clear()
            del self._wa_contexts[phone]
        ctx_file = self.data_dir / "wa_contexts" / f"{phone}.json"
        try:
            if ctx_file.exists():
                ctx_file.unlink()
        except Exception as e:
            logger.warning(f"Erreur suppression contexte WhatsApp {phone}: {e}")
        logger.info(f"📱 Contexte WhatsApp effacé: {phone}")

    # ── Contexte Web (persistance disque, clé par utilisateur) ───────────────
    # Phase 0 : _WEB_CONTEXT_KEY = "default" remplacé par une clé dérivée du
    # RuntimeContext courant. Le fichier legacy web_contexts/default.json reste
    # lisible uniquement pour local:owner (migration Phase -1).

    _WEB_CONTEXT_LEGACY_KEY = "default"

    @staticmethod
    def _resolve_web_context_key(
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        client: Optional[str] = None,
    ) -> str:
        """Construit une clé de contexte web par utilisateur.

        Priorité :
          1. web:<user_id>:<conversation_id>
          2. web:<user_id>:<client>
          3. fallback legacy : "default"   (local:owner uniquement)
        """
        from src.runtime.context import FALLBACK_USER_ID
        uid = (user_id or "").strip() or FALLBACK_USER_ID
        conv = (conversation_id or "").strip()
        cli = (client or "").strip()

        if conv:
            raw = f"web:{uid}:{conv}"
        elif cli:
            raw = f"web:{uid}:{cli}"
        else:
            raw = f"web:{uid}:default"

        # Sanitize : pas de slash ni de caractère dangereux dans le nom de fichier
        safe = raw.replace("/", "_").replace("\\", "_").replace(":", "__")
        return safe

    def _get_web_context_key(self) -> str:
        """Retourne la clé active en lisant le RuntimeContext courant si disponible."""
        try:
            from src.runtime.context import get_current_runtime_context
            ctx = get_current_runtime_context()
            if ctx is not None:
                return self._resolve_web_context_key(
                    user_id=ctx.user_id,
                    conversation_id=ctx.conversation_id,
                    client=ctx.client,
                )
        except Exception:
            pass
        return self._WEB_CONTEXT_LEGACY_KEY

    def _load_web_context(self, context_key: Optional[str] = None):
        """Charge (ou crée) le contexte de conversation web depuis le disque."""
        from src.core import ConversationContext
        _explicit_key = context_key is not None
        key = context_key or self._get_web_context_key()
        ctx = ConversationContext(max_messages=10)
        ctx_dir = self.data_dir / "web_contexts"

        # Tentative sur la clé courante
        ctx_file = ctx_dir / f"{key}.json"
        # Fallback legacy UNIQUEMENT pour local:owner et seulement quand la clé
        # est dérivée du RuntimeContext courant (pas fournie explicitement).
        # Si context_key est fourni explicitement, on ne fait jamais de fallback
        # vers le fichier d'un autre utilisateur.
        if not ctx_file.exists() and key != self._WEB_CONTEXT_LEGACY_KEY and not _explicit_key:
            try:
                from src.runtime.context import get_current_runtime_context, FALLBACK_USER_ID
                ctx_rt = get_current_runtime_context()
                _uid = (ctx_rt.user_id if ctx_rt else None) or FALLBACK_USER_ID
            except Exception:
                _uid = "local:owner"
            if _uid == "local:owner":
                legacy_file = ctx_dir / f"{self._WEB_CONTEXT_LEGACY_KEY}.json"
                if legacy_file.exists():
                    ctx_file = legacy_file
                    logger.debug(f"🌐 Contexte Web : fallback legacy pour local:owner")

        if ctx_file.exists():
            try:
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                for msg in data:
                    if msg.get("role") and msg.get("content"):
                        ctx.add_message(msg["role"], msg["content"])
                logger.debug(f"🌐 Contexte Web chargé (key={key}, {len(ctx.messages)} msgs)")
            except Exception as e:
                logger.warning(f"Erreur chargement contexte Web: {e}")
        # ── structured_state parallèle (V1) ──
        state_file = ctx_dir / f"{key}.state.json"
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                ctx.structured_state = StructuredState.from_dict(state_data)
            except Exception as e:
                logger.debug(f"structured_state Web non chargé: {e}")
        return ctx

    def _save_web_context(self, context, context_key: Optional[str] = None):
        """Sauvegarde le contexte web sur disque (écriture atomique)."""
        key = context_key or self._get_web_context_key()
        with self._identity_lock:
            try:
                ctx_dir = self.data_dir / "web_contexts"
                ctx_dir.mkdir(parents=True, exist_ok=True)
                data = [{"role": m.role, "content": m.content} for m in context.messages]
                atomic_write_json(ctx_dir / f"{key}.json", data)
                # ── structured_state parallèle (V1) ──
                if hasattr(context, 'structured_state') and not context.structured_state.is_empty():
                    atomic_write_json(
                        ctx_dir / f"{key}.state.json",
                        context.structured_state.to_dict(),
                    )
            except Exception as e:
                logger.warning(f"Erreur sauvegarde contexte Web: {e}")

    def clear_web_context(self, context_key: Optional[str] = None):
        """Efface le contexte de conversation web (disque)."""
        key = context_key or self._get_web_context_key()
        ctx_dir = self.data_dir / "web_contexts"
        for suffix in (".json", ".state.json"):
            ctx_file = ctx_dir / f"{key}{suffix}"
            try:
                if ctx_file.exists():
                    ctx_file.unlink()
            except Exception as e:
                logger.warning(f"Erreur suppression contexte Web {key}: {e}")
        logger.info(f"🌐 Contexte Web effacé (key={key})")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
