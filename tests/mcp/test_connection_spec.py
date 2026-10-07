from __future__ import annotations

import pytest

from src.mcp.connection_spec import (
    AuthKind,
    AuthSpec,
    ConnectionSpecError,
    DistributionKind,
    DistributionSpec,
    MCPConnectionSpec,
    RemoteSpec,
    TransportKind,
    connection_spec_from_legacy,
)


def test_legacy_specs_migrate_without_mutation():
    cases = {
        "npm:@scope/server": DistributionKind.NPM,
        "pypi:mcp-server": DistributionKind.PYPI,
        "local:weather": DistributionKind.LOCAL_PACKAGE,
        "exe:C:/Program Files/Vendor/server.exe": DistributionKind.EXECUTABLE,
    }
    for legacy, expected in cases.items():
        spec = connection_spec_from_legacy(legacy, version="1.2.3")
        assert spec.transport == TransportKind.STDIO
        assert spec.distribution is not None
        assert spec.distribution.kind == expected
        assert spec.distribution.version == "1.2.3"


def test_remote_http_contract_round_trips_without_secret_values():
    spec = MCPConnectionSpec(
        transport=TransportKind.STREAMABLE_HTTP,
        remote=RemoteSpec(
            url="https://mcp.example.test/v1",
            header_names=("Authorization", "X-Tenant"),
        ),
        auth=AuthSpec(
            kind=AuthKind.OAUTH2,
            secret_keys=("oauth_refresh_token",),
            scopes=("tools.read",),
            metadata_url="https://auth.example.test/.well-known/oauth-authorization-server",
            registration_mode="pre_registered",
            client_id="lumena-desktop",
        ),
        protocol_versions=("2025-06-18",),
    )
    payload = spec.to_dict()
    assert MCPConnectionSpec.from_dict(payload) == spec
    assert "refresh-token-value" not in repr(payload)


@pytest.mark.parametrize("url", [
    "http://example.test/mcp",
    "https://user:password@example.test/mcp",
    "file:///tmp/mcp",
    "https://example.test/mcp#fragment",
])
def test_remote_urls_fail_closed(url):
    with pytest.raises(ConnectionSpecError):
        RemoteSpec(url=url)


def test_loopback_http_is_allowed_for_local_runtime():
    remote = RemoteSpec(url="http://127.0.0.1:8765/mcp")
    assert remote.url.startswith("http://127.0.0.1")


def test_stdio_and_remote_xor_is_enforced():
    with pytest.raises(ConnectionSpecError, match="stdio_requires"):
        MCPConnectionSpec(transport=TransportKind.STDIO)
    with pytest.raises(ConnectionSpecError, match="remote_transport"):
        MCPConnectionSpec(transport=TransportKind.STREAMABLE_HTTP)


def test_oci_requires_digest():
    with pytest.raises(ConnectionSpecError, match="oci_digest_required"):
        DistributionSpec(kind=DistributionKind.OCI, locator="vendor/server:latest")


def test_unknown_fields_and_header_values_are_rejected():
    with pytest.raises(ConnectionSpecError, match="unknown_field"):
        MCPConnectionSpec.from_dict({
            "transport": "streamable_http",
            "remote": {"url": "https://example.test/mcp"},
            "surprise": True,
        })
    with pytest.raises(ConnectionSpecError, match="header_names"):
        RemoteSpec(
            url="https://example.test/mcp",
            header_names=("Authorization: Bearer secret",),
        )


def test_auth_none_rejects_hidden_oauth_configuration():
    with pytest.raises(ConnectionSpecError, match="auth_none_has_configuration"):
        AuthSpec(kind=AuthKind.NONE, client_id="must-not-be-ignored")


def test_oauth_requires_a_pre_registered_client_identifier():
    with pytest.raises(ConnectionSpecError, match="oauth_configuration_incomplete"):
        AuthSpec(
            kind=AuthKind.OAUTH2,
            secret_keys=("OAUTH_ACCESS_TOKEN",),
            metadata_url="https://auth.example.test/.well-known/oauth-authorization-server",
        )

