"""Bounded process isolation for the local Voice V2 TTS engine."""
from __future__ import annotations

import asyncio
import atexit
import multiprocessing as mp
from pathlib import Path
import queue
import tempfile
import time
import uuid
import wave
from typing import Any, Dict, Optional


def _write_test_wav(text: str) -> str:
    """Small deterministic fixture used by process lifecycle tests."""
    path = Path(tempfile.gettempdir()) / f"lumena_tts_worker_{uuid.uuid4().hex}.wav"
    frames = b"\0\0" * max(160, len(text) * 40)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(frames)
    return str(path)


def _worker_main(requests: Any, responses: Any, engine_kind: str) -> None:
    """Child entry point. It never imports the application or Web server."""
    engine = None
    if engine_kind == "lumena":
        from src.voice.tts import LumenaTTS  # child-only heavy import
        engine = LumenaTTS()
    while True:
        request = requests.get()
        request_id = request.get("id", "")
        action = request.get("action")
        if action == "shutdown":
            responses.put({"id": request_id, "ok": True, "action": action})
            return
        if action == "health":
            responses.put({"id": request_id, "ok": True, "action": action})
            continue
        if action != "synthesize":
            responses.put({"id": request_id, "ok": False, "error": "unknown_action"})
            continue
        try:
            if engine_kind == "test":
                if str(request.get("text", "")).startswith("__sleep__"):
                    time.sleep(2)
                path = _write_test_wav(str(request.get("text", "")))
                provider = "test-local"
            else:
                path_obj = asyncio.run(engine._synthesize(
                    str(request.get("text", "")),
                    local_only=bool(request.get("local_only", True)),
                    allow_xtts=bool(request.get("allow_xtts", False)),
                    piper_model=request.get("piper_model"),
                    prosody=request.get("prosody") or {},
                ))
                path = str(path_obj) if path_obj else ""
                provider = str(getattr(engine, "_last_provider", "") or "")
            responses.put({
                "id": request_id,
                "ok": bool(path),
                "path": path,
                "provider": provider,
            })
        except BaseException as exc:  # process boundary: return a bounded diagnostic
            responses.put({
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            })


class IsolatedTTSWorker:
    """Persistent, bounded worker process exposing LumenaTTS._synthesize."""

    def __init__(
        self,
        *,
        timeout_s: float = 90.0,
        queue_size: int = 4,
        max_restarts: int = 1,
        engine_kind: str = "lumena",
    ) -> None:
        self.timeout_s = max(1.0, min(600.0, float(timeout_s)))
        self.queue_size = max(1, min(32, int(queue_size)))
        self.max_restarts = max(0, min(5, int(max_restarts)))
        self.engine_kind = engine_kind
        self._ctx = mp.get_context("spawn")
        self._requests: Any = None
        self._responses: Any = None
        self._process: Optional[mp.Process] = None
        self._lock = asyncio.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._restarts = 0
        self._last_provider = ""
        self.last_error = ""
        atexit.register(self.close_now)

    @property
    def alive(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    def _start(self) -> None:
        if self.alive:
            return
        self._requests = self._ctx.Queue(maxsize=self.queue_size)
        self._responses = self._ctx.Queue(maxsize=self.queue_size)
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(self._requests, self._responses, self.engine_kind),
            name="lumena-tts-worker",
            daemon=True,
        )
        self._process.start()

    async def _response(self, request_id: str, timeout_s: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            cached = self._pending.pop(request_id, None)
            if cached is not None:
                return cached
            if not self.alive:
                raise RuntimeError("TTS worker stopped")
            remaining = max(0.01, min(0.1, deadline - time.monotonic()))
            try:
                result = await asyncio.to_thread(self._responses.get, True, remaining)
            except queue.Empty:
                continue
            result_id = str(result.get("id", ""))
            if result_id == request_id:
                return result
            self._pending[result_id] = result
        raise asyncio.TimeoutError("TTS worker timeout")

    async def _request(
        self,
        payload: Dict[str, Any],
        *,
        cancel_token: Any = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        async with self._lock:
            attempts = 2 if retry and self._restarts < self.max_restarts else 1
            for attempt in range(attempts):
                self._start()
                request_id = uuid.uuid4().hex
                request = {"id": request_id, **payload}
                response_task: Optional[asyncio.Task] = None
                try:
                    await asyncio.to_thread(self._requests.put, request, True, 0.5)
                    response_task = asyncio.create_task(
                        self._response(request_id, self.timeout_s)
                    )
                    while not response_task.done():
                        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                            raise asyncio.CancelledError
                        await asyncio.sleep(0.03)
                    result = await response_task
                    if not result.get("ok"):
                        raise RuntimeError(str(result.get("error", "TTS worker failure")))
                    self._last_provider = str(result.get("provider", "") or "")
                    self.last_error = ""
                    return result
                except asyncio.CancelledError:
                    # Cancellation of the caller is authoritative. A native model
                    # may not cooperate, so terminate the process boundary.  The
                    # response waiter belongs to this request: cancel and retrieve
                    # it before closing its queues, otherwise asyncio reports a
                    # delayed "Task exception was never retrieved".
                    if response_task is not None and not response_task.done():
                        response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                    self._terminate()
                    raise
                except Exception as exc:
                    if response_task is not None and not response_task.done():
                        response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                    self.last_error = str(exc)[:300]
                    self._terminate()
                    if attempt + 1 < attempts:
                        self._restarts += 1
                        continue
                    raise
            raise RuntimeError("TTS worker request exhausted")

    async def _synthesize(
        self,
        text: str,
        *,
        local_only: bool = True,
        allow_xtts: bool = False,
        piper_model: Optional[str] = None,
        prosody: Optional[dict] = None,
        _cancel_token: Any = None,
    ) -> Optional[Path]:
        result = await self._request({
            "action": "synthesize",
            "text": text,
            "local_only": local_only,
            "allow_xtts": allow_xtts,
            "piper_model": piper_model,
            "prosody": dict(prosody or {}),
        }, cancel_token=_cancel_token)
        path = str(result.get("path", "") or "")
        return Path(path) if path else None

    async def healthcheck(self) -> bool:
        try:
            await self._request({"action": "health"}, retry=False)
            return True
        except Exception:
            return False

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        for channel in (self._requests, self._responses):
            if channel is not None:
                try:
                    channel.close()
                    channel.join_thread()
                except Exception:
                    pass
        self._requests = None
        self._responses = None
        self._pending.clear()

    async def aclose(self) -> None:
        if self.alive:
            try:
                await self._request({"action": "shutdown"}, retry=False)
            except Exception:
                pass
        self._terminate()

    def close_now(self) -> None:
        self._terminate()
