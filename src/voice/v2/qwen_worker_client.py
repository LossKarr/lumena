"""Async client for the dedicated Qwen JSONL subprocess."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from typing import Any


class QwenWorkerError(RuntimeError):
    pass


class QwenWorkerClient:
    def __init__(
        self, *, python: Path, worker_script: Path, model_dir: Path,
        sox_dir: Path | None = None, device: str = "auto", dtype: str = "auto",
        startup_timeout_s: float = 45.0, request_timeout_s: float = 90.0,
    ) -> None:
        self.python = Path(python)
        self.worker_script = Path(worker_script)
        self.model_dir = Path(model_dir)
        self.sox_dir = Path(sox_dir) if sox_dir else None
        self.device = str(device)
        self.dtype = str(dtype)
        self.startup_timeout_s = max(1.0, min(180.0, float(startup_timeout_s)))
        self.request_timeout_s = max(1.0, min(600.0, float(request_timeout_s)))
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
        self._pending: dict[str, dict[str, Any]] = {}
        self._stderr: deque[str] = deque(maxlen=20)
        self._lock = asyncio.Lock()
        self.restarts = 0
        self.last_error_code = ""
        self.last_success_at: float | None = None
        self.device_effective = ""

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _start_sync(self) -> None:
        if self.alive:
            return
        if not self.python.is_file() or not self.worker_script.is_file() or not self.model_dir.is_dir():
            raise QwenWorkerError("worker_payload_missing")
        env = os.environ.copy()
        env.update({
            "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
        })
        if self.sox_dir and self.sox_dir.is_dir():
            env["PATH"] = str(self.sox_dir) + os.pathsep + env.get("PATH", "")
        command = [
            str(self.python), "-u", str(self.worker_script), "--model", str(self.model_dir),
            "--device", self.device, "--dtype", self.dtype,
        ]
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
            cwd=str(self.worker_script.parent.parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_stdout, name="qwen-tts-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="qwen-tts-stderr", daemon=True).start()
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if not self.alive:
                break
            try:
                event = self._responses.get(timeout=0.1)
            except queue.Empty:
                continue
            if event.get("event") == "ready":
                self.device_effective = str(event.get("device", ""))
                self.last_error_code = ""
                return
            if event.get("event") == "fatal":
                self.last_error_code = str(event.get("error", "model_load_failed"))[:64]
                break
        self._terminate_sync()
        raise QwenWorkerError(self.last_error_code or "worker_start_timeout")

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                try:
                    self._responses.put(value, timeout=0.1)
                except queue.Full:
                    self.last_error_code = "worker_response_overflow"

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.strip()[:300])

    def _write_sync(self, payload: dict[str, Any]) -> None:
        if not self.alive or self._process is None or self._process.stdin is None:
            raise QwenWorkerError("worker_stopped")
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._process.stdin.flush()

    def _poll_response(self, request_id: str) -> dict[str, Any] | None:
        cached = self._pending.pop(request_id, None)
        if cached is not None:
            return cached
        try:
            value = self._responses.get_nowait()
        except queue.Empty:
            return None
        result_id = str(value.get("id", ""))
        if result_id == request_id:
            return value
        if result_id:
            self._pending[result_id] = value
        return None

    async def request(self, payload: dict[str, Any], *, cancel: Any = None) -> dict[str, Any]:
        async with self._lock:
            try:
                await asyncio.to_thread(self._start_sync)
                request_id = uuid.uuid4().hex
                await asyncio.to_thread(self._write_sync, {"id": request_id, **payload})
                deadline = time.monotonic() + self.request_timeout_s
                while time.monotonic() < deadline:
                    if cancel is not None and getattr(cancel, "cancelled", False):
                        self.last_error_code = "worker_cancelled"
                        await asyncio.to_thread(self._terminate_sync)
                        raise asyncio.CancelledError
                    if not self.alive:
                        raise QwenWorkerError("worker_stopped")
                    result = self._poll_response(request_id)
                    if result is not None:
                        if not result.get("ok"):
                            self.last_error_code = str(result.get("error", "synthesis_failed"))[:64]
                            raise QwenWorkerError(self.last_error_code)
                        self.last_error_code = ""
                        self.last_success_at = time.time()
                        return result
                    await asyncio.sleep(0.025)
                self.last_error_code = "worker_timeout"
                await asyncio.to_thread(self._terminate_sync)
                raise QwenWorkerError("worker_timeout")
            except asyncio.CancelledError:
                if self.alive:
                    await asyncio.to_thread(self._terminate_sync)
                raise
            except Exception:
                if self.last_error_code == "":
                    self.last_error_code = "worker_error"
                raise

    async def synthesize(
        self, *, text: str, language: str, reference_path: str,
        reference_text: str, output_path: str, cancel: Any = None,
    ) -> dict[str, Any]:
        return await self.request({
            "action": "synthesize", "text": text, "language": language,
            "reference_path": reference_path, "reference_text": reference_text,
            "output_path": output_path,
        }, cancel=cancel)

    def _terminate_sync(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        self._pending.clear()
        while True:
            try:
                self._responses.get_nowait()
            except queue.Empty:
                break

    async def aclose(self) -> None:
        if self.alive:
            try:
                await self.request({"action": "shutdown"})
            except Exception:
                pass
        await asyncio.to_thread(self._terminate_sync)

    def status(self) -> dict[str, Any]:
        return {
            "alive": self.alive, "engine": "qwen3-tts", "restarts": self.restarts,
            "last_error_code": self.last_error_code[:64],
            "last_success_at": self.last_success_at,
            "device": self.device_effective or self.device,
            "stderr_lines": len(self._stderr),
        }
