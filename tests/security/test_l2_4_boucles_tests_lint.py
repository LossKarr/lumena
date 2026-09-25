"""Lot L2-4 - `test_and_fix` et `lint_and_fix` jugés comme `dev_run_fix`.

Mesure du 16 septembre 2026 (lecture seule, avant tout code) : ces deux handlers
resolvent `base_dir` exactement comme `dev_run_fix` (`project.py:2876` et `:2977` -
`ctx.resolve_path(project_dir)` sinon `ctx.runtime_root`), puis lancent leurs commandes
par le MEME lanceur `_run_project_cmd` (`create_subprocess_shell`, `cwd=...`). Ils
etaient donc restes hors garde alors que `dev_run_fix`, leur jumeau, est juge depuis L2-2.

`ide_terminal` reste HORS de ce lot, et c'est ecrit plutot que tu : il n'execute rien
localement (`ide.py:135` -> `bridge.terminal_run`), c'est l'IDE qui execute. Une garde de
dossier local n'y a aucun sens ; le rail IDE de mission le juge deja.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.reasoning.handlers import project as project_mod
from src.reasoning.handlers.context import HandlerContext
from src.tools.file_guardrails import OutsideAccessGrant, WorkspaceFileGuardrails


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    (ws / "projet").mkdir(parents=True)
    (root / "src").mkdir()
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    return {"root": root, "ws": ws, "tmp": tmp_path}


def _ctx(m, runtime=None) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=runtime or m["ws"])
    ctx.outside_access_grant = OutsideAccessGrant.none()
    ctx.lumena = SimpleNamespace(llm=SimpleNamespace())
    return ctx


@pytest.fixture
def lanceur_interdit(monkeypatch):
    """Toute execution reelle pendant ces tests est une faute."""
    async def _interdit(*a, **k):
        pytest.fail("commande lancee malgre le refus")
    monkeypatch.setattr(project_mod, "_run_project_cmd", _interdit)


# ── 1. Le code de Lumena est refuse, sans rien lancer ───────────────────────

@pytest.mark.asyncio
async def test_test_and_fix_refuse_le_depot_sans_lancer(monde, lanceur_interdit):
    m = monde
    r = await project_mod.test_and_fix_handler(
        _ctx(m), project_dir=str(m["root"] / "src"), test_command="pytest")
    assert "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_lint_and_fix_refuse_le_depot_sans_lancer(monde, lanceur_interdit):
    m = monde
    r = await project_mod.lint_and_fix_handler(
        _ctx(m), project_dir=str(m["root"] / "src"), lint_command="ruff check .")
    assert "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_refus_meme_quand_le_dossier_vient_du_tour(monde, lanceur_interdit):
    """Sans `project_dir`, le dossier est `ctx.runtime_root` : il est juge pareil."""
    m = monde
    r = await project_mod.test_and_fix_handler(
        _ctx(m, m["root"]), test_command="pytest")
    assert "refus" in str(r.output).lower()


# ── 2. Caracterisation : le workspace passe toujours la garde ───────────────

@pytest.mark.asyncio
async def test_test_and_fix_passe_la_garde_dans_le_workspace(monde, monkeypatch):
    """La garde laisse passer ; on arrete juste avant l'execution reelle."""
    m = monde
    appels = {}

    async def _capture(cmd, cwd, timeout=60):
        appels["cwd"] = str(cwd)
        return (0, "1 passed")

    monkeypatch.setattr(project_mod, "_run_project_cmd", _capture)
    r = await project_mod.test_and_fix_handler(
        _ctx(m), project_dir=str(m["ws"] / "projet"), test_command="pytest")
    assert "refus" not in str(r.output).lower()
    assert appels.get("cwd", "").endswith("projet")


@pytest.mark.asyncio
async def test_lint_and_fix_passe_la_garde_dans_le_workspace(monde, monkeypatch):
    m = monde
    appels = {}

    async def _capture(cmd, cwd, timeout=60):
        appels["cwd"] = str(cwd)
        return (0, "All checks passed!")

    monkeypatch.setattr(project_mod, "_run_project_cmd", _capture)
    r = await project_mod.lint_and_fix_handler(
        _ctx(m), project_dir=str(m["ws"] / "projet"), lint_command="ruff check .")
    assert "refus" not in str(r.output).lower()
    assert appels.get("cwd", "").endswith("projet")


# ── 3. Limite ecrite : ide_terminal n'execute pas localement ────────────────

def test_ide_terminal_n_execute_rien_localement():
    """Constat mesure, pas une garde : `ide_terminal` transmet au pont IDE. Une garde de
    dossier local n'y a aucun sens ; c'est le rail IDE de mission qui juge."""
    import inspect
    from src.reasoning.handlers import ide as ide_mod

    source = inspect.getsource(ide_mod._handle_ide_terminal)
    assert "bridge.terminal_run" in source
    for lanceur in ("subprocess", "create_subprocess", "Popen"):
        assert lanceur not in source
