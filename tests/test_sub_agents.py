"""
🧪 Tests: Sub-Agents
Tests unitaires pour le système de sub-agents LUMENA
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSubAgentImport:
    """Tests d'import des sub-agents."""
    
    def test_import_sub_agent(self):
        """Vérifie que le module s'importe."""
        from src.agents.sub_agent import SubAgentOrchestrator
        assert SubAgentOrchestrator is not None
    
    def test_import_agents(self):
        """Vérifie l'import des agents spécialisés."""
        from src.agents.sub_agent import CodeAgent, ResearchAgent, FileAgent
        assert CodeAgent is not None
        assert ResearchAgent is not None
        assert FileAgent is not None


class TestOrchestrator:
    """Tests de l'orchestrateur."""
    
    def test_get_orchestrator_singleton(self):
        """Vérifie le pattern singleton."""
        from src.agents.sub_agent import get_orchestrator
        orch1 = get_orchestrator()
        orch2 = get_orchestrator()
        assert orch1 is orch2
    
    def test_orchestrator_has_agents(self):
        """Vérifie que l'orchestrateur a des agents."""
        from src.agents.sub_agent import get_orchestrator
        orch = get_orchestrator()
        status = orch.get_status()
        assert status["total_agents"] == 3
    
    def test_format_status(self):
        """Vérifie le format du status."""
        from src.agents.sub_agent import get_orchestrator
        orch = get_orchestrator()
        status_str = orch.format_status()
        assert "CodeAgent" in status_str
        assert "ResearchAgent" in status_str
        assert "FileAgent" in status_str


class TestAgentTypes:
    """Tests des types d'agents."""
    
    def test_agent_type_enum(self):
        """Vérifie l'enum AgentType."""
        from src.agents.sub_agent import AgentType
        assert AgentType.CODE is not None
        assert AgentType.RESEARCH is not None
        assert AgentType.FILE is not None
        assert AgentType.GENERAL is not None
    
    def test_agent_capabilities(self):
        """Vérifie les capacités des agents."""
        from src.agents.sub_agent import CodeAgent, AgentType
        agent = CodeAgent()
        assert agent.agent_type == AgentType.CODE
        assert len(agent.capabilities) > 0


@pytest.mark.asyncio
class TestDelegation:
    """Tests de délégation de tâches."""
    
    async def test_delegate_to_agent(self):
        """Test la fonction delegate_to_agent."""
        from src.agents.sub_agent import delegate_to_agent
        result = await delegate_to_agent(
            "Test task",
            "code",
            {}
        )
        assert result is not None
