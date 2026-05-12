"""Tests Phase 0 — Identité runtime et fix contexte web global."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.context import (
    FALLBACK_OWNER_USER_ID,
    FALLBACK_USER_ID,
    RuntimeContext,
    get_current_runtime_context,
    pop_runtime_context,
    push_runtime_context,
)
from src.core_services.identity_service import IdentityService


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_ctx(**kwargs) -> RuntimeContext:
    defaults = dict(
        channel="web", client="browser",
        request_id=None, conversation_id=None, message_id=None,
        workspace_policy="default", task_id=None, client_caps={},
        workspace_path=None, active_file_path=None, open_files=[],
        resolved_workspace=None, resolved_date=None, resolution_reason=None,
    )
    defaults.update(kwargs)
    return RuntimeContext.build(**defaults)


# ── RuntimeContext : nouveaux champs ──────────────────────────────────────────


def test_runtime_context_carries_user_id():
    ctx = _build_ctx(user_id="telegram:42")
    assert ctx.user_id == "telegram:42"


def test_runtime_context_carries_owner_user_id():
    ctx = _build_ctx(owner_user_id="local:owner", user_id="telegram:42")
    assert ctx.owner_user_id == "local:owner"
    assert ctx.user_id == "telegram:42"


def test_runtime_context_carries_instance_id():
    ctx = _build_ctx(instance_id="inst-abc")
    assert ctx.instance_id == "inst-abc"


def test_runtime_context_carries_profile_id():
    ctx = _build_ctx(profile_id="profile-xyz")
    assert ctx.profile_id == "profile-xyz"


def test_runtime_context_carries_user_role():
    ctx = _build_ctx(user_role="guest")
    assert ctx.user_role == "guest"


def test_runtime_context_user_role_invalid_falls_back_to_guest():
    # Rôle inconnu → guest (jamais owner, pour ne pas escalader les droits)
    ctx = _build_ctx(user_role="superadmin")
    assert ctx.user_role == "guest"


# ── Fallback web → local:owner ────────────────────────────────────────────────


def test_web_chat_defaults_to_local_owner_user_id():
    ctx = _build_ctx(user_id=None)
    assert ctx.user_id == FALLBACK_USER_ID == "local:owner"


def test_web_chat_defaults_to_local_owner_owner_user_id():
    ctx = _build_ctx(owner_user_id=None)
    assert ctx.owner_user_id == FALLBACK_OWNER_USER_ID == "local:owner"


# ── Mapping canal → user_id stable ────────────────────────────────────────────


def test_telegram_user_id_format():
    ctx = _build_ctx(channel="telegram", user_id="telegram:12345")
    assert ctx.user_id == "telegram:12345"
    assert ctx.channel == "telegram"


def test_discord_user_id_format():
    ctx = _build_ctx(channel="discord", user_id="discord:guild1:user99")
    assert ctx.user_id == "discord:guild1:user99"


# ── Rétrocompatibilité : build sans nouveaux champs ───────────────────────────


def test_build_without_new_fields_still_works():
    ctx = RuntimeContext.build(
        channel="web", client="legacy-client",
        request_id=None, conversation_id=None, message_id=None,
        workspace_policy="default", task_id=None, client_caps={},
        workspace_path=None, active_file_path=None, open_files=[],
        resolved_workspace=None, resolved_date=None, resolution_reason=None,
        # pas de user_id, owner_user_id, user_role, profile_id, instance_id
    )
    assert ctx.user_id == FALLBACK_USER_ID
    assert ctx.owner_user_id == FALLBACK_OWNER_USER_ID
    assert ctx.user_role == "owner"
    assert ctx.profile_id is None


# ── Fix _WEB_CONTEXT_KEY global : clé par utilisateur ─────────────────────────


def test_web_context_key_differs_per_user_id():
    key_a = IdentityService._resolve_web_context_key(
        user_id="local:owner", conversation_id="conv_1"
    )
    key_b = IdentityService._resolve_web_context_key(
        user_id="telegram:42", conversation_id="conv_1"
    )
    assert key_a != key_b


def test_web_context_key_differs_per_conversation():
    key_a = IdentityService._resolve_web_context_key(
        user_id="local:owner", conversation_id="conv_1"
    )
    key_b = IdentityService._resolve_web_context_key(
        user_id="local:owner", conversation_id="conv_2"
    )
    assert key_a != key_b


def test_web_context_key_no_slash_in_filename():
    key = IdentityService._resolve_web_context_key(
        user_id="local:owner", conversation_id="conv/x"
    )
    assert "/" not in key
    assert "\\" not in key


def test_web_context_key_fallback_without_context():
    """Sans conversation_id ni client, la clé contient quand même user_id."""
    key = IdentityService._resolve_web_context_key(user_id="local:owner")
    assert "local__owner" in key or "local:owner".replace(":", "__") in key


# ── Lecture legacy fallback pour local:owner ──────────────────────────────────


def _make_identity_svc(tmp_path: Path) -> IdentityService:
    """Crée un IdentityService minimal pour les tests sans ServiceContext complet."""
    from unittest.mock import MagicMock
    mock_ctx = MagicMock()
    mock_ctx.data_dir = tmp_path
    mock_ctx.memory = None
    mock_ctx.llm = None
    svc = IdentityService.__new__(IdentityService)
    svc.ctx = mock_ctx
    svc._identity_lock = __import__("threading").Lock()
    svc._tg_contexts = {}
    svc._wa_contexts = {}
    svc._discord_contexts = {}
    svc._discord_users = {}
    svc._max_contexts = 500
    svc._last_code_context = {}
    svc._code_context_ttl = 1800.0
    return svc


def test_web_context_legacy_default_readable_for_local_owner(tmp_path):
    """Le fichier legacy web_contexts/default.json reste lisible pour local:owner."""
    ctx_dir = tmp_path / "web_contexts"
    ctx_dir.mkdir()
    legacy_file = ctx_dir / "default.json"
    legacy_file.write_text(
        json.dumps([{"role": "user", "content": "hello legacy"}]),
        encoding="utf-8",
    )

    svc = _make_identity_svc(tmp_path)
    legacy_key = IdentityService._WEB_CONTEXT_LEGACY_KEY
    loaded = svc._load_web_context(context_key=legacy_key)
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "hello legacy"


# ── Deux users web ont des fichiers de contexte séparés ───────────────────────


def test_two_web_users_get_separate_context_files(tmp_path):
    from src.core import ConversationContext

    key_alice = IdentityService._resolve_web_context_key(
        user_id="local:owner", conversation_id="conv_alice"
    )
    key_bob = IdentityService._resolve_web_context_key(
        user_id="telegram:99", conversation_id="conv_bob"
    )
    assert key_alice != key_bob

    svc = _make_identity_svc(tmp_path)

    ctx_alice = ConversationContext(max_messages=10)
    ctx_alice.add_message("user", "alice says hi")
    svc._save_web_context(ctx_alice, context_key=key_alice)

    ctx_bob = ConversationContext(max_messages=10)
    ctx_bob.add_message("user", "bob says hello")
    svc._save_web_context(ctx_bob, context_key=key_bob)

    loaded_alice = svc._load_web_context(context_key=key_alice)
    loaded_bob = svc._load_web_context(context_key=key_bob)

    assert loaded_alice.messages[0].content == "alice says hi"
    assert loaded_bob.messages[0].content == "bob says hello"
    assert loaded_alice.messages[0].content != loaded_bob.messages[0].content


# ── Failles corrigées (vérification explicite) ────────────────────────────────


def test_peer_is_valid_role_in_runtime_context():
    ctx = _build_ctx(channel="api", user_role="peer")
    assert ctx.user_role == "peer"


def test_invalid_role_becomes_guest_not_owner():
    # Sécurité : un rôle inconnu ne doit JAMAIS devenir owner
    ctx = _build_ctx(user_role="superadmin")
    assert ctx.user_role == "guest"
    assert ctx.user_role != "owner"


def test_external_channel_without_role_defaults_to_guest():
    # Telegram/Discord/API sans rôle explicite → guest, jamais owner
    for ch in ("telegram", "discord", "whatsapp", "api"):
        ctx = _build_ctx(channel=ch, user_role=None)
        assert ctx.user_role == "guest", f"canal {ch} devrait retomber sur guest"


def test_web_channel_without_role_defaults_to_owner():
    # Web local sans rôle → owner (propriétaire de l'instance)
    ctx = _build_ctx(channel="web", user_role=None)
    assert ctx.user_role == "owner"


def test_legacy_fallback_not_served_to_non_local_owner(tmp_path):
    """Un user non-local:owner ne doit pas recevoir le contexte legacy."""
    from src.core import ConversationContext

    ctx_dir = tmp_path / "web_contexts"
    ctx_dir.mkdir()
    (ctx_dir / "default.json").write_text(
        '[{"role": "user", "content": "secret legacy"}]',
        encoding="utf-8",
    )

    svc = _make_identity_svc(tmp_path)
    # Clé pour telegram:42, pas local:owner
    key_bob = IdentityService._resolve_web_context_key(
        user_id="telegram:42", conversation_id="conv_bob"
    )
    loaded = svc._load_web_context(context_key=key_bob)
    # Bob ne doit pas voir le contexte legacy de local:owner
    assert len(loaded.messages) == 0


def test_resolve_channel_key_uses_conversation_id_not_session_id():
    """resolve_channel_key ne doit pas utiliser session_id (inexistant)."""
    ctx = _build_ctx(channel="web", user_id="local:owner", conversation_id="conv_xyz")
    key = IdentityService.resolve_channel_key(ctx)
    assert "conv_xyz" in key
    assert "session_id" not in key
    assert "default" not in key


def test_chat_request_accepts_identity_fields():
    from web.routes.schemas import ChatRequest
    req = ChatRequest(
        message="test",
        user_id="telegram:42",
        owner_user_id="local:owner",
        user_role="guest",
        profile_id="prof-1",
        client_instance_id="inst-abc",
    )
    assert req.user_id == "telegram:42"
    assert req.owner_user_id == "local:owner"
    assert req.user_role == "guest"
    assert req.profile_id == "prof-1"
    assert req.client_instance_id == "inst-abc"


# ── Cache conversation : non-collision par user_id ────────────────────────────


def test_conversation_cache_key_differs_per_user():
    from web.routes.chat import _build_conversation_cache_key
    from web.routes.schemas import ChatRequest

    req_owner = ChatRequest(message="hi", client="browser", user_id="local:owner")
    req_tg = ChatRequest(message="hi", client="browser", user_id="telegram:42")

    key_owner = _build_conversation_cache_key(req_owner, "web", "browser")
    key_tg = _build_conversation_cache_key(req_tg, "web", "browser")

    assert key_owner != key_tg, (
        f"Deux utilisateurs avec le même client ne doivent pas partager la même clé de cache : {key_owner}"
    )


def test_conversation_cache_key_contains_user_id():
    from web.routes.chat import _build_conversation_cache_key
    from web.routes.schemas import ChatRequest

    req = ChatRequest(message="hi", client="browser", user_id="telegram:42")
    key = _build_conversation_cache_key(req, "web", "browser")
    assert "telegram:42" in key or "telegram" in key


def test_conversation_cache_key_fallback_without_user_id():
    from web.routes.chat import _build_conversation_cache_key
    from web.routes.schemas import ChatRequest

    req = ChatRequest(message="hi", client="browser")  # pas de user_id
    key = _build_conversation_cache_key(req, "web", "browser")
    assert "local:owner" in key


def test_conversation_cache_key_ide_session_includes_user_id():
    from web.routes.chat import _build_conversation_cache_key
    from web.routes.schemas import ChatRequest

    req_a = ChatRequest(message="hi", ide_session_id="sess1", user_id="local:owner")
    req_b = ChatRequest(message="hi", ide_session_id="sess1", user_id="telegram:42")

    key_a = _build_conversation_cache_key(req_a, "ide", "cursor")
    key_b = _build_conversation_cache_key(req_b, "ide", "cursor")

    assert key_a != key_b
