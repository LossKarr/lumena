"""Voice V2 — providers RÉELS faster-whisper + VAD micro (logique testée SANS hardware).

On ne lance NI micro NI Whisper : moteur STT fake injecté, frames VAD scriptées +
rms injecté. On prouve : routage transcribe (bytes/chemin), stream un-final, machine
à états VAD énergétique (start/end + capture), orchestrateur MicConversationSource,
imports paresseux (aucune lib hardware au niveau module ni à l'import de src.voice.v2).
"""
import ast
import importlib.util
import sys
import wave
from pathlib import Path

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent,
    RealSTTAdapter, RealVADProvider, MicConversationSource,
    STTProvider, VADProvider,
)


# ── RealSTTAdapter : routage + stream ──────────────────────────────────────────
class _FakeEngine:
    def __init__(self):
        self.calls = []
    async def transcribe_memory(self, audio_bytes, fast=True):
        self.calls.append(("memory", len(audio_bytes), fast))
        return "depuis la memoire"
    async def transcribe_file(self, path):
        self.calls.append(("file", path))
        return "depuis le fichier"


@pytest.mark.asyncio
async def test_real_stt_routes_bytes_to_memory_and_path_to_file():
    eng = _FakeEngine()
    stt = RealSTTAdapter(stt=eng)
    assert await stt.transcribe(b"\x00\x01\x02\x03") == "depuis la memoire"
    assert await stt.transcribe("C:/tmp/x.wav") == "depuis le fichier"
    assert ("memory", 4, True) in eng.calls and ("file", "C:/tmp/x.wav") in eng.calls


@pytest.mark.asyncio
async def test_real_stt_stream_yields_single_final():
    stt = RealSTTAdapter(stt=_FakeEngine())
    out = [(r.text, r.is_final) async for r in stt.stream(b"\x00\x01")]
    assert out == [("depuis la memoire", True)]


@pytest.mark.asyncio
async def test_real_stt_stream_empty_yields_nothing():
    class _Empty(_FakeEngine):
        async def transcribe_memory(self, audio_bytes, fast=True): return ""
    out = [r async for r in RealSTTAdapter(stt=_Empty()).stream(b"\x00")]
    assert out == []


def test_real_stt_unavailable_when_faster_whisper_absent(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert RealSTTAdapter(stt=_FakeEngine()).is_available() is False


# ── RealVADProvider : machine à états énergétique ──────────────────────────────
def _rms(frame: bytes) -> float:
    return 1000.0 if frame == b"L" else 0.0   # 'L'=loud, 'q'=quiet


@pytest.mark.asyncio
async def test_real_vad_emits_start_then_end_and_captures_utterance():
    frames = [b"q", b"L", b"L", b"L", b"q", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=30, min_speech_ms=10,
                          frames=frames, rms_fn=_rms)
    assert vad.is_available() is True
    evs = [(e.kind, e.t) async for e in vad.stream()]
    assert evs == [("speech_started", 10), ("speech_ended", 60)]
    # L'énoncé capturé couvre du 1er frame voisé jusqu'au hangover inclus.
    assert vad.last_utterance == b"LLLqqq"


@pytest.mark.asyncio
async def test_real_vad_min_speech_gates_spurious_blip():
    # min_speech=20ms => 2 frames voisés requis ; un seul 'L' isolé ne déclenche pas.
    frames = [b"q", b"L", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=20, min_speech_ms=20,
                          frames=frames, rms_fn=_rms)
    evs = [e async for e in vad.stream()]
    assert evs == []   # aucun start (blip trop court)


# ── Calibration auto VAD (one-shot, opt-in) ───────────────────────────────────
from src.voice.v2 import measure_noise_floor, calibrate_thresholds


def test_measure_noise_floor_is_median_and_handles_empty():
    assert measure_noise_floor([10, 12, 11, 200]) == 11.5   # médiane robuste au pic 200
    assert measure_noise_floor([]) is None


def test_calibrate_thresholds_low_noise_hits_min_and_speaking_floor():
    energy, speaking = calibrate_thresholds(20)              # 20*2.5=50 < min 180
    assert energy == 180
    assert speaking == 1200                                  # max(1200, 180*4=720)


def test_calibrate_thresholds_medium_noise():
    energy, speaking = calibrate_thresholds(200)             # 200*2.5=500
    assert energy == 500
    assert speaking == 2000                                  # max(1200, 500*4)


def test_calibrate_thresholds_loud_noise_hits_max():
    energy, speaking = calibrate_thresholds(400)             # 400*2.5=1000 > max 800
    assert energy == 800
    assert speaking == 3200                                  # max(1200, 800*4)


@pytest.mark.asyncio
async def test_vad_calibrate_applies_thresholds_from_frames():
    # bruit ambiant ~200 → energy=500, speaking=2000.
    frames = [b"n"] * 10
    vad = RealVADProvider(frames=[], rms_fn=lambda f: 200.0)
    res = await vad.calibrate(frames=frames)
    assert res["noise_floor"] == 200.0 and res["fallback"] is False
    assert res["energy_threshold"] == 500 and res["speaking_threshold"] == 2000
    assert vad.energy_threshold == 500 and vad.speaking_threshold == 2000


@pytest.mark.asyncio
async def test_vad_calibrate_empty_falls_back_to_current():
    vad = RealVADProvider(energy_threshold=300, frames=[], rms_fn=lambda f: 0.0)
    res = await vad.calibrate(frames=[])                     # aucun échantillon
    assert res["fallback"] is True and res["noise_floor"] is None
    assert vad.energy_threshold == 300                       # inchangé


@pytest.mark.asyncio
async def test_vad_calibrate_rms_error_falls_back():
    def _boom(frame):
        raise RuntimeError("rms cassé")
    vad = RealVADProvider(energy_threshold=300, frames=[], rms_fn=_boom)
    res = await vad.calibrate(frames=[b"x", b"y"])
    assert res["fallback"] is True
    assert vad.energy_threshold == 300                       # inchangé


# ── Partiels Whisper streamés : VAD speech_partial + source stt.partial ────────
@pytest.mark.asyncio
async def test_real_vad_emits_periodic_speech_partials():
    frames = [b"L", b"L", b"L", b"L", b"L", b"L", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10, silence_hangover_ms=20,
                          min_speech_ms=10, partial_every_ms=30,
                          frames=frames, rms_fn=_rms)   # _rms: 'L'=1000,'q'=0 (défini plus bas)
    kinds = [e.kind async for e in vad.stream()]
    assert kinds == ["speech_started", "speech_partial", "speech_partial", "speech_ended"]
    assert vad.partial_utterance != b""                 # snapshot capturé


@pytest.mark.asyncio
async def test_real_vad_no_partials_when_disabled():
    frames = [b"L", b"L", b"L", b"L", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10, silence_hangover_ms=20,
                          min_speech_ms=10, frames=frames, rms_fn=_rms)   # partial_every_ms=0
    kinds = [e.kind async for e in vad.stream()]
    assert "speech_partial" not in kinds                # défaut inchangé


class _PartialVAD:
    """VAD factice : start → partial (snapshot) → end (énoncé complet)."""
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2

    def __init__(self):
        self.partial_utterance = b""
        self.last_utterance = b"\x00" * 12000   # 375 ms (> min_utterance)

    async def stream(self, audio=None):
        from src.voice.v2 import VADEvent
        yield VADEvent(kind="speech_started", t=0)
        self.partial_utterance = b"\x00" * 4000
        yield VADEvent(kind="speech_partial", t=30)
        yield VADEvent(kind="speech_ended", t=60)


@pytest.mark.asyncio
async def test_mic_source_emits_partial_then_final_when_enabled():
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(_PartialVAD(), stt, tm, min_utterance_ms=300, emit_partials=True)
    await src.run()
    types = [e.type for e in _drain(tm)]
    assert "stt.partial" in types and "stt.final" in types
    assert stt.calls == 2                                # partiel + final
    assert stt.fast_flags == [True, False]               # partiel rapide, final précis
    assert tm.state.partial_transcript == "contenu transcrit"
    assert tm.state.final_transcript == "contenu transcrit"


@pytest.mark.asyncio
async def test_mic_source_ignores_partials_by_default():
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(_PartialVAD(), stt, tm, min_utterance_ms=300)  # emit_partials=False
    await src.run()
    types = [e.type for e in _drain(tm)]
    assert "stt.partial" not in types and "stt.final" in types
    assert stt.calls == 1                                # final seulement
    assert stt.fast_flags == [False]


# ── Prewarm STT/TTS (mocks ; aucun hardware, aucun playback) ───────────────────
@pytest.mark.asyncio
async def test_stt_prewarm_loads_model_and_reports():
    class _Loadable:
        def __init__(self): self.loaded = 0
        def load_model(self):
            self.loaded += 1
            return True
        async def transcribe_memory(self, b, fast=True): return ""
    eng = _Loadable()
    res = await RealSTTAdapter(stt=eng).prewarm()
    assert res["component"] == "stt" and res["ok"] is True
    assert eng.loaded == 1 and res["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_stt_prewarm_fallback_transcribes_when_no_load_model():
    class _NoLoader:
        def __init__(self): self.calls = 0
        async def transcribe_memory(self, b, fast=True):
            self.calls += 1
            return ""
    eng = _NoLoader()
    res = await RealSTTAdapter(stt=eng).prewarm()
    assert res["ok"] is True and eng.calls == 1     # silence court → force le chargement


@pytest.mark.asyncio
async def test_stt_prewarm_failure_is_non_blocking():
    class _Broken:
        def load_model(self): raise RuntimeError("cuda manquant")
        async def transcribe_memory(self, b, fast=True): return ""
    res = await RealSTTAdapter(stt=_Broken()).prewarm()
    assert res["ok"] is False and "cuda" in res["detail"]


@pytest.mark.asyncio
async def test_tts_prewarm_synthesizes_without_playback(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_CLOUD_ALLOWED", "0")
    from src.voice.v2 import LocalTTSAdapter

    class _FakeTTSEngine:
        _last_provider = "piper"
        def __init__(self): self.synth = 0; self.played = 0
        async def _synthesize(self, text, *, local_only=False):
            assert local_only is True
            self.synth += 1
            return "C:/tmp/prewarm.wav"
        def _play_audio(self, *a, **k):     # ne doit JAMAIS être appelé par le prewarm
            self.played += 1
    eng = _FakeTTSEngine()
    res = await LocalTTSAdapter(tts=eng).prewarm()
    assert res["component"] == "tts" and res["ok"] is True
    assert res["provider"] == "piper" and res["degraded"] is False
    assert eng.synth == 1 and eng.played == 0       # synthèse oui, playback NON


@pytest.mark.asyncio
async def test_tts_prewarm_marks_degraded_on_pyttsx3(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_CLOUD_ALLOWED", "0")
    from src.voice.v2 import LocalTTSAdapter

    class _Pyttsx3Engine:
        _last_provider = "pyttsx3"
        async def _synthesize(self, text, *, local_only=False):
            return "C:/tmp/x.wav"
    res = await LocalTTSAdapter(tts=_Pyttsx3Engine()).prewarm()
    assert res["ok"] is True and res["degraded"] is True and res["provider"] == "pyttsx3"


# ── Self-voice guard : seuil relevé pendant que Lumena parle ───────────────────
def _rms_levels(frame: bytes) -> float:
    return {b"q": 0.0, b"e": 400.0, b"V": 1200.0}[frame]   # q=silence, e=écho Piper, V=voix forte


@pytest.mark.asyncio
async def test_self_voice_guard_suppresses_echo_during_speaking():
    # Écho ~400 : passe le seuil normal (300) mais PAS le seuil 'speaking' (800).
    frames = [b"e", b"e", b"e", b"e"]
    common = dict(energy_threshold=300, speaking_threshold=800, frame_ms=10,
                  silence_hangover_ms=20, min_speech_ms=10, rms_fn=_rms_levels)

    speaking = RealVADProvider(frames=list(frames), is_speaking_fn=lambda: True, **common)
    assert [e async for e in speaking.stream()] == []          # écho ignoré pendant speaking

    idle = RealVADProvider(frames=list(frames), is_speaking_fn=lambda: False, **common)
    kinds = [e.kind async for e in idle.stream()]
    assert "speech_started" in kinds                            # même écho déclenche hors speaking


@pytest.mark.asyncio
async def test_self_voice_guard_allows_loud_barge_in_during_speaking():
    # Voix forte ~1200 : dépasse le seuil 'speaking' (800) → barge-in autorisé.
    frames = [b"V", b"V", b"V", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, speaking_threshold=800, frame_ms=10,
                          silence_hangover_ms=20, min_speech_ms=10,
                          is_speaking_fn=lambda: True, frames=frames, rms_fn=_rms_levels)
    kinds = [e.kind async for e in vad.stream()]
    assert kinds == ["speech_started", "speech_ended"]


@pytest.mark.asyncio
async def test_real_vad_closes_open_utterance_at_stream_end():
    frames = [b"L", b"L", b"L"]   # parole jamais suivie de silence → clôture en fin de flux
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=30, min_speech_ms=10,
                          frames=frames, rms_fn=_rms)
    evs = [e.kind async for e in vad.stream()]
    assert evs == ["speech_started", "speech_ended"]
    assert vad.last_utterance == b"LLL"


@pytest.mark.asyncio
async def test_real_vad_stop_closes_open_utterance():
    frames = [b"L"] * 100
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=30, min_speech_ms=10,
                          frames=frames, rms_fn=_rms)
    events = []
    async for ev in vad.stream():
        events.append(ev.kind)
        if ev.kind == "speech_started":
            vad.stop()
    assert events == ["speech_started", "speech_ended"]
    assert vad.last_utterance


# ── MicConversationSource : VAD (timing) + STT (contenu) → événements TM ───────
@pytest.mark.asyncio
async def test_mic_source_emits_vad_boundaries_and_stt_final():
    frames = [b"q", b"L", b"L", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=20, min_speech_ms=10,
                          frames=frames, rms_fn=_rms)
    stt = RealSTTAdapter(stt=_FakeEngine())
    tm = TurnManager()
    # Frames symboliques (1 octet) → on désactive le filtre durée pour tester le câblage.
    src = MicConversationSource(vad, stt, tm, min_utterance_ms=0)
    await src.run()
    types = [e.type for e in _drain(tm)]
    assert "vad.speech_started" in types
    assert "vad.speech_ended" in types
    assert "stt.final" in types
    # Le tour a bien été ouvert puis le contenu posé.
    assert tm.state.final_transcript == "depuis la memoire"


def _drain(tm: TurnManager):
    out = []
    while not tm.queue.empty():
        ev = tm.queue.get_nowait()
        tm.reduce(ev)
        out.append(ev)
    return out


# ── Filtre anti-fragments : énoncé trop court → pas de transcription ───────────
class _ScriptedVAD:
    """VAD factice : émet start+end et expose `last_utterance` de taille contrôlée."""
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2

    def __init__(self, utterance: bytes):
        self.last_utterance = utterance

    async def stream(self, audio=None):
        from src.voice.v2 import VADEvent
        yield VADEvent(kind="speech_started", t=0)
        yield VADEvent(kind="speech_ended", t=500)


class _CountingSTT:
    def __init__(self):
        self.calls = 0
        self.fast_flags = []
    def is_available(self): return True
    async def transcribe(self, audio, *, language="fr", fast=None):
        self.calls += 1
        self.fast_flags.append(fast)
        return "contenu transcrit"


@pytest.mark.asyncio
async def test_mic_source_skips_short_fragment():
    short = b"\x00" * 1600          # 50 ms @ 16 kHz/16-bit
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(_ScriptedVAD(short), stt, tm, min_utterance_ms=300)
    await src.run()
    types = [e.type for e in _drain(tm)]
    assert "vad.speech_started" in types and "vad.speech_ended" in types   # timing honnête
    assert "stt.final" not in types                                        # contenu filtré
    assert stt.calls == 0                                                   # Whisper jamais appelé
    assert src.fragments_skipped == 1


@pytest.mark.asyncio
async def test_mic_source_transcribes_long_enough_utterance():
    long = b"\x00" * 12000          # 375 ms ≥ seuil
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(_ScriptedVAD(long), stt, tm, min_utterance_ms=300)
    await src.run()
    types = [e.type for e in _drain(tm)]
    assert "stt.final" in types
    assert stt.calls == 1
    assert src.fragments_skipped == 0
    assert tm.state.final_transcript == "contenu transcrit"


@pytest.mark.asyncio
async def test_mic_source_discards_browser_dictation_without_duplicate_turn():
    long = b"\x00" * 12000
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(
        _ScriptedVAD(long), stt, tm, min_utterance_ms=300,
        suppress_input_fn=lambda: True,
    )
    await src.run()
    assert _drain(tm) == []
    assert stt.calls == 0


@pytest.mark.asyncio
async def test_mic_source_can_save_captured_utterance(tmp_path):
    long = b"\x01\x02" * 6000
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(_ScriptedVAD(long), stt, tm,
                                min_utterance_ms=300,
                                save_utterances_dir=tmp_path)
    await src.run()
    assert len(src.saved_utterances) == 1
    with wave.open(str(src.saved_utterances[0]), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert wf.readframes(wf.getnframes()) == long


@pytest.mark.asyncio
async def test_mic_source_stop_flushes_open_utterance():
    frames = [b"L"] * 100
    vad = RealVADProvider(energy_threshold=300, frame_ms=10,
                          silence_hangover_ms=30, min_speech_ms=10,
                          frames=frames, rms_fn=_rms)
    stt = _CountingSTT()
    tm = TurnManager()
    src = MicConversationSource(vad, stt, tm, min_utterance_ms=0)

    async def _stop_after_start():
        while tm.queue.empty():
            await asyncio.sleep(0)
        src.stop()

    import asyncio
    stopper = asyncio.create_task(_stop_after_start())
    await src.run()
    await stopper
    types = [e.type for e in _drain(tm)]
    assert "vad.speech_ended" in types
    assert "stt.final" in types
    assert stt.calls == 1


# ── Imports paresseux : aucune lib hardware au niveau module ───────────────────
@pytest.mark.parametrize("modname", [
    "src.voice.v2.providers.real_stt", "src.voice.v2.providers.real_vad",
])
def test_real_providers_no_top_level_hardware_import(modname):
    import importlib
    mod = importlib.import_module(modname)
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    top = []
    for node in tree.body:                     # niveau module uniquement
        if isinstance(node, ast.Import):
            top += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top.append(node.module or "")
    forbidden = {"pyaudio", "audioop", "faster_whisper"}
    assert not any((m or "").split(".")[0] in forbidden or "voice.stt" in (m or "")
                   for m in top), top


def test_importing_v2_does_not_pull_hardware():
    # Importer src.voice.v2 ne doit charger NI pyaudio NI faster_whisper.
    for heavy in ("pyaudio", "faster_whisper"):
        sys.modules.pop(heavy, None)
    import src.voice.v2  # noqa: F401
    assert "pyaudio" not in sys.modules
    assert "faster_whisper" not in sys.modules
