import pytest

from src.reasoning.public_activity import build_public_activity_event
from src.voice.v2 import VoiceDialoguePolicy, plan_speech, wants_full_voice_detail
from src.voice.v2.activity_narrator import PublicActivityNarrator


@pytest.mark.parametrize("language, fragment", [
    ("fr", "D'accord"), ("en", "Okay"), ("es", "De acuerdo"),
])
def test_acknowledgements_follow_session_language_and_make_no_success_claim(language, fragment):
    value = VoiceDialoguePolicy().message("working", language)
    assert fragment in value
    assert not any(word in value.lower() for word in ("terminé", "done", "completado"))


def test_private_reasoning_blocks_are_never_spoken():
    source = (
        "<thinking>Je dois inspecter le token secret.</thinking> "
        "Voici la réponse publique.\nREASONING: détail interne\nLe résultat reste à vérifier."
    )
    plan = plan_speech(source, canonical_verified=False)
    assert "token secret" not in plan.spoken
    assert "détail interne" not in plan.spoken
    assert "réponse publique" in plan.spoken
    assert "internal_reasoning" in plan.suppressed


@pytest.mark.parametrize("text", [
    "Donne-moi tous les détails", "give me all the details",
    "dame todos los detalles",
])
def test_full_detail_is_only_enabled_by_explicit_requests(text):
    assert wants_full_voice_detail(text) is True
    assert wants_full_voice_detail("les détails sont dans le fichier") is False


class _Speech:
    def __init__(self):
        self.values = []

    async def say(self, text, **kwargs):
        self.values.append((text, kwargs))
        return f"speech-{len(self.values)}"


@pytest.mark.asyncio
async def test_public_narration_is_deduplicated_and_never_reads_tool_arguments():
    speech = _Speech()
    now = [100.0]
    narrator = PublicActivityNarrator(speech, min_interval_s=0, clock=lambda: now[0])
    event = build_public_activity_event(
        "Je vérifie le résultat.", tool_name="verify_output", phase="verify",
        task_id="task-1", turn_id="turn-1",
    )
    assert event is not None
    first = await narrator.narrate(event)
    second = await narrator.narrate(event)
    assert first and second == ""
    assert speech.values[0][0] == "Je vérifie le résultat."
    assert narrator.last_drop_reason == "duplicate"

