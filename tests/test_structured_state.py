"""
🧪 Tests — StructuredState (V1)

Couvre :
- StructuredState dataclass : defaults, mutations, sérialisation, round-trip
- Intégration dans ConversationContext (parallel, non-breaking)
- Persistance via IdentityService (Web, Discord, Telegram, WhatsApp)
"""

import json
import pytest

from src.structured_state import StructuredState


# ── StructuredState dataclass ────────────────────────────────────────────────

class TestStructuredStateDefaults:
    def test_defaults(self):
        s = StructuredState()
        assert s.last_intent is None
        assert len(s.recent_tools) == 0
        assert s.established_facts == {}
        assert s.pending_questions == []
        assert s.is_empty() is True

    def test_is_empty_after_mutation(self):
        s = StructuredState()
        s.last_intent = "code_edit"
        assert s.is_empty() is False


class TestStructuredStateMutations:
    def test_record_tool(self):
        s = StructuredState(max_recent_tools=3)
        s.record_tool("write_file")
        s.record_tool("read_file")
        s.record_tool("edit_file")
        s.record_tool("grep_search")
        assert list(s.recent_tools) == ["read_file", "edit_file", "grep_search"]

    def test_set_and_remove_fact(self):
        s = StructuredState()
        s.set_fact("workspace", "/home/user/project")
        assert s.established_facts["workspace"] == "/home/user/project"
        s.remove_fact("workspace")
        assert "workspace" not in s.established_facts

    def test_pending_questions(self):
        s = StructuredState()
        s.add_pending_question("Quel fichier modifier ?")
        s.add_pending_question("Quel fichier modifier ?")  # duplicate
        assert len(s.pending_questions) == 1
        assert s.resolve_pending_question("Quel fichier modifier ?") is True
        assert len(s.pending_questions) == 0
        assert s.resolve_pending_question("non-existent") is False

    def test_clear_pending_questions(self):
        s = StructuredState()
        s.add_pending_question("Q1")
        s.add_pending_question("Q2")
        s.clear_pending_questions()
        assert s.pending_questions == []


class TestStructuredStateSerialization:
    def test_to_dict(self):
        s = StructuredState()
        s.last_intent = "question"
        s.record_tool("read_file")
        s.set_fact("lang", "python")
        s.add_pending_question("What version?")
        d = s.to_dict()
        assert d["last_intent"] == "question"
        assert d["recent_tools"] == ["read_file"]
        assert d["established_facts"] == {"lang": "python"}
        assert d["pending_questions"] == ["What version?"]

    def test_to_dict_is_json_safe(self):
        s = StructuredState()
        s.record_tool("write_file")
        s.set_fact("key", "value")
        json.dumps(s.to_dict())  # must not raise

    def test_from_dict_round_trip(self):
        original = StructuredState()
        original.last_intent = "code_edit"
        original.record_tool("edit_file")
        original.set_fact("workspace", "/tmp/test")
        original.add_pending_question("Ready?")

        d = original.to_dict()
        restored = StructuredState.from_dict(d)

        assert restored.last_intent == "code_edit"
        assert list(restored.recent_tools) == ["edit_file"]
        assert restored.established_facts == {"workspace": "/tmp/test"}
        assert restored.pending_questions == ["Ready?"]

    def test_from_dict_tolerant(self):
        s = StructuredState.from_dict({})
        assert s.is_empty()
        s2 = StructuredState.from_dict({"last_intent": "test"})
        assert s2.last_intent == "test"
        assert s2.recent_tools == __import__("collections").deque()

    def test_from_dict_bad_input(self):
        s = StructuredState.from_dict(None)
        assert s.is_empty()
        s2 = StructuredState.from_dict("not a dict")
        assert s2.is_empty()


# ── ConversationContext integration ──────────────────────────────────────────

class TestConversationContextIntegration:
    def test_has_structured_state(self):
        from src.core import ConversationContext
        ctx = ConversationContext()
        assert hasattr(ctx, 'structured_state')
        assert isinstance(ctx.structured_state, StructuredState)

    def test_structured_state_independent_of_messages(self):
        from src.core import ConversationContext
        ctx = ConversationContext()
        ctx.add_message("user", "hello")
        ctx.structured_state.last_intent = "greeting"
        ctx.structured_state.record_tool("search")
        # Messages are separate
        assert len(ctx.messages) == 1
        assert ctx.structured_state.last_intent == "greeting"

    def test_clear_resets_structured_state(self):
        from src.core import ConversationContext
        ctx = ConversationContext()
        ctx.structured_state.last_intent = "test"
        ctx.structured_state.set_fact("key", "value")
        ctx.clear()
        assert ctx.structured_state.is_empty()

    def test_get_history_for_llm_unchanged(self):
        from src.core import ConversationContext
        ctx = ConversationContext()
        ctx.add_message("user", "hello")
        ctx.add_message("assistant", "hi")
        ctx.structured_state.last_intent = "greeting"
        history = ctx.get_history_for_llm()
        # structured_state must NOT appear in LLM history
        assert len(history) == 2
        assert all("structured_state" not in str(h) for h in history)


# ── Persistence round-trip via IdentityService ───────────────────────────────

class TestPersistenceRoundTrip:
    def _make_identity_service(self, tmp_path):
        """Creates a minimal IdentityService with data_dir pointing to tmp_path."""
        from collections import OrderedDict
        from types import SimpleNamespace

        ctx = SimpleNamespace(
            data_dir=tmp_path,
            llm=None,
            memory=None,
            tts=None,
            emotion_manager=None,
            tool_system=None,
            repo_map=None,
            code_index=None,
            rules_loader=None,
            hook_system=None,
            instinct_system=None,
            auto_speak=False,
        )
        from src.core_services.identity_service import IdentityService
        return IdentityService(
            ctx,
            tg_contexts=OrderedDict(),
            discord_contexts=OrderedDict(),
            discord_users={},
        )

    def test_web_context_persistence(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        ctx = svc._load_web_context()
        ctx.add_message("user", "test")
        ctx.structured_state.last_intent = "question"
        ctx.structured_state.set_fact("lang", "python")
        svc._save_web_context(ctx)

        # Reload
        svc2 = self._make_identity_service(tmp_path)
        ctx2 = svc2._load_web_context()
        assert len(ctx2.messages) == 1
        assert ctx2.structured_state.last_intent == "question"
        assert ctx2.structured_state.established_facts["lang"] == "python"

    def test_web_context_no_state_file_when_empty(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        ctx = svc._load_web_context()
        ctx.add_message("user", "test")
        # Don't set any structured_state
        svc._save_web_context(ctx)
        state_file = tmp_path / "web_contexts" / "default.state.json"
        assert not state_file.exists()

    def test_discord_context_persistence(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        ctx = svc._load_discord_user_context("user_1", "chan_1", "Alice")
        ctx.add_message("user", "hello")
        ctx.structured_state.last_intent = "code_edit"
        ctx.structured_state.record_tool("write_file")
        svc._save_discord_user_context("user_1", "chan_1")

        # Reload with fresh service
        svc2 = self._make_identity_service(tmp_path)
        ctx2 = svc2._load_discord_user_context("user_1", "chan_1", "Alice")
        assert ctx2.structured_state.last_intent == "code_edit"
        assert list(ctx2.structured_state.recent_tools) == ["write_file"]

    def test_telegram_context_persistence(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        ctx = svc._load_tg_context("tg_123")
        ctx.add_message("user", "salut")
        ctx.structured_state.add_pending_question("Quel projet ?")
        svc._save_tg_context("tg_123", ctx)

        svc2 = self._make_identity_service(tmp_path)
        ctx2 = svc2._load_tg_context("tg_123")
        assert ctx2.structured_state.pending_questions == ["Quel projet ?"]

    def test_whatsapp_context_persistence(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        ctx = svc._load_wa_context("+33600000000")
        ctx.structured_state.set_fact("name", "Bob")
        svc._save_wa_context("+33600000000", ctx)

        svc2 = self._make_identity_service(tmp_path)
        ctx2 = svc2._load_wa_context("+33600000000")
        assert ctx2.structured_state.established_facts["name"] == "Bob"

    def test_corrupted_state_file_ignored(self, tmp_path):
        svc = self._make_identity_service(tmp_path)
        # Create a corrupted state file
        state_dir = tmp_path / "web_contexts"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "default.state.json").write_text("{broken", encoding="utf-8")
        # Load should not crash — just use empty state
        ctx = svc._load_web_context()
        assert ctx.structured_state.is_empty()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
