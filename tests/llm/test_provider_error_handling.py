"""
🧪 Tests - Provider Error Handling (Phase 5.2)

Tests pour la gestion des erreurs httpx et le cooldown des providers.
"""

import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta


class TestHandleHttpxError:
    """Tests pour _handle_httpx_error."""
    
    @pytest.fixture
    def llm(self):
        """Crée une instance de MultiProviderLLM."""
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_connect_error_marks_failure(self, llm):
        """ConnectError doit marquer le provider en échec."""
        error = httpx.ConnectError("Connection refused")
        msg = llm._handle_httpx_error(error, "openai")
        
        assert "Connexion impossible" in msg
        assert llm.provider_health["openai"]["failures"] == 1
    
    def test_connect_timeout_marks_failure(self, llm):
        """ConnectTimeout doit marquer le provider en échec."""
        error = httpx.ConnectTimeout("Timeout")
        msg = llm._handle_httpx_error(error, "anthropic")
        
        assert "Timeout connexion" in msg
        assert llm.provider_health["anthropic"]["failures"] == 1
    
    def test_read_timeout_marks_failure(self, llm):
        """ReadTimeout doit marquer le provider en échec."""
        error = httpx.ReadTimeout("Read timeout")
        msg = llm._handle_httpx_error(error, "deepseek")
        
        assert "Timeout lecture" in msg
        assert llm.provider_health["deepseek"]["failures"] == 1
    
    def test_pool_timeout_no_failure(self, llm):
        """PoolTimeout ne doit pas marquer d'échec (temporaire)."""
        error = httpx.PoolTimeout("Pool full")
        msg = llm._handle_httpx_error(error, "openai")
        
        assert "Pool saturé" in msg
        assert llm.provider_health["openai"]["failures"] == 0
    
    def test_http_401_no_failure(self, llm):
        """Erreur 401 doit informer sur la clé API mais pas cooldown."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        
        error = httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
        msg = llm._handle_httpx_error(error, "openai")
        
        assert "Clé API invalide" in msg
        # 401 ne devrait pas déclencher de cooldown
    
    def test_http_429_marks_failure(self, llm):
        """Erreur 429 (rate limit) doit marquer l'échec."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        
        error = httpx.HTTPStatusError("429", request=MagicMock(), response=mock_response)
        msg = llm._handle_httpx_error(error, "openai")
        
        assert "Rate limit" in msg
        assert llm.provider_health["openai"]["failures"] == 1
    
    def test_http_500_marks_failure(self, llm):
        """Erreur 5xx doit marquer l'échec."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service unavailable"
        
        error = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_response)
        msg = llm._handle_httpx_error(error, "google")
        
        assert "Erreur serveur" in msg
        assert llm.provider_health["google"]["failures"] == 1


class TestProviderCooldown:
    """Tests pour le système de cooldown des providers."""
    
    @pytest.fixture
    def llm(self):
        """Crée une instance avec max_failures=3."""
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM(model_name="qwen3-8b")
        llm.max_failures = 3
        llm.cooldown_minutes = 5
        return llm
    
    def test_no_cooldown_before_max_failures(self, llm):
        """Pas de cooldown avant max_failures atteint."""
        llm._mark_failure("openai")
        llm._mark_failure("openai")
        
        assert llm.provider_health["openai"]["failures"] == 2
        assert llm._is_healthy("openai") is True
    
    def test_cooldown_after_max_failures(self, llm):
        """Cooldown activé après max_failures atteint."""
        for _ in range(3):
            llm._mark_failure("openai")
        
        assert llm.provider_health["openai"]["healthy"] is False
        assert llm.provider_health["openai"]["cooldown_until"] is not None
        assert llm._is_healthy("openai") is False
    
    def test_cooldown_expires(self, llm):
        """Le cooldown doit expirer après le temps configuré."""
        for _ in range(3):
            llm._mark_failure("openai")
        
        # Simuler que le cooldown est passé
        llm.provider_health["openai"]["cooldown_until"] = datetime.now() - timedelta(minutes=1)
        
        assert llm._is_healthy("openai") is True
        assert llm.provider_health["openai"]["failures"] == 0
    
    def test_success_resets_failures(self, llm):
        """Un succès doit réinitialiser le compteur."""
        llm._mark_failure("anthropic")
        llm._mark_failure("anthropic")
        
        llm._mark_success("anthropic")
        
        assert llm.provider_health["anthropic"]["failures"] == 0
        assert llm.provider_health["anthropic"]["healthy"] is True


class TestProviderFallback:
    """Tests pour le système de fallback."""
    
    @pytest.fixture
    def llm(self):
        """Crée une instance de MultiProviderLLM."""
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_get_next_healthy_provider(self, llm):
        """Doit retourner le prochain provider healthy."""
        # Mettre deepseek en échec (premier dans la chaîne)
        for _ in range(3):
            llm._mark_failure("deepseek")
        
        next_provider = llm._get_next_provider("deepseek")
        assert next_provider == "mistral"
    
    def test_no_healthy_provider_returns_none(self, llm):
        """Doit retourner None si aucun provider healthy."""
        # Mettre tous en échec
        for provider in llm.fallback_order:
            for _ in range(3):
                llm._mark_failure(provider)
        
        next_provider = llm._get_next_provider("deepseek")
        assert next_provider is None
    
    def test_health_status_report(self, llm):
        """get_health_status doit retourner l'état de tous les providers."""
        llm._mark_failure("openai")
        
        status = llm.get_health_status()
        
        assert "openai" in status
        assert status["openai"]["failures"] == 1
        assert status["openai"]["healthy"] is True


class TestRemoteProtocolError:
    """Tests pour les erreurs de protocole."""
    
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_remote_protocol_error(self, llm):
        """RemoteProtocolError doit être gérée."""
        error = httpx.RemoteProtocolError("Broken pipe")
        msg = llm._handle_httpx_error(error, "anthropic")
        
        assert "Erreur protocole" in msg
        assert llm.provider_health["anthropic"]["failures"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
