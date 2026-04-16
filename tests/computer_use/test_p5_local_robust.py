"""Tests P5 — Mode local robuste : LOCAL_VALIDATED_MODELS, timeout Ollama, _ocr_fuzzy_find, config."""
import inspect
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ═══════════════════════════════════════════════════════════════════════════
#  P5.1 — LOCAL_VALIDATED_MODELS
# ═══════════════════════════════════════════════════════════════════════════

class TestLocalValidatedModels:
    def test_exists_and_is_dict(self):
        from src.llm.providers import LOCAL_VALIDATED_MODELS
        assert isinstance(LOCAL_VALIDATED_MODELS, dict)

    def test_has_required_categories(self):
        from src.llm.providers import LOCAL_VALIDATED_MODELS
        for cat in ("text", "vision", "code"):
            assert cat in LOCAL_VALIDATED_MODELS, f"catégorie '{cat}' manquante"

    def test_text_models_are_strings(self):
        from src.llm.providers import LOCAL_VALIDATED_MODELS
        for name in LOCAL_VALIDATED_MODELS["text"]:
            assert isinstance(name, str)
            assert len(name) > 0

    def test_vision_contains_known_models(self):
        from src.llm.providers import LOCAL_VALIDATED_MODELS
        vision = LOCAL_VALIDATED_MODELS["vision"]
        assert "minicpm-v" in vision
        assert "llava-llama3" in vision or "llava" in " ".join(vision)

    def test_code_models_also_in_text(self):
        """Les modèles code sont un sous-ensemble des modèles text."""
        from src.llm.providers import LOCAL_VALIDATED_MODELS
        text_set = set(LOCAL_VALIDATED_MODELS["text"])
        for m in LOCAL_VALIDATED_MODELS["code"]:
            assert m in text_set, f"code model '{m}' absent de text"

    def test_exported_from_init(self):
        from src.llm import LOCAL_VALIDATED_MODELS
        assert isinstance(LOCAL_VALIDATED_MODELS, dict)
        assert "vision" in LOCAL_VALIDATED_MODELS


# ═══════════════════════════════════════════════════════════════════════════
#  P5.2 — Timeout Ollama adaptatif
# ═══════════════════════════════════════════════════════════════════════════

class TestOllamaTimeoutAdaptive:
    def test_source_references_get_execution_mode(self):
        """analyze_with_ollama importe get_execution_mode pour adapter le timeout."""
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert "get_execution_mode" in source

    def test_local_timeout_60(self):
        """En mode local, le timeout Ollama est 60s."""
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert "60.0" in source
        assert "120.0" in source

    def test_timeout_branch_in_source(self):
        """Le code contient une branche local vs non-local."""
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert '"local"' in source or "'local'" in source


# ═══════════════════════════════════════════════════════════════════════════
#  P5.3 — _ocr_fuzzy_find
# ═══════════════════════════════════════════════════════════════════════════

class TestOcrFuzzyFind:
    def _make_vision_module(self):
        from src.computer_use.vision import VisionModule
        with patch("src.computer_use.vision.ScreenAnalyzer"):
            m = VisionModule.__new__(VisionModule)
            m.analyzer = MagicMock()
            m._provider_health = {}
            return m

    def test_method_exists(self):
        from src.computer_use.vision import VisionModule
        assert hasattr(VisionModule, "_ocr_fuzzy_find")

    def test_signature_returns_optional_tuple(self):
        """_ocr_fuzzy_find retourne Optional[Tuple[int, int]]."""
        import typing
        from src.computer_use.vision import VisionModule
        hints = typing.get_type_hints(VisionModule._ocr_fuzzy_find)
        assert "return" in hints
        ret = hints["return"]
        # Should be Optional[Tuple[int, int]] — check string repr
        ret_str = str(ret)
        assert "tuple" in ret_str.lower() or "Tuple" in ret_str

    @pytest.mark.asyncio
    async def test_returns_coords_on_good_match(self):
        """Match > 0.8 → retourne (x, y) centre de la région."""
        from src.computer_use.vision import TextRegion
        mod = self._make_vision_module()
        region = TextRegion(text="Valider", x=100, y=200, width=60, height=20, confidence=95)
        mod.analyzer.find_text_regions.return_value = [region]

        with patch("src.computer_use.vision.PIL_AVAILABLE", True), \
             patch("src.computer_use.vision.OCR_AVAILABLE", True), \
             patch("PIL.Image.open", return_value=MagicMock()):
            result = await mod._ocr_fuzzy_find("/tmp/test.png", "Valider")

        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == (130, 210)  # centre = (100+30, 200+10)

    @pytest.mark.asyncio
    async def test_returns_none_on_low_match(self):
        """Match < 0.8 → None."""
        from src.computer_use.vision import TextRegion
        mod = self._make_vision_module()
        region = TextRegion(text="Annuler", x=100, y=200, width=60, height=20, confidence=90)
        mod.analyzer.find_text_regions.return_value = [region]

        with patch("src.computer_use.vision.PIL_AVAILABLE", True), \
             patch("src.computer_use.vision.OCR_AVAILABLE", True), \
             patch("PIL.Image.open", return_value=MagicMock()):
            result = await mod._ocr_fuzzy_find("/tmp/test.png", "Confirmer le paiement")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_ocr(self):
        """OCR indisponible → None."""
        mod = self._make_vision_module()
        with patch("src.computer_use.vision.PIL_AVAILABLE", True), \
             patch("src.computer_use.vision.OCR_AVAILABLE", False):
            result = await mod._ocr_fuzzy_find("/tmp/test.png", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_regions(self):
        """Pas de régions OCR → None."""
        mod = self._make_vision_module()
        mod.analyzer.find_text_regions.return_value = []

        with patch("src.computer_use.vision.PIL_AVAILABLE", True), \
             patch("src.computer_use.vision.OCR_AVAILABLE", True), \
             patch("PIL.Image.open", return_value=MagicMock()):
            result = await mod._ocr_fuzzy_find("/tmp/test.png", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_fuzzy_match_with_ocr_typo(self):
        """OCR renvoie 'Validar' au lieu de 'Valider' — ratio > 0.8 → match."""
        from src.computer_use.vision import TextRegion
        mod = self._make_vision_module()
        region = TextRegion(text="Validar", x=50, y=80, width=40, height=16, confidence=85)
        mod.analyzer.find_text_regions.return_value = [region]

        with patch("src.computer_use.vision.PIL_AVAILABLE", True), \
             patch("src.computer_use.vision.OCR_AVAILABLE", True), \
             patch("PIL.Image.open", return_value=MagicMock()):
            result = await mod._ocr_fuzzy_find("/tmp/test.png", "Valider")

        # SequenceMatcher("valider", "validar").ratio() ≈ 0.857 > 0.8
        assert result is not None
        assert result == (70, 88)


# ═══════════════════════════════════════════════════════════════════════════
#  P5.3 — _find_element_with_ocr intègre fuzzy
# ═══════════════════════════════════════════════════════════════════════════

class TestFindElementWithOcrFuzzyIntegration:
    def test_source_calls_ocr_fuzzy_find(self):
        """_find_element_with_ocr appelle _ocr_fuzzy_find comme 3e stratégie."""
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule._find_element_with_ocr)
        assert "_ocr_fuzzy_find" in source


# ═══════════════════════════════════════════════════════════════════════════
#  P5.4 — Description mode local config.py
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigLocalDescription:
    def test_execution_mode_hint_mentions_ocr(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_EXECUTION_MODE"), None)
        assert entry is not None
        hint = entry.get("hint", "")
        assert "OCR" in hint

    def test_execution_mode_hint_mentions_navigation(self):
        """Le hint précise que la navigation web reste possible."""
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_EXECUTION_MODE"), None)
        assert entry is not None
        hint = entry.get("hint", "")
        assert "navigation" in hint.lower() or "web" in hint.lower()


# ═══════════════════════════════════════════════════════════════════════════
#  P5.5 — Contrats state/vision policy (renforcement P3)
# ═══════════════════════════════════════════════════════════════════════════

class TestStatePolicyLocalOcr:
    """Valide que build_state_policy retourne OCR en mode local."""

    def test_web_local_has_ocr(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            policy = build_state_policy("web")
        assert "ocr" in policy
        assert "dom" in policy

    def test_desktop_local_has_ocr(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            policy = build_state_policy("desktop")
        assert "ocr" in policy
        assert "uia" in policy

    def test_vision_policy_local_no_ocr(self):
        """build_vision_policy NE contient JAMAIS ocr (invariant P3)."""
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            policy = build_vision_policy("vision_describe")
        assert "ocr" not in policy
        assert policy == ["ollama"]
