
from src.local_models.identifiers import parse_model_reference
from src.local_models.state_store import LocalModelStateStore


def test_activation_round_trip_and_disable_keeps_installed(tmp_path):
    store = LocalModelStateStore(tmp_path / "state.json")
    ref = parse_model_reference("qwen3:8b")
    store.record_installed(ref, digest="abc", size_bytes=10)
    store.set_enabled(ref, True, verified=True)
    store.set_enabled(ref, False)
    entry = store.entry(ref)
    assert entry["installed"] is True
    assert entry["enabled"] is False
    assert entry["verified"] is True


def test_corrupt_store_fails_to_empty_versioned_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    state = LocalModelStateStore(path).load()
    assert state["version"] == 1
    assert state["models"] == {}


def test_record_absent_clears_assignment(tmp_path):
    store = LocalModelStateStore(tmp_path / "state.json")
    ref = parse_model_reference("qwen3:8b")
    store.record_installed(ref)
    store.assign("primary", ref)
    store.record_absent(ref)
    assert store.load()["assignments"] == {}
