"""Single owner and bounded priority queue for every Voice V2 utterance."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Callable, Optional


SPEECH_PRIORITIES = {
    "security": 100,
    "user": 90,
    "confirmation": 80,
    "final": 70,
    "error": 65,
    "status": 50,
    "milestone": 30,
}


@dataclass
class SpeechRequest:
    text: str
    kind: str
    priority: int
    ttl_s: float
    interruptible: bool
    replace_key: Optional[str]
    turn: Any
    sequence: int
    language: Optional[str] = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    preempted: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    started: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at >= self.ttl_s


class SpeechCoordinator:
    """Serialize speech, preempt by policy and keep memory bounded."""

    def __init__(
        self,
        runtime: Any,
        *,
        stop_fn: Optional[Callable[[], Any]] = None,
        max_queue: int = 16,
        playback_timeout_s: float = 120.0,
    ) -> None:
        self.runtime = runtime
        self._stop_fn = stop_fn
        self.max_queue = max(1, int(max_queue))
        self.playback_timeout_s = max(1.0, float(playback_timeout_s))
        self._queue: list[SpeechRequest] = []
        self._available = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._current: Optional[SpeechRequest] = None
        self._sequence = 0
        self._closing = False
        self.enqueued = 0
        self.spoken = 0
        self.dropped = 0
        self.replaced = 0
        self.preemptions = 0
        self.last_drop_reason: Optional[str] = None

    async def say(
        self,
        text: str,
        *,
        kind: str = "status",
        priority: Optional[int] = None,
        ttl_s: float = 20.0,
        interruptible: bool = True,
        replace_key: Optional[str] = None,
        turn: Any = None,
        language: Optional[str] = None,
    ) -> str:
        clean = str(text or "").strip()
        if not clean or self._closing:
            return ""
        self._sequence += 1
        request = SpeechRequest(
            text=clean,
            kind=kind,
            priority=int(priority if priority is not None else SPEECH_PRIORITIES.get(kind, 40)),
            ttl_s=max(0.1, min(float(ttl_s), 600.0)),
            interruptible=bool(interruptible),
            replace_key=replace_key,
            turn=turn or kind,
            sequence=self._sequence,
            language=str(language).strip().lower() if language else None,
        )
        self._drop_expired()
        if replace_key:
            kept = []
            for queued in self._queue:
                if queued.replace_key == replace_key:
                    self.replaced += 1
                    self.dropped += 1
                    self.last_drop_reason = "replaced"
                else:
                    kept.append(queued)
            self._queue = kept
        if len(self._queue) >= self.max_queue:
            victim = min(self._queue, key=lambda item: (item.priority, item.sequence))
            if victim.priority > request.priority:
                self.dropped += 1
                self.last_drop_reason = "queue_full_lower_priority"
                return ""
            self._queue.remove(victim)
            self.dropped += 1
            self.last_drop_reason = "queue_full"
        current = self._current
        did_preempt = bool(
            current and current.interruptible and request.priority > current.priority
        )
        if did_preempt:
            current.preempted.set()
            self.preemptions += 1
            self._stop_output()
        self._queue.append(request)
        self.enqueued += 1
        self._available.set()
        self._ensure_worker()
        # Give the owner task one scheduling point so an acknowledgement starts
        # before a very fast Agent can enqueue its final result.
        await asyncio.sleep(0)
        if did_preempt:
            try:
                await asyncio.wait_for(request.started.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
        return request.request_id

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="lumena-speech-coordinator")

    def _drop_expired(self) -> None:
        kept = []
        for request in self._queue:
            if request.expired:
                self.dropped += 1
                self.last_drop_reason = "expired"
            else:
                kept.append(request)
        self._queue = kept

    def _pop_next(self) -> Optional[SpeechRequest]:
        self._drop_expired()
        if not self._queue:
            return None
        request = max(self._queue, key=lambda item: (item.priority, -item.sequence))
        self._queue.remove(request)
        return request

    async def _run(self) -> None:
        while not self._closing:
            request = self._pop_next()
            if request is None:
                self._available.clear()
                await self._available.wait()
                continue
            if request.expired:
                self.dropped += 1
                self.last_drop_reason = "expired"
                continue
            self._current = request
            request.started.set()
            generation = ""
            try:
                if request.language:
                    generation = await self.runtime.speak(
                        request.text, turn=request.turn, language=request.language
                    )
                else:
                    generation = await self.runtime.speak(request.text, turn=request.turn)
                if not generation:
                    self.dropped += 1
                    self.last_drop_reason = "runtime_rejected"
                    continue
                wait_finished = getattr(self.runtime, "wait_finished", None)
                if callable(wait_finished):
                    completion = asyncio.create_task(
                        wait_finished(generation, timeout_s=self.playback_timeout_s)
                    )
                    preemption = asyncio.create_task(request.preempted.wait())
                    done, pending = await asyncio.wait(
                        {completion, preemption}, return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if preemption in done and preemption.result():
                        self.dropped += 1
                        self.last_drop_reason = "preempted"
                        continue
                self.spoken += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.dropped += 1
                self.last_drop_reason = "runtime_error"
            finally:
                self._current = None

    def _stop_output(self) -> None:
        if callable(self._stop_fn):
            try:
                self._stop_fn()
            except Exception:
                pass

    def stop_audio(self, *, clear_queue: bool = True) -> None:
        if self._current is not None:
            self._current.preempted.set()
        self._stop_output()
        if clear_queue:
            self.dropped += len(self._queue)
            self._queue.clear()
            self.last_drop_reason = "stopped"

    async def wait_idle(self, timeout_s: float = 3.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._current is None and not self._queue:
                return True
            await asyncio.sleep(0.005)
        return False

    async def aclose(self) -> None:
        self._closing = True
        self.stop_audio(clear_queue=True)
        self._available.set()
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None

    def status(self) -> dict[str, Any]:
        return {
            "queue_depth": len(self._queue),
            "current_kind": self._current.kind if self._current else None,
            "enqueued": self.enqueued,
            "spoken": self.spoken,
            "dropped": self.dropped,
            "replaced": self.replaced,
            "preemptions": self.preemptions,
            "last_drop_reason": self.last_drop_reason,
        }
