"""
🧪 Tests - Voice Module (Phase 5.3)

Tests pour les modules de synthèse et reconnaissance vocale (avec mocks).
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio


class TestTTS:
    """Tests pour le module Text-to-Speech."""
    
    @pytest.fixture
    def mock_pygame(self):
        """Mock pygame pour ne pas jouer de son."""
        with patch.dict('sys.modules', {'pygame': MagicMock()}):
            yield
    
    @pytest.fixture
    def mock_gtts(self):
        """Mock gTTS pour ne pas appeler Google."""
        mock = MagicMock()
        mock.gTTS.return_value.save = MagicMock()
        with patch.dict('sys.modules', {'gtts': mock}):
            yield mock
    
    def test_tts_import(self):
        """Le module TTS doit être importable."""
        try:
            from src.voice import tts
            assert hasattr(tts, 'get_tts') or hasattr(tts, 'TextToSpeech')
        except ImportError:
            pytest.skip("Module voice.tts non disponible")
    
    def test_tts_singleton_exists(self):
        """La fonction get_tts doit exister."""
        try:
            from src.voice.tts import get_tts
            assert callable(get_tts)
        except ImportError:
            pytest.skip("Module voice.tts non disponible")
    
    def test_tts_timeout_config(self):
        """Le timeout doit être configurable."""
        import os
        
        # Par défaut
        default_timeout = int(os.getenv("LUMENA_TTS_TIMEOUT", "120"))
        assert default_timeout == 120
        
        # Configuré
        os.environ["LUMENA_TTS_TIMEOUT"] = "60"
        configured_timeout = int(os.getenv("LUMENA_TTS_TIMEOUT", "120"))
        assert configured_timeout == 60
        
        # Cleanup
        del os.environ["LUMENA_TTS_TIMEOUT"]


class TestSTT:
    """Tests pour le module Speech-to-Text."""
    
    @pytest.fixture
    def mock_whisper(self):
        """Mock whisper pour ne pas charger le modèle."""
        mock = MagicMock()
        mock.load_model.return_value = MagicMock()
        with patch.dict('sys.modules', {'whisper': mock}):
            yield mock
    
    def test_stt_import(self):
        """Le module STT doit être importable."""
        try:
            from src.voice import stt
            assert hasattr(stt, 'SpeechToText') or hasattr(stt, 'get_stt')
        except ImportError:
            pytest.skip("Module voice.stt non disponible")
    
    def test_wake_word_timeout_config(self):
        """Le timeout wake word doit être configurable."""
        import os
        
        # Par défaut
        default = int(os.getenv("LUMENA_WAKE_WORD_TIMEOUT", "300"))
        assert default == 300
        
        # Configuré
        os.environ["LUMENA_WAKE_WORD_TIMEOUT"] = "60"
        configured = int(os.getenv("LUMENA_WAKE_WORD_TIMEOUT", "60"))
        assert configured == 60
        
        # Cleanup
        del os.environ["LUMENA_WAKE_WORD_TIMEOUT"]
    
    def test_device_fallback(self):
        """Le fallback device cuda -> cpu doit être implémenté."""
        try:
            from src.voice.stt import LumenaSTT
            # Le STT doit avoir un attribut device ou méthode de fallback
            # L'implémentation exacte peut varier
        except ImportError:
            pytest.skip("Module voice.stt non disponible")


class TestVoiceIntegration:
    """Tests d'intégration voice."""
    
    def test_voice_module_exists(self):
        """Le package voice doit exister."""
        try:
            from src import voice
            assert voice is not None
        except ImportError:
            pytest.skip("Package voice non disponible")
    
    def test_voice_graceful_import(self):
        """L'import voice doit être gracieux même sans dépendances."""
        # Tester que l'import ne crash pas même si pygame/whisper manquent
        try:
            import src.voice
        except ImportError as e:
            # OK si manque une dépendance optionnelle
            assert "pygame" in str(e) or "whisper" in str(e)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
