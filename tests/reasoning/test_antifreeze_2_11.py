"""
test_antifreeze_2_11.py - LOT 2.11 « anti-freeze » (run NotaBene, freeze 04:08:19).

Trois verrous :
  a. grep_search borné : exclusions dures (node_modules, .git, .backups…),
     double budget (fichiers + temps) avec résultat partiel honnête,
     exécution hors event loop (asyncio.to_thread).
  b. run_command : cwd (préfixe `cd X &&` et param cwd=) résolu
     workspace-aware ; introuvable = échec clair AVANT exécution,
     plus jamais de strip silencieux.
  c. spawn ≠ timeout : une erreur de lancement (Popen lève) est rendue
     telle quelle — plus jamais « Timeout commande (>120s) » en 1 ms.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.reasoning.handlers.files as files_mod
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import grep_search_handler
from src.reasoning.handlers.system import _resolve_cwd, run_command_handler
from src.tools.file_guardrails import WorkspaceFileGuardrails


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def files_ctx(tmp_path):
    """Contexte pour grep_search (guardrails ancrés sur tmp_path)."""
    return HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
    )


# ═══════════════ a. grep_search borné ═══════════════

class TestGrepExclusions:
    @pytest.mark.asyncio
    async def test_node_modules_jamais_scanne(self, files_ctx, tmp_path):
        proj = tmp_path / "proj"
        (proj / "node_modules" / "lib").mkdir(parents=True)
        (proj / "node_modules" / "lib" / "dep.js").write_text(
            "NEEDLE_211 in dependency", encoding="utf-8"
        )
        (proj / "app.py").write_text("x = 'NEEDLE_211'", encoding="utf-8")

        r = await grep_search_handler(files_ctx, pattern="NEEDLE_211", path="proj")
        assert "app.py" in r.output
        assert "node_modules" not in r.output

    @pytest.mark.asyncio
    async def test_backups_et_pycache_exclus(self, files_ctx, tmp_path):
        for d in (".backups", "__pycache__", ".git"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "f.txt").write_text("NEEDLE_HIDDEN", encoding="utf-8")
        (tmp_path / "visible.txt").write_text("NEEDLE_HIDDEN ok", encoding="utf-8")

        r = await grep_search_handler(files_ctx, pattern="NEEDLE_HIDDEN", path=".")
        assert "visible.txt" in r.output
        assert ".backups" not in r.output
        assert "__pycache__" not in r.output


class TestGrepBudgets:
    @pytest.mark.asyncio
    async def test_budget_fichiers_resultat_partiel_honnete(
        self, files_ctx, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(files_mod, "_GREP_MAX_FILES", 3)
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(f"contenu {i}", encoding="utf-8")

        r = await grep_search_handler(files_ctx, pattern="ZZZ_INTROUVABLE", path=".")
        assert "Recherche bornée" in r.output
        assert "budget de 3 fichiers" in r.output
        assert "path=" in r.output  # guidance pour cibler

    @pytest.mark.asyncio
    async def test_budget_temps_resultat_partiel_honnete(
        self, files_ctx, tmp_path, monkeypatch
    ):
        # deadline déjà dépassée (0.0 peut ne pas déclencher : résolution
        # de time.monotonic() sous Windows)
        monkeypatch.setattr(files_mod, "_GREP_MAX_SECONDS", -1.0)
        for i in range(5):
            (tmp_path / f"g{i}.txt").write_text("data", encoding="utf-8")

        r = await grep_search_handler(files_ctx, pattern="ZZZ_INTROUVABLE", path=".")
        assert "Recherche bornée" in r.output

    @pytest.mark.asyncio
    async def test_arbre_normal_comportement_inchange(self, files_ctx, tmp_path):
        (tmp_path / "code.py").write_text("def hello():\n    pass", encoding="utf-8")
        r = await grep_search_handler(files_ctx, pattern="def hello", path=".")
        assert "code.py" in r.output
        assert "Recherche bornée" not in r.output


class TestGrepEventLoop:
    def test_structurel_plus_de_rglob_materialise(self):
        src = Path(files_mod.__file__).read_text(encoding="utf-8")
        assert "list(target.rglob" not in src, (
            "grep_search ne doit plus matérialiser l'arborescence "
            "(cause du freeze NotaBene)"
        )

    def test_structurel_scan_via_to_thread(self):
        src = Path(files_mod.__file__).read_text(encoding="utf-8")
        assert "asyncio.to_thread(\n            _grep_search_sync" in src or (
            "to_thread" in src and "_grep_search_sync" in src
        )

    @pytest.mark.asyncio
    async def test_event_loop_non_bloque(self, files_ctx, tmp_path):
        for i in range(50):
            (tmp_path / f"h{i}.txt").write_text(f"ligne {i}" * 50, encoding="utf-8")
        flag = False

        async def canary():
            nonlocal flag
            await asyncio.sleep(0.01)
            flag = True

        task = asyncio.create_task(canary())
        r = await grep_search_handler(files_ctx, pattern="ZZZ_NOPE", path=".")
        await task
        assert flag, "grep_search a bloqué l'event loop"
        assert "Aucun résultat" in r.output


# ═══════════════ b. cwd résolu et honnête ═══════════════

class TestResolveCwd:
    def test_absolu_existant(self, tmp_path):
        assert _resolve_cwd(str(tmp_path), None) == str(tmp_path)

    def test_absolu_inexistant(self, tmp_path):
        assert _resolve_cwd(str(tmp_path / "nope"), None) is None

    def test_relatif_racine_lumena(self, tmp_path):
        (tmp_path / "sub").mkdir()
        resolved = _resolve_cwd("sub", tmp_path)
        assert resolved is not None
        assert Path(resolved) == (tmp_path / "sub").resolve()

    def test_relatif_workspace_missions(self, tmp_path, monkeypatch):
        """Le cas du run NotaBene : missions/<task_id> relatif au workspace."""
        import src.utils.paths as paths_mod

        ws = tmp_path / "ws"
        (ws / "missions" / "task_x").mkdir(parents=True)
        monkeypatch.setattr(paths_mod, "WORKSPACE_DIR", ws)
        other_root = tmp_path / "root"
        other_root.mkdir()

        resolved = _resolve_cwd("missions/task_x", other_root)
        assert resolved is not None
        assert Path(resolved) == (ws / "missions" / "task_x").resolve()

    def test_introuvable_partout(self, tmp_path):
        assert _resolve_cwd("missions/task_fantome_zzz", tmp_path) is None

    def test_quotes_strippees(self, tmp_path):
        (tmp_path / "q").mkdir()
        assert _resolve_cwd('"q"', tmp_path) is not None


class TestRunCommandCwd:
    @pytest.mark.asyncio
    async def test_cwd_explicite_introuvable_echec_clair_pas_timeout(self, ctx):
        r = await run_command_handler(
            ctx, command="echo jamais_execute", cwd="missions/task_fantome_zzz"
        )
        assert "introuvable" in r.output
        assert "NON exécutée" in r.output
        assert "Timeout" not in r.output
        assert "jamais_execute" not in r.output

    @pytest.mark.asyncio
    async def test_cd_prefixe_introuvable_echec_clair_commande_non_lancee(self, ctx):
        r = await run_command_handler(
            ctx, command="cd missions/task_fantome_zzz && echo jamais_execute"
        )
        assert "introuvable" in r.output
        assert "NON exécutée" in r.output
        assert "Timeout" not in r.output
        assert "jamais_execute" not in r.output

    @pytest.mark.asyncio
    async def test_cd_prefixe_relatif_racine_resolu_et_execute(self, ctx, tmp_path):
        sub = tmp_path / "subdir_211"
        sub.mkdir()
        r = await run_command_handler(ctx, command="cd subdir_211 && cd")
        assert r.success
        assert "subdir_211" in r.output

    @pytest.mark.asyncio
    async def test_cwd_explicite_relatif_workspace_resolu(self, ctx, tmp_path, monkeypatch):
        """missions/<task_id> passé en cwd= doit s'exécuter DANS le workspace."""
        import src.utils.paths as paths_mod

        ws = tmp_path / "workspace"
        mission_dir = ws / "missions" / "task_211"
        mission_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_mod, "WORKSPACE_DIR", ws)

        r = await run_command_handler(ctx, command="cd", cwd="missions/task_211")
        assert r.success
        assert "task_211" in r.output


# ═══════════════ c. spawn ≠ timeout ═══════════════

class TestSpawnNotTimeout:
    @pytest.mark.asyncio
    async def test_echec_spawn_rendu_honnetement(self, ctx, monkeypatch):
        import subprocess

        def _boom(*args, **kwargs):
            raise FileNotFoundError("cwd invalide simulé")

        monkeypatch.setattr(subprocess, "Popen", _boom)
        r = await run_command_handler(ctx, command="echo x")
        assert "Échec d'exécution" in r.output
        assert "pas un timeout" in r.output
        assert "FileNotFoundError" in r.output
        assert "Timeout commande" not in r.output

    @pytest.mark.asyncio
    async def test_vrai_timeout_toujours_rapporte(self, ctx):
        r = await run_command_handler(ctx, command="ping -n 100 127.0.0.1", timeout=2)
        assert r.success
        assert "Timeout" in r.output
