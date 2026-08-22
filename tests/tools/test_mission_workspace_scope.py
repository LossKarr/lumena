"""LOT 2.1 — scope workspace ISOLÉ par mission (cf. run PollApp multi-worker).

Sans scope, les workers parallèles écrivent dans un dossier fourre-tout dérivé du
NOM de fichier (style.css vs styles.css, app.js réécrit 3×) et l'épinglage
`_pinned_project` est un attribut de CLASSE → race entre workers concurrents.

Ici on prouve le nouveau chemin : un sous-dossier `missions/<slug>_<id>/`
DÉTERMINISTE, passé en PARAMÈTRE EXPLICITE (aucun état de classe, aucun global),
qui couvre write + read/edit/list + create_directory, et reste STRICTEMENT inactif
hors mission (chat / CodeAgent / mission sans mission_workspace).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.tools.file_guardrails import WorkspaceFileGuardrails


@pytest.fixture()
def g(tmp_path):
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None
    yield WorkspaceFileGuardrails(lumena_root=tmp_path)
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None


# ── résolution centrale (guardrails) : param explicite, dossier déterministe ─────

def test_get_workspace_path_mission_is_deterministic(g, tmp_path):
    # Avec subdir : pas de date, pas de projet-<nom>, un seul dossier.
    p1 = g.get_workspace_path("app.py", mission_workspace_subdir="missions/pollapp_m1")
    p2 = g.get_workspace_path("style.css", mission_workspace_subdir="missions/pollapp_m1")
    assert p1.parent == tmp_path / "missions" / "pollapp_m1"
    assert p2.parent == tmp_path / "missions" / "pollapp_m1"   # MÊME dossier
    # aucune dérivation par extension/date
    assert "projet-" not in str(p1) and datetime.now().strftime("%Y-%m-%d") not in str(p1)


def test_get_workspace_path_without_subdir_unchanged(g, tmp_path):
    # Sans subdir : comportement actuel (projet-<stem> dérivé du nom).
    p = g.get_workspace_path("notes.md")
    assert "projet-notes" in str(p)


def test_get_workspace_path_ignores_pinned_project_in_mission(g, tmp_path):
    # Garde-fou anti-race : même si un _pinned_project traîne (mono-agent),
    # le scope mission passe par le PARAMÈTRE, jamais par l'état de classe.
    WorkspaceFileGuardrails.pin_project("mono-agent-xyz")
    try:
        p = g.get_workspace_path("app.py", mission_workspace_subdir="missions/pollapp_m1")
    finally:
        WorkspaceFileGuardrails.pin_project(None)
    assert p.parent == tmp_path / "missions" / "pollapp_m1"
    assert "mono-agent-xyz" not in str(p)


def test_write_file_strict_lands_in_mission_dir(g, tmp_path):
    r1 = g.write_file_strict("app.py", "print('a')", mission_workspace_subdir="missions/x_m1")
    r2 = g.write_file_strict("test_app.py", "def test(): pass", mission_workspace_subdir="missions/x_m1")
    assert r1.success and r2.success
    assert r1.file_path.parent == tmp_path / "missions" / "x_m1"
    assert r2.file_path.parent == tmp_path / "missions" / "x_m1"   # colocalisés


def test_resolve_user_path_mission_wins_over_homonym(g, tmp_path):
    # Écrit app.py DANS la mission, et un homonyme AILLEURS (dérivation projet).
    g.write_file_strict("app.py", "MISSION", mission_workspace_subdir="missions/x_m1")
    g.write_file_strict("app.py", "AUTRE")  # part ailleurs (projet-app)
    # read-après-write : la version de la mission PRIME.
    resolved = g.resolve_user_path("app.py", mission_workspace_subdir="missions/x_m1")
    assert resolved == tmp_path / "missions" / "x_m1" / "app.py"
    assert resolved.read_text(encoding="utf-8") == "MISSION"


def test_two_missions_no_cross_leak(g, tmp_path):
    # Concurrence : deux subdirs distincts → deux dossiers, aucune fuite croisée
    # (prouve qu'on a évité la race de l'attribut de classe _pinned_project).
    a = g.write_file_strict("app.py", "A", mission_workspace_subdir="missions/a_1")
    b = g.write_file_strict("app.py", "B", mission_workspace_subdir="missions/b_2")
    assert a.file_path.parent == tmp_path / "missions" / "a_1"
    assert b.file_path.parent == tmp_path / "missions" / "b_2"
    assert a.file_path.read_text(encoding="utf-8") == "A"
    assert b.file_path.read_text(encoding="utf-8") == "B"


# ── LOT 2.8 : strip défensif du préfixe mission (run BudgetBuddy) ─────────────────
# Les modèles recopient le chemin complet → duplication missions/<id>/missions/<id>.

def test_strip_mission_workspace_prefix():
    from src.tools.file_guardrails import strip_mission_workspace_prefix as strip
    sub = "missions/task_96e6"
    assert strip("missions/task_96e6/storage.py", sub) == "storage.py"
    assert strip("workspace/missions/task_96e6/app.py", sub) == "app.py"
    assert strip("missions/task_96e6/tests/test_api.py", sub) == "tests/test_api.py"
    # chemin DÉJÀ dupliqué → ramené à la forme saine (strip en boucle)
    assert strip("missions/task_96e6/missions/task_96e6/storage.py", sub) == "storage.py"
    # backslashes Windows
    assert strip("missions\\task_96e6\\app.py", sub) == "app.py"
    # déjà relatif → inchangé
    assert strip("app.py", sub) == "app.py"
    assert strip("static/style.css", sub) == "static/style.css"
    # un préfixe qui n'est PAS le sub exact n'est pas strippé
    assert strip("missions/autre_task/x.py", sub) == "missions/autre_task/x.py"
    # hors mission (sub vide) → inchangé
    assert strip("missions/task_96e6/app.py", "") == "missions/task_96e6/app.py"


def test_write_with_full_path_no_duplication(g, tmp_path):
    # Repro BudgetBuddy : write_file avec le CHEMIN COMPLET → avant, atterrissait
    # dans missions/<id>/missions/<id>/ ; maintenant au BON endroit.
    r = g.write_file_strict("missions/task_96e6/storage.py", "x = 1",
                            mission_workspace_subdir="missions/task_96e6")
    assert r.success
    assert r.file_path == tmp_path / "missions" / "task_96e6" / "storage.py"
    assert not (tmp_path / "missions" / "task_96e6" / "missions").exists()


def test_read_with_full_path_no_duplication(g, tmp_path):
    g.write_file_strict("app.py", "REEL", mission_workspace_subdir="missions/task_96e6")
    resolved = g.resolve_user_path("missions/task_96e6/app.py",
                                   mission_workspace_subdir="missions/task_96e6")
    assert resolved == tmp_path / "missions" / "task_96e6" / "app.py"
    assert resolved.read_text(encoding="utf-8") == "REEL"


# ── helper pur HandlerContext.mission_workspace_subdir ────────────────────────────

def test_helper_active_only_in_mission(tmp_path):
    fg = WorkspaceFileGuardrails(tmp_path)
    # run de mission avec workspace → actif
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path, file_guardrails=fg,
                         is_mission_run=True, runtime_task_id="m1",
                         mission_workspace="missions/x_m1")
    assert ctx.mission_workspace_subdir() == "missions/x_m1"


def test_helper_inactive_off_mission(tmp_path):
    fg = WorkspaceFileGuardrails(tmp_path)
    # chat / CodeAgent : is_mission_run False → jamais de scope
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path, file_guardrails=fg,
                         is_mission_run=False, runtime_task_id="m1",
                         mission_workspace="missions/x_m1")
    assert ctx.mission_workspace_subdir() == ""
    # mission SANS workspace → chemin actuel
    ctx2 = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path, file_guardrails=fg,
                          is_mission_run=True, runtime_task_id="m1", mission_workspace="")
    assert ctx2.mission_workspace_subdir() == ""


def test_helper_rejects_traversal(tmp_path):
    fg = WorkspaceFileGuardrails(tmp_path)
    for bad in ("../etc", "missions/../../x", "/abs/path", "C:/win"):
        ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path, file_guardrails=fg,
                             is_mission_run=True, runtime_task_id="m1", mission_workspace=bad)
        assert ctx.mission_workspace_subdir() == "", bad


# ── bout-en-bout via les handlers (write / read / edit / create_directory) ────────

def _mission_ctx(tmp_path, sub="missions/x_m1"):
    return HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="m1", mission_workspace=sub,
    )


@pytest.mark.asyncio
async def test_handler_write_then_read_roundtrip_in_mission(tmp_path):
    from src.reasoning.handlers.files import (
        write_file_handler, read_file_handler, edit_file_handler,
    )
    ctx = _mission_ctx(tmp_path)
    # un homonyme hors mission, écrit AVANT (piège read-after-write)
    (tmp_path / "app.py").write_text("VIEUX", encoding="utf-8")

    w = await write_file_handler(ctx, path="app.py", content="MISSION_V1")
    assert "✅" in w.output
    assert (tmp_path / "missions" / "x_m1" / "app.py").read_text(encoding="utf-8") == "MISSION_V1"

    r = await read_file_handler(ctx, path="app.py")
    assert "MISSION_V1" in r.output and "VIEUX" not in r.output   # la mission prime

    e = await edit_file_handler(ctx, file_path="app.py", old_content="MISSION_V1", new_content="MISSION_V2")
    assert "✅" in e.output
    assert (tmp_path / "missions" / "x_m1" / "app.py").read_text(encoding="utf-8") == "MISSION_V2"


@pytest.mark.asyncio
async def test_handler_write_offmission_unchanged(tmp_path):
    from src.reasoning.handlers.files import write_file_handler
    # is_mission_run False → aucune redirection mission (comportement actuel)
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path))
    w = await write_file_handler(ctx, path="notes.md", content="hello")
    assert "✅" in w.output
    assert not (tmp_path / "missions").exists()   # rien sous missions/


@pytest.mark.asyncio
async def test_create_directory_in_mission_scope(tmp_path):
    from src.reasoning.handlers.files import create_directory_handler
    ctx = _mission_ctx(tmp_path)
    res = await create_directory_handler(ctx, path="assets")
    assert "✅" in res.output
    assert (tmp_path / "missions" / "x_m1" / "assets").is_dir()
