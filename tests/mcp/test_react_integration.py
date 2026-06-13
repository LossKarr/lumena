import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.mcp.react_integration import (
    AUTONOMY_CONFIRMATION_PHRASE,
    CAPABILITY_TOOL_NAME,
    MCPReActIntegration,
    MCPReActIntegrationDeps,
    MCP_LOOP_CATEGORY,
    RESUME_TASK_TOOL_NAME,
    RUN_AUTONOMY_TOOL_NAME,
    Phase26RegistrationError,
    TICKET_CONFIRMATION_PHRASE,
    TICKET_TOOL_NAME,
    _PHASE26_CAPABILITY_PARAMS,
    _PHASE26_TICKET_PARAMS,
    _register_phase26_native_handler,
    make_phase26_snapshot,
    phase26_snapshot_as_dict,
)
from src.mcp.approval_queue import ApprovalDecision, ApprovalResult


class FakeRegistry:
    def __init__(self):
        self.tools = {}
        self._native_handler_names = frozenset({"read_file"})
        self._dynamic_handlers = {}
        self._dynamic_policies = {}
        self._tool_modules = {}
        self._sig_cache = {}
        self._tools_desc_cache = "stale"
        self._tool_collection = object()

    def register(self, name, description, parameters, handler):
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def is_dynamic_handler(self, name):
        return name in self._dynamic_handlers

    def get_dynamic_handler_policy(self, name):
        return self._dynamic_policies.get(name)

    def get_tools_schema(self):
        schemas = []
        for name, tool in self.tools.items():
            required = tool.get("required", None)
            if required is None:
                required = list(tool["parameters"].keys())
            else:
                required = list(required)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"],
                        "parameters": {
                            "type": "object",
                            "properties": tool["parameters"],
                            "required": required,
                        },
                    },
                }
            )
        return schemas


class FakePhase24:
    def __init__(self, decision="ready_to_use_existing_capability"):
        self.decision = SimpleNamespace(value=decision)
        self.action = SimpleNamespace(
            kind=SimpleNamespace(value="use_existing"),
            target_server_id="github_srv",
            target_tool_name="search",
            proposed_risk_summary="none",
            requires_admin_nod=False,
        )
        self.evidence = {"actionable_intent": True}
        self.blockers = ()


class FakeAutonomousPlanner:
    def __init__(self, plan=None):
        self.plan = plan or FakePhase24()
        self.calls = []

    def plan_for_intent(self, intent, **kwargs):
        self.calls.append((intent, kwargs))
        return self.plan


class FakeBridge:
    def __init__(
        self,
        decision="ticket_proposed",
        *,
        action_kind="propose_install",
        risk_summary="install_required",
        ticket_id="",
    ):
        self.plan = SimpleNamespace(
            decision=SimpleNamespace(value=decision),
            action=SimpleNamespace(
                kind=SimpleNamespace(value=action_kind),
                target_server_id="github_srv",
                risk_summary=risk_summary,
                proposed_ticket_action_id=ticket_id,
            ),
            dry_run=True,
            evidence={"live_mode_enabled": False},
            blockers=(),
        )
        self.calls = []
        self.execute_calls = []

    def request_action_for_plan(self, plan, **kwargs):
        self.calls.append((plan, kwargs))
        return self.plan

    def execute_after_approval(self, action_id, approval_result, server_id, **kwargs):
        self.execute_calls.append((action_id, approval_result, server_id, kwargs))
        decision = (
            "executed_success_activate"
            if self.plan.action.risk_summary == "activation_required"
            else "executed_success_install"
        )
        return SimpleNamespace(
            decision=SimpleNamespace(value=decision),
            action=SimpleNamespace(
                kind=SimpleNamespace(value="execute_install"),
                target_server_id=server_id,
                risk_summary=self.plan.action.risk_summary,
                proposed_ticket_action_id=action_id,
            ),
            dry_run=False,
            evidence={"live_mode_enabled": True},
            blockers=(),
        )


class FakeLocalCreationOrchestrator:
    def __init__(self, ticket_id="e9953b6ad4ae430a8e1d4bf425f55e29"):
        self.ticket_id = ticket_id
        self.calls = []

    def propose_local_creation(self, intent, **kwargs):
        self.calls.append((intent, kwargs))
        return SimpleNamespace(
            approval_ticket_id=None if kwargs.get("dry_run") else self.ticket_id,
            suggested_server_id="airtable_e9953b6a",
            tool_name="mcp_local_create:airtable_e9953b6a",
            risk_summary="local_creation_required",
            dry_run=bool(kwargs.get("dry_run")),
        )


class FakeApprovalQueueAuto:
    def __init__(self, *, tool_name="mcp_install:github_srv", args=None):
        self.tool_name = tool_name
        self.args = args or {
            "server_id": "github_srv",
            "transport": "npm",
            "package_name": "@modelcontextprotocol/server-github",
            "package_spec": "npm:@modelcontextprotocol/server-github",
            "version": None,
            "trust_score": 90,
        }
        self.approve_if_calls = []

    def get(self, action_id):
        return SimpleNamespace(id=action_id, tool_name=self.tool_name)

    def approve_if(self, action_id, evaluator):
        self.approve_if_calls.append(action_id)
        request = SimpleNamespace(
            id=action_id,
            tool_name=self.tool_name,
            policy=SimpleNamespace(value="external_write_recoverable"),
            caller_kind="react",
            risk_summary="mcp_install:npm:90",
            args=dict(self.args),
        )
        if evaluator(request):
            return ApprovalResult(
                decision=ApprovalDecision.APPROVED,
                args=dict(self.args),
                reason="auto_approved",
            )
        return ApprovalResult(
            decision=ApprovalDecision.PENDING,
            reason="auto_approve_not_matched",
        )


class FakeAutoApproveEngine:
    def __init__(self, *, matched=True):
        self.matched = matched
        self.calls = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            decision=SimpleNamespace(value="matched" if self.matched else "no_match")
        )


class MultiApprovalQueueAuto:
    def __init__(self, tickets):
        self.tickets = dict(tickets)
        self.approve_if_calls = []

    def get(self, action_id):
        item = self.tickets.get(action_id)
        if item is None:
            return None
        return SimpleNamespace(id=action_id, tool_name=item["tool_name"])

    def approve_if(self, action_id, evaluator):
        self.approve_if_calls.append(action_id)
        item = self.tickets[action_id]
        request = SimpleNamespace(
            id=action_id,
            tool_name=item["tool_name"],
            policy=SimpleNamespace(value=item.get("policy", "local_write")),
            caller_kind="react",
            risk_summary=item.get("risk_summary", "none"),
            args=dict(item["args"]),
        )
        if evaluator(request):
            return ApprovalResult(
                decision=ApprovalDecision.APPROVED,
                args=dict(item["args"]),
                reason="auto_approved",
            )
        return ApprovalResult(
            decision=ApprovalDecision.PENDING,
            reason="auto_approve_not_matched",
        )


class SequencedAutonomousPlanner:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    def plan_for_intent(self, intent, **kwargs):
        self.calls.append((intent, kwargs))
        decision = self.decisions[min(len(self.calls) - 1, len(self.decisions) - 1)]
        return FakePhase24(decision)


class SequencedBridge:
    def __init__(self, ticket_ids):
        self.ticket_ids = dict(ticket_ids)
        self.calls = []
        self.execute_calls = []

    def request_action_for_plan(self, plan, **kwargs):
        self.calls.append((plan, kwargs))
        decision = getattr(getattr(plan, "decision", None), "value", "")
        mapping = {
            "needs_catalog_approval": ("propose_catalog_add", "catalog_add_required"),
            "needs_install_approval": ("propose_install", "install_required"),
            "needs_activation_approval": ("propose_activation", "activation_required"),
        }
        action_kind, risk_summary = mapping[decision]
        return SimpleNamespace(
            decision=SimpleNamespace(value="ticket_proposed"),
            action=SimpleNamespace(
                kind=SimpleNamespace(value=action_kind),
                target_server_id="github_srv",
                risk_summary=risk_summary,
                proposed_ticket_action_id=self.ticket_ids[risk_summary],
            ),
            dry_run=False,
            evidence={"live_mode_enabled": True},
            blockers=(),
        )

    def execute_after_approval(self, action_id, approval_result, server_id, **kwargs):
        self.execute_calls.append((action_id, approval_result, server_id, kwargs))
        action = getattr(approval_result, "args", {}).get("action")
        if action == "catalog_add":
            decision = "executed_success_catalog_add"
            kind = "execute_catalog_add"
            risk = "catalog_add_required"
        elif action == "activate":
            decision = "executed_success_activate"
            kind = "execute_activation"
            risk = "activation_required"
        else:
            decision = "executed_success_install"
            kind = "execute_install"
            risk = "install_required"
        return SimpleNamespace(
            decision=SimpleNamespace(value=decision),
            action=SimpleNamespace(
                kind=SimpleNamespace(value=kind),
                target_server_id=server_id,
                risk_summary=risk,
                proposed_ticket_action_id=action_id,
            ),
            dry_run=False,
            evidence={"live_mode_enabled": True},
            blockers=(),
        )


class FakeLocalCreationExecutor:
    def __init__(self, *, success=True):
        self.success = success
        self.calls = []

    def execute_approved_local_creation(self, approval_result, **kwargs):
        self.calls.append((approval_result, kwargs))
        return SimpleNamespace(
            success=self.success,
            catalog_status="declared" if self.success else None,
        )


def _chain(registry=None, planner=None, bridge=None):
    return SimpleNamespace(
        tool_registry=registry or FakeRegistry(),
        autonomous_planner=planner or FakeAutonomousPlanner(),
        execution_bridge=bridge or FakeBridge(),
    )


def _payload(raw):
    return json.loads(raw)


def test_register_phase26_native_handler_sets_required_category_and_caches():
    registry = FakeRegistry()
    _register_phase26_native_handler(
        registry,
        name=CAPABILITY_TOOL_NAME,
        description="desc",
        parameters=_PHASE26_CAPABILITY_PARAMS,
        required=("intent",),
        handler=lambda **kw: "ok",
    )

    assert registry.tools[CAPABILITY_TOOL_NAME]["required"] == ["intent"]
    assert registry._tool_modules[CAPABILITY_TOOL_NAME] == MCP_LOOP_CATEGORY
    assert registry._sig_cache[CAPABILITY_TOOL_NAME] == (True, None)
    assert registry._tools_desc_cache is None
    assert registry._tool_collection is None
    assert not registry.is_dynamic_handler(CAPABILITY_TOOL_NAME)
    assert registry.get_dynamic_handler_policy(CAPABILITY_TOOL_NAME) is None


def test_register_phase26_native_handler_refuses_native_collision():
    registry = FakeRegistry()
    with pytest.raises(Phase26RegistrationError) as exc:
        _register_phase26_native_handler(
            registry,
            name="read_file",
            description="desc",
            parameters={},
            required=(),
            handler=lambda **kw: "ok",
        )
    assert str(exc.value) == "collision_with_native_handler"


def test_register_phase26_native_handler_refuses_existing_tool_collision():
    registry = FakeRegistry()
    registry.tools[CAPABILITY_TOOL_NAME] = {"handler": lambda **kw: "old"}
    with pytest.raises(Phase26RegistrationError) as exc:
        _register_phase26_native_handler(
            registry,
            name=CAPABILITY_TOOL_NAME,
            description="desc",
            parameters={},
            required=(),
            handler=lambda **kw: "new",
        )
    assert str(exc.value) == "collision_with_existing_tool"


def test_get_tools_schema_required_distinguishes_absent_and_empty():
    registry = FakeRegistry()
    registry.register("legacy", "legacy", {"a": {}, "b": {}}, lambda **kw: "ok")
    registry.register("none_required", "none", {"a": {}, "b": {}}, lambda **kw: "ok")
    registry.tools["none_required"]["required"] = []
    registry.register("some_required", "some", {"a": {}, "b": {}}, lambda **kw: "ok")
    registry.tools["some_required"]["required"] = ["a"]

    schemas = {s["function"]["name"]: s["function"]["parameters"]["required"] for s in registry.get_tools_schema()}

    assert schemas["legacy"] == ["a", "b"]
    assert schemas["none_required"] == []
    assert schemas["some_required"] == ["a"]


def test_attach_to_tool_registry_registers_two_native_handlers():
    registry = FakeRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))

    attached, reason = integration.attach_to_tool_registry(registry)

    assert (attached, reason) == (True, "attached")
    assert CAPABILITY_TOOL_NAME in registry.tools
    assert TICKET_TOOL_NAME in registry.tools
    assert RUN_AUTONOMY_TOOL_NAME in registry.tools
    assert RESUME_TASK_TOOL_NAME in registry.tools
    assert registry._tool_modules[CAPABILITY_TOOL_NAME] == MCP_LOOP_CATEGORY
    assert registry._tool_modules[TICKET_TOOL_NAME] == MCP_LOOP_CATEGORY
    assert registry._tool_modules[RUN_AUTONOMY_TOOL_NAME] == MCP_LOOP_CATEGORY
    assert registry._tool_modules[RESUME_TASK_TOOL_NAME] == MCP_LOOP_CATEGORY
    assert registry.tools[TICKET_TOOL_NAME]["required"] == ["intent", "confirmation_phrase"]
    assert registry.tools[RUN_AUTONOMY_TOOL_NAME]["required"] == ["intent"]
    assert registry.tools[RESUME_TASK_TOOL_NAME]["required"] == ["intent"]
    assert not registry.is_dynamic_handler(TICKET_TOOL_NAME)


def test_attach_to_tool_registry_is_idempotent():
    registry = FakeRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))

    assert integration.attach_to_tool_registry(registry) == (True, "attached")
    assert integration.attach_to_tool_registry(registry) == (False, "already_attached")


def test_attach_to_tool_registry_invalid_registry_is_fail_safe():
    integration = MCPReActIntegration(MCPReActIntegrationDeps())
    assert integration.attach_to_tool_registry(None) == (False, "registry_invalid")
    assert integration.attach_to_tool_registry(object()) == (False, "registry_invalid")


def test_build_chain_uses_offline_and_network_sources_by_default(monkeypatch):
    monkeypatch.delenv("LUMENA_MCP_NETWORK_SEARCH_ENABLED", raising=False)
    registry = FakeRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))

    chain = integration._build_chain_for_registry(registry)
    sources = chain.autonomous_planner._deps.proposal_planner._deps.sources
    names = [s.name for s in sources]

    assert "curated" in names
    assert "npm_registry" in names
    assert "pypi_project_lookup" in names
    assert "smithery_directory" in names
    assert "pulsemcp_directory" in names
    assert "github_web_search" in names
    for source in sources:
        if getattr(source, "is_network", False):
            assert source.network_enabled is True


def test_build_chain_can_disable_network_sources(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_NETWORK_SEARCH_ENABLED", "0")
    registry = FakeRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))

    chain = integration._build_chain_for_registry(registry)
    sources = chain.autonomous_planner._deps.proposal_planner._deps.sources

    network_sources = [s for s in sources if getattr(s, "is_network", False)]
    assert network_sources
    assert all(s.network_enabled is False for s in network_sources)


def test_handle_capability_blocks_without_caller_context():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_request_mcp_capability(
        "chercher un outil externe pour github",
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["caller_kind_not_allowed"]


def test_handle_capability_blocks_code_agent():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_request_mcp_capability(
        "chercher un outil externe pour github",
        caller_kind="code_agent",
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["code_agent_out_of_scope"]


def test_handle_capability_react_ok_maps_phase24_plan():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    planner = FakeAutonomousPlanner(FakePhase24("ready_to_use_existing_capability"))
    raw = integration.handle_request_mcp_capability(
        "chercher un outil externe pour github",
        caller_kind="react",
        chain=_chain(planner=planner),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "use_existing"
    assert data["payload"]["target_server_id"] == "github_srv"
    assert planner.calls[0][1]["caller_kind"] == "react"


def test_handle_ticket_subagent_blocked_even_with_confirmation():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_request_mcp_ticket(
        "chercher un outil externe pour github",
        caller_kind="research_agent",
        confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["caller_kind_not_allowed"]


def test_handle_ticket_confirmation_required():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_request_mcp_ticket(
        "chercher un outil externe pour github",
        caller_kind="react",
        confirmation_phrase="wrong",
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["confirmation_phrase_invalid"]


def test_handle_ticket_dry_run_calls_bridge_only_for_request_action():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    bridge = FakeBridge("ticket_would_be_proposed")
    raw = integration.handle_request_mcp_ticket(
        "chercher un outil externe pour github",
        caller_kind="react",
        confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
        chain=_chain(bridge=bridge),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "ticket_would_be_proposed"
    assert bridge.calls[0][1]["dry_run"] is True
    assert bridge.execute_calls == []


def test_handle_ticket_live_without_env_is_blocked(monkeypatch):
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_request_mcp_ticket(
        "chercher un outil externe pour github",
        caller_kind="react",
        confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
        live=True,
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["live_requirements_not_met"]


def test_handle_ticket_local_creation_live_creates_real_pending_ticket(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    local_orchestrator = FakeLocalCreationOrchestrator()
    integration = MCPReActIntegration(
        MCPReActIntegrationDeps(
            catalog=object(),
            local_creation_orchestrator=local_orchestrator,
        )
    )
    bridge = FakeBridge(
        "ticket_descriptive_only",
        action_kind="none",
        risk_summary="local_creation_required",
    )

    raw = integration.handle_request_mcp_ticket(
        "connecter Airtable pour lire les bases et ecrire des lignes",
        caller_kind="react",
        confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
        live=True,
        chain=_chain(bridge=bridge),
    )

    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "ticket_proposed"
    assert data["payload"]["mapped_decision"] == "ticket_proposed"
    assert data["payload"]["action_kind"] == "local_create"
    assert data["payload"]["risk_summary"] == "local_creation_required"
    assert data["payload"]["proposed_ticket_action_id"] == (
        "e9953b6ad4ae430a8e1d4bf425f55e29"
    )
    assert data["payload"]["target_server_id"] == "airtable_e9953b6a"
    assert local_orchestrator.calls[0][1]["dry_run"] is False


def test_handle_ticket_local_creation_without_orchestrator_blocks(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    bridge = FakeBridge(
        "ticket_descriptive_only",
        action_kind="none",
        risk_summary="local_creation_required",
    )

    raw = integration.handle_request_mcp_ticket(
        "connecter Airtable pour lire les bases et ecrire des lignes",
        caller_kind="react",
        confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
        live=True,
        chain=_chain(bridge=bridge),
    )

    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["phase25_unavailable"]


def test_run_mcp_autonomy_ready_to_use_returns_target_tool():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        chain=_chain(planner=FakeAutonomousPlanner(FakePhase24("ready_to_use_existing_capability"))),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "autonomy_ready_to_use"
    assert data["payload"]["next_step"] == "call_target_tool"
    assert data["payload"]["target_tool_name"] == "search"


def test_run_mcp_autonomy_live_requires_autonomy_phrase(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        live=True,
        confirmation_phrase="wrong",
        chain=_chain(),
    )
    data = _payload(raw)
    assert data["decision"] == "blocked"
    assert data["blockers"] == ["confirmation_phrase_invalid"]


def test_run_mcp_autonomy_dry_run_proposes_next_step():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    bridge = FakeBridge("ticket_would_be_proposed")
    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        chain=_chain(
            planner=FakeAutonomousPlanner(FakePhase24("needs_install_approval")),
            bridge=bridge,
        ),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "autonomy_would_run"
    assert data["payload"]["next_step"] == "enable_live_or_confirm"


def test_run_mcp_autonomy_live_can_create_ticket(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE", raising=False)
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    bridge = FakeBridge(
        "ticket_proposed",
        ticket_id="e9953b6ad4ae430a8e1d4bf425f55e29",
    )
    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        live=True,
        confirmation_phrase=AUTONOMY_CONFIRMATION_PHRASE,
        chain=_chain(
            planner=FakeAutonomousPlanner(FakePhase24("needs_install_approval")),
            bridge=bridge,
        ),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "autonomy_ticket_created"
    assert data["payload"]["next_step"] == "approve_ticket_then_resume"


def test_run_mcp_autonomy_live_autoapproves_and_executes_install(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE", "1")
    ticket_id = "e9953b6ad4ae430a8e1d4bf425f55e29"
    bridge = FakeBridge("ticket_proposed", ticket_id=ticket_id)
    approval_queue = FakeApprovalQueueAuto()
    auto_engine = FakeAutoApproveEngine(matched=True)
    integration = MCPReActIntegration(
        MCPReActIntegrationDeps(
            catalog=object(),
            approval_queue=approval_queue,
            auto_approve_engine=auto_engine,
        )
    )

    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        live=True,
        confirmation_phrase=AUTONOMY_CONFIRMATION_PHRASE,
        chain=_chain(
            planner=FakeAutonomousPlanner(FakePhase24("needs_install_approval")),
            bridge=bridge,
        ),
    )

    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "auto_loop_exhausted"
    assert approval_queue.approve_if_calls == [ticket_id]
    assert auto_engine.calls[0]["tool_name"] == "mcp_install:github_srv"
    assert bridge.execute_calls[0][0] == ticket_id
    assert bridge.execute_calls[0][2] == "github_srv"


def test_run_mcp_autonomy_autoapprove_not_matched_keeps_ticket(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE", "1")
    ticket_id = "e9953b6ad4ae430a8e1d4bf425f55e29"
    bridge = FakeBridge("ticket_proposed", ticket_id=ticket_id)
    approval_queue = FakeApprovalQueueAuto()
    auto_engine = FakeAutoApproveEngine(matched=False)
    integration = MCPReActIntegration(
        MCPReActIntegrationDeps(
            catalog=object(),
            approval_queue=approval_queue,
            auto_approve_engine=auto_engine,
        )
    )

    raw = integration.handle_run_mcp_autonomy(
        "chercher un outil externe pour github",
        caller_kind="react",
        live=True,
        confirmation_phrase=AUTONOMY_CONFIRMATION_PHRASE,
        chain=_chain(
            planner=FakeAutonomousPlanner(FakePhase24("needs_install_approval")),
            bridge=bridge,
        ),
    )

    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "auto_approve_not_matched"
    assert data["payload"]["next_step"] == "approve_ticket_then_resume"
    assert approval_queue.approve_if_calls == [ticket_id]
    assert bridge.execute_calls == []


def test_run_mcp_autonomy_autoapproves_local_creation(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE", "1")
    approval_queue = FakeApprovalQueueAuto(
        tool_name="mcp_local_create:airtable_e9953b6a",
        args={
            "server_id": "airtable_e9953b6a",
            "action": "local_create",
            "intent": "connecter Airtable pour lire les bases et ecrire des lignes",
            "intent_hash": "a" * 64,
        },
    )
    auto_engine = FakeAutoApproveEngine(matched=True)
    local_orchestrator = FakeLocalCreationOrchestrator()
    local_executor = FakeLocalCreationExecutor(success=True)
    integration = MCPReActIntegration(
        MCPReActIntegrationDeps(
            catalog=object(),
            approval_queue=approval_queue,
            local_creation_orchestrator=local_orchestrator,
            local_creation_executor=local_executor,
            auto_approve_engine=auto_engine,
        )
    )
    bridge = FakeBridge(
        "ticket_descriptive_only",
        action_kind="none",
        risk_summary="local_creation_required",
    )

    raw = integration.handle_run_mcp_autonomy(
        "connecter Airtable pour lire les bases et ecrire des lignes",
        caller_kind="react",
        live=True,
        confirmation_phrase=AUTONOMY_CONFIRMATION_PHRASE,
        chain=_chain(
            planner=FakeAutonomousPlanner(FakePhase24("needs_local_creation")),
            bridge=bridge,
        ),
    )

    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "auto_loop_exhausted"
    assert approval_queue.approve_if_calls == [
        "e9953b6ad4ae430a8e1d4bf425f55e29",
    ]
    assert local_executor.calls[0][1]["server_id"] == "airtable_e9953b6a"
    assert local_executor.calls[0][1]["dry_run"] is False


def test_run_mcp_autonomy_autoapproves_catalog_install_activation_chain(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE", "1")
    ticket_ids = {
        "catalog_add_required": "e9953b6ad4ae430a8e1d4bf425f55e29",
        "install_required": "d6576736fd314dfda42d47b6e78f67d3",
        "activation_required": "b9cbff08fc9a492680c74a8b80b113e7",
    }
    approval_queue = MultiApprovalQueueAuto({
        ticket_ids["catalog_add_required"]: {
            "tool_name": "mcp_catalog_add:github_srv",
            "risk_summary": "catalog_add_required",
            "args": {
                "action": "catalog_add",
                "server_id": "github_srv",
                "display_name": "GitHub MCP",
                "package_spec": "npm:@modelcontextprotocol/server-github",
                "version": None,
                "trust_score": 90,
                "owner_profile": "lumena",
            },
        },
        ticket_ids["install_required"]: {
            "tool_name": "mcp_install:github_srv",
            "risk_summary": "install_required",
            "args": {
                "server_id": "github_srv",
                "transport": "npm",
                "package_name": "@modelcontextprotocol/server-github",
                "package_spec": "npm:@modelcontextprotocol/server-github",
                "version": None,
                "trust_score": 90,
            },
        },
        ticket_ids["activation_required"]: {
            "tool_name": "mcp_activate:github_srv",
            "risk_summary": "activation_required",
            "args": {"server_id": "github_srv", "action": "activate"},
        },
    })
    auto_engine = FakeAutoApproveEngine(matched=True)
    integration = MCPReActIntegration(
        MCPReActIntegrationDeps(
            catalog=object(),
            approval_queue=approval_queue,
            auto_approve_engine=auto_engine,
        )
    )
    planner = SequencedAutonomousPlanner([
        "needs_catalog_approval",
        "needs_catalog_approval",
        "needs_install_approval",
        "needs_install_approval",
        "needs_activation_approval",
        "needs_activation_approval",
        "ready_to_use_existing_capability",
    ])
    bridge = SequencedBridge(ticket_ids)

    raw = integration.handle_run_mcp_autonomy(
        "trouve et connecte un MCP GitHub",
        caller_kind="react",
        live=True,
        confirmation_phrase=AUTONOMY_CONFIRMATION_PHRASE,
        chain=_chain(planner=planner, bridge=bridge),
    )

    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "autonomy_ready_to_use"
    assert data["payload"]["next_step"] == "call_target_tool"
    assert data["payload"]["autonomy_steps"] == 4
    assert approval_queue.approve_if_calls == [
        ticket_ids["catalog_add_required"],
        ticket_ids["install_required"],
        ticket_ids["activation_required"],
    ]
    assert [c[0] for c in bridge.execute_calls] == [
        ticket_ids["catalog_add_required"],
        ticket_ids["install_required"],
        ticket_ids["activation_required"],
    ]
    assert [c["tool_name"] for c in auto_engine.calls] == [
        "mcp_catalog_add:github_srv",
        "mcp_install:github_srv",
        "mcp_activate:github_srv",
    ]


def test_resume_mcp_task_ready_to_use():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    raw = integration.handle_resume_mcp_task(
        "chercher un outil externe pour github",
        caller_kind="react",
        chain=_chain(planner=FakeAutonomousPlanner(FakePhase24("ready_to_use_existing_capability"))),
    )
    data = _payload(raw)
    assert data["decision"] == "ok"
    assert data["payload"]["recommendation_code"] == "resume_ready_to_use"
    assert data["payload"]["next_step"] == "call_target_tool"


def test_missing_catalog_blocks_handlers():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=None))
    raw = integration.handle_request_mcp_capability(
        "chercher un outil externe pour github",
        caller_kind="react",
        chain=_chain(),
    )
    assert _payload(raw)["blockers"] == ["phase24_unavailable"]


def test_snapshot_is_tuple_and_returns_fresh_dict():
    snapshot = make_phase26_snapshot(
        {
            "phase24_decision": "ready_to_use_existing_capability",
            "target_server_id": "github_srv",
            "raw_secret": "SECRET_PHASE24_CANDIDATE_RAW_LEAK",
        }
    )
    assert snapshot == (
        ("phase24_decision", "ready_to_use_existing_capability"),
        ("target_server_id", "github_srv"),
    )
    first = phase26_snapshot_as_dict(snapshot)
    second = phase26_snapshot_as_dict(snapshot)
    first["target_server_id"] = "mutated"
    assert second["target_server_id"] == "github_srv"


def test_no_forbidden_runtime_tokens_in_module_source():
    text = Path("src/mcp/react_integration.py").read_text(encoding="utf-8")
    forbidden = [
        "register_dynamic_handler(",
        ".approve(",
        ".reject(",
        "MCPPolicy",
        "SecretsService",
        "subprocess",
        "from web.routes import deps",
    ]
    for token in forbidden:
        assert token not in text


def test_allowed_phase22_to_25_imports_present():
    text = Path("src/mcp/react_integration.py").read_text(encoding="utf-8")
    assert "from src.mcp.capability_resolver import" in text
    assert "from src.mcp.proposal_planner import" in text
    assert "from src.mcp.autonomous_orchestrator import" in text
    assert "from src.mcp.execution_bridge import" in text
    assert "execute_after_approval(" in text
    assert ".approve_if(" in text
