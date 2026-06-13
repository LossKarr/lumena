"""
Tests Phase 11 v3 — AutoApproveEngine.

Couvre :
  - Validation add_pattern (DSL stricte, garde-fous, glob borné)
  - Évaluation : MATCHED, NO_MATCH, POLICY_MISMATCH, CALLER_NOT_ALLOWED,
    CONSTRAINTS_VIOLATED, QUOTA_EXCEEDED, EXPIRED, INTEGRITY_INVALID
  - Side effects bornés : quota incrémenté UNIQUEMENT sur MATCHED
  - Audit forensique : aucune valeur d'args dans audit.jsonl
  - Currency strict : amount_eur direct OU amount+currency=="EUR"
  - HMAC invalide : aucune confiance aux champs métier du pattern
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from cryptography.fernet import Fernet

from src.mcp.auto_approve import (
    AutoApproveDecision,
    AutoApproveEngine,
    AutoApproveError,
    AutoApproveEvaluation,
    AutoApprovePattern,
    _compute_integrity_hmac,
)
from src.mcp.policy import MCPPolicy


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


class _InMemorySecretsService:
    """SecretsService minimal en mémoire pour les tests (pas keyring)."""

    def __init__(self):
        self._store: Dict[str, str] = {}

    def get(self, scope: str, name: str) -> Optional[str]:
        return self._store.get(f"{scope}::{name}")

    def set(self, scope: str, name: str, value: str) -> None:
        self._store[f"{scope}::{name}"] = value


@pytest.fixture
def secrets() -> _InMemorySecretsService:
    return _InMemorySecretsService()


@pytest.fixture
def engine(tmp_path: Path, secrets: _InMemorySecretsService) -> AutoApproveEngine:
    return AutoApproveEngine(
        patterns_dir=tmp_path / "patterns",
        audit_log_path=tmp_path / "audit" / "audit.jsonl",
        quotas_dir=tmp_path / "quotas",
        secrets_service=secrets,
    )


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


_SENTINEL = object()


def _add_minimal_pattern(
    engine: AutoApproveEngine,
    *,
    profile: str = "alice",
    kind: str = "slack_notify",
    tool_name_pattern: str = "mcp__slack__send_message",
    policy: MCPPolicy = MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
    caller_kinds_allowed: Any = _SENTINEL,
    args_constraints: Any = _SENTINEL,
    quota_max_per_day: int = 10,
    expires_at: Optional[str] = None,
) -> str:
    if caller_kinds_allowed is _SENTINEL:
        caller_kinds_allowed = ["react"]
    if args_constraints is _SENTINEL:
        args_constraints = {"channel_allowlist": ["#general"]}
    if expires_at is None:
        expires_at = _future_iso(30)
    return engine.add_pattern(
        profile=profile,
        kind=kind,
        tool_name_pattern=tool_name_pattern,
        policy=policy,
        caller_kinds_allowed=caller_kinds_allowed,
        args_constraints=args_constraints,
        quota_max_per_day=quota_max_per_day,
        expires_at=expires_at,
    )


def _audit_lines(engine: AutoApproveEngine) -> List[Dict[str, Any]]:
    if not engine.audit_log_path.exists():
        return []
    out = []
    with open(engine.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(engine: AutoApproveEngine) -> str:
    if not engine.audit_log_path.exists():
        return ""
    return engine.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Validation add_pattern (DSL stricte, garde-fous)
# ══════════════════════════════════════════════════════════════════════════════


class TestAddPatternValidation:
    def test_profile_invalid_uppercase_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="Invalid profile"):
            _add_minimal_pattern(engine, profile="ALICE")

    def test_profile_invalid_empty_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="Invalid profile"):
            _add_minimal_pattern(engine, profile="")

    def test_kind_empty_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="Invalid kind"):
            _add_minimal_pattern(engine, kind="")

    def test_policy_must_be_mcppolicy_enum(self, engine):
        with pytest.raises(AutoApproveError, match="policy must be MCPPolicy"):
            engine.add_pattern(
                profile="alice",
                kind="x",
                tool_name_pattern="mcp__a__b",
                policy="external_write_recoverable",  # type: ignore[arg-type]
                caller_kinds_allowed=["react"],
                args_constraints={"channel_allowlist": ["#x"]},
                quota_max_per_day=1,
                expires_at=_future_iso(1),
            )

    def test_tool_name_too_short_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="too short"):
            _add_minimal_pattern(engine, tool_name_pattern="mcp__a")

    def test_tool_name_star_only_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="too short|too broad"):
            _add_minimal_pattern(engine, tool_name_pattern="*")

    def test_tool_name_mcp_star_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="too short|too broad"):
            _add_minimal_pattern(engine, tool_name_pattern="mcp__*")

    def test_tool_name_bad_format_rejected(self, engine):
        with pytest.raises(AutoApproveError):
            _add_minimal_pattern(engine, tool_name_pattern="not_mcp_format")

    def test_glob_allowed_for_read_only(self, engine):
        pid = _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__notion__*",
            policy=MCPPolicy.READ_ONLY,
        )
        assert isinstance(pid, str) and len(pid) == 32

    def test_glob_allowed_for_external_read(self, engine):
        pid = _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__notion__*",
            policy=MCPPolicy.EXTERNAL_READ,
        )
        assert isinstance(pid, str)

    def test_glob_forbidden_for_external_write_recoverable(self, engine):
        with pytest.raises(AutoApproveError, match="Glob.*only allowed"):
            _add_minimal_pattern(
                engine,
                tool_name_pattern="mcp__slack__*",
                policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            )

    def test_glob_forbidden_for_external_write_irreversible(self, engine):
        with pytest.raises(AutoApproveError, match="Glob.*only allowed"):
            _add_minimal_pattern(
                engine,
                tool_name_pattern="mcp__bank__*",
                policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            )

    def test_caller_kinds_empty_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="non-empty"):
            _add_minimal_pattern(engine, caller_kinds_allowed=[])

    def test_caller_kinds_unknown_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="Unknown caller_kind"):
            _add_minimal_pattern(engine, caller_kinds_allowed=["root"])

    def test_args_constraints_empty_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="non-empty"):
            _add_minimal_pattern(engine, args_constraints={})

    def test_args_constraints_unknown_key_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="Unknown constraint key"):
            _add_minimal_pattern(
                engine, args_constraints={"random_evil_field": "x"}
            )

    def test_allowlist_must_be_list(self, engine):
        with pytest.raises(AutoApproveError, match="must be of type list"):
            _add_minimal_pattern(
                engine, args_constraints={"to_allowlist": "alice@x.com"}
            )

    def test_allowlist_empty_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="non-empty"):
            _add_minimal_pattern(engine, args_constraints={"to_allowlist": []})

    def test_max_chars_zero_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="must be > 0"):
            _add_minimal_pattern(
                engine,
                args_constraints={
                    "channel_allowlist": ["#x"],
                    "subject_max_chars": 0,
                },
            )

    def test_amount_negative_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="must be > 0"):
            _add_minimal_pattern(
                engine,
                args_constraints={
                    "channel_allowlist": ["#x"],
                    "amount_max_eur": -1.0,
                },
            )

    def test_quota_zero_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="quota_max_per_day must be > 0"):
            _add_minimal_pattern(engine, quota_max_per_day=0)

    def test_quota_negative_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="quota_max_per_day must be > 0"):
            _add_minimal_pattern(engine, quota_max_per_day=-5)

    def test_quota_bool_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="must be int"):
            _add_minimal_pattern(engine, quota_max_per_day=True)  # type: ignore[arg-type]

    def test_expires_at_in_past_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="must be in the future"):
            _add_minimal_pattern(engine, expires_at=_past_iso(1))

    def test_expires_at_exceeds_max_lifetime_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="exceeds max lifetime"):
            _add_minimal_pattern(engine, expires_at=_future_iso(365))

    def test_expires_at_garbage_rejected(self, engine):
        with pytest.raises(AutoApproveError, match="ISO 8601"):
            _add_minimal_pattern(engine, expires_at="not_a_date")

    def test_attachments_forbidden_default_true(self, engine):
        pid = _add_minimal_pattern(engine)
        pat = engine.get_pattern(pid)
        assert pat is not None
        assert pat.args_constraints.get("attachments_forbidden") is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Persistence : add / get / list / remove
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_add_returns_uuid4_hex(self, engine):
        pid = _add_minimal_pattern(engine)
        parsed = uuid.UUID(pid)
        assert parsed.version == 4
        assert parsed.hex == pid

    def test_get_pattern_roundtrip(self, engine):
        pid = _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__slack__send_message",
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kinds_allowed=["react", "autonomy"],
        )
        pat = engine.get_pattern(pid)
        assert pat is not None
        assert pat.id == pid
        assert pat.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        assert pat.caller_kinds_allowed == ["react", "autonomy"]
        assert pat.tool_name_pattern == "mcp__slack__send_message"

    def test_get_pattern_unknown_returns_none(self, engine):
        assert engine.get_pattern(uuid.uuid4().hex) is None

    def test_get_pattern_invalid_id_raises(self, engine):
        with pytest.raises(AutoApproveError, match="Invalid pattern_id"):
            engine.get_pattern("not-uuid4")

    def test_list_patterns_by_profile(self, engine):
        pid_a = _add_minimal_pattern(engine, profile="alice")
        pid_b = _add_minimal_pattern(engine, profile="bob")
        alice = [p.id for p in engine.list_patterns(profile="alice")]
        bob = [p.id for p in engine.list_patterns(profile="bob")]
        assert pid_a in alice and pid_a not in bob
        assert pid_b in bob and pid_b not in alice

    def test_list_patterns_all_profiles(self, engine):
        pid_a = _add_minimal_pattern(engine, profile="alice")
        pid_b = _add_minimal_pattern(engine, profile="bob")
        all_ids = {p.id for p in engine.list_patterns()}
        assert {pid_a, pid_b}.issubset(all_ids)

    def test_remove_pattern_idempotent(self, engine):
        pid = _add_minimal_pattern(engine)
        assert engine.remove_pattern(pid) is True
        assert engine.remove_pattern(pid) is False
        assert engine.get_pattern(pid) is None

    def test_pattern_file_is_encrypted_on_disk(self, engine):
        pid = _add_minimal_pattern(
            engine,
            args_constraints={"channel_allowlist": ["#SECRET_CHANNEL_MARKER"]},
        )
        # Cherche le fichier
        files = list((engine.patterns_root).rglob(f"{pid}.json"))
        assert len(files) == 1
        raw = files[0].read_text(encoding="utf-8")
        assert "SECRET_CHANNEL_MARKER" not in raw
        # Doit être un wrapper avec ciphertext
        data = json.loads(raw)
        assert "ciphertext" in data


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — evaluate : MATCHED + NO_MATCH
# ══════════════════════════════════════════════════════════════════════════════


class TestEvaluateBasics:
    def test_evaluate_no_match_when_no_pattern(self, engine):
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.NO_MATCH

    def test_evaluate_matched_exact_tool_name(self, engine):
        pid = _add_minimal_pattern(engine)
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED
        assert res.matched_pattern_id == pid
        assert res.quota_consumed is True

    def test_evaluate_matched_glob_tool_name(self, engine):
        _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__notion__*",
            policy=MCPPolicy.READ_ONLY,
            args_constraints={"url_allowlist": ["https://notion.so/page"]},
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__notion__search",
            args={"url": "https://notion.so/page"},
            policy=MCPPolicy.READ_ONLY,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_evaluate_no_match_tool_name_does_not_match(self, engine):
        _add_minimal_pattern(engine, tool_name_pattern="mcp__slack__send_message")
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__other__tool",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.NO_MATCH

    def test_evaluate_invalid_args_type(self, engine):
        with pytest.raises(AutoApproveError, match="args must be a dict"):
            engine.evaluate(
                profile="alice",
                tool_name="mcp__a__b",
                args="not a dict",  # type: ignore[arg-type]
                policy=MCPPolicy.READ_ONLY,
                caller_kind="react",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Cohérence decisions : POLICY_MISMATCH, CALLER_NOT_ALLOWED
# ══════════════════════════════════════════════════════════════════════════════


class TestPolicyAndCallerCoherence:
    def test_policy_mismatch_returns_policy_mismatch_decision(self, engine):
        pid = _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__slack__send_message",
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,  # mismatch
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.POLICY_MISMATCH
        assert res.matched_pattern_id == pid

    def test_caller_not_allowed_returns_caller_not_allowed_decision(self, engine):
        pid = _add_minimal_pattern(
            engine,
            caller_kinds_allowed=["react"],
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="autonomy",  # not in allowed
        )
        assert res.decision == AutoApproveDecision.CALLER_NOT_ALLOWED
        assert res.matched_pattern_id == pid

    def test_constraints_violated_returns_decision_when_tool_and_policy_match(
        self, engine
    ):
        pid = _add_minimal_pattern(
            engine,
            args_constraints={"channel_allowlist": ["#general"]},
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#forbidden"},  # not in allowlist
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED
        assert res.matched_pattern_id == pid
        assert res.reason == "constraint_violated:channel_allowlist"

    def test_priority_policy_mismatch_over_caller_when_multiple_patterns(self, engine):
        # Pattern 1 : tool match mais policy mismatch
        _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__slack__send_message",
            policy=MCPPolicy.READ_ONLY,
            caller_kinds_allowed=["react"],
        )
        # Pattern 2 : tool match + policy match mais caller mismatch
        _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__slack__send_message",
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kinds_allowed=["scheduler"],
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        # Hiérarchie : POLICY_MISMATCH > CALLER_NOT_ALLOWED
        assert res.decision == AutoApproveDecision.POLICY_MISMATCH


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Constraints : allowlist, max_chars, attachments
# ══════════════════════════════════════════════════════════════════════════════


class TestConstraintsAllowlist:
    def test_to_allowlist_single_value_ok(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={"to_allowlist": ["alice@x.com", "bob@x.com"]},
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"to": "alice@x.com"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_to_allowlist_list_subset_ok(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={"to_allowlist": ["alice@x.com", "bob@x.com"]},
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"to": ["alice@x.com", "bob@x.com"]},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_to_allowlist_list_with_outsider_violates(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={"to_allowlist": ["alice@x.com"]},
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"to": ["alice@x.com", "evil@evil.com"]},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED

    def test_subject_max_chars_ok(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#x"],
                "subject_max_chars": 10,
            },
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#x", "subject": "hello"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_subject_max_chars_exceeded(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#x"],
                "subject_max_chars": 5,
            },
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#x", "subject": "way too long"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED
        assert res.reason == "constraint_violated:subject_max_chars"

    def test_attachments_forbidden_default_blocks_attachments(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={"channel_allowlist": ["#x"]},
            # attachments_forbidden=True par défaut
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#x", "attachments": ["file.pdf"]},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Currency strict : amount_eur/amount_usd
# ══════════════════════════════════════════════════════════════════════════════


class TestAmountCurrencyStrict:
    @pytest.fixture
    def pid_eur(self, engine):
        return _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#fin"],
                "amount_max_eur": 100.0,
            },
        )

    def _evaluate(self, engine, args):
        return engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args=args,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )

    def test_amount_eur_direct_field_ok(self, engine, pid_eur):
        res = self._evaluate(engine, {"channel": "#fin", "amount_eur": 50.0})
        assert res.decision == AutoApproveDecision.MATCHED

    def test_amount_eur_direct_field_exceeded(self, engine, pid_eur):
        res = self._evaluate(engine, {"channel": "#fin", "amount_eur": 200.0})
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED
        assert res.reason == "constraint_violated:amount_max_eur"

    def test_amount_with_eur_currency_ok(self, engine, pid_eur):
        res = self._evaluate(
            engine, {"channel": "#fin", "amount": 50.0, "currency": "EUR"}
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_amount_currency_case_insensitive(self, engine, pid_eur):
        for cur in ("eur", "Eur", "EUR"):
            engine.reset_quota(pid_eur)
            res = self._evaluate(
                engine, {"channel": "#fin", "amount": 50.0, "currency": cur}
            )
            assert res.decision == AutoApproveDecision.MATCHED, f"currency={cur}"

    def test_amount_without_currency_rejected(self, engine, pid_eur):
        res = self._evaluate(engine, {"channel": "#fin", "amount": 50.0})
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED
        assert res.reason == "constraint_violated:amount_max_eur"

    def test_amount_with_wrong_currency_rejected(self, engine, pid_eur):
        res = self._evaluate(
            engine, {"channel": "#fin", "amount": 50.0, "currency": "USD"}
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED

    def test_amount_missing_completely_rejected(self, engine, pid_eur):
        res = self._evaluate(engine, {"channel": "#fin"})
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED

    def test_amount_usd_direct_field_ok(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#fin"],
                "amount_max_usd": 100.0,
            },
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#fin", "amount_usd": 50.0},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED

    def test_amount_usd_with_eur_currency_rejected(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#fin"],
                "amount_max_usd": 100.0,
            },
        )
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#fin", "amount": 50.0, "currency": "EUR"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.CONSTRAINTS_VIOLATED


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Quota
# ══════════════════════════════════════════════════════════════════════════════


class TestQuota:
    def test_quota_incremented_only_on_matched(self, engine):
        pid = _add_minimal_pattern(engine, quota_max_per_day=10)
        # NO_MATCH ne doit pas incrémenter
        engine.evaluate(
            profile="alice",
            tool_name="mcp__other__tool",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert engine.get_quota_consumed(pid) == 0
        # CONSTRAINTS_VIOLATED ne doit pas incrémenter
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#forbidden"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert engine.get_quota_consumed(pid) == 0
        # MATCHED incrémente
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert engine.get_quota_consumed(pid) == 1

    def test_quota_exceeded_decision(self, engine):
        pid = _add_minimal_pattern(engine, quota_max_per_day=2)
        for _ in range(2):
            res = engine.evaluate(
                profile="alice",
                tool_name="mcp__slack__send_message",
                args={"channel": "#general"},
                policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
                caller_kind="react",
            )
            assert res.decision == AutoApproveDecision.MATCHED
        # 3e tentative
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.QUOTA_EXCEEDED
        assert res.matched_pattern_id == pid

    def test_reset_quota(self, engine):
        pid = _add_minimal_pattern(engine, quota_max_per_day=1)
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert engine.get_quota_consumed(pid) == 1
        assert engine.reset_quota(pid) is True
        assert engine.get_quota_consumed(pid) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Expiration
# ══════════════════════════════════════════════════════════════════════════════


class TestExpiration:
    def test_expired_pattern_returns_expired_or_no_match(self, engine, secrets):
        # Hack : add pattern avec expires_at futur, puis on simule expiration
        # en modifiant directement le fichier (mais ça casserait HMAC).
        # Approche : on crée un pattern, on l'écrit avec expires_at proche
        # puis on attend (impossible) — donc on triche via expires_at
        # juste >now mais on patche datetime.

        # Plus simple : on ajoute pattern qui expire dans 1 seconde
        from src.mcp import auto_approve as mod

        pid = _add_minimal_pattern(
            engine,
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        )
        # Patch _now_utc pour simuler future
        future = datetime.now(timezone.utc) + timedelta(days=2)
        original = mod._now_utc
        try:
            mod._now_utc = lambda: future
            res = engine.evaluate(
                profile="alice",
                tool_name="mcp__slack__send_message",
                args={"channel": "#general"},
                policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
                caller_kind="react",
            )
        finally:
            mod._now_utc = original
        assert res.decision == AutoApproveDecision.EXPIRED


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Intégrité HMAC : aucune confiance aux champs métier si invalide
# ══════════════════════════════════════════════════════════════════════════════


class TestIntegrityHMAC:
    def _corrupt_pattern_file(self, engine, pid: str, profile: str = "alice"):
        """Réécrit le fichier pattern avec un payload chiffré dont l'HMAC est
        invalide (champ tampered).
        """
        file_path = engine.patterns_root / profile / f"{pid}.json"
        # Récupère cipher + clé HMAC depuis l'engine
        cipher = engine._get_cipher()
        # On construit un faux record avec un HMAC garbage
        forged = {
            "id": pid,
            "profile": profile,
            "kind": "tampered",
            "tool_name_pattern": "mcp__evil__exec",
            "policy": MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE.value,
            "caller_kinds_allowed": ["react", "autonomy", "scheduler"],
            "args_constraints": {"channel_allowlist": ["#anywhere"]},
            "quota_max_per_day": 999999,
            "expires_at": _future_iso(30),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "integrity_hmac": "deadbeef" * 8,  # invalide
        }
        plaintext = json.dumps(forged).encode("utf-8")
        ciphertext = cipher.encrypt(plaintext).decode("utf-8")
        file_path.write_text(
            json.dumps({"ciphertext": ciphertext}), encoding="utf-8"
        )

    def test_corrupted_pattern_returns_integrity_invalid(self, engine):
        pid = _add_minimal_pattern(engine)
        self._corrupt_pattern_file(engine, pid)
        # On évalue avec le tool name TAMPERED — l'engine ne doit PAS faire
        # confiance à ce champ et retourner INTEGRITY_INVALID
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__evil__exec",
            args={"channel": "#anywhere"},
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.INTEGRITY_INVALID
        # AUCUN match — surtout pas de quota consommé
        assert res.quota_consumed is False

    def test_corrupted_pattern_get_pattern_returns_none(self, engine):
        pid = _add_minimal_pattern(engine)
        self._corrupt_pattern_file(engine, pid)
        assert engine.get_pattern(pid) is None

    def test_corrupted_pattern_list_patterns_excludes_it(self, engine):
        pid = _add_minimal_pattern(engine)
        self._corrupt_pattern_file(engine, pid)
        ids = [p.id for p in engine.list_patterns()]
        assert pid not in ids

    def test_integrity_audit_uses_filename_not_pattern_fields(self, engine):
        pid = _add_minimal_pattern(engine)
        self._corrupt_pattern_file(engine, pid)
        engine.evaluate(
            profile="alice",
            tool_name="mcp__whatever__x",
            args={},
            policy=MCPPolicy.READ_ONLY,
            caller_kind="react",
        )
        events = [e for e in _audit_lines(engine) if e.get("event") == "integrity_invalid"]
        assert events, "expected integrity_invalid audit event"
        ev = events[-1]
        # pattern_id provient du nom de fichier
        assert ev.get("pattern_id") == pid
        # Aucune confiance accordée aux champs métier tamperés
        blob = json.dumps(ev)
        assert "tampered" not in blob
        assert "mcp__evil__exec" not in blob
        assert "999999" not in blob

    def test_unreadable_pattern_treated_as_integrity_invalid(self, engine):
        pid = _add_minimal_pattern(engine)
        file_path = engine.patterns_root / "alice" / f"{pid}.json"
        file_path.write_text("not a valid wrapper", encoding="utf-8")
        res = engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.INTEGRITY_INVALID


# ══════════════════════════════════════════════════════════════════════════════
# Section 9bis — Binding fichier ↔ contenu (anti-copie/anti-rename)
# ══════════════════════════════════════════════════════════════════════════════


class TestFileBinding:
    """L'HMAC signe le contenu, pas le chemin. Si on copie un fichier
    pattern valide depuis profile="alice" vers profile="bob", ou si on le
    renomme vers un autre UUID, l'HMAC reste valide. Le binding check
    refuse ces déplacements."""

    SECRET_TOOL_MARKER = "mcp__slack__SECRET_FORENSIC_TOOL"
    SECRET_CHANNEL_MARKER = "#SECRET_FORENSIC_CHANNEL"

    def _add_pattern_in_profile(self, engine, profile: str) -> str:
        # Crée le dossier source proprement
        return engine.add_pattern(
            profile=profile,
            kind="forensic_test",
            tool_name_pattern=self.SECRET_TOOL_MARKER,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kinds_allowed=["react"],
            args_constraints={
                "channel_allowlist": [self.SECRET_CHANNEL_MARKER]
            },
            quota_max_per_day=10,
            expires_at=_future_iso(30),
        )

    def test_copied_pattern_from_alice_to_bob_never_matches(self, engine):
        pid = self._add_pattern_in_profile(engine, "alice")
        # Copie brutale du fichier alice/<pid>.json vers bob/<pid>.json
        src = engine.patterns_root / "alice" / f"{pid}.json"
        dst_dir = engine.patterns_root / "bob"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{pid}.json"
        dst.write_bytes(src.read_bytes())

        # evaluate sur profile="bob" doit refuser (binding mismatch :
        # record["profile"]=="alice" mais fichier dans dossier bob)
        res = engine.evaluate(
            profile="bob",
            tool_name=self.SECRET_TOOL_MARKER,
            args={"channel": self.SECRET_CHANNEL_MARKER},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.INTEGRITY_INVALID
        assert res.quota_consumed is False
        # Le quota d'alice n'a pas non plus été consommé via bob
        assert engine.get_quota_consumed(pid) == 0

    def test_copied_pattern_get_pattern_returns_none_for_wrong_profile(self, engine):
        pid = self._add_pattern_in_profile(engine, "alice")
        src = engine.patterns_root / "alice" / f"{pid}.json"
        dst_dir = engine.patterns_root / "bob"
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{pid}.json").write_bytes(src.read_bytes())

        # list_patterns(profile="bob") ne doit jamais voir ce pattern
        bob_patterns = engine.list_patterns(profile="bob")
        assert all(p.id != pid for p in bob_patterns) or len(bob_patterns) == 0
        # list_patterns(profile="alice") doit toujours voir l'original
        alice_patterns = engine.list_patterns(profile="alice")
        assert any(p.id == pid for p in alice_patterns)

    def test_renamed_pattern_file_rejected_by_get_list_evaluate(self, engine):
        # Crée un pattern, puis renomme le fichier vers un autre UUID4
        pid_original = self._add_pattern_in_profile(engine, "alice")
        pid_fake = uuid.uuid4().hex
        src = engine.patterns_root / "alice" / f"{pid_original}.json"
        dst = engine.patterns_root / "alice" / f"{pid_fake}.json"
        src.rename(dst)

        # get_pattern(pid_fake) doit refuser : record["id"] == pid_original
        # ne matche pas file.stem == pid_fake
        assert engine.get_pattern(pid_fake) is None
        # get_pattern(pid_original) doit aussi retourner None (fichier disparu)
        assert engine.get_pattern(pid_original) is None
        # list_patterns ne doit retourner aucun des deux
        all_ids = {p.id for p in engine.list_patterns()}
        assert pid_original not in all_ids
        assert pid_fake not in all_ids
        # evaluate : aucun match, retour INTEGRITY_INVALID (binding mismatch)
        res = engine.evaluate(
            profile="alice",
            tool_name=self.SECRET_TOOL_MARKER,
            args={"channel": self.SECRET_CHANNEL_MARKER},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.INTEGRITY_INVALID

    def test_binding_mismatch_audit_does_not_contain_pattern_fields(self, engine):
        """Audit forensique : si on copie un pattern alice→bob, l'audit
        binding_mismatch ne doit JAMAIS contenir les champs métier du
        record copié (record["id"], record["profile"]=="alice",
        record["tool_name_pattern"], record["caller_kinds_allowed"], etc.)
        — uniquement file.stem et le profile demandé."""
        pid = self._add_pattern_in_profile(engine, "alice")
        src = engine.patterns_root / "alice" / f"{pid}.json"
        dst_dir = engine.patterns_root / "bob"
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{pid}.json").write_bytes(src.read_bytes())

        engine.evaluate(
            profile="bob",
            tool_name=self.SECRET_TOOL_MARKER,
            args={"channel": self.SECRET_CHANNEL_MARKER},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        events = [
            e for e in _audit_lines(engine)
            if e.get("event") == "integrity_invalid"
        ]
        # Trouve l'event pour profile=bob
        bob_events = [e for e in events if e.get("profile") == "bob"]
        assert bob_events, "expected integrity_invalid audit event for profile=bob"
        ev = bob_events[-1]
        # pattern_id = file.stem (légitime ici car c'est aussi le record id,
        # mais ce qui compte est qu'il provienne du filename, pas du record)
        assert ev.get("pattern_id") == pid
        assert ev.get("profile") == "bob"
        assert ev.get("reason") == "binding_mismatch"

        # AUCUN champ métier du record ne doit fuiter dans cet event
        ev_blob = json.dumps(ev)
        # Le SECRET_TOOL_MARKER est dans record["tool_name_pattern"] — ne
        # doit pas apparaître dans l'audit du binding_mismatch
        assert "SECRET_FORENSIC_TOOL" not in ev_blob
        # forensic_test = record["kind"]
        assert "forensic_test" not in ev_blob
        # "alice" = record["profile"] (l'original copié)
        assert "alice" not in ev_blob

    def test_binding_check_succeeds_for_legitimate_pattern(self, engine):
        """Sanity check : un pattern non-copié doit évidemment passer
        le binding check et matcher normalement."""
        pid = self._add_pattern_in_profile(engine, "alice")
        res = engine.evaluate(
            profile="alice",
            tool_name=self.SECRET_TOOL_MARKER,
            args={"channel": self.SECRET_CHANNEL_MARKER},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        assert res.decision == AutoApproveDecision.MATCHED
        assert res.matched_pattern_id == pid


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Audit forensique : ZÉRO PII / args values
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensicNoPII:
    SECRET_EMAIL = "SUPER_SECRET_MARKER_42@evil.example.com"
    SECRET_URL = "https://hidden-c2-marker.example.com/secret"
    SECRET_AMOUNT_MARKER = "987654321.99"

    def test_audit_reason_for_constraint_violation_contains_only_key_name(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={"to_allowlist": ["alice@x.com"]},
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"to": self.SECRET_EMAIL},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "SUPER_SECRET_MARKER_42" not in blob
        assert "evil.example.com" not in blob
        events = [
            e for e in _audit_lines(engine)
            if e.get("event") == "evaluation_constraints_violated"
        ]
        assert events
        assert events[-1]["reason"] == "constraint_violated:to_allowlist"

    def test_audit_url_value_never_leaks(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#x"],
                "url_allowlist": ["https://allowed.example.com/a"],
            },
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#x", "url": self.SECRET_URL},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "hidden-c2-marker" not in blob

    def test_audit_amount_value_never_leaks(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#fin"],
                "amount_max_eur": 100.0,
            },
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={
                "channel": "#fin",
                "amount_eur": float(self.SECRET_AMOUNT_MARKER),
            },
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "987654321" not in blob

    def test_audit_quota_exceeded_reason_no_args(self, engine):
        pid = _add_minimal_pattern(engine, quota_max_per_day=1)
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={"channel": "#general"},
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={
                "channel": "#general",
                "secret_payload_marker": "BURIED_SECRET_HERE",
            },
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "BURIED_SECRET_HERE" not in blob
        events = [
            e for e in _audit_lines(engine)
            if e.get("event") == "evaluation_quota_exceeded"
        ]
        assert events
        assert events[-1]["reason"] == "quota_exceeded"

    def test_audit_policy_mismatch_no_pii(self, engine):
        _add_minimal_pattern(
            engine,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={
                "channel": "#general",
                "evil_payload": "MARKER_SECRET_POLICY",
            },
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "MARKER_SECRET_POLICY" not in blob
        events = [
            e for e in _audit_lines(engine)
            if e.get("event") == "evaluation_policy_mismatch"
        ]
        assert events
        assert events[-1]["reason"] == "policy_mismatch"

    def test_audit_caller_not_allowed_no_pii(self, engine):
        _add_minimal_pattern(engine, caller_kinds_allowed=["react"])
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={
                "channel": "#general",
                "leak": "MARKER_SECRET_CALLER",
            },
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="autonomy",
        )
        blob = _audit_blob(engine)
        assert "MARKER_SECRET_CALLER" not in blob

    def test_audit_full_forensic_scan_no_args_values_ever(self, engine):
        """Test combiné : plusieurs evaluations avec différents markers
        secrets dans args ne doivent JAMAIS apparaître dans audit.jsonl."""
        _add_minimal_pattern(
            engine,
            tool_name_pattern="mcp__slack__send_message",
            args_constraints={
                "channel_allowlist": ["#general"],
                "subject_max_chars": 5,
            },
        )
        markers = [
            "FORENSIC_MARKER_AAA",
            "FORENSIC_MARKER_BBB",
            "FORENSIC_MARKER_CCC",
            "FORENSIC_MARKER_DDD",
        ]
        # Multi-evaluations avec violations variées
        for i, m in enumerate(markers):
            engine.evaluate(
                profile="alice",
                tool_name="mcp__slack__send_message",
                args={
                    "channel": f"#{m}",
                    "subject": f"long_subject_{m}",
                    "to": f"{m}@x.com",
                    "url": f"https://{m}.example.com",
                },
                policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
                caller_kind="react",
            )
        blob = _audit_blob(engine)
        for m in markers:
            assert m not in blob, f"marker {m} leaked in audit"

    def test_audit_matched_event_does_not_log_args_values(self, engine):
        _add_minimal_pattern(
            engine,
            args_constraints={
                "channel_allowlist": ["#general"],
                "to_allowlist": ["alice@x.com"],
            },
        )
        engine.evaluate(
            profile="alice",
            tool_name="mcp__slack__send_message",
            args={
                "channel": "#general",
                "to": "alice@x.com",
                "body": "MATCHED_BODY_MARKER_999",
            },
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind="react",
        )
        blob = _audit_blob(engine)
        assert "MATCHED_BODY_MARKER_999" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Audit pattern lifecycle (add/remove)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditLifecycle:
    def test_pattern_added_event_logged(self, engine):
        pid = _add_minimal_pattern(engine)
        events = [e for e in _audit_lines(engine) if e.get("event") == "pattern_added"]
        assert events
        assert events[-1]["pattern_id"] == pid

    def test_pattern_removed_event_logged(self, engine):
        pid = _add_minimal_pattern(engine)
        engine.remove_pattern(pid)
        events = [e for e in _audit_lines(engine) if e.get("event") == "pattern_removed"]
        assert events
        assert events[-1]["pattern_id"] == pid
