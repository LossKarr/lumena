"""
runtime_watcher.py — Runtime Watcher MCP (Phase 12 v3).

Composant PASSIF de surveillance des MCP servers.

DOCTRINE Phase 12 :
  - Passive only : lit l'état, ne mute jamais le process.
  - Pas de câblage runtime : aucune modification de MCPSandboxRunner,
    MCPClient, tool_registry, etc.
  - Pas d'auto-restart, pas de kill, pas de notifications.
  - Pas de thread de polling automatique : APIs synchrones uniquement.
  - Persistance disque pour survivre au crash du watcher lui-même.
  - Audit sans PII (server_id + codes courts uniquement, jamais
    stderr/stdout/args/paths raw).

Source de vérité hybride :
  - PUSH : record_event(server_id, event_kind, error_code=...)
  - POLL : take_snapshot(server_id) — lit runner.state() (méthode, pas
    property)

Hors scope (refus formels) :
  - UNRESPONSIVE auto (faux positif sur MCP sain long-running)
  - Auto-restart, kill, signal
  - Thread de polling, notifications UI
  - Chiffrement snapshots disque
  - Métriques de latence des appels MCP

Layout disque :
    DATA_DIR/mcp_runtime_watcher/snapshots/<server_id>.json
    DATA_DIR/mcp_runtime_watcher/audit.jsonl
"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, FrozenSet, List, Optional, Tuple

from loguru import logger

from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_runtime_watcher"
_SNAPSHOTS_SUBDIR = "snapshots"
_AUDIT_FILENAME = "audit.jsonl"

_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_:\-]{1,64}$")

_WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

_VALID_EVENT_KINDS: FrozenSet[str] = frozenset(
    {"started", "stopped", "crashed", "restarted", "error"}
)

# Mapping event_kind → process_state (None = pas de changement)
_EVENT_KIND_TO_STATE: Dict[str, Optional[str]] = {
    "started":   "running",
    "restarted": "running",
    "stopped":   "stopped",
    "crashed":   "crashed",
    "error":     None,
}


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class RuntimeWatcherError(Exception):
    """Erreur générique du Runtime Watcher."""


# ──────────────────────────────────────────────────────────────────────────────
# Enums et dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class RuntimeHealth(Enum):
    HEALTHY    = "healthy"
    DEGRADED   = "degraded"
    UNHEALTHY  = "unhealthy"
    CRASH_LOOP = "crash_loop"
    UNKNOWN    = "unknown"


@dataclass(frozen=True)
class RuntimeSnapshot:
    server_id: str
    process_state: str
    uptime_seconds: float
    restart_count: int
    crash_count_window: int
    last_transition_ts: Optional[str]
    last_error_code: Optional[str]
    transitions_recent: List[Tuple[str, str]]


@dataclass(frozen=True)
class RuntimeReport:
    server_id: str
    health: RuntimeHealth
    snapshot: RuntimeSnapshot
    anomalies: List[str]


@dataclass
class _ServerEntry:
    """État interne pour un server_id enregistré."""
    server_id: str
    runner: Any  # duck-typed : doit exposer .state() callable
    process_state: str = "unknown"
    last_transition_ts: Optional[datetime] = None
    first_running_ts: Optional[datetime] = None  # pour uptime
    restart_count: int = 0
    last_error_code: Optional[str] = None
    last_state_read_ok: bool = True
    crash_timestamps: Deque[datetime] = field(default_factory=deque)
    transitions: Deque[Tuple[datetime, str]] = field(default_factory=deque)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _validate_server_id(server_id: Any) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise RuntimeWatcherError(
            f"Invalid server_id (regex violated): {server_id!r}"
        )
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        raise RuntimeWatcherError(
            f"Invalid server_id (path traversal): {server_id!r}"
        )
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        raise RuntimeWatcherError(
            f"Invalid server_id (Windows reserved name: {stem!r})"
        )


def _validate_error_code(error_code: Any) -> None:
    if error_code is None:
        return
    if not isinstance(error_code, str) or not _ERROR_CODE_RE.match(error_code):
        raise RuntimeWatcherError(
            "Invalid error_code (must match ^[a-z0-9_:-]{1,64}$). "
            "Use short codes like 'exit_nonzero', 'init_timeout'. "
            "Raw stderr / paths / emails forbidden."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Runtime Watcher
# ──────────────────────────────────────────────────────────────────────────────


class RuntimeWatcher:
    """Surveillance passive des MCP servers."""

    def __init__(
        self,
        snapshots_dir: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
        crash_loop_window_s: int = 300,
        crash_loop_threshold: int = 3,
        transitions_max_history: int = 50,
    ):
        if crash_loop_window_s <= 0:
            raise RuntimeWatcherError("crash_loop_window_s must be > 0")
        if crash_loop_threshold <= 0:
            raise RuntimeWatcherError("crash_loop_threshold must be > 0")
        if transitions_max_history <= 0:
            raise RuntimeWatcherError("transitions_max_history must be > 0")

        self._snapshots_dir = snapshots_dir or (
            DATA_DIR / _DEFAULT_DIRNAME / _SNAPSHOTS_SUBDIR
        )
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._crash_loop_window_s = crash_loop_window_s
        self._crash_loop_threshold = crash_loop_threshold
        self._transitions_max_history = transitions_max_history

        self._registry: Dict[str, _ServerEntry] = {}

        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def snapshots_dir(self) -> Path:
        return self._snapshots_dir

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def crash_loop_window_s(self) -> int:
        return self._crash_loop_window_s

    @property
    def crash_loop_threshold(self) -> int:
        return self._crash_loop_threshold

    @property
    def transitions_max_history(self) -> int:
        return self._transitions_max_history

    # ── Audit (sans PII) ──────────────────────────────────────────────────

    def _append_audit(self, event: str, **fields: Any) -> None:
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.runtime_watcher] audit write failed: {e}")

    # ── Register / unregister ────────────────────────────────────────────

    def register_runner(self, server_id: str, runner: Any) -> None:
        """Enregistre un runner sous server_id. Raise si déjà enregistré."""
        _validate_server_id(server_id)
        if runner is None:
            raise RuntimeWatcherError("runner must not be None")
        if not callable(getattr(runner, "state", None)):
            raise RuntimeWatcherError(
                "runner must expose a callable .state() method"
            )
        if server_id in self._registry:
            raise RuntimeWatcherError(
                f"server_id {server_id!r} already registered"
            )
        self._registry[server_id] = _ServerEntry(
            server_id=server_id,
            runner=runner,
        )
        self._append_audit("runner_registered", server_id=server_id)

    def unregister_runner(self, server_id: str) -> bool:
        """Idempotent : retire le runner. Retourne True si supprimé."""
        _validate_server_id(server_id)
        if server_id not in self._registry:
            return False
        del self._registry[server_id]
        self._append_audit("runner_unregistered", server_id=server_id)
        return True

    def list_watched_servers(self) -> List[str]:
        return sorted(self._registry.keys())

    def is_registered(self, server_id: str) -> bool:
        _validate_server_id(server_id)
        return server_id in self._registry

    # ── Transitions interne ──────────────────────────────────────────────

    def _append_transition(
        self, server_id: str, new_state: str, when: Optional[datetime] = None
    ) -> None:
        entry = self._registry[server_id]
        ts = when or _now_utc()
        entry.process_state = new_state
        entry.last_transition_ts = ts
        entry.transitions.append((ts, new_state))
        # Cap history
        while len(entry.transitions) > self._transitions_max_history:
            entry.transitions.popleft()
        # uptime tracking : première transition RUNNING observée
        if new_state == "running" and entry.first_running_ts is None:
            entry.first_running_ts = ts
        # Si transition vers non-running, reset uptime base
        if new_state != "running":
            entry.first_running_ts = None

    def _prune_crash_window(self, entry: _ServerEntry, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self._crash_loop_window_s)
        while entry.crash_timestamps and entry.crash_timestamps[0] < cutoff:
            entry.crash_timestamps.popleft()

    # ── record_event (push) ──────────────────────────────────────────────

    def record_event(
        self,
        server_id: str,
        event_kind: str,
        error_code: Optional[str] = None,
    ) -> None:
        """Enregistre un événement explicite pour un server enregistré.

        Mapping event_kind → process_state :
          started, restarted → running (+restart_count++)
          stopped            → stopped
          crashed            → crashed (+crash_timestamps append)
          error              → no state change (only last_error_code update)
        """
        _validate_server_id(server_id)
        if not isinstance(event_kind, str) or event_kind not in _VALID_EVENT_KINDS:
            raise RuntimeWatcherError(
                f"Invalid event_kind {event_kind!r}. "
                f"Valid: {sorted(_VALID_EVENT_KINDS)}"
            )
        _validate_error_code(error_code)
        if error_code is not None and event_kind not in ("crashed", "error"):
            raise RuntimeWatcherError(
                f"error_code is only allowed for event_kind 'crashed' or "
                f"'error', got event_kind={event_kind!r}"
            )
        if server_id not in self._registry:
            raise RuntimeWatcherError(
                f"server_id {server_id!r} not registered"
            )

        now = _now_utc()
        entry = self._registry[server_id]
        new_state = _EVENT_KIND_TO_STATE[event_kind]

        if new_state is not None:
            self._append_transition(server_id, new_state, when=now)
            if event_kind in ("started", "restarted"):
                entry.restart_count += 1
            if event_kind == "crashed":
                entry.crash_timestamps.append(now)
                self._prune_crash_window(entry, now)

        # error_code updated for crashed AND error (only if provided)
        if error_code is not None and event_kind in ("crashed", "error"):
            entry.last_error_code = error_code

        self._append_audit(
            "event_recorded",
            server_id=server_id,
            event_kind=event_kind,
            error_code=error_code,
        )

    # ── take_snapshot (poll) ─────────────────────────────────────────────

    def _read_runner_state(self, entry: _ServerEntry) -> Optional[str]:
        """Lit runner.state() (méthode). Retourne None si lecture échoue.

        Met à jour entry.last_state_read_ok :
          - False si state() raise ou retourne une valeur illisible
          - True si lecture réussie et état normalisé extrait
        """
        try:
            raw = entry.runner.state()
        except Exception:  # noqa: BLE001
            entry.last_state_read_ok = False
            return None
        if raw is None:
            entry.last_state_read_ok = False
            return None
        if isinstance(raw, str):
            entry.last_state_read_ok = True
            return raw.lower()
        value = getattr(raw, "value", None)
        if isinstance(value, str):
            entry.last_state_read_ok = True
            return value.lower()
        name = getattr(raw, "name", None)
        if isinstance(name, str):
            entry.last_state_read_ok = True
            return name.lower()
        entry.last_state_read_ok = False
        return None

    def _build_snapshot(
        self, entry: _ServerEntry, now: datetime
    ) -> RuntimeSnapshot:
        self._prune_crash_window(entry, now)
        uptime = 0.0
        if (
            entry.process_state == "running"
            and entry.first_running_ts is not None
        ):
            uptime = max(0.0, (now - entry.first_running_ts).total_seconds())
        transitions_recent = [
            (ts.isoformat(), state) for ts, state in entry.transitions
        ]
        return RuntimeSnapshot(
            server_id=entry.server_id,
            process_state=entry.process_state,
            uptime_seconds=uptime,
            restart_count=entry.restart_count,
            crash_count_window=len(entry.crash_timestamps),
            last_transition_ts=(
                entry.last_transition_ts.isoformat()
                if entry.last_transition_ts is not None else None
            ),
            last_error_code=entry.last_error_code,
            transitions_recent=transitions_recent,
        )

    def take_snapshot(self, server_id: str) -> RuntimeSnapshot:
        """Lit runner.state() (méthode), met à jour transitions si
        nouveau state, persiste snapshot disque, retourne snapshot."""
        _validate_server_id(server_id)
        if server_id not in self._registry:
            raise RuntimeWatcherError(
                f"server_id {server_id!r} not registered"
            )
        entry = self._registry[server_id]
        now = _now_utc()

        observed = self._read_runner_state(entry)
        if observed is not None and observed != entry.process_state:
            # Nouvelle transition observée via poll
            self._append_transition(server_id, observed, when=now)

        snapshot = self._build_snapshot(entry, now)
        self._persist_snapshot(snapshot)
        self._append_audit("snapshot_taken", server_id=server_id)
        return snapshot

    def take_all_snapshots(self) -> Dict[str, RuntimeSnapshot]:
        return {sid: self.take_snapshot(sid) for sid in self.list_watched_servers()}

    # ── Anomalies & report ───────────────────────────────────────────────

    def detect_anomalies(self, server_id: str) -> List[str]:
        _validate_server_id(server_id)
        if server_id not in self._registry:
            return ["runner_missing"]
        entry = self._registry[server_id]
        now = _now_utc()
        self._prune_crash_window(entry, now)
        anomalies: List[str] = []
        crash_count = len(entry.crash_timestamps)
        if crash_count >= self._crash_loop_threshold:
            anomalies.append("crash_loop")
        elif crash_count > 0:
            anomalies.append("recent_crash")
        if entry.process_state == "crashed":
            anomalies.append("state_crashed")
        if entry.process_state == "unknown" or entry.last_state_read_ok is False:
            anomalies.append("runner_unknown")
        return anomalies

    def _compute_health(
        self, anomalies: List[str], registered: bool
    ) -> RuntimeHealth:
        if not registered or "runner_missing" in anomalies:
            return RuntimeHealth.UNKNOWN
        if "crash_loop" in anomalies:
            return RuntimeHealth.CRASH_LOOP
        if "state_crashed" in anomalies:
            return RuntimeHealth.UNHEALTHY
        if "runner_unknown" in anomalies:
            return RuntimeHealth.UNKNOWN
        if "recent_crash" in anomalies:
            return RuntimeHealth.DEGRADED
        return RuntimeHealth.HEALTHY

    def get_report(self, server_id: str) -> RuntimeReport:
        _validate_server_id(server_id)
        if server_id not in self._registry:
            empty_snap = RuntimeSnapshot(
                server_id=server_id,
                process_state="unknown",
                uptime_seconds=0.0,
                restart_count=0,
                crash_count_window=0,
                last_transition_ts=None,
                last_error_code=None,
                transitions_recent=[],
            )
            return RuntimeReport(
                server_id=server_id,
                health=RuntimeHealth.UNKNOWN,
                snapshot=empty_snap,
                anomalies=["runner_missing"],
            )
        snapshot = self.take_snapshot(server_id)
        anomalies = self.detect_anomalies(server_id)
        health = self._compute_health(anomalies, registered=True)
        if anomalies:
            self._append_audit(
                "anomaly_detected",
                server_id=server_id,
                anomalies=anomalies,
                health=health.value,
            )
        return RuntimeReport(
            server_id=server_id,
            health=health,
            snapshot=snapshot,
            anomalies=anomalies,
        )

    # ── Persistance disque ───────────────────────────────────────────────

    def _snapshot_path(self, server_id: str) -> Path:
        return self._snapshots_dir / f"{server_id}.json"

    def _snapshot_to_dict(self, snapshot: RuntimeSnapshot) -> Dict[str, Any]:
        return {
            "server_id": snapshot.server_id,
            "process_state": snapshot.process_state,
            "uptime_seconds": snapshot.uptime_seconds,
            "restart_count": snapshot.restart_count,
            "crash_count_window": snapshot.crash_count_window,
            "last_transition_ts": snapshot.last_transition_ts,
            "last_error_code": snapshot.last_error_code,
            "transitions_recent": [
                list(t) for t in snapshot.transitions_recent
            ],
        }

    def _persist_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        path = self._snapshot_path(snapshot.server_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, self._snapshot_to_dict(snapshot))

    def load_snapshot_from_disk(
        self, server_id: str
    ) -> Optional[RuntimeSnapshot]:
        _validate_server_id(server_id)
        path = self._snapshot_path(server_id)
        if not path.exists():
            return None
        data = safe_read_json(path, default=None)
        if not isinstance(data, dict):
            return None
        try:
            transitions = [
                (t[0], t[1]) for t in data.get("transitions_recent", [])
                if isinstance(t, (list, tuple)) and len(t) == 2
            ]
            return RuntimeSnapshot(
                server_id=str(data["server_id"]),
                process_state=str(data["process_state"]),
                uptime_seconds=float(data["uptime_seconds"]),
                restart_count=int(data["restart_count"]),
                crash_count_window=int(data["crash_count_window"]),
                last_transition_ts=data.get("last_transition_ts"),
                last_error_code=data.get("last_error_code"),
                transitions_recent=transitions,
            )
        except (KeyError, ValueError, TypeError):
            return None

    def list_persisted_snapshots(self) -> List[str]:
        if not self._snapshots_dir.exists():
            return []
        return sorted(
            p.stem for p in self._snapshots_dir.glob("*.json") if p.is_file()
        )
