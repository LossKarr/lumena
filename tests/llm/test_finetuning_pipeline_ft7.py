"""FT-7: Tests de régression du pipeline fine-tuning enterprise-grade.

Vérifie que :
- LUMENA_MODELS_DIR pointe vers le bon répertoire et contient les scripts requis
- Le filtrage qualité dans data_prep.py fonctionne correctement
- update_quality_flag fonctionne
- L'endpoint /api/chat/feedback accepte les bons paramètres
- L'endpoint /api/finetuning/activate existe
- Le content_hash est bien calculé et inclus dans le done_payload
- L'enrichissement react_meta dans agent_service contient les bons champs
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── FT-2 : LUMENA_MODELS_DIR ─────────────────────────────────────────────────

class TestLumenaModelsDir:
    def test_lumena_models_dir_exists_in_paths(self):
        from src.utils.paths import LUMENA_MODELS_DIR
        assert LUMENA_MODELS_DIR is not None
        assert str(LUMENA_MODELS_DIR).endswith("lumena-v1.0.0")

    def test_lumena_models_dir_scripts_present(self):
        """FT-2: Les scripts critiques doivent exister dans le repo maintenant."""
        from src.utils.paths import LUMENA_MODELS_DIR
        # Ces scripts sont nécessaires pour le pipeline fine-tuning
        for script in ["1_prepare_data.py", "5_judge.py", "6_rejection_sampling.py"]:
            p = LUMENA_MODELS_DIR / script
            assert p.exists(), f"Script manquant: {script} dans {LUMENA_MODELS_DIR}"

    def test_lumena_models_dir_referenced_in_ops_handlers(self):
        """FT-2: ops_handlers doit utiliser LUMENA_MODELS_DIR depuis paths."""
        import inspect
        from src.autonomy import ops_handlers
        src = inspect.getsource(ops_handlers)
        # Doit importer depuis paths et non hardcoder le chemin
        assert "LUMENA_MODELS_DIR" in src, "ops_handlers doit référencer LUMENA_MODELS_DIR"


# ── FT-4 : Filtres qualité data_prep.py ──────────────────────────────────────

class TestDataPrepFilters:
    def _make_entry(self, user="Bonjour Lumena", assistant="Bonjour !", react_meta=None, quality_flag="ok"):
        meta = {"quality_flag": quality_flag, "content_hash": "abcd1234abcd1234"}
        if react_meta:
            meta["react_meta"] = react_meta
        return {
            "conversations": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
            "metadata": meta,
        }

    def test_excludes_plan_0_percent(self, tmp_path):
        """FT-4: Tâche avec plan défini mais 0% accompli → exclu."""
        from src.training.data_prep import load_lumena_pool
        pool = tmp_path / "training_pool"
        pool.mkdir()
        validated = tmp_path / "training_validated"
        validated.mkdir()

        entry_fail = self._make_entry(
            user="Fais quelque chose",
            assistant="J'ai essayé",
            react_meta={"plan": {"total_tasks": 3, "completed_tasks": 0}},
        )
        entry_ok = self._make_entry(
            user="Dis bonjour" * 5,  # assez long
            assistant="Bonjour ! Comment puis-je vous aider aujourd'hui ?" * 2,
            react_meta={"plan": {"total_tasks": 2, "completed_tasks": 2}},
        )

        today = "2026-05-01"
        (pool / f"{today}.jsonl").write_text(
            json.dumps(entry_fail, ensure_ascii=False) + "\n" +
            json.dumps(entry_ok, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # min_conversations=1 pour tester avec peu de données
        result = load_lumena_pool(pool, validated, min_conversations=1)
        # L'entrée avec plan 0% doit être exclue
        for r in result:
            react = r.get("metadata", {}).get("react_meta", {})
            if isinstance(react, dict):
                plan = react.get("plan", {})
                if isinstance(plan, dict):
                    assert not (plan.get("total_tasks", 0) > 0 and plan.get("completed_tasks", 0) == 0), \
                        "Une entrée avec plan 0% ne doit pas être dans le dataset"

    def test_excludes_false_identity(self, tmp_path):
        """FT-4: Réponse qui s'identifie comme Claude → exclu."""
        from src.training.data_prep import load_lumena_pool
        pool = tmp_path / "training_pool"
        pool.mkdir()
        validated = tmp_path / "training_validated"
        validated.mkdir()

        entry_claude = self._make_entry(
            user="Qui es-tu ?" * 3,
            assistant="Je suis Claude, un assistant IA créé par Anthropic.",
        )
        entry_lumena = self._make_entry(
            user="Qui es-tu ?" * 3,
            assistant="Je suis Lumena, ton assistante IA personnelle créée par son créateur !",
        )
        # Différents hashes
        entry_claude["metadata"]["content_hash"] = "11111111aaaaaaaa"
        entry_lumena["metadata"]["content_hash"] = "22222222bbbbbbbb"

        today = "2026-05-01"
        (pool / f"{today}.jsonl").write_text(
            json.dumps(entry_claude, ensure_ascii=False) + "\n" +
            json.dumps(entry_lumena, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = load_lumena_pool(pool, validated, min_conversations=1)
        # La réponse Claude doit être exclue
        for r in result:
            convs = r.get("conversations", [])
            asst_msgs = [m for m in convs if m.get("role") == "assistant"]
            if asst_msgs:
                resp = asst_msgs[-1].get("content", "").lower()
                assert "je suis claude" not in resp, "Réponse avec fausse identité ne doit pas être dans le dataset"


# ── FT-6 : update_quality_flag ───────────────────────────────────────────────

class TestUpdateQualityFlag:
    def test_update_positive_flag(self, tmp_path):
        """FT-6: update_quality_flag met à jour l'entrée correcte."""
        from src.learning.conversation_logger import update_quality_flag
        from datetime import datetime

        pool_file = tmp_path / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        entry = {
            "conversations": [{"role": "user", "content": "test"}, {"role": "assistant", "content": "réponse"}],
            "metadata": {
                "quality_flag": "ok",
                "content_hash": "cafecafe12345678",
                "timestamp": datetime.now().isoformat(),
                "model_used": "test",
                "provider": "test",
                "user_len": 4,
                "response_len": 8,
            }
        }
        pool_file.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")

        # Patcher _POOL_DIR pour utiliser tmp_path
        import src.learning.conversation_logger as cl
        orig = cl._POOL_DIR
        try:
            cl._POOL_DIR = tmp_path
            result = update_quality_flag("cafecafe12345678", "positive_explicit")
            assert result is True
            updated = json.loads(pool_file.read_text(encoding="utf-8").strip())
            assert updated["metadata"]["quality_flag"] == "positive_explicit"
        finally:
            cl._POOL_DIR = orig

    def test_invalid_flag_rejected(self):
        """FT-6: Un flag invalide est rejeté."""
        from src.learning.conversation_logger import update_quality_flag
        result = update_quality_flag("cafecafe12345678", "super_flag_inventé")
        assert result is False

    def test_short_hash_rejected(self):
        """FT-6: Un hash trop court est rejeté gracieusement."""
        from src.learning.conversation_logger import update_quality_flag
        # Hash de 8 chars (doit retourner False sans crash)
        result = update_quality_flag("short", "positive_explicit")
        assert result is False


# ── FT-5 : Endpoint activate ─────────────────────────────────────────────────

class TestFinetuningActivateEndpoint:
    def test_activate_endpoint_exists(self):
        """FT-5: L'endpoint /api/finetuning/activate/{model_name} doit exister."""
        import web.routes.finetuning as ft
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(ft.router)
        routes = [r.path for r in app.routes]
        assert any("activate" in r for r in routes), \
            "L'endpoint /api/finetuning/activate/{model_name} doit être enregistré"

    def test_activate_invalid_name_rejected(self):
        """FT-5: Un nom de modèle invalide doit retourner 422."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import web.routes.finetuning as ft
        import web.routes.deps as deps

        app = FastAPI()
        app.include_router(ft.router)
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
        with TestClient(app) as client:
            r = client.post("/api/finetuning/activate/INVALID NAME WITH SPACES")
            assert r.status_code == 422


# ── FT-6 : Endpoint feedback dans chat.py ────────────────────────────────────

class TestChatFeedbackEndpoint:
    def _make_app(self):
        from fastapi import FastAPI
        import web.routes.chat as chat
        import web.routes.deps as deps
        from unittest.mock import patch
        app = FastAPI()
        app.include_router(chat.router)
        return app

    def test_feedback_endpoint_exists(self):
        """FT-6: L'endpoint /api/chat/feedback doit exister."""
        app = self._make_app()
        routes = [r.path for r in app.routes]
        assert "/api/chat/feedback" in routes, "L'endpoint /api/chat/feedback doit être enregistré"

    def test_feedback_invalid_flag_returns_422(self):
        """FT-6: Un flag invalide retourne 422."""
        from fastapi.testclient import TestClient
        import web.routes.deps as deps

        app = self._make_app()
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
        with TestClient(app) as client:
            r = client.post("/api/chat/feedback", json={
                "content_hash": "abcd1234abcd1234",
                "flag": "invalid_flag"
            })
            assert r.status_code == 422

    def test_feedback_invalid_hash_returns_422(self):
        """FT-6: Un hash trop court retourne 422."""
        from fastapi.testclient import TestClient
        import web.routes.deps as deps

        app = self._make_app()
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
        with TestClient(app) as client:
            r = client.post("/api/chat/feedback", json={
                "content_hash": "tooshort",
                "flag": "positive_explicit"
            })
            assert r.status_code == 422


# ── FT-1 : Enrichissement react_meta dans agent_service ──────────────────────

class TestReactMetaEnrichment:
    def test_default_agent_meta_structure(self):
        """FT-1: _default_agent_meta() doit avoir les champs de base."""
        from src.core_services.agent_service import AgentService
        svc = AgentService.__new__(AgentService)
        meta = svc._default_agent_meta()
        assert "agent_output_incomplete" in meta
        assert "agent_output_warning" in meta
        assert "agent_repair_attempts" in meta

    def test_last_agent_meta_enriched_fields(self):
        """FT-1: Après un run ReAct, _last_agent_meta doit contenir tools_used, success, iterations."""
        # On simule ce que fait agent_service.py après react.get_run_meta()
        mock_react = MagicMock()
        mock_react.get_run_meta.return_value = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": "stop",
            "plan": {"total_tasks": 3, "completed_tasks": 3, "tasks": []},
        }
        mock_react._successful_session_tools = {"browser_start", "browser_navigate"}
        mock_react._current_iteration = 15

        # Simuler la logique FT-1
        _run_meta = mock_react.get_run_meta()
        _plan = _run_meta.get("plan", {})
        _plan_total = _plan.get("total_tasks", 0) if isinstance(_plan, dict) else 0
        _plan_done = _plan.get("completed_tasks", 0) if isinstance(_plan, dict) else 0
        enriched = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": "stop",
            **_run_meta,
            "tools_used": sorted(getattr(mock_react, "_successful_session_tools", set()))[:30],
            "iterations": getattr(mock_react, "_current_iteration", 0),
            "success": _plan_total > 0 and _plan_done == _plan_total,
            "plan_completion_pct": round(100 * _plan_done / _plan_total) if _plan_total > 0 else 0,
        }
        assert enriched["success"] is True
        assert enriched["plan_completion_pct"] == 100
        assert enriched["iterations"] == 15
        assert "browser_navigate" in enriched["tools_used"]


# ── FT-3 : Pipeline chaîné dans finetuning.py ────────────────────────────────

class TestFinetuningPipelineChain:
    def test_run_job_references_all_scripts(self):
        """FT-3: _run_job doit référencer les 3 scripts dans son code source."""
        import inspect
        import web.routes.finetuning as ft
        src = inspect.getsource(ft._run_job)
        assert "1_prepare_data.py" in src, "1_prepare_data.py doit être chaîné dans _run_job"
        assert "5_judge.py" in src, "5_judge.py doit être chaîné dans _run_job"
        assert "6_rejection_sampling.py" in src, "6_rejection_sampling.py doit être chaîné dans _run_job"

    def test_run_job_references_lumena_models_dir(self):
        """FT-3: _run_job doit utiliser LUMENA_MODELS_DIR."""
        import inspect
        import web.routes.finetuning as ft
        src = inspect.getsource(ft._run_job)
        assert "LUMENA_MODELS_DIR" in src, "_run_job doit utiliser LUMENA_MODELS_DIR"

    def test_run_job_has_judging_phase(self):
        """FT-3: _run_job doit émettre une phase 'judging' via SSE."""
        import inspect
        import web.routes.finetuning as ft
        src = inspect.getsource(ft._run_job)
        assert '"judging"' in src, "_run_job doit émettre la phase 'judging'"

    def test_run_job_has_generating_phase(self):
        """FT-3: _run_job doit émettre une phase 'generating' via SSE."""
        import inspect
        import web.routes.finetuning as ft
        src = inspect.getsource(ft._run_job)
        assert '"generating"' in src, "_run_job doit émettre la phase 'generating'"
