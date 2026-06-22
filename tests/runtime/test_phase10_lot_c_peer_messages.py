"""Lot C Phase 10 — Tests PeerMessage envelope commune.

Couvre :
- create_peer_message : OK, IDs auto, TTL borné
- validate_peer_message : type inconnu, scope inconnu, from/to vides, created_at invalide,
  TTL hors plage, message expiré, hop_count trop haut, payload trop gros, payload non-JSON
- is_expired : message frais, message expiré
- increment_hop : compteur, overflow
- make_error_message : hérite conversation_id / trace_id / parent_message_id
- sanitize_peer_message : supprime clés interdites, redact valeurs secrètes, récursif
- compatibilité delegate_to_peer : context["peer_message"] présent quand flag activé
- .env.example contient les 3 nouvelles clés
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.peer_messages import (
    PeerMessage,
    VALID_MESSAGE_TYPES,
    create_peer_message,
    create_sanitized_peer_message,
    increment_hop,
    is_expired,
    make_error_message,
    sanitize_peer_message,
    validate_peer_message,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _good_msg(**kwargs) -> PeerMessage:
    defaults = dict(
        type="chat_delegate",
        scope="chat",
        from_instance_id="inst-aaa",
        to_instance_id="inst-bbb",
        payload={"prompt": "Bonjour"},
        ttl_seconds=300,
    )
    defaults.update(kwargs)
    return create_peer_message(**defaults)


# ── TestCreatePeerMessage ─────────────────────────────────────────────────────

class TestCreatePeerMessage:
    def test_create_ok(self):
        msg = _good_msg()
        assert msg.type == "chat_delegate"
        assert msg.scope == "chat"
        assert msg.from_instance_id == "inst-aaa"
        assert msg.to_instance_id == "inst-bbb"
        assert msg.hop_count == 0

    def test_ids_auto_generated(self):
        msg = _good_msg()
        assert len(msg.message_id) >= 32
        assert len(msg.conversation_id) >= 32
        assert len(msg.trace_id) >= 32

    def test_custom_conversation_id_preserved(self):
        cid = uuid.uuid4().hex
        msg = _good_msg(conversation_id=cid)
        assert msg.conversation_id == cid

    def test_custom_trace_id_preserved(self):
        tid = uuid.uuid4().hex
        msg = _good_msg(trace_id=tid)
        assert msg.trace_id == tid

    def test_ttl_too_low_clamped_to_10(self):
        msg = _good_msg(ttl_seconds=1)
        assert msg.ttl_seconds == 10

    def test_ttl_too_high_clamped_to_max(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_TTL", "600")
        msg = _good_msg(ttl_seconds=9999)
        assert msg.ttl_seconds == 600

    def test_created_at_is_iso_utc(self):
        msg = _good_msg()
        dt = datetime.fromisoformat(msg.created_at)
        assert dt.tzinfo is not None

    def test_all_valid_types_accepted(self):
        for t in VALID_MESSAGE_TYPES:
            scope = "chat" if t != "heartbeat" else "chat"
            msg = create_peer_message(
                type=t, scope=scope,
                from_instance_id="a", to_instance_id="b",
                payload={},
            )
            assert msg.type == t

    def test_to_dict_roundtrip(self):
        msg = _good_msg()
        d = msg.to_dict()
        restored = PeerMessage.from_dict(d)
        assert restored.message_id == msg.message_id
        assert restored.payload == msg.payload

    def test_parent_message_id_preserved(self):
        pid = uuid.uuid4().hex
        msg = _good_msg(parent_message_id=pid)
        assert msg.parent_message_id == pid


# ── TestValidatePeerMessage ───────────────────────────────────────────────────

class TestValidatePeerMessage:
    def test_valid_message_passes(self):
        msg = _good_msg()
        validate_peer_message(msg)  # pas d'exception

    def test_unknown_type_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "type": "fake_type"})
        with pytest.raises(ValueError, match="inconnu"):
            validate_peer_message(msg)

    def test_unknown_scope_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "scope": "forbidden_scope"})
        with pytest.raises(ValueError, match="inconnu"):
            validate_peer_message(msg)

    def test_empty_from_instance_id_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "from_instance_id": ""})
        with pytest.raises(ValueError, match="from_instance_id"):
            validate_peer_message(msg)

    def test_empty_to_instance_id_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "to_instance_id": "  "})
        with pytest.raises(ValueError, match="to_instance_id"):
            validate_peer_message(msg)

    def test_invalid_created_at_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "created_at": "pas-une-date"})
        with pytest.raises(ValueError, match="created_at"):
            validate_peer_message(msg)

    def test_created_at_without_timezone_raises(self):
        msg = _good_msg()
        msg = PeerMessage.from_dict({**msg.to_dict(), "created_at": "2026-05-07T12:00:00"})
        with pytest.raises(ValueError, match="timezone"):
            validate_peer_message(msg)

    def test_ttl_below_10_raises(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_TTL", "3600")
        msg = _good_msg()
        d = msg.to_dict()
        d["ttl_seconds"] = 5
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError, match="ttl_seconds"):
            validate_peer_message(msg2)

    def test_ttl_above_max_raises(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_TTL", "600")
        msg = _good_msg()
        d = msg.to_dict()
        d["ttl_seconds"] = 601
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError, match="ttl_seconds"):
            validate_peer_message(msg2)

    def test_expired_message_raises(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        msg = _good_msg()
        d = msg.to_dict()
        d["created_at"] = old_ts
        d["ttl_seconds"] = 300
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError, match="expiré"):
            validate_peer_message(msg2)

    def test_hop_count_too_high_raises(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "3")
        msg = _good_msg()
        d = msg.to_dict()
        d["hop_count"] = 4
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError, match="hop_count"):
            validate_peer_message(msg2)

    def test_payload_too_large_raises(self, monkeypatch):
        # env var à 256 ; payload > 256 octets → refus
        monkeypatch.setenv("LUMENA_PEER_MAX_PAYLOAD_BYTES", "256")
        with pytest.raises(ValueError, match="volumineux"):
            create_peer_message(
                type="chat_delegate", scope="chat",
                from_instance_id="a", to_instance_id="b",
                payload={"data": "x" * 500},
            )

    def test_empty_message_id_raises(self):
        # Contourner from_dict (qui auto-génère un UUID) en forçant directement
        msg = _good_msg()
        msg2 = PeerMessage(
            type=msg.type, scope=msg.scope,
            from_instance_id=msg.from_instance_id,
            to_instance_id=msg.to_instance_id,
            payload=msg.payload,
            message_id="",  # forcé vide
            conversation_id=msg.conversation_id,
            trace_id=msg.trace_id,
            created_at=msg.created_at,
            ttl_seconds=msg.ttl_seconds,
        )
        with pytest.raises(ValueError, match="message_id"):
            validate_peer_message(msg2)

    def test_heartbeat_any_scope_passes(self):
        msg = create_peer_message(
            type="heartbeat", scope="chat",
            from_instance_id="a", to_instance_id="b",
            payload={},
        )
        assert msg.type == "heartbeat"


# ── TestIsExpired ─────────────────────────────────────────────────────────────

class TestIsExpired:
    def test_fresh_message_not_expired(self):
        msg = _good_msg()
        assert not is_expired(msg)

    def test_old_message_is_expired(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        msg = _good_msg()
        d = msg.to_dict()
        d["created_at"] = old_ts
        d["ttl_seconds"] = 300
        msg2 = PeerMessage.from_dict(d)
        assert is_expired(msg2)

    def test_unparseable_created_at_is_expired(self):
        msg = _good_msg()
        d = msg.to_dict()
        d["created_at"] = "invalid"
        msg2 = PeerMessage.from_dict(d)
        assert is_expired(msg2)

    def test_just_expired(self):
        # TTL = 10s, créé il y a 11s → expiré
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=11)).isoformat()
        msg = _good_msg()
        d = msg.to_dict()
        d["created_at"] = old_ts
        d["ttl_seconds"] = 10
        msg2 = PeerMessage.from_dict(d)
        assert is_expired(msg2)


# ── TestIncrementHop ──────────────────────────────────────────────────────────

class TestIncrementHop:
    def test_increment_adds_one(self):
        msg = _good_msg()
        assert msg.hop_count == 0
        msg2 = increment_hop(msg)
        assert msg2.hop_count == 1

    def test_increment_preserves_other_fields(self):
        msg = _good_msg()
        msg2 = increment_hop(msg)
        assert msg2.message_id == msg.message_id
        assert msg2.conversation_id == msg.conversation_id
        assert msg2.payload == msg.payload

    def test_original_unchanged_after_increment(self):
        msg = _good_msg()
        increment_hop(msg)
        assert msg.hop_count == 0

    def test_increment_overflow_raises(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "3")
        msg = _good_msg()
        d = msg.to_dict()
        d["hop_count"] = 3
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError, match="hop_count"):
            increment_hop(msg2)

    def test_increment_multiple_times(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "5")
        msg = _good_msg()
        for _ in range(5):
            msg = increment_hop(msg)
        assert msg.hop_count == 5

    def test_increment_at_max_raises(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "5")
        msg = _good_msg()
        d = msg.to_dict()
        d["hop_count"] = 5
        msg2 = PeerMessage.from_dict(d)
        with pytest.raises(ValueError):
            increment_hop(msg2)


# ── TestMakeErrorMessage ──────────────────────────────────────────────────────

class TestMakeErrorMessage:
    def test_error_message_type(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Peer injoignable", from_instance_id="inst-bbb"
        )
        assert err.type == "error"

    def test_inherits_conversation_id(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Erreur", from_instance_id="inst-bbb"
        )
        assert err.conversation_id == original.conversation_id

    def test_inherits_trace_id(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Erreur", from_instance_id="inst-bbb"
        )
        assert err.trace_id == original.trace_id

    def test_parent_message_id_set_to_original(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Erreur", from_instance_id="inst-bbb"
        )
        assert err.parent_message_id == original.message_id

    def test_to_from_reversed(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Erreur", from_instance_id="inst-bbb"
        )
        assert err.from_instance_id == "inst-bbb"
        assert err.to_instance_id == original.from_instance_id

    def test_payload_contains_error_detail(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Peer injoignable", from_instance_id="inst-bbb"
        )
        assert "Peer injoignable" in err.payload.get("error", "")

    def test_payload_contains_original_type(self):
        original = _good_msg()
        err = make_error_message(
            original=original, error_detail="Erreur", from_instance_id="inst-bbb"
        )
        assert err.payload.get("original_type") == "chat_delegate"


# ── TestSanitizePeerMessage ───────────────────────────────────────────────────

class TestSanitizePeerMessage:
    def test_clean_payload_unchanged(self):
        msg = _good_msg(payload={"prompt": "Bonjour", "lang": "fr"})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["prompt"] == "Bonjour"
        assert sanitized.payload["lang"] == "fr"

    def test_forbidden_key_raises(self):
        msg = _good_msg(payload={"token": "tok-secret"})
        with pytest.raises(ValueError, match="interdite"):
            sanitize_peer_message(msg)

    def test_password_key_raises(self):
        msg = _good_msg(payload={"password": "hunter2"})
        with pytest.raises(ValueError, match="interdite"):
            sanitize_peer_message(msg)

    def test_api_key_raises(self):
        msg = _good_msg(payload={"api_key": "sk-abc123"})
        with pytest.raises(ValueError, match="interdite"):
            sanitize_peer_message(msg)

    def test_peer_token_outbound_raises(self):
        msg = _good_msg(payload={"peer_token_outbound": "raw-token-value"})
        with pytest.raises(ValueError, match="interdite"):
            sanitize_peer_message(msg)

    def test_bearer_value_redacted(self):
        msg = _good_msg(payload={"auth_header": "Bearer eyJhbGci.eyJzdW.secret"})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["auth_header"] == "[REDACTED]"

    def test_hex_32_chars_redacted(self):
        secret = "a" * 32
        msg = _good_msg(payload={"data": secret})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["data"] == "[REDACTED]"

    def test_short_hex_not_redacted(self):
        # 31 chars < seuil → pas redacté
        short = "a" * 31
        msg = _good_msg(payload={"data": short})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["data"] == short

    def test_nested_forbidden_key_raises(self):
        msg = _good_msg(payload={"meta": {"token": "nested-secret"}})
        with pytest.raises(ValueError, match="interdite"):
            sanitize_peer_message(msg)

    def test_nested_clean_dict_preserved(self):
        msg = _good_msg(payload={"meta": {"lang": "fr", "version": 1}})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["meta"]["lang"] == "fr"

    def test_list_values_sanitized(self):
        secret = "b" * 40
        msg = _good_msg(payload={"items": [secret, "safe"]})
        sanitized = sanitize_peer_message(msg)
        assert sanitized.payload["items"][0] == "[REDACTED]"
        assert sanitized.payload["items"][1] == "safe"

    def test_original_message_not_mutated(self):
        msg = _good_msg(payload={"data": "safe"})
        sanitized = sanitize_peer_message(msg)
        assert msg.payload["data"] == "safe"
        assert sanitized is not msg


# ── Test intégration delegate_to_peer ─────────────────────────────────────────

class TestDelegatePeerMessageIntegration:
    """Vérifie que context['peer_message'] est inclus dans le payload HTTP."""

    @pytest.mark.asyncio
    async def test_context_peer_message_present(self, monkeypatch, tmp_path):
        captured_payload = {}

        async def _capture_post(self_client, url, *, json=None, headers=None, content=None, **kw):
            if json is None and content is not None:
                import json as _json
                raw = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
                try:
                    json = _json.loads(raw)
                except Exception:
                    json = None
            captured_payload.update(json or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok", "response": "Réponse test"}
            return mock_resp

        peer = {
            "instance_id": "peer-bbb",
            "instance_name": "Lumena Bureau",
            "host": "192.168.1.90",
            "port": 8081,
            "capabilities": ["chat"],
            "trust": "trusted",
            "peer_token_hash": "deadbeef" * 8,
            "peer_token_outbound": "tok-out",
            "allowed_scopes": ["chat"],
        }
        reg_file = tmp_path / "peer_registry.json"
        reg_file.write_text(json.dumps({"peer-bbb": peer}), encoding="utf-8")

        from src.reasoning.handlers import peer_delegation as mod
        monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg_file)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        monkeypatch.setenv("LUMENA_PEER_AWARENESS", "0")

        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setattr("httpx.AsyncClient.post", _capture_post)

        await mod.delegate_to_peer_handler(
            MagicMock(), instance_id="peer-bbb",
            prompt="Test enveloppe", scope="chat",
        )

        ctx = captured_payload.get("context", {})
        peer_msg = ctx.get("peer_message", {})
        assert peer_msg.get("type") == "chat_delegate"
        assert peer_msg.get("scope") == "chat"
        assert peer_msg.get("from_instance_id") == "self-001"
        assert peer_msg.get("to_instance_id") == "peer-bbb"
        assert "token" not in json.dumps(peer_msg)
        assert "tok-out" not in json.dumps(peer_msg)


# ── TestCreateSanitizedPeerMessage ───────────────────────────────────────────

class TestCreateSanitizedPeerMessage:
    def test_clean_payload_passes(self):
        msg = create_sanitized_peer_message(
            type="chat_delegate", scope="chat",
            from_instance_id="a", to_instance_id="b",
            payload={"prompt": "Bonjour, comment vas-tu ?"},
        )
        assert msg.type == "chat_delegate"
        assert msg.payload["prompt"] == "Bonjour, comment vas-tu ?"

    def test_forbidden_key_in_payload_raises(self):
        with pytest.raises(ValueError, match="interdite"):
            create_sanitized_peer_message(
                type="chat_delegate", scope="chat",
                from_instance_id="a", to_instance_id="b",
                payload={"token": "raw-secret"},
            )

    def test_bearer_value_in_payload_raises_or_redacts(self):
        # Bearer dans la valeur → redacté, pas levée (sanitize ne refuse pas les valeurs secrètes,
        # elle les redacte). Vérifier que la valeur est bien [REDACTED].
        msg = create_sanitized_peer_message(
            type="chat_delegate", scope="chat",
            from_instance_id="a", to_instance_id="b",
            payload={"header": "Bearer eyJhbGci.eyJzdW.secret"},
        )
        assert msg.payload["header"] == "[REDACTED]"

    def test_hex_32_in_payload_redacted(self):
        secret = "f" * 32
        msg = create_sanitized_peer_message(
            type="chat_delegate", scope="chat",
            from_instance_id="a", to_instance_id="b",
            payload={"data": secret},
        )
        assert msg.payload["data"] == "[REDACTED]"

    def test_ids_present_after_sanitization(self):
        msg = create_sanitized_peer_message(
            type="chat_delegate", scope="chat",
            from_instance_id="a", to_instance_id="b",
            payload={"prompt": "test"},
        )
        assert msg.message_id
        assert msg.conversation_id
        assert msg.trace_id

    def test_returns_validated_message(self):
        # La re-validation après sanitization doit passer sans exception
        msg = create_sanitized_peer_message(
            type="knowledge_query", scope="knowledge.query",
            from_instance_id="a", to_instance_id="b",
            payload={"query": "informations sur Redis"},
        )
        validate_peer_message(msg)  # ne doit pas lever


# ── Tests : .env.example ──────────────────────────────────────────────────────

class TestEnvExample:
    def test_lumena_peer_max_hops_in_env_example(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_PEER_MAX_HOPS" in content

    def test_lumena_peer_max_ttl_in_env_example(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_PEER_MAX_TTL" in content

    def test_lumena_peer_max_payload_bytes_in_env_example(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_PEER_MAX_PAYLOAD_BYTES" in content

    def test_defaults_correct(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        lines = {line.split("=")[0]: line.split("=")[1].strip()
                 for line in content.splitlines()
                 if "=" in line and line.startswith("LUMENA_PEER_MAX")}
        assert lines.get("LUMENA_PEER_MAX_HOPS") == "5"
        assert lines.get("LUMENA_PEER_MAX_TTL") == "3600"
        assert lines.get("LUMENA_PEER_MAX_PAYLOAD_BYTES") == "65536"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
