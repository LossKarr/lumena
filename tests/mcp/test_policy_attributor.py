"""
Tests Phase 16 v2 — PolicyAttributor.

Sections :
  1. Init & configuration
  2. ToolMetadata validation
  3. Tokenisation
  4. Keyword matching READ_ONLY
  5. Keyword matching EXTERNAL_READ
  6. LOCAL_WRITE explicite uniquement
  7. Keyword matching EXTERNAL_WRITE_RECOVERABLE
  8. Keyword matching EXTERNAL_WRITE_IRREVERSIBLE
  9. Trust gating après classification
  10. Keyword matching SECRETS_AUTH (+bigrammes)
  11. Description fallback restrictif (escalation only)
  12. Priorité conservatrice multi-match
  13. No match / refus
  14. Audit forensique no-PII
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.policy import MCPPolicy
from src.mcp.policy_attributor import (
    AttributionDecision,
    PolicyAttributor,
    ToolMetadata,
    _tokenize,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def attributor(tmp_path: Path) -> PolicyAttributor:
    return PolicyAttributor(
        audit_log_path=tmp_path / "attributor" / "audit.jsonl",
    )


def _tool(
    server_id: str = "alice",
    tool_name: str = "read_doc",
    description: Optional[str] = None,
    input_schema: Optional[Dict[str, Any]] = None,
) -> ToolMetadata:
    return ToolMetadata(
        server_id=server_id,
        tool_name=tool_name,
        description=description,
        input_schema=input_schema,
    )


def _audit_lines(attributor: PolicyAttributor) -> List[Dict[str, Any]]:
    if not attributor.audit_log_path.exists():
        return []
    out = []
    with open(attributor.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(attributor: PolicyAttributor) -> str:
    if not attributor.audit_log_path.exists():
        return ""
    return attributor.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_audit_dir_created(self, attributor):
        assert attributor.audit_log_path.parent.exists()

    def test_default_thresholds(self, attributor):
        assert attributor.min_trust_score_for_write == 70
        assert attributor.min_trust_score_for_secrets == 90

    def test_custom_thresholds_respected(self, tmp_path):
        a = PolicyAttributor(
            min_trust_score_for_write=50,
            min_trust_score_for_secrets=80,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert a.min_trust_score_for_write == 50
        assert a.min_trust_score_for_secrets == 80

    def test_secrets_below_write_raises(self, tmp_path):
        with pytest.raises(ValueError, match=">= min_trust_score_for_write"):
            PolicyAttributor(
                min_trust_score_for_write=80,
                min_trust_score_for_secrets=70,
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_invalid_threshold_range(self, tmp_path):
        with pytest.raises(ValueError, match="\\[0,100\\]"):
            PolicyAttributor(
                min_trust_score_for_write=-1,
                audit_log_path=tmp_path / "audit.jsonl",
            )
        with pytest.raises(ValueError, match="\\[0,100\\]"):
            PolicyAttributor(
                min_trust_score_for_secrets=101,
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_bool_threshold_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="must be int"):
            PolicyAttributor(
                min_trust_score_for_write=True,  # type: ignore
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_no_require_trust_score_param(self, tmp_path):
        """Vérifie qu'il n'y a PAS de paramètre require_trust_score dans l'API."""
        with pytest.raises(TypeError):
            PolicyAttributor(
                require_trust_score=True,  # type: ignore
                audit_log_path=tmp_path / "audit.jsonl",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — ToolMetadata validation
# ══════════════════════════════════════════════════════════════════════════════


class TestMetadataValidation:
    def test_server_id_invalid(self, attributor):
        d = attributor.attribute(_tool(server_id="ALICE", tool_name="read_doc"))
        assert d.policy is None
        assert d.reason.startswith("metadata_invalid:server_id")

    def test_server_id_windows_reserved(self, attributor):
        d = attributor.attribute(_tool(server_id="con", tool_name="read_doc"))
        assert d.policy is None
        assert "server_id" in d.reason

    def test_tool_name_empty(self, attributor):
        d = attributor.attribute(_tool(tool_name=""))
        assert d.policy is None
        assert "tool_name" in d.reason

    def test_tool_name_uppercase_accepted_fix_az(self, attributor):
        """Fix AZ — casse libre (windows-mcp expose App/Click/PowerShell).
        Le matching mots-clés est insensible à la casse (lower() en aval) :
        Read_Doc doit être classé comme read_doc."""
        d = attributor.attribute(_tool(tool_name="Read_Doc"))
        assert d.policy is not None

    def test_tool_name_bad_charset_still_rejected(self, attributor):
        d = attributor.attribute(_tool(tool_name="Read Doc!"))
        assert d.policy is None
        assert "tool_name" in d.reason

    def test_description_control_char(self, attributor):
        d = attributor.attribute(_tool(description="hello\x00world"))
        assert d.policy is None
        assert "description" in d.reason

    def test_description_too_long(self, attributor):
        d = attributor.attribute(_tool(description="x" * 4097))
        assert d.policy is None
        assert "description" in d.reason

    def test_input_schema_not_dict(self, attributor):
        d = attributor.attribute(_tool(input_schema="not_a_dict"))  # type: ignore
        assert d.policy is None
        assert "input_schema" in d.reason

    def test_trust_score_invalid_range(self, attributor):
        d = attributor.attribute(_tool(), trust_score=-1)
        assert d.policy is None
        assert "trust_score" in d.reason

    def test_trust_score_bool_rejected(self, attributor):
        d = attributor.attribute(_tool(), trust_score=True)  # type: ignore
        assert d.policy is None
        assert "trust_score" in d.reason

    def test_minimal_valid_accepted(self, attributor):
        d = attributor.attribute(_tool(tool_name="read_doc"))
        assert d.policy == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Tokenisation
# ══════════════════════════════════════════════════════════════════════════════


class TestTokenization:
    def test_tokenize_underscore(self):
        assert _tokenize("get_user_name") == ["get", "user", "name"]

    def test_tokenize_dash(self):
        assert _tokenize("get-user-name") == ["get", "user", "name"]

    def test_tokenize_dot(self):
        assert _tokenize("my.tool.name") == ["my", "tool", "name"]

    def test_tokenize_mixed_separators(self):
        assert _tokenize("my_tool-name.x") == ["my", "tool", "name", "x"]

    def test_tokenize_empty(self):
        assert _tokenize("") == []
        assert _tokenize(None) == []

    def test_tokenize_lowercase(self):
        assert _tokenize("Get_User") == ["get", "user"]


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Keyword matching READ_ONLY
# ══════════════════════════════════════════════════════════════════════════════


class TestReadOnlyKeywords:
    @pytest.mark.parametrize("tool_name", [
        "read_file",
        "get_status",
        "list_users",
        "search_items",
        "find_email",
        "view_page",
        "describe_table",
        "show_config",
        "query_db",
        "check_health",
        "lookup_record",
        "inspect_state",
    ])
    def test_read_only_classification(self, attributor, tool_name):
        d = attributor.attribute(_tool(tool_name=tool_name))
        assert d.policy == MCPPolicy.READ_ONLY

    def test_read_only_no_trust_required(self, attributor):
        d = attributor.attribute(_tool(tool_name="read_doc"), trust_score=None)
        assert d.policy == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Keyword matching EXTERNAL_READ
# ══════════════════════════════════════════════════════════════════════════════


class TestExternalReadKeywords:
    @pytest.mark.parametrize("tool_name", [
        "fetch_url",
        "download_doc",
        "scrape_page",
        "browse_site",
        "remote_lookup",
        "external_call",
    ])
    def test_external_read_classification(self, attributor, tool_name):
        d = attributor.attribute(_tool(tool_name=tool_name))
        assert d.policy == MCPPolicy.EXTERNAL_READ

    def test_external_read_no_trust_required(self, attributor):
        d = attributor.attribute(_tool(tool_name="fetch_url"), trust_score=None)
        assert d.policy == MCPPolicy.EXTERNAL_READ


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — LOCAL_WRITE explicite uniquement
# ══════════════════════════════════════════════════════════════════════════════


class TestLocalWriteExplicitOnly:
    def test_write_file_classified_local(self, attributor):
        d = attributor.attribute(_tool(tool_name="write_file"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_write_local_classified_local(self, attributor):
        d = attributor.attribute(_tool(tool_name="write_local"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_local_save_classified_local(self, attributor):
        d = attributor.attribute(_tool(tool_name="local_save"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_local_save_bigram(self, attributor):
        d = attributor.attribute(_tool(tool_name="local_save_doc"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_cache_file_classified_local(self, attributor):
        d = attributor.attribute(_tool(tool_name="cache_file"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    # save/store/persist seuls → EXTERNAL_WRITE_RECOVERABLE
    def test_save_alone_classified_recoverable(self, attributor):
        d = attributor.attribute(_tool(tool_name="save_doc"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_store_alone_classified_recoverable(self, attributor):
        d = attributor.attribute(_tool(tool_name="store_item"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_persist_alone_classified_recoverable(self, attributor):
        d = attributor.attribute(_tool(tool_name="persist_state"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    # ── Priorité LOCAL_WRITE > EXTERNAL_WRITE_RECOVERABLE (bug v2 corrigé) ──

    def test_priority_local_save_wins_over_recoverable(self, attributor):
        """local_save : bigramme (local,save) → LOCAL, "save" → RECOVERABLE.
        LOCAL doit gagner."""
        d = attributor.attribute(_tool(tool_name="local_save"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_priority_local_save_doc_wins_over_recoverable(self, attributor):
        """local_save_doc : bigramme (local,save) → LOCAL, "save" → RECOVERABLE.
        LOCAL doit gagner."""
        d = attributor.attribute(_tool(tool_name="local_save_doc"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_priority_local_store_item_wins_over_recoverable(self, attributor):
        """local_store_item : bigramme (local,store) → LOCAL, "store" → RECOVERABLE.
        LOCAL doit gagner."""
        d = attributor.attribute(_tool(tool_name="local_store_item"), trust_score=75)
        assert d.policy == MCPPolicy.LOCAL_WRITE

    def test_priority_save_doc_without_local_stays_recoverable(self, attributor):
        """save_doc sans signal local → RECOVERABLE (aucun bigramme local)."""
        d = attributor.attribute(_tool(tool_name="save_doc"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_priority_delete_local_file_stays_irreversible(self, attributor):
        """delete_local_file : IRREVERSIBLE doit rester au-dessus de LOCAL_WRITE."""
        d = attributor.attribute(_tool(tool_name="delete_local_file"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Keyword matching EXTERNAL_WRITE_RECOVERABLE
# ══════════════════════════════════════════════════════════════════════════════


class TestExternalWriteRecoverableKeywords:
    @pytest.mark.parametrize("tool_name", [
        "send_message",
        "create_issue",
        "post_comment",
        "publish_post",
        "add_item",
        "insert_record",
        "update_record",
        "edit_doc",
        "patch_field",
        "schedule_event",
        "submit_form",
        "reply_to_thread",
    ])
    def test_write_recoverable_classification(self, attributor, tool_name):
        d = attributor.attribute(_tool(tool_name=tool_name), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Keyword matching EXTERNAL_WRITE_IRREVERSIBLE
# ══════════════════════════════════════════════════════════════════════════════


class TestExternalWriteIrreversibleKeywords:
    @pytest.mark.parametrize("tool_name", [
        "delete_file",
        "drop_table",
        "destroy_resource",
        "purge_logs",
        "wipe_data",
        "truncate_table",
        "kill_process",
        "terminate_session",
        "shutdown_server",
        "exec_script",
        "execute_command",
        "run_shell",
        "eval_expr",
    ])
    def test_write_irreversible_classification(self, attributor, tool_name):
        d = attributor.attribute(_tool(tool_name=tool_name), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Trust gating après classification
# ══════════════════════════════════════════════════════════════════════════════


class TestTrustGating:
    def test_read_only_no_gate_even_without_trust(self, attributor):
        d = attributor.attribute(_tool(tool_name="read_doc"), trust_score=None)
        assert d.policy == MCPPolicy.READ_ONLY

    def test_external_read_no_gate_even_without_trust(self, attributor):
        d = attributor.attribute(_tool(tool_name="fetch_url"), trust_score=None)
        assert d.policy == MCPPolicy.EXTERNAL_READ

    def test_write_recoverable_trust_none_refused(self, attributor):
        d = attributor.attribute(_tool(tool_name="send_message"), trust_score=None)
        assert d.policy is None
        assert d.reason == "trust_score_missing_for_write"
        assert d.classified_policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_write_recoverable_trust_below_threshold(self, attributor):
        d = attributor.attribute(_tool(tool_name="send_message"), trust_score=50)
        assert d.policy is None
        assert d.reason == "trust_too_low_for_write:50"
        assert d.classified_policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_write_recoverable_trust_exact_threshold(self, attributor):
        d = attributor.attribute(_tool(tool_name="send_message"), trust_score=70)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_write_irreversible_trust_below(self, attributor):
        d = attributor.attribute(_tool(tool_name="delete_file"), trust_score=60)
        assert d.policy is None
        assert d.reason.startswith("trust_too_low_for_write")
        assert d.classified_policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE

    def test_local_write_trust_required(self, attributor):
        d = attributor.attribute(_tool(tool_name="write_file"), trust_score=50)
        assert d.policy is None
        assert d.reason.startswith("trust_too_low_for_write")
        assert d.classified_policy == MCPPolicy.LOCAL_WRITE

    def test_secrets_auth_trust_none(self, attributor):
        d = attributor.attribute(_tool(tool_name="oauth_login"), trust_score=None)
        assert d.policy is None
        assert d.reason == "trust_score_missing_for_secrets"
        assert d.classified_policy == MCPPolicy.SECRETS_AUTH

    def test_secrets_auth_trust_75_refused(self, attributor):
        """trust=75 ≥ write seuil mais < 90 secrets → refus secrets-specific."""
        d = attributor.attribute(_tool(tool_name="oauth_login"), trust_score=75)
        assert d.policy is None
        assert d.reason == "trust_too_low_for_secrets:75"
        assert d.classified_policy == MCPPolicy.SECRETS_AUTH

    def test_secrets_auth_trust_exact_90(self, attributor):
        d = attributor.attribute(_tool(tool_name="oauth_login"), trust_score=90)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_classification_logged_even_when_trust_refused(self, attributor):
        attributor.attribute(_tool(tool_name="send_message"), trust_score=50)
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_refused"]
        ev = events[-1]
        assert ev["classified_policy"] == "external_write_recoverable"
        assert ev["reason"].startswith("trust_too_low_for_write")

    def test_audit_includes_trust_score_used(self, attributor):
        attributor.attribute(_tool(tool_name="send_message"), trust_score=80)
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_ok"]
        assert events[-1]["trust_score_used"] == 80


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Keyword matching SECRETS_AUTH (+bigrammes)
# ══════════════════════════════════════════════════════════════════════════════


class TestSecretsAuthKeywords:
    @pytest.mark.parametrize("tool_name", [
        "auth_user",
        "authorize_request",
        "login_user",
        "logout_session",
        "get_token",
        "rotate_tokens",
        "list_credentials",
        "fetch_secret",
        "rotate_secrets",
        "set_password",
        "rotate_passwords",
        "oauth_callback",
        "get_apikey",
    ])
    def test_secrets_auth_classification(self, attributor, tool_name):
        d = attributor.attribute(_tool(tool_name=tool_name), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_bigram_api_key(self, attributor):
        d = attributor.attribute(_tool(tool_name="get_api_key"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH
        assert "api_key" in d.matched_keywords

    def test_bigram_refresh_token(self, attributor):
        d = attributor.attribute(_tool(tool_name="rotate_refresh_token"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_bigram_access_token(self, attributor):
        d = attributor.attribute(_tool(tool_name="get_access_token"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_cache_key_not_secrets_auth(self, attributor):
        """cache_key ne doit pas être attrapé comme SECRETS_AUTH (bigramme
        (cache, key) absent du whitelist)."""
        d = attributor.attribute(_tool(tool_name="get_cache_key"))
        # "get" → READ_ONLY ; "cache", "key" pas matchés ; bigramme
        # (cache,key) pas whitelisté
        assert d.policy == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Description fallback restrictif (escalation only)
# ══════════════════════════════════════════════════════════════════════════════


class TestDescriptionFallback:
    def test_description_can_escalate_read_to_write(self, attributor):
        """tool_name=READ_ONLY + description=WRITE → WRITE."""
        d = attributor.attribute(
            _tool(
                tool_name="get_doc",
                description="send and update via email",
            ),
            trust_score=75,
        )
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_description_can_escalate_read_to_secrets(self, attributor):
        """tool_name=opaque + description=SECRETS → SECRETS."""
        d = attributor.attribute(
            _tool(
                tool_name="get_x",
                description="returns oauth credentials",
            ),
            trust_score=95,
        )
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_description_alone_read_only_refused(self, attributor):
        """tool_name n'a rien matché + description READ_ONLY → refusé."""
        d = attributor.attribute(
            _tool(
                tool_name="opaque_action",
                description="read documents",
            ),
        )
        assert d.policy is None
        assert d.reason == "no_keyword_match"

    def test_description_alone_external_read_refused(self, attributor):
        """tool_name vide + description EXTERNAL_READ → refusé."""
        d = attributor.attribute(
            _tool(
                tool_name="opaque",
                description="fetches remote data",
            ),
        )
        assert d.policy is None
        assert d.reason == "no_keyword_match"

    def test_description_cannot_downgrade_secrets_to_read(self, attributor):
        """tool_name=SECRETS + description=READ → reste SECRETS."""
        d = attributor.attribute(
            _tool(
                tool_name="get_token",
                description="just reads value",
            ),
            trust_score=95,
        )
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_description_cannot_downgrade_irreversible(self, attributor):
        """tool_name=IRREVERSIBLE + description=UPDATE → reste IRREVERSIBLE."""
        d = attributor.attribute(
            _tool(
                tool_name="delete_thing",
                description="just an update",
            ),
            trust_score=75,
        )
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE

    def test_description_local_write_escalation(self, attributor):
        """tool_name=opaque + description=write_file → LOCAL_WRITE."""
        d = attributor.attribute(
            _tool(
                tool_name="opaque_action",
                description="performs a write_file operation",
            ),
            trust_score=75,
        )
        assert d.policy == MCPPolicy.LOCAL_WRITE


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Priorité conservatrice multi-match
# ══════════════════════════════════════════════════════════════════════════════


class TestMultiMatchPriority:
    def test_read_and_delete_returns_irreversible(self, attributor):
        d = attributor.attribute(_tool(tool_name="read_and_delete"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE

    def test_send_token_returns_secrets(self, attributor):
        d = attributor.attribute(_tool(tool_name="send_token"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_get_password_returns_secrets(self, attributor):
        d = attributor.attribute(_tool(tool_name="get_password"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_list_credentials_returns_secrets(self, attributor):
        d = attributor.attribute(_tool(tool_name="list_credentials"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_fetch_secret_returns_secrets(self, attributor):
        d = attributor.attribute(_tool(tool_name="fetch_secret"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_create_session_token_returns_secrets(self, attributor):
        d = attributor.attribute(_tool(tool_name="create_session_token"), trust_score=95)
        assert d.policy == MCPPolicy.SECRETS_AUTH

    def test_publish_delete_action_returns_irreversible(self, attributor):
        d = attributor.attribute(_tool(tool_name="publish_delete_action"), trust_score=75)
        assert d.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE

    def test_deterministic_token_order(self, attributor):
        d1 = attributor.attribute(_tool(tool_name="get_token"), trust_score=95)
        d2 = attributor.attribute(_tool(tool_name="token_get"), trust_score=95)
        assert d1.policy == d2.policy == MCPPolicy.SECRETS_AUTH


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — No match / refus
# ══════════════════════════════════════════════════════════════════════════════


class TestNoMatch:
    def test_xyz_no_match(self, attributor):
        d = attributor.attribute(_tool(tool_name="xyz_abc_def"))
        assert d.policy is None
        assert d.reason == "no_keyword_match"

    def test_unique_tool_name_no_description(self, attributor):
        d = attributor.attribute(_tool(tool_name="opaquething"))
        assert d.policy is None

    def test_empty_description(self, attributor):
        d = attributor.attribute(_tool(tool_name="opaqueact", description=""))
        assert d.policy is None

    def test_description_without_keyword(self, attributor):
        d = attributor.attribute(
            _tool(tool_name="opaqueact", description="just some xyz"),
        )
        assert d.policy is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensic:
    def test_audit_never_contains_description(self, attributor):
        attributor.attribute(_tool(
            tool_name="read_doc",
            description="DESCRIPTION_LEAK_MARKER_AAA contains secret",
        ))
        blob = _audit_blob(attributor)
        assert "DESCRIPTION_LEAK_MARKER_AAA" not in blob

    def test_audit_never_contains_input_schema(self, attributor):
        attributor.attribute(_tool(
            tool_name="read_doc",
            input_schema={"secret_field": "SCHEMA_LEAK_MARKER_BBB"},
        ))
        blob = _audit_blob(attributor)
        assert "SCHEMA_LEAK_MARKER_BBB" not in blob

    def test_audit_identifiers_present(self, attributor):
        attributor.attribute(_tool(
            server_id="alice", tool_name="read_doc",
        ))
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_ok"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["tool_name"] == "read_doc"
        assert ev["policy"] == "read_only"
        assert "read" in ev["matched_keywords"]

    def test_audit_does_not_stringify_attributor(self, attributor):
        attributor.attribute(_tool(tool_name="read_doc"))
        blob = _audit_blob(attributor)
        assert "PolicyAttributor" not in blob
        assert "object at 0x" not in blob

    def test_audit_reason_codes_short(self, attributor):
        attributor.attribute(_tool(tool_name="send_message"), trust_score=50)
        attributor.attribute(_tool(tool_name="read_doc"))
        attributor.attribute(_tool(tool_name="xyz_abc"))
        for ev in _audit_lines(attributor):
            assert "reason" in ev
            assert isinstance(ev["reason"], str)
            assert len(ev["reason"]) < 64  # codes courts

    def test_audit_multi_attribution_forensic_scan(self, attributor):
        markers = [f"FORENSIC_ATTRIBUTOR_MARKER_{i}" for i in range(10)]
        for i, m in enumerate(markers):
            attributor.attribute(_tool(
                tool_name="read_doc",
                description=f"some text {m}",
                input_schema={"field": m},
            ))
        blob = _audit_blob(attributor)
        for m in markers:
            assert m not in blob

    def test_audit_attribution_ok_format(self, attributor):
        attributor.attribute(_tool(tool_name="send_message"), trust_score=80)
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_ok"]
        ev = events[-1]
        assert ev["policy"] == "external_write_recoverable"
        assert ev["reason"].startswith("match:")
        assert ev["trust_score_used"] == 80

    def test_audit_attribution_refused_format(self, attributor):
        attributor.attribute(_tool(tool_name="send_message"), trust_score=None)
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_refused"]
        ev = events[-1]
        assert ev["reason"] == "trust_score_missing_for_write"
        assert ev["classified_policy"] == "external_write_recoverable"

    def test_matched_keywords_from_whitelist_only(self, attributor):
        """matched_keywords ne contiennent QUE des codes whitelist/bigrammes,
        jamais des tokens raw arbitraires."""
        d = attributor.attribute(_tool(tool_name="read_strange_arbitrary_thing"))
        assert d.policy == MCPPolicy.READ_ONLY
        # Seul "read" doit être présent (les autres tokens ne sont pas
        # dans la whitelist)
        for kw in d.matched_keywords:
            assert kw == "read" or "_" in kw  # bigramme ou whitelist

    def test_audit_no_module_paths(self, attributor):
        attributor.attribute(_tool(tool_name="read_doc"))
        blob = _audit_blob(attributor)
        assert "src.mcp" not in blob
        assert "C:\\" not in blob
        assert "/home/" not in blob

    def test_audit_metadata_invalid_does_not_leak(self, attributor):
        """server_id invalide avec marker → audit ne loggue ni server_id ni tool_name."""
        attributor.attribute(_tool(
            server_id="ATTACKER_SERVER_MARKER_CCC",
            tool_name="ATTACKER_TOOL_MARKER_DDD",
        ))
        blob = _audit_blob(attributor)
        assert "ATTACKER_SERVER_MARKER_CCC" not in blob
        assert "ATTACKER_TOOL_MARKER_DDD" not in blob

    def test_audit_metadata_partial_valid_logs_only_valid(self, attributor):
        """server_id valide + tool_name invalide → audit log server_id, pas tool_name."""
        attributor.attribute(_tool(
            server_id="alice",
            tool_name="!!!ATTACKER_TOOL_EEE!!!",
        ))
        blob = _audit_blob(attributor)
        assert "ATTACKER_TOOL_EEE" not in blob
        events = [e for e in _audit_lines(attributor) if e["event"] == "attribution_refused"]
        ev = events[-1]
        assert ev.get("server_id") == "alice"
        assert "tool_name" not in ev
