from __future__ import annotations

import pytest

from src.mcp.client_factory import create_mcp_client_from_runner
from src.mcp.connection_spec import (
    AuthKind,
    AuthSpec,
    MCPConnectionSpec,
    RemoteSpec,
    TransportKind,
)
from src.mcp.remote_client import RemoteMCPClient
from src.mcp.remote_runtime import RemoteMCPRuntime, RemoteRuntimeError
from src.mcp.sandbox_runner import ProcessState


def _spec(auth=True):
    return MCPConnectionSpec(
        transport=TransportKind.STREAMABLE_HTTP,
        remote=RemoteSpec(
            url="https://mcp.example.test/v1",
            header_names=("Authorization",) if auth else (),
        ),
        auth=AuthSpec(
            kind=AuthKind.BEARER if auth else AuthKind.NONE,
            secret_keys=("access_token",) if auth else (),
        ),
        protocol_versions=("2025-06-18",),
    ).to_dict()


def test_remote_runtime_uses_common_lifecycle_without_process():
    runtime = RemoteMCPRuntime("remote", _spec())
    assert runtime.process is None
    assert runtime.state() == ProcessState.INSTALLED
    runtime.start(runtime_env_secrets={"access_token": "secret"})
    client = create_mcp_client_from_runner(runtime)
    assert isinstance(client, RemoteMCPClient)
    assert runtime.state() == ProcessState.RUNNING
    runtime.stop()
    assert runtime.state() == ProcessState.STOPPED


def test_remote_runtime_fails_closed_on_missing_or_extra_credentials():
    runtime = RemoteMCPRuntime("remote", _spec())
    with pytest.raises(RemoteRuntimeError, match="missing"):
        runtime.start()
    with pytest.raises(RemoteRuntimeError, match="not_allowlisted"):
        RemoteMCPRuntime("public", _spec(auth=False)).start(
            runtime_env_secrets={"surprise": "secret"}
        )


def test_bearer_value_is_only_kept_in_memory_and_prefixed():
    runtime = RemoteMCPRuntime("remote", _spec())
    runtime.start(runtime_env_secrets={"access_token": "token-value"})
    assert runtime._auth_headers == {"Authorization": "Bearer token-value"}
    assert "token-value" not in repr(runtime.connection_spec.to_dict())

