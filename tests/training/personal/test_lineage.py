from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.training.personal.contracts import ModelVersionState, PersonalModelLineageV1, sha256_json, utc_now
from src.training.personal.lineage_store import LineageStore


def _record(lineage_id, reservation, *, status=ModelVersionState.EVALUATED):
    return PersonalModelLineageV1(
        lineage_id=lineage_id, owner_scope="owner:local", display_prefix="lumena",
        version=reservation["version"], model_name=reservation["model_name"], parent_version=None,
        base_model_id="base", base_revision="rev", tokenizer_revision="tok", adapter_method="lora",
        dataset_manifest_hash=sha256_json("dataset"), training_config_hash=sha256_json("config"),
        dependency_manifest={}, artifact_hashes={"adapter": sha256_json("adapter"), "ollama_canary": sha256_json("canary")}, evaluation_id="eval_1",
        created_at=utc_now(), status=status,
    )


def test_version_reservations_are_unique_under_concurrency(tmp_path) -> None:
    store = LineageStore(tmp_path)
    lineage = store.create(owner_scope="owner:local", display_prefix="lumena")
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: store.reserve_next(lineage), range(20)))
    assert len({item["version"] for item in reservations}) == 20
    assert reservations[0]["model_name"].startswith("lumena-model-")


def test_activation_and_global_default_require_separate_approval(tmp_path) -> None:
    store = LineageStore(tmp_path)
    lineage = store.create(owner_scope="owner:local", display_prefix="lumena")
    reservation = store.reserve_next(lineage)
    record = _record(lineage, reservation)
    store.register(record, reservation_id=reservation["reservation_id"])
    with pytest.raises(PermissionError, match="approval"):
        store.activate_personal(lineage, record.version, approval_id="")
    active = store.activate_personal(lineage, record.version, approval_id="approve-active")
    assert active.personal_active is True
    assert active.global_default is False
    with pytest.raises(PermissionError, match="approval"):
        store.set_global_default(lineage, record.version, approval_id="")
    default = store.set_global_default(lineage, record.version, approval_id="approve-default")
    assert default.global_default is True
    store.clear_global_default(approval_id="approve-principal")
    assert store.get(lineage, record.version).global_default is False
