"""Reprise de session : quand le contexte persistant est vivant mais que la
page active est morte, start() doit RÉCUPÉRER une page dans le contexte
existant — sans fermer/rouvrir le navigateur (sinon on perd un login / captcha
en cours). Régression du bug "elle ferme le Playwright pour le rouvrir".

Import de playwright_browser différé dans les fonctions (évite le flake d'ordre
d'import sur MAX_TABS lié au chargement de .env).
"""
import asyncio


class _FakePage:
    def __init__(self, closed=False):
        self._closed = closed
        self.url = "https://example.com"

    def is_closed(self):
        return self._closed


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.new_page_called = False

    async def new_page(self):
        self.new_page_called = True
        p = _FakePage()
        self.pages.append(p)
        return p


def test_start_recupere_page_vivante_sans_rouvrir():
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser(profile_name="lumena")
    live = _FakePage(closed=False)
    ctx = _FakeContext([live])
    b._context = ctx
    b._page = None            # page active perdue → is_running False
    b._playwright = object()  # un playwright "résiduel" qui NE doit PAS être relancé

    ok = asyncio.run(b.start())

    assert ok is True
    assert b._page is live           # page existante ré-acquise
    assert ctx.new_page_called is False
    assert b._context is ctx         # MÊME contexte → pas de réouverture
    assert b._playwright is not None  # pas de teardown


def test_start_recree_page_si_toutes_fermees():
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser(profile_name="lumena")
    dead = _FakePage(closed=True)
    ctx = _FakeContext([dead])
    b._context = ctx
    b._page = None

    ok = asyncio.run(b.start())

    assert ok is True
    assert ctx.new_page_called is True   # aucune page vivante → on en recrée une
    assert b._context is ctx             # toujours le même contexte
