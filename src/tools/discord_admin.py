"""
discord_admin.py - Client REST Discord pour l'administration autonome du serveur.

Lumena utilise ce module pour construire et gérer son serveur Discord :
- Créer/modifier/supprimer channels et catégories
- Créer/assigner/supprimer des rôles
- Envoyer messages et embeds
- Gérer les membres (kick, ban, rôles)
- Sondages, épingles, invitations
- Configurer le serveur (nom, description, règles)

Utilise l'API REST Discord v10 directement via aiohttp.
Token : DISCORD_TOKEN dans .env
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from loguru import logger

DISCORD_API = "https://discord.com/api/v10"


def _token() -> str:
    tok = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not tok:
        raise RuntimeError("DISCORD_TOKEN absent du .env")
    return tok


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bot {_token()}",
        "Content-Type": "application/json",
    }


async def _request(method: str, path: str, json: Optional[Dict] = None) -> Dict[str, Any]:
    """Petit wrapper aiohttp pour l'API Discord."""
    try:
        import aiohttp
    except ImportError:
        return {"ok": False, "error": "aiohttp non installé (pip install aiohttp)"}

    url = f"{DISCORD_API}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=_headers(), json=json) as resp:
                if resp.status == 204:
                    return {"ok": True}
                body = await resp.json()
                if resp.status >= 400:
                    if isinstance(body, dict):
                        err_msg = body.get("message", str(body))
                        err_details = body.get("errors", {})
                    else:
                        err_msg = str(body)
                        err_details = {}
                    return {"ok": False, "error": err_msg, "errors": err_details, "code": resp.status}
                if isinstance(body, list):
                    return {"ok": True, "data": body}
                return {"ok": True, **body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Découverte ─────────────────────────────────────────────────────────────

async def list_guilds() -> Dict[str, Any]:
    """Liste les serveurs (guilds) accessibles au bot via GET /users/@me/guilds."""
    return await _request("GET", "/users/@me/guilds")


# ─── Serveur ────────────────────────────────────────────────────────────────

async def get_guild(guild_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}")


async def modify_guild(guild_id: str, name: str = None, description: str = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if name:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    return await _request("PATCH", f"/guilds/{guild_id}", json=payload)


# ─── Channels ───────────────────────────────────────────────────────────────

CHANNEL_TYPES = {
    "text": 0,
    "voice": 2,
    "category": 4,
    "announcement": 5,
    "forum": 15,
    "stage": 13,
}


async def list_channels(guild_id: str) -> Dict[str, Any]:
    result = await _request("GET", f"/guilds/{guild_id}/channels")
    if not result.get("ok"):
        return result
    # result est la liste brute (la clé "ok" ajoutée ne gêne pas)
    # L'API retourne un array, pas un dict → le wrapper le met dans "*body"
    # En réalité aiohttp retourne une list, on la passe différemment
    return result


async def create_channel(guild_id: str, name: str, channel_type: str = "text",
                         topic: str = None, parent_id: str = None,
                         position: int = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": name,
        "type": CHANNEL_TYPES.get(channel_type, 0),
    }
    if topic:
        payload["topic"] = topic
    if parent_id:
        payload["parent_id"] = parent_id
    if position is not None:
        payload["position"] = position
    return await _request("POST", f"/guilds/{guild_id}/channels", json=payload)


async def modify_channel(channel_id: str, name: str = None, topic: str = None,
                         position: int = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if name:
        payload["name"] = name
    if topic is not None:
        payload["topic"] = topic
    if position is not None:
        payload["position"] = position
    return await _request("PATCH", f"/channels/{channel_id}", json=payload)


async def delete_channel(channel_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/channels/{channel_id}")


async def set_channel_permission(channel_id: str, overwrite_id: str,
                                  allow: int = 0, deny: int = 0,
                                  overwrite_type: int = 0) -> Dict[str, Any]:
    """Pose un permission overwrite sur un channel. type 0=rôle, 1=membre."""
    payload = {"allow": str(allow), "deny": str(deny), "type": overwrite_type}
    return await _request("PUT", f"/channels/{channel_id}/permissions/{overwrite_id}", json=payload)


# ─── Messages ────────────────────────────────────────────────────────────────

async def send_message(channel_id: str, content: str = "", embed: Dict = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if content:
        payload["content"] = content[:2000]
    if embed:
        payload["embeds"] = [embed]
    return await _request("POST", f"/channels/{channel_id}/messages", json=payload)


async def send_embed(channel_id: str, title: str, description: str = "",
                     color: int = 0x7289DA,
                     fields: List[Dict[str, Any]] = None,
                     footer: str = None,
                     image_url: str = None) -> Dict[str, Any]:
    embed: Dict[str, Any] = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
    }
    if fields:
        embed["fields"] = [
            {"name": f.get("name", "")[:256], "value": f.get("value", "")[:1024],
             "inline": f.get("inline", False)}
            for f in fields[:25]
        ]
    if footer:
        embed["footer"] = {"text": footer[:2048]}
    if image_url:
        embed["image"] = {"url": image_url}
    return await send_message(channel_id, embed=embed)


async def fetch_messages(channel_id: str, limit: int = 20) -> Dict[str, Any]:
    result = await _request("GET", f"/channels/{channel_id}/messages?limit={min(limit, 100)}")
    return result


async def pin_message(channel_id: str, message_id: str) -> Dict[str, Any]:
    return await _request("PUT", f"/channels/{channel_id}/pins/{message_id}")


async def unpin_message(channel_id: str, message_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/channels/{channel_id}/pins/{message_id}")


async def delete_message(channel_id: str, message_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/channels/{channel_id}/messages/{message_id}")


# ─── Rôles ──────────────────────────────────────────────────────────────────

PERMISSION_FLAGS = {
    "admin":          0x8,
    "manage_guild":   0x20,
    "manage_channels":0x10,
    "manage_roles":   0x10000000,
    "kick_members":   0x2,
    "ban_members":    0x4,
    "send_messages":  0x800,
    "read_messages":  0x400,
    "embed_links":    0x4000,
    "attach_files":   0x8000,
    "mention_everyone": 0x20000,
}


async def list_roles(guild_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}/roles")


async def create_role(guild_id: str, name: str, color: int = 0,
                      permissions: List[str] = None,
                      hoist: bool = False, mentionable: bool = False) -> Dict[str, Any]:
    perm_value = 0
    for p in (permissions or []):
        perm_value |= PERMISSION_FLAGS.get(p, 0)
    payload: Dict[str, Any] = {
        "name": name,
        "color": color,
        "permissions": str(perm_value),
        "hoist": hoist,
        "mentionable": mentionable,
    }
    return await _request("POST", f"/guilds/{guild_id}/roles", json=payload)


async def delete_role(guild_id: str, role_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/guilds/{guild_id}/roles/{role_id}")


async def assign_role(guild_id: str, user_id: str, role_id: str) -> Dict[str, Any]:
    return await _request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")


async def remove_role(guild_id: str, user_id: str, role_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")


# ─── Membres ────────────────────────────────────────────────────────────────

async def list_members(guild_id: str, limit: int = 50) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}/members?limit={min(limit, 1000)}")


async def kick_member(guild_id: str, user_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/guilds/{guild_id}/members/{user_id}")


async def ban_member(guild_id: str, user_id: str, reason: str = "",
                     delete_message_days: int = 0) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"delete_message_seconds": delete_message_days * 86400}
    return await _request("PUT", f"/guilds/{guild_id}/bans/{user_id}", json=payload)


async def unban_member(guild_id: str, user_id: str) -> Dict[str, Any]:
    return await _request("DELETE", f"/guilds/{guild_id}/bans/{user_id}")


async def get_member(guild_id: str, user_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}/members/{user_id}")


async def set_member_nickname(guild_id: str, user_id: str, nick: str) -> Dict[str, Any]:
    return await _request("PATCH", f"/guilds/{guild_id}/members/{user_id}",
                          json={"nick": nick[:32]})


# ─── Invitations ────────────────────────────────────────────────────────────

async def create_invite(channel_id: str, max_age: int = 86400,
                        max_uses: int = 0, unique: bool = True) -> Dict[str, Any]:
    payload = {"max_age": max_age, "max_uses": max_uses, "unique": unique}
    return await _request("POST", f"/channels/{channel_id}/invites", json=payload)


async def list_invites(guild_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}/invites")


# ─── Emoji ──────────────────────────────────────────────────────────────────

async def list_emojis(guild_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/guilds/{guild_id}/emojis")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
