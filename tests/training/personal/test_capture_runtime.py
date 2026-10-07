from src.training.personal.capture_runtime import PersonalCaptureRuntime
from src.training.personal.policy import PersonalLearningPolicy


def test_runtime_reloads_policy_and_captures_channel_without_blocking_contract(tmp_path) -> None:
    runtime = PersonalCaptureRuntime(tmp_path)
    disabled = runtime.submit_conversation(
        user_message="Construis cette fonctionnalité complète",
        response="La fonctionnalité est terminée et vérifiée.",
        source_surface="telegram",
        mode="agent",
        model_used="teacher",
        provider="provider",
    )
    assert disabled.accepted is False
    runtime.policy_store.save(PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True))
    accepted = runtime.submit_conversation(
        user_message="Construis cette fonctionnalité complète",
        response="La fonctionnalité est terminée et vérifiée.",
        source_surface="telegram",
        mode="agent",
        model_used="teacher",
        provider="provider",
        react_meta={"tools_used": ["write_file"], "plan": {"total_tasks": 1, "completed_tasks": 1}},
    )
    assert accepted.accepted is True
    assert runtime._capture.wait_until_idle(2)
    stored = runtime.store.get("owner:local", accepted.experience_id)
    assert stored is not None
    assert stored.source_surface == "telegram"
    assert stored.mode == "agent"
    assert stored.actions == ({"tool": "write_file"},)
    assert stored.result["success"] is True
