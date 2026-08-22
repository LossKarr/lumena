"""Voice V2 — LocalAudioPlayer + VoiceRuntime (gated, sans audio réel).

Tout est testé avec un play_fn factice et un TTSProvider factice : aucun son joué.
Prouve le gating LUMENA_VOICE_V2_TTS=0, la discipline generation_id au playback,
l'interruption (stop + troncature), et le statut dégradé sur pyttsx3.
"""
import ast
import asyncio
from pathlib import Path

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent, VoiceCommand,
    LocalAudioPlayer, ConversationAudioLedger, VoiceRuntime, v2_tts_enabled,
    FakeTTSProvider, TTSProvider, AudioResult,
)


# ── LocalAudioPlayer ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_player_plays_current_generation_and_feeds_ledger():
    led = ConversationAudioLedger()
    played = []
    p = LocalAudioPlayer(ledger=led, play_fn=lambda x: _async_append(played, x), stop_fn=lambda: None)
    led.register_generation("u_1", "a_1", "bonjour le monde")
    p.set_generation("a_1")
    r = await p.play(generation_id="a_1", sequence=0, text="bonjour", duration_ms=120)
    assert r == "played" and played == ["bonjour"]
    assert led.get("a_1").played_ms == 120


@pytest.mark.asyncio
async def test_player_drops_stale_generation():
    p = LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None)
    p.set_generation("a_2")
    r = await p.play(generation_id="a_1", text="vieux")
    assert r == "dropped_stale" and p.played == []


@pytest.mark.asyncio
async def test_player_converts_audio_path_string_to_path():
    received = []
    p = LocalAudioPlayer(play_fn=lambda x: _async_append(received, x), stop_fn=lambda: None)
    p.set_generation("a_1")
    r = await p.play(generation_id="a_1", text="bonjour", path="C:/tmp/lumena.wav")
    assert r == "played"
    assert isinstance(received[0], Path)


@pytest.mark.asyncio
async def test_player_cancel_and_stop():
    p = LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None)
    p.set_generation("a_1")
    tok = type("C", (), {"cancelled": True})()
    assert await p.play(generation_id="a_1", text="x", cancel=tok) == "cancelled"
    p.stop()
    assert await p.play(generation_id="a_1", text="x") == "stopped"


def _noop():
    async def _():
        return None
    return _()


def _async_append(lst, x):
    async def _():
        lst.append(x)
    return _()


def test_player_stop_before_play_does_not_resolve_lazy_tts():
    calls = []
    p = LocalAudioPlayer(stop_fn=None)
    p._resolve_stop_fn = lambda: calls.append("resolved")  # type: ignore[method-assign]
    p.stop()
    assert calls == []


# ── VoiceRuntime : gating ─────────────────────────────────────────────────────
def test_v2_tts_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_V2_TTS", raising=False)
    assert v2_tts_enabled() is False


@pytest.mark.asyncio
async def test_runtime_disabled_is_noop():
    tm = TurnManager()
    calls = {"synth": 0}

    class _CountTTS(TTSProvider):
        name = "count"; locality = "local"
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            calls["synth"] += 1
            return AudioResult(ok=True, text=text)

    player = LocalAudioPlayer(play_fn=lambda x: _noop())
    rt = VoiceRuntime(tm, _CountTTS(), player, enabled=False)
    await rt.dispatch([VoiceCommand("start_llm", {"generation_id": "a_1", "turn_id": "u_1", "text": "salut"})])
    assert calls["synth"] == 0 and player.played == []   # NO-OP


# ── VoiceRuntime : tour complet (sans audio réel) ─────────────────────────────
@pytest.mark.asyncio
async def test_runtime_full_tour_synth_and_play():
    """Tour complet en CHUNKING : la réponse est jouée segment par segment, dans l'ordre."""
    tm = TurnManager()
    led = ConversationAudioLedger()
    played = []
    player = LocalAudioPlayer(ledger=led, play_fn=lambda x: _async_append(played, x), stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player, respond_fn=lambda t: "Bonjour. Ca va.", enabled=True)
    tm._dispatcher = rt.dispatch
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    for _ in range(120):
        if len(played) >= 2:
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    # CHUNKING : un play par phrase, dans l'ordre.
    assert played == ["Bonjour", "Ca va"]
    gen = led.order[-1]
    # Le ledger accumule ce qui a été RÉELLEMENT entendu, segment par segment.
    assert "Bonjour" in led.get(gen).text_played and "Ca va" in led.get(gen).text_played
    assert rt.status_report()["state"] in ("speaking", "wake_listening", "interrupted")


# ── VoiceRuntime : chunking — finished émis UNE seule fois, en fin de stream ───
@pytest.mark.asyncio
async def test_runtime_chunking_emits_single_finished_after_all_segments():
    tm = TurnManager()
    led = ConversationAudioLedger()
    played = []
    finished = {"n": 0}
    player = LocalAudioPlayer(ledger=led, play_fn=lambda x: _async_append(played, x), stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player, respond_fn=lambda t: "Un. Deux. Trois.", enabled=True)

    base_emit = tm.emit
    async def _counting_emit(ev):
        if ev.type == "playback.finished":
            finished["n"] += 1
        await base_emit(ev)
    tm.emit = _counting_emit

    tm._dispatcher = rt.dispatch
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    for _ in range(160):
        if finished["n"] >= 1 and len(played) >= 3:
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    assert played == ["Un", "Deux", "Trois"]
    assert finished["n"] == 1          # finished émis UNE fois, pas par chunk


@pytest.mark.asyncio
async def test_runtime_chunking_playback_is_serial_not_overlapping():
    tm = TurnManager()
    active = {"n": 0, "max": 0}
    order = []

    def slow_play(x):
        async def _():
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            order.append(("start", x))
            await asyncio.sleep(0.05)
            order.append(("end", x))
            active["n"] -= 1
        return _()

    player = LocalAudioPlayer(play_fn=slow_play, stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player,
                      respond_fn=lambda t: "Un. Deux. Trois.", enabled=True)
    tm._dispatcher = rt.dispatch
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    for _ in range(160):
        if len(player.played) >= 3:
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    await rt.aclose()

    assert active["max"] == 1
    assert order == [
        ("start", "Un"), ("end", "Un"),
        ("start", "Deux"), ("end", "Deux"),
        ("start", "Trois"), ("end", "Trois"),
    ]


# ── VoiceRuntime : interruption mid-stream → troncature fine ──────────────────
@pytest.mark.asyncio
async def test_runtime_chunking_interruption_truncates_to_heard_segments():
    """Interruption en cours de stream : seuls les segments JOUÉS sont au ledger."""
    tm = TurnManager()
    led = ConversationAudioLedger()
    played = []

    def slow_play(x):
        async def _():
            await asyncio.sleep(0.05)     # chaque segment prend un peu de temps
            played.append(x)
        return _()

    player = LocalAudioPlayer(ledger=led, play_fn=slow_play, stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player,
                      respond_fn=lambda t: "Un. Deux. Trois. Quatre. Cinq.", enabled=True)
    tm._dispatcher = rt.dispatch
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    # On laisse jouer 1 ou 2 segments, puis on interrompt.
    for _ in range(30):
        if len(played) >= 1:
            break
        await asyncio.sleep(0.01)
    heard_at_interrupt = len(played)
    await tm.emit(VoiceEvent("user.stop_word", data={"word": "stop"}))
    for _ in range(30):
        if rt.status == "interrupted":
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)            # laisse retomber d'éventuelles tâches annulées
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    await rt.aclose()

    gen = led.order[-1]
    rec = led.get(gen)
    assert rec.interrupted is True
    assert heard_at_interrupt >= 1                 # au moins un segment entendu
    assert len(played) < 5                         # tous les segments n'ont PAS été joués
    # Le ledger ne contient QUE des segments réellement joués (troncature fine).
    for seg in played:
        assert seg in rec.text_played


# ── LocalTTSAdapter.stream : un chunk par phrase, avec chemin audio ───────────
@pytest.mark.asyncio
async def test_local_tts_adapter_stream_yields_per_sentence(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_CLOUD_ALLOWED", "0")
    from src.voice.v2.providers.local_tts import LocalTTSAdapter

    class _FakeEngine:
        _last_provider = "piper"
        async def _synthesize(self, text, *, local_only=False):
            assert local_only is True          # cloud interdit (local-first)
            return f"C:/tmp/{text[:3]}.wav"

    adapter = LocalTTSAdapter(tts=_FakeEngine())
    chunks = [c async for c in adapter.stream("Bonjour. Comment vas-tu ?", voice=None)]
    assert [c.sequence for c in chunks] == [0, 1]
    assert all(c.audio_path and c.audio_path.endswith(".wav") for c in chunks)
    assert all(c.provider == "piper" and c.degraded is False for c in chunks)


# ── VoiceRuntime : pyttsx3 -> degraded ────────────────────────────────────────
@pytest.mark.asyncio
async def test_runtime_marks_degraded_on_pyttsx3():
    class _Pyttsx3TTS(TTSProvider):
        name = "deg"; locality = "local"
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            return AudioResult(ok=True, text=text, audio_path="x.wav",
                               duration_ms=100, provider="pyttsx3", degraded=True)

    tm = TurnManager()
    player = LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None)
    rt = VoiceRuntime(tm, _Pyttsx3TTS(), player, respond_fn=lambda t: "ok", enabled=True)
    await rt._handle(VoiceCommand("start_llm", {"generation_id": "a_1", "turn_id": "u_1", "text": "x"}))
    for _ in range(20):
        if rt.degraded:
            break
        await asyncio.sleep(0)
    assert rt.degraded is True
    assert rt.status_report()["degraded"] is True and rt.status_report()["provider"] == "pyttsx3"


@pytest.mark.asyncio
async def test_slow_llm_never_blocks_actor_and_cancel_is_real():
    tm = TurnManager()
    gate = asyncio.Event()
    calls = {"cancelled": False}

    async def slow(_text):
        try:
            await gate.wait()
            return "trop tard"
        except asyncio.CancelledError:
            calls["cancelled"] = True
            raise

    rt = VoiceRuntime(
        tm, FakeTTSProvider(),
        LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None),
        respond_fn=slow, enabled=True,
    )
    t0 = asyncio.get_running_loop().time()
    await rt._handle(VoiceCommand("start_llm", {
        "generation_id": "a_slow", "turn_id": "u_1", "text": "attends",
    }))
    assert asyncio.get_running_loop().time() - t0 < 0.05
    await asyncio.sleep(0)  # la coroutine est entree dans respond_fn
    await rt._handle(VoiceCommand("cancel_llm", {"generation_id": "a_slow"}))
    await asyncio.sleep(0)
    assert calls["cancelled"] is True
    assert rt.ledger.order == []
    await rt.aclose()


@pytest.mark.asyncio
async def test_global_mute_prevents_synthesis():
    calls = {"synth": 0}

    class _CountTTS(TTSProvider):
        name = "count"; locality = "local"
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            calls["synth"] += 1
            return AudioResult(ok=True, text=text)

    rt = VoiceRuntime(
        TurnManager(), _CountTTS(), LocalAudioPlayer(play_fn=lambda x: _noop()),
        is_muted_fn=lambda: True, enabled=True,
    )
    assert await rt.speak("Tu ne dois pas entendre ceci") == ""
    assert calls["synth"] == 0


@pytest.mark.asyncio
async def test_replacing_same_generation_cannot_drop_new_task():
    gates = [asyncio.Event(), asyncio.Event()]
    calls = {"n": 0}

    async def respond(_text):
        index = calls["n"]
        calls["n"] += 1
        await gates[index].wait()
        return f"reponse {index}"

    rt = VoiceRuntime(
        TurnManager(), FakeTTSProvider(), LocalAudioPlayer(play_fn=lambda x: _noop()),
        respond_fn=respond, enabled=True,
    )
    command = VoiceCommand("start_llm", {
        "generation_id": "same", "turn_id": "u", "text": "x",
    })
    await rt._handle(command)
    await asyncio.sleep(0)
    await rt._handle(command)
    await asyncio.sleep(0)
    assert "same" in rt._llm_tasks
    gates[1].set()
    for _ in range(30):
        if rt.ledger.order:
            break
        await asyncio.sleep(0)
    assert rt.ledger.order == ["same"]
    assert rt.ledger.get("same").text_unplayed == "reponse 1"
    await rt.aclose()


# ── VoiceRuntime : interruption (stop + truncate + stale drop) ────────────────
@pytest.mark.asyncio
async def test_runtime_interruption_stops_and_truncates():
    tm = TurnManager()
    led = ConversationAudioLedger()
    player = LocalAudioPlayer(ledger=led, play_fn=lambda x: _noop(), stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player, enabled=True)
    led.register_generation("u_1", "a_1", "phrase complete longue")
    player.set_generation("a_1")
    led.on_chunk_played("a_1", "phrase", 100)
    await rt.dispatch([
        VoiceCommand("stop_playback"),
        VoiceCommand("clear_audio_queue"),
        VoiceCommand("truncate_conversation", {"generation_id": "a_1"}),
    ])
    # après clear, la génération courante est le sentinel -> tout chunk a_1 est périmé
    assert player.current_generation_id == "__cleared__"
    assert led.get("a_1").interrupted is True
    # un chunk de l'ancienne génération arrivé après l'interruption est rejeté
    assert await player.play(generation_id="a_1", text="late") == "dropped_stale"
    assert all(p["text"] != "late" for p in player.played)


# ── L'acteur n'est jamais bloqué par un playback long ────────────────────────
@pytest.mark.asyncio
async def test_actor_not_blocked_by_long_playback():
    tm = TurnManager()
    led = ConversationAudioLedger()
    finished_play = []

    def slow_play(x):
        async def _():
            await asyncio.sleep(0.5)          # playback long
            finished_play.append(x)
        return _()

    player = LocalAudioPlayer(ledger=led, play_fn=slow_play, stop_fn=lambda: None)
    rt = VoiceRuntime(tm, FakeTTSProvider(), player, respond_fn=lambda t: "Bonjour", enabled=True)
    tm._dispatcher = rt.dispatch
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    await asyncio.sleep(0.05)                 # laisse le play DÉMARRER en tâche de fond
    assert finished_play == []                # le playback long n'est pas terminé

    # interruption PENDANT le playback long
    t0 = asyncio.get_running_loop().time()
    await tm.emit(VoiceEvent("user.stop_word", data={"word": "stop"}))
    for _ in range(30):
        if rt.status == "interrupted":
            break
        await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - t0

    assert rt.status == "interrupted"         # stop traité…
    assert elapsed < 0.3                       # …bien AVANT la fin du play (0.5s) → acteur non bloqué
    assert finished_play == []                 # le play long a été annulé, jamais terminé

    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    await rt.aclose()


# ── Isolation : local_player n'importe pas la stack audio au niveau module ────
def test_local_player_no_top_level_audio_import():
    import src.voice.v2.providers.local_player as mod
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    top = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top.append(node.module or "")
    assert not any("voice.tts" in (m or "") for m in top), top
