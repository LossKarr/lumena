"""
Tests Phase 13 v2 — MCPOrchestrator.

Sections :
  1. Init & dependency wiring
  2. Validation context (+ confused deputy server_id/tool_name)
  3. Policy resolution
  4. Table policy Phase 13 (BLOCKED / APPROVED / PENDING / UNMAPPED)
  5. Runtime health gate
  6. Auto-approve facade
  7. ApprovalQueue.propose() facade
  8. Audit forensique no-PII
  9. Hiérarchie / priorité décision
  10. Sanity end-to-end
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.auto_approve import (
    AutoApproveDecision,
    AutoApproveEngine,
    AutoApproveEvaluation,
)
from src.mcp.orchestrator import (
    MCPCallContext,
    MCPCallDecision,
    MCPDecision,
    MCPOrchestrator,
    MCPOrchestratorError,
    PolicyResolver,
    _POLICY_DECISION_PHASE13,
)
from src.mcp.policy import MCPPolicy
from src.mcp.runtime_watcher import RuntimeHealth, RuntimeWatcher


# ──────────────────────────────────────────────────────────────────────────────
# Fakes / fixtures
# ──────────────────────────────────────────────────────────────────────────────


class _InMemorySecretsService:
    def __init__(self):
        self._store: Dict[str, str] = {}

    def get(self, scope: str, name: str) -> Optional[str]:
        return self._store.get(f"{scope}::{name}")

    def set(self, scope: str, name: str, value: str) -> None:
        self._store[f"{scope}::{name}"] = value


class _StubPolicyResolver:
    """Resolver paramétrable : map (server_id, tool_name) → policy ou None."""

    def __init__(self, default_policy: Optional[MCPPolicy] = None):
        self.default_policy = default_policy
        self.calls: List[tuple] = []
        self.raises = False
        self.return_non_policy = False

    def resolve(self, server_id: str, tool_name: str) -> Optional[MCPPolicy]:
        self.calls.append((server_id, tool_name))
        if self.raises:
            raise RuntimeError("resolver boom")
        if self.return_non_policy:
            return "not_a_policy"  # type: ignore[return-value]
        return self.default_policy


class _FakeApprovalQueue:
    """Mock minimal exposant propose()."""

    def __init__(self, ticket_id_to_return: Optional[str] = None):
        self.calls: List[Dict[str, Any]] = []
        self.raises = False
        if ticket_id_to_return is None:
            ticket_id_to_return = uuid.uuid4().hex
        self.ticket_id_to_return = ticket_id_to_return
        self.return_empty = False

    def propose(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        if self.raises:
            raise RuntimeError("propose boom")
        if self.return_empty:
            return ""
        return self.ticket_id_to_return


class _FakeAutoApproveEngine:
    """Fake engine retournant une AutoApproveEvaluation fixe."""

    def __init__(self, evaluation: AutoApproveEvaluation):
        self.evaluation = evaluation
        self.calls: List[Dict[str, Any]] = []
        self.raises = False

    def evaluate(self, **kwargs) -> AutoApproveEvaluation:
        self.calls.append(dict(kwargs))
        if self.raises:
            raise RuntimeError("auto-approve boom")
        return self.evaluation


def _no_match_eval() -> AutoApproveEvaluation:
    return AutoApproveEvaluation(decision=AutoApproveDecision.NO_MATCH)


def _matched_eval(pattern_id: str = None) -> AutoApproveEvaluation:
    return AutoApproveEvaluation(
        decision=AutoApproveDecision.MATCHED,
        matched_pattern_id=pattern_id or uuid.uuid4().hex,
        quota_consumed=True,
    )


def _integrity_invalid_eval() -> AutoApproveEvaluation:
    return AutoApproveEvaluation(
        decision=AutoApproveDecision.INTEGRITY_INVALID,
        reason="integrity_invalid",
    )


@pytest.fixture
def watcher(tmp_path: Path) -> RuntimeWatcher:
    return RuntimeWatcher(
        snapshots_dir=tmp_path / "watcher" / "snaps",
        audit_log_path=tmp_path / "watcher" / "audit.jsonl",
    )


def _build_orchestrator(
    tmp_path: Path,
    *,
    policy: Optional[MCPPolicy] = MCPPolicy.READ_ONLY,
    auto_approve_eval: Optional[AutoApproveEvaluation] = None,
    watcher: Optional[RuntimeWatcher] = None,
    block_unhealthy_runtime: bool = True,
):
    """Helper qui construit un orchestrator avec fakes injectés."""
    resolver = _StubPolicyResolver(default_policy=policy)
    engine = _FakeAutoApproveEngine(
        evaluation=auto_approve_eval or _no_match_eval()
    )
    aq = _FakeApprovalQueue()
    w = watcher or RuntimeWatcher(
        snapshots_dir=tmp_path / "watcher" / "snaps",
        audit_log_path=tmp_path / "watcher" / "audit.jsonl",
    )
    orch = MCPOrchestrator(
        policy_resolver=resolver,
        auto_approve_engine=engine,
        approval_queue=aq,
        runtime_watcher=w,
        audit_log_path=tmp_path / "orch" / "audit.jsonl",
        block_unhealthy_runtime=block_unhealthy_runtime,
    )
    return orch, resolver, engine, aq, w


def _ctx(
    *,
    server_id: str = "alice",
    tool_name: str = "mcp__alice__read_doc",
    args: Optional[Dict[str, Any]] = None,
    profile: str = "alice",
    caller_kind: str = "react",
) -> MCPCallContext:
    return MCPCallContext(
        server_id=server_id,
        tool_name=tool_name,
        args=args if args is not None else {"key": "value"},
        profile=profile,
        caller_kind=caller_kind,
    )


def _audit_lines(orch: MCPOrchestrator) -> List[Dict[str, Any]]:
    if not orch.audit_log_path.exists():
        return []
    out = []
    with open(orch.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(orch: MCPOrchestrator) -> str:
    if not orch.audit_log_path.exists():
        return ""
    return orch.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & dependency wiring
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_init_creates_audit_dir(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        assert orch.audit_log_path.parent.exists()

    def test_init_block_unhealthy_default_true(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        assert orch.block_unhealthy_runtime is True

    def test_init_block_unhealthy_false_respected(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, block_unhealthy_runtime=False)
        assert orch.block_unhealthy_runtime is False

    def test_init_rejects_missing_dependencies(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        eng = _FakeAutoApproveEngine(evaluation=_no_match_eval())
        aq = _FakeApprovalQueue()
        # policy_resolver None
        with pytest.raises(MCPOrchestratorError, match="policy_resolver"):
            MCPOrchestrator(
                policy_resolver=None,  # type: ignore[arg-type]
                auto_approve_engine=eng,
                approval_queue=aq,
                runtime_watcher=w,
                audit_log_path=tmp_path / "orch" / "a.jsonl",
            )
        # policy_resolver sans resolve()
        class NoResolve: pass
        with pytest.raises(MCPOrchestratorError, match="policy_resolver"):
            MCPOrchestrator(
                policy_resolver=NoResolve(),  # type: ignore[arg-type]
                auto_approve_engine=eng,
                approval_queue=aq,
                runtime_watcher=w,
                audit_log_path=tmp_path / "orch" / "a.jsonl",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Validation context
# ══════════════════════════════════════════════════════════════════════════════


class TestContextValidation:
    def test_profile_empty_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(profile=""))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:profile"

    def test_profile_uppercase_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(profile="ALICE"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:profile"

    def test_server_id_invalid_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(server_id="ALICE"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:server_id"

    def test_server_id_windows_reserved_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(server_id="con", tool_name="mcp__con__tool"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:server_id"

    def test_server_id_path_traversal_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        # "foo..bar" passe la regex mais doit être bloqué par check ..
        d = orch.decide(_ctx(server_id="foo..bar"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT

    def test_tool_name_bad_format_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(tool_name="not_mcp_format"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:tool_name"

    def test_caller_kind_unknown_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(caller_kind="root"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:caller_kind"

    def test_args_not_dict_refused(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        ctx = MCPCallContext(
            server_id="alice",
            tool_name="mcp__alice__tool",
            args="not a dict",  # type: ignore[arg-type]
            profile="alice",
            caller_kind="react",
        )
        d = orch.decide(ctx)
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:args"

    def test_tool_server_mismatch_refused(self, tmp_path):
        """Confused deputy : server_id="alice" mais tool_name cible bob."""
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__bob__exec",
        ))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:tool_server_mismatch"

    def test_tool_server_mismatch_audit_no_pii(self, tmp_path):
        """Forensique : args contient secret marker → audit ne contient pas
        la valeur et le reason est uniquement le code court."""
        orch, *_ = _build_orchestrator(tmp_path)
        orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__bob__exec",
            args={"secret_marker": "DEEP_FORENSIC_SECRET_123",
                  "url": "https://evil-marker.example.com"},
        ))
        blob = _audit_blob(orch)
        assert "DEEP_FORENSIC_SECRET_123" not in blob
        assert "evil-marker" not in blob
        events = [
            e for e in _audit_lines(orch)
            if e["event"] == "context_refused"
        ]
        assert events
        assert events[-1]["reason"] == "context_invalid:tool_server_mismatch"

    def test_valid_context_proceeds(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        d = orch.decide(_ctx())
        assert d.decision != MCPDecision.REFUSED_CONTEXT

    def test_audit_context_refused_no_args_values(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path)
        orch.decide(_ctx(profile="", args={"leak": "PROFILE_FAIL_MARKER"}))
        blob = _audit_blob(orch)
        assert "PROFILE_FAIL_MARKER" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Policy resolution
# ══════════════════════════════════════════════════════════════════════════════


class TestPolicyResolution:
    def test_policy_none_returns_refused_unknown(self, tmp_path):
        orch, resolver, *_ = _build_orchestrator(tmp_path, policy=None)
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "policy_unresolved"

    def test_resolver_raises_returns_refused_unknown(self, tmp_path):
        orch, resolver, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        resolver.raises = True
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "policy_unresolved"

    def test_resolver_returns_non_policy(self, tmp_path):
        orch, resolver, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        resolver.return_non_policy = True
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "policy_unresolved"

    def test_resolver_called_with_server_id_and_tool_name(self, tmp_path):
        orch, resolver, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        orch.decide(_ctx(server_id="alice", tool_name="mcp__alice__read"))
        assert resolver.calls == [("alice", "mcp__alice__read")]

    def test_audit_policy_unresolved_no_pii(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=None)
        orch.decide(_ctx(args={"leak": "UNRESOLVED_MARKER"}))
        blob = _audit_blob(orch)
        assert "UNRESOLVED_MARKER" not in blob

    def test_valid_policy_continues_flow(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY
        assert d.policy == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Table policy Phase 13
# ══════════════════════════════════════════════════════════════════════════════


class TestPolicyTable:
    def test_policy_table_read_only_to_approved_policy(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY
        assert d.reason == f"policy_allowed:{MCPPolicy.READ_ONLY.value}"

    def test_policy_table_external_read_to_approved_policy(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.EXTERNAL_READ)
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_policy_table_local_write_to_pending(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.LOCAL_WRITE)
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.PENDING_APPROVAL

    def test_policy_table_external_write_recoverable_to_pending(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__send"))
        assert d.decision == MCPDecision.PENDING_APPROVAL

    def test_policy_table_external_write_irreversible_to_blocked(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__delete"))
        assert d.decision == MCPDecision.BLOCKED_POLICY
        assert d.reason == f"policy_blocked:{MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE.value}"

    def test_policy_table_secrets_auth_to_blocked(self, tmp_path):
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.SECRETS_AUTH)
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.BLOCKED_POLICY
        assert d.reason == f"policy_blocked:{MCPPolicy.SECRETS_AUTH.value}"

    def test_policy_table_completeness(self):
        """Toutes les MCPPolicy doivent être présentes dans la table."""
        for pol in MCPPolicy:
            assert pol in _POLICY_DECISION_PHASE13, f"{pol} missing from table"

    def test_unmapped_policy_returns_refused_unknown(self, tmp_path, monkeypatch):
        """Si une policy n'est pas mappée, retourner REFUSED_UNKNOWN
        avec reason='policy_unmapped'. Simulé via monkeypatch."""
        orch, *_ = _build_orchestrator(tmp_path, policy=MCPPolicy.READ_ONLY)
        # Monkeypatch la table : retire READ_ONLY
        from src.mcp import orchestrator as _orch_module
        patched_table = dict(_orch_module._POLICY_DECISION_PHASE13)
        del patched_table[MCPPolicy.READ_ONLY]
        monkeypatch.setattr(
            _orch_module, "_POLICY_DECISION_PHASE13", patched_table
        )
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "policy_unmapped"
        assert d.policy == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Runtime health gate
# ══════════════════════════════════════════════════════════════════════════════


class _DummyRunner:
    def __init__(self, state_value: str = "running"):
        self._state = state_value

    def state(self) -> str:
        return self._state


class _RaisingRunner:
    def state(self):
        raise RuntimeError("boom")


class TestRuntimeHealthGate:
    def test_crash_loop_blocks_runtime(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
            crash_loop_threshold=2,
        )
        w.register_runner("alice", _DummyRunner("running"))
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME
        assert d.runtime_health == RuntimeHealth.CRASH_LOOP
        assert d.reason == "runtime_health:crash_loop"

    def test_unhealthy_blocks_runtime(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME
        assert d.runtime_health == RuntimeHealth.UNHEALTHY

    def test_degraded_does_not_block(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("running"))
        w.record_event("alice", "started")
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        w.record_event("alice", "restarted")  # state running, 1 crash récent
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.READ_ONLY,
            watcher=w,
        )
        d = orch.decide(_ctx())
        # DEGRADED ne bloque pas → on continue jusqu'à APPROVED_POLICY
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_unknown_does_not_block(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _RaisingRunner())
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.READ_ONLY,
            watcher=w,
        )
        d = orch.decide(_ctx())
        # UNKNOWN ne bloque pas explicitement → on continue
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_server_not_registered_does_not_block(self, tmp_path):
        """Watcher sans runner enregistré → on continue (non autoritaire)."""
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.READ_ONLY,
        )
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_block_unhealthy_false_disables_gate(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.READ_ONLY,
            watcher=w,
            block_unhealthy_runtime=False,
        )
        d = orch.decide(_ctx())
        # Gate désactivé → on continue jusqu'à APPROVED_POLICY
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_audit_runtime_blocked_no_pii(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__write",
            args={"hidden": "RUNTIME_BLOCK_MARKER"}
        ))
        blob = _audit_blob(orch)
        assert "RUNTIME_BLOCK_MARKER" not in blob
        events = [e for e in _audit_lines(orch) if e["event"] == "runtime_blocked"]
        assert events
        assert events[-1]["health"] == "unhealthy"

    def test_runtime_gate_blocks_before_auto_approve(self, tmp_path):
        """Runtime gate court-circuite : auto_approve.evaluate non appelé."""
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, _, engine, _, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
            auto_approve_eval=_matched_eval(),
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME
        # engine.evaluate NE DOIT PAS avoir été appelé
        assert engine.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Auto-approve facade
# ══════════════════════════════════════════════════════════════════════════════


class TestAutoApproveFacade:
    def test_matched_returns_approved_auto(self, tmp_path):
        pid = uuid.uuid4().hex
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_matched_eval(pattern_id=pid),
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.APPROVED_AUTO
        assert d.auto_approve_pattern_id == pid
        assert d.reason == "auto_matched"

    def test_integrity_invalid_returns_blocked_integrity(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_integrity_invalid_eval(),
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_INTEGRITY
        assert d.reason == "auto_integrity_invalid"

    def test_no_match_with_read_only_falls_to_approved_policy(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.READ_ONLY,
            auto_approve_eval=_no_match_eval(),
        )
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_no_match_with_write_falls_to_pending(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            auto_approve_eval=_no_match_eval(),
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__send"))
        assert d.decision == MCPDecision.PENDING_APPROVAL

    def test_constraints_violated_with_read_only_falls_to_approved_policy(
        self, tmp_path
    ):
        ev = AutoApproveEvaluation(
            decision=AutoApproveDecision.CONSTRAINTS_VIOLATED,
            reason="constraint_violated:to_allowlist",
        )
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY, auto_approve_eval=ev
        )
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_quota_exceeded_with_write_falls_to_pending(self, tmp_path):
        ev = AutoApproveEvaluation(
            decision=AutoApproveDecision.QUOTA_EXCEEDED,
            reason="quota_exceeded",
        )
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            auto_approve_eval=ev,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__send"))
        assert d.decision == MCPDecision.PENDING_APPROVAL

    def test_auto_approve_engine_raises_returns_refused_unknown(self, tmp_path):
        orch, _, engine, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        engine.raises = True
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "auto_approve_error"

    def test_auto_approve_called_with_correct_kwargs(self, tmp_path):
        orch, _, engine, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY
        )
        orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__alice__read",
            args={"key": "v"},
            profile="alice",
            caller_kind="react",
        ))
        assert len(engine.calls) == 1
        call = engine.calls[0]
        assert call["profile"] == "alice"
        assert call["tool_name"] == "mcp__alice__read"
        assert call["args"] == {"key": "v"}
        assert call["policy"] == MCPPolicy.READ_ONLY
        assert call["caller_kind"] == "react"

    def test_audit_auto_matched_no_pii(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_matched_eval(),
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__write",
            args={"leak": "AUTO_MATCHED_LEAK_MARKER"},
        ))
        blob = _audit_blob(orch)
        assert "AUTO_MATCHED_LEAK_MARKER" not in blob

    def test_audit_integrity_blocked_no_pii(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_integrity_invalid_eval(),
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__write",
            args={"leak": "INTEGRITY_LEAK_MARKER"},
        ))
        blob = _audit_blob(orch)
        assert "INTEGRITY_LEAK_MARKER" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — ApprovalQueue.propose() facade
# ══════════════════════════════════════════════════════════════════════════════


class TestProposeFacade:
    def test_propose_called_with_correct_kwargs(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__write",
            args={"channel": "#x"},
            caller_kind="react",
        ))
        assert len(aq.calls) == 1
        call = aq.calls[0]
        assert call["tool_name"] == "mcp__alice__write"
        assert call["args"] == {"channel": "#x"}
        assert call["policy"] == MCPPolicy.LOCAL_WRITE
        assert call["caller_kind"] == "react"
        assert call["risk_summary"] == "mcp_pending_approval:local_write"

    def test_propose_risk_summary_format_external_write(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        )
        orch.decide(_ctx(tool_name="mcp__alice__send"))
        assert aq.calls[-1]["risk_summary"] == \
            "mcp_pending_approval:external_write_recoverable"

    def test_propose_does_not_receive_profile_or_server_id(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(tool_name="mcp__alice__write"))
        call = aq.calls[-1]
        assert "profile" not in call
        assert "server_id" not in call

    def test_propose_ticket_id_returned_in_decision(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        aq.ticket_id_to_return = "abc123def456"
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.PENDING_APPROVAL
        assert d.approval_ticket_id == "abc123def456"

    def test_propose_raises_returns_refused_unknown(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        aq.raises = True
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "propose_failed"

    def test_propose_empty_ticket_id_returns_refused_unknown(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        aq.return_empty = True
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.reason == "propose_failed"

    def test_propose_not_called_when_approved_policy(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY
        )
        orch.decide(_ctx())
        assert aq.calls == []

    def test_propose_not_called_when_auto_matched(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_matched_eval(),
        )
        orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert aq.calls == []

    def test_propose_not_called_when_blocked_policy(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE
        )
        orch.decide(_ctx(tool_name="mcp__alice__delete"))
        assert aq.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensicNoPII:
    SECRET_MARKERS = [
        "FORENSIC_TOP_SECRET_AAA",
        "https://secret-c2-marker.example.com/y",
        "credit_card_4242424242424242",
        "MARKER_PASSWORD_ROOT",
        "OAUTH_TOKEN_FORENSIC_999",
    ]

    def test_audit_never_contains_args_values_multi_scenario(self, tmp_path):
        scenarios = [
            (MCPPolicy.READ_ONLY, _no_match_eval()),
            (MCPPolicy.LOCAL_WRITE, _no_match_eval()),
            (MCPPolicy.LOCAL_WRITE, _matched_eval()),
            (MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE, _no_match_eval()),
            (MCPPolicy.SECRETS_AUTH, _no_match_eval()),
            (MCPPolicy.LOCAL_WRITE, _integrity_invalid_eval()),
        ]
        for i, (pol, ev) in enumerate(scenarios):
            orch, *_ = _build_orchestrator(
                tmp_path / f"orch_{i}",
                policy=pol,
                auto_approve_eval=ev,
            )
            marker = self.SECRET_MARKERS[i % len(self.SECRET_MARKERS)]
            tool_suffix = "write" if pol != MCPPolicy.READ_ONLY else "read"
            orch.decide(_ctx(
                tool_name=f"mcp__alice__{tool_suffix}",
                args={
                    "secret": marker,
                    "url": f"https://{marker}.example.com",
                    "leak": f"value_{marker}",
                },
            ))
            blob = _audit_blob(orch)
            assert marker not in blob, f"marker {marker} leaked in scenario {i}"

    def test_audit_does_not_stringify_dependencies(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(tool_name="mcp__alice__write"))
        blob = _audit_blob(orch)
        assert "_FakeApprovalQueue" not in blob
        assert "_FakeAutoApproveEngine" not in blob
        assert "_StubPolicyResolver" not in blob
        assert "RuntimeWatcher" not in blob
        assert "object at 0x" not in blob

    def test_audit_identifiers_present_for_context(self, tmp_path):
        """Identifiants de contexte autorisés : server_id, tool_name,
        profile, caller_kind. À vérifier présents pour traçabilité."""
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__alice__write",
            profile="alice",
            caller_kind="react",
        ))
        events = [e for e in _audit_lines(orch) if e["event"] == "pending_enqueued"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["tool_name"] == "mcp__alice__write"
        assert ev["profile"] == "alice"
        assert ev["caller_kind"] == "react"

    def test_audit_risk_summary_is_code_short(self, tmp_path):
        """risk_summary doit être un code court mcp_pending_approval:<policy>,
        jamais dynamique selon args."""
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__write",
            args={"big_payload": "X" * 1000 + "PAYLOAD_MARKER"},
        ))
        # Risk summary envoyé à propose() doit être uniquement code court
        risk = aq.calls[-1]["risk_summary"]
        assert risk == "mcp_pending_approval:local_write"
        assert "PAYLOAD_MARKER" not in risk
        # Audit ne contient pas non plus le payload
        blob = _audit_blob(orch)
        assert "PAYLOAD_MARKER" not in blob

    def test_audit_decide_called_event_format(self, tmp_path):
        """Pas obligatoire d'avoir un event 'decide_called' — l'audit doit
        avoir AU MOINS un event décrivant la décision (policy_allowed,
        pending_enqueued, etc.)."""
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY
        )
        orch.decide(_ctx())
        events = _audit_lines(orch)
        assert events
        last = events[-1]
        assert "reason" in last
        assert "policy" in last

    def test_audit_no_args_values_for_blocked_policy(self, tmp_path):
        orch, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE
        )
        orch.decide(_ctx(
            tool_name="mcp__alice__delete",
            args={"target": "BLOCKED_POLICY_TARGET_MARKER"},
        ))
        blob = _audit_blob(orch)
        assert "BLOCKED_POLICY_TARGET_MARKER" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Hiérarchie / priorité décision
# ══════════════════════════════════════════════════════════════════════════════


class TestDecisionHierarchy:
    def test_refused_context_short_circuits_everything(self, tmp_path):
        """Si validation échoue : aucun appel resolver / engine / aq."""
        orch, resolver, engine, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.LOCAL_WRITE
        )
        orch.decide(_ctx(profile=""))
        assert resolver.calls == []
        assert engine.calls == []
        assert aq.calls == []

    def test_blocked_policy_short_circuits_runtime_auto_propose(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, _, engine, aq, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__delete"))
        assert d.decision == MCPDecision.BLOCKED_POLICY
        # auto_approve et propose non appelés
        assert engine.calls == []
        assert aq.calls == []

    def test_blocked_runtime_short_circuits_auto_propose(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, _, engine, aq, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME
        assert engine.calls == []
        assert aq.calls == []

    def test_blocked_integrity_short_circuits_propose(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_integrity_invalid_eval(),
        )
        orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert aq.calls == []

    def test_approved_auto_short_circuits_propose(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            auto_approve_eval=_matched_eval(),
        )
        orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert aq.calls == []

    def test_approved_policy_short_circuits_propose(self, tmp_path):
        orch, _, _, aq, _ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY
        )
        orch.decide(_ctx())
        assert aq.calls == []

    def test_order_validate_before_resolve(self, tmp_path):
        """Validation context doit court-circuiter resolver."""
        orch, resolver, *_ = _build_orchestrator(
            tmp_path, policy=MCPPolicy.READ_ONLY
        )
        orch.decide(_ctx(profile=""))
        assert resolver.calls == []

    def test_order_resolve_before_runtime(self, tmp_path):
        """Si policy non résoluble, runtime gate non appelé."""
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, resolver, *_ = _build_orchestrator(
            tmp_path, policy=None, watcher=w,
        )
        d = orch.decide(_ctx())
        assert d.decision == MCPDecision.REFUSED_UNKNOWN
        assert d.runtime_health is None

    def test_order_policy_block_before_runtime(self, tmp_path):
        """Policy block check avant runtime gate."""
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, *_ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__delete"))
        # BLOCKED_POLICY, pas BLOCKED_RUNTIME
        assert d.decision == MCPDecision.BLOCKED_POLICY
        assert d.runtime_health is None

    def test_order_runtime_before_integrity(self, tmp_path):
        """Runtime gate avant integrity check (engine non appelé)."""
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        w.register_runner("alice", _DummyRunner("crashed"))
        orch, _, engine, _, _ = _build_orchestrator(
            tmp_path,
            policy=MCPPolicy.LOCAL_WRITE,
            watcher=w,
            auto_approve_eval=_integrity_invalid_eval(),
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME
        assert engine.calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Sanity end-to-end
# ══════════════════════════════════════════════════════════════════════════════


class TestSanityEndToEnd:
    """Intégration avec vrai AutoApproveEngine et vrai ApprovalQueue.
    PAS de câblage runtime (tool_registry / MCPClient non utilisés)."""

    @pytest.fixture
    def real_engine(self, tmp_path):
        secrets = _InMemorySecretsService()
        return AutoApproveEngine(
            patterns_dir=tmp_path / "ae" / "patterns",
            audit_log_path=tmp_path / "ae" / "audit.jsonl",
            quotas_dir=tmp_path / "ae" / "quotas",
            secrets_service=secrets,
        )

    @pytest.fixture
    def real_aq(self, tmp_path):
        from src.mcp.approval_queue import ApprovalQueue
        secrets = _InMemorySecretsService()
        return ApprovalQueue(
            queue_dir=tmp_path / "aq",
            secrets_service=secrets,
        )

    def _orch_real(self, tmp_path, policy, engine, aq, watcher=None):
        resolver = _StubPolicyResolver(default_policy=policy)
        w = watcher or RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
        )
        return MCPOrchestrator(
            policy_resolver=resolver,
            auto_approve_engine=engine,
            approval_queue=aq,
            runtime_watcher=w,
            audit_log_path=tmp_path / "orch" / "a.jsonl",
        )

    def test_read_only_happy_path(self, tmp_path, real_engine, real_aq):
        orch = self._orch_real(
            tmp_path, MCPPolicy.READ_ONLY, real_engine, real_aq
        )
        d = orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__alice__read_doc",
        ))
        assert d.decision == MCPDecision.APPROVED_POLICY

    def test_auto_approve_match_consumes_quota(self, tmp_path, real_engine, real_aq):
        pid = real_engine.add_pattern(
            profile="alice",
            kind="slack",
            tool_name_pattern="mcp__alice__send",
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kinds_allowed=["react"],
            args_constraints={"channel_allowlist": ["#general"]},
            quota_max_per_day=5,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
        )
        orch = self._orch_real(
            tmp_path, MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            real_engine, real_aq,
        )
        d = orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__alice__send",
            args={"channel": "#general"},
            profile="alice",
        ))
        assert d.decision == MCPDecision.APPROVED_AUTO
        assert d.auto_approve_pattern_id == pid
        assert real_engine.get_quota_consumed(pid) == 1

    def test_external_write_no_match_creates_real_pending(
        self, tmp_path, real_engine, real_aq
    ):
        orch = self._orch_real(
            tmp_path, MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            real_engine, real_aq,
        )
        d = orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__alice__send",
            args={"channel": "#general"},
        ))
        assert d.decision == MCPDecision.PENDING_APPROVAL
        assert d.approval_ticket_id is not None
        # Le ticket existe réellement dans ApprovalQueue
        # (on ne lit pas son contenu ici pour éviter de coupler aux internals)

    def test_irreversible_blocked_policy(self, tmp_path, real_engine, real_aq):
        orch = self._orch_real(
            tmp_path, MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            real_engine, real_aq,
        )
        d = orch.decide(_ctx(
            tool_name="mcp__alice__delete",
        ))
        assert d.decision == MCPDecision.BLOCKED_POLICY

    def test_runtime_crash_loop_blocks_write(self, tmp_path, real_engine, real_aq):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "w" / "s",
            audit_log_path=tmp_path / "w" / "a.jsonl",
            crash_loop_threshold=2,
        )
        w.register_runner("alice", _DummyRunner("running"))
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        orch = self._orch_real(
            tmp_path, MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            real_engine, real_aq, watcher=w,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__send"))
        assert d.decision == MCPDecision.BLOCKED_RUNTIME

    def test_context_invalid_profile_refused(self, tmp_path, real_engine, real_aq):
        orch = self._orch_real(
            tmp_path, MCPPolicy.READ_ONLY, real_engine, real_aq,
        )
        d = orch.decide(_ctx(profile="!!INVALID!!"))
        assert d.decision == MCPDecision.REFUSED_CONTEXT

    def test_tool_server_mismatch_refused(self, tmp_path, real_engine, real_aq):
        orch = self._orch_real(
            tmp_path, MCPPolicy.READ_ONLY, real_engine, real_aq,
        )
        d = orch.decide(_ctx(
            server_id="alice",
            tool_name="mcp__bob__read",
        ))
        assert d.decision == MCPDecision.REFUSED_CONTEXT
        assert d.reason == "context_invalid:tool_server_mismatch"

    def test_multi_decisions_forensic_no_leak(self, tmp_path, real_engine, real_aq):
        """10 décisions de suite avec markers secrets variés → aucune fuite."""
        orch = self._orch_real(
            tmp_path, MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            real_engine, real_aq,
        )
        markers = [f"E2E_FORENSIC_MARKER_{i}" for i in range(10)]
        for m in markers:
            orch.decide(_ctx(
                server_id="alice",
                tool_name="mcp__alice__send",
                args={"channel": "#general", "leak": m},
            ))
        blob = _audit_blob(orch)
        for m in markers:
            assert m not in blob, f"E2E marker {m} leaked"

    def test_secrets_auth_blocked(self, tmp_path, real_engine, real_aq):
        orch = self._orch_real(
            tmp_path, MCPPolicy.SECRETS_AUTH, real_engine, real_aq,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__login"))
        assert d.decision == MCPDecision.BLOCKED_POLICY

    def test_local_write_no_match_creates_pending(
        self, tmp_path, real_engine, real_aq
    ):
        orch = self._orch_real(
            tmp_path, MCPPolicy.LOCAL_WRITE, real_engine, real_aq,
        )
        d = orch.decide(_ctx(tool_name="mcp__alice__write_file"))
        assert d.decision == MCPDecision.PENDING_APPROVAL
        assert d.approval_ticket_id is not None
