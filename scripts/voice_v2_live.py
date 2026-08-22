#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Launcher Voice V2 LIVE — boucle conversationnelle réelle avec le VRAI LLM.

PREMIER branchement réel : micro → VAD/STT → core.chat (voie rapide) → TTS Piper.
Logs console uniquement, pas d'UI/SSE, pas d'outils, AUCUN changement du flux legacy
(assistant_loop.py n'est PAS touché : ce launcher est séparé).

Réservé au chemin gated : nécessite LUMENA_VOICE_V2_LIVE=1. Sans le flag → ne lance rien.

Usage :
    $env:LUMENA_VOICE_V2_LIVE="1"; venv\Scripts\python.exe scripts\voice_v2_live.py
    # options : --device cpu --compute int8 --threshold 300 --no-calibrate --no-prewarm
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Local-first FORCÉ avant tout import (jamais de cloud).
os.environ["LUMENA_VOICE_CLOUD_ALLOWED"] = "0"
os.environ.setdefault("LUMENA_TTS_MODE", "offline")

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


async def _amain(args) -> int:
    from src.voice.v2 import run_voice_v2_live, v2_live_enabled  # noqa: PLC0415

    if not v2_live_enabled():
        print("⛔ LUMENA_VOICE_V2_LIVE != 1 — Voice V2 live désactivé. "
              "Lance avec $env:LUMENA_VOICE_V2_LIVE=\"1\".")
        return 2

    # Core réel (vrai LLM). Importé seulement quand le flag est armé.
    from src.core import LumenaCore  # noqa: PLC0415
    print("⏳ Initialisation du core Lumena...")
    core = LumenaCore()
    if hasattr(core, "initialize"):
        await core.initialize()

    await run_voice_v2_live(
        core, device=args.device, compute=args.compute,
        energy_threshold=args.threshold, hangover_ms=args.hangover_ms,
        speaking_threshold=args.speaking_threshold,
        calibrate=not args.no_calibrate, calibrate_ms=args.calibrate_ms,
        prewarm=not args.no_prewarm,
        disable_tools=not args.allow_tools,
        llm_mode=("agent" if args.agent else "direct" if args.direct_benchmark else "core_chat"),
        agent_max_iterations=args.agent_max_iterations)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Launcher Voice V2 LIVE (gated, hors legacy).")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--threshold", type=int, default=300)
    ap.add_argument("--hangover-ms", type=int, default=700)
    ap.add_argument("--speaking-threshold", type=int, default=None)
    ap.add_argument("--calibrate-ms", type=int, default=800)
    ap.add_argument("--no-calibrate", action="store_true")
    ap.add_argument("--no-prewarm", action="store_true")
    ap.add_argument("--allow-tools", action="store_true",
                    help="Autorise core.chat à utiliser chat_with_tools. Défaut: outils désactivés en live voix.")
    ap.add_argument("--core-chat", action="store_true",
                    help="Compatibilité : core.chat officiel est désormais le défaut.")
    ap.add_argument("--direct-benchmark", action="store_true",
                    help="LABO uniquement : LLM direct sans session/mémoire/outils. Jamais utilisé en produit.")
    ap.add_argument("--agent", action="store_true",
                    help="Mode TASK-AWARE : think_and_act (ReAct/outils) en tâche de fond, "
                         "feedback vocal + annulation coopérative. Défaut produit: mode Chat officiel.")
    ap.add_argument("--agent-max-iterations", type=int,
                    default=int(os.getenv("LUMENA_VOICE_AGENT_MAX_ITER", "6")),
                    help="Plafond ReAct en mode --agent voix (ou env LUMENA_VOICE_AGENT_MAX_ITER). "
                         "Defaut: 6 pour garder la voix rapide.")
    args = ap.parse_args()
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\n⏹️  Arrêt.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
