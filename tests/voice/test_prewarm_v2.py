"""Voice V2 — Prewarm (préchauffage non bloquant + capabilities, sans stack lourde)."""
import time

import pytest

from src.voice.v2 import (
    Prewarmer, ComponentUnavailable, detect_voice_capabilities,
    overall_state, ready_message, READY, DEGRADED, ERROR, SKIPPED,
)


def test_prewarm_mixed_results_never_raises():
    pw = Prewarmer(timeout_ms=300)
    pw.register("stt", lambda: True, required=True)          # ready
    pw.register("tts", lambda: False, required=True)         # degraded
    pw.register("xtts", lambda: (_ for _ in ()).throw(RuntimeError("gpu")), required=False)  # error
    pw.register("piper", lambda: (_ for _ in ()).throw(ComponentUnavailable("absent")))      # skipped

    statuses = pw.run_all()  # ne doit jamais lever
    assert statuses["stt"].state == READY
    assert statuses["tts"].state == DEGRADED
    assert statuses["xtts"].state == ERROR and "gpu" in statuses["xtts"].detail
    assert statuses["piper"].state == SKIPPED
    # un requis dégradé -> global dégradé (pas d'erreur car aucun requis en erreur)
    assert overall_state(statuses) == DEGRADED
    assert ready_message(statuses) == "Voix en mode dégradé."


def test_prewarm_all_ready_message():
    pw = Prewarmer(timeout_ms=300)
    pw.register("stt", lambda: True, required=True)
    pw.register("tts", lambda: None, required=True)          # None = prêt
    statuses = pw.run_all()
    assert overall_state(statuses) == READY
    assert ready_message(statuses) == "Je suis prête."


def test_prewarm_timeout_is_degraded_not_blocking():
    pw = Prewarmer(timeout_ms=100)
    pw.register("slow", lambda: time.sleep(5) or True, required=True)  # dépasse le timeout
    t0 = time.monotonic()
    statuses = pw.run_all()
    elapsed = time.monotonic() - t0
    assert statuses["slow"].state == DEGRADED and statuses["slow"].detail == "timeout"
    assert elapsed < 2.0   # n'attend pas les 5s (non bloquant)


@pytest.mark.asyncio
async def test_prewarm_async():
    pw = Prewarmer(timeout_ms=300)
    pw.register("stt", lambda: True, required=True)
    pw.register("tts", lambda: False, required=True)
    statuses = await pw.run_all_async()
    assert statuses["stt"].state == READY and statuses["tts"].state == DEGRADED


def test_detect_capabilities_import_free():
    caps = detect_voice_capabilities(xtts_reference="models/xtts/__inexistant__.wav")
    # clés attendues présentes
    for k in ("faster_whisper_available", "xtts_available", "piper_available",
              "edge_tts_available", "torch_available", "pyaudio_available",
              "xtts_voice_reference_found", "cuda_available", "webrtc_available"):
        assert k in caps
    # référence absente -> False ; cuda jamais importé -> 'unknown'
    assert caps["xtts_voice_reference_found"] is False
    assert caps["cuda_available"] == "unknown"
    assert caps["webrtc_available"] == "browser_side"


def test_detect_capabilities_does_not_import_torch():
    import sys
    # find_spec ne doit pas charger torch dans ce process
    before = "torch" in sys.modules
    detect_voice_capabilities()
    after = "torch" in sys.modules
    # si torch n'était pas déjà chargé, il ne doit pas l'être par la détection
    assert after == before
