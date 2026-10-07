"""Tests Phase F — `target_resolver.resolve_target`.

Couvre les 6 kinds + injection web_fetch + bornes input.
"""
from __future__ import annotations

import json
import pytest

from src.mcp.target_resolver import ResolvedTarget, resolve_target


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — kind="package_spec" (npm/pypi/local Phase 14 valides)
# ══════════════════════════════════════════════════════════════════════════════


class TestPackageSpec:
    @pytest.mark.parametrize("spec", [
        "npm:mcp-foo",
        "npm:@modelcontextprotocol/server-gmail",
        "pypi:mcp-bar",
        "pypi:My_Package.X-1",
        "local:my-mcp",
        "local:simple",
    ])
    def test_valid_package_spec(self, spec):
        r = resolve_target(spec)
        assert r.kind == "package_spec"
        assert r.package_spec == spec
        assert r.version == "latest"
        assert r.source_url is None
        assert r.raw_input == spec

    def test_invalid_transport_falls_through(self):
        r = resolve_target("apt:vim")
        # apt: n'est pas un transport supporte → fallback intent
        assert r.kind == "intent"
        assert r.package_spec is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — kind="github_url"
# ══════════════════════════════════════════════════════════════════════════════


class TestGithubUrl:
    @pytest.fixture(autouse=True)
    def _no_network_default_fetch(self, monkeypatch):
        """Fix AS : resolve_target a maintenant un fetcher README par
        DÉFAUT (réseau réel). On le neutralise — AUCUN test ne doit
        toucher le réseau (leçon Fix AB)."""
        monkeypatch.setattr(
            "src.mcp.target_resolver._default_github_readme_fetch",
            lambda url: "",
        )

    def test_github_url_without_fetcher(self):
        r = resolve_target("https://github.com/owner/repo")
        assert r.kind == "github_url"
        assert r.package_spec is None
        assert r.version is None
        assert r.source_url == "https://github.com/owner/repo"

    def test_github_url_with_subpath(self):
        r = resolve_target("https://github.com/owner/repo/tree/main/x")
        assert r.kind == "github_url"
        assert r.source_url == "https://github.com/owner/repo/tree/main/x"

    def test_github_url_http_scheme_accepted(self):
        r = resolve_target("http://github.com/o/r")
        assert r.kind == "github_url"

    def test_github_readme_extracts_npm_install(self):
        readme = "Install:\n```\nnpm install -g @org/mcp-tool\n```\n"
        r = resolve_target(
            "https://github.com/org/mcp-tool",
            web_fetch_callable=lambda u: readme,
        )
        assert r.kind == "github_url"
        assert r.package_spec == "npm:@org/mcp-tool"
        assert r.version == "latest"
        assert r.source_url == "https://github.com/org/mcp-tool"

    def test_github_readme_extracts_npx(self):
        readme = "Run: npx mcp-foo"
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: readme,
        )
        assert r.package_spec == "npm:mcp-foo"

    def test_github_readme_extracts_pip(self):
        readme = "Setup:\n    pip install mcp-py-tool\n"
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: readme,
        )
        assert r.package_spec == "pypi:mcp-py-tool"

    def test_github_readme_extracts_uvx(self):
        readme = "uvx mcp-fast"
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: readme,
        )
        assert r.package_spec == "pypi:mcp-fast"

    def test_github_readme_npm_takes_priority_over_pypi(self):
        readme = "npm install -g foo\npip install bar"
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: readme,
        )
        assert r.package_spec == "npm:foo"

    def test_github_readme_empty_keeps_unresolved(self):
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: "Random readme without install cmd",
        )
        assert r.package_spec is None
        assert r.version is None

    def test_github_readme_fetcher_raises_is_safe(self):
        def _boom(_url):
            raise RuntimeError("network down")
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=_boom,
        )
        # Pas de crash, juste pas de package_spec.
        assert r.kind == "github_url"
        assert r.package_spec is None

    def test_github_readme_fetcher_returns_non_string_is_safe(self):
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: None,
        )
        assert r.package_spec is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — kind="config_snippet" (claude_desktop / mcp.json)
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigSnippet:
    def test_npx_snippet(self):
        snippet = '{"command": "npx", "args": ["-y", "@scope/pkg"]}'
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec == "npm:@scope/pkg"
        assert r.version == "latest"

    def test_stdio_snippet_never_reexposes_literal_environment_values(self):
        snippet = (
            '{"command":"npx","args":["-y","@scope/pkg"],'
            '"env":{"API_TOKEN":"literal-secret-must-not-leak"}}'
        )
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.raw_input == "{stdio_mcp_config}"
        assert "literal-secret-must-not-leak" not in repr(r)

    def test_npm_snippet_picks_first_nonflag_arg(self):
        # `npm` accepte le 1er arg non-flag — sortie deterministe.
        snippet = '{"command": "npm", "args": ["-g", "pkg-bare"]}'
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec == "npm:pkg-bare"

    def test_uvx_snippet(self):
        snippet = '{"command": "uvx", "args": ["mcp-py"]}'
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec == "pypi:mcp-py"

    def test_pipx_snippet(self):
        snippet = '{"command": "pipx", "args": ["run", "mcp-pipx"]}'
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec == "pypi:run"  # 1st non-flag arg

    def test_mcp_servers_wrapper(self):
        snippet = (
            '{"mcpServers": {"gmail": '
            '{"command": "npx", "args": ["-y", "@example/gmail"]}}}'
        )
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec == "npm:@example/gmail"

    def test_malformed_json_falls_through_to_intent(self):
        r = resolve_target("{invalid json")
        assert r.kind == "intent"

    def test_unknown_command_falls_through(self):
        r = resolve_target('{"command": "bash", "args": ["-c", "echo"]}')
        # bash n'est pas reconnu → on tombe en fallback intent
        assert r.kind == "intent"

    def test_snippet_without_args_falls_through(self):
        r = resolve_target('{"command": "npx"}')
        assert r.kind == "intent"

    def test_array_root_falls_through(self):
        r = resolve_target('["not", "a", "snippet"]')
        assert r.kind == "intent"

    def test_remote_streamable_http_config_uses_secret_references_only(self):
        snippet = (
            '{"url":"https://mcp.example.com/v1",'
            '"headers":{"Authorization":"${MCP_TOKEN}"}}'
        )
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec.startswith("remote:mcp.example.com-v1-")
        assert r.connection_spec["transport"] == "streamable_http"
        assert r.connection_spec["auth"]["kind"] == "bearer"
        assert r.connection_spec["auth"]["secret_keys"] == ["MCP_TOKEN"]
        assert r.connection_spec["remote"]["header_names"] == ["Authorization"]
        assert "${MCP_TOKEN}" not in r.raw_input

    def test_remote_config_rejects_literal_header_secret(self):
        snippet = (
            '{"url":"https://mcp.example.com/v1",'
            '"headers":{"Authorization":"Bearer actual-secret"}}'
        )
        r = resolve_target(snippet)
        assert r.kind == "intent"
        assert r.connection_spec is None

    def test_legacy_sse_config_is_explicit(self):
        r = resolve_target(
            '{"url":"https://mcp.example.com/sse","transport":"sse"}'
        )
        assert r.connection_spec["transport"] == "legacy_sse"
        assert r.connection_spec["remote"]["legacy_sse_url"].endswith("/sse")

    def test_remote_oauth_config_contains_only_public_client_and_secret_name(self):
        snippet = json.dumps({
            "url": "https://mcp.example.com/v1",
            "oauth": {
                "metadata_url": "https://auth.example.com/.well-known/oauth-authorization-server",
                "client_id": "lumena-public-client",
                "scopes": ["files.read"],
            },
        })
        r = resolve_target(snippet)
        auth = r.connection_spec["auth"]
        assert auth["kind"] == "oauth2"
        assert auth["client_id"] == "lumena-public-client"
        assert auth["secret_keys"] == ["OAUTH_ACCESS_TOKEN"]
        assert r.connection_spec["remote"]["header_names"] == ["Authorization"]

    def test_host_application_executable_config_is_shell_free(self):
        snippet = (
            '{"command":"C:\\\\Program Files\\\\Roblox\\\\StudioMCP.exe",'
            '"args":["--stdio"],"env":{"ROBLOX_TOKEN":"${ROBLOX_TOKEN}"}}'
        )
        r = resolve_target(snippet)
        assert r.kind == "config_snippet"
        assert r.package_spec.startswith("exe:C:/Program Files/Roblox/")
        assert r.connection_spec["distribution"]["args"] == ["--stdio"]
        assert r.connection_spec["auth"]["secret_keys"] == ["ROBLOX_TOKEN"]
        assert "ROBLOX_TOKEN" not in r.raw_input

    @pytest.mark.parametrize("command", [
        r"C:\\Tools\\mcp.bat", r"C:\\Tools\\mcp.cmd", r"C:\\Tools\\mcp.ps1",
    ])
    def test_host_application_scripts_are_not_executable_transport(self, command):
        r = resolve_target(json.dumps({"command": command, "args": []}))
        assert r.kind == "intent"


class TestRemoteUrl:
    def test_https_endpoint_becomes_versioned_remote_contract(self):
        r = resolve_target("https://tools.example.com/mcp")
        assert r.kind == "remote_url"
        assert r.package_spec.startswith("remote:tools.example.com-mcp-")
        assert r.connection_spec["transport"] == "streamable_http"
        assert r.connection_spec["remote"]["url"] == "https://tools.example.com/mcp"

    def test_non_loopback_http_is_not_admitted(self):
        r = resolve_target("http://tools.example.com/mcp")
        assert r.kind == "intent"
        assert r.connection_spec is None

    def test_loopback_http_is_admitted_for_local_daemon(self):
        r = resolve_target("http://127.0.0.1:8765/mcp")
        assert r.kind == "remote_url"
        assert r.connection_spec["remote"]["url"].startswith("http://127.0.0.1")


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — kind="local_path"
# ══════════════════════════════════════════════════════════════════════════════


class TestLocalPath:
    def test_direct_windows_executable_becomes_executable_transport(self):
        r = resolve_target(
            r"C:\Users\alice\AppData\Local\Roblox\Versions\v1\StudioMCP.exe"
        )

        assert r.kind == "config_snippet"
        assert r.package_spec == (
            "exe:C:/Users/alice/AppData/Local/Roblox/Versions/v1/StudioMCP.exe"
        )
        assert r.version is None
        assert r.slug.startswith("studiomcp-")
        assert r.display_name == "StudioMCP"
        assert r.raw_input == "{local_executable_mcp_config}"
        assert r.connection_spec["transport"] == "stdio"
        assert r.connection_spec["distribution"]["kind"] == "executable"
        assert r.connection_spec["distribution"]["locator"] == (
            "C:/Users/alice/AppData/Local/Roblox/Versions/v1/StudioMCP.exe"
        )

    @pytest.mark.parametrize("suffix", ["bat", "cmd", "ps1"])
    def test_direct_windows_script_never_becomes_executable_transport(self, suffix):
        r = resolve_target(rf"C:\Tools\StudioMCP.{suffix}")

        assert not (r.package_spec or "").startswith("exe:")
        assert r.connection_spec is None

    @pytest.mark.parametrize("path", [
        "/home/me/my-mcp",
        "C:/Users/me/my-mcp",
        "./relative-mcp",
        r".\windows-rel",
    ])
    def test_local_paths_detected(self, path):
        r = resolve_target(path)
        assert r.kind == "local_path"

    def test_local_path_slug_extracted(self):
        r = resolve_target("/path/to/my-cool-mcp")
        assert r.package_spec == "local:my-cool-mcp"
        assert r.version == "latest"

    def test_local_path_invalid_slug_keeps_no_pkg(self):
        # Stem genre "$$$" → slug vide → pas de pkg
        r = resolve_target("/some/place/$$$")
        assert r.kind == "local_path"
        assert r.package_spec is None
        assert r.version is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — kind="intent" (texte libre)
# ══════════════════════════════════════════════════════════════════════════════


class TestIntent:
    @pytest.mark.parametrize("text", [
        "trouve moi un MCP pour scraper du web",
        "je veux un outil pour Gmail",
        "find me a tool that does X",
    ])
    def test_free_text_is_intent(self, text):
        r = resolve_target(text)
        assert r.kind == "intent"
        assert r.package_spec is None
        assert r.raw_input == text


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — kind="unknown" (vide / invalide)
# ══════════════════════════════════════════════════════════════════════════════


class TestUnknown:
    def test_empty_string_is_unknown(self):
        r = resolve_target("")
        assert r.kind == "unknown"

    def test_whitespace_only_is_unknown(self):
        r = resolve_target("   \n\t  ")
        assert r.kind == "unknown"

    def test_non_string_is_unknown(self):
        r = resolve_target(42)  # type: ignore[arg-type]
        assert r.kind == "unknown"
        r2 = resolve_target(None)  # type: ignore[arg-type]
        assert r2.kind == "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — bornes input
# ══════════════════════════════════════════════════════════════════════════════


class TestInputBounds:
    def test_long_input_truncated_to_512(self):
        long_text = "x" * 1000
        r = resolve_target(long_text)
        assert len(r.raw_input) <= 512

    def test_leading_trailing_whitespace_stripped(self):
        r = resolve_target("   npm:mcp-foo   ")
        assert r.kind == "package_spec"
        assert r.raw_input == "npm:mcp-foo"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — contrat ResolvedTarget (frozen + valid kind)
# ══════════════════════════════════════════════════════════════════════════════


class TestResolvedTargetContract:
    def test_is_frozen(self):
        r = ResolvedTarget(
            kind="intent", package_spec=None, version=None,
            source_url=None, raw_input="x",
        )
        with pytest.raises(Exception):
            r.kind = "unknown"  # type: ignore[misc]

    def test_kind_always_in_known_set(self):
        valid = {
            "intent", "github_url", "package_spec",
            "config_snippet", "local_path", "unknown",
        }
        for inp in [
            "", "abc", "npm:x", "https://github.com/a/b",
            '{"command": "npx", "args": ["pkg"]}',
            "/path/to/dir",
        ]:
            r = resolve_target(inp)
            assert r.kind in valid


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — priority ordering : package_spec > github > snippet > path > intent
# ══════════════════════════════════════════════════════════════════════════════


class TestPriorityOrdering:
    def test_package_spec_wins_over_local_pattern(self):
        # local:foo matche package_spec (npm/pypi/local: prefix) → kind="package_spec"
        r = resolve_target("local:my-mcp")
        assert r.kind == "package_spec"

    def test_github_url_wins_over_intent(self):
        r = resolve_target("https://github.com/o/r")
        assert r.kind == "github_url"
        assert r.kind != "intent"


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Phase I-1 : KNOWN_MCPS curated en tête de cascade
# ══════════════════════════════════════════════════════════════════════════════


class TestKnownMcpKind:
    @pytest.mark.parametrize("query,expected_slug", [
        ("slack", "slack"),
        ("github", "github"),
        ("memory", "memory"),
        ("postgres", "postgres"),
        ("Installe le MCP Slack pour moi", "slack"),
        ("gdrive", "google-drive"),
    ])
    def test_known_mcp_resolves(self, query, expected_slug):
        r = resolve_target(query)
        assert r.kind == "known_mcp"
        assert r.slug == expected_slug
        assert r.package_spec is not None
        assert r.package_spec.startswith(("npm:", "pypi:"))

    def test_known_mcp_payload_complete(self):
        r = resolve_target("slack")
        assert r.kind == "known_mcp"
        assert r.display_name == "Slack Workspace"
        assert r.semantic_category == "communication"
        assert r.trust_score is not None and r.trust_score >= 80
        assert r.docs_url is not None
        assert r.config_schema_dict is not None
        # Verifie le schéma persistable
        assert r.config_schema_dict["server_id"] == "slack"
        names = [f["name"] for f in r.config_schema_dict["fields"]]
        assert "SLACK_BOT_TOKEN" in names

    def test_package_spec_explicit_skips_known_lookup(self):
        # L'utilisateur a fourni un package_spec → resolution directe,
        # PAS de fallback known_mcp même si le nom contient "slack"
        r = resolve_target("npm:some-other-slack-clone")
        assert r.kind == "package_spec"
        assert r.slug is None

    def test_github_url_skips_known_lookup(self):
        r = resolve_target("https://github.com/x/slack-mcp")
        assert r.kind == "github_url"
        assert r.slug is None

    def test_unknown_text_falls_through_to_intent(self):
        r = resolve_target("blablabla unknown thing 12345")
        assert r.kind == "intent"
        assert r.slug is None

    def test_known_mcp_zero_config_has_empty_schema(self):
        r = resolve_target("memory")
        assert r.kind == "known_mcp"
        assert r.config_schema_dict is not None
        assert r.config_schema_dict["fields"] == []
