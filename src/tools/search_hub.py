"""🔍 SearchHub — Recherche web multi-provider pour Lumena.

Priorité: Brave Search API (si clé) → DuckDuckGo (gratuit, sans clé)

Configuration dans .env:
    BRAVE_SEARCH_API_KEY=<ta_clé>  # https://brave.com/search/api/

Sans clé Brave, DuckDuckGo est utilisé automatiquement.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from loguru import logger

# primp 1.x n'accepte que les noms courts ('chrome', 'safari', 'firefox', 'edge').
# Les versions numérotées ('chrome_118', 'safari_16'…) font imprimer un warning Rust
# directement sur stderr. On corrige _impersonates avant le premier import DDGS.
try:
    from ddgs.http_client import HttpClient as _DDGHttpClient
    _DDGHttpClient._impersonates = ("chrome", "safari", "firefox", "edge")
except Exception:
    pass  # DDG HTTP client monkey-patch optionnel


class SearchHub:
    """Recherche web avec Brave Search API (premium) + DuckDuckGo (fallback gratuit)."""

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self) -> None:
        self.brave_api_key: str = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        if self.brave_api_key:
            logger.info("SearchHub: Brave Search API activée")
        else:
            logger.info("SearchHub: pas de clé Brave → DuckDuckGo utilisé")

    # ── Public API ────────────────────────────────────────────────────────────

    async def web_search(self, query: str, count: int = 8) -> Dict[str, Any]:
        """Recherche web.

        Retourne:
            {
                "source": "Brave" | "DuckDuckGo" | "error",
                "query": str,
                "results": [{"title": str, "url": str, "description": str}],
                "error": str  # seulement si source == "error"
            }
        """
        if self.brave_api_key:
            try:
                return await self._brave_search(query, count)
            except Exception as e:
                logger.warning(f"SearchHub: Brave failed ({e}), fallback DuckDuckGo")
        return await self._ddg_search(query, count)

    # ── Providers ─────────────────────────────────────────────────────────────

    async def _brave_search(self, query: str, count: int) -> Dict[str, Any]:
        """Brave Search API — https://brave.com/search/api/"""
        import httpx

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.brave_api_key,
        }
        params = {
            "q": query,
            "count": min(count, 20),
            "search_lang": "fr",
            "country": "FR",
            "safesearch": "moderate",
            "freshness": "py",  # Resultats de la derniere annee
        }

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(self.BRAVE_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        results: List[Dict[str, str]] = []

        # Résultats web
        for item in data.get("web", {}).get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
            )

        # Résultats news (les 3 premiers)
        for item in data.get("news", {}).get("results", [])[:3]:
            results.append(
                {
                    "title": f"📰 {item.get('title', '')}",
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
            )

        return {"source": "Brave", "query": query, "results": results}

    async def _ddg_search(self, query: str, count: int) -> Dict[str, Any]:
        """DuckDuckGo — fallback sans clé API. Supporte ddgs (nouveau) et duckduckgo_search (ancien)."""
        try:
            import asyncio
            # Nouveau nom du package (ddgs) — avec fallback sur l'ancien nom
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # type: ignore[no-redef]

            def _sync_ddg():
                with DDGS() as ddg:
                    return list(ddg.text(query, max_results=count, timelimit="y"))

            raw = await asyncio.wait_for(
                asyncio.to_thread(_sync_ddg), timeout=15.0
            )

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "description": r.get("body", ""),
                }
                for r in raw
            ]
            return {"source": "DuckDuckGo", "query": query, "results": results}

        except ImportError:
            return {
                "source": "error",
                "query": query,
                "results": [],
                "error": "ddgs non installé — pip install ddgs",
            }
        except Exception as e:
            return {"source": "error", "query": query, "results": [], "error": str(e)}

    # ── Formatting ────────────────────────────────────────────────────────────

    @staticmethod
    def format_results(data: Dict[str, Any]) -> str:
        """Formate les résultats pour l'affichage Lumena."""
        if data.get("error") and not data.get("results"):
            return f"❌ Erreur de recherche: {data['error']}"

        source_icon = "🦁" if data["source"] == "Brave" else "🦆"
        lines = [f"{source_icon} **Résultats {data['source']} — « {data['query']} »:**\n"]

        for i, r in enumerate(data["results"], 1):
            lines.append(f"{i}. **{r['title']}**")
            if r.get("description"):
                lines.append(f"   {r['description'][:220]}")
            lines.append(f"   🔗 {r['url']}\n")

        if not data["results"]:
            lines.append("Aucun résultat trouvé.")

        return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
