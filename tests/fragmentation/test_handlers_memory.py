"""
test_handlers_memory.py - Tests fonctionnels des handlers mémoire fragmentés.

Teste chaque handler de memory.py avec un HandlerContext de test.
"""

import json
import sys

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.memory import (
    read_journal_handler,
    memory_search_handler,
    memory_stats_handler,
    memory_get_handler,
    learn_instinct_handler,
    suggest_instincts_handler,
    curiosity_status_handler,
    list_journal_dates_handler,
    search_journal_handler,
    get_memory_handler_defs,
)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Patch paths.py constants so handler reads from tmp_path, not real data/
    import src.reasoning.handlers.memory as mem_mod
    monkeypatch.setattr(mem_mod, "JOURNAL_JSON", tmp_path / "data" / "journal.json")
    monkeypatch.setattr(mem_mod, "JOURNAL_DIR", tmp_path / "data" / "memory" / "journal")
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def ctx_with_journal(tmp_path, monkeypatch):
    """Contexte avec un journal.json peuplé."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    journal = [
        {"timestamp": "2026-03-04T10:00:00", "type": "action", "content": "Test entry alpha"},
        {"timestamp": "2026-03-04T11:00:00", "type": "action", "content": "Test entry beta"},
        {"timestamp": "2026-03-03T09:00:00", "type": "action", "content": "Yesterday entry"},
    ]
    (data_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    # Patch paths.py constants in the memory handler module
    import src.reasoning.handlers.memory as mem_mod
    monkeypatch.setattr(mem_mod, "JOURNAL_JSON", data_dir / "journal.json")
    monkeypatch.setattr(mem_mod, "JOURNAL_DIR", data_dir / "memory" / "journal")
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def ctx_with_memory(tmp_path, monkeypatch):
    """Contexte avec un mock lumena.memory.

    `JOURNAL_DIR` est une constante de module : le handler ne consulte PAS
    `ctx.lumena_root` pour le journal. Sans ce monkeypatch — présent dans la
    fixture voisine, oublié ici — les tests lisent le journal RÉEL de l'instance.
    Constaté le 2026-08-16 : `test_no_results` est passé au rouge tout seul
    pendant la nuit, parce que Lumena avait écrit dans son journal à 23:45 (suite
    verte à 17:12, aucun code touché entre les deux). Un test qui dépend de
    l'activité autonome de l'application ne mesure plus rien.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    import src.reasoning.handlers.memory as mem_mod

    monkeypatch.setattr(
        mem_mod, "JOURNAL_DIR", tmp_path / "data" / "memory" / "journal"
    )
    lumena_mock = MagicMock()
    lumena_mock.memory.recall.return_value = [
        {"content": "L'utilisateur aime Python", "date": "2026-03-01", "tags": ["pref"]},
        {"content": "LUMENA est autonome", "date": "2026-03-02", "tags": ["identity"]},
    ]
    lumena_mock.memory.get_stats.return_value = {
        "total_memories": 42,
        "facts": 15,
        "conversations": 27,
    }
    ctx = HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )
    ctx.lumena = lumena_mock
    return ctx


# ─── read_journal ──────────────────────────────────────────────────────────

class TestReadJournal:
    @pytest.mark.asyncio
    async def test_no_journal_file(self, ctx):
        r = await read_journal_handler(ctx)
        assert r.success
        assert "Aucune entr\u00e9e" in r.output or "Aucun journal" in r.output

    @pytest.mark.asyncio
    async def test_read_today_entries(self, ctx_with_journal):
        r = await read_journal_handler(ctx_with_journal, date="2026-03-04")
        assert r.success
        assert "alpha" in r.output
        assert "beta" in r.output
        assert "Yesterday" not in r.output

    @pytest.mark.asyncio
    async def test_read_yesterday(self, ctx_with_journal):
        r = await read_journal_handler(ctx_with_journal, date="2026-03-03")
        assert r.success
        assert "Yesterday" in r.output

    @pytest.mark.asyncio
    async def test_no_entries_for_date(self, ctx_with_journal):
        r = await read_journal_handler(ctx_with_journal, date="2025-01-01")
        assert r.success
        assert "Aucune entrée" in r.output


# ─── memory_search ─────────────────────────────────────────────────────────

class TestMemorySearch:
    @pytest.mark.asyncio
    async def test_no_lumena(self, ctx):
        r = await memory_search_handler(ctx, query="python")
        assert r.success
        assert "Aucun souvenir" in r.output or "journal" in r.output.lower() or r.output

    @pytest.mark.asyncio
    async def test_with_results(self, ctx_with_memory):
        r = await memory_search_handler(ctx_with_memory, query="Python")
        assert r.success
        assert "L'utilisateur aime Python" in r.output
        assert "LUMENA est autonome" in r.output

    @pytest.mark.asyncio
    async def test_no_results(self, ctx_with_memory):
        ctx_with_memory.lumena.memory.recall.return_value = []
        r = await memory_search_handler(ctx_with_memory, query="xyz")
        assert r.success
        assert "Aucun souvenir" in r.output


# ─── memory_stats ──────────────────────────────────────────────────────────

class TestMemoryStats:
    @pytest.mark.asyncio
    async def test_no_lumena(self, ctx):
        r = await memory_stats_handler(ctx)
        assert not r.success

    @pytest.mark.asyncio
    async def test_returns_stats(self, ctx_with_memory):
        r = await memory_stats_handler(ctx_with_memory)
        assert r.success
        assert "42" in r.output
        assert "total_memories" in r.output


# ─── memory_get ────────────────────────────────────────────────────────────

class TestMemoryGet:
    @pytest.mark.asyncio
    async def test_no_lumena(self, ctx):
        r = await memory_get_handler(ctx, query="test")
        assert not r.success

    @pytest.mark.asyncio
    async def test_with_results(self, ctx_with_memory):
        r = await memory_get_handler(ctx_with_memory, query="Python")
        assert r.success
        assert "L'utilisateur aime Python" in r.output
        assert "pref" in r.output  # tag

    @pytest.mark.asyncio
    async def test_no_results(self, ctx_with_memory):
        ctx_with_memory.lumena.memory.recall.return_value = []
        r = await memory_get_handler(ctx_with_memory, query="nothing")
        assert r.success
        assert "Aucun souvenir" in r.output


# ─── learn_instinct ────────────────────────────────────────────────────────

class TestLearnInstinct:
    @pytest.mark.asyncio
    async def test_import_error(self, ctx):
        """Sans module instincts, retourne un échec propre."""
        with patch.dict(sys.modules, {"src.learning.instincts": None}):
            r = await learn_instinct_handler(
                ctx, pattern="test", response="action", was_successful=True
            )
            assert not r.success
            assert "non disponible" in r.output

    @pytest.mark.asyncio
    async def test_success_mock(self, ctx):
        instinct_mock = MagicMock()
        instinct_mock.confidence = 0.85
        instinct_mock.times_used = 3
        with patch(
            "src.reasoning.handlers.memory.get_instinct_system",
            create=True,
        ) as mock_get:
            # Patch at the import location inside the handler
            with patch(
                "src.learning.instincts.get_instinct_system",
                return_value=MagicMock(learn=MagicMock(return_value=instinct_mock)),
                create=True,
            ):
                # The handler does from ...learning.instincts import get_instinct_system
                # We need to mock the actual import path
                import importlib
                import sys

                # Create mock module
                mock_module = MagicMock()
                mock_module.get_instinct_system.return_value = MagicMock(
                    learn=MagicMock(return_value=instinct_mock)
                )
                sys.modules["src.learning.instincts"] = mock_module
                try:
                    r = await learn_instinct_handler(
                        ctx,
                        pattern="erreur fichier",
                        response="vérifier chemin",
                        was_successful=True,
                        category="file",
                    )
                    assert r.success
                    assert "Apprentissage enregistré" in r.output
                    assert "erreur fichier" in r.output
                finally:
                    sys.modules.pop("src.learning.instincts", None)


# ─── suggest_instincts ─────────────────────────────────────────────────────

class TestSuggestInstincts:
    @pytest.mark.asyncio
    async def test_import_error(self, ctx):
        with patch.dict(sys.modules, {"src.learning.instincts": None}):
            r = await suggest_instincts_handler(ctx, context="code python")
            assert not r.success
            assert "non disponible" in r.output


# ─── curiosity_status ──────────────────────────────────────────────────────

class TestCuriosityStatus:
    @pytest.mark.asyncio
    async def test_import_error(self, ctx):
        with patch.dict(sys.modules, {"src.autonomy.curiosity": None}):
            r = await curiosity_status_handler(ctx)
            assert not r.success
            assert "non disponible" in r.output


# ─── handler_defs ──────────────────────────────────────────────────────────

class TestHandlerDefs:
    def test_count(self):
        defs = get_memory_handler_defs()
        assert len(defs) == 11

    def test_names(self):
        defs = get_memory_handler_defs()
        names = {d.name for d in defs}
        expected = {
            "read_journal",
            "write_journal",
            "memory_search",
            "memory_stats",
            "memory_get",
            "learn_from_action",
            "suggest_instincts",
            "get_curiosity_status",
            "memory_add",
            "list_journal_dates",
            "search_journal",
        }
        assert names == expected

    def test_all_have_category(self):
        for d in get_memory_handler_defs():
            assert d.category == "memory"

    def test_all_callable(self):
        for d in get_memory_handler_defs():
            assert callable(d.handler)
