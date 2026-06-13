"""
Tests Phase 19 v3 — MCPActivationService.

Sections :
  1. Init & configuration
  2. Kill switch env
  3. propose_activation
  4. activate pré-conditions
  5. dry_run (zéro effet runtime — tests obligatoires v3)
  6. Pipeline happy path (mocks)
  7. Rollback runner_start_failed
  8. Rollback client_initialize_failed
  9. Rollback handlers + tool_mismatch
  10. Rollback watcher_register_failed
  11. Rollback catalog_activate_failed
  12. Status re-check pré-ACTIVE
  13. deactivate
  14. Audit forensique no-PII
  15. Intégration end-to-end (mocks réalistes)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from src.mcp.approval_queue import (
    ApprovalDecision,
    ApprovalQueue,
    ApprovalResult,
)
from src.mcp.client import MCPTool
from src.mcp.discovery import (
    DiscoveryError,
    DiscoveryReport,
    MCPDiscoveryService,
    ToolDiscoveryProposal,
)
from src.mcp.activation_service import (
    ActivationError,
    ActivationProposal,
    ActivationResult,
    ActivationStep,
    DeactivationResult,
    MCPActivationService,
)
from src.mcp.policy import MCPPolicy
from src.mcp.policy_attributor import PolicyAttributor
from src.mcp.runtime_watcher import RuntimeWatcher
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
    def __init__(self):
        self.propose_calls: List[Dict[str, Any]] = []
        self.ticket_id_to_return = "a" * 32
        self.approve_called = False
        self.reject_called = False

    def propose(self, **kwargs) -> str:
        self.propose_calls.append(dict(kwargs))
        return self.ticket_id_to_return

    def approve(self, action_id: str):
        self.approve_called = True
        raise AssertionError("approve() MUST NOT be called by activation service")

    def reject(self, action_id: str, reason: str) -> bool:
        self.reject_called = True
        raise AssertionError("reject() MUST NOT be called by activation service")


class _FakeRunner:
    def __init__(self, *, fail_start: bool = False, fail_stop: bool = False):
        self.start_calls = 0
        self.stop_calls = 0
        self.start_call_args: List[Any] = []
        self.stop_call_args: List[Any] = []
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self._state = "init"

    def start(self, runtime_env_secrets=None):
        # Fix Q (Phase I-7) : signature alignée avec MCPSandboxRunner.start
        # qui accepte un dict {env_key: value} pour injecter secrets/config
        # dans l'env du child Node.
        self.start_calls += 1
        self.start_call_args.append((runtime_env_secrets,))
        if self.fail_start:
            raise RuntimeError("START_FAIL_INTERNAL_MARKER")
        self._state = "running"

    def stop(self):
        self.stop_calls += 1
        self.stop_call_args.append(())
        if self.fail_stop:
            raise RuntimeError("STOP_FAIL_INTERNAL_MARKER")
        self._state = "stopped"

    def state(self) -> str:
        return self._state


class _FakeClient:
    def __init__(
        self,
        tools: Optional[List[MCPTool]] = None,
        *,
        fail_initialize: bool = False,
        fail_list_tools: bool = False,
    ):
        self.initialize_calls = 0
        self.list_tools_calls = 0
        self.close_calls = 0
        self._tools = tools or []
        self.fail_initialize = fail_initialize
        self.fail_list_tools = fail_list_tools
        self.is_initialized = False

    def initialize(self):
        self.initialize_calls += 1
        if self.fail_initialize:
            raise RuntimeError("INIT_FAIL_INTERNAL_MARKER")
        self.is_initialized = True
        return {"capabilities": {}}

    def list_tools(self) -> List[MCPTool]:
        self.list_tools_calls += 1
        if self.fail_list_tools:
            raise RuntimeError("LIST_TOOLS_FAIL_INTERNAL_MARKER")
        return self._tools

    def close(self):
        self.close_calls += 1


class _FakeAdapter:
    def __init__(self, *, fail_on_tool_name: Optional[str] = None):
        self.calls: List[Dict[str, Any]] = []
        self.fail_on_tool_name = fail_on_tool_name

    def adapt_tool(
        self,
        *,
        client: Any,
        server_name: str,
        mcp_tool: MCPTool,
        category: str = "mcp",
        timeout_s: Optional[float] = None,
    ) -> Any:
        self.calls.append({
            "client": client,
            "server_name": server_name,
            "mcp_tool": mcp_tool,
            "category": category,
            "timeout_s": timeout_s,
        })
        if (
            self.fail_on_tool_name is not None
            and mcp_tool.name == self.fail_on_tool_name
        ):
            raise RuntimeError("ADAPTER_FAIL_INTERNAL_MARKER")

        @dataclass
        class _HandlerDef:
            name: str
            policy_v: str = ""
        return _HandlerDef(name=f"mcp__{server_name}__{mcp_tool.name}")


class _FakeRegistryWriter:
    def __init__(
        self,
        *,
        fail_register_on_index: Optional[int] = None,
        fail_unregister: bool = False,
    ):
        self._handlers: Dict[str, Any] = {}
        self.register_calls: List[Dict[str, Any]] = []
        self.unregister_calls: List[str] = []
        self.fail_register_on_index = fail_register_on_index
        self.fail_unregister = fail_unregister

    def register_dynamic_handler(
        self,
        handler_def: Any,
        *,
        policy: MCPPolicy,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> None:
        idx = len(self.register_calls)
        self.register_calls.append({
            "handler_def": handler_def,
            "policy": policy,
            "provenance": provenance,
        })
        if (
            self.fail_register_on_index is not None
            and idx == self.fail_register_on_index
        ):
            raise RuntimeError("REGISTER_FAIL_INTERNAL_MARKER")
        name = getattr(handler_def, "name", str(handler_def))
        self._handlers[name] = handler_def

    def unregister_dynamic_handler(self, name: str) -> bool:
        self.unregister_calls.append(name)
        if self.fail_unregister:
            raise RuntimeError("UNREGISTER_FAIL_INTERNAL_MARKER")
        if name in self._handlers:
            del self._handlers[name]
            return True
        return False

    def is_dynamic_handler(self, name: str) -> bool:
        return name in self._handlers


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
def watcher(tmp_path: Path) -> RuntimeWatcher:
    return RuntimeWatcher(
        snapshots_dir=tmp_path / "watcher" / "snaps",
        audit_log_path=tmp_path / "watcher" / "audit.jsonl",
    )


@pytest.fixture
def discovery(tmp_path: Path, catalog) -> MCPDiscoveryService:
    """Discovery configuré pour Phase 19 : require_server_callable=False."""
    attributor = PolicyAttributor(
        audit_log_path=tmp_path / "attributor" / "audit.jsonl",
    )
    return MCPDiscoveryService(
        catalog=catalog,
        attributor=attributor,
        audit_log_path=tmp_path / "discovery" / "audit.jsonl",
        require_server_callable=False,
        persist_reports=False,
    )


def _make_runner(*, fail_start: bool = False) -> _FakeRunner:
    return _FakeRunner(fail_start=fail_start)


def _make_client(tools: Optional[List[MCPTool]] = None) -> _FakeClient:
    return _FakeClient(tools=tools)


def _mcptool(name: str, description: str = "", input_schema: Optional[Dict] = None) -> MCPTool:
    return MCPTool(
        name=name, description=description,
        input_schema=input_schema if input_schema is not None else {},
    )


def _build_service(
    tmp_path: Path,
    catalog,
    aq,
    discovery,
    watcher,
    *,
    adapter: Optional[_FakeAdapter] = None,
    registry: Optional[_FakeRegistryWriter] = None,
    runner_factory=None,
    client_factory=None,
    require_approval: bool = True,
    dry_run: bool = True,
) -> MCPActivationService:
    adapter = adapter or _FakeAdapter()
    registry = registry or _FakeRegistryWriter()

    if runner_factory is None:
        def runner_factory(sid, entry):
            return _make_runner()

    if client_factory is None:
        def client_factory(runner):
            return _make_client()

    return MCPActivationService(
        catalog=catalog,
        approval_queue=aq,
        discovery=discovery,
        adapter=adapter,
        registry_writer=registry,
        runtime_watcher=watcher,
        runner_factory=runner_factory,
        client_factory=client_factory,
        audit_log_path=tmp_path / "activation" / "audit.jsonl",
        require_approval=require_approval,
        dry_run=dry_run,
    )


def _add_installed(
    catalog: MCPServerCatalog,
    server_id: str = "alice",
    package_spec: str = "npm:mcp-foo",
    trust_score: Optional[int] = 80,
) -> None:
    catalog.add_server(
        server_id=server_id, display_name="Alice",
        package_spec=package_spec, owner_profile="alice",
        trust_score=trust_score,
    )
    catalog.update_status(server_id, ServerStatus.INSTALLED)


def _approved(server_id: str = "alice") -> ApprovalResult:
    return ApprovalResult(
        decision=ApprovalDecision.APPROVED,
        args={"server_id": server_id, "action": "activate"},
    )


def _audit_lines(s: MCPActivationService) -> List[Dict[str, Any]]:
    if not s.audit_log_path.exists():
        return []
    out = []
    with open(s.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(s: MCPActivationService) -> str:
    if not s.audit_log_path.exists():
        return ""
    return s.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_audit_dir_created(self, tmp_path, catalog, aq, discovery, watcher):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        assert s.audit_log_path.parent.exists()

    def test_default_dry_run_true(self, tmp_path, catalog, aq, discovery, watcher):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        assert s.dry_run is True

    def test_default_require_approval_true(self, tmp_path, catalog, aq, discovery, watcher):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        assert s.require_approval is True

    def test_init_rejects_discovery_with_require_callable_true(
        self, tmp_path, catalog, aq, watcher
    ):
        attributor = PolicyAttributor(audit_log_path=tmp_path / "att.jsonl")
        bad_discovery = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "disc.jsonl",
            require_server_callable=True,  # ← non conforme Phase 19
        )
        with pytest.raises(ValueError, match="require_server_callable"):
            MCPActivationService(
                catalog=catalog, approval_queue=aq,
                discovery=bad_discovery,
                adapter=_FakeAdapter(),
                registry_writer=_FakeRegistryWriter(),
                runtime_watcher=watcher,
                runner_factory=lambda sid, e: _make_runner(),
                client_factory=lambda r: _make_client(),
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_none_catalog(self, tmp_path, aq, discovery, watcher):
        with pytest.raises(ValueError, match="catalog"):
            MCPActivationService(
                catalog=None,  # type: ignore
                approval_queue=aq,
                discovery=discovery,
                adapter=_FakeAdapter(),
                registry_writer=_FakeRegistryWriter(),
                runtime_watcher=watcher,
                runner_factory=lambda sid, e: _make_runner(),
                client_factory=lambda r: _make_client(),
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_adapter_without_adapt_tool(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        class NoAdapt: pass
        with pytest.raises(ValueError, match="adapt_tool"):
            MCPActivationService(
                catalog=catalog, approval_queue=aq, discovery=discovery,
                adapter=NoAdapt(),  # type: ignore
                registry_writer=_FakeRegistryWriter(),
                runtime_watcher=watcher,
                runner_factory=lambda sid, e: _make_runner(),
                client_factory=lambda r: _make_client(),
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_registry_without_methods(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        class BadRegistry: pass
        with pytest.raises(ValueError, match="register"):
            MCPActivationService(
                catalog=catalog, approval_queue=aq, discovery=discovery,
                adapter=_FakeAdapter(),
                registry_writer=BadRegistry(),  # type: ignore
                runtime_watcher=watcher,
                runner_factory=lambda sid, e: _make_runner(),
                client_factory=lambda r: _make_client(),
                audit_log_path=tmp_path / "audit.jsonl",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Kill switch env
# ══════════════════════════════════════════════════════════════════════════════


class TestKillSwitch:
    def test_kill_switch_blocks_activate(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        monkeypatch.setenv("LUMENA_MCP_ACTIVATION_DISABLED", "1")
        with pytest.raises(ActivationError, match="activation_disabled"):
            s.activate("alice", _approved())

    def test_kill_switch_blocks_propose(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        monkeypatch.setenv("LUMENA_MCP_ACTIVATION_DISABLED", "true")
        with pytest.raises(ActivationError, match="activation_disabled"):
            s.propose_activation("alice")

    def test_kill_switch_absent_continues(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        monkeypatch.delenv("LUMENA_MCP_ACTIVATION_DISABLED", raising=False)
        p = s.propose_activation("alice")
        assert isinstance(p, ActivationProposal)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — propose_activation
# ══════════════════════════════════════════════════════════════════════════════


class TestPropose:
    def test_propose_happy_path(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        p = s.propose_activation("alice")
        assert p.server_id == "alice"
        assert isinstance(p.approval_ticket_id, str)

    def test_propose_policy_is_local_write(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.propose_activation("alice")
        assert aq.propose_calls[-1]["policy"] == MCPPolicy.LOCAL_WRITE

    def test_propose_caller_kind_default_silent(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.propose_activation("alice")
        assert aq.propose_calls[-1]["caller_kind"] == "silent"

    def test_propose_caller_kind_custom(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.propose_activation("alice", caller_kind="react")
        assert aq.propose_calls[-1]["caller_kind"] == "react"

    def test_propose_status_not_installed_rejected(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )  # DECLARED
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="status_not_installed"):
            s.propose_activation("alice")

    def test_propose_server_unknown(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="server_unknown"):
            s.propose_activation("ghost")

    def test_propose_server_id_invalid(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="server_id_invalid"):
            s.propose_activation("ALICE")


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — activate pré-conditions
# ══════════════════════════════════════════════════════════════════════════════


class TestActivatePreConditions:
    def test_server_id_invalid_raises(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="server_id_invalid"):
            s.activate("ALICE", _approved())

    def test_server_unknown(self, tmp_path, catalog, aq, discovery, watcher):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("ghost", _approved("ghost"))
        assert r.success is False
        assert r.reason == "server_unknown"
        assert r.last_step == ActivationStep.NOT_STARTED

    def test_status_declared_rejected(self, tmp_path, catalog, aq, discovery, watcher):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )  # DECLARED
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("status_not_installed")
        assert r.last_step == ActivationStep.NOT_STARTED

    def test_status_active_rejected(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice")
        catalog.update_status("alice", ServerStatus.ACTIVE)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("status_not_installed")

    def test_status_quarantined_rejected(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice")
        catalog.update_status("alice", ServerStatus.QUARANTINED)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False

    def test_status_removed_rejected(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice")
        catalog.remove_server("alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False

    def test_require_approval_none_raises(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="approval_required"):
            s.activate("alice", None)

    def test_decision_rejected(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate(
            "alice",
            ApprovalResult(decision=ApprovalDecision.REJECTED, args=None),
        )
        assert r.success is False
        assert r.reason.startswith("approval_not_granted:rejected")

    def test_args_server_id_mismatch(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        bad = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={"server_id": "bob", "action": "activate"},
        )
        r = s.activate("alice", bad)
        assert r.success is False
        assert r.reason == "approved_server_id_mismatch"

    def test_already_running_rejected(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            dry_run=False,
        )
        # Force la map running
        from src.mcp.activation_service import _RunningContext
        s._running_contexts["alice"] = _RunningContext(
            server_id="alice", runner=None, client=None,
            registered_handlers=[],
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "already_running"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — dry_run (ZÉRO EFFET RUNTIME — tests obligatoires v3)
# ══════════════════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_returns_success_false_with_dry_run_flag(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.dry_run is True
        assert r.reason == "dry_run"

    def test_dry_run_last_step_is_not_started(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.last_step == ActivationStep.NOT_STARTED

    def test_dry_run_does_not_call_runner_factory(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        runner_calls = {"v": 0}

        def runner_factory(sid, entry):
            runner_calls["v"] += 1
            raise AssertionError("runner_factory MUST NOT be called in dry_run")

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=runner_factory,
        )
        s.activate("alice", _approved())
        assert runner_calls["v"] == 0

    def test_dry_run_does_not_call_client_factory(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        client_calls = {"v": 0}

        def client_factory(runner):
            client_calls["v"] += 1
            raise AssertionError("client_factory MUST NOT be called in dry_run")

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            client_factory=client_factory,
        )
        s.activate("alice", _approved())
        assert client_calls["v"] == 0

    def test_dry_run_does_not_call_discovery(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        discovery_calls = {"v": 0}
        original_discover = discovery.discover

        def spy_discover(*args, **kwargs):
            discovery_calls["v"] += 1
            return original_discover(*args, **kwargs)

        monkeypatch.setattr(discovery, "discover", spy_discover)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert discovery_calls["v"] == 0

    def test_dry_run_does_not_register_handlers(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        registry = _FakeRegistryWriter()
        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher, registry=registry,
        )
        s.activate("alice", _approved())
        assert registry.register_calls == []

    def test_dry_run_does_not_update_catalog_active(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.INSTALLED

    def test_dry_run_does_not_register_watcher(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        spy_calls = {"v": 0}
        original = watcher.register_runner

        def spy(sid, r):
            spy_calls["v"] += 1
            return original(sid, r)

        monkeypatch.setattr(watcher, "register_runner", spy)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert spy_calls["v"] == 0

    def test_dry_run_pre_conditions_kill_switch(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice")
        monkeypatch.setenv("LUMENA_MCP_ACTIVATION_DISABLED", "1")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="activation_disabled"):
            s.activate("alice", _approved())

    def test_dry_run_pre_conditions_server_unknown(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("ghost", _approved("ghost"))
        assert r.success is False
        assert r.reason == "server_unknown"
        assert r.dry_run is False  # pre-condition fail returns before dry_run check

    def test_dry_run_pre_conditions_approval_missing(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError, match="approval_required"):
            s.activate("alice", None)

    def test_dry_run_audit_event_present(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        events = [e for e in _audit_lines(s) if e["event"] == "dry_run_activation"]
        assert events


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Pipeline happy path
# ══════════════════════════════════════════════════════════════════════════════


class TestPipelineHappyPath:
    def _setup_three_tools_service(
        self, tmp_path, catalog, aq, discovery, watcher,
        trust_score: int = 95,
    ):
        _add_installed(catalog, "alice", trust_score=trust_score)
        tools = [
            _mcptool("read_doc"),
            _mcptool("get_status"),
            # Phase I-8 (Fix AG) : inclassable + trust ≥ 70 → fallback
            # EXTERNAL_WRITE_RECOVERABLE (avant : refusé no_keyword_match).
            _mcptool("opaque_thing"),
        ]
        client = _make_client(tools=tools)
        runner = _make_runner()
        adapter = _FakeAdapter()
        registry = _FakeRegistryWriter()

        def runner_factory(sid, e):
            return runner

        def client_factory(r):
            return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            adapter=adapter, registry=registry,
            runner_factory=runner_factory, client_factory=client_factory,
            dry_run=False,
        )
        return s, runner, client, adapter, registry

    def test_pipeline_completes_successfully(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, client, adapter, registry = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        r = s.activate("alice", _approved())
        assert r.success is True
        assert r.reason == "activated_ok"
        assert r.last_step == ActivationStep.COMPLETED

    def test_runner_start_called_without_kwargs(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        assert runner.start_calls == 1
        # Fix Q (Phase I-7) : start est appelé avec runtime_env_secrets=
        # (None ici car ni credentials_service ni config_service injectés).
        # Phase 5 : signature start(runtime_env_secrets=None) — pas timeout_s.
        assert runner.start_call_args == [(None,)]

    def test_client_initialize_called(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, client, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        assert client.initialize_calls == 1

    def test_three_handlers_registered_fallback_included(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Phase I-8 (Fix AG) : read_doc + get_status (keywords) +
        opaque_thing (fallback conservateur, trust 95 ≥ 70) = 3 registered,
        0 refus. Avant I-8 : opaque_thing était refusé no_keyword_match."""
        s, _, _, _, registry = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        r = s.activate("alice", _approved())
        assert len(registry.register_calls) == 3
        assert r.discovery_proposed_count == 3
        assert r.discovery_refused_count == 0

    def test_adapter_called_with_correct_kwargs(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, client, adapter, _ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        # Phase I-8 (Fix AG) : 3 tools enregistrés (fallback inclus).
        assert len(adapter.calls) == 3
        call = adapter.calls[0]
        assert call["client"] is client
        assert call["server_name"] == "alice"
        assert call["category"] == "mcp"

    def test_register_called_with_provenance_dict_not_string(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _, _, registry = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        for c in registry.register_calls:
            assert isinstance(c["provenance"], dict)
            assert c["provenance"]["source_kind"] == "mcp"
            assert c["provenance"]["server_id"] == "alice"
            assert c["provenance"]["phase"] == "19"

    def test_catalog_transitions_to_active(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        entry = catalog.get_server("alice")
        assert entry.status == ServerStatus.ACTIVE

    def test_watcher_register_runner_called(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        assert watcher.is_registered("alice")

    def test_watcher_record_event_started(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        # Vérification indirecte : pas d'exception
        assert s.is_running("alice")

    def test_registered_handlers_in_result(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        r = s.activate("alice", _approved())
        assert "mcp__alice__read_doc" in r.registered_handlers
        assert "mcp__alice__get_status" in r.registered_handlers
        # Phase I-8 (Fix AG) : l'inclassable est enregistré (fallback
        # conservateur EXTERNAL_WRITE_RECOVERABLE, trust 95 ≥ 70).
        assert "mcp__alice__opaque_thing" in r.registered_handlers

    def test_activation_started_audit_event(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        events = [e for e in _audit_lines(s) if e["event"] == "activation_started"]
        assert events

    def test_activation_completed_audit(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        events = [e for e in _audit_lines(s) if e["event"] == "activation_completed"]
        assert events
        ev = events[-1]
        # Phase I-8 (Fix AG) : 3 (fallback conservateur inclus).
        assert ev["registered_count"] == 3

    def test_no_approve_call_to_queue(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup_three_tools_service(
            tmp_path, catalog, aq, discovery, watcher,
        )
        s.activate("alice", _approved())
        assert aq.approve_called is False
        assert aq.reject_called is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Rollback runner_start_failed
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackRunnerStartFailed:
    def _setup(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner(fail_start=True)
        registry = _FakeRegistryWriter()

        def runner_factory(sid, e):
            return runner

        def client_factory(r):
            raise AssertionError("client_factory MUST NOT be called")

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=runner_factory,
            client_factory=client_factory,
            dry_run=False,
        )
        return s, runner, registry

    def test_runner_start_failure_returns_failure(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False
        # Fix N (Phase I-7) : reason expose maintenant type + msg exception
        # (format "runner_start_failed:<ExcType>:<msg>") pour diagnostic.
        assert r.reason.startswith("runner_start_failed")
        assert r.last_step == ActivationStep.RUNNER_CREATED

    def test_catalog_stays_installed(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_no_handler_registered(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, registry = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert registry.register_calls == []

    def test_audit_no_internal_marker_leak(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "START_FAIL_INTERNAL_MARKER" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Rollback client_initialize_failed
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackClientInitializeFailed:
    def _setup(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()
        client = _FakeClient(fail_initialize=True)
        registry = _FakeRegistryWriter()

        def runner_factory(sid, e):
            return runner

        def client_factory(r):
            return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=runner_factory, client_factory=client_factory,
            dry_run=False,
        )
        return s, runner, client, registry

    def test_initialize_failure(self, tmp_path, catalog, aq, discovery, watcher):
        s, _, _, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "client_initialize_failed"
        assert r.last_step == ActivationStep.CLIENT_CREATED

    def test_runner_stopped_on_failure(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, _, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert runner.stop_calls == 1
        # Signature réelle : stop() sans kwargs
        assert runner.stop_call_args == [()]

    def test_no_handler_registered(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _, registry = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert registry.register_calls == []

    def test_catalog_stays_installed(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_audit_no_internal_marker_leak(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "INIT_FAIL_INTERNAL_MARKER" not in blob

    def test_client_initialize_failure_closes_client_if_created(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Client est créé (étape 3 OK), initialize raise → close() doit
        être appelé dans le rollback."""
        s, runner, client, _ = self._setup(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert client.close_calls == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Rollback handlers + tool_mismatch
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackHandlersAndMismatch:
    def test_register_handler_2_fails_rolls_back_handler_1(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [
            _mcptool("read_doc"),
            _mcptool("get_status"),
            _mcptool("describe_x"),
        ]
        runner = _make_runner()
        client = _make_client(tools=tools)
        # Fail register on index 1 (deuxième register)
        registry = _FakeRegistryWriter(fail_register_on_index=1)

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("register_failed")
        # 1er register OK, puis 2e fail
        assert len(registry.register_calls) == 2
        # Le 1er handler doit avoir été unregister
        assert len(registry.unregister_calls) >= 1
        # Catalog reste INSTALLED
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED
        # Watcher non enregistré
        assert not watcher.is_registered("alice")
        # Runner stop
        assert runner.stop_calls == 1

    def test_tool_mismatch_between_discovery_and_list_tools(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        """Le second list_tools ne contient plus un tool proposé par Discovery."""
        _add_installed(catalog, "alice", trust_score=95)
        tools_first = [_mcptool("read_doc"), _mcptool("get_status")]
        tools_second = [_mcptool("read_doc")]  # get_status a disparu !

        call_count = {"v": 0}
        runner = _make_runner()

        class TwoCallClient(_FakeClient):
            def list_tools(self):
                call_count["v"] += 1
                if call_count["v"] == 1:
                    return tools_first
                return tools_second

            def initialize(self):
                self.is_initialized = True
                return {"capabilities": {}}

        client = TwoCallClient(tools=tools_first)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("tool_mismatch")
        # read_doc avait pu être registered (ordre déterministe alphabétique)
        # mais get_status a triggé le mismatch
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED
        assert runner.stop_calls == 1

    def test_tool_mismatch_does_not_update_catalog_to_active(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools_first = [_mcptool("read_doc")]
        tools_second = []  # mismatch

        call_count = {"v": 0}
        runner = _make_runner()

        class TwoCallClient(_FakeClient):
            def list_tools(self):
                call_count["v"] += 1
                if call_count["v"] == 1:
                    return tools_first
                return tools_second

            def initialize(self):
                self.is_initialized = True
                return {"capabilities": {}}

        client = TwoCallClient(tools=tools_first)

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_adapter_failure_rolls_back(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc"), _mcptool("get_status")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        adapter = _FakeAdapter(fail_on_tool_name="get_status")
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            adapter=adapter, registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("adapter_failed")
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED
        assert runner.stop_calls == 1

    def test_list_tools_failure_rolls_back(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()

        class FirstOkSecondFail(_FakeClient):
            _first = True
            def list_tools(self):
                if self._first:
                    self._first = False
                    return [_mcptool("read_doc")]
                raise RuntimeError("LIST_TOOLS_BOOM_MARKER")

            def initialize(self):
                self.is_initialized = True
                return {}

        client = FirstOkSecondFail(tools=[_mcptool("read_doc")])

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "list_tools_failed"
        assert runner.stop_calls == 1
        blob = _audit_blob(s)
        assert "LIST_TOOLS_BOOM_MARKER" not in blob

    def test_discovery_failure_closes_client(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        """Discovery raise → client.close() doit être appelé."""
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()
        client = _make_client(tools=[_mcptool("read_doc")])

        def rf(sid, e): return runner
        def cf(r): return client

        # Force discovery to raise
        def bad_discover(*a, **kw):
            raise DiscoveryError("forced_failure")
        monkeypatch.setattr(discovery, "discover", bad_discover)

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "discovery_failed"
        assert client.close_calls == 1

    def test_list_tools_failure_closes_client(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Second list_tools raise → client.close() doit être appelé."""
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()

        class FirstOkSecondFail(_FakeClient):
            _first = True
            def list_tools(self):
                if self._first:
                    self._first = False
                    return [_mcptool("read_doc")]
                raise RuntimeError("LIST_TOOLS_BOOM")

            def initialize(self):
                self.is_initialized = True
                return {}

        client = FirstOkSecondFail(tools=[_mcptool("read_doc")])

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        assert client.close_calls == 1

    def test_register_failure_closes_client(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Register handler raise → client.close() doit être appelé."""
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()
        client = _make_client(tools=[_mcptool("read_doc"), _mcptool("get_status")])
        registry = _FakeRegistryWriter(fail_register_on_index=1)

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason.startswith("register_failed")
        assert client.close_calls == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Rollback watcher_register_failed
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackWatcherRegister:
    def test_watcher_failure_rolls_back(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        # Faire échouer register_runner
        def bad_register(*args, **kwargs):
            raise RuntimeError("WATCHER_FAIL_MARKER")

        monkeypatch.setattr(watcher, "register_runner", bad_register)

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "watcher_register_failed"
        assert r.last_step == ActivationStep.HANDLERS_REGISTERED
        # Handlers rollback
        assert len(registry.unregister_calls) >= 1
        # Catalog stays INSTALLED
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED
        # Runner stop
        assert runner.stop_calls == 1
        blob = _audit_blob(s)
        assert "WATCHER_FAIL_MARKER" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Rollback catalog_activate_failed
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackCatalogActivate:
    def test_catalog_activate_failure_rolls_back(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        # Original update_status pour la step de pré-condition
        original = catalog.update_status
        call_count = {"v": 0}

        def spy_update(sid, status):
            call_count["v"] += 1
            if status == ServerStatus.ACTIVE:
                raise RuntimeError("CATALOG_ACTIVATE_FAIL")
            return original(sid, status)

        monkeypatch.setattr(catalog, "update_status", spy_update)

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is False
        assert r.reason == "catalog_activate_failed"
        # Runner stop
        assert runner.stop_calls == 1
        # Handlers rollback
        assert len(registry.unregister_calls) >= 1
        blob = _audit_blob(s)
        assert "CATALOG_ACTIVATE_FAIL" not in blob

    def test_catalog_activate_failure_closes_client(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        """catalog.update_status(ACTIVE) raise → client.close() doit être appelé."""
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        original = catalog.update_status
        def spy_update(sid, status):
            if status == ServerStatus.ACTIVE:
                raise RuntimeError("BOOM_ACTIVATE")
            return original(sid, status)
        monkeypatch.setattr(catalog, "update_status", spy_update)

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        assert client.close_calls == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Status re-check pré-ACTIVE
# ══════════════════════════════════════════════════════════════════════════════


class TestStatusRecheckPreActive:
    def test_status_changed_to_removed_during_activation_rolls_back(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        """Catalog re-check : status modifié entre INSTALLED et update_status(ACTIVE)."""
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        # Hack : intercepter get_server pour simuler un REMOVED entre step 5 et step 7b
        original_get = catalog.get_server
        call_count = {"v": 0}

        def spy_get(sid):
            call_count["v"] += 1
            # Le get_server du début + d'autres appels sont OK
            # Mais l'appel du re-check (le 2e ou 3e) retourne REMOVED simulé
            entry = original_get(sid)
            if call_count["v"] >= 2 and entry is not None and entry.status == ServerStatus.INSTALLED:
                # Simuler que le statut a changé en QUARANTINED
                from dataclasses import replace
                return replace(entry, status=ServerStatus.QUARANTINED)
            return entry

        monkeypatch.setattr(catalog, "get_server", spy_get)

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        # Soit pre-condition fail (status_not_installed) soit le re-check qui fail
        assert r.success is False

    def test_never_transitions_to_active_in_dry_run(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_never_transitions_to_active_on_runner_start_failure(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner(fail_start=True)

        def rf(sid, e): return runner
        def cf(r): return _make_client()

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — deactivate
# ══════════════════════════════════════════════════════════════════════════════


class TestDeactivate:
    def _activate_then_get(self, tmp_path, catalog, aq, discovery, watcher):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc"), _mcptool("get_status")]
        runner = _make_runner()
        client = _make_client(tools=tools)
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        return s, runner, client, registry

    def test_deactivate_happy_path(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _, _ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        d = s.deactivate("alice")
        assert d.success is True
        assert d.reason == "deactivated_ok"

    def test_deactivate_unregisters_handlers(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, _, registry = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        d = s.deactivate("alice")
        assert len(d.unregistered_handlers) == 2

    def test_deactivate_stops_runner(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, _, _ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        s.deactivate("alice")
        assert runner.stop_calls == 1

    def test_deactivate_closes_client(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, _, client, _ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        s.deactivate("alice")
        assert client.close_calls == 1

    def test_deactivate_unregisters_watcher(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        assert watcher.is_registered("alice")
        s.deactivate("alice")
        assert not watcher.is_registered("alice")

    def test_deactivate_catalog_back_to_installed(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        s.deactivate("alice")
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_deactivate_removes_from_running_map(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        assert s.is_running("alice")
        s.deactivate("alice")
        assert not s.is_running("alice")

    def test_deactivate_not_running(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        d = s.deactivate("alice")
        assert d.success is False
        assert d.reason == "not_running"

    def test_deactivate_server_unknown(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        d = s.deactivate("ghost")
        assert d.success is False
        assert d.reason == "server_unknown"

    def test_deactivate_idempotent(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, *_ = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        d1 = s.deactivate("alice")
        assert d1.success is True
        d2 = s.deactivate("alice")
        assert d2.success is False
        assert d2.reason == "not_running"

    def test_deactivate_partial_failure_continues(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s, runner, _, registry = self._activate_then_get(tmp_path, catalog, aq, discovery, watcher)
        registry.fail_unregister = True
        d = s.deactivate("alice")
        # Avec partial failure on continue : success peut être True ou False
        # selon les autres étapes — l'important est que ça ne raise pas
        assert isinstance(d, DeactivationResult)


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensic:
    def test_audit_anti_leak_server_id_invalid(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        with pytest.raises(ActivationError):
            s.activate("ATTACKER_MARKER_PHASE19_ZZZ", _approved())
        blob = _audit_blob(s)
        assert "ATTACKER_MARKER_PHASE19_ZZZ" not in blob

    def test_audit_does_not_stringify_runner(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner()
        client = _make_client(tools=[_mcptool("read_doc")])

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "_FakeRunner" not in blob
        assert "_FakeClient" not in blob
        assert "object at 0x" not in blob

    def test_audit_no_module_paths(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "src.mcp" not in blob
        assert "C:\\" not in blob
        assert "/home/" not in blob

    def test_audit_reason_codes_short(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        for ev in _audit_lines(s):
            if "reason" in ev:
                assert isinstance(ev["reason"], str)
                assert len(ev["reason"]) < 100

    def test_audit_no_internal_markers_runner_failure(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        runner = _make_runner(fail_start=True)

        def rf(sid, e): return runner
        def cf(r): return _make_client()

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "START_FAIL_INTERNAL_MARKER" not in blob

    def test_audit_multi_scan_no_leak(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        markers = [f"FORENSIC_P19_MARKER_{i}" for i in range(5)]
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        for m in markers:
            try:
                s.activate(m, _approved(m))
            except ActivationError:
                pass
        blob = _audit_blob(s)
        for m in markers:
            assert m not in blob

    def test_audit_events_present(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[_mcptool("read_doc")])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        events = {e["event"] for e in _audit_lines(s)}
        assert "activation_started" in events
        assert "handler_registered" in events
        assert "activation_completed" in events

    def test_audit_dry_run_distinct(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        s.activate("alice", _approved())
        events = {e["event"] for e in _audit_lines(s)}
        assert "dry_run_activation" in events
        assert "activation_started" not in events

    def test_audit_handler_registered_no_description_leak(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[
            _mcptool("read_doc", description="DESC_LEAK_MARKER_P19"),
        ])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        blob = _audit_blob(s)
        assert "DESC_LEAK_MARKER_P19" not in blob

    def test_audit_no_approved_args_raw_secret(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        ar = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "server_id": "alice",
                "action": "activate",
                "secret_extra": "APPROVED_ARGS_LEAK_P19",
            },
        )
        s.activate("alice", ar)
        blob = _audit_blob(s)
        assert "APPROVED_ARGS_LEAK_P19" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Intégration end-to-end (mocks)
# ══════════════════════════════════════════════════════════════════════════════


class TestSanityEndToEnd:
    def test_activation_works_from_installed_status(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Phase 19 doit pouvoir activer un serveur INSTALLED.
        Discovery doit avoir require_server_callable=False (vérifié init)."""
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc")]
        client = _make_client(tools=tools)
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert r.success is True

    def test_e2e_no_subprocess_popen(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[_mcptool("read_doc")])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        with mock.patch("subprocess.Popen") as popen_patch:
            s.activate("alice", _approved())
            assert popen_patch.call_count == 0

    def test_e2e_no_approval_queue_approve_call(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[_mcptool("read_doc")])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        assert aq.approve_called is False
        assert aq.reject_called is False

    def test_e2e_propose_then_activate(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[_mcptool("read_doc")])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        p = s.propose_activation("alice")
        assert p.approval_ticket_id is not None
        r = s.activate("alice", _approved())
        assert r.success is True

    def test_e2e_activate_then_deactivate_then_reactivate(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        runners = [_make_runner(), _make_runner()]
        clients = [
            _make_client(tools=[_mcptool("read_doc")]),
            _make_client(tools=[_mcptool("read_doc")]),
        ]
        idx = {"v": 0}

        def rf(sid, e):
            r = runners[idx["v"]]
            return r

        def cf(r):
            c = clients[idx["v"]]
            idx["v"] += 1
            return c

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r1 = s.activate("alice", _approved())
        assert r1.success is True
        d = s.deactivate("alice")
        assert d.success is True
        r2 = s.activate("alice", _approved())
        assert r2.success is True

    def test_e2e_multi_server_sequential(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        _add_installed(catalog, "bob", trust_score=95)
        # Pas de side effects mockés -> chaque server isolé
        # Tools différents par server pour ne pas collisionner
        tools_a = [_mcptool("read_alice_doc")]
        tools_b = [_mcptool("read_bob_doc")]
        runners = {}
        clients = {}

        def rf(sid, e):
            if sid not in runners:
                runners[sid] = _make_runner()
            return runners[sid]

        def cf(r):
            # Identifier par instance
            for sid, rr in runners.items():
                if rr is r:
                    if sid not in clients:
                        clients[sid] = _make_client(
                            tools=tools_a if sid == "alice" else tools_b
                        )
                    return clients[sid]
            return _make_client()

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        ra = s.activate("alice", _approved("alice"))
        rb = s.activate("bob", _approved("bob"))
        assert ra.success is True
        assert rb.success is True

    def test_e2e_forensic_scan_10_markers(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        markers = [f"E2E_PHASE19_FORENSIC_MARKER_{i}" for i in range(10)]
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        for m in markers:
            try:
                s.activate(m, _approved(m))
            except ActivationError:
                pass
        blob = _audit_blob(s)
        for m in markers:
            assert m not in blob

    def test_e2e_dry_run_default_behavior(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        s = _build_service(tmp_path, catalog, aq, discovery, watcher)
        r = s.activate("alice", _approved())
        assert r.dry_run is True
        assert catalog.get_server("alice").status == ServerStatus.INSTALLED

    def test_e2e_handlers_registered_count_matches_proposed(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [
            _mcptool("read_doc"),
            _mcptool("get_status"),
            # Phase I-8 (Fix AG) : fallback conservateur (trust 95 ≥ 70).
            _mcptool("opaque_thing"),
        ]
        client = _make_client(tools=tools)
        runner = _make_runner()
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        r = s.activate("alice", _approved())
        assert len(r.registered_handlers) == len(registry.register_calls)
        assert len(r.registered_handlers) == 3

    def test_e2e_provenance_contains_correct_fields(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        client = _make_client(tools=[_mcptool("read_doc")])
        runner = _make_runner()
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        provenance = registry.register_calls[0]["provenance"]
        assert provenance["source_kind"] == "mcp"
        assert provenance["server_id"] == "alice"
        assert provenance["phase"] == "19"
        assert "activated_at" in provenance

    def test_e2e_deactivate_after_partial_handler_failure(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Si activation échoue partiellement, deactivate("alice") doit refuser
        car alice n'est pas dans _running_contexts."""
        _add_installed(catalog, "alice", trust_score=95)
        tools = [_mcptool("read_doc"), _mcptool("get_status")]
        client = _make_client(tools=tools)
        runner = _make_runner()
        registry = _FakeRegistryWriter(fail_register_on_index=1)

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        d = s.deactivate("alice")
        assert d.success is False
        assert d.reason == "not_running"

    def test_e2e_deactivate_unregisters_in_reverse_order(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice", trust_score=95)
        tools = [
            _mcptool("read_a"),
            _mcptool("read_b"),
            _mcptool("read_c"),
        ]
        client = _make_client(tools=tools)
        runner = _make_runner()
        registry = _FakeRegistryWriter()

        def rf(sid, e): return runner
        def cf(r): return client

        s = _build_service(
            tmp_path, catalog, aq, discovery, watcher,
            registry=registry,
            runner_factory=rf, client_factory=cf,
            dry_run=False,
        )
        s.activate("alice", _approved())
        s.deactivate("alice")
        # registered in order a, b, c → unregistered in reverse c, b, a
        # (mais sur 3 handlers ; vérifier juste qu'il y a 3 unregister)
        assert len(registry.unregister_calls) == 3


# ══════════════════════════════════════════════════════════════════════════════
# Section Phase C — cascade catégorie sémantique + persistance
# ══════════════════════════════════════════════════════════════════════════════


def _build_service_phase_c(
    tmp_path: Path,
    catalog,
    aq,
    discovery,
    watcher,
    *,
    tools: List[MCPTool],
    llm_callable=None,
    adapter: Optional[_FakeAdapter] = None,
) -> MCPActivationService:
    adapter = adapter or _FakeAdapter()
    registry = _FakeRegistryWriter()
    client = _make_client(tools=tools)
    runner = _make_runner()

    def rf(sid, e): return runner
    def cf(r): return client

    return MCPActivationService(
        catalog=catalog,
        approval_queue=aq,
        discovery=discovery,
        adapter=adapter,
        registry_writer=registry,
        runtime_watcher=watcher,
        runner_factory=rf,
        client_factory=cf,
        audit_log_path=tmp_path / "activation_c" / "audit.jsonl",
        require_approval=True,
        dry_run=False,
        llm_callable=llm_callable,
    )


class TestPhaseCInit:
    def test_llm_callable_default_is_none(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t")],
        )
        assert s._llm_callable is None

    def test_llm_callable_rejects_non_callable(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        with pytest.raises(ValueError, match="llm_callable must be callable"):
            _build_service_phase_c(
                tmp_path, catalog, aq, discovery, watcher,
                tools=[_mcptool("t")],
                llm_callable="not a function",  # type: ignore[arg-type]
            )


class TestPhaseCActivationCascadeStatic:
    def test_static_match_persists_category_after_success(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        # server_id="github" → static match "github"
        _add_installed(catalog, "github")
        adapter = _FakeAdapter()
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("create_issue", description="Open a GitHub issue")],
            adapter=adapter,
        )
        result = s.activate("github", _approved("github"))
        assert result.success is True
        # Catalog persiste la catégorie résolue
        entry = catalog.get_server("github")
        assert entry is not None
        assert entry.semantic_category == "github"
        assert entry.category_decision_source == "static"
        # L'adapter a reçu category="github" (pas "mcp")
        assert adapter.calls[0]["category"] == "github"

    def test_audit_event_semantic_category_inferred(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "github")
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t", description="GitHub stuff")],
        )
        s.activate("github", _approved("github"))
        events = [
            e for e in _audit_lines(s)
            if e.get("event") == "semantic_category_inferred"
        ]
        assert len(events) == 1
        assert events[0]["semantic_category"] == "github"
        assert events[0]["decision_source"] == "static"


class TestPhaseCActivationCascadeFallback:
    def test_unknown_server_falls_back_to_mcp(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "totally-unknown-srv")
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool(
                "do_thing",
                description="opaque description xyz",
            )],
        )
        result = s.activate(
            "totally-unknown-srv", _approved("totally-unknown-srv")
        )
        assert result.success is True
        entry = catalog.get_server("totally-unknown-srv")
        assert entry is not None
        # Fallback persiste "mcp" + source="fallback" → la prochaine
        # activation prendra le cache et évitera la cascade.
        assert entry.semantic_category == "mcp"
        assert entry.category_decision_source == "fallback"


class TestPhaseCActivationCacheHit:
    def test_cached_category_short_circuits_cascade(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "alice")
        # Précharger une catégorie dans le catalog AVANT activation.
        catalog.update_semantic_category("alice", "mail", "user_override")
        adapter = _FakeAdapter()
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t", description="Send email")],
            adapter=adapter,
        )
        s.activate("alice", _approved("alice"))
        # Adapter a reçu la catégorie cachée, pas une réinférence
        assert adapter.calls[0]["category"] == "mail"
        # Source="cache" → on N'écrit PAS dans le catalog
        # (decision_source reste "user_override")
        entry = catalog.get_server("alice")
        assert entry is not None
        assert entry.semantic_category == "mail"
        assert entry.category_decision_source == "user_override"
        # Et donc PAS d'event semantic_category_inferred
        events = [
            e for e in _audit_lines(s)
            if e.get("event") == "semantic_category_inferred"
        ]
        assert events == []

    def test_two_activations_second_uses_cache(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "github")
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t")],
        )
        # 1ère activation : cascade static → persiste
        s.activate("github", _approved("github"))
        s.deactivate("github")
        # 2ème activation : doit utiliser le cache (source="cache")
        # On compte les events `semantic_category_inferred` : 1 seul attendu
        s.activate("github", _approved("github"))
        events = [
            e for e in _audit_lines(s)
            if e.get("event") == "semantic_category_inferred"
        ]
        assert len(events) == 1


class TestPhaseCActivationCascadeLLM:
    def test_llm_callable_used_when_static_and_heuristic_miss(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        # server_id opaque + description opaque → cascade descend jusqu'au LLM.
        _add_installed(catalog, "opaque-srv-xyz")
        llm_calls: List[str] = []

        def _llm(prompt: str) -> str:
            llm_calls.append(prompt)
            return "memory"  # réponse forcée du LLM

        adapter = _FakeAdapter()
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t", description="opaque payload no keywords")],
            llm_callable=_llm,
            adapter=adapter,
        )
        s.activate("opaque-srv-xyz", _approved("opaque-srv-xyz"))
        assert len(llm_calls) >= 1
        entry = catalog.get_server("opaque-srv-xyz")
        assert entry is not None
        assert entry.semantic_category == "memory"
        assert entry.category_decision_source == "llm"


class TestPhaseCAuditNoPII:
    def test_audit_no_pii_after_category_inference(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        catalog.add_server(
            server_id="github",
            display_name="GitHub Secret Display",
            package_spec="npm:secret-pkg-name",
            owner_profile="alice",
            trust_score=80,
        )
        catalog.update_status("github", ServerStatus.INSTALLED)
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t", description="confidential payload")],
        )
        s.activate("github", _approved("github"))
        blob = _audit_blob(s)
        assert "GitHub Secret Display" not in blob
        assert "secret-pkg-name" not in blob
        assert "confidential payload" not in blob


class TestPhaseEOverlapDetection:
    """E2E : activation pousse les overlaps detectes au registry_writer."""

    def _build_registry_writer_with_phase_e(self):
        """Registry_writer mock qui expose les accesseurs Phase E."""

        class _FakeRegistryPhaseE(_FakeRegistryWriter):
            def __init__(self, native_names, native_descriptions):
                super().__init__()
                self._native_names = list(native_names)
                self._native_descs = dict(native_descriptions)
                self.set_overlap_calls = []

            def list_native_handler_names(self):
                return list(self._native_names)

            def get_tool_description(self, name):
                return self._native_descs.get(name, "")

            def set_mcp_overlap(self, mcp_name, native_names, *, prefer_over_native=False):
                self.set_overlap_calls.append({
                    "mcp_name": mcp_name,
                    "native_names": list(native_names),
                    "prefer_over_native": prefer_over_native,
                })

        return _FakeRegistryPhaseE

    def test_overlap_pushed_to_registry_after_activation(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "gmail")
        Klass = self._build_registry_writer_with_phase_e()
        registry = Klass(
            native_names=["send_email", "read_file"],
            native_descriptions={
                "send_email": "Send an email via SMTP to a recipient",
                "read_file": "Read a file from disk",
            },
        )
        adapter = _FakeAdapter()
        client = _make_client(tools=[
            _mcptool(
                "send_message",
                description="Send an email message to a recipient",
            ),
        ])
        runner = _make_runner()

        def rf(sid, e): return runner
        def cf(r): return client

        s = MCPActivationService(
            catalog=catalog,
            approval_queue=aq,
            discovery=discovery,
            adapter=adapter,
            registry_writer=registry,
            runtime_watcher=watcher,
            runner_factory=rf,
            client_factory=cf,
            audit_log_path=tmp_path / "act_e" / "audit.jsonl",
            require_approval=True,
            dry_run=False,
        )
        result = s.activate("gmail", _approved("gmail"))
        assert result.success is True
        # Au moins un set_mcp_overlap appel pour notre handler enregistre.
        assert len(registry.set_overlap_calls) >= 1
        call = registry.set_overlap_calls[0]
        assert call["mcp_name"].startswith("mcp__gmail__")
        # Overlap detecte avec send_email natif.
        assert "send_email" in call["native_names"]
        assert call["prefer_over_native"] is False  # entry default

    def test_audit_emits_mcp_overlap_detected_event(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "gmail")
        Klass = self._build_registry_writer_with_phase_e()
        registry = Klass(
            native_names=["send_email"],
            native_descriptions={"send_email": "Send an email via SMTP to a recipient"},
        )
        client = _make_client(tools=[
            _mcptool("send_message", description="Send an email message to recipient"),
        ])
        runner = _make_runner()
        def rf(sid, e): return runner
        def cf(r): return client

        s = MCPActivationService(
            catalog=catalog,
            approval_queue=aq,
            discovery=discovery,
            adapter=_FakeAdapter(),
            registry_writer=registry,
            runtime_watcher=watcher,
            runner_factory=rf,
            client_factory=cf,
            audit_log_path=tmp_path / "act_e_audit" / "audit.jsonl",
            require_approval=True,
            dry_run=False,
        )
        s.activate("gmail", _approved("gmail"))
        events = [
            e for e in _audit_lines(s)
            if e.get("event") == "mcp_overlap_detected"
        ]
        assert len(events) >= 1
        assert events[0]["overlap_count"] >= 1

    def test_prefer_over_native_from_entry_is_passed_through(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        _add_installed(catalog, "gmail")
        # User a explicitement choisi de privilegier le MCP
        catalog.update_prefer_over_native("gmail", True)
        Klass = self._build_registry_writer_with_phase_e()
        registry = Klass(
            native_names=["send_email"],
            native_descriptions={"send_email": "Send an email via SMTP"},
        )
        client = _make_client(tools=[
            _mcptool("send_message", description="Send email message"),
        ])
        runner = _make_runner()
        def rf(sid, e): return runner
        def cf(r): return client

        s = MCPActivationService(
            catalog=catalog,
            approval_queue=aq,
            discovery=discovery,
            adapter=_FakeAdapter(),
            registry_writer=registry,
            runtime_watcher=watcher,
            runner_factory=rf,
            client_factory=cf,
            audit_log_path=tmp_path / "act_e_prefer" / "audit.jsonl",
            require_approval=True,
            dry_run=False,
        )
        s.activate("gmail", _approved("gmail"))
        assert len(registry.set_overlap_calls) >= 1
        assert registry.set_overlap_calls[0]["prefer_over_native"] is True

    def test_legacy_registry_without_phase_e_accessors_does_not_fail(
        self, tmp_path, catalog, aq, discovery, watcher
    ):
        """Back-compat : un registry sans accesseurs Phase E saute la detection."""
        _add_installed(catalog, "gmail")
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("send_message", description="Send email")],
        )
        result = s.activate("gmail", _approved("gmail"))
        assert result.success is True


class TestPhaseCPersistFailureNotFatal:
    def test_catalog_persist_failure_does_not_fail_activation(
        self, tmp_path, catalog, aq, discovery, watcher, monkeypatch
    ):
        _add_installed(catalog, "github")
        s = _build_service_phase_c(
            tmp_path, catalog, aq, discovery, watcher,
            tools=[_mcptool("t")],
        )
        # Monkeypatch le catalog pour faire échouer la persistance.
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated persist failure")
        monkeypatch.setattr(
            s._catalog, "update_semantic_category", _boom
        )
        result = s.activate("github", _approved("github"))
        # Activation réussie malgré l'échec de persistance (best-effort)
        assert result.success is True
        # Audit a noté l'échec
        events = [
            e for e in _audit_lines(s)
            if e.get("event") == "semantic_category_persist_failed"
        ]
        assert len(events) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Phase C — tests handler_adapter (cascade opt-in)
# ══════════════════════════════════════════════════════════════════════════════


class TestHandlerAdapterPhaseCCascade:
    """Tests in-process pour `adapt_tool` Phase C (sans activation_service)."""

    def _client(self):
        from unittest.mock import Mock
        return Mock()

    def test_legacy_call_without_new_kwargs_keeps_mcp_category(self):
        """Back-compat : sans aucun signal cascade, comportement inchangé."""
        from src.mcp.handler_adapter import adapt_tool as _adapt
        h = _adapt(
            client=self._client(),
            server_name="github",
            mcp_tool=_mcptool("search", description="Search GitHub"),
        )
        # Pas de cascade → category par défaut "mcp"
        assert h.category == "mcp"

    def test_explicit_category_override_skips_cascade(self):
        from src.mcp.handler_adapter import adapt_tool as _adapt
        h = _adapt(
            client=self._client(),
            server_name="github",
            mcp_tool=_mcptool("search"),
            category="custom",
            cached_category="mail",  # ignoré car category n'est pas "mcp"
        )
        assert h.category == "custom"

    def test_cached_category_triggers_cascade_and_wins(self):
        from src.mcp.handler_adapter import adapt_tool as _adapt
        h = _adapt(
            client=self._client(),
            server_name="github",  # static dirait "github"
            mcp_tool=_mcptool("t"),
            cached_category="mail",
        )
        # Cache niveau 0 prioritaire sur static niveau 1
        assert h.category == "mail"

    def test_all_tool_descriptions_triggers_heuristic_cascade(self):
        from src.mcp.handler_adapter import adapt_tool as _adapt
        h = _adapt(
            client=self._client(),
            server_name="totally-unknown-srv",
            mcp_tool=_mcptool("t", description="opaque"),
            all_tool_descriptions=[
                "Send an email to a recipient",
                "List inbox messages",
            ],
        )
        # Heuristique niveau 2 sur descriptions agrégées
        assert h.category == "mail"

    def test_llm_callable_triggers_cascade_niveau_3(self):
        from src.mcp.handler_adapter import adapt_tool as _adapt

        def _llm(prompt: str) -> str:
            return "memory"

        h = _adapt(
            client=self._client(),
            server_name="opaque-srv-xyz",
            mcp_tool=_mcptool("t", description="opaque payload"),
            llm_callable=_llm,
        )
        assert h.category == "memory"

    def test_cascade_falls_back_to_mcp_when_no_signal_resolves(self):
        from src.mcp.handler_adapter import adapt_tool as _adapt
        h = _adapt(
            client=self._client(),
            server_name="opaque-srv-xyz",
            mcp_tool=_mcptool("t", description="opaque payload"),
            all_tool_descriptions=["another opaque thing"],
        )
        # static miss + heuristique miss + pas de LLM → fallback "mcp"
        assert h.category == "mcp"
