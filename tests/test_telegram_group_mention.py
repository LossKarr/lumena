"""Tests for Telegram group mention filtering.

Lumena should only respond in groups when:
- @bot_username is in the message text/caption
- The message is a reply to one of the bot's own messages

In private chats, all messages are processed normally.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.channels.telegram_channel import TelegramChannel


# ── Helpers ────────────────────────────────────────────

def _make_channel() -> TelegramChannel:
    ch = TelegramChannel(token="dummy-token")
    ch._on_message_received = AsyncMock(return_value="ok reply")
    return ch


def _make_context(bot_username: str = "lumena_bot"):
    ctx = MagicMock()
    ctx.bot.username = bot_username
    ctx.bot.send_chat_action = AsyncMock()
    return ctx


def _make_message(
    text: str = "hello",
    chat_type: str = "private",
    reply_to_bot: bool = False,
    bot_username: str = "lumena_bot",
):
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.chat.type = chat_type
    msg.chat_id = 123
    msg.message_id = 1
    msg.from_user.id = 42
    msg.from_user.first_name = "Tester"
    msg.from_user.username = "tester"
    msg.from_user.language_code = "fr"
    msg.from_user.is_bot = False
    msg.reply_text = AsyncMock()

    if reply_to_bot:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.from_user.is_bot = True
        msg.reply_to_message.from_user.username = bot_username
    else:
        msg.reply_to_message = None

    return msg


def _make_update(message):
    update = MagicMock()
    update.message = message
    return update


# ── _should_respond_in_group ──────────────────────────

class TestShouldRespondInGroup:
    def setup_method(self):
        self.ch = _make_channel()

    def test_mention_in_text(self):
        msg = _make_message(text="@lumena_bot do something", chat_type="group")
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is True

    def test_mention_case_insensitive(self):
        msg = _make_message(text="@Lumena_Bot help", chat_type="group")
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is True

    def test_no_mention_no_reply(self):
        msg = _make_message(text="hello everyone", chat_type="group")
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is False

    def test_reply_to_bot(self):
        msg = _make_message(text="yes", chat_type="group", reply_to_bot=True)
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is True

    def test_reply_to_other_bot(self):
        msg = _make_message(text="yes", chat_type="group", reply_to_bot=True, bot_username="other_bot")
        # reply_to_message has other_bot, but we check for lumena_bot
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is False

    def test_mention_in_caption(self):
        """Photo/doc with caption containing @mention."""
        msg = _make_message(text="", chat_type="group")
        msg.text = None
        msg.caption = "Look at this @lumena_bot"
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is True

    def test_no_text_no_caption(self):
        msg = _make_message(text="", chat_type="group")
        msg.text = None
        msg.caption = None
        assert self.ch._should_respond_in_group(msg, "lumena_bot") is False


# ── _strip_bot_mention ────────────────────────────────

class TestStripBotMention:
    def test_removes_mention(self):
        result = TelegramChannel._strip_bot_mention("@lumena_bot hello", "lumena_bot")
        assert result == "hello"

    def test_case_insensitive(self):
        result = TelegramChannel._strip_bot_mention("@LUMENA_BOT hello", "lumena_bot")
        assert result == "hello"

    def test_no_mention(self):
        result = TelegramChannel._strip_bot_mention("hello world", "lumena_bot")
        assert result == "hello world"

    def test_mid_text(self):
        result = TelegramChannel._strip_bot_mention("hey @lumena_bot help me", "lumena_bot")
        assert result == "hey  help me"

    def test_multiple_mentions(self):
        result = TelegramChannel._strip_bot_mention("@lumena_bot @lumena_bot hi", "lumena_bot")
        assert result == "hi"


# ── _handle_message integration ───────────────────────

class TestHandleMessageGroupFilter:
    def setup_method(self):
        self.ch = _make_channel()

    @pytest.mark.asyncio
    async def test_private_chat_always_processed(self):
        msg = _make_message(text="hello", chat_type="private")
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_without_mention_ignored(self):
        msg = _make_message(text="hello everyone", chat_type="group")
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_with_mention_processed(self):
        msg = _make_message(text="@lumena_bot what time is it?", chat_type="group")
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_awaited_once()
        # The content passed should have @mention stripped
        call_args = self.ch._on_message_received.call_args[0][0]
        assert "@lumena_bot" not in call_args.content

    @pytest.mark.asyncio
    async def test_supergroup_with_mention_processed(self):
        msg = _make_message(text="@lumena_bot hello", chat_type="supergroup")
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_supergroup_without_mention_ignored(self):
        msg = _make_message(text="random chat", chat_type="supergroup")
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_reply_to_bot_processed(self):
        msg = _make_message(text="yes please", chat_type="group", reply_to_bot=True)
        update = _make_update(msg)
        ctx = _make_context()
        await self.ch._handle_message(update, ctx)
        self.ch._on_message_received.assert_awaited_once()
