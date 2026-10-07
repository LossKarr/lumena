"""Non-blocking omnichannel capture into the canonical experience store."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any

from .contracts import LearningExperienceV1, new_id, utc_now
from .experience_store import AppendResult, ExperienceStore
from .policy import PersonalLearningPolicy
from .redaction import redact_value


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    accepted: bool
    status_code: str
    experience_id: str = ""


class ExperienceCapture:
    def __init__(
        self,
        store: ExperienceStore,
        policy: PersonalLearningPolicy,
        *,
        queue_size: int = 1024,
        autostart: bool = True,
    ) -> None:
        self.store = store
        self.policy = policy
        self._queue: queue.Queue[LearningExperienceV1] = queue.Queue(maxsize=max(1, queue_size))
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_by_channel: dict[tuple[str, str, str], str] = {}
        self._metrics_lock = threading.Lock()
        self._metrics = {"submitted": 0, "stored": 0, "duplicate": 0, "dropped": 0, "failed": 0, "excluded": 0}
        if autostart:
            self.start()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="lumena-personal-capture", daemon=True)
        self._worker.start()

    def _increment(self, key: str) -> None:
        with self._metrics_lock:
            self._metrics[key] += 1

    def submit(
        self,
        *,
        owner_scope: str,
        source_surface: str,
        mode: str,
        goal: str,
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        public_context: dict[str, Any] | None = None,
        tool_schemas: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        observations: list[dict[str, Any]] | None = None,
        result: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        feedback: dict[str, Any] | None = None,
        teacher_provider: str = "",
        teacher_model: str = "",
        license_policy: str = "user_owned",
        project_id: str = "",
        conversation_id: str = "",
        internal: bool = False,
    ) -> CaptureReceipt:
        decision = self.policy.capture_decision(
            source_surface=source_surface,
            project_id=project_id,
            conversation_id=conversation_id,
            internal=internal,
        )
        if not decision.allowed:
            self._increment("excluded")
            return CaptureReceipt(False, decision.reason_code)

        payload = redact_value({
            "goal": goal,
            "public_context": public_context or {},
            "messages": list(messages),
            "tool_schemas": tool_schemas or [],
            "actions": actions or [],
            "observations": observations or [],
            "result": result or {},
            "evidence": evidence or [],
            "feedback": feedback or {},
        }).value
        experience_id = new_id("exp")
        experience = LearningExperienceV1(
            experience_id=experience_id,
            owner_scope=owner_scope,
            source_surface=source_surface,
            mode=mode,
            created_at=utc_now(),
            goal=payload["goal"],
            public_context=payload["public_context"],
            messages=tuple(payload["messages"]),
            tool_schemas=tuple(payload["tool_schemas"]),
            actions=tuple(payload["actions"]),
            observations=tuple(payload["observations"]),
            result=payload["result"],
            evidence=tuple(payload["evidence"]),
            feedback=payload["feedback"],
            teacher_provider=teacher_provider,
            teacher_model=teacher_model,
            privacy_state="redacted",
            license_policy=license_policy,
        )
        try:
            self._queue.put_nowait(experience)
        except queue.Full:
            self._increment("dropped")
            return CaptureReceipt(False, "capture_queue_full")
        self._increment("submitted")
        if conversation_id:
            self._last_by_channel[(owner_scope, source_surface, conversation_id)] = experience_id
        return CaptureReceipt(True, "queued", experience_id)

    def attach_feedback(
        self,
        *,
        owner_scope: str,
        source_surface: str,
        conversation_id: str,
        feedback: dict[str, Any],
    ) -> bool:
        experience_id = self._last_by_channel.get((owner_scope, source_surface, conversation_id))
        if not experience_id:
            return False
        redacted = redact_value(feedback).value
        try:
            self.store.append_annotation(owner_scope, experience_id, kind="feedback", payload=redacted)
            return True
        except KeyError:
            return False

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                experience = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                outcome: AppendResult = self.store.append(experience)
                self._increment("stored" if outcome.appended else "duplicate")
            except Exception:
                self._increment("failed")
            finally:
                self._queue.task_done()

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        completed = threading.Event()

        def waiter() -> None:
            self._queue.join()
            completed.set()

        threading.Thread(target=waiter, daemon=True).start()
        return completed.wait(max(0.0, timeout))

    def close(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        if drain:
            self.wait_until_idle(timeout)
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=max(0.0, timeout))

    def metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return dict(self._metrics)
