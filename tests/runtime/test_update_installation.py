from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.runtime.update_installation import (
    UpdateInstallationError, apply_transaction, git_checkout_ready, git_fast_forward_to,
    prepare_transaction, rollback_transaction, safe_managed_path,
)
from src.runtime.update_guard_smoke import run_update_guard_smoke


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fixture(root: Path, tmp_path: Path, *, protected: bool = False):
    old = root / "src" / "feature.txt"
    obsolete = root / "src" / "obsolete.txt"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")
    obsolete.write_text("remove-me", encoding="utf-8")
    old_manifest = {
        "schema_version": 1, "version": "1.0.47", "files": [
            {"path": "src/feature.txt", "size": 3, "sha256": hashlib.sha256(b"old").hexdigest()},
            {"path": "src/obsolete.txt", "size": 9, "sha256": hashlib.sha256(b"remove-me").hexdigest()},
        ],
    }
    (root / "managed-files.json").write_bytes(_canonical(old_manifest))
    (root / "build-info.json").write_text('{"version":"1.0.47","commit":"old"}\n', encoding="utf-8")

    payloads = {"src/feature.txt": b"new", "web/new.txt": b"created"}
    managed = {"schema_version": 1, "version": "1.0.48", "files": [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in payloads.items()
    ]}
    managed_bytes = _canonical(managed)
    commit = "a" * 40
    build_info = _canonical({
        "version": "1.0.48", "commit": commit,
        "managed_manifest_sha256": hashlib.sha256(managed_bytes).hexdigest(),
        "guard_smoke_profile": "update-v1", "data_schema_version": 1,
    })
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in payloads.items():
            output.writestr(name, content)
        output.writestr("managed-files.json", managed_bytes)
        output.writestr("build-info.json", build_info)
        if protected:
            output.writestr("data/stolen.txt", b"bad")
    manifest = {
        "schema_version": 1, "version": "1.0.48", "channel": "stable",
        "published_at": "2026-08-23T12:00:00Z", "commit": commit,
        "asset_name": "lumena-update-windows-x64.zip", "asset_size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "min_supported_version": "1.0.47", "python": "3.12",
        "requirements_lock_sha256": "c" * 64, "data_schema_version": 1,
        "reads_data_schema_min": 1, "reads_data_schema_max": 1,
        "downgrade_supported": True,
        "ci": {"workflow": "Certified Release - Lumena", "run_id": 42,
               "run_url": "https://github.com/LossKarr/lumena/actions/runs/42",
               "conclusion": "success", "commit": commit},
        "guard_smoke_profile": "update-v1",
        "managed_files_sha256": hashlib.sha256(managed_bytes).hexdigest(),
        "restart_required": True,
        "release_notes_url": "https://github.com/LossKarr/lumena/releases/tag/v1.0.48",
    }
    manifest_path = tmp_path / "update-manifest.json"
    manifest_path.write_bytes(_canonical(manifest))
    return archive, manifest_path


def test_apply_and_rollback_restore_exact_managed_state(tmp_path: Path) -> None:
    root = tmp_path / "app"
    archive, manifest = _fixture(root, tmp_path)
    user_data = root / "data" / "user.json"
    user_data.parent.mkdir(parents=True)
    user_data.write_text("private", encoding="utf-8")
    plan = prepare_transaction(
        root=root, archive=archive, release_manifest_path=manifest,
        transaction_dir=root / "data" / "updates" / "tx",
    )

    apply_transaction(plan)
    assert (root / "src" / "feature.txt").read_text() == "new"
    assert not (root / "src" / "obsolete.txt").exists()
    assert (root / "web" / "new.txt").read_text() == "created"
    assert user_data.read_text() == "private"

    rollback_transaction(plan)
    assert (root / "src" / "feature.txt").read_text() == "old"
    assert (root / "src" / "obsolete.txt").read_text() == "remove-me"
    assert not (root / "web" / "new.txt").exists()
    assert user_data.read_text() == "private"


def test_apply_failure_rolls_back_partial_changes(tmp_path: Path) -> None:
    root = tmp_path / "app"
    archive, manifest = _fixture(root, tmp_path)
    plan = prepare_transaction(
        root=root, archive=archive, release_manifest_path=manifest,
        transaction_dir=tmp_path / "tx",
    )
    value = json.loads(plan.read_text(encoding="utf-8"))
    (Path(value["payload_dir"]) / "web" / "new.txt").unlink()

    with pytest.raises(UpdateInstallationError, match="rollback execute"):
        apply_transaction(plan)

    assert (root / "src" / "feature.txt").read_text() == "old"
    assert (root / "src" / "obsolete.txt").read_text() == "remove-me"


def test_archive_cannot_escape_or_write_protected_state(tmp_path: Path) -> None:
    root = tmp_path / "app"
    archive, manifest = _fixture(root, tmp_path, protected=True)

    with pytest.raises(UpdateInstallationError, match="protege"):
        prepare_transaction(
            root=root, archive=archive, release_manifest_path=manifest,
            transaction_dir=tmp_path / "tx",
        )

    for value in ("../escape", "C:/escape", "/absolute", "workspace/file.txt", "installer/setup.iss"):
        with pytest.raises(UpdateInstallationError):
            safe_managed_path(value)


def test_dirty_git_checkout_is_refused_without_mutation(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    marker = tmp_path / "user-change.txt"
    marker.write_text("keep", encoding="utf-8")

    ready, reason = git_checkout_ready(tmp_path)

    assert not ready and "modifie" in reason
    assert marker.read_text() == "keep"


def test_guard_smoke_requires_exact_identity_and_truth_lock(tmp_path: Path) -> None:
    managed = tmp_path / "managed-files.json"
    managed.write_text('{"schema_version":1,"files":[]}\n', encoding="utf-8")
    (tmp_path / "build-info.json").write_text(
        '{"commit":"abc","guard_smoke_profile":"update-v1","data_schema_version":1}\n',
        encoding="utf-8",
    )

    result = run_update_guard_smoke(tmp_path, expected_version="1.0.47", expected_commit="abc")

    assert result["ok"]
    assert result["checks"]["mission_truth_lock"]


def test_clean_git_checkout_uses_fast_forward_only(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(["git", "clone", str(remote), str(source)], capture_output=True, check=True)
    for repo in (source,):
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Updater Test"], cwd=repo, check=True)
    (source / "version.txt").write_text("one", encoding="utf-8")
    subprocess.run(["git", "add", "version.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "one"], cwd=source, capture_output=True, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=source, capture_output=True, check=True)
    subprocess.run(["git", "clone", str(remote), str(checkout)], capture_output=True, check=True)
    (source / "version.txt").write_text("two", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "two"], cwd=source, capture_output=True, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=source, capture_output=True, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()

    git_fast_forward_to(checkout, commit)

    assert (checkout / "version.txt").read_text() == "two"
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == commit
