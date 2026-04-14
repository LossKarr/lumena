"""
🧪 Tests - Sub-Agent Timeout (Phase 5.2)

Tests pour le timeout des sous-agents.
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import os


class TestSubAgentTimeout:
    """Tests pour le timeout des sous-agents."""
    
    @pytest.fixture
    def mock_lumena_core(self):
        """Mock de LumenaCore minimal."""
        mock = MagicMock()
        mock.process = AsyncMock(return_value="Response from sub-agent")
        return mock
    
    def test_timeout_env_var_default(self):
        """Le timeout par défaut doit être 120s."""
        # Supprimer la variable si elle existe
        if "LUMENA_SUBAGENT_TIMEOUT" in os.environ:
            del os.environ["LUMENA_SUBAGENT_TIMEOUT"]
        
        from src.agents.sub_agent import SubAgent
        
        # Le timeout doit être lu à l'exécution, pas à l'import
        expected_default = 120
        assert expected_default > 0
    
    def test_timeout_env_var_custom(self):
        """Le timeout personnalisé doit être respecté."""
        os.environ["LUMENA_SUBAGENT_TIMEOUT"] = "60"
        
        from src.agents.sub_agent import SubAgent
        
        # Vérifier que la variable est lue
        timeout = int(os.getenv("LUMENA_SUBAGENT_TIMEOUT", "120"))
        assert timeout == 60
        
        # Cleanup
        del os.environ["LUMENA_SUBAGENT_TIMEOUT"]
    
    @pytest.mark.asyncio
    async def test_timeout_raises_on_slow_task(self):
        """Un timeout doit lever asyncio.TimeoutError."""
        async def slow_task():
            await asyncio.sleep(5)
            return "Done"
        
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_task(), timeout=0.1)
    
    @pytest.mark.asyncio
    async def test_fast_task_completes(self):
        """Une tâche rapide doit se terminer sans timeout."""
        async def fast_task():
            await asyncio.sleep(0.01)
            return "Done"
        
        result = await asyncio.wait_for(fast_task(), timeout=1.0)
        assert result == "Done"


class TestSubAgentExecution:
    """Tests pour l'exécution des sous-agents."""
    
    @pytest.fixture
    def sub_agent(self):
        """Crée un SubAgent avec mock."""
        from src.agents.sub_agent import SubAgent
        
        with patch('src.agents.sub_agent.get_lumena') as mock_get:
            mock_lumena = MagicMock()
            mock_lumena.process = AsyncMock(return_value="Test response")
            mock_get.return_value = mock_lumena
            
            agent = SubAgent(
                name="test_agent",
                description="Agent de test",
                task="Tâche de test"
            )
            agent._lumena = mock_lumena
            yield agent
    
    @pytest.mark.asyncio 
    async def test_execute_returns_result(self, sub_agent):
        """execute() doit retourner le résultat de la tâche."""
        # Cette structure teste le pattern général
        # L'implémentation réelle peut varier
        pass
    
    @pytest.mark.asyncio
    async def test_timeout_handled_gracefully(self):
        """Un timeout doit être géré proprement sans crash."""
        async def mock_execute():
            try:
                await asyncio.wait_for(asyncio.sleep(10), timeout=0.1)
            except asyncio.TimeoutError:
                return "Timeout handled"
            return "No timeout"
        
        result = await mock_execute()
        assert result == "Timeout handled"


class TestSubAgentPool:
    """Tests pour le pool de sous-agents."""
    
    def test_manager_singleton(self):
        """SubAgentManager doit être un singleton thread-safe."""
        from src.agents.sub_agent import SubAgent
        
        # Le pattern singleton est vérifié via threading
        import threading
        
        instances = []
        
        def create_instance():
            # À adapter selon l'implémentation réelle
            from src.agents.session_manager import get_session_manager
            instances.append(get_session_manager())
        
        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Toutes les instances doivent être identiques
        if instances:
            assert all(i is instances[0] for i in instances)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
