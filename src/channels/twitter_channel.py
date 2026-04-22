"""
Twitter/X channel for Lumena.

Utilise l'API X v2 via tweepy.
- Free tier: post tweets + read own timeline
- Basic tier ($100/mo): search tweets, read mentions, filtered stream
- Polling des mentions toutes les 60s (Free compatible)

Env vars:
    TWITTER_BEARER_TOKEN       — Bearer token (read-only, search)
    TWITTER_API_KEY            — OAuth 1.0a consumer key (write)
    TWITTER_API_SECRET         — OAuth 1.0a consumer secret
    TWITTER_ACCESS_TOKEN       — OAuth 1.0a access token
    TWITTER_ACCESS_TOKEN_SECRET — OAuth 1.0a access token secret
    LUMENA_DISABLE_TWITTER     — 1 pour désactiver
    LUMENA_TWITTER_POLL_INTERVAL — Secondes entre chaque poll mentions (défaut: 90)
    LUMENA_TWITTER_MAX_TWEET_LEN — Longueur max tweet (défaut: 280)
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import BaseChannel, ChannelMessage, ChannelType

try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False
    logger.warning("tweepy not installed. Run: pip install tweepy>=4.14.0")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


_TWITTER_MAX = 280


def _split_tweet(text: str, max_len: int = _TWITTER_MAX) -> List[str]:
    """Découpe un texte long en tweets chaînés (1/N, 2/N...)."""
    if len(text) <= max_len:
        return [text]

    # Réserver de l'espace pour " (X/Y)"
    suffix_reserve = 8  # " (XX/YY)"
    chunk_len = max_len - suffix_reserve
    parts: List[str] = []
    while text:
        if len(text) <= chunk_len + suffix_reserve:
            parts.append(text)
            break
        # Chercher meilleur point de coupure
        window = text[:chunk_len]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(". ")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = chunk_len
        else:
            cut += 1  # inclure le séparateur
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    total = len(parts)
    if total > 1:
        parts = [f"{p} ({i + 1}/{total})" for i, p in enumerate(parts)]
    return parts


class TwitterChannel(BaseChannel):
    """Twitter/X channel — polling mentions + post tweets."""

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_secret: Optional[str] = None,
    ):
        # TWITTER n'est pas dans ChannelType pour l'instant, on l'ajoute dynamiquement
        # ou on utilise API comme fallback
        super().__init__(ChannelType.TWITTER)

        self._bearer_token = bearer_token or os.getenv("TWITTER_BEARER_TOKEN", "")
        self._api_key = api_key or os.getenv("TWITTER_API_KEY", "")
        self._api_secret = api_secret or os.getenv("TWITTER_API_SECRET", "")
        self._access_token = access_token or os.getenv("TWITTER_ACCESS_TOKEN", "")
        self._access_token_secret = access_token_secret or os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

        self._disable = _env_flag("LUMENA_DISABLE_TWITTER", False)
        self._poll_interval = max(30, _env_int("LUMENA_TWITTER_POLL_INTERVAL", 90))
        self._max_tweet_len = _env_int("LUMENA_TWITTER_MAX_TWEET_LEN", _TWITTER_MAX)

        self._client: Optional[Any] = None          # tweepy.Client (v2 read+write)
        self._me: Optional[Any] = None               # tweepy.User (self)
        self._poll_task: Optional[asyncio.Task] = None
        self._last_mention_id: Optional[str] = None
        self._last_error: Optional[str] = None
        self._state = "stopped"
        self._stats: Dict[str, int] = {
            "tweets_sent": 0,
            "mentions_received": 0,
            "replies_sent": 0,
            "errors": 0,
        }

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return (
            TWEEPY_AVAILABLE
            and bool(self._bearer_token or self._api_key)
            and not self._disable
        )

    @property
    def can_write(self) -> bool:
        """True si les credentials OAuth 1.0a sont configurés (écriture)."""
        return bool(self._api_key and self._api_secret
                     and self._access_token and self._access_token_secret)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def state(self) -> str:
        return self._state

    def get_runtime_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_available,
            "can_write": self.can_write,
            "running": self.is_running,
            "state": self._state,
            "last_error": self._last_error,
            "stats": dict(self._stats),
            "poll_interval": self._poll_interval,
        }

    # ─── Start / Stop ────────────────────────────────────────────────────

    async def start(self) -> bool:
        if self._disable:
            self._state = "disabled"
            self._last_error = "twitter disabled by LUMENA_DISABLE_TWITTER=1"
            logger.info("Twitter/X disabled by configuration")
            return False

        if self.is_running:
            return True

        if not TWEEPY_AVAILABLE:
            self._state = "error"
            self._last_error = "tweepy not installed"
            logger.error("tweepy not installed. Run: pip install tweepy>=4.14.0")
            return False

        if not self._bearer_token and not self._api_key:
            self._state = "error"
            self._last_error = "No Twitter credentials (TWITTER_BEARER_TOKEN or TWITTER_API_KEY)"
            logger.error(self._last_error)
            return False

        self._state = "starting"

        try:
            # Client v2 — OAuth 1.0a pour write, Bearer pour read
            self._client = tweepy.Client(
                bearer_token=self._bearer_token or None,
                consumer_key=self._api_key or None,
                consumer_secret=self._api_secret or None,
                access_token=self._access_token or None,
                access_token_secret=self._access_token_secret or None,
                wait_on_rate_limit=True,
            )

            # Récupérer notre propre profil
            me = self._client.get_me(
                user_fields=["id", "name", "username", "description", "public_metrics"]
            )
            if me and me.data:
                self._me = me.data
                logger.info(
                    f"Twitter/X connected as @{self._me.username} "
                    f"(followers: {self._me.public_metrics.get('followers_count', 0)})"
                )
            else:
                self._state = "error"
                self._last_error = "Could not fetch own profile — check credentials"
                logger.error(self._last_error)
                return False

            # Démarrer polling des mentions
            self._poll_task = asyncio.create_task(self._poll_mentions_loop())
            self.is_running = True
            self._state = "running"
            self._last_error = None
            return True

        except tweepy.errors.Unauthorized as e:
            self._state = "error"
            self._last_error = f"Twitter auth failed: {e}"
            logger.error(self._last_error)
            return False
        except Exception as e:
            self._state = "error"
            self._last_error = f"Twitter startup error: {e}"
            logger.error(self._last_error)
            self._stats["errors"] += 1
            return False

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        self._state = "stopped"
        logger.info("Twitter/X channel stopped")

    # ─── Core: send_message (post tweet or reply) ────────────────────────

    async def send_message(self, content: str, target_id: str = "", **kwargs) -> bool:
        """
        Poste un tweet ou une réponse.

        Args:
            content: Texte du tweet
            target_id: tweet_id auquel répondre (optionnel)
        """
        if not self._client or not self.can_write:
            self._last_error = "Twitter write credentials not configured"
            logger.warning(self._last_error)
            return False

        parts = _split_tweet(content, self._max_tweet_len)
        reply_to = target_id if target_id else None

        try:
            for part in parts:
                resp = await asyncio.to_thread(
                    self._client.create_tweet,
                    text=part,
                    in_reply_to_tweet_id=reply_to,
                )
                if resp and resp.data:
                    reply_to = resp.data.get("id")  # chaîner les réponses
                    self._stats["tweets_sent"] += 1

            return True
        except tweepy.errors.Forbidden as e:
            self._last_error = f"Tweet forbidden: {e}"
            logger.error(self._last_error)
            self._stats["errors"] += 1
            return False
        except tweepy.errors.TooManyRequests:
            self._last_error = "Twitter rate limit hit"
            logger.warning(self._last_error)
            self._stats["errors"] += 1
            return False
        except Exception as e:
            self._last_error = f"Tweet error: {e}"
            logger.error(self._last_error)
            self._stats["errors"] += 1
            return False

    # ─── Polling mentions ────────────────────────────────────────────────

    async def _poll_mentions_loop(self) -> None:
        """Boucle de polling des mentions toutes les _poll_interval secondes."""
        logger.info(f"Twitter mention polling started (every {self._poll_interval}s)")

        # Petit délai initial pour laisser le daemon démarrer
        await asyncio.sleep(5)

        while self.is_running:
            try:
                await self._check_mentions()
            except asyncio.CancelledError:
                break
            except tweepy.errors.TooManyRequests:
                logger.warning("Twitter rate limit on mentions — backing off 5min")
                await asyncio.sleep(300)
                continue
            except Exception as e:
                err_str = str(e)
                if "402" in err_str or "Payment Required" in err_str or "credits" in err_str:
                    logger.warning("Twitter mentions polling désactivé — Free tier ne permet pas la lecture des mentions (402). Seul le posting est disponible.")
                    self._poll_task = None
                    return  # stop la boucle définitivement
                logger.error(f"Twitter mention poll error: {e}")
                self._stats["errors"] += 1
            await asyncio.sleep(self._poll_interval)

    async def _check_mentions(self) -> None:
        """Vérifie les nouvelles mentions et y répond via le callback."""
        if not self._client or not self._me:
            return

        kwargs: Dict[str, Any] = {
            "expansions": ["author_id"],
            "tweet_fields": ["created_at", "conversation_id", "in_reply_to_user_id"],
            "user_fields": ["username", "name"],
            "max_results": 10,
        }
        if self._last_mention_id:
            kwargs["since_id"] = self._last_mention_id

        try:
            mentions = await asyncio.to_thread(
                self._client.get_users_mentions,
                self._me.id,
                **kwargs,
            )
        except tweepy.errors.Forbidden:
            # Free tier ne permet pas get_users_mentions — silencieux
            return
        except Exception as e:
            err_str = str(e)
            if "402" in err_str or "Payment Required" in err_str or "credits" in err_str:
                raise  # remonter au loop qui va stopper
            raise

        if not mentions or not mentions.data:
            return

        # Map author_id → user info
        users_map: Dict[str, Any] = {}
        if mentions.includes and "users" in mentions.includes:
            for u in mentions.includes["users"]:
                users_map[u.id] = u

        for tweet in reversed(mentions.data):  # du plus ancien au plus récent
            self._stats["mentions_received"] += 1

            # Mettre à jour le dernier ID vu
            if not self._last_mention_id or int(tweet.id) > int(self._last_mention_id):
                self._last_mention_id = str(tweet.id)

            # Ne pas répondre à soi-même
            author = users_map.get(tweet.author_id)
            if author and author.username == self._me.username:
                continue

            author_name = author.username if author else str(tweet.author_id)

            # Nettoyer le texte (retirer @lumena du début)
            text = tweet.text
            if self._me.username:
                text = text.replace(f"@{self._me.username}", "").strip()

            logger.info(f"Twitter mention from @{author_name}: {text[:80]}...")

            # Construire le ChannelMessage
            msg = ChannelMessage(
                content=text,
                channel_type=ChannelType.TWITTER,
                user_id=str(tweet.author_id),
                username=author_name,
                timestamp=tweet.created_at or datetime.now(timezone.utc),
                channel_id=str(tweet.conversation_id) if tweet.conversation_id else None,
                reply_to=str(tweet.id),
                metadata={
                    "tweet_id": str(tweet.id),
                    "conversation_id": str(tweet.conversation_id) if tweet.conversation_id else None,
                    "platform": "twitter",
                },
            )

            # Appeler le callback (Lumena répond)
            response = await self._on_message_received(msg)

            if response and self.can_write:
                # Répondre au tweet
                success = await self.send_message(
                    f"@{author_name} {response}",
                    target_id=str(tweet.id),
                )
                if success:
                    self._stats["replies_sent"] += 1
                    logger.info(f"Twitter replied to @{author_name}")

    # ─── Actions publiques (utilisées par les handlers ReAct) ────────────

    async def post_tweet(self, text: str) -> Dict[str, Any]:
        """Poste un tweet. Retourne les données du tweet créé."""
        if not self._client or not self.can_write:
            return {"error": "Twitter write credentials not configured"}

        parts = _split_tweet(text, self._max_tweet_len)
        results = []

        try:
            reply_to = None
            for part in parts:
                resp = await asyncio.to_thread(
                    self._client.create_tweet,
                    text=part,
                    in_reply_to_tweet_id=reply_to,
                )
                if resp and resp.data:
                    results.append(resp.data)
                    reply_to = resp.data.get("id")
                    self._stats["tweets_sent"] += 1

            return {"success": True, "tweets": results, "count": len(results)}
        except Exception as e:
            self._stats["errors"] += 1
            return {"error": str(e)}

    async def search_tweets(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Recherche des tweets (nécessite Basic tier)."""
        if not self._client:
            return []

        try:
            resp = await asyncio.to_thread(
                self._client.search_recent_tweets,
                query=query,
                max_results=min(max_results, 100),
                tweet_fields=["created_at", "public_metrics", "author_id"],
                expansions=["author_id"],
                user_fields=["username", "name"],
            )
            if not resp or not resp.data:
                return []

            users_map = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    users_map[u.id] = u

            tweets = []
            for t in resp.data:
                author = users_map.get(t.author_id)
                tweets.append({
                    "id": str(t.id),
                    "text": t.text,
                    "author": author.username if author else str(t.author_id),
                    "created_at": str(t.created_at) if t.created_at else "",
                    "metrics": t.public_metrics or {},
                })
            return tweets
        except tweepy.errors.Forbidden:
            logger.warning("Twitter search requires Basic tier ($100/mo)")
            return []
        except Exception as e:
            logger.error(f"Twitter search error: {e}")
            return []

    async def get_timeline(self, user_id: Optional[str] = None, max_results: int = 10) -> List[Dict[str, Any]]:
        """Récupère la timeline d'un utilisateur."""
        if not self._client:
            return []

        uid = user_id or (str(self._me.id) if self._me else None)
        if not uid:
            return []

        try:
            resp = await asyncio.to_thread(
                self._client.get_users_tweets,
                uid,
                max_results=min(max_results, 100),
                tweet_fields=["created_at", "public_metrics"],
            )
            if not resp or not resp.data:
                return []

            return [
                {
                    "id": str(t.id),
                    "text": t.text,
                    "created_at": str(t.created_at) if t.created_at else "",
                    "metrics": t.public_metrics or {},
                }
                for t in resp.data
            ]
        except Exception as e:
            logger.error(f"Twitter timeline error: {e}")
            return []

    async def like_tweet(self, tweet_id: str) -> bool:
        """Like un tweet."""
        if not self._client or not self._me:
            return False
        try:
            await asyncio.to_thread(self._client.like, tweet_id)
            return True
        except Exception as e:
            logger.error(f"Twitter like error: {e}")
            return False

    async def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Récupère les infos d'un utilisateur par username."""
        if not self._client:
            return None
        try:
            resp = await asyncio.to_thread(
                self._client.get_user,
                username=username,
                user_fields=["description", "public_metrics", "created_at", "location", "verified"],
            )
            if resp and resp.data:
                u = resp.data
                return {
                    "id": str(u.id),
                    "name": u.name,
                    "username": u.username,
                    "description": u.description or "",
                    "followers": u.public_metrics.get("followers_count", 0) if u.public_metrics else 0,
                    "following": u.public_metrics.get("following_count", 0) if u.public_metrics else 0,
                    "tweets": u.public_metrics.get("tweet_count", 0) if u.public_metrics else 0,
                    "verified": getattr(u, "verified", False),
                    "location": getattr(u, "location", ""),
                }
            return None
        except Exception as e:
            logger.error(f"Twitter user info error: {e}")
            return None

    async def get_my_stats(self) -> Dict[str, Any]:
        """Récupère les stats du compte Lumena."""
        if not self._me:
            return {"error": "Not connected"}
        info = await self.get_user_info(self._me.username)
        if info:
            info["runtime"] = dict(self._stats)
        return info or {"error": "Could not fetch stats"}


# ─── Singleton accessor ──────────────────────────────────────────────────

_instance: Optional[TwitterChannel] = None
_instance_lock = threading.Lock()


def get_twitter_channel() -> TwitterChannel:
    """Retourne le singleton TwitterChannel."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = TwitterChannel()
    return _instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
