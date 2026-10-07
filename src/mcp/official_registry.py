"""Read-only client for the official MCP Registry v0.1 API.

The registry is a discovery signal, not a trust decision.  Responses are
strictly bounded, normalised and cached for offline use; raw payloads are never
written to Lumena audit logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import hashlib
import re

from src.utils.persistence import atomic_write_json, safe_read_json


OFFICIAL_REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
OFFICIAL_REGISTRY_API_PATH = "/v0.1/servers"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_CURSOR_LEN = 2048
_MAX_PAGES = 50
_MAX_ENTRIES = 5000


class OfficialRegistryError(RuntimeError):
    """Registry response is unavailable or violates the bounded contract."""


@dataclass(frozen=True)
class RegistryDistribution:
    registry_type: str
    identifier: str
    version: Optional[str]
    transport: str
    registry_base_url: Optional[str] = None
    subfolder: Optional[str] = None


@dataclass(frozen=True)
class RegistryRemote:
    transport_type: str
    url: str


@dataclass(frozen=True)
class OfficialRegistryRecord:
    canonical_name: str
    publisher_namespace: str
    display_name: str
    description: str
    version: str
    status: str
    is_latest: bool
    published_at: Optional[str]
    updated_at: Optional[str]
    packages: Tuple[RegistryDistribution, ...]
    remotes: Tuple[RegistryRemote, ...]
    source_registry: str = "official_mcp_registry"


FetchResult = Tuple[int, Mapping[str, str], bytes]
FetchCallable = Callable[[str, Mapping[str, str], float], FetchResult]


def _clean_string(raw: Any, *, field: str, limit: int, required: bool = False) -> str:
    if raw is None and not required:
        return ""
    if not isinstance(raw, str) or (required and not raw) or len(raw) > limit:
        raise OfficialRegistryError(f"registry_{field}_invalid")
    if "\x00" in raw:
        raise OfficialRegistryError(f"registry_{field}_invalid")
    return raw


def _official_meta(item: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = item.get("_meta", {})
    if not isinstance(meta, Mapping):
        return {}
    official = meta.get("io.modelcontextprotocol.registry/official", {})
    return official if isinstance(official, Mapping) else {}


def _normalise_record(raw: Any) -> OfficialRegistryRecord:
    if not isinstance(raw, Mapping):
        raise OfficialRegistryError("registry_record_not_object")
    server = raw.get("server", raw)
    if not isinstance(server, Mapping):
        raise OfficialRegistryError("registry_server_not_object")
    name = _clean_string(server.get("name"), field="name", limit=255, required=True)
    version = _clean_string(server.get("version"), field="version", limit=128, required=True)
    description = _clean_string(server.get("description", ""), field="description", limit=4000)
    title = _clean_string(server.get("title", ""), field="title", limit=256)
    meta = _official_meta(raw)
    status = _clean_string(meta.get("status", "active"), field="status", limit=32) or "active"
    if status not in {"active", "deprecated", "deleted"}:
        raise OfficialRegistryError("registry_status_invalid")
    is_latest = meta.get("isLatest", meta.get("is_latest", False))
    if not isinstance(is_latest, bool):
        raise OfficialRegistryError("registry_is_latest_invalid")

    packages: List[RegistryDistribution] = []
    raw_packages = server.get("packages", [])
    if not isinstance(raw_packages, list) or len(raw_packages) > 64:
        raise OfficialRegistryError("registry_packages_invalid")
    for package in raw_packages:
        if not isinstance(package, Mapping):
            raise OfficialRegistryError("registry_package_not_object")
        packages.append(RegistryDistribution(
            registry_type=_clean_string(package.get("registryType"), field="registry_type", limit=32, required=True),
            identifier=_clean_string(package.get("identifier"), field="identifier", limit=1024, required=True),
            version=(
                _clean_string(package.get("version"), field="package_version", limit=128)
                or None
            ),
            transport=_clean_string(package.get("transport"), field="package_transport", limit=32, required=True),
            registry_base_url=(
                _clean_string(package.get("registryBaseUrl"), field="registry_base_url", limit=2048)
                or None
            ),
            subfolder=(
                _clean_string(package.get("subfolder"), field="subfolder", limit=512)
                or None
            ),
        ))

    remotes: List[RegistryRemote] = []
    raw_remotes = server.get("remotes", [])
    if not isinstance(raw_remotes, list) or len(raw_remotes) > 32:
        raise OfficialRegistryError("registry_remotes_invalid")
    for remote in raw_remotes:
        if not isinstance(remote, Mapping):
            raise OfficialRegistryError("registry_remote_not_object")
        remotes.append(RegistryRemote(
            transport_type=_clean_string(remote.get("type"), field="remote_type", limit=32, required=True),
            url=_clean_string(remote.get("url"), field="remote_url", limit=2048, required=True),
        ))

    namespace = name.split("/", 1)[0]
    return OfficialRegistryRecord(
        canonical_name=name,
        publisher_namespace=namespace,
        display_name=title or name.rsplit("/", 1)[-1],
        description=description,
        version=version,
        status=status,
        is_latest=is_latest,
        published_at=(
            _clean_string(meta.get("publishedAt", meta.get("published_at")), field="published_at", limit=64)
            or None
        ),
        updated_at=(
            _clean_string(meta.get("updatedAt", meta.get("updated_at")), field="updated_at", limit=64)
            or None
        ),
        packages=tuple(packages),
        remotes=tuple(remotes),
    )


class OfficialMCPRegistryClient:
    def __init__(
        self,
        *,
        cache_path: Path,
        fetcher: Optional[FetchCallable] = None,
        base_url: str = OFFICIAL_REGISTRY_BASE_URL,
        timeout_s: float = 8.0,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ):
        self._cache_path = Path(cache_path)
        self._fetcher = fetcher or self._fetch
        self._base_url = base_url.rstrip("/")
        self._timeout_s = max(0.1, min(float(timeout_s), 60.0))
        self._max_response_bytes = max(1024, min(int(max_response_bytes), _MAX_RESPONSE_BYTES))

    def search(
        self,
        query: str = "",
        *,
        offline: bool = False,
        limit: int = 100,
    ) -> Tuple[OfficialRegistryRecord, ...]:
        query = _clean_string(query.strip(), field="search", limit=256)
        limit = max(1, min(int(limit), _MAX_ENTRIES))
        if offline:
            return self._read_cache(query=query, limit=limit)

        cached = safe_read_json(self._cache_path, default={})
        etag = cached.get("etag") if isinstance(cached, dict) else None
        headers = {"Accept": "application/json", "User-Agent": "Lumena-MCP-Registry/1"}
        if isinstance(etag, str) and etag:
            headers["If-None-Match"] = etag

        records: List[OfficialRegistryRecord] = []
        cursor: Optional[str] = None
        page = 0
        response_etag: Optional[str] = None
        try:
            while len(records) < limit:
                page += 1
                if page > _MAX_PAGES:
                    raise OfficialRegistryError("registry_page_limit_exceeded")
                params: Dict[str, str] = {
                    "limit": str(min(100, limit - len(records))),
                    "version": "latest",
                }
                if query:
                    params["search"] = query
                if cursor:
                    params["cursor"] = cursor
                url = f"{self._base_url}{OFFICIAL_REGISTRY_API_PATH}?{urlencode(params)}"
                page_headers = headers if page == 1 else {
                    "Accept": "application/json",
                    "User-Agent": "Lumena-MCP-Registry/1",
                }
                status, response_headers, body = self._fetcher(
                    url, page_headers, self._timeout_s
                )
                if status == 304 and page == 1:
                    return self._read_cache(query=query, limit=limit)
                if status != 200:
                    raise OfficialRegistryError(f"registry_http_{status}")
                if page == 1:
                    candidate = response_headers.get("ETag") or response_headers.get("etag")
                    response_etag = candidate if isinstance(candidate, str) else None
                if len(body) > self._max_response_bytes:
                    raise OfficialRegistryError("registry_response_too_large")
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OfficialRegistryError("registry_json_invalid") from exc
                if not isinstance(payload, dict):
                    raise OfficialRegistryError("registry_payload_not_object")
                raw_servers = payload.get("servers")
                metadata = payload.get("metadata", {})
                if not isinstance(raw_servers, list) or len(raw_servers) > 100:
                    raise OfficialRegistryError("registry_servers_invalid")
                if not isinstance(metadata, dict):
                    raise OfficialRegistryError("registry_metadata_invalid")
                for item in raw_servers:
                    record = _normalise_record(item)
                    if record.status == "active" and record.is_latest:
                        records.append(record)
                        if len(records) >= limit:
                            break
                next_cursor = metadata.get("nextCursor")
                if next_cursor in (None, ""):
                    break
                if not isinstance(next_cursor, str) or len(next_cursor) > _MAX_CURSOR_LEN:
                    raise OfficialRegistryError("registry_cursor_invalid")
                if next_cursor == cursor:
                    raise OfficialRegistryError("registry_cursor_loop")
                cursor = next_cursor
        except (OfficialRegistryError, OSError, TimeoutError):
            cached_records = self._read_cache(query=query, limit=limit)
            if cached_records:
                return cached_records
            raise

        self._write_cache(records, response_etag)
        return tuple(records)

    def _write_cache(
        self,
        records: Sequence[OfficialRegistryRecord],
        etag: Optional[str],
    ) -> None:
        payload = {
            "schema_version": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "etag": etag,
            "records": [self._record_to_dict(record) for record in records],
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._cache_path, payload)

    def _read_cache(self, *, query: str, limit: int) -> Tuple[OfficialRegistryRecord, ...]:
        payload = safe_read_json(self._cache_path, default={})
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return ()
        raw_records = payload.get("records")
        if not isinstance(raw_records, list) or len(raw_records) > _MAX_ENTRIES:
            return ()
        words = tuple(word.casefold() for word in query.split() if word)
        out: List[OfficialRegistryRecord] = []
        for raw in raw_records:
            try:
                record = self._record_from_dict(raw)
            except (OfficialRegistryError, TypeError, ValueError):
                continue
            haystack = f"{record.canonical_name} {record.display_name} {record.description}".casefold()
            if words and not all(word in haystack for word in words):
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return tuple(out)

    @staticmethod
    def _record_to_dict(record: OfficialRegistryRecord) -> Dict[str, Any]:
        return {
            "canonical_name": record.canonical_name,
            "publisher_namespace": record.publisher_namespace,
            "display_name": record.display_name,
            "description": record.description,
            "version": record.version,
            "status": record.status,
            "is_latest": record.is_latest,
            "published_at": record.published_at,
            "updated_at": record.updated_at,
            "packages": [vars(package) for package in record.packages],
            "remotes": [vars(remote) for remote in record.remotes],
        }

    @staticmethod
    def _record_from_dict(raw: Any) -> OfficialRegistryRecord:
        if not isinstance(raw, dict):
            raise OfficialRegistryError("registry_cache_record_invalid")
        packages = tuple(RegistryDistribution(**item) for item in raw.get("packages", []))
        remotes = tuple(RegistryRemote(**item) for item in raw.get("remotes", []))
        return OfficialRegistryRecord(
            canonical_name=raw["canonical_name"],
            publisher_namespace=raw["publisher_namespace"],
            display_name=raw["display_name"],
            description=raw["description"],
            version=raw["version"],
            status=raw["status"],
            is_latest=raw["is_latest"],
            published_at=raw.get("published_at"),
            updated_at=raw.get("updated_at"),
            packages=packages,
            remotes=remotes,
        )

    @staticmethod
    def _fetch(url: str, headers: Mapping[str, str], timeout_s: float) -> FetchResult:
        request = Request(url, headers=dict(headers), method="GET")
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed HTTPS base by default
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            return int(response.status), dict(response.headers.items()), body


class OfficialRegistrySearchSource:
    """Phase-23 compatible adapter for installable npm/PyPI registry records."""

    def __init__(self, client: OfficialMCPRegistryClient, *, network_enabled: bool = True):
        self._client = client
        self._network_enabled = bool(network_enabled)

    @property
    def name(self) -> str:
        return "official_registry"

    @property
    def is_network(self) -> bool:
        return True

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(self, query_tokens: set[str], *, limit: int) -> List[Dict[str, Any]]:
        if not self._network_enabled:
            return []
        query = " ".join(sorted(token for token in query_tokens if isinstance(token, str)))
        records = self._client.search(query, limit=max(1, min(limit * 3, 100)))
        out: List[Dict[str, Any]] = []
        for record in records:
            selected = next(
                (
                    package for package in record.packages
                    if package.registry_type in {"npm", "pypi"}
                    and package.transport == "stdio"
                ),
                None,
            )
            if selected is None:
                remote = next(
                    (
                        item for item in record.remotes
                        if item.transport_type in {
                            "streamable-http", "streamable_http", "sse"
                        }
                    ),
                    None,
                )
                if remote is None:
                    continue
                try:
                    from src.mcp.connection_spec import (
                        MCPConnectionSpec, RemoteSpec, TransportKind,
                    )
                    remote_transport = (
                        TransportKind.LEGACY_SSE
                        if remote.transport_type == "sse"
                        else TransportKind.STREAMABLE_HTTP
                    )
                    connection_spec = MCPConnectionSpec(
                        transport=remote_transport,
                        remote=RemoteSpec(
                            url=remote.url,
                            legacy_sse_url=(
                                remote.url
                                if remote_transport == TransportKind.LEGACY_SSE
                                else None
                            ),
                        ),
                    ).to_dict()
                except (TypeError, ValueError):
                    continue
                raw_slug = re.sub(
                    r"[^a-z0-9_.-]", "-", record.canonical_name.lower()
                ).strip("-.") or "remote-mcp"
                suffix = hashlib.sha256(remote.url.encode("utf-8")).hexdigest()[:8]
                package_name = record.canonical_name
                package_spec = f"remote:{raw_slug[:54].rstrip('-')}-{suffix}"
                transport = "remote"
                mcp_transport_hint = (
                    "sse" if remote_transport == TransportKind.LEGACY_SSE else "http"
                )
            else:
                transport = selected.registry_type
                package_name = selected.identifier
                package_spec = f"{transport}:{selected.identifier}"
                mcp_transport_hint = "stdio"
                connection_spec = None
            out.append({
                "source": self.name,
                "package_name": package_name,
                "package_spec": package_spec,
                "version": (selected.version if selected else None) or record.version,
                "package_transport": transport,
                "mcp_transport_hint": mcp_transport_hint,
                "description": record.description,
                "tools_hint": [],
                "downloads_count": 0,
                "last_publish_date": record.updated_at or record.published_at or "",
                "has_repo": False,
                "has_license": False,
                "license_id": None,
                "official_namespace": record.publisher_namespace,
                "connection_spec": connection_spec,
            })
            if len(out) >= limit:
                break
        return out

