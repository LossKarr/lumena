"""LOT BR-1 — navigateur borné + auto-relance : le ReAct ne gèle plus jamais.

Contexte (run réel 2026-07-10 21:54) : `browser_navigate` appelé sur un singleton
Playwright zombie → relance non bornée → l'appel n'est JAMAIS revenu → tour ReAct
gelé définitivement (seul le LLM avait un timeout, pas les outils).

Invariants verrouillés ici :
1. Un outil `browser_*` pendu rend une Observation d'échec HONNÊTE (jamais un gel,
   jamais une exception) et déclenche le reset du singleton.
2. `force_reset_playwright_browser()` remplace le singleton par un objet NEUF
   (verrou neuf) sans attendre l'instance pendue.
3. `start()` est borné : un démarrage zombie rend False au lieu de pendre.
4. Le chemin nominal (outil browser_* rapide) est strictement inchangé.
"""

import asyncio

import pytest

import src.tools.playwright_browser as pb
from src.reasoning.tool_registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


def _register(registry, name, handler):
    registry.tools[name] = {"handler": handler, "parameters": {}}


# ── 1. Outil browser_* pendu → observation d'échec + reset, pas de gel ──────

@pytest.mark.asyncio
async def test_browser_tool_hang_returns_honest_observation(registry, monkeypatch):
    monkeypatch.setenv("LUMENA_BROWSER_TOOL_TIMEOUT", "0.2")
    reset_calls = []
    monkeypatch.setattr(
        pb, "force_reset_playwright_browser", lambda: reset_calls.append(1)
    )

    async def _hangs(**kwargs):
        await asyncio.sleep(30)

    _register(registry, "browser_hangs_test", _hangs)

    obs = await asyncio.wait_for(
        registry._execute_inner("browser_hangs_test", {}), timeout=10
    )
    assert obs.success is False
    assert "n'a pas répondu" in obs.content
    assert "browser_hangs_test" in obs.content  # le modèle sait quoi réessayer
    assert reset_calls == [1]  # le singleton a bien été réinitialisé


@pytest.mark.asyncio
async def test_browser_tool_timeout_survives_reset_failure(registry, monkeypatch):
    """Le reset qui casse ne doit pas masquer l'observation d'échec."""
    monkeypatch.setenv("LUMENA_BROWSER_TOOL_TIMEOUT", "0.2")

    def _broken_reset():
        raise RuntimeError("boom")

    monkeypatch.setattr(pb, "force_reset_playwright_browser", _broken_reset)

    async def _hangs(**kwargs):
        await asyncio.sleep(30)

    _register(registry, "browser_hangs_test", _hangs)
    obs = await registry._execute_inner("browser_hangs_test", {})
    assert obs.success is False
    assert "n'a pas répondu" in obs.content


# ── 2. force_reset remplace le singleton (verrou empoisonné inatteignable) ──

def test_force_reset_replaces_singleton():
    orig = pb._playwright_browser
    try:
        first = pb.get_playwright_browser(headless=True)
        assert pb._playwright_browser is first
        pb.force_reset_playwright_browser()
        assert pb._playwright_browser is None
        second = pb.get_playwright_browser(headless=True)
        assert second is not first
        assert second._lock is not first._lock  # verrou NEUF, pas le poison
    finally:
        pb._playwright_browser = orig


def test_force_reset_noop_without_instance():
    orig = pb._playwright_browser
    try:
        pb._playwright_browser = None
        pb.force_reset_playwright_browser()  # ne doit pas lever
        assert pb._playwright_browser is None
    finally:
        pb._playwright_browser = orig


# ── 3. start() borné : démarrage zombie → False, pas un gel ────────────────

@pytest.mark.asyncio
async def test_start_bounded_returns_false(monkeypatch):
    browser = pb.PlaywrightBrowser(headless=True, profile_name=None)

    async def _never_returns():
        await asyncio.sleep(60)

    monkeypatch.setattr(browser, "_start_inner", _never_returns)
    monkeypatch.setattr(pb, "BROWSER_START_TIMEOUT_S", 0.2)

    result = await asyncio.wait_for(browser.start(), timeout=10)
    assert result is False


# ── 4. Chemin nominal strictement inchangé ──────────────────────────────────

@pytest.mark.asyncio
async def test_browser_tool_fast_path_unchanged(registry, monkeypatch):
    monkeypatch.setenv("LUMENA_BROWSER_TOOL_TIMEOUT", "240")

    async def _fast(**kwargs):
        return "ok-rapide"

    _register(registry, "browser_fast_test", _fast)
    obs = await registry._execute_inner("browser_fast_test", {})
    assert obs.success is True
    assert "ok-rapide" in obs.content


@pytest.mark.asyncio
async def test_non_browser_tool_not_wrapped(registry, monkeypatch):
    """Les outils non-browser ne passent PAS par la borne (zéro changement)."""
    monkeypatch.setenv("LUMENA_BROWSER_TOOL_TIMEOUT", "0.05")

    async def _slowish(**kwargs):
        await asyncio.sleep(0.3)  # > au timeout browser : ne doit PAS être coupé
        return "fini"

    _register(registry, "lecture_lente_test", _slowish)
    obs = await registry._execute_inner("lecture_lente_test", {})
    assert obs.success is True
    assert "fini" in obs.content
