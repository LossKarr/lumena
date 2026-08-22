"""Anti-freeze (A+B) — le navigateur Playwright partagé est sérialisé + lease borné.

Bug runtime (log A 26/06) : chat ET mission ont appelé `web_fetch` en même temps →
deux `page.goto` sur le MÊME navigateur → hang. Cause : `web_fetch`/`web_search`/…
n'étaient pas couverts par le lease. + le lease s'attendait sans timeout (freeze infini).
"""
from __future__ import annotations

import asyncio

import pytest

from src.subagents import resource_lease as RL


@pytest.fixture(autouse=True)
def _reset_browser_excl():
    RL.reset_browser_exclusivity_for_tests()  # singleton navigateur propre par test
    yield
    RL.reset_browser_exclusivity_for_tests()


# ── A : couverture des outils navigateur ─────────────────────────────────────────

def test_browser_tools_mapped_to_browser_lease():
    # Priorité 1 : web_fetch RETIRÉ (urllib-first, leasé seulement dans son fallback).
    for name in ("web_search", "deep_research", "web_crawl", "web_crawl_campaign"):
        assert RL.resource_key_for(name) == "browser", name
    # outils navigateur explicites toujours couverts
    assert RL.resource_key_for("browser_search_google") == "browser"
    assert RL.resource_key_for("browser") == "browser"


def test_http_search_not_browser_locked():
    # DuckDuckGo HTTP → pas le navigateur → pas de lease (garde le parallélisme)
    assert RL.resource_key_for("web_search_brave") is None
    assert RL.resource_key_for("read_file") is None
    # Priorité 1 (garde-fou reviewer #1) : web_fetch n'est PLUS leasé browser par défaut.
    assert RL.resource_key_for("web_fetch") is None


@pytest.mark.asyncio
async def test_two_browser_tools_serialize():
    lease = RL.ResourceLease()
    order = []

    async def fetch(tag):
        async with lease.hold(RL.resource_key_for("web_search")):  # outil navigateur réel
            order.append(f"start-{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end-{tag}")

    await asyncio.gather(fetch("A"), fetch("B"))
    # jamais deux 'start' sans un 'end' entre les deux → accès navigateur sérialisé
    for i in range(1, len(order)):
        if order[i].startswith("start"):
            assert order[i - 1].startswith("end")


# ── B : timeout du lease (anti-freeze infini) ────────────────────────────────────

def test_lease_wait_timeout_default_and_env(monkeypatch):
    monkeypatch.delenv("LUMENA_TOOL_LEASE_TIMEOUT", raising=False)
    assert RL.lease_wait_timeout() == 600.0
    monkeypatch.setenv("LUMENA_TOOL_LEASE_TIMEOUT", "30")
    assert RL.lease_wait_timeout() == 30.0
    monkeypatch.setenv("LUMENA_TOOL_LEASE_TIMEOUT", "99999")  # borné à 3600
    assert RL.lease_wait_timeout() == 3600.0


@pytest.mark.asyncio
async def test_hold_times_out_instead_of_hanging():
    lease = RL.ResourceLease()
    async with lease.hold("browser"):  # déjà tenu
        with pytest.raises(asyncio.TimeoutError):
            async with lease.hold("browser", timeout=0.05):
                pass  # ne doit jamais entrer


# ── B bout-en-bout : execute() renvoie une erreur propre au lieu de freezer ──────

@pytest.mark.asyncio
async def test_execute_returns_error_when_browser_busy(monkeypatch):
    # Une MISSION tient le navigateur → l'op navigateur d'un autre owner (chat) timeoute proprement.
    from src.reasoning.tool_registry import ToolRegistry

    RL.reset_browser_exclusivity_for_tests()
    monkeypatch.setattr(RL, "lease_wait_timeout", lambda: 0.05)
    reg = ToolRegistry(lumena=None)
    excl = RL.get_browser_exclusivity()
    async with excl.action("missionA"):  # missionA pilote le navigateur
        obs = await reg.execute("web_search", {"query": "x"})  # autre owner (chat) → outil navigateur
    assert obs.success is False
    assert "occup" in obs.content.lower() or "abandonn" in obs.content.lower()


# ── #4 Exclusivité par mission : pas de mélange entre owners + same-owner sérialisé ──

@pytest.mark.asyncio
async def test_browser_exclusivity_cross_owner_blocks():
    excl = RL.BrowserExclusivity()
    async with excl.action("A"):
        # B ne peut pas s'intercaler tant que A tient (timeout court)
        with pytest.raises(asyncio.TimeoutError):
            async with excl.action("B", timeout=0.05):
                pass


# ── Priorité 1 — web_fetch : HTTP libre, fallback navigateur verrouillé ──────────

class _FakeUrlopen:
    """Réponse urllib factice (context manager) → pas de réseau réel."""
    def __init__(self, body=b"<html><body>hello-http</body></html>"):
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


@pytest.mark.asyncio
async def test_web_fetch_http_not_blocked_by_browser_owner(monkeypatch):
    """Garde-fou reviewer #3 : un web_fetch HTTP NE doit PAS attendre le lease
    navigateur, même si une mission tient le navigateur. (Avant Priorité 1 : timeout.)"""
    import urllib.request
    from src.reasoning.tool_registry import ToolRegistry

    RL.reset_browser_exclusivity_for_tests()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeUrlopen())
    monkeypatch.setattr(RL, "lease_wait_timeout", lambda: 0.05)
    reg = ToolRegistry(lumena=None)
    excl = RL.get_browser_exclusivity()
    async with excl.action("missionA"):  # une mission TIENT le navigateur
        obs = await reg.execute("web_fetch", {"url": "https://example.com"})
    assert obs.success is True            # plus de blocage : HTTP pur passe en parallèle
    assert "hello-http" in obs.content.lower()


@pytest.mark.asyncio
async def test_web_fetch_fallback_acquires_browser_lease(monkeypatch):
    """Garde-fou reviewer #2 : si urllib échoue, le fallback Playwright DOIT prendre
    l'exclusivité navigateur (owner-aware sticky)."""
    import urllib.request
    from src.reasoning.handlers import web as web_handlers

    RL.reset_browser_exclusivity_for_tests()

    def _boom(*a, **k):
        raise OSError("urllib down (test)")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    held = {}

    class _FakeBrowser:
        async def get_page_content(self, url):
            # au moment du pilotage navigateur, l'exclusivité doit être tenue
            held["owner"] = RL.get_browser_exclusivity().current_owner()
            return {"success": True, "content": "contenu via navigateur"}

    monkeypatch.setattr(
        "src.tools.playwright_browser.get_playwright_browser",
        lambda *a, **k: _FakeBrowser(),
    )

    tok = RL.set_browser_owner("missionX")  # contexte mission
    try:
        res = await web_handlers.web_fetch_handler(None, "https://blocked.example")
    finally:
        RL.clear_browser_owner(tok)

    assert res.success is True
    assert held.get("owner") == "missionX"  # le fallback a bien pris le lease, owner-aware


def test_parallel_browser_steering_helper():
    """Steering : ajouté SEULEMENT en délégation parallèle (≥2 workers)."""
    from src.reasoning.handlers.missions import apply_parallel_browser_steering as steer
    base = "Recherche web sur le Comté."
    # parallèle → steering présent, oriente vers le HTTP, déconseille browser_*
    out = steer(base, worker_count=6)
    assert out.startswith(base)
    assert "web_fetch" in out and "web_search_brave" in out
    assert "browser_navigate" in out
    # worker unique → inchangé (pas de contention)
    assert steer(base, worker_count=1) == base
    assert steer("", worker_count=6) == ""


@pytest.mark.asyncio
async def test_browser_exclusivity_sticky_between_actions():
    excl = RL.BrowserExclusivity()
    async with excl.action("A"):
        pass  # action finie mais A reste owner (sticky)
    # B reste bloqué : A n'a pas relâché l'exclusivité
    with pytest.raises(asyncio.TimeoutError):
        async with excl.action("B", timeout=0.05):
            pass
    await excl.release_owner("A")  # A libère → B passe
    async with excl.action("B", timeout=1.0):
        assert excl.current_owner() == "B"


@pytest.mark.asyncio
async def test_browser_exclusivity_same_owner_serialized():
    excl = RL.BrowserExclusivity()
    peak = {"cur": 0, "max": 0}

    async def op():
        async with excl.action("A"):  # même owner, concurrents
            peak["cur"] += 1
            peak["max"] = max(peak["max"], peak["cur"])
            await asyncio.sleep(0.02)
            peak["cur"] -= 1

    await asyncio.gather(op(), op(), op())
    assert peak["max"] == 1  # JAMAIS deux actions du même owner en parallèle (pas de deadlock)


@pytest.mark.asyncio
async def test_release_owner_idempotent_and_owner_checked():
    excl = RL.BrowserExclusivity()
    # 1) jamais utilisé → release ne lève pas, no-op
    await excl.release_owner("never")
    async with excl.action("A"):
        pass
    # 2) un AUTRE owner ne peut pas libérer A
    await excl.release_owner("B")
    assert excl.current_owner() == "A"
    # 3) A libère ; re-libérer est idempotent
    await excl.release_owner("A")
    await excl.release_owner("A")
    assert excl.current_owner() is None
