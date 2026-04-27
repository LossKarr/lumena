"""
🧪 Tests — Alimentation du StructuredState depuis ReActLoop (V1)

Couvre :
- _feed_structured_tool : enregistre les outils dans recent_tools
- _feed_structured_intent : alimente last_intent
- _feed_structured_clarification : ajoute une pending_question
- _feed_structured_facts_from_runtime : pose channel/workspace
- _structured_state retourne None si pas de ConversationContext
- Pas de crash si ConversationContext est None ou sans structured_state
"""

import pytest
from types import SimpleNamespace

from src.reasoning.react import ReActLoop
from src.core import ConversationContext
from src.structured_state import StructuredState


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_loop(*, conversation_context=None, runtime_ctx=None) -> ReActLoop:
    """Crée un ReActLoop minimal avec un ConversationContext optionnel."""
    loop = ReActLoop(
        llm_chat_func=None,
        conversation_context=conversation_context,
        runtime_ctx=runtime_ctx,
    )
    return loop


# ── Tests: _structured_state property ────────────────────────────────────────

class TestStructuredStateProperty:
    def test_returns_structured_state_when_available(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        assert loop._structured_state is ctx.structured_state

    def test_returns_none_when_no_context(self):
        loop = _make_loop(conversation_context=None)
        assert loop._structured_state is None

    def test_returns_none_when_context_has_no_structured_state(self):
        ctx = SimpleNamespace(messages=[])  # no structured_state attr
        loop = _make_loop(conversation_context=ctx)
        assert loop._structured_state is None


# ── Tests: _feed_structured_tool ─────────────────────────────────────────────

class TestFeedStructuredTool:
    def test_records_tool(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_tool("write_file")
        loop._feed_structured_tool("read_file")
        assert list(ctx.structured_state.recent_tools) == ["write_file", "read_file"]

    def test_no_crash_without_context(self):
        loop = _make_loop(conversation_context=None)
        loop._feed_structured_tool("write_file")  # must not raise

    def test_fifo_bounded(self):
        ctx = ConversationContext()
        ctx.structured_state.max_recent_tools = 3
        loop = _make_loop(conversation_context=ctx)
        for i in range(5):
            loop._feed_structured_tool(f"tool_{i}")
        assert list(ctx.structured_state.recent_tools) == ["tool_2", "tool_3", "tool_4"]


# ── Tests: _feed_structured_intent ───────────────────────────────────────────

class TestFeedStructuredIntent:
    def test_sets_intent(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_intent("code_edit")
        assert ctx.structured_state.last_intent == "code_edit"

    def test_none_intent_does_not_overwrite(self):
        ctx = ConversationContext()
        ctx.structured_state.last_intent = "existing"
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_intent(None)
        assert ctx.structured_state.last_intent == "existing"

    def test_empty_string_sets_none(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_intent("  ")
        assert ctx.structured_state.last_intent is None

    def test_no_crash_without_context(self):
        loop = _make_loop(conversation_context=None)
        loop._feed_structured_intent("test")  # must not raise


# ── Tests: _feed_structured_clarification ────────────────────────────────────

class TestFeedStructuredClarification:
    def test_adds_question(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_clarification("Quel fichier modifier ?")
        assert ctx.structured_state.pending_questions == ["Quel fichier modifier ?"]

    def test_no_duplicate(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_clarification("Q?")
        loop._feed_structured_clarification("Q?")
        assert ctx.structured_state.pending_questions == ["Q?"]

    def test_no_crash_without_context(self):
        loop = _make_loop(conversation_context=None)
        loop._feed_structured_clarification("test")


# ── Tests: _feed_structured_facts_from_runtime ───────────────────────────────

class TestFeedStructuredFactsFromRuntime:
    def test_sets_channel_and_workspace(self):
        ctx = ConversationContext()
        rt = SimpleNamespace(channel="discord", workspace_path="/home/user/project")
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts["channel"] == "discord"
        assert ctx.structured_state.established_facts["workspace"] == "/home/user/project"

    def test_sets_only_channel_if_no_workspace(self):
        ctx = ConversationContext()
        rt = SimpleNamespace(channel="telegram")
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts["channel"] == "telegram"
        assert "workspace" not in ctx.structured_state.established_facts

    def test_no_crash_without_runtime_ctx(self):
        ctx = ConversationContext()
        loop = _make_loop(conversation_context=ctx, runtime_ctx=None)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts == {}

    def test_no_crash_without_context(self):
        loop = _make_loop(conversation_context=None, runtime_ctx=SimpleNamespace(channel="web"))
        loop._feed_structured_facts_from_runtime()

    def test_sets_active_file(self):
        ctx = ConversationContext()
        rt = SimpleNamespace(channel="ide", workspace_path=None, active_file_path="/src/foo.py")
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts["active_file"] == "/src/foo.py"

    def test_prefers_resolved_workspace_over_workspace_path(self):
        ctx = ConversationContext()
        rt = SimpleNamespace(
            channel="ide",
            workspace_path="/old/path",
            resolved_workspace="/resolved/path",
        )
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts["workspace"] == "/resolved/path"

    def test_uses_source_channel_fallback(self):
        ctx = ConversationContext()
        rt = SimpleNamespace(source_channel="discord")  # pas de 'channel'
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)
        loop._feed_structured_facts_from_runtime()
        assert ctx.structured_state.established_facts["channel"] == "discord"


# ── Tests: _infer_intent_from_query ──────────────────────────────────────────

class TestInferIntentFromQuery:
    def test_discord(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("crée un salon discord") == "discord"

    def test_code_edit(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("modifie le fichier main.py") == "code_edit"

    def test_web_search(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("recherche la doc de pytest") == "web_search"

    def test_question(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("comment ça marche ?") == "question"

    def test_create_project(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("génère un projet Flask") == "create_project"

    def test_returns_none_on_ambiguous(self):
        from src.reasoning.react import ReActLoop
        assert ReActLoop._infer_intent_from_query("ok") is None

    def test_no_overwrite_if_already_set(self):
        ctx = ConversationContext()
        ctx.structured_state.last_intent = "existing"
        loop = _make_loop(conversation_context=ctx)
        loop._feed_structured_intent(None)  # None → no-op
        assert ctx.structured_state.last_intent == "existing"


# ── Tests: _reset_structured_pending ─────────────────────────────────────────

class TestResetStructuredPending:
    def test_clears_pending_questions(self):
        ctx = ConversationContext()
        ctx.structured_state.add_pending_question("Q1?")
        ctx.structured_state.add_pending_question("Q2?")
        loop = _make_loop(conversation_context=ctx)
        loop._reset_structured_pending()
        assert ctx.structured_state.pending_questions == []

    def test_no_crash_without_context(self):
        loop = _make_loop(conversation_context=None)
        loop._reset_structured_pending()  # must not raise

    def test_does_not_touch_other_fields(self):
        ctx = ConversationContext()
        ctx.structured_state.last_intent = "code_edit"
        ctx.structured_state.record_tool("write_file")
        ctx.structured_state.add_pending_question("Q?")
        loop = _make_loop(conversation_context=ctx)
        loop._reset_structured_pending()
        # Seules les pending_questions sont effacées
        assert ctx.structured_state.last_intent == "code_edit"
        assert list(ctx.structured_state.recent_tools) == ["write_file"]
        assert ctx.structured_state.pending_questions == []


# ── Tests: integration scenario ──────────────────────────────────────────────

class TestIntegrationScenario:
    def test_full_feeding_scenario(self):
        """Simule un mini-cycle : intent → tools → clarification."""
        ctx = ConversationContext()
        rt = SimpleNamespace(channel="ide", workspace_path="/tmp/project")
        loop = _make_loop(conversation_context=ctx, runtime_ctx=rt)

        # 1. Run start : facts + intent
        loop._feed_structured_facts_from_runtime()
        loop._feed_structured_intent("code_edit")

        # 2. Tool executions
        loop._feed_structured_tool("read_file")
        loop._feed_structured_tool("edit_file")
        loop._feed_structured_tool("write_file")

        # 3. Clarification
        loop._feed_structured_clarification("Quel framework utiliser ?")

        # Verify
        ss = ctx.structured_state
        assert ss.last_intent == "code_edit"
        assert list(ss.recent_tools) == ["read_file", "edit_file", "write_file"]
        assert ss.established_facts == {"channel": "ide", "workspace": "/tmp/project"}
        assert ss.pending_questions == ["Quel framework utiliser ?"]
        assert not ss.is_empty()

    def test_serialization_after_feeding(self):
        """Le structured_state reste sérialisable après alimentation."""
        import json
        ctx = ConversationContext()
        loop = _make_loop(
            conversation_context=ctx,
            runtime_ctx=SimpleNamespace(channel="web", workspace_path="/app"),
        )
        loop._feed_structured_facts_from_runtime()
        loop._feed_structured_intent("question")
        loop._feed_structured_tool("web_search")
        d = ctx.structured_state.to_dict()
        json.dumps(d)  # must not raise
        restored = StructuredState.from_dict(d)
        assert restored.last_intent == "question"
        assert list(restored.recent_tools) == ["web_search"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
