from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module
from src.runtime.session_store import SessionStore


@pytest.mark.asyncio
async def test_sessions_api_lists_persisted_sessions(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    store.record_message(
        conversation_id="conv_api_sessions",
        role="user",
        content="Construis un historique de session",
        channel="web",
        client="browser",
    )
    store.record_message(
        conversation_id="conv_api_sessions",
        role="assistant",
        content="Historique enregistre.",
        channel="web",
        client="browser",
        status="done",
        model_used="gpt-test",
        provider_used="openai",
    )
    monkeypatch.setattr(server_module, "_SESSION_STORE", store)

    payload = await server_module.list_sessions()

    assert payload["success"] is True
    assert payload["total"] == 1
    assert payload["sessions"][0]["conversation_id"] == "conv_api_sessions"
    assert payload["stats"]["total"] == 1


@pytest.mark.asyncio
async def test_get_session_includes_persisted_messages_without_orchestrator(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    store.record_message(conversation_id="conv_detail", role="user", content="hello")
    store.record_message(conversation_id="conv_detail", role="assistant", content="bonjour", status="done")

    monkeypatch.setattr(server_module, "_SESSION_STORE", store)
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", False)

    payload = await server_module.get_session("conv_detail", limit=20)

    assert payload["conversation_id"] == "conv_detail"
    assert payload["count"] == 0
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["session"]["message_count"] == 2
