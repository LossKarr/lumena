from src.learning import conversation_logger


class _Runtime:
    def __init__(self):
        self.calls = []

    def submit_conversation(self, **kwargs):
        self.calls.append(kwargs)


def test_legacy_logger_bridges_surface_to_personal_capture(tmp_path, monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(conversation_logger, "_POOL_DIR", tmp_path)
    monkeypatch.setattr(conversation_logger, "_rotation_done", True)
    import src.training.personal.capture_runtime as bridge
    monkeypatch.setattr(bridge, "get_personal_capture_runtime", lambda: runtime)
    saved = conversation_logger.queue_conversation(
        "Explique ce résultat de manière détaillée",
        "Voici une réponse complète et suffisamment longue pour être conservée.",
        model_used="model-x",
        provider="provider-x",
        source_surface="voice",
        mode="chat",
        conversation_id="conversation-1",
    )
    assert saved is True
    assert runtime.calls[0]["source_surface"] == "voice"
    assert runtime.calls[0]["conversation_id"] == "conversation-1"
    assert runtime.calls[0]["quality_flag"] == "ok"
