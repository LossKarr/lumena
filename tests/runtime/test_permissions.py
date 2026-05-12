"""Tests Phase 1 — Permissions minimales."""
from __future__ import annotations

import pytest

from src.runtime.permissions import (
    can_use_scope,
    is_guest,
    is_owner,
    require_not_guest,
    require_owner,
    require_scope,
)


# ── is_owner ──────────────────────────────────────────────────────────────────

def test_is_owner_true():
    assert is_owner("owner") is True

def test_is_owner_false_for_admin():
    assert is_owner("admin") is False

def test_is_owner_false_for_guest():
    assert is_owner("guest") is False

def test_is_owner_false_for_none():
    assert is_owner(None) is False


# ── is_guest ──────────────────────────────────────────────────────────────────

def test_is_guest_true_for_guest():
    assert is_guest("guest") is True

def test_is_guest_true_for_peer():
    assert is_guest("peer") is True

def test_is_guest_false_for_owner():
    assert is_guest("owner") is False

def test_is_guest_false_for_user():
    assert is_guest("user") is False


# ── can_use_scope ─────────────────────────────────────────────────────────────

def test_owner_can_use_any_scope():
    for scope in ("dangerous_tools", "manage_users", "manage_peers", "admin_routes", "chat"):
        assert can_use_scope("owner", scope) is True

def test_guest_cannot_use_dangerous_tools():
    assert can_use_scope("guest", "dangerous_tools") is False

def test_guest_cannot_manage_users():
    assert can_use_scope("guest", "manage_users") is False

def test_guest_cannot_manage_peers():
    assert can_use_scope("guest", "manage_peers") is False

def test_guest_cannot_use_admin_routes():
    assert can_use_scope("guest", "admin_routes") is False

def test_peer_cannot_use_dangerous_tools():
    assert can_use_scope("peer", "dangerous_tools") is False

def test_user_cannot_use_admin_routes():
    assert can_use_scope("user", "admin_routes") is False

def test_admin_can_use_admin_routes():
    assert can_use_scope("admin", "admin_routes") is True

def test_admin_can_manage_users():
    assert can_use_scope("admin", "manage_users") is True

def test_user_can_use_chat():
    assert can_use_scope("user", "chat") is True

def test_unknown_role_treated_as_guest():
    assert can_use_scope("superadmin", "dangerous_tools") is False


# ── require_owner ─────────────────────────────────────────────────────────────

def test_require_owner_passes_for_owner():
    require_owner("owner")  # no exception

def test_require_owner_raises_for_guest():
    with pytest.raises(PermissionError):
        require_owner("guest")

def test_require_owner_raises_for_admin():
    with pytest.raises(PermissionError):
        require_owner("admin")


# ── require_scope ─────────────────────────────────────────────────────────────

def test_require_scope_passes_for_owner():
    require_scope("owner", "dangerous_tools")  # no exception

def test_require_scope_raises_for_guest_dangerous():
    with pytest.raises(PermissionError):
        require_scope("guest", "dangerous_tools")

def test_require_scope_raises_for_user_admin_routes():
    with pytest.raises(PermissionError):
        require_scope("user", "admin_routes")


# ── require_not_guest ─────────────────────────────────────────────────────────

def test_require_not_guest_passes_for_user():
    require_not_guest("user")  # no exception

def test_require_not_guest_raises_for_guest():
    with pytest.raises(PermissionError):
        require_not_guest("guest")

def test_require_not_guest_raises_for_peer():
    with pytest.raises(PermissionError):
        require_not_guest("peer")


# ── Mode single-user historique (owner par défaut) ────────────────────────────

def test_single_user_default_role_is_owner():
    from src.runtime.context import RuntimeContext, FALLBACK_USER_ID
    ctx = RuntimeContext.build(
        channel="web", client="browser",
        request_id=None, conversation_id=None, message_id=None,
        workspace_policy="default", task_id=None, client_caps={},
        workspace_path=None, active_file_path=None, open_files=[],
        resolved_workspace=None, resolved_date=None, resolution_reason=None,
    )
    assert ctx.user_id == FALLBACK_USER_ID
    assert is_owner(ctx.user_role) is True
