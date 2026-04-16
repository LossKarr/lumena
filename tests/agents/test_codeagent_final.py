"""Tests pour PLAN_CODEAGENT_FINAL — F2 (split), F3 (LRU), F4 (brackets), F5 (cache)."""
import json
import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.sub_agent import (
    CodeAgent,
    AgentTask,
    AgentType,
    ActionResult,
    StatusCode,
    _count_brackets_clean,
    _WEB_BRACKET_EXTS,
)


def _make_agent() -> CodeAgent:
    """Créer un CodeAgent correctement initialisé pour les tests."""
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = None
    agent._session_memory = {
        "files_read": {},
        "errors_seen": [],
        "edits_done": [],
    }
    agent._session_memory_last_used = 0.0
    agent._SESSION_MEMORY_TTL = 4 * 3600
    return agent


# ── F3: Tests LRU session memory ──


class TestF3SessionMemoryLRU:
    """La session memory utilise LRU au lieu de FIFO."""

    def test_lru_keeps_recent(self):
        """Lire A..M → A éjecté (le plus ancien non reaccédé), M présent."""
        agent = _make_agent()
        for letter in "ABCDEFGHIJKLM":
            agent._record_session_read(letter, f"content_{letter}")
        files = agent._session_memory["files_read"]
        assert len(files) == 12
        assert "A" not in files, "A devrait être éjecté (premier inséré, jamais réaccédé)"
        assert "M" in files, "M devrait être présent (dernier inséré)"

    def test_lru_refresh_keeps_old(self):
        """Lire A..L, puis relire A → A reste quand M arrive."""
        agent = _make_agent()
        for letter in "ABCDEFGHIJKL":
            agent._record_session_read(letter, f"content_{letter}")
        # Refresh A → A passe en fin de dict (accès récent)
        agent._record_session_read("A", "content_A_refreshed")
        # Ajouter M → doit éjecter B (le plus ancien non-refreshé), pas A
        agent._record_session_read("M", "content_M")
        files = agent._session_memory["files_read"]
        assert len(files) == 12
        assert "A" in files, "A devrait rester (refreshé récemment)"
        assert "B" not in files, "B devrait être éjecté (le plus ancien non-refreshé)"

    def test_lru_max_12(self):
        """Lire 14 fichiers → exactement 12 en cache."""
        agent = _make_agent()
        for i in range(14):
            agent._record_session_read(f"file_{i}.py", f"content_{i}")
        files = agent._session_memory["files_read"]
        assert len(files) == 12
        assert "file_0.py" not in files
        assert "file_1.py" not in files
        assert "file_13.py" in files


# ── F4: Tests bracket guard string-aware ──


class TestF4BracketGuard:
    """_count_brackets_clean ignore strings et commentaires."""

    def test_simple_balanced(self):
        code = "function f() { return { a: 1 }; }"
        braces, parens = _count_brackets_clean(code)
        assert braces == 0, "Les braces doivent être équilibrées"

    def test_string_braces_ignored(self):
        code = 'const s = "{ not a real brace }"; function f() {}'
        braces, parens = _count_brackets_clean(code)
        assert braces == 0, "Les braces dans les strings doivent être ignorées"

    def test_comment_braces_ignored(self):
        code = "// { this is a comment }\nfunction f() {}"
        braces, parens = _count_brackets_clean(code)
        assert braces == 0, "Les braces dans les commentaires doivent être ignorées"

    def test_block_comment_ignored(self):
        code = "/* { unbalanced */ function f() { return 1; }"
        braces, parens = _count_brackets_clean(code)
        assert braces == 0

    def test_unbalanced_detected(self):
        code = "function f() { if (x) {"
        braces, parens = _count_brackets_clean(code)
        assert braces > 0, "Braces non fermées doivent donner un net positif"

    def test_web_bracket_exts_complete(self):
        expected = {".js", ".ts", ".jsx", ".tsx", ".vue", ".html", ".htm", ".css"}
        assert _WEB_BRACKET_EXTS == expected


# ── F5: Tests cache _gather_project_context ──


class TestF5ContextCache:
    """Le cache évite les appels répétés à _gather_project_context."""

    @pytest.mark.asyncio
    async def test_cache_hit_no_second_call(self):
        """Un 2e read_file sur le même .py ne rappelle pas _gather_project_context."""
        agent = _make_agent()
        task = AgentTask(
            task_id="t1", description="test task", agent_type=AgentType.CODE,
        )

        call_count = 0
        _orig = agent._gather_project_context

        def counting_gather(*a, **kw):
            nonlocal call_count
            call_count += 1
            return "--- Imports du fichier cible ---\nsome import"

        agent._gather_project_context = counting_gather
        agent._record_session_read = MagicMock()
        agent._record_session_edit = MagicMock()
        agent._record_session_error = MagicMock()
        agent._find_related_tests = MagicMock(return_value=[])
        agent._execute_loop_action = AsyncMock(return_value=ActionResult("ok"))

        context_cache: dict[str, str] = {}
        target_files_seen: list[str] = []

        # Premier appel avec read_file → doit appeler _gather_project_context
        obs1, _, _ = await agent._post_action_hooks(
            action={"action": "read_file", "path": "src/foo.py"},
            action_type="read_file",
            observation=ActionResult("file content"),
            messages=[], task=task,
            session_snapshots={}, target_files_seen=target_files_seen,
            edits_since_last_test=0, reads_since_last_edit=0,
            context_cache=context_cache,
        )
        assert call_count == 1
        assert "src/foo.py" in context_cache

        # Deuxième appel avec le même path → doit utiliser le cache
        target_files_seen_2: list[str] = []
        obs2, _, _ = await agent._post_action_hooks(
            action={"action": "read_file", "path": "src/foo.py"},
            action_type="read_file",
            observation=ActionResult("file content again"),
            messages=[], task=task,
            session_snapshots={}, target_files_seen=target_files_seen_2,
            edits_since_last_test=0, reads_since_last_edit=0,
            context_cache=context_cache,
        )
        assert call_count == 1, "_gather_project_context ne doit PAS être rappelé (cache hit)"


# ── F2: Tests split _single_code_attempt ──


class TestF2SplitMethods:
    """Les 4 méthodes extraites de _single_code_attempt existent et fonctionnent."""

    def test_build_initial_messages_with_workspace(self, tmp_path):
        agent = _make_agent()
        agent._gather_project_context = MagicMock(return_value="")
        task = AgentTask(
            task_id="t1",
            description="fix src/main.py",
            agent_type=AgentType.CODE,
            context={"workspace_path": str(tmp_path)},
        )
        messages, targets, project_files = agent._build_initial_messages(task, [], 1)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "WORKSPACE ACTIF" in messages[1]["content"]

    def test_build_initial_messages_no_workspace(self):
        agent = _make_agent()
        agent._gather_project_context = MagicMock(return_value="")
        task = AgentTask(
            task_id="t2",
            description="do something",
            agent_type=AgentType.CODE,
        )
        with patch("src.utils.project_registry.resolve_workspace",
                   return_value=MagicMock(path=None, intent="unknown", source="fallback", confidence=0.0)):
            messages, targets, project_files = agent._build_initial_messages(task, [], 1)
        assert len(messages) == 2
        assert "WORKSPACE ACTIF" not in messages[1]["content"]

    def test_build_initial_messages_with_prior_failures(self):
        agent = _make_agent()
        agent._gather_project_context = MagicMock(return_value="")
        task = AgentTask(
            task_id="t3",
            description="fix bug",
            agent_type=AgentType.CODE,
        )
        with patch("src.utils.project_registry.resolve_workspace",
                   return_value=MagicMock(path=None, intent="unknown", source="fallback", confidence=0.0)):
            messages, _, _ = agent._build_initial_messages(task, ["Erreur: X not found"], 2)
        assert "Tentatives précédentes" in messages[1]["content"]
        assert "Erreur: X not found" in messages[1]["content"]

    def test_process_llm_response_valid_json(self):
        agent = _make_agent()
        messages, report = [], []
        raw = json.dumps({"action": "read_file", "path": "foo.py"})
        tag, payload = agent._process_llm_response(raw, 1, messages, report)
        assert tag == "action"
        assert payload["action"] == "read_file"

    def test_process_llm_response_done(self):
        agent = _make_agent()
        messages, report = [], []
        raw = json.dumps({"action": "done", "summary": "Terminé"})
        tag, payload = agent._process_llm_response(raw, 1, messages, report)
        assert tag == "done"
        assert payload["summary"] == "Terminé"

    def test_process_llm_response_empty(self):
        agent = _make_agent()
        messages, report = [], []
        tag, payload = agent._process_llm_response("", 1, messages, report)
        assert tag == "continue"
        assert len(messages) == 2, "Doit ajouter 2 messages retry"

    def test_process_llm_response_text_success(self):
        agent = _make_agent()
        agent._result_success = MagicMock()
        messages, report = [], []
        tag, payload = agent._process_llm_response("Just some text response", 1, messages, report)
        assert tag == "success_text"

    def test_process_llm_response_truncated_json(self):
        agent = _make_agent()
        messages, report = [], []
        raw = '{"action": "write_file", "path": "foo.py", "content": "def hello():'
        tag, payload = agent._process_llm_response(raw, 1, messages, report)
        assert tag == "continue"
        assert len(messages) == 2, "Doit ajouter 2 messages retry pour JSON tronqué"

    @pytest.mark.asyncio
    async def test_maybe_compact_under_threshold(self):
        """Peu de messages → pas de compaction."""
        agent = _make_agent()
        agent._session_memory = {"files_read": {}}
        llm = AsyncMock()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "ok"},
        ]
        result = await agent._maybe_compact(messages, llm, [])
        assert result is messages, "Messages ne doivent pas être modifiés"
