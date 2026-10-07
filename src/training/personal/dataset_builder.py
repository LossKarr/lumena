"""Deterministic dataset manifests and stable holdout construction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.utils.persistence import atomic_write_json, atomic_write_text

from .contracts import DatasetManifestV1, ExperienceState, LearningExperienceV1, canonical_json, sha256_json
from .experience_store import ExperienceStore
from .policy import PersonalLearningPolicy


class DatasetBuilder:
    def __init__(self, store: ExperienceStore) -> None:
        self.store = store

    @staticmethod
    def _group_key(experience: LearningExperienceV1) -> str:
        project = str(experience.public_context.get("project_id", ""))
        return project or experience.semantic_cluster or experience.computed_content_hash()

    @staticmethod
    def _split(group_key: str, *, eval_percent: int, holdout_percent: int) -> str:
        bucket = int(hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:8], 16) % 100
        if bucket < holdout_percent:
            return "holdout"
        if bucket < holdout_percent + eval_percent:
            return "eval"
        return "train"

    def build_sft(
        self,
        owner_scope: str,
        policy: PersonalLearningPolicy,
        *,
        eval_percent: int = 10,
        holdout_percent: int = 10,
    ) -> DatasetManifestV1:
        if not 0 <= eval_percent <= 40 or not 0 <= holdout_percent <= 40 or eval_percent + holdout_percent >= 80:
            raise ValueError("dataset_split_ratio_invalid")
        index = self.store._load_index(owner_scope)
        experiences: list[LearningExperienceV1] = []
        for experience_id, record in sorted(index["records"].items()):
            if record.get("state") != ExperienceState.ACCEPTED.value:
                continue
            experience = self.store.get(owner_scope, experience_id)
            if experience is None:
                continue
            if not policy.dataset_license_decision(experience.license_policy).allowed:
                continue
            experiences.append(experience)
        if not experiences:
            raise ValueError("dataset_no_accepted_experiences")

        groups: dict[str, str] = {}
        train_ids: list[str] = []
        eval_ids: list[str] = []
        holdout_ids: list[str] = []
        distribution: dict[str, dict[str, int]] = {"surface": {}, "teacher": {}, "split": {}}
        for experience in experiences:
            group = self._group_key(experience)
            split = groups.setdefault(group, self._split(group, eval_percent=eval_percent, holdout_percent=holdout_percent))
            target = {"train": train_ids, "eval": eval_ids, "holdout": holdout_ids}[split]
            target.append(experience.experience_id)
            surface = experience.source_surface
            teacher = f"{experience.teacher_provider}:{experience.teacher_model}".strip(":") or "unknown"
            distribution["surface"][surface] = distribution["surface"].get(surface, 0) + 1
            distribution["teacher"][teacher] = distribution["teacher"].get(teacher, 0) + 1
            distribution["split"][split] = distribution["split"].get(split, 0) + 1

        ordered = sorted(experiences, key=lambda item: item.experience_id)
        source_rows = [(item.experience_id, item.to_dict()["content_hash"]) for item in ordered]
        source_hash = sha256_json(source_rows)
        config = {"kind": "sft", "eval_percent": eval_percent, "holdout_percent": holdout_percent, "algorithm": "group_hash_v1"}
        manifest = DatasetManifestV1(
            manifest_id=f"dataset_{sha256_json({'source': source_hash, 'config': config})[:20]}",
            owner_scope=owner_scope,
            created_at=max(item.created_at for item in ordered),
            experience_ids=tuple(item.experience_id for item in ordered),
            train_ids=tuple(sorted(train_ids)),
            eval_ids=tuple(sorted(eval_ids)),
            holdout_ids=tuple(sorted(holdout_ids)),
            builder_config=config,
            distribution=distribution,
            source_hash=source_hash,
        )
        self._persist(owner_scope, manifest, ordered)
        return manifest

    def _persist(self, owner_scope: str, manifest: DatasetManifestV1, experiences: list[LearningExperienceV1]) -> Path:
        base = self.store.owner_root(owner_scope) / "datasets" / manifest.manifest_hash()
        by_id = {item.experience_id: item for item in experiences}
        atomic_write_json(base / "manifest.json", manifest.to_dict())
        for split, ids in (("train", manifest.train_ids), ("eval", manifest.eval_ids), ("holdout", manifest.holdout_ids)):
            rows = []
            for experience_id in ids:
                experience = by_id[experience_id]
                rows.append(canonical_json({"experience_id": experience_id, "messages": list(experience.messages)}))
            atomic_write_text(base / f"{split}.jsonl", "\n".join(rows) + ("\n" if rows else ""))
        return base
