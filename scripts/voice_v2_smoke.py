#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Smoke test MANUEL du système Voice V2 — HORS pytest, flag-gated, local-first.

But : premier contact AUDIO RÉEL (à valider à l'oreille), en réutilisant la logique
déjà prouvée en pytest (TurnManager + LocalTTSAdapter synthèse-seule + LocalAudioPlayer
playback cancellable). Ne touche RIEN en prod (pas d'assistant_loop, pas de STT, pas
de WebRTC) et ne s'exécute en audio réel QUE si le flag est armé.

Usage :
    # Mode DRY (aucun son, vérifie le câblage) :
    python scripts/voice_v2_smoke.py

    # Audio réel (local XTTS/Piper, jamais le cloud) :
    LUMENA_VOICE_V2_TTS=1 python scripts/voice_v2_smoke.py
    # Windows PowerShell :
    $env:LUMENA_VOICE_V2_TTS="1"; python scripts/voice_v2_smoke.py

    # Si la sortie est en mojibake (console legacy), forcer UTF-8 :
    $env:PYTHONIOENCODING="utf-8"; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; \
        $env:LUMENA_VOICE_V2_TTS="1"; venv\Scripts\python.exe scripts\voice_v2_smoke.py

Options : --text "..."  --answer "..."  --interrupt-after 1.5  --scenario all|full|interrupt
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── Local-first FORCÉ avant tout import (jamais de cloud dans ce smoke) ──
os.environ["LUMENA_VOICE_CLOUD_ALLOWED"] = "0"
os.environ.setdefault("LUMENA_TTS_MODE", "offline")   # XTTS/Piper local, pas Edge

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Console Windows : forcer UTF-8 (code page 65001 + flux) pour éviter le mojibake.
def _setup_console_utf8() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # CP_UTF8
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
    TurnManager, VoiceEvent, VoiceRuntime, LocalAudioPlayer, ConversationAudioLedger,
    FakeTTSProvider, LocalTTSAdapter, detect_voice_capabilities, v2_tts_enabled,
)

DEFAULT_ANSWER = (
    "Bonjour, je suis Lumena. Voici une phrase un peu plus longue pour que tu puisses "
    "m'interrompre pendant que je parle. Si tu m'as coupée avant la fin, c'est que "
    "l'interruption fonctionne correctement."
)


def _hr(title: str) -> None:
    print("\n" + "=" * 70 + f"\n  {title}\n" + "=" * 70)


def _build(real: bool):
    led = ConversationAudioLedger()
    if real:
        from src.voice.tts import get_tts
        tts = get_tts()
        provider = LocalTTSAdapter(tts=tts)
        player = LocalAudioPlayer(ledger=led)          # vrai _play_audio (lazy)
    else:
        provider = FakeTTSProvider(chunk_ms=300)
        played = []

        def _fake_play(x):
            async def _():
                print(f"   [DRY] (jouerait ~2s) : {x!r}")
                await asyncio.sleep(2.0)   # simule un playback long (pour démontrer la coupe)
                played.append(x)
            return _()

        player = LocalAudioPlayer(ledger=led, play_fn=_fake_play, stop_fn=lambda: print("   [DRY] stop()"))
    tm = TurnManager()
    rt = VoiceRuntime(tm, provider, player, respond_fn=lambda _t: _build.answer, enabled=True)
    tm._dispatcher = rt.dispatch
    return tm, rt, player, led


_build.answer = DEFAULT_ANSWER  # type: ignore[attr-defined]


async def _wait_until(pred, timeout=20.0, step=0.02):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while loop.time() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


async def scenario_full_tour(real: bool, text_user: str):
    _hr("Scénario 1 — tour complet (synthèse + playback)")
    tm, rt, player, led = _build(real)
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": text_user}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    await _wait_until(lambda: bool(rt._finished), timeout=25.0)
    await asyncio.sleep(0.2)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    await rt.aclose()
    print(f"   provider utilisé : {rt.last_provider or '(fake)'} | dégradé : {rt.degraded}")
    print(f"   chunks joués     : {len(player.played)}")
    if rt.degraded:
        print("   ⚠️  DÉGRADÉ : fallback pyttsx3 (voix robotique). Installe XTTS/Piper pour la vraie voix.")
    print(f"   statut final     : {rt.status_report()}")


async def scenario_interruption(real: bool, text_user: str, interrupt_after: float):
    _hr(f"Scénario 2 — interruption après {interrupt_after}s (stop pendant le playback)")
    tm, rt, player, led = _build(real)
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": text_user}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    # attendre que la génération soit ARMÉE (playback lancé), pas qu'il finisse
    await _wait_until(lambda: player.current_generation_id not in (None, "__cleared__"), timeout=10.0)
    await asyncio.sleep(interrupt_after)
    print(f"   → STOP (l'acteur doit réagir immédiatement, sans attendre la fin du playback)")
    await tm.emit(VoiceEvent("user.stop_word", data={"word": "stop"}))
    ok = await _wait_until(lambda: rt.status == "interrupted", timeout=3.0)
    await asyncio.sleep(0.2)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    await rt.aclose()
    gen = led.order[-1] if led.order else None
    ps = led.get(gen) if gen else None
    print(f"   stop traité      : {ok} | statut : {rt.status}")
    if ps:
        print(f"   entendu (ledger) : {ps.text_played[:80]!r}")
        print(f"   tronqué          : interrupted={ps.interrupted}")
    print(f"   chunks périmés rejetés : {len(player.dropped)}")


def main():
    ap = argparse.ArgumentParser(description="Smoke test manuel Voice V2 (local-first, flag-gated).")
    ap.add_argument("--text", default="dis bonjour", help="transcript utilisateur simulé")
    ap.add_argument("--answer", default=DEFAULT_ANSWER, help="réponse que Lumena va prononcer")
    ap.add_argument("--interrupt-after", type=float, default=1.0, help="délai avant le stop (s)")
    ap.add_argument("--scenario", choices=("all", "full", "interrupt"), default="all",
                    help="scénario à lancer : all, full ou interrupt")
    args = ap.parse_args()
    _build.answer = args.answer  # type: ignore[attr-defined]

    real = v2_tts_enabled()
    _hr("Voice V2 — Smoke test manuel")
    print(f"  LUMENA_VOICE_V2_TTS = {'1 (AUDIO RÉEL)' if real else '0 (DRY, aucun son)'}")
    print(f"  LUMENA_VOICE_CLOUD_ALLOWED = {os.environ['LUMENA_VOICE_CLOUD_ALLOWED']}  (cloud interdit)")
    print(f"  LUMENA_TTS_MODE = {os.environ.get('LUMENA_TTS_MODE')}")
    caps = detect_voice_capabilities()
    print("  Capacités :", {k: caps[k] for k in ("xtts_available", "piper_available",
                                                  "edge_tts_available", "pyttsx3_available",
                                                  "xtts_voice_reference_found", "torch_available")})
    if not real:
        print("\n  ▶ Mode DRY : aucun son. Pour l'audio réel :")
        print("      $env:LUMENA_VOICE_V2_TTS=\"1\"; python scripts/voice_v2_smoke.py   (PowerShell)")

    asyncio.run(_run_all(real, args))
    _hr("Fin du smoke test")


async def _run_all(real: bool, args):
    if args.scenario in ("all", "full"):
        await scenario_full_tour(real, args.text)
    if args.scenario in ("all", "interrupt"):
        await scenario_interruption(real, args.text, args.interrupt_after)


if __name__ == "__main__":
    main()
