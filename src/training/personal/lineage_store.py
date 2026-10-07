"""Transactional personal-model lineage, activation and rollback state."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json

from .contracts import ModelVersionState, PersonalModelLineageV1, utc_now
from .naming import SemanticVersion, build_model_name


class LineageStore:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "lineages" / "registry.json"
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=10)

    @staticmethod
    def _default() -> dict[str, Any]:
        return {"schema_version": 1, "lineages": {}, "personal_active": None, "global_default": None, "updated_at": utc_now()}

    def _load(self) -> dict[str, Any]:
        data = safe_read_json(self.path, default={})
        return data if isinstance(data, dict) and data.get("schema_version") == 1 and isinstance(data.get("lineages"), dict) else self._default()

    def _write(self, data: dict[str, Any]) -> None:
        data["updated_at"] = utc_now()
        atomic_write_json(self.path, data)

    def create(self, *, owner_scope: str, display_prefix: str) -> str:
        lineage_id = f"lineage_{uuid.uuid4().hex}"
        with self._thread_lock, self._file_lock:
            data = self._load()
            data["lineages"][lineage_id] = {
                "owner_scope": owner_scope,
                "display_prefix": display_prefix,
                "versions": {},
                "reservations": {},
                "created_at": utc_now(),
            }
            self._write(data)
        return lineage_id

    def reserve_next(self, lineage_id: str, bump: str = "patch") -> dict[str, str]:
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineage = data["lineages"].get(lineage_id)
            if not isinstance(lineage, dict):
                raise KeyError("lineage_not_found")
            versions = [SemanticVersion.parse(version) for version in lineage["versions"]]
            reservations = [SemanticVersion.parse(version) for version in lineage.get("reservations", {})]
            if not versions and not reservations:
                next_version = SemanticVersion(1, 0, 0)
            else:
                latest = max(versions + reservations)
                next_version = latest.bump(bump)
            version = str(next_version)
            reservation_id = f"reservation_{uuid.uuid4().hex}"
            model_name = build_model_name(lineage["display_prefix"], next_version)
            lineage.setdefault("reservations", {})[version] = {"reservation_id": reservation_id, "model_name": model_name, "created_at": utc_now()}
            self._write(data)
            return {"lineage_id": lineage_id, "version": version, "model_name": model_name, "reservation_id": reservation_id}

    def register(self, record: PersonalModelLineageV1, *, reservation_id: str) -> PersonalModelLineageV1:
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineage = data["lineages"].get(record.lineage_id)
            if not isinstance(lineage, dict) or lineage.get("owner_scope") != record.owner_scope:
                raise PermissionError("lineage_owner_mismatch")
            reservation = lineage.get("reservations", {}).get(record.version)
            if not isinstance(reservation, dict) or reservation.get("reservation_id") != reservation_id:
                raise ValueError("lineage_reservation_invalid")
            if reservation.get("model_name") != record.model_name:
                raise ValueError("lineage_model_name_mismatch")
            if record.version in lineage["versions"]:
                raise ValueError("lineage_version_exists")
            lineage["versions"][record.version] = record.to_dict()
            lineage["reservations"].pop(record.version, None)
            self._write(data)
            return record

    def versions(self, lineage_id: str) -> list[PersonalModelLineageV1]:
        with self._thread_lock, self._file_lock:
            lineage = self._load()["lineages"].get(lineage_id)
            if not isinstance(lineage, dict):
                raise KeyError("lineage_not_found")
            return sorted((PersonalModelLineageV1.from_dict(item) for item in lineage["versions"].values()), key=lambda item: SemanticVersion.parse(item.version))

    def activate_personal(self, lineage_id: str, version: str, *, approval_id: str) -> PersonalModelLineageV1:
        if not approval_id:
            raise PermissionError("activation_requires_approval")
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineage = data["lineages"].get(lineage_id)
            if not isinstance(lineage, dict) or version not in lineage["versions"]:
                raise KeyError("lineage_version_not_found")
            target = PersonalModelLineageV1.from_dict(lineage["versions"][version])
            if target.status not in {ModelVersionState.EVALUATED, ModelVersionState.AVAILABLE, ModelVersionState.ACTIVE, ModelVersionState.ARCHIVED}:
                raise ValueError("lineage_version_not_activatable")
            if "ollama_canary" not in target.artifact_hashes:
                raise ValueError("lineage_version_canary_required")
            for other_lineage in data["lineages"].values():
                for key, item in other_lineage["versions"].items():
                    restored = PersonalModelLineageV1.from_dict(item)
                    if restored.personal_active:
                        other_lineage["versions"][key] = replace(restored, personal_active=False, global_default=False, status=ModelVersionState.AVAILABLE).to_dict()
            activated = replace(target, personal_active=True, status=ModelVersionState.ACTIVE)
            lineage["versions"][version] = activated.to_dict()
            data["personal_active"] = {"lineage_id": lineage_id, "version": version, "approval_id": approval_id, "updated_at": utc_now()}
            data["global_default"] = None
            self._write(data)
            return activated

    def set_global_default(self, lineage_id: str, version: str, *, approval_id: str) -> PersonalModelLineageV1:
        if not approval_id:
            raise PermissionError("global_default_requires_approval")
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineage = data["lineages"].get(lineage_id)
            if not isinstance(lineage, dict) or version not in lineage["versions"]:
                raise KeyError("lineage_version_not_found")
            target = PersonalModelLineageV1.from_dict(lineage["versions"][version])
            if not target.personal_active:
                raise ValueError("global_default_requires_personal_active")
            updated = replace(target, global_default=True)
            lineage["versions"][version] = updated.to_dict()
            data["global_default"] = {"lineage_id": lineage_id, "version": version, "approval_id": approval_id, "updated_at": utc_now()}
            self._write(data)
            return updated

    def clear_global_default(self, *, approval_id: str) -> None:
        if not approval_id:
            raise PermissionError("global_default_requires_approval")
        with self._thread_lock, self._file_lock:
            data = self._load()
            for lineage in data["lineages"].values():
                for key, item in lineage.get("versions", {}).items():
                    record = PersonalModelLineageV1.from_dict(item)
                    if record.global_default:
                        lineage["versions"][key] = replace(record, global_default=False).to_dict()
            data["global_default"] = None
            self._write(data)

    def active(self) -> PersonalModelLineageV1 | None:
        with self._thread_lock, self._file_lock:
            data = self._load()
            pointer = data.get("personal_active")
            if not isinstance(pointer, dict):
                return None
            item = data["lineages"][pointer["lineage_id"]]["versions"][pointer["version"]]
            return PersonalModelLineageV1.from_dict(item)

    def snapshot(self, *, owner_scope: str | None = None) -> dict[str, Any]:
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineages = {}
            for lineage_id, lineage in data["lineages"].items():
                if owner_scope and lineage.get("owner_scope") != owner_scope:
                    continue
                lineages[lineage_id] = {
                    "owner_scope": lineage.get("owner_scope"),
                    "display_prefix": lineage.get("display_prefix"),
                    "created_at": lineage.get("created_at"),
                    "versions": list(lineage.get("versions", {}).values()),
                }
            return {
                "schema_version": 1,
                "lineages": lineages,
                "personal_active": data.get("personal_active"),
                "global_default": data.get("global_default"),
                "updated_at": data.get("updated_at"),
            }

    def get(self, lineage_id: str, version: str) -> PersonalModelLineageV1:
        with self._thread_lock, self._file_lock:
            lineage = self._load()["lineages"].get(lineage_id)
            item = lineage.get("versions", {}).get(version) if isinstance(lineage, dict) else None
            if not isinstance(item, dict):
                raise KeyError("lineage_version_not_found")
            return PersonalModelLineageV1.from_dict(item)

    def update_candidate(
        self,
        lineage_id: str,
        version: str,
        *,
        status: ModelVersionState,
        evaluation_id: str | None,
        artifact_hashes: dict[str, str] | None = None,
    ) -> PersonalModelLineageV1:
        """Update evaluation state without changing activation pointers."""
        if status not in {ModelVersionState.CANDIDATE, ModelVersionState.EVALUATED, ModelVersionState.REJECTED, ModelVersionState.AVAILABLE, ModelVersionState.INVALIDATED}:
            raise ValueError("lineage_candidate_status_invalid")
        with self._thread_lock, self._file_lock:
            data = self._load()
            lineage = data["lineages"].get(lineage_id)
            item = lineage.get("versions", {}).get(version) if isinstance(lineage, dict) else None
            if not isinstance(item, dict):
                raise KeyError("lineage_version_not_found")
            current = PersonalModelLineageV1.from_dict(item)
            if current.personal_active or current.global_default:
                raise ValueError("active_version_cannot_be_rewritten")
            updated = replace(
                current,
                status=status,
                evaluation_id=evaluation_id,
                artifact_hashes=current.artifact_hashes if artifact_hashes is None else dict(artifact_hashes),
            )
            lineage["versions"][version] = updated.to_dict()
            self._write(data)
            return updated

    def rollback(self, lineage_id: str, version: str, *, approval_id: str) -> PersonalModelLineageV1:
        if not approval_id:
            raise PermissionError("rollback_requires_approval")
        target = self.get(lineage_id, version)
        if target.status not in {ModelVersionState.EVALUATED, ModelVersionState.AVAILABLE, ModelVersionState.ACTIVE, ModelVersionState.ARCHIVED}:
            raise ValueError("rollback_target_not_available")
        return self.activate_personal(lineage_id, version, approval_id=approval_id)
