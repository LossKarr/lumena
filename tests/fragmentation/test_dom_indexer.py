"""
Tests unitaires pour src/computer_use/dom_indexer.py
(DOMElement, DOMSnapshot, DOMIndexer, render_set_of_mark)

Phase 2.2 : DOM Accessibility Index + Set-of-Mark overlay.
"""

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.computer_use.dom_indexer import (
    CONTAINER_ROLES,
    DOMElement,
    DOMIndexer,
    DOMSnapshot,
    INTERACTIVE_ROLES,
    MAX_ELEMENTS,
    get_dom_indexer,
    render_set_of_mark,
)


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_tree(children: list) -> dict:
    """Crée un arbre accessibility minimal."""
    return {"role": "RootWebArea", "name": "Test Page", "children": children}


def _make_node(role: str, name: str = "", children: list = None) -> dict:
    node = {"role": role, "name": name}
    if children:
        node["children"] = children
    return node


def _make_page(tree: dict = None, title: str = "Test Page", url: str = "https://test.com") -> MagicMock:
    """Crée un mock de page Playwright."""
    page = MagicMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.accessibility = MagicMock()
    page.accessibility.snapshot = AsyncMock(return_value=tree)
    page.evaluate = AsyncMock(return_value=[])
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return page


def _run(coro):
    """Utilise le loop de session (set par conftest.py event_loop autouse).
    Si appelé avant la fixture (rare), crée un loop et le réutilise.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop fermé")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ─── DOMElement ────────────────────────────────────────────────────────────

class TestDOMElement:
    def test_to_text_basic(self):
        elem = DOMElement(index=1, role="button", name="Enregistrer")
        assert elem.to_text() == '[1] button "Enregistrer"'

    def test_to_text_with_value(self):
        elem = DOMElement(index=2, role="textbox", name="Email", value="alice@test.com")
        text = elem.to_text()
        assert "[2]" in text
        assert "textbox" in text
        assert '"Email"' in text
        assert 'value="alice@test.com"' in text

    def test_to_text_with_options(self):
        elem = DOMElement(index=3, role="combobox", name="Pays", options=["France", "USA", "UK"])
        text = elem.to_text()
        assert 'options=[' in text
        assert '"France"' in text

    def test_to_text_with_many_options_truncated(self):
        opts = [f"Option{i}" for i in range(8)]
        elem = DOMElement(index=1, role="combobox", name="Select", options=opts)
        text = elem.to_text()
        assert "+3" in text  # 8 - 5 = 3 extra

    def test_to_text_checked(self):
        elem = DOMElement(index=1, role="checkbox", name="Accept", checked=True)
        assert "checked" in elem.to_text()

    def test_to_text_unchecked(self):
        elem = DOMElement(index=1, role="checkbox", name="Accept", checked=False)
        assert "unchecked" in elem.to_text()

    def test_to_text_disabled(self):
        elem = DOMElement(index=1, role="button", name="Save", disabled=True)
        assert "(disabled)" in elem.to_text()

    def test_center_with_bbox(self):
        elem = DOMElement(index=1, role="button", name="X", bbox=(100, 200, 80, 40))
        assert elem.center == (140, 220)

    def test_center_without_bbox(self):
        elem = DOMElement(index=1, role="button", name="X")
        assert elem.center is None


# ─── DOMSnapshot ───────────────────────────────────────────────────────────

class TestDOMSnapshot:
    def test_to_text(self):
        snap = DOMSnapshot(
            url="https://test.com",
            title="Test",
            elements=[
                DOMElement(index=1, role="button", name="OK"),
                DOMElement(index=2, role="link", name="Home"),
            ],
            total_interactive=2,
        )
        text = snap.to_text()
        assert "Page: Test" in text
        assert "URL: https://test.com" in text
        assert "Interactive elements: 2" in text
        assert '[1] button "OK"' in text
        assert '[2] link "Home"' in text

    def test_to_text_truncated(self):
        snap = DOMSnapshot(
            url="https://test.com",
            title="T",
            elements=[DOMElement(index=1, role="button", name="X")],
            total_interactive=150,
            truncated=True,
        )
        text = snap.to_text()
        assert "showing top 1" in text


# ─── DOMIndexer._collect_interactive ───────────────────────────────────────

class TestCollectInteractive:
    def test_collects_buttons(self):
        indexer = DOMIndexer()
        tree = _make_tree([
            _make_node("button", "Submit"),
            _make_node("button", "Cancel"),
        ])
        results = indexer._collect_interactive(tree)
        assert len(results) == 2

    def test_ignores_containers(self):
        indexer = DOMIndexer()
        tree = _make_tree([
            _make_node("navigation", "main-nav", [
                _make_node("link", "Home"),
                _make_node("link", "About"),
            ]),
        ])
        results = indexer._collect_interactive(tree)
        assert len(results) == 2  # only links, not navigation

    def test_ignores_nameless_buttons(self):
        indexer = DOMIndexer()
        tree = _make_tree([_make_node("button", "")])
        results = indexer._collect_interactive(tree)
        assert len(results) == 0  # no name = skipped

    def test_keeps_nameless_textbox(self):
        indexer = DOMIndexer()
        tree = _make_tree([_make_node("textbox", "")])
        results = indexer._collect_interactive(tree)
        assert len(results) == 1  # textbox kept even without name

    def test_collects_all_interactive_roles(self):
        indexer = DOMIndexer()
        children = [_make_node(role, f"elem_{role}") for role in INTERACTIVE_ROLES]
        tree = _make_tree(children)
        results = indexer._collect_interactive(tree)
        assert len(results) == len(INTERACTIVE_ROLES)

    def test_deeply_nested(self):
        indexer = DOMIndexer()
        tree = _make_tree([
            _make_node("group", "g1", [
                _make_node("list", "l1", [
                    _make_node("listitem", "", [
                        _make_node("button", "Deep Button"),
                    ]),
                ]),
            ]),
        ])
        results = indexer._collect_interactive(tree)
        assert len(results) == 1
        assert results[0]["name"] == "Deep Button"


# ─── DOMIndexer._extract_options ──────────────────────────────────────────

class TestExtractOptions:
    def test_extracts_select_options(self):
        indexer = DOMIndexer()
        node = _make_node("combobox", "Country", [
            _make_node("option", "France"),
            _make_node("option", "USA"),
            _make_node("option", ""),  # empty option skipped
        ])
        options = indexer._extract_options(node)
        assert options == ["France", "USA"]

    def test_no_children(self):
        indexer = DOMIndexer()
        node = _make_node("button", "OK")
        assert indexer._extract_options(node) == []


# ─── DOMIndexer.snapshot ──────────────────────────────────────────────────

class TestDOMIndexerSnapshot:
    def test_snapshot_basic(self):
        tree = _make_tree([
            _make_node("button", "Submit"),
            _make_node("link", "Home"),
            _make_node("textbox", "Email"),
        ])
        page = _make_page(tree)
        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        assert snap.url == "https://test.com"
        assert snap.title == "Test Page"
        assert len(snap.elements) == 3
        assert snap.total_interactive == 3
        assert not snap.truncated
        assert snap.elements[0].index == 1
        assert snap.elements[0].role == "button"
        assert snap.elements[0].name == "Submit"
        assert snap.elements[2].role == "textbox"

    def test_snapshot_truncation(self):
        tree = _make_tree(
            [_make_node("button", f"Btn{i}") for i in range(150)]
        )
        page = _make_page(tree)
        indexer = DOMIndexer(max_elements=100)
        snap = _run(indexer.snapshot(page))
        assert len(snap.elements) == 100
        assert snap.total_interactive == 150
        assert snap.truncated

    def test_snapshot_empty_tree(self):
        page = _make_page(None)
        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        assert len(snap.elements) == 0
        assert snap.total_interactive == 0

    def test_snapshot_accessibility_fails(self):
        page = _make_page()
        page.accessibility.snapshot = AsyncMock(side_effect=RuntimeError("kaboom"))
        # Fallback DOM extraction should still return interactive elements.
        page.evaluate = AsyncMock(return_value=[
            {"role": "button", "name": "Continuer", "disabled": False, "checked": None, "options": []},
            {"role": "link", "name": "Accueil", "disabled": False, "checked": None, "options": []},
        ])
        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        assert len(snap.elements) == 2
        assert snap.total_interactive == 2
        assert snap.elements[0].role == "button"

    def test_snapshot_tree_none_falls_back_to_dom(self):
        page = _make_page(None)
        page.evaluate = AsyncMock(return_value=[
            {"role": "textbox", "name": "Recherche", "disabled": False, "checked": None, "options": []},
        ])
        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        assert len(snap.elements) == 1
        assert snap.elements[0].role == "textbox"


# ─── DOMIndexer.enrich_with_bboxes ────────────────────────────────────────

class TestEnrichWithBboxes:
    def test_enriches_matching_elements(self):
        tree = _make_tree([
            _make_node("button", "Submit"),
            _make_node("link", "Home"),
        ])
        page = _make_page(tree)
        # JS returns bboxes
        page.evaluate = AsyncMock(return_value=[
            {"role": "button", "name": "Submit", "x": 10, "y": 20, "w": 100, "h": 30},
            {"role": "link", "name": "Home", "x": 200, "y": 50, "w": 80, "h": 20},
        ])

        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        snap = _run(indexer.enrich_with_bboxes(page, snap))

        assert snap.elements[0].bbox == (10, 20, 100, 30)
        assert snap.elements[1].bbox == (200, 50, 80, 20)

    def test_handles_js_failure(self):
        tree = _make_tree([_make_node("button", "OK")])
        page = _make_page(tree)
        page.evaluate = AsyncMock(side_effect=RuntimeError("JS error"))

        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        snap = _run(indexer.enrich_with_bboxes(page, snap))
        assert snap.elements[0].bbox is None

    def test_empty_snapshot(self):
        page = _make_page(None)
        indexer = DOMIndexer()
        snap = DOMSnapshot(url="", title="", elements=[], total_interactive=0)
        result = _run(indexer.enrich_with_bboxes(page, snap))
        assert len(result.elements) == 0

    def test_duplicate_role_name_get_distinct_bboxes(self):
        tree = _make_tree([
            _make_node("link", "Voir l’annonce"),
            _make_node("link", "Voir l’annonce"),
        ])
        page = _make_page(tree)
        page.evaluate = AsyncMock(return_value=[
            {"role": "link", "name": "Voir l’annonce", "x": 100, "y": 200, "w": 80, "h": 20},
            {"role": "link", "name": "Voir l’annonce", "x": 100, "y": 260, "w": 80, "h": 20},
        ])

        indexer = DOMIndexer()
        snap = _run(indexer.snapshot(page))
        snap = _run(indexer.enrich_with_bboxes(page, snap))

        assert len(snap.elements) == 2
        assert snap.elements[0].bbox == (100, 200, 80, 20)
        assert snap.elements[1].bbox == (100, 260, 80, 20)


# ─── render_set_of_mark ───────────────────────────────────────────────────

class TestRenderSetOfMark:
    def test_draws_labels(self):
        from PIL import Image
        img = Image.new("RGB", (800, 600), (255, 255, 255))
        elements = [
            DOMElement(index=1, role="button", name="OK", bbox=(100, 100, 80, 30)),
            DOMElement(index=2, role="link", name="Home", bbox=(300, 200, 60, 20)),
        ]
        result = render_set_of_mark(img, elements)
        assert result.size == (800, 600)
        # Vérifier que l'image a été modifiée (pas identique)
        assert result.tobytes() != img.tobytes()

    def test_skips_elements_without_bbox(self):
        from PIL import Image
        img = Image.new("RGB", (200, 200), (128, 128, 128))
        elements = [
            DOMElement(index=1, role="button", name="NoBbox"),  # no bbox
        ]
        result = render_set_of_mark(img, elements)
        # Should return copy without modification
        assert result.size == (200, 200)

    def test_empty_elements(self):
        from PIL import Image
        img = Image.new("RGB", (200, 200), (0, 0, 0))
        result = render_set_of_mark(img, [])
        assert result.size == (200, 200)

    def test_no_show_bbox(self):
        from PIL import Image
        img = Image.new("RGB", (400, 400), (255, 255, 255))
        elements = [
            DOMElement(index=1, role="button", name="X", bbox=(50, 50, 100, 40)),
        ]
        result = render_set_of_mark(img, elements, show_bbox=False)
        assert result.size == (400, 400)

    def test_large_index_wraps_colors(self):
        from PIL import Image
        img = Image.new("RGB", (1000, 100), (200, 200, 200))
        # Index >10 should wrap color palette
        elements = [
            DOMElement(index=15, role="button", name="X", bbox=(10, 10, 50, 30)),
        ]
        result = render_set_of_mark(img, elements)
        assert result.size == (1000, 100)


# ─── Singleton ─────────────────────────────────────────────────────────────

class TestGetDomIndexer:
    def test_returns_same_instance(self):
        # Reset singleton
        import src.computer_use.dom_indexer as mod
        mod._dom_indexer = None
        a = get_dom_indexer()
        b = get_dom_indexer()
        assert a is b

    def test_custom_max_elements(self):
        import src.computer_use.dom_indexer as mod
        mod._dom_indexer = None
        idx = get_dom_indexer(max_elements=50)
        assert idx.max_elements == 50
        mod._dom_indexer = None  # cleanup


# ─── Constants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_interactive_roles_are_strings(self):
        for role in INTERACTIVE_ROLES:
            assert isinstance(role, str)
            assert len(role) > 0

    def test_no_overlap_interactive_container(self):
        overlap = INTERACTIVE_ROLES & CONTAINER_ROLES
        assert len(overlap) == 0, f"Roles en commun: {overlap}"

    def test_max_elements_positive(self):
        assert MAX_ELEMENTS > 0
        assert MAX_ELEMENTS == 100
