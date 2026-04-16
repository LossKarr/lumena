from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import ToolRegistry, ReActLoop


class _DummyTools:
    def get_tools_description(self) -> str:
        return "- cursor_ide_local(action, workspace_path, create_if_missing)"

    async def execute(self, _name: str, _args):
        return "ok"


async def _dummy_llm(_messages, **kwargs):
    return "ACTION: FINAL\nACTION_INPUT: done"


def test_cursor_ide_tool_registered():
    registry = ToolRegistry()
    assert "cursor_ide_local" in registry.tools


@pytest.mark.asyncio
async def test_cursor_ide_status_handler(monkeypatch, tmp_path: Path):
    registry = ToolRegistry(lumena_root=tmp_path)

    fake_ide = tmp_path / "cursor-ide-local"
    fake_ide.mkdir(parents=True, exist_ok=True)
    (fake_ide / "package.json").write_text("{}", encoding="utf-8")

    import src.reasoning.handlers.computer_use as _cu_mod
    monkeypatch.setattr(_cu_mod, "_resolve_cursor_ide_root", lambda _ctx: fake_ide)
    monkeypatch.setattr(
        _cu_mod,
        "_cursor_ide_status",
        lambda: {"running": True, "process_count": 2, "processes": []},
    )

    obs = await registry.execute("cursor_ide_local", {"action": "status"})
    assert "running=True" in obs.content
    assert "process_count=2" in obs.content


def test_react_prompt_adds_cursor_ide_rule_for_project_requests():
    """P2: cursor_ide_context section removed — the static cursor IDE injection
    no longer appears for all project requests. It is now replaced by
    ide_runtime_context which only appears when self.tools.ide_context is set."""
    loop = ReActLoop(llm_chat_func=_dummy_llm, tools=_DummyTools())

    # Without active IDE tools context: no cursor IDE section injected
    prompt_no_ide = loop._build_react_prompt("cree un projet web fullstack")
    assert "PRIORITE IDE LOCAL (cursor-ide-local)" not in prompt_no_ide
    assert "ACTION: cursor_ide_local" not in prompt_no_ide

    # Verify the new ide_runtime_context key exists in the prompt builder
    # (it appears only when tools.ide_context["workspace_path"] is set)
    from src.reasoning.react import ReActLoop as _RL
    import inspect
    src = inspect.getsource(_RL._build_react_prompt)
    assert "ide_runtime_context" in src, "ide_runtime_context must be in _build_react_prompt"
