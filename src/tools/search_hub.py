"""🔍 SearchHub — Recherche web multi-provider pour Lumena.

Priorité: Brave Search API (si clé) → DuckDuckGo (gratuit, sans clé)

Configuration dans .env:
    BRAVE_SEARCH_API_KEY=<ta_clé>  # https://brave.com/search/api/

Sans clé Brave, DuckDuckGo est utilisé automatiquement.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List

from loguru import logger

class SearchHub:
    """Recherche web avec Brave Search API (premium) + DuckDuckGo (fallback gratuit)."""

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self) -> None:
        self.brave_api_key: str = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        self._ddg_timeout_s = self._bounded_float_env(
            "LUMENA_DDG_SEARCH_TIMEOUT_S", 15.0, minimum=3.0, maximum=60.0
        )
        self._ddg_breaker_s = self._bounded_float_env(
            "LUMENA_DDG_BREAKER_S", 90.0, minimum=5.0, maximum=900.0
        )
        self._ddg_degraded_until = 0.0
        max_parallel = int(
            self._bounded_float_env(
                "LUMENA_DDG_MAX_PARALLEL", 2.0, minimum=1.0, maximum=4.0
            )
        )
        self._ddg_gate = asyncio.Semaphore(max_parallel)
        if self.brave_api_key:
            logger.info("SearchHub: Brave Search API activée")
        else:
            logger.info("SearchHub: pas de clé Brave → DuckDuckGo utilisé")

    @staticmethod
    def _bounded_float_env(
        name: str, default: float, *, minimum: float, maximum: float
    ) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

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
        """DuckDuckGo fallback, isolated behind a killable process boundary."""
        now = time.monotonic()
        if now < self._ddg_degraded_until:
            remaining = max(1, int(self._ddg_degraded_until - now))
            return {
                "source": "error",
                "query": query,
                "results": [],
                "error": (
                    "DuckDuckGo temporairement indisponible après un timeout "
                    f"({remaining}s). Change de stratégie: utilise web_fetch sur "
                    "une source connue ou le navigateur, puis réessaie plus tard."
                ),
            }

        try:
            async with self._ddg_gate:
                raw = await self._run_ddg_subprocess(query, count)

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "description": r.get("body", ""),
                }
                for r in raw
            ]
            self._ddg_degraded_until = 0.0
            return {"source": "DuckDuckGo", "query": query, "results": results}
        except asyncio.TimeoutError:
            self._ddg_degraded_until = time.monotonic() + self._ddg_breaker_s
            logger.warning(
                "SearchHub: DuckDuckGo timeout après {:.1f}s; circuit ouvert {:.1f}s",
                self._ddg_timeout_s,
                self._ddg_breaker_s,
            )
            return {
                "source": "error",
                "query": query,
                "results": [],
                "error": (
                    f"DuckDuckGo a dépassé {self._ddg_timeout_s:g}s et a été arrêté. "
                    "Change de stratégie: utilise web_fetch sur une source connue "
                    "ou le navigateur; ne relance pas la même recherche en boucle."
                ),
            }
        except Exception as e:
            logger.warning(f"SearchHub: DuckDuckGo isolé en échec: {e}")
            return {
                "source": "error",
                "query": query,
                "results": [],
                "error": (
                    f"DuckDuckGo indisponible: {e}. Change de stratégie avec "
                    "web_fetch sur une source connue ou le navigateur."
                ),
            }

    async def _run_ddg_subprocess(
        self, query: str, count: int
    ) -> List[Dict[str, Any]]:
        worker = Path(__file__).with_name("ddg_worker.py")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        payload = json.dumps(
            {"query": str(query), "count": max(1, min(int(count), 20))}
        ).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload), timeout=self._ddg_timeout_s
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            try:
                await asyncio.shield(asyncio.wait_for(process.wait(), timeout=2.0))
            except asyncio.TimeoutError:
                logger.error("SearchHub: processus DuckDuckGo impossible à récolter")
            raise

        error_text = stderr.decode("utf-8", errors="replace").strip()
        try:
            data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"réponse DuckDuckGo invalide ({error_text or exc})"
            ) from exc
        if process.returncode != 0 or not data.get("ok"):
            raise RuntimeError(data.get("error") or error_text or "échec DuckDuckGo")
        results = data.get("results")
        if not isinstance(results, list):
            raise RuntimeError("résultats DuckDuckGo invalides")
        return results

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
