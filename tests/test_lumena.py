"""
Tests unitaires pour LUMENA Core
"""

import pytest
import asyncio
from pathlib import Path
import sys

# Ajouter le chemin du projet
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import LumenaCore, OllamaClient, ConversationContext, Message
from src.personality import LumenaPersonality, Mood, EnergyLevel


class TestConversationContext:
    """Tests pour le contexte de conversation."""
    
    def test_add_message(self):
        ctx = ConversationContext()
        msg = ctx.add_message("user", "Salut !")
        
        assert len(ctx.messages) == 1
        assert msg.role == "user"
        assert msg.content == "Salut !"
    
    def test_get_history_for_llm(self):
        ctx = ConversationContext()
        ctx.add_message("user", "Message 1")
        ctx.add_message("assistant", "Réponse 1")
        ctx.add_message("user", "Message 2")
        
        history = ctx.get_history_for_llm()
        
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
    
    def test_clear(self):
        ctx = ConversationContext()
        ctx.add_message("user", "Test")
        ctx.clear()
        
        assert len(ctx.messages) == 0


class TestLumenaPersonality:
    """Tests pour la personnalité de LUMENA."""
    
    def test_default_traits(self):
        personality = LumenaPersonality()
        
        assert personality.name == "Lumena"
        assert personality.traits["curiosity"] > 0
        assert personality.current_mood == Mood.NEUTRAL
    
    def test_update_mood(self):
        personality = LumenaPersonality()
        personality.update_mood(Mood.HAPPY)
        
        assert personality.current_mood == Mood.HAPPY
    
    def test_get_greeting(self):
        personality = LumenaPersonality()
        greeting = personality.get_greeting()
        
        assert isinstance(greeting, str)
        assert len(greeting) > 0
    
    def test_system_prompt_generation(self):
        personality = LumenaPersonality()
        prompt = personality.get_system_prompt()
        
        assert "Lumena" in prompt
        assert "Curiosité" in prompt or "curieuse" in prompt.lower()


class TestOllamaClient:
    """Tests pour le client Ollama."""
    
    @pytest.mark.asyncio
    async def test_is_available_when_running(self):
        """Vérifie is_available retourne un bool (mocké pour éviter appel réseau)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        client = OllamaClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await client.is_available()
        assert isinstance(result, bool)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_list_models(self):
        """Liste les modèles (mocké pour éviter appel réseau)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        client = OllamaClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:8b"}]}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            models = await client.list_models()
        assert isinstance(models, list)
        assert "qwen3:8b" in models


class TestLumenaCore:
    """Tests pour le cerveau de LUMENA."""
    
    def test_initialization(self):
        core = LumenaCore()
        
        assert core.personality is not None
        assert core.llm is not None
        assert core.context is not None
    
    def test_greet(self):
        core = LumenaCore()
        greeting = core.greet()
        
        assert isinstance(greeting, str)
        assert len(greeting) > 0
    
    def test_set_mood(self):
        core = LumenaCore()
        core.set_mood(Mood.CURIOUS)
        
        assert core.personality.current_mood == Mood.CURIOUS
    
    def test_clear_context(self):
        core = LumenaCore()
        core.context.add_message("user", "Test")
        core.clear_context()
        
        assert len(core.context.messages) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
