"""Tests Phase 7 — Compactage structuré intégré.

Couvre :
  7.1 — _history_observation_limit() dynamique via RuntimeContext
  7.2 — Injection MEMORY.md conditionnelle (seulement REACT/PROJECT)
  7.3 — Fenêtre _format_history() adaptée à l'intent (tool_direct=3, project=7, react=5)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime_ctx(intent="react", max_output_tokens=8192, max_context_window=32768, mode="agent"):
    """Construit un RuntimeContext minimal pour les tests."""
    from src.core_services.runtime_context import RuntimeContext
    return RuntimeContext(
        active_model="test-model",
        active_provider="openai",
        max_context_window=max_context_window,
        max_output_tokens=max_output_tokens,
        intent=intent,
        mode=mode,
    )


def _make_react_loop(runtime_ctx=None):
    """Construit un ReActLoop minimal (sans vraie LLM)."""
    from src.reasoning.react import ReActLoop, ToolRegistry

    tools = MagicMock(spec=ToolRegistry)
    tools.lumena = None
    tools.get_tools_description.return_value = ""
    tools._caller_set_allowed = False
    tools._is_ide_runtime = MagicMock(return_value=False)  # Ne pas déclencher chemin IDE
    tools.ide_context = {}

    loop = ReActLoop.__new__(ReActLoop)
    loop.history = []
    loop.runtime_ctx = runtime_ctx
    loop.timeout_seconds = 600
    loop.tools = tools
    return loop


# ---------------------------------------------------------------------------
# Phase 7.1 — obs_limit dynamique
# ---------------------------------------------------------------------------

class TestHistoryObsLimitDynamic:
    """_history_observation_limit() retourne une valeur dynamique depuis RuntimeContext."""

    def test_fallback_sans_runtime_ctx(self):
        loop = _make_react_loop(runtime_ctx=None)
        limit = loop._history_observation_limit()
        assert 300 <= limit <= 20000, f"fallback hors plage: {limit}"

    def test_dynamic_deepseek_chat(self):
        """deepseek-chat: output=8192, ctx=64000 -> max(4000, min(64000//20=3200, 16000))=4000"""
        ctx = _make_runtime_ctx(max_output_tokens=8192, max_context_window=64000)
        loop = _make_react_loop(runtime_ctx=ctx)
        limit = loop._history_observation_limit()
        assert limit == max(4000, min(64000 // 20, 16000))

    def test_dynamic_gemini_flash(self):
        """gemini-2.5-flash: output=65536, ctx=1048576 -> max(4000, min(1048576//20=52428, 16000))=16000"""
        ctx = _make_runtime_ctx(max_output_tokens=65536, max_context_window=1048576)
        loop = _make_react_loop(runtime_ctx=ctx)
        limit = loop._history_observation_limit()
        assert limit == 16000

    def test_dynamic_small_model(self):
        """Modele tres petit: output=512, ctx=4096 -> max(4000, min(4096//20=204, 16000))=4000 (clamp min)"""
        ctx = _make_runtime_ctx(max_output_tokens=512, max_context_window=4096)
        loop = _make_react_loop(runtime_ctx=ctx)
        limit = loop._history_observation_limit()
        assert limit == 4000  # clamp minimum

    def test_dynamic_zero_values_fallback(self):
        """max_output=0 → pas de dynamic, fallback env var."""
        ctx = _make_runtime_ctx(max_output_tokens=0, max_context_window=32000)
        loop = _make_react_loop(runtime_ctx=ctx)
        limit = loop._history_observation_limit()
        # 0 → pas de dynamic → env var fallback (8000 défaut)
        assert 300 <= limit <= 20000


# ---------------------------------------------------------------------------
# Phase 7.2 — MEMORY permanente conditionnelle
# ---------------------------------------------------------------------------

class TestPermanentMemoryConditional:
    """Phase 7.2: MEMORY.md injectée seulement pour intent react/project."""

    def _build_parts(self, intent: str) -> list[str]:
        """Appelle _build_identity_context() sur un loop avec le bon intent."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        ctx = _make_runtime_ctx(intent=intent)

        tools = MagicMock(spec=ToolRegistry)
        lum = MagicMock()
        lum.personality = None
        lum.memory = None
        lum.emotion_manager = None
        lum.get_permanent_memory_context.return_value = "## PERMANENT MEMORY TEST"
        tools.lumena = lum
        tools._caller_set_allowed = False

        loop = ReActLoop.__new__(ReActLoop)
        loop.history = []
        loop.runtime_ctx = ctx
        loop.timeout_seconds = 600
        loop.tools = tools

        result = loop._build_identity_context("test query")
        return result

    def test_react_includes_permanent_memory(self):
        result = self._build_parts("react")
        assert "PERMANENT MEMORY TEST" in result, "MEMORY.md doit être injectée pour intent=react"

    def test_project_includes_permanent_memory(self):
        result = self._build_parts("project")
        assert "PERMANENT MEMORY TEST" in result, "MEMORY.md doit être injectée pour intent=project"

    def test_tool_direct_skips_permanent_memory(self):
        result = self._build_parts("tool_direct")
        assert "PERMANENT MEMORY TEST" not in result, "MEMORY.md ne doit PAS être injectée pour intent=tool_direct"

    def test_no_runtime_ctx_includes_permanent_memory(self):
        """Sans runtime_ctx, comportement par défaut = injecter (comme avant)."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        tools = MagicMock(spec=ToolRegistry)
        lum = MagicMock()
        lum.personality = None
        lum.memory = None
        lum.emotion_manager = None
        lum.get_permanent_memory_context.return_value = "## PERMANENT MEMORY FALLBACK"
        tools.lumena = lum
        tools._caller_set_allowed = False

        loop = ReActLoop.__new__(ReActLoop)
        loop.history = []
        loop.runtime_ctx = None
        loop.timeout_seconds = 600
        loop.tools = tools

        result = loop._build_identity_context("test query")
        assert "PERMANENT MEMORY FALLBACK" in result, "Sans runtime_ctx, MEMORY.md doit être injectée (fallback)"


# ---------------------------------------------------------------------------
# Phase 7.3 — Fenêtre _format_history() par intent
# ---------------------------------------------------------------------------

def _make_step(n: int):
    """Crée une étape ReAct factice."""
    from src.reasoning.react import ReActStep, Thought, Action, Observation, ActionType
    return ReActStep(
        thought=Thought(content=f"Pensée {n}"),
        action=Action(action_type=ActionType.TOOL_CALL, tool_name="fake_tool"),
        observation=Observation(content=f"Résultat {n}" * 40),  # ~320 chars
    )


class TestFormatHistoryWindow:
    """Phase 7.3: fenêtre de _format_history() adaptée à l'intent."""

    def _format_with_intent(self, intent: str, n_steps: int) -> str:
        ctx = _make_runtime_ctx(intent=intent)
        loop = _make_react_loop(runtime_ctx=ctx)
        loop.history = [_make_step(i) for i in range(n_steps)]
        loop.tools = loop.tools  # déjà set par _make_react_loop
        return loop._format_history()

    def test_tool_direct_window_3(self):
        """intent=tool_direct → fenêtre 3 → sur 8 steps, pre_window = 5 steps résumés."""
        result = self._format_with_intent("tool_direct", 8)
        # Doit contenir RÉSUMÉ (5 steps en pré-window)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" in result

    def test_tool_direct_window_3_small_history(self):
        """intent=tool_direct, 2 steps → pas de pre_window."""
        result = self._format_with_intent("tool_direct", 2)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" not in result

    def test_project_window_7(self):
        """intent=project → fenêtre 7 → sur 6 steps, pas de pre_window."""
        result = self._format_with_intent("project", 6)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" not in result

    def test_project_window_7_large_history(self):
        """intent=project → fenêtre 7 → sur 10 steps, pre_window = 3 steps."""
        result = self._format_with_intent("project", 10)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" in result

    def test_react_window_5(self):
        """intent=react → fenêtre 5 → sur 8 steps, pre_window = 3 steps."""
        result = self._format_with_intent("react", 8)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" in result

    def test_react_window_5_small_history(self):
        """intent=react → fenêtre 5 → sur 4 steps, pas de pre_window."""
        result = self._format_with_intent("react", 4)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" not in result

    def test_default_intent_window_5(self):
        """intent inconnu → défaut 5."""
        result = self._format_with_intent("unknown_intent", 8)
        assert "RÉSUMÉ ÉTAPES PRÉCÉDENTES" in result


# ---------------------------------------------------------------------------
# RuntimeContext — champ intent
# ---------------------------------------------------------------------------

class TestRuntimeContextIntentField:
    """Vérifie que RuntimeContext a bien le champ intent."""

    def test_default_intent_is_react(self):
        from src.core_services.runtime_context import RuntimeContext
        ctx = RuntimeContext(active_model="m", active_provider="p")
        assert ctx.intent == "react"

    def test_intent_configurable(self):
        from src.core_services.runtime_context import RuntimeContext
        ctx = RuntimeContext(active_model="m", active_provider="p", intent="tool_direct")
        assert ctx.intent == "tool_direct"

    def test_build_runtime_snapshot_passes_intent(self):
        """build_runtime_snapshot() propage le paramètre intent."""
        from src.llm.multi_provider import MultiProviderLLM
        mp = MagicMock(spec=MultiProviderLLM)
        mp.model_name = "test"
        mp.provider = MagicMock()
        mp.provider.value = "openai"
        mp.context_window = 32000
        mp.max_output_tokens = 4096
        mp.provider_health = {}
        mp.fallback_order = []
        # Appel réel à build_runtime_snapshot sur une vraie instance
        from src.llm.multi_provider import MultiProviderLLM as RealMPLLM
        import inspect
        # Appel via méthode non-mockée
        result = RealMPLLM.build_runtime_snapshot(mp, source_channel="web", mode="agent", intent="project")
        assert result.intent == "project"
