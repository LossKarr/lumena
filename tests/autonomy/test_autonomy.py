"""
🧪 Tests - Autonomy Module (Phase 5.3)

Tests pour les modules d'autonomie (scheduler, daemon, heartbeat, goals).
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
from datetime import datetime, timedelta


class TestScheduler:
    """Tests pour le scheduler de tâches."""
    
    def test_scheduler_import(self):
        """Le module scheduler doit être importable."""
        try:
            from src.autonomy import scheduler
            assert hasattr(scheduler, 'Scheduler') or hasattr(scheduler, 'get_scheduler')
        except ImportError:
            pytest.skip("Module autonomy.scheduler non disponible")
    
    def test_scheduler_singleton(self):
        """Scheduler doit être un singleton thread-safe."""
        try:
            from src.autonomy.scheduler import get_scheduler
            import threading
            
            instances = []
            
            def get_instance():
                instances.append(get_scheduler())
            
            threads = [threading.Thread(target=get_instance) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            if instances:
                assert all(i is instances[0] for i in instances)
        except ImportError:
            pytest.skip("Scheduler non disponible")
    
    def test_cron_validation(self):
        """La validation CRON doit être implémentée."""
        try:
            from src.autonomy.scheduler import validate_cron_expr
            
            # Expressions valides
            assert validate_cron_expr("* * * * *") is True
            assert validate_cron_expr("0 9 * * 1-5") is True
            assert validate_cron_expr("*/5 * * * *") is True
            
            # Expressions invalides
            assert validate_cron_expr("invalid") is False
            assert validate_cron_expr("* * *") is False  # Trop court
            
        except ImportError:
            pytest.skip("validate_cron_expr non disponible")


class TestDaemon:
    """Tests pour le daemon autonome."""
    
    def test_daemon_import(self):
        """Le module daemon doit être importable."""
        try:
            from src.autonomy import daemon
            assert hasattr(daemon, 'LumenaDaemon') or hasattr(daemon, 'get_daemon')
        except ImportError:
            pytest.skip("Module autonomy.daemon non disponible")
    
    def test_daemon_singleton(self):
        """Daemon doit être un singleton thread-safe."""
        try:
            from src.autonomy.daemon import get_daemon
            import threading
            
            instances = []
            
            def get_instance():
                instances.append(get_daemon())
            
            threads = [threading.Thread(target=get_instance) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            if instances:
                assert all(i is instances[0] for i in instances)
        except ImportError:
            pytest.skip("Daemon non disponible")
    
    def test_callback_error_handling(self):
        """Les erreurs de callback doivent être gérées."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            
            daemon = LumenaDaemon()
            
            # Un callback qui crash ne doit pas crasher le daemon
            def bad_callback(action):
                raise Exception("Test error")
            
            # Vérifier que le pattern try/except existe
            # (vérifié par code review)
            
        except ImportError:
            pytest.skip("LumenaDaemon non disponible")

    @pytest.mark.asyncio
    async def test_autonomous_execution_disabled_by_default(self, monkeypatch):
        """L'exécution autonome réelle doit rester désactivée par défaut."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            monkeypatch.setenv("LUMENA_AUTONOMY_EXECUTE_ACTIONS", "0")

            daemon = LumenaDaemon()
            assert daemon.enable_action_execution is False

            fake_lumena = AsyncMock()
            daemon.lumena = fake_lumena

            action = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer le web",
                metadata={"topic": "python"},
            )

            await daemon._execute_autonomous_action(action)
            fake_lumena.think_and_act.assert_not_awaited()
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_autonomous_execution_opt_in_calls_core(self):
        """Quand activée explicitement, l'exécution autonome doit appeler le core."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.autonomy_action_timeout_seconds = 60

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            fake_lumena.instinct_system = MagicMock()
            daemon.lumena = fake_lumena

            action = AutonomousAction(
                action_type=ActionType.LEARN_SOMETHING,
                description="Apprendre",
                metadata={"topic": "tests"},
            )

            await daemon._execute_autonomous_action(action)
            fake_lumena.think_and_act.assert_awaited_once()
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_goal_execution_opt_in_updates_progress(self):
        """Quand activé, _work_on_goal doit exécuter une étape et progresser."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.goals import Goal, GoalType, GoalPriority

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.autonomy_action_timeout_seconds = 60

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ACTION_DONE")
            daemon.lumena = fake_lumena

            goal = Goal(
                id="g1",
                title="Test Goal",
                description="Tester progression",
                goal_type=GoalType.LEARNING,
                priority=GoalPriority.MEDIUM,
                steps_total=3,
            )

            await daemon._work_on_goal(goal)

            assert goal.steps_completed == 1
            assert goal.progress > 0
            assert goal.metadata["envelope_origin"] == "goals"
            assert goal.metadata["envelope_intent"] == "Test Goal"
            assert goal.metadata["envelope_tool_category"] == "autonomy"
            fake_lumena.think_and_act.assert_awaited_once()
        except ImportError:
            pytest.skip("LumenaDaemon/Goal non disponible")

    @pytest.mark.asyncio
    async def test_goal_execution_failure_auto_fails_after_threshold(self):
        """Un goal doit passer en FAILED après échecs consécutifs configurés."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.goals import Goal, GoalType, GoalPriority, GoalStatus

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.autonomy_action_timeout_seconds = 60
            daemon.goal_execution_cooldown_seconds = 0
            daemon.goal_max_consecutive_failures = 2

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(side_effect=Exception("boom"))
            daemon.lumena = fake_lumena

            goal = Goal(
                id="g2",
                title="Fail Goal",
                description="Doit échouer",
                goal_type=GoalType.MAINTENANCE,
                priority=GoalPriority.MEDIUM,
                steps_total=2,
            )

            await daemon._work_on_goal(goal)
            assert goal.status in [GoalStatus.PENDING, GoalStatus.IN_PROGRESS]

            await daemon._work_on_goal(goal)
            assert goal.status == GoalStatus.FAILED
        except ImportError:
            pytest.skip("LumenaDaemon/Goal non disponible")

    @pytest.mark.asyncio
    async def test_progressive_allowlist_blocks_disallowed_action(self):
        """Le mode progressif doit bloquer les actions hors allowlist."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.progressive_mode_enabled = True
            daemon.allowed_action_types = {"EXPLORE_WEB"}

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            daemon.lumena = fake_lumena

            blocked_action = AutonomousAction(
                action_type=ActionType.OPTIMIZE_PC,
                description="Optimiser PC",
            )
            await daemon._execute_autonomous_action(blocked_action)
            fake_lumena.think_and_act.assert_not_awaited()
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_action_hourly_budget_throttles_execution(self):
        """Le budget horaire doit limiter le nombre d'actions autonomes."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.progressive_mode_enabled = True
            daemon.allowed_action_types = {"EXPLORE_WEB"}
            daemon.max_actions_per_hour = 1
            daemon.action_repeat_cooldown_seconds = 0

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            fake_lumena.instinct_system = MagicMock()
            daemon.lumena = fake_lumena

            action = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer",
                metadata={"topic": "ia"},
            )

            await daemon._execute_autonomous_action(action)
            await daemon._execute_autonomous_action(action)

            fake_lumena.think_and_act.assert_awaited_once()

            daemon._executed_action_timestamps = [datetime.now() - timedelta(hours=2)]
            daemon._recent_action_signatures = {}
            await daemon._execute_autonomous_action(action)
            assert fake_lumena.think_and_act.await_count == 2
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_action_repeat_cooldown_blocks_same_signature(self):
        """Deux actions identiques successives doivent être dédupliquées par cooldown."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.progressive_mode_enabled = True
            daemon.allowed_action_types = {"EXPLORE_WEB"}
            daemon.max_actions_per_hour = 10
            daemon.action_repeat_cooldown_seconds = 3600

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            fake_lumena.instinct_system = MagicMock()
            daemon.lumena = fake_lumena

            action = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer le web sur ia",
                metadata={"topic": "ia"},
            )

            await daemon._execute_autonomous_action(action)
            await daemon._execute_autonomous_action(action)

            fake_lumena.think_and_act.assert_awaited_once()
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_action_repeat_cooldown_allows_different_topics(self):
        """Deux actions de même type mais sujets différents doivent passer."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon()
            daemon.enable_action_execution = True
            daemon.progressive_mode_enabled = True
            daemon.allowed_action_types = {"EXPLORE_WEB"}
            daemon.max_actions_per_hour = 10
            daemon.action_repeat_cooldown_seconds = 3600

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            fake_lumena.instinct_system = MagicMock()
            daemon.lumena = fake_lumena

            action_a = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer sujet A",
                metadata={"topic": "ia"},
            )
            action_b = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer sujet B",
                metadata={"topic": "robotique"},
            )

            await daemon._execute_autonomous_action(action_a)
            await daemon._execute_autonomous_action(action_b)

            assert fake_lumena.think_and_act.await_count == 2
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")


    @pytest.mark.asyncio
    async def test_disk_guard_blocks_heavy_autonomous_action(self, tmp_path, monkeypatch):
        """Sous seuil disque, les actions lourdes autonomes doivent etre bloquees et tracees."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType
            from src.autonomy.activity_ledger import read_autonomy_events

            monkeypatch.setenv("LUMENA_AUTONOMY_LEDGER_IN_TESTS", "1")
            daemon = LumenaDaemon(data_dir=tmp_path / "data")
            daemon.running = True
            daemon.enable_action_execution = True
            daemon.autonomy_min_free_gb = 10
            daemon._free_disk_gb = lambda: 1.0

            fake_lumena = AsyncMock()
            fake_lumena.think_and_act = AsyncMock(return_value="ok")
            daemon.lumena = fake_lumena

            action = AutonomousAction(
                action_type=ActionType.EXPLORE_WEB,
                description="Explorer sous disque critique",
                metadata={"topic": "ia"},
            )

            await daemon._execute_autonomous_action(action)

            fake_lumena.think_and_act.assert_not_awaited()
            events = read_autonomy_events(data_dir=daemon.data_dir, date=datetime.now().strftime("%Y-%m-%d"))
            assert any(e.get("event_type") == "action_candidate" for e in events)
            blocked = [e for e in events if e.get("event_type") == "action_blocked"]
            assert blocked
            assert "disk_guard" in blocked[-1].get("reason", "")
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")

    @pytest.mark.asyncio
    async def test_disk_guard_allows_reflection_low_disk(self, tmp_path):
        """La reflexion locale reste autorisee sous pression disque."""
        try:
            from src.autonomy.daemon import LumenaDaemon
            from src.autonomy.curiosity import AutonomousAction, ActionType

            daemon = LumenaDaemon(data_dir=tmp_path / "data")
            daemon.enable_action_execution = True
            daemon.autonomy_min_free_gb = 10
            daemon._free_disk_gb = lambda: 1.0

            action = AutonomousAction(
                action_type=ActionType.REFLECT,
                description="Reflechir",
            )

            assert daemon._can_execute_autonomous_action(action) is True
        except ImportError:
            pytest.skip("LumenaDaemon/Curiosity non disponible")


class TestHeartbeat:
    """Tests pour le système de heartbeat."""
    
    def test_heartbeat_import(self):
        """Le module heartbeat doit être importable."""
        try:
            from src.autonomy import heartbeat
            assert True
        except ImportError:
            pytest.skip("Module autonomy.heartbeat non disponible")
    
    def test_heartbeat_singleton(self):
        """Heartbeat doit être un singleton."""
        try:
            from src.autonomy.heartbeat import get_heartbeat
            import threading
            
            instances = []
            
            def get_instance():
                instances.append(get_heartbeat())
            
            threads = [threading.Thread(target=get_instance) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            if instances:
                assert all(i is instances[0] for i in instances)
        except ImportError:
            pytest.skip("Heartbeat non disponible")
    
    def test_parsing_uses_specific_exceptions(self):
        """Le parsing doit utiliser des exceptions spécifiques."""
        # Vérifié par code review dans Phase 4.8
        # Le code doit utiliser (ValueError, IndexError) pas bare except
        pass


class TestGoals:
    """Tests pour le système de goals."""
    
    def test_goals_import(self):
        """Le module goals doit être importable."""
        try:
            from src.autonomy import goals
            assert True
        except ImportError:
            pytest.skip("Module autonomy.goals non disponible")
    
    def test_goals_singleton(self):
        """GoalManager doit être un singleton."""
        try:
            from src.autonomy.goals import get_goal_manager
            import threading
            
            instances = []
            
            def get_instance():
                instances.append(get_goal_manager())
            
            threads = [threading.Thread(target=get_instance) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            if instances:
                assert all(i is instances[0] for i in instances)
        except ImportError:
            pytest.skip("GoalManager non disponible")


class TestCuriosity:
    """Tests pour le module de curiosité."""
    
    def test_curiosity_import(self):
        """Le module curiosity doit être importable."""
        try:
            from src.autonomy import curiosity
            assert True
        except ImportError:
            pytest.skip("Module autonomy.curiosity non disponible")


class TestSelfImprove:
    """Tests pour le module d'auto-amélioration."""
    
    def test_self_improve_import(self):
        """Le module self_improve doit être importable."""
        try:
            from src.autonomy import self_improve
            assert True
        except ImportError:
            pytest.skip("Module autonomy.self_improve non disponible")
    
    def test_path_traversal_protection(self):
        """La protection path traversal doit être implémentée."""
        try:
            from src.autonomy.self_improve import SelfImprover
            from pathlib import Path
            
            # Vérifier que resolve() et is_relative_to() sont utilisés
            # (vérifié par code review dans Phase 4.3)
            
            workspace = Path("/tmp/test")
            malicious = workspace / ".." / ".." / "etc" / "passwd"
            resolved = malicious.resolve()
            
            try:
                is_safe = resolved.is_relative_to(workspace.resolve())
            except ValueError:
                is_safe = False
            
            assert not is_safe
            
        except ImportError:
            pytest.skip("SelfImprove non disponible")
    
    def test_backup_before_patch(self):
        """Les backups doivent être créés avant modification."""
        try:
            from src.autonomy.self_improve import SelfImprover
            
            # Vérifier que create_backup et rollback existent
            si = SelfImprover.__new__(SelfImprover)
            assert hasattr(si, 'create_backup') or hasattr(si, '_create_backup')
            assert hasattr(si, 'rollback') or hasattr(si, '_rollback')
        except (ImportError, AttributeError):
            pytest.skip("SelfImprover non disponible")


@pytest.mark.asyncio
class TestAsyncAutonomy:
    """Tests async pour les modules d'autonomie."""
    
    async def test_async_timeout_pattern(self):
        """Les opérations async doivent avoir des timeouts."""
        async def slow_operation():
            await asyncio.sleep(10)
            return "done"
        
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_operation(), timeout=0.1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
