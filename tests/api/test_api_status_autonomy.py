from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module


class _FakeLumena:
	def __init__(self):
		self.repo_map = None
		self.code_index = None
		self.rules_loader = None
		self.hook_system = None
		self.instinct_system = None
		self.memory = SimpleNamespace(get_stats=lambda: {"count": 0})
		self.emotion_manager = SimpleNamespace(get_stats=lambda: {"mood": "neutral", "energy": "high"})
		self._skills = {}
		self.skills_auto_activation = True


class _FakeDaemon:
	running = True

	def get_status(self):
		return {
			"running": True,
			"autonomy_action_execution": True,
			"actions_last_hour": 3,
			"user_present": False,
			"uptime": "0:10:00",
		}


@pytest.mark.asyncio
async def test_api_status_exposes_autonomy_runtime_fields(monkeypatch):
	monkeypatch.setattr(server_module, "lumena", _FakeLumena())
	monkeypatch.setattr(server_module, "telegram_channel", None)
	monkeypatch.setattr(server_module, "AUTONOMY_DAEMON_AVAILABLE", True)
	monkeypatch.setattr(server_module, "AUTONOMY_ON_WEB_ENABLED", True)
	monkeypatch.setattr(server_module, "_AUTONOMY_DAEMON", _FakeDaemon())
	monkeypatch.setattr(server_module, "_AUTONOMY_LAST_ERROR", None)

	payload = await server_module.get_status()

	assert payload["status"] == "ok"
	assert payload["autonomy_enabled_on_web"] is True
	assert payload["autonomy_available"] is True
	assert payload["autonomy_running"] is True
	assert payload["autonomy_action_execution"] is True
	assert payload["autonomy_actions_last_hour"] == 3
