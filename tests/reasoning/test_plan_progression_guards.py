"""
🧪 Tests — Guards de progression du plan (B, A, C)

Mission B : Guard 5 — un outil d'exploration ne peut pas auto-avancer une tâche métier
Mission A : remember_code_context / get_recent_code_context — continuité projet multi-tour
Mission C : delegate_task_handler utilise le contexte récent si project_path absent
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from src.reasoning.react import ReActLoop, ToolRegistry
from src.reasoning.plan_evidence import _EXPLORATION_TOOLS_STRICT, _BUSINESS_ACTION_STARTERS
from src.core_services.identity_service import IdentityService


# ════════════════════════════════════════════════════════════════════════════════
# Mission B — Guard 5 : outil exploration ne peut pas marquer une tâche métier
# ════════════════════════════════════════════════════════════════════════════════

class TestGuard5BusinessTaskNotCompletedByExploration:
    """Guard 5 : les outils d'exploration ne peuvent pas auto-avancer une tâche métier."""

    def _make_loop_with_plan(self, tasks: list[str]) -> ReActLoop:
        from src.reasoning.react import TaskItem
        loop = ReActLoop(llm_chat_func=None)
        loop._task_plan = [TaskItem(description=t) for t in tasks]
        return loop

    def test_run_command_cannot_mark_deleguer_task(self):
        """run_command ne peut pas auto-avancer 'Déléguer la correction au CodeAgent'."""
        loop = self._make_loop_with_plan([
            "Déléguer la correction au CodeAgent",
        ])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="run_command",
            tool_args={"command": "cd workspace\\rolling-ball-labyrinth"},
            observation_content="C:\\Users\\charl\\Desktop\\lumena\\workspace\\rolling-ball-labyrinth",
            iteration=1,
        )
        assert not loop._task_plan[0].completed, (
            "Guard 5 : run_command ne peut pas cocher une tâche qui débute par 'déléguer'"
        )

    def test_find_files_cannot_mark_corriger_task(self):
        """find_files ne peut pas auto-avancer 'Corriger le bug de physique'."""
        loop = self._make_loop_with_plan(["Corriger le bug de physique"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="find_files",
            tool_args={"pattern": "*.js"},
            observation_content="✅ 3 fichiers trouvés",
            iteration=1,
        )
        assert not loop._task_plan[0].completed

    def test_list_directory_cannot_mark_envoyer_task(self):
        loop = self._make_loop_with_plan(["Envoyer le rapport par mail"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="list_directory",
            tool_args={"path": "."},
            observation_content="✅ rapport.pdf présent",
            iteration=1,
        )
        assert not loop._task_plan[0].completed

    def test_read_file_cannot_mark_creer_task(self):
        loop = self._make_loop_with_plan(["Créer le fichier config.json"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="read_file",
            tool_args={"path": "config.json"},
            observation_content="✅ contenu lu",
            iteration=1,
        )
        assert not loop._task_plan[0].completed

    def test_write_file_CAN_mark_creer_task(self):
        """write_file N'est PAS un outil d'exploration → peut marquer une tâche création."""
        loop = self._make_loop_with_plan(["Créer le fichier config.json"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="write_file",
            tool_args={"path": "config.json"},
            observation_content="✅ Fichier écrit: config.json",
            iteration=1,
        )
        assert loop._task_plan[0].completed, (
            "write_file (mutation) doit pouvoir marquer une tâche création"
        )

    def test_run_command_can_mark_non_business_task(self):
        """run_command peut auto-avancer une tâche sans verbe d'action métier en tête."""
        loop = self._make_loop_with_plan(["Vérifier que le serveur est lancé"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="run_command",
            tool_args={"command": "curl localhost:3000"},
            observation_content="✅ HTTP 200 OK — serveur actif",
            iteration=1,
        )
        # "vérifier" n'est pas dans _BUSINESS_ACTION_STARTERS → Guard 5 ne bloque pas
        assert loop._task_plan[0].completed

    def test_parallel_tools_cannot_mark_deleguer_task(self):
        """parallel_tools ne doit pas contourner Guard 5 sur une tâche métier."""
        loop = self._make_loop_with_plan(["Déléguer la correction au CodeAgent"])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="parallel_tools",
            tool_args={"tools": [{"tool": "read_file", "args": {"path": "js/game.js"}}]},
            observation_content="✅ 2 outils exécutés en paralléle",
            iteration=1,
        )
        assert not loop._task_plan[0].completed

    def test_delegate_report_without_urls_cannot_credit_sourced_research(self):
        loop = self._make_loop_with_plan([
            "Étape 3: Rechercher 2 recommandations énergétiques sourcées"
        ])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="delegate_and_wait",
            tool_args={"objectives": ["rechercher des recommandations"]},
            observation_content="Les workers ont trouvé deux recommandations fiables.",
            iteration=1,
        )
        assert not loop._task_plan[0].completed

    def test_delegate_report_with_urls_can_credit_sourced_research(self):
        loop = self._make_loop_with_plan([
            "Étape 3: Rechercher 2 recommandations énergétiques sourcées"
        ])
        loop._last_auto_advance_iter = -1
        loop._update_plan_progress(
            tool_name="delegate_and_wait",
            tool_args={"objectives": ["rechercher des recommandations"]},
            observation_content=(
                "Deux recommandations: https://ademe.fr/guide et "
                "https://ecologie.gouv.fr/aides"
            ),
            iteration=1,
        )
        assert loop._task_plan[0].completed

    def test_business_action_starters_coverage(self):
        """Les verbes métier clés sont bien dans _BUSINESS_ACTION_STARTERS."""
        for verb in ("déléguer", "corriger", "créer", "envoyer", "modifier", "déployer"):
            assert verb in _BUSINESS_ACTION_STARTERS, f"'{verb}' absent de _BUSINESS_ACTION_STARTERS"

    def test_exploration_tools_strict_coverage(self):
        """Les outils d'exploration critiques sont bien dans _EXPLORATION_TOOLS_STRICT."""
        for tool in ("run_command", "find_files", "list_directory", "read_file", "grep_search", "parallel_tools"):
            assert tool in _EXPLORATION_TOOLS_STRICT, f"'{tool}' absent de _EXPLORATION_TOOLS_STRICT"


# ════════════════════════════════════════════════════════════════════════════════
# Mission A — IdentityService : mémorisation et récupération du contexte projet
# ════════════════════════════════════════════════════════════════════════════════

class TestIdentityServiceCodeContext:
    """Tests de remember_code_context / get_recent_code_context."""

    def _make_svc(self) -> IdentityService:
        ctx = SimpleNamespace(
            data_dir=Path("."),
            llm=None, memory=None, tts=None, emotion_manager=None,
            tool_system=None, repo_map=None, code_index=None,
            rules_loader=None, hook_system=None, instinct_system=None,
            auto_speak=False,
        )
        return IdentityService(ctx, tg_contexts=OrderedDict(), discord_contexts=OrderedDict(), discord_users={})

    def test_remember_and_retrieve(self):
        svc = self._make_svc()
        svc.remember_code_context(
            channel_key="discord:12345",
            workspace_path="/project/workspace/mon-jeu",
            project_slug="mon-jeu",
        )
        result = svc.get_recent_code_context("discord:12345")
        assert result is not None
        assert result["workspace_path"] == "/project/workspace/mon-jeu"
        assert result["project_slug"] == "mon-jeu"

    def test_ttl_expiry(self):
        svc = self._make_svc()
        svc._code_context_ttl = 0.01  # 10ms TTL
        svc.remember_code_context("web:default", "/workspace/test")
        time.sleep(0.05)
        assert svc.get_recent_code_context("web:default") is None

    def test_unknown_channel_returns_none(self):
        svc = self._make_svc()
        assert svc.get_recent_code_context("telegram:unknown") is None

    def test_empty_channel_key_no_crash(self):
        svc = self._make_svc()
        svc.remember_code_context("", "/workspace/test")  # must not raise
        assert svc.get_recent_code_context("") is None

    def test_overwrite_updates_context(self):
        svc = self._make_svc()
        svc.remember_code_context("web:s1", "/workspace/old-project")
        svc.remember_code_context("web:s1", "/workspace/new-project", project_slug="new")
        result = svc.get_recent_code_context("web:s1")
        assert result["workspace_path"] == "/workspace/new-project"

    def test_resolve_channel_key_telegram(self):
        rt = SimpleNamespace(channel="telegram", session_id=None)
        sender = {"id": "42"}
        key = IdentityService.resolve_channel_key(rt, sender)
        assert key == "telegram:42"

    def test_resolve_channel_key_no_sender(self):
        # Phase 0 : la clé utilise user_id + conversation_id, pas session_id
        rt = SimpleNamespace(channel="web", user_id="local:owner", conversation_id="sess123")
        key = IdentityService.resolve_channel_key(rt)
        assert "web" in key and "local:owner" in key and "sess123" in key

    def test_resolve_channel_key_fallback(self):
        rt = SimpleNamespace(channel="web")
        # Ni user_id ni session_id → fallback sur user_id=local:owner
        key = IdentityService.resolve_channel_key(rt)
        assert "web" in key


# ════════════════════════════════════════════════════════════════════════════════
# Mission A — Injection du contexte récent dans le prompt (intégration légère)
# ════════════════════════════════════════════════════════════════════════════════

class TestRecentProjectPromptInjection:
    """Vérifie que le contexte projet récent est injecté dans le prompt quand pertinent."""

    @pytest.mark.asyncio
    async def test_recent_project_injected_on_continuation(self, tmp_path: Path):
        """Quand une requête est une continuation, le chemin du projet récent doit être
        mentionné dans le prompt envoyé au LLM."""
        prompts_seen: list[str] = []
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            # Capturer le contenu du prompt système
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    prompts_seen.append(m.get("content", ""))
            return (
                "THOUGHT: Je réponds.\n"
                "ACTION: FINAL\n"
                "ACTION_INPUT: Voici la réponse."
            )

        # Préparer identity_service avec un contexte récent
        from src.core_services.identity_service import IdentityService
        from collections import OrderedDict

        _ctx_obj = SimpleNamespace(
            data_dir=tmp_path,
            llm=None, memory=None, tts=None, emotion_manager=None,
            tool_system=None, repo_map=None, code_index=None,
            rules_loader=None, hook_system=None, instruct_system=None,
            instinct_system=None, auto_speak=False,
        )
        svc = IdentityService(_ctx_obj, tg_contexts=OrderedDict(), discord_contexts=OrderedDict(), discord_users={})
        # Phase 0 : la clé utilise user_id, pas session_id
        svc.remember_code_context(
            "web:local:owner",
            str(tmp_path / "workspace" / "mon-jeu"),
            project_slug="mon-jeu",
        )

        # Créer le dossier pour que os.path.isdir soit True dans le prompt building
        (tmp_path / "workspace" / "mon-jeu").mkdir(parents=True, exist_ok=True)

        # Mock lumena avec l'identity_service
        _lum_mock = SimpleNamespace(
            _identity_svc=svc,
            runtime_root=tmp_path,
        )
        registry = ToolRegistry(lumena=_lum_mock, lumena_root=tmp_path)

        # RuntimeContext pointant vers le bon canal (user_id, pas session_id)
        rt = SimpleNamespace(channel="web", user_id="local:owner")

        loop = ReActLoop(
            llm_chat_func=_llm,
            tools=registry,
            runtime_ctx=rt,
        )
        await loop.run("corrige le bug dans le jeu")

        # Le prompt doit mentionner le chemin du projet récent
        all_prompts = "\n".join(prompts_seen)
        assert "mon-jeu" in all_prompts or str(tmp_path / "workspace" / "mon-jeu") in all_prompts, (
            "Le chemin du projet récent doit apparaître dans le prompt sur une requête de continuation. "
            f"Prompts vus : {all_prompts[:500]}"
        )

    @pytest.mark.asyncio
    async def test_no_injection_when_not_continuation(self, tmp_path: Path):
        """Sans mot-clé de continuation, le contexte récent n'est PAS injecté."""
        prompts_seen: list[str] = []

        async def _llm(messages, **kw):
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    prompts_seen.append(m.get("content", ""))
            return "THOUGHT: OK.\nACTION: FINAL\nACTION_INPUT: Réponse."

        from src.core_services.identity_service import IdentityService
        from collections import OrderedDict

        _ctx_obj = SimpleNamespace(
            data_dir=tmp_path, llm=None, memory=None, tts=None,
            emotion_manager=None, tool_system=None, repo_map=None,
            code_index=None, rules_loader=None, hook_system=None,
            instinct_system=None, auto_speak=False,
        )
        svc = IdentityService(_ctx_obj, tg_contexts=OrderedDict(), discord_contexts=OrderedDict(), discord_users={})
        svc.remember_code_context("web:default", str(tmp_path / "workspace" / "mon-jeu"))
        (tmp_path / "workspace" / "mon-jeu").mkdir(parents=True, exist_ok=True)

        _lum_mock = SimpleNamespace(_identity_svc=svc, runtime_root=tmp_path)
        registry = ToolRegistry(lumena=_lum_mock, lumena_root=tmp_path)
        rt = SimpleNamespace(channel="web", session_id="default")

        loop = ReActLoop(llm_chat_func=_llm, tools=registry, runtime_ctx=rt)
        await loop.run("quelle est la météo à Paris ?")

        all_prompts = "\n".join(prompts_seen)
        # "quelle est la météo" n'est pas une continuation → pas d'injection
        assert "PROJET ACTIF RÉCENT" not in all_prompts, (
            "Le contexte récent ne doit pas être injecté sur une requête sans continuation"
        )


# ════════════════════════════════════════════════════════════════════════════════
# Mission C — delegate_task utilise le contexte récent si project_path absent
# ════════════════════════════════════════════════════════════════════════════════

class TestDelegateTaskUsesRecentContext:
    """delegate_task_handler préfère get_recent_code_context quand project_path vide."""

    @pytest.mark.asyncio
    async def test_delegate_task_uses_recent_path(self, tmp_path: Path):
        """Si project_path est vide mais qu'un contexte récent existe avec un dossier valide,
        TaskContext doit être résolu avec ce chemin."""
        from src.reasoning.handlers.agents import delegate_task_handler
        from src.reasoning.handlers.context import HandlerContext
        from src.core_services.identity_service import IdentityService
        from collections import OrderedDict

        # Créer un dossier projet réel
        project_dir = tmp_path / "workspace" / "mon-super-jeu"
        project_dir.mkdir(parents=True, exist_ok=True)

        # Préparer identity_service
        _ctx_obj = SimpleNamespace(
            data_dir=tmp_path, llm=None, memory=None, tts=None,
            emotion_manager=None, tool_system=None, repo_map=None,
            code_index=None, rules_loader=None, hook_system=None,
            instinct_system=None, auto_speak=False,
        )
        svc = IdentityService(_ctx_obj, tg_contexts=OrderedDict(), discord_contexts=OrderedDict(), discord_users={})
        # Phase 0 : la clé utilise user_id, pas session_id
        rt = SimpleNamespace(channel="web", user_id="local:owner")
        svc.remember_code_context("web:local:owner", str(project_dir), project_slug="mon-super-jeu")

        # Capturer l'appel TaskContext.from_delegate_call
        captured_project_path: list[str] = []
        from src.agents import task_context as _tc_mod
        _orig = _tc_mod.TaskContext.from_delegate_call.__func__

        @classmethod
        def _patched(cls, description, context=None, project_path="", **kw):
            captured_project_path.append(project_path)
            return _orig(cls, description, context=context, project_path=project_path, **kw)

        # Mock minimal lumena
        _lum_mock = SimpleNamespace(
            _identity_svc=svc,
            memory=None,
            runtime_root=tmp_path,
            runtime_ctx=rt,
        )
        _ctx_mock = SimpleNamespace(
            lumena=_lum_mock,
            runtime_root=tmp_path,
            runtime_ctx=rt,
        )

        original_from_delegate = _tc_mod.TaskContext.from_delegate_call
        _tc_mod.TaskContext.from_delegate_call = _patched

        try:
            # Appel sans project_path
            await delegate_task_handler(
                ctx=_ctx_mock,
                description="corrige le bug de physique",
                project_path="",
            )
        except Exception:
            pass  # On s'intéresse seulement au project_path capturé
        finally:
            _tc_mod.TaskContext.from_delegate_call = original_from_delegate

        assert captured_project_path, "from_delegate_call doit avoir été appelé"
        assert captured_project_path[0] == str(project_dir), (
            f"project_path attendu: {project_dir}, obtenu: {captured_project_path[0]}"
        )

    @pytest.mark.asyncio
    async def test_delegate_task_explicit_path_takes_priority(self, tmp_path: Path):
        """Un project_path explicite doit avoir la priorité sur le contexte récent."""
        from src.reasoning.handlers.agents import delegate_task_handler
        from src.reasoning.handlers.context import HandlerContext
        from src.core_services.identity_service import IdentityService
        from collections import OrderedDict

        explicit_dir = tmp_path / "workspace" / "projet-explicite"
        explicit_dir.mkdir(parents=True, exist_ok=True)
        recent_dir = tmp_path / "workspace" / "projet-recent"
        recent_dir.mkdir(parents=True, exist_ok=True)

        _ctx_obj = SimpleNamespace(
            data_dir=tmp_path, llm=None, memory=None, tts=None,
            emotion_manager=None, tool_system=None, repo_map=None,
            code_index=None, rules_loader=None, hook_system=None,
            instinct_system=None, auto_speak=False,
        )
        svc = IdentityService(_ctx_obj, tg_contexts=OrderedDict(), discord_contexts=OrderedDict(), discord_users={})
        rt = SimpleNamespace(channel="web", session_id="u2")
        svc.remember_code_context("web:u2", str(recent_dir), project_slug="recent")

        captured_project_path: list[str] = []
        from src.agents import task_context as _tc_mod
        _orig = _tc_mod.TaskContext.from_delegate_call.__func__

        @classmethod
        def _patched(cls, description, context=None, project_path="", **kw):
            captured_project_path.append(project_path)
            return _orig(cls, description, context=context, project_path=project_path, **kw)

        _lum_mock = SimpleNamespace(
            _identity_svc=svc, memory=None,
            runtime_root=tmp_path, runtime_ctx=rt,
        )
        _ctx_mock = SimpleNamespace(
            lumena=_lum_mock, runtime_root=tmp_path, runtime_ctx=rt,
        )
        original_from_delegate = _tc_mod.TaskContext.from_delegate_call
        _tc_mod.TaskContext.from_delegate_call = _patched

        try:
            await delegate_task_handler(
                ctx=_ctx_mock,
                description="travail sur le projet",
                project_path=str(explicit_dir),
            )
        except Exception:
            pass
        finally:
            _tc_mod.TaskContext.from_delegate_call = original_from_delegate

        assert captured_project_path, "from_delegate_call doit avoir été appelé"
        assert captured_project_path[0] == str(explicit_dir), (
            "L'explicit project_path doit avoir la priorité sur le contexte récent"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
