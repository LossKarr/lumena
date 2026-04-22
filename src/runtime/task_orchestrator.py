"""In-memory task orchestration for long running omnichannel flows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Literal, Optional
import json
import os
import uuid

TaskState = Literal["queued", "running", "waiting_io", "checkpointed", "done", "failed", "cancelled"]
_VALID_TASK_STATES = {"queued", "running", "waiting_io", "checkpointed", "done", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex}"


@dataclass
class TaskRecord:
    task_id: str
    conversation_id: str
    channel: str
    message_preview: str
    state: TaskState = "queued"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_checkpoint: Optional[Dict[str, Any]] = None
    checkpoint_history: List[Dict[str, Any]] = field(default_factory=list)
    checkpoint_compaction: Dict[str, Any] = field(default_factory=dict)
    result_summary: Optional[str] = None
    last_error: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TaskOrchestrator:
    def __init__(
        self,
        persistence_path: Optional[str | Path] = None,
        checkpoint_history_max: Optional[int] = None,
        checkpoint_compact_min_drop: Optional[int] = None,
    ) -> None:
        self._lock = Lock()
        self._tasks: Dict[str, TaskRecord] = {}
        self._conversation_index: Dict[str, List[str]] = {}
        self._persistence_path = self._resolve_persistence_path(persistence_path)
        self._checkpoint_history_max = self._resolve_positive_int(
            explicit_value=checkpoint_history_max,
            env_key="LUMENA_TASK_CHECKPOINT_HISTORY_MAX",
            default=40,
            minimum=1,
        )
        self._checkpoint_compact_min_drop = self._resolve_positive_int(
            explicit_value=checkpoint_compact_min_drop,
            env_key="LUMENA_TASK_CHECKPOINT_COMPACT_MIN_DROP",
            default=1,
            minimum=1,
        )
        self._persistence_last_saved_at: Optional[str] = None
        self._persistence_last_error: Optional[str] = None
        self._load_from_disk()

    @staticmethod
    def _resolve_persistence_path(persistence_path: Optional[str | Path]) -> Optional[Path]:
        raw = str(persistence_path or os.getenv("LUMENA_TASK_ORCHESTRATOR_STATE_PATH", "")).strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    @staticmethod
    def _resolve_positive_int(
        *,
        explicit_value: Optional[int],
        env_key: str,
        default: int,
        minimum: int = 1,
    ) -> int:
        candidate = explicit_value
        if candidate is None:
            raw = os.getenv(env_key)
            if raw is not None:
                try:
                    candidate = int(str(raw).strip())
                except (TypeError, ValueError):
                    candidate = None
        if candidate is None:
            candidate = default
        return max(minimum, int(candidate))

    @staticmethod
    def _normalize_task_state(raw_state: Any) -> TaskState:
        state = str(raw_state or "queued").strip().lower()
        if state not in _VALID_TASK_STATES:
            return "queued"
        return state  # type: ignore[return-value]

    @staticmethod
    def _task_record_from_dict(payload: Dict[str, Any]) -> TaskRecord:
        metadata = payload.get("metadata")
        checkpoint = payload.get("last_checkpoint")
        checkpoint_history_raw = payload.get("checkpoint_history")
        checkpoint_compaction_raw = payload.get("checkpoint_compaction")

        checkpoint_history: List[Dict[str, Any]] = []
        if isinstance(checkpoint_history_raw, list):
            for item in checkpoint_history_raw:
                if isinstance(item, dict):
                    checkpoint_history.append(dict(item))
        return TaskRecord(
            task_id=str(payload.get("task_id") or _new_task_id()),
            conversation_id=str(payload.get("conversation_id") or "conv_unknown"),
            channel=str(payload.get("channel") or "web").strip().lower() or "web",
            message_preview=str(payload.get("message_preview") or "")[:300],
            state=TaskOrchestrator._normalize_task_state(payload.get("state")),
            created_at=str(payload.get("created_at") or _now_iso()),
            updated_at=str(payload.get("updated_at") or _now_iso()),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            last_checkpoint=dict(checkpoint) if isinstance(checkpoint, dict) else None,
            checkpoint_history=checkpoint_history,
            checkpoint_compaction=(
                dict(checkpoint_compaction_raw)
                if isinstance(checkpoint_compaction_raw, dict)
                else {}
            ),
            result_summary=(
                str(payload.get("result_summary"))[:1000]
                if payload.get("result_summary") is not None
                else None
            ),
            last_error=(
                str(payload.get("last_error"))[:800]
                if payload.get("last_error") is not None
                else None
            ),
            cancel_requested=bool(payload.get("cancel_requested", False)),
        )

    @staticmethod
    def _extract_checkpoint_phase(entry: Dict[str, Any]) -> str:
        payload = entry.get("payload")
        if isinstance(payload, dict):
            phase = payload.get("phase")
            if phase is not None:
                value = str(phase).strip()
                if value:
                    return value
        return "unknown"

    def _compact_checkpoint_history_locked(self, record: TaskRecord) -> None:
        if self._checkpoint_history_max <= 0:
            return
        overflow = len(record.checkpoint_history) - self._checkpoint_history_max
        if overflow < self._checkpoint_compact_min_drop:
            return

        dropped = record.checkpoint_history[:overflow]
        record.checkpoint_history = record.checkpoint_history[overflow:]

        summary = dict(record.checkpoint_compaction or {})
        compacted_by_phase = summary.get("compacted_by_phase")
        if not isinstance(compacted_by_phase, dict):
            compacted_by_phase = {}

        for item in dropped:
            phase = self._extract_checkpoint_phase(item)
            compacted_by_phase[phase] = int(compacted_by_phase.get(phase, 0)) + 1

        summary["compacted_total"] = int(summary.get("compacted_total", 0)) + len(dropped)
        summary["compacted_by_phase"] = compacted_by_phase
        summary["last_compacted_at"] = _now_iso()
        summary["history_max"] = self._checkpoint_history_max
        record.checkpoint_compaction = summary

    def _append_checkpoint_locked(self, record: TaskRecord, checkpoint: Dict[str, Any]) -> None:
        payload = dict(checkpoint or {})
        record.last_checkpoint = payload
        record.checkpoint_history.append(
            {
                "ts": _now_iso(),
                "payload": payload,
            }
        )
        self._compact_checkpoint_history_locked(record)

    def _snapshot_payload_locked(self, *, saved_at: str) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "saved_at": saved_at,
            "tasks": [record.to_dict() for record in self._tasks.values()],
            "conversation_index": {
                conversation_id: list(task_ids)
                for conversation_id, task_ids in self._conversation_index.items()
            },
        }

    def _persist_locked(self) -> None:
        if not self._persistence_path:
            return
        save_ts = _now_iso()
        payload = self._snapshot_payload_locked(saved_at=save_ts)
        tmp_path = self._persistence_path.with_suffix(f"{self._persistence_path.suffix}.tmp")
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._persistence_path)
            self._persistence_last_saved_at = save_ts
            self._persistence_last_error = None
        except Exception as exc:
            self._persistence_last_error = str(exc)[:800]
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass  # tmp file cleanup best-effort

    def _load_from_disk(self) -> None:
        if not self._persistence_path or not self._persistence_path.exists():
            return
        try:
            raw_payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._persistence_last_error = str(exc)[:800]
            return

        loaded_tasks: Dict[str, TaskRecord] = {}
        loaded_index: Dict[str, List[str]] = {}
        for item in raw_payload.get("tasks", []):
            if not isinstance(item, dict):
                continue
            record = self._task_record_from_dict(item)
            self._compact_checkpoint_history_locked(record)
            loaded_tasks[record.task_id] = record
            loaded_index.setdefault(record.conversation_id, []).append(record.task_id)

        # Keep index only for known tasks and preserve ordering from persisted payload when possible.
        persisted_index = raw_payload.get("conversation_index")
        if isinstance(persisted_index, dict):
            remapped: Dict[str, List[str]] = {}
            for conversation_id, task_ids in persisted_index.items():
                if not isinstance(task_ids, list):
                    continue
                ordered = [task_id for task_id in task_ids if task_id in loaded_tasks]
                if ordered:
                    remapped[str(conversation_id)] = ordered
            for conversation_id, task_ids in loaded_index.items():
                remapped.setdefault(conversation_id, task_ids)
            loaded_index = remapped

        self._tasks = loaded_tasks
        self._conversation_index = loaded_index
        self._persistence_last_saved_at = str(raw_payload.get("saved_at") or _now_iso())
        self._persistence_last_error = None

    def flush(self) -> None:
        with self._lock:
            self._persist_locked()

    def start_task(
        self,
        *,
        conversation_id: str,
        channel: str,
        message_preview: str,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> TaskRecord:
        with self._lock:
            effective_id = (task_id or "").strip() or _new_task_id()
            existing = self._tasks.get(effective_id)
            if existing:
                return existing
            record = TaskRecord(
                task_id=effective_id,
                conversation_id=conversation_id.strip() or "conv_unknown",
                channel=channel.strip().lower() or "web",
                message_preview=(message_preview or "")[:300],
                metadata=dict(metadata or {}),
            )
            self._tasks[effective_id] = record
            self._conversation_index.setdefault(record.conversation_id, []).append(effective_id)
            self._persist_locked()
            return record

    def update_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        checkpoint: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        result_summary: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            record.state = state
            record.updated_at = _now_iso()
            if checkpoint is not None:
                self._append_checkpoint_locked(record, checkpoint)
            if error is not None:
                record.last_error = str(error)[:800]
            if result_summary is not None:
                record.result_summary = str(result_summary)[:1000]
            self._persist_locked()
            return record

    def mark_running(self, task_id: str) -> Optional[TaskRecord]:
        return self.update_state(task_id, "running")

    def mark_checkpoint(self, task_id: str, checkpoint: Dict[str, Any]) -> Optional[TaskRecord]:
        return self.update_state(task_id, "checkpointed", checkpoint=checkpoint)

    def mark_waiting_io(
        self,
        task_id: str,
        error: Optional[str] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskRecord]:
        return self.update_state(task_id, "waiting_io", error=error, checkpoint=checkpoint)

    def mark_done(self, task_id: str, result_summary: Optional[str] = None) -> Optional[TaskRecord]:
        return self.update_state(task_id, "done", result_summary=result_summary)

    def mark_failed(self, task_id: str, error: str) -> Optional[TaskRecord]:
        return self.update_state(task_id, "failed", error=error)

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"success": False, "message": "task_not_found", "task_id": task_id}
            record.cancel_requested = True
            if record.state not in {"done", "failed", "cancelled"}:
                record.state = "cancelled"
            record.updated_at = _now_iso()
            self._persist_locked()
            return {"success": True, "task": record.to_dict()}

    def resume_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"success": False, "message": "task_not_found", "task_id": task_id}
            if record.cancel_requested or record.state == "cancelled":
                return {"success": False, "message": "task_cancelled", "task_id": task_id}
            if record.state in {"done"}:
                return {"success": False, "message": "task_already_done", "task_id": task_id}
            record.state = "running"
            record.updated_at = _now_iso()
            self._persist_locked()
            return {"success": True, "task": record.to_dict()}

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
            return bool(record and record.cancel_requested)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._tasks.get(task_id)
            return record.to_dict() if record else None

    def list_all_tasks(self, limit: int = 200, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retourne toutes les taches, triees par updated_at DESC."""
        with self._lock:
            records = list(self._tasks.values())
        if state_filter:
            records = [r for r in records if r.state == state_filter]
        records.sort(key=lambda r: r.updated_at or r.created_at or "", reverse=True)
        return [r.to_dict() for r in records[:max(1, int(limit))]]

    def get_conversation_tasks(self, conversation_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            ids = list(self._conversation_index.get(conversation_id, []))[-max(1, int(limit)) :]
            out: List[Dict[str, Any]] = []
            for task_id in ids:
                record = self._tasks.get(task_id)
                if record:
                    out.append(record.to_dict())
            return out

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._tasks)
            backlog = sum(1 for record in self._tasks.values() if record.state in {"queued", "running", "waiting_io", "checkpointed"})
            waiting_io = sum(1 for record in self._tasks.values() if record.state == "waiting_io")
            cancelled = sum(1 for record in self._tasks.values() if record.state == "cancelled")
            failed = sum(1 for record in self._tasks.values() if record.state == "failed")
            done = sum(1 for record in self._tasks.values() if record.state == "done")
            checkpoints_live = sum(len(record.checkpoint_history) for record in self._tasks.values())
            checkpoints_compacted = sum(
                int(record.checkpoint_compaction.get("compacted_total", 0))
                for record in self._tasks.values()
            )
            return {
                "total_tasks": total,
                "backlog_tasks": backlog,
                "waiting_io_tasks": waiting_io,
                "done_tasks": done,
                "failed_tasks": failed,
                "cancelled_tasks": cancelled,
                "active_conversations": len(self._conversation_index),
                "checkpoint_history_max": self._checkpoint_history_max,
                "checkpoint_compact_min_drop": self._checkpoint_compact_min_drop,
                "checkpoints_live": checkpoints_live,
                "checkpoints_compacted_total": checkpoints_compacted,
                "persistence_enabled": self._persistence_path is not None,
                "persistence_path": str(self._persistence_path) if self._persistence_path else None,
                "persistence_last_saved_at": self._persistence_last_saved_at,
                "persistence_last_error": self._persistence_last_error,
            }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
