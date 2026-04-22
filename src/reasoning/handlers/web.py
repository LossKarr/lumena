"""
web.py - Handlers web fragmentés depuis react.py.

Handlers: web_search_real, web_search_brave, web_fetch, deep_research,
          web_crawl_campaign, web_crawl_campaign_status,
          web_crawl_campaign_pro_report, web_crawl_campaign_explain.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers ───────────────────────────────────────────────────────────────

def _resolve_web_crawl_profile(profile: str) -> Dict[str, Any]:
    """Résout un profil de crawl en paramètres concrets."""
    value = (profile or "balanced").strip().lower()
    if value == "fast":
        return {
            "max_depth": 1,
            "request_timeout_sec": 12,
            "request_retries": 0,
            "retry_backoff_sec": 0.20,
            "max_links_per_page": 25,
            "delay_sec": 0.0,
        }
    if value == "deep":
        return {
            "max_depth": 3,
            "request_timeout_sec": 25,
            "request_retries": 2,
            "retry_backoff_sec": 0.55,
            "max_links_per_page": 70,
            "delay_sec": 0.0,
        }
    # balanced (default)
    return {
        "max_depth": 2,
        "request_timeout_sec": 18,
        "request_retries": 1,
        "retry_backoff_sec": 0.35,
        "max_links_per_page": 40,
        "delay_sec": 0.0,
    }


# ─── Handlers ──────────────────────────────────────────────────────────────

async def web_search_real_handler(ctx: HandlerContext, query: str) -> HandlerResult:
    """
    Effectue une vraie recherche web avec le navigateur contrôlé.
    Utilise Playwright pour scraper les vrais résultats.
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser

        browser = get_playwright_browser()
        result = await browser.search_google(query)

        if result["success"] and result.get("results"):
            output = f"🔍 Recherche: {query} (source: {result.get('source', 'Web')})\n"
            output += f"📊 {result['results_count']} résultats:\n\n"

            for r in result["results"][:5]:
                output += f"{r['position']}. **{r['title']}**\n"
                output += f"   🔗 {r['url']}\n"
                if r.get("description"):
                    output += f"   {r['description'][:150]}...\n"
                output += "\n"

            return HandlerResult.ok(output, handler_name="web_search")
        else:
            # Fallback: ouvrir dans le navigateur système
            import urllib.parse
            import webbrowser

            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            webbrowser.open(search_url)
            return HandlerResult.ok(
                f"Recherche ouverte dans le navigateur pour: {query}",
                handler_name="web_search",
            )
    except Exception as e:
        # Fallback final
        try:
            import urllib.parse
            import webbrowser

            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            webbrowser.open(search_url)
            return HandlerResult.ok(
                f"Recherche lancée pour: {query}", handler_name="web_search"
            )
        except Exception as browser_err:
            return HandlerResult.fail(
                f"Erreur recherche: {e}. Browser fallback aussi échoué: {browser_err}",
                handler_name="web_search",
            )


async def web_search_brave_handler(
    ctx: HandlerContext, query: str, count: int = 8
) -> HandlerResult:
    """Recherche web via Brave Search API ou DuckDuckGo en fallback."""
    try:
        hub = ctx.get_search_hub()
        data = await hub.web_search(query, count=int(count))
        from ...tools.search_hub import SearchHub

        output = SearchHub.format_results(data)
        return HandlerResult.ok(output, handler_name="web_search_brave")
    except Exception as e:
        logger.error(f"web_search_brave error: {e}")
        return HandlerResult.fail(
            f"❌ Erreur recherche: {e}", handler_name="web_search_brave"
        )


async def web_fetch_handler(ctx: HandlerContext, url: str) -> HandlerResult:
    """Récupère le contenu d'une page web (urllib puis fallback Playwright anti-bot)."""
    # --- Tentative 1 : urllib rapide ---
    urllib_err = None
    try:
        import urllib.error
        import urllib.request
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text: List[str] = []
                self.skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ["script", "style", "noscript"]:
                    self.skip = True

            def handle_endtag(self, tag):
                if tag in ["script", "style", "noscript"]:
                    self.skip = False

            def handle_data(self, data):
                if not self.skip:
                    text = data.strip()
                    if text:
                        self.text.append(text)

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=7) as response:
            html = response.read().decode("utf-8", errors="ignore")

        extractor = TextExtractor()
        extractor.feed(html)
        text = " ".join(extractor.text)

        if len(text) > 1500:
            text = text[:1500] + "..."

        return HandlerResult.ok(
            f"Contenu de {url}:\n{text}", handler_name="web_fetch"
        )
    except Exception as urllib_err_exc:
        urllib_err = urllib_err_exc  # fallback Playwright ci-dessous

    # --- Tentative 2 : Playwright stealth (anti-bot / SPA) ---
    try:
        from ...tools.playwright_browser import get_playwright_browser

        browser = get_playwright_browser()
        result = await browser.get_page_content(url)
        if result.get("success") and result.get("content"):
            text = result["content"]
            if len(text) > 2000:
                text = text[:2000] + "..."
            return HandlerResult.ok(
                f"Contenu de {url} (via navigateur):\n{text}",
                handler_name="web_fetch",
            )
        return HandlerResult.fail(
            f"Erreur fetch: urllib={urllib_err}, navigateur={result.get('error', 'vide')}",
            handler_name="web_fetch",
        )
    except Exception as pw_err:
        return HandlerResult.fail(
            f"Erreur fetch: urllib={urllib_err}, navigateur={pw_err}",
            handler_name="web_fetch",
        )


async def deep_research_handler(
    ctx: HandlerContext, query: str, max_pages: int = 5
) -> HandlerResult:
    """Recherche approfondie avec analyse multi-pages."""
    try:
        from ...tools.playwright_browser import get_playwright_browser

        browser = get_playwright_browser()
        result = await browser.deep_research(query, max_pages)

        if result["success"]:
            output = f"🔬 **Recherche approfondie: {query}**\n\n"
            output += f"📊 {result['pages_analyzed']} pages analysées dans {result['tabs_opened']} onglets\n\n"

            # Sources
            output += "📋 **Sources consultées:**\n"
            for source in result.get("sources", []):
                output += f"  • {source['title'][:60]}...\n    🔗 {source['url']}\n"

            output += "\n---\n\n"

            # Synthèse
            output += "📝 **Synthèse:**\n\n"
            output += result.get("synthesis", "Pas de synthèse disponible.")

            return HandlerResult.ok(output, handler_name="deep_research")

        return HandlerResult.fail(
            f"❌ Erreur: {result.get('error', 'Recherche impossible')}",
            handler_name="deep_research",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur deep_research: {e}", handler_name="deep_research"
        )


async def web_crawl_campaign_handler(
    ctx: HandlerContext,
    start_url: str,
    campaign_id: str = "",
    profile: str = "balanced",
    pages_per_run: int = 200,
    max_total_pages: int = 5000,
    max_depth: int = 3,
    keyword_hint: str = "",
    same_domain_only: bool = True,
) -> HandlerResult:
    """Lance ou reprend une campagne de scalping web."""
    try:
        crawler = ctx.get_web_crawler()
        defaults = _resolve_web_crawl_profile(profile)
        effective_depth = int(
            max_depth if max_depth and max_depth > 0 else defaults["max_depth"]
        )

        result = await crawler.crawl_campaign(
            start_url=start_url,
            campaign_id=campaign_id,
            pages_per_run=pages_per_run,
            max_total_pages=max_total_pages,
            max_depth=effective_depth,
            keyword_hint=keyword_hint,
            same_domain_only=same_domain_only,
            request_timeout_sec=defaults["request_timeout_sec"],
            request_retries=defaults["request_retries"],
            retry_backoff_sec=defaults["retry_backoff_sec"],
            max_links_per_page=defaults["max_links_per_page"],
            delay_sec=defaults["delay_sec"],
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl_campaign: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl_campaign",
            )
        output = (
            f"🌐 Campagne crawl: {result.get('campaign_id')}\n"
            f"- run_id: {result.get('run_id')}\n"
            f"- profile: {profile}\n"
            f"- visités: {result.get('run_visited')}\n"
            f"- intéressants: {result.get('run_interesting')}\n"
            f"- erreurs: {result.get('run_errors')}\n"
            f"- total: {result.get('pages_crawled_total')}/{result.get('max_total_pages')}\n"
            f"- done: {result.get('done')}\n"
            f"- next: {result.get('next')}"
        )
        return HandlerResult.ok(output, handler_name="web_crawl_campaign")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl_campaign: {e}", handler_name="web_crawl_campaign"
        )


async def web_crawl_campaign_status_handler(
    ctx: HandlerContext, campaign_id: str
) -> HandlerResult:
    """Retourne l'état d'une campagne de scalping web."""
    try:
        crawler = ctx.get_web_crawler()
        result = crawler.campaign_status(campaign_id)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl_campaign_status: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl_campaign_status",
            )
        output = (
            f"📊 Campagne: {result.get('campaign_id')}\n"
            f"- seed: {result.get('seed_url')}\n"
            f"- runs: {result.get('runs')}\n"
            f"- total: {result.get('pages_crawled_total')}/{result.get('max_total_pages')}\n"
            f"- intéressants: {result.get('interesting_total')}\n"
            f"- erreurs: {result.get('errors_total')}\n"
            f"- queue: {result.get('queue_remaining')}\n"
            f"- done: {result.get('done')}"
        )
        return HandlerResult.ok(output, handler_name="web_crawl_campaign_status")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl_campaign_status: {e}",
            handler_name="web_crawl_campaign_status",
        )


async def web_crawl_campaign_pro_report_handler(
    ctx: HandlerContext,
    campaign_id: str,
    top_n_findings: int = 40,
    include_last_runs: int = 5,
    report_title: str = "",
    send_via_email: bool = False,
    mail_alias: str = "",
    mail_to: str = "",
    mail_cc: str = "",
    mail_bcc: str = "",
) -> HandlerResult:
    """Génère un rapport premium professionnel d'une campagne web."""
    try:
        crawler = ctx.get_web_crawler()
        result = crawler.campaign_generate_pro_report(
            campaign_id=campaign_id,
            top_n_findings=top_n_findings,
            include_last_runs=include_last_runs,
            report_title=report_title,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl_campaign_pro_report: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl_campaign_pro_report",
            )

        lines = [
            f"📘 Rapport premium: {result.get('campaign_id')}",
            f"- score: {result.get('overall_score')}/100",
            f"- report_md: {result.get('report_md')}",
            f"- report_json: {result.get('report_json')}",
        ]

        if send_via_email:
            if not mail_alias.strip() or not mail_to.strip():
                lines.append("- email: non envoyé (mail_alias + mail_to requis)")
            else:
                hub = ctx.get_mail_hub()
                md_path = Path(str(result.get("report_md") or ""))
                md_content = (
                    md_path.read_text(encoding="utf-8", errors="replace")
                    if md_path.exists()
                    else ""
                )
                if len(md_content) > 18000:
                    md_content = (
                        md_content[:18000] + "\n\n...[rapport tronqué pour email]"
                    )
                email_result = hub.send_message(
                    alias=mail_alias,
                    to=mail_to,
                    subject=f"Rapport Web Premium — {result.get('campaign_id')} — score {result.get('overall_score')}/100",
                    body=md_content,
                    cc=mail_cc,
                    bcc=mail_bcc,
                )
                if email_result.get("success"):
                    lines.append(f"- email: envoyé à {mail_to}")
                else:
                    lines.append(
                        f"- email: échec ({email_result.get('error', 'erreur inconnue')})"
                    )

        return HandlerResult.ok(
            "\n".join(lines), handler_name="web_crawl_campaign_pro_report"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl_campaign_pro_report: {e}",
            handler_name="web_crawl_campaign_pro_report",
        )


async def web_crawl_campaign_explain_handler(
    ctx: HandlerContext, campaign_id: str, page_url: str = ""
) -> HandlerResult:
    """Explique en clair ce que propose une page d'une campagne."""
    try:
        crawler = ctx.get_web_crawler()
        result = crawler.campaign_explain_page(
            campaign_id=campaign_id, page_url=page_url
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl_campaign_explain: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl_campaign_explain",
            )

        exp = result.get("explanation") or {}
        where_or_contact = exp.get("where_or_contact") or {}
        output = (
            f"🧠 Explication page\n"
            f"- page: {exp.get('page_url')}\n"
            f"- titre: {exp.get('title')}\n"
            f"- offre: {exp.get('what_page_offers')}\n"
            f"- cible: {exp.get('target_audience')}\n"
            f"- où/contact: {where_or_contact.get('location')}\n"
            f"- emails: {', '.join(where_or_contact.get('emails') or []) or '-'}\n"
            f"- téléphones: {', '.join(where_or_contact.get('phones') or []) or '-'}\n"
            f"- prix: {', '.join(exp.get('pricing_signals') or []) or '-'}\n"
            f"- CTA: {', '.join(exp.get('cta_signals') or []) or '-'}"
        )
        return HandlerResult.ok(output, handler_name="web_crawl_campaign_explain")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl_campaign_explain: {e}",
            handler_name="web_crawl_campaign_explain",
        )


async def web_crawl_handler(
    ctx: HandlerContext,
    start_url: str,
    profile: str = "balanced",
    max_pages: int = 100,
    max_depth: int = 2,
    keyword_hint: str = "",
    same_domain_only: bool = True,
    request_timeout_sec: int = 0,
    request_retries: int = -1,
    retry_backoff_sec: float = -1.0,
    max_links_per_page: int = 0,
    include_patterns: str = "",
    exclude_patterns: str = "",
    delay_sec: float = -1.0,
) -> HandlerResult:
    """Crawl web simple (une passe, sans reprise). Explore une URL et ses liens jusqu'à la limite."""
    try:
        crawler = ctx.get_web_crawler()
        defaults = _resolve_web_crawl_profile(profile)
        effective_depth = int(max_depth if max_depth and max_depth > 0 else defaults["max_depth"])
        effective_timeout = int(request_timeout_sec if request_timeout_sec and request_timeout_sec > 0 else defaults["request_timeout_sec"])
        effective_retries = int(request_retries if request_retries >= 0 else defaults["request_retries"])
        effective_backoff = float(retry_backoff_sec if retry_backoff_sec >= 0 else defaults["retry_backoff_sec"])
        effective_links = int(max_links_per_page if max_links_per_page and max_links_per_page > 0 else defaults["max_links_per_page"])
        effective_delay = float(delay_sec if delay_sec >= 0 else defaults["delay_sec"])

        result = await crawler.crawl(
            start_url=start_url,
            max_pages=max_pages,
            max_depth=effective_depth,
            keyword_hint=keyword_hint,
            same_domain_only=same_domain_only,
            request_timeout_sec=effective_timeout,
            request_retries=effective_retries,
            retry_backoff_sec=effective_backoff,
            max_links_per_page=effective_links,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            delay_sec=effective_delay,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl",
            )
        lines = [
            f"🌐 Crawl terminé: {start_url}",
            f"- run_id: {result.get('run_id', '-')}",
            f"- profil: {profile}",
            f"- pages visitées: {result.get('visited', 0)}",
            f"- pages capturées: {result.get('captured', 0)}",
            f"- pages intéressantes: {result.get('interesting', 0)}",
            f"- erreurs: {result.get('errors', 0)}",
            f"- durée: {result.get('duration_sec', '?')}s",
        ]
        if result.get("report_md"):
            lines.append(f"- rapport MD: {result.get('report_md')}")
        if result.get("report_json"):
            lines.append(f"- rapport JSON: {result.get('report_json')}")
        top = result.get("top") or []
        if top:
            lines.append("- top pages:")
            for item in top[:5]:
                lines.append(f"  • [{item.get('score')}] {item.get('title') or item.get('url')}")
        return HandlerResult.ok("\n".join(lines), handler_name="web_crawl")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl: {e}", handler_name="web_crawl"
        )


async def web_crawl_campaign_export_handler(
    ctx: HandlerContext,
    campaign_id: str,
    top_n: int = 100,
) -> HandlerResult:
    """Exporte les résultats d'une campagne de crawl (top N pages pertinentes) vers un fichier."""
    try:
        crawler = ctx.get_web_crawler()
        result = crawler.export_campaign(campaign_id=campaign_id, top_n=top_n)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ web_crawl_campaign_export: {result.get('error', 'erreur inconnue')}",
                handler_name="web_crawl_campaign_export",
            )
        return HandlerResult.ok(
            f"✅ Export campagne '{campaign_id}': {result.get('file_path', 'fichier généré')}",
            handler_name="web_crawl_campaign_export",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur web_crawl_campaign_export: {e}", handler_name="web_crawl_campaign_export"
        )


# ─── Registration ──────────────────────────────────────────────────────────

def get_web_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers web pour le registre V2."""
    return [
        HandlerDef(
            name="web_search",
            description="Recherche sur internet (ouvre Google avec la requete).",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "La requete de recherche"},
                },
                "required": ["query"],
            },
            handler=web_search_real_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_search_brave",
            description=(
                "Recherche web rapide (DuckDuckGo si pas de cle Brave — qualite limitee). "
                "Preferer browser_search_google pour des resultats plus precis."
            ),
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "La requete de recherche"},
                    "count": {
                        "type": "integer",
                        "description": "Nombre de resultats (defaut: 8, max: 20)",
                    },
                },
                "required": ["query"],
            },
            handler=web_search_brave_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_fetch",
            description="Recupere le contenu d'une page web (fallback navigateur automatique si le site bloque).",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL a recuperer"},
                },
                "required": ["url"],
            },
            handler=web_fetch_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="deep_research",
            description="Recherche approfondie: ouvre plusieurs pages, analyse et synthetise.",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "La requete de recherche"},
                    "max_pages": {
                        "type": "integer",
                        "description": "Nombre de pages a analyser (defaut: 5)",
                    },
                },
                "required": ["query"],
            },
            handler=deep_research_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl_campaign",
            description="Lance ou reprend une campagne de scalping web avec reprise automatique.",
            parameters={
                "properties": {
                    "start_url": {"type": "string", "description": "URL de depart"},
                    "campaign_id": {
                        "type": "string",
                        "description": "ID campagne (vide = nouveau)",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Profil: fast | balanced | deep",
                    },
                    "pages_per_run": {
                        "type": "integer",
                        "description": "Pages a traiter dans ce run",
                    },
                    "max_total_pages": {
                        "type": "integer",
                        "description": "Limite globale campagne",
                    },
                    "max_depth": {"type": "integer", "description": "Profondeur max"},
                    "keyword_hint": {
                        "type": "string",
                        "description": "Mots-cles prioritaires",
                    },
                    "same_domain_only": {
                        "type": "boolean",
                        "description": "Limiter au domaine initial",
                    },
                },
                "required": ["start_url"],
            },
            handler=web_crawl_campaign_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl_campaign_status",
            description="Retourne l'etat d'une campagne de scalping web.",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID campagne"},
                },
                "required": ["campaign_id"],
            },
            handler=web_crawl_campaign_status_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl_campaign_pro_report",
            description="Genere un rapport premium professionnel d'une campagne web.",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID campagne"},
                    "top_n_findings": {
                        "type": "integer",
                        "description": "Nombre de points cles",
                    },
                    "include_last_runs": {
                        "type": "integer",
                        "description": "Runs recents analyses",
                    },
                    "report_title": {
                        "type": "string",
                        "description": "Titre du rapport",
                    },
                    "send_via_email": {
                        "type": "boolean",
                        "description": "Envoyer le resume par email",
                    },
                    "mail_alias": {
                        "type": "string",
                        "description": "Alias compte mail",
                    },
                    "mail_to": {"type": "string", "description": "Destinataire"},
                    "mail_cc": {"type": "string", "description": "CC"},
                    "mail_bcc": {"type": "string", "description": "BCC"},
                },
                "required": ["campaign_id"],
            },
            handler=web_crawl_campaign_pro_report_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl_campaign_explain",
            description="Explique en clair ce que propose une page d'une campagne.",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID campagne"},
                    "page_url": {
                        "type": "string",
                        "description": "URL a expliquer (optionnel)",
                    },
                },
                "required": ["campaign_id"],
            },
            handler=web_crawl_campaign_explain_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl",
            description=(
                "Crawl web simple (une seule passe, sans reprise). Explore une URL en profondeur "
                "et retourne un résumé des pages trouvées. Pour des crawls massifs, préférer web_crawl_campaign."
            ),
            parameters={
                "properties": {
                    "start_url": {"type": "string", "description": "URL de départ"},
                    "profile": {"type": "string", "description": "Profil: fast | balanced | deep", "default": "balanced"},
                    "max_pages": {"type": "integer", "description": "Nombre max de pages à visiter", "default": 100},
                    "max_depth": {"type": "integer", "description": "Profondeur maximale des liens", "default": 2},
                    "keyword_hint": {"type": "string", "description": "Mots-clés prioritaires pour filtrer les pages", "default": ""},
                    "same_domain_only": {"type": "boolean", "description": "Rester sur le même domaine", "default": True},
                    "include_patterns": {"type": "string", "description": "Patterns d'URL à inclure (séparés par virgule)", "default": ""},
                    "exclude_patterns": {"type": "string", "description": "Patterns d'URL à exclure (séparés par virgule)", "default": ""},
                    "request_timeout_sec": {"type": "integer", "description": "Timeout par requête en secondes (0 = profil par défaut)", "default": 0},
                    "max_links_per_page": {"type": "integer", "description": "Limite de liens suivis par page (0 = profil par défaut)", "default": 0},
                    "delay_sec": {"type": "number", "description": "Délai entre requêtes en secondes (-1 = profil par défaut)", "default": -1},
                },
                "required": ["start_url"],
            },
            handler=web_crawl_handler,
            category="web",
            source_module="handlers.web",
        ),
        HandlerDef(
            name="web_crawl_campaign_export",
            description="Exporte les résultats d'une campagne de crawl (top N pages pertinentes) dans un fichier CSV/JSON.",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID de la campagne à exporter"},
                    "top_n": {"type": "integer", "description": "Nombre de pages les plus pertinentes à exporter", "default": 100},
                },
                "required": ["campaign_id"],
            },
            handler=web_crawl_campaign_export_handler,
            category="web",
            source_module="handlers.web",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
