from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.runtime.update_manifest import (
    ReleaseManifest, UpdateManifestError, Version, validate_certification,
    validate_download_response_url, validate_download_url,
)


def _manifest() -> dict:
    commit = "a" * 40
    return {
        "schema_version": 1, "version": "1.0.48", "channel": "stable",
        "published_at": "2026-08-23T12:00:00Z", "commit": commit,
        "asset_name": "lumena-update-windows-x64.zip", "asset_size": 123,
        "sha256": "b" * 64, "min_supported_version": "1.0.47", "python": "3.12",
        "requirements_lock_sha256": "c" * 64, "data_schema_version": 1,
        "reads_data_schema_min": 1, "reads_data_schema_max": 2,
        "downgrade_supported": True,
        "ci": {"workflow": "Certified Release - Lumena", "run_id": 42,
               "run_url": "https://github.com/LossKarr/lumena/actions/runs/42",
               "conclusion": "success", "commit": commit},
        "guard_smoke_profile": "update-v1", "managed_files_sha256": "d" * 64,
        "restart_required": True,
        "release_notes_url": "https://github.com/LossKarr/lumena/releases/tag/v1.0.48",
    }


def test_manifest_accepts_exact_certified_contract() -> None:
    manifest = ReleaseManifest.from_mapping(_manifest())
    assert str(manifest.version) == "1.0.48"
    assert manifest.ci.commit == manifest.commit


@pytest.mark.parametrize("field", ["sha256", "managed_files_sha256", "requirements_lock_sha256"])
def test_manifest_refuses_bad_hashes(field: str) -> None:
    value = _manifest()
    value[field] = "nope"
    with pytest.raises(UpdateManifestError):
        ReleaseManifest.from_mapping(value)


def test_manifest_refuses_red_ci_or_different_commit() -> None:
    for mutation in ("conclusion", "commit"):
        value = _manifest()
        value["ci"][mutation] = "failure" if mutation == "conclusion" else "e" * 40
        with pytest.raises(UpdateManifestError, match="CI proof"):
            ReleaseManifest.from_mapping(value)


def test_manifest_refuses_other_repository_urls() -> None:
    value = _manifest()
    value["release_notes_url"] = "https://github.com/attacker/repo/releases/tag/v1.0.48"
    with pytest.raises(UpdateManifestError, match="untrusted"):
        ReleaseManifest.from_mapping(value)


def test_compatibility_distinguishes_upgrade_downgrade_and_full_installer() -> None:
    manifest = ReleaseManifest.from_mapping(_manifest())
    result = manifest.classify(
        current_version=Version.parse("1.0.47"), local_data_schema=1,
        local_requirements_sha256="f" * 64,
    )
    assert result.direction == "upgrade"
    assert result.compatible
    assert result.requires_full_installer

    older = _manifest()
    older["version"] = "1.0.46"
    older["release_notes_url"] = "https://github.com/LossKarr/lumena/releases/tag/v1.0.46"
    down = ReleaseManifest.from_mapping(older).classify(
        current_version=Version.parse("1.0.47"), local_data_schema=3,
        local_requirements_sha256="c" * 64,
    )
    assert down.direction == "downgrade"
    assert not down.compatible
    assert "schema" in (down.blocked_reason or "")


def test_certification_must_match_every_authoritative_field() -> None:
    manifest = ReleaseManifest.from_mapping(_manifest())
    cert = {
        "schema_version": 1, "version": "1.0.48", "commit": manifest.commit,
        "ci_run_id": 42, "ci_run_url": manifest.ci.run_url, "ci_conclusion": "success",
        "full_regression_required": True, "guard_smoke_profile": "update-v1",
        "asset_sha256": manifest.sha256, "managed_files_sha256": manifest.managed_files_sha256,
    }
    validate_certification(cert, manifest)
    broken = copy.deepcopy(cert)
    broken["asset_sha256"] = "0" * 64
    with pytest.raises(UpdateManifestError, match="asset_sha256"):
        validate_certification(broken, manifest)


@pytest.mark.parametrize("url", [
    "http://github.com/LossKarr/lumena/releases/download/v1/x.zip",
    "https://evil.example/LossKarr/lumena/releases/download/v1/x.zip",
    "https://github.com/other/repo/releases/download/v1/x.zip",
])
def test_download_url_rejects_untrusted_locations(url: str) -> None:
    with pytest.raises(UpdateManifestError):
        validate_download_url(url)


def test_catalog_download_url_requires_exact_lumena_release_namespace() -> None:
    validate_download_url(
        "https://github.com/LossKarr/lumena/releases/download/v1.0.51/update-manifest.json"
    )
    with pytest.raises(UpdateManifestError):
        validate_download_url(
            "https://release-assets.githubusercontent.com/github-production-release-asset/file"
        )


@pytest.mark.parametrize("host", [
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
])
def test_effective_download_url_accepts_official_github_release_cdns(host: str) -> None:
    validate_download_response_url(f"https://{host}/github-production-release-asset/file")


@pytest.mark.parametrize("url", [
    "http://release-assets.githubusercontent.com/file",
    "https://release-assets.githubusercontent.com.evil.example/file",
    "https://evil.example/file",
    "https://github.com/other/repo/releases/download/v1.0.51/file.zip",
])
def test_effective_download_url_rejects_insecure_or_untrusted_destinations(url: str) -> None:
    with pytest.raises(UpdateManifestError):
        validate_download_response_url(url)
