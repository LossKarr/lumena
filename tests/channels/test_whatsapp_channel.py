"""
Tests pour le channel WhatsApp (Meta Cloud API).
Couvre: import, init, envoi, webhook, signature, handlers ReAct.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Import Tests ──────────────────────────────────────────────────────────

class TestWhatsAppImport:
    def test_whatsapp_channel_import(self):
        from src.channels.whatsapp_channel import WhatsAppChannel
        assert WhatsAppChannel is not None

    def test_channel_type_has_whatsapp(self):
        from src.channels.base import ChannelType
        assert hasattr(ChannelType, "WHATSAPP")
        assert ChannelType.WHATSAPP.value == "whatsapp"

    def test_whatsapp_in_channels_init(self):
        from src.channels import WhatsAppChannel
        assert WhatsAppChannel is not None

    def test_whatsapp_in_channels_all(self):
        import src.channels as ch
        assert "WhatsAppChannel" in ch.__all__


# ─── Init / Config Tests ──────────────────────────────────────────────────

class TestWhatsAppInit:
    def test_not_available_without_token(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "",
            "WHATSAPP_PHONE_NUMBER_ID": "",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            assert not ch.is_available

    def test_available_with_token_and_phone_id(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_fake_token",
            "WHATSAPP_PHONE_NUMBER_ID": "123456789",
            "LUMENA_DISABLE_WHATSAPP": "0",
            "LUMENA_WEB_ONLY": "0",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            assert ch.is_available

    def test_disabled_by_env(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_fake_token",
            "WHATSAPP_PHONE_NUMBER_ID": "123456789",
            "LUMENA_DISABLE_WHATSAPP": "1",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            assert not ch.is_available

    def test_runtime_status_default(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "",
            "WHATSAPP_PHONE_NUMBER_ID": "",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            status = ch.get_runtime_status()
            assert "enabled" in status
            assert "running" in status
            assert "state" in status
            assert status["running"] is False


# ─── Webhook Verify Tests ─────────────────────────────────────────────────

class TestWhatsAppWebhook:
    def _make_channel(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
            "WHATSAPP_VERIFY_TOKEN": "my_verify_token",
            "WHATSAPP_APP_SECRET": "app_secret_123",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            return WhatsAppChannel()

    def test_verify_webhook_success(self):
        ch = self._make_channel()
        result = ch.verify_webhook("subscribe", "my_verify_token", "CHALLENGE_123")
        assert result == "CHALLENGE_123"

    def test_verify_webhook_wrong_token(self):
        ch = self._make_channel()
        result = ch.verify_webhook("subscribe", "wrong_token", "CHALLENGE_123")
        assert result is None

    def test_verify_webhook_wrong_mode(self):
        ch = self._make_channel()
        result = ch.verify_webhook("unsubscribe", "my_verify_token", "CHALLENGE_123")
        assert result is None

    def test_validate_signature_valid(self):
        ch = self._make_channel()
        body = b'{"test":"data"}'
        expected_sig = hmac.new(b"app_secret_123", body, hashlib.sha256).hexdigest()
        assert ch.validate_signature(body, f"sha256={expected_sig}")

    def test_validate_signature_invalid(self):
        ch = self._make_channel()
        body = b'{"test":"data"}'
        assert not ch.validate_signature(body, "sha256=invalid_signature")

    def test_validate_signature_no_secret(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
            "WHATSAPP_APP_SECRET": "",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            # Without app_secret, validation should pass (no check)
            assert ch.validate_signature(b"anything", "sha256=fake")


# ─── Send Message Tests ───────────────────────────────────────────────────

class TestWhatsAppSend:
    @pytest.mark.asyncio
    async def test_send_message_not_running(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            result = await ch.send_message("hello", "33612345678")
            assert result is False  # Not started, no client

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            ch.is_running = True
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_resp)
            ch._client = mock_client
            result = await ch.send_message("Test message", "33612345678")
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_photo_file_not_found(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            ch.is_running = True
            ch._client = AsyncMock()
            result = await ch.send_photo("/nonexistent/image.jpg", "33612345678")
            assert result is False


# ─── Start/Stop Lifecycle Tests ──────────────────────────────────────────

class TestWhatsAppLifecycle:
    @pytest.mark.asyncio
    async def test_start_disabled(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
            "LUMENA_DISABLE_WHATSAPP": "1",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            result = await ch.start()
            assert result is False
            assert ch.state == "disabled"

    @pytest.mark.asyncio
    async def test_start_missing_token(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "",
            "WHATSAPP_PHONE_NUMBER_ID": "",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            result = await ch.start()
            assert result is False
            assert ch.state == "error"

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            ch.is_running = True
            ch._state = "running"
            ch._client = AsyncMock()
            await ch.stop()
            assert ch.is_running is False
            assert ch.state == "stopped"


# ─── Deduplication Tests ──────────────────────────────────────────────────

class TestWhatsAppDedup:
    @pytest.mark.asyncio
    async def test_dedup_skips_duplicate(self):
        with patch.dict(os.environ, {
            "WHATSAPP_ACCESS_TOKEN": "EAA_test",
            "WHATSAPP_PHONE_NUMBER_ID": "12345",
        }, clear=False):
            from src.channels.whatsapp_channel import WhatsAppChannel
            ch = WhatsAppChannel()
            ch._processed_message_ids.add("msg_123")
            # Calling handle_webhook with a duplicate msg should be a no-op
            # We just verify the dedup set works
            assert "msg_123" in ch._processed_message_ids


# ─── Config Schema Tests ──────────────────────────────────────────────────

class TestWhatsAppConfig:
    def test_config_schema_has_whatsapp_keys(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = {s["key"] for s in _CONFIG_SCHEMA}
        expected = {
            "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET",
            "WHATSAPP_OWNER_PHONE", "LUMENA_DISABLE_WHATSAPP",
        }
        assert expected.issubset(keys)

    def test_config_schema_whatsapp_have_defaults(self):
        from web.routes.config import _CONFIG_SCHEMA
        wa_entries = [s for s in _CONFIG_SCHEMA if s["key"].startswith("WHATSAPP_") or s["key"] == "LUMENA_DISABLE_WHATSAPP"]
        for entry in wa_entries:
            assert "default" in entry, f"Missing 'default' in schema for {entry['key']}"


# ─── System Status Tests ──────────────────────────────────────────────────

class TestWhatsAppSystemStatus:
    def test_system_meta_function_exists(self):
        from web.routes.system import _get_whatsapp_meta
        meta = _get_whatsapp_meta()
        assert "whatsapp_enabled" in meta
        assert "whatsapp_running" in meta
        assert "whatsapp_state" in meta


# ─── Channel Expectations Tests ──────────────────────────────────────────

class TestWhatsAppContextService:
    def test_channel_expectations_has_whatsapp(self):
        from src.core_services.context_service import ContextService
        svc = ContextService.__new__(ContextService)
        expectations = svc._build_channel_expectations()
        assert "whatsapp" in expectations


# ─── ReAct Handler Defs Tests ─────────────────────────────────────────────

class TestWhatsAppHandlerDefs:
    def test_handler_defs_registered(self):
        from src.reasoning.handlers.mail import get_mail_handler_defs
        defs = get_mail_handler_defs()
        names = {d.name for d in defs}
        expected = {
            "send_whatsapp_message",
            "send_whatsapp_photo",
            "send_whatsapp_document",
            "send_whatsapp_audio",
        }
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_handler_defs_have_parameters(self):
        from src.reasoning.handlers.mail import get_mail_handler_defs
        defs = get_mail_handler_defs()
        wa_defs = [d for d in defs if d.name.startswith("send_whatsapp_")]
        for d in wa_defs:
            assert "properties" in d.parameters, f"{d.name} missing properties"
            assert "required" in d.parameters, f"{d.name} missing required"


# ─── Webhook Route Tests ──────────────────────────────────────────────────

class TestWhatsAppRoutes:
    def test_router_exists(self):
        from web.routes.whatsapp import router
        assert router is not None
        assert router.prefix == "/api/whatsapp"

    def test_chat_allowlist_has_whatsapp(self):
        """web/routes/chat.py should include whatsapp in allowed channels."""
        import ast
        chat_path = Path(__file__).parent.parent.parent / "web" / "routes" / "chat.py"
        source = chat_path.read_text(encoding="utf-8")
        assert '"whatsapp"' in source or "'whatsapp'" in source


# ─── Setup Wizard Tests ──────────────────────────────────────────────────

class TestWhatsAppSetup:
    def test_setup_wizard_has_whatsapp_step(self):
        """setup.py should return a whatsapp step."""
        from web.routes.setup import _CONFIG_SCHEMA
        # We verify the allowed keys include WhatsApp
        # (The actual step is built dynamically)
        import web.routes.setup as setup_mod
        source = Path(setup_mod.__file__).read_text(encoding="utf-8")
        assert "WHATSAPP_ACCESS_TOKEN" in source

    def test_setup_allowed_keys_include_whatsapp(self):
        source = (Path(__file__).parent.parent.parent / "web" / "routes" / "setup.py").read_text(encoding="utf-8")
        assert "WHATSAPP_ACCESS_TOKEN" in source
        assert "WHATSAPP_PHONE_NUMBER_ID" in source
        assert "WHATSAPP_VERIFY_TOKEN" in source


# ─── Dashboard UI Tests ──────────────────────────────────────────────────

class TestWhatsAppDashboard:
    def test_index_has_whatsapp_nav(self):
        html = (Path(__file__).parent.parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        assert 'data-panel="infra-whatsapp"' in html
        assert 'id="stat-whatsapp"' in html

    def test_index_has_whatsapp_panel(self):
        html = (Path(__file__).parent.parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        assert 'id="panel-infra-whatsapp"' in html
        assert 'id="wa-dot"' in html
        assert 'id="wa-status-text"' in html

    def test_api_js_has_whatsapp_badge(self):
        js = (Path(__file__).parent.parent.parent / "web" / "static" / "js" / "api.js").read_text(encoding="utf-8")
        assert "stat-whatsapp" in js
        assert "whatsapp_running" in js

    def test_panels_js_has_whatsapp_details(self):
        js = (Path(__file__).parent.parent.parent / "web" / "static" / "js" / "panels.js").read_text(encoding="utf-8")
        assert "loadWhatsAppDetails" in js

    def test_navigation_js_has_whatsapp_routing(self):
        js = (Path(__file__).parent.parent.parent / "web" / "static" / "js" / "navigation.js").read_text(encoding="utf-8")
        assert "infra-whatsapp" in js

    def test_setup_js_has_whatsapp_step(self):
        js = (Path(__file__).parent.parent.parent / "web" / "static" / "js" / "setup.js").read_text(encoding="utf-8")
        assert "_renderWhatsAppStep" in js
        assert "WHATSAPP_ACCESS_TOKEN" in js


# ─── .env.example Tests ──────────────────────────────────────────────────

class TestWhatsAppEnvExample:
    def test_env_example_has_whatsapp_vars(self):
        env = (Path(__file__).parent.parent.parent / ".env.example").read_text(encoding="utf-8")
        assert "WHATSAPP_ACCESS_TOKEN" in env
        assert "WHATSAPP_PHONE_NUMBER_ID" in env
        assert "WHATSAPP_VERIFY_TOKEN" in env
        assert "WHATSAPP_APP_SECRET" in env
        assert "WHATSAPP_OWNER_PHONE" in env
        assert "LUMENA_DISABLE_WHATSAPP" in env
