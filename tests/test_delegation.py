"""
🧪 Tests: Cross-Agent Delegation & DelegationContext

Vérifie :
- DelegationContext immuable (frozen=True)
- Détection de cycles (A→B→A)
- Limite de profondeur (max_depth=3)
- delegate_to() retourne un nouveau contexte
- Intégration avec AgentTask
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestDelegationContext:
    """Tests unitaires pour DelegationContext."""

    def test_import(self):
        """DelegationContext, exceptions, et nouveaux exports s'importent."""
        from src.agents.sub_agent import (
            DelegationContext,
            DelegationDepthExceeded,
            DelegationCycleDetected,
        )
        assert DelegationContext is not None
        assert DelegationDepthExceeded is not None
        assert DelegationCycleDetected is not None

    def test_frozen_immutable(self):
        """frozen=True empêche les mutations — thread-safe par construction."""
        from src.agents.sub_agent import DelegationContext

        ctx = DelegationContext(agent_chain=("A",), depth=1, root_task_id="t1")
        with pytest.raises(AttributeError):
            ctx.depth = 99  # type: ignore

    def test_delegate_to_creates_new_context(self):
        """delegate_to retourne un NOUVEAU contexte, l'original est inchangé."""
        from src.agents.sub_agent import DelegationContext

        ctx = DelegationContext(
            agent_chain=("PlannerAgent",),
            depth=0,
            max_depth=3,
            root_task_id="t1",
        )
        child = ctx.delegate_to("CodeAgent")

        # Original inchangé
        assert ctx.depth == 0
        assert ctx.agent_chain == ("PlannerAgent",)

        # Enfant correct
        assert child.depth == 1
        assert child.agent_chain == ("PlannerAgent", "CodeAgent")
        assert child.root_task_id == "t1"

    def test_chain_str(self):
        """chain_str affiche la chaîne lisible."""
        from src.agents.sub_agent import DelegationContext

        ctx = DelegationContext(agent_chain=("A", "B", "C"))
        assert ctx.chain_str == "A → B → C"

        empty = DelegationContext()
        assert empty.chain_str == "(root)"

    def test_cycle_detected(self):
        """Détecte un cycle A→B→A."""
        from src.agents.sub_agent import DelegationContext, DelegationCycleDetected

        ctx = DelegationContext(
            agent_chain=("PlannerAgent", "CodeAgent"),
            depth=1,
            max_depth=3,
            root_task_id="t1",
        )
        # Tenter de redéléguer à PlannerAgent → cycle
        with pytest.raises(DelegationCycleDetected, match="Cycle détecté"):
            ctx.delegate_to("PlannerAgent")

    def test_cycle_detected_self(self):
        """Détecte un auto-cycle A→A."""
        from src.agents.sub_agent import DelegationContext, DelegationCycleDetected

        ctx = DelegationContext(
            agent_chain=("CodeAgent",),
            depth=0,
            max_depth=3,
            root_task_id="t1",
        )
        with pytest.raises(DelegationCycleDetected):
            ctx.delegate_to("CodeAgent")

    def test_depth_exceeded(self):
        """Lève DelegationDepthExceeded quand max_depth atteint."""
        from src.agents.sub_agent import DelegationContext, DelegationDepthExceeded

        ctx = DelegationContext(
            agent_chain=("A", "B", "C"),
            depth=3,
            max_depth=3,
            root_task_id="t1",
        )
        with pytest.raises(DelegationDepthExceeded, match="Profondeur max"):
            ctx.delegate_to("D")

    def test_valid_chain_of_3(self):
        """Une chaîne de 3 délégations est valide (depth 0→1→2)."""
        from src.agents.sub_agent import DelegationContext

        ctx = DelegationContext(
            agent_chain=("A",),
            depth=0,
            max_depth=3,
            root_task_id="t1",
        )
        c1 = ctx.delegate_to("B")
        c2 = c1.delegate_to("C")
        c3 = c2.delegate_to("D")

        assert c3.depth == 3
        assert c3.agent_chain == ("A", "B", "C", "D")

    def test_depth_4_refused(self):
        """depth=3 + delegate → refusé."""
        from src.agents.sub_agent import DelegationContext, DelegationDepthExceeded

        ctx = DelegationContext(
            agent_chain=("A",),
            depth=0,
            max_depth=3,
            root_task_id="t1",
        )
        c1 = ctx.delegate_to("B")
        c2 = c1.delegate_to("C")
        c3 = c2.delegate_to("D")
        with pytest.raises(DelegationDepthExceeded):
            c3.delegate_to("E")


class TestAgentTaskDelegation:
    """Tests d'intégration AgentTask + DelegationContext."""

    def test_agent_task_default_none(self):
        """delegation_ctx est None par défaut."""
        from src.agents.sub_agent import AgentTask, AgentType

        task = AgentTask(task_id="t1", description="test", agent_type=AgentType.CODE)
        assert task.delegation_ctx is None

    def test_agent_task_with_ctx(self):
        """AgentTask accepte un DelegationContext."""
        from src.agents.sub_agent import AgentTask, AgentType, DelegationContext

        ctx = DelegationContext(
            agent_chain=("PlannerAgent",),
            depth=1,
            root_task_id="t0",
        )
        task = AgentTask(
            task_id="t1",
            description="test",
            agent_type=AgentType.CODE,
            delegation_ctx=ctx,
        )
        assert task.delegation_ctx is not None
        assert task.delegation_ctx.depth == 1

    def test_to_dict_includes_delegation(self):
        """to_dict() sérialise le DelegationContext."""
        from src.agents.sub_agent import AgentTask, AgentType, DelegationContext

        ctx = DelegationContext(
            agent_chain=("Planner", "Code"),
            depth=1,
            root_task_id="t0",
        )
        task = AgentTask(
            task_id="t1",
            description="test",
            agent_type=AgentType.CODE,
            delegation_ctx=ctx,
        )
        d = task.to_dict()
        assert d["delegation_ctx"] is not None
        assert d["delegation_ctx"]["agent_chain"] == ["Planner", "Code"]
        assert d["delegation_ctx"]["depth"] == 1

    def test_to_dict_none_delegation(self):
        """to_dict() retourne None si pas de DelegationContext."""
        from src.agents.sub_agent import AgentTask, AgentType

        task = AgentTask(task_id="t1", description="test", agent_type=AgentType.CODE)
        d = task.to_dict()
        assert d["delegation_ctx"] is None


class TestDelegateMethod:
    """Tests pour SubAgent.delegate() — nécessite mock orchestrateur."""

    @pytest.mark.asyncio
    async def test_delegate_creates_child_task(self):
        """delegate() crée une sous-tâche avec DelegationContext."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from src.agents.sub_agent import (
            SubAgent, AgentType, AgentTask, AgentResult, StatusCode,
            DelegationContext,
        )

        agent = SubAgent(name="TestAgent", agent_type=AgentType.CODE)
        # Simuler une tâche courante
        agent.current_task = AgentTask(
            task_id="parent_t1",
            description="parent task",
            agent_type=AgentType.CODE,
        )

        # Mock l'orchestrateur
        mock_result = AgentResult(
            task_id="child_t1",
            success=True,
            output="delegated result",
            status_code=StatusCode.SUCCESS,
        )
        mock_orch = MagicMock()
        mock_orch.task_counter = 0
        mock_orch._infer_agent_type = MagicMock(return_value=AgentType.RESEARCH)
        mock_agent = MagicMock()
        mock_agent.name = "ResearchAgent"
        mock_orch.get_agent = MagicMock(return_value=mock_agent)
        mock_orch.execute_task = AsyncMock(return_value=mock_result)

        with patch("src.agents.sub_agent.get_orchestrator", return_value=mock_orch):
            result = await agent.delegate(
                "recherche web",
                agent_type=AgentType.RESEARCH,
                context={"query": "test"},
            )

        assert result.success is True
        assert result.output == "delegated result"
        # Vérifier que execute_task a été appelé avec un DelegationContext
        call_args = mock_orch.execute_task.call_args
        child_task = call_args[0][0]
        assert child_task.delegation_ctx is not None
        assert "TestAgent" in child_task.delegation_ctx.agent_chain
        assert child_task.delegation_ctx.depth == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
