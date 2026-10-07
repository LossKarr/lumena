"""Real preference-pair contracts for optional DPO training."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from src.utils.persistence import atomic_write_json, atomic_write_text

from .contracts import canonical_json, new_id, sha256_json, utc_now
from .redaction import has_unredacted_sensitive_data


@dataclass(frozen=True, slots=True)
class PreferencePairV1:
    pair_id: str
    owner_scope: str
    prompt: tuple[dict[str, Any], ...]
    chosen: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    tool_schemas: tuple[dict[str, Any], ...] = ()
    source_experience_ids: tuple[str, ...] = ()
    created_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.pair_id or not self.owner_scope or not self.prompt or not self.chosen or not self.rejected:
            raise ValueError("preference_pair_required_field_missing")
        if canonical_json(self.chosen) == canonical_json(self.rejected):
            raise ValueError("preference_pair_identical_answers")
        if not self.evidence:
            raise ValueError("preference_pair_evidence_required")
        if has_unredacted_sensitive_data(asdict(self)):
            raise ValueError("preference_pair_contains_sensitive_data")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def content_hash(self) -> str:
        payload = self.to_dict()
        payload.pop("pair_id", None)
        payload.pop("created_at", None)
        return sha256_json(payload)


class PreferenceDatasetBuilder:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def build(self, owner_scope: str, pairs: Iterable[PreferencePairV1]) -> dict[str, Any]:
        unique: dict[str, PreferencePairV1] = {}
        for pair in pairs:
            if pair.owner_scope != owner_scope:
                raise PermissionError("preference_pair_owner_mismatch")
            unique.setdefault(pair.content_hash(), pair)
        if not unique:
            raise ValueError("preference_dataset_empty")
        ordered = [unique[key] for key in sorted(unique)]
        dataset_hash = sha256_json([pair.content_hash() for pair in ordered])
        base = self.root / "datasets" / f"dpo_{dataset_hash}"
        rows = []
        for pair in ordered:
            rows.append(canonical_json({
                "prompt": list(pair.prompt),
                "chosen": list(pair.chosen),
                "rejected": list(pair.rejected),
                "tools": list(pair.tool_schemas),
                "evidence": list(pair.evidence),
            }))
        manifest = {
            "schema_version": 1,
            "kind": "dpo",
            "owner_scope": owner_scope,
            "dataset_hash": dataset_hash,
            "pair_count": len(ordered),
            "source_pair_ids": [pair.pair_id for pair in ordered],
            "created_at": max((pair.created_at or utc_now()) for pair in ordered),
        }
        atomic_write_json(base / "manifest.json", manifest)
        atomic_write_text(base / "train.jsonl", "\n".join(rows) + "\n")
        return manifest


def make_preference_pair(
    *, owner_scope: str, prompt: list[dict[str, Any]], chosen: list[dict[str, Any]],
    rejected: list[dict[str, Any]], evidence: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None = None, source_experience_ids: list[str] | None = None,
) -> PreferencePairV1:
    return PreferencePairV1(
        pair_id=new_id("pref"), owner_scope=owner_scope, prompt=tuple(prompt), chosen=tuple(chosen),
        rejected=tuple(rejected), evidence=tuple(evidence), tool_schemas=tuple(tool_schemas or ()),
        source_experience_ids=tuple(source_experience_ids or ()), created_at=utc_now(),
    )
