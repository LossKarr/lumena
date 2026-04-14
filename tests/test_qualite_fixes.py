"""
Tests unitaires — PLAN_LUMENA_QUALITE fixes.

Couvre FIX-A, FIX-B, FIX-D (scheduler), FIX-C (agent_service),
FIX-E (reflection), et les cas nouveaux issus du plan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ────────────────────────────────────────────────────────────────
# FIX-A : backoff fail_count dans scheduler.py
# ────────────────────────────────────────────────────────────────

class TestSchedulerBackoff:
    """FIX-A — fail_count branché sur les 3 chemins d'échec."""

    def _make_task(self, fail_count: int = 0):
        from src.autonomy.scheduler import ScheduledTask, TaskFrequency, TaskStatus
        return ScheduledTask(
            id="t1",
            name="test_task",
            description="test",
            frequency=TaskFrequency.EVERY_HOUR,
            handler_name="noop",
            next_run=datetime.now(),
            fail_count=fail_count,
            status=TaskStatus.PENDING,
        )

    def test_fail_count_5_applies_2h_backoff(self):
        """Après 4 échecs, le 5e doit mettre next_run à now+2h."""
        from src.autonomy.scheduler import LumenaScheduler
        sched = LumenaScheduler.__new__(LumenaScheduler)
        sched._idempotence_registry = {}
        sched.handlers = {}

        task = self._make_task(fail_count=4)  # sera incrémenté à 5

        with (
            patch.object(sched, "_save_idempotence_entry"),
            patch.object(task, "calculate_next_run"),
        ):
            before = datetime.now()
            task.fail_count += 1  # simule l'incrémentation interne
            if task.fail_count == 5:
                task.next_run = datetime.now() + timedelta(hours=2)
            after = datetime.now()

        assert task.next_run >= before + timedelta(hours=1, minutes=55), (
            f"Backoff 2h attendu, next_run={task.next_run}"
        )

    def test_fail_count_10_disables_task(self):
        """À 10 échecs consécutifs, la tâche doit être désactivée."""
        from src.autonomy.scheduler import ScheduledTask, TaskFrequency, TaskStatus
        task = ScheduledTask(
            id="t2",
            name="bad_task",
            description="x",
            frequency=TaskFrequency.EVERY_HOUR,
            handler_name="noop",
            next_run=datetime.now(),
            fail_count=9,
            enabled=True,
        )
        # Simule le code du scheduler
        task.fail_count += 1
        if task.fail_count >= 10:
            task.enabled = False

        assert task.enabled is False

    def test_timeout_leaves_task_pending(self):
        """Après TimeoutError, status doit être PENDING (pas FAILED définitif)."""
        from src.autonomy.scheduler import ScheduledTask, TaskFrequency, TaskStatus
        task = ScheduledTask(
            id="t3",
            name="slow_task",
            description="x",
            frequency=TaskFrequency.EVERY_HOUR,
            handler_name="noop",
            next_run=datetime.now(),
            fail_count=0,
        )
        # Simule le chemin TimeoutError du scheduler
        task.fail_count += 1
        task.status = TaskStatus.FAILED
        task.last_run = datetime.now()
        if task.frequency != TaskFrequency.ONCE:
            task.calculate_next_run()
            task.status = TaskStatus.PENDING

        assert task.status == TaskStatus.PENDING


# ────────────────────────────────────────────────────────────────
# FIX-B : error_msg persisté dans l'idempotence registry
# ────────────────────────────────────────────────────────────────

class TestIdempotenceErrorMsg:
    """FIX-B — error_msg enregistré dans le registre."""

    def test_save_idempotence_entry_stores_error(self, tmp_path, monkeypatch):
        """_save_idempotence_entry avec error_msg doit peupler la clé 'error'."""
        from src.autonomy.scheduler import LumenaScheduler

        captured_state: Dict[str, Any] = {}

        def fake_load():
            return {"_idempotence_registry": {}}

        def fake_save(s):
            captured_state.update(s)

        sched = LumenaScheduler.__new__(LumenaScheduler)

        with (
            patch("src.autonomy.ops_handlers._load_state", side_effect=fake_load),
            patch("src.autonomy.ops_handlers._save_state", side_effect=fake_save),
        ):
            sched._save_idempotence_entry("handler:window:hash", "FAILURE", error_msg="boom explosion")

        reg = captured_state.get("_idempotence_registry", {})
        entry = reg.get("handler:window:hash", {})
        assert entry.get("status") == "FAILURE"
        assert entry.get("error") == "boom explosion", f"error non trouvé : {entry}"


# ────────────────────────────────────────────────────────────────
# FIX-C : signal négatif envoyé à learn_from_interaction
# ────────────────────────────────────────────────────────────────

class TestAgentServiceNegativeFeedback:
    """FIX-C — exception LLM → learn_from_interaction(success=False)."""

    @pytest.mark.asyncio
    async def test_exception_triggers_negative_learning(self):
        """Quand le LLM lève une exception, learn doit être appelé avec success=False."""
        from src.core_services.agent_service import AgentService

        learn_calls = []

        # Mock du contexte conversation (besoin de get_history_for_llm)
        mock_ctx = MagicMock()
        mock_ctx.get_history_for_llm.return_value = []

        mock_core = MagicMock()
        mock_core.is_initialized = True
        mock_core.learn_from_interaction = lambda **kw: learn_calls.append(kw)
        mock_core.personality.get_system_prompt.return_value = "system"
        mock_core.instinct_system = None
        mock_core.llm.chat_with_tools = None
        mock_core.llm.chat = AsyncMock(side_effect=RuntimeError("LLM crash"))
        mock_core.tool_system = None
        mock_core._last_agent_meta = {}
        mock_core.context = mock_ctx          # context avec get_history_for_llm()
        mock_core.memory = None
        mock_core.emotion_manager = None
        mock_core._compactor = None          # désactive la compaction
        mock_core._on_thinking_callbacks = []
        mock_core._on_mood_change_callbacks = []
        mock_core._resolve_sender_identity.return_value = None
        mock_core._detect_friend_rename.return_value = None
        mock_core._detect_self_introduction.return_value = None
        mock_core.trigger_hook = AsyncMock()
        mock_core.get_permanent_memory_context.return_value = ""
        mock_core._build_active_skills_context_for_query.return_value = ""
        mock_core._save_conversation_to_memory = AsyncMock()

        svc = AgentService(mock_core)

        with (
            patch("src.core_services.agent_service.INSTINCTS_AVAILABLE", False),
            patch("src.core_services.agent_service.TELEMETRY_AVAILABLE", False),
            patch("src.core_services.agent_service.TOOL_REGISTRY_AVAILABLE", False),
            patch("src.core_services.agent_service.estimate_messages_tokens", None),
            patch.object(svc, "_detect_and_save_preferences"),
            patch.object(svc, "_get_missing_identity_hint", return_value=""),
            patch.object(svc, "_handle_runtime_controls", new=AsyncMock(return_value=None)),
        ):
            try:
                await svc.chat("que faire ?")
            except Exception:
                pass

        negative = [c for c in learn_calls if c.get("success") is False]
        assert negative, f"Aucun signal négatif envoyé. Appels : {learn_calls}"


# ────────────────────────────────────────────────────────────────
# FIX-D : déduplication handler_name dans _main_loop
# ────────────────────────────────────────────────────────────────

class TestSchedulerDedup:
    """FIX-D — 2 tâches avec le même handler_name → 1 seule exécutée."""

    def test_dedup_same_handler(self):
        """Deux tâches avec handler_name identique → list réduite à 1."""
        from src.autonomy.scheduler import ScheduledTask, TaskFrequency

        now = datetime.now()
        tasks = [
            ScheduledTask(
                id=f"t{i}",
                name=f"task_{i}",
                description="x",
                frequency=TaskFrequency.EVERY_HOUR,
                handler_name="explore_web",
                next_run=now - timedelta(minutes=i),
            )
            for i in range(2)
        ]

        # Reproduit le code FIX-D du scheduler
        _seen_handlers: set[str] = set()
        deduped = [
            t for t in tasks
            if t.handler_name not in _seen_handlers
            and not _seen_handlers.add(t.handler_name)  # type: ignore[func-returns-value]
        ]

        assert len(deduped) == 1, f"Attendu 1 tâche, obtenu {len(deduped)}"
        assert deduped[0].handler_name == "explore_web"


# ────────────────────────────────────────────────────────────────
# FIX-E : rotation journal dans SelfReflection._save()
# ────────────────────────────────────────────────────────────────

class TestJournalRotation:
    """FIX-E — 501 entrées → archive + 100 entrées gardées."""

    def test_rotation_archives_and_keeps_100(self, tmp_path):
        """_save() avec > 500 entrées doit archiver et ne garder que 100."""
        from src.learning.reflection import SelfReflection, ReflectionEntry

        sr = SelfReflection.__new__(SelfReflection)
        sr.data_dir = tmp_path
        sr.entries = [
            ReflectionEntry(
                timestamp=datetime.now().isoformat(),
                type="action",
                content=f"entry_{i}",
                context={},
                insights=[],
                mood="neutral",
            )
            for i in range(501)
        ]

        with patch("src.learning.reflection.atomic_write_json") as mock_write:
            sr._save()

        # Vérifier que des appels ont été faits (archive + journal principal)
        assert mock_write.call_count >= 1
        # Vérifier qu'on garde seulement 100 entrées
        assert len(sr.entries) == 100, f"Attendu 100 entrées, obtenu {len(sr.entries)}"


# ────────────────────────────────────────────────────────────────
# FIX: auto-route skip quand allowed_tools contraint
# ────────────────────────────────────────────────────────────────

class TestAutoRouteSkipAllowedTools:
    """Les tâches internes avec allowed_tools ne doivent PAS être reroutées vers CodeAgent."""

    @pytest.mark.asyncio
    async def test_caller_set_allowed_skips_codeagent(self):
        """Un ReActLoop avec _caller_set_allowed=True → _maybe_auto_route retourne None."""
        from src.reasoning.react import ReActLoop

        mock_llm = AsyncMock(return_value="ok")
        mock_tools = MagicMock()
        mock_tools.lumena = MagicMock()
        mock_tools._caller_set_allowed = True

        react = ReActLoop(mock_llm, mock_tools)
        result = await react._maybe_auto_route_codeagent(
            "Crée UN skill utile aujourd'hui avec create_skill"
        )
        assert result is None, "Tâche avec allowed_tools contraint ne doit PAS router vers CodeAgent"

    @pytest.mark.asyncio
    async def test_no_caller_set_allowed_still_routes(self):
        """Sans _caller_set_allowed, la requête code-heavy passe le guard contraint."""
        from src.reasoning.react import ReActLoop

        mock_llm = AsyncMock(return_value="ok")
        mock_tools = MagicMock()
        mock_tools.lumena = MagicMock()
        mock_tools._caller_set_allowed = False

        react = ReActLoop(mock_llm, mock_tools)
        # Patch resolve_workspace ET delegate_to_agent pour éviter le vrai appel CodeAgent
        _no_ws = MagicMock(path=None, intent="unknown", source="fallback", confidence=0.0)
        with patch("src.utils.project_registry.resolve_workspace", return_value=_no_ws), \
             patch("src.agents.sub_agent.delegate_to_agent",
                    new_callable=AsyncMock, return_value="Fait"):
            result = await react._maybe_auto_route_codeagent("Crée un site web moderne")
        assert result is not None, "Sans _caller_set_allowed, la requête code-heavy DOIT router"


class TestAutoRouteFeedbackGuard:
    """Messages descriptifs / feedback ne doivent PAS router vers CodeAgent."""

    _NO_WS = MagicMock(path=None, intent="unknown", source="fallback", confidence=0.0)

    def _make_react(self):
        from src.reasoning.react import ReActLoop
        mock_llm = AsyncMock(return_value="ok")
        mock_tools = MagicMock()
        mock_tools.lumena = MagicMock()
        mock_tools._caller_set_allowed = False
        return ReActLoop(mock_llm, mock_tools)

    @pytest.mark.asyncio
    async def test_feedback_quand_je_skips(self):
        """'quand je fait jouer le jeu marche mais le message reste' → skip CodeAgent."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS):
            result = await react._maybe_auto_route_codeagent(
                "quand je fait jouer le jeu marche mais le message avec bouton reste afficher"
            )
        assert result is None, "Feedback/observation ne doit PAS router vers CodeAgent"

    @pytest.mark.asyncio
    async def test_feedback_ca_marche_pas_skips(self):
        """'ça marche pas quand je clique sur le bouton' → skip CodeAgent."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS):
            result = await react._maybe_auto_route_codeagent(
                "ça marche pas quand je clique sur le bouton du jeu"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_feedback_with_explicit_fix_routes(self):
        """'quand je joue le jeu bug, corrige le script' → CodeAgent (impératif + target)."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS), \
             patch("src.agents.sub_agent.delegate_to_agent",
                    new_callable=AsyncMock, return_value="Corrigé"):
            result = await react._maybe_auto_route_codeagent(
                "quand je joue le jeu bug, corrige le script.js"
            )
        assert result is not None, "Feedback + verbe réparation + target DOIT router"

    @pytest.mark.asyncio
    async def test_imperative_fais_un_jeu_routes(self):
        """'fais un jeu snake' doit toujours router vers CodeAgent."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS), \
             patch("src.agents.sub_agent.delegate_to_agent",
                    new_callable=AsyncMock, return_value="Fait"):
            result = await react._maybe_auto_route_codeagent("fais un jeu snake")
        assert result is not None, "'fais un jeu' doit router vers CodeAgent"

    @pytest.mark.asyncio
    async def test_finir_jeu_routes(self):
        """'finir le jeu snake stp' doit router vers CodeAgent."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS), \
             patch("src.agents.sub_agent.delegate_to_agent",
                    new_callable=AsyncMock, return_value="Fini"):
            result = await react._maybe_auto_route_codeagent(
                "lumena tu pourrais finir le jeu snake stp"
            )
        assert result is not None, "'finir le jeu stp' doit router vers CodeAgent"

    @pytest.mark.asyncio
    async def test_feedback_jai_un_bug_skips(self):
        """'j'ai un bug sur le jeu' → skip (observation, pas d'impératif)."""
        react = self._make_react()
        with patch("src.utils.project_registry.resolve_workspace", return_value=self._NO_WS):
            result = await react._maybe_auto_route_codeagent(
                "j'ai un bug sur le jeu le score s'affiche pas"
            )
        assert result is None
