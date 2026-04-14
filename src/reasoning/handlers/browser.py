"""
browser.py - Handlers navigateur (Playwright) fragmentes depuis react.py.

Phase 2.1: Migration Selenium -> Playwright pour de meilleures performances,
des profils persistants et un auto-wait integre.

Handlers (35): browser_start, browser_stop, browser_navigate, browser_search_google,
    browser_get_content, browser_click, browser_accept_cookies,
    browser_click_at, browser_type, browser_screenshot, browser_scroll,
    browser_tabs, browser_new_tab, browser_back, browser_refresh,
    browser_close_all_tabs, browser_switch_tab, browser_close_tab,
    browser_tab_find, browser_tab_switch,
    browser_dom_state, browser_click_index, browser_type_index,
    browser_evaluate, browser_forward, browser_wait_for, browser_page_info,
    browser_deep_research, browser_hover, browser_select, browser_keyboard_press,
    browser_save_pdf, browser_upload_file, browser_block_resources,
    browser_unblock_resources.

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

async def browser_start(ctx: HandlerContext, *, headless: bool = False) -> HandlerResult:
    """Demarre le navigateur Playwright (Chromium)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser(headless=headless)
        if await browser.start():
            return HandlerResult.ok("🌐 Navigateur Playwright demarré avec succes")
        return HandlerResult.fail("Echec du demarrage du navigateur")
    except ImportError:
        return HandlerResult.fail("Playwright non installe. pip install playwright && playwright install chromium")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_stop(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Ferme le navigateur."""
    try:
        from ...tools.playwright_browser import close_playwright_browser
        await close_playwright_browser()
        return HandlerResult.ok("🌐 Navigateur ferme")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_navigate(ctx: HandlerContext, *, url: str) -> HandlerResult:
    """Navigue vers une URL."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.navigate(url)
        if result["success"]:
            # Attendre le rendu JS pour que dom_state ait des éléments
            try:
                await browser._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # timeout acceptable, la page est déjà navigable
            return HandlerResult.ok(f"✅ Navigué vers: {result['title']} ({result['url']})")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_search_google(ctx: HandlerContext, *, query: str) -> HandlerResult:
    """Fait une recherche Google."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.search_google(query)
        if result["success"]:
            output = f"🔍 Recherche: {query}\n📊 {result['results_count']} resultats:\n\n"
            for r in result["results"][:5]:
                output += f"{r['position']}. **{r['title']}**\n   {r['url']}\n   {r['description'][:100]}...\n\n"
            return HandlerResult.ok(output)
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_get_content(ctx: HandlerContext, *, url: str = None) -> HandlerResult:
    """Recupere le contenu de la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.get_page_content(url)
        if result["success"]:
            return HandlerResult.ok(f"📄 Page: {result['title']}\n\n{result['content'][:3000]}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_click(ctx: HandlerContext, *, selector: str = "", by: str = "css", text: str = "") -> HandlerResult:
    """Clique sur un element."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        resolved_selector = (selector or "").strip()
        resolved_by = (by or "css").strip().lower()

        if text and text.strip():
            resolved_selector = text.strip()
            if resolved_by == "css":
                resolved_by = "partial_text"

        if not resolved_selector:
            return HandlerResult.fail("Erreur: selector ou text requis")

        result = await browser.click_element(resolved_selector, resolved_by)
        if result["success"]:
            return HandlerResult.ok(f"✅ Clique sur: {resolved_selector}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_accept_cookies(ctx: HandlerContext) -> HandlerResult:
    """Accepte les cookies via selecteurs et textes frequents."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.accept_cookies()
        if result.get("success"):
            return HandlerResult.ok(
                f"🍪 Cookies acceptes\n"
                f"- methode: {result.get('method')}\n"
                f"- cible: {result.get('selector')}"
            )
        return HandlerResult.fail(f"Cookies non acceptes: {result.get('error')}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_click_at(ctx: HandlerContext, *, x: int, y: int) -> HandlerResult:
    """Clique via souris a des coordonnees x,y."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.click_at(x, y)
        if result.get("success"):
            pos = result.get("clicked_at") or {}
            vp = result.get("viewport") or {}
            return HandlerResult.ok(
                f"🖱️ Clic souris effectue\n"
                f"- x: {pos.get('x')}\n"
                f"- y: {pos.get('y')}\n"
                f"- viewport: {vp.get('w')}x{vp.get('h')}"
            )
        return HandlerResult.fail(f"Erreur clic souris: {result.get('error')}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_type(ctx: HandlerContext, *, selector: str, text: str, by: str = "css") -> HandlerResult:
    """Tape du texte dans un champ de la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.type_in_field(selector, text, by)
        if result["success"]:
            return HandlerResult.ok(f"✅ Texte tape dans: {selector}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_screenshot(ctx: HandlerContext, *, filename: str = None) -> HandlerResult:
    """Prend une capture d'ecran du navigateur."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.screenshot(filename)
        if result["success"]:
            return HandlerResult.ok(f"📸 Screenshot sauvegarde: {result['path']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_scroll(ctx: HandlerContext, *, direction: str = "down", amount: int = 500) -> HandlerResult:
    """Scroll dans la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.scroll(direction, amount)
        if result["success"]:
            return HandlerResult.ok(f"📜 Scrolle {direction}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_tabs(ctx: HandlerContext) -> HandlerResult:
    """Liste les onglets."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.get_tabs()
        if result["success"]:
            output = f"📑 {result['count']} onglets:\n"
            for tab in result["tabs"]:
                active = "→ " if tab["active"] else "  "
                output += f"{active}[{tab['index']}] {tab['title'][:50]}\n"
            return HandlerResult.ok(output)
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_new_tab(ctx: HandlerContext, *, url: str = None) -> HandlerResult:
    """Ouvre un nouvel onglet."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.new_tab(url)
        if result["success"]:
            return HandlerResult.ok(f"📑 Nouvel onglet ouvert: {result['url']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_back(ctx: HandlerContext) -> HandlerResult:
    """Retour en arriere."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.go_back()
        if result["success"]:
            return HandlerResult.ok(f"⬅️ Retour a: {result['url']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_refresh(ctx: HandlerContext) -> HandlerResult:
    """Rafraichit la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.refresh()
        if result["success"]:
            return HandlerResult.ok(f"🔄 Page rafraichie: {result['url']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_close_all_tabs(ctx: HandlerContext) -> HandlerResult:
    """Ferme tous les onglets sauf le principal."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.close_all_tabs_except_main()
        if result["success"]:
            return HandlerResult.ok(f"🗑️ {result['closed_tabs']} onglets fermes")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_switch_tab(ctx: HandlerContext, index: int = 0) -> HandlerResult:
    """Change d'onglet par index."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.switch_tab(int(index))
        if result["success"]:
            return HandlerResult.ok(
                f"✅ Onglet actif: #{index}\n"
                f"- Titre: {result.get('title', '?')}\n"
                f"- URL: {result.get('url', '?')}"
            )
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_close_tab(ctx: HandlerContext) -> HandlerResult:
    """Ferme l'onglet actuel et bascule sur le premier."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.close_tab()
        if result["success"]:
            return HandlerResult.ok("✅ Onglet ferme.")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Smart Tab Manager (Phase 2.3) ────────────────────────────────────────────

async def browser_tab_find(ctx: HandlerContext, *, query: str) -> HandlerResult:
    """Recherche un onglet par titre ou URL (insensible a la casse)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non demarre")
        result = await browser.tab_find(query)
        if not result["matches"]:
            return HandlerResult.ok(f"🔍 Aucun onglet ne correspond a \"{query}\"")
        output = f"🔍 {result['count']} onglet(s) trouvé(s) pour \"{query}\":\n"
        for m in result["matches"]:
            active = "→ " if m["active"] else "  "
            output += f"{active}[{m['index']}] {m['title'][:50]} — {m['url'][:60]}\n"
        return HandlerResult.ok(output)
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_tab_switch(ctx: HandlerContext, *, query: str) -> HandlerResult:
    """Bascule sur un onglet par recherche texte (titre ou URL).

    Utilise le premier onglet correspondant. Pour un switch par index,
    utiliser browser_switch_tab.
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non demarre")
        result = await browser.switch_tab_by_query(query)
        if result["success"]:
            return HandlerResult.ok(
                f"✅ Onglet actif: #{result['active_tab']}\n"
                f"- Titre: {result.get('title', '?')}\n"
                f"- URL: {result.get('url', '?')}"
            )
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── DOM Index handlers (Phase 2.2) ───────────────────────────────────────────

async def browser_dom_state(ctx: HandlerContext, *, screenshot: bool = False) -> HandlerResult:
    """
    Retourne l'etat DOM indexe de la page courante.
    Si screenshot=True, un screenshot Set-of-Mark est sauvegarde en plus.
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer, render_set_of_mark

        browser = get_playwright_browser()

        # Auto-recovery: si page morte mais contexte existe, récupérer
        if browser._context and (not browser._page or browser._page.is_closed()):
            pages = browser._context.pages
            if pages:
                browser._page = pages[-1]
                logger.debug("🔄 browser_dom_state: page récupérée depuis contexte")
            else:
                try:
                    browser._page = await browser._context.new_page()
                    logger.debug("🔄 browser_dom_state: nouvelle page créée")
                except Exception as exc:
                    logger.debug(f"[Browser] new_page failed: {exc}")

        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        # Attendre que le DOM soit prêt avant le snapshot
        try:
            await browser._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass  # timeout = page déjà chargée ou lente, on continue

        indexer = get_dom_indexer()
        snap = await indexer.snapshot(browser._page)
        snap = await indexer.enrich_with_bboxes(browser._page, snap)

        output = snap.to_text()

        # Optional: screenshot avec Set-of-Mark overlay
        if screenshot and snap.elements:
            try:
                from PIL import Image
                from pathlib import Path
                import io

                raw = await browser._page.screenshot(type="png")
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                img_som = render_set_of_mark(img, snap.elements)

                from ...utils.paths import SCREENSHOTS_DIR
                som_dir = SCREENSHOTS_DIR
                som_dir.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = som_dir / f"som_{ts}.png"
                img_som.save(str(path))

                output += f"\n\n🖼️ Set-of-Mark screenshot: {path}"
            except Exception as e:
                output += f"\n\n⚠️ Screenshot Set-of-Mark echoue: {e}"

        return HandlerResult.ok(output)
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_click_index(ctx: HandlerContext, *, index: int) -> HandlerResult:
    """Clique sur l'element DOM indexe par son numero [N]."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()
        snap = await indexer.snapshot(browser._page)
        snap = await indexer.enrich_with_bboxes(browser._page, snap)

        # Trouver l'element par index
        target = None
        for elem in snap.elements:
            if elem.index == int(index):
                target = elem
                break

        if target is None:
            return HandlerResult.fail(
                f"Element [{index}] introuvable. {len(snap.elements)} elements disponibles (1-{len(snap.elements)})"
            )

        center = target.center
        if center is None:
            return HandlerResult.fail(
                f"Element [{index}] ({target.role} \"{target.name}\") n'a pas de position connue"
            )

        cx, cy = center
        result = await browser.click_at(cx, cy)
        if result.get("success"):
            return HandlerResult.ok(
                f"✅ Clic sur [{index}] {target.role} \"{target.name}\" a ({cx}, {cy})"
            )
        return HandlerResult.fail(f"Erreur clic: {result.get('error')}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_type_index(ctx: HandlerContext, *, index: int, text: str) -> HandlerResult:
    """Tape du texte dans l'element DOM indexe par son numero [N]."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()
        snap = await indexer.snapshot(browser._page)
        snap = await indexer.enrich_with_bboxes(browser._page, snap)

        # Trouver l'element par index
        target = None
        for elem in snap.elements:
            if elem.index == int(index):
                target = elem
                break

        if target is None:
            return HandlerResult.fail(
                f"Element [{index}] introuvable. {len(snap.elements)} elements disponibles (1-{len(snap.elements)})"
            )

        # Verifier que c'est un champ de texte
        text_roles = {"textbox", "searchbox", "combobox", "spinbutton"}
        if target.role not in text_roles:
            return HandlerResult.fail(
                f"Element [{index}] ({target.role} \"{target.name}\") n'est pas un champ de texte. "
                f"Roles acceptes: {', '.join(sorted(text_roles))}"
            )

        center = target.center
        if center is None:
            return HandlerResult.fail(
                f"Element [{index}] ({target.role} \"{target.name}\") n'a pas de position connue"
            )

        # Cliquer d'abord pour focus, puis taper
        cx, cy = center
        await browser.click_at(cx, cy)

        # Utiliser page.keyboard.type pour taper le texte
        await browser._page.keyboard.type(text, delay=30)

        return HandlerResult.ok(
            f"✅ Tape \"{text}\" dans [{index}] {target.role} \"{target.name}\""
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Phase 2.4 — Handlers évolués Playwright 1.58+ ────────────────────────────

async def browser_evaluate(ctx: HandlerContext, *, script: str) -> HandlerResult:
    """Exécute du JavaScript dans la page (retourne le résultat sérialisable)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")
        result = await browser.evaluate(script)
        if result["success"]:
            return HandlerResult.ok(f"✅ JS exécuté\n→ {result.get('result')}")
        return HandlerResult.fail(f"Erreur JS: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_forward(ctx: HandlerContext) -> HandlerResult:
    """Avance d'une page dans l'historique (équivalent bouton →)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.go_forward()
        if result["success"]:
            return HandlerResult.ok(f"➡️ Avancé vers: {result['url']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_wait_for(ctx: HandlerContext, *, selector: str, timeout: int = 5000) -> HandlerResult:
    """Attend qu'un élément CSS apparaisse dans la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.wait_for_selector(selector, timeout=timeout)
        if result["success"]:
            return HandlerResult.ok(f"✅ Élément trouvé: {selector}")
        return HandlerResult.fail(f"Timeout: {selector}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_page_info(ctx: HandlerContext) -> HandlerResult:
    """Retourne l'URL et le titre de la page active."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.get_page_info()
        if result["success"]:
            return HandlerResult.ok(f"📄 Titre: {result['title']}\n🔗 URL: {result['url']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_deep_research(ctx: HandlerContext, *, query: str, max_pages: int = 5) -> HandlerResult:
    """Recherche approfondie multi-pages: Google → ouvre chaque résultat → synthèse."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.deep_research(query, max_pages=max_pages)
        if result["success"]:
            sources = "\n".join(f"  [{s['title'][:40]}]({s['url']})" for s in result.get("sources", []))
            return HandlerResult.ok(
                f"🔬 Deep Research: {query}\n"
                f"📑 {result['pages_analyzed']} pages analysées\n\n"
                f"{result['synthesis']}\n\n"
                f"Sources:\n{sources}"
            )
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_hover(ctx: HandlerContext, *, selector: str, by: str = "css") -> HandlerResult:
    """Survole un élément (déclenche :hover, tooltips, menus déroulants)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.hover(selector, by=by)
        if result["success"]:
            return HandlerResult.ok(f"🖱️ Survol: {selector}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_select(ctx: HandlerContext, *, selector: str, value: str = "",
                         label: str = "", index: int = -1, by: str = "css") -> HandlerResult:
    """Sélectionne une option dans une liste déroulante <select>."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.select_option(selector, value=value, label=label, index=index, by=by)
        if result["success"]:
            return HandlerResult.ok(f"✅ Option sélectionnée: {result.get('selected')}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_keyboard_press(ctx: HandlerContext, *, key: str) -> HandlerResult:
    """Presse une touche clavier (Enter, Tab, Escape, ArrowDown, Control+a, etc.)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.keyboard_press(key)
        if result["success"]:
            return HandlerResult.ok(f"⌨️ Touche pressée: {key}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_save_pdf(ctx: HandlerContext, *, filename: str = None) -> HandlerResult:
    """Exporte la page courante en PDF (nécessite headless=True)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.save_pdf(filename)
        if result["success"]:
            return HandlerResult.ok(f"📄 PDF sauvegardé: {result['path']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_upload_file(ctx: HandlerContext, *, selector: str, file_paths: List[str],
                              by: str = "css") -> HandlerResult:
    """Upload un ou plusieurs fichiers via un <input type='file'>."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.upload_file(selector, file_paths, by=by)
        if result["success"]:
            return HandlerResult.ok(f"📤 Fichiers uploadés: {result['uploaded']}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_block_resources(ctx: HandlerContext, *, resource_types: List[str] = None,
                                  url_patterns: List[str] = None) -> HandlerResult:
    """Bloque trackers/pubs ou types de ressources pour accélérer la navigation."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.block_resources(resource_types=resource_types, url_patterns=url_patterns)
        if result["success"]:
            types_str = ", ".join(result["blocked_types"]) or "aucun"
            urls_count = len(result["blocked_url_patterns"])
            return HandlerResult.ok(
                f"🚫 Filtres réseau activés\n"
                f"- Types bloqués: {types_str}\n"
                f"- Patterns URL bloqués: {urls_count}"
            )
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_unblock_resources(ctx: HandlerContext) -> HandlerResult:
    """Retire tous les filtres réseau — retour à la navigation normale."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.unblock_resources()
        if result["success"]:
            return HandlerResult.ok("✅ Filtres réseau supprimés")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ── Phase 3 — Handlers avancés ─────────────────────────────────────────────────

async def browser_trace_start(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Démarre l'enregistrement Playwright Trace (debug visuel)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.trace_start()
        if result["success"]:
            return HandlerResult.ok("🎬 Trace Playwright démarrée. Navigue puis appelle browser_trace_stop.")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_trace_stop(ctx: HandlerContext, *, name: str = "") -> HandlerResult:
    """Arrête la trace et sauvegarde le .zip pour analyse visuelle."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.trace_stop(name=name or None)
        if result["success"]:
            return HandlerResult.ok(
                f"🎬 Trace sauvegardée: {result['path']}\n"
                f"Ouvre avec: npx playwright show-trace {result['path']}"
            )
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_network_requests(
    ctx: HandlerContext, *, url_filter: str = "", resource_type: str = "", limit: int = 50
) -> HandlerResult:
    """Affiche les requêtes réseau interceptées (XHR, fetch, etc.)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.network_get_requests(
            url_filter=url_filter or None,
            resource_type=resource_type or None,
            limit=limit,
        )
        if result["success"]:
            entries = result["requests"]
            if not entries:
                return HandlerResult.ok("📡 Aucune requête réseau capturée. Navigue d'abord.")
            lines = [f"📡 **{result['filtered_count']}** requêtes (sur {result['total_captured']} capturées):\n"]
            for e in entries[-20:]:  # Limiter l'affichage à 20
                status = e.get("status") or "..."
                method = e.get("method", "?")
                rtype = e.get("resource_type", "?")
                url = e["url"][:120]
                lines.append(f"  {status} {method} [{rtype}] {url}")
            return HandlerResult.ok("\n".join(lines))
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_network_clear(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Vide le buffer de requêtes réseau."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = browser.network_clear()
        return HandlerResult.ok(f"📡 {result['cleared']} requêtes supprimées du buffer")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_emulate_device(ctx: HandlerContext, *, device: str) -> HandlerResult:
    """Émule un device (mobile/tablette/desktop)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.emulate_device(device)
        if result["success"]:
            vp = result["viewport"]
            mobile_str = " (mobile)" if result.get("mobile") else ""
            return HandlerResult.ok(f"📱 Device émulé: {result['device']} — {vp['width']}x{vp['height']}{mobile_str}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_set_geolocation(
    ctx: HandlerContext, *, latitude: float, longitude: float, accuracy: float = 100
) -> HandlerResult:
    """Définit une géolocalisation simulée."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.set_geolocation(latitude, longitude, accuracy)
        if result["success"]:
            return HandlerResult.ok(f"📍 Géolocalisation: {latitude}, {longitude}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_emulate_media(
    ctx: HandlerContext, *, color_scheme: str = "", media: str = ""
) -> HandlerResult:
    """Émule les media CSS (dark mode, print, etc.)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.emulate_media(
            color_scheme=color_scheme or None,
            media=media or None,
        )
        if result["success"]:
            parts = []
            if color_scheme:
                parts.append(f"color_scheme={color_scheme}")
            if media:
                parts.append(f"media={media}")
            return HandlerResult.ok(f"🎨 Media émulé: {', '.join(parts)}")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_cookies_get(ctx: HandlerContext, *, url: str = "") -> HandlerResult:
    """Retourne les cookies du navigateur."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        urls = [url] if url else None
        result = await browser.cookies_get(urls=urls)
        if result["success"]:
            cookies = result["cookies"]
            if not cookies:
                return HandlerResult.ok("🍪 Aucun cookie.")
            lines = [f"🍪 **{result['count']}** cookies:"]
            for c in cookies[:30]:
                val_preview = str(c.get("value", ""))[:40]
                lines.append(f"  {c['name']} = {val_preview}  (domain: {c.get('domain', '?')})")
            if result["count"] > 30:
                lines.append(f"  ... et {result['count'] - 30} de plus")
            return HandlerResult.ok("\n".join(lines))
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_cookies_clear(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Supprime tous les cookies."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.cookies_clear()
        if result["success"]:
            return HandlerResult.ok("🍪 Tous les cookies supprimés")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_storage_get(
    ctx: HandlerContext, *, kind: str = "local", key: str = ""
) -> HandlerResult:
    """Lit le localStorage ou sessionStorage."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.storage_get(kind=kind, key=key or None)
        if result["success"]:
            data = result["data"]
            if not data:
                return HandlerResult.ok(f"💾 {kind}Storage vide.")
            lines = [f"💾 **{kind}Storage** ({len(data)} clé(s)):"]
            for k, v in list(data.items())[:20]:
                lines.append(f"  {k} = {str(v)[:80]}")
            return HandlerResult.ok("\n".join(lines))
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_storage_set(
    ctx: HandlerContext, *, key: str, value: str, kind: str = "local"
) -> HandlerResult:
    """Écrit dans localStorage/sessionStorage."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.storage_set(key=key, value=value, kind=kind)
        if result["success"]:
            return HandlerResult.ok(f"💾 {kind}Storage[{key}] défini")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_storage_clear(ctx: HandlerContext, *, kind: str = "local") -> HandlerResult:
    """Vide le localStorage ou sessionStorage."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.storage_clear(kind=kind)
        if result["success"]:
            return HandlerResult.ok(f"💾 {kind}Storage vidé")
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_batch(
    ctx: HandlerContext, *, actions: list, stop_on_error: bool = True
) -> HandlerResult:
    """Exécute plusieurs actions navigateur en séquence (batch)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.batch_actions(actions, stop_on_error=stop_on_error)
        if result["success"]:
            return HandlerResult.ok(
                f"✅ Batch terminé: {result['succeeded']}/{result['total']} actions réussies"
            )
        lines = [f"⚠️ Batch partiel: {result['succeeded']}/{result['total']} réussies"]
        for r in result["results"]:
            status = "✅" if r.get("success") else "❌"
            lines.append(f"  {status} [{r['index']}] {r['action']}: {r.get('error', 'ok')}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_screenshot_labels(
    ctx: HandlerContext, *, max_labels: int = 80
) -> HandlerResult:
    """Prend un screenshot avec labels [1], [2]... sur les éléments interactifs."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.screenshot_with_labels(max_labels=max_labels)
        if result["success"]:
            lines = [
                f"📸 Screenshot avec {result['labels_count']} labels: {result['path']}",
                "",
            ]
            for lbl in result["labels"][:30]:
                lines.append(f"  [{lbl['label']}] <{lbl['tag']}> {lbl['text']}")
            if result["labels_count"] > 30:
                lines.append(f"  ... et {result['labels_count'] - 30} de plus")
            return HandlerResult.ok("\n".join(lines))
        return HandlerResult.fail(f"Erreur: {result['error']}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Handler Definitions ──────────────────────────────────────────────────────

def get_browser_handler_defs() -> List[HandlerDef]:
    """Retourne les definitions des 35 handlers browser."""
    return [
        HandlerDef(
            name="browser_start",
            description="Demarre le navigateur Chromium controle (Playwright)",
            parameters={
                "properties": {
                    "headless": {"type": "boolean", "description": "Mode sans fenetre", "default": False},
                },
                "required": [],
            },
            handler=browser_start,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_stop",
            description="Ferme le navigateur Chromium controle",
            parameters={"properties": {}, "required": []},
            handler=browser_stop,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_navigate",
            description="Navigue vers une URL dans le navigateur controle",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL a ouvrir"},
                },
                "required": ["url"],
            },
            handler=browser_navigate,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_search_google",
            description="Recherche Google haute qualite via navigateur. Meilleur que web_search_brave pour des resultats precis.",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Requete de recherche"},
                },
                "required": ["query"],
            },
            handler=browser_search_google,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_get_content",
            description="Recupere le contenu textuel de la page actuelle",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL optionnelle a visiter d'abord"},
                },
                "required": [],
            },
            handler=browser_get_content,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_click",
            description="Clique sur un element de la page (par CSS selector, XPath, ID, etc.)",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Selecteur de l'element"},
                    "by": {"type": "string", "description": "Type: css, xpath, id, class, name, text, partial_text", "default": "css"},
                    "text": {"type": "string", "description": "Texte du bouton/lien a cliquer"},
                },
                "required": ["selector"],
            },
            handler=browser_click,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_accept_cookies",
            description="Accepte automatiquement les bandeaux cookies (boutons frequents)",
            parameters={"properties": {}, "required": []},
            handler=browser_accept_cookies,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_click_at",
            description="Clique a des coordonnees ecran (simulation souris)",
            parameters={
                "properties": {
                    "x": {"type": "integer", "description": "Coordonnee X"},
                    "y": {"type": "integer", "description": "Coordonnee Y"},
                },
                "required": ["x", "y"],
            },
            handler=browser_click_at,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_type",
            description="Tape du texte dans un champ de la page",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Selecteur du champ"},
                    "text": {"type": "string", "description": "Texte a taper"},
                    "by": {"type": "string", "description": "Type: css, xpath, id, class, name", "default": "css"},
                },
                "required": ["selector", "text"],
            },
            handler=browser_type,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_screenshot",
            description="Prend une capture d'ecran du navigateur",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier"},
                },
                "required": [],
            },
            handler=browser_screenshot,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_scroll",
            description="Scroll dans la page",
            parameters={
                "properties": {
                    "direction": {"type": "string", "description": "up, down, top, bottom"},
                    "amount": {"type": "integer", "description": "Pixels a scroller", "default": 500},
                },
                "required": ["direction"],
            },
            handler=browser_scroll,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_tabs",
            description="Liste les onglets ouverts",
            parameters={"properties": {}, "required": []},
            handler=browser_tabs,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_new_tab",
            description="Ouvre un nouvel onglet",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL optionnelle"},
                },
                "required": [],
            },
            handler=browser_new_tab,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_back",
            description="Retourne a la page precedente",
            parameters={"properties": {}, "required": []},
            handler=browser_back,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_refresh",
            description="Rafraichit la page actuelle",
            parameters={"properties": {}, "required": []},
            handler=browser_refresh,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_close_all_tabs",
            description="Ferme tous les onglets sauf le principal",
            parameters={"properties": {}, "required": []},
            handler=browser_close_all_tabs,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_switch_tab",
            description="Change d'onglet par index (0 = premier onglet)",
            parameters={
                "properties": {
                    "index": {"type": "integer", "description": "Index de l'onglet (0-based)"},
                },
                "required": ["index"],
            },
            handler=browser_switch_tab,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_close_tab",
            description="Ferme l'onglet actuel et bascule sur le premier",
            parameters={"properties": {}, "required": []},
            handler=browser_close_tab,
            category="browser",
            source_module="handlers.browser",
        ),
        # ─── Phase 2.3 — Smart Tab Manager ────────────────────────────
        HandlerDef(
            name="browser_tab_find",
            description="Recherche un onglet par texte (titre ou URL, insensible a la casse)",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Texte a chercher dans le titre ou l'URL"},
                },
                "required": ["query"],
            },
            handler=browser_tab_find,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_tab_switch",
            description="Bascule sur un onglet par recherche texte (titre ou URL) — premier resultat",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Texte a chercher pour trouver l'onglet"},
                },
                "required": ["query"],
            },
            handler=browser_tab_switch,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_dom_state",
            description="Retourne l'etat DOM indexe de la page (boutons, liens, inputs numérotes [1], [2], ...)",
            parameters={
                "properties": {
                    "screenshot": {"type": "boolean", "description": "Si True, sauvegarde un screenshot Set-of-Mark avec les labels [N]", "default": False},
                },
                "required": [],
            },
            handler=browser_dom_state,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_click_index",
            description="Clique sur l'element DOM par son index [N] (obtenu via browser_dom_state)",
            parameters={
                "properties": {
                    "index": {"type": "integer", "description": "Numero de l'element a cliquer (1-based)"},
                },
                "required": ["index"],
            },
            handler=browser_click_index,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_type_index",
            description="Tape du texte dans un champ par son index [N] (obtenu via browser_dom_state)",
            parameters={
                "properties": {
                    "index": {"type": "integer", "description": "Numero du champ (1-based)"},
                    "text": {"type": "string", "description": "Texte a taper"},
                },
                "required": ["index", "text"],
            },
            handler=browser_type_index,
            category="browser",
            source_module="handlers.browser",
        ),
        # ─── Phase 2.4 — Handlers évolués Playwright 1.58+ ─────────────
        HandlerDef(
            name="browser_evaluate",
            description="Exécute du JavaScript dans la page et retourne le résultat (ex: lire DOM, manipuler état)",
            parameters={
                "properties": {
                    "script": {"type": "string", "description": "Code JavaScript à exécuter"},
                },
                "required": ["script"],
            },
            handler=browser_evaluate,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_forward",
            description="Avance d'une page dans l'historique (équivalent bouton →)",
            parameters={"properties": {}, "required": []},
            handler=browser_forward,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_wait_for",
            description="Attend qu'un sélecteur CSS apparaisse dans la page (utile après navigation JS)",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Sélecteur CSS à attendre"},
                    "timeout": {"type": "integer", "description": "Timeout en ms (défaut: 5000)", "default": 5000},
                },
                "required": ["selector"],
            },
            handler=browser_wait_for,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_page_info",
            description="Retourne l'URL et le titre de la page active sans en changer le contenu",
            parameters={"properties": {}, "required": []},
            handler=browser_page_info,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_deep_research",
            description="Recherche approfondie multi-pages: Google → ouvre chaque résultat → synthèse complète",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Sujet ou question à researcher"},
                    "max_pages": {"type": "integer", "description": "Nombre max de pages à analyser (défaut: 5)", "default": 5},
                },
                "required": ["query"],
            },
            handler=browser_deep_research,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_hover",
            description="Survole un élément pour déclencher :hover CSS, tooltips ou menus déroulants",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Sélecteur de l'élément"},
                    "by": {"type": "string", "description": "Type: css, xpath, id, text", "default": "css"},
                },
                "required": ["selector"],
            },
            handler=browser_hover,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_select",
            description="Sélectionne une option dans une liste déroulante <select>",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Sélecteur du <select>"},
                    "value": {"type": "string", "description": "Valeur de l'option (attribut value)"},
                    "label": {"type": "string", "description": "Texte visible de l'option"},
                    "index": {"type": "integer", "description": "Index 0-based de l'option"},
                    "by": {"type": "string", "description": "Type de sélecteur: css, xpath, id", "default": "css"},
                },
                "required": ["selector"],
            },
            handler=browser_select,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_keyboard_press",
            description="Presse une touche clavier: Enter, Tab, Escape, ArrowDown, Control+a, etc.",
            parameters={
                "properties": {
                    "key": {"type": "string", "description": "Touche à presser (ex: Enter, Tab, Escape, Control+a)"},
                },
                "required": ["key"],
            },
            handler=browser_keyboard_press,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_save_pdf",
            description="Exporte la page courante en PDF (nécessite headless=True)",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier PDF (auto-généré si vide)"},
                },
                "required": [],
            },
            handler=browser_save_pdf,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_upload_file",
            description="Upload un ou plusieurs fichiers via un <input type='file'>",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Sélecteur de l'input file"},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Chemins absolus des fichiers à uploader"},
                    "by": {"type": "string", "description": "Type de sélecteur: css, xpath, id", "default": "css"},
                },
                "required": ["selector", "file_paths"],
            },
            handler=browser_upload_file,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_block_resources",
            description="Bloque les trackers/pubs ou types de ressources (image, font, script...) pour accélérer la nav",
            parameters={
                "properties": {
                    "resource_types": {"type": "array", "items": {"type": "string"}, "description": "Types à bloquer: image, font, media, stylesheet, script"},
                    "url_patterns": {"type": "array", "items": {"type": "string"}, "description": "Sous-chaînes d'URL à bloquer (défaut: trackers connus)"},
                },
                "required": [],
            },
            handler=browser_block_resources,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_unblock_resources",
            description="Retire tous les filtres réseau — retour à la navigation normale",
            parameters={"properties": {}, "required": []},
            handler=browser_unblock_resources,
            category="browser",
            source_module="handlers.browser",
        ),
        # ── Phase 3 — Debug visuel & features avancées ──
        HandlerDef(
            name="browser_trace_start",
            description=(
                "Démarre l'enregistrement Playwright Trace (debug visuel). "
                "Capture screenshots + snapshots DOM à chaque action. "
                "Arrêter avec browser_trace_stop pour obtenir le fichier .zip analysable."
            ),
            parameters={"properties": {}, "required": []},
            handler=browser_trace_start,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_trace_stop",
            description=(
                "Arrête la trace et sauvegarde le .zip. "
                "Ouvrir avec: npx playwright show-trace <fichier>. "
                "Permet d'analyser visuellement chaque étape de navigation."
            ),
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom du fichier trace (optionnel, auto-généré sinon)"},
                },
                "required": [],
            },
            handler=browser_trace_stop,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_network_requests",
            description=(
                "Affiche les requêtes réseau capturées (XHR, fetch, images, scripts...). "
                "Utile pour debug API calls, vérifier les requêtes envoyées par la page."
            ),
            parameters={
                "properties": {
                    "url_filter": {"type": "string", "description": "Sous-chaîne URL pour filtrer (ex: 'api/')"},
                    "resource_type": {"type": "string", "description": "Type: xhr, fetch, document, script, image, font"},
                    "limit": {"type": "integer", "description": "Max résultats (défaut: 50)", "default": 50},
                },
                "required": [],
            },
            handler=browser_network_requests,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_network_clear",
            description="Vide le buffer de requêtes réseau capturées",
            parameters={"properties": {}, "required": []},
            handler=browser_network_clear,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_emulate_device",
            description=(
                "Émule un device mobile/tablette/desktop. "
                "Presets: iphone_14, iphone_14_pro_max, pixel_7, ipad_pro, "
                "galaxy_s23, desktop_1080p, desktop_1440p. "
                "Ou format libre 'WxH' (ex: '375x812')."
            ),
            parameters={
                "properties": {
                    "device": {"type": "string", "description": "Nom du preset ou dimensions WxH"},
                },
                "required": ["device"],
            },
            handler=browser_emulate_device,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_set_geolocation",
            description="Simule une géolocalisation (GPS) pour la page courante",
            parameters={
                "properties": {
                    "latitude": {"type": "number", "description": "Latitude (-90 à 90)"},
                    "longitude": {"type": "number", "description": "Longitude (-180 à 180)"},
                    "accuracy": {"type": "number", "description": "Précision en mètres (défaut: 100)", "default": 100},
                },
                "required": ["latitude", "longitude"],
            },
            handler=browser_set_geolocation,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_emulate_media",
            description=(
                "Émule les media CSS: dark mode (color_scheme='dark'), "
                "mode print (media='print'), etc."
            ),
            parameters={
                "properties": {
                    "color_scheme": {"type": "string", "description": "'dark', 'light', ou 'no-preference'"},
                    "media": {"type": "string", "description": "'screen' ou 'print'"},
                },
                "required": [],
            },
            handler=browser_emulate_media,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_cookies_get",
            description="Récupère les cookies du navigateur (optionnel: filtrer par URL)",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL pour filtrer les cookies (optionnel)"},
                },
                "required": [],
            },
            handler=browser_cookies_get,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_cookies_clear",
            description="Supprime tous les cookies du navigateur",
            parameters={"properties": {}, "required": []},
            handler=browser_cookies_clear,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_storage_get",
            description="Lit le localStorage ou sessionStorage de la page courante",
            parameters={
                "properties": {
                    "kind": {"type": "string", "description": "'local' ou 'session' (défaut: local)", "default": "local"},
                    "key": {"type": "string", "description": "Clé spécifique (optionnel — si vide, retourne tout)"},
                },
                "required": [],
            },
            handler=browser_storage_get,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_storage_set",
            description="Écrit une valeur dans le localStorage ou sessionStorage",
            parameters={
                "properties": {
                    "key": {"type": "string", "description": "Clé"},
                    "value": {"type": "string", "description": "Valeur"},
                    "kind": {"type": "string", "description": "'local' ou 'session' (défaut: local)", "default": "local"},
                },
                "required": ["key", "value"],
            },
            handler=browser_storage_set,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_storage_clear",
            description="Vide le localStorage ou sessionStorage",
            parameters={
                "properties": {
                    "kind": {"type": "string", "description": "'local' ou 'session' (défaut: local)", "default": "local"},
                },
                "required": [],
            },
            handler=browser_storage_clear,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_batch",
            description=(
                "Exécute plusieurs actions navigateur en séquence rapide (batch). "
                "Actions supportées: navigate, click, click_at, type, scroll, wait, "
                "evaluate, screenshot, keyboard, hover, select. "
                "Ex: [{\"action\":\"navigate\",\"url\":\"...\"},{\"action\":\"click\",\"selector\":\"#btn\"}]"
            ),
            parameters={
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Liste d'actions [{action: 'click', selector: '#btn'}, ...]",
                    },
                    "stop_on_error": {"type": "boolean", "description": "Arrêter à la 1ère erreur (défaut: true)", "default": True},
                },
                "required": ["actions"],
            },
            handler=browser_batch,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_screenshot_labels",
            description=(
                "Prend un screenshot avec labels visuels [1], [2]... sur chaque élément "
                "interactif (boutons, liens, inputs). Retourne la map des labels pour "
                "savoir quoi cliquer. Idéal pour le debug visuel et l'analyse de page."
            ),
            parameters={
                "properties": {
                    "max_labels": {"type": "integer", "description": "Nombre max de labels (défaut: 80)", "default": 80},
                },
                "required": [],
            },
            handler=browser_screenshot_labels,
            category="browser",
            source_module="handlers.browser",
        ),
        # ─── Aliases legacy ────────────────────────────────────────────────
        HandlerDef(
            name="browser_get_text",
            description="Alias de browser_get_content. Récupère le contenu textuel de la page actuelle.",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL optionnelle à visiter d'abord"},
                },
                "required": [],
            },
            handler=browser_get_content,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_list_tabs",
            description="Alias de browser_tabs. Liste les onglets ouverts dans le navigateur.",
            parameters={"properties": {}, "required": []},
            handler=browser_tabs,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_open_tab",
            description="Alias de browser_new_tab. Ouvre un nouvel onglet avec une URL optionnelle.",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL optionnelle à ouvrir"},
                },
                "required": [],
            },
            handler=browser_new_tab,
            category="browser",
            source_module="handlers.browser",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
