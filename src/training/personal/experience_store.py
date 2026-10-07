"""Durable append-only store for per-user learning experiences."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json
from src.utils.paths import DATA_DIR

from .contracts import (
    EXPERIENCE_TRANSITIONS,
    ExperienceState,
    LearningExperienceV1,
    canonical_json,
    sha256_json,
    utc_now,
    validate_transition,
)
from .redaction import has_unredacted_sensitive_data


_SAFE_PART_RE = re.compile(r"[^a-z0-9_-]+")


def safe_owner_segment(owner_scope: str) -> str:
    raw = str(owner_scope or "").strip().lower()
    if not raw:
        raise ValueError("owner_scope_required")
    slug = _SAFE_PART_RE.sub("-", raw).strip("-")[:48] or "owner"
    return f"{slug}-{sha256_json(raw)[:12]}"


@dataclass(frozen=True, slots=True)
class AppendResult:
    experience_id: str
    appended: bool
    duplicate_of: str | None
    content_hash: str


class ExperienceStore:
    """Store raw immutable experiences plus an independently rebuildable index."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_experiences_per_owner: int = 1_000_000,
        max_bytes_per_owner: int = 20 * 1024 * 1024 * 1024,
    ) -> None:
        self.root = Path(root) if root else DATA_DIR / "personal_model"
        self.max_experiences_per_owner = max(1, int(max_experiences_per_owner))
        self.max_bytes_per_owner = max(1024, int(max_bytes_per_owner))
        self._thread_lock = RLock()

    def owner_root(self, owner_scope: str) -> Path:
        if owner_scope == "owner:local":
            return self.root
        return self.root / "users" / safe_owner_segment(owner_scope)

    def _paths(self, owner_scope: str) -> dict[str, Path]:
        base = self.owner_root(owner_scope)
        return {
            "base": base,
            "events": base / "experiences" / "events.jsonl",
            "state_events": base / "index" / "state_events.jsonl",
            "annotations": base / "index" / "annotations.jsonl",
            "index": base / "index" / "experience_index.json",
            "quarantine": base / "quarantine" / "corrupt_records.jsonl",
            "lock": base / ".experience_store.lock",
        }

    @staticmethod
    def _empty_index(owner_scope: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "owner_scope": owner_scope,
            "records": {},
            "content_hashes": {},
            "counts": {},
            "updated_at": utc_now(),
        }

    def _load_index(self, owner_scope: str) -> dict[str, Any]:
        path = self._paths(owner_scope)["index"]
        data = safe_read_json(path, default={})
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("owner_scope") != owner_scope:
            return self._empty_index(owner_scope)
        if not isinstance(data.get("records"), dict) or not isinstance(data.get("content_hashes"), dict):
            return self._empty_index(owner_scope)
        return data

    @staticmethod
    def _append_line(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (canonical_json(value) + "\n").encode("utf-8")
        with open(path, "ab", buffering=0) as handle:
            handle.write(data)
            os.fsync(handle.fileno())

    @staticmethod
    def _recount(index: dict[str, Any]) -> None:
        counts: dict[str, int] = {}
        for record in index["records"].values():
            state = str(record.get("state", "raw"))
            counts[state] = counts.get(state, 0) + 1
        index["counts"] = counts
        index["updated_at"] = utc_now()

    def append(self, experience: LearningExperienceV1) -> AppendResult:
        if experience.privacy_state not in {"redacted", "approved_local", "approved_cloud"}:
            raise ValueError("experience_privacy_not_allowed")
        payload = experience.to_dict()
        if has_unredacted_sensitive_data(payload):
            raise ValueError("experience_contains_unredacted_sensitive_data")
        content_hash = payload["content_hash"]
        paths = self._paths(experience.owner_scope)
        lock = FileLock(str(paths["lock"]), timeout=10)

        with self._thread_lock, lock:
            index = self._load_index(experience.owner_scope)
            duplicate = index["content_hashes"].get(content_hash)
            if duplicate:
                return AppendResult(duplicate, False, duplicate, content_hash)
            if experience.experience_id in index["records"]:
                raise ValueError("experience_id_collision")
            if len(index["records"]) >= self.max_experiences_per_owner:
                raise ValueError("experience_quota_count_exceeded")
            current_bytes = paths["events"].stat().st_size if paths["events"].exists() else 0
            encoded_size = len(canonical_json(payload).encode("utf-8")) + 1
            if current_bytes + encoded_size > self.max_bytes_per_owner:
                raise ValueError("experience_quota_bytes_exceeded")

            line_number = len(index["records"]) + 1
            self._append_line(paths["events"], payload)
            index["records"][experience.experience_id] = {
                "content_hash": content_hash,
                "state": experience.quality_state.value,
                "line": line_number,
                "created_at": experience.created_at,
                "source_surface": experience.source_surface,
            }
            index["content_hashes"][content_hash] = experience.experience_id
            self._recount(index)
            atomic_write_json(paths["index"], index)
            return AppendResult(experience.experience_id, True, None, content_hash)

    def transition(self, owner_scope: str, experience_id: str, target: ExperienceState, *, reason_code: str) -> dict[str, Any]:
        paths = self._paths(owner_scope)
        lock = FileLock(str(paths["lock"]), timeout=10)
        with self._thread_lock, lock:
            index = self._load_index(owner_scope)
            record = index["records"].get(experience_id)
            if not record:
                raise KeyError("experience_not_found")
            current = ExperienceState(record["state"])
            validate_transition(current, target, EXPERIENCE_TRANSITIONS)
            event = {
                "schema_version": 1,
                "experience_id": experience_id,
                "owner_scope": owner_scope,
                "from": current.value,
                "to": target.value,
                "reason_code": str(reason_code or "unspecified")[:128],
                "created_at": utc_now(),
            }
            self._append_line(paths["state_events"], event)
            record["state"] = target.value
            record["updated_at"] = event["created_at"]
            self._recount(index)
            atomic_write_json(paths["index"], index)
            return event

    def append_annotation(self, owner_scope: str, experience_id: str, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append metadata without mutating the immutable experience record."""
        paths = self._paths(owner_scope)
        lock = FileLock(str(paths["lock"]), timeout=10)
        with self._thread_lock, lock:
            index = self._load_index(owner_scope)
            if experience_id not in index["records"]:
                raise KeyError("experience_not_found")
            if has_unredacted_sensitive_data(payload):
                raise ValueError("annotation_contains_unredacted_sensitive_data")
            event = {
                "schema_version": 1,
                "experience_id": experience_id,
                "owner_scope": owner_scope,
                "kind": str(kind or "annotation")[:64],
                "payload": payload,
                "created_at": utc_now(),
            }
            self._append_line(paths["annotations"], event)
            if kind == "curation" and payload.get("semantic_cluster"):
                index["records"][experience_id]["semantic_cluster"] = str(payload["semantic_cluster"])[:96]
                self._recount(index)
                atomic_write_json(paths["index"], index)
            return event

    def get(self, owner_scope: str, experience_id: str) -> LearningExperienceV1 | None:
        paths = self._paths(owner_scope)
        if not paths["events"].exists():
            return None
        for raw in paths["events"].read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                data = json.loads(raw)
                if data.get("experience_id") == experience_id and data.get("owner_scope") == owner_scope:
                    return LearningExperienceV1.from_dict(data)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return None

    def stats(self, owner_scope: str) -> dict[str, Any]:
        paths = self._paths(owner_scope)
        lock = FileLock(str(paths["lock"]), timeout=10)
        with self._thread_lock, lock:
            index = self._load_index(owner_scope)
            return {
                "owner_scope": owner_scope,
                "total": len(index["records"]),
                "counts": dict(index.get("counts", {})),
                "bytes": paths["events"].stat().st_size if paths["events"].exists() else 0,
                "updated_at": index.get("updated_at"),
            }

    def reconcile(self, owner_scope: str) -> dict[str, Any]:
        paths = self._paths(owner_scope)
        lock = FileLock(str(paths["lock"]), timeout=10)
        with self._thread_lock, lock:
            index = self._empty_index(owner_scope)
            corrupt = 0
            duplicates = 0
            if paths["events"].exists():
                for line_number, raw in enumerate(paths["events"].read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    try:
                        data = json.loads(raw)
                        experience = LearningExperienceV1.from_dict(data)
                        if experience.owner_scope != owner_scope:
                            raise ValueError("owner_scope_mismatch")
                        payload = experience.to_dict()
                        content_hash = payload["content_hash"]
                        if content_hash in index["content_hashes"] or experience.experience_id in index["records"]:
                            duplicates += 1
                            continue
                        index["records"][experience.experience_id] = {
                            "content_hash": content_hash,
                            "state": experience.quality_state.value,
                            "line": line_number,
                            "created_at": experience.created_at,
                            "source_surface": experience.source_surface,
                        }
                        index["content_hashes"][content_hash] = experience.experience_id
                    except Exception as exc:
                        corrupt += 1
                        self._append_line(paths["quarantine"], {
                            "schema_version": 1,
                            "line": line_number,
                            "raw_hash": sha256_json(raw),
                            "error_code": type(exc).__name__,
                            "created_at": utc_now(),
                        })

            if paths["state_events"].exists():
                for raw in paths["state_events"].read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(raw)
                        record = index["records"].get(event.get("experience_id"))
                        if record and event.get("owner_scope") == owner_scope:
                            current = ExperienceState(record["state"])
                            target = ExperienceState(event["to"])
                            validate_transition(current, target, EXPERIENCE_TRANSITIONS)
                            record["state"] = target.value
                            record["updated_at"] = event.get("created_at")
                    except Exception:
                        corrupt += 1
            if paths["annotations"].exists():
                for raw in paths["annotations"].read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(raw)
                        record = index["records"].get(event.get("experience_id"))
                        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                        if record and event.get("owner_scope") == owner_scope and event.get("kind") == "curation" and payload.get("semantic_cluster"):
                            record["semantic_cluster"] = str(payload["semantic_cluster"])[:96]
                    except Exception:
                        corrupt += 1
            self._recount(index)
            atomic_write_json(paths["index"], index)
            return {"records": len(index["records"]), "duplicates": duplicates, "corrupt": corrupt}
