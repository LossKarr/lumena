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


    @pytest.mark.asyncio
    async def test_anthropic_model_refusal_does_not_cooldown_provider(self):
        """Un refus Fable est un refus modele, pas une panne du provider Anthropic."""
        from src.llm.multi_provider import MultiProviderLLM

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}, clear=True):
            llm = MultiProviderLLM(model_name="claude-fable-5")
            llm._is_code_heavy_request = MagicMock(return_value=(False, None))
            llm._continue_if_needed = AsyncMock(side_effect=lambda **kw: kw["initial_result"])
            llm._chat_provider_result = AsyncMock(
                side_effect=ValueError("anthropic_refusal:claude-fable-5")
            )

            text = await llm.chat([{"role": "user", "content": "test"}], no_upgrade=True)

        assert text.startswith("[Refus]")
        assert llm.provider_health["anthropic"]["failures"] == 0
        assert llm.get_last_response_meta()["fallback_used"] is False
        assert llm._chat_provider_result.await_count == 1


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


@pytest.mark.asyncio
async def test_exhausted_credit_429_is_not_retried(monkeypatch):
    """Un solde épuisé n'est pas une limitation de débit transitoire."""
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import ProviderType

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        429,
        request=request,
        text='{"error":{"code":"credit_balance_exhausted"}}',
    )
    error = httpx.HTTPStatusError("quota", request=request, response=response)
    llm = MultiProviderLLM(model_name="gpt-6-sol")
    llm._TRANSIENT_RETRIES = 2
    inner = AsyncMock(side_effect=error)
    monkeypatch.setattr(llm, "_chat_provider_result_inner", inner)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await llm._chat_provider_result_with_retry(
                ProviderType.OPENAI,
                [{"role": "user", "content": "hello"}],
                0.0,
                16,
                "gpt-6-sol",
            )
    finally:
        await llm.close()

    assert inner.await_count == 1


def test_anthropic_low_credit_http_400_is_classified_as_quota():
    from src.llm.multi_provider import _raise_for_anthropic_error

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        400,
        request=request,
        text='{"error":{"message":"Your credit balance is too low to access the Anthropic API."}}',
    )
    with pytest.raises(RuntimeError, match="402 Anthropic API credit quota exhausted"):
        _raise_for_anthropic_error(response)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
