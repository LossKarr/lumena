"""
🌟 LUMENA - Speech-to-Text (STT)

Module pour écouter et transcrire la voix de l'utilisateur.
Utilise faster-whisper pour la transcription locale.
"""

import asyncio
import os
import sys
import tempfile
import threading
import wave
import io
import numpy as np
from pathlib import Path
from typing import Optional, Callable, Generator
from threading import Thread, Event
from loguru import logger

# Vérifier les dépendances
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    logger.warning("pyaudio non installé. Installez avec: pip install pyaudio")

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
    
    # Phase 4.10.1: Fix CUDA DLL Loading on Windows
    if os.name == 'nt':
        import site
        # Base du venv
        venv_base = Path(sys.executable).parent.parent
        site_packages = venv_base / "Lib" / "site-packages"
        
        # Chemins des DLL NVIDIA (depuis les paquets pip)
        cuda_paths = [
            site_packages / "nvidia" / "cublas" / "bin",
            site_packages / "nvidia" / "cudnn" / "bin",
            # Fallback pour certains environnements
            venv_base / "Scripts",
            Path(os.environ.get("CUDA_PATH", "")) / "bin"
        ]
        
        for path in cuda_paths:
            if path and path.exists():
                logger.debug(f"🌟 Adding DLL directory to search path: {path}")
                try:
                    os.add_dll_directory(str(path))
                except Exception:
                    pass  # DLL directory ajout best-effort
                # Ajouter au PATH pour les bibliothèques qui ne respectent pas add_dll_directory
                os.environ["PATH"] = str(path) + os.pathsep + os.environ["PATH"]

except ImportError:
    WHISPER_AVAILABLE = False
    logger.warning("faster-whisper non installé. Installez avec: pip install faster-whisper")


class LumenaSTT:
    """
    Système Speech-to-Text pour LUMENA.
    
    Utilise faster-whisper pour une transcription locale rapide et précise.
    """
    
    # Configuration audio
    SAMPLE_RATE = 16000
    CHUNK_SIZE = 1024
    CHANNELS = 1
    FORMAT = 8  # pyaudio.paInt16 = 8
    
    def __init__(
        self,
        model_size: str = os.getenv("LUMENA_STT_MODEL", "small"),
        device: str = os.getenv("LUMENA_STT_DEVICE", "cuda"),
        compute_type: str = os.getenv("LUMENA_STT_COMPUTE", "float16"),
        language: str = "fr",
    ):
        """
        Initialise le STT.
        
        Args:
            model_size: Taille du modèle Whisper
            device: Device pour l'inférence (cuda/cpu)
            compute_type: Type de calcul (float16/int8/float32)
            language: Langue de transcription
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        
        # Modèle Whisper
        self.model: Optional[WhisperModel] = None
        
        # État d'enregistrement
        self.is_recording = False
        self.is_listening = False
        self._stop_event = Event()
        self._stt_lock = threading.Lock()
        self._audio_data = []
        
        # Callbacks
        self._on_transcription: Optional[Callable[[str], None]] = None
        
        # Phase 4.12: Moteur de Flux Circulaire (Alpha-Continu)
        self.energy_threshold = 300
        self.ambient_noise_calibrated = False
        self._circular_buffer = np.zeros(self.SAMPLE_RATE * 6, dtype=np.int16)  # 6s buffer
        self._max_buffer_len = self.SAMPLE_RATE * 6  # 6 secondes (phrases longues)
        
        # Fichiers temporaires
        self.temp_dir = Path(tempfile.gettempdir()) / "lumena_stt"
        self.temp_dir.mkdir(exist_ok=True)
        
        logger.info(f"STT initialisé (modèle: {model_size}, device: {device})")
    
    async def calibrate(self, duration: float = 1.0):
        """Calibre le seuil de bruit ambiant."""
        if not SR_AVAILABLE:
            return
            
        logger.info(f"🎤 Calibration du bruit ambiant ({duration}s)...")
        loop = asyncio.get_running_loop()
        try:
            mic_index = os.getenv("LUMENA_STT_MIC_INDEX")
            if mic_index is not None: mic_index = int(mic_index)
            
            r = sr.Recognizer()
            def _do_calibrate():
                with sr.Microphone(device_index=mic_index) as source:
                    r.adjust_for_ambient_noise(source, duration=duration)
                    return r.energy_threshold
            
            _stt_mult = float(os.environ.get("LUMENA_STT_CALIBRATION_MULTIPLIER", "2.5"))
            self.energy_threshold = await loop.run_in_executor(None, _do_calibrate) * _stt_mult
            self.ambient_noise_calibrated = True
            logger.info(f"✅ Calibration terminée. Seuil: {self.energy_threshold:.1f}")
        except Exception as e:
            logger.warning(f"⚠️ Échec calibration: {e}")
            
    def normalize_audio(self, audio_data: bytes) -> bytes:
        """Normalise le volume audio (Bias + Multiplier)."""
        import audioop
        try:
            # RMS actuel
            rms = audioop.rms(audio_data, 2)
            if rms < 10: return audio_data
            
            # Cible 2000 RMS (niveau confortable pour Whisper)
            target = 2000
            gain = target / max(rms, 1)
            gain = min(gain, 8.0) # Max 8x pour éviter la saturation du bruit
            
            return audioop.mul(audio_data, 2, gain)
        except Exception:
            return audio_data  # gain non applicable, retour audio brut
        
    def load_model(self):
        """Charge le modèle Whisper."""
        if not WHISPER_AVAILABLE:
            logger.error("faster-whisper non disponible")
            return False
        
        if self.model is not None:
            return True
        
        # Phase 4.5: Boucle itérative au lieu de récursion pour fallback
        devices_to_try = [(self.device, self.compute_type)]
        if self.device == "cuda":
            devices_to_try.append(("cpu", "float32"))
        
        for device, compute_type in devices_to_try:
            try:
                logger.info(f"Chargement du modèle Whisper {self.model_size} ({device})...")
                self.model = WhisperModel(
                    self.model_size,
                    device=device,
                    compute_type=compute_type
                )
                self.device = device
                self.compute_type = compute_type
                logger.info(f"Modèle Whisper chargé ! (device: {device})")
                return True
            except Exception as e:
                logger.error(f"Erreur chargement modèle ({device}): {e}")
                continue
        
        return False
    
    def _calculate_energy(self, audio_path: str) -> float:
        """Calcule l'énergie moyenne de l'audio pour détecter le silence."""
        try:
            with wave.open(audio_path, 'rb') as wf:
                params = wf.getparams()
                frames = wf.readframes(params.nframes)
                
                # Utiliser audioop si disponible (plus rapide)
                try:
                    import audioop
                    return audioop.rms(frames, 2)
                except ImportError:
                    # Fallback manuel simple
                    import struct
                    samples = struct.unpack(f"{len(frames)//2}h", frames)
                    if not samples: return 0
                    return sum(abs(s) for s in samples) / len(samples)
        except Exception:
            return 1000  # Par défaut, on considère qu'il y a du son en cas d'erreur
            
    async def transcribe_file(self, audio_path: str) -> str:
        """
        Transcrit un fichier audio.
        
        Args:
            audio_path: Chemin vers le fichier audio
            
        Returns:
            Texte transcrit
        """
        # Vérifier l'énergie avant de charger le modèle
        energy = self._calculate_energy(audio_path)
        logger.debug(f"🔉 Niveau sonore détecté: {energy:.1f} (seuil: {self.energy_threshold:.1f})")
        if energy < self.energy_threshold:
            logger.debug(f"🔇 Silence détecté, transcription sautée.")
            return ""
        
        # Phase 4.10.3: Normalisation Bloom-style pour Whisper
        try:
            with wave.open(audio_path, 'rb') as wf:
                params = wf.getparams()
                frames = wf.readframes(params.nframes)
            
            norm_frames = self.normalize_audio(frames)
            
            with wave.open(audio_path, 'wb') as wf:
                wf.setparams(params)
                wf.writeframes(norm_frames)
        except Exception as e:
            logger.debug(f"⚠️ Normalisation skip: {e}")
            
        logger.info(f"🎤 Son détecté (niveau: {energy:.1f}), analyse en cours...")

        if not self.load_model():
            return ""
        
        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=self.language,
                beam_size=5
            )
            text = " ".join([segment.text for segment in segments]).strip()
            return self._clean_elite(text)
        except Exception as e:
            logger.error(f"Erreur transcription: {e}")
            return ""

    async def transcribe_memory(self, audio_bytes: bytes, fast: bool = True) -> str:
        """Transcrit l'audio directement depuis la mémoire (Vitesse Alpha)."""
        if not self.load_model(): return ""
        try:
            # Conversion optimisée
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Paramètres Alpha
            beam_size = 1 if fast else 5
            initial_prompt = (
                "Lumena, Luména, Lumi, ouvre, ferme, écris, cherche, "
                "dis-moi, aide-moi, analyse, joue, arrête, démarre, crée, montre."
            ) if fast else None
            
            segments, info = self.model.transcribe(
                audio_np, 
                language=self.language, 
                beam_size=beam_size,
                initial_prompt=initial_prompt,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join([s.text for s in segments]).strip()
            return self._clean_elite(text)
        except Exception as e:
            logger.error(f"Erreur transcription mémoire: {e}")
            return ""

    def _clean_elite(self, text: str) -> str:
        """Nettoyage Elite des hallucinations Whisper."""
        if not text: return ""
        text_lower = text.lower().strip()
        # Hallucinations connues — rejet si le texte CONTIENT l'une d'elles
        hallus = [
            "merci d'avoir regardé", "sous-titres", "sous titres",
            "merci de votre écoute", "amara.org", "sous-titrage",
            "merci d'avoir écouté", "merci pour votre",
            "abonnez-vous", "aimez cette vidéo",
            "la communauté d'amara", "réalisés par la communauté",
        ]
        for h in hallus:
            if h in text_lower:
                logger.debug(f"⚠️ Hallucination Whisper rejetée: '{text}'")
                return ""
        # Texte trop court pour être une vraie commande
        if len(text_lower) < 2:
            return ""
        return text.strip()

    def check_similarity(self, word: str, target: str) -> float:
        """Calcule la similarité Elite (Phonétique + SeqMatcher)."""
        import difflib
        word, target = word.lower().strip(), target.lower().strip()
        if word == target: return 1.0
        def phonetic(s):
            for char in "aeiouyhw": s = s.replace(char, "")
            return s
        if phonetic(word) == phonetic(target) and len(word) > 2: return 0.95
        return difflib.SequenceMatcher(None, word, target).ratio()
    
    def start_recording(self) -> bool:
        """
        Démarre l'enregistrement audio.
        
        Returns:
            True si l'enregistrement a démarré
        """
        if not PYAUDIO_AVAILABLE:
            logger.error("pyaudio non disponible")
            return False
        
        if self.is_recording:
            return True
        
        self._stop_event.clear()
        self._audio_data = []
        
        def record_thread():
            import pyaudio
            
            pa = pyaudio.PyAudio()
            
            # Phase 4.8: Support micro spécifique
            mic_index = os.getenv("LUMENA_STT_MIC_INDEX")
            if mic_index is not None:
                mic_index = int(mic_index)
                logger.debug(f"🎙️ Utilisation du micro index: {mic_index}")

            stream = pa.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.SAMPLE_RATE,
                input=True,
                input_device_index=mic_index,
                frames_per_buffer=self.CHUNK_SIZE
            )
            
            self.is_recording = True
            logger.info("Enregistrement démarré...")
            
            while not self._stop_event.is_set():
                try:
                    data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                    with self._stt_lock:
                        self._audio_data.append(data)
                    
                    # Phase 4.9: Monitoring temps réel toutes les ~0.5s
                    if len(self._audio_data) % 5 == 0:
                        import audioop
                        energy = audioop.rms(data, 2)
                        if energy > self.energy_threshold:
                            logger.debug(f"🎙️ Son détecté! (niveau: {energy:.1f} > seuil: {self.energy_threshold:.1f})")
                except Exception as e:
                    logger.error(f"Erreur enregistrement: {e}")
                    break
            
            stream.stop_stream()
            stream.close()
            pa.terminate()
            self.is_recording = False
            logger.info("Enregistrement arrêté")
        
        Thread(target=record_thread, daemon=True).start()
        return True
    
    async def stop_recording_and_transcribe(self) -> str:
        """
        Arrête l'enregistrement et transcrit l'audio.
        
        Returns:
            Texte transcrit
        """
        self._stop_event.set()
        
        # Attendre la fin de l'enregistrement
        await asyncio.sleep(0.2)
        
        if not self._audio_data:
            return ""
        
        # Sauvegarder en WAV
        audio_file = self.temp_dir / "recording.wav"
        
        try:
            with wave.open(str(audio_file), 'wb') as wf:
                wf.setnchannels(self.CHANNELS)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(self.SAMPLE_RATE)
                # Normalisation avant sauvegarde
                frames = b''.join(self._audio_data)
                frames = self.normalize_audio(frames)
                wf.writeframes(frames)
            
            # Transcrire
            text = await self.transcribe_file(str(audio_file))
            
            # Callback
            if self._on_transcription and text:
                self._on_transcription(text)
            
            return text
            
        finally:
            # Nettoyer
            if audio_file.exists():
                audio_file.unlink()
            self._audio_data = []
    
    def stop_recording(self):
        """Arrête l'enregistrement proprement."""
        self._stop_event.set()
        self.is_recording = False

    async def listen_once(self, timeout: float = 5.0) -> str:
        """
        Écoute une commande unique de manière Élite (VAD + Auto-stop).
        """
        # Sécurité: arrêter le fond sonore si actif
        was_listening = self.is_listening
        self.stop_listening()
        self.stop_recording()
        await asyncio.sleep(0.1)

        if not SR_AVAILABLE:
            return await self._listen_once_legacy(timeout)

        logger.info(f"👂 Écoute de la commande (max {timeout}s)...")
        
        r = sr.Recognizer()
        r.energy_threshold = self.energy_threshold
        r.dynamic_energy_threshold = False   # seuil fixe — on a déjà calibré
        r.pause_threshold = 1.0              # 1.0s de silence = fin phrase (réactif, anti-écho TTS)
        r.non_speaking_duration = 0.4        # 0.4s de silence minimum avant coupure
        
        mic_index = os.getenv("LUMENA_STT_MIC_INDEX")
        if mic_index: mic_index = int(mic_index)
        
        loop = asyncio.get_running_loop()
        try:
            with sr.Microphone(device_index=mic_index) as source:
                # Écouter jusqu'au silence ou timeout total
                audio_data = await loop.run_in_executor(
                    None, 
                    lambda: r.listen(source, timeout=timeout, phrase_time_limit=timeout)
                )
                
                # Transcription ultra-précise (beam_size=5 pour la commande)
                raw_data = audio_data.get_raw_data()
                
                if not self.load_model(): return ""
                audio_np = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
                
                # Vérifier que l'audio n'est pas juste du silence/bruit
                energy = np.sqrt(np.mean((audio_np * 32768.0)**2))
                if energy < self.energy_threshold * 0.5:
                    logger.debug(f"🔇 Audio trop faible ({energy:.0f}), ignoré")
                    return ""
                
                segments, _ = self.model.transcribe(
                    audio_np,
                    language=self.language,
                    beam_size=2,          # 2 = équilibre vitesse/précision (~300ms gagnés vs beam=5)
                    initial_prompt=(
                        "Lumena, Luména, ouvre, ferme, écris, cherche, "
                        "dis-moi, aide-moi, analyse, arrête, démarre, crée."
                    ),
                    condition_on_previous_text=False,
                    vad_filter=True,
                )
                text = " ".join([s.text for s in segments]).strip()
                
                final_text = self._clean_elite(text)
                if final_text:
                    logger.info(f"📝 Commande entendue: {final_text}")
                return final_text

        except Exception as e:
            logger.debug(f"Interruption ou silence: {e}")
            return ""

    async def _listen_once_legacy(self, timeout):
        """Fallback si SR non dispo."""
        if not self.start_recording(): return ""
        await asyncio.sleep(timeout)
        return await self.stop_recording_and_transcribe()
    
    def on_transcription(self, callback: Callable[[str], None]):
        """Enregistre un callback pour les transcriptions."""
        self._on_transcription = callback
    
    async def detect_wake_word(
        self, 
        wake_words: list = ["lumena", "luména", "lumi", "lumina", "hey lumena", "ok lumena"],
        callback: Optional[Callable[[], None]] = None,
        max_listen_seconds: int = None
    ) -> Optional[str]:
        """
        Détection Alpha-Continu (Zéro-Latence, Zéro-Gaps).
        
        Retourne:
            None      → wake word non détecté (timeout)
            ""        → wake word seul, aucune commande dans le chunk
            "texte"   → wake word + commande capturée dans le même souffle
                        (ex: "Lumena ouvre le projet" → retourne "ouvre le projet")
        """
        if max_listen_seconds is None:
            max_listen_seconds = int(os.getenv("LUMENA_WAKE_WORD_TIMEOUT", "3600"))

        if not self.ambient_noise_calibrated:
            await self.calibrate(duration=1.0)

        self.is_listening = True
        self._circular_buffer = np.zeros(self._max_buffer_len, dtype=np.int16)
        
        # Démarrer le flux PyAudio en mode continu
        if not self.start_recording(): return False
        
        logger.info(f"🎙️ [ALPHA] Écoute continue activée (Seuil: {self.energy_threshold:.1f})")
        
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        # VAD end-of-utterance (inspiré OpenAI Realtime API)
        # On attend que la parole S'ARRÊTE avant de transcrire (évite de couper les phrases longues)
        _speech_started_at: Optional[float] = None   # quand la voix a commencé
        _last_energy_high: Optional[float] = None    # dernier chunk actif
        _speech_chunks: list = []                     # buffer croissant — capte les phrases longues (>6s)
        SILENCE_CUTOFF = 0.8    # s de silence = fin d'élocution (OpenAI default: 0.5-1.0s)
        MIN_SPEECH_DURATION = 0.3  # 0.3s — capture "oui", "non", "stop" (était 0.6s, trop restrictif)
        
        try:
            while self.is_listening:
                current_time = loop.time()
                if (current_time - start_time) > max_listen_seconds: break
                
                # Cycle rapide 0.3s pour être réactif à la fin de parole
                await asyncio.sleep(0.3)
                
                # Récupérer les nouveaux chunks depuis record_thread
                with self._stt_lock:
                    if not self._audio_data: 
                        # Aucun nouveau chunk — vérifier si silence prolongé après parole
                        if _speech_started_at and _last_energy_high:
                            silence_duration = loop.time() - _last_energy_high
                            if silence_duration >= SILENCE_CUTOFF:
                                # Fin d'élocution détectée → transcrire maintenant
                                pass  # sort du if, tombe dans la transcription ci-dessous
                            else:
                                continue
                        else:
                            continue
                        new_bytes = b""
                        new_samples = np.array([], dtype=np.int16)
                    else:
                        new_bytes = b"".join(self._audio_data)
                        self._audio_data = []  # Vider pour le prochain cycle
                        new_samples = np.frombuffer(new_bytes, dtype=np.int16)
                
                if len(new_samples) > 0:
                    # Mettre à jour le buffer circulaire
                    self._circular_buffer = np.roll(self._circular_buffer, -len(new_samples))
                    self._circular_buffer[-len(new_samples):] = new_samples
                    
                    # Gating d'énergie (sur les nouveaux échantillons)
                    energy = np.sqrt(np.mean(new_samples.astype(np.float32)**2))
                    
                    if energy >= self.energy_threshold:
                        # Parole active — accumuler dans le buffer dédié
                        if _speech_started_at is None:
                            _speech_started_at = loop.time()
                            _speech_chunks = []  # reset à chaque début de prise de parole
                        _last_energy_high = loop.time()
                        _speech_chunks.append(new_bytes)  # accumule toute la parole
                        continue  # Attendre la fin de la phrase
                    else:
                        # Silence — vérifier si fin d'élocution
                        if _speech_started_at is None:
                            continue  # Pas encore de parole
                        if new_bytes:                              # inclure les frames de silence terminal
                            _speech_chunks.append(new_bytes)
                        silence_duration = loop.time() - _last_energy_high
                        if silence_duration < SILENCE_CUTOFF:
                            continue  # Pause trop courte (respiration, hésitation)
                        # Vérifier durée minimale de parole
                        speech_duration = _last_energy_high - _speech_started_at
                        if speech_duration < MIN_SPEECH_DURATION:
                            # Bruit parasite trop court — reset
                            _speech_started_at = None
                            _last_energy_high = None
                            _speech_chunks = []
                            continue

                # Fin d'élocution confirmée → transcrire le buffer complet
                _speech_started_at = None
                _last_energy_high = None

                # Transcription Alpha — buffer croissant (gère les phrases >6s), fallback circulaire
                _raw = b"".join(_speech_chunks) if _speech_chunks else self._circular_buffer.tobytes()
                _speech_chunks = []
                text = await self.transcribe_memory(_raw, fast=True)
                if not text: continue
                
                logger.debug(f"👂 [ALPHA] '{text}'")
                
                # Match Elite
                text_clean = "".join(c for c in text.lower() if c.isalnum() or c.isspace())
                words = text_clean.split()
                
                targets = wake_words + ["luména", "lumi", "lumina", "hey lumena", "ok lumena"]
                for target in targets:
                    for i, w in enumerate(words):
                        if self.check_similarity(w, target) > 0.85:
                            # Extraire tout ce qui suit le wake word dans le même chunk
                            trailing = " ".join(words[i+1:]).strip()
                            trailing = self._clean_elite(trailing)
                            # Rejeter les trailing d'un seul mot très court (artefact Whisper)
                            trailing_words = trailing.split()
                            if len(trailing_words) == 1 and len(trailing_words[0]) <= 3:
                                logger.debug(f"⚠️ Trailing trop court ignoré: '{trailing}' — attend la suite...")
                                trailing = ""
                            logger.info(f"🚀 [ELITE] Déclenchement par '{target}' — trailing: '{trailing}'")
                            if callback: callback()
                            self.stop_listening()
                            return trailing  # "" si mot seul, "commande" si phrase complète
        finally:
            self.stop_listening()

        return None

    async def _detect_legacy(self, wake_words, callback, max_listen_seconds):
        """Fallback si SR n'est pas là."""
        # Garder une version simplifiée de l'ancien code si besoin
        await asyncio.sleep(1.0)
        return False
    
    def stop_listening(self):
        """Arrête l'écoute continue."""
        self.is_listening = False
        self._stop_event.set()
        self.is_recording = False

    async def detect_speech_onset(self, timeout: float = 0.5) -> bool:
        """Détecte un début de parole pendant que Lumena parle (barge-in).

        Lance un court enregistrement PyAudio et vérifie si l'énergie
        dépasse le seuil ambiant.  Rapide : pas de transcription, juste
        un gate d'énergie.

        Returns:
            True si de la voix humaine est détectée.
        """
        import pyaudio

        pa = pyaudio.PyAudio()
        mic_index = os.getenv("LUMENA_STT_MIC_INDEX")
        if mic_index is not None:
            mic_index = int(mic_index)

        try:
            stream = pa.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.SAMPLE_RATE,
                input=True,
                input_device_index=mic_index,
                frames_per_buffer=self.CHUNK_SIZE,
            )

            chunks_needed = int(self.SAMPLE_RATE * timeout / self.CHUNK_SIZE)
            for _ in range(max(chunks_needed, 1)):
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                energy = np.sqrt(np.mean(samples ** 2))
                if energy > self.energy_threshold * 1.6:
                    stream.stop_stream()
                    stream.close()
                    pa.terminate()
                    return True

            stream.stop_stream()
            stream.close()
        except Exception as e:
            logger.debug(f"detect_speech_onset error: {e}")
        finally:
            pa.terminate()
        return False

    def _listen_barge_in_sync(
        self,
        timeout: float = 10.0,
        silence_cutoff: float = 0.8,
        human_factor: float = 2.5,
        stop_event=None,
    ) -> str:
        """Écoute en parallèle du TTS — capture la parole humaine au-dessus du fond.

        Mesure d'abord l'énergie de fond (haut-parleurs + ambiant), puis détecte
        un signal nettement plus fort. Capture jusqu'à `silence_cutoff` secondes
        de silence, puis transcrit. Synchrone — à exécuter dans un executor.
        """
        import pyaudio

        pa = pyaudio.PyAudio()
        mic_index = os.getenv("LUMENA_STT_MIC_INDEX")
        if mic_index is not None:
            mic_index = int(mic_index)

        speech_buffer = []
        try:
            stream = pa.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.SAMPLE_RATE,
                input=True,
                input_device_index=mic_index,
                frames_per_buffer=self.CHUNK_SIZE,
            )

            chunks_per_sec = max(1, self.SAMPLE_RATE // self.CHUNK_SIZE)
            CALIB = 15  # ~0.45s de calibration du fond
            MIN_SPEECH = 3  # chunks consécutifs minimum pour valider

            # Phase 1 : calibration fond ambiant (TTS inclus)
            bg_levels = []
            for _ in range(CALIB):
                if stop_event and stop_event.is_set():
                    stream.stop_stream()
                    stream.close()
                    return ""
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                s = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                bg_levels.append(float(np.sqrt(np.mean(s ** 2))) + 1.0)

            bg_mean = float(np.mean(bg_levels)) if bg_levels else float(self.energy_threshold)
            # On utilise uniquement le fond mesuré (TTS inclus) × human_factor.
            # On supprime le plancher energy_threshold*1.2 qui bloquait la détection
            # en environnement bruyant (ex: seuil calibré 389 → plancher 467).
            speech_threshold = max(bg_mean * human_factor, 40.0)
            logger.debug(f"🎤 barge-in: fond={bg_mean:.0f}, seuil={speech_threshold:.0f}")

            # Phase 2 : détection + capture continue
            max_chunks = int(timeout * chunks_per_sec)
            silence_limit = max(1, int(silence_cutoff * chunks_per_sec))
            consecutive_high = 0
            consecutive_low = 0
            in_speech = False

            for _ in range(max_chunks):
                if stop_event and stop_event.is_set():
                    break
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                s = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                energy = float(np.sqrt(np.mean(s ** 2)))

                if energy > speech_threshold:
                    consecutive_high += 1
                    consecutive_low = 0
                    if consecutive_high >= MIN_SPEECH:
                        in_speech = True
                else:
                    consecutive_low += 1
                    consecutive_high = 0

                if in_speech:
                    speech_buffer.append(data)
                    if consecutive_low >= silence_limit:
                        break

            stream.stop_stream()
            stream.close()

        except Exception as e:
            logger.debug(f"listen_barge_in error: {e}")
            return ""
        finally:
            try:
                pa.terminate()
            except Exception:
                pass  # PyAudio terminate best-effort

        if not speech_buffer or len(speech_buffer) < 3:
            return ""

        if not self.load_model():
            return ""

        raw = b"".join(speech_buffer)
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        energy = float(np.sqrt(np.mean((audio_np * 32768.0) ** 2)))
        if energy < float(self.energy_threshold) * 0.3:
            return ""

        try:
            segments, _ = self.model.transcribe(
                audio_np,
                language=self.language,
                beam_size=5,
                initial_prompt=(
                    "Lumena, Luména, ouvre, ferme, écris, cherche, "
                    "dis-moi, aide-moi, analyse, arrête, démarre, crée."
                ),
                condition_on_previous_text=False,
                vad_filter=True,
            )
            text = " ".join([seg.text for seg in segments]).strip()
            result = self._clean_elite(text)
            if result:
                logger.info(f"🎤 Barge-in capturé: '{result}'")
            return result
        except Exception as e:
            logger.debug(f"barge-in transcription error: {e}")
            return ""

    async def listen_barge_in(self, timeout: float = 10.0) -> str:
        """Écoute la parole humaine en parallèle du TTS (async via executor)."""
        loop = asyncio.get_running_loop()
        stop_ev = threading.Event()
        self._barge_in_stop_event = stop_ev
        try:
            return await loop.run_in_executor(
                None,
                lambda: self._listen_barge_in_sync(timeout=timeout, stop_event=stop_ev),
            )
        finally:
            self._barge_in_stop_event = None

    def stop_barge_in(self):
        """Signale à listen_barge_in de s'arrêter proprement."""
        ev = getattr(self, "_barge_in_stop_event", None)
        if ev:
            ev.set()


# Instance par défaut avec lock thread-safe (Phase 2.1)
_stt_instance: Optional[LumenaSTT] = None
_stt_lock = threading.Lock()


def get_stt() -> LumenaSTT:
    """Obtient l'instance singleton du STT (thread-safe)."""
    global _stt_instance
    
    # Double-check locking pattern
    if _stt_instance is None:
        with _stt_lock:
            if _stt_instance is None:
                _stt_instance = LumenaSTT()
    return _stt_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
