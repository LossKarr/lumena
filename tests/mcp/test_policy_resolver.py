"""
Tests Phase 15 v2 — ToolRegistryPolicyResolver.

Sections :
  1. Init & configuration
  2. Validation server_id / tool_name (+lowercase strict)
  3. Validation binding tool_server
  4. Catalog server unknown / removed
  5. require_callable (délègue à catalog.is_callable())
  6. ToolRegistry lookup
  7. End-to-end happy path
  8. Audit forensique no-PII (+anti-leak inputs invalides)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.policy import MCPPolicy
from src.mcp.policy_resolver import (
    ToolRegistryPolicyResolver,
)
from src.mcp.server_catalog import (
    CatalogError,
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


class _FakeToolRegistry:
    """ToolRegistry-like : map tool_name → policy.

    Sert également à simuler des exceptions sur les méthodes.
    """

    def __init__(self):
        self._handlers: Dict[str, Optional[MCPPolicy]] = {}
        self.raise_is_dynamic = False
        self.raise_get_policy = False
        self.is_dyn_calls: List[str] = []
        self.get_policy_calls: List[str] = []

    def register(self, tool_name: str, policy: Optional[MCPPolicy]) -> None:
        self._handlers[tool_name] = policy

    def is_dynamic_handler(self, name: str) -> bool:
        self.is_dyn_calls.append(name)
        if self.raise_is_dynamic:
            raise RuntimeError("is_dyn boom")
        return name in self._handlers

    def get_dynamic_handler_policy(self, name: str) -> Optional[MCPPolicy]:
        self.get_policy_calls.append(name)
        if self.raise_get_policy:
            raise RuntimeError("get_policy boom")
        return self._handlers.get(name)


@pytest.fixture
def catalog(tmp_path: Path) -> MCPServerCatalog:
    return MCPServerCatalog(
        catalog_dir=tmp_path / "catalog",
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
        secrets_service=_InMemorySecretsService(),
    )


@pytest.fixture
def registry() -> _FakeToolRegistry:
    return _FakeToolRegistry()


@pytest.fixture
def resolver(tmp_path: Path, registry, catalog) -> ToolRegistryPolicyResolver:
    return ToolRegistryPolicyResolver(
        tool_registry=registry,
        server_catalog=catalog,
        require_callable=True,
        audit_log_path=tmp_path / "resolver" / "audit.jsonl",
    )


def _add_active_server(
    catalog: MCPServerCatalog,
    server_id: str = "alice",
    owner_profile: str = "alice",
) -> None:
    catalog.add_server(
        server_id=server_id,
        display_name="Alice",
        package_spec="npm:mcp-foo",
        owner_profile=owner_profile,
    )
    catalog.update_status(server_id, ServerStatus.INSTALLED)
    catalog.update_status(server_id, ServerStatus.ACTIVE)


def _audit_lines(resolver: ToolRegistryPolicyResolver) -> List[Dict[str, Any]]:
    if not resolver.audit_log_path.exists():
        return []
    out = []
    with open(resolver.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(resolver: ToolRegistryPolicyResolver) -> str:
    if not resolver.audit_log_path.exists():
        return ""
    return resolver.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_audit_dir_created(self, resolver):
        assert resolver.audit_log_path.parent.exists()

    def test_require_callable_default_true(self, tmp_path, registry, catalog):
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert r.require_callable is True

    def test_require_callable_false_respected(self, tmp_path, registry, catalog):
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        assert r.require_callable is False

    def test_init_rejects_none_tool_registry(self, tmp_path, catalog):
        with pytest.raises(ValueError, match="tool_registry"):
            ToolRegistryPolicyResolver(
                tool_registry=None,  # type: ignore[arg-type]
                server_catalog=catalog,
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_tool_registry_without_methods(self, tmp_path, catalog):
        class NoMethods:
            pass
        with pytest.raises(ValueError, match="is_dynamic_handler"):
            ToolRegistryPolicyResolver(
                tool_registry=NoMethods(),  # type: ignore[arg-type]
                server_catalog=catalog,
                audit_log_path=tmp_path / "audit.jsonl",
            )

    def test_init_rejects_none_catalog(self, tmp_path, registry):
        with pytest.raises(ValueError, match="server_catalog"):
            ToolRegistryPolicyResolver(
                tool_registry=registry,
                server_catalog=None,  # type: ignore[arg-type]
                audit_log_path=tmp_path / "audit.jsonl",
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Validation server_id / tool_name
# ══════════════════════════════════════════════════════════════════════════════


class TestServerIdToolNameValidation:
    def test_server_id_uppercase_returns_none(self, resolver):
        assert resolver.resolve("ALICE", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events
        assert events[-1]["reason"] == "server_id_invalid"

    def test_server_id_windows_reserved_returns_none(self, resolver):
        assert resolver.resolve("con", "mcp__con__tool") is None

    def test_server_id_path_traversal_returns_none(self, resolver):
        assert resolver.resolve("foo..bar", "mcp__foo__tool") is None

    def test_server_id_none_returns_none(self, resolver):
        assert resolver.resolve(None, "mcp__alice__tool") is None  # type: ignore

    def test_server_id_empty_returns_none(self, resolver):
        assert resolver.resolve("", "mcp__alice__tool") is None

    def test_tool_name_none_returns_none(self, resolver):
        assert resolver.resolve("alice", None) is None  # type: ignore

    def test_tool_name_bad_format_returns_none(self, resolver):
        assert resolver.resolve("alice", "not_mcp_format") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "tool_name_invalid"

    # Correction 2 — segment SERVER lowercase strict (slug dérivé par nous)
    def test_tool_name_uppercase_server_segment_rejected(self, resolver):
        assert resolver.resolve("alice", "mcp__Alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "tool_name_invalid"

    # Fix AZ — segment TOOL en casse libre (la spec MCP ne l'impose pas :
    # windows-mcp expose App/Click/PowerShell ; lowercase strict rendait
    # ses tools INEXÉCUTABLES après enregistrement).
    def test_tool_name_uppercase_tool_segment_accepted_fix_az(
        self, resolver, registry, catalog
    ):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__Tool", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__Tool") == MCPPolicy.READ_ONLY

    def test_tool_name_full_lowercase_accepted(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_tool_name_with_dots_and_dashes_accepted(self, resolver, registry, catalog):
        _add_active_server(catalog, "a-b.c")
        registry.register("mcp__a-b.c__t.o-o.l", MCPPolicy.READ_ONLY)
        assert resolver.resolve("a-b.c", "mcp__a-b.c__t.o-o.l") == MCPPolicy.READ_ONLY

    def test_tool_name_with_underscores_accepted(self, resolver, registry, catalog):
        _add_active_server(catalog, "a_b")
        registry.register("mcp__a_b__c_d", MCPPolicy.READ_ONLY)
        assert resolver.resolve("a_b", "mcp__a_b__c_d") == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Validation binding tool_server
# ══════════════════════════════════════════════════════════════════════════════


class TestBinding:
    def test_binding_mismatch_returns_none(self, resolver):
        assert resolver.resolve("alice", "mcp__bob__exec") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "tool_server_mismatch"

    def test_binding_match_continues(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_binding_audit_contains_both_when_validated(self, resolver):
        resolver.resolve("alice", "mcp__bob__exec")
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        ev = events[-1]
        assert ev.get("server_id") == "alice"
        assert ev.get("tool_name") == "mcp__bob__exec"
        assert ev["reason"] == "tool_server_mismatch"

    def test_binding_strict_case(self, resolver):
        """server_id lowercase, mais tool_name doit matcher exactement
        son préfixe mcp__<server_id>__."""
        # tool_name avec server "bob" lowercase ne matche pas server_id "alice"
        assert resolver.resolve("alice", "mcp__bob__tool") is None

    def test_binding_long_server_id_short_tool(self, resolver, registry, catalog):
        _add_active_server(catalog, "very-long-server-id-name")
        registry.register("mcp__very-long-server-id-name__t", MCPPolicy.READ_ONLY)
        assert (
            resolver.resolve("very-long-server-id-name", "mcp__very-long-server-id-name__t")
            == MCPPolicy.READ_ONLY
        )

    def test_binding_short_server_long_tool(self, resolver, registry, catalog):
        _add_active_server(catalog, "a")
        registry.register("mcp__a__very-long-tool-name", MCPPolicy.READ_ONLY)
        assert (
            resolver.resolve("a", "mcp__a__very-long-tool-name")
            == MCPPolicy.READ_ONLY
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Catalog server unknown / removed
# ══════════════════════════════════════════════════════════════════════════════


class TestCatalogUnknownRemoved:
    def test_server_unknown_returns_none(self, resolver):
        assert resolver.resolve("ghost", "mcp__ghost__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "server_unknown"

    def test_server_removed_returns_none(self, resolver, catalog):
        _add_active_server(catalog, "alice")
        catalog.remove_server("alice")
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "server_removed"
        assert events[-1]["status"] == "removed"

    def test_server_removed_blocks_even_with_require_callable_false(
        self, tmp_path, registry, catalog
    ):
        _add_active_server(catalog, "alice")
        catalog.remove_server("alice")
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert r.resolve("alice", "mcp__alice__tool") is None

    def test_catalog_get_server_raises_returns_none(self, resolver, registry, catalog, monkeypatch):
        def raise_(*_a, **_k):
            raise CatalogError("boom")
        monkeypatch.setattr(catalog, "get_server", raise_)
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"

    def test_catalog_get_server_unexpected_exception_returns_none(
        self, resolver, catalog, monkeypatch
    ):
        def raise_(*_a, **_k):
            raise RuntimeError("unexpected")
        monkeypatch.setattr(catalog, "get_server", raise_)
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"

    def test_active_server_continues_to_registry_lookup(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_unknown_then_add_then_resolve(self, resolver, registry, catalog):
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.EXTERNAL_READ)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.EXTERNAL_READ

    def test_audit_does_not_leak_serverentry_fields(self, resolver, catalog):
        catalog.add_server(
            server_id="alice",
            display_name="LEAK_DISPLAY_MARKER_AAA",
            package_spec="npm:mcp-foo",
            owner_profile="alice",
        )
        resolver.resolve("alice", "mcp__alice__tool")
        blob = _audit_blob(resolver)
        assert "LEAK_DISPLAY_MARKER_AAA" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — require_callable (délègue à catalog.is_callable())
# ══════════════════════════════════════════════════════════════════════════════


class TestRequireCallable:
    def test_require_true_active_continues(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_require_true_declared_returns_none(self, resolver, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "server_not_callable"
        assert events[-1]["status"] == "declared"

    def test_require_true_installed_returns_none(self, resolver, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        catalog.update_status("alice", ServerStatus.INSTALLED)
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["status"] == "installed"

    def test_require_true_quarantined_returns_none(self, resolver, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        catalog.update_status("alice", ServerStatus.QUARANTINED)
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["status"] == "quarantined"

    def test_require_false_installed_continues(self, tmp_path, registry, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        catalog.update_status("alice", ServerStatus.INSTALLED)
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert r.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_require_false_declared_continues(self, tmp_path, registry, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert r.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_require_false_quarantined_continues(self, tmp_path, registry, catalog):
        catalog.add_server(
            server_id="alice", display_name="x",
            package_spec="npm:mcp-foo", owner_profile="alice",
        )
        catalog.update_status("alice", ServerStatus.QUARANTINED)
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert r.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY

    def test_require_false_removed_still_blocked(self, tmp_path, registry, catalog):
        """REMOVED bloque toujours, même require_callable=False."""
        _add_active_server(catalog, "alice")
        catalog.remove_server("alice")
        r = ToolRegistryPolicyResolver(
            tool_registry=registry,
            server_catalog=catalog,
            require_callable=False,
            audit_log_path=tmp_path / "audit.jsonl",
        )
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        assert r.resolve("alice", "mcp__alice__tool") is None

    def test_is_callable_raises_returns_none(self, resolver, registry, catalog, monkeypatch):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        def raise_(*_a, **_k):
            raise RuntimeError("is_callable boom")
        monkeypatch.setattr(catalog, "is_callable", raise_)
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — ToolRegistry lookup
# ══════════════════════════════════════════════════════════════════════════════


class TestToolRegistryLookup:
    def test_is_dynamic_handler_false_returns_none(self, resolver, catalog):
        _add_active_server(catalog, "alice")
        # registry vide → is_dynamic_handler False
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "tool_not_registered"

    def test_is_dynamic_handler_raises_returns_none(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.raise_is_dynamic = True
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"

    def test_policy_not_attributed_returns_none(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", None)  # registered but no policy
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "policy_not_attributed"

    def test_get_policy_raises_returns_none(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        registry.raise_get_policy = True
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"

    def test_policy_invalid_type_returns_none(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", "not_a_policy")  # type: ignore[arg-type]
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "policy_invalid"

    @pytest.mark.parametrize("policy", list(MCPPolicy))
    def test_all_six_policies_supported(self, resolver, registry, catalog, policy):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", policy)
        assert resolver.resolve("alice", "mcp__alice__tool") == policy

    def test_registry_called_with_exact_tool_name(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        resolver.resolve("alice", "mcp__alice__tool")
        assert registry.is_dyn_calls[-1] == "mcp__alice__tool"
        assert registry.get_policy_calls[-1] == "mcp__alice__tool"

    def test_get_policy_not_called_if_not_registered(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        # not registered
        resolver.resolve("alice", "mcp__alice__tool")
        assert registry.get_policy_calls == []

    def test_tool_registered_after_init_resolves(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        # First call fails
        assert resolver.resolve("alice", "mcp__alice__tool") is None
        # Register after, second call succeeds
        registry.register("mcp__alice__tool", MCPPolicy.LOCAL_WRITE)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.LOCAL_WRITE

    def test_idempotent_resolve(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        r1 = resolver.resolve("alice", "mcp__alice__tool")
        r2 = resolver.resolve("alice", "mcp__alice__tool")
        assert r1 == r2 == MCPPolicy.READ_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — End-to-end happy path
# ══════════════════════════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_happy_path_read_only(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__read_doc", MCPPolicy.READ_ONLY)
        assert resolver.resolve("alice", "mcp__alice__read_doc") == MCPPolicy.READ_ONLY

    @pytest.mark.parametrize("policy", list(MCPPolicy))
    def test_happy_path_all_policies(self, resolver, registry, catalog, policy):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", policy)
        assert resolver.resolve("alice", "mcp__alice__tool") == policy

    def test_resolve_ok_audit(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        resolver.resolve("alice", "mcp__alice__tool")
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_ok"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["tool_name"] == "mcp__alice__tool"
        assert ev["policy"] == "read_only"

    def test_tool_absent_active_server_returns_none(self, resolver, catalog):
        _add_active_server(catalog, "alice")
        assert resolver.resolve("alice", "mcp__alice__tool") is None

    def test_no_state_between_resolutions(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__a", MCPPolicy.READ_ONLY)
        registry.register("mcp__alice__b", MCPPolicy.LOCAL_WRITE)
        assert resolver.resolve("alice", "mcp__alice__a") == MCPPolicy.READ_ONLY
        assert resolver.resolve("alice", "mcp__alice__b") == MCPPolicy.LOCAL_WRITE
        assert resolver.resolve("alice", "mcp__alice__a") == MCPPolicy.READ_ONLY

    def test_multi_server_isolation(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        _add_active_server(catalog, "bob")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        registry.register("mcp__bob__tool", MCPPolicy.LOCAL_WRITE)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.READ_ONLY
        assert resolver.resolve("bob", "mcp__bob__tool") == MCPPolicy.LOCAL_WRITE
        # Cross-server : binding mismatch
        assert resolver.resolve("alice", "mcp__bob__tool") is None
        assert resolver.resolve("bob", "mcp__alice__tool") is None

    def test_resolve_with_real_catalog(self, resolver, registry, catalog):
        """Test avec vraie instance MCPServerCatalog (pas mock)."""
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.EXTERNAL_READ)
        assert resolver.resolve("alice", "mcp__alice__tool") == MCPPolicy.EXTERNAL_READ
        # is_callable utilisé en interne (vérifié indirectement par succès)
        assert catalog.is_callable("alice") is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Audit forensique no-PII (anti-leak inputs invalides)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensic:
    # Correction 3 — anti-leak inputs invalides
    def test_audit_no_leak_when_server_id_invalid(self, resolver):
        """server_id avec marker secret → audit NE LOG NI server_id NI tool_name."""
        resolver.resolve(
            "ATTACKER_SECRET_MARKER_AAA",
            "mcp__alice__ATTACKER_SECRET_MARKER_BBB",
        )
        blob = _audit_blob(resolver)
        assert "ATTACKER_SECRET_MARKER_AAA" not in blob
        assert "ATTACKER_SECRET_MARKER_BBB" not in blob
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        ev = events[-1]
        assert "server_id" not in ev
        assert "tool_name" not in ev
        assert ev["reason"] == "server_id_invalid"

    def test_audit_no_tool_name_leak_when_tool_name_invalid(self, resolver):
        """server_id valide mais tool_name avec marker → log server_id PAS tool_name."""
        resolver.resolve(
            "alice",
            "!!!ATTACKER_TOOL_MARKER_CCC!!!",
        )
        blob = _audit_blob(resolver)
        assert "ATTACKER_TOOL_MARKER_CCC" not in blob
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        ev = events[-1]
        assert ev.get("server_id") == "alice"
        assert "tool_name" not in ev
        assert ev["reason"] == "tool_name_invalid"

    def test_audit_logs_both_when_tool_server_mismatch(self, resolver):
        """server_id et tool_name valides individuellement, binding échoue
        → log les deux (codes courts)."""
        resolver.resolve("alice", "mcp__bob__exec")
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        ev = events[-1]
        assert ev.get("server_id") == "alice"
        assert ev.get("tool_name") == "mcp__bob__exec"
        assert ev["reason"] == "tool_server_mismatch"

    def test_audit_no_leak_both_inputs_invalid(self, resolver):
        """Les deux inputs invalides avec markers → aucun leak."""
        resolver.resolve("MARKER_DDD", "MARKER_EEE")
        blob = _audit_blob(resolver)
        assert "MARKER_DDD" not in blob
        assert "MARKER_EEE" not in blob

    def test_audit_does_not_stringify_dependencies(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        resolver.resolve("alice", "mcp__alice__tool")
        blob = _audit_blob(resolver)
        assert "_FakeToolRegistry" not in blob
        assert "MCPServerCatalog" not in blob
        assert "object at 0x" not in blob

    def test_audit_no_serverentry_fields(self, resolver, catalog):
        catalog.add_server(
            server_id="alice",
            display_name="FORENSIC_DISPLAY_FFF",
            package_spec="npm:mcp-forensic-pkg-ggg",
            owner_profile="alice",
            notes="forensic-notes-hhh",
        )
        # status DECLARED → server_not_callable
        resolver.resolve("alice", "mcp__alice__tool")
        blob = _audit_blob(resolver)
        assert "FORENSIC_DISPLAY_FFF" not in blob
        assert "mcp-forensic-pkg-ggg" not in blob
        assert "forensic-notes-hhh" not in blob

    def test_audit_reason_codes_short(self, resolver, registry, catalog):
        """Tous les reason codes sont des codes courts pré-définis."""
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        resolver.resolve("alice", "mcp__alice__tool")
        resolver.resolve("alice", "mcp__alice__absent")  # tool_not_registered
        events = _audit_lines(resolver)
        valid_reasons = {
            "server_id_invalid", "tool_name_invalid", "tool_server_mismatch",
            "server_unknown", "server_removed", "server_not_callable",
            "tool_not_registered", "policy_not_attributed", "policy_invalid",
            "dependency_error",
        }
        for ev in events:
            if ev["event"] == "resolve_failed":
                assert ev["reason"] in valid_reasons
            elif ev["event"] == "resolve_ok":
                assert ev["policy"] in {p.value for p in MCPPolicy}

    def test_audit_multi_resolve_forensic_scan(self, resolver, registry, catalog):
        """Résolutions variées avec markers dans des INPUTS INVALIDES → aucune fuite.

        Note : pour binding mismatch, server_id et tool_name sont tous deux
        validés individuellement et DOIVENT être loggués (par spec). Donc on
        n'utilise pas de marker secret dans le tool_name de cette branche.
        """
        markers = [f"FORENSIC_RESOLVER_MARKER_{i}" for i in range(10)]
        for i, m in enumerate(markers):
            if i % 2 == 0:
                # server_id invalide (marker dans server_id et tool_name invalides)
                resolver.resolve(m, f"!!!{m}!!!")
            else:
                # tool_name invalide (marker dans tool_name uniquement)
                resolver.resolve("alice", m)
        blob = _audit_blob(resolver)
        for m in markers:
            assert m not in blob, f"marker {m} leaked"
            assert m.lower() not in blob, f"marker {m.lower()} leaked"

    def test_audit_identifiers_present_for_resolve_ok(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.LOCAL_WRITE)
        resolver.resolve("alice", "mcp__alice__tool")
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_ok"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["tool_name"] == "mcp__alice__tool"
        assert ev["policy"] == "local_write"

    def test_audit_exception_traceback_does_not_leak(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        # Provoquer une exception spécifique avec message identifiable
        original = registry.is_dynamic_handler
        def boom(name):
            raise RuntimeError("INTERNAL_TRACEBACK_LEAK_MARKER_XYZ")
        registry.is_dynamic_handler = boom  # type: ignore
        resolver.resolve("alice", "mcp__alice__tool")
        blob = _audit_blob(resolver)
        assert "INTERNAL_TRACEBACK_LEAK_MARKER_XYZ" not in blob
        events = [e for e in _audit_lines(resolver) if e["event"] == "resolve_failed"]
        assert events[-1]["reason"] == "dependency_error"
        registry.is_dynamic_handler = original  # type: ignore

    def test_audit_no_module_paths(self, resolver, registry, catalog):
        _add_active_server(catalog, "alice")
        registry.register("mcp__alice__tool", MCPPolicy.READ_ONLY)
        resolver.resolve("alice", "mcp__alice__tool")
        blob = _audit_blob(resolver)
        assert "src.mcp" not in blob
        assert "C:\\" not in blob
        assert "/home/" not in blob
