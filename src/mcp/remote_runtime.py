"""Runner-shaped lifecycle adapter for remote MCP connections.

ActivationService owns one lifecycle contract.  This adapter lets remote MCPs
participate in that contract without inventing a subprocess or bypassing the
existing discovery, policy, registry, watcher and rollback chain.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from src.mcp.connection_spec import AuthKind, MCPConnectionSpec, TransportKind
from src.mcp.remote_client import RemoteMCPClient
from src.mcp.sandbox_runner import ProcessState


class RemoteRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteRunnerSpec:
    name: str
    env_keys_allowlist: tuple[str, ...]


class RemoteMCPRuntime:
    def __init__(self, server_id: str, connection_spec: Mapping[str, Any]):
        self.connection_spec = MCPConnectionSpec.from_dict(connection_spec)
        if self.connection_spec.transport == TransportKind.STDIO or self.connection_spec.remote is None:
            raise RemoteRuntimeError("remote_connection_spec_required")
        self.spec = RemoteRunnerSpec(
            name=server_id,
            env_keys_allowlist=self.connection_spec.auth.secret_keys,
        )
        if len(self.connection_spec.remote.header_names) != len(
            self.connection_spec.auth.secret_keys
        ):
            if self.connection_spec.auth.kind != AuthKind.NONE:
                raise RemoteRuntimeError("remote_auth_header_mapping_invalid")
        self._state = ProcessState.INSTALLED
        self._auth_headers: Dict[str, str] = {}
        self._client: Optional[RemoteMCPClient] = None

    @property
    def process(self) -> None:
        return None

    def state(self) -> ProcessState:
        if self._client is not None and self._client.is_closed:
            return ProcessState.STOPPED
        return self._state

    def start(self, runtime_env_secrets: Optional[Dict[str, str]] = None) -> None:
        provided = dict(runtime_env_secrets or {})
        missing = set(self.spec.env_keys_allowlist) - set(provided)
        extra = set(provided) - set(self.spec.env_keys_allowlist)
        if missing:
            raise RemoteRuntimeError("remote_credentials_missing")
        if extra:
            raise RemoteRuntimeError("remote_credentials_not_allowlisted")
        remote = self.connection_spec.remote
        assert remote is not None
        headers: Dict[str, str] = {}
        for header_name, secret_key in zip(remote.header_names, self.spec.env_keys_allowlist):
            value = provided[secret_key]
            if self.connection_spec.auth.kind in {
                AuthKind.BEARER,
                AuthKind.OAUTH2,
                AuthKind.ENTERPRISE_MANAGED,
                AuthKind.DEVICE_CODE,
            } and header_name.casefold() == "authorization":
                if not value.casefold().startswith("bearer "):
                    value = f"Bearer {value}"
            headers[header_name] = value
        self._auth_headers = headers
        self._state = ProcessState.RUNNING

    def create_client(self, *, default_timeout_s: float = 30.0) -> Any:
        if self._state != ProcessState.RUNNING:
            raise RemoteRuntimeError("remote_runtime_not_started")
        remote = self.connection_spec.remote
        assert remote is not None
        versions = self.connection_spec.protocol_versions
        if self.connection_spec.transport == TransportKind.LEGACY_SSE:
            from src.mcp.legacy_sse_client import LegacySSEMCPClient
            client = LegacySSEMCPClient(
                server_name=self.spec.name,
                remote=remote,
                auth_headers=self._auth_headers,
                default_timeout_s=default_timeout_s,
            )
        else:
            client = RemoteMCPClient(
                server_name=self.spec.name,
                remote=remote,
                auth_headers=self._auth_headers,
                protocol_version=versions[0] if versions else "2025-06-18",
                default_timeout_s=default_timeout_s,
            )
        self._client = client
        return client

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
        self._state = ProcessState.STOPPED
        self._auth_headers.clear()

