from src.runtime.channel_envelope import ChannelContinuityRegistry, ChannelEnvelope


def test_channel_envelope_builds_stable_payload():
    envelope = ChannelEnvelope.from_request(
        channel="IDE",
        client="cursor-ide-local",
        request_id=None,
        conversation_id=None,
        message_id=None,
        task_id="task_abc",
        client_caps={"diff": True},
    )

    payload = envelope.to_dict()
    assert payload["channel"] == "ide"
    assert payload["client"] == "cursor-ide-local"
    assert payload["task_id"] == "task_abc"
    assert payload["client_caps"]["diff"] is True
    assert payload["request_id"].startswith("req_")
    assert payload["conversation_id"].startswith("conv_")
    assert payload["message_id"].startswith("msg_")
    assert payload["conversation_source"] == "generated"


def test_channel_continuity_registry_prefers_explicit_then_task():
    registry = ChannelContinuityRegistry()

    first = ChannelEnvelope.from_request(
        channel="web",
        client="omni-client",
        request_id="req_1",
        conversation_id=None,
        message_id="msg_1",
        task_id="task_shared",
        client_caps={"session_id": "A"},
    )
    first_resolved = registry.resolve(first)
    auto_conversation = first_resolved.conversation_id
    assert first_resolved.conversation_source == "generated"

    explicit = ChannelEnvelope.from_request(
        channel="ide",
        client="omni-client",
        request_id="req_2",
        conversation_id="conv_manual",
        message_id="msg_2",
        task_id="task_shared",
        client_caps={"session_id": "A"},
    )
    explicit_resolved = registry.resolve(explicit)
    assert explicit_resolved.conversation_id == "conv_manual"
    assert explicit_resolved.conversation_source == "explicit"

    follow_up = ChannelEnvelope.from_request(
        channel="telegram",
        client="omni-client",
        request_id="req_3",
        conversation_id=None,
        message_id="msg_3",
        task_id="task_shared",
        client_caps={"session_id": "A"},
    )
    follow_up_resolved = registry.resolve(follow_up)
    assert follow_up_resolved.conversation_id == "conv_manual"
    assert follow_up_resolved.conversation_source == "task"
    assert follow_up_resolved.conversation_id != auto_conversation

    stats = registry.stats()
    assert stats["source_hits"]["explicit"] == 1
    assert stats["source_hits"]["task"] == 1
    assert stats["task_rebinds"] >= 1


def test_channel_continuity_registry_client_session_cross_channel():
    registry = ChannelContinuityRegistry()

    web_envelope = ChannelEnvelope.from_request(
        channel="web",
        client="omni-client",
        request_id="req_web",
        conversation_id=None,
        message_id="msg_web",
        task_id=None,
        client_caps={"session_id": "session-1"},
    )
    web_resolved = registry.resolve(web_envelope)

    ide_envelope = ChannelEnvelope.from_request(
        channel="ide",
        client="omni-client",
        request_id="req_ide",
        conversation_id=None,
        message_id="msg_ide",
        task_id=None,
        client_caps={"session_id": "session-1"},
    )
    ide_resolved = registry.resolve(ide_envelope)
    assert ide_resolved.conversation_id == web_resolved.conversation_id
    assert ide_resolved.conversation_source == "client_session"

    second_session = ChannelEnvelope.from_request(
        channel="telegram",
        client="omni-client",
        request_id="req_tg",
        conversation_id=None,
        message_id="msg_tg",
        task_id=None,
        client_caps={"session_id": "session-2"},
    )
    tg_resolved = registry.resolve(second_session)
    assert tg_resolved.conversation_id != web_resolved.conversation_id


def test_channel_continuity_registry_prunes_over_max_records():
    registry = ChannelContinuityRegistry(max_records=100)
    created_ids = []
    for index in range(140):
        envelope = ChannelEnvelope.from_request(
            channel="web",
            client=f"client-{index}",
            request_id=f"req_{index}",
            conversation_id=f"conv_{index}",
            message_id=f"msg_{index}",
            task_id=None,
            client_caps={"session_id": f"session-{index}"},
        )
        resolved = registry.resolve(envelope)
        created_ids.append(resolved.conversation_id)

    stats = registry.stats()
    assert stats["records_total"] <= 100
    assert registry.get_conversation(created_ids[0]) is None
    assert registry.get_conversation(created_ids[-1]) is not None
