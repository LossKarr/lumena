"""
mail.py - Handlers mail fragmentés depuis react.py.

Handlers: mail_account_upsert, mail_list_accounts, mail_quick_test,
          mail_list_messages, mail_read_message, mail_download_attachments,
          mail_send, mail_reply_message, mail_delete_message,
          mail_move_message, mail_remove_account,
          telegram_send_document, send_critical_sms,
          place_critical_call, notify_critical.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Mail Handlers ─────────────────────────────────────────────────────────

async def mail_account_upsert_handler(
    ctx: HandlerContext,
    alias: str,
    email_address: str,
    imap_host: str,
    imap_port: int = 993,
    smtp_host: str = "",
    smtp_port: int = 465,
    username: str = "",
    password_env: str = "",
    imap_ssl: bool = True,
    smtp_ssl: bool = True,
) -> HandlerResult:
    """Configure un compte email (IMAP/SMTP) via alias."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.upsert_account(
            alias=alias,
            email_address=email_address,
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            username=username,
            password_env=password_env,
            imap_ssl=imap_ssl,
            smtp_ssl=smtp_ssl,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_account_upsert: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_account_upsert",
            )
        return HandlerResult.ok(
            f"✅ Compte mail configuré: {result.get('alias')}\n"
            f"- fichier: {result.get('accounts_file')}\n"
            f"- secret: lu depuis password_env",
            handler_name="mail_account_upsert",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_account_upsert: {e}",
            handler_name="mail_account_upsert",
        )


async def mail_list_accounts_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les comptes email configurés."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.list_accounts()
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_list_accounts: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_list_accounts",
            )
        accounts = result.get("accounts") or []
        if not accounts:
            return HandlerResult.ok(
                "📭 Aucun compte mail configuré",
                handler_name="mail_list_accounts",
            )
        lines = [f"📬 Comptes mail ({result.get('count', len(accounts))})"]
        for item in accounts:
            lines.append(
                f"- {item.get('alias')}: {item.get('email')} "
                f"(IMAP={item.get('imap_host')}, SMTP={item.get('smtp_host')}, ENV={item.get('password_env')})"
            )
        return HandlerResult.ok(
            "\n".join(lines), handler_name="mail_list_accounts"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_list_accounts: {e}",
            handler_name="mail_list_accounts",
        )


async def mail_quick_test_handler(
    ctx: HandlerContext, alias: str
) -> HandlerResult:
    """Teste la connexion IMAP/SMTP d'un compte email."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.quick_test(alias=alias)
        return HandlerResult.ok(
            f"🧪 Test mail {alias}\n"
            f"- success: {result.get('success')}\n"
            f"- imap_ok: {result.get('imap_ok')}\n"
            f"- smtp_ok: {result.get('smtp_ok')}\n"
            f"- imap_error: {result.get('imap_error') or '-'}\n"
            f"- smtp_error: {result.get('smtp_error') or '-'}",
            handler_name="mail_quick_test",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_quick_test: {e}",
            handler_name="mail_quick_test",
        )


async def mail_list_messages_handler(
    ctx: HandlerContext,
    alias: str,
    folder: str = "INBOX",
    limit: int = 25,
    unseen_only: bool = False,
    sender_filter: str = "",
    subject_filter: str = "",
    sort_by: str = "date",
    order: str = "desc",
) -> HandlerResult:
    """Lit et trie les emails d'un dossier."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.list_messages(
            alias=alias,
            folder=folder,
            limit=limit,
            unseen_only=unseen_only,
            sender_filter=sender_filter,
            subject_filter=subject_filter,
            sort_by=sort_by,
            order=order,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_list_messages: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_list_messages",
            )
        messages = result.get("messages") or []
        if not messages:
            return HandlerResult.ok(
                f"📭 Aucun email trouvé sur {alias}/{folder}",
                handler_name="mail_list_messages",
            )
        lines = [
            f"📨 Emails {alias}/{folder}",
            f"- count: {result.get('count', len(messages))}",
        ]
        for item in messages:
            lines.append(
                f"- uid={item.get('uid')} | {item.get('date')} | "
                f"{item.get('from')} | {item.get('subject')}"
            )
        return HandlerResult.ok(
            "\n".join(lines), handler_name="mail_list_messages"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_list_messages: {e}",
            handler_name="mail_list_messages",
        )


async def mail_read_message_handler(
    ctx: HandlerContext,
    alias: str,
    uid: str,
    folder: str = "INBOX",
    max_chars: int = 12000,
) -> HandlerResult:
    """Lit un email complet par UID."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.read_message(
            alias=alias, uid=uid, folder=folder, max_chars=max_chars
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_read_message: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_read_message",
            )
        attachments = result.get("attachments") or []
        attachment_lines = [
            f"  • {item.get('filename')} ({item.get('content_type')}, {item.get('size')} octets)"
            for item in attachments[:20]
        ]
        attachments_block = (
            "\n".join(attachment_lines) if attachment_lines else "  • Aucune"
        )
        return HandlerResult.ok(
            f"📩 Email lu\n"
            f"- uid: {result.get('uid')}\n"
            f"- date: {result.get('date')}\n"
            f"- from: {result.get('from')}\n"
            f"- to: {result.get('to')}\n"
            f"- subject: {result.get('subject')}\n"
            f"- attachments ({len(attachments)}):\n{attachments_block}\n"
            f"- body:\n{result.get('body') or ''}",
            handler_name="mail_read_message",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_read_message: {e}",
            handler_name="mail_read_message",
        )


async def mail_download_attachments_handler(
    ctx: HandlerContext,
    alias: str,
    uid: str,
    folder: str = "INBOX",
    output_dir: str = "",
    overwrite: bool = False,
    max_files: int = 25,
) -> HandlerResult:
    """Télécharge les pièces jointes d'un email vers un dossier local."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.download_attachments(
            alias=alias,
            uid=uid,
            folder=folder,
            output_dir=output_dir,
            overwrite=overwrite,
            max_files=max_files,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_download_attachments: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_download_attachments",
            )
        attachments = result.get("attachments") or []
        lines = [
            f"📎 Pièces jointes téléchargées",
            f"- uid: {result.get('uid')}",
            f"- dossier: {result.get('output_dir')}",
            f"- count: {result.get('count', len(attachments))}",
        ]
        for item in attachments[:30]:
            lines.append(
                f"  • {item.get('filename')} ({item.get('size')} octets)"
            )
        return HandlerResult.ok(
            "\n".join(lines), handler_name="mail_download_attachments"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_download_attachments: {e}",
            handler_name="mail_download_attachments",
        )


async def mail_send_handler(
    ctx: HandlerContext,
    alias: str,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    attachments: str = "",
) -> HandlerResult:
    """Envoie un email."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.send_message(
            alias=alias,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_send: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_send",
            )
        attached = result.get("attachments") or []
        return HandlerResult.ok(
            f"✅ Email envoyé\n"
            f"- alias: {alias}\n"
            f"- to: {', '.join(result.get('to') or [])}\n"
            f"- subject: {result.get('subject')}\n"
            f"- attachments: {', '.join(attached) if attached else '-'}",
            handler_name="mail_send",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_send: {e}", handler_name="mail_send"
        )


async def mail_reply_message_handler(
    ctx: HandlerContext,
    alias: str,
    uid: str,
    body: str,
    folder: str = "INBOX",
    cc: str = "",
    bcc: str = "",
    reply_all: bool = False,
    attachments: str = "",
) -> HandlerResult:
    """Répond à un email existant par UID."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.reply_message(
            alias=alias,
            uid=uid,
            body=body,
            folder=folder,
            cc=cc,
            bcc=bcc,
            reply_all=reply_all,
            attachments=attachments,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_reply_message: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_reply_message",
            )
        attached = result.get("attachments") or []
        return HandlerResult.ok(
            f"↩️ Réponse envoyée\n"
            f"- alias: {alias}\n"
            f"- uid_source: {result.get('uid')}\n"
            f"- to: {', '.join(result.get('to') or [])}\n"
            f"- cc: {', '.join(result.get('cc') or []) or '-'}\n"
            f"- subject: {result.get('subject')}\n"
            f"- reply_all: {result.get('reply_all')}\n"
            f"- attachments: {', '.join(attached) if attached else '-'}",
            handler_name="mail_reply_message",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_reply_message: {e}",
            handler_name="mail_reply_message",
        )


async def mail_delete_message_handler(
    ctx: HandlerContext,
    alias: str,
    uid: str,
    folder: str = "INBOX",
    expunge: bool = True,
) -> HandlerResult:
    """Supprime un email par UID."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.delete_message(
            alias=alias, uid=uid, folder=folder, expunge=expunge
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_delete_message: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_delete_message",
            )
        return HandlerResult.ok(
            f"🗑️ Email supprimé uid={result.get('uid')} (expunge={result.get('expunged')})",
            handler_name="mail_delete_message",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_delete_message: {e}",
            handler_name="mail_delete_message",
        )


async def mail_move_message_handler(
    ctx: HandlerContext,
    alias: str,
    uid: str,
    target_folder: str,
    source_folder: str = "INBOX",
) -> HandlerResult:
    """Déplace un email vers un autre dossier."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.move_message(
            alias=alias,
            uid=uid,
            target_folder=target_folder,
            source_folder=source_folder,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_move_message: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_move_message",
            )
        return HandlerResult.ok(
            f"📂 Email déplacé uid={result.get('uid')}\n"
            f"- source: {result.get('source')}\n"
            f"- target: {result.get('target')}",
            handler_name="mail_move_message",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_move_message: {e}",
            handler_name="mail_move_message",
        )


async def mail_remove_account_handler(
    ctx: HandlerContext, alias: str
) -> HandlerResult:
    """Supprime un compte email configuré."""
    try:
        hub = ctx.get_mail_hub()
        result = hub.remove_account(alias=alias)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_remove_account: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_remove_account",
            )
        return HandlerResult.ok(
            f"🗑️ Compte supprimé: {result.get('alias')}",
            handler_name="mail_remove_account",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_remove_account: {e}",
            handler_name="mail_remove_account",
        )


# ─── WhatsApp Handlers ────────────────────────────────────────────────────

def _get_whatsapp_channel():
    """Récupère le channel WhatsApp depuis deps (web) ou None."""
    try:
        from web.routes import deps
        ch = getattr(deps, "whatsapp_channel", None)
        if ch and getattr(ch, "is_running", False):
            return ch
    except ImportError:
        pass
    return None


async def send_whatsapp_message_handler(
    ctx: HandlerContext,
    message: str,
    phone_number: str = "",
) -> HandlerResult:
    """Envoie un message texte WhatsApp."""
    try:
        ch = _get_whatsapp_channel()
        if not ch:
            return HandlerResult.fail(
                "❌ send_whatsapp_message: WhatsApp non connecté",
                handler_name="send_whatsapp_message",
            )
        if not phone_number:
            import os as _os
            phone_number = _os.getenv("WHATSAPP_OWNER_PHONE", "").strip()
        if not phone_number:
            return HandlerResult.fail(
                "❌ send_whatsapp_message: phone_number requis",
                handler_name="send_whatsapp_message",
            )
        ok = await ch.send_message(message, phone_number)
        if not ok:
            return HandlerResult.fail(
                f"❌ send_whatsapp_message: échec envoi vers {phone_number}",
                handler_name="send_whatsapp_message",
            )
        return HandlerResult.ok(
            f"✅ Message WhatsApp envoyé à {phone_number}",
            handler_name="send_whatsapp_message",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur send_whatsapp_message: {e}",
            handler_name="send_whatsapp_message",
        )


async def send_whatsapp_photo_handler(
    ctx: HandlerContext,
    file_path: str,
    phone_number: str = "",
    caption: str = "",
) -> HandlerResult:
    """Envoie une photo via WhatsApp."""
    try:
        ch = _get_whatsapp_channel()
        if not ch:
            return HandlerResult.fail(
                "❌ send_whatsapp_photo: WhatsApp non connecté",
                handler_name="send_whatsapp_photo",
            )
        if not phone_number:
            import os as _os
            phone_number = _os.getenv("WHATSAPP_OWNER_PHONE", "").strip()
        if not phone_number:
            return HandlerResult.fail(
                "❌ send_whatsapp_photo: phone_number requis",
                handler_name="send_whatsapp_photo",
            )
        resolved = ctx.resolve_path(file_path)
        if not resolved.exists():
            return HandlerResult.fail(
                f"❌ send_whatsapp_photo: fichier introuvable: {file_path}",
                handler_name="send_whatsapp_photo",
            )
        ok = await ch.send_photo(str(resolved), phone_number, caption=caption)
        if not ok:
            return HandlerResult.fail(
                f"❌ send_whatsapp_photo: échec envoi vers {phone_number}",
                handler_name="send_whatsapp_photo",
            )
        return HandlerResult.ok(
            f"✅ Photo WhatsApp envoyée à {phone_number}: {resolved.name}",
            handler_name="send_whatsapp_photo",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur send_whatsapp_photo: {e}",
            handler_name="send_whatsapp_photo",
        )


async def send_whatsapp_document_handler(
    ctx: HandlerContext,
    file_path: str,
    phone_number: str = "",
    caption: str = "",
) -> HandlerResult:
    """Envoie un document via WhatsApp."""
    try:
        ch = _get_whatsapp_channel()
        if not ch:
            return HandlerResult.fail(
                "❌ send_whatsapp_document: WhatsApp non connecté",
                handler_name="send_whatsapp_document",
            )
        if not phone_number:
            import os as _os
            phone_number = _os.getenv("WHATSAPP_OWNER_PHONE", "").strip()
        if not phone_number:
            return HandlerResult.fail(
                "❌ send_whatsapp_document: phone_number requis",
                handler_name="send_whatsapp_document",
            )
        resolved = ctx.resolve_path(file_path)
        if not resolved.exists():
            return HandlerResult.fail(
                f"❌ send_whatsapp_document: fichier introuvable: {file_path}",
                handler_name="send_whatsapp_document",
            )
        ok = await ch.send_document(str(resolved), phone_number, caption=caption)
        if not ok:
            return HandlerResult.fail(
                f"❌ send_whatsapp_document: échec envoi vers {phone_number}",
                handler_name="send_whatsapp_document",
            )
        return HandlerResult.ok(
            f"✅ Document WhatsApp envoyé à {phone_number}: {resolved.name}",
            handler_name="send_whatsapp_document",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur send_whatsapp_document: {e}",
            handler_name="send_whatsapp_document",
        )


async def send_whatsapp_audio_handler(
    ctx: HandlerContext,
    file_path: str,
    phone_number: str = "",
) -> HandlerResult:
    """Envoie un audio via WhatsApp."""
    try:
        ch = _get_whatsapp_channel()
        if not ch:
            return HandlerResult.fail(
                "❌ send_whatsapp_audio: WhatsApp non connecté",
                handler_name="send_whatsapp_audio",
            )
        if not phone_number:
            import os as _os
            phone_number = _os.getenv("WHATSAPP_OWNER_PHONE", "").strip()
        if not phone_number:
            return HandlerResult.fail(
                "❌ send_whatsapp_audio: phone_number requis",
                handler_name="send_whatsapp_audio",
            )
        resolved = ctx.resolve_path(file_path)
        if not resolved.exists():
            return HandlerResult.fail(
                f"❌ send_whatsapp_audio: fichier introuvable: {file_path}",
                handler_name="send_whatsapp_audio",
            )
        ok = await ch.send_audio(str(resolved), phone_number)
        if not ok:
            return HandlerResult.fail(
                f"❌ send_whatsapp_audio: échec envoi vers {phone_number}",
                handler_name="send_whatsapp_audio",
            )
        return HandlerResult.ok(
            f"✅ Audio WhatsApp envoyé à {phone_number}: {resolved.name}",
            handler_name="send_whatsapp_audio",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur send_whatsapp_audio: {e}",
            handler_name="send_whatsapp_audio",
        )


# ─── Telegram / Critical Alert Handlers ───────────────────────────────────

async def telegram_send_document_handler(
    ctx: HandlerContext,
    file_path: str,
    caption: str = "",
    target_chat_id: str = "",
) -> HandlerResult:
    """Envoie un document vers le chat Telegram."""
    try:
        tool_system = getattr(ctx.lumena, "tool_system", None) if ctx.lumena else None
        if tool_system is None:
            return HandlerResult.fail(
                "❌ telegram_send_document: tool_system indisponible",
                handler_name="telegram_send_document",
            )

        sender = getattr(tool_system, "_telegram_document_sender", None)
        if not callable(sender):
            return HandlerResult.fail(
                "❌ telegram_send_document: transport Telegram non lié",
                handler_name="telegram_send_document",
            )

        resolved = ctx.resolve_path(file_path)
        if not resolved.exists() or not resolved.is_file():
            return HandlerResult.fail(
                f"❌ telegram_send_document: fichier introuvable: {file_path}",
                handler_name="telegram_send_document",
            )

        # Résolution du chat_id: explicite > env var > mémoire owner
        if target_chat_id and str(target_chat_id).strip():
            chat_id = str(target_chat_id).strip()
        else:
            import os as _os
            chat_id = _os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if not chat_id and ctx.lumena is not None:
                try:
                    _mem = getattr(ctx.lumena, "memory", None)
                    if _mem and hasattr(_mem, "get_fact"):
                        chat_id = _mem.get_fact("telegram_owner_id") or ""
                except Exception as e:
                    logger.debug("[mail] chat_id lookup skipped: %s", e)
        if not chat_id:
            return HandlerResult.fail(
                "❌ telegram_send_document: chat_id Telegram inconnu (fournir target_chat_id)",
                handler_name="telegram_send_document",
            )
        safe_caption = (caption or "")[:1024]
        sent = await sender(str(resolved), str(chat_id), safe_caption)
        if not sent:
            return HandlerResult.fail(
                f"❌ telegram_send_document: échec envoi vers chat {chat_id}",
                handler_name="telegram_send_document",
            )
        return HandlerResult.ok(
            f"✅ Document Telegram envoyé\n- chat_id: {chat_id}\n- file: {resolved.name}",
            handler_name="telegram_send_document",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur telegram_send_document: {e}",
            handler_name="telegram_send_document",
        )


async def send_critical_sms_handler(
    ctx: HandlerContext,
    message: str,
    to_number: str = "",
    severity: str = "high",
) -> HandlerResult:
    """Envoie un SMS critique via Twilio."""
    try:
        hub = ctx.get_critical_alert_hub()
        result = hub.send_critical_sms(
            message=message, to_number=to_number, severity=severity
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ send_critical_sms: {result.get('error', 'erreur inconnue')}",
                handler_name="send_critical_sms",
            )
        return HandlerResult.ok(
            f"✅ SMS critique envoyé vers {result.get('to')} (sid={result.get('sid', '-')})",
            handler_name="send_critical_sms",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur send_critical_sms: {e}",
            handler_name="send_critical_sms",
        )


async def place_critical_call_handler(
    ctx: HandlerContext,
    message: str,
    to_number: str = "",
    severity: str = "critical",
) -> HandlerResult:
    """Déclenche un appel vocal critique via Twilio."""
    try:
        hub = ctx.get_critical_alert_hub()
        result = hub.place_critical_call(
            message=message, to_number=to_number, severity=severity
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ place_critical_call: {result.get('error', 'erreur inconnue')}",
                handler_name="place_critical_call",
            )
        return HandlerResult.ok(
            f"✅ Appel critique lancé vers {result.get('to')} (sid={result.get('sid', '-')})",
            handler_name="place_critical_call",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur place_critical_call: {e}",
            handler_name="place_critical_call",
        )


async def notify_critical_handler(
    ctx: HandlerContext,
    message: str,
    to_number: str = "",
    severity: str = "critical",
    prefer: str = "auto",
) -> HandlerResult:
    """Notification critique intelligente (SMS/appel/auto)."""
    try:
        hub = ctx.get_critical_alert_hub()
        result = hub.notify_critical(
            message=message,
            to_number=to_number,
            severity=severity,
            prefer=prefer,
        )
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ notify_critical: {result}",
                handler_name="notify_critical",
            )
        return HandlerResult.ok(
            f"✅ Notification critique envoyée: {result}",
            handler_name="notify_critical",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur notify_critical: {e}",
            handler_name="notify_critical",
        )


async def mail_list_folders_handler(
    ctx: HandlerContext, alias: str = ""
) -> HandlerResult:
    """Liste les dossiers IMAP d'un compte email."""
    if not alias:
        return HandlerResult.fail(
            "❌ mail_list_folders: alias requis.", handler_name="mail_list_folders"
        )
    try:
        hub = ctx.get_mail_hub()
        result = hub.list_folders(alias=alias)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ mail_list_folders: {result.get('error', 'erreur inconnue')}",
                handler_name="mail_list_folders",
            )
        folders = result.get("folders") or []
        if not folders:
            return HandlerResult.ok(
                f"📂 Aucun dossier trouvé pour {alias}",
                handler_name="mail_list_folders",
            )
        lines = [f"📂 Dossiers IMAP {alias} ({len(folders)}):"]
        for f in folders:
            lines.append(f"  - {f}")
        return HandlerResult.ok(
            "\n".join(lines), handler_name="mail_list_folders"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mail_list_folders: {e}",
            handler_name="mail_list_folders",
        )


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def get_mail_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 16 handlers mail."""
    return [
        HandlerDef(
            name="mail_account_upsert",
            description="Configure un compte email (IMAP/SMTP) via alias. À utiliser pour toute demande de configuration de boîte mail.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias unique du compte"},
                    "email_address": {"type": "string", "description": "Adresse email"},
                    "imap_host": {"type": "string", "description": "Serveur IMAP"},
                    "imap_port": {"type": "integer", "description": "Port IMAP (ex: 993)", "default": 993},
                    "smtp_host": {"type": "string", "description": "Serveur SMTP", "default": ""},
                    "smtp_port": {"type": "integer", "description": "Port SMTP", "default": 465},
                    "username": {"type": "string", "description": "Login", "default": ""},
                    "password_env": {"type": "string", "description": "Nom de variable d'environnement contenant le mot de passe"},
                    "imap_ssl": {"type": "boolean", "description": "IMAP en SSL", "default": True},
                    "smtp_ssl": {"type": "boolean", "description": "SMTP en SSL", "default": True},
                },
                "required": ["alias", "email_address", "imap_host", "password_env"],
            },
            handler=mail_account_upsert_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_list_accounts",
            description="Liste les comptes email configurés.",
            parameters={"properties": {}, "required": []},
            handler=mail_list_accounts_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_quick_test",
            description="Teste la connexion IMAP/SMTP d'un compte email.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                },
                "required": ["alias"],
            },
            handler=mail_quick_test_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_list_messages",
            description="Lit et trie les emails d'un dossier.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "folder": {"type": "string", "description": "Dossier IMAP", "default": "INBOX"},
                    "limit": {"type": "integer", "description": "Nombre max d'emails", "default": 25},
                    "unseen_only": {"type": "boolean", "description": "Seulement non lus", "default": False},
                    "sender_filter": {"type": "string", "description": "Filtre expéditeur", "default": ""},
                    "subject_filter": {"type": "string", "description": "Filtre sujet", "default": ""},
                    "sort_by": {"type": "string", "description": "date|from|subject", "default": "date"},
                    "order": {"type": "string", "description": "asc|desc", "default": "desc"},
                },
                "required": ["alias"],
            },
            handler=mail_list_messages_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_read_message",
            description="Lit un email complet par UID (inclut la liste des pièces jointes).",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "uid": {"type": "string", "description": "UID IMAP"},
                    "folder": {"type": "string", "description": "Dossier IMAP", "default": "INBOX"},
                    "max_chars": {"type": "integer", "description": "Taille max du corps", "default": 12000},
                },
                "required": ["alias", "uid"],
            },
            handler=mail_read_message_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_download_attachments",
            description="Télécharge les pièces jointes d'un email vers un dossier local.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "uid": {"type": "string", "description": "UID IMAP"},
                    "folder": {"type": "string", "description": "Dossier IMAP", "default": "INBOX"},
                    "output_dir": {"type": "string", "description": "Dossier de sortie", "default": ""},
                    "overwrite": {"type": "boolean", "description": "Écraser les fichiers existants", "default": False},
                    "max_files": {"type": "integer", "description": "Nombre max d'attachments", "default": 25},
                },
                "required": ["alias", "uid"],
            },
            handler=mail_download_attachments_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_send",
            description="Envoie un email.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "to": {"type": "string", "description": "Destinataire(s) séparés par virgule"},
                    "subject": {"type": "string", "description": "Sujet"},
                    "body": {"type": "string", "description": "Corps"},
                    "cc": {"type": "string", "description": "CC", "default": ""},
                    "bcc": {"type": "string", "description": "BCC", "default": ""},
                    "attachments": {"type": "string", "description": "Chemins de fichiers séparés par virgule", "default": ""},
                },
                "required": ["alias", "to", "subject", "body"],
            },
            handler=mail_send_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_reply_message",
            description="Répond à un email existant par UID en conservant le thread.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "uid": {"type": "string", "description": "UID IMAP du message source"},
                    "body": {"type": "string", "description": "Corps de la réponse"},
                    "folder": {"type": "string", "description": "Dossier IMAP", "default": "INBOX"},
                    "cc": {"type": "string", "description": "CC", "default": ""},
                    "bcc": {"type": "string", "description": "BCC", "default": ""},
                    "reply_all": {"type": "boolean", "description": "Inclure les destinataires originaux", "default": False},
                    "attachments": {"type": "string", "description": "Chemins de fichiers séparés par virgule", "default": ""},
                },
                "required": ["alias", "uid", "body"],
            },
            handler=mail_reply_message_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_delete_message",
            description="Supprime un email par UID.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "uid": {"type": "string", "description": "UID IMAP"},
                    "folder": {"type": "string", "description": "Dossier IMAP", "default": "INBOX"},
                    "expunge": {"type": "boolean", "description": "Purge immédiate", "default": True},
                },
                "required": ["alias", "uid"],
            },
            handler=mail_delete_message_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_move_message",
            description="Déplace un email vers un autre dossier (tri).",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                    "uid": {"type": "string", "description": "UID IMAP"},
                    "target_folder": {"type": "string", "description": "Dossier cible"},
                    "source_folder": {"type": "string", "description": "Dossier source", "default": "INBOX"},
                },
                "required": ["alias", "uid", "target_folder"],
            },
            handler=mail_move_message_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_remove_account",
            description="Supprime un compte email configuré.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte"},
                },
                "required": ["alias"],
            },
            handler=mail_remove_account_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="telegram_send_document",
            description="Envoie un document vers le chat Telegram courant (ou un chat ciblé).",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin local du fichier"},
                    "caption": {"type": "string", "description": "Légende", "default": ""},
                    "target_chat_id": {"type": "string", "description": "Chat ID Telegram NUMERIQUE (ex: 1942152541). Vide = chat courant.", "default": ""},
                },
                "required": ["file_path"],
            },
            handler=telegram_send_document_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        # ── WhatsApp handlers ──
        HandlerDef(
            name="send_whatsapp_message",
            description="Envoie un message texte WhatsApp à un numéro.",
            parameters={
                "properties": {
                    "message": {"type": "string", "description": "Texte du message (max 4096 chars)"},
                    "phone_number": {"type": "string", "description": "Numéro destinataire format international (ex: 33612345678). Vide = owner.", "default": ""},
                },
                "required": ["message"],
            },
            handler=send_whatsapp_message_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="send_whatsapp_photo",
            description="Envoie une photo via WhatsApp.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin local de l'image (JPG, PNG, WebP)"},
                    "phone_number": {"type": "string", "description": "Numéro destinataire. Vide = owner.", "default": ""},
                    "caption": {"type": "string", "description": "Légende (optionnel)", "default": ""},
                },
                "required": ["file_path"],
            },
            handler=send_whatsapp_photo_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="send_whatsapp_document",
            description="Envoie un document (PDF, Excel, etc.) via WhatsApp.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin local du fichier"},
                    "phone_number": {"type": "string", "description": "Numéro destinataire. Vide = owner.", "default": ""},
                    "caption": {"type": "string", "description": "Légende (optionnel)", "default": ""},
                },
                "required": ["file_path"],
            },
            handler=send_whatsapp_document_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="send_whatsapp_audio",
            description="Envoie un fichier audio via WhatsApp.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin local du fichier audio (MP3, OGG, M4A)"},
                    "phone_number": {"type": "string", "description": "Numéro destinataire. Vide = owner.", "default": ""},
                },
                "required": ["file_path"],
            },
            handler=send_whatsapp_audio_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="send_critical_sms",
            description="Envoie un SMS critique vers ton numéro (Twilio).",
            parameters={
                "properties": {
                    "message": {"type": "string", "description": "Message d'alerte"},
                    "to_number": {"type": "string", "description": "Numéro cible", "default": ""},
                    "severity": {"type": "string", "description": "info|medium|high|critical (SMS: high/critical)", "default": "high"},
                },
                "required": ["message"],
            },
            handler=send_critical_sms_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="place_critical_call",
            description="Déclenche un appel vocal critique vers ton numéro (Twilio).",
            parameters={
                "properties": {
                    "message": {"type": "string", "description": "Message vocal"},
                    "to_number": {"type": "string", "description": "Numéro cible", "default": ""},
                    "severity": {"type": "string", "description": "high|critical", "default": "critical"},
                },
                "required": ["message"],
            },
            handler=place_critical_call_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="notify_critical",
            description="Notification critique intelligente (SMS/appel/auto).",
            parameters={
                "properties": {
                    "message": {"type": "string", "description": "Message d'alerte"},
                    "to_number": {"type": "string", "description": "Numéro cible", "default": ""},
                    "severity": {"type": "string", "description": "info|medium|high|critical", "default": "critical"},
                    "prefer": {"type": "string", "description": "auto|sms|call|both", "default": "auto"},
                },
                "required": ["message"],
            },
            handler=notify_critical_handler,
            category="mail",
            source_module="handlers.mail",
        ),
        HandlerDef(
            name="mail_list_folders",
            description="Liste les dossiers IMAP d'un compte email.",
            parameters={
                "properties": {
                    "alias": {"type": "string", "description": "Alias du compte email"},
                },
                "required": ["alias"],
            },
            handler=mail_list_folders_handler,
            category="mail",
            source_module="handlers.mail",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
