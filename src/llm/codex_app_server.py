"""Async supervisor for the Codex App Server JSONL protocol.

The supervisor is deliberately independent from Lumena's provider and web
lifecycle. Importing this module starts no process. Higher rollout lots own the
singleton and decide when subscription execution is enabled.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from loguru import logger


DEFAULT_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_STDERR_BYTES = 64 * 1024
DEFAULT_NOTIFICATION_QUEUE_SIZE = 256
DEFAULT_SERVER_REQUEST_LIMIT = 32
_OBSOLETE_CODEX_SERVICE_TIERS = frozenset({"default"})

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|(?:sk|sess|eyJ)[-_A-Za-z0-9.]{12,}|"
    r'"(?:access_token|refresh_token|id_token|api_key)"\s*:\s*"[^"]+")'
)


class CodexAppServerError(RuntimeError):
    """Base error for the local App Server transport."""


class CodexAppServerProtocolError(CodexAppServerError):
    """The subprocess violated the bounded JSONL protocol."""


class CodexAppServerProcessError(CodexAppServerError):
    """The App Server process stopped or its stdio channel broke."""


class CodexAppServerTimeout(CodexAppServerError):
    """A bounded App Server operation exceeded its deadline."""


class CodexAppServerRPCError(CodexAppServerError):
    """An App Server request returned an RPC error object."""

    def __init__(self, code: int | None, message: str, data: Any = None):
        super().__init__(f"Codex App Server RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class CodexAppServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class CodexNotification:
    method: str
    params: Any = None


@dataclass(frozen=True)
class CodexAppServerSnapshot:
    state: CodexAppServerState
    pid: int | None
    pending_requests: int
    queued_notifications: int
    dropped_notifications: int
    restart_count: int
    stderr_tail: str
    last_error: str
    request_count: int = 0
    request_error_count: int = 0
    request_timeout_count: int = 0
    turn_count: int = 0
    last_latency_ms: float = 0.0
    average_latency_ms: float = 0.0


@dataclass(frozen=True)
class CodexAppServerConfig:
    command: tuple[str, ...]
    cwd: str | None = None
    environ: Mapping[str, str] | None = None
    request_timeout_s: float = 30.0
    handshake_timeout_s: float = 15.0
    shutdown_timeout_s: float = 5.0
    write_timeout_s: float = 10.0
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    notification_queue_size: int = DEFAULT_NOTIFICATION_QUEUE_SIZE
    server_request_limit: int = DEFAULT_SERVER_REQUEST_LIMIT
    max_auto_restarts: int = 1

    @classmethod
    def from_executable(
        cls,
        executable: str,
        *,
        cwd: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        config_overrides: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> "CodexAppServerConfig":
        command: list[str] = [str(executable)]
        for key, value in (config_overrides or {}).items():
            command.extend(("--config", f"{key}={json.dumps(value)}"))
        command.append("app-server")
        return cls(
            command=tuple(command),
            cwd=str(cwd) if cwd is not None else None,
            environ=environ,
            **kwargs,
        )


ServerRequestHandler = Callable[[Any], Any | Awaitable[Any]]
NotificationPredicate = Callable[[CodexNotification], bool]


_shared_codex_app_server: "CodexAppServerSupervisor | None" = None
_codex_turn_execution_lock = asyncio.Lock()


def codex_turn_execution_lock() -> asyncio.Lock:
    """Serialize turns consuming the supervisor's shared notification queue.

    Request/response RPCs remain multiplexed by id, but turn notifications are
    delivered through one bounded queue. Until App Server exposes per-thread
    subscriptions to this client, one lock prevents concurrent consumers from
    stealing each other's events.
    """

    return _codex_turn_execution_lock


def attach_shared_codex_app_server(
    supervisor: "CodexAppServerSupervisor",
) -> None:
    """Publish an already-running supervisor to non-web execution surfaces.

    This does not start a process. The web account flow remains the only owner
    of startup and authentication; CodeAgent can only borrow that explicit
    local session.
    """

    global _shared_codex_app_server
    if not supervisor.is_running:
        raise CodexAppServerProcessError(
            "Cannot attach a Codex App Server that is not running"
        )
    _shared_codex_app_server = supervisor


def get_shared_codex_app_server() -> "CodexAppServerSupervisor | None":
    """Return the running shared supervisor, never a stale instance."""

    supervisor = _shared_codex_app_server
    if supervisor is None or not supervisor.is_running:
        return None
    return supervisor


def detach_shared_codex_app_server(
    supervisor: "CodexAppServerSupervisor | None" = None,
) -> bool:
    """Forget the shared supervisor without stopping or replacing it."""

    global _shared_codex_app_server
    current = _shared_codex_app_server
    if current is None or (supervisor is not None and current is not supervisor):
        return False
    _shared_codex_app_server = None
    return True


def redact_codex_diagnostic(value: str, *, limit: int = 4096) -> str:
    """Return a bounded diagnostic with credential-like values removed."""

    text = _SECRET_RE.sub("[REDACTED]", value or "")
    home = str(Path.home())
    if home:
        text = text.replace(home, "~")
    return text[-limit:]


def build_codex_app_server_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess environment that cannot silently use API billing."""

    env = dict(os.environ if base is None else base)
    blocked = {
        "OPENAI_API_KEY",
        "OPENAI_ORGANIZATION",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "OPENAI_PROJECT_ID",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    }
    for key in tuple(env):
        if key.upper() in blocked:
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def codex_compatibility_config_overrides(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return narrow overrides for obsolete values in the user's Codex config.

    The installed Codex CLI currently accepts ``fast`` or ``flex`` for
    ``service_tier``. Older desktop configurations may still contain
    ``default``. Lumena fixes that value only for its child process and never
    rewrites the user's global configuration.
    """

    env = os.environ if environ is None else environ
    codex_home = Path(env.get("CODEX_HOME") or (Path.home() / ".codex"))
    config_path = codex_home / "config.toml"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    service_tier = str(config.get("service_tier", "") or "").strip().lower()
    if service_tier in _OBSOLETE_CODEX_SERVICE_TIERS:
        return {"service_tier": "flex"}
    return {}


async def ensure_shared_codex_app_server(app: Any = None) -> "CodexAppServerSupervisor | None":
    """LOT Z33 phase 0 — ouvrir (ou retrouver) la session Codex partagee.

    Run du 2026-08-21, mesure au log :

        02:28:55  mission creee (duree_minutes=90), Codex travaille
        02:28→02:32  CodeAgent, agent, 46 outils sur un tour
        02:33:42  [resume] mission relancee au demarrage   ← REDEMARRAGE
        02:33:57  ECHEC « Aucune session Codex connectee »

    `_shared_codex_app_server` est un global de MODULE, pose uniquement par la
    route web quand l'utilisateur clique dans Configuration. Il meurt avec le
    process. Les missions, elles, sont PERSISTEES et relancees au boot : la
    mission repart, la session non, et elle meurt sur place.

    `lifespan` arretait la session (`stop_attached_codex_app_server`) sans
    jamais la rouvrir : ce pendant n'existait pas.

    ⚠️ Verifie en direct avant d'ecrire cette fonction : un script NEUF trouve le
    compte deja `CONNECTED` (l'auth est stockee sur disque par le CLI lui-meme).
    Il n'y a donc RIEN a reconnecter — juste un processus a relancer. C'est ce
    qui rend ce correctif petit et sur.

    Rend None sans jamais lever : abonnement desactive, CLI absent ou demarrage
    en echec laissent Lumena tourner exactement comme avant.
    """
    current = get_shared_codex_app_server()
    if current is not None:
        return current
    try:
        # Import tardif : `codex_subscription` importe DEJA ce module — un import
        # en tete creerait un cycle.
        from src.llm.codex_subscription import (
            CodexCLIState,
            load_codex_subscription_settings,
            probe_codex_cli_async,
        )
    except Exception as exc:  # pragma: no cover - import defensif
        logger.debug("[Z33] session Codex indisponible (import): {}", exc)
        return None

    try:
        settings = load_codex_subscription_settings()
        if not settings.enabled:
            return None
        preflight = await probe_codex_cli_async(settings.cli_path or None)
        if preflight.state is not CodexCLIState.READY:
            logger.info(
                "[Z33] session Codex non ouverte — CLI {} : {}",
                preflight.state.value, (preflight.detail or "")[:160],
            )
            return None
        supervisor = CodexAppServerSupervisor(
            CodexAppServerConfig.from_executable(
                preflight.executable,
                config_overrides=codex_compatibility_config_overrides(),
            )
        )
    except Exception as exc:
        logger.warning("[Z33] preparation session Codex impossible: {}", exc)
        return None

    try:
        await supervisor.start()
    except Exception as exc:
        logger.warning("[Z33] demarrage session Codex echoue: {}", exc)
        try:
            await supervisor.stop()
        except Exception:
            pass
        return None

    attach_shared_codex_app_server(supervisor)
    if app is not None:
        try:
            app.state.codex_app_server = supervisor
        except Exception:
            pass
    logger.info("[Z33] session Codex ouverte (app-server demarre)")
    return supervisor


async def stop_attached_codex_app_server(app: Any) -> bool:
    """Stop a supervisor stored on ``app.state`` without creating one.

    The helper keeps the historical lifespan dormant when subscription mode
    has never been used and gives later route lots one authoritative shutdown
    slot.
    """

    state = getattr(app, "state", None)
    leases = getattr(state, "codex_collaboration_leases", None) if state else None
    if isinstance(leases, dict):
        for lease in tuple(leases.values()):
            try:
                lease.release()
            except Exception:
                pass
        leases.clear()
    supervisor = getattr(state, "codex_app_server", None) if state else None
    if supervisor is None:
        return False
    try:
        await supervisor.stop()
    finally:
        detach_shared_codex_app_server(supervisor)
        state.codex_app_server = None
    return True


class CodexAppServerSupervisor:
    """Own one Codex App Server process and multiplex its JSONL protocol."""

    def __init__(self, config: CodexAppServerConfig):
        if not config.command:
            raise ValueError("Codex App Server command cannot be empty")
        if config.max_message_bytes < 1024:
            raise ValueError("max_message_bytes must be at least 1024")
        if config.notification_queue_size < 1:
            raise ValueError("notification_queue_size must be positive")
        if config.server_request_limit < 1:
            raise ValueError("server_request_limit must be positive")

        self.config = config
        self._state = CodexAppServerState.STOPPED
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._auto_restart_task: asyncio.Task[None] | None = None
        self._server_request_tasks: set[asyncio.Task[None]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notification_queue: asyncio.Queue[CodexNotification] = asyncio.Queue(
            maxsize=config.notification_queue_size
        )
        self._notification_waiters: list[
            tuple[str | None, NotificationPredicate | None, asyncio.Future[CodexNotification]]
        ] = []
        self._server_handlers: dict[str, ServerRequestHandler] = {}
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_size = 0
        self._dropped_notifications = 0
        self._restart_count = 0
        self._last_error = ""
        self._initialized_result: Any = None
        self._running_event = asyncio.Event()
        self._closing = False
        self._request_count = 0
        self._request_error_count = 0
        self._request_timeout_count = 0
        self._turn_count = 0
        self._last_latency_ms = 0.0
        self._total_latency_ms = 0.0

    @property
    def state(self) -> CodexAppServerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return (
            self._state is CodexAppServerState.RUNNING
            and self._process is not None
            and self._process.returncode is None
        )

    @property
    def initialized_result(self) -> Any:
        return self._initialized_result

    def snapshot(self) -> CodexAppServerSnapshot:
        stderr = b"".join(self._stderr_chunks).decode("utf-8", errors="replace")
        return CodexAppServerSnapshot(
            state=self._state,
            pid=self._process.pid if self._process is not None else None,
            pending_requests=len(self._pending),
            queued_notifications=self._notification_queue.qsize(),
            dropped_notifications=self._dropped_notifications,
            restart_count=self._restart_count,
            stderr_tail=redact_codex_diagnostic(
                stderr, limit=self.config.max_stderr_bytes
            ),
            last_error=redact_codex_diagnostic(self._last_error),
            request_count=self._request_count,
            request_error_count=self._request_error_count,
            request_timeout_count=self._request_timeout_count,
            turn_count=self._turn_count,
            last_latency_ms=round(self._last_latency_ms, 3),
            average_latency_ms=round(
                self._total_latency_ms / self._request_count,
                3,
            ) if self._request_count else 0.0,
        )

    async def __aenter__(self) -> "CodexAppServerSupervisor":
        await self.start()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.stop()

    def register_server_request_handler(
        self, method: str, handler: ServerRequestHandler
    ) -> None:
        self._server_handlers[method] = handler

    async def start(self) -> None:
        """Start and initialize the process. Repeated calls are idempotent."""

        async with self._lifecycle_lock:
            if self.is_running:
                return
            self._closing = False
            self._restart_count = 0
            await self._start_locked()

    async def _start_locked(self) -> None:
        await self._dispose_process_locked(terminate=True)
        self._state = CodexAppServerState.STARTING
        self._running_event.clear()
        self._last_error = ""

        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": self.config.cwd,
            "env": build_codex_app_server_environment(self.config.environ),
            "limit": self.config.max_message_bytes + 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.config.command, **kwargs
            )
        except (OSError, ValueError) as exc:
            self._state = CodexAppServerState.FAILED
            self._last_error = str(exc)
            raise CodexAppServerProcessError(
                f"Unable to start Codex App Server: {redact_codex_diagnostic(str(exc))}"
            ) from exc

        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="codex-app-server-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name="codex-app-server-stderr"
        )
        self._wait_task = asyncio.create_task(
            self._wait_loop(), name="codex-app-server-wait"
        )

        try:
            self._initialized_result = await self._request_once(
                "initialize",
                {
                    "clientInfo": {"name": "lumena", "version": "1"},
                    "capabilities": {},
                },
                timeout=self.config.handshake_timeout_s,
            )
            await self.notify("initialized", {})
        except Exception:
            self._closing = True
            await self._dispose_process_locked(terminate=True)
            self._closing = False
            self._state = CodexAppServerState.FAILED
            raise

        self._state = CodexAppServerState.RUNNING
        self._running_event.set()

    async def wait_until_running(self, timeout: float = 10.0) -> None:
        if self.is_running:
            return
        try:
            await asyncio.wait_for(self._running_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexAppServerTimeout(
                f"Codex App Server did not become ready within {timeout}s"
            ) from exc
        if not self.is_running:
            raise CodexAppServerProcessError(
                self._last_error or "Codex App Server is not running"
            )

    async def request(
        self, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        """Send one request. Failed requests are never replayed automatically."""

        if not self.is_running:
            raise CodexAppServerProcessError("Codex App Server is not running")
        return await self._request_once(method, params, timeout=timeout)

    async def _request_once(
        self, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        started_at = time.monotonic()
        self._request_count += 1
        if method == "turn/start":
            self._turn_count += 1
        error_counted = False
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {
            "id": request_id,
            "method": method,
            "params": {} if params is None else params,
        }
        try:
            await self._write_message(message)
            wait_s = self.config.request_timeout_s if timeout is None else timeout
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=wait_s)
            except asyncio.TimeoutError as exc:
                self._request_timeout_count += 1
                self._request_error_count += 1
                error_counted = True
                raise CodexAppServerTimeout(
                    f"Codex App Server request '{method}' timed out after {wait_s}s"
                ) from exc
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception:
            if not error_counted:
                self._request_error_count += 1
            raise
        finally:
            self._last_latency_ms = (time.monotonic() - started_at) * 1000.0
            self._total_latency_ms += self._last_latency_ms
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any = None) -> None:
        await self._write_message(
            {"method": method, "params": {} if params is None else params}
        )

    async def next_notification(
        self, *, timeout: float | None = None
    ) -> CodexNotification:
        try:
            if timeout is None:
                return await self._notification_queue.get()
            return await asyncio.wait_for(self._notification_queue.get(), timeout)
        except asyncio.TimeoutError as exc:
            raise CodexAppServerTimeout("Timed out waiting for Codex notification") from exc

    async def wait_for_notification(
        self,
        method: str | None = None,
        *,
        predicate: NotificationPredicate | None = None,
        timeout: float = 30.0,
    ) -> CodexNotification:
        future: asyncio.Future[CodexNotification] = (
            asyncio.get_running_loop().create_future()
        )
        waiter = (method, predicate, future)
        self._notification_waiters.append(waiter)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexAppServerTimeout("Timed out waiting for Codex notification") from exc
        finally:
            try:
                self._notification_waiters.remove(waiter)
            except ValueError:
                pass

    async def _write_message(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexAppServerProcessError("Codex App Server stdin is unavailable")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(payload) > self.config.max_message_bytes:
            raise CodexAppServerProtocolError("Outgoing App Server message is too large")
        async with self._write_lock:
            try:
                process.stdin.write(payload)
                await asyncio.wait_for(
                    process.stdin.drain(), timeout=self.config.write_timeout_s
                )
            except asyncio.TimeoutError as exc:
                raise CodexAppServerTimeout("Codex App Server stdin backpressure timeout") from exc
            except (BrokenPipeError, ConnectionError, OSError) as exc:
                raise CodexAppServerProcessError(
                    f"Codex App Server write failed: {redact_codex_diagnostic(str(exc))}"
                ) from exc

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise CodexAppServerProtocolError(
                        "Incoming App Server message exceeds the configured limit"
                    ) from exc
                if not line:
                    raise CodexAppServerProcessError("Codex App Server stdout reached EOF")
                if len(line) > self.config.max_message_bytes:
                    raise CodexAppServerProtocolError(
                        "Incoming App Server message exceeds the configured limit"
                    )
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CodexAppServerProtocolError(
                        "Codex App Server emitted malformed JSONL"
                    ) from exc
                if not isinstance(message, dict):
                    raise CodexAppServerProtocolError(
                        "Codex App Server message must be a JSON object"
                    )
                await self._dispatch_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._transport_failed(exc)

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        has_id = "id" in message
        has_method = isinstance(message.get("method"), str)
        if has_id and not has_method:
            request_id = message.get("id")
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                future.set_exception(
                    CodexAppServerRPCError(
                        error.get("code"),
                        str(error.get("message", "Unknown error")),
                        error.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return

        if has_method and has_id:
            if len(self._server_request_tasks) >= self.config.server_request_limit:
                await self._write_message(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32000,
                            "message": "Lumena server-request queue is full",
                        },
                    }
                )
                return
            task = asyncio.create_task(
                self._handle_server_request(message),
                name=f"codex-server-request-{message.get('method')}",
            )
            self._server_request_tasks.add(task)
            task.add_done_callback(self._server_request_done)
            return

        if has_method:
            notification = CodexNotification(
                method=message["method"], params=message.get("params")
            )
            self._publish_notification(notification)
            return

        raise CodexAppServerProtocolError("Unrecognized Codex App Server message")

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        handler = self._server_handlers.get(method)
        if handler is None:
            await self._write_message(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": f"Unsupported method: {method}"},
                }
            )
            return
        try:
            result = handler(message.get("params"))
            if inspect.isawaitable(result):
                result = await result
            await self._write_message({"id": message["id"], "result": result})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # handlers are an explicit trust boundary
            await self._write_message(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32001,
                        "message": redact_codex_diagnostic(str(exc), limit=256),
                    },
                }
            )

    def _publish_notification(self, notification: CodexNotification) -> None:
        for method, predicate, future in tuple(self._notification_waiters):
            if future.done() or (method is not None and method != notification.method):
                continue
            if predicate is not None and not predicate(notification):
                continue
            future.set_result(notification)

        if self._notification_queue.full():
            try:
                self._notification_queue.get_nowait()
                self._dropped_notifications += 1
            except asyncio.QueueEmpty:
                pass
        self._notification_queue.put_nowait(notification)

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    return
                self._append_stderr(chunk)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            return

    def _append_stderr(self, chunk: bytes) -> None:
        tail = (b"".join(self._stderr_chunks) + chunk)[
            -self.config.max_stderr_bytes :
        ]
        self._stderr_chunks.clear()
        if tail:
            self._stderr_chunks.append(tail)
        self._stderr_size = len(tail)

    def _server_request_done(self, task: asyncio.Task[None]) -> None:
        self._server_request_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _wait_loop(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            returncode = await process.wait()
        except asyncio.CancelledError:
            raise
        if not self._closing:
            await self._transport_failed(
                CodexAppServerProcessError(
                    f"Codex App Server exited unexpectedly with code {returncode}"
                )
            )

    async def _transport_failed(self, exc: Exception) -> None:
        if self._closing or self._state in {
            CodexAppServerState.STOPPED,
            CodexAppServerState.STOPPING,
        }:
            return
        self._state = CodexAppServerState.FAILED
        self._running_event.clear()
        self._last_error = str(exc)
        self._fail_pending(exc)
        if (
            self._restart_count < self.config.max_auto_restarts
            and (self._auto_restart_task is None or self._auto_restart_task.done())
        ):
            self._auto_restart_task = asyncio.create_task(
                self._auto_restart(), name="codex-app-server-restart"
            )

    async def _auto_restart(self) -> None:
        async with self._lifecycle_lock:
            if self._closing or self._state is not CodexAppServerState.FAILED:
                return
            self._restart_count += 1
            try:
                await self._start_locked()
            except Exception as exc:
                self._state = CodexAppServerState.FAILED
                self._last_error = str(exc)

    def _fail_pending(self, exc: Exception) -> None:
        error = (
            exc
            if isinstance(exc, CodexAppServerError)
            else CodexAppServerProcessError(str(exc))
        )
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def stop(self) -> None:
        """Stop the process and every helper task. Safe to call repeatedly."""

        async with self._lifecycle_lock:
            if self._state is CodexAppServerState.STOPPED and self._process is None:
                return
            self._closing = True
            self._state = CodexAppServerState.STOPPING
            self._running_event.clear()
            if self._auto_restart_task is not None:
                self._auto_restart_task.cancel()
                await self._await_cancelled(self._auto_restart_task)
                self._auto_restart_task = None
            self._fail_pending(CodexAppServerProcessError("Codex App Server stopped"))
            await self._dispose_process_locked(terminate=True)
            self._state = CodexAppServerState.STOPPED
            self._closing = False

    async def _dispose_process_locked(self, *, terminate: bool) -> None:
        process = self._process
        current = asyncio.current_task()
        tasks = (self._reader_task, self._stderr_task, self._wait_task)
        for task in tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()

        if process is not None:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
            if terminate and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            if process.returncode is None:
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=self.config.shutdown_timeout_s
                    )
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()

        for task in tasks:
            if task is not None and task is not current:
                await self._await_cancelled(task)
        for task in tuple(self._server_request_tasks):
            task.cancel()
            await self._await_cancelled(task)
        self._server_request_tasks.clear()
        self._reader_task = None
        self._stderr_task = None
        self._wait_task = None
        self._process = None

    @staticmethod
    async def _await_cancelled(task: asyncio.Task[Any]) -> None:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
