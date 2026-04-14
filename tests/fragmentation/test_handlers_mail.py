"""
Tests unitaires pour handlers/mail.py — 15 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Les hubs sont mockés via ctx._mail_hub et ctx._critical_alert_hub.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.mail import (
    mail_account_upsert_handler,
    mail_list_accounts_handler,
    mail_quick_test_handler,
    mail_list_messages_handler,
    mail_read_message_handler,
    mail_download_attachments_handler,
    mail_send_handler,
    mail_reply_message_handler,
    mail_delete_message_handler,
    mail_move_message_handler,
    mail_remove_account_handler,
    telegram_send_document_handler,
    send_critical_sms_handler,
    place_critical_call_handler,
    notify_critical_handler,
    get_mail_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    c = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")
    c._mail_hub = MagicMock()
    c._critical_alert_hub = MagicMock()
    return c


# ─── mail_account_upsert ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_account_upsert_success(ctx):
    ctx._mail_hub.upsert_account.return_value = {
        "success": True, "alias": "work", "accounts_file": "/data/mail/accounts.json"
    }
    r = await mail_account_upsert_handler(
        ctx, alias="work", email_address="a@b.com", imap_host="imap.b.com"
    )
    assert r.success
    assert "Compte mail configuré" in r.output


@pytest.mark.asyncio
async def test_mail_account_upsert_failure(ctx):
    ctx._mail_hub.upsert_account.return_value = {"success": False, "error": "bad alias"}
    r = await mail_account_upsert_handler(
        ctx, alias="", email_address="a@b.com", imap_host="imap.b.com"
    )
    assert not r.success


# ─── mail_list_accounts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_list_accounts_empty(ctx):
    ctx._mail_hub.list_accounts.return_value = {"success": True, "accounts": []}
    r = await mail_list_accounts_handler(ctx)
    assert r.success
    assert "Aucun" in r.output


@pytest.mark.asyncio
async def test_mail_list_accounts_with_data(ctx):
    ctx._mail_hub.list_accounts.return_value = {
        "success": True, "count": 1,
        "accounts": [{"alias": "work", "email": "a@b.com", "imap_host": "imap", "smtp_host": "smtp", "password_env": "P"}]
    }
    r = await mail_list_accounts_handler(ctx)
    assert r.success
    assert "work" in r.output


# ─── mail_quick_test ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_quick_test(ctx):
    ctx._mail_hub.quick_test.return_value = {
        "success": True, "imap_ok": True, "smtp_ok": True,
        "imap_error": None, "smtp_error": None,
    }
    r = await mail_quick_test_handler(ctx, alias="work")
    assert r.success
    assert "Test mail" in r.output


# ─── mail_list_messages ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_list_messages_empty(ctx):
    ctx._mail_hub.list_messages.return_value = {"success": True, "messages": []}
    r = await mail_list_messages_handler(ctx, alias="work")
    assert r.success
    assert "Aucun email" in r.output


@pytest.mark.asyncio
async def test_mail_list_messages_with_data(ctx):
    ctx._mail_hub.list_messages.return_value = {
        "success": True, "count": 1,
        "messages": [{"uid": "1", "date": "2026-03-01", "from": "a@b", "subject": "Hi"}]
    }
    r = await mail_list_messages_handler(ctx, alias="work")
    assert r.success
    assert "uid=1" in r.output


# ─── mail_read_message ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_read_message_success(ctx):
    ctx._mail_hub.read_message.return_value = {
        "success": True, "uid": "1", "date": "2026-03-01",
        "from": "a@b", "to": "c@d", "subject": "Test",
        "attachments": [], "body": "Hello",
    }
    r = await mail_read_message_handler(ctx, alias="work", uid="1")
    assert r.success
    assert "Hello" in r.output


@pytest.mark.asyncio
async def test_mail_read_message_failure(ctx):
    ctx._mail_hub.read_message.return_value = {"success": False, "error": "not found"}
    r = await mail_read_message_handler(ctx, alias="work", uid="999")
    assert not r.success


# ─── mail_download_attachments ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_download_attachments_success(ctx):
    ctx._mail_hub.download_attachments.return_value = {
        "success": True, "uid": "1", "output_dir": "/tmp/dl",
        "count": 1, "attachments": [{"filename": "a.pdf", "size": 1024}]
    }
    r = await mail_download_attachments_handler(ctx, alias="work", uid="1")
    assert r.success
    assert "a.pdf" in r.output


# ─── mail_send ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_send_success(ctx):
    ctx._mail_hub.send_message.return_value = {
        "success": True, "to": ["a@b.com"], "subject": "Hi", "attachments": []
    }
    r = await mail_send_handler(ctx, alias="work", to="a@b.com", subject="Hi", body="Hello")
    assert r.success
    assert "Email envoyé" in r.output


@pytest.mark.asyncio
async def test_mail_send_failure(ctx):
    ctx._mail_hub.send_message.return_value = {"success": False, "error": "SMTP error"}
    r = await mail_send_handler(ctx, alias="work", to="a@b.com", subject="Hi", body="Hello")
    assert not r.success


# ─── mail_reply_message ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_reply_message_success(ctx):
    ctx._mail_hub.reply_message.return_value = {
        "success": True, "uid": "1", "to": ["a@b.com"],
        "cc": [], "subject": "Re: Hi", "reply_all": False, "attachments": []
    }
    r = await mail_reply_message_handler(ctx, alias="work", uid="1", body="Thanks")
    assert r.success
    assert "Réponse envoyée" in r.output


# ─── mail_delete_message ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_delete_message_success(ctx):
    ctx._mail_hub.delete_message.return_value = {"success": True, "uid": "1", "expunged": True}
    r = await mail_delete_message_handler(ctx, alias="work", uid="1")
    assert r.success
    assert "supprimé" in r.output


# ─── mail_move_message ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_move_message_success(ctx):
    ctx._mail_hub.move_message.return_value = {
        "success": True, "uid": "1", "source": "INBOX", "target": "Archives"
    }
    r = await mail_move_message_handler(ctx, alias="work", uid="1", target_folder="Archives")
    assert r.success
    assert "déplacé" in r.output


# ─── mail_remove_account ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mail_remove_account_success(ctx):
    ctx._mail_hub.remove_account.return_value = {"success": True, "alias": "work"}
    r = await mail_remove_account_handler(ctx, alias="work")
    assert r.success
    assert "supprimé" in r.output


# ─── telegram_send_document ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_send_document_no_lumena(ctx):
    """Sans lumena, retourne erreur."""
    r = await telegram_send_document_handler(ctx, file_path="/tmp/a.pdf")
    assert not r.success
    assert "indisponible" in (r.error or r.output)


@pytest.mark.asyncio
async def test_telegram_send_document_success(ctx, tmp_path):
    """Avec lumena.tool_system._telegram_document_sender, envoie le document."""
    mock_lumena = MagicMock()
    mock_ts = MagicMock()
    # Crée un vrai fichier pour passer la vérification d'existence
    test_file = tmp_path / "a.pdf"
    test_file.write_bytes(b"fake-pdf")
    mock_ts._telegram_document_sender = AsyncMock(return_value=True)
    mock_ts._resolve_user_path = MagicMock(return_value=test_file)
    mock_ts._resolve_telegram_chat_id = MagicMock(return_value="123456")
    mock_lumena.tool_system = mock_ts
    ctx.lumena = mock_lumena
    r = await telegram_send_document_handler(ctx, file_path=str(test_file))
    assert r.success
    assert "envoyé" in r.output.lower()


# ─── send_critical_sms ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_critical_sms_via_hub(ctx):
    ctx._critical_alert_hub.send_critical_sms.return_value = {
        "success": True, "to": "+33...", "sid": "SM123"
    }
    r = await send_critical_sms_handler(ctx, message="Alert!")
    assert r.success
    assert "SMS critique envoyé" in r.output


# ─── place_critical_call ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_critical_call_via_hub(ctx):
    ctx._critical_alert_hub.place_critical_call.return_value = {
        "success": True, "to": "+33...", "sid": "CA123"
    }
    r = await place_critical_call_handler(ctx, message="URGENT!")
    assert r.success
    assert "Appel critique" in r.output


# ─── notify_critical ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_critical_via_hub(ctx):
    ctx._critical_alert_hub.notify_critical.return_value = {
        "success": True, "method": "sms"
    }
    r = await notify_critical_handler(ctx, message="ALERT!")
    assert r.success
    assert "Notification critique" in r.output


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_mail_handler_defs()
    assert len(defs) == 20


def test_handler_defs_names():
    defs = get_mail_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_expected_names():
    expected = {
        "mail_account_upsert", "mail_list_accounts", "mail_quick_test",
        "mail_list_messages", "mail_read_message", "mail_download_attachments",
        "mail_send", "mail_reply_message", "mail_delete_message",
        "mail_move_message", "mail_remove_account",
        "telegram_send_document", "send_critical_sms",
        "place_critical_call", "notify_critical",
        "mail_list_folders",
        "send_whatsapp_message", "send_whatsapp_document",
        "send_whatsapp_photo", "send_whatsapp_audio",
    }
    defs = get_mail_handler_defs()
    actual = {d.name for d in defs}
    assert actual == expected


def test_handler_defs_have_handlers():
    for d in get_mail_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
