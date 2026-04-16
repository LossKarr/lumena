from pathlib import Path
import sys

import pytest
from telegram.error import BadRequest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.channels.telegram_channel import TelegramChannel


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id: int, text: str, parse_mode=None):
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        if len(self.calls) == 1 and parse_mode:
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return None


class _FailBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id: int, text: str, parse_mode=None):
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        raise RuntimeError("network down")


class _FakeApp:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_send_message_falls_back_to_plain_text_on_parse_error():
    channel = TelegramChannel(token="dummy-token")
    bot = _FakeBot()
    channel._app = _FakeApp(bot)
    channel.is_running = True

    ok = await channel.send_message("Hello *broken", "123", parse_mode="Markdown")

    assert ok is True
    assert len(bot.calls) == 2
    assert bot.calls[0]["parse_mode"] == "Markdown"
    assert bot.calls[1]["parse_mode"] is None


@pytest.mark.asyncio
async def test_send_message_does_not_retry_for_non_parse_errors():
    channel = TelegramChannel(token="dummy-token")
    bot = _FailBot()
    channel._app = _FakeApp(bot)
    channel.is_running = True

    ok = await channel.send_message("Hello", "123", parse_mode="Markdown")

    assert ok is False
    assert len(bot.calls) == 1
    assert bot.calls[0]["parse_mode"] == "Markdown"


# ── Tests _sanitize_markdown (P5 fix) ──


class TestSanitizeMarkdown:
    """Tests unitaires de _sanitize_markdown()."""

    def test_balanced_markers_untouched(self):
        text = "Hello *world* and **bold** text"
        result = TelegramChannel._sanitize_markdown(text)
        assert result == text

    def test_unpaired_star_stripped(self):
        text = "Hello *world"
        result = TelegramChannel._sanitize_markdown(text)
        assert result.count("*") % 2 == 0

    def test_unpaired_underscore_stripped(self):
        text = "Hello _italic text"
        result = TelegramChannel._sanitize_markdown(text)
        assert result.count("_") % 2 == 0

    def test_unpaired_bold_double_star_stripped(self):
        """Un nombre impair de ** doit être corrigé."""
        text = "**bold** and **broken"
        result = TelegramChannel._sanitize_markdown(text)
        # Après correction, nombre de ** doit être pair
        import re
        bold_count = len(re.findall(r'\*\*', result))
        assert bold_count % 2 == 0

    def test_code_blocks_preserved(self):
        text = "```python\ncode *here*\n```\nAnd *broken"
        result = TelegramChannel._sanitize_markdown(text)
        assert "```python" in result
        assert "code *here*" in result

    def test_backtick_balanced(self):
        text = "Use `function` and `broken"
        result = TelegramChannel._sanitize_markdown(text)
        assert result.count("`") % 2 == 0

    def test_empty_string(self):
        assert TelegramChannel._sanitize_markdown("") == ""

    def test_no_markers(self):
        text = "Just plain text here"
        assert TelegramChannel._sanitize_markdown(text) == text
