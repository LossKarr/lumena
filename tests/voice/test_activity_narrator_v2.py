from datetime import timedelta

import pytest

from src.reasoning.public_activity import build_public_activity_event
from src.voice.v2.activity_narrator import PublicActivityNarrator


class _Speech:
    def __init__(self):
        self.calls = []

    async def say(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return f"r{len(self.calls)}"


def _event(text="Je vérifie la configuration.", **kwargs):
    return build_public_activity_event(text, tool_name="read_file", **kwargs)


@pytest.mark.asyncio
async def test_narrator_speaks_model_projection_without_tool_name_or_llm():
    speech = _Speech()
    narrator = PublicActivityNarrator(speech, min_interval_s=0)
    event = _event()
    await narrator.narrate(event)
    assert speech.calls[0][0] == "Je vérifie la configuration."
    assert "read_file" not in speech.calls[0][0]


@pytest.mark.asyncio
async def test_narrator_deduplicates_same_public_phase():
    speech = _Speech()
    narrator = PublicActivityNarrator(speech, min_interval_s=0)
    assert await narrator.narrate(_event())
    assert await narrator.narrate(_event()) == ""
    assert len(speech.calls) == 1
    assert narrator.status()["last_drop_reason"] == "duplicate"


@pytest.mark.asyncio
async def test_narrator_enforces_cadence_and_budget_without_fixed_messages():
    clock = {"now": 10.0}
    speech = _Speech()
    narrator = PublicActivityNarrator(
        speech, min_interval_s=2, max_updates_per_minute=2,
        clock=lambda: clock["now"],
    )
    assert await narrator.narrate(_event("Je lis le premier module."))
    clock["now"] += 1
    assert await narrator.narrate(_event("Je lis le second module.")) == ""
    clock["now"] += 2
    assert await narrator.narrate(_event("Je compare les deux chemins."))
    clock["now"] += 3
    assert await narrator.narrate(_event("Je vérifie une autre piste.")) == ""
    assert narrator.status()["last_drop_reason"] == "budget"


@pytest.mark.asyncio
async def test_expired_activity_is_silent():
    speech = _Speech()
    narrator = PublicActivityNarrator(speech, min_interval_s=0)
    event = _event()
    object.__setattr__(event, "expires_at", event.created_at - timedelta(seconds=1))
    assert await narrator.narrate(event) == ""
    assert speech.calls == []
