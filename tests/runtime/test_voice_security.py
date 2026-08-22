from src.runtime.voice_security import (
    VoiceConfirmationBroker,
)
from src.reasoning.tool_registry import _voice_confirmation_required


def test_critical_classifier_targets_external_or_destructive_actions():
    assert _voice_confirmation_required("delete_file", semantic_category="files")
    assert _voice_confirmation_required("github_issue_create", semantic_category="github")
    assert _voice_confirmation_required("submit_peer_task", semantic_category="peers")
    assert not _voice_confirmation_required("read_file", semantic_category="files")
    assert not _voice_confirmation_required("mission_status", semantic_category="missions")


def test_confirmation_is_exact_one_shot_and_argument_bound():
    now = [10.0]
    broker = VoiceConfirmationBroker(time_fn=lambda: now[0])
    broker.authorize(
        conversation_id="voice-1", tool_name="delete_file",
        arguments={"path": "a.txt"}, ttl_s=30,
    )
    assert not broker.consume(
        conversation_id="voice-1", tool_name="delete_file",
        arguments={"path": "b.txt"},
    )
    # L'essai erroné consomme l'autorisation : aucun replay avec les bons arguments.
    assert not broker.consume(
        conversation_id="voice-1", tool_name="delete_file",
        arguments={"path": "a.txt"},
    )


def test_confirmation_expires_and_cannot_be_replayed():
    now = [100.0]
    broker = VoiceConfirmationBroker(time_fn=lambda: now[0])
    kwargs = dict(
        conversation_id="voice-2", tool_name="send_mail",
        arguments={"to": "x@example.test"},
    )
    broker.authorize(**kwargs, ttl_s=5)
    now[0] = 106.0
    assert not broker.consume(**kwargs)
    broker.authorize(**kwargs, ttl_s=5)
    assert broker.consume(**kwargs)
    assert not broker.consume(**kwargs)


def test_screen_request_is_deduplicated_then_approved_once():
    now = [20.0]
    broker = VoiceConfirmationBroker(time_fn=lambda: now[0])
    kwargs = dict(
        conversation_id="voice-3", tool_name="publish_site",
        arguments={"target": "prod"},
    )
    first = broker.request_confirmation(**kwargs, ttl_s=60)
    second = broker.request_confirmation(**kwargs, ttl_s=60)
    assert first == second
    assert broker.list_requests()[0]["tool_name"] == "publish_site"
    assert broker.approve(first, ttl_s=10)
    assert broker.list_requests() == []
    assert broker.consume(**kwargs)
    assert not broker.consume(**kwargs)


def test_expired_screen_request_cannot_be_approved():
    now = [30.0]
    broker = VoiceConfirmationBroker(time_fn=lambda: now[0])
    request_id = broker.request_confirmation(
        conversation_id="voice-4", tool_name="delete_file",
        arguments={"path": "x"}, ttl_s=5,
    )
    now[0] = 36.0
    assert not broker.approve(request_id)
    assert broker.list_requests() == []
