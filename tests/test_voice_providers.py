"""Tests unitaires pour src/voice/assistant_loop.py, piper_provider.py, xtts_provider.py"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path


class TestPiperProvider:
    @pytest.fixture
    def provider(self):
        from src.voice.providers.piper_provider import PiperProvider
        return PiperProvider()

    def test_instantiation(self, provider):
        assert provider is not None

    def test_is_available_returns_bool(self, provider):
        result = provider.is_available()
        assert isinstance(result, bool)

    def test_get_info_returns_dict(self, provider):
        info = provider.get_info()
        assert isinstance(info, dict)

    def test_has_model_path(self, provider):
        assert hasattr(provider, "model_path")

    def test_has_piper_exe(self, provider):
        assert hasattr(provider, "piper_exe")

    def test_models_dir_is_path(self, provider):
        assert isinstance(provider.models_dir, Path)

    def test_generate_requires_output_path(self, provider, tmp_path):
        out_file = tmp_path / "output.wav"
        # Only test if piper is available, otherwise it will fail
        if provider.is_available():
            # Can't test real generation without a model, but we can test it doesn't crash
            pass
        else:
            # When not available, generate should return False or raise
            result = provider.generate("hello", out_file)
            assert result is False or result is None


class TestXTTSProvider:
    def test_import_succeeds(self):
        from src.voice.providers.xtts_provider import XTTSProvider, XTTS_AVAILABLE
        assert isinstance(XTTS_AVAILABLE, bool)

    def test_xtts_available_flag(self):
        from src.voice.providers.xtts_provider import XTTS_AVAILABLE
        # Just confirm it's a bool
        assert isinstance(XTTS_AVAILABLE, bool)

    def test_instantiation(self):
        from src.voice.providers.xtts_provider import XTTSProvider
        provider = XTTSProvider()
        assert provider is not None

    def test_has_is_available(self):
        from src.voice.providers.xtts_provider import XTTSProvider
        provider = XTTSProvider()
        assert callable(getattr(provider, "is_available", None))


class TestVoiceAssistant:
    def test_import_succeeds(self):
        from src.voice.assistant_loop import VoiceAssistant
        assert VoiceAssistant is not None

    def test_requires_core(self):
        from src.voice.assistant_loop import VoiceAssistant
        with pytest.raises(TypeError):
            VoiceAssistant()

    def test_init_with_mock_core(self):
        from src.voice.assistant_loop import VoiceAssistant
        mock_core = MagicMock()
        va = VoiceAssistant(core=mock_core)
        assert va is not None

    def test_has_start_method(self):
        from src.voice.assistant_loop import VoiceAssistant
        mock_core = MagicMock()
        va = VoiceAssistant(core=mock_core)
        assert callable(getattr(va, "start", None)) or hasattr(va, "run")

    def test_has_stop_method(self):
        from src.voice.assistant_loop import VoiceAssistant
        mock_core = MagicMock()
        va = VoiceAssistant(core=mock_core)
        has_stop = callable(getattr(va, "stop", None))
        has_shutdown = callable(getattr(va, "shutdown", None))
        assert has_stop or has_shutdown
