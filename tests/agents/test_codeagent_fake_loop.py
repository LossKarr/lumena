import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.codeagent_todo import CodeAgentTodoState
from src.agents.sub_agent import CodeAgent, ActionResult


def _agent(tmp_path):
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path
    agent.workspace_path = str(tmp_path)
    agent._session_memory = {
        "files_read": {},
        "errors_seen": [],
        "edits_done": [],
        "grep_zero_results": {},
    }
    agent._session_memory_last_used = 0.0
    agent._SESSION_MEMORY_TTL = 4 * 3600
    agent._read_count_per_file = {}
    agent._edited_files = set()
    agent._codeagent_todo = CodeAgentTodoState()
    agent._attempt_profile = None
    agent._write_counts = {}
    agent._edit_restricted_files = set()
    agent._self_repair_count = 0
    agent._self_repair_count_per_file = {}
    agent._syntax_clean_snapshot = {}
    agent._check_python_syntax = AsyncMock(return_value="")
    agent._check_python_types = AsyncMock(return_value="")
    agent._check_web_syntax = AsyncMock(return_value="")
    return agent


@pytest.mark.asyncio
async def test_fake_llm_plan_write_file_updates_todo(tmp_path):
    agent = _agent(tmp_path)
    task = MagicMock()
    task.task_id = "fake-loop"
    task.description = "Creer un script simple"

    messages, report = [], []
    raw_actions = [
        {"action": "plan", "steps": ["Creer hello.py", "Terminer"]},
        {"action": "write_file", "path": "hello.py", "content": "print('hello')\n"},
    ]

    last_observation: ActionResult | None = None
    for i, action in enumerate(raw_actions, start=1):
        tag, payload = agent._process_llm_response(json.dumps(action), i, messages, report)
        assert tag == "action"
        last_observation = await agent._execute_loop_action(payload, snapshots={})
        last_observation, _, _ = await agent._post_action_hooks(
            action=payload,
            action_type=payload["action"],
            observation=last_observation,
            messages=messages,
            task=task,
            session_snapshots={},
            target_files_seen=[],
            edits_since_last_test=0,
            reads_since_last_edit=0,
            context_cache={},
        )

    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert last_observation is not None
    assert "TODO_CODEAGENT" in last_observation.full()
    assert "[completed] Creer hello.py" in last_observation.full()
