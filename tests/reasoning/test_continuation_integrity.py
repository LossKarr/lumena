"""
🧪 Tests - LLM Continuation Integrity (Phase 5.2)

Tests pour la continuation automatique et la détection de répétition.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestContinuationDetection:
    """Tests pour la détection de continuation nécessaire."""
    
    @pytest.fixture
    def llm(self):
        """Crée une instance de MultiProviderLLM."""
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_is_length_finish_reason(self, llm):
        """Doit détecter les raisons de fin 'length'."""
        assert llm._is_length_finish_reason("length") is True
        assert llm._is_length_finish_reason("max_tokens") is True
        assert llm._is_length_finish_reason("max_output_tokens") is True
        assert llm._is_length_finish_reason("LENGTH") is True  # Case insensitive
        
    def test_is_not_length_finish_reason(self, llm):
        """Doit rejeter les autres raisons de fin."""
        assert llm._is_length_finish_reason("stop") is False
        assert llm._is_length_finish_reason("end_turn") is False
        assert llm._is_length_finish_reason(None) is False
        assert llm._is_length_finish_reason("") is False


class TestMergeTextSegments:
    """Tests pour la fusion des segments de texte."""
    
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_merge_with_no_overlap(self, llm):
        """Fusion sans chevauchement."""
        base = "Hello world"
        continuation = " this is new"
        
        result = llm._merge_text_segments(base, continuation)
        assert result == "Hello world this is new"
    
    def test_merge_with_overlap(self, llm):
        """Fusion avec chevauchement doit éliminer les doublons."""
        base = "The quick brown fox"
        continuation = "brown fox jumps over"
        
        result = llm._merge_text_segments(base, continuation)
        
        # Ne doit pas avoir "brown fox" en double
        assert result.count("brown fox") == 1
    
    def test_merge_empty_base(self, llm):
        """Base vide retourne la continuation."""
        result = llm._merge_text_segments("", "new text")
        assert result == "new text"
    
    def test_merge_empty_continuation(self, llm):
        """Continuation vide retourne la base."""
        result = llm._merge_text_segments("base text", "")
        assert result == "base text"
    
    def test_merge_exact_duplicate(self, llm):
        """Si continuation est dans la fin de base, retourne base."""
        base = "Hello world ending"
        continuation = "ending"
        
        result = llm._merge_text_segments(base, continuation)
        assert result == base


class TestContinuationWarning:
    """Tests pour les warnings de continuation."""
    
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_warning_in_metadata(self, llm):
        """Le warning doit être dans les métadonnées."""
        llm._set_last_response_meta(
            continuation_warning="⚠️ Réponse potentiellement incomplète"
        )
        
        meta = llm.get_last_response_meta()
        assert "continuation_warning" in meta
        assert "incomplète" in meta["continuation_warning"]
    
    def test_continuation_steps_tracked(self, llm):
        """Le nombre de continuations doit être tracké."""
        llm._set_last_response_meta(
            continuation_used=True,
            continuation_steps=2
        )
        
        meta = llm.get_last_response_meta()
        assert meta["continuation_used"] is True
        assert meta["continuation_steps"] == 2


class TestContinueIfNeeded:
    """Tests pour _continue_if_needed."""
    
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM(model_name="qwen3-8b")
        llm.max_continuation_steps = 3
        return llm
    
    @pytest.mark.asyncio
    async def test_no_continuation_for_stop(self, llm):
        """Pas de continuation si finish_reason=stop."""
        from src.llm.providers import ProviderType
        
        initial_result = {
            "text": "Complete response",
            "finish_reason": "stop"
        }
        
        with patch.object(llm, '_chat_provider_result', new_callable=AsyncMock):
            result = await llm._continue_if_needed(
                provider=ProviderType.OLLAMA,
                base_messages=[{"role": "user", "content": "test"}],
                temperature=0.7,
                max_tokens=1000,
                initial_result=initial_result
            )
        
        assert result["continuation_used"] is False
        assert result["continuation_steps"] == 0
    
    @pytest.mark.asyncio
    async def test_continuation_on_length(self, llm):
        """Continuation déclenchée si finish_reason=length."""
        from src.llm.providers import ProviderType
        
        initial_result = {
            "text": "Incomplete response...",
            "finish_reason": "length"
        }
        
        continuation_result = {
            "text": " more content here.",
            "finish_reason": "stop"
        }
        
        with patch.object(llm, '_chat_provider_result', new_callable=AsyncMock) as mock:
            mock.return_value = continuation_result
            
            result = await llm._continue_if_needed(
                provider=ProviderType.OLLAMA,
                base_messages=[{"role": "user", "content": "test"}],
                temperature=0.7,
                max_tokens=1000,
                initial_result=initial_result
            )
        
        assert result["continuation_used"] is True
        assert result["continuation_steps"] >= 1
    
    @pytest.mark.asyncio
    async def test_max_continuation_steps_limit(self, llm):
        """Le nombre de continuations doit être limité."""
        from src.llm.providers import ProviderType
        
        initial_result = {
            "text": "Start",
            "finish_reason": "length"
        }
        
        # Toujours retourner length pour forcer le max
        continuation_result = {
            "text": " more",
            "finish_reason": "length"
        }
        
        with patch.object(llm, '_chat_provider_result', new_callable=AsyncMock) as mock:
            mock.return_value = continuation_result
            
            result = await llm._continue_if_needed(
                provider=ProviderType.OLLAMA,
                base_messages=[{"role": "user", "content": "test"}],
                temperature=0.7,
                max_tokens=1000,
                initial_result=initial_result
            )
        
        # Doit s'arrêter après max_continuation_steps
        assert result["continuation_steps"] <= llm.max_continuation_steps
        # Doit avoir un warning
        assert result.get("continuation_warning") is not None


class TestLastUserMessage:
    """Tests pour l'extraction du dernier message utilisateur."""
    
    @pytest.fixture
    def llm(self):
        from src.llm.multi_provider import MultiProviderLLM
        return MultiProviderLLM(model_name="qwen3-8b")
    
    def test_extract_last_user_message(self, llm):
        """Doit extraire le dernier message de l'utilisateur."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        
        result = llm._last_user_message(messages)
        assert result == "Second question"
    
    def test_empty_messages(self, llm):
        """Liste vide retourne chaîne vide."""
        assert llm._last_user_message([]) == ""
    
    def test_no_user_message(self, llm):
        """Pas de message user retourne chaîne vide."""
        messages = [
            {"role": "system", "content": "System"},
            {"role": "assistant", "content": "Hello"},
        ]
        assert llm._last_user_message(messages) == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
