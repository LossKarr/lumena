"""
Tests pour les features Phase 1 & 2 de Lumena
- Phase 1: apply_patch, learn_from_action, suggest_instincts, get_curiosity_status
- Phase 2: bg_start, bg_status, bg_list, bg_cancel
"""

import pytest
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestPhase1DormantPowers:
    """Tests pour Phase 1 - Pouvoirs dormants."""
    
    def test_self_improve_import(self):
        """Vérifie que self_improve peut être importé."""
        from src.autonomy.self_improve import get_self_improver
        improver = get_self_improver()
        assert improver is not None
    
    def test_instincts_import(self):
        """Vérifie que instincts peut être importé."""
        from src.learning.instincts import get_instinct_system
        instincts = get_instinct_system()
        assert instincts is not None
    
    def test_curiosity_import(self):
        """Vérifie que curiosity peut être importé."""
        from src.autonomy.curiosity import get_curiosity_module
        curiosity = get_curiosity_module()
        assert curiosity is not None
    
    def test_curiosity_get_status(self):
        """Vérifie que get_status retourne les bonnes clés."""
        from src.autonomy.curiosity import get_curiosity_module
        curiosity = get_curiosity_module()
        status = curiosity.get_status()
        
        assert "boredom" in status
        assert "curiosity" in status
        assert "energy" in status
    
    def test_instincts_suggest(self):
        """Vérifie que suggest fonctionne."""
        from src.learning.instincts import get_instinct_system
        instincts = get_instinct_system()
        suggestions = instincts.suggest("test context")
        
        assert isinstance(suggestions, list)


class TestPhase2BackgroundTasks:
    """Tests pour Phase 2 - Background Tasks."""
    
    def test_manager_import(self):
        """Vérifie que le manager peut être importé."""
        from src.background.manager import get_task_manager
        manager = get_task_manager()
        assert manager is not None
    
    def test_manager_has_methods(self):
        """Vérifie que le manager a les bonnes méthodes."""
        from src.background.manager import BackgroundTaskManager
        
        assert hasattr(BackgroundTaskManager, 'start_command')
        assert hasattr(BackgroundTaskManager, 'get_status')
        assert hasattr(BackgroundTaskManager, 'get_all_tasks')
        assert hasattr(BackgroundTaskManager, 'cancel_task')
    
    @pytest.mark.asyncio
    async def test_start_command(self):
        """Vérifie qu'on peut lancer une commande."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.background.manager import BackgroundTaskManager
        manager = BackgroundTaskManager()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.pid = 99999
        mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=mock_proc):
            task = await manager.start_command("test", "echo hello")
            await asyncio.sleep(0.1)  # let _run_command task complete
        assert task is not None
        assert task.id is not None
        assert task.name == "test"


class TestReactToolsRegistered:
    """Vérifie que les outils Phase 1 & 2 sont enregistrés dans React."""
    
    def test_tools_registered(self):
        """Vérifie que tous les outils Phase 1 & 2 sont enregistrés."""
        from src.reasoning.react import ToolRegistry
        
        registry = ToolRegistry()
        tools = registry.tools
        
        # Phase 1
        phase1_tools = ['apply_patch', 'learn_from_action', 'suggest_instincts', 'get_curiosity_status']
        for tool in phase1_tools:
            assert tool in tools, f"Outil {tool} manquant dans Phase 1"
        
        # Phase 2
        phase2_tools = ['bg_start', 'bg_status', 'bg_list', 'bg_cancel']
        for tool in phase2_tools:
            assert tool in tools, f"Outil {tool} manquant dans Phase 2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
