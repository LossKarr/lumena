"""Versioned contracts for Lumena personal-model training.

All persistent records pass through these contracts.  They deliberately avoid
provider SDK objects so manifests remain portable and inspectable over years.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar


SCHEMA_VERSION = 1
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _require_text(value: str, name: str, *, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name}_required")
    if len(text) > maximum:
        raise ValueError(f"{name}_too_long")
    return text


def _require_hash(value: str, name: str) -> str:
    if not _HASH_RE.fullmatch(str(value or "")):
        raise ValueError(f"{name}_invalid")
    return value


class ExperienceState(str, Enum):
    RAW = "raw"
    CANDIDATE = "candidate"
    JUDGING = "judging"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    INCLUDED_IN_DATASET = "included_in_dataset"
    TRAINED = "trained"
    EXCLUDED = "excluded"


EXPERIENCE_TRANSITIONS: dict[ExperienceState, frozenset[ExperienceState]] = {
    ExperienceState.RAW: frozenset({ExperienceState.CANDIDATE, ExperienceState.REJECTED, ExperienceState.QUARANTINED, ExperienceState.EXCLUDED}),
    ExperienceState.CANDIDATE: frozenset({ExperienceState.JUDGING, ExperienceState.REJECTED, ExperienceState.QUARANTINED, ExperienceState.EXCLUDED}),
    ExperienceState.JUDGING: frozenset({ExperienceState.ACCEPTED, ExperienceState.REJECTED, ExperienceState.QUARANTINED}),
    ExperienceState.ACCEPTED: frozenset({ExperienceState.INCLUDED_IN_DATASET, ExperienceState.EXCLUDED}),
    ExperienceState.INCLUDED_IN_DATASET: frozenset({ExperienceState.TRAINED, ExperienceState.EXCLUDED}),
    ExperienceState.TRAINED: frozenset({ExperienceState.EXCLUDED}),
    ExperienceState.REJECTED: frozenset({ExperienceState.CANDIDATE, ExperienceState.EXCLUDED}),
    ExperienceState.QUARANTINED: frozenset({ExperienceState.CANDIDATE, ExperienceState.REJECTED, ExperienceState.EXCLUDED}),
    ExperienceState.EXCLUDED: frozenset(),
}


class TrainingRunState(str, Enum):
    QUEUED = "queued"
    WAITING_IDLE = "waiting_idle"
    RUNNING = "running"
    RESUMING = "resuming"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    UNKNOWN_INTERRUPTED = "unknown_interrupted"
    FAILED = "failed"
    COMPLETED = "completed"


RUN_TRANSITIONS: dict[TrainingRunState, frozenset[TrainingRunState]] = {
    TrainingRunState.QUEUED: frozenset({TrainingRunState.WAITING_IDLE, TrainingRunState.RUNNING, TrainingRunState.CANCELLING, TrainingRunState.CANCELLED, TrainingRunState.FAILED}),
    TrainingRunState.WAITING_IDLE: frozenset({TrainingRunState.RUNNING, TrainingRunState.CANCELLING, TrainingRunState.CANCELLED, TrainingRunState.FAILED}),
    TrainingRunState.RUNNING: frozenset({TrainingRunState.PAUSING, TrainingRunState.CANCELLING, TrainingRunState.COMPLETED, TrainingRunState.FAILED, TrainingRunState.UNKNOWN_INTERRUPTED}),
    TrainingRunState.RESUMING: frozenset({TrainingRunState.RUNNING, TrainingRunState.FAILED, TrainingRunState.CANCELLING, TrainingRunState.UNKNOWN_INTERRUPTED}),
    TrainingRunState.PAUSING: frozenset({TrainingRunState.PAUSED, TrainingRunState.CANCELLING, TrainingRunState.FAILED, TrainingRunState.UNKNOWN_INTERRUPTED}),
    TrainingRunState.PAUSED: frozenset({TrainingRunState.QUEUED, TrainingRunState.RESUMING, TrainingRunState.CANCELLING, TrainingRunState.CANCELLED}),
    TrainingRunState.CANCELLING: frozenset({TrainingRunState.CANCELLED, TrainingRunState.FAILED, TrainingRunState.UNKNOWN_INTERRUPTED}),
    TrainingRunState.CANCELLED: frozenset(),
    TrainingRunState.UNKNOWN_INTERRUPTED: frozenset({TrainingRunState.RESUMING, TrainingRunState.CANCELLED, TrainingRunState.FAILED}),
    TrainingRunState.FAILED: frozenset({TrainingRunState.QUEUED, TrainingRunState.RESUMING}),
    TrainingRunState.COMPLETED: frozenset(),
}


class ModelVersionState(str, Enum):
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    AVAILABLE = "available"
    ACTIVE = "active"
    ARCHIVED = "archived"
    INVALIDATED = "invalidated"


def validate_transition(current: Enum, target: Enum, transitions: dict) -> None:
    if target not in transitions.get(current, frozenset()):
        raise ValueError(f"invalid_transition:{current.value}->{target.value}")


@dataclass(frozen=True, slots=True)
class LearningExperienceV1:
    experience_id: str
    owner_scope: str
    source_surface: str
    mode: str
    created_at: str
    goal: str
    public_context: dict[str, Any]
    messages: tuple[dict[str, Any], ...]
    tool_schemas: tuple[dict[str, Any], ...] = ()
    actions: tuple[dict[str, Any], ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
    result: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[dict[str, Any], ...] = ()
    feedback: dict[str, Any] = field(default_factory=dict)
    teacher_provider: str = ""
    teacher_model: str = ""
    quality_state: ExperienceState = ExperienceState.RAW
    privacy_state: str = "redacted"
    license_policy: str = "unknown"
    content_hash: str = ""
    semantic_cluster: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.experience_id, "experience_id", maximum=96)
        _require_text(self.owner_scope, "owner_scope", maximum=128)
        _require_text(self.source_surface, "source_surface", maximum=64)
        _require_text(self.mode, "mode", maximum=64)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("experience_schema_version_unsupported")
        if not self.messages:
            raise ValueError("messages_required")
        if self.privacy_state not in {"redacted", "blocked", "approved_local", "approved_cloud"}:
            raise ValueError("privacy_state_invalid")
        if self.content_hash:
            _require_hash(self.content_hash, "content_hash")

    def payload_for_hash(self) -> dict[str, Any]:
        return {
            "owner_scope": self.owner_scope,
            "source_surface": self.source_surface,
            "mode": self.mode,
            "goal": self.goal,
            "public_context": self.public_context,
            "messages": list(self.messages),
            "tool_schemas": list(self.tool_schemas),
            "actions": list(self.actions),
            "observations": list(self.observations),
            "result": self.result,
            "evidence": list(self.evidence),
            "feedback": self.feedback,
            "teacher_provider": self.teacher_provider,
            "teacher_model": self.teacher_model,
        }

    def computed_content_hash(self) -> str:
        return sha256_json(self.payload_for_hash())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quality_state"] = self.quality_state.value
        data["messages"] = list(self.messages)
        data["tool_schemas"] = list(self.tool_schemas)
        data["actions"] = list(self.actions)
        data["observations"] = list(self.observations)
        data["evidence"] = list(self.evidence)
        data["content_hash"] = self.content_hash or self.computed_content_hash()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearningExperienceV1":
        payload = dict(data)
        payload["quality_state"] = ExperienceState(payload.get("quality_state", "raw"))
        for key in ("messages", "tool_schemas", "actions", "observations", "evidence"):
            payload[key] = tuple(payload.get(key) or ())
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DatasetManifestV1:
    manifest_id: str
    owner_scope: str
    created_at: str
    experience_ids: tuple[str, ...]
    train_ids: tuple[str, ...]
    eval_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    builder_config: dict[str, Any]
    distribution: dict[str, Any]
    source_hash: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.manifest_id, "manifest_id", maximum=96)
        _require_text(self.owner_scope, "owner_scope", maximum=128)
        _require_hash(self.source_hash, "source_hash")
        all_splits = list(self.train_ids) + list(self.eval_ids) + list(self.holdout_ids)
        if len(all_splits) != len(set(all_splits)):
            raise ValueError("dataset_split_overlap")
        if set(all_splits) != set(self.experience_ids):
            raise ValueError("dataset_split_incomplete")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def manifest_hash(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class TrainingRunV1:
    run_id: str
    owner_scope: str
    lineage_id: str
    target_version: str
    dataset_manifest_hash: str
    training_config_hash: str
    state: TrainingRunState
    created_at: str
    updated_at: str
    checkpoint_path: str = ""
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    error_code: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id", maximum=96)
        _require_text(self.owner_scope, "owner_scope", maximum=128)
        _require_hash(self.dataset_manifest_hash, "dataset_manifest_hash")
        _require_hash(self.training_config_hash, "training_config_hash")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrainingRunV1":
        payload = dict(data)
        payload["state"] = TrainingRunState(payload["state"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class PersonalModelLineageV1:
    lineage_id: str
    owner_scope: str
    display_prefix: str
    version: str
    model_name: str
    parent_version: str | None
    base_model_id: str
    base_revision: str
    tokenizer_revision: str
    adapter_method: str
    dataset_manifest_hash: str
    training_config_hash: str
    dependency_manifest: dict[str, str]
    artifact_hashes: dict[str, str]
    evaluation_id: str | None
    created_at: str
    status: ModelVersionState
    personal_active: bool = False
    global_default: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.lineage_id, "lineage_id", maximum=96)
        _require_text(self.owner_scope, "owner_scope", maximum=128)
        _require_hash(self.dataset_manifest_hash, "dataset_manifest_hash")
        _require_hash(self.training_config_hash, "training_config_hash")
        if self.global_default and not self.personal_active:
            raise ValueError("global_default_requires_personal_active")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalModelLineageV1":
        payload = dict(data)
        payload["status"] = ModelVersionState(payload["status"])
        return cls(**payload)
