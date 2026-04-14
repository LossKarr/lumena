"""
Tests pour les erreurs Market Sentinel.

Vérifie:
1. Héritage LumenaError
2. Catégorisation des erreurs
3. Flag retryable
4. Codes d'erreur IBKR
5. Status codes Grok
"""

import pytest

from src.markets.errors import (
    MarketError,
    IBKRError,
    GrokError,
    MARKET_ERROR_CATEGORIES,
    is_retryable_market_error,
)


class TestMarketError:
    """Tests pour la classe MarketError de base."""
    
    def test_inheritance_from_lumena_error(self):
        """Vérifie l'héritage depuis LumenaError."""
        error = MarketError("Test error")
        
        # Doit avoir les attributs de LumenaError
        assert hasattr(error, "category")
        assert hasattr(error, "retryable")
        assert hasattr(error, "original")
    
    def test_default_values(self):
        """Vérifie les valeurs par défaut."""
        error = MarketError("Test error")
        
        assert error.category == "internal"
        assert error.retryable is False
        assert error.original is None
        assert str(error) == "Test error"
    
    def test_custom_values(self):
        """Vérifie les valeurs personnalisées."""
        original = ValueError("Original error")
        error = MarketError(
            "Custom error",
            category="network",
            retryable=True,
            original=original,
        )
        
        assert error.category == "network"
        assert error.retryable is True
        assert error.original is original
    
    def test_all_categories_valid(self):
        """Vérifie que toutes les catégories sont documentées."""
        valid_categories = set(MARKET_ERROR_CATEGORIES.keys())
        
        for category in ["auth", "rate_limit", "timeout", "network", "format", "config", "internal"]:
            assert category in valid_categories


class TestIBKRError:
    """Tests pour IBKRError."""
    
    def test_basic_creation(self):
        """Test création basique."""
        error = IBKRError("Connection lost", error_code=502)
        
        assert error.error_code == 502
        assert "[IBKR-502]" in str(error)
    
    def test_retryable_codes(self):
        """Vérifie les codes retryable."""
        # Code 162 = pacing violation = retryable
        error = IBKRError("Pacing violation", error_code=162)
        assert error.retryable is True
        assert error.category == "rate_limit"
        
        # Code 1100 = connectivity lost = retryable
        error = IBKRError("Connectivity lost", error_code=1100)
        assert error.retryable is True
    
    def test_non_retryable_codes(self):
        """Vérifie les codes non-retryable."""
        # Code 200 = no security definition = not retryable
        error = IBKRError("No security definition", error_code=200)
        assert error.retryable is False
        assert error.category == "config"
        
        # Code 354 = no market data subscription = not retryable
        error = IBKRError("No subscription", error_code=354)
        assert error.retryable is False
        assert error.category == "config"
    
    def test_error_code_502_is_network(self):
        """Vérifie que code 502 = network."""
        error = IBKRError("Cannot connect", error_code=502)
        assert error.category == "network"
    
    def test_explicit_retryable_override(self):
        """Vérifie qu'on peut forcer retryable."""
        # Normalement 200 n'est pas retryable
        error = IBKRError("Test", error_code=200, retryable=True)
        assert error.retryable is True


class TestGrokError:
    """Tests pour GrokError."""
    
    def test_basic_creation(self):
        """Test création basique."""
        error = GrokError("Invalid JSON", raw_response='{"broken')
        
        assert error.raw_response == '{"broken'
        assert error.category == "format"
    
    def test_status_code_401_is_auth(self):
        """Vérifie que HTTP 401 = auth."""
        error = GrokError("Unauthorized", status_code=401)
        
        assert error.category == "auth"
        assert error.retryable is False
        assert "[HTTP-401]" in str(error)
    
    def test_status_code_429_is_rate_limit(self):
        """Vérifie que HTTP 429 = rate_limit."""
        error = GrokError("Too many requests", status_code=429)
        
        assert error.category == "rate_limit"
        assert error.retryable is True
    
    def test_status_code_500_is_retryable(self):
        """Vérifie que HTTP 5xx = retryable."""
        for code in [500, 502, 503, 504]:
            error = GrokError(f"Server error {code}", status_code=code)
            assert error.retryable is True
            assert error.category == "internal"
    
    def test_timeout_in_message_is_timeout(self):
        """Vérifie détection timeout dans message."""
        error = GrokError("Request timeout after 30s")
        
        assert error.category == "timeout"
        assert error.retryable is True


class TestIsRetryableMarketError:
    """Tests pour is_retryable_market_error()."""
    
    def test_market_error_retryable(self):
        """Test avec MarketError retryable."""
        error = MarketError("Test", retryable=True)
        assert is_retryable_market_error(error) is True
    
    def test_market_error_not_retryable(self):
        """Test avec MarketError non-retryable."""
        error = MarketError("Test", retryable=False)
        assert is_retryable_market_error(error) is False
    
    def test_ibkr_error_pacing(self):
        """Test avec IBKRError pacing."""
        error = IBKRError("Pacing", error_code=162)
        assert is_retryable_market_error(error) is True
    
    def test_grok_error_rate_limit(self):
        """Test avec GrokError rate limit."""
        error = GrokError("Rate limit", status_code=429)
        assert is_retryable_market_error(error) is True
    
    def test_grok_error_auth_not_retryable(self):
        """Test avec GrokError auth."""
        error = GrokError("Unauthorized", status_code=401)
        assert is_retryable_market_error(error) is False
    
    def test_standard_timeout_exception(self):
        """Test avec TimeoutError standard."""
        error = TimeoutError("Connection timed out")
        result = is_retryable_market_error(error)
        assert result is True
    
    def test_standard_connection_error(self):
        """Test avec ConnectionError standard."""
        error = ConnectionError("Connection refused")
        result = is_retryable_market_error(error)
        assert result is True


class TestErrorStringRepresentation:
    """Tests pour la représentation string des erreurs."""
    
    def test_market_error_str(self):
        """Test __str__ de MarketError."""
        error = MarketError("Simple error")
        assert str(error) == "Simple error"
    
    def test_market_error_with_original(self):
        """Test __str__ avec original."""
        original = ValueError("Original")
        error = MarketError("Wrapped error", original=original)
        
        # Le format dépend de LumenaError
        assert "Wrapped error" in str(error)
    
    def test_ibkr_error_with_code(self):
        """Test __str__ de IBKRError avec code."""
        error = IBKRError("Connection failed", error_code=502)
        assert "[IBKR-502]" in str(error)
        assert "Connection failed" in str(error)
    
    def test_ibkr_error_without_code(self):
        """Test __str__ de IBKRError sans code."""
        error = IBKRError("Unknown error", error_code=0)
        assert "[IBKR-" not in str(error)
        assert "Unknown error" in str(error)
    
    def test_grok_error_with_status(self):
        """Test __str__ de GrokError avec status."""
        error = GrokError("Bad request", status_code=400)
        assert "[HTTP-400]" in str(error)
        assert "Bad request" in str(error)
