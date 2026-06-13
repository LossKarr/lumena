"""
Tests Phase 17 v3 — MCPDiscoveryService.

Sections :
  1. Init & configuration
  2. Validation server_id (anti-leak)
  3. Catalog gates (unknown / not_callable / REMOVED)
  4. initialize / list_tools failures + is_initialized
  5. Defensive MCPTool validation
  6. Anti-confused-deputy (production behavior)
  7. PolicyAttributor integration
  8. DiscoveryReport invariants
  9. Persistance disque + JSON sérialisation
  10. has_/length helpers (audit booléens + entiers structurels)
  11. Audit forensique no-PII
  12. Sanity end-to-end (mocks)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.client import MCPTool
from src.mcp.discovery import (
    DiscoveryError,
    DiscoveryReport,
    MCPDiscoveryService,
    ToolDiscoveryProposal,
    _proposal_to_dict,
    _report_to_dict,
)
from src.mcp.policy import MCPPolicy
from src.mcp.policy_attributor import (
    AttributionDecision,
    PolicyAttributor,
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


class _FakeClient:
    """MCPClient-like : expose is_initialized + initialize + list_tools."""

    def __init__(
        self,
        tools: Optional[List[Any]] = None,
        already_initialized: bool = False,
    ):
        self._is_initialized = already_initialized
        self._tools = tools or []
        self.init_call_count = 0
        self.list_tools_call_count = 0
        self.init_raises = False
        self.list_tools_raises = False
        self.list_tools_returns_non_list = False

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    def initialize(self) -> Any:
        self.init_call_count += 1
        if self.init_raises:
            raise RuntimeError("INIT_FAIL_INTERNAL_DETAIL_MARKER")
        self._is_initialized = True
        return {"capabilities": {}}

    def list_tools(self) -> List[MCPTool]:
        self.list_tools_call_count += 1
        if self.list_tools_raises:
            raise RuntimeError("LIST_TOOLS_FAIL_INTERNAL_DETAIL_MARKER")
        if self.list_tools_returns_non_list:
            return "not_a_list"  # type: ignore[return-value]
        return self._tools


def _mcptool(
    name: str = "read_doc",
    description: str = "",
    input_schema: Optional[Dict[str, Any]] = None,
) -> MCPTool:
    return MCPTool(
        name=name,
        description=description,
        input_schema=input_schema if input_schema is not None else {},
    )


@pytest.fixture
def catalog(tmp_path: Path) -> MCPServerCatalog:
    return MCPServerCatalog(
        catalog_dir=tmp_path / "catalog",
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
        secrets_service=_InMemorySecretsService(),
    )


@pytest.fixture
def attributor(tmp_path: Path) -> PolicyAttributor:
    return PolicyAttributor(
        audit_log_path=tmp_path / "attributor" / "audit.jsonl",
    )


@pytest.fixture
def service(tmp_path: Path, catalog, attributor) -> MCPDiscoveryService:
    return MCPDiscoveryService(
        catalog=catalog,
        attributor=attributor,
        audit_log_path=tmp_path / "discovery" / "audit.jsonl",
        reports_dir=tmp_path / "discovery" / "reports",
        require_server_callable=True,
        persist_reports=False,
    )


def _add_active(catalog: MCPServerCatalog, server_id: str = "alice",
                trust_score: Optional[int] = None) -> None:
    catalog.add_server(
        server_id=server_id,
        display_name="Alice",
        package_spec="npm:mcp-foo",
        owner_profile="alice",
        trust_score=trust_score,
    )
    catalog.update_status(server_id, ServerStatus.INSTALLED)
    catalog.update_status(server_id, ServerStatus.ACTIVE)


def _audit_lines(service: MCPDiscoveryService) -> List[Dict[str, Any]]:
    if not service.audit_log_path.exists():
        return []
    out = []
    with open(service.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(service: MCPDiscoveryService) -> str:
    if not service.audit_log_path.exists():
        return ""
    return service.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_audit_dir_created(self, service):
        assert service.audit_log_path.parent.exists()

    def test_default_require_server_callable_true(self, tmp_path, catalog, attributor):
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert s.require_server_callable is True

    def test_default_persist_reports_false(self, tmp_path, catalog, attributor):
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert s.persist_reports is False

    def test_init_rejects_none_catalog(self, tmp_path, attributor):
        with pytest.raises(ValueError, match="catalog"):
            MCPDiscoveryService(
                catalog=None,  # type: ignore
                attributor=attributor,
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_attributor_without_attribute_method(self, tmp_path, catalog):
        class NoAttribute:
            pass
        with pytest.raises(ValueError, match="attribute"):
            MCPDiscoveryService(
                catalog=catalog, attributor=NoAttribute(),  # type: ignore
                audit_log_path=tmp_path / "audit.jsonl",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Validation server_id (anti-leak)
# ══════════════════════════════════════════════════════════════════════════════


class TestServerIdValidation:
    def test_uppercase_rejected_no_leak(self, service):
        client = _FakeClient()
        with pytest.raises(DiscoveryError, match="server_id_invalid"):
            service.discover("ALICE_ATTACKER_MARKER", client)
        blob = _audit_blob(service)
        assert "ALICE_ATTACKER_MARKER" not in blob

    def test_windows_reserved_rejected(self, service):
        with pytest.raises(DiscoveryError):
            service.discover("con", _FakeClient())

    def test_path_traversal_rejected(self, service):
        with pytest.raises(DiscoveryError):
            service.discover("foo..bar", _FakeClient())

    def test_none_rejected(self, service):
        with pytest.raises(DiscoveryError):
            service.discover(None, _FakeClient())  # type: ignore

    def test_empty_rejected(self, service):
        with pytest.raises(DiscoveryError):
            service.discover("", _FakeClient())

    def test_invalid_server_id_audit_does_not_log_server_id(self, service):
        client = _FakeClient()
        with pytest.raises(DiscoveryError):
            service.discover("ATTACKER_SERVER_ID_SECRET_MARKER", client)
        events = [e for e in _audit_lines(service) if e["event"] == "server_id_invalid"]
        assert events
        ev = events[-1]
        assert "server_id" not in ev


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Catalog gates
# ══════════════════════════════════════════════════════════════════════════════


class TestCatalogGates:
    def test_server_unknown(self, service):
        with pytest.raises(DiscoveryError, match="server_unknown"):
            service.discover("ghost", _FakeClient())

    def test_server_removed_with_require_callable_true(self, service, catalog):
        _add_active(catalog, "alice")
        catalog.remove_server("alice")
        with pytest.raises(DiscoveryError, match="server_not_callable"):
            service.discover("alice", _FakeClient())

    def test_server_active_continues(self, service, catalog):
        _add_active(catalog, "alice")
        report = service.discover("alice", _FakeClient(tools=[]))
        assert report.discovered_count == 0

    def test_server_declared_with_require_callable_false(
        self, tmp_path, catalog, attributor
    ):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            require_server_callable=False,
        )
        report = s.discover("alice", _FakeClient(tools=[]))
        assert report.discovered_count == 0

    def test_server_removed_blocked_even_with_require_callable_false(
        self, tmp_path, catalog, attributor
    ):
        _add_active(catalog, "alice")
        catalog.remove_server("alice")
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            require_server_callable=False,
        )
        with pytest.raises(DiscoveryError, match="server_not_callable"):
            s.discover("alice", _FakeClient())

    def test_audit_logs_status_for_failures(self, service, catalog):
        _add_active(catalog, "alice")
        catalog.update_status("alice", ServerStatus.INSTALLED)
        # status INSTALLED → is_callable False
        with pytest.raises(DiscoveryError):
            service.discover("alice", _FakeClient())
        events = [
            e for e in _audit_lines(service)
            if e["event"] == "discovery_failed"
        ]
        assert events
        assert events[-1]["status"] == "installed"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — initialize / list_tools failures + is_initialized
# ══════════════════════════════════════════════════════════════════════════════


class TestInitializeListTools:
    def test_initialize_called_when_not_initialized(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[], already_initialized=False)
        service.discover("alice", client)
        assert client.init_call_count == 1

    def test_initialize_skipped_when_already_initialized(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[], already_initialized=True)
        service.discover("alice", client)
        assert client.init_call_count == 0

    def test_list_tools_called_when_already_initialized(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[], already_initialized=True)
        service.discover("alice", client)
        assert client.list_tools_call_count == 1

    def test_initialize_failure_raises_discovery_error(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[])
        client.init_raises = True
        with pytest.raises(DiscoveryError, match="initialize_failed"):
            service.discover("alice", client)
        # list_tools non appelé
        assert client.list_tools_call_count == 0

    def test_initialize_failure_audit_no_traceback_leak(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[])
        client.init_raises = True
        with pytest.raises(DiscoveryError):
            service.discover("alice", client)
        blob = _audit_blob(service)
        assert "INIT_FAIL_INTERNAL_DETAIL_MARKER" not in blob

    def test_list_tools_failure_raises(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[])
        client.list_tools_raises = True
        with pytest.raises(DiscoveryError, match="list_tools_failed"):
            service.discover("alice", client)

    def test_list_tools_failure_audit_no_traceback_leak(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[])
        client.list_tools_raises = True
        with pytest.raises(DiscoveryError):
            service.discover("alice", client)
        blob = _audit_blob(service)
        assert "LIST_TOOLS_FAIL_INTERNAL_DETAIL_MARKER" not in blob

    def test_list_tools_returns_non_list_raises(self, service, catalog):
        _add_active(catalog, "alice")
        client = _FakeClient(tools=[])
        client.list_tools_returns_non_list = True
        with pytest.raises(DiscoveryError, match="list_tools_failed"):
            service.discover("alice", client)


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Defensive MCPTool validation
# ══════════════════════════════════════════════════════════════════════════════


class TestDefensiveMCPToolValidation:
    """Defensive : protègent contre un MCPClient mal typé ou un MCPTool
    construit hors-spec. Le vrai MCPClient.list_tools() retourne uniquement
    des MCPTool bien formés ; ces cas ne devraient pas survenir en prod."""

    def test_empty_name_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1
        assert len(report.proposals) == 0

    def test_uppercase_name_valid_fix_az(self, service, catalog):
        """Fix AZ — la spec MCP n'impose pas la casse : windows-mcp expose
        App/Click/PowerShell (PascalCase). Runtime 2026-06-13 02:43 : les
        19 tools refusés `name_invalid` → activation 'réussie' avec 0 tool."""
        _add_active(catalog, "alice")
        tools = [_mcptool(name="ReadDoc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 0
        assert report.discovered_count == 1

    def test_uppercase_name_bad_charset_still_invalid(self, service, catalog):
        """Fix AZ élargit la CASSE, jamais le charset."""
        _add_active(catalog, "alice")
        tools = [_mcptool(name="Read Doc!")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_name_too_long_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="a" * 129)]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_description_control_char_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", description="hello\x00world")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_description_too_long_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", description="x" * 4097)]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_input_schema_not_dict_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        # Fake MCPTool via duck typing : input_schema = "not_a_dict"
        @dataclass(frozen=True)
        class FakeTool:
            name: str = "read_doc"
            description: str = ""
            input_schema: Any = "not_a_dict"
        report = service.discover("alice", _FakeClient(tools=[FakeTool()]))
        assert report.invalid_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Anti-confused-deputy (production behavior)
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiConfusedDeputy:
    """Production : un MCP server malveillant ne doit pas pouvoir spoof
    le namespacing en nommant son outil mcp__other__steal."""

    def test_mcp_prefix_in_name_rejected(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="mcp__bob__steal")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1
        events = [
            e for e in _audit_lines(service)
            if e["event"] == "tool_invalid"
        ]
        assert events
        assert events[-1]["reason"] == "name_spoofing"

    def test_mcp_prefix_only_rejected(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="mcp__")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_mcp_substring_anywhere_rejected(self, service, catalog):
        """mcp__ comme substring → refus."""
        _add_active(catalog, "alice")
        tools = [_mcptool(name="x_mcp__y")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 1

    def test_audit_spoofing_reason_present(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="mcp__bob__exfiltrate")]
        service.discover("alice", _FakeClient(tools=tools))
        events = [e for e in _audit_lines(service) if e["event"] == "tool_invalid"]
        assert events[-1]["reason"] == "name_spoofing"

    def test_legitimate_name_accepted(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.invalid_count == 0
        assert len(report.proposals) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — PolicyAttributor integration
# ══════════════════════════════════════════════════════════════════════════════


class TestPolicyAttributorIntegration:
    def test_attributor_called_with_correct_metadata(self, tmp_path, catalog):
        _add_active(catalog, "alice")
        calls = []

        class CapturingAttributor:
            def attribute(self, tool, *, trust_score=None):
                calls.append((tool, trust_score))
                return AttributionDecision(
                    policy=MCPPolicy.READ_ONLY,
                    reason="match:read_only",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=["read"],
                    classified_policy=MCPPolicy.READ_ONLY,
                )

        s = MCPDiscoveryService(
            catalog=catalog, attributor=CapturingAttributor(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [_mcptool(name="read_doc", description="reads a doc")]
        s.discover("alice", _FakeClient(tools=tools), trust_score=85)
        assert len(calls) == 1
        tool_metadata, trust = calls[0]
        assert tool_metadata.server_id == "alice"
        assert tool_metadata.tool_name == "read_doc"
        assert tool_metadata.description == "reads a doc"
        assert trust == 85

    def test_policy_attributed_proposed_policy_set(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert len(report.proposals) == 1
        assert report.proposals[0].proposed_policy == MCPPolicy.READ_ONLY

    def test_policy_none_refused(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="opaque_action")]  # no keyword match
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.refused_count == 1
        assert len(report.proposals) == 1
        assert report.proposals[0].proposed_policy is None

    def test_attributor_raise_error_count_continue(self, tmp_path, catalog):
        _add_active(catalog, "alice")

        class RaisingAttributor:
            def attribute(self, tool, *, trust_score=None):
                if tool.tool_name == "boom":
                    raise RuntimeError("ATTRIBUTOR_DETAIL_MARKER_INTERNAL")
                return AttributionDecision(
                    policy=MCPPolicy.READ_ONLY,
                    reason="match:read_only",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                )

        s = MCPDiscoveryService(
            catalog=catalog, attributor=RaisingAttributor(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [
            _mcptool(name="read_doc"),
            _mcptool(name="boom"),
            _mcptool(name="get_status"),
        ]
        report = s.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 3
        assert report.error_count == 1
        assert len(report.proposals) == 2

    def test_attributor_raise_audit_no_leak(self, tmp_path, catalog):
        _add_active(catalog, "alice")

        class RaisingAttributor:
            def attribute(self, tool, *, trust_score=None):
                raise RuntimeError("ATTRIBUTOR_DETAIL_MARKER_INTERNAL")

        s = MCPDiscoveryService(
            catalog=catalog, attributor=RaisingAttributor(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [_mcptool(name="read_doc")]
        s.discover("alice", _FakeClient(tools=tools))
        blob = s.audit_log_path.read_text(encoding="utf-8")
        assert "ATTRIBUTOR_DETAIL_MARKER_INTERNAL" not in blob

    def test_trust_score_fallback_to_entry_trust_score(self, service, catalog):
        _add_active(catalog, "alice", trust_score=85)
        tools = [_mcptool(name="send_message")]
        report = service.discover("alice", _FakeClient(tools=tools))
        # trust=85 ≥ 70 → attributed
        assert report.proposed_count == 1
        assert report.proposals[0].proposed_policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        assert report.proposals[0].trust_score_used == 85

    def test_trust_score_override_param(self, service, catalog):
        _add_active(catalog, "alice", trust_score=20)  # below threshold
        tools = [_mcptool(name="send_message")]
        # Override trust_score=90
        report = service.discover("alice", _FakeClient(tools=tools), trust_score=90)
        assert report.proposed_count == 1
        assert report.proposals[0].trust_score_used == 90

    def test_trust_score_invalid_raises(self, service, catalog):
        _add_active(catalog, "alice")
        with pytest.raises(DiscoveryError, match="trust_score_invalid"):
            service.discover("alice", _FakeClient(tools=[]), trust_score=150)

    def test_six_policies_all_supported(self, service, catalog):
        _add_active(catalog, "alice", trust_score=95)
        # 6 outils représentant chaque policy
        tools = [
            _mcptool(name="read_doc"),         # READ_ONLY
            _mcptool(name="fetch_url"),        # EXTERNAL_READ
            _mcptool(name="write_file"),       # LOCAL_WRITE
            _mcptool(name="send_message"),     # EXTERNAL_WRITE_RECOVERABLE
            _mcptool(name="delete_file"),      # EXTERNAL_WRITE_IRREVERSIBLE
            _mcptool(name="oauth_login"),      # SECRETS_AUTH
        ]
        report = service.discover("alice", _FakeClient(tools=tools))
        policies = {p.proposed_policy for p in report.proposals}
        assert MCPPolicy.READ_ONLY in policies
        assert MCPPolicy.EXTERNAL_READ in policies
        assert MCPPolicy.LOCAL_WRITE in policies
        assert MCPPolicy.EXTERNAL_WRITE_RECOVERABLE in policies
        assert MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE in policies
        assert MCPPolicy.SECRETS_AUTH in policies

    def test_matched_keywords_passed_to_proposal(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert "read" in report.proposals[0].matched_keywords

    def test_classified_policy_passed_to_proposal(self, service, catalog):
        _add_active(catalog, "alice", trust_score=20)
        tools = [_mcptool(name="send_message")]
        report = service.discover("alice", _FakeClient(tools=tools))
        # Trust too low → policy None mais classified_policy reste set
        assert report.proposals[0].proposed_policy is None
        assert report.proposals[0].classified_policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — DiscoveryReport invariants
# ══════════════════════════════════════════════════════════════════════════════


class TestDiscoveryReportInvariants:
    def test_discovered_count_equals_tools_returned(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name=f"read_{i}") for i in range(7)]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 7

    def test_proposals_length_equals_discovered_minus_invalid_minus_error(
        self, tmp_path, catalog
    ):
        _add_active(catalog, "alice")

        class MixedAttributor:
            def attribute(self, tool, *, trust_score=None):
                if tool.tool_name == "boom":
                    raise RuntimeError("err")
                return AttributionDecision(
                    policy=MCPPolicy.READ_ONLY,
                    reason="match:read_only",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                )

        s = MCPDiscoveryService(
            catalog=catalog, attributor=MixedAttributor(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [
            _mcptool(name="read_a"),       # OK
            _mcptool(name=""),             # invalid
            _mcptool(name="boom"),         # error
            _mcptool(name="read_b"),       # OK
        ]
        report = s.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 4
        assert report.invalid_count == 1
        assert report.error_count == 1
        assert len(report.proposals) == 4 - 1 - 1

    def test_proposed_plus_refused_equals_proposals_length(self, service, catalog):
        _add_active(catalog, "alice", trust_score=95)
        tools = [
            _mcptool(name="read_doc"),     # proposed
            _mcptool(name="opaque_xyz"),   # refused (no match)
            _mcptool(name="get_status"),   # proposed
        ]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.proposed_count + report.refused_count == len(report.proposals)

    def test_attributor_exception_does_not_create_proposal(self, tmp_path, catalog):
        _add_active(catalog, "alice")

        class AlwaysRaise:
            def attribute(self, tool, *, trust_score=None):
                raise RuntimeError("err")

        s = MCPDiscoveryService(
            catalog=catalog, attributor=AlwaysRaise(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [_mcptool(name="read_doc")]
        report = s.discover("alice", _FakeClient(tools=tools))
        assert report.error_count == 1
        assert report.discovered_count == 1
        assert len(report.proposals) == 0

    def test_proposals_order_preserved(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name=f"read_{c}") for c in ["a", "b", "c"]]
        report = service.discover("alice", _FakeClient(tools=tools))
        names = [p.tool_name for p in report.proposals]
        assert names == ["read_a", "read_b", "read_c"]

    def test_ts_iso_format(self, service, catalog):
        _add_active(catalog, "alice")
        report = service.discover("alice", _FakeClient(tools=[]))
        # ISO 8601 with +00:00 suffix (UTC)
        assert "T" in report.ts
        assert report.ts.endswith("+00:00") or report.ts.endswith("Z")

    def test_empty_tools_report(self, service, catalog):
        _add_active(catalog, "alice")
        report = service.discover("alice", _FakeClient(tools=[]))
        assert report.discovered_count == 0
        assert report.proposed_count == 0
        assert report.refused_count == 0
        assert report.invalid_count == 0
        assert report.error_count == 0
        assert report.proposals == []

    def test_namespaced_name_format(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.proposals[0].namespaced_name == "mcp__alice__read_doc"

    def test_invariant_holds_with_complex_mix(self, tmp_path, catalog):
        _add_active(catalog, "alice", trust_score=85)

        class MixedAttributor:
            def attribute(self, tool, *, trust_score=None):
                if tool.tool_name == "raise_me":
                    raise RuntimeError("err")
                # Use real PolicyAttributor logic via dispatch
                pa = PolicyAttributor(
                    audit_log_path=Path("/tmp/throwaway.jsonl"),
                )
                return pa.attribute(tool, trust_score=trust_score)

        s = MCPDiscoveryService(
            catalog=catalog, attributor=MixedAttributor(),
            audit_log_path=tmp_path / "audit.jsonl",
        )
        tools = [
            _mcptool(name="read_a"),       # OK
            _mcptool(name="opaque_xyz"),   # refused
            _mcptool(name=""),             # invalid
            _mcptool(name="raise_me"),     # error
            _mcptool(name="get_b"),        # OK
        ]
        report = s.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 5
        assert report.invalid_count == 1
        assert report.error_count == 1
        assert (
            len(report.proposals) ==
            report.discovered_count - report.invalid_count - report.error_count
        )
        assert report.proposed_count + report.refused_count == len(report.proposals)


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Persistance disque + JSON sérialisation
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistenceJSON:
    def test_persist_false_no_file(self, service, catalog):
        _add_active(catalog, "alice")
        service.discover("alice", _FakeClient(tools=[_mcptool()]))
        files = list(service.reports_dir.glob("*.json")) if service.reports_dir.exists() else []
        assert files == []

    def test_persist_true_file_created(self, tmp_path, catalog, attributor):
        _add_active(catalog, "alice")
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            reports_dir=tmp_path / "reports",
            persist_reports=True,
        )
        s.discover("alice", _FakeClient(tools=[_mcptool(name="read_doc")]))
        files = list(s.reports_dir.glob("alice_*.json"))
        assert len(files) == 1

    def test_persist_report_serializes_policy_values_not_enums(
        self, tmp_path, catalog, attributor
    ):
        """Vérifie que les MCPPolicy sont sérialisées en .value (str), pas en
        objets Enum."""
        _add_active(catalog, "alice")
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            reports_dir=tmp_path / "reports",
            persist_reports=True,
        )
        s.discover("alice", _FakeClient(tools=[_mcptool(name="read_doc")]))
        files = list(s.reports_dir.glob("alice_*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        proposals = data["proposals"]
        assert len(proposals) == 1
        # Doit être une string "read_only", pas une représentation Enum
        assert proposals[0]["proposed_policy"] == "read_only"
        assert "MCPPolicy" not in files[0].read_text(encoding="utf-8")

    def test_report_to_dict_is_json_serializable(self):
        proposal = ToolDiscoveryProposal(
            server_id="alice",
            tool_name="read_doc",
            namespaced_name="mcp__alice__read_doc",
            proposed_policy=MCPPolicy.READ_ONLY,
            attribution_reason="match:read_only",
            matched_keywords=["read"],
            trust_score_used=85,
            classified_policy=MCPPolicy.READ_ONLY,
            has_description=True,
            has_input_schema=False,
            description_length=42,
            input_schema_keys_count=0,
        )
        report = DiscoveryReport(
            server_id="alice",
            ts="2026-06-02T12:00:00+00:00",
            discovered_count=1,
            proposed_count=1,
            refused_count=0,
            invalid_count=0,
            error_count=0,
            proposals=[proposal],
        )
        d = _report_to_dict(report)
        # Doit être JSON-encodable sans erreur
        s = json.dumps(d)
        assert "read_only" in s

    def test_refused_policy_none_serializes_as_null(self):
        proposal = ToolDiscoveryProposal(
            server_id="alice",
            tool_name="opaque",
            namespaced_name="mcp__alice__opaque",
            proposed_policy=None,
            attribution_reason="no_keyword_match",
            matched_keywords=[],
            trust_score_used=None,
            classified_policy=None,
            has_description=False,
            has_input_schema=False,
            description_length=0,
            input_schema_keys_count=0,
        )
        d = _proposal_to_dict(proposal)
        s = json.dumps(d)
        # Le JSON doit contenir "proposed_policy": null
        assert '"proposed_policy": null' in s
        assert '"classified_policy": null' in s
        assert '"trust_score_used": null' in s

    def test_persist_invalid_and_error_only_no_proposals(
        self, tmp_path, catalog, attributor
    ):
        _add_active(catalog, "alice")
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            reports_dir=tmp_path / "reports",
            persist_reports=True,
        )
        tools = [_mcptool(name="")]  # invalid
        s.discover("alice", _FakeClient(tools=tools))
        files = list(s.reports_dir.glob("alice_*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["invalid_count"] == 1
        assert data["proposals"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — has_/length helpers
# ══════════════════════════════════════════════════════════════════════════════


class TestHasLengthHelpers:
    def test_description_empty_has_false(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", description="")]
        report = service.discover("alice", _FakeClient(tools=tools))
        p = report.proposals[0]
        assert p.has_description is False
        assert p.description_length == 0

    def test_description_present_has_true(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", description="abc")]
        report = service.discover("alice", _FakeClient(tools=tools))
        p = report.proposals[0]
        assert p.has_description is True
        assert p.description_length == 3

    def test_input_schema_empty_has_false(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", input_schema={})]
        report = service.discover("alice", _FakeClient(tools=tools))
        p = report.proposals[0]
        assert p.has_input_schema is False
        assert p.input_schema_keys_count == 0

    def test_input_schema_with_keys_has_true(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", input_schema={"a": 1, "b": 2})]
        report = service.discover("alice", _FakeClient(tools=tools))
        p = report.proposals[0]
        assert p.has_input_schema is True
        assert p.input_schema_keys_count == 2

    def test_description_marker_not_in_audit_only_length(self, service, catalog):
        _add_active(catalog, "alice")
        marker = "DESC_RAW_LEAK_MARKER_PHASE17"
        tools = [_mcptool(name="read_doc", description=f"hello {marker} world")]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        assert marker not in blob
        events = [e for e in _audit_lines(service) if e["event"] == "proposal_added"]
        assert events
        # description_length doit refléter la vraie longueur
        assert events[-1]["description_length"] == len(f"hello {marker} world")

    def test_input_schema_marker_not_in_audit(self, service, catalog):
        _add_active(catalog, "alice")
        marker = "SCHEMA_RAW_LEAK_MARKER_PHASE17"
        tools = [_mcptool(name="read_doc", input_schema={"secret": marker})]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        assert marker not in blob

    def test_audit_proposal_added_format(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc", description="abc",
                          input_schema={"a": 1})]
        service.discover("alice", _FakeClient(tools=tools))
        events = [e for e in _audit_lines(service) if e["event"] == "proposal_added"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["tool_name"] == "read_doc"
        assert ev["namespaced_name"] == "mcp__alice__read_doc"
        assert ev["policy"] == "read_only"  # str, pas Enum
        assert ev["has_description"] is True
        assert ev["has_input_schema"] is True
        assert ev["description_length"] == 3
        assert ev["input_schema_keys_count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensic:
    def test_audit_no_description_raw(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc",
                          description="FORENSIC_DESC_AAA payload")]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        assert "FORENSIC_DESC_AAA" not in blob

    def test_audit_no_input_schema_raw(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc",
                          input_schema={"key": "FORENSIC_SCHEMA_BBB"})]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        assert "FORENSIC_SCHEMA_BBB" not in blob

    def test_audit_no_stringification_dependencies(self, service, catalog):
        _add_active(catalog, "alice")
        service.discover("alice", _FakeClient(tools=[_mcptool()]))
        blob = _audit_blob(service)
        assert "_FakeClient" not in blob
        assert "MCPServerCatalog" not in blob
        assert "object at 0x" not in blob

    def test_audit_anti_leak_tool_name_when_invalid(self, service, catalog):
        _add_active(catalog, "alice")
        # Fix AZ : uppercase est désormais VALIDE — le marker doit être
        # invalide par CHARSET (espace + !) pour tester l'anti-leak.
        marker = "ATTACKER TOOL NAME MARKER PHASE17!"
        # name vide → invalid avant tout log
        tools = [_mcptool(name="")]
        # On utilise un fake où name est le marker mais regex échoue
        tools_with_marker = [_mcptool(name=marker)]  # charset → invalid
        service.discover("alice", _FakeClient(tools=tools_with_marker))
        blob = _audit_blob(service)
        assert marker not in blob

    def test_audit_multi_scan_forensic_no_leak(self, service, catalog):
        _add_active(catalog, "alice")
        markers_desc = [f"FORENSIC_M{i}_DESC" for i in range(5)]
        markers_schema = [f"FORENSIC_M{i}_SCHEMA" for i in range(5)]
        tools = [
            _mcptool(
                name=f"read_doc_{i}",
                description=f"prefix {md} suffix",
                input_schema={"k": ms},
            )
            for i, (md, ms) in enumerate(zip(markers_desc, markers_schema))
        ]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        for m in markers_desc + markers_schema:
            assert m not in blob

    def test_audit_no_module_paths(self, service, catalog):
        _add_active(catalog, "alice")
        service.discover("alice", _FakeClient(tools=[_mcptool()]))
        blob = _audit_blob(service)
        assert "src.mcp" not in blob
        assert "C:\\" not in blob
        assert "/home/" not in blob

    def test_audit_reason_codes_short(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [_mcptool(name="mcp__bob__steal")]
        service.discover("alice", _FakeClient(tools=tools))
        for ev in _audit_lines(service):
            if "reason" in ev:
                assert isinstance(ev["reason"], str)
                assert len(ev["reason"]) < 80

    def test_audit_summary_format(self, service, catalog):
        _add_active(catalog, "alice")
        service.discover("alice", _FakeClient(tools=[_mcptool()]))
        events = [e for e in _audit_lines(service) if e["event"] == "discovery_summary"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["discovered_count"] == 1
        assert "proposed_count" in ev
        assert "refused_count" in ev
        assert "invalid_count" in ev
        assert "error_count" in ev

    def test_audit_policy_fields_are_strings(self, service, catalog):
        """Toutes les policies loggées doivent être .value (str)."""
        _add_active(catalog, "alice")
        tools = [_mcptool(name="read_doc")]
        service.discover("alice", _FakeClient(tools=tools))
        events = [e for e in _audit_lines(service) if e["event"] == "proposal_added"]
        ev = events[-1]
        assert isinstance(ev["policy"], str)
        assert isinstance(ev["classified_policy"], str)
        assert "MCPPolicy" not in json.dumps(ev)

    def test_audit_started_event_present(self, service, catalog):
        _add_active(catalog, "alice")
        service.discover("alice", _FakeClient(tools=[]))
        events = [e for e in _audit_lines(service) if e["event"] == "discovery_started"]
        assert events
        assert events[-1]["status"] == "active"


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Sanity end-to-end (mocks)
# ══════════════════════════════════════════════════════════════════════════════


class TestSanityEndToEnd:
    """End-to-end avec vrais PolicyAttributor + MCPServerCatalog.
    MCPClient mocké (Phase 17 ne démarre pas de subprocess)."""

    def test_e2e_three_tools_mixed_policies(self, service, catalog):
        _add_active(catalog, "alice", trust_score=95)
        tools = [
            _mcptool(name="read_doc"),
            _mcptool(name="send_message"),
            _mcptool(name="oauth_login"),
        ]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 3
        assert report.proposed_count == 3
        assert report.refused_count == 0

    def test_e2e_trust_score_low_only_reads_proposed(self, service, catalog):
        _add_active(catalog, "alice", trust_score=50)
        tools = [
            _mcptool(name="read_doc"),       # READ_ONLY, no gate
            _mcptool(name="send_message"),   # need 70, refused
            _mcptool(name="oauth_login"),    # need 90, refused
        ]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.proposed_count == 1
        assert report.refused_count == 2
        proposed = [p for p in report.proposals if p.proposed_policy is not None]
        assert len(proposed) == 1
        assert proposed[0].proposed_policy == MCPPolicy.READ_ONLY

    def test_e2e_trust_score_fallback_catalog(self, service, catalog):
        _add_active(catalog, "alice", trust_score=85)
        tools = [_mcptool(name="send_message")]
        # trust_score paramètre None → fallback à 85
        report = service.discover("alice", _FakeClient(tools=tools), trust_score=None)
        assert report.proposed_count == 1
        assert report.proposals[0].trust_score_used == 85

    def test_e2e_persistance_round_trip(self, tmp_path, catalog, attributor):
        _add_active(catalog, "alice", trust_score=95)
        s = MCPDiscoveryService(
            catalog=catalog, attributor=attributor,
            audit_log_path=tmp_path / "audit.jsonl",
            reports_dir=tmp_path / "reports",
            persist_reports=True,
        )
        tools = [_mcptool(name="read_doc"), _mcptool(name="oauth_login")]
        report = s.discover("alice", _FakeClient(tools=tools))
        files = list(s.reports_dir.glob("alice_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        # Roundtrip basique
        assert data["server_id"] == "alice"
        assert data["discovered_count"] == 2
        assert len(data["proposals"]) == 2

    def test_e2e_repeat_discovery_no_state_carryover(self, service, catalog):
        _add_active(catalog, "alice")
        tools_a = [_mcptool(name="read_doc")]
        tools_b = [_mcptool(name="get_status")]
        r1 = service.discover("alice", _FakeClient(tools=tools_a))
        r2 = service.discover("alice", _FakeClient(tools=tools_b))
        assert r1.proposals[0].tool_name == "read_doc"
        assert r2.proposals[0].tool_name == "get_status"

    def test_e2e_no_call_tool_in_implementation(self, service, catalog):
        """Vérifie qu'aucune méthode call_tool n'est invoquée. Le FakeClient
        n'expose pas call_tool, le code production ne doit jamais l'appeler."""
        _add_active(catalog, "alice")

        class StrictClient:
            is_initialized = False
            def initialize(self):
                return {}
            def list_tools(self):
                return [_mcptool(name="read_doc")]
            def call_tool(self, *args, **kwargs):
                raise AssertionError("call_tool MUST NOT be invoked by Discovery")

        service.discover("alice", StrictClient())

    def test_e2e_anti_confused_deputy_blocks_spoof(self, service, catalog):
        _add_active(catalog, "alice")
        tools = [
            _mcptool(name="read_doc"),
            _mcptool(name="mcp__bob__steal"),    # spoof
            _mcptool(name="get_status"),
        ]
        report = service.discover("alice", _FakeClient(tools=tools))
        assert report.discovered_count == 3
        assert report.invalid_count == 1
        assert len(report.proposals) == 2
        names = [p.tool_name for p in report.proposals]
        assert "mcp__bob__steal" not in names

    def test_e2e_forensic_scan_10_markers_no_leak(self, service, catalog):
        _add_active(catalog, "alice", trust_score=95)
        markers = [f"E2E_FORENSIC_PHASE17_{i}" for i in range(10)]
        tools = [
            _mcptool(
                name=f"read_doc_{i}",
                description=f"text {m} payload",
                input_schema={"f": m},
            )
            for i, m in enumerate(markers)
        ]
        service.discover("alice", _FakeClient(tools=tools))
        blob = _audit_blob(service)
        for m in markers:
            assert m not in blob, f"marker {m} leaked"
