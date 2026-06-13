"""Tests Phase I-4 — `credentials_service.MCPCredentialsService`."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp.credentials_service import (
    MCPCredentialsError,
    MCPCredentialsService,
)
from src.services.secrets_service import SecretsService


@pytest.fixture
def creds(tmp_path: Path) -> MCPCredentialsService:
    svc = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / "master.key",
    )
    return MCPCredentialsService(svc)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Validations
# ══════════════════════════════════════════════════════════════════════════════


class TestValidations:
    def test_construct_requires_secrets_service(self):
        with pytest.raises(MCPCredentialsError):
            MCPCredentialsService("not a service")  # type: ignore[arg-type]

    @pytest.mark.parametrize("sid", [
        "", "Bad-Caps", "with space", "with/slash",
        "x" * 65,
    ])
    def test_invalid_server_id(self, creds, sid):
        with pytest.raises(MCPCredentialsError):
            creds.has(sid, "X_TOKEN")

    @pytest.mark.parametrize("key", [
        "", "lowercase", "x", "X", "1FOO", "X-DASH", "X.DOT",
        "X" * 65,
    ])
    def test_invalid_key_name(self, creds, key):
        with pytest.raises(MCPCredentialsError):
            creds.set("alice", key, "v")

    def test_set_non_string_value_raises(self, creds):
        with pytest.raises(MCPCredentialsError):
            creds.set("alice", "FOO_BAR", 42)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — CRUD basique
# ══════════════════════════════════════════════════════════════════════════════


class TestCRUD:
    def test_set_then_get(self, creds):
        creds.set("alice", "SLACK_BOT_TOKEN", "xoxb-secret")
        assert creds.get("alice", "SLACK_BOT_TOKEN") == "xoxb-secret"

    def test_get_missing_returns_none(self, creds):
        assert creds.get("alice", "MISSING_KEY") is None

    def test_has_true_after_set(self, creds):
        creds.set("alice", "FOO_TOKEN", "v")
        assert creds.has("alice", "FOO_TOKEN") is True

    def test_has_false_when_absent(self, creds):
        assert creds.has("alice", "OTHER_TOKEN") is False

    def test_delete_returns_true_when_existed(self, creds):
        creds.set("alice", "FOO_TOKEN", "v")
        assert creds.delete("alice", "FOO_TOKEN") is True
        assert creds.get("alice", "FOO_TOKEN") is None

    def test_delete_returns_false_when_absent(self, creds):
        assert creds.delete("alice", "NEVER_SET") is False

    def test_set_empty_value_deletes(self, creds):
        creds.set("alice", "FOO_TOKEN", "v")
        creds.set("alice", "FOO_TOKEN", "")
        assert creds.get("alice", "FOO_TOKEN") is None

    def test_isolation_between_servers(self, creds):
        creds.set("alice", "SHARED_KEY", "valueA")
        creds.set("bob", "SHARED_KEY", "valueB")
        assert creds.get("alice", "SHARED_KEY") == "valueA"
        assert creds.get("bob", "SHARED_KEY") == "valueB"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Listing (jamais de valeurs)
# ══════════════════════════════════════════════════════════════════════════════


class TestListing:
    def test_list_keys_returns_only_names(self, creds):
        creds.set("alice", "TOK_A", "value-a")
        creds.set("alice", "TOK_B", "value-b")
        keys = creds.list_keys("alice")
        assert keys == ["TOK_A", "TOK_B"]
        # Aucun secret ne fuit
        assert "value-a" not in str(keys)

    def test_list_keys_empty_for_unknown(self, creds):
        assert creds.list_keys("never-touched") == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Helpers métier (has_all, missing_keys, status_map)
# ══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_has_all_true_when_all_set(self, creds):
        creds.set("alice", "A_KEY", "1")
        creds.set("alice", "B_KEY", "2")
        assert creds.has_all("alice", ["A_KEY", "B_KEY"]) is True

    def test_has_all_false_when_one_missing(self, creds):
        creds.set("alice", "A_KEY", "1")
        assert creds.has_all("alice", ["A_KEY", "B_KEY"]) is False

    def test_has_all_true_for_empty_requirements(self, creds):
        assert creds.has_all("alice", []) is True

    def test_missing_keys(self, creds):
        creds.set("alice", "A_KEY", "1")
        assert creds.missing_keys("alice", ["A_KEY", "B_KEY", "C_KEY"]) == ["B_KEY", "C_KEY"]

    def test_status_map(self, creds):
        creds.set("alice", "A_KEY", "1")
        m = creds.status_map("alice", ["A_KEY", "B_KEY"])
        assert m == {"A_KEY": "set", "B_KEY": "missing"}


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Export pour runtime (allowlist stricte)
# ══════════════════════════════════════════════════════════════════════════════


class TestExportForRuntime:
    def test_only_allowlist_keys_returned(self, creds):
        creds.set("alice", "ALLOWED_TOKEN", "v1")
        creds.set("alice", "OTHER_TOKEN", "v2")
        out = creds.export_for_runtime("alice", ["ALLOWED_TOKEN"])
        assert out == {"ALLOWED_TOKEN": "v1"}
        assert "OTHER_TOKEN" not in out

    def test_missing_keys_in_allowlist_skipped(self, creds):
        creds.set("alice", "PRESENT_KEY", "v")
        out = creds.export_for_runtime("alice", ["PRESENT_KEY", "MISSING_KEY"])
        assert out == {"PRESENT_KEY": "v"}

    def test_empty_allowlist_returns_empty(self, creds):
        creds.set("alice", "ANY_KEY", "v")
        assert creds.export_for_runtime("alice", []) == {}


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Clear all
# ══════════════════════════════════════════════════════════════════════════════


class TestClearAll:
    def test_clear_all_removes_everything(self, creds):
        creds.set("alice", "A_KEY", "1")
        creds.set("alice", "B_KEY", "2")
        assert creds.clear_all("alice") == 2
        assert creds.list_keys("alice") == []

    def test_clear_all_other_server_untouched(self, creds):
        creds.set("alice", "K_TOKEN", "x")
        creds.set("bob", "K_TOKEN", "y")
        creds.clear_all("alice")
        assert creds.has("bob", "K_TOKEN") is True
