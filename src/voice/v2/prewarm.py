"""Prewarm — préchauffage des composants voix + détection capabilities (V2.2/V2.4).

But : éviter le gel « 5-10s au premier mot » en préchargeant STT/TTS/VAD/profil
AU MOMENT d'activer le mode voix, et reporter un statut par composant. Aucun
warmer ne doit jamais bloquer ni crasher le wizard → tout échec = `degraded`/`error`.

À ce stade : aucun import de la stack lourde. `detect_voice_capabilities` utilise
`importlib.util.find_spec` (ne charge pas les modules). Les warmers réels seront
injectés plus tard ; ici on fournit le cadre + des warmers factices pour les tests.
"""
from __future__ import annotations

import asyncio
import importlib.util
import functools
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Union

# États possibles d'un composant.
PREPARING, READY, DEGRADED, ERROR, SKIPPED = "preparing", "ready", "degraded", "error", "skipped"


class ComponentUnavailable(Exception):
    """Levée par un warmer quand le composant est absent (→ statut 'skipped', non bloquant)."""


@dataclass
class ComponentStatus:
    name: str
    state: str = PREPARING
    detail: str = ""
    elapsed_ms: int = 0
    required: bool = False


# Un warmer : callable sans arg. Retour True/None = prêt, False = dégradé,
# lève ComponentUnavailable = absent, toute autre exception = erreur.
Warmer = Callable[[], Optional[bool]]


@dataclass
class Prewarmer:
    timeout_ms: int = 8000
    _warmers: List[tuple] = field(default_factory=list)  # (name, warmer, required)

    def register(self, name: str, warmer: Warmer, *, required: bool = False) -> None:
        self._warmers.append((name, warmer, required))

    # ── Exécution synchrone (chaque warmer dans un thread + join(timeout)) ──
    def _run_one_sync(self, name: str, warmer: Warmer, required: bool) -> ComponentStatus:
        result: Dict[str, object] = {}
        t0 = time.monotonic()

        def _target():
            try:
                result["value"] = warmer()
            except ComponentUnavailable as e:
                result["unavailable"] = str(e) or "absent"
            except Exception as e:  # jamais propagé au wizard
                result["error"] = f"{type(e).__name__}: {e}"

        th = threading.Thread(target=_target, daemon=True)
        th.start()
        th.join(self.timeout_ms / 1000.0)
        elapsed = int((time.monotonic() - t0) * 1000)
        if th.is_alive():
            return ComponentStatus(name, DEGRADED, "timeout", elapsed, required)
        if "unavailable" in result:
            return ComponentStatus(name, SKIPPED, str(result["unavailable"]), elapsed, required)
        if "error" in result:
            return ComponentStatus(name, ERROR, str(result["error"]), elapsed, required)
        val = result.get("value", True)
        if val is False:
            return ComponentStatus(name, DEGRADED, "warmer returned False", elapsed, required)
        return ComponentStatus(name, READY, "", elapsed, required)

    def run_all(self) -> Dict[str, ComponentStatus]:
        return {name: self._run_one_sync(name, w, req) for (name, w, req) in self._warmers}

    # ── Exécution asynchrone (warmers sync exécutés en thread executor) ──
    async def run_all_async(self) -> Dict[str, ComponentStatus]:
        loop = asyncio.get_event_loop()
        out: Dict[str, ComponentStatus] = {}
        for name, warmer, required in self._warmers:
            t0 = time.monotonic()
            try:
                val = await asyncio.wait_for(
                    loop.run_in_executor(None, warmer), timeout=self.timeout_ms / 1000.0
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                out[name] = ComponentStatus(
                    name, DEGRADED if val is False else READY,
                    "warmer returned False" if val is False else "", elapsed, required,
                )
            except asyncio.TimeoutError:
                out[name] = ComponentStatus(name, DEGRADED, "timeout",
                                            int((time.monotonic() - t0) * 1000), required)
            except ComponentUnavailable as e:
                out[name] = ComponentStatus(name, SKIPPED, str(e) or "absent",
                                            int((time.monotonic() - t0) * 1000), required)
            except Exception as e:
                out[name] = ComponentStatus(name, ERROR, f"{type(e).__name__}: {e}",
                                            int((time.monotonic() - t0) * 1000), required)
        return out


def overall_state(statuses: Dict[str, ComponentStatus]) -> str:
    """Synthèse globale : error si un requis est en erreur, ready si tous requis prêts, sinon degraded."""
    req = [s for s in statuses.values() if s.required]
    if any(s.state == ERROR for s in req):
        return ERROR
    if req and all(s.state == READY for s in req):
        return READY
    if not req and all(s.state == READY for s in statuses.values()):
        return READY
    return DEGRADED


def ready_message(statuses: Dict[str, ComponentStatus]) -> str:
    """Micro-réponse à jouer une fois prêt (cf. micro-réponses locales)."""
    return "Je suis prête." if overall_state(statuses) == READY else "Voix en mode dégradé."


def _cuda_runtime_files() -> Dict[str, bool]:
    """Probe the DLL/shared-library files required by CTranslate2, without loading them."""
    roots: List[Path] = []
    if os.name == "nt":
        venv = Path(sys.executable).resolve().parent.parent
        site = venv / "Lib" / "site-packages" / "nvidia"
        roots.extend([site / "cublas" / "bin", site / "cudnn" / "bin"])
        cuda_path = os.getenv("CUDA_PATH", "").strip()
        if cuda_path:
            roots.append(Path(cuda_path) / "bin")
        patterns = {"cublas": "cublas64_*.dll", "cudnn": "cudnn64_*.dll"}
    else:
        roots.extend(Path(item) for item in os.getenv("LD_LIBRARY_PATH", "").split(os.pathsep) if item)
        patterns = {"cublas": "libcublas.so*", "cudnn": "libcudnn.so*"}
    result: Dict[str, bool] = {}
    for name, pattern in patterns.items():
        result[name] = any(root.is_dir() and next(root.glob(pattern), None) for root in roots)
        if not result[name]:
            result[name] = shutil.which(pattern.split("*")[0]) is not None
    return result


@functools.lru_cache(maxsize=1)
def detect_voice_hardware() -> Dict[str, object]:
    """Bounded local hardware probe used for the effective Voice profile."""
    cpu_count = os.cpu_count() or 1
    try:
        import psutil  # noqa: PLC0415
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        ram_gb = 0.0
    gpu: Dict[str, object] = {
        "available": False, "name": None, "vram_gb": 0.0,
        "vram_free_gb": 0.0, "driver_version": None,
    }
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        line = next((value for value in completed.stdout.splitlines() if value.strip()), "")
        if completed.returncode == 0 and line:
            parts = [part.strip() for part in line.split(",")]
            gpu.update({
                "available": True, "name": parts[0],
                "vram_gb": round(float(parts[1]) / 1024, 1),
                "vram_free_gb": round(float(parts[2]) / 1024, 1),
                "driver_version": parts[3] if len(parts) > 3 else None,
            })
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    libraries = _cuda_runtime_files()
    cuda_ready = bool(
        gpu["available"] and libraries["cublas"] and libraries["cudnn"]
        and float(gpu["vram_free_gb"] or 0) >= 1.5
    )
    npu = importlib.util.find_spec("openvino") is not None
    if cuda_ready:
        profile = "nvidia"
    elif npu:
        profile = "npu"
    elif cpu_count >= 4 and ram_gb >= 8:
        profile = "cpu_fast"
    else:
        profile = "cpu_low"
    return {
        "profile": profile, "cpu_count": cpu_count, "ram_gb": ram_gb,
        "gpu": gpu, "cuda_runtime": libraries, "cuda_ready": cuda_ready,
        "npu_runtime": "openvino" if npu else None,
    }


def detect_voice_capabilities(
    xtts_reference: Union[str, Path] = "models/xtts/lumena_voice.wav",
) -> Dict[str, object]:
    """Détecte la disponibilité SANS importer la stack lourde (`find_spec` seulement).

    `cuda_available` reste 'unknown' : on ne l'importe pas (torch est lourd) ;
    ce sera résolu plus tard, à l'activation réelle.
    """
    def has(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False

    return {
        "faster_whisper_available": has("faster_whisper"),
        "xtts_available": has("TTS"),
        "piper_available": has("piper") or has("piper_phonemize"),
        "edge_tts_available": has("edge_tts"),
        "pyttsx3_available": has("pyttsx3"),
        "torch_available": has("torch"),
        "pyaudio_available": has("pyaudio"),
        "xtts_voice_reference_found": Path(xtts_reference).exists(),
        "cuda_available": detect_voice_hardware()["cuda_ready"],
        "hardware": detect_voice_hardware(),
        "silero_vad_available": has("silero_vad") and has("torch"),
        "vad_engine": "silero_onnx" if has("silero_vad") and has("torch") else "energy",
        "server_aec_available": (
            has("pywebrtc_audio") or has("webrtc_audio_processing")
            or has("webrtc_audio_processing_python")
        ),
        "activation_engine": "local_stt_phrase_gate",
        # WebRTC navigateur ne constitue pas l'AEC du micro PyAudio serveur.
        "webrtc_available": "browser_side",
    }
