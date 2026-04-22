"""
Telegram channel for Lumena.
"""

import asyncio
import os
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Import Vision Module for image analysis
try:
    from ..computer_use.vision import get_vision, VisionModule
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    get_vision = None

from loguru import logger

from .base import BaseChannel, ChannelMessage, ChannelType
try:
    from ..utils.file_lock import ProcessFileLock, default_lock_path
except ImportError:
    from src.utils.file_lock import ProcessFileLock, default_lock_path  # fallback absolu
try:
    from ..telemetry import publish_trace, push_trace_context, pop_trace_context
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False  # telemetry non disponible

try:
    from telegram import Update
    from telegram.error import (
        BadRequest as TelegramBadRequest,
        Conflict,
        TelegramError,
        TimedOut as TelegramTimedOut,
    )
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    TelegramError = Exception  # type: ignore[assignment]
    Conflict = Exception  # type: ignore[assignment]
    TelegramBadRequest = Exception  # type: ignore[assignment]
    TelegramTimedOut = None  # type: ignore[assignment]
    logger.warning("python-telegram-bot not installed. Run: pip install python-telegram-bot")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default  # parsing int échoué


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default  # parsing float échoué


_TG_MAX = 4000  # marge de sécurité sous la limite Telegram (4096)


def _split_smart(text: str, max_len: int = _TG_MAX) -> list:
    """Découpe un texte en morceaux <= max_len caractères sans couper les mots.

    Stratégie (cascade) :
    1. Si le texte tient dans max_len → retourne [text] directement.
    2. Coupe au dernier double-saut de ligne (\n\n) avant la limite.
    3. Coupe au dernier saut de ligne simple (\n) avant la limite.
    4. Coupe au dernier espace avant la limite.
    5. Coupe dure (dernier recours, cas extrêmes sans espaces).
    """
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # cherche le meilleur point de coupure
        window = text[:max_len]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_len  # dernier recours
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return [p for p in parts if p]


class TelegramChannel(BaseChannel):
    """Telegram channel implementation."""

    def __init__(self, token: Optional[str] = None):
        super().__init__(ChannelType.TELEGRAM)

        self.token = token or os.getenv("TELEGRAM_TOKEN")
        self._app: Optional[Any] = None

        self._conflict_seen = False
        self._conflict_reported = False
        self._last_error: Optional[str] = None
        self._state = "stopped"

        self._web_only_mode = _env_flag("LUMENA_WEB_ONLY", False)
        self._disable_telegram = _env_flag("LUMENA_DISABLE_TELEGRAM", False) or self._web_only_mode
        lock_default = str(default_lock_path("lumena_telegram.lock"))
        self._lock_path = Path(os.getenv("LUMENA_TELEGRAM_LOCK_PATH", lock_default))
        self._lock: Optional[ProcessFileLock] = None

        self._connect_timeout = _env_float("LUMENA_TELEGRAM_CONNECT_TIMEOUT", 15.0)
        self._read_timeout = _env_float("LUMENA_TELEGRAM_READ_TIMEOUT", 30.0)
        self._write_timeout = _env_float("LUMENA_TELEGRAM_WRITE_TIMEOUT", 30.0)
        self._pool_timeout = _env_float("LUMENA_TELEGRAM_POOL_TIMEOUT", 30.0)
        self._bootstrap_retries = _env_int("LUMENA_TELEGRAM_BOOTSTRAP_RETRIES", 1)
        self._startup_retries = max(0, _env_int("LUMENA_TELEGRAM_STARTUP_RETRIES", 2))
        self._startup_retry_delay = max(0.0, _env_float("LUMENA_TELEGRAM_STARTUP_RETRY_DELAY", 2.0))
        self._transient_error_active = False
        self._transient_error_count = 0
        self._transient_backoff_until = 0.0
        self._next_transient_log_at = 0.0

    @property
    def is_available(self) -> bool:
        return TELEGRAM_AVAILABLE and bool(self.token) and not self._disable_telegram

    @property
    def conflict_seen(self) -> bool:
        return self._conflict_seen

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def state(self) -> str:
        return self._state

    def get_runtime_status(self) -> Dict[str, Any]:
        self._refresh_transient_state()
        return {
            "enabled": self.is_available,
            "running": self.is_running,
            "conflict_seen": self._conflict_seen,
            "last_error": self._last_error,
            "state": self._state,
            "transient_error": self._transient_error_active,
            "transient_backoff_sec": self._current_transient_backoff_sec(),
        }

    async def start(self) -> bool:
        if self._disable_telegram:
            self._state = "disabled"
            if self._web_only_mode:
                self._last_error = "telegram disabled by LUMENA_WEB_ONLY=1"
            else:
                self._last_error = "telegram disabled by LUMENA_DISABLE_TELEGRAM=1"
            logger.info("Telegram disabled by configuration")
            return False

        if self._state == "disabled_conflict":
            logger.warning("Telegram remains disabled after conflict in this process")
            return False

        if self.is_running:
            return True

        if not TELEGRAM_AVAILABLE:
            self._state = "error"
            self._last_error = "python-telegram-bot not installed"
            logger.error("python-telegram-bot not installed")
            return False

        if not self.token:
            self._state = "error"
            self._last_error = "TELEGRAM_TOKEN missing"
            logger.error("TELEGRAM_TOKEN missing")
            return False

        self._state = "starting"
        self._last_error = None
        self._conflict_seen = False
        self._conflict_reported = False
        self._reset_transient_polling_state()

        self._lock = ProcessFileLock(
            self._lock_path,
            lock_name="lumena-telegram",
            owner_id=f"telegram:{os.getpid()}",
        )
        if not self._lock.acquire():
            holder = self._lock.read_lock_info()
            holder_pid = holder.get("pid", "unknown")
            self._mark_conflict(f"telegram lock busy ({self._lock_path}) owner_pid={holder_pid}")
            logger.warning(
                f"Telegram disabled: lock already taken ({self._lock_path}, pid={holder_pid})"
            )
            return False

        max_attempts = self._startup_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                builder = Application.builder().token(self.token)
                builder = self._configure_builder_timeouts(builder)
                self._app = builder.build()
                self._app.add_handler(CommandHandler("start", self._handle_start))
                self._app.add_handler(CommandHandler("help", self._handle_help))
                self._app.add_handler(
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
                )
                # Handler for photos/images
                self._app.add_handler(
                    MessageHandler(filters.PHOTO, self._handle_photo)
                )
                self._app.add_handler(
                    MessageHandler(filters.Document.ALL, self._handle_document)
                )

                def _polling_error_callback(error: TelegramError) -> None:
                    if isinstance(error, Conflict):
                        self._mark_conflict("Conflict: terminated by other getUpdates request")
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(self._shutdown_after_conflict())
                        except RuntimeError:
                            self.is_running = False  # pas d'event loop pour shutdown
                        return

                    if self._is_transient_polling_error(error):
                        self._record_transient_polling_error(error)
                        return

                    self._reset_transient_polling_state()
                    self._last_error = f"Polling error: {error}"
                    logger.error(f"Telegram polling error: {error}")

                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(
                    drop_pending_updates=True,
                    bootstrap_retries=self._bootstrap_retries,
                    error_callback=_polling_error_callback,
                )

                if self._conflict_seen:
                    await self._shutdown_after_conflict()
                    return False

                self.is_running = True
                self._state = "running"
                self._last_error = None
                self._reset_transient_polling_state()
                logger.info("Telegram connected")
                return True

            except Exception as e:
                self._last_error = f"Startup error: {e}"
                is_timeout = self._is_timeout_error(e)
                can_retry = is_timeout and attempt < max_attempts
                if can_retry:
                    logger.warning(
                        "Telegram startup timeout (attempt {}/{}): {}. Retrying in {:.1f}s",
                        attempt,
                        max_attempts,
                        e,
                        self._startup_retry_delay,
                    )
                    await self._safe_shutdown(release_lock=False)
                    if self._startup_retry_delay > 0:
                        await asyncio.sleep(self._startup_retry_delay)
                    continue

                self._state = "error"
                logger.error(f"Telegram startup error: {e}")
                await self._safe_shutdown()
                return False

        self._state = "error"
        self._last_error = "Startup failed after retries"
        await self._safe_shutdown()
        return False

    def _configure_builder_timeouts(self, builder: Any) -> Any:
        timeout_settings = {
            "connect_timeout": self._connect_timeout,
            "read_timeout": self._read_timeout,
            "write_timeout": self._write_timeout,
            "pool_timeout": self._pool_timeout,
            "get_updates_connect_timeout": self._connect_timeout,
            "get_updates_read_timeout": self._read_timeout,
            "get_updates_write_timeout": self._write_timeout,
            "get_updates_pool_timeout": self._pool_timeout,
        }
        for method_name, value in timeout_settings.items():
            method = getattr(builder, method_name, None)
            if callable(method):
                builder = method(value)
        return builder

    def _is_timeout_error(self, error: Exception) -> bool:
        if TelegramTimedOut is not None and isinstance(error, TelegramTimedOut):
            return True
        return "timed out" in str(error).lower()

    def _is_transient_polling_error(self, error: Exception) -> bool:
        if self._is_timeout_error(error):
            return True
        msg = str(error).lower()
        transient_markers = (
            "bad gateway",
            "gateway timeout",
            "temporarily unavailable",
            "timed out",
            "timeout",
            "readerror",
            "readtimeout",
            "connecterror",
            "remoteprotocolerror",
            "network is unreachable",
            "connection reset",
        )
        return any(marker in msg for marker in transient_markers)

    def _current_transient_backoff_sec(self) -> float:
        if not self._transient_error_active:
            return 0.0
        return max(0.0, self._transient_backoff_until - time.monotonic())

    def _refresh_transient_state(self) -> None:
        if not self._transient_error_active:
            return
        if time.monotonic() < self._transient_backoff_until:
            return
        self._transient_error_active = False
        if self.is_running and self._state == "running_degraded":
            self._state = "running"

    def _reset_transient_polling_state(self) -> None:
        self._transient_error_active = False
        self._transient_error_count = 0
        self._transient_backoff_until = 0.0
        self._next_transient_log_at = 0.0
        if self.is_running and self._state == "running_degraded":
            self._state = "running"

    def _record_transient_polling_error(self, error: Exception) -> None:
        now = time.monotonic()
        self._transient_error_active = True
        self._transient_error_count += 1
        exp = min(self._transient_error_count - 1, 5)
        backoff_sec = min(60.0, float(2**exp))
        self._transient_backoff_until = now + backoff_sec
        self._last_error = f"Polling transient error: {error}"
        if self.is_running:
            self._state = "running_degraded"
        if now >= self._next_transient_log_at:
            logger.warning(
                "Telegram polling transient error (count={}): {}. Backoff {:.1f}s",
                self._transient_error_count,
                error,
                backoff_sec,
            )
            self._next_transient_log_at = now + backoff_sec

    def _is_parse_entities_error(self, error: Exception) -> bool:
        if isinstance(error, TelegramBadRequest):
            msg = str(error).lower()
            return "can't parse entities" in msg or "can\'t parse entities" in msg
        return False

    @staticmethod
    def _sanitize_markdown(text: str) -> str:
        """Fix unpaired Markdown markers to prevent Telegram parse errors.

        Ensures each inline marker (`, *, _) appears an even number of times
        outside of code blocks.  If a marker count is odd the last occurrence
        is stripped so Telegram can parse the message.
        """
        import re

        # 1. Protect fenced code blocks (``` ... ```) — they are always paired
        _blocks: list[str] = []

        def _save_block(m: re.Match) -> str:
            _blocks.append(m.group(0))
            return f"\x00CB{len(_blocks) - 1}\x00"

        safe = re.sub(r"```[\s\S]*?```", _save_block, text)

        # 2. Protect valid inline code spans (` text `) — paired single backticks.
        # Ces spans sont déjà valides et ne doivent pas être comptés dans la
        # vérification d'équilibre, sinon on retire des backticks légitimes.
        _inlines: list[str] = []

        def _save_inline(m: re.Match) -> str:
            _inlines.append(m.group(0))
            return f"\x00CI{len(_inlines) - 1}\x00"

        safe = re.sub(r"`[^`\n]+`", _save_inline, safe)

        # 3. Fix unbalanced bold pairs (**) then single inline markers
        _bold_count = len(re.findall(r'\*\*', safe))
        if _bold_count % 2 != 0:
            idx = safe.rfind('**')
            safe = safe[:idx] + safe[idx + 2:]

        for ch in ("`", "*", "_"):
            if safe.count(ch) % 2 != 0:
                idx = safe.rfind(ch)
                safe = safe[:idx] + safe[idx + 1 :]

        # 4. Restore inline code spans
        for i, span in enumerate(_inlines):
            safe = safe.replace(f"\x00CI{i}\x00", span)

        # 5. Restore fenced code blocks
        for i, block in enumerate(_blocks):
            safe = safe.replace(f"\x00CB{i}\x00", block)

        return safe

    async def _shutdown_after_conflict(self) -> None:
        self._mark_conflict("Conflict: terminated by other getUpdates request")
        await self._safe_shutdown()
        self._state = "disabled_conflict"
        logger.warning(
            "Telegram channel disabled after Conflict. Keep only one bot instance running."
        )

    async def stop(self) -> None:
        await self._safe_shutdown()
        if self._state != "disabled_conflict":
            self._state = "stopped"
            self._last_error = None
        self._conflict_seen = False
        self._conflict_reported = False
        self._reset_transient_polling_state()
        logger.info("Telegram disconnected")

    def _mark_conflict(self, reason: str) -> None:
        self._reset_transient_polling_state()
        self._conflict_seen = True
        self._state = "disabled_conflict"
        self._last_error = reason
        if not self._conflict_reported:
            self._conflict_reported = True
            logger.warning(
                f"Telegram Conflict detected: {reason}. Channel is stopping to avoid loop spam."
            )

    def _release_lock(self) -> None:
        if self._lock:
            self._lock.release()
        self._lock = None

    async def _safe_shutdown(self, release_lock: bool = True) -> None:
        if self._app:
            updater = getattr(self._app, "updater", None)
            if updater:
                try:
                    await asyncio.wait_for(updater.stop(), timeout=5)
                except (Exception, asyncio.CancelledError):
                    pass  # updater stop best-effort
            try:
                await asyncio.wait_for(self._app.stop(), timeout=3)
            except (Exception, asyncio.CancelledError):
                pass  # app stop best-effort
            try:
                await asyncio.wait_for(self._app.shutdown(), timeout=3)
            except (Exception, asyncio.CancelledError):
                pass  # app shutdown best-effort
        self._app = None
        self.is_running = False
        if release_lock:
            self._release_lock()

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Hello! I am Lumena on Telegram.\n\n"
            "You can ask questions, request help, or just chat.\n"
            "Use /help to see commands."
        )

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Commands:\n\n"
            "/start - Start conversation\n"
            "/help - Show this help\n\n"
            "You can also send any text message.",
        )

    def _should_respond_in_group(self, message, bot_username: str) -> bool:
        """Return True if the bot should respond to this group/supergroup message."""
        text = message.text or message.caption or ""
        if f"@{bot_username}" in text.lower():
            return True
        reply = message.reply_to_message
        if reply and reply.from_user and reply.from_user.is_bot:
            if reply.from_user.username and reply.from_user.username.lower() == bot_username.lower():
                return True
        return False

    @staticmethod
    def _strip_bot_mention(text: str, bot_username: str) -> str:
        """Remove @bot_username from the text (case-insensitive)."""
        import re
        return re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE).strip()

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        bot_username = (context.bot.username or "").lower()

        # In groups, only respond when mentioned or when replying to bot
        if message.chat.type in ("group", "supergroup"):
            if not self._should_respond_in_group(message, bot_username):
                return

        # Strip @mention from text for cleaner processing
        clean_text = self._strip_bot_mention(message.text, bot_username) if bot_username else message.text
        text = clean_text.lower()

        channel_msg = ChannelMessage(
            content=clean_text,
            channel_type=ChannelType.TELEGRAM,
            user_id=str(message.from_user.id),
            username=message.from_user.first_name
            or message.from_user.username
            or "User",
            timestamp=datetime.now(),
            chat_id=str(message.chat_id),
            metadata={
                "message_id": str(message.message_id),
                "chat_type": message.chat.type,
                "language_code": message.from_user.language_code,
            },
        )

        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        screenshot_keywords = ["capture", "screenshot", "ecran", "screen", "photo d'ecran"]
        is_screenshot_request = any(kw in text for kw in screenshot_keywords)

        if is_screenshot_request:
            try:
                import pyautogui
                import tempfile

                screenshot = pyautogui.screenshot()
                temp_path = Path(tempfile.gettempdir()) / "lumena_screenshot.png"
                screenshot.save(str(temp_path))

                await context.bot.send_chat_action(
                    chat_id=message.chat_id,
                    action="upload_photo",
                )

                with open(temp_path, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=message.chat_id,
                        photo=photo,
                        caption="Here is your screenshot.",
                    )

                logger.info(f"Screenshot sent to {message.chat_id}")
                return

            except Exception as e:
                logger.error(f"Screenshot error: {e}")
                await message.reply_text(f"Cannot take screenshot: {e}")
                return

        trace_tokens = {}
        if TELEMETRY_AVAILABLE:
            trace_tokens = push_trace_context(channel="telegram", mode="chat")
            publish_trace(
                stage="input_received",
                status="start",
                channel="telegram",
                mode="chat",
                summary=message.text,
            )

        response = await self._on_message_received(channel_msg)
        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="output_sent",
                status="ok",
                channel="telegram",
                mode="chat",
                summary=response,
            )
            if trace_tokens:
                pop_trace_context(trace_tokens)

        if response:
            for chunk in _split_smart(response):
                await message.reply_text(chunk)

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle photo messages from Telegram.
        Downloads the photo, saves it, analyzes it with Vision LLM,
        then passes the analysis to Lumena's brain for an intelligent response.
        """
        message = update.message
        bot_username = (context.bot.username or "").lower()

        # In groups, only respond when mentioned or when replying to bot
        if message.chat.type in ("group", "supergroup"):
            if not self._should_respond_in_group(message, bot_username):
                return

        chat_id = message.chat_id
        user_id = message.from_user.id
        username = message.from_user.first_name or message.from_user.username or "User"
        caption = message.caption or ""
        
        # Get the largest photo (best quality)
        photo = message.photo[-1]  # Last element is the largest
        file_id = photo.file_id
        
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            # Download the photo
            file = await context.bot.get_file(file_id)
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_user{user_id}.jpg"
            
            # Save to received_images folder
            from ..utils.paths import RECEIVED_IMAGES_DIR
            images_dir = RECEIVED_IMAGES_DIR
            images_dir.mkdir(parents=True, exist_ok=True)
            
            save_path = images_dir / filename
            
            # Download to the save path
            await file.download_to_drive(str(save_path))
            logger.info(f"📷 Photo received from {username} saved to: {save_path}")
            
            # Step 1: Analyze image with Vision Module to get description
            image_description = ""
            if VISION_AVAILABLE:
                vision = get_vision()
                
                # Simple prompt to get image description
                vision_prompt = """Décris cette image de manière factuelle et détaillée:
- Ce que tu vois (objets, personnes, texte, scène)
- Tout texte visible dans l'image (OCR)
- Le contexte général

Sois précis et concis."""
                
                # Try Gemini first (free), then Claude, then Ollama local
                result = await vision.analyze_with_gemini(str(save_path), vision_prompt)

                if not result.get("success"):
                    logger.info("🔄 Gemini Vision failed, trying Claude...")
                    result = await vision.analyze_with_claude(str(save_path), vision_prompt)

                if not result.get("success"):
                    logger.info("🔄 Claude Vision failed, trying Ollama local...")
                    result = await vision.analyze_with_ollama(str(save_path), vision_prompt)

                if result.get("success"):
                    image_description = result.get("answer", "")
                    logger.info(f"📷 Image analyzed successfully")
                else:
                    error = result.get("error", "Erreur inconnue")
                    logger.warning(f"All vision providers failed: {error}, falling back to OCR...")
                    # Fallback OCR avec pytesseract
                    try:
                        from PIL import Image as PILImage
                        img = PILImage.open(str(save_path))
                        ocr_text = vision.analyzer.extract_text(img)
                        if ocr_text and ocr_text.strip():
                            image_description = f"[Texte extrait par OCR]\n{ocr_text.strip()}"
                            logger.info(f"📷 OCR fallback successful ({len(ocr_text)} chars)")
                        else:
                            image_description = "[Image reçue mais aucun texte détecté par OCR. Installe un modèle vision local avec: ollama pull llava]"
                            logger.info("📷 OCR fallback: no text found in image")
                    except Exception as ocr_err:
                        logger.error(f"OCR fallback failed: {ocr_err}")
                        image_description = "[Image reçue mais impossible de l'analyser. Installe un modèle vision local avec: ollama pull llava]"
            else:
                image_description = "[Module Vision non disponible]"
            
            # Step 2: Build message for Lumena's brain
            from .base import build_image_combined_message
            combined_message = build_image_combined_message(caption, image_description, str(save_path))
            
            # Step 3: Create ChannelMessage and pass to Lumena's brain
            channel_msg = ChannelMessage(
                content=combined_message,
                channel_type=ChannelType.TELEGRAM,
                user_id=str(user_id),
                username=username,
                timestamp=datetime.now(),
                chat_id=str(chat_id),
                metadata={
                    "message_id": str(message.message_id),
                    "chat_type": message.chat.type,
                    "language_code": message.from_user.language_code,
                    "has_photo": True,
                    "photo_path": str(save_path),
                    "photo_filename": filename,
                },
            )
            
            # Step 4: Send to Lumena's brain for response
            logger.info(f"📷 Sending to Lumena brain, combined_message length: {len(combined_message)}")
            response = await self._on_message_received(channel_msg)
            logger.info(f"📷 Response received from brain: {type(response)} - {repr(response)[:200] if response else 'None/Empty'}")
            
            if not response:
                response = f"📷 Image reçue et analysée.\n\n{image_description}"
                logger.info("📷 Using fallback response (image description)")
            
            # Send response
            logger.info(f"📷 Sending response to Telegram ({len(response)} chars)")
            for chunk in _split_smart(response):
                await message.reply_text(chunk)
            logger.info("📷 Response sent successfully")
            
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await message.reply_text(f"❌ Erreur lors du traitement de l'image: {e}")

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle generic document uploads from Telegram users."""
        message = update.message
        bot_username = (context.bot.username or "").lower()

        # In groups, only respond when mentioned or when replying to bot
        if message.chat.type in ("group", "supergroup"):
            if not self._should_respond_in_group(message, bot_username):
                return

        chat_id = message.chat_id
        user_id = message.from_user.id
        username = message.from_user.first_name or message.from_user.username or "User"
        caption = message.caption or ""
        document = message.document

        if not document:
            await message.reply_text("❌ Aucun document détecté.")
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            telegram_file = await context.bot.get_file(document.file_id)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            day_key = datetime.now().strftime("%Y-%m-%d")
            original_name = Path(document.file_name or f"document_{document.file_unique_id}").name
            filename = f"{timestamp}_{original_name}"

            from ..utils.paths import WORKSPACE_DIR as _WS
            docs_dir = _WS / day_key / "telegram" / "received_documents"
            docs_dir.mkdir(parents=True, exist_ok=True)
            save_path = docs_dir / filename

            await telegram_file.download_to_drive(str(save_path))
            logger.info(f"📎 Document reçu depuis Telegram: {save_path}")

            mime_type = (document.mime_type or "application/octet-stream").lower()
            file_size = int(document.file_size or 0)
            suffix = save_path.suffix.lower()
            text_like = mime_type.startswith("text/") or suffix in {
                ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".ini", ".toml", ".py", ".js", ".ts", ".html", ".css"
            }

            excerpt = ""
            if text_like:
                try:
                    raw = save_path.read_bytes()
                    max_preview = max(500, min(_env_int("LUMENA_TELEGRAM_DOC_PREVIEW_CHARS", 2500), 12000))
                    decoded = raw.decode("utf-8", errors="replace")
                    normalized = decoded.strip()
                    excerpt = normalized[:max_preview]
                    if len(normalized) > max_preview:
                        excerpt += "\n...[tronqué]"
                except Exception:
                    excerpt = ""  # extraction excerpt échouée

            if excerpt:
                combined_message = (
                    f"[📎 Document reçu sur Telegram]\n"
                    f"- fichier: {original_name}\n"
                    f"- chemin_local: {save_path}\n"
                    f"- mime: {mime_type}\n"
                    f"- taille_octets: {file_size}\n"
                    f"- caption: {caption or '-'}\n\n"
                    f"Contenu extrait:\n{excerpt}\n\n"
                    f"Traite la demande utilisateur à partir de ce document."
                )
            else:
                combined_message = (
                    f"[📎 Document reçu sur Telegram]\n"
                    f"- fichier: {original_name}\n"
                    f"- chemin_local: {save_path}\n"
                    f"- mime: {mime_type}\n"
                    f"- taille_octets: {file_size}\n"
                    f"- caption: {caption or '-'}\n\n"
                    f"Le document est binaire ou non prévisualisable. Utilise le chemin local pour l'analyser/modifier/envoyer."
                )

            channel_msg = ChannelMessage(
                content=combined_message,
                channel_type=ChannelType.TELEGRAM,
                user_id=str(user_id),
                username=username,
                timestamp=datetime.now(),
                chat_id=str(chat_id),
                attachments=[
                    {
                        "filename": original_name,
                        "path": str(save_path),
                        "mime_type": mime_type,
                        "size": file_size,
                    }
                ],
                metadata={
                    "message_id": str(message.message_id),
                    "chat_type": message.chat.type,
                    "language_code": message.from_user.language_code,
                    "has_document": True,
                    "document_path": str(save_path),
                    "document_filename": original_name,
                    "document_mime": mime_type,
                    "document_size": file_size,
                    "document_caption": caption,
                },
            )

            response = await self._on_message_received(channel_msg)
            if not response:
                response = (
                    f"📎 Document reçu: {original_name}\n"
                    f"Chemin local: {save_path}\n"
                    f"Tu peux maintenant me demander de le modifier, puis de le renvoyer."
                )

            for chunk in _split_smart(response):
                await message.reply_text(chunk)

        except Exception as e:
            logger.error(f"Error handling document: {e}")
            await message.reply_text(f"❌ Erreur lors du traitement du document: {e}")

    async def send_message(self, content: str, target_id: str, **kwargs) -> bool:
        if not self._app or not self.is_running:
            return False

        parse_mode = kwargs.get("parse_mode", None)
        if parse_mode:
            content = self._sanitize_markdown(content)
        chunks = _split_smart(content)
        try:
            for chunk in chunks:
                await self._app.bot.send_message(
                    chat_id=int(target_id),
                    text=chunk,
                    parse_mode=parse_mode,
                )
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="output_sent",
                    status="ok",
                    channel="telegram",
                    mode="chat",
                    summary=content,
                )
            return True
        except Exception as e:
            # If markdown/html formatting is invalid, retry as plain text once.
            if parse_mode and self._is_parse_entities_error(e):
                try:
                    await self._app.bot.send_message(
                        chat_id=int(target_id),
                        text=content,
                        parse_mode=None,
                    )
                    logger.warning(
                        "Telegram parse_mode fallback applied for chat_id={}: {}",
                        target_id,
                        e,
                    )
                    if TELEMETRY_AVAILABLE:
                        publish_trace(
                            stage="output_sent",
                            status="ok",
                            channel="telegram",
                            mode="chat",
                            summary=content,
                        )
                    return True
                except Exception as retry_error:
                    logger.error(f"Telegram send fallback error: {retry_error}")
                    if TELEMETRY_AVAILABLE:
                        publish_trace(
                            stage="pipeline_error",
                            status="error",
                            channel="telegram",
                            mode="chat",
                            error=str(retry_error),
                        )
                    return False
            logger.error(f"Telegram send error: {e}")
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="pipeline_error",
                    status="error",
                    channel="telegram",
                    mode="chat",
                    error=str(e),
                )
            return False

    async def send_photo(self, photo_path: str, target_id: str, caption: str = "") -> bool:
        if not self._app or not self.is_running:
            return False

        try:
            photo = Path(photo_path)
            if not photo.exists():
                logger.error(f"Photo not found: {photo_path}")
                return False

            with open(photo, "rb") as f:
                await self._app.bot.send_photo(
                    chat_id=int(target_id),
                    photo=f,
                    caption=caption[:1024] if caption else None,
                )

            logger.info(f"Photo sent to {target_id}")
            return True

        except Exception as e:
            logger.error(f"Telegram photo send error: {e}")
            return False

    async def send_document(self, file_path: str, target_id: str, caption: str = "") -> bool:
        if not self._app or not self.is_running:
            return False

        try:
            doc = Path(file_path)
            if not doc.exists():
                logger.error(f"File not found: {file_path}")
                return False

            with open(doc, "rb") as f:
                await self._app.bot.send_document(
                    chat_id=int(target_id),
                    document=f,
                    caption=caption[:1024] if caption else None,
                )

            logger.info(f"Document sent to {target_id}")
            return True

        except Exception as e:
            logger.error(f"Telegram document send error: {e}")
            return False
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
