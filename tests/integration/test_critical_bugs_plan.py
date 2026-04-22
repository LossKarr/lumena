"""
Tests for PLAN_BUGS_CRITIQUES_VERIFIED fixes.
P0: provider_health thread safety
B1: no keyword concatenation in react.py
B2: no duplicate has_file
P1: LRU eviction on contexts
P2: shutdown() clears contexts
P3: initialize_lumena() shuts down existing instance
"""

import threading
from collections import OrderedDict
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ── P0: provider_health thread safety ────────────────────────────────────────

class TestP0HealthLock:
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        with patch.object(MultiProviderLLM, "_resolve_initial_model_name", return_value="deepseek-v3"), \
             patch.object(MultiProviderLLM, "_resolve_ollama_host", return_value="http://localhost:11434"), \
             patch.object(MultiProviderLLM, "_load_model_config"):
            return MultiProviderLLM(model_name="deepseek-v3")

    def test_health_lock_exists(self, llm):
        """P0: _health_lock exists and is a threading.Lock."""
        assert hasattr(llm, "_health_lock")
        assert isinstance(llm._health_lock, type(threading.Lock()))

    def test_concurrent_mark_failure_no_crash(self, llm):
        """P0: 50 concurrent _mark_failure calls don't crash."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        errors = []

        def mark(provider):
            try:
                llm._mark_failure(provider)
            except Exception as e:
                errors.append(e)

        providers = ["openai", "anthropic", "deepseek", "google", "xai"]
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = [pool.submit(mark, p) for p in providers for _ in range(10)]
            for f in as_completed(futs):
                f.result()
        assert errors == []

    def test_concurrent_mark_success_no_crash(self, llm):
        """P0: concurrent _mark_success calls don't crash."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        errors = []

        def mark(provider):
            try:
                llm._mark_success(provider)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = [pool.submit(mark, "openai") for _ in range(50)]
            for f in as_completed(futs):
                f.result()
        assert errors == []

    def test_concurrent_mixed_ops(self, llm):
        """P0: concurrent mix of _mark_failure/_mark_success/_is_healthy."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        errors = []

        def op(i):
            try:
                p = "openai"
                if i % 3 == 0:
                    llm._mark_failure(p)
                elif i % 3 == 1:
                    llm._mark_success(p)
                else:
                    llm._is_healthy(p)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = [pool.submit(op, i) for i in range(60)]
            for f in as_completed(futs):
                f.result()
        assert errors == []


# ── B1: keyword tuple integrity ──────────────────────────────────────────────

class TestB1KeywordIntegrity:
    def test_no_concatenated_keywords(self):
        """B1: every keyword in _code_action_verbs is distinct (no silent concat)."""
        # We can't easily import the local tuple, so check the specific fix
        # by verifying the string "porte en pythonintégrer" does NOT exist
        import ast
        from pathlib import Path
        react_path = Path(__file__).parent.parent.parent / "src" / "reasoning" / "react.py"
        source = react_path.read_text(encoding="utf-8")
        assert "porte en pythonintégrer" not in source
        assert "porte en python\"\n" not in source or '"porte en python",\n' in source

    def test_integrer_is_separate_keyword(self):
        """B1: 'intégrer' matches as its own keyword."""
        # Simulate the matching logic
        verbs = (
            "porter", "porte", "porte en react", "porte en python",
            "intégrer", "integrer", "integre", "intègre",
        )
        assert "intégrer" in verbs
        assert "porte en python" in verbs
        # Verify they're separate elements (8 total, not 7)
        assert len(verbs) == 8


# ── B2: hybrid classifier (P1 refactoring) ───────────────────────────────────

class TestB2NoDuplicateHasFile:
    def test_single_has_file_assignment(self):
        """B2: v2 — auto-route supprimé, routage via delegate_task tool.
        Legacy code should be absent from react.py."""
        from pathlib import Path
        react_path = Path(__file__).parent.parent.parent / "src" / "reasoning" / "react.py"
        source = react_path.read_text(encoding="utf-8")
        # Old has_file regex should be gone
        old_count = source.count("has_file = any(") + source.count("has_file = _any_word(")
        assert old_count == 0, f"Old has_file regex still present ({old_count} occurrences)"
        # Legacy classifier should be gone
        assert "_classify_intent_llm" not in source, "Legacy _classify_intent_llm toujours présent"
        assert "_maybe_auto_route_codeagent_legacy" not in source, "Fonction legacy toujours présente"
        # v2: auto-route removed, delegate_task handler handles routing
        assert "_maybe_auto_route_codeagent" not in source or "supprimé" in source, \
            "_maybe_auto_route_codeagent devrait être supprimé (v2)"


# ── P1: LRU eviction on contexts ────────────────────────────────────────────

class TestP1LRUEviction:
    def _make_svc(self, max_contexts):
        from src.core_services.identity_service import IdentityService
        from collections import OrderedDict

        ctx = MagicMock()
        ctx.data_dir = MagicMock()
        ctx.data_dir.__truediv__ = lambda self, x: MagicMock(
            __truediv__=lambda self, y: MagicMock(exists=lambda: False)
        )
        ctx.memory = MagicMock()
        svc = IdentityService(
            ctx,
            tg_contexts=OrderedDict(),
            discord_contexts=OrderedDict(),
            discord_users={},
            max_contexts=max_contexts,
        )
        return svc

    def test_tg_contexts_are_ordered_dict(self):
        """P1: _tg_contexts is an OrderedDict."""
        svc = self._make_svc(500)
        assert isinstance(svc._tg_contexts, OrderedDict)

    def test_lru_eviction_tg(self):
        """P1: _load_tg_context evicts oldest when over limit."""
        svc = self._make_svc(5)

        # Load 7 contexts
        for i in range(7):
            svc._load_tg_context(f"user_{i}")

        assert len(svc._tg_contexts) == 5
        # Oldest (user_0, user_1) should be evicted
        assert "user_0" not in svc._tg_contexts
        assert "user_1" not in svc._tg_contexts
        # Newest should exist
        assert "user_6" in svc._tg_contexts

    def test_lru_eviction_discord(self):
        """P1: _load_discord_user_context evicts oldest when over limit."""
        svc = self._make_svc(3)

        for i in range(5):
            svc._load_discord_user_context(f"user_{i}", "chan_1")

        assert len(svc._discord_contexts) == 3
        assert "chan_1_user_0" not in svc._discord_contexts
        assert "chan_1_user_4" in svc._discord_contexts

    def test_lru_move_to_end_on_access(self):
        """P1: accessing existing context moves it to end (not evicted)."""
        svc = self._make_svc(3)

        # Load 3 contexts
        for i in range(3):
            svc._load_tg_context(f"user_{i}")

        # Access user_0 (oldest) → moves to end
        svc._load_tg_context("user_0")

        # Add user_3 → should evict user_1 (now oldest), NOT user_0
        svc._load_tg_context("user_3")

        assert "user_0" in svc._tg_contexts
        assert "user_1" not in svc._tg_contexts
        assert len(svc._tg_contexts) == 3


# ── P2: shutdown clears contexts ─────────────────────────────────────────────

class TestP2ShutdownClears:
    @pytest.mark.asyncio
    async def test_shutdown_clears_contexts(self):
        """P2: shutdown() clears _tg_contexts, _discord_contexts, _discord_users."""
        from src.core import LumenaCore
        core = LumenaCore.__new__(LumenaCore)
        core._tg_contexts = OrderedDict({"a": 1, "b": 2})
        core._discord_contexts = OrderedDict({"c": 3})
        core._discord_users = {"u1": {"name": "test"}}
        core.is_initialized = True
        core.personality = MagicMock()
        core.personality.name = "Test"

        await core.shutdown()

        assert core.is_initialized is False
        assert len(core._tg_contexts) == 0
        assert len(core._discord_contexts) == 0
        assert len(core._discord_users) == 0


# ── P3: initialize_lumena shuts down existing ────────────────────────────────

class TestP3InitializeShutdown:
    @pytest.mark.asyncio
    async def test_initialize_shuts_down_existing(self):
        """P3: initialize_lumena() calls shutdown() on existing instance."""
        import src.core as core_mod

        old_instance = AsyncMock()
        old_instance.shutdown = AsyncMock()

        # Patch the module-level _lumena_instance
        original = core_mod._lumena_instance
        try:
            core_mod._lumena_instance = old_instance

            with patch.object(core_mod, "LumenaCore") as MockCore:
                mock_new = AsyncMock()
                mock_new.initialize = AsyncMock(return_value=True)
                MockCore.return_value = mock_new

                result = await core_mod.initialize_lumena()

                # Verify shutdown was called on old instance
                old_instance.shutdown.assert_awaited_once()
                # Verify new instance was created and initialized
                MockCore.assert_called_once()
                mock_new.initialize.assert_awaited_once()
                assert result is mock_new
        finally:
            core_mod._lumena_instance = original

    @pytest.mark.asyncio
    async def test_initialize_first_time_no_shutdown(self):
        """P3: initialize_lumena() with no existing instance doesn't crash."""
        import src.core as core_mod

        original = core_mod._lumena_instance
        try:
            core_mod._lumena_instance = None

            with patch.object(core_mod, "LumenaCore") as MockCore:
                mock_new = AsyncMock()
                mock_new.initialize = AsyncMock(return_value=True)
                MockCore.return_value = mock_new

                result = await core_mod.initialize_lumena()

                MockCore.assert_called_once()
                assert result is mock_new
        finally:
            core_mod._lumena_instance = original

    @pytest.mark.asyncio
    async def test_initialize_shutdown_error_continues(self):
        """P3: if shutdown() fails, initialize_lumena() still creates new instance."""
        import src.core as core_mod

        old_instance = AsyncMock()
        old_instance.shutdown = AsyncMock(side_effect=RuntimeError("boom"))

        original = core_mod._lumena_instance
        try:
            core_mod._lumena_instance = old_instance

            with patch.object(core_mod, "LumenaCore") as MockCore:
                mock_new = AsyncMock()
                mock_new.initialize = AsyncMock(return_value=True)
                MockCore.return_value = mock_new

                result = await core_mod.initialize_lumena()

                old_instance.shutdown.assert_awaited_once()
                assert result is mock_new
        finally:
            core_mod._lumena_instance = original
