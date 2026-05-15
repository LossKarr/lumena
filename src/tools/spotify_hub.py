"""🎵 SpotifyHub — Contrôle Spotify via API officielle pour Lumena.

Nécessite dans .env:
    SPOTIFY_CLIENT_ID=<ton_client_id>
    SPOTIFY_CLIENT_SECRET=<ton_client_secret>
    SPOTIFY_REDIRECT_URI=http://localhost:8888/callback  (optionnel)

Obtenir les credentials: https://developer.spotify.com/dashboard
→ "Create app" → copier Client ID et Client Secret
→ Ajouter http://localhost:8888/callback dans "Redirect URIs"

Premier lancement: une fenêtre navigateur s'ouvre pour l'autorisation OAuth.
Le token est mis en cache dans ~/.cache/lumena_spotify_token.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class SpotifyHub:
    """Contrôle complet de Spotify via l'API officielle (spotipy)."""

    SCOPES = (
        "user-read-playback-state "
        "user-modify-playback-state "
        "user-read-currently-playing "
        "user-read-recently-played "
        "playlist-read-private "
        "user-library-read"
    )

    def __init__(self) -> None:
        self._sp = None

    def available(self) -> bool:
        """Vérifie si les credentials Spotify sont configurés."""
        return bool(
            os.getenv("SPOTIFY_CLIENT_ID", "").strip()
            and os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        )

    def _get_sp(self):
        """Retourne le client Spotipy, en l'initialisant si nécessaire."""
        if self._sp is not None:
            return self._sp

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError:
            raise RuntimeError(
                "spotipy non installé — pip install spotipy\n"
                "Puis configurer SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET dans .env"
            )

        client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback").strip()

        if not client_id or not client_secret:
            raise RuntimeError(
                "SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET requis dans .env\n"
                "Voir: https://developer.spotify.com/dashboard"
            )

        cache_path = Path.home() / ".cache" / "lumena_spotify_token"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=self.SCOPES,
                cache_path=str(cache_path),
                open_browser=True,
            )
        )
        logger.info("SpotifyHub: client Spotipy initialisé")
        return self._sp

    # ── Lecture ───────────────────────────────────────────────────────────────

    def search_and_play(self, query: str, media_type: str = "auto") -> Dict[str, Any]:
        """Cherche et joue un morceau, album, artiste ou playlist.

        Args:
            query: Texte de recherche (ex: "Daft Punk", "Bohemian Rhapsody", "Random Access Memories")
            media_type: "track" | "album" | "artist" | "playlist" | "auto"
        """
        sp = self._get_sp()

        # Détection automatique du type
        if media_type == "auto":
            q_lower = query.lower()
            if any(kw in q_lower for kw in ["album", "ep", "mixtape"]):
                media_type = "album"
            elif any(kw in q_lower for kw in ["playlist"]):
                media_type = "playlist"
            elif any(kw in q_lower for kw in ["artiste", "artist"]):
                media_type = "artist"
            else:
                media_type = "track"

        results = sp.search(q=query, limit=1, type=media_type)
        items = results.get(media_type + "s", {}).get("items", [])

        if not items:
            return {
                "success": False,
                "error": f"Aucun résultat pour '{query}' (type: {media_type})",
            }

        item = items[0]
        uri = item["uri"]
        name = item.get("name", "")

        if media_type == "track":
            sp.start_playback(uris=[uri])
        else:
            sp.start_playback(context_uri=uri)

        artist = ""
        if media_type == "track" and item.get("artists"):
            artist = item["artists"][0]["name"]
        elif media_type == "album" and item.get("artists"):
            artist = item["artists"][0]["name"]

        return {
            "success": True,
            "type": media_type,
            "name": name,
            "artist": artist,
            "uri": uri,
        }

    def pause(self) -> Dict[str, Any]:
        """Met en pause la lecture."""
        try:
            self._get_sp().pause_playback()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def resume(self) -> Dict[str, Any]:
        """Reprend la lecture."""
        try:
            self._get_sp().start_playback()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def next_track(self) -> Dict[str, Any]:
        """Passe au morceau suivant."""
        try:
            self._get_sp().next_track()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def prev_track(self) -> Dict[str, Any]:
        """Revient au morceau précédent."""
        try:
            self._get_sp().previous_track()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_volume(self, volume: int) -> Dict[str, Any]:
        """Règle le volume (0-100)."""
        try:
            volume = max(0, min(100, int(volume)))
            self._get_sp().volume(volume)
            return {"success": True, "volume": volume}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def current_track(self) -> Dict[str, Any]:
        """Retourne les infos du morceau en cours."""
        try:
            sp = self._get_sp()
            pb = sp.current_playback()

            if not pb or not pb.get("item"):
                return {
                    "success": True,
                    "playing": False,
                    "message": "Rien en cours de lecture",
                }

            item = pb["item"]
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            album = item.get("album", {}).get("name", "")
            progress_ms = pb.get("progress_ms", 0)
            duration_ms = item.get("duration_ms", 1)
            progress_pct = int(progress_ms / duration_ms * 100)
            is_playing = pb.get("is_playing", False)
            device = pb.get("device", {})

            return {
                "success": True,
                "playing": is_playing,
                "name": item.get("name", ""),
                "artists": artists,
                "album": album,
                "progress_pct": progress_pct,
                "volume": device.get("volume_percent", 0),
                "device": device.get("name", ""),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_to_queue(self, query: str) -> Dict[str, Any]:
        """Ajoute un morceau à la file d'attente."""
        try:
            sp = self._get_sp()
            results = sp.search(q=query, limit=1, type="track")
            items = results.get("tracks", {}).get("items", [])

            if not items:
                return {"success": False, "error": f"Aucun résultat pour '{query}'"}

            item = items[0]
            sp.add_to_queue(item["uri"])
            artists = ", ".join(a["name"] for a in item.get("artists", []))

            return {
                "success": True,
                "name": item["name"],
                "artists": artists,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
