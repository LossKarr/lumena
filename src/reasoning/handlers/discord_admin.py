"""
discord_admin.py - Handlers d'administration Discord pour Lumena.

Permet à Lumena de construire et gérer son serveur Discord en totale autonomie :
créer channels/catégories, rôles, embeds, gérer les membres, etc.

Handlers (24):
    discord_server_info, discord_server_configure,
    discord_list_channels, discord_create_channel, discord_create_category,
    discord_modify_channel, discord_delete_channel,
    discord_send, discord_send_embed, discord_fetch_messages,
    discord_pin, discord_unpin, discord_delete_message,
    discord_list_roles, discord_create_role, discord_delete_role,
    discord_assign_role, discord_remove_role,
    discord_list_members, discord_kick, discord_ban, discord_unban,
    discord_create_invite, discord_list_invites.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── helpers ────────────────────────────────────────────────────────────────

def _ok_msg(data: Dict[str, Any], label: str) -> str:
    """Formate un retour succès lisible."""
    id_ = data.get("id", "")
    name = data.get("name", "") or data.get("title", "")
    return f"✅ {label}" + (f" — **{name}**" if name else "") + (f" (id: {id_})" if id_ else "")


# Cache module-level pour le guild auto-détecté (évite un appel API à chaque commande)
_auto_guild_id: str | None = None

# Chemin du fichier de persistance (chargé au démarrage, mis à jour à chaque auto-detect)
def _discord_state_path() -> Path:
    # `DATA_DIR` fait EXACTEMENT ceci : `LUMENA_DATA_DIR` sinon `ROOT_DIR/data`.
    # La reconstruction a la main comptait les `parents[3]`, ce qui casse au
    # premier deplacement du fichier — et c'est le seul site du depot que le
    # garde des chemins visait vraiment.
    from src.utils.paths import DATA_DIR
    return DATA_DIR / "memory" / "discord_state.json"


def _load_discord_state() -> None:
    """Charge le guild_id persisté depuis le démarrage précédent."""
    global _auto_guild_id
    if _auto_guild_id:
        return
    try:
        import json
        p = _discord_state_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            gid = data.get("guild_id", "")
            if gid:
                _auto_guild_id = gid
                logger.debug("[Discord] Guild restauré depuis discord_state.json: {}", gid)
    except Exception as e:
        logger.debug("[Discord] Chargement discord_state.json échoué: {}", e)


def _save_discord_state(guild_id: str) -> None:
    """Persiste le guild_id pour les sessions suivantes."""
    try:
        import json
        p = _discord_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"guild_id": guild_id}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("[Discord] Sauvegarde discord_state.json échouée: {}", e)


# Charger l'état persisté dès l'import du module
_load_discord_state()


def _resolve_guild_id(guild_id: str | None) -> str:
    """Résout le guild_id : valeur explicite > env > auto-detect depuis le bot connecté."""
    import os
    if guild_id:
        return guild_id
    env_gid = os.getenv("DISCORD_GUILD_ID", "")
    if env_gid:
        return env_gid
    # Auto-detect : le guild_id sera résolu au premier appel async
    global _auto_guild_id
    return _auto_guild_id or ""


async def _resolve_guild_id_async(guild_id: str | None) -> str:
    """Version async de _resolve_guild_id — auto-détecte le guild via l'API si nécessaire."""
    import os
    if guild_id:
        return guild_id
    env_gid = os.getenv("DISCORD_GUILD_ID", "")
    if env_gid:
        return env_gid

    global _auto_guild_id
    if _auto_guild_id:
        return _auto_guild_id

    # Auto-detect : appeler /users/@me/guilds pour trouver le premier serveur
    tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
    if not tok:
        return ""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            headers = {"Authorization": f"Bot {tok}"}
            async with s.get("https://discord.com/api/v10/users/@me/guilds", headers=headers) as resp:
                if resp.status == 200:
                    guilds = await resp.json()
                    if guilds and isinstance(guilds, list):
                        _auto_guild_id = str(guilds[0]["id"])
                        logger.info("[Discord] Guild auto-détecté: {} ({})", guilds[0].get("name"), _auto_guild_id)
                        _save_discord_state(_auto_guild_id)
                        return _auto_guild_id
    except Exception as e:
        logger.debug("[Discord] Auto-detect guild échoué: {}", e)
    return ""


# Cache nom → (id, type) par guild (évite les appels API répétés)
# type 0 = text, 2 = voice, 4 = category, 5 = announcement, 13 = stage, 15 = forum
_channel_name_cache: Dict[str, Dict[str, tuple]] = {}  # guild_id → {name: (id, type)}
# Types considérés comme "texte" (on peut y envoyer des messages)
_TEXT_CHANNEL_TYPES = {0, 5, 10, 11, 12, 15}


async def _resolve_channel_id(channel_id: str, channel_name: str | None = None,
                               guild_id: str | None = None) -> str:
    """
    Retourne le vrai channel_id.
    Si channel_id semble invalide et qu'un channel_name est fourni,
    résout l'ID via l'API Discord (avec cache).
    Préfère les canaux TEXTE aux canaux vocaux en cas d'ambiguïté.
    """
    import aiohttp, os
    DISCORD_API = "https://discord.com/api/v10"
    tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
    headers = {"Authorization": f"Bot {tok}"}

    # Si on a déjà un channel_id qui ressemble valide et pas de name, on l'utilise
    if channel_id and not channel_name:
        return channel_id

    gid = await _resolve_guild_id_async(guild_id)
    if not gid:
        return channel_id  # pas de guild → on tente avec ce qu'on a

    # Mise à jour du cache si vide ou si on cherche un nom inconnu
    _lookup_key = channel_name.lower().strip("#").strip() if channel_name else ""
    if gid not in _channel_name_cache or (_lookup_key and _lookup_key not in _channel_name_cache.get(gid, {})):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{DISCORD_API}/guilds/{gid}/channels", headers=headers) as resp:
                    channels = await resp.json()
            if isinstance(channels, list):
                _channel_name_cache[gid] = {
                    ch["name"].lower(): (ch["id"], ch.get("type", 0))
                    for ch in channels if isinstance(ch, dict)
                }
        except Exception as e:
            logger.debug(f"Discord channel cache build: {e}")

    # Résolution par nom si fourni
    if channel_name:
        cache = _channel_name_cache.get(gid, {})
        lookup = channel_name.lower().strip("#").strip()
        # Match exact
        if lookup in cache:
            return cache[lookup][0]
        # Recherche partielle — collecter TOUS les matches, préférer texte
        _matches = []
        for name, (cid, ctype) in cache.items():
            if lookup in name or name in lookup:
                _matches.append((cid, ctype, name))
        if _matches:
            # Trier : texte d'abord, puis par longueur de nom (plus court = plus précis)
            _matches.sort(key=lambda m: (0 if m[1] in _TEXT_CHANNEL_TYPES else 1, len(m[2])))
            chosen = _matches[0]
            if len(_matches) > 1:
                logger.info(f"Discord resolve '{lookup}': {len(_matches)} matches, choisi #{chosen[2]} (type={chosen[1]})")
            return chosen[0]

    return channel_id


# ─── Handlers ───────────────────────────────────────────────────────────────

async def discord_list_guilds(ctx: HandlerContext) -> HandlerResult:
    """Liste tous les serveurs Discord accessibles au bot (étape zéro pour obtenir un guild_id)."""
    try:
        from ...tools.discord_admin import list_guilds
        r = await list_guilds()
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur Discord: {r.get('error')}", handler_name="discord_list_guilds")
        guilds = r.get("data", [])
        if not guilds:
            return HandlerResult.ok("Aucun serveur Discord accessible pour ce bot.", handler_name="discord_list_guilds")
        lines = ["**Serveurs Discord accessibles :**"]
        for g in guilds:
            name = g.get("name", "?")
            gid = g.get("id", "?")
            owner = "👑 " if g.get("owner") else ""
            lines.append(f"- {owner}**{name}** — ID: `{gid}`")
        lines.append("\nUtilise l'ID souhaité dans les autres commandes Discord.")
        return HandlerResult.ok("\n".join(lines), handler_name="discord_list_guilds")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="discord_list_guilds")


async def discord_server_info(ctx: HandlerContext, *, guild_id: str = None) -> HandlerResult:
    """Récupère les infos du serveur Discord (nom, membres, channels, etc.)."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import get_guild
        r = await get_guild(guild_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        lines = [
            f"🏠 **{r.get('name')}**",
            f"- ID: {r.get('id')}",
            f"- Description: {r.get('description') or 'aucune'}",
            f"- Membres approx: {r.get('approximate_member_count', '?')}",
            f"- Owner ID: {r.get('owner_id')}",
            f"- Boosting level: {r.get('premium_tier', 0)}",
        ]
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_server_configure(ctx: HandlerContext, *, guild_id: str = None,
                                   name: str = None, description: str = None) -> HandlerResult:
    """Modifie le nom et/ou la description du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import modify_guild
        r = await modify_guild(guild_id, name=name, description=description)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        changes = []
        if name:
            changes.append(f"nom → {name}")
        if description is not None:
            changes.append(f"description → {description[:60]}")
        return HandlerResult.ok(f"✅ Serveur modifié: {', '.join(changes)}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Channels ───────────────────────────────────────────────────────────────

async def discord_list_channels(ctx: HandlerContext, *, guild_id: str = None) -> HandlerResult:
    """Liste tous les channels et catégories du serveur."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import _request
        r = await _request("GET", f"/guilds/{guild_id}/channels")
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        # L'API renvoie un array — on le récupère depuis la réponse brute
        import aiohttp, os
        DISCORD_API = "https://discord.com/api/v10"
        tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
        headers = {"Authorization": f"Bot {tok}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/guilds/{guild_id}/channels", headers=headers) as resp:
                channels = await resp.json()
        type_labels = {0: "💬", 2: "🔊", 4: "📁", 5: "📢", 13: "🎤", 15: "🗂️"}
        lines = []
        # Séparer catégories et salons
        categories = {c["id"]: c for c in channels if c["type"] == 4}
        cat_children: dict = {cid: [] for cid in categories}
        orphans = []
        for ch in channels:
            if ch["type"] == 4:
                continue
            pid = ch.get("parent_id")
            if pid and pid in cat_children:
                cat_children[pid].append(ch)
            else:
                orphans.append(ch)
        # Mettre à jour le cache nom→(id, type) pendant qu'on a la liste
        # IMPORTANT: doit être un tuple (id, type) comme _resolve_channel_id l'attend
        _channel_name_cache[guild_id] = {
            ch["name"].lower(): (ch["id"], ch.get("type", 0))
            for ch in channels
            if isinstance(ch, dict) and ch.get("type") != 4
        }
        # Afficher catégories dans l'ordre, avec leurs IDs et salons enfants
        for cat in sorted(categories.values(), key=lambda x: x.get("position", 0)):
            children = sorted(cat_children[cat["id"]], key=lambda x: x.get("position", 0))
            lines.append(f"\n📁 **{cat['name'].upper()}** (id: {cat['id']})")
            for ch in children:
                icon = type_labels.get(ch["type"], "•")
                lines.append(f"  {icon} {ch['name']} (id: {ch['id']})")
        # Salons sans catégorie
        if orphans:
            lines.append("\n📌 **SANS CATÉGORIE**")
            for ch in sorted(orphans, key=lambda x: x.get("position", 0)):
                icon = type_labels.get(ch["type"], "•")
                lines.append(f"  {icon} {ch['name']} (id: {ch['id']})")
        return HandlerResult.ok(f"📑 {len(channels)} channels:\n" + "\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_create_category(ctx: HandlerContext, *, guild_id: str = None,
                                  name: str = None, position: int = None) -> HandlerResult:
    """Crée une catégorie (dossier) dans le serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import create_channel
        r = await create_channel(guild_id, name, channel_type="category", position=position)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"📁 Catégorie créée: **{r.get('name')}** (id: {r.get('id')})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_create_channel(ctx: HandlerContext, *, guild_id: str = None, name: str = None,
                                 channel_type: str = "text", topic: str = None,
                                 parent_id: str = None, position: int = None) -> HandlerResult:
    """Crée un channel dans le serveur (text, voice, announcement, forum, stage)."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import create_channel
        r = await create_channel(guild_id, name, channel_type=channel_type,
                                 topic=topic, parent_id=parent_id, position=position)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        icon = {"text": "💬", "voice": "🔊", "announcement": "📢", "forum": "🗂️"}.get(channel_type, "•")
        return HandlerResult.ok(f"{icon} Channel créé: **#{r.get('name')}** (id: {r.get('id')})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_modify_channel(ctx: HandlerContext, *, channel_id: str,
                                 name: str = None, topic: str = None,
                                 position: int = None) -> HandlerResult:
    """Renomme un channel, change son topic ou sa position."""
    try:
        from ...tools.discord_admin import modify_channel
        r = await modify_channel(channel_id, name=name, topic=topic, position=position)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"✅ Channel modifié: **#{r.get('name')}**")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_delete_channel(ctx: HandlerContext, *, channel_id: str) -> HandlerResult:
    """Supprime un channel ou une catégorie Discord."""
    try:
        from ...tools.discord_admin import delete_channel
        r = await delete_channel(channel_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"🗑️ Channel supprimé (id: {channel_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Messages ────────────────────────────────────────────────────────────────

async def discord_send(ctx: HandlerContext, *, channel_id: str = None,
                       channel_name: str = None, guild_id: str = None,
                       content: str = "") -> HandlerResult:
    """Envoie un message texte dans un channel Discord.
    Accepte channel_id OU channel_name (résolution automatique).
    """
    try:
        if not content:
            return HandlerResult.fail("Paramètre 'content' requis (le message à envoyer)")
        if not channel_id and not channel_name:
            return HandlerResult.fail("Fournir channel_id OU channel_name")
        resolved = await _resolve_channel_id(channel_id or "", channel_name, guild_id)
        if not resolved:
            return HandlerResult.fail("Impossible de résoudre le channel. Utiliser discord_list_channels pour obtenir les IDs.")
        from ...tools.discord_admin import send_message
        r = await send_message(resolved, content=content)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        _ch_label = f"#{channel_name}" if channel_name else f"channel:{resolved}"
        return HandlerResult.ok(f"📨 Message envoyé dans {_ch_label} (id: {r.get('id')})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_send_embed(ctx: HandlerContext, *, channel_id: str = None,
                             channel_name: str = None, guild_id: str = None,
                             title: str = "",
                             description: str = "", color: int = 0x7289DA,
                             fields: List[Dict[str, Any]] = None,
                             footer: str = None,
                             image_url: str = None) -> HandlerResult:
    """Envoie un embed riche (annonce, règles, présentation) dans un channel Discord.
    Accepte channel_id OU channel_name (résolution automatique).
    """
    try:
        if not channel_id and not channel_name:
            return HandlerResult.fail("Fournir channel_id OU channel_name")
        resolved = await _resolve_channel_id(channel_id or "", channel_name, guild_id)
        if not resolved:
            return HandlerResult.fail("Impossible de résoudre le channel. Utiliser discord_list_channels pour obtenir les IDs.")
        from ...tools.discord_admin import send_embed
        r = await send_embed(resolved, title=title, description=description,
                             color=color, fields=fields, footer=footer, image_url=image_url)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"📣 Embed envoyé: **{title}** (id: {r.get('id')})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_fetch_messages(ctx: HandlerContext, *, channel_id: str = None,
                                 channel_name: str = None,
                                 guild_id: str = None,
                                 limit: int = 20) -> HandlerResult:
    """Récupère les derniers messages d'un channel Discord.
    Utilise channel_id (depuis discord_list_channels) OU channel_name (résolution auto).
    """
    try:
        import aiohttp, os
        DISCORD_API = "https://discord.com/api/v10"
        tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
        headers = {"Authorization": f"Bot {tok}"}

        if not channel_id and not channel_name:
            return HandlerResult.fail("Fournir channel_id (depuis discord_list_channels) ou channel_name")

        # Résolution de l'ID (par nom si nécessaire)
        resolved_id = await _resolve_channel_id(
            channel_id or "", channel_name,
            guild_id or await _resolve_guild_id_async(None)
        )
        if not resolved_id:
            return HandlerResult.fail("Impossible de résoudre le channel. Utiliser discord_list_channels d'abord.")

        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/channels/{resolved_id}/messages?limit={min(limit,100)}",
                             headers=headers) as resp:
                messages = await resp.json()
        if isinstance(messages, dict) and messages.get("message"):
            err = messages["message"]
            # Si "Unknown Channel" et qu'on a un nom, le cache est peut-être périmé → on le vide et réessaie
            if "Unknown Channel" in err and channel_name:
                gid = guild_id or await _resolve_guild_id_async(None)
                _channel_name_cache.pop(gid, None)
                resolved_id = await _resolve_channel_id("", channel_name, gid)
                if resolved_id:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{DISCORD_API}/channels/{resolved_id}/messages?limit={min(limit,100)}",
                                         headers=headers) as resp:
                            messages = await resp.json()
                    if isinstance(messages, dict) and messages.get("message"):
                        return HandlerResult.fail(f"Erreur: {messages['message']}")
                else:
                    return HandlerResult.fail(f"Channel '{channel_name}' introuvable. Vérifier discord_list_channels.")
            else:
                return HandlerResult.fail(f"Erreur: {err}")
        lines = [f"📜 {len(messages)} derniers messages:\n"]
        for m in messages[:20]:
            author = m.get("author", {}).get("username", "?")
            content = m.get("content", "")[:120]
            lines.append(f"**{author}**: {content}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_pin(ctx: HandlerContext, *, channel_id: str, message_id: str) -> HandlerResult:
    """Épingle un message dans un channel Discord."""
    try:
        from ...tools.discord_admin import pin_message
        r = await pin_message(channel_id, message_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"📌 Message épinglé (id: {message_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_unpin(ctx: HandlerContext, *, channel_id: str, message_id: str) -> HandlerResult:
    """Désépingle un message dans un channel Discord."""
    try:
        from ...tools.discord_admin import unpin_message
        r = await unpin_message(channel_id, message_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"📌 Message désépinglé (id: {message_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_delete_message(ctx: HandlerContext, *, channel_id: str, message_id: str) -> HandlerResult:
    """Supprime un message dans un channel Discord."""
    try:
        from ...tools.discord_admin import delete_message
        r = await delete_message(channel_id, message_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"🗑️ Message supprimé (id: {message_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Permissions channels ────────────────────────────────────────────────────

async def discord_set_channel_permissions(
    ctx: HandlerContext, *,
    channel_id: str,
    overwrite_id: str,
    allow: int = 0,
    deny: int = 0,
    overwrite_type: int = 0,
) -> HandlerResult:
    """
    Pose un permission overwrite sur un channel ou une catégorie Discord.
    Bits utiles : VIEW_CHANNEL=0x400, SEND_MESSAGES=0x800, READ_MESSAGE_HISTORY=0x10000.
    overwrite_type : 0=rôle, 1=membre.
    Exemple — rendre invisible à @everyone : overwrite_id=guild_id, deny=1024, allow=0.
    """
    try:
        from ...tools.discord_admin import set_channel_permission
        r = await set_channel_permission(
            channel_id, overwrite_id,
            allow=allow, deny=deny, overwrite_type=overwrite_type,
        )
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur Discord: {r.get('error')}", handler_name="discord_set_channel_permissions")
        action = "autorisé" if allow and not deny else "refusé" if deny and not allow else "modifié"
        return HandlerResult.ok(
            f"✅ Permissions {action} sur channel `{channel_id}` pour overwrite `{overwrite_id}`\n"
            f"  allow={allow} | deny={deny}",
            handler_name="discord_set_channel_permissions",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="discord_set_channel_permissions")


# ─── Rôles ──────────────────────────────────────────────────────────────────

async def discord_list_roles(ctx: HandlerContext, *, guild_id: str = None) -> HandlerResult:
    """Liste les rôles du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        import aiohttp, os
        DISCORD_API = "https://discord.com/api/v10"
        tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
        headers = {"Authorization": f"Bot {tok}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/guilds/{guild_id}/roles", headers=headers) as resp:
                roles = await resp.json()
        if isinstance(roles, dict):
            return HandlerResult.fail(f"Erreur: {roles.get('message', roles)}")
        lines = [f"🎭 {len(roles)} rôles:\n"]
        for ro in sorted(roles, key=lambda x: -x.get("position", 0)):
            color = f"#{ro.get('color', 0):06X}" if ro.get("color") else "no color"
            hoisted = "🔺" if ro.get("hoist") else ""
            lines.append(f"  {hoisted}**{ro['name']}** ({color}) — id: {ro['id']}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_create_role(ctx: HandlerContext, *, guild_id: str = None, name: str,
                              color: int = 0, hoist: bool = False,
                              mentionable: bool = False,
                              permissions: List[str] = None) -> HandlerResult:
    """Crée un rôle dans le serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import create_role
        r = await create_role(guild_id, name=name, color=color, hoist=hoist,
                              mentionable=mentionable, permissions=permissions or [])
        if not r.get("ok"):
            err = r.get('error', '')
            details = r.get('errors', {})
            return HandlerResult.fail(f"Erreur Discord: {err}" + (f" | {details}" if details else ""))
        return HandlerResult.ok(f"🎭 Rôle créé: **{r.get('name')}** (id: {r.get('id')})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_delete_role(ctx: HandlerContext, *, guild_id: str = None, role_id: str) -> HandlerResult:
    """Supprime un rôle du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import delete_role
        r = await delete_role(guild_id, role_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"🗑️ Rôle supprimé (id: {role_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_assign_role(ctx: HandlerContext, *, guild_id: str = None,
                              user_id: str, role_id: str) -> HandlerResult:
    """Assigne un rôle à un membre du serveur."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import assign_role
        r = await assign_role(guild_id, user_id, role_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"✅ Rôle {role_id} assigné à {user_id}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_remove_role(ctx: HandlerContext, *, guild_id: str = None,
                              user_id: str, role_id: str) -> HandlerResult:
    """Retire un rôle à un membre du serveur."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import remove_role
        r = await remove_role(guild_id, user_id, role_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"✅ Rôle {role_id} retiré à {user_id}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Membres ────────────────────────────────────────────────────────────────

async def discord_list_members(ctx: HandlerContext, *, guild_id: str = None, limit: int = 50) -> HandlerResult:
    """Liste les membres du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        import aiohttp, os
        DISCORD_API = "https://discord.com/api/v10"
        tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
        headers = {"Authorization": f"Bot {tok}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/guilds/{guild_id}/members?limit={min(limit,1000)}",
                             headers=headers) as resp:
                members = await resp.json()
        if isinstance(members, dict):
            return HandlerResult.fail(f"Erreur: {members.get('message', members)}")
        lines = [f"👥 {len(members)} membres:\n"]
        for m in members[:30]:
            user = m.get("user", {})
            nick = m.get("nick") or user.get("username", "?")
            roles = len(m.get("roles", []))
            bot_tag = " 🤖" if user.get("bot") else ""
            lines.append(f"  **{nick}**{bot_tag} ({roles} rôles) — id: {user.get('id')}")
        if len(members) > 30:
            lines.append(f"  ... et {len(members) - 30} autres")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_kick(ctx: HandlerContext, *, guild_id: str = None, user_id: str) -> HandlerResult:
    """Expulse (kick) un membre du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import kick_member
        r = await kick_member(guild_id, user_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"👢 Membre expulsé (id: {user_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_ban(ctx: HandlerContext, *, guild_id: str = None, user_id: str,
                      reason: str = "", delete_days: int = 0) -> HandlerResult:
    """Bannit un membre du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import ban_member
        r = await ban_member(guild_id, user_id, reason=reason, delete_message_days=delete_days)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"🔨 Membre banni (id: {user_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_unban(ctx: HandlerContext, *, guild_id: str = None, user_id: str) -> HandlerResult:
    """Retire le ban d'un membre du serveur Discord."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import unban_member
        r = await unban_member(guild_id, user_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        return HandlerResult.ok(f"✅ Ban levé (id: {user_id})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Invitations ────────────────────────────────────────────────────────────

async def discord_create_invite(ctx: HandlerContext, *, channel_id: str,
                                max_age: int = 86400, max_uses: int = 0) -> HandlerResult:
    """Crée un lien d'invitation pour le serveur Discord."""
    try:
        from ...tools.discord_admin import create_invite
        r = await create_invite(channel_id, max_age=max_age, max_uses=max_uses)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        code = r.get("code", "")
        expire = f"{max_age // 3600}h" if max_age else "permanent"
        uses = f"{max_uses} uses max" if max_uses else "illimité"
        return HandlerResult.ok(
            f"🔗 Invitation créée: https://discord.gg/{code}\n"
            f"- Expire: {expire}\n"
            f"- Utilisations: {uses}"
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def discord_list_invites(ctx: HandlerContext, *, guild_id: str = None) -> HandlerResult:
    """Liste toutes les invitations actives du serveur."""
    try:
        guild_id = await _resolve_guild_id_async(guild_id)
        from ...tools.discord_admin import list_invites
        r = await list_invites(guild_id)
        if not r.get("ok"):
            return HandlerResult.fail(f"Erreur: {r.get('error')}")
        import aiohttp, os
        DISCORD_API = "https://discord.com/api/v10"
        tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
        headers = {"Authorization": f"Bot {tok}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/guilds/{guild_id}/invites", headers=headers) as resp:
                invites = await resp.json()
        if isinstance(invites, dict):
            return HandlerResult.fail(f"Erreur: {invites.get('message', invites)}")
        lines = [f"🔗 {len(invites)} invitations:\n"]
        for inv in invites:
            code = inv.get("code", "")
            uses = inv.get("uses", 0)
            ch = inv.get("channel", {}).get("name", "?")
            lines.append(f"  discord.gg/{code} — #{ch} — {uses} utilisations")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Handler Definitions ─────────────────────────────────────────────────────

def get_discord_admin_handler_defs() -> List[HandlerDef]:
    """Retourne les handlers d'administration Discord."""
    return [
        HandlerDef(
            name="discord_list_guilds",
            description="Liste tous les serveurs Discord accessibles au bot. À appeler en premier pour obtenir un guild_id quand il n'est pas connu.",
            parameters={"properties": {}, "required": []},
            handler=discord_list_guilds,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_server_info",
            description="Affiche les infos du serveur Discord (nom, membres, boost level, owner). guild_id est auto-détecté si absent.",
            parameters={"properties": {"guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"}}, "required": []},
            handler=discord_server_info,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_server_configure",
            description="Modifie le nom ou la description du serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "name": {"type": "string", "description": "Nouveau nom du serveur"},
                "description": {"type": "string", "description": "Nouvelle description"},
            }, "required": []},
            handler=discord_server_configure,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_list_channels",
            description="Liste tous les channels et catégories du serveur Discord. guild_id est auto-détecté si absent — appeler directement sans connaître l'ID.",
            parameters={"properties": {"guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"}}, "required": []},
            handler=discord_list_channels,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_create_category",
            description="Crée une catégorie (dossier) pour organiser les channels. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "name": {"type": "string", "description": "Nom de la catégorie"},
                "position": {"type": "integer", "description": "Position d'affichage (0 = tout en haut)"},
            }, "required": ["name"]},
            handler=discord_create_category,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_create_channel",
            description="Crée un channel Discord (text, voice, announcement, forum, stage). guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "name": {"type": "string", "description": "Nom du channel à créer"},
                "channel_type": {"type": "string", "description": "text | voice | announcement | forum | stage", "default": "text"},
                "topic": {"type": "string", "description": "Description/sujet du channel"},
                "parent_id": {"type": "string", "description": "ID de la catégorie parente"},
                "position": {"type": "integer", "description": "Position d'affichage (0 = tout en haut)"},
            }, "required": ["name"]},
            handler=discord_create_channel,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_modify_channel",
            description="Renomme un channel, change son topic ou sa position",
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel Discord à modifier"},
                "name": {"type": "string", "description": "Nouveau nom du channel"},
                "topic": {"type": "string", "description": "Nouveau sujet/description du channel"},
                "position": {"type": "integer", "description": "Nouvelle position d'affichage"},
            }, "required": ["channel_id"]},
            handler=discord_modify_channel,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_delete_channel",
            description="Supprime un channel ou une catégorie Discord définitivement",
            parameters={"properties": {"channel_id": {"type": "string", "description": "ID du channel Discord à supprimer"}}, "required": ["channel_id"]},
            handler=discord_delete_channel,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_send",
            description=(
                "Envoie un message texte dans un channel Discord. "
                "Accepte channel_name (nom du salon, ex: 'général') OU channel_id. "
                "Préférer channel_name pour éviter les erreurs d'ID."
            ),
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel (depuis discord_list_channels)"},
                "channel_name": {"type": "string", "description": "Nom du channel (résolution auto, ex: 'général'). Préférer cette option."},
                "guild_id": {"type": "string", "description": "ID du serveur (optionnel)"},
                "content": {"type": "string", "description": "Contenu du message (max 2000 caractères)"},
            }, "required": ["content"]},
            handler=discord_send,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_send_embed",
            description=(
                "Envoie un embed riche dans un channel Discord. "
                "Accepte channel_name (nom du salon) OU channel_id. "
                "Exemple ACTION_INPUT: {\"channel_name\": \"général\", \"title\": \"Mon titre\", \"description\": \"Texte\"}"
            ),
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel (depuis discord_list_channels)"},
                "channel_name": {"type": "string", "description": "Nom du channel (résolution auto). Préférer cette option."},
                "guild_id": {"type": "string", "description": "ID du serveur (optionnel)"},
                "title": {"type": "string", "description": "Titre de l'embed"},
                "description": {"type": "string", "description": "Texte principal de l'embed"},
                "color": {"type": "integer", "description": "Couleur décimale (ex: 16734003 pour orange, 7528954 pour bleu Discord)", "default": 7528954},
                "fields": {"type": "array", "description": "Liste d'objets [{\"name\": \"Titre\", \"value\": \"Contenu\", \"inline\": false}]"},
                "footer": {"type": "string", "description": "Texte en pied d'embed"},
                "image_url": {"type": "string", "description": "URL d'une image à afficher"},
            }, "required": ["title"]},
            handler=discord_send_embed,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_fetch_messages",
            description=(
                "Récupère les derniers messages d'un channel Discord. "
                "IMPORTANT: utiliser channel_name (nom du salon, ex: 'général') OU "
                "channel_id UNIQUEMENT si l'ID vient de discord_list_channels — "
                "ne jamais deviner ou construire un channel_id."
            ),
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID exact du channel (depuis discord_list_channels uniquement)"},
                "channel_name": {"type": "string", "description": "Nom du channel (résolution auto, ex: 'général', 'gaming'). Préférer cette option."},
                "guild_id": {"type": "string", "description": "ID du serveur (optionnel, utilise DISCORD_GUILD_ID si absent)"},
                "limit": {"type": "integer", "default": 20, "description": "Nombre de messages (max 100)"},
            }, "required": []},
            handler=discord_fetch_messages,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_pin",
            description="Épingle un message important dans un channel Discord",
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel Discord"},
                "message_id": {"type": "string", "description": "ID du message à épingler"},
            }, "required": ["channel_id", "message_id"]},
            handler=discord_pin,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_unpin",
            description="Désépingle un message d'un channel Discord",
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel Discord"},
                "message_id": {"type": "string", "description": "ID du message à désépingler"},
            }, "required": ["channel_id", "message_id"]},
            handler=discord_unpin,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_delete_message",
            description="Supprime un message dans un channel Discord",
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel Discord"},
                "message_id": {"type": "string", "description": "ID du message à supprimer"},
            }, "required": ["channel_id", "message_id"]},
            handler=discord_delete_message,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_set_channel_permissions",
            description=(
                "Pose un permission overwrite sur un channel ou une catégorie Discord. "
                "Bits : VIEW_CHANNEL=1024, SEND_MESSAGES=2048, READ_MESSAGE_HISTORY=65536. "
                "Pour rendre un salon invisible à @everyone : overwrite_id=guild_id, deny=1024, overwrite_type=0. "
                "overwrite_type : 0=rôle, 1=membre."
            ),
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel ou de la catégorie Discord"},
                "overwrite_id": {"type": "string", "description": "ID du rôle (overwrite_type=0) ou du membre (overwrite_type=1). Pour @everyone utiliser le guild_id."},
                "allow": {"type": "integer", "description": "Bits des permissions à autoriser (0 = aucune)", "default": 0},
                "deny": {"type": "integer", "description": "Bits des permissions à refuser (0 = aucune)", "default": 0},
                "overwrite_type": {"type": "integer", "description": "0 = rôle (défaut), 1 = membre", "default": 0},
            }, "required": ["channel_id", "overwrite_id"]},
            handler=discord_set_channel_permissions,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_list_roles",
            description="Liste tous les rôles du serveur Discord avec leur couleur et permissions. guild_id est auto-détecté si absent.",
            parameters={"properties": {"guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"}}, "required": []},
            handler=discord_list_roles,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_create_role",
            description="Crée un rôle dans le serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "name": {"type": "string", "description": "Nom du rôle à créer"},
                "color": {"type": "integer", "description": "Couleur décimale (ex: 16734003 pour orange, 3447003 pour bleu). 0 = pas de couleur", "default": 0},
                "hoist": {"type": "boolean", "description": "Afficher séparément dans la liste", "default": False},
                "mentionable": {"type": "boolean", "description": "Mentionnable par tous", "default": False},
                "permissions": {"type": "array", "items": {"type": "string"},
                                "description": "Liste de permissions: admin, manage_guild, manage_channels, kick_members, ban_members, send_messages"},
            }, "required": ["name"]},
            handler=discord_create_role,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_delete_role",
            description="Supprime un rôle du serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "role_id": {"type": "string", "description": "ID du rôle à supprimer"},
            }, "required": ["role_id"]},
            handler=discord_delete_role,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_assign_role",
            description="Assigne un rôle à un membre du serveur. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "user_id": {"type": "string", "description": "ID de l'utilisateur Discord"},
                "role_id": {"type": "string", "description": "ID du rôle à assigner"},
            }, "required": ["user_id", "role_id"]},
            handler=discord_assign_role,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_remove_role",
            description="Retire un rôle à un membre du serveur. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "user_id": {"type": "string", "description": "ID de l'utilisateur Discord"},
                "role_id": {"type": "string", "description": "ID du rôle à retirer"},
            }, "required": ["user_id", "role_id"]},
            handler=discord_remove_role,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_list_members",
            description="Liste les membres du serveur Discord (username, rôles, bots). guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "limit": {"type": "integer", "default": 50, "description": "Nombre max de membres à retourner"},
            }, "required": []},
            handler=discord_list_members,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_kick",
            description="Expulse (kick) un membre du serveur — il peut revenir avec une invitation. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "user_id": {"type": "string", "description": "ID de l'utilisateur à expulser"},
            }, "required": ["user_id"]},
            handler=discord_kick,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_ban",
            description="Bannit définitivement un membre du serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "user_id": {"type": "string", "description": "ID de l'utilisateur à bannir"},
                "reason": {"type": "string", "description": "Raison du ban (visible dans les logs)"},
                "delete_days": {"type": "integer", "description": "Jours de messages à supprimer (0-7)", "default": 0},
            }, "required": ["user_id"]},
            handler=discord_ban,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_unban",
            description="Lève le ban d'un membre du serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {
                "guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"},
                "user_id": {"type": "string", "description": "ID de l'utilisateur à débannir"},
            }, "required": ["user_id"]},
            handler=discord_unban,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_create_invite",
            description="Crée un lien d'invitation pour rejoindre le serveur",
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel Discord pour l'invitation"},
                "max_age": {"type": "integer", "description": "Durée de validité en secondes (0=permanent)", "default": 86400},
                "max_uses": {"type": "integer", "description": "Utilisations max (0=illimité)", "default": 0},
            }, "required": ["channel_id"]},
            handler=discord_create_invite,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        HandlerDef(
            name="discord_list_invites",
            description="Liste toutes les invitations actives du serveur Discord. guild_id est auto-détecté si absent.",
            parameters={"properties": {"guild_id": {"type": "string", "description": "ID du serveur Discord (optionnel — auto-détecté si absent)"}}, "required": []},
            handler=discord_list_invites,
            category="discord",
            source_module="handlers.discord_admin",
        ),
        # Alias: le LLM essaie parfois "discord_send_message" au lieu de "discord_send"
        HandlerDef(
            name="discord_send_message",
            description=(
                "Alias de discord_send — Envoie un message dans un channel Discord. "
                "Accepte channel_name (nom du salon, ex: 'général') OU channel_id."
            ),
            parameters={"properties": {
                "channel_id": {"type": "string", "description": "ID du channel (depuis discord_list_channels)"},
                "channel_name": {"type": "string", "description": "Nom du channel (résolution auto). Préférer cette option."},
                "guild_id": {"type": "string", "description": "ID du serveur (optionnel)"},
                "content": {"type": "string", "description": "Contenu du message (max 2000 caractères)"},
            }, "required": ["content"]},
            handler=discord_send,
            category="discord",
            source_module="handlers.discord_admin",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
