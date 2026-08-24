"""
P0 — Harness d'acceptation CodeAgent.

Quatre scénarios clés (hors LLM — purement structurels) :
  1. bugfix Python avec test existant
  2. bugfix JS/TS multi-fichiers
  3. refactor simple cross-file
  4. tâche en workspace externe (isolation)

Ces tests valident les invariants structurels du pipeline CodeAgent :
  - Workspace isolation (bon répertoire cible)
  - Pas de fuite singleton entre workspaces
  - Gate métriques fonctionnelles
  - Convention scanner accessible

Ils n'exécutent PAS de LLM réel — ils patchent les couches LLM.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures partagées
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """Projet Python minimal avec un bug calculable."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "calculator.py").write_text(
        "def add(a, b):\n    return a - b  # BUG: should be a + b\n\n"
        "def multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calculator.py").write_text(
        "from src.calculator import add\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def js_project(tmp_path: Path) -> Path:
    """Projet JS minimal multi-fichiers."""
    (tmp_path / "utils.js").write_text(
        "function formatDate(d) { return d.toISOString(); }\nmodule.exports = { formatDate };\n",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "const { formatDate } = require('./utils');\nconsole.log(formatDate(new Date()));\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"name":"test-js","version":"1.0.0","scripts":{"test":"node --check app.js"}}\n',
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def external_workspace(tmp_path: Path) -> Path:
    """Workspace externe à Lumena (projet utilisateur fictif)."""
    ws = tmp_path / "client_project"
    ws.mkdir()
    (ws / "index.html").write_text("<html><body>Hello</body></html>", encoding="utf-8")
    (ws / "style.css").write_text("body { margin: 0; }", encoding="utf-8")
    return ws


# ═══════════════════════════════════════════════════════════════════════════════
# Scénario 1 — Bugfix Python avec test
# ═══════════════════════════════════════════════════════════════════════════════

class TestPythonBugfixAcceptance:
    """Scénario 1 : bugfix Python + test existant."""

    def test_workspace_resolves_correctly(self, python_project: Path):
        """TaskContext doit résoudre vers python_project, pas vers Lumena."""
        from src.agents.task_context import TaskContext

        ctx = TaskContext.from_delegate_call(
            description="Corrige le bug dans add() — elle soustrait au lieu d'additionner",
            project_path=str(python_project),
        )
        assert ctx.workspace_path == python_project
        assert ctx.resolution_source == "explicit_param"

    def test_intent_is_modify_for_existing_project(self, python_project: Path):
        """Un projet avec fichiers → intent=modify."""
        from src.agents.task_context import TaskContext

        ctx = TaskContext.from_delegate_call(
            description="Corrige le bug dans add() — elle soustrait au lieu d'additionner",
            project_path=str(python_project),
        )
        assert ctx.intent == "modify"

    def test_python_project_has_test_file(self, python_project: Path):
        """Le projet de test doit contenir un fichier de test pytest."""
        test_files = list(python_project.rglob("test_*.py"))
        assert len(test_files) >= 1, "Au moins un fichier test_*.py requis"

    def test_code_chunker_no_singleton_leak(self, python_project: Path):
        """CodeChunker pour project A ne doit pas polluer project B."""
        from src.context.code_chunker import CodeChunker

        chunker_a = CodeChunker(python_project)
        chunks_a = chunker_a.chunk_project(['.py'])

        other_project = python_project.parent / "other_project"
        other_project.mkdir(exist_ok=True)
        (other_project / "hello.py").write_text("def hello(): return 'world'\n")
        chunker_b = CodeChunker(other_project)
        chunks_b = chunker_b.chunk_project(['.py'])

        # Les deux chunkers opèrent sur leurs propres workspaces
        files_a = {c.file_path for c in chunks_a}
        files_b = {c.file_path for c in chunks_b}
        assert files_a.isdisjoint(files_b), "Fuite de chunks entre deux workspaces différents"


# ═══════════════════════════════════════════════════════════════════════════════
# Scénario 2 — Bugfix JS/TS multi-fichiers
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSBugfixAcceptance:
    """Scénario 2 : bugfix JS multi-fichiers."""

    def test_js_project_files_exist(self, js_project: Path):
        """Les deux fichiers JS doivent exister."""
        assert (js_project / "utils.js").exists()
        assert (js_project / "app.js").exists()

    def test_workspace_resolves_to_js_project(self, js_project: Path):
        """TaskContext doit pointer vers le projet JS."""
        from src.agents.task_context import TaskContext

        ctx = TaskContext.from_delegate_call(
            description="Corrige formatDate pour accepter les timestamps en millisecondes",
            project_path=str(js_project),
        )
        assert ctx.workspace_path == js_project

    def test_code_index_extension_not_hardcoded(self):
        """CodeIndex.index_project() ne doit pas hardcoder ['.py']."""
        import inspect
        from src.context import code_index as ci_module
        src = inspect.getsource(ci_module.CodeIndex.index_project)
        # La ligne chunk_project(['.py']) ne doit plus exister
        assert "chunk_project(['.py'])" not in src, (
            "CodeIndex.index_project() hardcode ['.py'] — corrige P4 d'abord"
        )

    def test_node_check_syntax_validator_accessible(self, js_project: Path):
        """node --check doit être exécutable comme validation syntaxique."""
        import subprocess
        result = subprocess.run(
            ["node", "--check", str(js_project / "app.js")],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, f"node --check a échoué : {result.stderr.decode()}"


# ═══════════════════════════════════════════════════════════════════════════════
# Scénario 3 — Refactor cross-file simple
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossFileRefactorAcceptance:
    """Scénario 3 : renommage de fonction cross-file."""

    def test_two_workspaces_independent_chunkers(self, tmp_path: Path):
        """Deux workspaces simultanés → chunks non-contaminés."""
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()

        (ws1 / "a.py").write_text(
            "def foo(x, y):\n    \"\"\"Additionne x et y.\"\"\"\n    return x + y\n"
        )
        (ws2 / "b.py").write_text(
            "def bar(name):\n    \"\"\"Retourne un message de bienvenue.\"\"\"\n    return f'Hello {name}'\n"
        )

        from src.context.code_chunker import CodeChunker
        c1 = CodeChunker(ws1)
        c2 = CodeChunker(ws2)

        chunks1 = c1.chunk_project(['.py'])
        chunks2 = c2.chunk_project(['.py'])

        names1 = {c.symbol_name for c in chunks1}
        names2 = {c.symbol_name for c in chunks2}

        assert "foo" in names1
        assert "bar" in names2
        # foo ne doit pas apparaître dans ws2
        assert "foo" not in names2
        assert "bar" not in names1

    def test_task_context_legacy_dict_has_workspace(self, tmp_path: Path):
        """to_legacy_dict() expose workspace_path pour le CodeAgent."""
        (tmp_path / "lib.py").write_text("def compute(x): return x*2\n")

        from src.agents.task_context import TaskContext
        ctx = TaskContext.from_delegate_call(
            description="Renomme compute en transform dans tous les fichiers",
            project_path=str(tmp_path),
        )
        d = ctx.to_legacy_dict()
        assert "workspace_path" in d
        assert Path(d["workspace_path"]) == tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# Scénario 4 — Workspace externe (isolation Lumena)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExternalWorkspaceAcceptance:
    """Scénario 4 : projet utilisateur hors de l'arborescence Lumena."""

    def test_external_workspace_intent_modify(self, external_workspace: Path):
        """Un workspace externe avec fichiers → intent=modify."""
        from src.agents.task_context import TaskContext

        ctx = TaskContext.from_delegate_call(
            description="Ajoute un header responsive et un menu de navigation",
            project_path=str(external_workspace),
        )
        assert ctx.intent == "modify"
        assert ctx.workspace_path == external_workspace

    def test_external_workspace_not_lumena(self, external_workspace: Path):
        """Le workspace résolu ne doit pas pointer vers l'arborescence Lumena."""
        from src.agents.task_context import TaskContext
        import sys

        lumena_root = Path(sys.modules.get("src", None).__file__).parent.parent \
            if "src" in sys.modules else Path.cwd()

        ctx = TaskContext.from_delegate_call(
            description="Ajoute un header responsive et un menu de navigation",
            project_path=str(external_workspace),
        )

        # Le workspace résolu ne doit pas être dans Lumena
        try:
            ctx.workspace_path.relative_to(lumena_root)
            is_inside_lumena = True
        except ValueError:
            is_inside_lumena = False

        assert not is_inside_lumena, (
            f"Le workspace {ctx.workspace_path} est dans Lumena — isolation défaillante"
        )

    def test_drive_less_windows_path_from_text_recovers_drive(self):
        """Un chemin extrait du texte sans drive Windows doit être normalisé."""
        from src.agents.task_context import TaskContext

        raw = r'Corrige le projet dans "\Users\user\Desktop\lumena\workspace\2026-04-26\echo-drift"'
        path = TaskContext._extract_path_from_texts([raw])

        assert path is not None
        assert str(path).lower().endswith(r"users\user\desktop\lumena\workspace\2026-04-26\echo-drift")
        assert path.drive, "Le drive Windows doit être restauré"

    def test_code_index_singleton_keyed_by_workspace(self, external_workspace: Path, tmp_path: Path):
        """get_code_index() doit retourner des instances distinctes par workspace."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb non disponible")

        from src.context.code_index import CodeIndex

        ws_a = external_workspace
        ws_b = tmp_path / "ws_b"
        ws_b.mkdir(exist_ok=True)
        (ws_b / "x.py").write_text("x = 1\n")

        persist_a = tmp_path / "persist_a"
        persist_b = tmp_path / "persist_b"

        idx_a = CodeIndex(ws_a, persist_dir=persist_a)
        idx_b = CodeIndex(ws_b, persist_dir=persist_b)

        assert idx_a.project_root != idx_b.project_root
        assert idx_a.persist_dir != idx_b.persist_dir


# ═══════════════════════════════════════════════════════════════════════════════
# Tests métriques gate (P0)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateMetrics:
    """Valide que le module gate_metrics fonctionne correctement."""

    def test_import_gate_metrics(self):
        from src.utils.gate_metrics import (
            record_gate_pass, record_gate_fail, record_gate_retry,
            record_wrong_workspace, record_rollback, record_lsp_fail_open,
            get_summary,
        )
        assert callable(record_gate_pass)
        assert callable(get_summary)

    def test_gate_pass_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["gate_pass"]
        gm.record_gate_pass(task_id="test-001")
        after = gm.get_summary()["gate_pass"]
        assert after == before + 1

    def test_gate_fail_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["gate_fail"]
        gm.record_gate_fail(task_id="test-002", reason="syntax error")
        after = gm.get_summary()["gate_fail"]
        assert after == before + 1

    def test_gate_retry_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["gate_retry_count"]
        gm.record_gate_retry(task_id="test-003")
        after = gm.get_summary()["gate_retry_count"]
        assert after == before + 1

    def test_wrong_workspace_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["wrong_workspace_context_count"]
        gm.record_wrong_workspace(task_id="test-004", attempted="/tmp/wrong")
        after = gm.get_summary()["wrong_workspace_context_count"]
        assert after == before + 1

    def test_rollback_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["rollback_count"]
        gm.record_rollback(task_id="test-005")
        after = gm.get_summary()["rollback_count"]
        assert after == before + 1

    def test_lsp_fail_open_increments_counter(self):
        from src.utils import gate_metrics as gm

        before = gm.get_summary()["lsp_fail_open_count"]
        gm.record_lsp_fail_open(task_id="test-006", error="LSP timeout")
        after = gm.get_summary()["lsp_fail_open_count"]
        assert after == before + 1

    def test_gate_pass_rate_computed(self):
        from src.utils import gate_metrics as gm

        # Réinitialise les compteurs mémoire pour ce test
        import src.utils.gate_metrics as _gm
        with _gm._lock:
            _gm._counters["gate_pass"] = 3
            _gm._counters["gate_fail"] = 1

        summary = gm.get_summary()
        assert summary["gate_pass_rate"] == pytest.approx(0.75, abs=0.001)

    def test_gate_pass_rate_none_when_no_data(self):
        from src.utils import gate_metrics as gm
        import src.utils.gate_metrics as _gm

        with _gm._lock:
            _gm._counters["gate_pass"] = 0
            _gm._counters["gate_fail"] = 0

        summary = gm.get_summary()
        assert summary["gate_pass_rate"] is None

    def test_thread_safety(self):
        """Les compteurs doivent être thread-safe."""
        import threading
        from src.utils import gate_metrics as gm
        import src.utils.gate_metrics as _gm

        with _gm._lock:
            _gm._counters["gate_pass"] = 0

        def increment():
            for _ in range(100):
                gm.record_gate_pass(task_id="thread-test")

        threads = [threading.Thread(target=increment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert _gm._counters["gate_pass"] == 500


# ═══════════════════════════════════════════════════════════════════════════════
# Tests flags (P0)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpgradeFinalFlags:
    """Valide que les nouveaux flags sont définis et opt-IN par défaut."""

    def test_verification_gate_flag_off_by_default(self):
        import os
        os.environ.pop("LUMENA_VERIFICATION_GATE", None)
        # Reimport pour forcer la relecture
        import importlib
        import src.config.codeagent_flags as flags
        importlib.reload(flags)
        assert flags.VERIFICATION_GATE is False

    def test_swe_pipeline_flag_off_by_default(self):
        import os
        os.environ.pop("LUMENA_SWE_PIPELINE", None)
        import importlib
        import src.config.codeagent_flags as flags
        importlib.reload(flags)
        assert flags.SWE_PIPELINE is False

    def test_convention_scan_flag_on_by_default(self):
        import os
        os.environ.pop("LUMENA_CONVENTION_SCAN", None)
        import importlib
        import src.config.codeagent_flags as flags
        importlib.reload(flags)
        assert flags.CONVENTION_SCAN is True

    def test_flags_respect_env_override(self):
        import os
        import importlib
        os.environ["LUMENA_VERIFICATION_GATE"] = "1"
        import src.config.codeagent_flags as flags
        importlib.reload(flags)
        assert flags.VERIFICATION_GATE is True
        del os.environ["LUMENA_VERIFICATION_GATE"]
        importlib.reload(flags)
