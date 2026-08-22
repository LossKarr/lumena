"""
🌟 LUMENA - Text-to-Speech (TTS)

Module pourr faire parler LUMENA en utilisant edge-tts.

Fonctionnalités:
- Système de fallback (edge-tts → pyttsx3)
- Métriques de latence et succès
"""

import asyncio
import tempfile
import os
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
try:
    from .providers.piper_provider import PiperProvider
except ImportError:
    try:
        from src.voice.providers.piper_provider import PiperProvider  # fallback absolu
    except ImportError:
        PiperProvider = None  # type: ignore

try:
    from .providers.xtts_provider import XTTSProvider
except ImportError:
    try:
        from src.voice.providers.xtts_provider import XTTSProvider
    except ImportError:
        XTTSProvider = None  # type: ignore

# Provider principal: edge-tts
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts non installé. pip install edge-tts")

# Fallback: pyttsx3 (offline)
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False
    logger.debug("pyttsx3 non installé (fallback offline). pip install pyttsx3")


@dataclass
class TTSMetrics:
    """
    Métriques TTS.
    
    Permet de suivre les performances et la fiabilité du TTS.
    """
    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    fallback_count: int = 0
    total_latency_ms: float = 0.0
    last_error: Optional[str] = None
    last_success: Optional[datetime] = None
    provider_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    @property
    def success_rate(self) -> float:
        """Taux de succès en pourcentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.success_count / self.total_requests) * 100
    
    @property
    def avg_latency_ms(self) -> float:
        """Latence moyenne en millisecondes."""
        if self.success_count == 0:
            return 0.0
        return self.total_latency_ms / self.success_count
    
    def record_success(self, provider: str, latency_ms: float):
        """Enregistre un succès."""
        self.total_requests += 1
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.last_success = datetime.now()
        
        if provider not in self.provider_stats:
            self.provider_stats[provider] = {"success": 0, "failure": 0}
        self.provider_stats[provider]["success"] += 1
    
    def record_failure(self, provider: str, error: str):
        """Enregistre un échec."""
        self.total_requests += 1
        self.failure_count += 1
        self.last_error = error
        
        if provider not in self.provider_stats:
            self.provider_stats[provider] = {"success": 0, "failure": 0}
        self.provider_stats[provider]["failure"] += 1
    
    def record_fallback(self):
        """Enregistre un fallback."""
        self.fallback_count += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """Exporte les métriques."""
        return {
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "fallback_count": self.fallback_count,
            "success_rate": f"{self.success_rate:.1f}%",
            "avg_latency_ms": f"{self.avg_latency_ms:.0f}ms",
            "last_error": self.last_error,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "provider_stats": self.provider_stats
        }


# Métriques globales
_tts_metrics = TTSMetrics()


class LumenaTTS:
    """
    Système Text-to-Speech pour LUMENA.
    
    Utilise edge-tts (Microsoft Edge TTS) avec fallback vers pyttsx3.
    Inclut métriques de performances.
    """
    
    # Voix françaises recommandées
    VOICES = {
        "fr_female_1": "fr-FR-VivienneMultilingualNeural",  # Meilleure voix FR (ultra-naturelle)
        "fr_female_2": "fr-FR-DeniseNeural",              # Voix française féminine classique
        "fr_female_3": "fr-FR-EloiseNeural",              # Voix française féminine alternative
        "fr_male": "fr-FR-HenriNeural",                   # Voix française masculine
        "fr_child": "fr-FR-CoralieNeural",                # Voix enfantine
        "en_female": "en-US-JennyNeural",                 # Voix anglaise féminine
        "en_male": "en-US-GuyNeural",                     # Voix anglaise masculine
    }
    
    def __init__(
        self, 
        voice: str = "fr_female_1",
        rate: str = "+10%",       # Vitesse de parole (+ = plus rapide)
        volume: str = "+0%",      # Volume
        pitch: str = "+5Hz",      # Hauteur de voix
        enable_fallback: bool = True,  # Activer le fallback
    ):
        """
        Initialise le TTS.
        
        Args:
            voice: Clé de voix (voir VOICES) ou nom complet de la voix
            rate: Modification de vitesse (ex: "+10%", "-5%")
            volume: Modification de volume
            pitch: Modification de hauteur
            enable_fallback: Utiliser pyttsx3 si edge-tts échoue
        """
        self.voice = self.VOICES.get(voice, voice)
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.enable_fallback = enable_fallback
        
        # Cache pour les fichiers audio
        self.cache_dir = Path(tempfile.gettempdir()) / "lumena_tts"
        self.cache_dir.mkdir(exist_ok=True)
        
        # État
        self.is_speaking = False
        self._current_process: Optional[asyncio.subprocess.Process] = None
        self._stop_speaking = False  # flag barge-in : interrompre la lecture en cours

        # Engine pyttsx3 (fallback)
        self._pyttsx3_engine = None
        
        # Métriques
        self.metrics = _tts_metrics

        # Provider Piper (Local ONNX — fallback d'urgence)
        self.piper = PiperProvider() if PiperProvider is not None else None
        _piper_avail = self.piper.is_available() if self.piper is not None else False

        # Provider XTTS v2 (Local ultra-naturel — prioritaire en mode premium/offline)
        self.xtts = XTTSProvider() if XTTSProvider is not None else None
        _xtts_avail = self.xtts.is_available() if self.xtts is not None else False

        # Mode TTS:
        #   fast    (défaut) : Edge-TTS → XTTS → Piper → pyttsx3
        #   premium          : XTTS  → Edge-TTS → Piper → pyttsx3
        #   offline          : XTTS  → Piper → pyttsx3
        self._tts_mode = os.getenv("LUMENA_TTS_MODE", "fast")

        logger.info(
            f"TTS initialisé — voix: {self.voice} | mode: {self._tts_mode} "
            f"| piper: {_piper_avail} | xtts: {_xtts_avail}"
        )
    
    async def speak(self, text: str, wait: bool = True) -> Optional[Path]:
        """
        Fait parler LUMENA avec fallback automatique.
        
        Args:
            text: Texte à prononcer
            wait: Attendre la fin de la lecture
            
        Returns:
            Chemin vers le fichier audio généré
        """
        if not text or not text.strip():
            return None
        
        # Nettoyer le texte
        text = self._clean_text(text)
        
        # Générer un nom de fichier unique (base)
        # ── Sentence pipeline : multi-phrases + wait + edge-tts → latence perçue -40% ──
        import re as _re_pipe
        if (wait and EDGE_TTS_AVAILABLE and self._tts_mode != "offline"
                and _re_pipe.search(r'[.!?…]\s+\S', text)):
            _ok = await self._speak_sentences(text)
            if _ok or self._stop_speaking:
                return None  # lecture terminée ou interrompue volontairement

        # ── V2 : synthèse seule (cascade providers, SANS playback) ──
        audio_file = await self._synthesize(text)
        if audio_file is None:
            return None
        # Jouer l'audio
        if wait and audio_file and audio_file.exists():
            await self._play_audio(audio_file)
        elif audio_file and audio_file.exists():
            asyncio.create_task(self._play_audio(audio_file))
        return audio_file

    async def _synthesize(
        self, text: str, *, local_only: bool = False, allow_xtts: bool = True,
        piper_model: Optional[str] = None,
    ) -> Optional[Path]:
        """Phase SYNTHÈSE seule (V2) — cascade de providers → fichier audio, SANS playback.

        Ne prend JAMAIS le chemin `_speak_sentences` (réservé à `speak()`).
        `local_only=True` interdit Edge-TTS (cloud) — utilisé quand le cloud n'est pas autorisé.
        Retourne le `Path` du fichier généré, ou None si aucun provider n'a réussi.
        """
        if not text or not text.strip():
            return None
        text = self._clean_text(text)
        import hashlib
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]

        start_time = time.time()
        success = False
        audio_file = None
        provider = None  # provider effectivement utilisé (pour statut V2 : pyttsx3 -> degraded)
        effective_piper_model = None
        if self.piper is not None:
            try:
                if piper_model and self.piper.is_available(piper_model):
                    effective_piper_model = piper_model
            except (TypeError, ValueError):
                effective_piper_model = None
        piper_model_name = effective_piper_model or getattr(self.piper, "model_name", "default")
        piper_cache_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", str(piper_model_name))

        # 0. XTTS v2 — ultra-naturel local (prioritaire si mode premium/offline)
        if (allow_xtts and not success and self._tts_mode in ("premium", "offline")
                and self.xtts is not None and self.xtts.is_available()):
            provider = "xtts"
            audio_file = self.cache_dir / f"lumena_xtts_{text_hash}.wav"
            try:
                if audio_file.exists() and audio_file.stat().st_size > 0:
                    success = True
                elif await self.xtts.generate(text, audio_file):
                    success = True
                if success:
                    latency_ms = (time.time() - start_time) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider}): {audio_file}")
            except Exception as e:
                logger.warning(f"XTTS v2 échoué: {e}")
                self.metrics.record_failure(provider, str(e))

        # 1. Piper (Local ONNX — dernier recours avant pyttsx3, qualité correcte)
        # NOTE: Piper passe APRÈS Edge-TTS en mode premium/fast — uniquement si tout le reste échoue
        if (not success and self._tts_mode == "offline" and self.piper is not None
                and self.piper.is_available(effective_piper_model)):
            provider = "piper"
            audio_file = self.cache_dir / f"lumena_piper_utf8_v2_{piper_cache_tag}_{text_hash}.wav"
            try:
                if audio_file.exists():
                    success = True
                elif await self.piper.generate(
                    text, audio_file, model_name=effective_piper_model,
                ):
                    success = True
                
                if success:
                    latency_ms = (time.time() - start_time) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider}): {audio_file}")
            except Exception as e:
                logger.warning(f"Piper échoué: {e}")
                self.metrics.record_failure(provider, str(e))
        
        # 2. Edge-TTS (cloud, VivienneMultilingualNeural, ~200ms — pas disponible si mode offline)
        #    V2 : interdit si local_only (cloud non autorisé).
        if not success and EDGE_TTS_AVAILABLE and self._tts_mode != "offline" and not local_only:
            provider = "edge-tts"
            audio_file = self.cache_dir / f"lumena_{text_hash}.mp3"
            try:
                if audio_file.exists():
                    success = True
                else:
                    communicate = edge_tts.Communicate(
                        text,
                        self.voice,
                        rate=self.rate,
                        volume=self.volume,
                        pitch=self.pitch
                    )
                    await communicate.save(str(audio_file))
                    success = True
                
                if success:
                    latency_ms = (time.time() - start_time) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider}): {audio_file}")
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"edge-tts échoué: {error_msg}")
                self.metrics.record_failure(provider, error_msg)
        
        # 2b. XTTS v2 (fallback offline en mode fast si edge-tts indisponible)
        if (allow_xtts and not success and self._tts_mode == "fast"
                and self.xtts is not None and self.xtts.is_available()):
            provider = "xtts"
            audio_file = self.cache_dir / f"lumena_xtts_{text_hash}.wav"
            try:
                if audio_file.exists() and audio_file.stat().st_size > 0:
                    success = True
                elif await self.xtts.generate(text, audio_file):
                    success = True
                if success:
                    latency_ms = (time.time() - start_time) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider} fallback offline): {audio_file}")
            except Exception as e:
                logger.warning(f"XTTS v2 fallback échoué: {e}")
                self.metrics.record_failure(provider, str(e))

        # 2c. Piper (fallback local si Edge-TTS échoue en mode fast/premium)
        if (not success and self._tts_mode != "offline" and self.piper is not None
                and self.piper.is_available(effective_piper_model)):
            provider = "piper"
            audio_file = self.cache_dir / f"lumena_piper_utf8_v2_{piper_cache_tag}_{text_hash}.wav"
            try:
                if audio_file.exists():
                    success = True
                elif await self.piper.generate(
                    text, audio_file, model_name=effective_piper_model,
                ):
                    success = True
                if success:
                    latency_ms = (time.time() - start_time) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider} fallback): {audio_file}")
            except Exception as e:
                logger.warning(f"Piper fallback échoué: {e}")
                self.metrics.record_failure(provider, str(e))

        # 3. pyttsx3 (dernier recours — voix robotique Windows)
        if not success and self.enable_fallback and PYTTSX3_AVAILABLE:
            provider = "pyttsx3"
            self.metrics.record_fallback()
            # Start time resetté pour le fallback
            start_time_fallback = time.time()
            
            try:
                audio_file = await self._speak_pyttsx3(text)
                if audio_file:
                    success = True
                    latency_ms = (time.time() - start_time_fallback) * 1000
                    self.metrics.record_success(provider, latency_ms)
                    logger.debug(f"Audio généré ({provider} fallback): {audio_file}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"pyttsx3 fallback échoué: {error_msg}")
                self.metrics.record_failure(provider, error_msg)
        
        if not success:
            logger.error(f"❌ TTS: Aucun provider disponible pour le texte: {text[:50]}...")
            return None

        self._last_provider = provider  # exposé pour le statut V2 (LocalTTSAdapter)
        return audio_file

    async def speak_async(self, text: str) -> Optional[Path]:
        """Alias pour speak(wait=True)."""
        return await self.speak(text, wait=True)
    
    async def _speak_pyttsx3(self, text: str) -> Optional[Path]:
        """
        Fallback TTS avec pyttsx3 (offline).
        
        Note: pyttsx3 est synchrone, on l'exécute dans un thread.
        """
        if not PYTTSX3_AVAILABLE:
            return None
        
        import hashlib
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        audio_file = self.cache_dir / f"lumena_fallback_{text_hash}.wav"
        
        if audio_file.exists():
            return audio_file
        
        def _generate():
            try:
                engine = pyttsx3.init()
                # Configurer la voix française si disponible
                voices = engine.getProperty('voices')
                for voice in voices:
                    if 'french' in voice.name.lower() or 'fr' in voice.id.lower():
                        engine.setProperty('voice', voice.id)
                        break
                engine.save_to_file(text, str(audio_file))
                engine.runAndWait()
                return audio_file
            except Exception as e:
                logger.error(f"pyttsx3 erreur: {e}")
                return None
        
        # Exécuter dans un thread
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _generate)
        return result
    
    async def _speak_sentences(self, text: str) -> bool:
        """Joue le texte phrase par phrase (latence perçue réduite de ~40%).

        La 1ère phrase est jouée dès qu'elle est synthétisée au lieu d'attendre
        que TOUT le texte soit généré. Respecte _stop_speaking entre chaque phrase.
        Returns True si tout a joué, False si interrompu ou erreur.
        """
        import re as _re_sp
        import hashlib

        # Découper en phrases et regrouper les clauses trop courtes (< 25 chars)
        raw = _re_sp.split(r'(?<=[.!?…])\s+', text.strip())
        sentences: list = []
        buf = ""
        for s in raw:
            buf = (buf + " " + s).strip() if buf else s
            if len(buf) >= 25:
                sentences.append(buf)
                buf = ""
        if buf:
            if sentences:
                sentences[-1] += " " + buf
            else:
                sentences.append(buf)

        if not sentences:
            return False

        self._stop_speaking = False
        self.is_speaking = True
        try:
            import pygame
            for sentence in sentences:
                if self._stop_speaking:
                    return False
                sentence = sentence.strip()
                if not sentence:
                    continue

                h = hashlib.md5(sentence.encode()).hexdigest()[:8]
                audio_file = self.cache_dir / f"lumena_{h}.mp3"
                try:
                    if not audio_file.exists():
                        communicate = edge_tts.Communicate(
                            sentence, self.voice, rate=self.rate,
                            volume=self.volume, pitch=self.pitch,
                        )
                        await communicate.save(str(audio_file))

                    if self._stop_speaking:
                        return False

                    if not pygame.mixer.get_init():
                        pygame.mixer.init(frequency=22050, size=-16, channels=1)
                    pygame.mixer.music.load(str(audio_file))
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if self._stop_speaking:
                            pygame.mixer.music.stop()
                            return False
                        await asyncio.sleep(0.05)

                except Exception as e:
                    logger.warning(f"_speak_sentences: {e}")
                    return False
            return True
        finally:
            self.is_speaking = False

    async def speak_stream(self, text: str):
        """
        Génère et joue l'audio en streaming (plus rapide pour les longs textes).
        
        Args:
            text: Texte à prononcer
        """
        if not EDGE_TTS_AVAILABLE:
            return
        
        text = self._clean_text(text)
        
        # Fichier temporaire
        audio_file = self.cache_dir / f"lumena_stream_{os.getpid()}.mp3"
        
        try:
            communicate = edge_tts.Communicate(
                text,
                self.voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch
            )
            
            # Streaming vers fichier
            with open(audio_file, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
            
            # Jouer
            await self._play_audio(audio_file)
            
        except Exception as e:
            logger.error(f"Erreur TTS streaming: {e}")
        finally:
            # Nettoyer
            if audio_file.exists():
                try:
                    audio_file.unlink()
                except OSError:
                    pass  # fichier audio temp cleanup best-effort
    
    def _clean_text(self, text: str) -> str:
        """Nettoie le texte pour une meilleure prononciation."""
        import re
        
        # Supprimer les emojis (optionnel, edge-tts les ignore de toute façon)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+", 
            flags=re.UNICODE
        )
        text = emoji_pattern.sub(" ", text)
        
        # Nettoyer les espaces multiples
        text = re.sub(r'\s+', ' ', text)
        
        # Limiter la longueur
        if len(text) > 5000:
            text = text[:5000] + "..."
        
        return text.strip()
    
    def stop_speaking(self):
        """Interrompt immédiatement la lecture audio en cours (barge-in)."""
        self._stop_speaking = True
        try:
            import pygame
            if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
        except Exception:
            pass  # pygame stop best-effort
        if self._current_process:
            try:
                self._current_process.terminate()
            except Exception:
                pass  # process terminate best-effort

    async def _play_audio(self, audio_file: Path):
        """Joue un fichier audio."""
        if not audio_file.exists():
            return
        
        self.is_speaking = True
        self._stop_speaking = False
        
        # Timeout playback (Phase 4.6) - éviter blocage infini
        PLAYBACK_TIMEOUT_SECONDS = 120
        
        try:
            # Utiliser pygame si disponible, sinon ffplay
            try:
                import warnings
                warnings.filterwarnings("ignore", message="pkg_resources", category=DeprecationWarning)
                import pygame
                import time
                if not pygame.mixer.get_init():
                    # Piper = 22050Hz, 16-bit, Mono
                    pygame.mixer.init(frequency=22050, size=-16, channels=1)
                
                logger.debug(f"▶️ Lecture audio (pygame): {audio_file.name}")
                pygame.mixer.music.load(str(audio_file))
                pygame.mixer.music.play()
                
                # Boucle avec timeout + barge-in (Phase 4.6)
                start_time = time.time()
                while pygame.mixer.music.get_busy():
                    elapsed = time.time() - start_time
                    if elapsed > PLAYBACK_TIMEOUT_SECONDS:
                        logger.warning(f"⏱️ Timeout playback TTS après {PLAYBACK_TIMEOUT_SECONDS}s")
                        pygame.mixer.music.stop()
                        break
                    if self._stop_speaking:
                        pygame.mixer.music.stop()
                        logger.debug("⏹️ Lecture interrompue (barge-in)")
                        break
                    await asyncio.sleep(0.05)
                    
            except ImportError:
                # Fallback: ffplay (doit être installé)
                self._current_process = await asyncio.create_subprocess_exec(
                    "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_file),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                try:
                    await asyncio.wait_for(self._current_process.wait(), timeout=PLAYBACK_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning(f"⏱️ Timeout ffplay après {PLAYBACK_TIMEOUT_SECONDS}s")
                    self._current_process.terminate()
                
        except Exception as e:
            logger.error(f"Erreur lecture audio: {e}")
        finally:
            self.is_speaking = False
            self._current_process = None
    
    async def stop(self):
        """Arrête la lecture en cours."""
        if self._current_process:
            self._current_process.terminate()
            await self._current_process.wait()
        
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:
            pass  # pygame music stop best-effort
        
        self.is_speaking = False
    
    def set_voice(self, voice: str):
        """Change la voix."""
        self.voice = self.VOICES.get(voice, voice)
        logger.info(f"Voix changée: {self.voice}")
    
    def set_rate(self, rate: str):
        """Change la vitesse."""
        self.rate = rate
    
    def clear_cache(self):
        """Nettoie le cache des fichiers audio."""
        import shutil
        try:
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(exist_ok=True)
            logger.info("Cache TTS nettoyé")
        except Exception as e:
            logger.error(f"Erreur nettoyage cache: {e}")
    
    @staticmethod
    async def list_voices(language: str = "fr") -> list:
        """Liste les voix disponibles pour une langue."""
        if not EDGE_TTS_AVAILABLE:
            return []
        
        try:
            voices = await edge_tts.list_voices()
            filtered = [v for v in voices if v["Locale"].startswith(language)]
            return filtered
        except Exception as e:
            logger.error(f"Erreur listing voix: {e}")
            return []
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Retourne les métriques TTS.
        
        Utile pour diagnostiquer les problèmes de voix.
        """
        return self.metrics.to_dict()
    
    def get_status(self) -> Dict[str, Any]:
        """
        Retourne le statut complet du TTS.
        """
        return {
            "voice": self.voice,
            "is_speaking": self.is_speaking,
            "providers": {
                "edge_tts": EDGE_TTS_AVAILABLE,
                "pyttsx3": PYTTSX3_AVAILABLE,
            },
            "fallback_enabled": self.enable_fallback,
            "metrics": self.get_metrics()
        }


# Instance par défaut avec lock thread-safe (Phase 2.1)
import threading
_tts_instance: Optional[LumenaTTS] = None
_tts_lock = threading.Lock()


def get_tts() -> LumenaTTS:
    """Obtient l'instance singleton du TTS (thread-safe)."""
    global _tts_instance
    
    # Double-check locking pattern
    if _tts_instance is None:
        with _tts_lock:
            if _tts_instance is None:
                _tts_instance = LumenaTTS()
    return _tts_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
