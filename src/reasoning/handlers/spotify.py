"""
spotify.py - Handlers Spotify API fragmentés depuis react.py.

Handlers: spotify_api_play, spotify_pause, spotify_resume, spotify_next,
          spotify_prev, spotify_volume, spotify_current, spotify_queue.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Handlers ──────────────────────────────────────────────────────────────

async def spotify_api_play_handler(
    ctx: HandlerContext, query: str, media_type: str = "auto"
) -> HandlerResult:
    """Joue via l'API Spotify — fallback clavier si non configurée."""
    try:
        hub = ctx.get_spotify_hub()
        result = hub.search_and_play(query, media_type)
        if result["success"]:
            info = result["name"]
            if result.get("artist"):
                info = f"{result['artist']} — {result['name']}"
            return HandlerResult.ok(
                f"🎵 Lecture en cours: **{info}** ({result['type']})",
                handler_name="spotify_api_play",
            )
        return HandlerResult.fail(
            f"❌ Spotify: {result.get('error')}",
            handler_name="spotify_api_play",
        )
    except RuntimeError as e:
        # API non configurée → fallback clavier
        logger.warning(f"Spotify API indisponible ({e}) → fallback clavier")
        # Fallback vers le handler computer_use spotify_play (keyboard)
        try:
            from .computer_use import spotify_play_handler

            return await spotify_play_handler(ctx, query=query)
        except ImportError:
            return HandlerResult.fail(
                f"❌ Spotify API et fallback clavier indisponibles: {e}",
                handler_name="spotify_api_play",
            )
    except Exception as e:
        logger.error(f"spotify_api_play error: {e}")
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_api_play"
        )


async def spotify_pause_handler(ctx: HandlerContext) -> HandlerResult:
    """Met en pause Spotify."""
    try:
        result = ctx.get_spotify_hub().pause()
        if result["success"]:
            return HandlerResult.ok(
                "⏸️ Spotify en pause", handler_name="spotify_pause"
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_pause"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_pause"
        )


async def spotify_resume_handler(ctx: HandlerContext) -> HandlerResult:
    """Reprend la lecture Spotify."""
    try:
        result = ctx.get_spotify_hub().resume()
        if result["success"]:
            return HandlerResult.ok(
                "▶️ Lecture Spotify reprise", handler_name="spotify_resume"
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_resume"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_resume"
        )


async def spotify_next_handler(ctx: HandlerContext) -> HandlerResult:
    """Morceau suivant."""
    try:
        result = ctx.get_spotify_hub().next_track()
        if result["success"]:
            return HandlerResult.ok(
                "⏭️ Morceau suivant", handler_name="spotify_next"
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_next"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_next"
        )


async def spotify_prev_handler(ctx: HandlerContext) -> HandlerResult:
    """Morceau précédent."""
    try:
        result = ctx.get_spotify_hub().prev_track()
        if result["success"]:
            return HandlerResult.ok(
                "⏮️ Morceau précédent", handler_name="spotify_prev"
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_prev"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_prev"
        )


async def spotify_volume_handler(
    ctx: HandlerContext, level: int
) -> HandlerResult:
    """Règle le volume Spotify."""
    try:
        result = ctx.get_spotify_hub().set_volume(int(level))
        if result["success"]:
            return HandlerResult.ok(
                f"🔊 Volume Spotify: {result['volume']}%",
                handler_name="spotify_volume",
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_volume"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_volume"
        )


async def spotify_current_handler(ctx: HandlerContext) -> HandlerResult:
    """Morceau en cours de lecture."""
    try:
        r = ctx.get_spotify_hub().current_track()
        if not r["success"]:
            return HandlerResult.fail(
                f"❌ {r.get('error')}", handler_name="spotify_current"
            )
        if not r.get("playing"):
            return HandlerResult.ok(
                f"⏹️ {r.get('message', 'Rien en cours de lecture')}",
                handler_name="spotify_current",
            )
        status = "▶️" if r["playing"] else "⏸️"
        output = (
            f"{status} **{r['artists']} — {r['name']}**\n"
            f"   💿 Album: {r['album']}\n"
            f"   📊 Progression: {r['progress_pct']}% | 🔊 Volume: {r['volume']}%"
        )
        if r.get("device"):
            output += f" | 📱 {r['device']}"
        return HandlerResult.ok(output, handler_name="spotify_current")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_current"
        )


async def spotify_queue_handler(
    ctx: HandlerContext, query: str
) -> HandlerResult:
    """Ajoute un morceau à la file d'attente Spotify."""
    try:
        result = ctx.get_spotify_hub().add_to_queue(query)
        if result["success"]:
            return HandlerResult.ok(
                f"➕ Ajouté à la file: **{result['artists']} — {result['name']}**",
                handler_name="spotify_queue",
            )
        return HandlerResult.fail(
            f"❌ {result.get('error')}", handler_name="spotify_queue"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Spotify: {e}", handler_name="spotify_queue"
        )


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def get_spotify_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 8 handlers Spotify API."""
    return [
        HandlerDef(
            name="spotify_api_play",
            description=(
                "Joue un morceau, album, artiste ou playlist via l'API Spotify officielle. "
                "Nécessite SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET dans .env. "
                "Détecte automatiquement le type (track/album/artist/playlist). "
                "Fallback vers spotify_play (clavier) si API non configurée."
            ),
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Artiste, titre, album ou playlist à jouer"},
                    "media_type": {"type": "string", "description": "Type: track | album | artist | playlist | auto", "default": "auto"},
                },
                "required": ["query"],
            },
            handler=spotify_api_play_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_pause",
            description="Met en pause la lecture Spotify en cours",
            parameters={"properties": {}, "required": []},
            handler=spotify_pause_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_resume",
            description="Reprend la lecture Spotify mise en pause",
            parameters={"properties": {}, "required": []},
            handler=spotify_resume_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_next",
            description="Passe au morceau suivant dans Spotify",
            parameters={"properties": {}, "required": []},
            handler=spotify_next_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_prev",
            description="Revient au morceau précédent dans Spotify",
            parameters={"properties": {}, "required": []},
            handler=spotify_prev_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_volume",
            description="Règle le volume de Spotify entre 0 et 100",
            parameters={
                "properties": {
                    "level": {"type": "integer", "description": "Volume de 0 (muet) à 100 (max)"},
                },
                "required": ["level"],
            },
            handler=spotify_volume_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_current",
            description="Affiche le morceau actuellement en lecture sur Spotify (titre, artiste, album, progression, volume, appareil)",
            parameters={"properties": {}, "required": []},
            handler=spotify_current_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
        HandlerDef(
            name="spotify_queue",
            description="Ajoute un morceau à la file d'attente Spotify",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Morceau à ajouter à la file d'attente"},
                },
                "required": ["query"],
            },
            handler=spotify_queue_handler,
            category="spotify",
            source_module="handlers.spotify",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
