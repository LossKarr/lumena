from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning.react import ReActLoop


class _DummyTools:
    def get_tools_description(self) -> str:
        return "- list_directory: list files"

    async def execute(self, _name: str, _args):
        return "ok"


async def _dummy_llm(_messages, **kwargs):
    return "ACTION: FINAL\nACTION_INPUT: done"


def test_react_prompt_includes_active_skills_context():
    loop = ReActLoop(
        llm_chat_func=_dummy_llm,
        tools=_DummyTools(),
        active_skills_context="## Skills actifs\n- pdf",
    )

    prompt = loop._build_react_prompt("fais un pdf")
    assert "## Skills actifs runtime:" in prompt
    assert "## Skills actifs" in prompt
    assert "- pdf" in prompt
