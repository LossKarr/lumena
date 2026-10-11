"""Voice-specific finalization of generic signed release components."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
from typing import Any

from src.runtime.component_service import ComponentServiceError
from src.runtime.component_store import ComponentStore


def activate_installed_voice_pack(*, root: Path) -> dict[str, Any]:
    """Import the doubly signed product Voice Pack from its component generation."""
    store = ComponentStore(Path(root).resolve())
    active = store.active("lumena-voice")
    if active is None:
        raise ComponentServiceError("Lumena voice component is not active")
    component_dir = (store.root / active.path).resolve()
    archives = list(component_dir.glob("*.voicepack.zip"))
    if len(archives) != 1:
        raise ComponentServiceError("Lumena voice pack payload is invalid")
    try:
        from .voice_pack_service import get_voice_pack_service
        return get_voice_pack_service().install(archives[0], activate=True)
    except Exception as exc:
        raise ComponentServiceError("Lumena voice pack activation failed") from exc


async def _qwen_canary_async() -> dict[str, Any]:
    from .providers.qwen3_tts import Qwen3TTSProvider
    from .qwen_runtime import probe_qwen_runtime, record_qwen_performance
    from .voice_pack_service import get_voice_pack_service
    from .voice_profile import load_profile

    runtime = probe_qwen_runtime(timeout_s=30.0, deep=False)
    if not runtime.get("ready"):
        raise ComponentServiceError("Qwen runtime probe failed")
    profile = get_voice_pack_service().apply_to_profile(load_profile())
    provider = Qwen3TTSProvider()
    started = time.perf_counter()
    result = await provider.synthesize("Lumena est prête.", profile)
    elapsed = time.perf_counter() - started
    try:
        if not result.ok or not result.audio_path or result.duration_ms <= 0:
            raise ComponentServiceError("Qwen synthesis canary failed")
        audio_seconds = result.duration_ms / 1000.0
        rtf = elapsed / audio_seconds
        max_first_audio_ms = int(os.getenv("LUMENA_QWEN_AUTO_MAX_FIRST_AUDIO_MS", "2500"))
        max_rtf = float(os.getenv("LUMENA_QWEN_AUTO_MAX_RTF", "0.85"))
        qualified = elapsed * 1000 <= max_first_audio_ms and rtf <= max_rtf
        record_qwen_performance(
            qualified=qualified, first_audio_ms=int(elapsed * 1000), rtf=rtf,
        )
        return {
            "ok": True, "qualified": qualified,
            "first_audio_ms": int(elapsed * 1000), "rtf": round(rtf, 4),
            "provider": result.provider,
        }
    finally:
        if result.audio_path:
            Path(result.audio_path).unlink(missing_ok=True)
        await provider.aclose()


def run_qwen_install_canary() -> dict[str, Any]:
    """Run the real local synthesis proof and persist automatic-routing fitness."""
    return asyncio.run(_qwen_canary_async())
