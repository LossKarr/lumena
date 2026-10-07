"""Process singleton that bridges existing channels into canonical capture."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from src.utils.paths import DATA_DIR

from .capture import CaptureReceipt, ExperienceCapture
from .experience_store import ExperienceStore
from .policy import PersonalLearningPolicyStore


class PersonalCaptureRuntime:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or (DATA_DIR / "personal_model"))
        self.policy_store = PersonalLearningPolicyStore(self.root / "config" / "policy.json")
        self.store = ExperienceStore(self.root)
        self._lock = RLock()
        self._policy_key = ""
        self._capture: ExperienceCapture | None = None

    def _current(self) -> ExperienceCapture:
        policy = self.policy_store.load()
        key = repr(policy.to_dict())
        with self._lock:
            if self._capture is None or key != self._policy_key:
                previous = self._capture
                self._capture = ExperienceCapture(self.store, policy)
                self._policy_key = key
                if previous is not None:
                    previous.close(drain=False, timeout=0.05)
            return self._capture

    def submit_conversation(
        self,
        *,
        user_message: str,
        response: str,
        source_surface: str,
        mode: str,
        model_used: str,
        provider: str,
        conversation_id: str = "",
        react_meta: dict[str, Any] | None = None,
        quality_flag: str = "unreviewed",
    ) -> CaptureReceipt:
        meta = dict(react_meta or {})
        plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
        incomplete = bool(meta.get("agent_output_incomplete"))
        actions = [{"tool": str(name)[:96]} for name in (meta.get("tools_used") or []) if isinstance(name, str)]
        evidence = []
        if plan:
            evidence.append({"kind": "plan_progress", "total": int(plan.get("total_tasks", 0) or 0), "done": int(plan.get("completed_tasks", 0) or 0)})
        return self._current().submit(
            owner_scope="owner:local",
            source_surface=(source_surface or "web").strip().lower(),
            mode=(mode or "chat").strip().lower(),
            goal=user_message,
            messages=[{"role": "user", "content": user_message}, {"role": "assistant", "content": response}],
            public_context={"conversation_id": conversation_id} if conversation_id else {},
            actions=actions,
            result={"success": not incomplete, "status": "completed" if not incomplete else "incomplete"},
            evidence=evidence,
            feedback={"quality_flag": "incomplete" if incomplete else str(quality_flag or "unreviewed")[:64]},
            teacher_provider=provider,
            teacher_model=model_used,
            conversation_id=conversation_id,
            internal=user_message.startswith("[INTERNAL_"),
        )

    def metrics(self) -> dict[str, int]:
        capture = self._capture
        return capture.metrics() if capture else {"submitted": 0, "stored": 0, "duplicate": 0, "dropped": 0, "failed": 0, "excluded": 0}


_RUNTIME: PersonalCaptureRuntime | None = None
_RUNTIME_LOCK = RLock()


def get_personal_capture_runtime() -> PersonalCaptureRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = PersonalCaptureRuntime()
        return _RUNTIME
