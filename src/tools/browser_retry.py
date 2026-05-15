"""
browser_retry.py — Action Contract Engine pour Playwright.

Fournit un mécanisme de retry déterministe (délais fixes, pas aléatoires)
pour les actions browser. Chaque action peut avoir des pré-conditions et
des stratégies de recovery ordonnées.

Usage:
    from src.tools.browser_retry import BrowserRetryPolicy, is_retryable_error

    policy = BrowserRetryPolicy()
    result = await policy.execute(my_action, browser=browser)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, Tuple
from loguru import logger


# ─── Erreurs retryables ────────────────────────────────────────────────────────

# Sous-chaînes d'erreur qui indiquent un état transitoire récupérable
_RETRYABLE_ERROR_PATTERNS: tuple[str, ...] = (
    "element not found",
    "element is not visible",
    "element is not attached",
    "detached",
    "timeout",
    "not visible",
    "intercepted",
    "stale",
    "target closed",
    "execution context was destroyed",
    "navigating",
    "frame was detached",
    "element is outside of the viewport",
    "element is not stable",
    "waiting for",
)

# Erreurs NON retryables (échec définitif)
_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "net::err_name_not_resolved",
    "net::err_connection_refused",
    "net::err_connection_timed_out",
    "ssl",
    "certificate",
    "blocked by client",
    "navigation blocked",
)


def is_retryable_error(error: Exception) -> bool:
    """Retourne True si l'erreur est transitoire et peut être retentée."""
    msg = str(error).lower()
    if any(p in msg for p in _FATAL_ERROR_PATTERNS):
        return False
    return any(p in msg for p in _RETRYABLE_ERROR_PATTERNS)


# ─── Stratégies de retry ───────────────────────────────────────────────────────

async def _strategy_same(browser: Any, **kwargs) -> None:
    """Retry identique — aucune action préalable."""
    pass


async def _strategy_wait_dom(browser: Any, **kwargs) -> None:
    """Attendre que le DOM soit stable avant de réessayer."""
    page = getattr(browser, "_page", None)
    if page:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        try:
            await asyncio.sleep(0.1)
        except Exception:
            pass


async def _strategy_dismiss_popups(browser: Any, **kwargs) -> None:
    """Tenter de fermer les popups/overlays avant de réessayer."""
    page = getattr(browser, "_page", None)
    if not page:
        return
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.1)
    except Exception:
        pass
    try:
        await page.evaluate("""
            () => {
                const sels = [
                    '[aria-label*="close" i]', '[aria-label*="fermer" i]',
                    'button.close', '[class*="modal"] button[class*="close" i]',
                    '[data-testid*="close" i]',
                ];
                for (const sel of sels) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) { el.click(); return true; }
                }
                return false;
            }
        """)
    except Exception:
        pass


async def _strategy_scroll_to_element(browser: Any, *, index: Optional[int] = None, **kwargs) -> None:
    """Scroller vers l'élément cible avant de réessayer."""
    page = getattr(browser, "_page", None)
    if not page or index is None:
        return
    try:
        from src.computer_use.dom_indexer import get_dom_indexer
        indexer = get_dom_indexer()
        snap = await indexer.snapshot(page)
        snap = await indexer.enrich_with_bboxes(page, snap)
        for elem in snap.elements:
            if elem.index == int(index):
                await page.evaluate(
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
                    {"role": elem.role, "name": elem.name},
                )
                await asyncio.sleep(0.15)
                break
    except Exception as e:
        logger.debug(f"[browser_retry] scroll_to_element: {e}")


# Stratégies ordonnées par tentative
_RETRY_STRATEGIES: tuple[Callable, ...] = (
    _strategy_same,           # Tentative 1 : retry immédiat
    _strategy_wait_dom,       # Tentative 2 : attendre DOM stable
    _strategy_dismiss_popups, # Tentative 3 : fermer popups
)

# Délais FIXES entre tentatives (ms) — déterministe, pas de random
_RETRY_DELAYS_MS: tuple[int, ...] = (200, 500, 1000)


# ─── BrowserRetryPolicy ────────────────────────────────────────────────────────

@dataclass
class BrowserRetryPolicy:
    """
    Politique de retry déterministe pour les actions browser.

    Délais fixes (pas aléatoires) = comportement prévisible et reproductible.
    Stratégies ordonnées = recovery progressif.
    """

    max_retries: int = 3
    delays_ms: tuple[int, ...] = field(default_factory=lambda: _RETRY_DELAYS_MS)
    strategies: tuple[Callable, ...] = field(default_factory=lambda: _RETRY_STRATEGIES)

    async def execute(
        self,
        action: Callable[..., Coroutine[Any, Any, Any]],
        *,
        browser: Any = None,
        action_label: str = "",
        index: Optional[int] = None,
        **action_kwargs: Any,
    ) -> Any:
        """
        Exécute une action avec retry déterministe.

        Args:
            action: Coroutine à exécuter.
            browser: Instance PlaywrightBrowser (pour les stratégies de recovery).
            action_label: Nom de l'action pour les logs.
            index: Index DOM cible (pour scroll_to_element).
            **action_kwargs: Arguments passés à l'action.

        Returns:
            Résultat de l'action si succès.

        Raises:
            Exception: Si toutes les tentatives échouent.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await action(**action_kwargs)
                if attempt > 0:
                    logger.debug(
                        f"[browser_retry] '{action_label}' réussi à la tentative {attempt + 1}"
                    )
                return result

            except Exception as e:
                last_error = e

                if not is_retryable_error(e):
                    logger.debug(f"[browser_retry] Erreur fatale '{action_label}': {e}")
                    raise

                if attempt >= self.max_retries:
                    break

                # Appliquer la stratégie de recovery pour cette tentative
                strategy_idx = min(attempt, len(self.strategies) - 1)
                strategy = self.strategies[strategy_idx]
                delay_ms = self.delays_ms[min(attempt, len(self.delays_ms) - 1)]

                logger.debug(
                    f"[browser_retry] '{action_label}' tentative {attempt + 1}/{self.max_retries} "
                    f"échouée ({e}), stratégie={strategy.__name__}, délai={delay_ms}ms"
                )

                # Appliquer la stratégie
                try:
                    await strategy(browser, index=index)
                except Exception as se:
                    logger.debug(f"[browser_retry] Stratégie {strategy.__name__} échouée: {se}")

                # Délai fixe (déterministe)
                await asyncio.sleep(delay_ms / 1000.0)

        raise last_error or RuntimeError(f"Action '{action_label}' échouée après {self.max_retries} tentatives")


# ─── Instance globale (singleton) ─────────────────────────────────────────────

_default_policy = BrowserRetryPolicy()


def get_browser_retry_policy() -> BrowserRetryPolicy:
    """Retourne la politique de retry globale."""
    return _default_policy


async def retry_browser_action(
    action: Callable[..., Coroutine[Any, Any, Any]],
    *,
    browser: Any = None,
    action_label: str = "",
    index: Optional[int] = None,
    max_retries: int = 3,
    **action_kwargs: Any,
) -> Any:
    """
    Raccourci pour exécuter une action browser avec retry.

    Args:
        action: Coroutine à exécuter.
        browser: Instance PlaywrightBrowser.
        action_label: Nom de l'action pour les logs.
        index: Index DOM cible (pour scroll_to_element).
        max_retries: Nombre max de tentatives (défaut: 3).
        **action_kwargs: Arguments passés à l'action.

    Returns:
        Résultat de l'action si succès.
    """
    policy = BrowserRetryPolicy(max_retries=max_retries)
    return await policy.execute(
        action,
        browser=browser,
        action_label=action_label,
        index=index,
        **action_kwargs,
    )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
