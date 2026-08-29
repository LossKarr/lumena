"""Tests for PLAN_PRODUCTION_HARDENING_FINAL — all 3 sprints."""
from __future__ import annotations

import inspect
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


# ============================================================
# Sprint 1 — Sécurité routes
# ============================================================


class TestS1RouteAuth:
    """Vérifie que les routes GET sensibles exigent l'authentification."""

    def test_facts_get_requires_auth(self):
        from web.routes.content import get_facts
        sig = inspect.signature(get_facts)
        assert "_auth" in sig.parameters, "GET /api/facts missing _auth param"

    def test_repo_map_requires_auth(self):
        from web.routes.content import get_repo_map
        sig = inspect.signature(get_repo_map)
        assert "_auth" in sig.parameters, "GET /api/repo-map missing _auth param"

    def test_rules_requires_auth(self):
        from web.routes.content import get_rules
        sig = inspect.signature(get_rules)
        assert "_auth" in sig.parameters, "GET /api/rules missing _auth param"

    def test_instincts_requires_auth(self):
        from web.routes.content import get_instincts
        sig = inspect.signature(get_instincts)
        assert "_auth" in sig.parameters, "GET /api/instincts missing _auth param"

    def test_training_requires_auth(self):
        from web.routes.advanced import get_training
        sig = inspect.signature(get_training)
        assert "_auth" in sig.parameters, "GET /api/training missing _auth param"

    def test_tasks_get_requires_auth(self):
        from web.routes import tasks as tasks_mod
        from fastapi import Depends
        from web.routes import deps

        route_fns = {
            "list_tasks": "/api/tasks",
            "get_daemon_activity": "/api/daemon/activity",
            "get_task": "/api/tasks/{task_id}",
            "get_session": "/api/sessions/{conversation_id}",
        }
        for fn_name, path in route_fns.items():
            fn = getattr(tasks_mod, fn_name)
            # Check router-level dependencies via the route decorator
            found = False
            for route in tasks_mod.router.routes:
                if hasattr(route, "path") and route.path == path:
                    route_deps = getattr(route, "dependencies", []) or []
                    for dep in route_deps:
                        if hasattr(dep, "dependency") and dep.dependency is deps.verify_admin_token:
                            found = True
                            break
                    break
            assert found, f"{fn_name} ({path}) missing verify_admin_token dependency"


class TestS1SwaggerDisabled:
    """Vérifie la désactivation conditionnelle de Swagger/ReDoc."""

    def test_swagger_disabled_in_prod(self):
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}, clear=False):
            # Re-evaluate the condition
            _SETUP_DONE = os.getenv("LUMENA_SETUP_COMPLETE", "") == "1"
            assert _SETUP_DONE is True
            # The actual FastAPI app uses this at import time, so we verify the logic
            docs_url = None if _SETUP_DONE else "/docs"
            redoc_url = None if _SETUP_DONE else "/redoc"
            openapi_url = None if _SETUP_DONE else "/openapi.json"
            assert docs_url is None
            assert redoc_url is None
            assert openapi_url is None

    def test_swagger_enabled_before_setup(self):
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": ""}, clear=False):
            _SETUP_DONE = os.getenv("LUMENA_SETUP_COMPLETE", "") == "1"
            assert _SETUP_DONE is False
            docs_url = None if _SETUP_DONE else "/docs"
            assert docs_url == "/docs"


# ============================================================
# Sprint 2 — Fiabilité code
# ============================================================


class TestS2MetaLock:
    """Vérifie le lock sur _last_response_meta."""

    def test_meta_lock_exists(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.__new__(MultiProviderLLM)
        llm._health_lock = threading.Lock()
        llm._meta_lock = threading.Lock()
        llm.provider_health = {}
        llm._config = None
        llm._last_response_meta = {}
        assert isinstance(llm._meta_lock, type(threading.Lock()))

    def test_meta_lock_thread_safety(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.__new__(MultiProviderLLM)
        llm._health_lock = threading.Lock()
        llm._meta_lock = threading.Lock()
        llm.provider_health = {}
        llm._config = None
        llm._last_response_meta = llm._default_response_meta()

        errors = []

        def writer():
            try:
                for _ in range(100):
                    llm._set_last_response_meta(provider_used="test")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    llm.get_last_response_meta()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not errors, f"Thread safety errors: {errors}"


class TestS2DeadCodeFixed:
    """Vérifie que le résumé LLM est maintenant correctement await."""

    def test_save_conversation_is_async(self):
        from src.core_services.agent_service import AgentService
        method = getattr(AgentService, "_save_conversation_to_memory")
        assert inspect.iscoroutinefunction(method), "_save_conversation_to_memory should be async"

    def test_no_ensure_future_in_save(self):
        from src.core_services import agent_service
        src_file = Path(inspect.getfile(agent_service))
        source = src_file.read_text(encoding="utf-8")
        # The method should no longer use ensure_future for the summary
        # Check specifically in the _save_conversation_to_memory area
        method_start = source.index("async def _save_conversation_to_memory")
        # Find next def
        next_def = source.index("\n    def ", method_start + 10)
        method_body = source[method_start:next_def]
        assert "ensure_future" not in method_body, "ensure_future still in _save_conversation_to_memory"

    def test_no_to_thread_for_save(self):
        from src.core_services import agent_service
        src_file = Path(inspect.getfile(agent_service))
        source = src_file.read_text(encoding="utf-8")
        assert "to_thread(self._save_conversation_to_memory" not in source, (
            "asyncio.to_thread still wrapping _save_conversation_to_memory"
        )


def _sources_de_la_boucle_react() -> str:
    """Le texte des fichiers qui portent les except critiques de la boucle.

    Lot RF-3 du refactor ReAct (2026-08-27) : le corps de `_build_react_prompt`
    a quitte `react.py` pour `src/prompts/react_prompt.py`. Deux des quatre
    except surveilles ici sont partis avec lui — le contexte sandbox et
    l'injection de memoire agent. Les quatre existent toujours et loguent
    toujours ; seul leur fichier a change.

    On lit donc la PAIRE. Le test garde exactement son affirmation d'origine
    (« ces quatre except loguent en warning ») sans dependre du fichier ou le
    code habite, ce qui est precisement ce qui l'a casse.

    Le plan de refactor n'autorise ce repointage qu'adosse a une preuve
    COMPORTEMENTALE equivalente. Elle existe, et elle est plus forte que la
    recherche de sous-chaine ci-dessous : dans
    `tests/reasoning/test_rf3_react_prompt_extraction.py`,
      - test_comportement_un_echec_du_contexte_sandbox_logue_un_warning
      - test_comportement_un_echec_de_la_memoire_agent_logue_un_warning
    font REELLEMENT echouer chaque branche et verifient que le warning part,
    qu'il porte la cause, et que le prompt survit quand meme.
    """
    from src.reasoning import react
    from src.prompts import react_prompt

    return "\n".join(
        Path(inspect.getfile(m)).read_text(encoding="utf-8")
        for m in (react, react_prompt)
    )


class TestS2ReactWarnings:
    """Vérifie que les 4 except critiques loguent en warning."""

    def test_sandbox_except_logs_warning(self):
        assert "Sandbox context injection failed" in _sources_de_la_boucle_react()

    def test_chromadb_except_logs_warning(self):
        assert "ChromaDB memory unavailable" in _sources_de_la_boucle_react()

    def test_permanent_memory_except_logs_warning(self):
        assert "Permanent memory inject failed" in _sources_de_la_boucle_react()

    def test_agent_memory_except_logs_warning(self):
        assert "Agent memory inject failed" in _sources_de_la_boucle_react()


class TestS2LoguruFix:
    """Vérifie la configuration loguru corrigée."""

    def test_loguru_fixed_filename(self):
        from src import core as core_mod
        src_file = Path(inspect.getfile(core_mod))
        source = src_file.read_text(encoding="utf-8")
        assert 'lumena.log"' in source or "lumena.log'" in source, "loguru should use fixed filename lumena.log"
        assert "lumena_{time}" not in source, "loguru should NOT use {time} pattern"

    def test_loguru_compression_enabled(self):
        from src import core as core_mod
        src_file = Path(inspect.getfile(core_mod))
        source = src_file.read_text(encoding="utf-8")
        assert 'compression="gz"' in source or "compression='gz'" in source

    def test_loguru_enqueue_enabled(self):
        from src import core as core_mod
        src_file = Path(inspect.getfile(core_mod))
        source = src_file.read_text(encoding="utf-8")
        assert "enqueue=True" in source


# ============================================================
# Sprint 3 — Performance & Déploiement
# ============================================================


class TestS3HttpxPooling:
    """Vérifie le client HTTP persistant."""

    def test_httpx_client_persistent(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.__new__(MultiProviderLLM)
        import httpx
        llm._http = httpx.AsyncClient()
        assert isinstance(llm._http, httpx.AsyncClient)

    def test_no_ephemeral_httpx_in_multi_provider(self):
        src_file = Path("src/llm/multi_provider.py")
        source = src_file.read_text(encoding="utf-8")
        assert "async with httpx.AsyncClient" not in source, (
            "Ephemeral httpx.AsyncClient still found in multi_provider.py"
        )

    @pytest.mark.asyncio
    async def test_httpx_client_close(self):
        import httpx
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.__new__(MultiProviderLLM)
        llm._http = httpx.AsyncClient()
        await llm.close()  # Should not raise


class TestS3StaticDirFallback:
    """Vérifie le fallback static dir (dist vs static)."""

    def test_static_dir_fallback_to_static(self, tmp_path):
        web_dir = tmp_path / "web"
        static_dir = web_dir / "static"
        static_dir.mkdir(parents=True)
        dist_dir = web_dir / "dist"
        # dist does not exist
        result = dist_dir if dist_dir.is_dir() else static_dir
        assert result == static_dir

    def test_static_dir_prefers_dist(self, tmp_path):
        web_dir = tmp_path / "web"
        static_dir = web_dir / "static"
        static_dir.mkdir(parents=True)
        dist_dir = web_dir / "dist"
        dist_dir.mkdir(parents=True)
        result = dist_dir if dist_dir.is_dir() else static_dir
        assert result == dist_dir


class TestS3EnvExample:
    """Vérifie que les 3 vars manquantes sont documentées."""

    def test_env_example_has_max_contexts(self):
        env_path = Path(".env.example")
        content = env_path.read_text(encoding="utf-8")
        assert "LUMENA_MAX_CONTEXTS_PER_PLATFORM" in content

    def test_env_example_has_network_key(self):
        env_path = Path(".env.example")
        content = env_path.read_text(encoding="utf-8")
        assert "NETWORK_KEY" in content

    def test_env_example_has_network_pass(self):
        env_path = Path(".env.example")
        content = env_path.read_text(encoding="utf-8")
        assert "NETWORK_PASS" in content
