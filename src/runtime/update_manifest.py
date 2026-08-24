"""Strict contracts for certified Lumena GitHub update releases."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

EXPECTED_REPOSITORY = "LossKarr/lumena"
UPDATE_ASSET_NAME = "lumena-update-windows-x64.zip"
INSTALLER_ASSET_NAME = "lumena-setup-windows-x64.exe"
MANIFEST_ASSET_NAME = "update-manifest.json"
CERTIFICATION_ASSET_NAME = "release-certification.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class UpdateManifestError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: object) -> "Version":
        text = str(value or "").strip()
        match = _VERSION_RE.fullmatch(text)
        if not match:
            raise UpdateManifestError(f"invalid semantic version: {text!r}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class CIProof:
    workflow: str
    run_id: int
    run_url: str
    conclusion: str
    commit: str


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: int
    version: Version
    channel: str
    published_at: str
    commit: str
    asset_name: str
    asset_size: int
    sha256: str
    min_supported_version: Version
    python: str
    requirements_lock_sha256: str
    data_schema_version: int
    reads_data_schema_min: int
    reads_data_schema_max: int
    downgrade_supported: bool
    ci: CIProof
    guard_smoke_profile: str
    managed_files_sha256: str
    restart_required: bool
    release_notes_url: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseManifest":
        if not isinstance(value, Mapping):
            raise UpdateManifestError("manifest must be a JSON object")
        required = {
            "schema_version", "version", "channel", "published_at", "commit",
            "asset_name", "asset_size", "sha256", "min_supported_version", "python",
            "requirements_lock_sha256", "data_schema_version", "reads_data_schema_min",
            "reads_data_schema_max", "downgrade_supported", "ci", "guard_smoke_profile",
            "managed_files_sha256", "restart_required", "release_notes_url",
        }
        missing = sorted(required - set(value))
        if missing:
            raise UpdateManifestError(f"missing manifest fields: {', '.join(missing)}")
        if value.get("schema_version") != 1:
            raise UpdateManifestError("unsupported manifest schema")

        commit = str(value.get("commit", "")).lower()
        sha256 = str(value.get("sha256", "")).lower()
        requirements_sha = str(value.get("requirements_lock_sha256", "")).lower()
        managed_sha = str(value.get("managed_files_sha256", "")).lower()
        if not _COMMIT_RE.fullmatch(commit):
            raise UpdateManifestError("manifest commit must be an exact Git SHA")
        for label, digest in (
            ("asset", sha256), ("requirements lock", requirements_sha),
            ("managed files", managed_sha),
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise UpdateManifestError(f"invalid {label} sha256")

        asset_name = str(value.get("asset_name", ""))
        if asset_name != UPDATE_ASSET_NAME:
            raise UpdateManifestError("unexpected update asset name")
        channel = str(value.get("channel", "")).lower()
        if channel != "stable":
            raise UpdateManifestError("only stable manifests are supported")
        published_at = str(value.get("published_at", ""))
        try:
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise UpdateManifestError("invalid published_at timestamp") from exc

        ci_value = value.get("ci")
        if not isinstance(ci_value, Mapping):
            raise UpdateManifestError("ci proof must be an object")
        ci_commit = str(ci_value.get("commit", "")).lower()
        ci_url = str(ci_value.get("run_url", ""))
        ci_run_id = _positive_int(ci_value.get("run_id"), "ci run_id")
        if ci_commit != commit or ci_value.get("conclusion") != "success":
            raise UpdateManifestError("CI proof is not green for the release commit")
        _validate_github_url(ci_url, expected_prefix="/LossKarr/lumena/actions/runs/")
        if not ci_url.rstrip("/").endswith(f"/{ci_run_id}"):
            raise UpdateManifestError("CI run URL does not match run_id")

        release_notes_url = str(value.get("release_notes_url", ""))
        _validate_github_url(release_notes_url, expected_prefix="/LossKarr/lumena/releases/tag/v")
        version = Version.parse(value.get("version"))
        if not release_notes_url.rstrip("/").endswith(f"/v{version}"):
            raise UpdateManifestError("release notes URL does not match version")

        schema = _positive_int(value.get("data_schema_version"), "data schema")
        reads_min = _positive_int(value.get("reads_data_schema_min"), "read schema minimum")
        reads_max = _positive_int(value.get("reads_data_schema_max"), "read schema maximum")
        if reads_min > reads_max or not reads_min <= schema <= reads_max:
            raise UpdateManifestError("invalid data schema compatibility interval")

        return cls(
            schema_version=1,
            version=version,
            channel=channel,
            published_at=published_at,
            commit=commit,
            asset_name=asset_name,
            asset_size=_positive_int(value.get("asset_size"), "asset size"),
            sha256=sha256,
            min_supported_version=Version.parse(value.get("min_supported_version")),
            python=str(value.get("python", "")),
            requirements_lock_sha256=requirements_sha,
            data_schema_version=schema,
            reads_data_schema_min=reads_min,
            reads_data_schema_max=reads_max,
            downgrade_supported=value.get("downgrade_supported") is True,
            ci=CIProof(
                workflow=str(ci_value.get("workflow", "")), run_id=ci_run_id,
                run_url=ci_url, conclusion="success", commit=ci_commit,
            ),
            guard_smoke_profile=str(value.get("guard_smoke_profile", "")),
            managed_files_sha256=managed_sha,
            restart_required=value.get("restart_required") is True,
            release_notes_url=release_notes_url,
        )

    @classmethod
    def from_json(cls, content: str | bytes) -> "ReleaseManifest":
        try:
            value = json.loads(content)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateManifestError("manifest is not valid JSON") from exc
        return cls.from_mapping(value)

    def classify(
        self, *, current_version: Version, local_data_schema: int,
        local_requirements_sha256: str,
    ) -> "Compatibility":
        if current_version == self.version:
            direction = "current"
        elif self.version > current_version:
            direction = "upgrade"
        else:
            direction = "downgrade"

        reasons: list[str] = []
        if current_version < self.min_supported_version and direction == "upgrade":
            reasons.append("version installee trop ancienne")
        if not self.reads_data_schema_min <= local_data_schema <= self.reads_data_schema_max:
            reasons.append("schema de donnees incompatible")
        if direction == "downgrade" and not self.downgrade_supported:
            reasons.append("retrogradation non certifiee")
        requirements_match = local_requirements_sha256.lower() == self.requirements_lock_sha256
        requires_full_installer = not requirements_match
        return Compatibility(
            direction=direction,
            compatible=not reasons,
            requires_full_installer=requires_full_installer,
            blocked_reason="; ".join(reasons) or None,
        )


@dataclass(frozen=True)
class Compatibility:
    direction: str
    compatible: bool
    requires_full_installer: bool
    blocked_reason: str | None


def validate_certification(value: Mapping[str, Any], manifest: ReleaseManifest) -> None:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise UpdateManifestError("invalid release certification")
    expected = {
        "version": str(manifest.version), "commit": manifest.commit,
        "ci_run_id": manifest.ci.run_id, "ci_run_url": manifest.ci.run_url,
        "ci_conclusion": "success", "full_regression_required": True,
        "guard_smoke_profile": manifest.guard_smoke_profile,
        "asset_sha256": manifest.sha256,
        "managed_files_sha256": manifest.managed_files_sha256,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise UpdateManifestError(f"certification mismatch: {key}")


def validate_download_url(url: str, *, repository: str = EXPECTED_REPOSITORY) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com"}:
        raise UpdateManifestError("download URL is outside trusted GitHub HTTPS hosts")
    if parsed.hostname == "github.com":
        prefix = f"/{repository}/releases/download/"
        if not parsed.path.startswith(prefix):
            raise UpdateManifestError("download URL is outside the Lumena release namespace")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise UpdateManifestError(f"invalid {label}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise UpdateManifestError(f"invalid {label}") from exc
    if parsed <= 0:
        raise UpdateManifestError(f"invalid {label}")
    return parsed


def _validate_github_url(url: str, *, expected_prefix: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith(expected_prefix):
        raise UpdateManifestError("untrusted GitHub proof URL")
