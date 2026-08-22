"""Consent-based continuity bridge between Lumena and Codex threads.

The bridge never starts or authenticates App Server. It consumes an already
connected supervisor, exposes metadata discovery before detailed reads, keeps
only approved handoff summaries, and serializes workspace writers with an
inter-process lock. Hidden reasoning and credential-like values are never
persisted or returned.
"""

from __future__ import annotations

import hashlib
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from src.llm.codex_app_server import (
    CodexAppServerSupervisor,
    redact_codex_diagnostic,
)
from src.utils.file_lock import ProcessFileLock
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


THREAD_LIST_METHOD = "thread/list"
THREAD_READ_METHOD = "thread/read"
THREAD_LOADED_LIST_METHOD = "thread/loaded/list"
THREAD_RESUME_METHOD = "thread/resume"
THREAD_FORK_METHOD = "thread/fork"
TURN_START_METHOD = "turn/start"
TURN_STEER_METHOD = "turn/steer"
TURN_INTERRUPT_METHOD = "turn/interrupt"

_REGISTRY_FILE = DATA_DIR / "codex" / "collaboration.json"
_LOCK_DIR = DATA_DIR / "codex" / "workspace_locks"
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|(?:sk|sess|eyJ)[-_A-Za-z0-9.]{12,}|"
    r'"(?:access_token|refresh_token|id_token|api_key)"\s*:\s*"[^"]+")'
)
_TEST_RE = re.compile(r"(?i)(?:^|\s)(?:python\s+-m\s+)?pytest(?:\s|$)")


class CodexShareMode(str, Enum):
    NONE = "none"
    SELECTED = "selected"
    WORKSPACE = "workspace"
    ALL_LOCAL = "all_local"


class CollaborationState(str, Enum):
    DISCOVERED = "discovered"
    LINKED_READ_ONLY = "linked_read_only"
    HANDOFF_READY = "handoff_ready"
    CODEX_WORKING = "codex_working"
    LUMENA_VERIFYING = "lumena_verifying"
    ACCEPTED = "accepted"
    LUMENA_WORKING = "lumena_working"
    CODEX_REVIEWING = "codex_reviewing"
    PAUSED = "paused"
    DISSOCIATED = "dissociated"
    FAILED = "failed"


@dataclass(frozen=True)
class CodexThreadSummary:
    thread_id: str
    cwd: str
    name: str = ""
    preview: str = ""
    status: str = "notLoaded"
    active_flags: tuple[str, ...] = ()
    source_kind: str = ""
    model_provider: str = ""
    created_at: int | float | None = None
    updated_at: int | float | None = None
    is_pinned: bool = False

    @property
    def waiting_on_approval(self) -> bool:
        return "waitingOnApproval" in self.active_flags


@dataclass(frozen=True)
class CodexHandoff:
    thread_id: str
    workspace: str
    objective: str = ""
    completed: tuple[str, ...] = ()
    files_touched: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    next_action: str = ""
    evidence: tuple[str, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class CollaborationLink:
    thread_id: str
    workspace: str
    state: str = CollaborationState.LINKED_READ_ONLY.value
    status: str = "notLoaded"
    active_flags: tuple[str, ...] = ()
    cursor: str = ""
    handoff: dict[str, Any] = field(default_factory=dict)
    memory_approved: bool = False
    updated_at: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    text = _SECRET_RE.sub("[REDACTED]", str(value or ""))
    return redact_codex_diagnostic(text, limit=limit).strip()


def _resolved_workspace(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Workspace Codex introuvable")
    return path


def _status_parts(raw: Any) -> tuple[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return "notLoaded", ()
    status = str(raw.get("type", "notLoaded") or "notLoaded")
    flags = raw.get("activeFlags", ())
    if not isinstance(flags, Sequence) or isinstance(flags, (str, bytes)):
        flags = ()
    return status, tuple(str(item) for item in flags if str(item))


def normalise_thread_summary(raw: Any) -> CodexThreadSummary | None:
    if not isinstance(raw, Mapping):
        return None
    thread_id = str(raw.get("id", "") or raw.get("threadId", "")).strip()
    if not thread_id:
        return None
    status, flags = _status_parts(raw.get("status"))
    source = raw.get("source")
    source_kind = ""
    if isinstance(source, Mapping):
        source_kind = str(source.get("kind", "") or "")
    return CodexThreadSummary(
        thread_id=thread_id,
        cwd=str(raw.get("cwd", "") or ""),
        name=_clean_text(raw.get("name", ""), limit=240),
        preview=_clean_text(raw.get("preview", ""), limit=500),
        status=status,
        active_flags=flags,
        source_kind=source_kind or str(raw.get("sourceKind", "") or ""),
        model_provider=str(raw.get("modelProvider", "") or ""),
        created_at=raw.get("createdAt"),
        updated_at=raw.get("updatedAt"),
        is_pinned=bool(raw.get("isPinned", False)),
    )


def _item_type(item: Mapping[str, Any]) -> str:
    return str(item.get("type", "") or "")


def _command_text(item: Mapping[str, Any]) -> str:
    command = item.get("command", "")
    if isinstance(command, Sequence) and not isinstance(command, (str, bytes)):
        return " ".join(str(part) for part in command)
    return str(command or "")


def build_handoff(thread: Any, *, workspace: str | Path) -> CodexHandoff:
    """Build a bounded, reasoning-free handoff from a `thread/read` payload."""

    raw = thread if isinstance(thread, Mapping) else {}
    thread_id = str(raw.get("id", "") or raw.get("threadId", "")).strip()
    objective = _clean_text(raw.get("name") or raw.get("preview"), limit=1000)
    completed: list[str] = []
    files: set[str] = set()
    tests: list[str] = []
    errors: list[str] = []
    evidence: list[str] = []
    turns = raw.get("turns", ())
    if not isinstance(turns, Sequence) or isinstance(turns, (str, bytes)):
        turns = ()
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        turn_status = str(turn.get("status", "") or "")
        if turn_status and turn_status not in {"completed", "inProgress"}:
            errors.append(f"Tour {turn.get('id', '?')}: {turn_status}")
        items = turn.get("items", ())
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            kind = _item_type(item)
            if kind == "agentMessage":
                text = _clean_text(item.get("text", ""), limit=2000)
                if text:
                    completed.append(text)
            elif kind == "fileChange":
                changes = item.get("changes", ())
                if isinstance(changes, Sequence) and not isinstance(
                    changes, (str, bytes)
                ):
                    for change in changes:
                        if isinstance(change, Mapping):
                            path = _clean_text(change.get("path", ""), limit=500)
                            if path:
                                files.add(path)
                evidence.append("Mutation de fichier rapportee par Codex")
            elif kind == "commandExecution":
                command = _clean_text(_command_text(item), limit=1000)
                exit_code = item.get("exitCode")
                if command and _TEST_RE.search(command):
                    tests.append(f"{command} -> exit {exit_code}")
                if exit_code not in (None, 0):
                    errors.append(f"Commande en echec ({exit_code}): {command}")
                if command:
                    evidence.append(f"Commande: {command} (exit {exit_code})")
    return CodexHandoff(
        thread_id=thread_id,
        workspace=str(Path(workspace).resolve()),
        objective=objective,
        completed=tuple(completed[-5:]),
        files_touched=tuple(sorted(files)),
        tests=tuple(tests[-10:]),
        errors=tuple(errors[-10:]),
        decisions=(),
        next_action=(
            "Lumena doit verifier les fichiers sur disque et relancer les tests."
        ),
        evidence=tuple(evidence[-20:]),
        created_at=_now(),
    )


def sanitise_thread_for_ui(thread: Any) -> dict[str, Any]:
    """Return user-visible history without reasoning or opaque tool payloads."""

    raw = thread if isinstance(thread, Mapping) else {}
    summary = normalise_thread_summary(raw)
    turns_out: list[dict[str, Any]] = []
    turns = raw.get("turns", ())
    if isinstance(turns, Sequence) and not isinstance(turns, (str, bytes)):
        for turn in turns:
            if not isinstance(turn, Mapping):
                continue
            visible: list[dict[str, Any]] = []
            items = turn.get("items", ())
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    kind = _item_type(item)
                    if kind in {"userMessage", "agentMessage"}:
                        text = _clean_text(item.get("text", ""), limit=4000)
                        if text:
                            visible.append({"type": kind, "text": text})
                    elif kind == "commandExecution":
                        visible.append(
                            {
                                "type": kind,
                                "command": _clean_text(_command_text(item), limit=1000),
                                "exit_code": item.get("exitCode"),
                                "status": str(item.get("status", "") or ""),
                            }
                        )
                    elif kind == "fileChange":
                        paths: list[str] = []
                        changes = item.get("changes", ())
                        if isinstance(changes, Sequence) and not isinstance(
                            changes, (str, bytes)
                        ):
                            for change in changes:
                                if isinstance(change, Mapping):
                                    path = _clean_text(change.get("path", ""), limit=500)
                                    if path:
                                        paths.append(path)
                        visible.append({"type": kind, "paths": paths})
            turns_out.append(
                {
                    "id": str(turn.get("id", "") or ""),
                    "status": str(turn.get("status", "") or ""),
                    "items": visible,
                }
            )
    return {
        "thread": asdict(summary) if summary is not None else None,
        "turns": turns_out,
    }


class CodexCollaborationRegistry:
    """Persist consent and approved summaries, never raw thread history."""

    def __init__(self, path: str | Path = _REGISTRY_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        payload = safe_read_json(
            self.path,
            default={"version": 1, "share_mode": "selected", "links": {}},
        )
        if not isinstance(payload, dict):
            payload = {}
        payload["version"] = 1
        if payload.get("share_mode") not in {item.value for item in CodexShareMode}:
            payload["share_mode"] = CodexShareMode.SELECTED.value
        if not isinstance(payload.get("links"), dict):
            payload["links"] = {}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, payload)

    def share_mode(self) -> CodexShareMode:
        with self._lock:
            return CodexShareMode(self._read()["share_mode"])

    def set_share_mode(self, mode: CodexShareMode | str) -> CodexShareMode:
        selected = mode if isinstance(mode, CodexShareMode) else CodexShareMode(mode)
        with self._lock:
            payload = self._read()
            payload["share_mode"] = selected.value
            self._write(payload)
        return selected

    def get(self, thread_id: str) -> CollaborationLink | None:
        key = str(thread_id or "").strip()
        with self._lock:
            raw = self._read()["links"].get(key)
        if not isinstance(raw, Mapping):
            return None
        return CollaborationLink(
            thread_id=key,
            workspace=str(raw.get("workspace", "") or ""),
            state=str(raw.get("state", CollaborationState.LINKED_READ_ONLY.value)),
            status=str(raw.get("status", "notLoaded") or "notLoaded"),
            active_flags=tuple(raw.get("active_flags", ()) or ()),
            cursor=str(raw.get("cursor", "") or ""),
            handoff=dict(raw.get("handoff", {}) or {}),
            memory_approved=bool(raw.get("memory_approved", False)),
            updated_at=str(raw.get("updated_at", "") or ""),
        )

    def list_links(self) -> tuple[CollaborationLink, ...]:
        with self._lock:
            keys = tuple(self._read()["links"])
        return tuple(link for key in keys if (link := self.get(key)) is not None)

    def put(self, link: CollaborationLink) -> CollaborationLink:
        if not link.thread_id.strip() or not link.workspace.strip():
            raise ValueError("Un lien Codex exige un thread et un workspace")
        stored = CollaborationLink(**{**asdict(link), "updated_at": _now()})
        with self._lock:
            payload = self._read()
            payload["links"][stored.thread_id] = asdict(stored)
            self._write(payload)
        return stored

    def delete(self, thread_id: str) -> bool:
        key = str(thread_id or "").strip()
        with self._lock:
            payload = self._read()
            existed = payload["links"].pop(key, None) is not None
            if existed:
                self._write(payload)
        return existed


class WorkspaceWriterLease:
    """One-writer lease shared by Lumena and Codex processes."""

    def __init__(self, workspace: str | Path, *, actor: str, lock_dir: str | Path = _LOCK_DIR):
        self.workspace = _resolved_workspace(workspace)
        self.actor = str(actor or "").strip()
        if self.actor not in {"lumena", "codex"}:
            raise ValueError("L'acteur doit etre 'lumena' ou 'codex'")
        digest = hashlib.sha256(str(self.workspace).lower().encode("utf-8")).hexdigest()
        self.lock = ProcessFileLock(
            Path(lock_dir) / f"{digest}.lock",
            "codex-collaboration-workspace",
            owner_id=f"{self.actor}:{self.workspace}",
        )

    def acquire(self) -> bool:
        return self.lock.acquire()

    def release(self) -> None:
        self.lock.release()

    def owner_info(self) -> dict[str, Any]:
        return self.lock.read_lock_info()

    def __enter__(self) -> "WorkspaceWriterLease":
        if not self.acquire():
            owner = self.owner_info().get("owner_id", "un autre acteur")
            raise RuntimeError(f"Workspace deja en mutation par {owner}")
        return self

    def __exit__(self, *_args: Any) -> None:
        self.release()


class CodexCollaborationService:
    """Protocol operations gated by local consent and workspace ownership."""

    def __init__(
        self,
        supervisor: CodexAppServerSupervisor,
        *,
        registry: CodexCollaborationRegistry | None = None,
        lock_dir: str | Path = _LOCK_DIR,
    ):
        if supervisor is None or not supervisor.is_running:
            raise RuntimeError("Codex App Server n'est pas connecte")
        self.supervisor = supervisor
        self.registry = registry or CodexCollaborationRegistry()
        self.lock_dir = Path(lock_dir)

    async def discover_threads(
        self,
        workspace: str | Path,
        *,
        cursor: str = "",
        limit: int = 25,
        include_other_workspaces: bool = False,
    ) -> tuple[tuple[CodexThreadSummary, ...], str]:
        mode = self.registry.share_mode()
        if mode is CodexShareMode.NONE:
            return (), ""
        root = _resolved_workspace(workspace)
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 100)),
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "archived": False,
            "sourceKinds": ["cli", "vscode", "appServer"],
        }
        if cursor:
            params["cursor"] = cursor
        if not include_other_workspaces or mode is not CodexShareMode.ALL_LOCAL:
            params["cwd"] = str(root)
        payload = await self.supervisor.request(THREAD_LIST_METHOD, params, timeout=30)
        data = payload.get("data", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            data = ()
        summaries = tuple(
            summary
            for raw in data
            if (summary := normalise_thread_summary(raw)) is not None
        )
        return summaries, str(payload.get("nextCursor", "") or "") if isinstance(payload, Mapping) else ""

    async def loaded_thread_ids(self) -> tuple[str, ...]:
        payload = await self.supervisor.request(THREAD_LOADED_LIST_METHOD, {}, timeout=15)
        data = payload.get("data", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            return ()
        return tuple(str(item) for item in data if str(item))

    async def link(self, thread_id: str, workspace: str | Path) -> CollaborationLink:
        root = _resolved_workspace(workspace)
        existing = self.registry.get(thread_id)
        if existing is not None and Path(existing.workspace) == root:
            return existing
        payload = await self.supervisor.request(
            THREAD_READ_METHOD,
            {"threadId": thread_id, "includeTurns": False},
            timeout=30,
        )
        thread = payload.get("thread", {}) if isinstance(payload, Mapping) else {}
        summary = normalise_thread_summary(thread)
        if summary is None:
            raise ValueError("Tache Codex introuvable")
        if summary.cwd and Path(summary.cwd).resolve() != root:
            if self.registry.share_mode() is not CodexShareMode.ALL_LOCAL:
                raise PermissionError("Cette tache Codex appartient a un autre workspace")
            root = _resolved_workspace(summary.cwd)
        return self.registry.put(
            CollaborationLink(
                thread_id=thread_id,
                workspace=str(root),
                state=CollaborationState.LINKED_READ_ONLY.value,
                status=summary.status,
                active_flags=summary.active_flags,
            )
        )

    def dissociate(self, thread_id: str) -> bool:
        return self.registry.delete(thread_id)

    def _require_read_consent(self, thread_id: str, workspace: Path) -> None:
        mode = self.registry.share_mode()
        link = self.registry.get(thread_id)
        if mode is CodexShareMode.NONE:
            raise PermissionError("Le partage Codex est desactive")
        if mode is CodexShareMode.SELECTED and link is None:
            raise PermissionError("Cette tache doit d'abord etre liee explicitement")
        if link is not None and Path(link.workspace).resolve() != workspace:
            raise PermissionError("Le lien Codex appartient a un autre workspace")

    async def read_thread(
        self, thread_id: str, workspace: str | Path, *, include_turns: bool = True
    ) -> Mapping[str, Any]:
        root = _resolved_workspace(workspace)
        self._require_read_consent(thread_id, root)
        payload = await self.supervisor.request(
            THREAD_READ_METHOD,
            {"threadId": thread_id, "includeTurns": bool(include_turns)},
            timeout=30,
        )
        thread = payload.get("thread", {}) if isinstance(payload, Mapping) else {}
        summary = normalise_thread_summary(thread)
        if summary is None:
            raise ValueError("Tache Codex introuvable")
        if summary.cwd and Path(summary.cwd).resolve() != root:
            raise PermissionError("Lecture inter-workspace refusee")
        return thread

    async def create_handoff(
        self,
        thread_id: str,
        workspace: str | Path,
        *,
        approve_memory: bool = False,
    ) -> CodexHandoff:
        root = _resolved_workspace(workspace)
        thread = await self.read_thread(thread_id, root, include_turns=True)
        handoff = build_handoff(thread, workspace=root)
        previous = self.registry.get(thread_id)
        self.registry.put(
            CollaborationLink(
                thread_id=thread_id,
                workspace=str(root),
                state=CollaborationState.HANDOFF_READY.value,
                status=(previous.status if previous else "notLoaded"),
                active_flags=(previous.active_flags if previous else ()),
                cursor=(previous.cursor if previous else ""),
                handoff=asdict(handoff),
                memory_approved=bool(approve_memory),
            )
        )
        return handoff

    async def resume_for_handoff(
        self, thread_id: str, workspace: str | Path, *, write: bool = False
    ) -> Mapping[str, Any]:
        root = _resolved_workspace(workspace)
        self._require_read_consent(thread_id, root)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": str(root),
            "approvalPolicy": "never",
            "sandbox": "workspace-write" if write else "read-only",
        }
        return await self.supervisor.request(THREAD_RESUME_METHOD, params, timeout=30)

    async def start_turn(
        self,
        thread_id: str,
        workspace: str | Path,
        instruction: str,
        *,
        write: bool = False,
    ) -> tuple[str, WorkspaceWriterLease | None]:
        root = _resolved_workspace(workspace)
        self._require_read_consent(thread_id, root)
        linked = self.registry.get(thread_id)
        if linked and "waitingOnApproval" in linked.active_flags:
            raise RuntimeError("Codex attend une approbation utilisateur; aucune auto-acceptation")
        lease: WorkspaceWriterLease | None = None
        if write:
            lease = WorkspaceWriterLease(root, actor="codex", lock_dir=self.lock_dir)
            if not lease.acquire():
                owner = lease.owner_info().get("owner_id", "un autre acteur")
                raise RuntimeError(f"Workspace deja en mutation par {owner}")
        try:
            await self.resume_for_handoff(thread_id, root, write=write)
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": _clean_text(instruction, limit=12000)}],
                "cwd": str(root),
                "approvalPolicy": "never",
                "sandboxPolicy": (
                    {
                        "type": "workspaceWrite",
                        "writableRoots": [str(root)],
                        "networkAccess": False,
                    }
                    if write
                    else {
                        "type": "readOnly",
                        "networkAccess": False,
                    }
                ),
            }
            result = await self.supervisor.request(TURN_START_METHOD, params, timeout=30)
            turn = result.get("turn", {}) if isinstance(result, Mapping) else {}
            turn_id = str(turn.get("id", "") or result.get("turnId", "")) if isinstance(result, Mapping) else ""
            if not turn_id:
                raise RuntimeError("Codex n'a retourne aucun identifiant de tour")
            if linked:
                self.registry.put(
                    CollaborationLink(
                        **{
                            **asdict(linked),
                            "state": CollaborationState.CODEX_WORKING.value,
                            "status": "active",
                        }
                    )
                )
            return turn_id, lease
        except Exception:
            if lease is not None:
                lease.release()
            raise

    async def steer(self, thread_id: str, turn_id: str, instruction: str) -> str:
        result = await self.supervisor.request(
            TURN_STEER_METHOD,
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": _clean_text(instruction, limit=8000)}],
            },
            timeout=15,
        )
        return str(result.get("turnId", "") or "") if isinstance(result, Mapping) else ""

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        await self.supervisor.request(
            TURN_INTERRUPT_METHOD,
            {"threadId": thread_id, "turnId": turn_id},
            timeout=15,
        )

    async def fork_ephemeral(self, thread_id: str) -> str:
        result = await self.supervisor.request(
            THREAD_FORK_METHOD,
            {"threadId": thread_id, "ephemeral": True},
            timeout=30,
        )
        thread = result.get("thread", {}) if isinstance(result, Mapping) else {}
        return str(thread.get("id", "") or "") if isinstance(thread, Mapping) else ""


@contextmanager
def lumena_workspace_writer(
    workspace: str | Path, *, lock_dir: str | Path = _LOCK_DIR
) -> Iterator[WorkspaceWriterLease]:
    """Acquire the same one-writer contract for a Lumena mutation phase."""

    with WorkspaceWriterLease(workspace, actor="lumena", lock_dir=lock_dir) as lease:
        yield lease
