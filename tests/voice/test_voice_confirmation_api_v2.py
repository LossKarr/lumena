import pytest
from fastapi import HTTPException

from src.runtime.voice_security import get_voice_confirmation_broker
from web.routes.advanced import approve_voice_confirmation, list_voice_confirmations


@pytest.mark.asyncio
async def test_admin_api_lists_and_approves_exact_pending_request():
    broker = get_voice_confirmation_broker()
    broker.clear()
    request_id = broker.request_confirmation(
        conversation_id="voice-api", tool_name="delete_file",
        arguments={"path": "x.txt"}, ttl_s=60,
    )
    listed = await list_voice_confirmations()
    assert listed["requests"][0]["request_id"] == request_id
    approved = await approve_voice_confirmation(request_id)
    assert approved == {"approved": True, "request_id": request_id}
    broker.clear()


@pytest.mark.asyncio
async def test_admin_api_rejects_missing_or_replayed_request():
    broker = get_voice_confirmation_broker()
    broker.clear()
    with pytest.raises(HTTPException) as exc:
        await approve_voice_confirmation("voice_confirm_missing")
    assert exc.value.status_code == 404
