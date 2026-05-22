"""
sirene.py — Service de connexion à recherche-entreprises.api.gouv.fr (SIRENE).

API publique officielle, sans clé. Host DISTINCT de data.gouv (raison du module séparé).

V3.2 (lecture seule) :
  - Rechercher des entreprises (nom, dirigeant, SIRET, SIREN, raison sociale)
  - Lookup direct par SIRET (14 chiffres)

Doc officielle : https://recherche-entreprises.api.gouv.fr/docs
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Optional

from loguru import logger

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

from src.utils.url_safety import assert_url_safe


_BASE_URL = "https://recherche-entreprises.api.gouv.fr"


class _RateLimiter:
    """Token bucket simple in-memory (identique à DataGouvService)."""

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


class SireneError(ValueError):
    """Erreur de validation côté SIRENE (SIRET malformé, etc.)."""


class SireneService:
    """Client HTTP pour l'API publique recherche-entreprises.api.gouv.fr."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self._timeout = timeout
        self._user_agent = "Lumena/1.0 (https://github.com/LossKarr/lumena)"
        # Doc API : ~7 req/s sans clé recommandé → 7 prudent
        self._rate_limiter = _RateLimiter(max_calls=7, period_s=1.0)

    @property
    def is_available(self) -> bool:
        return _HTTPX

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        url = f"{self.base_url}{path}"
        assert_url_safe(url)
        await self._rate_limiter.acquire()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    # ── recherche entreprises ───────────────────────────────────────────

    async def search_companies(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 10,
    ) -> Dict[str, Any]:
        """Recherche full-text dans la base SIRENE.

        Accepte : nom commercial, raison sociale, dirigeant, SIRET, SIREN, NAF.
        Returns: dict {"results": [...], "total_results": int, "page": int, ...}
        """
        params: Dict[str, Any] = {
            "q": query,
            "page": max(1, int(page)),
            "per_page": min(max(1, int(per_page)), 25),
        }
        return await self._get("/search", params=params)

    # ── lookup direct par SIRET ─────────────────────────────────────────

    @staticmethod
    def _normalize_siret(siret: str) -> str:
        """Retire espaces/tirets/etc. Garde uniquement les chiffres."""
        return re.sub(r"\D", "", siret or "")

    async def get_company_by_siret(self, siret: str) -> Optional[Dict[str, Any]]:
        """Lookup direct par SIRET (14 chiffres).

        Retourne le premier résultat ou None si introuvable.
        Lève SireneError si le SIRET est mal formé.
        """
        normalized = self._normalize_siret(siret)
        if len(normalized) != 14:
            raise SireneError(
                f"SIRET invalide : doit faire 14 chiffres "
                f"(reçu {len(normalized)} après nettoyage)."
            )
        result = await self.search_companies(normalized, per_page=1)
        results = result.get("results") or []
        return results[0] if results else None


_INSTANCE: Optional[SireneService] = None


def get_sirene_service() -> SireneService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SireneService()
    return _INSTANCE
