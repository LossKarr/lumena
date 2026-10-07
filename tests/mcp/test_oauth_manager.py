from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from src.mcp.oauth_manager import MCPOAuthError, MCPOAuthManager


class Credentials:
    def __init__(self):
        self.values = {}

    def set(self, server_id, key, value):
        self.values[(server_id, key)] = value

    def get(self, server_id, key):
        return self.values.get((server_id, key))


class OAuthServer:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if method == "GET":
            payload = {
                "authorization_endpoint": "https://auth.example.test/authorize",
                "token_endpoint": "https://auth.example.test/token",
                "scopes_supported": ["files.read", "files.write"],
            }
        else:
            payload = {"access_token": "access-secret", "refresh_token": "refresh-secret"}
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()


def test_pkce_state_and_tokens_are_bounded_and_encrypted_service_only():
    clock = [100.0]
    server = OAuthServer()
    credentials = Credentials()
    manager = MCPOAuthManager(
        credentials_service=credentials, http=server, now=lambda: clock[0]
    )
    started = manager.begin(
        server_id="drive",
        metadata_url="https://auth.example.test/.well-known/oauth-authorization-server",
        client_id="lumena-client",
        redirect_uri="http://127.0.0.1:8765/api/mcp/oauth/callback",
        scopes=("files.read",),
    )
    query = parse_qs(urlsplit(started.authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [started.state]
    assert "code_verifier" not in query
    sid, stored = manager.complete(state=started.state, code="one-time-code")
    assert sid == "drive"
    assert stored == ("OAUTH_ACCESS_TOKEN", "OAUTH_REFRESH_TOKEN")
    assert credentials.values[("drive", "OAUTH_ACCESS_TOKEN")] == "access-secret"
    wire = server.calls[-1][3].decode()
    assert "code_verifier=" in wire
    assert "access-secret" not in wire
    with pytest.raises(MCPOAuthError, match="state_invalid"):
        manager.complete(state=started.state, code="replay")


def test_scope_escalation_and_non_https_metadata_are_refused():
    manager = MCPOAuthManager(credentials_service=Credentials(), http=OAuthServer())
    with pytest.raises(MCPOAuthError, match="scope_not_supported"):
        manager.begin(
            server_id="drive",
            metadata_url="https://auth.example.test/metadata",
            client_id="client",
            redirect_uri="http://localhost:8765/callback",
            scopes=("admin",),
        )
    with pytest.raises(MCPOAuthError, match="url_invalid"):
        manager.fetch_metadata("http://auth.example.test/metadata")


def test_expired_state_is_single_use_and_never_exchanged():
    clock = [100.0]
    server = OAuthServer()
    manager = MCPOAuthManager(
        credentials_service=Credentials(), http=server,
        now=lambda: clock[0], flow_ttl_s=60,
    )
    started = manager.begin(
        server_id="x", metadata_url="https://auth.example.test/meta",
        client_id="client", redirect_uri="http://localhost:8765/callback",
    )
    clock[0] = 161.0
    with pytest.raises(MCPOAuthError, match="expired"):
        manager.complete(state=started.state, code="code")
    assert len(server.calls) == 1
