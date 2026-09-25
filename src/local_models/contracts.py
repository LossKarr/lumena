"""Pure contracts shared by local-model adapters, routes and chat tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalModelSource(str, Enum):
    OLLAMA = "ollama"
    HUGGINGFACE = "huggingface"


class LocalModelState(str, Enum):
    DISCOVERABLE = "discoverable"
    INSTALLING = "installing"
    INSTALLED = "installed"
    VERIFIED = "verified"
    ENABLED = "enabled"
    SELECTED = "selected"
    LOADED = "loaded"
    DISABLED = "disabled"
    DELETING = "deleting"
    ABSENT = "absent"
    INCOMPATIBLE = "incompatible"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_INTERRUPTED = "unknown_interrupted"


@dataclass(frozen=True, slots=True)
class ModelReference:
    source: LocalModelSource
    canonical: str
    pull_reference: str
    repository: str = ""
    quantization: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data


@dataclass(frozen=True, slots=True)
class CatalogModel:
    reference: ModelReference
    display_name: str
    description: str = ""
    category: str = "text"
    parameter_count: int | None = None
    size_bytes: int | None = None
    quantization: str = ""
    license: str | None = None
    gated: bool | None = None
    downloads: int | None = None
    capabilities: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    confidence: str = "unknown"
    fetched_at: str = field(default_factory=utc_now)
    stale: bool = False
    partial: bool = False
    conversion_required: bool = False
    installed: bool = False
    enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reference"] = self.reference.as_dict()
        return data


@dataclass(frozen=True, slots=True)
class InstalledModel:
    reference: ModelReference
    digest: str
    size_bytes: int | None
    modified_at: str | None
    family: str = ""
    parameter_count: int | None = None
    quantization: str = ""
    capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reference"] = self.reference.as_dict()
        return data


@dataclass(slots=True)
class LocalModelJob:
    job_id: str
    operation: str
    reference: ModelReference
    state: JobState = JobState.QUEUED
    progress_percent: float = 0.0
    completed_bytes: int | None = None
    total_bytes: int | None = None
    status_code: str = "queued"
    error_code: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    idempotency_key: str = ""
    verified: bool = False
    proof: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reference"] = self.reference.as_dict()
        data["state"] = self.state.value
        data["proof"] = dict(self.proof)
        return data
