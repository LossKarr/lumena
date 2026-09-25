from src.local_models.identifiers import parse_model_reference
from src.local_models.registry import register_local_model, unregister_local_model
from src.local_models.state_store import LocalModelStateStore


def test_registry_enable_disable_is_symmetric(monkeypatch, tmp_path):
    import src.llm.providers as providers

    ref = parse_model_reference("test-local-manager:1b")
    key = "test-local-manager-1b"
    store = LocalModelStateStore(tmp_path / "state.json")
    monkeypatch.setattr(providers, "_probe_ollama_model", lambda *a, **k: {"ok": False})
    providers.AVAILABLE_MODELS.pop(key, None)
    providers.MODEL_SKILLS.pop(key, None)
    try:
        assert register_local_model(ref, store) == key
        assert key in providers.AVAILABLE_MODELS
        assert store.is_enabled(ref) is True
        assert unregister_local_model(ref, store) is True
        assert key not in providers.AVAILABLE_MODELS
        assert key not in providers.MODEL_SKILLS
        assert store.is_enabled(ref) is False
    finally:
        providers.AVAILABLE_MODELS.pop(key, None)
        providers.MODEL_SKILLS.pop(key, None)
