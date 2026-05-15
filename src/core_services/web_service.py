"""
WebService — Recherche web, fetch URL, résumé.

Migré depuis LumenaCore (4 méthodes, dépendance self.llm pour summarize_url).
"""

import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from .base_service import BaseService


class WebService(BaseService):
    """Recherche web, récupération d'URL, résumé de pages."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._last_mentioned_url: Optional[str] = None
        self._last_search_query: Optional[str] = None
        self._last_fetched_content: Optional[str] = None
        self._last_page_title: Optional[str] = None

    async def search_web(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """Effectue une recherche web et retourne les résultats."""
        logger.info(f"🔍 Recherche web: {query}")
        self._last_search_query = query
        try:
            search_url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(search_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    if data.get("Abstract"):
                        results.append({
                            "title": data.get("Heading", "Résultat"),
                            "link": data.get("AbstractURL", ""),
                            "snippet": data.get("Abstract", "")[:300]
                        })
                    for topic in data.get("RelatedTopics", [])[:num_results]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            results.append({
                                "title": topic.get("Text", "")[:100],
                                "link": topic.get("FirstURL", ""),
                                "snippet": topic.get("Text", "")[:200]
                            })
                    logger.info(f"✅ {len(results)} résultats trouvés")
                    return results[:num_results]
        except Exception as e:
            logger.error(f"❌ Erreur recherche web: {e}")
        return []

    async def fetch_url(self, url: str, max_content: int = 5000) -> Dict[str, Any]:
        """Récupère le contenu d'une URL."""
        from ..utils.url_safety import assert_url_safe
        try:
            assert_url_safe(url)
        except ValueError as e:
            return {"error": str(e), "url": url}
        logger.info(f"📥 Récupération: {url}")
        self._last_mentioned_url = url
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    content = response.text
                    title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
                    title = title_match.group(1).strip() if title_match else url
                    clean_content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    clean_content = re.sub(r'<style[^>]*>.*?</style>', '', clean_content, flags=re.DOTALL | re.IGNORECASE)
                    clean_content = re.sub(r'<[^>]+>', ' ', clean_content)
                    clean_content = re.sub(r'\s+', ' ', clean_content).strip()
                    if len(clean_content) > max_content:
                        clean_content = clean_content[:max_content] + "..."
                    self._last_fetched_content = clean_content
                    self._last_page_title = title
                    logger.info(f"✅ Page récupérée: {title[:50]}... ({len(clean_content)} chars)")
                    return {
                        "title": title,
                        "content": clean_content,
                        "url": url,
                        "success": True
                    }
                else:
                    logger.warning(f"⚠️ HTTP {response.status_code} pour {url}")
                    return {"success": False, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.error(f"❌ Erreur fetch URL: {e}")
            return {"success": False, "error": str(e)}

    def open_google_search(self, query: str) -> str:
        """Ouvre une recherche Google dans le navigateur."""
        import webbrowser
        import urllib.parse
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.google.com/search?q={encoded_query}"
        try:
            webbrowser.open(search_url)
            logger.info(f"🌐 Recherche Google ouverte: {query}")
            self._last_mentioned_url = search_url
            self._last_search_query = query
            return search_url
        except Exception as e:
            logger.error(f"❌ Erreur ouverture navigateur: {e}")
            return ""

    async def summarize_url(self, url: str) -> str:
        """Récupère une URL et génère un résumé du contenu."""
        result = await self.fetch_url(url)
        if not result.get("success"):
            return f"❌ Impossible de récupérer {url}: {result.get('error')}"
        content = result.get("content", "")
        title = result.get("title", "Page web")
        summary_prompt = f"""Résume le contenu suivant de la page "{title}" en français:

{content[:3000]}

Fais un résumé concis mais informatif en 3-5 points."""
        summary = await self.llm.chat([
            {"role": "system", "content": "Tu es un assistant qui résume des pages web de façon claire et concise."},
            {"role": "user", "content": summary_prompt}
        ])
        return f"📄 **{title}**\n\n{summary}"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
