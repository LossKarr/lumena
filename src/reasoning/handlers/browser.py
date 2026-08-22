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

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


_BROWSER_SPA_SHELL_MARKERS: tuple[str, ...] = (
    "document.documentelement",
    "localstorage.getitem",
    "colorscheme",
    "prefers-color-scheme",
    "function k(",
    "theme\",\"system",
    "webpack",
    "__next",
)


def _format_form_state_summary(form_state: Optional[Dict[str, Any]]) -> str:
    """Formate un résumé compact d'état de formulaire pour le raisonnement browser."""
    if not form_state:
        return ""
    try:
        filled = int(form_state.get("filled", 0))
        checked = int(form_state.get("checked", 0))
        disabled_buttons = int(form_state.get("disabled_buttons", 0))
        enabled_submit_buttons = int(form_state.get("enabled_submit_buttons", 0))
        controls = int(form_state.get("controls", 0))
    except Exception:
        return ""
    return (
        "Form state: "
        f"filled={filled}, checked={checked}, "
        f"disabled_buttons={disabled_buttons}, "
        f"enabled_submit_buttons={enabled_submit_buttons}, "
        f"controls={controls}"
    )


def _normalize_dom_identity_text(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _build_dom_snapshot_meta(snap: Any) -> Dict[str, Any]:
    """Construit une meta compacte du dernier browser_dom_state pour valider les index."""
    index_map: Dict[str, Dict[str, str]] = {}
    for elem in getattr(snap, "elements", []) or []:
        index_map[str(int(elem.index))] = {
            "role": str(elem.role or "").strip().lower(),
            "name": _normalize_dom_identity_text(elem.name or ""),
        }
    return {
        "url": str(getattr(snap, "url", "") or "").strip(),
        "title": str(getattr(snap, "title", "") or "").strip(),
        "indexes": index_map,
        "interactive_count": int(getattr(snap, "total_interactive", 0) or 0),
    }


def _validate_index_against_last_dom_snapshot(browser: Any, snap: Any, target: Any, index: int) -> Optional[str]:
    """Empêche d'agir sur un index DOM devenu périmé depuis le dernier browser_dom_state."""
    meta = getattr(browser, "_last_dom_snapshot_meta", None)
    if not isinstance(meta, dict) or not meta:
        return None

    current_url = str(getattr(snap, "url", "") or "").strip()
    current_title = str(getattr(snap, "title", "") or "").strip()
    previous_url = str(meta.get("url", "") or "").strip()
    previous_title = str(meta.get("title", "") or "").strip()

    if previous_url and current_url and previous_url != current_url:
        return (
            f"Index DOM périmé: le dernier `browser_dom_state` visait `{previous_url}` "
            f"mais la page courante est `{current_url}`. Relis le DOM avant d'agir."
        )
    if previous_title and current_title and previous_title != current_title and not previous_url:
        return (
            f"Index DOM périmé: le dernier `browser_dom_state` visait la page `{previous_title}` "
            f"mais la page courante semble être `{current_title}`. Relis le DOM avant d'agir."
        )

    expected = (meta.get("indexes") or {}).get(str(int(index)))
    if not expected:
        return (
            f"Index DOM périmé: l'élément [{index}] n'était pas présent dans le dernier "
            "`browser_dom_state`. Relis le DOM avant d'agir."
        )

    current_role = str(getattr(target, "role", "") or "").strip().lower()
    current_name = _normalize_dom_identity_text(getattr(target, "name", "") or "")
    expected_role = str(expected.get("role", "") or "").strip().lower()
    expected_name = _normalize_dom_identity_text(expected.get("name", "") or "")

    if current_role != expected_role or current_name != expected_name:
        return (
            f"Index DOM périmé: [{index}] était `{expected_role} \"{expected_name}\"` "
            f"lors du dernier `browser_dom_state`, mais vaut maintenant "
            f"`{current_role} \"{current_name}\"`. Relis le DOM avant d'agir."
        )
    return None


def _looks_like_spa_shell_noise(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    marker_hits = sum(1 for marker in _BROWSER_SPA_SHELL_MARKERS if marker in lower)
    if marker_hits >= 2:
        return True
    if lower.count("=>") >= 2 and lower.count("{") >= 10 and lower.count("}") >= 10:
        return True
    return False


async def _extract_visible_text_snapshot(page: Any, *, max_chars: int = 3000) -> str:
    """Extrait le texte réellement visible et utile sur une SPA/chat."""
    try:
        result = await page.evaluate(
            """
            ({ maxChars }) => {
                const selectors = [
                    '.ProseMirror',
                    '[data-testid*="message"]',
                    '[class*="message"]',
                    '[role="main"]',
                    'main',
                    'article',
                    'section',
                ];
                const chunks = [];
                const seen = new Set();

                const pushText = (value) => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    if (!text || text.length < 8 || seen.has(text)) return;
                    seen.add(text);
                    chunks.push(text);
                };

                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (chunks.length >= 24) break;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        pushText(el.innerText || el.textContent || '');
                    }
                }

                if (!chunks.length) {
                    pushText(document.body?.innerText || '');
                }

                return chunks.join('\\n---\\n').slice(0, maxChars);
            }
            """,
            {"maxChars": max_chars},
        )
        return str(result or "").strip()
    except Exception:
        return ""


async def _read_browser_form_state(page: Any) -> Optional[Dict[str, Any]]:
    """Lit un état formulaire léger après une action clavier/saisie."""
    try:
        state = await page.evaluate(
            """
            () => {
                const controls = Array.from(
                    document.querySelectorAll(
                        'input, textarea, select, button, [role="button"], [role="textbox"], [role="searchbox"], [role="combobox"], [contenteditable="true"], [contenteditable=""], .ProseMirror'
                    )
                );
                const fields = controls.filter(el =>
                    ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
                    || el.isContentEditable
                    || el.matches?.('[contenteditable="true"], [contenteditable=""], .ProseMirror, [role="textbox"], [role="searchbox"], [role="combobox"]')
                );
                const filled = fields.filter(el => {
                    if ('value' in el) return String(el.value || '').trim().length > 0;
                    return String(el.innerText || el.textContent || '').trim().length > 0;
                }).length;
                const checked = controls.filter(el => Boolean(el.checked)).length;
                const buttons = controls.filter(el =>
                    el.tagName === 'BUTTON'
                    || (el.tagName === 'INPUT' && String(el.type || '').toLowerCase() === 'submit')
                    || String(el.getAttribute?.('role') || '').toLowerCase() === 'button'
                );
                const disabledButtons = buttons.filter(el =>
                    Boolean(el.disabled) || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true'
                ).length;
                const enabledSubmitButtons = buttons.filter(el => {
                    const disabled = Boolean(el.disabled) || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                    if (disabled) return false;
                    const type = String(el.type || '').toLowerCase();
                    const label = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').toLowerCase();
                    return type === 'submit'
                        || /envoyer|send|submit|continuer|continue|vérifier|verifier|réinitialiser|reinitialiser|connexion|login/.test(label);
                }).length;
                return {
                    filled,
                    checked,
                    disabled_buttons: disabledButtons,
                    enabled_submit_buttons: enabledSubmitButtons,
                    controls: controls.length,
                };
            }
            """
        )
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _normalize_browser_text_value(value: Any) -> str:
    """Normalise une valeur texte browser pour comparaison robuste."""
    text = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


async def _read_point_text_value(page: Any, x: int, y: int) -> str:
    """Relit la valeur persistante du champ vise a l'ecran."""
    try:
        value = await page.evaluate(
            """
            ({x, y}) => {
                const isTextLike = (el) => {
                    if (!el) return false;
                    const tag = String(el.tagName || '').toLowerCase();
                    const role = String(el.getAttribute?.('role') || '').toLowerCase();
                    const type = String(el.getAttribute?.('type') || el.type || '').toLowerCase();
                    return tag === 'textarea'
                        || el.isContentEditable
                        || role === 'textbox'
                        || role === 'searchbox'
                        || role === 'combobox'
                        || el.matches?.('[contenteditable="true"], [contenteditable=""], .ProseMirror')
                        || (tag === 'input' && ![
                            'checkbox', 'radio', 'submit', 'button', 'reset',
                            'hidden', 'file', 'image', 'range', 'color'
                        ].includes(type));
                };
                const active = document.activeElement;
                const raw = document.elementFromPoint(x, y);
                const target = isTextLike(active)
                    ? active
                    : raw && (
                        raw.matches?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                            ? raw
                            : raw.closest?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                    );
                if (!target) return '';
                if ('value' in target) return String(target.value || '');
                return String(target.innerText || target.textContent || '');
            }
            """,
            {"x": x, "y": y},
        )
        return str(value or "")
    except Exception:
        return ""


async def _probe_point_interaction_state(page: Any, x: int, y: int) -> Optional[Dict[str, Any]]:
    """Décrit l'élément réellement visé à l'écran pour éviter les faux succès de clic."""
    try:
        state = await page.evaluate(
            """
            ({x, y}) => {
                const raw = document.elementFromPoint(x, y);
                const target = raw && (
                    raw.closest?.('button, input, textarea, select, a, [role], [contenteditable="true"], [contenteditable=""], .ProseMirror')
                    || raw
                );
                if (!target) return null;
                const tag = String(target.tagName || '').toLowerCase();
                const role = String(target.getAttribute?.('role') || '').toLowerCase();
                const type = String(target.getAttribute?.('type') || target.type || '').toLowerCase();
                const label = String(
                    target.getAttribute?.('aria-label')
                    || target.innerText
                    || target.textContent
                    || target.value
                    || ''
                ).trim();
                const disabled = Boolean(target.disabled)
                    || String(target.getAttribute?.('aria-disabled') || '').toLowerCase() === 'true';
                const textLike =
                    tag === 'textarea'
                    || tag === 'select'
                    || target.isContentEditable
                    || role === 'textbox'
                    || role === 'searchbox'
                    || role === 'combobox'
                    || (tag === 'input' && ![
                        'checkbox', 'radio', 'submit', 'button', 'reset',
                        'hidden', 'file', 'image', 'range', 'color'
                    ].includes(type));
                const buttonLike =
                    tag === 'button'
                    || tag === 'a'
                    || role === 'button'
                    || role === 'link'
                    || (tag === 'input' && ['submit', 'button'].includes(type));
                const submitLike =
                    type === 'submit'
                    || /envoyer|send|submit|continuer|continue|create account|next|suivant/i.test(label);
                return { tag, role, type, label, disabled, text_like: textLike, button_like: buttonLike, submit_like: submitLike };
            }
            """,
            {"x": x, "y": y},
        )
        return state if isinstance(state, dict) else None
    except Exception:
        return None


async def _get_dom_snapshot_and_target(
    browser: Any,
    indexer: Any,
    index: int,
    *,
    ensure_visible: bool = False,
) -> Tuple[Any, Any]:
    """Résout un élément DOM indexé et tente un scrollIntoView si nécessaire."""
    page = browser._page

    async def _snapshot() -> Any:
        snap = await indexer.snapshot(page)
        return await indexer.enrich_with_bboxes(page, snap)

    def _find_target(current_snap: Any) -> Any:
        for elem in current_snap.elements:
            if elem.index == int(index):
                return elem
        return None

    snap = await _snapshot()
    target = _find_target(snap)
    if target is None or not ensure_visible or target.center is not None:
        return snap, target

    try:
        await page.evaluate(
            """
            ({ role, name, tag }) => {
                const norm = (value) => String(value || '').trim().replace(/\\s+/g, ' ').toLowerCase();
                const inferRole = (el) => {
                    const explicit = norm(el.getAttribute?.('role'));
                    if (explicit) return explicit;
                    const tagName = norm(el.tagName);
                    if (tagName === 'button') return 'button';
                    if (tagName === 'a') return 'link';
                    if (tagName === 'textarea') return 'textbox';
                    if (tagName === 'select') return 'combobox';
                    if (tagName !== 'input') return '';
                    const type = norm(el.type);
                    if (type === 'checkbox') return 'checkbox';
                    if (type === 'radio') return 'radio';
                    if (type === 'submit' || type === 'button') return 'button';
                    return 'textbox';
                };
                const inferName = (el) => norm(
                    el.getAttribute?.('aria-label')
                    || el.innerText
                    || el.textContent
                    || el.value
                    || el.placeholder
                );

                const wantedRole = norm(role);
                const wantedName = norm(name);
                const wantedTag = norm(tag);
                const candidates = Array.from(
                    document.querySelectorAll('input, textarea, select, button, a, [role], [contenteditable="true"], [contenteditable=""]')
                );
                for (const el of candidates) {
                    const elRole = inferRole(el);
                    const elName = inferName(el);
                    const elTag = norm(el.tagName);
                    if (wantedRole && elRole !== wantedRole) continue;
                    if (wantedTag && elTag !== wantedTag) continue;
                    if (wantedName) {
                        const sameName =
                            elName === wantedName
                            || elName.includes(wantedName)
                            || wantedName.includes(elName);
                        if (!sameName) continue;
                    }
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    if (typeof el.focus === 'function') el.focus({ preventScroll: true });
                    return true;
                }
                return false;
            }
            """,
            {"role": target.role, "name": target.name, "tag": target.tag},
        )
        try:
            await page.wait_for_timeout(100)
        except Exception:
            pass
        snap = await _snapshot()
        target = _find_target(snap)
    except Exception:
        pass

    return snap, target


# ─── Auto-visual enrichment (post-action screenshot + description) ──────────

async def _auto_visual_enrich(ctx: HandlerContext, result: HandlerResult,
                              action_label: str = "") -> HandlerResult:
    """Prend un screenshot et ajoute une description vision à l'observation.

    Activé par `LUMENA_BROWSER_AUTO_SCREENSHOT` (défaut: 1). Désactivable.
    Cascade vision : Ollama local → Gemini Flash → fallback payant (cf.
    `MultiProviderLLM.describe_image_cascade`). 100% gratuit si Ollama
    vision installé ou clé Gemini présente (free tier).

    N'échoue jamais : si la capture ou la description plante, retourne
    le `result` original inchangé. Greffé uniquement sur actions majeures
    (navigate, click) pour limiter la latence.
    """
    if os.getenv("LUMENA_BROWSER_AUTO_SCREENSHOT", "1") not in ("1", "true", "True"):
        return result
    if not result.success:
        return result
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not getattr(browser, "_page", None):
            return result
        shot = await browser.screenshot(full_page=False)
        if not shot.get("success"):
            return result
        shot_path = shot.get("path") or ""

        # Description vision (best-effort, ne bloque jamais)
        description = ""
        try:
            llm = getattr(ctx.lumena, "llm", None) if ctx and ctx.lumena else None
            if llm and hasattr(llm, "describe_image_cascade"):
                description = await llm.describe_image_cascade(
                    shot_path,
                    prompt=(
                        "Capture d'écran navigateur après une action. Décris en 3-4 lignes : "
                        "que voit-on à l'écran maintenant ? Formulaires ouverts, popups, "
                        "résultats visibles, état de chargement. Français, concis."
                    ),
                    max_chars=500,
                )
        except Exception as ve:
            logger.debug(f"[auto-visual] describe_image_cascade: {ve}")

        # Assembler l'observation enrichie
        hint_lines = [f"📸 Screenshot: {shot_path}"]
        if description:
            hint_lines.append(f"👁️ Vue: {description.strip()}")
        hint_block = "\n" + "\n".join(hint_lines)
        return HandlerResult(
            success=True,
            output=(result.output or "") + hint_block,
            error=None,
            duration_ms=result.duration_ms,
            handler_name=result.handler_name,
        )
    except Exception as e:
        logger.debug(f"[auto-visual] skip ({action_label}): {e}")
        return result


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


def critical_page_failures(failed_resources: Any) -> list:
    """H5 — échecs HTTP same-origin qui condamnent réellement la page.

    `failed_resources` (verrou 2.7.2) contient des entrées `"/chemin (404)"`,
    déjà filtrées same-host et plafonnées à 5 par le navigateur. On en retire le
    favicon : son absence est cosmétique et ne casse aucune page — c'est la même
    exclusion que `web_project_runtime_verifier._critical_http_response`.

    Pur, défensif : toute entrée illisible est ignorée plutôt que de faire
    échouer une navigation.
    """
    out = []
    try:
        for entry in (failed_resources or []):
            text = str(entry or "").strip()
            if not text:
                continue
            path = text.split(" (")[0].strip().lower()
            if path.endswith("/favicon.ico") or path.endswith("favicon.ico"):
                continue
            out.append(text)
    except Exception:
        return []
    return out


def _record_mission_http_failures(ctx: HandlerContext, failures: list) -> None:
    """H5 — persiste sur la mission un échec HTTP observé au navigateur.

    Le test réel du 2026-08-13 a montré que la porte web (F2) restait inerte
    quand le lead vérifie « à la main » : il a utilisé `serve_website` +
    `browser_navigate`, jamais `browser_verify_local_project`. Or F2 ne lit que
    `web_runtime_failed`, posé par ce seul vérificateur. La mission a donc été
    clôturée `completed` avec sa page d'accueil en **404** — alors que
    `browser_navigate` l'avait vu et affiché.

    Le fait était produit, montré, puis jeté. Ici il devient un fait de clôture,
    quel que soit l'outil qui l'a observé. Fail-open : un signal manquant ne doit
    jamais casser une navigation.
    """
    if not failures:
        return
    try:
        if not getattr(ctx, "is_mission_run", False):
            return
        task_id = getattr(ctx, "runtime_task_id", None)
        core = getattr(ctx, "lumena", None)
        orch = getattr(core, "task_orchestrator", None) if core is not None else None
        if not task_id or orch is None:
            return
        meta = (orch.get_task(task_id) or {}).get("metadata") or {}
        known = list(meta.get("web_http_failures") or [])
        for f in failures:
            if f not in known:
                known.append(f)
        orch.set_task_metadata(task_id, web_http_failures=known[:10])
        logger.warning(
            "[H5] échec HTTP same-origin enregistré sur la mission : {}", failures[:3]
        )
    except Exception as exc:
        logger.debug("[H5] enregistrement d'échec HTTP ignoré: {}", exc)


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
            _nav_msg = f"✅ Navigué vers: {result['title']} ({result['url']})"
            # 2.7.2 (run MiniPanier) — ressources ≥400 de la page : le lead VOIT
            # qu'un CSS/JS ne charge pas (page cassée à l'écran) au lieu de la
            # croire belle. Même-host only, déjà plafonné côté navigateur.
            _failed = result.get("failed_resources") or []
            # H5 — le fait ne se contente plus d'être affiché : il est persisté
            # sur la mission pour peser à la clôture (favicon exclu).
            _record_mission_http_failures(ctx, critical_page_failures(_failed))
            if _failed:
                _nav_msg += (
                    "\n\n⚠️ RESSOURCES EN ÉCHEC sur cette page : "
                    + ", ".join(_failed)
                    + "\n→ La page se charge SANS ces fichiers (style/JS manquant "
                    "= UI cassée ou non-interactive). Corrige les chemins ou la "
                    "config du serveur AVANT de conclure « la page marche »."
                )
            base = HandlerResult.ok(_nav_msg)
            return await _auto_visual_enrich(ctx, base, action_label="navigate")
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


async def _extract_google_search_results(page: Any, max_results: int = 10) -> str:
    """Fix H: Extrait les résultats textuels d'une page Google Search.

    Google Search retourne du CSS minifié via browser_get_content.
    Cette fonction extrait les snippets de résultats directement du DOM.
    """
    try:
        results = await page.evaluate(
            """
            ({ maxResults }) => {
                const out = [];
                const seen = new Set();

                // Méthode 1: cartes de résultats standards (div.g)
                const divGs = document.querySelectorAll('div.g, div[data-hveid]');
                for (const el of divGs) {
                    if (out.length >= maxResults) break;
                    const title = el.querySelector('h3')?.innerText?.trim();
                    const link = el.querySelector('a[href]')?.href;
                    const snippet = el.querySelector('[data-sncf], .VwiC3b, .IsZvec, .MUxGbd')?.innerText?.trim();
                    if (title && title.length > 3) {
                        const key = title.slice(0, 50);
                        if (!seen.has(key)) {
                            seen.add(key);
                            out.push({
                                title,
                                url: link || '',
                                snippet: snippet || '',
                            });
                        }
                    }
                }

                // Méthode 2: résultats locaux (Google Maps intégrés)
                if (out.length === 0) {
                    const localResults = document.querySelectorAll('[data-cid], [data-ftid]');
                    for (const el of localResults) {
                        if (out.length >= maxResults) break;
                        const name = el.querySelector('[class*="fontHeadline"]')?.innerText?.trim()
                            || el.querySelector('h3, h2')?.innerText?.trim();
                        if (name) {
                            const rating = el.querySelector('[aria-label*="étoile"]')?.getAttribute('aria-label') || '';
                            const addr = el.querySelector('[class*="fontBody"] span')?.innerText?.trim() || '';
                            out.push({ title: name, url: '', snippet: [rating, addr].filter(Boolean).join(' — ') });
                        }
                    }
                }

                // Méthode 3: fallback texte visible
                if (out.length === 0) {
                    const main = document.querySelector('#main, #search, [role="main"]');
                    if (main) {
                        const text = main.innerText?.trim().slice(0, 3000);
                        if (text) out.push({ title: 'Résultats Google', url: '', snippet: text });
                    }
                }

                return out;
            }
            """,
            {"maxResults": max_results},
        )
        if not results:
            return ""
        lines = []
        for i, r in enumerate(results, 1):
            title = str(r.get("title", "")).strip()
            url = str(r.get("url", "")).strip()
            snippet = str(r.get("snippet", "")).strip()
            line = f"{i}. **{title}**"
            if url:
                line += f"\n   🔗 {url[:100]}"
            if snippet:
                line += f"\n   {snippet[:200]}"
            lines.append(line)
        return "\n\n".join(lines)
    except Exception as e:
        return ""


async def browser_get_content(ctx: HandlerContext, *, url: str = None) -> HandlerResult:
    """Recupere le contenu de la page."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = await browser.get_page_content(url)
        if result["success"]:
            title = str(result.get("title", "") or "?")
            content = str(result.get("content", "") or "")
            page = getattr(browser, "_page", None)

            # Fix H: Détection Google Search — retourner les snippets de résultats
            # au lieu du CSS minifié que browser_get_content retournerait normalement
            if page:
                current_url = (page.url or "").lower()
                if "google.com/search" in current_url or "recherche google" in title.lower():
                    search_results = await _extract_google_search_results(page)
                    if search_results:
                        return HandlerResult.ok(
                            f"📄 Page: {title}\n\n"
                            "🔍 Résultats Google Search extraits:\n\n"
                            f"{search_results}"
                        )

            if page and _looks_like_spa_shell_noise(content):
                visible_text = await _extract_visible_text_snapshot(page)
                if visible_text:
                    return HandlerResult.ok(
                        f"📄 Page: {title}\n\n"
                        "⚠️ SPA shell détectée — texte visible extrait du DOM.\n\n"
                        f"{visible_text[:3000]}"
                    )
            return HandlerResult.ok(f"📄 Page: {title}\n\n{content[:3000]}")
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
            base = HandlerResult.ok(f"✅ Clique sur: {resolved_selector}")
            return await _auto_visual_enrich(ctx, base, action_label="click")
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
    """Rafraichit la page.

    Fix C: Sur les SPAs de chat (Mistral, Claude, ChatGPT, etc.), un refresh détruit
    la conversation en cours. Ce guard bloque le refresh et suggère browser_get_content
    à la place pour lire l'état actuel sans perdre la conversation.
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()

        # Fix C: Guard anti-refresh sur les SPAs de chat
        # Détecter si on est sur une SPA de chat connue
        if browser.is_running and browser._page:
            try:
                current_url = (browser._page.url or "").lower()
                _CHAT_SPA_DOMAINS = (
                    "chat.mistral.ai", "claude.ai", "chatgpt.com", "chat.openai.com",
                    "gemini.google.com", "duck.ai", "huggingface.co/chat",
                    "perplexity.ai", "you.com", "poe.com",
                )
                if any(domain in current_url for domain in _CHAT_SPA_DOMAINS):
                    return HandlerResult.fail(
                        f"⚠️ Refresh bloqué sur SPA de chat ({current_url.split('/')[2]}) — "
                        "un refresh détruirait la conversation en cours. "
                        "Utilise browser_get_content pour lire l'état actuel de la conversation, "
                        "ou browser_evaluate pour extraire les messages."
                    )
            except Exception:
                pass  # En cas d'erreur, continuer le refresh normal

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

        # Fermer le bandeau cookies AVANT d'indexer (rapide ~0.5s) : sinon ses
        # boutons occupent les premiers index et le LLM clique dessus par erreur
        # (ex. "Préférences de consentement"). Best-effort, jamais bloquant.
        try:
            await browser.accept_cookies()
        except Exception:
            pass

        indexer = get_dom_indexer()
        snap = await indexer.snapshot(browser._page)
        snap = await indexer.enrich_with_bboxes(browser._page, snap)

        # Fix 6: Adapter l'output DOM au context_window du modèle actif
        # Le modèle de l'utilisateur reste inchangé — on calibre seulement la taille de l'output
        try:
            from ...computer_use.dom_indexer import get_dom_profile_for_context
            _runtime_ctx = getattr(ctx.lumena, "runtime_ctx", None) if ctx and ctx.lumena else None
            _max_ctx = getattr(_runtime_ctx, "max_context_window", 0) if _runtime_ctx else 0
            _profile = get_dom_profile_for_context(int(_max_ctx or 0))
            output = snap.to_text(
                max_elements=_profile["max_elements"],
                max_label=_profile["max_label"],
            )
        except Exception:
            output = snap.to_text()

        try:
            form_state = await browser._page.evaluate(
                """
                () => {
                    const controls = Array.from(
                        document.querySelectorAll('input, textarea, select, button, [role="button"], [role="switch"]')
                    );
                    let filled = 0;
                    let checked = 0;
                    let disabledButtons = 0;
                    let enabledSubmitButtons = 0;

                    for (const el of controls) {
                        const tag = (el.tagName || '').toUpperCase();
                        const type = ((el.getAttribute && el.getAttribute('type')) || el.type || '').toLowerCase();
                        const role = (el.getAttribute && el.getAttribute('role')) || '';
                        const isDisabled = !!(
                            el.disabled ||
                            (el.getAttribute && el.getAttribute('aria-disabled') === 'true')
                        );

                        const isTextLike =
                            tag === 'TEXTAREA' ||
                            tag === 'SELECT' ||
                            (tag === 'INPUT' && ![
                                'checkbox', 'radio', 'submit', 'button', 'reset',
                                'hidden', 'file', 'image', 'range', 'color'
                            ].includes(type));
                        if (isTextLike) {
                            const value = typeof el.value === 'string' ? el.value.trim() : '';
                            if (value) {
                                filled += 1;
                            }
                        }

                        const isCheckable =
                            (tag === 'INPUT' && (type === 'checkbox' || type === 'radio')) ||
                            role === 'switch';
                        if (isCheckable) {
                            const isChecked = typeof el.checked === 'boolean'
                                ? !!el.checked
                                : (el.getAttribute && el.getAttribute('aria-checked') === 'true');
                            if (isChecked) {
                                checked += 1;
                            }
                        }

                        const isButtonLike =
                            tag === 'BUTTON' ||
                            (tag === 'INPUT' && (type === 'submit' || type === 'button')) ||
                            role === 'button';
                        if (isButtonLike && isDisabled) {
                            disabledButtons += 1;
                        }
                        if (
                            (tag === 'BUTTON' && /generate|submit|create|send|soumettre|générer/i.test(el.innerText || el.textContent || '')) ||
                            (tag === 'INPUT' && type === 'submit')
                        ) {
                            if (!isDisabled) {
                                enabledSubmitButtons += 1;
                            }
                        }
                    }

                    return {
                        filled,
                        checked,
                        disabled_buttons: disabledButtons,
                        enabled_submit_buttons: enabledSubmitButtons,
                        controls: controls.length,
                    };
                }
                """
            )
        except Exception:
            form_state = None

        summary = _format_form_state_summary(form_state)
        if summary:
            output += f"\\n\\n{summary}"
        try:
            browser._last_dom_snapshot_meta = _build_dom_snapshot_meta(snap)
        except Exception:
            pass

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


async def browser_click_index(
    ctx: HandlerContext,
    *,
    index: int,
    expected_label: str = "",
    expected_role: str = "",
) -> HandlerResult:
    """Clique sur l'element DOM indexe par son numero [N].

    Retry intelligent (Fix 2/3/4):
    - Tentative 1: clic direct
    - Tentative 2: re-snapshot DOM + scroll vers l'élément
    - Tentative 3: dismiss popups + re-snapshot + clic
    """
    try:
        if int(index) < 1:
            return HandlerResult.fail(
                f"Index DOM invalide: [{index}] — les elements browser sont indexes a partir de 1"
            )

        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer
        from ...tools.browser_retry import is_retryable_error

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()

        # Fix 4: Scroll déterministe — garantir que l'élément est dans le viewport
        snap, target = await _get_dom_snapshot_and_target(
            browser, indexer, index, ensure_visible=True
        )

        if target is None:
            # Fix 2: Retry avec re-snapshot si élément introuvable (DOM peut avoir changé)
            _RETRY_DELAYS = (200, 500)
            for delay_ms in _RETRY_DELAYS:
                try:
                    await browser._page.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass
                import asyncio as _asyncio
                await _asyncio.sleep(delay_ms / 1000.0)
                snap, target = await _get_dom_snapshot_and_target(
                    browser, indexer, index, ensure_visible=True
                )
                if target is not None:
                    break
            if target is None:
                return HandlerResult.fail(
                    f"Element [{index}] introuvable apres retry. {len(snap.elements)} elements disponibles (1-{len(snap.elements)})"
                )

        stale_reason = _validate_index_against_last_dom_snapshot(browser, snap, target, index)
        if stale_reason:
            return HandlerResult.fail(stale_reason)

        center = target.center
        if center is None:
            # Fix 4: Scroll explicite si pas de bbox
            try:
                await browser._page.evaluate(
                    """
                    ({ role, name }) => {
                        const norm = (v) => String(v || '').trim().replace(/\\s+/g, ' ').toLowerCase();
                        const candidates = Array.from(document.querySelectorAll(
                            'input, textarea, select, button, a, [role], [contenteditable="true"]'
                        ));
                        for (const el of candidates) {
                            const elRole = norm(el.getAttribute?.('role') || el.tagName);
                            const elName = norm(
                                el.getAttribute?.('aria-label') || el.innerText || el.textContent || el.value || ''
                            );
                            if (role && elRole !== norm(role)) continue;
                            if (name && !elName.includes(norm(name))) continue;
                            el.scrollIntoView({ block: 'center', inline: 'center' });
                            return true;
                        }
                        return false;
                    }
                    """,
                    {"role": target.role, "name": target.name},
                )
                import asyncio as _asyncio
                await _asyncio.sleep(0.15)
                snap, target = await _get_dom_snapshot_and_target(browser, indexer, index)
            except Exception:
                pass
            if target is None or target.center is None:
                return HandlerResult.fail(
                    f"Element [{index}] ({target.role if target else '?'} \"{target.name if target else '?'}\") n'a pas de position connue"
                )

        cx, cy = target.center
        interaction_state = await _probe_point_interaction_state(browser._page, cx, cy)
        expected_role_norm = str(expected_role or "").strip().lower()
        expected_label_norm = str(expected_label or "").strip().lower()
        if interaction_state:
            actual_role = str(
                interaction_state.get("role") or interaction_state.get("tag") or target.role or ""
            ).strip().lower()
            actual_label = str(
                interaction_state.get("label") or target.name or ""
            ).strip().lower()
            if expected_role_norm and actual_role != expected_role_norm:
                return HandlerResult.fail(
                    f"Element [{index}] ne correspond pas au role attendu "
                    f"({actual_role or '?'} != {expected_role_norm})"
                )
            if expected_label_norm and expected_label_norm not in actual_label:
                return HandlerResult.fail(
                    f"Element [{index}] ne correspond pas au libelle attendu "
                    f"({actual_label or '?'} ne contient pas {expected_label_norm})"
                )
        if interaction_state and interaction_state.get("disabled"):
            label = interaction_state.get("label") or target.name
            role = interaction_state.get("role") or interaction_state.get("tag") or target.role
            return HandlerResult.fail(
                f"Element [{index}] ({role} \"{label}\") est desactive — clic utile impossible"
            )

        # Fix 3: Post-vérification — capturer l'URL avant le clic pour détecter navigation
        url_before = browser._page.url if browser._page else ""

        result = await browser.click_at(cx, cy)
        if result.get("success"):
            # Fix 3: Post-vérification — vérifier si le DOM a changé (navigation ou mutation)
            post_info = ""
            try:
                import asyncio as _asyncio
                await _asyncio.sleep(0.1)
                url_after = browser._page.url if browser._page else ""
                if url_after and url_before and url_after != url_before:
                    post_info = f" → navigation vers {url_after}"
            except Exception:
                pass
            base = HandlerResult.ok(
                f"✅ Clic sur [{index}] {target.role} \"{target.name}\" a ({cx}, {cy}){post_info}"
            )
            return await _auto_visual_enrich(ctx, base, action_label="click_index")

        # Fix 2: Retry si le clic échoue (erreur transitoire)
        error_msg = result.get("error", "")
        if is_retryable_error(Exception(error_msg)):
            import asyncio as _asyncio
            await _asyncio.sleep(0.3)
            # Re-snapshot et réessayer
            snap2, target2 = await _get_dom_snapshot_and_target(browser, indexer, index, ensure_visible=True)
            if target2 and target2.center:
                cx2, cy2 = target2.center
                result2 = await browser.click_at(cx2, cy2)
                if result2.get("success"):
                    base = HandlerResult.ok(
                        f"✅ Clic sur [{index}] {target2.role} \"{target2.name}\" a ({cx2}, {cy2}) (retry)"
                    )
                    return await _auto_visual_enrich(ctx, base, action_label="click_index")

        return HandlerResult.fail(f"Erreur clic: {result.get('error')}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_type_index(ctx: HandlerContext, *, index: int, text: str) -> HandlerResult:
    """Tape du texte dans l'element DOM indexe par son numero [N]."""
    try:
        if int(index) < 1:
            return HandlerResult.fail(
                f"Index DOM invalide: [{index}] — les elements browser sont indexes a partir de 1"
            )

        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()
        snap, target = await _get_dom_snapshot_and_target(
            browser, indexer, index, ensure_visible=True
        )

        if target is None:
            return HandlerResult.fail(
                f"Element [{index}] introuvable. {len(snap.elements)} elements disponibles (1-{len(snap.elements)})"
            )

        stale_reason = _validate_index_against_last_dom_snapshot(browser, snap, target, index)
        if stale_reason:
            return HandlerResult.fail(stale_reason)

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
        page = browser._page

        # Écriture robuste Playwright: indépendante du Caps Lock/layout clavier.
        current_value = ""
        try:
            filled = await page.evaluate(
                """
                ({x, y, text}) => {
                    const isTextLike = (el) => {
                        if (!el) return false;
                        const tag = String(el.tagName || '').toLowerCase();
                        const role = String(el.getAttribute?.('role') || '').toLowerCase();
                        const type = String(el.getAttribute?.('type') || el.type || '').toLowerCase();
                        return tag === 'textarea'
                            || el.isContentEditable
                            || role === 'textbox'
                            || role === 'searchbox'
                            || role === 'combobox'
                            || el.matches?.('[contenteditable="true"], [contenteditable=""], .ProseMirror')
                            || (tag === 'input' && ![
                                'checkbox', 'radio', 'submit', 'button', 'reset',
                                'hidden', 'file', 'image', 'range', 'color'
                            ].includes(type));
                    };
                    const active = document.activeElement;
                    const raw = document.elementFromPoint(x, y);
                    const target = isTextLike(active)
                        ? active
                        : raw && (
                            raw.matches?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                                ? raw
                                : raw.closest?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                        );
                    if (!target) return { ok: false, reason: 'target_not_found' };
                    target.focus();
                    if ('value' in target) {
                        target.value = '';
                        target.value = text;
                    } else {
                        target.textContent = text;
                    }
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    return {
                        ok: true,
                        value: 'value' in target ? String(target.value || '') : String(target.innerText || target.textContent || ''),
                    };
                }
                """,
                {"x": cx, "y": cy, "text": text},
            )
            if isinstance(filled, dict):
                current_value = str(filled.get("value", "") or "")
        except Exception:
            current_value = ""

        if _normalize_browser_text_value(current_value) != _normalize_browser_text_value(text):
            try:
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            except Exception:
                pass
            try:
                await page.keyboard.insert_text(text)
            except Exception:
                await page.keyboard.type(text, delay=20)
            try:
                current_value = await page.evaluate(
                    """
                    () => {
                        const el = document.activeElement;
                        if (!el) return '';
                        if ('value' in el) return String(el.value || '');
                        return String(el.innerText || el.textContent || '');
                    }
                    """
                )
            except Exception:
                current_value = text
        if _normalize_browser_text_value(current_value) != _normalize_browser_text_value(text):
            return HandlerResult.fail(
                f"Echec de saisie dans [{index}] {target.role} \"{target.name}\" "
                f"(valeur actuelle: {current_value!r})"
            )

        async def _read_form_state() -> dict:
            try:
                state = await page.evaluate(
                    """
                    () => {
                        const controls = Array.from(
                            document.querySelectorAll('input, textarea, select, button, [role="button"]')
                        );
                        const fields = controls.filter(el =>
                            ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
                        );
                        const filled = fields.filter(el => {
                            if ('value' in el) return String(el.value || '').trim().length > 0;
                            return String(el.textContent || '').trim().length > 0;
                        }).length;
                        const checked = controls.filter(el => Boolean(el.checked)).length;
                        const buttons = controls.filter(el =>
                            el.tagName === 'BUTTON'
                            || (el.tagName === 'INPUT' && String(el.type || '').toLowerCase() === 'submit')
                            || String(el.getAttribute?.('role') || '').toLowerCase() === 'button'
                        );
                        const disabledButtons = buttons.filter(el =>
                            Boolean(el.disabled) || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true'
                        ).length;
                        const enabledSubmitButtons = buttons.filter(el => {
                            const disabled = Boolean(el.disabled) || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                            if (disabled) return false;
                            const type = String(el.type || '').toLowerCase();
                            const label = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').toLowerCase();
                            return type === 'submit'
                                || /envoyer|send|submit|continuer|continue|vérifier|verifier|réinitialiser|reinitialiser|connexion|login/.test(label);
                        }).length;
                        return {
                            filled,
                            checked,
                            disabled_buttons: disabledButtons,
                            enabled_submit_buttons: enabledSubmitButtons,
                            controls: controls.length,
                        };
                    }
                    """
                )
                return state if isinstance(state, dict) else {}
            except Exception:
                return {}

        try:
            await page.evaluate(
                """
                ({x, y}) => {
                    const isTextLike = (el) => {
                        if (!el) return false;
                        const tag = String(el.tagName || '').toLowerCase();
                        const role = String(el.getAttribute?.('role') || '').toLowerCase();
                        const type = String(el.getAttribute?.('type') || el.type || '').toLowerCase();
                        return tag === 'textarea'
                            || el.isContentEditable
                            || role === 'textbox'
                            || role === 'searchbox'
                            || role === 'combobox'
                            || el.matches?.('[contenteditable="true"], [contenteditable=""], .ProseMirror')
                            || (tag === 'input' && ![
                                'checkbox', 'radio', 'submit', 'button', 'reset',
                                'hidden', 'file', 'image', 'range', 'color'
                            ].includes(type));
                    };
                    const active = document.activeElement;
                    const raw = document.elementFromPoint(x, y);
                    const target = isTextLike(active)
                        ? active
                        : raw && (
                            raw.matches?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                                ? raw
                                : raw.closest?.('input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="searchbox"], [role="combobox"], .ProseMirror')
                        );
                    if (!target) return false;
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    target.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
                    target.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
                    if (typeof target.blur === 'function') target.blur();
                    return true;
                }
                """,
                {"x": cx, "y": cy},
            )
        except Exception:
            pass

        try:
            await page.wait_for_timeout(80)
        except Exception:
            pass

        persisted_value = await _read_point_text_value(page, cx, cy)
        if _normalize_browser_text_value(persisted_value) != _normalize_browser_text_value(text):
            return HandlerResult.fail(
                f"Echec de saisie dans [{index}] {target.role} \"{target.name}\" "
                f"(valeur persistante: {persisted_value!r})"
            )

        form_state = await _read_browser_form_state(page) or {}
        if form_state.get("enabled_submit_buttons", 0) == 0:
            try:
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(80)
            except Exception:
                pass
            updated_state = await _read_browser_form_state(page)
            if updated_state:
                form_state = updated_state

        summary = ""
        note = ""
        if form_state:
            summary = (
                "\n\nForm state: "
                f"filled={int(form_state.get('filled', 0))}, "
                f"checked={int(form_state.get('checked', 0))}, "
                f"disabled_buttons={int(form_state.get('disabled_buttons', 0))}, "
                f"enabled_submit_buttons={int(form_state.get('enabled_submit_buttons', 0))}, "
                f"controls={int(form_state.get('controls', 0))}"
            )
            if int(form_state.get("enabled_submit_buttons", 0)) > 0:
                note = "\nSoumission prete: un bouton d'envoi/validation est actif."
            else:
                note = "\nSoumission non prete: aucun bouton d'envoi/validation actif apres saisie."

        base = HandlerResult.ok(
            f"✅ Tape \"{text}\" dans [{index}] {target.role} \"{target.name}\"{summary}{note}"
        )
        return await _auto_visual_enrich(ctx, base, action_label="type_index")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


# ─── Phase 2.4 — Handlers évolués Playwright 1.58+ ────────────────────────────

async def browser_dismiss_popups(ctx: HandlerContext) -> HandlerResult:
    """Ferme les popups/modals/bannières courants (cookies, newsletters, overlays).

    Essaye dans l'ordre : touche Escape, puis clics sur sélecteurs connus de
    boutons de fermeture (aria-label, data-testid, class CSS). Ne plante jamais.
    Retourne combien de tentatives ont cliqué quelque chose.
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")
        page = browser._page
        actions: list[str] = []

        # 1) Escape (ferme la plupart des modals bien codés)
        try:
            await page.keyboard.press("Escape")
            actions.append("Escape")
        except Exception:
            pass

        # 2) Clics sur patterns de boutons de fermeture connus
        selectors = [
            '[aria-label*="fermer" i]',
            '[aria-label*="close" i]',
            '[aria-label*="dismiss" i]',
            '[data-testid*="close" i]',
            '[data-testid*="dismiss" i]',
            'button[aria-label="Close"]',
            'button[aria-label="Fermer"]',
            'button.close',
            'button[class*="close" i]',
            # Cookies
            '[id*="cookie" i] button',
            '[class*="cookie" i] button[class*="accept" i]',
            'button:has-text("Tout accepter")',
            'button:has-text("Accepter tout")',
            'button:has-text("J\'accepte")',
            'button:has-text("OK")',
            # Newsletters Airbnb-like
            'button:has-text("Non merci")',
            'button:has-text("Plus tard")',
            'button:has-text("Ignorer")',
        ]

        script = """
        (selectors) => {
            let clicked = 0;
            for (const sel of selectors) {
                try {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && el.offsetParent !== null) {
                            el.click();
                            clicked++;
                            if (clicked >= 3) return clicked;
                        }
                    }
                } catch (e) {}
            }
            return clicked;
        }
        """
        try:
            clicked = await page.evaluate(script, selectors)
            if clicked:
                actions.append(f"{clicked} bouton(s) de fermeture cliqué(s)")
        except Exception as e:
            logger.debug(f"[dismiss_popups] JS evaluate: {e}")

        # 3) Clic sur overlay / backdrop (dernière tentative)
        backdrop_script = """
        () => {
            const sels = ['.modal-backdrop', '[class*="backdrop" i]', '[class*="overlay" i]'];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) {
                    el.click();
                    return true;
                }
            }
            return false;
        }
        """
        try:
            if await page.evaluate(backdrop_script):
                actions.append("overlay cliqué")
        except Exception:
            pass

        summary = ", ".join(actions) if actions else "aucune action (pas de popup détecté)"
        return HandlerResult.ok(f"🚫 Popups: {summary}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_search_maps(
    ctx: HandlerContext,
    *,
    query: str,
    location: str = "",
    max_results: int = 8,
) -> HandlerResult:
    """Fix G: Recherche des lieux sur Google Maps et retourne une liste structurée.

    Contrairement à browser_navigate + browser_dom_state qui ne font pas apparaître
    le panneau latéral de Google Maps, ce handler construit une URL optimisée et
    extrait les résultats directement via JavaScript.

    Args:
        query: Ce qu'on cherche (ex: "boulangerie", "karting", "pharmacie")
        location: Ville ou adresse (ex: "Bois-Colombes 92270", "Paris 75001")
        max_results: Nombre max de résultats (défaut: 8)

    Returns:
        Liste structurée des lieux avec nom, adresse, note.
    """
    try:
        from urllib.parse import quote
        from ...tools.playwright_browser import get_playwright_browser

        browser = get_playwright_browser()
        if not browser.is_running:
            if not await browser.start():
                return HandlerResult.fail("Navigateur non démarré")

        # Construire l'URL Google Maps Search
        search_term = f"{query} {location}".strip()
        url = f"https://www.google.com/maps/search/{quote(search_term)}"

        # Naviguer vers la page
        nav = await browser.navigate(url)
        if not nav.get("success"):
            return HandlerResult.fail(f"Navigation échouée: {nav.get('error')}")

        page = browser._page
        if not page:
            return HandlerResult.fail("Page non disponible")

        # Attendre que les résultats se chargent (panneau latéral [role="feed"])
        try:
            await page.wait_for_selector('[role="feed"]', timeout=8000)
        except Exception:
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

        # Extraire les résultats via JavaScript
        results = await page.evaluate(
            """
            ({ maxResults }) => {
                const feed = document.querySelector('[role="feed"]');
                if (!feed) {
                    // Fallback: chercher les liens vers des lieux
                    const cards = Array.from(document.querySelectorAll('a[href*="/maps/place/"]')).slice(0, maxResults);
                    return cards.map(a => {
                        const parent = a.closest('[jsaction]') || a.parentElement;
                        return {
                            name: a.getAttribute('aria-label')?.trim()
                                || a.querySelector('[class*="fontHeadline"]')?.textContent?.trim()
                                || a.textContent?.trim().slice(0, 60),
                            address: parent?.querySelector('[class*="fontBody"] span')?.textContent?.trim() || '',
                            rating: parent?.querySelector('span[aria-label*="étoile"]')?.getAttribute('aria-label') || '',
                        };
                    }).filter(r => r.name);
                }

                const items = Array.from(feed.querySelectorAll('[jsaction*="mouseover"]')).slice(0, maxResults);
                return items.map(item => {
                    const nameEl = item.querySelector('[class*="fontHeadlineSmall"], [class*="qBF1Pd"]');
                    const addrEl = item.querySelector('[class*="fontBodyMedium"] > span:first-child, [class*="W4Efsd"] > span');
                    const ratingEl = item.querySelector('span[aria-label*="étoile"], span[aria-label*="star"]');
                    return {
                        name: nameEl?.textContent?.trim() || '',
                        address: addrEl?.textContent?.trim() || '',
                        rating: ratingEl?.getAttribute('aria-label')?.trim() || '',
                    };
                }).filter(r => r.name);
            }
            """,
            {"maxResults": max_results},
        )

        if not results:
            return HandlerResult.fail(
                f"Aucun résultat Maps trouvé pour '{search_term}'. "
                "Essaie browser_navigate + browser_dom_state pour explorer manuellement."
            )

        count = len(results)
        lines = [f"📍 {count} lieu(x) trouvé(s) pour '{search_term}':\n"]
        for i, r in enumerate(results, 1):
            name = str(r.get("name", "?"))
            address = str(r.get("address", ""))
            rating = str(r.get("rating", ""))
            line = f"[{i}] **{name}**"
            if address:
                line += f" — {address}"
            if rating:
                line += f" ({rating})"
            lines.append(line)

        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_get_chat_messages(ctx: HandlerContext, *, max_messages: int = 20) -> HandlerResult:
    """Fix D: Extrait tous les messages d'une conversation SPA de chat.

    Contrairement à browser_get_content qui tronque le contenu, ce handler
    extrait spécifiquement les messages de la conversation (questions + réponses)
    en utilisant les sélecteurs sémantiques des SPAs de chat connues.

    Fonctionne avec: Mistral Chat, Claude.ai, ChatGPT, Gemini, Duck.ai, HuggingChat.
    Retourne les messages dans l'ordre chronologique avec leur rôle (user/assistant).
    """
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        messages = await browser._page.evaluate(
            """
            ({ maxMessages }) => {
                const results = [];
                const seen = new Set();

                const pushMsg = (role, text) => {
                    const t = String(text || '').replace(/\\s+/g, ' ').trim();
                    if (!t || t.length < 5) return;
                    const key = role + ':' + t.slice(0, 80);
                    if (seen.has(key)) return;
                    seen.add(key);
                    results.push({ role, text: t });
                };

                // Mistral Chat / Le Chat
                const mistralMsgs = document.querySelectorAll('[class*="message"], [class*="Message"]');
                if (mistralMsgs.length > 0) {
                    for (const el of mistralMsgs) {
                        const isUser = el.className.toLowerCase().includes('user') ||
                                       el.getAttribute('data-role') === 'user';
                        const isAssistant = el.className.toLowerCase().includes('assistant') ||
                                            el.className.toLowerCase().includes('bot') ||
                                            el.getAttribute('data-role') === 'assistant';
                        if (isUser || isAssistant) {
                            pushMsg(isUser ? 'user' : 'assistant', el.innerText || el.textContent);
                        }
                    }
                }

                // Fallback générique: chercher les éléments avec data-role ou aria-label
                if (results.length === 0) {
                    const roleEls = document.querySelectorAll('[data-role], [data-message-author-role]');
                    for (const el of roleEls) {
                        const role = el.getAttribute('data-role') ||
                                     el.getAttribute('data-message-author-role') || 'unknown';
                        pushMsg(role, el.innerText || el.textContent);
                    }
                }

                // Fallback 2: extraire tout le texte visible structuré
                if (results.length === 0) {
                    const mainContent = document.querySelector(
                        '[role="main"], main, .conversation, .chat-messages, .messages'
                    );
                    if (mainContent) {
                        const paragraphs = mainContent.querySelectorAll('p, [class*="text"], [class*="content"]');
                        for (const p of paragraphs) {
                            const text = (p.innerText || p.textContent || '').trim();
                            if (text.length > 20) {
                                pushMsg('unknown', text);
                            }
                        }
                    }
                }

                return results.slice(-maxMessages);
            }
            """,
            {"maxMessages": max_messages},
        )

        if not messages:
            # Fallback: utiliser browser_get_content
            return HandlerResult.fail(
                "Aucun message de conversation détecté. "
                "Utilise browser_get_content pour lire le contenu brut de la page."
            )

        count = len(messages)
        lines = [f"💬 {count} message(s) dans la conversation:\n"]
        for i, msg in enumerate(messages, 1):
            role_icon = "👤" if msg.get("role") == "user" else "🤖"
            role_label = "Vous" if msg.get("role") == "user" else "IA"
            text = str(msg.get("text", ""))[:500]
            lines.append(f"{role_icon} [{i}] {role_label}: {text}")
            if len(text) == 500:
                lines.append("  [... tronqué à 500 chars]")

        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


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


async def browser_select_index(ctx: HandlerContext, *, index: int, label: str = "",
                               value: str = "", option_index: int = -1) -> HandlerResult:
    """Choisit une option dans le <select> DOM indexe par son numero [N].

    ── LOT Z19 — l'index qu'on affiche doit pouvoir servir partout ────────────

    Run « Pelage » (2026-08-17). `browser_dom_state` venait d'afficher :

        [9]  combobox "-- Choisir --Marie Curie"
        [11] combobox "Bain (25€) Tonte (40€) Soin complet (60€)"

    La mission a fait le geste naturel — `browser_select(selector='[9]',
    label='Marie Curie', by='index')` — et Playwright a leve :

        '[9]' is not a valid selector

    `browser_click_index` et `browser_type_index` existaient ; le TROISIEME
    outil de la famille, non. La mission a donc pilote les trois <select> en
    JavaScript (`browser_evaluate`) : 10 appels, budget navigateur a 36/32.

    Ce que ca coute vraiment : Z16 exige une interaction METIER reelle, et un
    <select> est dans presque tous les formulaires metier (client, forfait,
    etat). Sans cet outil, Z16 pousse la mission a ecrire son propre JS pour
    simuler l'interaction — exactement le trou que Z16 devait fermer.

    L'index etait calcule, il etait affiche au modele, et il etait jete au seul
    outil qui en avait besoin.
    """
    try:
        if int(index) < 1:
            return HandlerResult.fail(
                f"Index DOM invalide: [{index}] — les elements browser sont indexes a partir de 1"
            )

        if not str(label).strip() and not str(value).strip() and int(option_index) < 0:
            return HandlerResult.fail(
                f"Rien a selectionner dans [{index}] — fournis `label` (texte visible de "
                f"l'option), `value` (attribut value) ou `option_index` (0-based)."
            )

        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()
        snap, target = await _get_dom_snapshot_and_target(
            browser, indexer, index, ensure_visible=True
        )

        if target is None:
            return HandlerResult.fail(
                f"Element [{index}] introuvable. {len(snap.elements)} elements disponibles (1-{len(snap.elements)})"
            )

        stale_reason = _validate_index_against_last_dom_snapshot(browser, snap, target, index)
        if stale_reason:
            return HandlerResult.fail(stale_reason)

        # Un <select> est indexe en role `combobox` par les deux chemins du
        # DOM indexer. Tout autre role est une erreur d'aiguillage : on nomme
        # le role reel ET l'outil qui convient, sinon le modele reessaie a
        # l'aveugle (leçon des messages de `browser_type_index`).
        if target.role != "combobox":
            return HandlerResult.fail(
                f"Element [{index}] ({target.role} \"{target.name}\") n'est pas une liste "
                f"deroulante. Pour ce role, utilise "
                f"{'browser_type_index' if target.role in {'textbox', 'searchbox', 'spinbutton'} else 'browser_click_index'}."
            )

        center = target.center
        if center is None:
            return HandlerResult.fail(
                f"Element [{index}] ({target.role} \"{target.name}\") n'a pas de position connue"
            )

        page = browser._page
        cx, cy = center
        chosen = await page.evaluate(
            """
            ({x, y, label, value, optionIndex}) => {
                const at = document.elementFromPoint(x, y);
                const sel = at && (at.closest ? at.closest('select') : null);
                if (!sel) return {ok: false, error: 'aucun <select> a cette position'};
                const opts = Array.from(sel.options || []);
                if (!opts.length) return {ok: false, error: 'ce <select> n a aucune option'};

                const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                let found = -1;
                if (label) {
                    const want = norm(label);
                    found = opts.findIndex(o => norm(o.textContent) === want);
                    if (found < 0) found = opts.findIndex(o => norm(o.textContent).includes(want));
                } else if (value) {
                    const want = norm(value);
                    found = opts.findIndex(o => norm(o.value) === want);
                } else if (optionIndex >= 0 && optionIndex < opts.length) {
                    found = optionIndex;
                }
                if (found < 0) {
                    return {
                        ok: false,
                        error: 'option introuvable',
                        available: opts.map(o => String(o.textContent || '').trim()),
                    };
                }
                sel.selectedIndex = found;
                // input PUIS change : certaines pages n'ecoutent que l'un des deux.
                sel.dispatchEvent(new Event('input', {bubbles: true}));
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                return {
                    ok: true,
                    text: String(opts[found].textContent || '').trim(),
                    value: String(opts[found].value || ''),
                };
            }
            """,
            {"x": cx, "y": cy, "label": str(label or ""),
             "value": str(value or ""), "optionIndex": int(option_index)},
        )

        if not (chosen or {}).get("ok"):
            dispo = (chosen or {}).get("available") or []
            detail = ""
            if dispo:
                apercu = ", ".join(f'"{o}"' for o in dispo[:8])
                if len(dispo) > 8:
                    apercu += f", … (+{len(dispo) - 8})"
                detail = f" Options reellement presentes : [{apercu}]."
            return HandlerResult.fail(
                f"Echec de selection dans [{index}] \"{target.name}\" : "
                f"{(chosen or {}).get('error', 'inconnu')}.{detail}"
            )

        base = f"✅ Option \"{chosen.get('text')}\" selectionnee dans [{index}]"
        try:
            summary = _format_form_state_summary(await _read_browser_form_state(page))
            if summary:
                base = f"{base}\n\n{summary}"
        except Exception:
            pass
        return HandlerResult.ok(base)
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_keyboard_press(ctx: HandlerContext, *, key: str) -> HandlerResult:
    """Presse une touche clavier (Enter, Tab, Escape, ArrowDown, Control+a, etc.)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        page = getattr(browser, "_page", None)
        before_form_state = None
        if page and str(key).lower() in {"enter", "tab"}:
            before_form_state = await _read_browser_form_state(page)
        result = await browser.keyboard_press(key)
        if result["success"]:
            base = HandlerResult.ok(f"⌨️ Touche pressée: {key}")
            submit_strategy = str(result.get("submit_strategy", "") or "").strip()
            submit_button_label = str(result.get("submit_button_label", "") or "").strip()
            if submit_strategy:
                extra = f"\nSoumission assistée Playwright: {submit_strategy}"
                if submit_button_label:
                    extra += f" ({submit_button_label})"
                base = HandlerResult.ok(f"{base.output}{extra}")
            if page and str(key).lower() in {"enter", "tab"}:
                form_state = await _read_browser_form_state(page)
                summary = _format_form_state_summary(form_state)
                note = ""
                try:
                    prev_filled = int((before_form_state or {}).get("filled", 0))
                    prev_submit = int((before_form_state or {}).get("enabled_submit_buttons", 0))
                    cur_filled = int((form_state or {}).get("filled", 0))
                    cur_submit = int((form_state or {}).get("enabled_submit_buttons", 0))
                    if str(key).lower() == "enter" and cur_submit > 0:
                        if prev_filled > 0 and cur_filled == 0:
                            note = (
                                "\nObservation browser: Enter a vide le champ, "
                                "mais un bouton d'envoi reste actif — la soumission n'est probablement pas partie."
                            )
                        elif cur_submit >= prev_submit:
                            note = (
                                "\nObservation browser: Enter n'a pas finalise l'envoi — "
                                "un bouton d'envoi reste disponible."
                            )
                except Exception:
                    note = ""
                if summary:
                    base = HandlerResult.ok(f"{base.output}\n\n{summary}{note}")
                return await _auto_visual_enrich(ctx, base, action_label="keyboard_press")
            return base
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


# ═══════════════════════════════════════════════════════════════════════════════
# ─── Phase 4 : Dialogs, Drag&Drop, Downloads, Frames, Metrics, Smart Click ───
# ═══════════════════════════════════════════════════════════════════════════════

async def browser_screenshot_labels(
    ctx: HandlerContext, *, max_labels: int = 80
) -> HandlerResult:
    """Prend un screenshot avec labels alignés sur le même snapshot que browser_dom_state."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        from ...computer_use.dom_indexer import get_dom_indexer, render_set_of_mark
        from ...utils.paths import SCREENSHOTS_DIR
        from PIL import Image
        from datetime import datetime
        import io

        browser = get_playwright_browser()
        if not browser.is_running or not browser._page:
            return HandlerResult.fail("Navigateur non demarre ou aucune page active")

        indexer = get_dom_indexer()
        snap = await indexer.snapshot(browser._page)
        snap = await indexer.enrich_with_bboxes(browser._page, snap)
        elements = list((snap.elements or [])[: max(1, int(max_labels or 80))])
        try:
            browser._last_dom_snapshot_meta = _build_dom_snapshot_meta(snap)
        except Exception:
            pass

        raw = await browser._page.screenshot(type="png")
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        som = render_set_of_mark(img, elements)

        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOTS_DIR / f"screenshot_labels_{ts}.png"
        som.save(str(path))

        lines = [
            f"📸 Screenshot avec {len(elements)} labels: {path}",
            "",
        ]
        for elem in elements[:30]:
            label_text = elem.name or elem.description or elem.value or ""
            lines.append(f"  [{elem.index}] <{elem.tag or elem.role}> {label_text}")
        if len(elements) > 30:
            lines.append(f"  ... et {len(elements) - 30} de plus")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_handle_dialog(ctx: HandlerContext, *, policy: str = "auto_accept",
                                  prompt_text: str = "") -> HandlerResult:
    """Configure la gestion automatique des dialogs natifs (alert/confirm/prompt)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            await browser.start()
        result = await browser.set_dialog_policy(policy=policy, prompt_text=prompt_text)
        if result.get("success"):
            return HandlerResult.ok(f"💬 Policy dialog = '{policy}' (prompt='{prompt_text}')")
        return HandlerResult.fail(result.get("error", "échec"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_dialog_log(ctx: HandlerContext, *, limit: int = 20) -> HandlerResult:
    """Affiche l'historique des dialogs interceptés."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = browser.get_dialog_log(limit=limit)
        if not result.get("dialogs"):
            return HandlerResult.ok("Aucun dialog intercepté. Policy actuelle: " + result.get("policy", "?"))
        lines = [f"💬 {result['count']} dialog(s) interceptés (policy={result['policy']}):", ""]
        for d in result["dialogs"]:
            lines.append(f"  [{d['type']}] {d['message'][:80]} → {d['action']}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_drag(ctx: HandlerContext, *, source_selector: str, target_selector: str,
                        by: str = "css") -> HandlerResult:
    """Drag & drop d'un élément source vers une cible."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.drag(source_selector, target_selector, by=by)
        if result.get("success"):
            return HandlerResult.ok(
                f"🖐️  Drag '{source_selector}' → '{target_selector}' "
                f"(méthode={result.get('method')}, distance={result.get('distance_px', '?')}px)"
            )
        return HandlerResult.fail(result.get("error", "échec drag"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_drag_at(ctx: HandlerContext, *, from_x: int, from_y: int,
                            to_x: int, to_y: int) -> HandlerResult:
    """Drag souris par coordonnées."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.drag_at(from_x, from_y, to_x, to_y)
        if result.get("success"):
            return HandlerResult.ok(
                f"🖐️  Drag ({from_x},{from_y})→({to_x},{to_y}) "
                f"distance={result.get('distance_px')}px"
            )
        return HandlerResult.fail(result.get("error", "échec drag_at"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_wait_for_download(ctx: HandlerContext, *, timeout_ms: int = 30000) -> HandlerResult:
    """Attend le prochain téléchargement et le sauve dans data/browser_downloads/."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.wait_for_download(timeout_ms=timeout_ms)
        if result.get("success"):
            return HandlerResult.ok(
                f"⬇️  Download: {result['filename']} ({result['size']} bytes) → {result['path']}"
            )
        return HandlerResult.fail(result.get("error", "échec download"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_list_downloads(ctx: HandlerContext, *, limit: int = 20) -> HandlerResult:
    """Liste les téléchargements de la session."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        result = browser.list_downloads(limit=limit)
        if not result.get("downloads"):
            return HandlerResult.ok(f"Aucun download. Dossier: {result['downloads_dir']}")
        lines = [f"⬇️  {result['count']} download(s) — dossier: {result['downloads_dir']}", ""]
        for d in result["downloads"]:
            lines.append(f"  [{d.get('state', '?')}] {d.get('filename', '?')} "
                         f"({d.get('size', 0)}B) ← {d.get('url', '')[:60]}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_verify(
    ctx: HandlerContext,
    *,
    before_url: Optional[str] = None,
    expect_text: Optional[str] = None,
    forbid_text: Optional[List[str]] = None,
) -> HandlerResult:
    """W3 — Confirme qu'une action (formulaire, login, inscription…) a vraiment
    abouti : URL changée, texte de succès présent, ABSENCE d'erreur. Une erreur
    détectée (« identifiants incorrects », « champ obligatoire »…) prime."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        res = await browser.verify_submission(
            before_url=before_url,
            expect_text=expect_text,
            forbid_text=forbid_text,
        )
        icon = "✅" if res.get("confirmed") else "❌"
        lines = [
            f"{icon} Action {'CONFIRMÉE' if res.get('confirmed') else 'NON confirmée'} — {res.get('reason', '')}",
            f"   url: {res.get('url', '')}",
        ]
        for s in res.get("signals", []):
            lines.append(f"   • {s}")
        # confirmed=False est une info utile, pas une erreur d'exécution → on renvoie ok()
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_save_login(
    ctx: HandlerContext, *, service: str, username: str, password: str,
    login_url: Optional[str] = None,
) -> HandlerResult:
    """W1 — Enregistre des identifiants dans le coffre chiffré (jamais en clair)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        res = browser.save_login(service, username, password, login_url=login_url)
        if not res.get("success"):
            return HandlerResult.fail(res.get("error", "échec enregistrement"))
        return HandlerResult.ok(
            f"🔐 Identifiants enregistrés pour {res['domain']} (user: {res['username']}, mot de passe: ***)"
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_list_logins(ctx: HandlerContext) -> HandlerResult:
    """W1 — Liste les sites pour lesquels un identifiant est enregistré (valeurs jamais exposées)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        res = browser.list_logins()
        if not res.get("domains"):
            return HandlerResult.ok("Aucun identifiant enregistré.")
        return HandlerResult.ok(
            "🔐 Identifiants enregistrés pour : " + ", ".join(res["domains"])
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_login(
    ctx: HandlerContext, *, service: str, login_url: Optional[str] = None,
) -> HandlerResult:
    """W1 — Connecte Lumena à un site avec les identifiants du coffre, puis
    CONFIRME la réussite (détecte un échec de login au lieu de continuer à l'aveugle)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré (browser_start d'abord)")
        res = await browser.login(service, login_url=login_url)
        if not res.get("success"):
            return HandlerResult.fail(
                f"❌ Connexion à {res.get('domain', service)} NON confirmée — "
                f"{res.get('error') or res.get('reason', 'échec')}"
            )
        return HandlerResult.ok(
            f"✅ Connectée à {res['domain']} (user: {res['username']}) — confirmé, url: {res.get('url', '')}"
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_find(
    ctx: HandlerContext, *, query: str, max_pages: int = 8,
    must_include: Optional[List[str]] = None,
) -> HandlerResult:
    """W4 — Cherche une info PRÉCISE sur le web (l'aiguille dans la botte de foin) :
    balaie les résultats, extrait le passage qui contient la réponse, classe par
    pertinence, et s'arrête dès qu'il trouve. Renvoie le meilleur passage + sources."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        res = await browser.find_needle(query, max_pages=max_pages, must_include=must_include)
        if not res.get("success"):
            return HandlerResult.fail(
                f"🔍 Rien trouvé pour « {query} » ({res.get('pages_scanned', 0)} pages balayées) — "
                f"{res.get('error', '')}"
            )
        best = res["best_answer"]
        lines = [
            f"🎯 Meilleure réponse pour « {query} » ({res['pages_scanned']} pages balayées) :",
            "",
            f"« {best['passage']} »",
            f"   └─ source : {best['title'] or best['url']}",
            f"      {best['url']}  (score {best['score']}, termes: {', '.join(best['matched'])})",
        ]
        others = res["findings"][1:4]
        if others:
            lines.append("")
            lines.append("Autres sources pertinentes :")
            for f in others:
                lines.append(f"  • [{f['score']}] {f['title'] or f['url']} — {f['url']}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_check_challenge(ctx: HandlerContext) -> HandlerResult:
    """W2 — Détecte si un captcha ou une étape 2FA/OTP bloque la page. Si oui,
    demande à l'utilisateur (ask_user) puis applique avec browser_solve_challenge.
    Le navigateur reste ouvert sur la même page pendant l'attente."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        res = await browser.detect_challenge()
        kind = res.get("kind")
        if kind == "none":
            return HandlerResult.ok("✅ Aucun captcha ni 2FA détecté — tu peux continuer.")
        if kind == "captcha":
            return HandlerResult.ok(
                f"🧑‍🤝‍🧑 RELAIS HUMAIN — captcha {res.get('provider')} détecté.\n"
                f"➡️ Utilise `ask_user` pour demander à l'utilisateur de résoudre le "
                f"captcha dans la fenêtre du navigateur (elle reste ouverte), puis "
                f"appelle `browser_solve_challenge` avec done=true. Ne ferme PAS le navigateur."
            )
        return HandlerResult.ok(
            "🧑‍🤝‍🧑 RELAIS HUMAIN — vérification 2FA / code à usage unique demandée.\n"
            "➡️ Utilise `ask_user` pour demander le code (SMS / e-mail / app), puis "
            "appelle `browser_solve_challenge` avec code=\"<le code>\". Ne ferme PAS le navigateur."
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_solve_challenge(
    ctx: HandlerContext, *, code: Optional[str] = None, done: bool = False,
) -> HandlerResult:
    """W2 — Applique l'aide humaine à un captcha/2FA : `code` = code 2FA/OTP à
    saisir ; `done=true` = l'humain a résolu le captcha dans la fenêtre."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        res = await browser.solve_challenge(code=code, done=done)
        if res.get("kind") == "captcha" and "cleared" in res:
            return (HandlerResult.ok(f"✅ Captcha franchi — url: {res.get('url', '')}")
                    if res.get("cleared")
                    else HandlerResult.fail("❌ Le captcha est toujours présent. "
                                            "Demande à l'utilisateur de le terminer dans la fenêtre."))
        if res.get("kind") == "otp":
            return (HandlerResult.ok(f"✅ Code accepté — connexion/vérification confirmée (url: {res.get('url', '')})")
                    if res.get("success")
                    else HandlerResult.fail(f"❌ Code refusé — {res.get('reason', 'vérification non confirmée')}"))
        # ni code ni done → détection renvoyée
        return HandlerResult.ok(res.get("needs", "Aucun challenge détecté."))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_frames(ctx: HandlerContext) -> HandlerResult:
    """Liste toutes les frames/iframes de la page active."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.list_frames()
        if not result.get("success"):
            return HandlerResult.fail(result.get("error", "échec"))
        lines = [f"🖼️  {result['count']} frame(s):", ""]
        for f in result["frames"]:
            tag = "MAIN" if f["is_main"] else (f"#{f['index']}")
            name = f" name='{f['name']}'" if f["name"] else ""
            lines.append(f"  [{tag}]{name} url={f['url']}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_frame_click(ctx: HandlerContext, *, frame: str, selector: str,
                                by: str = "css") -> HandlerResult:
    """Clique un élément dans une frame (référence par nom, '#index', ou URL partielle)."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.frame_click(frame, selector, by=by)
        if result.get("success"):
            return HandlerResult.ok(f"🖼️  Clic dans frame '{frame}' sur '{selector}'")
        return HandlerResult.fail(result.get("error", "échec frame_click"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_frame_type(ctx: HandlerContext, *, frame: str, selector: str,
                               text: str, by: str = "css") -> HandlerResult:
    """Tape du texte dans un champ d'une frame."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.frame_type(frame, selector, text, by=by)
        if result.get("success"):
            return HandlerResult.ok(f"🖼️  Typed {result['chars']} chars dans frame '{frame}'")
        return HandlerResult.fail(result.get("error", "échec frame_type"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_frame_content(ctx: HandlerContext, *, frame: str,
                                  max_chars: int = 5000) -> HandlerResult:
    """Récupère le contenu textuel d'une frame."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.frame_content(frame, max_chars=max_chars)
        if result.get("success"):
            trunc = " [tronqué]" if result.get("truncated") else ""
            return HandlerResult.ok(
                f"🖼️  Frame '{frame}' ({result['url']}){trunc}:\n\n{result['content']}"
            )
        return HandlerResult.fail(result.get("error", "échec frame_content"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_frame_evaluate(ctx: HandlerContext, *, frame: str, script: str) -> HandlerResult:
    """Exécute du JavaScript dans une frame."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.frame_evaluate(frame, script)
        if result.get("success"):
            return HandlerResult.ok(f"🖼️  Frame '{frame}' result: {result.get('result')!r}")
        return HandlerResult.fail(result.get("error", "échec frame_evaluate"))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_metrics(ctx: HandlerContext) -> HandlerResult:
    """Retourne les métriques de performance (Core Web Vitals) de la page active."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.get_metrics()
        if not result.get("success"):
            return HandlerResult.fail(result.get("error", "échec metrics"))
        m = result["metrics"]
        lines = [
            f"📊 Métriques — {result['url']}",
            "",
            f"  TTFB:                {m.get('ttfb_ms', '?')} ms",
            f"  Response time:       {m.get('response_time_ms', '?')} ms",
            f"  First Paint (FP):    {m.get('first_paint_ms', '?')} ms",
            f"  First Contentful (FCP): {m.get('first_contentful_paint_ms', '?')} ms",
            f"  Largest Contentful (LCP): {m.get('largest_contentful_paint_ms', '?')} ms",
            f"  DOM Content Loaded:  {m.get('dom_content_loaded_ms', '?')} ms",
            f"  Load Complete:       {m.get('load_complete_ms', '?')} ms",
            "",
            f"  DOM nodes:           {m.get('dom_nodes', '?')}",
            f"  Resources loaded:    {m.get('resources_count', '?')}",
            f"  Transfer size:       {m.get('transfer_size_kb', '?')} KB",
            f"  JS heap:             {m.get('js_heap_mb', '?')} MB",
        ]
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def browser_click_smart(ctx: HandlerContext, *, hint: str, selector: str = "",
                                by: str = "css") -> HandlerResult:
    """Clic intelligent self-healing : essaye sélecteur → role+name → texte → fuzzy DOM scan."""
    try:
        from ...tools.playwright_browser import get_playwright_browser
        browser = get_playwright_browser()
        if not browser.is_running:
            return HandlerResult.fail("Navigateur non démarré")
        result = await browser.click_smart(hint=hint, selector=selector, by=by)
        if result.get("success"):
            base = HandlerResult.ok(
                f"✨ click_smart réussi (stratégie={result['strategy']}) "
                f"sur '{hint or selector}'"
            )
            return await _auto_visual_enrich(ctx, base, action_label="click_smart")
        tried = ", ".join(result.get("tried", []))
        return HandlerResult.fail(f"{result.get('error', 'échec')} — tried: {tried}")
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
            description="Clique sur un element de la page (par CSS selector, XPath, ID, ou par texte via 'text').",
            parameters={
                "properties": {
                    "selector": {"type": "string", "description": "Selecteur de l'element (ou utiliser 'text')"},
                    "by": {"type": "string", "description": "Type: css, xpath, id, class, name, text, partial_text", "default": "css"},
                    "text": {"type": "string", "description": "Texte du bouton/lien a cliquer (alternative a selector)"},
                },
                "required": [],
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
                    "direction": {"type": "string", "description": "up, down, top, bottom", "default": "down"},
                    "amount": {"type": "integer", "description": "Pixels a scroller", "default": 500},
                },
                "required": [],
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
                    "index": {"type": "integer", "description": "Index de l'onglet (0-based)", "default": 0},
                },
                "required": [],
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
            name="browser_dismiss_popups",
            description="Ferme cookies/newsletters/modals (Escape + patterns connus). À utiliser si popup bloque l'interaction.",
            parameters={"properties": {}, "required": []},
            handler=browser_dismiss_popups,
            category="browser",
            source_module="handlers.browser",
        ),
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
            name="browser_select_index",
            description=(
                "Choisit une option dans la liste deroulante DOM indexee [N] (vue par "
                "browser_dom_state) — le pendant de browser_click_index pour les <select>"
            ),
            parameters={
                "properties": {
                    "index": {"type": "integer", "description": "Index DOM [N] du <select>"},
                    "label": {"type": "string", "description": "Texte visible de l'option"},
                    "value": {"type": "string", "description": "Attribut value de l'option"},
                    "option_index": {"type": "integer", "description": "Rang 0-based de l'option"},
                },
                "required": ["index"],
            },
            handler=browser_select_index,
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
        # ── Phase 4 — Dialogs, Drag & Drop, Downloads, Frames, Metrics, Smart ──
        HandlerDef(
            name="browser_handle_dialog",
            description=(
                "Configure la gestion auto des dialogs natifs (alert/confirm/prompt). "
                "Policy: 'auto_accept' (défaut, accepte tous), 'auto_dismiss' (refuse), "
                "'manual'. prompt_text = texte par défaut pour les prompt()."
            ),
            parameters={
                "properties": {
                    "policy": {"type": "string", "description": "auto_accept|auto_dismiss|manual", "default": "auto_accept"},
                    "prompt_text": {"type": "string", "description": "Texte de réponse pour les prompt()", "default": ""},
                },
                "required": [],
            },
            handler=browser_handle_dialog,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_dialog_log",
            description="Historique des dialogs natifs interceptés (alert/confirm/prompt).",
            parameters={
                "properties": {
                    "limit": {"type": "integer", "description": "Nombre max", "default": 20},
                },
                "required": [],
            },
            handler=browser_dialog_log,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_drag",
            description=(
                "Drag & drop d'un élément source vers une cible (CSS/XPath/text). "
                "Essaye d'abord l'API native Playwright, puis fallback mouse manuel 10-steps."
            ),
            parameters={
                "properties": {
                    "source_selector": {"type": "string", "description": "Sélecteur source"},
                    "target_selector": {"type": "string", "description": "Sélecteur cible"},
                    "by": {"type": "string", "description": "css|xpath|text", "default": "css"},
                },
                "required": ["source_selector", "target_selector"],
            },
            handler=browser_drag,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_drag_at",
            description="Drag souris par coordonnées (x,y) → (x,y).",
            parameters={
                "properties": {
                    "from_x": {"type": "integer", "description": "Coordonnée X source"},
                    "from_y": {"type": "integer", "description": "Coordonnée Y source"},
                    "to_x": {"type": "integer", "description": "Coordonnée X cible"},
                    "to_y": {"type": "integer", "description": "Coordonnée Y cible"},
                },
                "required": ["from_x", "from_y", "to_x", "to_y"],
            },
            handler=browser_drag_at,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_wait_for_download",
            description=(
                "Attend le prochain téléchargement et le sauve dans data/browser_downloads/. "
                "Utile après un clic sur bouton 'Télécharger'."
            ),
            parameters={
                "properties": {
                    "timeout_ms": {"type": "integer", "description": "Timeout en ms", "default": 30000},
                },
                "required": [],
            },
            handler=browser_wait_for_download,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_list_downloads",
            description="Liste les fichiers téléchargés pendant la session (data/browser_downloads/).",
            parameters={
                "properties": {
                    "limit": {"type": "integer", "description": "Nombre max de résultats", "default": 20},
                },
                "required": [],
            },
            handler=browser_list_downloads,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_verify",
            description=(
                "Vérifie qu'une action vient de RÉUSSIR (soumission de formulaire, "
                "connexion, inscription…) : URL changée, texte de succès présent, "
                "et ABSENCE de message d'erreur. À appeler après un submit/clic "
                "décisif. Passe before_url (l'URL d'avant l'action) et/ou expect_text "
                "(un texte attendu en cas de succès)."
            ),
            parameters={
                "properties": {
                    "before_url": {"type": "string", "description": "URL avant l'action (pour détecter une navigation)"},
                    "expect_text": {"type": "string", "description": "Texte attendu si l'action a réussi"},
                    "forbid_text": {"type": "array", "items": {"type": "string"}, "description": "Textes d'erreur supplémentaires à surveiller"},
                },
                "required": [],
            },
            handler=browser_verify,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_save_login",
            description=(
                "Enregistre des identifiants (login/mot de passe) dans le coffre "
                "chiffré pour un site, afin que Lumena puisse s'y connecter seule "
                "ensuite. Le mot de passe n'est jamais stocké ni affiché en clair."
            ),
            parameters={
                "properties": {
                    "service": {"type": "string", "description": "Domaine ou URL du site (ex: github.com)"},
                    "username": {"type": "string", "description": "Identifiant / email"},
                    "password": {"type": "string", "description": "Mot de passe (chiffré au stockage)"},
                    "login_url": {"type": "string", "description": "URL de la page de connexion (optionnel)"},
                },
                "required": ["service", "username", "password"],
            },
            handler=browser_save_login,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_list_logins",
            description="Liste les sites pour lesquels un identifiant est enregistré (valeurs jamais exposées).",
            parameters={"properties": {}, "required": []},
            handler=browser_list_logins,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_login",
            description=(
                "Connecte Lumena à un site avec les identifiants du coffre "
                "(enregistrés via browser_save_login), puis CONFIRME la réussite. "
                "Détecte un échec de connexion (mauvais identifiants) au lieu de "
                "continuer à l'aveugle."
            ),
            parameters={
                "properties": {
                    "service": {"type": "string", "description": "Domaine ou URL du site (ex: github.com)"},
                    "login_url": {"type": "string", "description": "URL de la page de connexion (optionnel si déjà enregistrée)"},
                },
                "required": ["service"],
            },
            handler=browser_login,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_find",
            description=(
                "Cherche une information PRÉCISE sur le web (l'aiguille dans la "
                "botte de foin) : balaie plusieurs résultats, extrait le passage "
                "exact qui répond, classe par pertinence et s'arrête dès qu'il "
                "trouve. Idéal quand un simple web_search ne suffit pas. "
                "must_include = termes obligatoires dans la réponse."
            ),
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "La question / l'information recherchée"},
                    "max_pages": {"type": "integer", "description": "Nombre max de pages à balayer", "default": 8},
                    "must_include": {"type": "array", "items": {"type": "string"}, "description": "Termes qui DOIVENT figurer dans la réponse"},
                },
                "required": ["query"],
            },
            handler=browser_find,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_check_challenge",
            description=(
                "Détecte si un captcha (reCAPTCHA/hCaptcha/Cloudflare) ou une "
                "étape 2FA/OTP bloque la page. À appeler quand une action semble "
                "bloquée. Si un blocage est trouvé, demande l'aide humaine via "
                "ask_user (le navigateur reste ouvert) puis browser_solve_challenge."
            ),
            parameters={"properties": {}, "required": []},
            handler=browser_check_challenge,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_solve_challenge",
            description=(
                "Applique l'aide humaine à un captcha/2FA. Passe code=\"123456\" "
                "pour saisir un code 2FA/OTP fourni par l'utilisateur, ou done=true "
                "si l'utilisateur a résolu le captcha lui-même dans la fenêtre."
            ),
            parameters={
                "properties": {
                    "code": {"type": "string", "description": "Code 2FA/OTP fourni par l'utilisateur"},
                    "done": {"type": "boolean", "description": "True si l'humain a résolu le captcha dans la fenêtre"},
                },
                "required": [],
            },
            handler=browser_solve_challenge,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_frames",
            description="Liste toutes les frames/iframes de la page (main + sous-frames) avec leur URL.",
            parameters={"properties": {}, "required": []},
            handler=browser_frames,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_frame_click",
            description=(
                "Clique un élément DANS une iframe. "
                "frame = nom, '#<index>', ou sous-chaîne d'URL ('' ou 'main' pour la frame principale)."
            ),
            parameters={
                "properties": {
                    "frame": {"type": "string", "description": "Référence de frame"},
                    "selector": {"type": "string", "description": "Sélecteur de l'élément"},
                    "by": {"type": "string", "description": "Type: css, xpath, text", "default": "css"},
                },
                "required": ["frame", "selector"],
            },
            handler=browser_frame_click,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_frame_type",
            description="Tape du texte dans un champ à l'intérieur d'une iframe.",
            parameters={
                "properties": {
                    "frame": {"type": "string", "description": "Référence de frame (nom, #index, ou URL partielle)"},
                    "selector": {"type": "string", "description": "Sélecteur du champ"},
                    "text": {"type": "string", "description": "Texte à taper"},
                    "by": {"type": "string", "description": "Type: css, xpath, text", "default": "css"},
                },
                "required": ["frame", "selector", "text"],
            },
            handler=browser_frame_type,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_frame_content",
            description="Récupère le texte d'une iframe (frame = nom, '#index', ou URL partielle).",
            parameters={
                "properties": {
                    "frame": {"type": "string", "description": "Référence de frame (nom, #index, ou URL partielle)"},
                    "max_chars": {"type": "integer", "description": "Nombre max de caractères retournés", "default": 5000},
                },
                "required": ["frame"],
            },
            handler=browser_frame_content,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_frame_evaluate",
            description="Exécute du JavaScript dans une iframe spécifique.",
            parameters={
                "properties": {
                    "frame": {"type": "string", "description": "Référence de frame (nom, #index, ou URL partielle)"},
                    "script": {"type": "string", "description": "Expression JS à évaluer"},
                },
                "required": ["frame", "script"],
            },
            handler=browser_frame_evaluate,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_metrics",
            description=(
                "Métriques de performance de la page (Core Web Vitals): TTFB, FP, FCP, LCP, "
                "DOMContentLoaded, Load Complete, DOM nodes, transfer size, JS heap."
            ),
            parameters={"properties": {}, "required": []},
            handler=browser_metrics,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_click_smart",
            description=(
                "Clic intelligent self-healing : essaye plusieurs stratégies en cascade — "
                "(1) sélecteur exact, (2) role+name accessible (get_by_role), "
                "(3) texte visible (get_by_text), (4) fuzzy DOM scan pondéré sur le hint. "
                "Idéal quand le sélecteur exact peut casser après une màj UI."
            ),
            parameters={
                "properties": {
                    "hint": {"type": "string", "description": "Description textuelle de l'élément (ex: 'bouton connexion')"},
                    "selector": {"type": "string", "description": "Sélecteur exact (optionnel, tenté en premier)", "default": ""},
                    "by": {"type": "string", "description": "Type de sélecteur: css, xpath, text", "default": "css"},
                },
                "required": ["hint"],
            },
            handler=browser_click_smart,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_get_chat_messages",
            description=(
                "Fix D: Extrait tous les messages d'une conversation SPA de chat (Mistral, Claude, ChatGPT, etc.). "
                "Contrairement à browser_get_content qui tronque, ce handler retourne TOUS les messages "
                "de la conversation dans l'ordre chronologique avec leur rôle (user/assistant). "
                "À utiliser après avoir envoyé un message pour lire la réponse complète de l'IA. "
                "Fonctionne avec: chat.mistral.ai, claude.ai, chatgpt.com, gemini.google.com, duck.ai, huggingface.co/chat."
            ),
            parameters={
                "properties": {
                    "max_messages": {
                        "type": "integer",
                        "description": "Nombre max de messages à retourner (défaut: 20)",
                        "default": 20,
                    },
                },
                "required": [],
            },
            handler=browser_get_chat_messages,
            category="browser",
            source_module="handlers.browser",
        ),
        HandlerDef(
            name="browser_search_maps",
            description=(
                "Fix G: Recherche des lieux sur Google Maps et retourne une liste structurée. "
                "Utilise directement l'URL Google Maps + extraction JS du panneau latéral. "
                "Beaucoup plus fiable que browser_navigate + browser_dom_state pour trouver des commerces. "
                "Exemples: query='boulangerie', location='Bois-Colombes 92270' → liste des boulangeries proches. "
                "Fonctionne avec: pharmacie, restaurant, karting, hôtel, etc."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Ce qu'on cherche (ex: 'boulangerie', 'karting', 'pharmacie de garde')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Ville ou adresse (ex: 'Bois-Colombes 92270', 'Paris 75001'). Optionnel si la ville est déjà connue.",
                        "default": "",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 8)",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
            handler=browser_search_maps,
            category="browser",
            source_module="handlers.browser",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
