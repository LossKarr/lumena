"""
Tests Phase 14 v3 — MCPServerCatalog.

Sections :
  1. Init & configuration
  2. add_server validation (+ forensique package_spec par transport)
  3. get_server / list_servers (+ include_removed)
  4. Transitions de statut (+ same-same, last_active auto)
  5. update_trust_score
  6. update_last_active
  7. remove_server
  8. is_callable / is_known
  9. Persistance + HMAC + binding fichier↔contenu
  10. Audit forensique no-PII
  11. Anti-confused-deputy / sanity
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.server_catalog import (
    CatalogError,
    MCPServerCatalog,
    ServerEntry,
    ServerStatus,
    _ALLOWED_TRANSITIONS,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


class _InMemorySecretsService:
    def __init__(self):
        self._store: Dict[str, str] = {}

    def get(self, scope: str, name: str) -> Optional[str]:
        return self._store.get(f"{scope}::{name}")

    def set(self, scope: str, name: str, value: str) -> None:
        self._store[f"{scope}::{name}"] = value


@pytest.fixture
def catalog(tmp_path: Path) -> MCPServerCatalog:
    return MCPServerCatalog(
        catalog_dir=tmp_path / "catalog",
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
        secrets_service=_InMemorySecretsService(),
    )


def _add_minimal(
    catalog: MCPServerCatalog,
    *,
    server_id: str = "alice-mcp",
    display_name: str = "Alice MCP",
    package_spec: str = "npm:mcp-foo",
    owner_profile: str = "alice",
    version: Optional[str] = None,
    trust_score: Optional[int] = None,
    notes: Optional[str] = None,
) -> ServerEntry:
    return catalog.add_server(
        server_id=server_id,
        display_name=display_name,
        package_spec=package_spec,
        owner_profile=owner_profile,
        version=version,
        trust_score=trust_score,
        notes=notes,
    )


def _audit_lines(catalog: MCPServerCatalog) -> List[Dict[str, Any]]:
    if not catalog.audit_log_path.exists():
        return []
    out = []
    with open(catalog.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(catalog: MCPServerCatalog) -> str:
    if not catalog.audit_log_path.exists():
        return ""
    return catalog.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_default_dirs_created(self, catalog):
        assert catalog.servers_dir.exists()
        assert catalog.audit_log_path.parent.exists()

    def test_servers_dir_property(self, catalog):
        assert catalog.servers_dir.name == "servers"

    def test_audit_log_path_property(self, catalog):
        assert catalog.audit_log_path.name == "audit.jsonl"


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — add_server validation
# ══════════════════════════════════════════════════════════════════════════════


class TestServerIdValidation:
    def test_invalid_uppercase_rejected(self, catalog):
        with pytest.raises(CatalogError, match="server_id"):
            _add_minimal(catalog, server_id="ALICE")

    def test_invalid_empty_rejected(self, catalog):
        with pytest.raises(CatalogError, match="server_id"):
            _add_minimal(catalog, server_id="")

    def test_windows_reserved_rejected(self, catalog):
        with pytest.raises(CatalogError, match="windows_reserved"):
            _add_minimal(catalog, server_id="con")

    def test_windows_reserved_with_extension_rejected(self, catalog):
        with pytest.raises(CatalogError, match="windows_reserved"):
            _add_minimal(catalog, server_id="con.json")

    def test_path_traversal_rejected(self, catalog):
        with pytest.raises(CatalogError, match="server_id"):
            _add_minimal(catalog, server_id="foo..bar")


class TestDisplayNameValidation:
    def test_empty_rejected(self, catalog):
        with pytest.raises(CatalogError, match="display_name"):
            _add_minimal(catalog, display_name="")

    def test_null_char_rejected(self, catalog):
        with pytest.raises(CatalogError, match="display_name"):
            _add_minimal(catalog, display_name="foo\x00bar")

    def test_newline_rejected(self, catalog):
        with pytest.raises(CatalogError, match="display_name"):
            _add_minimal(catalog, display_name="foo\nbar")

    def test_too_long_rejected(self, catalog):
        with pytest.raises(CatalogError, match="display_name"):
            _add_minimal(catalog, display_name="x" * 201)

    def test_valid_with_unicode_ok(self, catalog):
        entry = _add_minimal(catalog, display_name="My Server 🚀")
        assert entry.display_name == "My Server 🚀"


class TestPackageSpecValidation:
    @pytest.mark.parametrize("bad_spec", [
        # URL
        "https://evil.com/pkg.tar.gz",
        "http://attacker/x",
        # Path traversal
        "local:../../../etc/passwd",
        "npm:foo..bar",
        # Backslash
        "local:foo\\bar",
        "npm:foo\\bar",
        # Drive Windows
        "C:\\evil.exe",
        "C:/evil.exe",
        "D:foo",
        # Espaces / tabs
        "npm:foo bar",
        "pypi:foo\tbar",
        # Shell metachars
        "local:foo;rm",
        "local:foo&calc.exe",
        "npm:foo|cat",
        "local:`whoami`",
        "local:$(whoami)",
        # Null
        "local:foo\x00bar",
        # Empty / pas de transport
        "",
        "just-a-name",
        # Wrong transport
        "ftp:foo",
        "file:///etc/passwd",
        # local: avec chemin
        "local:./foo",
        # Quotes
        'local:foo"bar',
        "local:foo'bar",
    ])
    def test_package_spec_rejected(self, catalog, bad_spec):
        with pytest.raises(CatalogError, match="package_spec"):
            _add_minimal(catalog, server_id="srv-x", package_spec=bad_spec)

    @pytest.mark.parametrize("good_spec", [
        "npm:@anthropic/mcp-slack",
        "npm:mcp-foo",
        "pypi:mcp-bar",
        "pypi:requests",
        "local:my-server",
        "local:my_server_42",
    ])
    def test_package_spec_accepted(self, catalog, good_spec):
        sid = f"srv-{abs(hash(good_spec)) % 100000}"
        _add_minimal(catalog, server_id=sid, package_spec=good_spec)

    # Correction 1 — slash par transport
    def test_npm_scoped_with_slash_accepted(self, catalog):
        _add_minimal(
            catalog, server_id="srv1",
            package_spec="npm:@anthropic/mcp-slack",
        )

    def test_npm_double_slash_refused(self, catalog):
        with pytest.raises(CatalogError, match="package_spec_npm"):
            _add_minimal(
                catalog, server_id="srv2",
                package_spec="npm:foo/bar",
            )

    def test_pypi_with_slash_refused(self, catalog):
        with pytest.raises(CatalogError, match="package_spec"):
            _add_minimal(
                catalog, server_id="srv3",
                package_spec="pypi:foo/bar",
            )

    def test_local_with_slash_refused(self, catalog):
        with pytest.raises(CatalogError, match="package_spec"):
            _add_minimal(
                catalog, server_id="srv4",
                package_spec="local:foo/bar",
            )

    # Correction 2 — version séparée
    @pytest.mark.parametrize("spec_with_version", [
        "npm:mcp-foo@latest",
        "npm:@anthropic/mcp-slack@1.2.3",
        "pypi:mcp-bar==1.0.0",
    ])
    def test_package_spec_with_version_refused(self, catalog, spec_with_version):
        with pytest.raises(CatalogError, match="package_spec"):
            _add_minimal(
                catalog, server_id="srv-v",
                package_spec=spec_with_version,
            )

    def test_package_spec_clean_with_version_field(self, catalog):
        entry = _add_minimal(
            catalog, server_id="srv-v1",
            package_spec="npm:mcp-foo", version="latest",
        )
        assert entry.package_spec == "npm:mcp-foo"
        assert entry.version == "latest"

    def test_package_spec_pypi_with_version_field(self, catalog):
        entry = _add_minimal(
            catalog, server_id="srv-v2",
            package_spec="pypi:mcp-bar", version="1.0.0",
        )
        assert entry.version == "1.0.0"


class TestVersionValidation:
    def test_version_none_ok(self, catalog):
        entry = _add_minimal(catalog, server_id="srv-vn", version=None)
        assert entry.version is None

    def test_version_too_long_rejected(self, catalog):
        with pytest.raises(CatalogError, match="version"):
            _add_minimal(catalog, server_id="srv-vt", version="x" * 65)

    def test_version_with_space_rejected(self, catalog):
        with pytest.raises(CatalogError, match="version"):
            _add_minimal(catalog, server_id="srv-vs", version="1.0 0")


class TestOwnerProfileValidation:
    def test_invalid_uppercase_rejected(self, catalog):
        with pytest.raises(CatalogError, match="owner_profile"):
            _add_minimal(catalog, owner_profile="ALICE")

    def test_empty_rejected(self, catalog):
        with pytest.raises(CatalogError, match="owner_profile"):
            _add_minimal(catalog, owner_profile="")


class TestTrustScoreValidation:
    def test_none_ok(self, catalog):
        entry = _add_minimal(catalog, server_id="srv-tn", trust_score=None)
        assert entry.trust_score is None

    def test_negative_rejected(self, catalog):
        with pytest.raises(CatalogError, match="trust_score"):
            _add_minimal(catalog, server_id="srv-t1", trust_score=-1)

    def test_above_100_rejected(self, catalog):
        with pytest.raises(CatalogError, match="trust_score"):
            _add_minimal(catalog, server_id="srv-t2", trust_score=101)

    def test_bool_rejected(self, catalog):
        with pytest.raises(CatalogError, match="trust_score"):
            _add_minimal(catalog, server_id="srv-tb", trust_score=True)  # type: ignore

    def test_float_rejected(self, catalog):
        with pytest.raises(CatalogError, match="trust_score"):
            _add_minimal(catalog, server_id="srv-tf", trust_score=80.5)  # type: ignore

    def test_valid_range(self, catalog):
        for sc in [0, 50, 100]:
            sid = f"srv-ts{sc}"
            entry = _add_minimal(catalog, server_id=sid, trust_score=sc)
            assert entry.trust_score == sc


class TestNotesValidation:
    def test_none_ok(self, catalog):
        entry = _add_minimal(catalog, server_id="srv-nn", notes=None)
        assert entry.notes is None

    def test_too_long_rejected(self, catalog):
        with pytest.raises(CatalogError, match="notes"):
            _add_minimal(catalog, server_id="srv-nl", notes="x" * 257)

    def test_special_chars_rejected(self, catalog):
        with pytest.raises(CatalogError, match="notes"):
            _add_minimal(catalog, server_id="srv-ns", notes="hello@world")

    def test_short_code_ok(self, catalog):
        entry = _add_minimal(
            catalog, server_id="srv-nok",
            notes="reason:audit_clean",
        )
        assert entry.notes == "reason:audit_clean"


class TestAddServerGeneral:
    def test_add_returns_entry_status_declared(self, catalog):
        entry = _add_minimal(catalog)
        assert entry.status == ServerStatus.DECLARED

    def test_added_at_equals_updated_at(self, catalog):
        entry = _add_minimal(catalog)
        assert entry.added_at == entry.updated_at

    def test_double_add_same_server_id_rejected(self, catalog):
        _add_minimal(catalog, server_id="dup")
        with pytest.raises(CatalogError, match="server_already_exists"):
            _add_minimal(catalog, server_id="dup")

    def test_last_active_at_initially_none(self, catalog):
        entry = _add_minimal(catalog)
        assert entry.last_active_at is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — get_server / list_servers
# ══════════════════════════════════════════════════════════════════════════════


class TestGetListServers:
    def test_get_unknown_returns_none(self, catalog):
        assert catalog.get_server("unknown-sid") is None

    def test_get_invalid_id_raises(self, catalog):
        with pytest.raises(CatalogError):
            catalog.get_server("INVALID")

    def test_list_empty(self, catalog):
        assert catalog.list_servers() == []

    def test_list_sorted_by_id(self, catalog):
        _add_minimal(catalog, server_id="zeta")
        _add_minimal(catalog, server_id="alpha")
        _add_minimal(catalog, server_id="beta")
        ids = [e.server_id for e in catalog.list_servers()]
        assert ids == ["alpha", "beta", "zeta"]

    def test_list_with_status_filter(self, catalog):
        _add_minimal(catalog, server_id="srv-a")
        _add_minimal(catalog, server_id="srv-b")
        catalog.update_status("srv-a", ServerStatus.INSTALLED)
        only_installed = catalog.list_servers(status_filter=ServerStatus.INSTALLED)
        ids = [e.server_id for e in only_installed]
        assert ids == ["srv-a"]

    def test_list_with_owner_profile_filter(self, catalog):
        _add_minimal(catalog, server_id="srv-a", owner_profile="alice")
        _add_minimal(catalog, server_id="srv-b", owner_profile="bob")
        alice_servers = catalog.list_servers(owner_profile_filter="alice")
        assert len(alice_servers) == 1
        assert alice_servers[0].server_id == "srv-a"

    def test_list_excludes_removed_by_default(self, catalog):
        _add_minimal(catalog, server_id="srv-keep")
        _add_minimal(catalog, server_id="srv-gone")
        catalog.remove_server("srv-gone")
        ids = [e.server_id for e in catalog.list_servers()]
        assert "srv-keep" in ids
        assert "srv-gone" not in ids

    def test_list_include_removed_true(self, catalog):
        _add_minimal(catalog, server_id="srv-keep")
        _add_minimal(catalog, server_id="srv-gone")
        catalog.remove_server("srv-gone")
        ids = [e.server_id for e in catalog.list_servers(include_removed=True)]
        assert "srv-keep" in ids
        assert "srv-gone" in ids

    def test_list_status_filter_removed_with_include_false_returns_empty(self, catalog):
        _add_minimal(catalog, server_id="srv-gone")
        catalog.remove_server("srv-gone")
        out = catalog.list_servers(
            status_filter=ServerStatus.REMOVED, include_removed=False
        )
        assert out == []

    def test_list_combination_filters(self, catalog):
        _add_minimal(catalog, server_id="srv-a", owner_profile="alice")
        _add_minimal(catalog, server_id="srv-b", owner_profile="alice")
        catalog.update_status("srv-a", ServerStatus.INSTALLED)
        out = catalog.list_servers(
            status_filter=ServerStatus.INSTALLED,
            owner_profile_filter="alice",
        )
        assert len(out) == 1
        assert out[0].server_id == "srv-a"

    def test_list_invalid_status_filter_type(self, catalog):
        with pytest.raises(CatalogError, match="status_filter_type"):
            catalog.list_servers(status_filter="installed")  # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Transitions de statut
# ══════════════════════════════════════════════════════════════════════════════


class TestTransitions:
    def test_declared_to_installed_ok(self, catalog):
        _add_minimal(catalog)
        e = catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert e.status == ServerStatus.INSTALLED

    def test_declared_to_active_refused(self, catalog):
        _add_minimal(catalog)
        with pytest.raises(CatalogError, match="status_transition_invalid"):
            catalog.update_status("alice-mcp", ServerStatus.ACTIVE)

    def test_installed_to_active_ok(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        e = catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        assert e.status == ServerStatus.ACTIVE

    def test_active_to_installed_rollback_ok(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        e = catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert e.status == ServerStatus.INSTALLED

    def test_active_to_quarantined_ok(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        e = catalog.update_status("alice-mcp", ServerStatus.QUARANTINED)
        assert e.status == ServerStatus.QUARANTINED

    def test_quarantined_to_active_ok(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.QUARANTINED)
        e = catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        assert e.status == ServerStatus.ACTIVE

    def test_removed_terminal_no_transitions(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.REMOVED)
        for target in [
            ServerStatus.DECLARED, ServerStatus.INSTALLED,
            ServerStatus.ACTIVE, ServerStatus.QUARANTINED,
        ]:
            with pytest.raises(CatalogError, match="status_transition_invalid"):
                catalog.update_status("alice-mcp", target)

    def test_declared_to_removed_ok(self, catalog):
        _add_minimal(catalog)
        e = catalog.update_status("alice-mcp", ServerStatus.REMOVED)
        assert e.status == ServerStatus.REMOVED

    def test_installed_to_removed_ok(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        e = catalog.update_status("alice-mcp", ServerStatus.REMOVED)
        assert e.status == ServerStatus.REMOVED

    @pytest.mark.parametrize("status", [
        ServerStatus.DECLARED, ServerStatus.INSTALLED,
        ServerStatus.ACTIVE, ServerStatus.QUARANTINED, ServerStatus.REMOVED,
    ])
    def test_same_to_same_refused(self, catalog, status):
        """Aucune self-loop dans la table : same → same toujours refusé."""
        _add_minimal(catalog)
        # Amener au status cible
        if status == ServerStatus.INSTALLED:
            catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        elif status == ServerStatus.ACTIVE:
            catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
            catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        elif status == ServerStatus.QUARANTINED:
            catalog.update_status("alice-mcp", ServerStatus.QUARANTINED)
        elif status == ServerStatus.REMOVED:
            catalog.update_status("alice-mcp", ServerStatus.REMOVED)
        # status DECLARED reste par défaut
        # Tentative same → same doit raise
        with pytest.raises(CatalogError, match="status_transition_invalid"):
            catalog.update_status("alice-mcp", status)

    def test_transition_invalid_type(self, catalog):
        _add_minimal(catalog)
        with pytest.raises(CatalogError, match="new_status_type"):
            catalog.update_status("alice-mcp", "installed")  # type: ignore

    def test_transition_unknown_server(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_status("ghost", ServerStatus.INSTALLED)

    def test_audit_server_status_changed(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        events = [e for e in _audit_lines(catalog) if e["event"] == "server_status_changed"]
        assert events
        ev = events[-1]
        assert ev["from_status"] == "declared"
        assert ev["to_status"] == "installed"

    def test_updated_at_modified_on_transition(self, catalog):
        import time as _time
        e1 = _add_minimal(catalog)
        # Force le clock à avancer d'au moins 1ms : sur Windows, deux appels
        # à datetime.now().isoformat() consécutifs peuvent retomber sur la
        # même microseconde et produire un faux échec.
        _time.sleep(0.002)
        e2 = catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert e2.updated_at != e1.updated_at

    # Correction 6 — last_active_at auto
    def test_transition_to_active_sets_last_active_at_automatically(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        e = catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        assert e.last_active_at is not None

    def test_transition_to_installed_does_not_set_last_active_at(self, catalog):
        _add_minimal(catalog)
        e = catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert e.last_active_at is None

    def test_transition_to_quarantined_does_not_set_last_active_at(self, catalog):
        _add_minimal(catalog)
        e = catalog.update_status("alice-mcp", ServerStatus.QUARANTINED)
        assert e.last_active_at is None

    def test_last_active_at_preserved_when_active_to_installed(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        e_active = catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        active_ts = e_active.last_active_at
        e_inst = catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert e_inst.last_active_at == active_ts

    def test_allowed_transitions_no_self_loop(self):
        for src, dests in _ALLOWED_TRANSITIONS.items():
            assert src not in dests, f"self-loop in {src}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — update_trust_score
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateTrustScore:
    def test_update_valid(self, catalog):
        _add_minimal(catalog, trust_score=50)
        e = catalog.update_trust_score("alice-mcp", 80)
        assert e.trust_score == 80

    def test_update_out_of_range(self, catalog):
        _add_minimal(catalog)
        with pytest.raises(CatalogError, match="trust_score"):
            catalog.update_trust_score("alice-mcp", 150)

    def test_update_none_rejected(self, catalog):
        _add_minimal(catalog, trust_score=50)
        with pytest.raises(CatalogError, match="trust_score_none"):
            catalog.update_trust_score("alice-mcp", None)  # type: ignore

    def test_update_unknown_server(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_trust_score("ghost", 50)

    def test_audit_trust_score_updated(self, catalog):
        _add_minimal(catalog)
        catalog.update_trust_score("alice-mcp", 75)
        events = [e for e in _audit_lines(catalog) if e["event"] == "server_trust_score_updated"]
        assert events
        assert events[-1]["trust_score"] == 75


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — update_last_active
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateLastActive:
    def test_last_active_at_updated(self, catalog):
        _add_minimal(catalog)
        e = catalog.update_last_active("alice-mcp")
        assert e.last_active_at is not None

    def test_audit_last_active_event(self, catalog):
        _add_minimal(catalog)
        catalog.update_last_active("alice-mcp")
        events = [e for e in _audit_lines(catalog) if e["event"] == "server_last_active_updated"]
        assert events

    def test_unknown_server_raises(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_last_active("ghost")


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — remove_server
# ══════════════════════════════════════════════════════════════════════════════


class TestRemoveServer:
    def test_remove_sets_status_removed(self, catalog):
        _add_minimal(catalog)
        assert catalog.remove_server("alice-mcp") is True
        e = catalog.get_server("alice-mcp")
        assert e is not None
        assert e.status == ServerStatus.REMOVED

    def test_remove_idempotent(self, catalog):
        _add_minimal(catalog)
        assert catalog.remove_server("alice-mcp") is True
        assert catalog.remove_server("alice-mcp") is False

    def test_remove_unknown_returns_false(self, catalog):
        assert catalog.remove_server("ghost") is False

    def test_remove_excluded_from_default_list(self, catalog):
        _add_minimal(catalog, server_id="srv-x")
        catalog.remove_server("srv-x")
        assert all(e.server_id != "srv-x" for e in catalog.list_servers())

    def test_audit_server_removed(self, catalog):
        _add_minimal(catalog)
        catalog.remove_server("alice-mcp")
        events = [e for e in _audit_lines(catalog) if e["event"] == "server_removed"]
        assert events


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — is_callable / is_known
# ══════════════════════════════════════════════════════════════════════════════


class TestIsCallableIsKnown:
    def test_is_callable_active_true(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        assert catalog.is_callable("alice-mcp") is True

    def test_is_callable_installed_false(self, catalog):
        """Correction 2 : INSTALLED ne suffit pas pour callable."""
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        assert catalog.is_callable("alice-mcp") is False

    def test_is_callable_declared_false(self, catalog):
        _add_minimal(catalog)
        assert catalog.is_callable("alice-mcp") is False

    def test_is_callable_quarantined_false(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.QUARANTINED)
        assert catalog.is_callable("alice-mcp") is False

    def test_is_callable_removed_false(self, catalog):
        _add_minimal(catalog)
        catalog.remove_server("alice-mcp")
        assert catalog.is_callable("alice-mcp") is False

    def test_is_callable_unknown_false(self, catalog):
        assert catalog.is_callable("ghost") is False

    def test_is_known_true_for_present_non_removed(self, catalog):
        _add_minimal(catalog)
        assert catalog.is_known("alice-mcp") is True

    def test_is_known_false_for_removed(self, catalog):
        _add_minimal(catalog)
        catalog.remove_server("alice-mcp")
        assert catalog.is_known("alice-mcp") is False

    def test_is_known_false_for_unknown(self, catalog):
        assert catalog.is_known("ghost") is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Persistance disque + HMAC + binding
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistenceHMACBinding:
    def test_entry_written_at_expected_path(self, catalog):
        _add_minimal(catalog, server_id="srv-w")
        path = catalog.servers_dir / "srv-w.json"
        assert path.exists()

    def test_load_roundtrip(self, catalog):
        original = _add_minimal(
            catalog, server_id="srv-r",
            package_spec="npm:mcp-foo", version="1.2.3",
        )
        loaded = catalog.get_server("srv-r")
        assert loaded is not None
        assert loaded.server_id == original.server_id
        assert loaded.package_spec == "npm:mcp-foo"
        assert loaded.version == "1.2.3"

    def test_disk_contains_entry_and_integrity_hmac(self, catalog):
        _add_minimal(catalog, server_id="srv-h")
        path = catalog.servers_dir / "srv-h.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "entry" in data
        assert "integrity_hmac" in data

    def test_hmac_mismatch_skipped_and_audited(self, catalog):
        _add_minimal(catalog, server_id="srv-hm")
        path = catalog.servers_dir / "srv-hm.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        # Tamper avec une valeur métier sans recalcul HMAC
        data["entry"]["display_name"] = "TAMPERED"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert catalog.get_server("srv-hm") is None
        events = [e for e in _audit_lines(catalog) if e["event"] == "integrity_invalid"]
        assert events

    def test_malformed_json_skipped(self, catalog):
        _add_minimal(catalog, server_id="srv-mal")
        path = catalog.servers_dir / "srv-mal.json"
        path.write_text("not valid json", encoding="utf-8")
        assert catalog.get_server("srv-mal") is None

    def test_binding_mismatch_filename_vs_record(self, catalog):
        """Copie alice.json → bob.json sans toucher au record → binding détecté."""
        _add_minimal(catalog, server_id="alice")
        src = catalog.servers_dir / "alice.json"
        dst = catalog.servers_dir / "bob.json"
        dst.write_bytes(src.read_bytes())
        # get_server("bob") doit refuser : record.server_id == "alice" mais file.stem == "bob"
        assert catalog.get_server("bob") is None
        events = [e for e in _audit_lines(catalog) if e["event"] == "binding_mismatch"]
        assert events
        assert events[-1]["server_id"] == "bob"  # filename, pas record value

    def test_binding_mismatch_audit_does_not_contain_record_fields(self, catalog):
        """Forensique : copie d'un server tamperé → audit ne contient pas
        les champs métier du record copié."""
        _add_minimal(
            catalog,
            server_id="alice-leak",
            display_name="DISPLAY_LEAK_MARKER_NEVER_LOG",
            package_spec="npm:pkg-leak-marker-zzz",
            owner_profile="alice",
        )
        src = catalog.servers_dir / "alice-leak.json"
        dst = catalog.servers_dir / "bob-leak.json"
        dst.write_bytes(src.read_bytes())
        catalog.get_server("bob-leak")
        blob = _audit_blob(catalog)
        assert "DISPLAY_LEAK_MARKER" not in blob
        # package_spec (lowercase respectant regex npm) ne doit pas non plus fuiter
        assert "pkg-leak-marker-zzz" not in blob

    def test_renamed_pattern_file_skipped(self, catalog):
        """Renommer alice.json → renamed-x.json détecté."""
        _add_minimal(catalog, server_id="alice")
        src = catalog.servers_dir / "alice.json"
        dst = catalog.servers_dir / "renamed-x.json"
        src.rename(dst)
        # get_server("alice") : fichier disparu, None
        assert catalog.get_server("alice") is None
        # get_server("renamed-x") : record.server_id != filename, binding mismatch
        assert catalog.get_server("renamed-x") is None
        # list_servers ignore les deux
        ids = [e.server_id for e in catalog.list_servers()]
        assert "alice" not in ids
        assert "renamed-x" not in ids

    def test_list_servers_ignores_corrupted_entries(self, catalog):
        _add_minimal(catalog, server_id="srv-good")
        _add_minimal(catalog, server_id="srv-bad")
        bad_path = catalog.servers_dir / "srv-bad.json"
        bad_path.write_text("garbage", encoding="utf-8")
        ids = [e.server_id for e in catalog.list_servers()]
        assert "srv-good" in ids
        assert "srv-bad" not in ids

    def test_integrity_audit_uses_filename(self, catalog):
        _add_minimal(catalog, server_id="srv-pid")
        path = catalog.servers_dir / "srv-pid.json"
        path.write_text("garbage", encoding="utf-8")
        catalog.get_server("srv-pid")
        events = [e for e in _audit_lines(catalog) if e["event"] == "integrity_invalid"]
        assert events
        assert events[-1]["server_id"] == "srv-pid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensicNoPII:
    def test_audit_never_contains_display_name(self, catalog):
        _add_minimal(
            catalog, server_id="srv-d",
            display_name="DISPLAY_LEAK_AAA_FORENSIC",
        )
        catalog.update_status("srv-d", ServerStatus.INSTALLED)
        catalog.update_status("srv-d", ServerStatus.ACTIVE)
        catalog.remove_server("srv-d")
        blob = _audit_blob(catalog)
        assert "DISPLAY_LEAK_AAA_FORENSIC" not in blob

    def test_audit_never_contains_package_spec(self, catalog):
        _add_minimal(
            catalog, server_id="srv-p",
            package_spec="npm:pkg-leak-marker-bbb",
        )
        blob = _audit_blob(catalog)
        assert "pkg-leak-marker-bbb" not in blob

    def test_audit_never_contains_version(self, catalog):
        _add_minimal(
            catalog, server_id="srv-v",
            version="LEAK-VERSION-CCC",
        )
        blob = _audit_blob(catalog)
        assert "LEAK-VERSION-CCC" not in blob

    def test_audit_never_contains_notes(self, catalog):
        _add_minimal(
            catalog, server_id="srv-n",
            notes="leak-notes-marker-ddd",
        )
        blob = _audit_blob(catalog)
        assert "leak-notes-marker-ddd" not in blob

    def test_audit_identifiers_present(self, catalog):
        _add_minimal(catalog, server_id="srv-id", owner_profile="alice")
        events = [e for e in _audit_lines(catalog) if e["event"] == "server_added"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "srv-id"
        assert ev["owner_profile"] == "alice"

    def test_audit_codes_short_only(self, catalog):
        _add_minimal(catalog)
        catalog.update_status("alice-mcp", ServerStatus.INSTALLED)
        catalog.update_status("alice-mcp", ServerStatus.ACTIVE)
        events = _audit_lines(catalog)
        for ev in events:
            for k, v in ev.items():
                if k in ("ts", "event", "server_id", "owner_profile",
                         "from_status", "to_status", "status",
                         "trust_score", "reason"):
                    continue
                # Aucun champ inattendu
                assert False, f"unexpected audit field {k}={v!r}"

    def test_audit_multi_scenario_no_pii_scan(self, catalog):
        markers = [f"FORENSIC_MARKER_{i}_AAA" for i in range(10)]
        for i, m in enumerate(markers):
            sid = f"srv-m{i}"
            try:
                catalog.add_server(
                    server_id=sid,
                    display_name=m,
                    package_spec="npm:mcp-foo",
                    owner_profile="alice",
                    notes=f"code-{i}",
                )
                catalog.update_status(sid, ServerStatus.INSTALLED)
                catalog.update_trust_score(sid, 80)
                catalog.update_last_active(sid)
            except CatalogError:
                pass
        blob = _audit_blob(catalog)
        for m in markers:
            assert m not in blob, f"marker {m} leaked in audit"

    def test_audit_no_stringification_of_entry(self, catalog):
        _add_minimal(catalog)
        blob = _audit_blob(catalog)
        assert "ServerEntry(" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Anti-confused-deputy / sanity
# ══════════════════════════════════════════════════════════════════════════════


class TestSanityConfusedDeputy:
    def test_owner_filter_does_not_leak_other_owners(self, catalog):
        _add_minimal(catalog, server_id="srv-alice", owner_profile="alice")
        _add_minimal(catalog, server_id="srv-bob", owner_profile="bob")
        bob_servers = catalog.list_servers(owner_profile_filter="bob")
        assert all(e.owner_profile == "bob" for e in bob_servers)

    def test_copied_file_owner_b_does_not_get_alice_server(self, catalog):
        """Si on copie srv-alice.json en srv-bob.json, get_server('srv-bob')
        doit refuser (binding mismatch)."""
        _add_minimal(catalog, server_id="srv-alice", owner_profile="alice")
        src = catalog.servers_dir / "srv-alice.json"
        dst = catalog.servers_dir / "srv-bob.json"
        dst.write_bytes(src.read_bytes())
        assert catalog.get_server("srv-bob") is None

    def test_get_server_validates_id_before_lookup(self, catalog):
        with pytest.raises(CatalogError):
            catalog.get_server("INVALID_ID_FORMAT")

    def test_remove_server_validates_id(self, catalog):
        with pytest.raises(CatalogError):
            catalog.remove_server("INVALID")

    def test_update_status_validates_id(self, catalog):
        with pytest.raises(CatalogError):
            catalog.update_status("INVALID", ServerStatus.INSTALLED)


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Phase B : catégorie sémantique + prefer_over_native
# ══════════════════════════════════════════════════════════════════════════════


from src.mcp.server_catalog import _VALID_DECISION_SOURCES  # noqa: E402


class TestPhaseBDefaults:
    def test_new_entry_has_no_semantic_category(self, catalog):
        e = _add_minimal(catalog)
        assert e.semantic_category is None
        assert e.category_decision_source == ""
        assert e.prefer_over_native is False

    def test_get_server_returns_defaults_for_fresh_entry(self, catalog):
        _add_minimal(catalog, server_id="srv-x")
        e = catalog.get_server("srv-x")
        assert e is not None
        assert e.semantic_category is None
        assert e.category_decision_source == ""
        assert e.prefer_over_native is False


class TestPhaseBUpdateSemanticCategory:
    @pytest.mark.parametrize("category", [
        "mail", "project", "git", "github", "media", "memory",
    ])
    def test_set_valid_category_static_source(self, catalog, category):
        _add_minimal(catalog, server_id="srv-cat")
        e = catalog.update_semantic_category("srv-cat", category, "static")
        assert e.semantic_category == category
        assert e.category_decision_source == "static"

    @pytest.mark.parametrize("source", [
        "static", "heuristic", "llm", "fallback", "user_override",
    ])
    def test_all_valid_decision_sources_accepted(self, catalog, source):
        _add_minimal(catalog, server_id="srv-src")
        e = catalog.update_semantic_category("srv-src", "mail", source)
        assert e.category_decision_source == source

    def test_reset_to_none_with_empty_source(self, catalog):
        _add_minimal(catalog, server_id="srv-reset")
        catalog.update_semantic_category("srv-reset", "mail", "static")
        e = catalog.update_semantic_category("srv-reset", None, "")
        assert e.semantic_category is None
        assert e.category_decision_source == ""

    def test_unknown_category_raises(self, catalog):
        _add_minimal(catalog, server_id="srv-bad-cat")
        with pytest.raises(CatalogError, match="semantic_category_unknown"):
            catalog.update_semantic_category(
                "srv-bad-cat", "not_a_real_category", "static"
            )

    def test_non_string_category_raises(self, catalog):
        _add_minimal(catalog, server_id="srv-bad-cat-type")
        with pytest.raises(CatalogError, match="semantic_category_type"):
            catalog.update_semantic_category("srv-bad-cat-type", 42, "static")

    def test_unknown_source_raises(self, catalog):
        _add_minimal(catalog, server_id="srv-bad-src")
        with pytest.raises(CatalogError, match="decision_source_unknown"):
            catalog.update_semantic_category(
                "srv-bad-src", "mail", "telepathy"
            )

    def test_non_string_source_raises(self, catalog):
        _add_minimal(catalog, server_id="srv-bad-src-type")
        with pytest.raises(CatalogError, match="decision_source_type"):
            catalog.update_semantic_category(
                "srv-bad-src-type", "mail", 1
            )

    def test_server_not_found_raises(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_semantic_category("srv-missing", "mail", "static")

    def test_invalid_server_id_raises(self, catalog):
        with pytest.raises(CatalogError, match="context_invalid:server_id"):
            catalog.update_semantic_category("INVALID", "mail", "static")

    def test_updated_at_is_bumped(self, catalog):
        e0 = _add_minimal(catalog, server_id="srv-ts")
        import time
        time.sleep(0.002)
        e1 = catalog.update_semantic_category("srv-ts", "mail", "static")
        assert e1.updated_at >= e0.updated_at
        assert e1.added_at == e0.added_at  # immutable


class TestPhaseBUpdatePreferOverNative:
    def test_default_false(self, catalog):
        e = _add_minimal(catalog, server_id="srv-pn")
        assert e.prefer_over_native is False

    def test_set_true(self, catalog):
        _add_minimal(catalog, server_id="srv-pn-t")
        e = catalog.update_prefer_over_native("srv-pn-t", True)
        assert e.prefer_over_native is True

    def test_toggle_false(self, catalog):
        _add_minimal(catalog, server_id="srv-pn-f")
        catalog.update_prefer_over_native("srv-pn-f", True)
        e = catalog.update_prefer_over_native("srv-pn-f", False)
        assert e.prefer_over_native is False

    def test_non_bool_int_rejected(self, catalog):
        _add_minimal(catalog, server_id="srv-pn-int")
        with pytest.raises(CatalogError, match="prefer_over_native_type"):
            catalog.update_prefer_over_native("srv-pn-int", 1)  # type: ignore[arg-type]

    def test_non_bool_string_rejected(self, catalog):
        _add_minimal(catalog, server_id="srv-pn-str")
        with pytest.raises(CatalogError, match="prefer_over_native_type"):
            catalog.update_prefer_over_native("srv-pn-str", "true")  # type: ignore[arg-type]

    def test_server_not_found_raises(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_prefer_over_native("srv-missing-pn", True)

    def test_invalid_server_id_raises(self, catalog):
        with pytest.raises(CatalogError, match="context_invalid:server_id"):
            catalog.update_prefer_over_native("INVALID", True)


class TestPhaseBPersistenceRoundTrip:
    def test_round_trip_with_category_set(self, catalog):
        _add_minimal(catalog, server_id="srv-rt-cat")
        catalog.update_semantic_category("srv-rt-cat", "mail", "llm")
        catalog.update_prefer_over_native("srv-rt-cat", True)
        # Reload via fresh-loaded get_server (force read-from-disk).
        e = catalog.get_server("srv-rt-cat")
        assert e is not None
        assert e.semantic_category == "mail"
        assert e.category_decision_source == "llm"
        assert e.prefer_over_native is True

    def test_disk_json_omits_new_fields_when_default(self, catalog):
        """Backward-compat HMAC : fresh entry sans classification doit
        produire un JSON canonique IDENTIQUE au format pré-Phase B."""
        _add_minimal(catalog, server_id="srv-omit")
        path = catalog.servers_dir / "srv-omit.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        entry_dict = record["entry"]
        assert "semantic_category" not in entry_dict
        assert "category_decision_source" not in entry_dict
        assert "prefer_over_native" not in entry_dict

    def test_disk_json_emits_new_fields_when_set(self, catalog):
        _add_minimal(catalog, server_id="srv-emit")
        catalog.update_semantic_category("srv-emit", "mail", "static")
        catalog.update_prefer_over_native("srv-emit", True)
        path = catalog.servers_dir / "srv-emit.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        entry_dict = record["entry"]
        assert entry_dict["semantic_category"] == "mail"
        assert entry_dict["category_decision_source"] == "static"
        assert entry_dict["prefer_over_native"] is True

    def test_hmac_still_verified_after_category_update(self, catalog):
        _add_minimal(catalog, server_id="srv-hmac")
        catalog.update_semantic_category("srv-hmac", "mail", "static")
        # _load_entry_from_path passe par _verify_hmac → si KO, return None.
        e = catalog.get_server("srv-hmac")
        assert e is not None and e.semantic_category == "mail"


class TestPhaseBBackwardCompat:
    """Prouve qu'un fichier JSON pré-Phase B (sans les 3 nouveaux champs) se
    charge avec defaults sans casser HMAC ni binding."""

    def test_legacy_entry_without_new_fields_loads_with_defaults(
        self, catalog
    ):
        # On forge un record au format pré-Phase B : dict sans nouveaux
        # champs, HMAC calculé sur ce dict avec la clé du catalog.
        legacy_entry = {
            "server_id": "legacy-srv",
            "display_name": "Legacy",
            "package_spec": "npm:legacy-mcp",
            "version": None,
            "owner_profile": "alice",
            "trust_score": None,
            "status": "declared",
            "added_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "last_active_at": None,
            "notes": None,
        }
        hmac_hex = catalog._compute_hmac(legacy_entry)
        record = {"entry": legacy_entry, "integrity_hmac": hmac_hex}
        path = catalog.servers_dir / "legacy-srv.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        e = catalog.get_server("legacy-srv")
        assert e is not None
        assert e.server_id == "legacy-srv"
        assert e.semantic_category is None
        assert e.category_decision_source == ""
        assert e.prefer_over_native is False

    def test_legacy_entry_can_be_upgraded_in_place(self, catalog):
        """Un fichier legacy doit pouvoir être mis à jour avec une
        catégorie sans erreur — couvre le cas migration douce."""
        legacy_entry = {
            "server_id": "legacy-up",
            "display_name": "Legacy Up",
            "package_spec": "npm:legacy-up",
            "version": None,
            "owner_profile": "alice",
            "trust_score": None,
            "status": "declared",
            "added_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "last_active_at": None,
            "notes": None,
        }
        hmac_hex = catalog._compute_hmac(legacy_entry)
        path = catalog.servers_dir / "legacy-up.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"entry": legacy_entry, "integrity_hmac": hmac_hex},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        e = catalog.update_semantic_category("legacy-up", "mail", "static")
        assert e.semantic_category == "mail"
        # Re-read pour valider que la nouvelle HMAC est stockée.
        reread = catalog.get_server("legacy-up")
        assert reread is not None and reread.semantic_category == "mail"

    def test_corrupted_semantic_category_type_rejected(self, catalog):
        """Si le JSON disque a un type bogus pour semantic_category, le
        load doit retourner None (format invalide) — défense en profondeur."""
        _add_minimal(catalog, server_id="srv-corrupt")
        path = catalog.servers_dir / "srv-corrupt.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["entry"]["semantic_category"] = 123  # type invalide
        # Recompute HMAC pour que la corruption passe l'intégrité et
        # tape uniquement la validation de type dans _dict_to_entry.
        record["integrity_hmac"] = catalog._compute_hmac(record["entry"])
        path.write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        assert catalog.get_server("srv-corrupt") is None


class TestPhaseBAuditNoPII:
    def test_category_update_audit_event_present(self, catalog):
        _add_minimal(catalog, server_id="srv-audit-cat")
        catalog.update_semantic_category("srv-audit-cat", "mail", "static")
        events = [
            e for e in _audit_lines(catalog)
            if e.get("event") == "server_semantic_category_updated"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["server_id"] == "srv-audit-cat"
        assert ev["semantic_category"] == "mail"
        assert ev["decision_source"] == "static"

    def test_category_reset_audit_uses_empty_string(self, catalog):
        _add_minimal(catalog, server_id="srv-audit-reset")
        catalog.update_semantic_category(
            "srv-audit-reset", "mail", "static"
        )
        catalog.update_semantic_category("srv-audit-reset", None, "")
        events = [
            e for e in _audit_lines(catalog)
            if e.get("event") == "server_semantic_category_updated"
        ]
        assert events[-1]["semantic_category"] == ""

    def test_prefer_update_audit_event_present(self, catalog):
        _add_minimal(catalog, server_id="srv-audit-pn")
        catalog.update_prefer_over_native("srv-audit-pn", True)
        events = [
            e for e in _audit_lines(catalog)
            if e.get("event") == "server_prefer_over_native_updated"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["server_id"] == "srv-audit-pn"
        assert ev["prefer_over_native"] is True

    def test_audit_has_no_pii_after_category_update(self, catalog):
        _add_minimal(
            catalog,
            server_id="srv-pii",
            display_name="Sensitive Name",
            package_spec="npm:secret-pkg",
            notes="confidential notes",
        )
        catalog.update_semantic_category("srv-pii", "mail", "user_override")
        catalog.update_prefer_over_native("srv-pii", True)
        blob = _audit_blob(catalog)
        assert "Sensitive Name" not in blob
        assert "secret-pkg" not in blob
        assert "confidential notes" not in blob
        assert "ServerEntry(" not in blob


class TestPhaseBValidDecisionSourcesContract:
    def test_valid_sources_set_is_exact(self):
        assert _VALID_DECISION_SOURCES == frozenset({
            "", "static", "heuristic", "llm", "fallback", "user_override",
        })


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Phase I-1 : config_schema persisté
# ══════════════════════════════════════════════════════════════════════════════


class TestPhaseI1ConfigSchemaDefaults:
    def test_new_entry_config_schema_none(self, catalog):
        e = _add_minimal(catalog)
        assert e.config_schema is None

    def test_load_after_add_keeps_none(self, catalog):
        _add_minimal(catalog, server_id="srv-i1")
        e = catalog.get_server("srv-i1")
        assert e is not None
        assert e.config_schema is None


class TestPhaseI1UpdateConfigSchema:
    def test_set_simple_schema(self, catalog):
        _add_minimal(catalog, server_id="srv-cs")
        sample = {
            "server_id": "srv-cs",
            "fields": [{"name": "X", "label": "X", "description": "x",
                        "kind": "string", "sensitivity": "normal", "required": True}],
            "detected_from": "curated",
        }
        e = catalog.update_config_schema("srv-cs", sample)
        assert e.config_schema == sample

    def test_set_then_load_persists(self, catalog):
        _add_minimal(catalog, server_id="srv-cs2")
        sample = {
            "server_id": "srv-cs2",
            "fields": [], "auth_flows": [],
            "detected_from": "package",
        }
        catalog.update_config_schema("srv-cs2", sample)
        e = catalog.get_server("srv-cs2")
        assert e is not None
        assert e.config_schema == sample

    def test_set_none_clears(self, catalog):
        _add_minimal(catalog, server_id="srv-cs3")
        catalog.update_config_schema("srv-cs3", {"server_id": "srv-cs3", "fields": []})
        e1 = catalog.get_server("srv-cs3")
        assert e1.config_schema is not None
        catalog.update_config_schema("srv-cs3", None)
        e2 = catalog.get_server("srv-cs3")
        assert e2.config_schema is None

    def test_non_dict_schema_raises(self, catalog):
        _add_minimal(catalog, server_id="srv-cs4")
        with pytest.raises(CatalogError, match="config_schema_type"):
            catalog.update_config_schema("srv-cs4", "not a dict")  # type: ignore[arg-type]

    def test_server_not_found_raises(self, catalog):
        with pytest.raises(CatalogError, match="server_not_found"):
            catalog.update_config_schema("srv-missing-cs", {"server_id": "x", "fields": []})

    def test_invalid_server_id_raises(self, catalog):
        with pytest.raises(CatalogError, match="context_invalid:server_id"):
            catalog.update_config_schema("INVALID!", {"server_id": "x", "fields": []})


class TestPhaseI1BackwardCompat:
    def test_omit_field_when_none(self, catalog):
        """Sans schéma défini, le JSON disque ne doit PAS contenir config_schema
        (back-compat HMAC avec entries pré-Phase I-1)."""
        _add_minimal(catalog, server_id="srv-bc")
        path = catalog.servers_dir / "srv-bc.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        assert "config_schema" not in record["entry"]

    def test_legacy_entry_loads_with_none(self, catalog):
        """Fichier pré-Phase I-1 (sans config_schema) doit se charger
        avec config_schema=None."""
        legacy = {
            "server_id": "legacy-i1",
            "display_name": "Legacy",
            "package_spec": "npm:legacy",
            "version": None,
            "owner_profile": "alice",
            "trust_score": None,
            "status": "declared",
            "added_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "last_active_at": None,
            "notes": None,
        }
        hmac_hex = catalog._compute_hmac(legacy)
        path = catalog.servers_dir / "legacy-i1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"entry": legacy, "integrity_hmac": hmac_hex}),
            encoding="utf-8",
        )
        e = catalog.get_server("legacy-i1")
        assert e is not None
        assert e.config_schema is None

    def test_corrupted_config_schema_type_rejected(self, catalog):
        """JSON sur disque avec config_schema non-dict → load None."""
        _add_minimal(catalog, server_id="srv-corrupt-cs")
        path = catalog.servers_dir / "srv-corrupt-cs.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["entry"]["config_schema"] = "not a dict"  # type invalide
        record["integrity_hmac"] = catalog._compute_hmac(record["entry"])
        path.write_text(json.dumps(record), encoding="utf-8")
        assert catalog.get_server("srv-corrupt-cs") is None


class TestPhaseI1AuditNoPII:
    def test_audit_event_emitted(self, catalog):
        _add_minimal(catalog, server_id="srv-audit-cs")
        catalog.update_config_schema(
            "srv-audit-cs",
            {"server_id": "srv-audit-cs", "fields": [], "detected_from": "curated"},
        )
        events = [
            e for e in _audit_lines(catalog)
            if e.get("event") == "server_config_schema_updated"
        ]
        assert len(events) == 1
        assert events[0]["server_id"] == "srv-audit-cs"
        assert events[0]["detected_from"] == "curated"

    def test_audit_no_field_values_leaked(self, catalog):
        """Le schéma peut contenir des descriptions/labels, mais l'audit
        ne doit JAMAIS contenir la liste des champs eux-mêmes (anti-leak)."""
        _add_minimal(catalog, server_id="srv-noleak")
        catalog.update_config_schema(
            "srv-noleak",
            {
                "server_id": "srv-noleak",
                "fields": [{"name": "API_SECRET_FIELD_NAME", "label": "x",
                            "description": "x", "kind": "string", "sensitivity": "normal",
                            "required": True}],
            },
        )
        blob = _audit_blob(catalog)
        # Le nom de champ ne doit pas apparaître dans l'audit.
        assert "API_SECRET_FIELD_NAME" not in blob
