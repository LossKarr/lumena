"""Versioned MCP connection contracts.

The legacy catalog stores a single ``package_spec`` string.  That remains
readable, but it cannot describe remote servers, host applications or OCI
images without conflating identity, distribution, transport and auth.  This
module provides the strict, serialisable contract used by new entries.

Secret *names* may appear in the contract; secret values never may.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit


CONNECTION_SPEC_VERSION = 1
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_LOCATOR_RE = re.compile(r"^[A-Za-z0-9@._:/+() -]{1,1024}$")
_PROTOCOL_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class ConnectionSpecError(ValueError):
    """The connection contract is malformed or unsafe."""


class DistributionKind(str, Enum):
    NPM = "npm"
    PYPI = "pypi"
    LOCAL_PACKAGE = "local_package"
    EXECUTABLE = "executable"
    HOST_APP = "host_app"
    OCI = "oci"


class TransportKind(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    LEGACY_SSE = "legacy_sse"


class AuthKind(str, Enum):
    NONE = "none"
    STATIC_SECRET = "static_secret"
    BEARER = "bearer"
    OAUTH2 = "oauth2"
    ENTERPRISE_MANAGED = "enterprise_managed"
    DEVICE_CODE = "device_code"


def _tuple_of_strings(raw: Sequence[str], *, field: str, limit: int = 32) -> Tuple[str, ...]:
    if isinstance(raw, (str, bytes)) or len(raw) > limit:
        raise ConnectionSpecError(f"invalid_{field}")
    out = tuple(raw)
    if any(not isinstance(item, str) or not item or len(item) > 512 for item in out):
        raise ConnectionSpecError(f"invalid_{field}")
    if any("\x00" in item or "\r" in item or "\n" in item for item in out):
        raise ConnectionSpecError(f"invalid_{field}")
    return out


def _validate_url(raw: str, *, field: str, allow_loopback_http: bool = True) -> str:
    if not isinstance(raw, str) or len(raw) > 2048:
        raise ConnectionSpecError(f"invalid_{field}")
    parsed = urlsplit(raw)
    if parsed.username or parsed.password or parsed.fragment:
        raise ConnectionSpecError(f"invalid_{field}")
    host = (parsed.hostname or "").lower()
    allowed_http = allow_loopback_http and host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and allowed_http):
        raise ConnectionSpecError(f"invalid_{field}")
    if not host:
        raise ConnectionSpecError(f"invalid_{field}")
    return raw


@dataclass(frozen=True)
class DistributionSpec:
    kind: DistributionKind
    locator: str
    version: Optional[str] = None
    digest: Optional[str] = None
    platform: Optional[str] = None
    architecture: Optional[str] = None
    entrypoint: Optional[str] = None
    args: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DistributionKind):
            raise ConnectionSpecError("invalid_distribution_kind")
        if not isinstance(self.locator, str) or not _SAFE_LOCATOR_RE.fullmatch(self.locator):
            raise ConnectionSpecError("invalid_distribution_locator")
        if self.digest is not None and not _DIGEST_RE.fullmatch(self.digest):
            raise ConnectionSpecError("invalid_distribution_digest")
        for field, value in (
            ("version", self.version),
            ("platform", self.platform),
            ("architecture", self.architecture),
            ("entrypoint", self.entrypoint),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value
                or len(value) > 512
                or any(char in value for char in ("\x00", "\r", "\n"))
            ):
                raise ConnectionSpecError(f"invalid_distribution_{field}")
        args = _tuple_of_strings(self.args, field="distribution_args")
        object.__setattr__(self, "args", args)
        if self.kind == DistributionKind.EXECUTABLE:
            target = Path(self.locator)
            if not target.is_absolute() or target.suffix.lower() != ".exe":
                raise ConnectionSpecError("invalid_executable_locator")
        if self.kind == DistributionKind.OCI and self.digest is None:
            raise ConnectionSpecError("oci_digest_required")


@dataclass(frozen=True)
class RemoteSpec:
    url: str
    legacy_sse_url: Optional[str] = None
    header_names: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _validate_url(self.url, field="remote_url"))
        if self.legacy_sse_url is not None:
            object.__setattr__(
                self,
                "legacy_sse_url",
                _validate_url(self.legacy_sse_url, field="legacy_sse_url"),
            )
        names = _tuple_of_strings(self.header_names, field="header_names")
        if any(not _NAME_RE.fullmatch(name) for name in names):
            raise ConnectionSpecError("invalid_header_names")
        object.__setattr__(self, "header_names", names)


@dataclass(frozen=True)
class AuthSpec:
    kind: AuthKind = AuthKind.NONE
    secret_keys: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    metadata_url: Optional[str] = None
    registration_mode: Optional[str] = None
    client_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AuthKind):
            raise ConnectionSpecError("invalid_auth_kind")
        keys = _tuple_of_strings(self.secret_keys, field="secret_keys")
        if any(not _NAME_RE.fullmatch(key) for key in keys):
            raise ConnectionSpecError("invalid_secret_keys")
        object.__setattr__(self, "secret_keys", keys)
        object.__setattr__(self, "scopes", _tuple_of_strings(self.scopes, field="scopes", limit=64))
        if self.metadata_url is not None:
            object.__setattr__(
                self,
                "metadata_url",
                _validate_url(self.metadata_url, field="metadata_url", allow_loopback_http=False),
            )
        if self.kind == AuthKind.NONE and (
            keys
            or self.scopes
            or self.metadata_url
            or self.registration_mode
            or self.client_id
        ):
            raise ConnectionSpecError("auth_none_has_configuration")
        if self.registration_mode is not None and (
            not isinstance(self.registration_mode, str)
            or self.registration_mode not in {"pre_registered", "dynamic", "manual"}
        ):
            raise ConnectionSpecError("invalid_registration_mode")
        if self.client_id is not None and (
            not isinstance(self.client_id, str)
            or not self.client_id
            or len(self.client_id) > 512
            or any(char in self.client_id for char in ("\x00", "\r", "\n"))
        ):
            raise ConnectionSpecError("invalid_oauth_client_id")
        if self.kind == AuthKind.OAUTH2 and (
            self.metadata_url is None or not keys or not self.client_id
        ):
            raise ConnectionSpecError("oauth_configuration_incomplete")


@dataclass(frozen=True)
class MCPConnectionSpec:
    transport: TransportKind
    distribution: Optional[DistributionSpec] = None
    remote: Optional[RemoteSpec] = None
    auth: AuthSpec = AuthSpec()
    protocol_versions: Tuple[str, ...] = ()
    schema_version: int = CONNECTION_SPEC_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONNECTION_SPEC_VERSION:
            raise ConnectionSpecError("unsupported_connection_spec_version")
        if not isinstance(self.transport, TransportKind):
            raise ConnectionSpecError("invalid_transport_kind")
        versions = _tuple_of_strings(
            self.protocol_versions, field="protocol_versions", limit=16
        )
        if any(not _PROTOCOL_RE.fullmatch(version) for version in versions):
            raise ConnectionSpecError("invalid_protocol_versions")
        object.__setattr__(self, "protocol_versions", versions)
        if self.transport == TransportKind.STDIO:
            if self.distribution is None or self.remote is not None:
                raise ConnectionSpecError("stdio_requires_distribution_only")
        elif self.remote is None:
            raise ConnectionSpecError("remote_transport_requires_remote_spec")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "transport": self.transport.value,
            "auth": {
                "kind": self.auth.kind.value,
                "secret_keys": list(self.auth.secret_keys),
                "scopes": list(self.auth.scopes),
                "metadata_url": self.auth.metadata_url,
                "registration_mode": self.auth.registration_mode,
                "client_id": self.auth.client_id,
            },
            "protocol_versions": list(self.protocol_versions),
        }
        if self.distribution is not None:
            out["distribution"] = {
                "kind": self.distribution.kind.value,
                "locator": self.distribution.locator,
                "version": self.distribution.version,
                "digest": self.distribution.digest,
                "platform": self.distribution.platform,
                "architecture": self.distribution.architecture,
                "entrypoint": self.distribution.entrypoint,
                "args": list(self.distribution.args),
            }
        if self.remote is not None:
            out["remote"] = {
                "url": self.remote.url,
                "legacy_sse_url": self.remote.legacy_sse_url,
                "header_names": list(self.remote.header_names),
            }
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MCPConnectionSpec":
        if not isinstance(raw, Mapping):
            raise ConnectionSpecError("connection_spec_not_object")
        allowed = {"schema_version", "transport", "distribution", "remote", "auth", "protocol_versions"}
        if set(raw) - allowed:
            raise ConnectionSpecError("connection_spec_unknown_field")
        dist_raw = raw.get("distribution")
        distribution = None
        if dist_raw is not None:
            if not isinstance(dist_raw, Mapping):
                raise ConnectionSpecError("distribution_not_object")
            distribution = DistributionSpec(
                kind=DistributionKind(dist_raw.get("kind")),
                locator=dist_raw.get("locator"),
                version=dist_raw.get("version"),
                digest=dist_raw.get("digest"),
                platform=dist_raw.get("platform"),
                architecture=dist_raw.get("architecture"),
                entrypoint=dist_raw.get("entrypoint"),
                args=tuple(dist_raw.get("args", ())),
            )
        remote_raw = raw.get("remote")
        remote = None
        if remote_raw is not None:
            if not isinstance(remote_raw, Mapping):
                raise ConnectionSpecError("remote_not_object")
            remote = RemoteSpec(
                url=remote_raw.get("url"),
                legacy_sse_url=remote_raw.get("legacy_sse_url"),
                header_names=tuple(remote_raw.get("header_names", ())),
            )
        auth_raw = raw.get("auth", {"kind": "none"})
        if not isinstance(auth_raw, Mapping):
            raise ConnectionSpecError("auth_not_object")
        auth = AuthSpec(
            kind=AuthKind(auth_raw.get("kind", "none")),
            secret_keys=tuple(auth_raw.get("secret_keys", ())),
            scopes=tuple(auth_raw.get("scopes", ())),
            metadata_url=auth_raw.get("metadata_url"),
            registration_mode=auth_raw.get("registration_mode"),
            client_id=auth_raw.get("client_id"),
        )
        try:
            transport = TransportKind(raw.get("transport"))
        except (TypeError, ValueError) as exc:
            raise ConnectionSpecError("invalid_transport_kind") from exc
        return cls(
            schema_version=raw.get("schema_version", CONNECTION_SPEC_VERSION),
            transport=transport,
            distribution=distribution,
            remote=remote,
            auth=auth,
            protocol_versions=tuple(raw.get("protocol_versions", ())),
        )


def connection_spec_from_legacy(
    package_spec: str,
    *,
    version: Optional[str] = None,
    entry_args: Sequence[str] = (),
) -> MCPConnectionSpec:
    """Build the new contract without mutating a legacy catalog entry."""
    prefix, separator, locator = package_spec.partition(":")
    if not separator or not locator:
        raise ConnectionSpecError("legacy_package_spec_invalid")
    mapping = {
        "npm": DistributionKind.NPM,
        "pypi": DistributionKind.PYPI,
        "local": DistributionKind.LOCAL_PACKAGE,
        "exe": DistributionKind.EXECUTABLE,
    }
    try:
        kind = mapping[prefix]
    except KeyError as exc:
        raise ConnectionSpecError("legacy_package_transport_unknown") from exc
    return MCPConnectionSpec(
        transport=TransportKind.STDIO,
        distribution=DistributionSpec(
            kind=kind,
            locator=locator,
            version=version,
            args=tuple(entry_args),
        ),
    )

