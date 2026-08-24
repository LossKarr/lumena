from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_release_artifacts import PROTECTED_TOP_LEVEL, UPDATE_ASSET, build_release_artifacts, collect_payload
from src.version import MIN_UPDATE_VERSION, __version__

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def _build(tmp_path: Path) -> dict[str, Path]:
    return build_release_artifacts(
        root=ROOT, output=tmp_path, commit=COMMIT, ci_run_id=123,
        ci_run_url="https://github.com/LossKarr/lumena/actions/runs/123",
        release_notes_url=f"https://github.com/LossKarr/lumena/releases/tag/v{__version__}",
        published_at="2026-08-23T12:00:00Z",
    )


def test_payload_is_explicit_and_never_contains_user_state() -> None:
    names = {name for name, _ in collect_payload(ROOT)}
    assert "src/version.py" in names
    assert "web/index.html" in names
    assert ".env" not in names
    assert not any(name == "installer" or name.startswith("installer/") for name in names)
    assert not any(name.split("/", 1)[0].lower() in PROTECTED_TOP_LEVEL for name in names)


def test_builder_emits_self_consistent_certified_artifacts(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    certification = json.loads(paths["certification"].read_text(encoding="utf-8"))
    assert paths["archive"].name == UPDATE_ASSET
    assert manifest["version"] == __version__
    assert manifest["min_supported_version"] == MIN_UPDATE_VERSION
    assert manifest["commit"] == COMMIT
    assert manifest["ci"]["conclusion"] == "success"
    assert manifest["ci"]["commit"] == COMMIT
    assert manifest["asset_size"] == paths["archive"].stat().st_size
    assert certification["asset_sha256"] == manifest["sha256"]
    assert paths["checksum"].read_text(encoding="ascii").startswith(manifest["sha256"])
    with zipfile.ZipFile(paths["archive"]) as archive:
        names = set(archive.namelist())
        assert {"managed-files.json", "build-info.json"} <= names
        assert ".env" not in names
        build_info = json.loads(archive.read("build-info.json"))
        assert build_info["managed_manifest_sha256"] == manifest["managed_files_sha256"]


def test_builder_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    assert _build(tmp_path / "one")["archive"].read_bytes() == _build(tmp_path / "two")["archive"].read_bytes()


def test_builder_refuses_non_exact_commit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="40-character"):
        build_release_artifacts(
            root=ROOT, output=tmp_path, commit="main", ci_run_id=123,
            ci_run_url="https://github.com/LossKarr/lumena/actions/runs/123",
            release_notes_url="https://github.com/LossKarr/lumena/releases/tag/v0",
        )
