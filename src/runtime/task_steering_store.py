"""Atomic, persistent steering queue backed by TaskOrchestrator."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import uuid

TEXT_KINDS = {"add_constraint", "replace_constraint", "remove_constraint", "reprioritize"}
CONTROL_KINDS = {"pause", "resume", "cancel"}
STEERING_KINDS = TEXT_KINDS | CONTROL_KINDS
DELIVERY_POLICIES = {"next_checkpoint", "urgent_safe_boundary"}
TERMINAL_TASK_STATES = {"done", "failed", "cancelled"}
TERMINAL_COMMAND_STATES = {
    "applied", "partially_applied", "rejected", "superseded", "cancelled", "late",
}


class SteeringConflict(ValueError):
    """An idempotency key was reused with different content."""


class SteeringQueueFull(RuntimeError):
    """The queue cannot compact without dropping an active command."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _canonical_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class TaskSteeringStore:
    """Own steering mutations; mutable state only lives in the orchestrator."""

    def __init__(
        self,
        orchestrator: Any,
        *,
        max_commands: Optional[int] = None,
        max_text_chars: Optional[int] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.max_commands = max_commands or _positive_env("LUMENA_STEERING_HISTORY_MAX", 200, 10)
        self.max_text_chars = max_text_chars or _positive_env("LUMENA_STEERING_TEXT_MAX_CHARS", 8000, 64)

    def _mutate(
        self,
        task_id: str,
        callback: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    ) -> Any:
        atomic = getattr(self.orchestrator, "mutate_task_metadata", None)
        if callable(atomic):
            return atomic(task_id, callback)
        # Compatibility for small test doubles; production is always atomic.
        record = self.orchestrator.get_task(task_id)
        if not record:
            raise KeyError(task_id)
        metadata = dict(record.get("metadata") or {})
        result = callback(metadata, dict(record))
        self.orchestrator.set_task_metadata(task_id, **metadata)
        return result

    @staticmethod
    def _commands(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [dict(item) for item in metadata.get("steering_commands") or [] if isinstance(item, dict)]

    def _compact(self, metadata: Dict[str, Any], commands: List[Dict[str, Any]]) -> None:
        overflow = len(commands) - self.max_commands
        if overflow <= 0:
            metadata["steering_commands"] = commands
            return
        removable = [
            index for index, command in enumerate(commands)
            if command.get("status") in TERMINAL_COMMAND_STATES
        ]
        if len(removable) < overflow:
            raise SteeringQueueFull("steering_queue_full")
        removed_indexes = set(removable[:overflow])
        metadata["steering_commands"] = [
            command for index, command in enumerate(commands) if index not in removed_indexes
        ]
        summary = dict(metadata.get("steering_compaction") or {})
        summary["compacted_total"] = int(summary.get("compacted_total", 0)) + overflow
        summary["last_compacted_at"] = _now_iso()
        summary["history_max"] = self.max_commands
        metadata["steering_compaction"] = summary

    def enqueue(
        self,
        task_id: str,
        kind: str,
        *,
        text: str = "",
        delivery_policy: str = "next_checkpoint",
        idempotency_key: Optional[str] = None,
        source_channel: str = "internal",
        source_conversation_id: str = "",
        requester_user_id: str = "",
        owner_user_id: str = "",
        root_task_id: Optional[str] = None,
        parent_command_id: Optional[str] = None,
        supersedes: Iterable[str] = (),
        scope: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        kind = str(kind or "").strip()
        if kind not in STEERING_KINDS:
            raise ValueError(f"unsupported steering kind: {kind}")
        delivery_policy = str(delivery_policy or "next_checkpoint").strip()
        if delivery_policy not in DELIVERY_POLICIES:
            raise ValueError(f"unsupported delivery policy: {delivery_policy}")
        text = str(text or "").strip()
        if kind in TEXT_KINDS and not text:
            raise ValueError("steering_text_required")
        if len(text) > self.max_text_chars:
            raise ValueError("steering_text_too_long")
        idem = str(idempotency_key or "").strip()
        supersedes_tuple = tuple(dict.fromkeys(str(item).strip() for item in supersedes if str(item).strip()))
        fingerprint = _canonical_hash({
            "task_id": task_id, "kind": kind, "text": text,
            "delivery_policy": delivery_policy, "supersedes": supersedes_tuple,
            "scope": scope or {},
        })

        def transaction(metadata: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
            commands = self._commands(metadata)
            if idem:
                for existing in commands:
                    if existing.get("idempotency_key") != idem:
                        continue
                    if existing.get("request_fingerprint") != fingerprint:
                        raise SteeringConflict("idempotency_conflict")
                    return dict(existing)
            known_ids = {str(item.get("command_id") or "") for item in commands}
            if any(item not in known_ids for item in supersedes_tuple):
                raise ValueError("invalid_supersedes")
            sequence = int(metadata.get("steering_next_sequence", 1) or 1)
            now = _now_iso()
            terminal = str(task.get("state") or "") in TERMINAL_TASK_STATES
            command = {
                "schema_version": 2,
                "command_id": f"steer_{uuid.uuid4().hex}",
                "mission_id": task_id,
                "root_task_id": str(root_task_id or task_id),
                "target_task_id": task_id,
                "parent_command_id": parent_command_id,
                "sequence": sequence,
                "kind": kind,
                "delivery_policy": delivery_policy,
                "text": text,
                "payload": dict(payload or ({"text": text} if text else {})),
                "source_channel": str(source_channel or "internal"),
                "source_conversation_id": str(source_conversation_id or ""),
                "requester_user_id": str(requester_user_id or ""),
                "owner_user_id": str(owner_user_id or ""),
                "created_at": now,
                "received_at": now,
                "status": "late" if terminal else "pending",
                "supersedes": list(supersedes_tuple),
                "scope": dict(scope or {}),
                "delivery": {},
                "outcome": {},
                "idempotency_key": idem or None,
                "request_fingerprint": fingerprint,
                "applied_at": None,
            }
            commands.append(command)
            metadata["steering_next_sequence"] = sequence + 1
            metadata["steering_revision"] = int(metadata.get("steering_revision", 0)) + 1
            initial_objective = str(metadata.get("objective") or task.get("message_preview") or "")
            metadata.setdefault("initial_objective", initial_objective)
            metadata.setdefault("objective_revision", 0)
            metadata.setdefault("active_constraints", [])
            metadata.setdefault("superseded_constraints", [])
            if not terminal:
                for existing in commands[:-1]:
                    is_superseded = existing.get("command_id") in supersedes_tuple
                    if is_superseded and existing.get("status") not in TERMINAL_COMMAND_STATES:
                        existing["status"] = "superseded"
                        existing["superseded_at"] = now
                        existing["superseded_by"] = command["command_id"]
            self._compact(metadata, commands)
            return dict(command)

        command = self._mutate(task_id, transaction)
        from .steering_observability import publish_steering_event
        publish_steering_event(
            "steering_late" if command.get("status") == "late" else "steering_pending",
            command,
        )
        if command.get("status") == "late":
            return command
        if kind == "cancel":
            self.orchestrator.cancel_task(task_id, propagate=True)
            self.record_outcome(task_id, command["command_id"], "applied", {"control": "cancel"})
        elif kind in {"pause", "resume"}:
            pause_requested = kind == "pause"
            self._mutate(
                task_id,
                lambda metadata, _task: metadata.update(
                    pause_requested=pause_requested, paused=False,
                ),
            )
            if kind == "resume":
                self.record_outcome(task_id, command["command_id"], "applied", {"control": "resume"})
        return command

    def list(self, task_id: str) -> List[Dict[str, Any]]:
        record = self.orchestrator.get_task(task_id)
        if not record:
            raise KeyError(task_id)
        commands = self._commands(record.get("metadata") or {})
        return sorted(commands, key=lambda item: int(item.get("sequence", 0)))

    def get(self, task_id: str, command_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.list(task_id) if item.get("command_id") == command_id), None)

    def deliver_pending_text(self, task_id: str, *, consumer_id: str = "react") -> Tuple[str, List[str]]:
        def transaction(metadata: Dict[str, Any], _task: Dict[str, Any]) -> Tuple[str, List[str]]:
            commands = self._commands(metadata)
            pending = [
                item for item in commands
                if item.get("status") == "pending" and item.get("kind") in TEXT_KINDS
            ]
            pending.sort(key=lambda item: (
                0 if item.get("delivery_policy") == "urgent_safe_boundary" else 1,
                int(item.get("sequence", 0)),
            ))
            now = _now_iso()
            texts: List[str] = []
            ids: List[str] = []
            for command in pending:
                command["status"] = "delivered"
                previous_delivery = dict(command.get("delivery") or {})
                command["delivery"] = {
                    **previous_delivery,
                    "consumer_id": consumer_id,
                    "delivered_at": now,
                    "attempt": int(previous_delivery.get("attempt", 0)) + 1,
                }
                value = str(command.get("text") or (command.get("payload") or {}).get("text") or "").strip()
                if value:
                    texts.append(value)
                ids.append(str(command.get("command_id") or ""))
            if ids:
                metadata["steering_commands"] = commands
            if not texts:
                return "", ids
            return (
                "ORIENTATIONS UTILISATEUR RECUES PENDANT LE TRAVAIL "
                "(poursuis l'objectif initial et tout ce qui n'est pas explicitement retire):\n- "
                + "\n- ".join(texts),
                ids,
            )
        result = self._mutate(task_id, transaction)
        from .steering_observability import publish_steering_event
        for command_id in result[1]:
            command = self.get(task_id, command_id)
            if command:
                publish_steering_event("steering_delivered", command)
        return result

    def recover_orphaned_deliveries(self, task_id: str) -> List[str]:
        """Requeue commands persisted as delivered but never incorporated."""
        def transaction(metadata: Dict[str, Any], _task: Dict[str, Any]) -> List[str]:
            commands = self._commands(metadata)
            recovered: List[str] = []
            for command in commands:
                if command.get("status") != "delivered":
                    continue
                command["status"] = "pending"
                delivery = dict(command.get("delivery") or {})
                delivery["recovery_count"] = int(delivery.get("recovery_count", 0)) + 1
                delivery["recovered_at"] = _now_iso()
                command["delivery"] = delivery
                recovered.append(str(command.get("command_id") or ""))
            if recovered:
                metadata["steering_commands"] = commands
            return recovered
        recovered = self._mutate(task_id, transaction)
        if recovered:
            from .steering_observability import publish_steering_event
            for command_id in recovered:
                command = self.get(task_id, command_id)
                if command:
                    publish_steering_event("steering_recovered", command)
        return recovered

    def transition(
        self,
        task_id: str,
        command_id: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        allowed = {"incorporated", "applied", "partially_applied", "rejected", "cancelled"}
        if status not in allowed:
            raise ValueError(f"unsupported steering status: {status}")

        def transaction(metadata: Dict[str, Any], _task: Dict[str, Any]) -> Dict[str, Any]:
            commands = self._commands(metadata)
            for command in commands:
                if command.get("command_id") != command_id:
                    continue
                if command.get("status") in {"superseded", "late", "cancelled"}:
                    return dict(command)
                now = _now_iso()
                command["status"] = status
                command["outcome"] = {"recorded_at": now, **dict(details or {})}
                if status in {"applied", "partially_applied"}:
                    command["applied_at"] = now
                metadata["steering_commands"] = commands
                return dict(command)
            raise KeyError(command_id)
        command = self._mutate(task_id, transaction)
        from .steering_observability import publish_steering_event
        publish_steering_event(f"steering_{command.get('status')}", command)
        return command

    def record_outcome(
        self,
        task_id: str,
        command_id: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.transition(task_id, command_id, status, details)

    def acknowledge_control(self, task_id: str, kind: str) -> List[str]:
        ids = [
            str(item.get("command_id") or "") for item in self.list(task_id)
            if item.get("kind") == kind and item.get("status") == "pending"
        ]
        for command_id in ids:
            self.record_outcome(task_id, command_id, "applied", {"control": kind})
        return ids
