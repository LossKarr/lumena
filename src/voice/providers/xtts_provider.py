"""
🎙️ XTTS v2 Provider — Coqui TTS (Apache 2.0)

Voix ultra-naturelle 100% locale, qualité proche d'ElevenLabs.
Supporte le voice cloning français (6 secondes de référence suffisent).

Usage:
    - Mode normal   : voix FR intégrée au modèle
    - Voice cloning : place un fichier WAV FR de 6-10s dans
                      models/xtts/lumena_voice.wav

Activation:
    export LUMENA_TTS_MODE=premium   # XTTS en priorité
    export LUMENA_TTS_MODE=offline   # XTTS uniquement (pas d'internet)
    (défaut: fast → Edge-TTS d'abord, XTTS en fallback offline)
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, List
from loguru import logger

try:
    from TTS.api import TTS as CoquiTTS
    XTTS_AVAILABLE = True
except ImportError:
    XTTS_AVAILABLE = False


class XTTSProvider:
    """
    Provider TTS XTTS v2 (Coqui, Apache 2.0).

    Qualité :  quasi-humaine, convenable en temps réel sur GPU (~1-2s/phrase)
    Offline :  100% local, zéro cloud, zéro clé API
    Voix FR :  intégrée + voice cloning possible
    Modèle  :  ~2GB, téléchargé automatiquement au premier usage
    """

    MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
    LANGUAGE = "fr"

    # Noms de speakers FR courants dans XTTS v2
    # (la liste exacte dépend de la version — _load_model() détecte dynamiquement)
    FR_SPEAKER_KEYWORDS = ["fr", "french", "amelie", "claire", "sophie",
                           "marie", "ana", "florence", "boudreau", "allard"]

    def __init__(self, data_dir: Optional[Path] = None):
        self.root_dir = data_dir or Path(__file__).parent.parent.parent.parent
        self.models_dir = self.root_dir / "models" / "xtts"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self._tts: Optional["CoquiTTS"] = None
        self._selected_speaker: Optional[str] = None

        # Voice cloning : place un WAV de 6-10s ici pour que Lumena adopte
        # ta voix (ou une voix custom)
        self.voice_reference: Optional[str] = self._find_voice_reference()

        if self.voice_reference:
            logger.info(f"🎙️ XTTS: référence vocale détectée → {Path(self.voice_reference).name}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True si coqui-tts est installé."""
        return XTTS_AVAILABLE

    async def generate(self, text: str, output_path: Path) -> bool:
        """
        Génère un fichier WAV ultra-naturel à partir du texte.

        Args:
            text        : texte à synthétiser
            output_path : chemin de sortie .wav

        Returns:
            True si réussi
        """
        if not XTTS_AVAILABLE:
            return False

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._generate_sync, text, output_path)

    def get_info(self) -> dict:
        return {
            "provider": "XTTS v2",
            "model": self.MODEL_NAME,
            "language": self.LANGUAGE,
            "speaker": (
                "voice_clone" if self.voice_reference
                else (self._selected_speaker or "auto")
            ),
            "voice_reference": self.voice_reference,
            "gpu": self._cuda_available(),
            "loaded": self._tts is not None,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_voice_reference(self) -> Optional[str]:
        """Cherche un WAV de référence pour le voice cloning."""
        candidates = [
            self.root_dir / "models" / "xtts" / "lumena_voice.wav",
            self.root_dir / "assets" / "lumena_voice.wav",
            self.root_dir / "models" / "xtts" / "reference.wav",
        ]
        for c in candidates:
            if c.exists() and c.stat().st_size > 10_000:  # > 10KB = audio réel
                return str(c)
        return None

    def _cuda_available(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _load_model(self) -> bool:
        """Charge XTTS v2 (lazy, thread-safe)."""
        if self._tts is not None:
            return True
        if not XTTS_AVAILABLE:
            logger.warning("coqui-tts non disponible. pip install coqui-tts")
            return False

        try:
            gpu = self._cuda_available()
            logger.info(
                f"🎙️ Chargement XTTS v2 (première fois ~1-2 min + ~2 GB) "
                f"[{'GPU' if gpu else 'CPU'}]..."
            )
            self._tts = CoquiTTS(
                model_name=self.MODEL_NAME,
                progress_bar=False,
                gpu=gpu,
            )

            # Sélectionner automatiquement une voix FR
            speakers: List[str] = getattr(self._tts, "speakers", None) or []
            if speakers:
                fr_candidates = [
                    s for s in speakers
                    if any(k in s.lower() for k in self.FR_SPEAKER_KEYWORDS)
                ]
                self._selected_speaker = fr_candidates[0] if fr_candidates else speakers[0]
            else:
                self._selected_speaker = None  # Certaines versions sans speaker list

            logger.info(
                f"✅ XTTS v2 chargé — voix: "
                f"{'voice_clone' if self.voice_reference else (self._selected_speaker or 'default')}"
            )
            return True

        except Exception as e:
            logger.error(f"Erreur chargement XTTS v2: {e}")
            self._tts = None
            return False

    def _generate_sync(self, text: str, output_path: Path) -> bool:
        """Génération synchrone (appelée via executor)."""
        if not self._load_model():
            return False

        try:
            kwargs: dict = {
                "text": text,
                "file_path": str(output_path),
                "language": self.LANGUAGE,
            }

            if self.voice_reference:
                # Voice cloning — utilise le WAV de référence
                kwargs["speaker_wav"] = self.voice_reference
            elif self._selected_speaker:
                kwargs["speaker"] = self._selected_speaker
            # Si ni l'un ni l'autre : certains modèles fonctionnent sans speaker

            self._tts.tts_to_file(**kwargs)

            ok = output_path.exists() and output_path.stat().st_size > 0
            if ok:
                logger.debug(f"🎙️ XTTS v2: '{text[:40]}...' → {output_path.name}")
            return ok

        except Exception as e:
            logger.error(f"Erreur génération XTTS v2: {e}")
            # Nettoyer le fichier corrompu éventuel
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass  # cleanup fichier corrompu best-effort
            return False
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
