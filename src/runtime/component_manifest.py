"""Signed release-component manifests used for optional large Lumena runtimes.

The application installer is intentionally kept below hosting limits. Large
runtime archives are described by this manifest, verified with an embedded
Ed25519 public key, and installed transactionally by ``component_installer``.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


COMPONENT_MANIFEST_MAX_BYTES = 1024 * 1024
COMPONENT_MAX_BYTES = 16 * 1024 * 1024 * 1024
EXPECTED_COMPONENT_REPOSITORY = "LossKarr/lumena"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_PLATFORMS = frozenset({"windows-x64"})


class ComponentManifestError(ValueError):
    """A component manifest is untrusted, malformed or internally inconsistent."""


def canonical_component_manifest_bytes(value: Mapping[str, Any]) -> bytes:
    payload = dict(value)
    payload.pop("signature", None)
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_relative(value: object, *, label: str) -> str:
    raw = str(value or "")
    decoded = unquote(raw)
    if not raw or "\\" in raw or ":" in raw or decoded != raw:
        raise ComponentManifestError(f"invalid {label}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ComponentManifestError(f"invalid {label}")
    return path.as_posix()


def _positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ComponentManifestError(f"invalid {label}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ComponentManifestError(f"invalid {label}") from exc
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        raise ComponentManifestError(f"invalid {label}")
    return parsed


def validate_component_url(
    url: str, *, filename: str, release_version: str,
    repository: str = EXPECTED_COMPONENT_REPOSITORY,
) -> None:
    parsed = urlparse(str(url or ""))
    expected = f"/{repository}/releases/download/v{release_version}/{filename}"
    if (
        parsed.scheme != "https" or parsed.hostname != "github.com"
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or unquote(parsed.path) != parsed.path
        or parsed.path != expected
    ):
        raise ComponentManifestError("component URL is outside the certified release")


@dataclass(frozen=True)
class ComponentArtifact:
    component_id: str
    version: str
    platform: str
    filename: str
    install_dir: str
    size: int
    unpacked_size: int
    sha256: str
    urls: tuple[str, ...]
    dependencies: tuple[str, ...]
    required: bool
    archive_format: str
    source_revision: str | None
    license_id: str

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, release_version: str,
    ) -> "ComponentArtifact":
        if not isinstance(value, Mapping):
            raise ComponentManifestError("component must be an object")
        required_fields = {
            "id", "version", "platform", "filename", "install_dir", "size",
            "sha256", "unpacked_size", "urls", "dependencies", "required", "archive_format",
            "source_revision", "license",
        }
        if set(value) != required_fields:
            raise ComponentManifestError("component fields are incomplete or unknown")
        component_id = str(value.get("id", "")).strip().lower()
        version = str(value.get("version", "")).strip()
        platform = str(value.get("platform", "")).strip().lower()
        if not _ID.fullmatch(component_id):
            raise ComponentManifestError("invalid component id")
        if not _VERSION.fullmatch(version) or version.lower() in {"latest", "main", "master"}:
            raise ComponentManifestError("invalid component version")
        if platform not in _PLATFORMS:
            raise ComponentManifestError("unsupported component platform")
        filename = _safe_relative(value.get("filename"), label="component filename")
        if "/" in filename or not filename.lower().endswith(".zip"):
            raise ComponentManifestError("invalid component filename")
        install_dir = _safe_relative(value.get("install_dir"), label="install directory")
        digest = str(value.get("sha256", "")).strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ComponentManifestError("invalid component sha256")
        size = _positive_int(value.get("size"), label="component size", maximum=COMPONENT_MAX_BYTES)
        unpacked_size = _positive_int(
            value.get("unpacked_size"), label="component unpacked size",
            maximum=COMPONENT_MAX_BYTES,
        )
        if unpacked_size < size // 20:
            raise ComponentManifestError("invalid component unpacked size")
        urls_value = value.get("urls")
        if not isinstance(urls_value, list) or not 1 <= len(urls_value) <= 4:
            raise ComponentManifestError("component URLs are missing")
        urls = tuple(str(item) for item in urls_value)
        if len(set(urls)) != len(urls):
            raise ComponentManifestError("duplicate component URL")
        for url in urls:
            validate_component_url(
                url, filename=filename, release_version=release_version,
            )
        deps_value = value.get("dependencies")
        if not isinstance(deps_value, list):
            raise ComponentManifestError("component dependencies must be a list")
        dependencies = tuple(str(item).strip().lower() for item in deps_value)
        if len(set(dependencies)) != len(dependencies) or any(
            not _ID.fullmatch(item) for item in dependencies
        ):
            raise ComponentManifestError("invalid component dependency")
        if value.get("required") not in {True, False}:
            raise ComponentManifestError("required must be boolean")
        if value.get("archive_format") != "zip":
            raise ComponentManifestError("unsupported component archive")
        source_revision_value = value.get("source_revision")
        source_revision = None if source_revision_value is None else str(source_revision_value).lower()
        if source_revision is not None and not _IMMUTABLE_REVISION.fullmatch(source_revision):
            raise ComponentManifestError("component source revision is not immutable")
        license_id = str(value.get("license", "")).strip()
        if not license_id or len(license_id) > 128:
            raise ComponentManifestError("component license is missing")
        return cls(
            component_id=component_id, version=version, platform=platform,
            filename=filename, install_dir=install_dir, size=size,
            unpacked_size=unpacked_size, sha256=digest,
            urls=urls, dependencies=dependencies, required=bool(value["required"]),
            archive_format="zip", source_revision=source_revision,
            license_id=license_id,
        )


@dataclass(frozen=True)
class ComponentManifest:
    schema_version: int
    release_version: str
    platform: str
    generated_at: str
    components: tuple[ComponentArtifact, ...]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, trusted_keys: Mapping[str, bytes | str],
    ) -> "ComponentManifest":
        if not isinstance(value, Mapping):
            raise ComponentManifestError("manifest must be an object")
        required_fields = {
            "schema_version", "release_version", "platform", "generated_at",
            "components", "signature",
        }
        if set(value) != required_fields or value.get("schema_version") != 1:
            raise ComponentManifestError("unsupported component manifest schema")
        release_version = str(value.get("release_version", "")).strip()
        if not _VERSION.fullmatch(release_version):
            raise ComponentManifestError("invalid release version")
        platform = str(value.get("platform", "")).strip().lower()
        if platform not in _PLATFORMS:
            raise ComponentManifestError("unsupported manifest platform")
        generated_at = str(value.get("generated_at", ""))
        try:
            parsed_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if parsed_time.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise ComponentManifestError("invalid manifest timestamp") from exc
        components_value = value.get("components")
        if not isinstance(components_value, list) or not components_value:
            raise ComponentManifestError("component list is empty")
        components = tuple(
            ComponentArtifact.from_mapping(item, release_version=release_version)
            for item in components_value
        )
        ids = [item.component_id for item in components]
        if len(set(ids)) != len(ids):
            raise ComponentManifestError("duplicate component id")
        if any(item.platform != platform for item in components):
            raise ComponentManifestError("component platform does not match manifest")
        cls._validate_dependencies(components)
        cls._verify_signature(value, trusted_keys)
        return cls(1, release_version, platform, generated_at, components, dict(value))

    @classmethod
    def from_json(
        cls, content: str | bytes, *, trusted_keys: Mapping[str, bytes | str],
    ) -> "ComponentManifest":
        if isinstance(content, bytes) and len(content) > COMPONENT_MANIFEST_MAX_BYTES:
            raise ComponentManifestError("component manifest is too large")
        if isinstance(content, str) and len(content.encode("utf-8")) > COMPONENT_MANIFEST_MAX_BYTES:
            raise ComponentManifestError("component manifest is too large")
        try:
            value = json.loads(content)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComponentManifestError("component manifest is not valid JSON") from exc
        return cls.from_mapping(value, trusted_keys=trusted_keys)

    @staticmethod
    def _verify_signature(
        value: Mapping[str, Any], trusted_keys: Mapping[str, bytes | str],
    ) -> None:
        signature = value.get("signature")
        if not isinstance(signature, Mapping) or signature.get("algorithm") != "ed25519":
            raise ComponentManifestError("component manifest signature is missing")
        key_id = str(signature.get("key_id", ""))
        raw_key = trusted_keys.get(key_id)
        if raw_key is None:
            raise ComponentManifestError("component manifest signer is not trusted")
        try:
            key_bytes = base64.b64decode(raw_key, validate=True) if isinstance(raw_key, str) else bytes(raw_key)
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key.verify(
                base64.b64decode(str(signature.get("value", "")), validate=True),
                canonical_component_manifest_bytes(value),
            )
        except Exception as exc:
            raise ComponentManifestError("component manifest signature is invalid") from exc

    @staticmethod
    def _validate_dependencies(components: Sequence[ComponentArtifact]) -> None:
        graph = {item.component_id: set(item.dependencies) for item in components}
        for component_id, dependencies in graph.items():
            if component_id in dependencies or not dependencies.issubset(graph):
                raise ComponentManifestError("unknown or self component dependency")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ComponentManifestError("cyclic component dependency")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for component_id in graph:
            visit(component_id)

    def installation_order(self) -> tuple[ComponentArtifact, ...]:
        by_id = {item.component_id: item for item in self.components}
        ordered: list[ComponentArtifact] = []
        completed: set[str] = set()
        while len(ordered) < len(self.components):
            ready = sorted(
                (item for item in self.components if item.component_id not in completed
                 and set(item.dependencies).issubset(completed)),
                key=lambda item: item.component_id,
            )
            if not ready:  # Defensive: cycles are rejected during parsing.
                raise ComponentManifestError("component dependency order is impossible")
            for item in ready:
                ordered.append(by_id[item.component_id])
                completed.add(item.component_id)
        return tuple(ordered)
