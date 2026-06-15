"""Régression du bug "contenu lu ≠ affiché" (Polyester) : _ensure_active_tab_index
ne doit JAMAIS écraser self._page (vivante et présente) avec tabs[index]. Un
onglet fantôme restauré par Chrome qui se glisse à l'index 0 ne doit pas
détourner la page courante.
"""


class _FakePage:
    def __init__(self, name, closed=False):
        self.name = name
        self._closed = closed

    def is_closed(self):
        return self._closed


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages


def _make_browser(pages, active_index, current):
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser()
    b._context = _FakeContext(pages)
    b._active_tab_index = active_index
    b._page = current
    return b


def test_garde_la_page_courante_malgre_un_fantome_en_index_0():
    # Onglet fantôme restauré à l'index 0 ; la vraie page (viaduc) est à l'index 1
    ghost = _FakePage("polyester")
    real = _FakePage("viaduc")
    b = _make_browser([ghost, real], active_index=0, current=real)

    b._ensure_active_tab_index()

    assert b._page is real            # self._page PRÉSERVÉE (pas clobberée par le fantôme)
    assert b._active_tab_index == 1   # index resynchronisé sur la vraie page


def test_resync_index_sans_changer_la_page():
    p0, p1, p2 = _FakePage("a"), _FakePage("b"), _FakePage("c")
    b = _make_browser([p0, p1, p2], active_index=99, current=p2)
    b._ensure_active_tab_index()
    assert b._page is p2
    assert b._active_tab_index == 2


def test_fallback_si_page_courante_fermee():
    dead = _FakePage("dead", closed=True)
    alive = _FakePage("alive")
    b = _make_browser([alive], active_index=0, current=dead)
    b._ensure_active_tab_index()
    # page morte → on retombe sur tabs[index]
    assert b._page is alive


def test_fallback_si_page_courante_absente_des_tabs():
    orphan = _FakePage("orphan")  # pas dans la liste
    t0 = _FakePage("t0")
    b = _make_browser([t0], active_index=0, current=orphan)
    b._ensure_active_tab_index()
    assert b._page is t0


def test_aucun_onglet():
    b = _make_browser([], active_index=3, current=_FakePage("x"))
    b._ensure_active_tab_index()
    assert b._page is None
    assert b._active_tab_index == 0
