"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Grok Client
====================
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.markets.grok.client import GrokClient, GrokError, HTTPX_AVAILABLE
from src.markets.grok.schemas import (
    SignalDirection,
    SymbolSignal,
    GrokScanRequest,
    GrokScanResponse,
    GrokAnalysisRequest,
    MarketAnalysis,
)


class TestGrokClientInit:
    """Tests d'initialisation."""
    
    def test_default_init(self):
        """Initialisation par défaut."""
        client = GrokClient(api_key="test-key")
        
        assert client._api_key == "test-key"
        assert client._timeout == 30.0
        assert client._base_url == "https://api.x.ai"
    
    def test_custom_init(self):
        """Initialisation personnalisée."""
        client = GrokClient(
            api_key="custom-key",
            timeout=60.0,
            base_url="https://custom.api.com",
        )
        
        assert client._timeout == 60.0
        assert client._base_url == "https://custom.api.com"
    
    def test_model_constants(self):
        """Vérifie les constantes de modèles."""
        assert GrokClient.SCAN_MODEL == "grok-4-1-fast-non-reasoning"
        assert GrokClient.ANALYSIS_MODEL == "grok-4-1-fast-reasoning"
        assert GrokClient.BASE_URL == "https://api.x.ai"


class TestGrokClientCooldown:
    """Tests du cooldown."""
    
    def test_not_in_cooldown_initially(self):
        """Pas en cooldown initialement."""
        client = GrokClient(api_key="test-key")
        
        assert not client.is_in_cooldown
        assert client.cooldown_remaining_seconds == 0.0
    
    def test_activate_cooldown(self):
        """Activation du cooldown."""
        client = GrokClient(api_key="test-key")
        
        client._activate_cooldown()
        
        assert client.is_in_cooldown
        assert client.cooldown_remaining_seconds > 0
        assert client.cooldown_remaining_seconds <= 60
    
    def test_cooldown_expires(self):
        """Le cooldown expire après le délai."""
        client = GrokClient(api_key="test-key")
        
        # Simuler un cooldown passé
        client._cooldown_until = datetime.now() - timedelta(seconds=1)
        
        assert not client.is_in_cooldown
    
    def test_cooldown_blocks(self):
        """Le cooldown bloque les requêtes."""
        client = GrokClient(api_key="test-key")
        client._activate_cooldown()
        
        assert client._is_in_cooldown()


class TestGrokClientRateLimit:
    """Tests de la limite horaire."""
    
    def test_rate_limit_not_reached(self):
        """Limite pas atteinte initialement."""
        client = GrokClient(api_key="test-key")
        
        assert client.calls_remaining_hour == GrokClient.MAX_CALLS_PER_HOUR
    
    def test_rate_limit_tracking(self):
        """Suivi du compteur horaire."""
        client = GrokClient(api_key="test-key")
        
        # Simuler des appels
        client._hour_started = datetime.now()
        client._call_count_hour = 50
        
        assert client.calls_remaining_hour == 50
    
    def test_rate_limit_reset_after_hour(self):
        """Le compteur se reset après une heure."""
        client = GrokClient(api_key="test-key")
        
        # Simuler des appels d'il y a 2 heures
        client._hour_started = datetime.now() - timedelta(hours=2)
        client._call_count_hour = 100
        
        # Le check devrait reset le compteur
        assert client._check_rate_limit()
        assert client._call_count_hour == 0


class TestGrokClientStats:
    """Tests des statistiques."""
    
    def test_get_stats(self):
        """Récupère les statistiques."""
        client = GrokClient(api_key="test-key")
        
        stats = client.get_stats()
        
        assert "in_cooldown" in stats
        assert "cooldown_remaining_seconds" in stats
        assert "calls_this_hour" in stats
        assert "calls_remaining_hour" in stats
        assert "max_calls_per_hour" in stats
        assert "httpx_available" in stats
    
    def test_stats_reflect_state(self):
        """Les stats reflètent l'état actuel."""
        client = GrokClient(api_key="test-key")
        client._call_count_hour = 25
        client._hour_started = datetime.now()
        
        stats = client.get_stats()
        
        assert stats["calls_this_hour"] == 25
        assert stats["calls_remaining_hour"] == 75


class TestGrokClientValidation:
    """Tests de validation Pydantic."""
    
    def test_validate_response_success(self):
        """Validation réussie."""
        client = GrokClient(api_key="test-key")
        
        data = {
            "signals": [],
            "market_sentiment": "neutral",
        }
        
        result = client._validate_response(data, GrokScanResponse)
        
        assert isinstance(result, GrokScanResponse)
        assert result.market_sentiment == "neutral"
    
    def test_validate_response_with_signals(self):
        """Validation avec signaux."""
        client = GrokClient(api_key="test-key")
        
        data = {
            "signals": [
                {
                    "symbol": "AAPL",
                    "direction": "long",
                    "strength": "strong",
                    "confidence": 0.85,
                    "reason": "Technical breakout",
                }
            ],
            "market_sentiment": "bullish",
        }
        
        result = client._validate_response(data, GrokScanResponse)
        
        assert len(result.signals) == 1
        assert result.signals[0].symbol == "AAPL"
        assert result.signals[0].direction == SignalDirection.LONG
    
    def test_validate_response_failure(self):
        """Validation échoue sur données invalides."""
        client = GrokClient(api_key="test-key")
        
        data = {
            "signals": "not_a_list",  # Devrait être une liste
        }
        
        with pytest.raises(GrokError) as exc_info:
            client._validate_response(data, GrokScanResponse)
        
        assert exc_info.value.category == "format"
        assert not exc_info.value.retryable


class TestGrokClientMockedAPI:
    """Tests avec API mockée."""
    
    @pytest.fixture
    def client(self):
        """Crée un client Grok pour les tests."""
        return GrokClient(api_key="test-key")
    
    @pytest.mark.asyncio
    async def test_scan_in_cooldown_returns_none(self, client):
        """Scan pendant cooldown retourne None."""
        client._activate_cooldown()
        
        request = GrokScanRequest(symbols=["AAPL"])
        result = await client.scan(request)
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_analyze_in_cooldown_returns_none(self, client):
        """Analyze pendant cooldown retourne None."""
        client._activate_cooldown()
        
        signal = SymbolSignal(symbol="AAPL", direction=SignalDirection.LONG)
        request = GrokAnalysisRequest(symbol="AAPL", signal=signal)
        result = await client.analyze(request)
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_scan_rate_limit_exceeded_returns_none(self, client):
        """Scan avec limite horaire atteinte retourne None."""
        client._hour_started = datetime.now()
        client._call_count_hour = GrokClient.MAX_CALLS_PER_HOUR
        
        request = GrokScanRequest(symbols=["AAPL"])
        result = await client.scan(request)
        
        assert result is None


class TestGrokError:
    """Tests pour GrokError."""
    
    def test_create_error(self):
        """Création d'erreur basique."""
        error = GrokError("Test error")
        
        assert str(error) == "Test error"
        assert error.category == "unknown"
        assert not error.retryable
        assert error.status_code is None
    
    def test_create_rate_limit_error(self):
        """Création d'erreur rate limit."""
        error = GrokError(
            "Rate limit exceeded",
            category="rate_limit",
            retryable=True,
            status_code=429,
        )
        
        assert error.category == "rate_limit"
        assert error.retryable
        assert error.status_code == 429
    
    def test_create_format_error(self):
        """Création d'erreur de format."""
        error = GrokError(
            "JSON irréparable",
            category="format",
            retryable=False,
        )
        
        assert error.category == "format"
        assert not error.retryable


class TestGrokClientNoImportFromLLM:
    """Vérifie qu'il n'y a pas d'import de src/llm/."""
    
    def test_no_llm_import(self):
        """Le module grok n'importe pas de src/llm/."""
        import src.markets.grok.client as client_module
        import src.markets.grok.schemas as schemas_module
        import src.markets.grok.prompts as prompts_module
        
        # Vérifier les imports
        for module in [client_module, schemas_module, prompts_module]:
            source_file = module.__file__
            with open(source_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            assert "from src.llm" not in content
            assert "import src.llm" not in content
