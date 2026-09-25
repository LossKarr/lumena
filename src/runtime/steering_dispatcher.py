"""Urgent steering signals and safe-boundary execution state."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, Awaitable, Dict, Iterator, Optional

from .task_steering_store import TaskSteeringStore


class _Interrupted:
    pass


STEERING_INTERRUPTED = _Interrupted()


@dataclass(frozen=True, slots=True)
class ExecutionBoundary:
    phase: str
    safe_boundary: str


class SteeringSignalRegistry:
    """Process-local wakeups only; commands themselves remain persisted."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: Dict[str, Event] = {}
        self._phases: Dict[str, str] = {}

    def _event(self, task_id: str) -> Event:
        with self._lock:
            return self._events.setdefault(task_id, Event())

    def signal(self, task_id: str) -> None:
        self._event(task_id).set()

    def is_set(self, task_id: str) -> bool:
        return self._event(task_id).is_set()

    def clear(self, task_id: str) -> None:
        self._event(task_id).clear()

    def set_phase(self, task_id: str, phase: str) -> None:
        with self._lock:
            self._phases[task_id] = phase

    def boundary(self, task_id: str) -> ExecutionBoundary:
        with self._lock:
            phase = self._phases.get(task_id, "checkpoint")
        boundary = "generation_interruptible" if phase == "llm" else (
            "after_current_operation" if phase == "tool" else "next_checkpoint"
        )
        return ExecutionBoundary(phase, boundary)


SIGNALS = SteeringSignalRegistry()


@contextmanager
def execution_phase(task_id: Optional[str], phase: str) -> Iterator[None]:
    if not task_id:
        yield
        return
    SIGNALS.set_phase(task_id, phase)
    try:
        yield
    finally:
        SIGNALS.set_phase(task_id, "checkpoint")


async def await_interruptible_llm(
    awaitable: Awaitable[Any],
    *,
    task_id: Optional[str],
    timeout: float,
) -> Any:
    """Cancel only the current LLM await when urgent steering is signalled."""
    if not task_id:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    llm_task = asyncio.ensure_future(awaitable)

    async def wait_signal() -> bool:
        while not SIGNALS.is_set(task_id):
            await asyncio.sleep(0.05)
        return True

    signal_task = asyncio.create_task(wait_signal())
    SIGNALS.set_phase(task_id, "llm")
    try:
        done, _pending = await asyncio.wait(
            {llm_task, signal_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            llm_task.cancel()
            signal_task.cancel()
            await asyncio.gather(llm_task, signal_task, return_exceptions=True)
            raise asyncio.TimeoutError()
        if signal_task in done and SIGNALS.is_set(task_id):
            llm_task.cancel()
            await asyncio.gather(llm_task, return_exceptions=True)
            return STEERING_INTERRUPTED
        signal_task.cancel()
        await asyncio.gather(signal_task, return_exceptions=True)
        return await llm_task
    finally:
        SIGNALS.set_phase(task_id, "checkpoint")


class SteeringDispatcher:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.store = TaskSteeringStore(orchestrator)

    def enqueue(self, task_id: str, kind: str, **kwargs: Any) -> Dict[str, Any]:
        command = self.store.enqueue(task_id, kind, **kwargs)
        if (
            command.get("status") == "pending"
            and command.get("delivery_policy") == "urgent_safe_boundary"
        ):
            SIGNALS.signal(task_id)
        boundary = SIGNALS.boundary(task_id)
        return {**command, "safe_boundary": boundary.safe_boundary, "execution_phase": boundary.phase}
