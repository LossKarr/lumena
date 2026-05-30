#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Smoke CONVERSATIONNEL complet Voice V2 — HORS pytest, flag-gated, hors prod.

Boucle entière, locale : micro → VAD/STT (Whisper) → fake LLM (echo) → TTS Piper.
Assemble UNIQUEMENT des briques déjà prouvées en pytest :
  TurnManager(barge_in_on_vad=True) + MicConversationSource + EndpointTimerService
  + VoiceRuntime (LocalTTSAdapter synthèse + LocalAudioPlayer playback).

Le LLM est un STUB echo « J'ai bien entendu : {transcript} » : on valide la BOUCLE
complète sans dépendre d'un vrai modèle. Ne touche RIEN en prod (pas d'assistant_loop,
pas de WebRTC). N'ouvre micro/Whisper/Piper QUE si LUMENA_VOICE_V2_STT=1.

⚠️  Limite connue : sans annulation d'écho acoustique, le micro peut capter la voix
de Lumena (faux barge-in). Pour la démo barge-in, parle franchement par-dessus.

Usage :
    # DRY (aucun hardware, frames scriptées + TTS fake) :
    python scripts/voice_v2_conversation_smoke.py

    # Réel (micro + Whisper local + Piper local, jamais le cloud) :
    $env:LUMENA_VOICE_V2_STT="1"; venv\Scripts\python.exe scripts\voice_v2_conversation_smoke.py

Options : --seconds 20  --threshold 300  --hangover-ms 700  --device cpu  --compute int8
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Local-first FORCÉ avant tout import (jamais de cloud dans ce smoke).
os.environ["LUMENA_VOICE_CLOUD_ALLOWED"] = "0"
os.environ.setdefault("LUMENA_TTS_MODE", "offline")   # Piper/XTTS local, pas Edge

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_console_utf8() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_setup_console_utf8()

from src.voice.v2 import (  # noqa: E402
    TurnManager, ConversationAudioLedger,
    RealVADProvider, RealSTTAdapter, LocalTTSAdapter, LocalAudioPlayer,
    FakeVADProvider, FakeTTSProvider,
    MicConversationSource, EndpointTimerService, VoiceRuntime, detect_voice_capabilities,
)


def _stt_enabled() -> bool:
    return os.getenv("LUMENA_VOICE_V2_STT", "0").strip() == "1"


def _echo_llm(transcript: str) -> str:
    """Fake LLM : echo simple pour valider la boucle complète."""
    return f"J'ai bien entendu : {transcript}"


class _ConversationObserver:
    """Dispatcher COMPOSITE : VoiceRuntime (TTS/playback) + EndpointTimerService (timers).

    Affiche le déroulé : transcript, réponse, provider TTS, barge-in.
    """
    def __init__(self, tm: TurnManager, runtime: VoiceRuntime):
        self.tm = tm
        self.runtime = runtime
        self.timer = EndpointTimerService(tm)
        self.turns = 0
        self.interruptions = 0

    async def dispatch(self, commands):
        for cmd in commands:
            if cmd.name == "start_stt":
                print(f"\n  🎙️  nouveau tour ({cmd.data.get('turn_id')})")
            elif cmd.name == "start_llm":
                self.turns += 1
                txt = cmd.data.get("text", "")
                print(f"  📝 transcript : « {txt} »")
                print(f"  🤖 réponse    : « {_echo_llm(txt)} »")
            elif cmd.name == "stop_playback":
                self.interruptions += 1
                print("  ⛔ BARGE-IN : voix coupée, nouveau tour")
        # Les deux services se partagent les commandes (chacun ignore ce qui ne le concerne pas).
        await self.runtime.dispatch(commands)
        await self.timer.dispatch(commands)


def _runtime_pending(observer) -> bool:
    rt = observer.runtime
    tasks = list(getattr(rt, "_play_tasks", [])) + list(getattr(rt, "_producer_tasks", []))
    return any(not t.done() for t in tasks)


async def _settle_after_input(tm, observer, settle_seconds: float) -> None:
    """Laisse le dernier transcript drainé produire sa réponse TTS avant shutdown."""
    deadline = asyncio.get_running_loop().time() + settle_seconds
    while asyncio.get_running_loop().time() < deadline:
        if tm.queue.empty() and not _runtime_pending(observer):
            return
        await asyncio.sleep(0.05)


async def _run_loop(tm, mic, observer, seconds, drain_seconds, settle_seconds):
    run_task = asyncio.create_task(tm.run())
    mic_task = asyncio.create_task(mic.run())
    try:
        await asyncio.sleep(seconds)
    except KeyboardInterrupt:
        pass
    mic.stop()
    if not mic_task.done():
        print(f"\n⏳ Fin d'écoute : drainage jusqu'à {drain_seconds:.0f}s "
              "(laisse Whisper/Piper finir)...")
        try:
            await asyncio.wait_for(asyncio.shield(mic_task), timeout=drain_seconds)
        except asyncio.TimeoutError:
            mic_task.cancel()
        except asyncio.CancelledError:
            pass
    # Toujours attendre la fin EFFECTIVE de la tâche micro (même annulée) avant de
    # fermer la boucle : sinon ses ressources (lecture pyaudio) restent ouvertes.
    if not mic_task.done():
        mic_task.cancel()
    await asyncio.gather(mic_task, return_exceptions=True)

    await _settle_after_input(tm, observer, settle_seconds)

    await tm.shutdown()
    try:
        await asyncio.wait_for(run_task, timeout=3.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    observer.timer.cancel_all()
    await observer.runtime.aclose()
    # Laisse l'event loop finaliser les transports subprocess (Piper) avant sa fermeture
    # → évite « Exception ignored in BaseSubprocessTransport.__del__ ».
    await asyncio.sleep(0.2)


async def run_real(seconds, threshold, hangover_ms, device, compute,
                   speaking_threshold=None, drain_seconds=20.0,
                   settle_seconds=12.0, calibrate=False, calibrate_ms=800,
                   prewarm=False, partial_every_ms=0, model="small",
                   save_utterances=None) -> int:
    caps = detect_voice_capabilities()
    print(f"Capabilities: faster_whisper={caps['faster_whisper_available']} "
          f"pyaudio={caps['pyaudio_available']}")
    if not caps["faster_whisper_available"] or not caps["pyaudio_available"]:
        print("⚠️  faster-whisper ou pyaudio absent : smoke réel impossible.")
        return 2

    tm = TurnManager(barge_in_on_vad=True)
    ledger = ConversationAudioLedger()
    player = LocalAudioPlayer(ledger=ledger)            # playback réel (lazy get_tts._play_audio)
    tts = LocalTTSAdapter()                              # synthèse réelle Piper/XTTS (local-first)
    runtime = VoiceRuntime(tm, tts, player, respond_fn=_echo_llm, enabled=True)
    observer = _ConversationObserver(tm, runtime)
    tm._dispatcher = observer.dispatch

    # Self-voice guard : pendant 'speaking', le seuil VAD est relevé pour que l'écho
    # de Piper réentendu par le micro ne déclenche pas de faux barge-in. Par défaut
    # ~2.7x le seuil normal ; ajustable via --speaking-threshold.
    spk_thr = speaking_threshold if speaking_threshold is not None else int(threshold * 2.7)
    vad = RealVADProvider(energy_threshold=threshold, silence_hangover_ms=hangover_ms,
                          speaking_threshold=spk_thr,
                          is_speaking_fn=lambda: tm.state.mode == "speaking",
                          partial_every_ms=partial_every_ms)
    print(f"VAD: seuil={threshold}, seuil_speaking={spk_thr} (self-voice guard)"
          + (f", partiels toutes les {partial_every_ms} ms" if partial_every_ms else ""))

    # Tap d'affichage des partiels (ils ne génèrent pas de commande, donc invisibles
    # du dispatcher) : on enveloppe tm.emit pour les logguer.
    if partial_every_ms:
        _base_emit = tm.emit
        async def _emit_tap(ev):
            if ev.type == "stt.partial":
                print(f"  · partiel : « {ev.get('text', '')} »")
            await _base_emit(ev)
        tm.emit = _emit_tap

    # Calibration auto (opt-in) : mesure le bruit ambiant AVANT l'écoute et recalcule
    # energy_threshold + speaking_threshold (plancher anti-écho 1200). Le défaut (sans
    # --calibrate) reste les seuils ci-dessus.
    if calibrate:
        print(f"🔧 Calibration : reste SILENCIEUX {calibrate_ms/1000:.1f}s (mesure du bruit ambiant)...")
        res = await vad.calibrate(duration_ms=calibrate_ms)
        if res["fallback"]:
            print("   ⚠️  calibration impossible (mesure vide) → seuils par défaut conservés.")
        else:
            print(f"   noise_floor={res['noise_floor']:.0f} → energy_threshold="
                  f"{res['energy_threshold']}, speaking_threshold={res['speaking_threshold']}")

    from src.voice.stt import LumenaSTT  # noqa: PLC0415 — explicite : bypass defaults cuda
    print(f"STT: model={model}, device={device}, compute={compute} | TTS: Piper/XTTS local (cloud OFF)")
    stt = RealSTTAdapter(stt=LumenaSTT(model_size=model, device=device, compute_type=compute))
    if not stt.is_available():
        print("⚠️  STT indisponible (Whisper non chargeable).")
        return 2
    mic = MicConversationSource(vad, stt, tm, emit_partials=bool(partial_every_ms),
                                save_utterances_dir=save_utterances)

    # Prewarm (opt-in) : charge Whisper + initialise Piper (mini-synthèse SANS playback)
    # AVANT « Parle maintenant » pour supprimer la latence du 1er tour.
    if prewarm:
        print("🔥 Prewarm STT/TTS...")
        s = await stt.prewarm()
        print(f"   STT : {'OK' if s['ok'] else 'ÉCHEC'} en {s['latency_ms']} ms"
              + ("" if s["ok"] else f" ({s.get('detail','')})"))
        t = await tts.prewarm()
        print(f"   TTS : {'OK' if t['ok'] else 'ÉCHEC'} en {t['latency_ms']} ms "
              f"(provider={t.get('provider') or '?'}, dégradé={t.get('degraded')})")

    print(f"\n🎤 Parle maintenant — boucle conversationnelle {seconds:.0f}s. (Ctrl+C pour arrêter)")
    print("   (Parle PAR-DESSUS la réponse pour tester le barge-in.)\n")
    await _run_loop(tm, mic, observer, seconds, drain_seconds, settle_seconds)

    print(f"\n✅ Smoke conversationnel terminé.")
    print(f"   Tours          : {observer.turns}")
    print(f"   Interruptions  : {observer.interruptions}")
    print(f"   Fragments skip : {mic.fragments_skipped}")
    if mic.saved_utterances:
        print(f"   Audio capturé   : {mic.saved_utterances[-1].parent}")
    print(f"   TTS provider   : {runtime.last_provider or '(aucun)'}  "
          f"(dégradé={runtime.degraded})")
    return 0


async def run_dry() -> int:
    """DRY : VAD frames scriptées + STT fake + TTS fake → valide la boucle sans hardware."""
    print("Mode DRY (LUMENA_VOICE_V2_STT != 1) : aucun micro, aucun Whisper, aucun Piper.\n")

    def _rms(frame):
        return 1000.0 if frame == b"L" else 0.0

    frames = [b"q", b"L", b"L", b"L", b"q", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10, silence_hangover_ms=30,
                          min_speech_ms=10, frames=frames, rms_fn=_rms)

    class _FakeWhisper:
        async def transcribe_memory(self, audio_bytes, fast=True):
            return "bonjour lumena"
        async def transcribe_file(self, path):
            return ""

    stt = RealSTTAdapter(stt=_FakeWhisper())

    played = []
    def _fake_play(target):
        async def _():
            played.append(str(target))
        return _()

    tm = TurnManager(barge_in_on_vad=True)
    ledger = ConversationAudioLedger()
    player = LocalAudioPlayer(ledger=ledger, play_fn=_fake_play, stop_fn=lambda: None)
    runtime = VoiceRuntime(tm, FakeTTSProvider(), player, respond_fn=_echo_llm, enabled=True)
    observer = _ConversationObserver(tm, runtime)
    tm._dispatcher = observer.dispatch

    mic = MicConversationSource(vad, stt, tm, min_utterance_ms=0)   # frames symboliques
    run_task = asyncio.create_task(tm.run())
    await mic.run()
    await asyncio.sleep(0.6)   # timer d'endpointing + synthèse/playback fakes
    await tm.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)
    observer.timer.cancel_all()
    await runtime.aclose()

    ok = observer.turns >= 1 and bool(played)
    print(f"\n{'✅' if ok else '❌'} DRY : boucle micro→STT→LLM→TTS câblée. "
          f"tours={observer.turns} segments_joués={played}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke conversationnel Voice V2 (manuel, hors prod).")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--threshold", type=int, default=300)
    ap.add_argument("--hangover-ms", type=int, default=700)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--model", default="small",
                    help="Modèle Whisper faster-whisper (small, medium, large-v3...).")
    ap.add_argument("--drain-seconds", type=float, default=20.0)
    ap.add_argument("--settle-seconds", type=float, default=12.0,
                    help="temps laissé après l'écoute pour jouer la réponse finale.")
    ap.add_argument("--speaking-threshold", type=int, default=None,
                    help="Seuil VAD pendant que Lumena parle (self-voice guard). "
                         "Défaut ~2.7x --threshold.")
    ap.add_argument("--calibrate", action="store_true",
                    help="Calibration auto one-shot du seuil VAD (mesure du bruit ambiant).")
    ap.add_argument("--calibrate-ms", type=int, default=800,
                    help="Durée de mesure du bruit ambiant pour --calibrate.")
    ap.add_argument("--prewarm", action="store_true",
                    help="Précharge Whisper + initialise Piper (mini-synthèse) avant l'écoute.")
    ap.add_argument("--partials", action="store_true",
                    help="Émet des stt.partial pendant la parole (transcription incrémentale).")
    ap.add_argument("--partial-ms", type=int, default=400,
                    help="Intervalle entre partiels (avec --partials).")
    ap.add_argument("--save-utterances", default=None,
                    help="Dossier où sauvegarder les énoncés micro envoyés à Whisper (.wav).")
    args = ap.parse_args()

    if _stt_enabled():
        return asyncio.run(run_real(
            args.seconds, args.threshold, args.hangover_ms, args.device, args.compute,
            speaking_threshold=args.speaking_threshold,
            drain_seconds=args.drain_seconds,
            settle_seconds=args.settle_seconds,
            calibrate=args.calibrate, calibrate_ms=args.calibrate_ms,
            prewarm=args.prewarm,
            partial_every_ms=(args.partial_ms if args.partials else 0),
            model=args.model,
            save_utterances=args.save_utterances))
    return asyncio.run(run_dry())


if __name__ == "__main__":
    raise SystemExit(main())
