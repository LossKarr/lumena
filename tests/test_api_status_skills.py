from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests._server_compat import server_module


class _FakeLumenaSkills:
    def __init__(self):
        self.repo_map = None
        self.code_index = None
        self.rules_loader = None
        self.hook_system = None
        self.instinct_system = None
        self.emotion_manager = SimpleNamespace(get_stats=lambda: {"mood": "neutral", "energy": "medium"})
        self.memory = SimpleNamespace(get_stats=lambda: {"count": 12})
        self._skills = {"pdf": "s1", "docx": "s2", "xlsx": "s3"}
        self.skills_auto_activation = True

    def get_last_active_skills(self):
        return ["pdf", "docx"]


@pytest.mark.asyncio
async def test_api_status_exposes_skills_runtime_fields(monkeypatch):
    monkeypatch.setattr(server_module, "lumena", _FakeLumenaSkills())
    monkeypatch.setattr(server_module, "telegram_channel", None)

    payload = await server_module.get_status()

    assert payload["status"] == "ok"
    assert payload["skills_loaded"] == 3
    assert payload["skills_last_active"] == ["pdf", "docx"]
    assert payload["skills_auto_activation"] is True
