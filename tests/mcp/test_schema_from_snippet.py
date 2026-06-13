"""Tests Phase I-3 Niveau 4 — `schema_from_user_snippet`."""
from __future__ import annotations

import pytest

from src.mcp.config_schema import Sensitivity
from src.mcp.schema_from_snippet import schema_from_user_snippet


class TestSnippetFormats:
    def test_dotenv_format(self):
        snippet = "SLACK_BOT_TOKEN=xoxb-abc\nSLACK_TEAM_ID=T01"
        s = schema_from_user_snippet(server_id="slack", snippet=snippet)
        assert s.detected_from == "user"
        names = s.field_names()
        assert "SLACK_BOT_TOKEN" in names
        assert "SLACK_TEAM_ID" in names

    def test_export_format(self):
        snippet = "export MY_API_KEY=abc\nexport MY_URL=https://x"
        s = schema_from_user_snippet(server_id="x", snippet=snippet)
        names = s.field_names()
        assert "MY_API_KEY" in names
        assert "MY_URL" in names

    def test_json_flat(self):
        snippet = '{"SLACK_BOT_TOKEN": "x", "SLACK_TEAM_ID": "T"}'
        s = schema_from_user_snippet(server_id="x", snippet=snippet)
        names = s.field_names()
        assert "SLACK_BOT_TOKEN" in names
        assert "SLACK_TEAM_ID" in names

    def test_claude_desktop_nested(self):
        snippet = """{
            "mcpServers": {
                "x": { "command": "npx", "args": [],
                       "env": {"ALPHA_KEY": "a", "BETA_TOKEN": "b"} }
            }
        }"""
        s = schema_from_user_snippet(server_id="x", snippet=snippet)
        names = s.field_names()
        assert "ALPHA_KEY" in names
        assert "BETA_TOKEN" in names

    def test_markdown_list(self):
        snippet = """
        Required env vars:
        - SLACK_BOT_TOKEN: bot token
        - SLACK_TEAM_ID: workspace id
        """
        s = schema_from_user_snippet(server_id="x", snippet=snippet)
        names = s.field_names()
        assert "SLACK_BOT_TOKEN" in names
        assert "SLACK_TEAM_ID" in names

    def test_markdown_table(self):
        snippet = """
        ## Env
        | Var | Description |
        |-----|-------------|
        | `MY_API_KEY` | api |
        | `MY_PORT` | port |
        """
        s = schema_from_user_snippet(server_id="x", snippet=snippet)
        names = s.field_names()
        assert "MY_API_KEY" in names
        assert "MY_PORT" in names


class TestHeuristics:
    def test_token_becomes_secret(self):
        s = schema_from_user_snippet(
            server_id="x", snippet="MY_API_TOKEN=abc",
        )
        f = s.get_field("MY_API_TOKEN")
        assert f.sensitivity == Sensitivity.SECRET

    def test_url_becomes_normal(self):
        s = schema_from_user_snippet(
            server_id="x", snippet="API_URL=https://api",
        )
        f = s.get_field("API_URL")
        assert f.sensitivity == Sensitivity.NORMAL


class TestEdgeCases:
    @pytest.mark.parametrize("bad", ["", "   ", None, 42, {}, ["a"]])
    def test_invalid_snippet_returns_empty(self, bad):
        s = schema_from_user_snippet(server_id="x", snippet=bad)  # type: ignore[arg-type]
        assert s.fields == ()
        assert s.detected_from == "user"

    def test_empty_server_id_defaults(self):
        s = schema_from_user_snippet(server_id="", snippet="API_KEY=x")
        assert s.server_id == "unknown"
