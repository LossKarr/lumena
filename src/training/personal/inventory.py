"""Privacy-safe inventory of historical personal-training assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.utils.paths import data_dir_for_root

from .contracts import canonical_json, utc_now


@dataclass(frozen=True, slots=True)
class JsonlInventory:
    path: str
    files: int
    bytes: int
    lines: int
    valid: int
    invalid: int
    unique: int
    duplicates: int
    judged: int
    aggregate_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inventory_jsonl(directory: Path, *, label: str | None = None) -> JsonlInventory:
    directory = Path(directory)
    files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    total_bytes = total_lines = valid = invalid = judged = 0
    seen: set[str] = set()
    file_hashes: list[tuple[str, str]] = []
    for path in files:
        raw = path.read_bytes()
        total_bytes += len(raw)
        file_hashes.append((path.name, hashlib.sha256(raw).hexdigest()))
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            total_lines += 1
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("not_object")
                valid += 1
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                content_hash = metadata.get("content_hash")
                if not content_hash:
                    content_hash = hashlib.sha256(canonical_json(item.get("messages") or item.get("conversations") or item).encode("utf-8")).hexdigest()
                seen.add(str(content_hash))
                if metadata.get("judge_score") is not None or metadata.get("judged_by") or metadata.get("judge_model"):
                    judged += 1
            except Exception:
                invalid += 1
    return JsonlInventory(
        path=label or directory.name,
        files=len(files),
        bytes=total_bytes,
        lines=total_lines,
        valid=valid,
        invalid=invalid,
        unique=len(seen),
        duplicates=max(0, valid - len(seen)),
        judged=judged,
        aggregate_hash=hashlib.sha256(canonical_json(file_hashes).encode("utf-8")).hexdigest(),
    )


def build_inventory(root: Path) -> dict[str, Any]:
    root = Path(root)
    data = data_dir_for_root(root)
    directories = {
        "training_pool": data / "training_pool",
        "training_validated": data / "training_validated",
        "training_dpo": data / "training_dpo",
        "training_retrain": data / "training_retrain",
    }
    assets = {name: inventory_jsonl(path, label=name).to_dict() for name, path in directories.items()}
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "assets": assets,
        "configuration_presence": {
            name: bool(__import__("os").environ.get(name, "").strip())
            for name in (
                "LUMENA_JUDGE_MODEL", "LUMENA_DEFAULT_MODEL", "LUMENA_FINETUNING_AUTO_PREPARE",
                "LUMENA_FINETUNING_AUTO_JUDGE", "LUMENA_FINETUNING_AUTO_SAMPLING",
                "LUMENA_LEGACY_AUTO_RETRAIN_ENABLE",
            )
        },
        "artifacts": {
            "legacy_versions_registry": (data / "model_versions.json").exists(),
            "legacy_job_state": (data / "ops" / "finetuning_job.json").exists(),
            "finetuned_registry": (data / "memory" / "finetuned_models.json").exists(),
        },
    }
