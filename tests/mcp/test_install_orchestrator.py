"""
Tests Phase 18 v3 — MCPInstallOrchestrator.

Sections :
  1. Init & configuration
  2. Kill switch env
  3. propose_install happy path
  4. propose_install refus
  5. ApprovalQueue API correcte (proposition)
  6. execute_approved_install ApprovalResult validation
  7. Catalog re-check après approval
  8. MCPSandboxRunner integration (réutilisation Phase 5)
  9. Catalog status transitions
  10. LOCAL transport (execute via generated uv package)
  11. dry_run (success=False, dry_run=True)
  12. Audit forensique no-PII
  13. Sanity end-to-end (mocks)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from src.mcp.approval_queue import (
    ApprovalDecision,
    ApprovalQueue,
    ApprovalResult,
)
from src.mcp.install_orchestrator import (
    InstallError,
    InstallProposal,
    InstallResult,
    InstallTransport,
    MCPInstallOrchestrator,
    _parse_package_spec,
)
from src.mcp.policy import MCPPolicy
from src.mcp.sandbox_runner import (
    MCPInstallSpec,
    MCPSandboxError,
    MCPSandboxRunner,
)
from src.mcp.server_catalog import (
    MCPServerCatalog,
    ServerStatus,
)


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


class _FakeApprovalQueue:
    """Mock minimal exposant propose() + propose() trace + interdit
    .approve()."""

    def __init__(self, ticket_id_to_return: Optional[str] = None):
        self.propose_calls: List[Dict[str, Any]] = []
        if ticket_id_to_return is None:
            ticket_id_to_return = "a" * 32  # placeholder UUID-like
        self.ticket_id_to_return = ticket_id_to_return
        self.approve_called = False
        self.reject_called = False

    def propose(self, **kwargs) -> str:
        self.propose_calls.append(dict(kwargs))
        return self.ticket_id_to_return

    def approve(self, action_id: str):
        self.approve_called = True
        raise AssertionError(
            "ApprovalQueue.approve() MUST NEVER be called by the orchestrator"
        )

    def reject(self, action_id: str, reason: str) -> bool:
        self.reject_called = True
        raise AssertionError(
            "ApprovalQueue.reject() MUST NEVER be called by the orchestrator"
        )


@pytest.fixture
def catalog(tmp_path: Path) -> MCPServerCatalog:
    return MCPServerCatalog(
        catalog_dir=tmp_path / "catalog",
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
        secrets_service=_InMemorySecretsService(),
    )


@pytest.fixture
def aq() -> _FakeApprovalQueue:
    return _FakeApprovalQueue()


@pytest.fixture
def orch(tmp_path: Path, catalog, aq) -> MCPInstallOrchestrator:
    """Orchestrator default : dry_run=True."""
    return MCPInstallOrchestrator(
        catalog=catalog,
        approval_queue=aq,
        install_root=tmp_path / "install_root",
        audit_log_path=tmp_path / "orch" / "audit.jsonl",
        dry_run=True,
    )


@pytest.fixture
def orch_live(tmp_path: Path, catalog, aq) -> MCPInstallOrchestrator:
    """Orchestrator avec dry_run=False (pour tests subprocess mockés)."""
    return MCPInstallOrchestrator(
        catalog=catalog,
        approval_queue=aq,
        install_root=tmp_path / "install_root",
        audit_log_path=tmp_path / "orch" / "audit.jsonl",
        dry_run=False,
    )


def _add_declared(
    catalog: MCPServerCatalog,
    server_id: str = "alice",
    package_spec: str = "npm:mcp-foo",
    version: Optional[str] = None,
    trust_score: Optional[int] = 80,
) -> None:
    catalog.add_server(
        server_id=server_id,
        display_name="Alice",
        package_spec=package_spec,
        owner_profile="alice",
        version=version,
        trust_score=trust_score,
    )


def _approval_result_for(
    server_id: str,
    package_spec: str = "npm:mcp-foo",
    version: Optional[str] = None,
    trust_score: int = 80,
    transport: str = "npm",
    package_name: str = "mcp-foo",
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
    args_override: Optional[Dict[str, Any]] = None,
) -> ApprovalResult:
    if args_override is not None:
        args = args_override
    else:
        args = {
            "server_id": server_id,
            "transport": transport,
            "package_name": package_name,
            "package_spec": package_spec,
            "version": version,
            "trust_score": trust_score,
        }
    return ApprovalResult(decision=decision, args=args)


def _audit_lines(orch: MCPInstallOrchestrator) -> List[Dict[str, Any]]:
    if not orch.audit_log_path.exists():
        return []
    out = []
    with open(orch.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(orch: MCPInstallOrchestrator) -> str:
    if not orch.audit_log_path.exists():
        return ""
    return orch.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_audit_dir_created(self, orch):
        assert orch.audit_log_path.parent.exists()

    def test_default_dry_run_true(self, tmp_path, catalog, aq):
        o = MCPInstallOrchestrator(
            catalog=catalog, approval_queue=aq,
            install_root=tmp_path / "ir",
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert o.dry_run is True

    def test_default_min_trust_70(self, orch):
        assert orch.min_trust_score_for_install == 70

    def test_default_env_disable_flag(self, orch):
        assert orch.env_disable_flag == "LUMENA_MCP_INSTALL_DISABLED"

    def test_init_rejects_none_catalog(self, tmp_path, aq):
        with pytest.raises(ValueError, match="catalog"):
            MCPInstallOrchestrator(
                catalog=None,  # type: ignore
                approval_queue=aq,
                install_root=tmp_path / "ir",
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_none_queue(self, tmp_path, catalog):
        with pytest.raises(ValueError, match="approval_queue"):
            MCPInstallOrchestrator(
                catalog=catalog, approval_queue=None,  # type: ignore
                install_root=tmp_path / "ir",
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_queue_without_propose(self, tmp_path, catalog):
        class NoPropose: pass
        with pytest.raises(ValueError, match="propose"):
            MCPInstallOrchestrator(
                catalog=catalog, approval_queue=NoPropose(),  # type: ignore
                install_root=tmp_path / "ir",
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_invalid_min_trust(self, tmp_path, catalog, aq):
        with pytest.raises(ValueError, match="\\[0,100\\]"):
            MCPInstallOrchestrator(
                catalog=catalog, approval_queue=aq,
                install_root=tmp_path / "ir",
                audit_log_path=tmp_path / "audit.jsonl",
                min_trust_score_for_install=101,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Kill switch env
# ══════════════════════════════════════════════════════════════════════════════


class TestKillSwitch:
    def test_kill_switch_blocks_propose(self, orch, catalog, monkeypatch):
        _add_declared(catalog, "alice")
        monkeypatch.setenv("LUMENA_MCP_INSTALL_DISABLED", "1")
        with pytest.raises(InstallError, match="install_disabled"):
            orch.propose_install("alice")

    def test_kill_switch_blocks_execute(self, orch, catalog, monkeypatch):
        _add_declared(catalog, "alice")
        monkeypatch.setenv("LUMENA_MCP_INSTALL_DISABLED", "true")
        with pytest.raises(InstallError, match="install_disabled"):
            orch.execute_approved_install(
                "alice", _approval_result_for("alice")
            )

    def test_kill_switch_absent_continues(self, orch, catalog, monkeypatch):
        _add_declared(catalog, "alice")
        monkeypatch.delenv("LUMENA_MCP_INSTALL_DISABLED", raising=False)
        proposal = orch.propose_install("alice")
        assert isinstance(proposal, InstallProposal)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — propose_install happy path
# ══════════════════════════════════════════════════════════════════════════════


class TestProposeHappyPath:
    def test_npm_simple(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        p = orch.propose_install("alice")
        assert p.server_id == "alice"
        assert p.transport == InstallTransport.NPM
        assert p.package_name == "mcp-foo"
        assert p.package_spec == "npm:mcp-foo"

    def test_npm_scoped(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:@anthropic/mcp-slack")
        p = orch.propose_install("alice")
        assert p.transport == InstallTransport.NPM
        assert p.package_name == "@anthropic/mcp-slack"

    def test_pypi(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="pypi:mcp-bar")
        p = orch.propose_install("alice")
        assert p.transport == InstallTransport.PYPI
        assert p.package_name == "mcp-bar"

    def test_local_proposal_accepted(self, orch, catalog):
        """local: accepté côté propose. Refus uniquement côté execute."""
        _add_declared(catalog, "alice", package_spec="local:my-server")
        p = orch.propose_install("alice")
        assert p.transport == InstallTransport.LOCAL
        assert p.package_name == "my-server"

    def test_proposal_contains_version(self, orch, catalog):
        _add_declared(
            catalog, "alice", package_spec="npm:mcp-foo",
            version="1.2.3",
        )
        p = orch.propose_install("alice")
        assert p.version == "1.2.3"

    def test_proposal_contains_trust_score(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=85)
        p = orch.propose_install("alice")
        assert p.trust_score == 85

    def test_proposal_contains_ticket_id(self, orch, catalog):
        _add_declared(catalog, "alice")
        p = orch.propose_install("alice")
        assert isinstance(p.approval_ticket_id, str)
        assert p.approval_ticket_id


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — propose_install refus
# ══════════════════════════════════════════════════════════════════════════════


class TestProposeRefus:
    def test_server_id_invalid(self, orch):
        with pytest.raises(InstallError, match="server_id_invalid"):
            orch.propose_install("ALICE")

    def test_server_unknown(self, orch):
        with pytest.raises(InstallError, match="server_unknown"):
            orch.propose_install("ghost")

    def test_status_installed_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        catalog.update_status("alice", ServerStatus.INSTALLED)
        with pytest.raises(InstallError, match="status_not_declared"):
            orch.propose_install("alice")

    def test_status_active_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        catalog.update_status("alice", ServerStatus.INSTALLED)
        catalog.update_status("alice", ServerStatus.ACTIVE)
        with pytest.raises(InstallError, match="status_not_declared"):
            orch.propose_install("alice")

    def test_status_quarantined_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        catalog.update_status("alice", ServerStatus.QUARANTINED)
        with pytest.raises(InstallError, match="status_not_declared"):
            orch.propose_install("alice")

    def test_status_removed_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        catalog.remove_server("alice")
        with pytest.raises(InstallError, match="status_not_declared"):
            orch.propose_install("alice")

    def test_trust_score_none(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=None)
        with pytest.raises(InstallError, match="trust_score_missing"):
            orch.propose_install("alice")

    def test_trust_score_below_threshold(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=50)
        with pytest.raises(InstallError, match="trust_too_low_for_install"):
            orch.propose_install("alice")


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — ApprovalQueue API correcte (proposition)
# ══════════════════════════════════════════════════════════════════════════════


class TestApprovalQueueProposeAPI:
    def test_propose_called_with_correct_kwargs(self, orch, catalog, aq):
        _add_declared(catalog, "alice", trust_score=80)
        orch.propose_install("alice")
        assert len(aq.propose_calls) == 1
        call = aq.propose_calls[0]
        assert call["tool_name"] == "mcp_install:alice"
        assert "args" in call
        assert call["policy"] == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        assert call["caller_kind"] == "silent"

    def test_policy_is_external_write_recoverable(self, orch, catalog, aq):
        _add_declared(catalog, "alice")
        orch.propose_install("alice")
        assert aq.propose_calls[-1]["policy"] == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_risk_summary_format(self, orch, catalog, aq):
        _add_declared(catalog, "alice", trust_score=85)
        orch.propose_install("alice")
        assert aq.propose_calls[-1]["risk_summary"] == "mcp_install:npm:85"

    def test_args_contains_all_fields(self, orch, catalog, aq):
        _add_declared(
            catalog, "alice", package_spec="npm:mcp-foo",
            version="1.0", trust_score=80,
        )
        orch.propose_install("alice")
        args = aq.propose_calls[-1]["args"]
        assert args["server_id"] == "alice"
        assert args["transport"] == "npm"
        assert args["package_name"] == "mcp-foo"
        assert args["package_spec"] == "npm:mcp-foo"
        assert args["version"] == "1.0"
        assert args["trust_score"] == 80

    def test_ticket_id_returned_in_proposal(self, orch, catalog, aq):
        _add_declared(catalog, "alice")
        aq.ticket_id_to_return = "b" * 32
        p = orch.propose_install("alice")
        assert p.approval_ticket_id == "b" * 32

    def test_caller_kind_param_respected(self, orch, catalog, aq):
        _add_declared(catalog, "alice")
        orch.propose_install("alice", caller_kind="react")
        assert aq.propose_calls[-1]["caller_kind"] == "react"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — execute_approved_install ApprovalResult validation
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteApprovalResultValidation:
    def test_approval_none_raises(self, orch, catalog):
        _add_declared(catalog, "alice")
        with pytest.raises(InstallError, match="approval_invalid"):
            orch.execute_approved_install("alice", None)

    def test_approval_wrong_type_raises(self, orch, catalog):
        _add_declared(catalog, "alice")
        with pytest.raises(InstallError, match="approval_invalid"):
            orch.execute_approved_install("alice", "not_an_approval")

    def test_decision_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = _approval_result_for(
            "alice", decision=ApprovalDecision.REJECTED,
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approval_not_granted:rejected"

    def test_decision_pending(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = _approval_result_for(
            "alice", decision=ApprovalDecision.PENDING,
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approval_not_granted:pending"

    def test_decision_expired(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = _approval_result_for(
            "alice", decision=ApprovalDecision.EXPIRED,
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approval_not_granted:expired"

    def test_args_none_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = ApprovalResult(decision=ApprovalDecision.APPROVED, args=None)
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approved_args_missing"

    def test_args_empty_rejected(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = ApprovalResult(decision=ApprovalDecision.APPROVED, args={})
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approved_args_missing"

    def test_args_server_id_mismatch(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = _approval_result_for("alice")
        ar = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={**ar.args, "server_id": "bob"},
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "approved_server_id_mismatch"

    def test_execute_never_calls_approval_queue_approve(
        self, orch, catalog, aq
    ):
        """Garde-fou critique : approve() est l'action humaine."""
        _add_declared(catalog, "alice")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        assert aq.approve_called is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Catalog re-check après approval
# ══════════════════════════════════════════════════════════════════════════════


class TestCatalogRecheck:
    def test_server_removed_between_propose_and_execute(self, orch, catalog):
        _add_declared(catalog, "alice")
        ar = _approval_result_for("alice")
        catalog.remove_server("alice")
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason.startswith("status_not_declared")

    def test_package_spec_changed(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        # Approval avec ancien spec
        ar = _approval_result_for(
            "alice", package_spec="npm:mcp-foo-OLD",
            package_name="mcp-foo-OLD",
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:package_spec"

    def test_version_changed(self, orch, catalog):
        _add_declared(catalog, "alice", version="1.0")
        ar = _approval_result_for("alice", version="2.0")
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:version"

    def test_trust_score_changed(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=80)
        ar = _approval_result_for("alice", trust_score=85)
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:trust_score"

    def test_trust_score_below_threshold_after_change(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=80)
        # Modify catalog AFTER approval to drop trust to 50
        catalog.update_trust_score("alice", 50)
        ar = _approval_result_for("alice", trust_score=80)
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        # trust_score différent → catalog_changed:trust_score wins
        assert r.reason == "catalog_changed:trust_score"

    def test_transport_changed_between_approval_and_execute(self, orch, catalog):
        """Catalog : npm:mcp-foo ; approval : transport=pypi → mismatch."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for(
            "alice", transport="pypi",  # mismatch
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:transport"

    def test_package_name_changed_between_approval_and_execute(self, orch, catalog):
        """Catalog : npm:mcp-foo ; approval : package_name=mcp-bar → mismatch."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for(
            "alice", package_name="mcp-bar",  # mismatch
        )
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:package_name"

    def test_transport_mismatch_does_not_instantiate_runner(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice", transport="pypi")

        runner_called = {"v": False}

        def mock_init(self, *args, **kwargs):
            runner_called["v"] = True
            raise AssertionError("Runner MUST NOT be instantiated on mismatch")

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "catalog_changed:transport"
        assert runner_called["v"] is False

    def test_package_name_mismatch_does_not_update_catalog(
        self, orch_live, catalog
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice", package_name="mcp-bar")
        orch_live.execute_approved_install("alice", ar)
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.DECLARED


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — MCPSandboxRunner integration
# ══════════════════════════════════════════════════════════════════════════════


class TestSandboxRunnerIntegration:
    def test_no_direct_subprocess_popen_in_install_orchestrator(self):
        """Garde-fou : aucun subprocess.Popen direct."""
        source = (
            Path(__file__).resolve().parent.parent.parent
            / "src" / "mcp" / "install_orchestrator.py"
        )
        text = source.read_text(encoding="utf-8")
        # Tolère "subprocess" en commentaire/docstring (références conceptuelles)
        # mais refuse "subprocess.Popen" et "Popen("
        assert "subprocess.Popen" not in text
        assert "Popen(" not in text

    def test_execute_uses_mcpsandboxrunner_install(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        captured = {"called": False, "init_args": None}

        original_init = MCPSandboxRunner.__init__
        original_install = MCPSandboxRunner.install

        def mock_init(self, spec, *args, **kwargs):
            captured["init_args"] = (spec, kwargs)
            # Skip vrai init
            self.spec = spec
            self._server_dir = kwargs.get("mcp_root") / spec.name
            self._state = None

        def mock_install(self):
            captured["called"] = True

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        assert captured["called"] is True

    def test_runner_mcp_root_is_install_root_not_target_dir(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        captured = {}

        def mock_init(self, spec, *args, **kwargs):
            captured["mcp_root"] = kwargs.get("mcp_root")
            captured["spec_name"] = spec.name
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        # mcp_root doit être install_root, PAS install_root/<server_id>
        assert captured["mcp_root"] == orch_live.install_root
        assert captured["mcp_root"].name != "alice"

    def test_runner_spec_name_equals_server_id(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        captured = {}

        def mock_init(self, spec, *args, **kwargs):
            captured["spec_name"] = spec.name
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        assert captured["spec_name"] == "alice"

    def test_target_path_relative_is_server_id(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.target_path_relative == "alice"

    def test_npm_maps_to_runner_transport_npm(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        captured = {}

        def mock_init(self, spec, *args, **kwargs):
            captured["transport"] = spec.transport
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        assert captured["transport"] == "npm"

    def test_pypi_maps_to_runner_transport_uv(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="pypi:mcp-bar")
        ar = _approval_result_for(
            "alice", package_spec="pypi:mcp-bar",
            transport="pypi", package_name="mcp-bar",
        )
        captured = {}

        def mock_init(self, spec, *args, **kwargs):
            captured["transport"] = spec.transport
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        assert captured["transport"] == "uv"

    def test_runner_install_raises_returns_failure(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self):
            raise MCPSandboxError("install boom")

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is False
        # Phase I-8 (Fix AK.2) : le reason expose le type d'exception
        # (traçabilité payload) — l'audit garde le code court.
        assert r.reason == "runner_install_failed:MCPSandboxError"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Catalog status transitions
# ══════════════════════════════════════════════════════════════════════════════


class TestCatalogStatusTransitions:
    def test_success_updates_catalog_declared_to_installed(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is True
        entry = catalog.get_server("alice")
        assert entry is not None
        assert entry.status == ServerStatus.INSTALLED

    def test_failure_does_not_update_catalog(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self):
            raise MCPSandboxError("nope")

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.DECLARED

    def test_never_transitions_to_active(
        self, orch_live, catalog, monkeypatch
    ):
        """Phase 18 ne doit JAMAIS faire INSTALLED → ACTIVE."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        update_calls: List[Any] = []
        original = catalog.update_status

        def spy_update(server_id, new_status):
            update_calls.append((server_id, new_status))
            return original(server_id, new_status)

        monkeypatch.setattr(catalog, "update_status", spy_update)

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)

        for sid, st in update_calls:
            assert st != ServerStatus.ACTIVE

    def test_idempotence_after_installed(self, orch_live, catalog, monkeypatch):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        catalog.update_status("alice", ServerStatus.INSTALLED)
        ar = _approval_result_for("alice")
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "status_not_declared:installed"


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — LOCAL transport (execute via generated uv package)
# ══════════════════════════════════════════════════════════════════════════════


class TestLocalTransport:
    def test_local_transport_proposal_still_allowed(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="local:my-server")
        p = orch.propose_install("alice")
        assert p.transport == InstallTransport.LOCAL

    def test_local_transport_execute_uses_uv_generated_package(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="local:my-server")
        ar = _approval_result_for(
            "alice", package_spec="local:my-server",
            transport="local", package_name="my-server",
        )
        fake_pkg = type("Pkg", (), {
            "package_dir": Path("C:/safe/generated/alice"),
            "module_name": "lumena_mcp_alice",
        })()
        monkeypatch.setattr(
            "src.mcp.install_orchestrator.resolve_local_mcp_package",
            lambda server_id: fake_pkg,
        )
        captured = {}

        def mock_init(self, spec, *args, **kwargs):
            captured["spec"] = spec
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is True
        assert captured["spec"].transport == "uv"
        assert captured["spec"].package == str(fake_pkg.package_dir)
        assert captured["spec"].args == ["-m", "lumena_mcp_alice"]
        assert captured["spec"].require_wheels_only is False
        assert r.transport == InstallTransport.LOCAL

    def test_local_transport_missing_package_does_not_instantiate_runner(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="local:my-server")
        ar = _approval_result_for(
            "alice", package_spec="local:my-server",
            transport="local", package_name="my-server",
        )
        from src.mcp.local_package import LocalMCPPackageError
        monkeypatch.setattr(
            "src.mcp.install_orchestrator.resolve_local_mcp_package",
            lambda server_id: (_ for _ in ()).throw(LocalMCPPackageError("missing")),
        )

        runner_init_called = {"v": False}

        def mock_init(self, *args, **kwargs):
            runner_init_called["v"] = True
            raise AssertionError("Runner MUST NOT be instantiated without local package")

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.reason == "local_package_missing"
        assert runner_init_called["v"] is False

    def test_local_transport_missing_package_does_not_update_catalog(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="local:my-server")
        ar = _approval_result_for(
            "alice", package_spec="local:my-server",
            transport="local", package_name="my-server",
        )
        from src.mcp.local_package import LocalMCPPackageError
        monkeypatch.setattr(
            "src.mcp.install_orchestrator.resolve_local_mcp_package",
            lambda server_id: (_ for _ in ()).throw(LocalMCPPackageError("missing")),
        )
        orch_live.execute_approved_install("alice", ar)
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.DECLARED


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — dry_run
# ══════════════════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_returns_success_false(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False

    def test_dry_run_result_has_dry_run_true(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        r = orch.execute_approved_install("alice", ar)
        assert r.dry_run is True
        assert r.reason == "dry_run"

    def test_dry_run_never_updates_catalog(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.DECLARED

    def test_dry_run_never_instantiates_runner(
        self, orch, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        runner_called = {"v": False}

        def mock_init(self, *args, **kwargs):
            runner_called["v"] = True
            raise AssertionError("Runner MUST NOT be instantiated in dry_run")

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        orch.execute_approved_install("alice", ar)
        assert runner_called["v"] is False

    def test_dry_run_audit_event(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        events = [e for e in _audit_lines(orch) if e["event"] == "dry_run_install"]
        assert events


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensic:
    def test_audit_anti_leak_server_id_invalid(self, orch):
        with pytest.raises(InstallError):
            orch.propose_install("ATTACKER_SERVER_ID_MARKER")
        blob = _audit_blob(orch)
        assert "ATTACKER_SERVER_ID_MARKER" not in blob

    def test_audit_does_not_stringify_runner_queue_catalog(
        self, orch, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        blob = _audit_blob(orch)
        assert "_FakeApprovalQueue" not in blob
        assert "MCPSandboxRunner" not in blob
        assert "MCPServerCatalog" not in blob
        assert "object at 0x" not in blob

    def test_audit_events_present(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        orch.propose_install("alice")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        events = {e["event"] for e in _audit_lines(orch)}
        assert "install_proposed" in events
        assert "dry_run_install" in events

    def test_audit_does_not_log_approved_args_raw_secrets(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        # Inject a marker in args (mauvaise pratique, mais simulons une fuite)
        ar = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "server_id": "alice",
                "transport": "npm",
                "package_name": "mcp-foo",
                "package_spec": "npm:mcp-foo",
                "version": None,
                "trust_score": 80,
                "secret_marker": "APPROVED_ARGS_LEAK_MARKER_AAA",
            },
        )
        orch.execute_approved_install("alice", ar)
        blob = _audit_blob(orch)
        assert "APPROVED_ARGS_LEAK_MARKER_AAA" not in blob

    def test_audit_reason_codes_short(self, orch, catalog):
        _add_declared(catalog, "alice", trust_score=50)
        with pytest.raises(InstallError):
            orch.propose_install("alice")
        for ev in _audit_lines(orch):
            if "reason" in ev:
                assert isinstance(ev["reason"], str)
                assert len(ev["reason"]) < 80

    def test_audit_no_module_paths(self, orch, catalog):
        _add_declared(catalog, "alice")
        orch.propose_install("alice")
        blob = _audit_blob(orch)
        assert "src.mcp" not in blob
        assert "C:\\" not in blob
        assert "/home/" not in blob

    def test_audit_target_path_relative_only(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")
        orch.execute_approved_install("alice", ar)
        events = [
            e for e in _audit_lines(orch)
            if e["event"] == "dry_run_install"
        ]
        ev = events[-1]
        assert ev["target_path_relative"] == "alice"
        # Aucun path absolu
        assert "/" not in ev["target_path_relative"]
        assert "\\" not in ev["target_path_relative"]

    def test_audit_multi_scan_no_leak(self, orch, catalog):
        markers = [f"FORENSIC_PHASE18_MARKER_{i}" for i in range(5)]
        for i, m in enumerate(markers):
            sid = f"srv-{i}"
            _add_declared(catalog, sid, package_spec=f"npm:{m.lower()}-pkg")
        for i in range(5):
            try:
                orch.propose_install(f"srv-{i}")
            except InstallError:
                pass
        blob = _audit_blob(orch)
        for m in markers:
            assert m not in blob
            assert m.lower() not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Sanity end-to-end (mocks)
# ══════════════════════════════════════════════════════════════════════════════


class TestSanityEndToEnd:
    def test_propose_execute_happy_path_live(
        self, orch_live, catalog, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        p = orch_live.propose_install("alice")
        assert isinstance(p, InstallProposal)

        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        r = orch_live.execute_approved_install("alice", ar)
        assert r.success is True
        assert r.reason == "installed_ok"

    def test_propose_dry_run_default(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        orch.propose_install("alice")
        ar = _approval_result_for("alice")
        r = orch.execute_approved_install("alice", ar)
        assert r.dry_run is True
        assert r.success is False

    def test_multi_server_isolation(self, orch, catalog):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        _add_declared(catalog, "bob", package_spec="pypi:mcp-bar")
        pa = orch.propose_install("alice")
        pb = orch.propose_install("bob")
        assert pa.server_id == "alice"
        assert pb.server_id == "bob"
        assert pa.transport == InstallTransport.NPM
        assert pb.transport == InstallTransport.PYPI

    def test_idempotent_propose_two_tickets(self, orch, catalog, aq):
        """Propose 2× : 2 tickets distincts (idempotence côté queue)."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        aq.ticket_id_to_return = "c" * 32
        orch.propose_install("alice")
        aq.ticket_id_to_return = "d" * 32
        orch.propose_install("alice")
        assert len(aq.propose_calls) == 2

    def test_e2e_no_subprocess_popen_anywhere(
        self, orch_live, catalog, monkeypatch
    ):
        """Garde-fou : durant un cycle complet, aucun subprocess.Popen."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)

        with mock.patch("subprocess.Popen") as popen_patch:
            orch_live.propose_install("alice")
            orch_live.execute_approved_install("alice", ar)
            assert popen_patch.call_count == 0

    def test_forensic_scan_10_markers(self, orch, catalog):
        markers = [f"E2E_PHASE18_MARKER_{i}" for i in range(10)]
        # Tente d'injecter markers dans server_ids invalides
        for m in markers:
            try:
                orch.propose_install(m)
            except InstallError:
                pass
        blob = _audit_blob(orch)
        for m in markers:
            assert m not in blob

    def test_parse_package_spec_helper(self):
        assert _parse_package_spec("npm:foo") == (
            InstallTransport.NPM, "foo"
        )
        assert _parse_package_spec("npm:@scope/x") == (
            InstallTransport.NPM, "@scope/x"
        )
        assert _parse_package_spec("pypi:bar") == (
            InstallTransport.PYPI, "bar"
        )
        assert _parse_package_spec("local:slug") == (
            InstallTransport.LOCAL, "slug"
        )
        assert _parse_package_spec("invalid") is None
        assert _parse_package_spec("") is None
        assert _parse_package_spec(None) is None

    def test_execute_after_status_changed_to_installed(self, orch, catalog):
        """Une 2e execute après INSTALLED → rejet status_not_declared."""
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        catalog.update_status("alice", ServerStatus.INSTALLED)
        ar = _approval_result_for("alice")
        r = orch.execute_approved_install("alice", ar)
        assert r.success is False
        assert r.reason == "status_not_declared:installed"

    def test_approval_queue_approve_never_invoked_end_to_end(
        self, orch_live, catalog, aq, monkeypatch
    ):
        _add_declared(catalog, "alice", package_spec="npm:mcp-foo")
        orch_live.propose_install("alice")
        ar = _approval_result_for("alice")

        def mock_init(self, spec, *args, **kwargs):
            self.spec = spec

        def mock_install(self): pass

        monkeypatch.setattr(MCPSandboxRunner, "__init__", mock_init)
        monkeypatch.setattr(MCPSandboxRunner, "install", mock_install)
        orch_live.execute_approved_install("alice", ar)
        assert aq.approve_called is False
        assert aq.reject_called is False
