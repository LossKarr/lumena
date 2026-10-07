from __future__ import annotations

from pathlib import Path

import pytest

from src.training.personal.control_plane import PersonalModelControlPlane


def test_control_plane_defaults_to_disabled_and_reports_truth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "deepseek-flash")
    plane = PersonalModelControlPlane(tmp_path)
    status = plane.status()
    assert status["principal_model"] == "deepseek-flash"
    assert status["effective_default"] == "deepseek-flash"
    assert status["policy"]["learning_enabled"] is False
    assert "personal_learning_disabled" in plane.health()["blockers"]


def test_settings_are_validated_and_audited(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path)
    changed = plane.update_training_settings({"enabled": True, "max_cpu_percent": 55}, actor="web-owner")
    assert changed["settings"]["max_cpu_percent"] == 55
    assert plane.training_settings()["effective"]["enabled"] is True
    assert plane.audit_trail()[0]["event"] == "training_settings_updated"
    with pytest.raises(ValueError, match="unknown"):
        plane.update_training_settings({"made_up": True}, actor="web-owner")


def test_sensitive_action_uses_single_use_scoped_approval(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path)
    ticket = plane.approval_preview(action="change_cloud_judge", resource="policy", actor="web-owner")
    updated = plane.update_policy({"cloud_judge_enabled": True}, actor="web-owner", approval_token=ticket["approval_token"])
    assert updated["policy"]["cloud_judge_enabled"] is True
    with pytest.raises(PermissionError, match="used"):
        plane.update_policy({"cloud_judge_enabled": False}, actor="web-owner", approval_token=ticket["approval_token"])


def test_backup_is_fixed_under_personal_root_and_uses_scoped_approval(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path / "data" / "personal_model")
    (plane.root / "lineages").mkdir(parents=True)
    (plane.root / "lineages" / "registry.json").write_text('{"schema_version":1}', encoding="utf-8")
    ticket = plane.approval_preview(action="export_data", resource="personal-model-backup", actor="owner")
    result = plane.create_backup(approval_token=ticket["approval_token"])
    assert Path(result["archive"]).parent == plane.root / "backups"
    assert result["backup_name"].endswith(".lumena-model.zip")


def test_migration_refuses_sources_outside_data_root(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path / "data" / "personal_model")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="outside_data_root"):
        plane.migration_preview([outside])


def test_migration_requires_a_bounded_source_list(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path / "data" / "personal_model")
    with pytest.raises(ValueError, match="source_count_invalid"):
        plane.migration_preview([])
    with pytest.raises(ValueError, match="source_count_invalid"):
        plane.migration_preview([Path(f"training_pool/{number}.jsonl") for number in range(257)])


def test_relative_migration_source_is_resolved_inside_data_root(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path / "data" / "personal_model")
    source = tmp_path / "data" / "training_pool" / "history.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"messages":[{"role":"user","content":"bonjour"},{"role":"assistant","content":"salut"}]}\n', encoding="utf-8")

    preview = plane.migration_preview([Path("training_pool/history.jsonl")])

    assert preview["dry_run"] is True
    assert preview["report"]["source_files"] == 1
    assert preview["report"]["source_lines"] == 1


def test_delete_learning_data_requires_scoped_approval_and_preserves_configuration(tmp_path) -> None:
    plane = PersonalModelControlPlane(tmp_path / "data" / "personal_model")
    experience = plane.root / "experiences" / "events.jsonl"
    configuration = plane.root / "config" / "policy.json"
    experience.parent.mkdir(parents=True)
    configuration.parent.mkdir(parents=True)
    experience.write_text("{}\n", encoding="utf-8")
    configuration.write_text("{}", encoding="utf-8")

    with pytest.raises(PermissionError):
        plane.delete_learning_data(approval_token="")
    ticket = plane.approval_preview(action="delete_data", resource="personal-learning-data", actor="owner")
    result = plane.delete_learning_data(approval_token=ticket["approval_token"], actor="owner")

    assert result["deleted_files"] == 1
    assert not experience.exists()
    assert configuration.exists()
