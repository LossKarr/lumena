"""Tests Phase I-3 Niveau 2 — `schema_extractor.extract_schema_from_package`."""
from __future__ import annotations

import pytest

from src.mcp.config_schema import ConfigKind, Sensitivity
from src.mcp.schema_extractor import extract_schema_from_package


# ══════════════════════════════════════════════════════════════════════════════
# Helpers — README synthétiques
# ══════════════════════════════════════════════════════════════════════════════


_README_SLACK = """
# mcp-server-slack

## Configuration

```json
{
  "mcpServers": {
    "slack": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-slack"],
      "env": {
        "SLACK_BOT_TOKEN": "xoxb-your-token",
        "SLACK_TEAM_ID": "T01234567"
      }
    }
  }
}
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Your bot user token |
| `SLACK_TEAM_ID` | Workspace ID |
"""

_README_BASH = """
# my-mcp

## Setup

```bash
export MY_API_KEY="abc123"
export MY_BASE_URL="https://api.example.com"
export MY_TIMEOUT_MS="30000"
```
"""

_README_TABLE = """
# Configuration

| Variable           | Required | Description    |
|--------------------|----------|----------------|
| `POSTGRES_HOST`    | yes      | host           |
| `POSTGRES_PORT`    | yes      | port           |
| `POSTGRES_DB`      | yes      | database name  |
| `POSTGRES_PASSWORD`| yes      | password       |
"""


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Extraction réussie
# ══════════════════════════════════════════════════════════════════════════════


class TestExtractFromClaudeDesktop:
    def test_slack_json_block_detected(self):
        s = extract_schema_from_package(
            server_id="slack",
            package_spec="npm:@modelcontextprotocol/server-slack",
            readme_override=_README_SLACK,
        )
        assert s is not None
        names = s.field_names()
        assert "SLACK_BOT_TOKEN" in names
        assert "SLACK_TEAM_ID" in names
        assert s.detected_from == "package"

    def test_kind_secret_for_token(self):
        s = extract_schema_from_package(
            server_id="slack",
            package_spec="npm:x",
            readme_override=_README_SLACK,
        )
        f = s.get_field("SLACK_BOT_TOKEN")
        assert f.kind == ConfigKind.SECRET_TOKEN
        assert f.sensitivity == Sensitivity.SECRET

    def test_kind_string_for_team_id(self):
        s = extract_schema_from_package(
            server_id="slack",
            package_spec="npm:x",
            readme_override=_README_SLACK,
        )
        f = s.get_field("SLACK_TEAM_ID")
        # SLACK_TEAM_ID se termine en ID → STRING NORMAL (pas secret)
        assert f.sensitivity == Sensitivity.NORMAL


class TestExtractFromBash:
    def test_bash_exports_detected(self):
        s = extract_schema_from_package(
            server_id="my-mcp",
            package_spec="pypi:my-mcp",
            readme_override=_README_BASH,
        )
        assert s is not None
        names = s.field_names()
        assert "MY_API_KEY" in names
        assert "MY_BASE_URL" in names
        assert "MY_TIMEOUT_MS" in names

    def test_api_key_classified_secret(self):
        s = extract_schema_from_package(
            server_id="my-mcp", package_spec="pypi:my-mcp",
            readme_override=_README_BASH,
        )
        f = s.get_field("MY_API_KEY")
        assert f.sensitivity == Sensitivity.SECRET
        assert f.kind == ConfigKind.SECRET_API_KEY

    def test_base_url_classified_url(self):
        s = extract_schema_from_package(
            server_id="my-mcp", package_spec="pypi:my-mcp",
            readme_override=_README_BASH,
        )
        f = s.get_field("MY_BASE_URL")
        assert f.kind == ConfigKind.URL
        assert f.sensitivity == Sensitivity.NORMAL


class TestExtractFromMarkdownTable:
    def test_postgres_fields(self):
        s = extract_schema_from_package(
            server_id="postgres",
            package_spec="npm:@x/postgres-mcp",
            readme_override=_README_TABLE,
        )
        assert s is not None
        names = s.field_names()
        for need in ("POSTGRES_HOST", "POSTGRES_PORT",
                     "POSTGRES_DB", "POSTGRES_PASSWORD"):
            assert need in names

    def test_password_classified_secret(self):
        s = extract_schema_from_package(
            server_id="pg", package_spec="npm:x",
            readme_override=_README_TABLE,
        )
        f = s.get_field("POSTGRES_PASSWORD")
        assert f.kind == ConfigKind.SECRET_PASSWORD
        assert f.sensitivity == Sensitivity.SECRET

    def test_port_classified_integer(self):
        s = extract_schema_from_package(
            server_id="pg", package_spec="npm:x",
            readme_override=_README_TABLE,
        )
        f = s.get_field("POSTGRES_PORT")
        assert f.kind == ConfigKind.INTEGER


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Heuristiques classification
# ══════════════════════════════════════════════════════════════════════════════


class TestKindHeuristics:
    @pytest.mark.parametrize("var,expected_kind,expected_sens", [
        ("SOMETHING_SECRET", ConfigKind.SECRET_API_KEY, Sensitivity.SECRET),
        ("MY_PASSWORD", ConfigKind.SECRET_PASSWORD, Sensitivity.SECRET),
        ("AUTH_TOKEN", ConfigKind.SECRET_TOKEN, Sensitivity.SECRET),
        ("OAUTH_CLIENT_SECRET", ConfigKind.OAUTH_CLIENT_SECRET, Sensitivity.SECRET),
        ("OAUTH_CLIENT_ID", ConfigKind.OAUTH_CLIENT_ID, Sensitivity.SECRET),
        ("WEBHOOK_URL", ConfigKind.WEBHOOK_URL, Sensitivity.SENSITIVE),
        ("BASE_URL", ConfigKind.URL, Sensitivity.NORMAL),
        ("DB_PORT", ConfigKind.INTEGER, Sensitivity.NORMAL),
        ("ALLOWED_PATHS", ConfigKind.PATH_DIR, Sensitivity.NORMAL),
        ("CONFIG_FILE", ConfigKind.PATH_FILE, Sensitivity.NORMAL),
        ("DATABASE_URL", ConfigKind.CONNECTION_STRING, Sensitivity.SENSITIVE),
        ("DSN", ConfigKind.CONNECTION_STRING, Sensitivity.SENSITIVE),
        ("USER_EMAIL", ConfigKind.EMAIL, Sensitivity.NORMAL),
    ])
    def test_heuristic(self, var, expected_kind, expected_sens):
        readme = f"```bash\nexport {var}=value\n```"
        s = extract_schema_from_package(
            server_id="t", package_spec="npm:t", readme_override=readme,
        )
        assert s is not None
        f = s.get_field(var)
        assert f is not None
        assert f.kind == expected_kind
        assert f.sensitivity == expected_sens


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Filtres (faux positifs)
# ══════════════════════════════════════════════════════════════════════════════


class TestFalsePositiveFilters:
    @pytest.mark.parametrize("var", [
        "PATH", "HOME", "USER", "SHELL", "NODE_ENV",
        "PYTHONPATH", "TODO", "EXAMPLE",
    ])
    def test_excluded_names(self, var):
        readme = f"```bash\nexport {var}=value\n```"
        s = extract_schema_from_package(
            server_id="t", package_spec="npm:t", readme_override=readme,
        )
        # Soit None (aucun champ), soit le champ exclu n'apparaît pas
        if s is not None:
            assert var not in s.field_names()

    def test_too_short_names_excluded(self):
        readme = "```bash\nexport X=1\nexport AB=2\n```"
        s = extract_schema_from_package(
            server_id="t", package_spec="npm:t", readme_override=readme,
        )
        assert s is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Fetch HTTP (mocké)
# ══════════════════════════════════════════════════════════════════════════════


class TestFetchInjected:
    def test_npm_fetch_path(self):
        calls = []
        def fake_fetch(url, t):
            calls.append(url)
            return {"readme": _README_BASH}
        s = extract_schema_from_package(
            server_id="my-mcp",
            package_spec="npm:@scope/my-mcp",
            fetch_json=fake_fetch,
        )
        assert s is not None
        assert any("registry.npmjs.org" in u for u in calls)

    def test_pypi_fetch_path(self):
        calls = []
        def fake_fetch(url, t):
            calls.append(url)
            return {"info": {"description": _README_BASH}}
        s = extract_schema_from_package(
            server_id="my-mcp", package_spec="pypi:my-mcp",
            fetch_json=fake_fetch,
        )
        assert s is not None
        assert any("pypi.org" in u for u in calls)

    def test_fetch_failure_returns_none(self):
        def boom(url, t):
            raise RuntimeError("network down")
        assert extract_schema_from_package(
            server_id="x", package_spec="npm:x", fetch_json=boom,
        ) is None

    def test_empty_readme_returns_none(self):
        def empty(url, t):
            return {"readme": ""}
        assert extract_schema_from_package(
            server_id="x", package_spec="npm:x", fetch_json=empty,
        ) is None

    def test_invalid_package_spec(self):
        assert extract_schema_from_package(
            server_id="x", package_spec="apt:foo",
        ) is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Robustesse
# ══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_empty_server_id(self):
        assert extract_schema_from_package(
            server_id="", package_spec="npm:x", readme_override="...",
        ) is None

    def test_no_env_vars_in_readme(self):
        assert extract_schema_from_package(
            server_id="x", package_spec="npm:x",
            readme_override="# Nothing here\n\nJust prose.",
        ) is None

    def test_determinism_sort_order(self):
        readme = "```bash\nexport ZULU=1\nexport ALPHA=2\nexport MIKE=3\n```"
        s = extract_schema_from_package(
            server_id="x", package_spec="npm:x", readme_override=readme,
        )
        assert [f.name for f in s.fields] == ["ALPHA", "MIKE", "ZULU"]
