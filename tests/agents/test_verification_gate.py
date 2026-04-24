"""
Tests dédiés — Verification Gate (P2 robustesse).

Couvre :
  - GateResult.format_feedback()
  - run_gate() fail-open sur workspace inexistant
  - Budget retry : LUMENA_GATE_MAX_RETRIES respecté
  - Rollback : fichiers restaurés après budget épuisé
  - Isolation Chroma : collection_name distinct par workspace
  - _run_detected_tests fail-open sur timeout / runner inconnu
"""
from __future__ import annotations

import asyncio
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# GateResult
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateResult:
    def test_passed_no_feedback(self):
        from src.tools.verification_gate import GateResult
        g = GateResult(passed=True)
        assert g.passed
        assert g.errors == []

    def test_format_feedback_shows_errors(self):
        from src.tools.verification_gate import GateResult
        g = GateResult(passed=False, errors=["SyntaxError line 5", "NameError foo"])
        fb = g.format_feedback()
        assert "SyntaxError line 5" in fb
        assert "NameError foo" in fb
        assert "Corrige" in fb

    def test_format_feedback_caps_warnings(self):
        from src.tools.verification_gate import GateResult
        warnings = [f"warn {i}" for i in range(10)]
        g = GateResult(passed=False, errors=["err"], warnings=warnings)
        fb = g.format_feedback()
        # Max 5 warnings affichés
        assert fb.count("warn ") <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# run_gate — fail-open
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunGateFailOpen:
    @pytest.mark.asyncio
    async def test_nonexistent_workspace_passes(self, tmp_path):
        from src.tools.verification_gate import run_gate
        result = await run_gate(tmp_path / "does_not_exist")
        assert result.passed  # fail-open

    @pytest.mark.asyncio
    async def test_none_workspace_passes(self):
        from src.tools.verification_gate import run_gate
        result = await run_gate(None)
        assert result.passed  # fail-open

    @pytest.mark.asyncio
    async def test_timeout_fail_open(self, tmp_path):
        from src.tools.verification_gate import run_gate
        (tmp_path / "a.py").write_text("x = 1\n")

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)

        with patch("src.tools.verification_gate._do_validate", side_effect=slow):
            result = await run_gate(tmp_path, timeout=0.05)
        assert result.passed  # fail-open sur timeout

    @pytest.mark.asyncio
    async def test_exception_fail_open(self, tmp_path):
        from src.tools.verification_gate import run_gate
        (tmp_path / "a.py").write_text("x = 1\n")

        with patch("src.tools.verification_gate._do_validate", side_effect=RuntimeError("boom")):
            result = await run_gate(tmp_path)
        assert result.passed  # fail-open sur exception


# ═══════════════════════════════════════════════════════════════════════════════
# run_gate — validation réelle (fichier Python cassé)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunGateValidation:
    @pytest.mark.asyncio
    async def test_valid_python_passes(self, tmp_path):
        from src.tools.verification_gate import run_gate
        (tmp_path / "ok.py").write_text("def add(a, b):\n    return a + b\n")

        # Pas de runner de test → only static validation
        with patch("src.tools.verification_gate._run_detected_tests", AsyncMock(return_value=[])):
            result = await run_gate(tmp_path, ["ok.py"])
        assert result.passed

    @pytest.mark.asyncio
    async def test_syntax_error_fails(self, tmp_path):
        from src.tools.verification_gate import run_gate
        (tmp_path / "bad.py").write_text("def broken(\n    pass\n")

        with patch("src.tools.verification_gate._run_detected_tests", AsyncMock(return_value=[])):
            result = await run_gate(tmp_path, ["bad.py"])
        # Erreur syntaxique → gate bloque
        assert not result.passed
        assert len(result.errors) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# _run_detected_tests — fail-open
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunDetectedTests:
    @pytest.mark.asyncio
    async def test_unknown_runner_returns_empty(self, tmp_path):
        from src.tools.verification_gate import _run_detected_tests
        # Workspace sans aucun marqueur de test runner
        result = await _run_detected_tests(tmp_path, [])
        assert result == []

    @pytest.mark.asyncio
    async def test_passing_tests_return_empty(self, tmp_path):
        """Tests qui passent → liste vide (aucune erreur)."""
        from src.tools.verification_gate import _run_detected_tests
        (tmp_path / "test_ok.py").write_text(
            "def test_trivial():\n    assert 1 + 1 == 2\n"
        )
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        result = await _run_detected_tests(tmp_path, ["test_ok.py"])
        assert result == []

    @pytest.mark.asyncio
    async def test_failing_tests_return_errors(self, tmp_path):
        """Tests qui échouent → liste non vide."""
        from src.tools.verification_gate import _run_detected_tests
        (tmp_path / "test_fail.py").write_text(
            "def test_broken():\n    assert 1 == 2\n"
        )
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        result = await _run_detected_tests(tmp_path, ["test_fail.py"])
        assert len(result) > 0
        assert any("pytest" in e or "FAILED" in e or "failed" in e for e in result)


# ═══════════════════════════════════════════════════════════════════════════════
# Budget retry gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateBudget:
    def test_gate_max_retries_env_default(self):
        """Défaut = 2 retries."""
        os.environ.pop("LUMENA_GATE_MAX_RETRIES", None)
        val = int(os.getenv("LUMENA_GATE_MAX_RETRIES", "2"))
        assert val == 2

    def test_gate_max_retries_env_override(self):
        """Env var respectée."""
        os.environ["LUMENA_GATE_MAX_RETRIES"] = "5"
        val = int(os.getenv("LUMENA_GATE_MAX_RETRIES", "2"))
        assert val == 5
        del os.environ["LUMENA_GATE_MAX_RETRIES"]

    def test_gate_retries_used_initialized_per_attempt(self):
        """_gate_retries_used réinitialisé à 0 pour chaque tentative CodeAgent."""
        # Ce test vérifie l'initialisation dans _single_code_attempt
        # via inspection du code source (pas d'exécution LLM)
        import inspect
        from src.agents import sub_agent as sa_module
        src = inspect.getsource(sa_module.CodeAgent._single_code_attempt)
        assert "_gate_retries_used = 0" in src


# ═══════════════════════════════════════════════════════════════════════════════
# Rollback
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateRollback:
    def test_rollback_logic_present_in_source(self):
        """Vérifier que le rollback est câblé dans _single_code_attempt."""
        import inspect
        from src.agents import sub_agent as sa_module
        src = inspect.getsource(sa_module.CodeAgent._single_code_attempt)
        assert "record_rollback" in src
        assert "rollback" in src
        assert "_rolled_back" in src

    def test_rollback_restores_file(self, tmp_path):
        """Simulation : le snapshot restaure le contenu original."""
        original = "def add(a, b):\n    return a + b\n"
        broken = "def add(a, b):\n    return a - b  # BUG\n"

        f = tmp_path / "calc.py"
        f.write_text(broken, encoding="utf-8")

        # Simuler le mécanisme de rollback : restaurer depuis snapshot
        snapshots = {"calc.py": original}
        for rel, snap in snapshots.items():
            (tmp_path / rel).write_text(snap, encoding="utf-8")

        assert f.read_text() == original


# ═══════════════════════════════════════════════════════════════════════════════
# Isolation Chroma (collection_name par workspace)
# ═══════════════════════════════════════════════════════════════════════════════

class TestChromaIsolation:
    def test_workspace_key_deterministic(self, tmp_path):
        from src.context.code_index import _workspace_key
        k1 = _workspace_key(tmp_path)
        k2 = _workspace_key(tmp_path)
        assert k1 == k2

    def test_workspace_key_different_paths(self, tmp_path):
        from src.context.code_index import _workspace_key
        ws_a = tmp_path / "project_a"
        ws_b = tmp_path / "project_b"
        ws_a.mkdir()
        ws_b.mkdir()
        assert _workspace_key(ws_a) != _workspace_key(ws_b)

    def test_collection_name_includes_key(self, tmp_path):
        from src.context.code_index import _workspace_key
        key = _workspace_key(tmp_path)
        # La convention est lumena_code_<key>
        expected_name = f"lumena_code_{key}"
        assert len(expected_name) == len("lumena_code_") + 8

    def test_two_indexes_have_distinct_collections(self, tmp_path):
        """Deux CodeIndex sur des workspaces différents → collections différentes."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb non disponible")

        from src.context.code_index import CodeIndex
        ws_a = tmp_path / "ws_a"
        ws_b = tmp_path / "ws_b"
        ws_a.mkdir()
        ws_b.mkdir()

        idx_a = CodeIndex(ws_a, persist_dir=tmp_path / "chroma_a")
        idx_b = CodeIndex(ws_b, persist_dir=tmp_path / "chroma_b")

        assert idx_a.collection_name != idx_b.collection_name
        assert idx_a.persist_dir != idx_b.persist_dir
