from __future__ import annotations

import time

from src.training.personal.capture import ExperienceCapture
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.policy import PersonalLearningPolicy


def _capture(tmp_path, *, queue_size=16, autostart=True):
    return ExperienceCapture(
        ExperienceStore(tmp_path),
        PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True),
        queue_size=queue_size,
        autostart=autostart,
    )


def test_capture_is_non_blocking_redacted_and_links_feedback(tmp_path) -> None:
    capture = _capture(tmp_path)
    started = time.perf_counter()
    receipt = capture.submit(
        owner_scope="owner:local",
        source_surface="web",
        mode="chat",
        goal="help",
        messages=[
            {"role": "user", "content": "clé api_key=SECRET_CAPTURE_LEAK_123456789"},
            {"role": "assistant", "content": "done"},
        ],
        conversation_id="conv-1",
    )
    elapsed = time.perf_counter() - started
    assert receipt.accepted is True
    assert elapsed < 0.1
    assert capture.wait_until_idle()
    stored = capture.store.get("owner:local", receipt.experience_id)
    assert stored is not None
    assert "SECRET_CAPTURE_LEAK" not in str(stored.to_dict())
    assert capture.attach_feedback(
        owner_scope="owner:local", source_surface="web", conversation_id="conv-1", feedback={"rating": -1}
    ) is True
    capture.close()


def test_capture_overflow_never_blocks_response_path(tmp_path) -> None:
    capture = _capture(tmp_path, queue_size=1, autostart=False)
    first = capture.submit(owner_scope="owner:local", source_surface="agent", mode="agent", goal="one", messages=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "ok"}])
    second = capture.submit(owner_scope="owner:local", source_surface="agent", mode="agent", goal="two", messages=[{"role": "user", "content": "two"}, {"role": "assistant", "content": "ok"}])
    assert first.accepted is True
    assert second.status_code == "capture_queue_full"
    assert capture.metrics()["dropped"] == 1


def test_capture_policy_excludes_internal_probes(tmp_path) -> None:
    capture = _capture(tmp_path)
    receipt = capture.submit(owner_scope="owner:local", source_surface="web", mode="chat", goal="probe", messages=[{"role": "user", "content": "probe"}, {"role": "assistant", "content": "ok"}], internal=True)
    assert receipt.accepted is False
    assert receipt.status_code == "internal_activity_excluded"
    capture.close()
