"""
twitter.py — Handlers Twitter/X pour Lumena (ReAct V2).

Permet à Lumena d'interagir avec Twitter/X via sa boucle de raisonnement :
poster des tweets, répondre, chercher, liker, consulter profils.

Handlers (10):
    twitter_post_tweet, twitter_reply,
    twitter_search, twitter_get_timeline,
    twitter_like, twitter_get_user_info,
    twitter_get_my_stats, twitter_get_mentions,
    twitter_status, twitter_compose_thread.
"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── helpers ────────────────────────────────────────────────────────────────

def _get_channel():
    """Récupère le singleton TwitterChannel."""
    try:
        from src.channels.twitter_channel import get_twitter_channel
        return get_twitter_channel()
    except Exception:
        return None


def _require_channel():
    """Récupère le channel ou retourne un HandlerResult d'erreur."""
    ch = _get_channel()
    if ch is None:
        return None, HandlerResult.fail(
            "❌ Twitter non disponible — vérifier `pip install tweepy>=4.14.0`",
            handler_name="twitter",
        )
    if not ch.is_available:
        return None, HandlerResult.fail(
            "❌ Twitter non configuré — vérifier TWITTER_BEARER_TOKEN et/ou TWITTER_API_KEY dans .env",
            handler_name="twitter",
        )
    return ch, None


def _fmt_tweet(t: dict) -> str:
    """Formate un tweet pour affichage."""
    metrics = t.get("metrics", {})
    likes = metrics.get("like_count", 0)
    rt = metrics.get("retweet_count", 0)
    replies = metrics.get("reply_count", 0)
    return (
        f"@{t.get('author', '?')} — {t.get('created_at', '')}\n"
        f"{t.get('text', '')}\n"
        f"❤️ {likes}  🔁 {rt}  💬 {replies}\n"
        f"ID: {t.get('id', '')}"
    )


# ─── Handlers ───────────────────────────────────────────────────────────────

async def twitter_post_tweet_handler(ctx: HandlerContext, text: str) -> HandlerResult:
    """Poste un tweet sur le compte X de Lumena."""
    ch, err = _require_channel()
    if err:
        return err

    if not text.strip():
        return HandlerResult.fail("❌ Texte du tweet vide", handler_name="twitter_post_tweet")

    if not ch.can_write:
        return HandlerResult.fail(
            "❌ Écriture Twitter impossible — configurer TWITTER_API_KEY, TWITTER_API_SECRET, "
            "TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET dans .env",
            handler_name="twitter_post_tweet",
        )

    result = await ch.post_tweet(text)
    if "error" in result:
        return HandlerResult.fail(f"❌ {result['error']}", handler_name="twitter_post_tweet")

    count = result.get("count", 1)
    tweet_ids = [t.get("id", "") for t in result.get("tweets", [])]
    return HandlerResult.ok(
        f"✅ Tweet posté ({count} partie{'s' if count > 1 else ''}) — IDs: {', '.join(tweet_ids)}",
        handler_name="twitter_post_tweet",
    )


async def twitter_reply_handler(ctx: HandlerContext, tweet_id: str, text: str) -> HandlerResult:
    """Répond à un tweet spécifique."""
    ch, err = _require_channel()
    if err:
        return err

    if not ch.can_write:
        return HandlerResult.fail(
            "❌ Écriture Twitter impossible — configurer les credentials OAuth",
            handler_name="twitter_reply",
        )

    success = await ch.send_message(text, target_id=tweet_id)
    if success:
        return HandlerResult.ok(
            f"✅ Réponse postée au tweet {tweet_id}",
            handler_name="twitter_reply",
        )
    return HandlerResult.fail(
        f"❌ Échec réponse: {ch.last_error}",
        handler_name="twitter_reply",
    )


async def twitter_search_handler(ctx: HandlerContext, query: str, max_results: int = 10) -> HandlerResult:
    """Recherche des tweets (nécessite le tier Basic)."""
    ch, err = _require_channel()
    if err:
        return err

    tweets = await ch.search_tweets(query, max_results=max_results)
    if not tweets:
        return HandlerResult.ok(
            f"Aucun tweet trouvé pour « {query} » (ou recherche non disponible sur le Free tier)",
            handler_name="twitter_search",
        )

    formatted = [_fmt_tweet(t) for t in tweets]
    return HandlerResult.ok(
        f"🔍 {len(tweets)} tweets trouvés pour « {query} »:\n\n" + "\n\n---\n\n".join(formatted),
        handler_name="twitter_search",
    )


async def twitter_get_timeline_handler(ctx: HandlerContext, username: str = "", max_results: int = 10) -> HandlerResult:
    """Récupère la timeline d'un utilisateur (ou la nôtre)."""
    ch, err = _require_channel()
    if err:
        return err

    user_id = None
    if username:
        info = await ch.get_user_info(username.lstrip("@"))
        if info:
            user_id = info["id"]
        else:
            return HandlerResult.fail(
                f"❌ Utilisateur @{username} non trouvé",
                handler_name="twitter_get_timeline",
            )

    tweets = await ch.get_timeline(user_id=user_id, max_results=max_results)
    if not tweets:
        return HandlerResult.ok("Timeline vide", handler_name="twitter_get_timeline")

    target = f"@{username}" if username else "Lumena"
    formatted = [_fmt_tweet({"author": username or "lumena", **t}) for t in tweets]
    return HandlerResult.ok(
        f"📋 Timeline de {target} ({len(tweets)} tweets):\n\n" + "\n\n---\n\n".join(formatted),
        handler_name="twitter_get_timeline",
    )


async def twitter_like_handler(ctx: HandlerContext, tweet_id: str) -> HandlerResult:
    """Like un tweet."""
    ch, err = _require_channel()
    if err:
        return err

    success = await ch.like_tweet(tweet_id)
    if success:
        return HandlerResult.ok(f"❤️ Tweet {tweet_id} liké", handler_name="twitter_like")
    return HandlerResult.fail(f"❌ Échec like tweet {tweet_id}", handler_name="twitter_like")


async def twitter_get_user_info_handler(ctx: HandlerContext, username: str) -> HandlerResult:
    """Récupère les infos publiques d'un utilisateur Twitter."""
    ch, err = _require_channel()
    if err:
        return err

    info = await ch.get_user_info(username.lstrip("@"))
    if not info:
        return HandlerResult.fail(
            f"❌ Utilisateur @{username} non trouvé",
            handler_name="twitter_get_user_info",
        )

    return HandlerResult.ok(
        f"👤 @{info['username']} ({info['name']})\n"
        f"📝 {info['description']}\n"
        f"📊 {info['followers']} followers · {info['following']} following · {info['tweets']} tweets\n"
        f"📍 {info['location']}" + (" ✅ Vérifié" if info.get('verified') else ""),
        handler_name="twitter_get_user_info",
    )


async def twitter_get_my_stats_handler(ctx: HandlerContext) -> HandlerResult:
    """Affiche les stats du compte Twitter de Lumena."""
    ch, err = _require_channel()
    if err:
        return err

    stats = await ch.get_my_stats()
    if "error" in stats:
        return HandlerResult.fail(f"❌ {stats['error']}", handler_name="twitter_get_my_stats")

    runtime = stats.pop("runtime", {})
    return HandlerResult.ok(
        f"📊 Stats Twitter @{stats.get('username', '?')}\n"
        f"Followers: {stats.get('followers', 0)} · Following: {stats.get('following', 0)}\n"
        f"Total tweets: {stats.get('tweets', 0)}\n"
        f"─── Session ───\n"
        f"Tweets envoyés: {runtime.get('tweets_sent', 0)}\n"
        f"Mentions reçues: {runtime.get('mentions_received', 0)}\n"
        f"Réponses envoyées: {runtime.get('replies_sent', 0)}\n"
        f"Erreurs: {runtime.get('errors', 0)}",
        handler_name="twitter_get_my_stats",
    )


async def twitter_status_handler(ctx: HandlerContext) -> HandlerResult:
    """Vérifie le statut de connexion Twitter."""
    ch = _get_channel()
    if ch is None:
        return HandlerResult.ok(
            "Twitter: ❌ non disponible (tweepy non installé)",
            handler_name="twitter_status",
        )

    status = ch.get_runtime_status()
    state_emoji = {"running": "✅", "disabled": "⏸️", "error": "❌", "stopped": "⬛"}.get(
        status["state"], "❓"
    )
    lines = [
        f"Twitter: {state_emoji} {status['state']}",
        f"Écriture: {'✅' if status['can_write'] else '❌ (OAuth non configuré)'}",
    ]
    if status.get("last_error"):
        lines.append(f"Dernière erreur: {status['last_error']}")

    stats = status.get("stats", {})
    if any(v > 0 for v in stats.values()):
        lines.append(f"Session: {stats.get('tweets_sent', 0)} tweets, "
                      f"{stats.get('mentions_received', 0)} mentions, "
                      f"{stats.get('replies_sent', 0)} réponses")

    return HandlerResult.ok("\n".join(lines), handler_name="twitter_status")


async def twitter_compose_thread_handler(
    ctx: HandlerContext, topic: str, points: str = ""
) -> HandlerResult:
    """
    Compose et poste un thread Twitter sur un sujet donné.

    Lumena utilise son LLM pour rédiger le thread, puis le poste.

    Args:
        topic: Le sujet du thread
        points: Points clés à aborder (optionnel, séparés par des virgules)
    """
    ch, err = _require_channel()
    if err:
        return err

    if not ch.can_write:
        return HandlerResult.fail(
            "❌ Écriture Twitter impossible — configurer les credentials OAuth",
            handler_name="twitter_compose_thread",
        )

    # Demander au LLM de rédiger le thread
    try:
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.get_instance()

        prompt = (
            f"Rédige un thread Twitter (3 à 6 tweets max) sur le sujet suivant:\n"
            f"Sujet: {topic}\n"
        )
        if points:
            prompt += f"Points à aborder: {points}\n"
        prompt += (
            "\nRègles:\n"
            "- Chaque tweet fait MAX 270 caractères (laisser de la marge)\n"
            "- Premier tweet = accroche forte\n"
            "- Dernier tweet = conclusion + CTA\n"
            "- Ton: expert mais accessible, pas de jargon\n"
            "- Utilise des emojis avec parcimonie\n"
            "- PAS de hashtags excessifs (1-2 max sur le dernier tweet)\n"
            "\nRetourne UNIQUEMENT un JSON array de strings, chaque string = un tweet.\n"
            'Exemple: ["Tweet 1...", "Tweet 2...", "Tweet 3..."]'
        )

        raw = await llm.chat_async(prompt, system="Tu es un expert en communication Twitter.")
        raw = raw.strip()

        # Parser le JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        tweets = json.loads(raw)
        if not isinstance(tweets, list) or not all(isinstance(t, str) for t in tweets):
            return HandlerResult.fail(
                "❌ Le LLM n'a pas retourné un format valide",
                handler_name="twitter_compose_thread",
            )

    except json.JSONDecodeError:
        return HandlerResult.fail(
            "❌ Impossible de parser le thread généré par le LLM",
            handler_name="twitter_compose_thread",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur génération thread: {e}",
            handler_name="twitter_compose_thread",
        )

    # Poster le thread
    reply_to = None
    posted_ids = []

    for i, tweet_text in enumerate(tweets):
        try:
            resp = await ch._client.create_tweet(
                text=tweet_text,
                in_reply_to_tweet_id=reply_to,
            )
            if resp and resp.data:
                tid = resp.data.get("id")
                posted_ids.append(tid)
                reply_to = tid
                ch._stats["tweets_sent"] += 1
        except Exception as e:
            return HandlerResult.fail(
                f"❌ Erreur au tweet {i + 1}/{len(tweets)}: {e}\n"
                f"Tweets postés: {len(posted_ids)}/{len(tweets)}",
                handler_name="twitter_compose_thread",
            )

    return HandlerResult.ok(
        f"✅ Thread posté ({len(posted_ids)} tweets)\n"
        f"IDs: {', '.join(str(x) for x in posted_ids)}\n\n"
        f"Contenu:\n" + "\n---\n".join(tweets),
        handler_name="twitter_compose_thread",
    )


async def twitter_get_mentions_handler(ctx: HandlerContext, max_results: int = 10) -> HandlerResult:
    """Récupère les dernières mentions de Lumena sur Twitter."""
    ch, err = _require_channel()
    if err:
        return err

    if not ch._me:
        return HandlerResult.fail("❌ Pas connecté à Twitter", handler_name="twitter_get_mentions")

    try:
        import tweepy
        mentions = await ch._client.get_users_mentions(
            ch._me.id,
            max_results=min(max_results, 100),
            tweet_fields=["created_at", "public_metrics", "author_id"],
            expansions=["author_id"],
            user_fields=["username", "name"],
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ {e}", handler_name="twitter_get_mentions")

    if not mentions or not mentions.data:
        return HandlerResult.ok("Aucune mention récente", handler_name="twitter_get_mentions")

    users_map = {}
    if mentions.includes and "users" in mentions.includes:
        for u in mentions.includes["users"]:
            users_map[u.id] = u

    lines = []
    for t in mentions.data:
        author = users_map.get(t.author_id)
        author_name = author.username if author else "?"
        lines.append(f"@{author_name}: {t.text}\n  📅 {t.created_at}  ID: {t.id}")

    return HandlerResult.ok(
        f"📬 {len(lines)} mentions récentes:\n\n" + "\n\n".join(lines),
        handler_name="twitter_get_mentions",
    )


# ─── Registry ───────────────────────────────────────────────────────────────

HANDLERS: list[HandlerDef] = [
    HandlerDef(
        name="twitter_post_tweet",
        handler=twitter_post_tweet_handler,
        description="Poste un tweet sur le compte X/Twitter de Lumena",
        parameters={"text": "Texte du tweet (max 280 caractères)"},
        category="social",
    ),
    HandlerDef(
        name="twitter_reply",
        handler=twitter_reply_handler,
        description="Répond à un tweet spécifique par son ID",
        parameters={"tweet_id": "ID du tweet auquel répondre", "text": "Texte de la réponse"},
        category="social",
    ),
    HandlerDef(
        name="twitter_search",
        handler=twitter_search_handler,
        description="Recherche des tweets par mots-clés (nécessite tier Basic)",
        parameters={"query": "Termes de recherche", "max_results": "(optionnel) Nombre de résultats (défaut 10)"},
        category="social",
    ),
    HandlerDef(
        name="twitter_get_timeline",
        handler=twitter_get_timeline_handler,
        description="Récupère la timeline d'un utilisateur Twitter ou celle de Lumena",
        parameters={"username": "(optionnel) @username, vide = timeline Lumena", "max_results": "(optionnel) Nombre max"},
        category="social",
    ),
    HandlerDef(
        name="twitter_like",
        handler=twitter_like_handler,
        description="Like un tweet par son ID",
        parameters={"tweet_id": "ID du tweet à liker"},
        category="social",
    ),
    HandlerDef(
        name="twitter_get_user_info",
        handler=twitter_get_user_info_handler,
        description="Récupère les infos publiques d'un utilisateur Twitter (@username)",
        parameters={"username": "@username de l'utilisateur"},
        category="social",
    ),
    HandlerDef(
        name="twitter_get_my_stats",
        handler=twitter_get_my_stats_handler,
        description="Affiche les statistiques du compte Twitter de Lumena",
        parameters={},
        category="social",
    ),
    HandlerDef(
        name="twitter_get_mentions",
        handler=twitter_get_mentions_handler,
        description="Récupère les dernières mentions de Lumena sur Twitter",
        parameters={"max_results": "(optionnel) Nombre de mentions (défaut 10)"},
        category="social",
    ),
    HandlerDef(
        name="twitter_status",
        handler=twitter_status_handler,
        description="Vérifie le statut de connexion Twitter/X de Lumena",
        parameters={},
        category="social",
    ),
    HandlerDef(
        name="twitter_compose_thread",
        handler=twitter_compose_thread_handler,
        description="Compose et poste un thread Twitter (3-6 tweets) sur un sujet donné",
        parameters={"topic": "Sujet du thread", "points": "(optionnel) Points clés à aborder, séparés par des virgules"},
        category="social",
    ),
]


def get_twitter_handler_defs() -> list[HandlerDef]:
    """Retourne les 10 définitions de handlers Twitter/X."""
    return HANDLERS
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
