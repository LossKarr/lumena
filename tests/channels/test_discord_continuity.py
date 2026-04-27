"""
🧪 Tests — Discord ↔ ChannelContinuityRegistry (V1)

Couvre :
- _resolve_conversation_id produit des IDs stables
- DM → conversation_id explicite dm_{user_id}
- Thread → conversation_id explicite thread_{thread_id}
- Canal+User → session continuity via registry
- Deux messages du même user/canal réutilisent le même conversation_id
- Deux users dans le même canal obtiennent des IDs différents
- conversation_id et conversation_source propagés dans ChannelMessage.metadata
"""

import pytest
from types import SimpleNamespace

from src.channels.discord_channel import DiscordChannel
from src.runtime.channel_envelope import ChannelContinuityRegistry


# ── Helpers : mock Discord message ──────────────────────────────────────────

def _make_channel(channel_id: int = 100, *, parent_id=None, name="general"):
    """Simule un salon Discord."""
    ch = SimpleNamespace(
        id=channel_id,
        name=name,
        topic="",
    )
    if parent_id is not None:
        ch.parent_id = parent_id
    return ch


def _make_dm_channel(channel_id: int = 900):
    """Simule un DM channel (pas de parent_id, pas de guild)."""
    return SimpleNamespace(id=channel_id, name="DM")


def _make_guild(guild_id: int = 1):
    return SimpleNamespace(id=guild_id)


def _make_author(user_id: int = 42, display_name: str = "TestUser"):
    return SimpleNamespace(id=user_id, display_name=display_name)


def _make_message(
    msg_id: int = 1000,
    *,
    channel=None,
    author=None,
    guild=None,
    content: str = "hello",
):
    return SimpleNamespace(
        id=msg_id,
        channel=channel or _make_channel(),
        author=author or _make_author(),
        guild=guild or _make_guild(),
        content=content,
        mentions=[],
        attachments=[],
    )


def _make_discord_channel() -> DiscordChannel:
    """Crée un DiscordChannel sans token (pas de bot réel)."""
    dc = DiscordChannel(token=None)
    return dc


# ── Tests ────────────────────────────────────────────────────────────────────

class TestResolveConversationId:
    def test_has_registry(self):
        dc = _make_discord_channel()
        assert hasattr(dc, '_continuity_registry')
        assert isinstance(dc._continuity_registry, ChannelContinuityRegistry)

    def test_dm_returns_explicit_id(self):
        dc = _make_discord_channel()
        msg = _make_message(channel=_make_dm_channel(), guild=None)
        conv_id, source = dc._resolve_conversation_id(msg, is_dm=True)
        assert conv_id == "dm_42"
        assert source == "explicit"

    def test_thread_returns_explicit_id(self):
        dc = _make_discord_channel()
        thread_ch = _make_channel(channel_id=555, parent_id=100)
        msg = _make_message(channel=thread_ch)
        conv_id, source = dc._resolve_conversation_id(msg)
        assert conv_id == "thread_555"
        assert source == "explicit"

    def test_channel_user_generates_session(self):
        dc = _make_discord_channel()
        msg = _make_message()
        conv_id, source = dc._resolve_conversation_id(msg)
        assert conv_id  # non-vide
        assert source == "generated"  # premier message → nouveau

    def test_same_user_same_channel_stable_id(self):
        dc = _make_discord_channel()
        ch = _make_channel(channel_id=200)
        author = _make_author(user_id=50)
        guild = _make_guild()

        msg1 = _make_message(msg_id=1, channel=ch, author=author, guild=guild)
        msg2 = _make_message(msg_id=2, channel=ch, author=author, guild=guild)

        conv1, src1 = dc._resolve_conversation_id(msg1)
        conv2, src2 = dc._resolve_conversation_id(msg2)

        assert conv1 == conv2
        assert src2 == "client_session"  # deuxième hit → résolu par session

    def test_different_users_different_ids(self):
        dc = _make_discord_channel()
        ch = _make_channel(channel_id=200)
        guild = _make_guild()

        msg_alice = _make_message(
            msg_id=1, channel=ch, author=_make_author(user_id=10, display_name="Alice"),
            guild=guild,
        )
        msg_bob = _make_message(
            msg_id=2, channel=ch, author=_make_author(user_id=20, display_name="Bob"),
            guild=guild,
        )

        conv_alice, _ = dc._resolve_conversation_id(msg_alice)
        conv_bob, _ = dc._resolve_conversation_id(msg_bob)

        assert conv_alice != conv_bob

    def test_different_channels_different_ids(self):
        dc = _make_discord_channel()
        author = _make_author(user_id=50)
        guild = _make_guild()

        msg_ch1 = _make_message(
            msg_id=1, channel=_make_channel(channel_id=300), author=author, guild=guild,
        )
        msg_ch2 = _make_message(
            msg_id=2, channel=_make_channel(channel_id=400), author=author, guild=guild,
        )

        conv1, _ = dc._resolve_conversation_id(msg_ch1)
        conv2, _ = dc._resolve_conversation_id(msg_ch2)

        assert conv1 != conv2

    def test_dm_stable_across_messages(self):
        dc = _make_discord_channel()
        dm_ch = _make_dm_channel(channel_id=900)
        author = _make_author(user_id=77)

        msg1 = _make_message(msg_id=1, channel=dm_ch, author=author, guild=None)
        msg2 = _make_message(msg_id=2, channel=dm_ch, author=author, guild=None)

        conv1, _ = dc._resolve_conversation_id(msg1, is_dm=True)
        conv2, _ = dc._resolve_conversation_id(msg2, is_dm=True)

        assert conv1 == conv2 == "dm_77"

    def test_registry_stats_populated(self):
        dc = _make_discord_channel()
        msg = _make_message()
        dc._resolve_conversation_id(msg)
        dc._resolve_conversation_id(msg)

        stats = dc._continuity_registry.stats()
        assert stats["records_total"] >= 1
        total_hits = sum(stats["source_hits"].values())
        assert total_hits == 2


class TestConversationIdInMetadata:
    """Vérifie que le conversation_id est propagé dans ChannelMessage.metadata.

    On ne peut pas facilement appeler on_message (nécessite un vrai bot),
    donc on vérifie la structure du code en s'assurant que _resolve_conversation_id
    fonctionne correctement et que les clés metadata attendues sont documentées.
    """

    def test_resolve_returns_tuple(self):
        dc = _make_discord_channel()
        msg = _make_message()
        result = dc._resolve_conversation_id(msg)
        assert isinstance(result, tuple)
        assert len(result) == 2
        conv_id, source = result
        assert isinstance(conv_id, str)
        assert source in ("explicit", "generated", "task", "client_session")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
