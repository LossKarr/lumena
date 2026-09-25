"""Bounded dispatch onto the IDE server loop, including foreign agent threads."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class TransportUnavailable(RuntimeError):
    def __init__(self, message: str, *, outcome: str = "not_sent") -> None:
        super().__init__(message)
        self.outcome = outcome


class TransportBusy(RuntimeError):
    pass


@dataclass(eq=False)
class _Ticket:
    result: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    task: asyncio.Task | None = None


class IDELoopDispatcher:
    """Only the owner loop creates tasks or touches their asyncio futures.

    Admission includes callbacks not yet started. A timed-out caller does not
    free its slot until the owner has actually discarded or stopped its work.
    """

    def __init__(self, limit: int = 64) -> None:
        if limit < 1:
            raise ValueError("positive dispatch limit required")
        self._limit = limit
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tickets: set[_Ticket] = set()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._tickets)

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("IDE transport already has an owner")
            self._loop = loop

    def _release(self, ticket: _Ticket) -> None:
        with self._lock:
            self._tickets.discard(ticket)

    @staticmethod
    def _publish(ticket: _Ticket, *, value: Any = None, error: BaseException | None = None) -> None:
        try:
            if error is None:
                ticket.result.set_result(value)
            else:
                ticket.result.set_exception(error)
        except concurrent.futures.InvalidStateError:
            # The caller can cancel concurrently with an owner-loop completion.
            pass

    async def dispatch(self, factory: Callable[[], Awaitable[Any]], timeout: float) -> Any:
        deadline = time.monotonic() + timeout
        ticket = _Ticket()
        with self._lock:
            loop = self._loop
            if loop is None or loop.is_closed() or not loop.is_running():
                raise TransportUnavailable("IDE transport unavailable")
            if len(self._tickets) >= self._limit:
                raise TransportBusy("IDE transport busy")
            self._tickets.add(ticket)

        def finished(task: asyncio.Task) -> None:
            try:
                self._publish(ticket, value=task.result())
            except BaseException as exc:
                self._publish(ticket, error=exc)
            finally:
                self._release(ticket)

        def start() -> None:
            if ticket.result.done():
                self._release(ticket)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._publish(ticket, error=TimeoutError())
                self._release(ticket)
                return

            async def execute() -> Any:
                return await asyncio.wait_for(factory(), timeout=remaining)

            ticket.task = loop.create_task(execute())
            ticket.task.add_done_callback(finished)

        def cancelled(result: concurrent.futures.Future) -> None:
            if not result.cancelled():
                return

            def cancel_on_owner() -> None:
                if ticket.task is not None:
                    ticket.task.cancel()

            try:
                loop.call_soon_threadsafe(cancel_on_owner)
            except RuntimeError:
                pass  # close() owns final draining, never touch a foreign task.

        ticket.result.add_done_callback(cancelled)
        try:
            loop.call_soon_threadsafe(start, context=contextvars.copy_context())
        except RuntimeError as exc:
            self._release(ticket)
            raise TransportUnavailable("IDE transport unavailable") from exc
        result = asyncio.wrap_future(ticket.result)
        if asyncio.get_running_loop() is loop:
            # The owner enforces the deadline and completes coroutine cleanup
            # before publishing. A second timer here can return before cleanup.
            return await result
        return await asyncio.wait_for(result, timeout=timeout)

    async def close(self) -> None:
        with self._lock:
            if self._loop is not None and self._loop is not asyncio.get_running_loop():
                raise RuntimeError("IDE transport must close on its owner loop")
            self._loop = None
            tickets = tuple(self._tickets)
        tasks = []
        for ticket in tickets:
            self._publish(ticket, error=TransportUnavailable("IDE transport stopped", outcome="unknown"))
            if ticket.task is not None:
                ticket.task.cancel()
                tasks.append(ticket.task)
            else:
                self._release(ticket)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
