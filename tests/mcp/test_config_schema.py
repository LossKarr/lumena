"""Tests Phase I-1 — `config_schema.py` (schema universel)."""
from __future__ import annotations

import pytest

from src.mcp.config_schema import (
    AuthFlow,
    ConfigField,
    ConfigKind,
    MCPConfigSchema,
    Sensitivity,
    ValidationRule,
    auth_flow_from_dict,
    auth_flow_to_dict,
    config_field_from_dict,
    config_field_to_dict,
    default_sensitivity_for,
    schema_from_dict,
    schema_to_dict,
)


class TestDefaultSensitivity:
    @pytest.mark.parametrize("kind,expected", [
        (ConfigKind.SECRET_API_KEY, Sensitivity.SECRET),
        (ConfigKind.SECRET_PASSWORD, Sensitivity.SECRET),
        (ConfigKind.OAUTH_CLIENT_SECRET, Sensitivity.SECRET),
        (ConfigKind.WEBHOOK_URL, Sensitivity.SENSITIVE),
        (ConfigKind.CONNECTION_STRING, Sensitivity.SENSITIVE),
        (ConfigKind.PATH_DIR, Sensitivity.NORMAL),
        (ConfigKind.STRING, Sensitivity.NORMAL),
        (ConfigKind.BOOLEAN, Sensitivity.NORMAL),
    ])
    def test_default(self, kind, expected):
        assert default_sensitivity_for(kind) == expected


class TestConfigFieldRoundTrip:
    def test_minimal(self):
        f = ConfigField(
            name="X", label="X", description="x",
            kind=ConfigKind.STRING, sensitivity=Sensitivity.NORMAL,
        )
        assert config_field_from_dict(config_field_to_dict(f)) == f

    def test_full(self):
        f = ConfigField(
            name="SLACK_BOT_TOKEN", label="Token", description="d",
            kind=ConfigKind.SECRET_TOKEN, sensitivity=Sensitivity.SECRET,
            required=True, default=None, placeholder="xoxb-",
            obtained_from="api.slack.com",
            docs_url="https://example",
            validation=ValidationRule(regex=r"^xoxb-", min_length=10),
            group="Auth", depends_on=None,
            autonomy_resolvable=False,
        )
        d = config_field_to_dict(f)
        assert d["validation"]["regex"] == r"^xoxb-"
        f2 = config_field_from_dict(d)
        assert f2 == f

    def test_from_dict_invalid_kind(self):
        d = {
            "name": "X", "label": "X", "description": "x",
            "kind": "bogus_kind", "sensitivity": "secret", "required": True,
        }
        assert config_field_from_dict(d) is None

    def test_from_dict_missing_required(self):
        assert config_field_from_dict({"name": "X"}) is None


class TestAuthFlowRoundTrip:
    def test_roundtrip(self):
        a = AuthFlow(
            kind="oauth2_authorization_code",
            provider="google",
            authorize_url="https://x", token_url="https://y",
            redirect_uri="http://localhost",
            scopes=("scope1", "scope2"),
            docs_url="https://docs",
        )
        assert auth_flow_from_dict(auth_flow_to_dict(a)) == a


class TestMCPConfigSchema:
    def test_helpers(self):
        s = MCPConfigSchema(
            server_id="slack",
            fields=(
                ConfigField("A", "A", "a", ConfigKind.SECRET_TOKEN, Sensitivity.SECRET),
                ConfigField("B", "B", "b", ConfigKind.STRING, Sensitivity.NORMAL, required=False),
                ConfigField("C", "C", "c", ConfigKind.URL, Sensitivity.NORMAL),
            ),
        )
        assert s.field_names() == ["A", "B", "C"]
        assert s.required_field_names() == ["A", "C"]
        assert s.secret_field_names() == ["A"]
        assert s.non_secret_field_names() == ["B", "C"]
        assert s.get_field("A").name == "A"
        assert s.get_field("Z") is None

    def test_roundtrip(self):
        s = MCPConfigSchema(
            server_id="slack",
            fields=(
                ConfigField("SLACK_BOT_TOKEN", "Token", "d",
                            ConfigKind.SECRET_TOKEN, Sensitivity.SECRET),
            ),
            auth_flows=(
                AuthFlow(kind="api_key", provider="slack"),
            ),
            detected_from="curated",
            detected_at="2026-06-09T00:00:00+00:00",
        )
        assert schema_from_dict(schema_to_dict(s)) == s

    def test_from_dict_invalid_root(self):
        assert schema_from_dict("not a dict") is None
        assert schema_from_dict({"no_server_id": True}) is None

    def test_from_dict_skips_invalid_fields(self):
        d = {
            "server_id": "x",
            "fields": [
                {"name": "OK", "label": "OK", "description": "ok",
                 "kind": "string", "sensitivity": "normal", "required": True},
                {"bogus": "field"},  # ignoré
                "not-a-dict",        # ignoré
            ],
        }
        s = schema_from_dict(d)
        assert s is not None
        assert len(s.fields) == 1
        assert s.fields[0].name == "OK"

    def test_from_dict_unknown_detected_from_falls_back(self):
        d = {
            "server_id": "x",
            "fields": [],
            "detected_from": "alien-source",
        }
        s = schema_from_dict(d)
        assert s.detected_from == "curated"
