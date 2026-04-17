"""
WhatsApp Business Cloud API channel for Lumena.

Utilise l'API REST Meta directement via httpx (pas de SDK tiers).
- Réception: webhook POST depuis Meta (nécessite endpoint public)
- Envoi: POST https://graph.facebook.com/v21.0/{phone_id}/messages
- Media: upload/download via API graph.facebook.com

Env vars requises:
  WHATSAPP_ACCESS_TOKEN       — Token permanent (System User recommandé)
  WHATSAPP_PHONE_NUMBER_ID    — ID du numéro WA Business
  WHATSAPP_VERIFY_TOKEN       — Token arbitraire pour vérification webhook
  WHATSAPP_APP_SECRET         — App secret pour validation signature (optionnel mais recommandé)
"""

import asyncio
import hashlib
import hmac
import mimetypes
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from .base import BaseChannel, ChannelMessage, ChannelType

# Vision module (optionnel — pour analyser les images reçues)
try:
    from ..computer_use.vision import get_vision, VisionModule
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    get_vision = None

try:
    from ..telemetry import publish_trace, push_trace_context, pop_trace_context
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False

# ─── Constantes ────────────────────────────────────────────────────────
_WA_API_VERSION = "v21.0"
_WA_API_BASE = f"https://graph.facebook.com/{_WA_API_VERSION}"
_WA_MAX_TEXT = 4096
_WA_MAX_CAPTION = 1024
_WA_MEDIA_MAX_SIZE = 16 * 1024 * 1024   # 16 MB
_WA_SUPPORTED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_WA_SUPPORTED_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".zip"}
_WA_SUPPORTED_AUDIO_EXT = {".mp3", ".ogg", ".amr", ".m4a", ".opus"}
_WA_SUPPORTED_VIDEO_EXT = {".mp4", ".3gp"}


def _env_flag(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    val = os.getenv(key, "")
    try:
        return float(val) if val else default
    except ValueError:
        return default


def _split_smart(text: str, max_len: int) -> List[str]:
    """Découpe un texte en chunks ≤ max_len.
    Cascade: double newline → newline → espace → coupure dure."""
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = -1
        for sep in ("\n\n", "\n", " "):
            idx = remaining.rfind(sep, 0, max_len)
            if idx > max_len // 4:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


class WhatsAppChannel(BaseChannel):

    def __init__(self):
        super().__init__(ChannelType.WHATSAPP)

        # --- Config via env vars ---
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "lumena_wa_verify")
        self.app_secret = os.getenv("WHATSAPP_APP_SECRET", "")

        self._disable_whatsapp = _env_flag("LUMENA_DISABLE_WHATSAPP", False) or _env_flag("LUMENA_WEB_ONLY", False)

        # --- Timeouts ---
        self._send_timeout = _env_float("LUMENA_WHATSAPP_SEND_TIMEOUT", 30.0)
        self._download_timeout = _env_float("LUMENA_WHATSAPP_DOWNLOAD_TIMEOUT", 60.0)

        # --- Client httpx réutilisé (connection pooling) ---
        self._client: Optional[httpx.AsyncClient] = None

        # --- État runtime ---
        self._state = "stopped"
        self._last_error: Optional[str] = None
        self._processed_message_ids: set = set()
        self._max_dedup_size = 10000

    # ─── Properties ────────────────────────────────────────────────────
    @property
    def is_available(self) -> bool:
        return bool(self.access_token) and bool(self.phone_number_id) and not self._disable_whatsapp

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def get_runtime_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_available,
            "running": self.is_running,
            "state": self._state,
            "last_error": self._last_error,
            "dedup_cache_size": len(self._processed_message_ids),
        }

    # ─── Lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> bool:
        if self._disable_whatsapp:
            self._state = "disabled"
            self._last_error = "whatsapp disabled by config"
            logger.info("WhatsApp disabled by configuration")
            return False

        if not self.access_token or not self.phone_number_id:
            self._state = "error"
            self._last_error = "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID missing"
            logger.error(self._last_error)
            return False

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._send_timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )

        try:
            resp = await self._client.get(f"{_WA_API_BASE}/{self.phone_number_id}")
            if resp.status_code == 200:
                data = resp.json()
                display_phone = data.get("display_phone_number", "unknown")
                logger.info(f"WhatsApp connected: {display_phone}")
                self._state = "running"
                self.is_running = True
                self._last_error = None
                return True
            else:
                self._last_error = f"API check failed: HTTP {resp.status_code}"
                logger.warning(self._last_error)
                self._state = "error"
                self.is_running = False
                return False
        except Exception as e:
            self._last_error = f"API check error: {e}"
            logger.warning(f"WhatsApp API check failed (starting anyway): {e}")
            self._state = "running"
            self.is_running = True
            return True

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self.is_running = False
        self._state = "stopped"
        self._last_error = None
        self._processed_message_ids.clear()
        logger.info("WhatsApp disconnected")

    # ─── Envoi messages ────────────────────────────────────────────────
    async def send_message(self, content: str, target_id: str, **kwargs) -> bool:
        if not self._client or not self.is_running:
            return False

        for chunk in _split_smart(content, _WA_MAX_TEXT):
            payload = {
                "messaging_product": "whatsapp",
                "to": target_id,
                "type": "text",
                "text": {"body": chunk},
            }
            try:
                resp = await self._client.post(
                    f"{_WA_API_BASE}/{self.phone_number_id}/messages",
                    json=payload,
                )
                if resp.status_code not in (200, 201):
                    logger.error(f"WhatsApp send error: {resp.status_code} {resp.text[:200]}")
                    return False
            except Exception as e:
                logger.error(f"WhatsApp send error: {e}")
                return False

        if TELEMETRY_AVAILABLE:
            publish_trace(stage="output_sent", status="ok", channel="whatsapp", mode="chat", summary=content)
        return True

    async def send_photo(self, photo_path: str, target_id: str, caption: str = "") -> bool:
        return await self._send_media(photo_path, target_id, "image", caption)

    async def send_document(self, file_path: str, target_id: str, caption: str = "") -> bool:
        return await self._send_media(file_path, target_id, "document", caption)

    async def send_audio(self, audio_path: str, target_id: str) -> bool:
        return await self._send_media(audio_path, target_id, "audio", "")

    async def send_video(self, video_path: str, target_id: str, caption: str = "") -> bool:
        return await self._send_media(video_path, target_id, "video", caption)

    async def _send_media(self, file_path: str, target_id: str, media_type: str, caption: str) -> bool:
        if not self._client or not self.is_running:
            return False

        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return False

        if path.stat().st_size > _WA_MEDIA_MAX_SIZE:
            logger.error(f"File too large for WhatsApp: {path.stat().st_size} bytes (max {_WA_MEDIA_MAX_SIZE})")
            return False

        try:
            media_id = await self._upload_media(path, media_type)
            if not media_id:
                return False

            payload: Dict[str, Any] = {
                "messaging_product": "whatsapp",
                "to": target_id,
                "type": media_type,
                media_type: {"id": media_id},
            }
            if caption and media_type in ("image", "document", "video"):
                payload[media_type]["caption"] = caption[:_WA_MAX_CAPTION]
            if media_type == "document":
                payload[media_type]["filename"] = path.name

            resp = await self._client.post(
                f"{_WA_API_BASE}/{self.phone_number_id}/messages",
                json=payload,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"WhatsApp media send error: {resp.status_code} {resp.text[:200]}")
                return False

            logger.info(f"WhatsApp {media_type} sent to {target_id}: {path.name}")
            return True

        except Exception as e:
            logger.error(f"WhatsApp media send error: {e}")
            return False

    async def _upload_media(self, path: Path, media_type: str) -> Optional[str]:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f, mime)}
                data = {"messaging_product": "whatsapp", "type": mime}
                resp = await self._client.post(
                    f"{_WA_API_BASE}/{self.phone_number_id}/media",
                    files=files,
                    data=data,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
            if resp.status_code in (200, 201):
                return resp.json().get("id")
            else:
                logger.error(f"WhatsApp media upload failed: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"WhatsApp media upload error: {e}")
            return None

    # ─── Webhook handling ──────────────────────────────────────────────
    def verify_webhook(self, mode: str, token: str, challenge: str) -> Optional[str]:
        if mode == "subscribe" and token == self.verify_token:
            logger.info("WhatsApp webhook verified")
            return challenge
        logger.warning(f"WhatsApp webhook verification failed: mode={mode}")
        return None

    def validate_signature(self, payload: bytes, signature: str) -> bool:
        if not self.app_secret:
            return True
        expected = "sha256=" + hmac.new(
            self.app_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def handle_webhook(self, body: Dict[str, Any]) -> None:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    await self._process_incoming_message(msg, value)

    async def _process_incoming_message(self, msg: Dict[str, Any], value: Dict[str, Any]) -> None:
        msg_id = msg.get("id", "")

        if msg_id in self._processed_message_ids:
            return
        self._processed_message_ids.add(msg_id)
        if len(self._processed_message_ids) > self._max_dedup_size:
            self._processed_message_ids.clear()

        sender = msg.get("from", "")
        msg_type = msg.get("type", "text")
        timestamp = msg.get("timestamp", "")

        contacts = value.get("contacts", [])
        username = ""
        if contacts:
            profile = contacts[0].get("profile", {})
            username = profile.get("name", sender)

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
            await self._handle_text_message(sender, username, text, msg_id, timestamp)
        elif msg_type == "image":
            await self._handle_image_message(msg, sender, username, msg_id, timestamp)
        elif msg_type == "document":
            await self._handle_document_message(msg, sender, username, msg_id, timestamp)
        elif msg_type == "audio":
            await self._handle_audio_message(msg, sender, username, msg_id, timestamp)
        elif msg_type == "video":
            await self._handle_video_message(msg, sender, username, msg_id, timestamp)
        elif msg_type == "location":
            loc = msg.get("location", {})
            text = f"📍 Position: {loc.get('latitude', '?')}, {loc.get('longitude', '?')}"
            if loc.get("name"):
                text += f" ({loc['name']})"
            await self._handle_text_message(sender, username, text, msg_id, timestamp)
        elif msg_type == "reaction":
            pass
        else:
            logger.info(f"WhatsApp: unsupported message type '{msg_type}' from {sender}")
            await self.send_message("Ce type de message n'est pas encore supporté.", sender)

    # ─── Send "read" receipt ───────────────────────────────────────────
    async def _mark_as_read(self, message_id: str) -> None:
        if not self._client:
            return
        try:
            await self._client.post(
                f"{_WA_API_BASE}/{self.phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
            )
        except Exception:
            pass

    # ─── Handlers par type de message ──────────────────────────────────
    async def _handle_text_message(self, sender: str, username: str, text: str, msg_id: str, timestamp: str) -> None:
        await self._mark_as_read(msg_id)

        channel_msg = ChannelMessage(
            content=text,
            channel_type=ChannelType.WHATSAPP,
            user_id=sender,
            username=username,
            timestamp=datetime.now(),
            chat_id=sender,
            metadata={
                "message_id": msg_id,
                "chat_type": "private",
                "wa_timestamp": timestamp,
            },
        )

        trace_tokens = {}
        if TELEMETRY_AVAILABLE:
            trace_tokens = push_trace_context(channel="whatsapp", mode="chat")
            publish_trace(stage="input_received", status="start", channel="whatsapp", mode="chat", summary=text)

        response = await self._on_message_received(channel_msg)

        if TELEMETRY_AVAILABLE:
            publish_trace(stage="output_sent", status="ok", channel="whatsapp", mode="chat", summary=response)
            if trace_tokens:
                pop_trace_context(trace_tokens)

        if response:
            for chunk in _split_smart(response, _WA_MAX_TEXT):
                await self.send_message(chunk, sender)

    async def _handle_image_message(self, msg: Dict, sender: str, username: str, msg_id: str, timestamp: str) -> None:
        await self._mark_as_read(msg_id)

        image_info = msg.get("image", {})
        media_id = image_info.get("id", "")
        caption = image_info.get("caption", "")
        mime_type = image_info.get("mime_type", "image/jpeg")

        save_path = await self._download_media(media_id, mime_type, "image", sender)
        if not save_path:
            await self.send_message("❌ Impossible de télécharger l'image.", sender)
            return

        image_description = await self._analyze_image(save_path)

        from .base import build_image_combined_message
        combined_message = build_image_combined_message(caption, image_description, str(save_path))

        channel_msg = ChannelMessage(
            content=combined_message,
            channel_type=ChannelType.WHATSAPP,
            user_id=sender,
            username=username,
            timestamp=datetime.now(),
            chat_id=sender,
            attachments=[{"filename": save_path.name, "path": str(save_path), "mime_type": mime_type}],
            metadata={
                "message_id": msg_id,
                "chat_type": "private",
                "has_photo": True,
                "photo_path": str(save_path),
            },
        )

        response = await self._on_message_received(channel_msg)
        if not response:
            response = f"📷 Image reçue et analysée.\n\n{image_description}"
        for chunk in _split_smart(response, _WA_MAX_TEXT):
            await self.send_message(chunk, sender)

    async def _handle_document_message(self, msg: Dict, sender: str, username: str, msg_id: str, timestamp: str) -> None:
        await self._mark_as_read(msg_id)

        doc_info = msg.get("document", {})
        media_id = doc_info.get("id", "")
        caption = doc_info.get("caption", "")
        mime_type = doc_info.get("mime_type", "application/octet-stream")
        filename = doc_info.get("filename", f"document_{media_id}")

        save_path = await self._download_media(media_id, mime_type, "document", sender, filename)
        if not save_path:
            await self.send_message("❌ Impossible de télécharger le document.", sender)
            return

        file_size = save_path.stat().st_size
        suffix = save_path.suffix.lower()
        text_like = mime_type.startswith("text/") or suffix in {
            ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log",
            ".ini", ".toml", ".py", ".js", ".ts", ".html", ".css"
        }

        excerpt = ""
        if text_like:
            try:
                raw = save_path.read_bytes()
                max_preview = 2500
                decoded = raw.decode("utf-8", errors="replace").strip()
                excerpt = decoded[:max_preview]
                if len(decoded) > max_preview:
                    excerpt += "\n...[tronqué]"
            except Exception:
                pass

        if excerpt:
            combined = (
                f"[📎 Document reçu sur WhatsApp]\n"
                f"- fichier: {filename}\n- chemin_local: {save_path}\n"
                f"- mime: {mime_type}\n- taille_octets: {file_size}\n"
                f"- caption: {caption or '-'}\n\nContenu extrait:\n{excerpt}\n\n"
                f"Traite la demande utilisateur à partir de ce document."
            )
        else:
            combined = (
                f"[📎 Document reçu sur WhatsApp]\n"
                f"- fichier: {filename}\n- chemin_local: {save_path}\n"
                f"- mime: {mime_type}\n- taille_octets: {file_size}\n"
                f"- caption: {caption or '-'}\n\n"
                f"Le document est binaire ou non prévisualisable. Utilise le chemin local pour l'analyser."
            )

        channel_msg = ChannelMessage(
            content=combined,
            channel_type=ChannelType.WHATSAPP,
            user_id=sender,
            username=username,
            timestamp=datetime.now(),
            chat_id=sender,
            attachments=[{"filename": filename, "path": str(save_path), "mime_type": mime_type, "size": file_size}],
            metadata={
                "message_id": msg_id,
                "has_document": True,
                "document_path": str(save_path),
                "document_filename": filename,
            },
        )

        response = await self._on_message_received(channel_msg)
        if not response:
            response = f"📎 Document reçu: {filename}\nChemin local: {save_path}"
        for chunk in _split_smart(response, _WA_MAX_TEXT):
            await self.send_message(chunk, sender)

    async def _handle_audio_message(self, msg: Dict, sender: str, username: str, msg_id: str, timestamp: str) -> None:
        await self._mark_as_read(msg_id)

        audio_info = msg.get("audio", {})
        media_id = audio_info.get("id", "")
        mime_type = audio_info.get("mime_type", "audio/ogg")

        save_path = await self._download_media(media_id, mime_type, "audio", sender)
        if not save_path:
            await self.send_message("❌ Impossible de télécharger l'audio.", sender)
            return

        transcription = ""
        try:
            from ..voice.stt import get_stt
            stt = get_stt()
            if stt:
                transcription = await asyncio.to_thread(stt.transcribe_file, str(save_path))
        except Exception as e:
            logger.warning(f"WhatsApp STT failed: {e}")

        if transcription:
            combined = f"[🎤 Message vocal WhatsApp — transcription]\n{transcription}"
        else:
            combined = (
                f"[🎤 Message vocal reçu sur WhatsApp]\n"
                f"- chemin_local: {save_path}\n"
                f"- mime: {mime_type}\n"
                f"Impossible de transcrire. Fichier audio sauvegardé au chemin ci-dessus."
            )

        channel_msg = ChannelMessage(
            content=combined,
            channel_type=ChannelType.WHATSAPP,
            user_id=sender,
            username=username,
            timestamp=datetime.now(),
            chat_id=sender,
            metadata={"message_id": msg_id, "has_audio": True, "audio_path": str(save_path)},
        )

        response = await self._on_message_received(channel_msg)
        if response:
            for chunk in _split_smart(response, _WA_MAX_TEXT):
                await self.send_message(chunk, sender)

    async def _handle_video_message(self, msg: Dict, sender: str, username: str, msg_id: str, timestamp: str) -> None:
        await self._mark_as_read(msg_id)

        video_info = msg.get("video", {})
        media_id = video_info.get("id", "")
        caption = video_info.get("caption", "")
        mime_type = video_info.get("mime_type", "video/mp4")

        save_path = await self._download_media(media_id, mime_type, "video", sender)
        if not save_path:
            await self.send_message("❌ Impossible de télécharger la vidéo.", sender)
            return

        combined = (
            f"[🎬 Vidéo reçue sur WhatsApp]\n"
            f"- chemin_local: {save_path}\n- mime: {mime_type}\n"
            f"- caption: {caption or '-'}\n\n"
            f"L'analyse vidéo automatique n'est pas encore supportée. Fichier sauvegardé."
        )

        channel_msg = ChannelMessage(
            content=combined,
            channel_type=ChannelType.WHATSAPP,
            user_id=sender,
            username=username,
            timestamp=datetime.now(),
            chat_id=sender,
            metadata={"message_id": msg_id, "has_video": True, "video_path": str(save_path)},
        )

        response = await self._on_message_received(channel_msg)
        if response:
            for chunk in _split_smart(response, _WA_MAX_TEXT):
                await self.send_message(chunk, sender)

    # ─── Download media depuis Meta ────────────────────────────────────
    async def _download_media(self, media_id: str, mime_type: str, media_type: str, sender: str, filename: str = "") -> Optional[Path]:
        if not self._client:
            return None

        try:
            resp = await self._client.get(f"{_WA_API_BASE}/{media_id}")
            if resp.status_code != 200:
                logger.error(f"WhatsApp media URL fetch failed: {resp.status_code}")
                return None
            media_url = resp.json().get("url")
            if not media_url:
                return None

            resp = await self._client.get(
                media_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self._download_timeout,
            )
            if resp.status_code != 200:
                logger.error(f"WhatsApp media download failed: {resp.status_code}")
                return None

            ext = mimetypes.guess_extension(mime_type) or ""
            if not ext and "/" in mime_type:
                ext = "." + mime_type.split("/")[-1]

            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not filename:
                filename = f"{timestamp_str}_{sender}{ext}"

            try:
                from ..utils.paths import RECEIVED_IMAGES_DIR, WORKSPACE_DIR
            except ImportError:
                RECEIVED_IMAGES_DIR = Path("data/received_images")
                WORKSPACE_DIR = Path("workspace")

            if media_type == "image":
                save_dir = RECEIVED_IMAGES_DIR
            else:
                day_key = datetime.now().strftime("%Y-%m-%d")
                save_dir = WORKSPACE_DIR / day_key / "whatsapp" / f"received_{media_type}s"
            save_dir.mkdir(parents=True, exist_ok=True)

            save_path = save_dir / filename
            save_path.write_bytes(resp.content)
            logger.info(f"WhatsApp {media_type} saved: {save_path}")
            return save_path

        except Exception as e:
            logger.error(f"WhatsApp media download error: {e}")
            return None

    async def _analyze_image(self, save_path: Path) -> str:
        if not VISION_AVAILABLE:
            return "[Module Vision non disponible]"

        vision = get_vision()
        prompt = (
            "Décris cette image de manière factuelle et détaillée:\n"
            "- Ce que tu vois (objets, personnes, texte, scène)\n"
            "- Tout texte visible dans l'image (OCR)\n"
            "- Le contexte général\n\nSois précis et concis."
        )

        for method_name in ("analyze_with_gemini", "analyze_with_claude", "analyze_with_ollama"):
            method = getattr(vision, method_name, None)
            if method:
                result = await method(str(save_path), prompt)
                if result.get("success"):
                    return result.get("answer", "")

        try:
            from PIL import Image as PILImage
            img = PILImage.open(str(save_path))
            ocr_text = vision.analyzer.extract_text(img)
            if ocr_text and ocr_text.strip():
                return f"[Texte extrait par OCR]\n{ocr_text.strip()}"
        except Exception:
            pass

        return "[Image reçue mais impossible de l'analyser]"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
