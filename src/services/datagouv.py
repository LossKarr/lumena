"""
datagouv.py — Service de connexion à l'API publique data.gouv.fr.

V1 (lecture seule) :
  - Rechercher des datasets (INSEE, DVF, marchés publics, etc.)
  - Récupérer les métadonnées + resources (CSV/JSON/XLSX) d'un dataset
  - Télécharger une resource dans le workspace courant
  - Interroger les organismes publics

API publique, sans clé requise.
Doc officielle : https://doc.data.gouv.fr/

SIRENE / lookup entreprise : voir src/services/sirene.py (V3, host distinct).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

from src.utils.url_safety import assert_url_safe


_BASE_URL = "https://www.data.gouv.fr/api/1"


class _RateLimiter:
    """Token bucket simple in-memory. Prudent par défaut tant qu'on n'a pas mesuré."""

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


class DataGouvService:
    """Client HTTP pour l'API publique data.gouv.fr (lecture seule V1)."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self._timeout = timeout
        self._user_agent = "Lumena/1.0 (https://github.com/LossKarr/lumena)"
        # Rate limit prudent : 10 req/s. Doc data.gouv ne publie pas de quota
        # explicite sans clé. À relâcher si profilage montre que c'est trop bas.
        self._rate_limiter = _RateLimiter(max_calls=10, period_s=1.0)

    # ── helpers ─────────────────────────────────────────────────────────

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

    # ── recherche datasets ──────────────────────────────────────────────

    async def search_datasets(
        self,
        query: str,
        *,
        page_size: int = 20,
        organization: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Recherche full-text dans les datasets data.gouv.

        Returns: dict avec 'data' (list), 'total' (int), 'next_page' (str|None).
        """
        params: Dict[str, Any] = {"q": query, "page_size": min(page_size, 100)}
        if organization:
            params["organization"] = organization
        if tag:
            params["tag"] = tag
        return await self._get("/datasets/", params=params)

    async def get_dataset(self, slug_or_id: str) -> Dict[str, Any]:
        """Métadonnées + liste des resources téléchargeables."""
        return await self._get(f"/datasets/{slug_or_id}/")

    async def get_organization(self, slug: str) -> Dict[str, Any]:
        """Métadonnées d'un organisme public."""
        return await self._get(f"/organizations/{slug}/")

    async def search_organizations(
        self, query: str, page_size: int = 20
    ) -> Dict[str, Any]:
        return await self._get(
            "/organizations/", params={"q": query, "page_size": min(page_size, 100)}
        )

    # ── téléchargement resource ─────────────────────────────────────────

    async def download_resource(
        self,
        resource_url: str,
        target_path: Path,
        *,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> Path:
        """Télécharge une resource dans target_path.

        Garde-fous : assert_url_safe (SSRF), max_bytes (100 MB), streaming.
        """
        if not _HTTPX:
            raise RuntimeError("httpx non installé")
        assert_url_safe(resource_url)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        total = 0
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=True
        ) as client:
            async with client.stream(
                "GET", resource_url, headers=self._headers()
            ) as r:
                if r.status_code == 404:
                    is_stable_url = "/api/1/datasets/r/" in resource_url
                    if is_stable_url:
                        hint = (
                            "Cette ressource renvoie 404 même via l'URL data.gouv stable "
                            "(`/api/1/datasets/r/<id>`). Choisir une autre ressource du même "
                            "dataset, ou prévenir l'utilisateur que le fichier n'est pas "
                            "disponible côté data.gouv."
                        )
                    else:
                        hint = (
                            "Le dataset référence cette URL mais l'hébergeur a retiré le fichier. "
                            "Réessayer avec l'URL `latest` (`https://www.data.gouv.fr/api/1/datasets/r/<id>`) "
                            "ou essayer une autre resource du même dataset."
                        )
                    raise FileNotFoundError(
                        f"Resource indisponible (404) : {resource_url}. {hint}"
                    )
                r.raise_for_status()
                with target_path.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            f.close()
                            target_path.unlink(missing_ok=True)
                            raise ValueError(
                                f"Resource dépasse max_bytes={max_bytes} "
                                f"(reçu {total}). Filtrer la requête en amont."
                            )
                        f.write(chunk)
        logger.info(f"[datagouv] téléchargé {total} octets dans {target_path}")
        return target_path


_INSTANCE: Optional[DataGouvService] = None


def get_datagouv_service() -> DataGouvService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DataGouvService()
    return _INSTANCE
