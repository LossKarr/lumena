"""
geo_gouv.py — Service de géocodage France (V3.3).

Couvre 2 APIs publiques officielles, sans clé :
  - api-adresse.data.gouv.fr (BAN — Base Adresse Nationale)
      * GET /search/?q=...  → adresse → coordonnées GPS
      * GET /reverse/?lon=...&lat=... → coordonnées → adresse
  - geo.api.gouv.fr
      * GET /communes?code=<INSEE>  → métadonnées commune

Module séparé de datagouv et SIRENE (host distinct).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from loguru import logger

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

from src.utils.url_safety import assert_url_safe


_BAN_URL = "https://api-adresse.data.gouv.fr"
_GEO_URL = "https://geo.api.gouv.fr"


class _RateLimiter:
    def __init__(self, max_calls: int = 10, period_s: float = 1.0):
        self._max = max_calls
        self._period = period_s
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max:
                wait = self._period - (now - self._calls[0])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._calls.append(time.monotonic())


class GeoError(ValueError):
    """Erreur de validation côté Geo (coordonnées invalides, INSEE malformé...)."""


class GeoGouvService:
    """Client HTTP pour BAN + geo.api.gouv.fr (lecture seule)."""

    def __init__(
        self,
        ban_url: Optional[str] = None,
        geo_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.ban_url = (ban_url or _BAN_URL).rstrip("/")
        self.geo_url = (geo_url or _GEO_URL).rstrip("/")
        self._timeout = timeout
        self._user_agent = "Lumena/1.0 (https://github.com/LossKarr/lumena)"
        # BAN/Geo tolèrent ~10 req/s sans clé
        self._rate_limiter = _RateLimiter(max_calls=10, period_s=1.0)

    @property
    def is_available(self) -> bool:
        return _HTTPX

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }

    async def _get(self, full_url: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        assert_url_safe(full_url)
        await self._rate_limiter.acquire()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(full_url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    # ── BAN : geocoding ─────────────────────────────────────────────────

    async def search_address(
        self,
        query: str,
        *,
        limit: int = 5,
        postcode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Adresse libre → liste de candidats avec coordonnées GPS.

        Returns: GeoJSON FeatureCollection (BAN).
        """
        params: Dict[str, Any] = {"q": query, "limit": min(max(1, int(limit)), 20)}
        if postcode:
            params["postcode"] = postcode
        return await self._get(f"{self.ban_url}/search/", params=params)

    # ── BAN : reverse geocoding ─────────────────────────────────────────

    @staticmethod
    def _validate_coords(lon: float, lat: float) -> None:
        try:
            lon_f = float(lon)
            lat_f = float(lat)
        except (TypeError, ValueError):
            raise GeoError(f"Coordonnées non numériques : lon={lon!r}, lat={lat!r}")
        if not -180.0 <= lon_f <= 180.0:
            raise GeoError(f"Longitude hors plage [-180, 180] : {lon_f}")
        if not -90.0 <= lat_f <= 90.0:
            raise GeoError(f"Latitude hors plage [-90, 90] : {lat_f}")

    async def reverse_geocode(self, lon: float, lat: float) -> Dict[str, Any]:
        """Coordonnées GPS → adresse la plus proche."""
        self._validate_coords(lon, lat)
        return await self._get(
            f"{self.ban_url}/reverse/",
            params={"lon": float(lon), "lat": float(lat)},
        )

    # ── geo.api.gouv.fr : commune ───────────────────────────────────────

    @staticmethod
    def _validate_insee(code: str) -> str:
        import re
        code_str = (code or "").strip()
        # INSEE communal : 5 caractères (chiffres ou A/B pour Corse 2A/2B)
        if not re.fullmatch(r"[0-9A-B]{5}", code_str.upper()):
            raise GeoError(
                f"Code INSEE invalide : `{code}` (attendu 5 caractères, "
                "ex: 75056 pour Paris, 2A004 pour Ajaccio)."
            )
        return code_str

    async def get_commune_info(self, code_insee: str) -> Optional[Dict[str, Any]]:
        """Métadonnées d'une commune par code INSEE (5 caractères).

        Returns: dict de la première commune correspondante ou None.
        """
        code = self._validate_insee(code_insee)
        data = await self._get(
            f"{self.geo_url}/communes",
            params={
                "code": code,
                "fields": (
                    "nom,code,codesPostaux,siren,codeEpci,"
                    "codeDepartement,codeRegion,population,surface"
                ),
            },
        )
        # Geo API renvoie une liste de communes
        if isinstance(data, list) and data:
            return data[0]
        return None


_INSTANCE: Optional[GeoGouvService] = None


def get_geo_gouv_service() -> GeoGouvService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = GeoGouvService()
    return _INSTANCE
