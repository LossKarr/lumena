#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Smoke test MANUEL STT/VAD Voice V2 — HORS pytest, flag-gated, hors prod.

But : premier contact MICRO + WHISPER RÉELS, en réutilisant la logique déjà prouvée
en pytest (TurnManager + endpointing silence + MicConversationSource). Ne touche RIEN
en prod (pas d'assistant_loop, pas de WebRTC) et n'ouvre le micro / ne charge Whisper
QUE si le flag est armé.

Usage :
    # Mode DRY (aucun micro, frames scriptées, vérifie le câblage) :
    python scripts/voice_v2_stt_smoke.py

    # Micro + Whisper réels (faster-whisper local, VAD énergétique) :
    $env:LUMENA_VOICE_V2_STT="1"; venv\Scripts\python.exe scripts\voice_v2_stt_smoke.py

    # Si mojibake (console legacy), forcer UTF-8 :
    $env:PYTHONIOENCODING="utf-8"; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; \
        $env:LUMENA_VOICE_V2_STT="1"; venv\Scripts\python.exe scripts\voice_v2_stt_smoke.py

Options : --seconds 12  --threshold 300  --hangover-ms 700  --device cpu|cuda  --compute int8|float32|float16
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

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
    TurnManager,
    RealVADProvider, RealSTTAdapter,
    MicConversationSource, EndpointTimerService, detect_voice_capabilities,
)


def _stt_enabled() -> bool:
    return os.getenv("LUMENA_VOICE_V2_STT", "0").strip() == "1"


class _ObservingDispatcher:
    """Affiche chaque commande émise ET pilote le timer d'endpointing."""
    def __init__(self, tm: TurnManager):
        self.timer = EndpointTimerService(tm)
        self.transcripts: list[str] = []

    async def dispatch(self, commands):
        for cmd in commands:
            if cmd.name == "start_stt":
                print(f"  → start_stt (tour {cmd.data.get('turn_id')})")
            elif cmd.name == "arm_endpoint_timer":
                print(f"  → arm_endpoint_timer ({cmd.data.get('wait_ms')} ms)")
            elif cmd.name == "cancel_endpoint_timer":
                print("  → cancel_endpoint_timer (parole reprise)")
            elif cmd.name == "start_llm":
                txt = cmd.data.get("text", "")
                self.transcripts.append(txt)
                print(f"  ★ FIN DE TOUR → start_llm : « {txt} »")
            elif cmd.name in ("stop_playback", "clear_audio_queue"):
                print(f"  ⛔ {cmd.name} (barge-in)")
        await self.timer.dispatch(commands)


class _DebugSTT:
    """Wrapper de smoke : affiche la taille audio capturée et le transcript."""
    def __init__(self, inner):
        self.inner = inner

    def is_available(self):
        return self.inner.is_available()

    async def transcribe(self, audio, *, language: str = "fr") -> str:
        if isinstance(audio, (bytes, bytearray)):
            seconds = len(audio) / 2 / 16000
            print(f"  · STT input: {len(audio)} bytes ≈ {seconds:.2f}s audio")
        else:
            print(f"  · STT input: {audio}")
        text = await self.inner.transcribe(audio, language=language)
        print(f"  · STT final: « {text} »" if text else "  · STT final: <vide>")
        return text


async def run_real(seconds: float, threshold: int, hangover_ms: int,
                   device: str = "cpu", compute: str = "int8",
                   drain_seconds: float = 15.0) -> int:
    caps = detect_voice_capabilities()
    print(f"Capabilities: faster_whisper={caps['faster_whisper_available']} "
          f"pyaudio={caps['pyaudio_available']}")
    if not caps["faster_whisper_available"] or not caps["pyaudio_available"]:
        print("⚠️  faster-whisper ou pyaudio absent : impossible de faire le smoke réel.")
        return 2

    tm = TurnManager(barge_in_on_vad=True)
    disp = _ObservingDispatcher(tm)
    tm._dispatcher = disp.dispatch

    vad = RealVADProvider(energy_threshold=threshold, silence_hangover_ms=hangover_ms)
    from src.voice.stt import LumenaSTT  # noqa: PLC0415 - explicite pour bypass les defaults d'import
    print(f"STT smoke: device={device}, compute={compute}")
    stt = _DebugSTT(RealSTTAdapter(stt=LumenaSTT(device=device, compute_type=compute)))
    if not stt.is_available():
        print("⚠️  STT indisponible (modèle Whisper non chargeable).")
        return 2
    mic = MicConversationSource(vad, stt, tm)

    print(f"\n🎤 Parle maintenant — écoute {seconds:.0f}s. (Ctrl+C pour arrêter)\n")
    run_task = asyncio.create_task(tm.run())
    mic_task = asyncio.create_task(mic.run())
    try:
        await asyncio.sleep(seconds)
    except KeyboardInterrupt:
        pass
    mic.stop()
    if not mic_task.done():
        print(f"\n⏳ Fin d'écoute : drainage STT jusqu'à {drain_seconds:.0f}s "
              "(laisse Whisper finir le dernier énoncé)...")
        try:
            await asyncio.wait_for(mic_task, timeout=drain_seconds)
        except asyncio.TimeoutError:
            mic_task.cancel()
        except asyncio.CancelledError:
            pass
    await tm.shutdown()
    try:
        await asyncio.wait_for(run_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    disp.timer.cancel_all()

    print(f"\n✅ Smoke réel terminé. Tours détectés : {len(disp.transcripts)}")
    for i, t in enumerate(disp.transcripts, 1):
        print(f"   {i}. {t}")
    return 0


async def run_dry() -> int:
    """DRY : frames scriptées (loud/quiet) + STT fake → valide le câblage sans hardware."""
    print("Mode DRY (LUMENA_VOICE_V2_STT != 1) : aucun micro, aucun Whisper.\n")

    def _rms(frame):  # 'L'=loud, 'q'=quiet
        return 1000.0 if frame == b"L" else 0.0

    frames = [b"q", b"L", b"L", b"L", b"q", b"q", b"q", b"q"]
    vad = RealVADProvider(energy_threshold=300, frame_ms=10, silence_hangover_ms=30,
                          min_speech_ms=10, frames=frames, rms_fn=_rms)

    class _FakeWhisper:
        async def transcribe_memory(self, audio_bytes, fast=True):
            return "ouvre le fichier de test"
        async def transcribe_file(self, path):
            return ""

    stt = RealSTTAdapter(stt=_FakeWhisper())
    tm = TurnManager(barge_in_on_vad=True)
    disp = _ObservingDispatcher(tm)
    tm._dispatcher = disp.dispatch

    # Frames symboliques (1 octet) → filtre durée désactivé pour le DRY de câblage.
    mic = MicConversationSource(vad, stt, tm, min_utterance_ms=0)
    run_task = asyncio.create_task(tm.run())
    await mic.run()
    await asyncio.sleep(0.6)   # laisse le timer d'endpointing (~300 ms) tirer → start_llm
    await tm.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)
    disp.timer.cancel_all()

    ok = bool(disp.transcripts)
    print(f"\n{'✅' if ok else '❌'} DRY : événements VAD→STT câblés ; "
          f"transcripts={disp.transcripts}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke STT/VAD Voice V2 (manuel, hors prod).")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--threshold", type=int, default=300)
    ap.add_argument("--hangover-ms", type=int, default=700)
    ap.add_argument("--device", choices=("cpu", "cuda"), default="cpu",
                    help="device Whisper pour le smoke. Défaut cpu pour éviter les DLL CUDA manquantes.")
    ap.add_argument("--compute", choices=("int8", "float32", "float16"), default=None,
                    help="compute type Whisper. Défaut: int8 en CPU, float16 en CUDA.")
    ap.add_argument("--drain-seconds", type=float, default=15.0,
                    help="temps laissé à Whisper pour finir après la fenêtre d'écoute.")
    args = ap.parse_args()

    # Le smoke doit valider le pipeline micro→VAD→STT, pas diagnostiquer CUDA.
    # On force CPU par défaut avant que get_stt() ne crée le singleton LumenaSTT.
    if _stt_enabled():
        os.environ["LUMENA_STT_DEVICE"] = args.device
        os.environ["LUMENA_STT_COMPUTE"] = args.compute or ("float16" if args.device == "cuda" else "int8")

    if _stt_enabled():
        compute = args.compute or ("float16" if args.device == "cuda" else "int8")
        return asyncio.run(run_real(args.seconds, args.threshold, args.hangover_ms,
                                    args.device, compute, args.drain_seconds))
    return asyncio.run(run_dry())


if __name__ == "__main__":
    raise SystemExit(main())
