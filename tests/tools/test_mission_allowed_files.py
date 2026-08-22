"""LOT 2.3 — périmètre d'écriture PAR WORKER (allowed_files), option A stricte.

Un worker n'ÉCRIT que ses fichiers assignés (relatifs au dossier mission 2.1) ;
la LECTURE reste libre. Le garde couvre TOUS les chemins mutatifs (write, edit,
multi_edit, insert_at_anchor, apply_patch, apply_patch_new, delete, create_zip,
undo_edit, create_directory) — sinon un trou permet de contourner le contrat.
Strictement inactif hors mission (chat / CodeAgent / lead / worker sans liste).
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.tools.file_guardrails import WorkspaceFileGuardrails


def _worker_ctx(tmp_path, allowed, sub="missions/pollapp_m1"):
    return HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="w1",
        mission_workspace=sub, mission_allowed_files=list(allowed),
    )


# ── helper pur ────────────────────────────────────────────────────────────────

def test_allowed_set_normalizes(tmp_path):
    ctx = _worker_ctx(tmp_path, ["app.py", "static\\style.css", "/abs", "../x", "ok/../bad"])
    s = ctx.mission_allowed_files_set()
    assert "app.py" in s and "static/style.css" in s
    assert "/abs" not in s and "../x" not in s and "ok/../bad" not in s


def test_allowed_set_none_off_mission(tmp_path):
    # is_mission_run False → aucune restriction
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path),
                         is_mission_run=False, runtime_task_id="w1",
                         mission_allowed_files=["app.py"])
    assert ctx.mission_allowed_files_set() is None


def test_allowed_set_none_when_empty(tmp_path):
    ctx = _worker_ctx(tmp_path, [])
    assert ctx.mission_allowed_files_set() is None


# ── write : dans / hors périmètre ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_in_scope_ok_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import write_file_handler
    ctx = _worker_ctx(tmp_path, ["app.py"])
    ok = await write_file_handler(ctx, path="app.py", content="print('ok')")
    assert "✅" in ok.output
    assert (tmp_path / "missions" / "pollapp_m1" / "app.py").exists()

    blocked = await write_file_handler(ctx, path="test_app.py", content="x")
    assert "périmètre" in blocked.output or "⛔" in blocked.output
    assert not (tmp_path / "missions" / "pollapp_m1" / "test_app.py").exists()


@pytest.mark.asyncio
async def test_worker_without_list_unrestricted(tmp_path):
    from src.reasoning.handlers.files import write_file_handler
    ctx = _worker_ctx(tmp_path, [])   # mission workspace mais PAS de allowed_files
    r = await write_file_handler(ctx, path="anything.py", content="x")
    assert "✅" in r.output


@pytest.mark.asyncio
async def test_lead_not_restricted(tmp_path):
    # Le lead a un workspace mais AUCUN allowed_files → écrit partout (intégration 2.5).
    from src.reasoning.handlers.files import write_file_handler
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path),
                         is_mission_run=True, runtime_task_id="lead",
                         mission_workspace="missions/pollapp_lead")
    for f in ("app.py", "test_app.py", "static/style.css"):
        r = await write_file_handler(ctx, path=f, content="x")
        assert "✅" in r.output, f


@pytest.mark.asyncio
async def test_chat_offmission_unrestricted(tmp_path):
    from src.reasoning.handlers.files import write_file_handler
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path))
    r = await write_file_handler(ctx, path="whatever.py", content="x")
    assert "✅" in r.output


# ── lecture toujours libre ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_always_allowed(tmp_path):
    from src.reasoning.handlers.files import read_file_handler
    # CONTRAT.md est posé sur disque par write_mission_contract (LOT 2.10 : son
    # écriture MANUELLE via write_file est désormais refusée en mission) — on le
    # matérialise comme l'outil le ferait.
    d = tmp_path / "missions" / "pollapp_m1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CONTRAT.md").write_text("le contrat", encoding="utf-8")
    # le worker (n'a que app.py) peut quand même LIRE le contrat
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await read_file_handler(worker, path="CONTRAT.md")
    assert "le contrat" in r.output


# ── edit / multi_edit / apply_patch / delete / undo hors périmètre bloqués ───────

@pytest.mark.asyncio
async def test_edit_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, edit_file_handler
    # crée test_app.py via le lead (worker ne le possède pas)
    lead = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path),
                          is_mission_run=True, runtime_task_id="lead",
                          mission_workspace="missions/pollapp_m1")
    await write_file_handler(lead, path="test_app.py", content="A")
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await edit_file_handler(worker, file_path="test_app.py", old_content="A", new_content="B")
    assert "périmètre" in r.output or "⛔" in r.output
    assert (tmp_path / "missions" / "pollapp_m1" / "test_app.py").read_text(encoding="utf-8") == "A"


@pytest.mark.asyncio
async def test_multi_edit_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import multi_edit_file_handler
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await multi_edit_file_handler(worker, edits=[
        {"file_path": "test_app.py", "old": "x", "new": "y"},
    ])
    assert "périmètre" in r.output or "⛔" in r.output


@pytest.mark.asyncio
async def test_delete_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, delete_file_handler
    lead = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path),
                          is_mission_run=True, runtime_task_id="lead",
                          mission_workspace="missions/pollapp_m1")
    await write_file_handler(lead, path="other.py", content="x")
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await delete_file_handler(worker, path="other.py")
    assert "périmètre" in r.output or "⛔" in r.output
    assert (tmp_path / "missions" / "pollapp_m1" / "other.py").exists()


# ── create_directory : parent d'un fichier possédé OK, sinon refus ───────────────

@pytest.mark.asyncio
async def test_create_directory_parent_of_owned_ok(tmp_path):
    from src.reasoning.handlers.files import create_directory_handler
    worker = _worker_ctx(tmp_path, ["static/style.css"])
    ok = await create_directory_handler(worker, path="static")
    assert "✅" in ok.output
    assert (tmp_path / "missions" / "pollapp_m1" / "static").is_dir()


@pytest.mark.asyncio
async def test_create_directory_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import create_directory_handler
    worker = _worker_ctx(tmp_path, ["static/style.css"])
    r = await create_directory_handler(worker, path="secret")
    assert "périmètre" in r.output or "⛔" in r.output


# ── insert_at_anchor / apply_patch simple / undo_edit hors périmètre ─────────────

def _lead_ctx(tmp_path, sub="missions/pollapp_m1"):
    return HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path),
                          is_mission_run=True, runtime_task_id="lead",
                          mission_workspace=sub)


@pytest.mark.asyncio
async def test_insert_at_anchor_scope(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, insert_at_anchor_handler
    lead = _lead_ctx(tmp_path)
    await write_file_handler(lead, path="other.py", content="# ANCRE\n")
    worker = _worker_ctx(tmp_path, ["app.py"])
    await write_file_handler(worker, path="app.py", content="# ANCRE\n")
    # hors périmètre → bloqué
    r = await insert_at_anchor_handler(worker, path="other.py", anchor="# ANCRE", content="x = 1")
    assert "périmètre" in r.output or "⛔" in r.output
    # dans le périmètre → OK
    ok = await insert_at_anchor_handler(worker, path="app.py", anchor="# ANCRE", content="x = 1")
    assert "✅" in ok.output


@pytest.mark.asyncio
async def test_apply_patch_simple_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, apply_patch_handler
    lead = _lead_ctx(tmp_path)
    await write_file_handler(lead, path="test_app.py", content="A = 1\n")
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await apply_patch_handler(worker, file_path="test_app.py", old_content="A = 1", new_content="A = 2")
    assert "périmètre" in r.output or "⛔" in r.output
    assert (tmp_path / "missions" / "pollapp_m1" / "test_app.py").read_text(encoding="utf-8") == "A = 1\n"


@pytest.mark.asyncio
async def test_undo_edit_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, undo_edit_handler
    lead = _lead_ctx(tmp_path)
    await write_file_handler(lead, path="other.py", content="v1")
    worker = _worker_ctx(tmp_path, ["app.py"])
    r = await undo_edit_handler(worker, file_path="other.py")
    assert "périmètre" in r.output or "⛔" in r.output


# ── apply_patch_new : LE bypass dangereux (résolvait lumena_root/hunk en dur) ────

@pytest.mark.asyncio
async def test_apply_patch_new_routes_into_mission_dir(tmp_path):
    # Preuve de la FIN du bypass 2.1 : en mission, un `Add File: app.py` atterrit
    # dans missions/<id>/, PAS à lumena_root/app.py.
    from src.reasoning.handlers.files import apply_patch_new_handler
    worker = _worker_ctx(tmp_path, ["app.py"])
    patch = "*** Begin Patch\n*** Add File: app.py\n+print('mission')\n*** End Patch"
    r = await apply_patch_new_handler(worker, patch_content=patch)
    assert "❌" not in r.output and "⛔" not in r.output, r.output
    assert (tmp_path / "missions" / "pollapp_m1" / "app.py").exists()
    assert not (tmp_path / "app.py").exists()   # l'ancien bypass aurait écrit ICI


@pytest.mark.asyncio
async def test_apply_patch_new_out_of_scope_blocked(tmp_path):
    from src.reasoning.handlers.files import apply_patch_new_handler
    worker = _worker_ctx(tmp_path, ["app.py"])
    patch = "*** Begin Patch\n*** Add File: test_app.py\n+x = 1\n*** End Patch"
    r = await apply_patch_new_handler(worker, patch_content=patch)
    assert "périmètre" in r.output or "⛔" in r.output
    assert not (tmp_path / "missions" / "pollapp_m1" / "test_app.py").exists()
    assert not (tmp_path / "test_app.py").exists()


@pytest.mark.asyncio
async def test_apply_patch_new_offmission_unchanged(tmp_path):
    # Hors mission : résolution historique (lumena_root) conservée.
    from src.reasoning.handlers.files import apply_patch_new_handler
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path))
    patch = "*** Begin Patch\n*** Add File: libre.py\n+x = 1\n*** End Patch"
    r = await apply_patch_new_handler(ctx, patch_content=patch)
    assert "❌" not in r.output, r.output
    assert (tmp_path / "libre.py").exists()


# ── create_zip : le zip de sortie respecte le périmètre ──────────────────────────

@pytest.mark.asyncio
async def test_create_zip_scope(tmp_path):
    from src.reasoning.handlers.files import write_file_handler, create_zip_handler
    worker = _worker_ctx(tmp_path, ["app.py", "livrable.zip"])
    await write_file_handler(worker, path="app.py", content="x = 1")
    src = str(tmp_path / "missions" / "pollapp_m1" / "app.py")
    # zip possédé → OK
    ok = await create_zip_handler(worker, source_paths=src, zip_path=str(tmp_path / "missions" / "pollapp_m1" / "livrable.zip"))
    assert "✅" in ok.output or "zip" in ok.output.lower(), ok.output
    # zip HORS périmètre → bloqué
    r = await create_zip_handler(worker, source_paths=src, zip_path=str(tmp_path / "missions" / "pollapp_m1" / "evil.zip"))
    assert "périmètre" in r.output or "⛔" in r.output


# ── LOT 2.8 : owned en forme COMPLÈTE (le format réel du run BudgetBuddy) ────────
# Le lead avait passé allowed_files=['missions/<id>/storage.py'] (workspace-relatif)
# alors que le garde compare en mission-relatif → le worker était REFUSÉ sur ses
# propres fichiers (« hors de ton périmètre. Tu possèdes : [le fichier exact] »),
# et la seule écriture acceptée était la duplication missions/<id>/missions/<id>.

def test_owned_fullform_normalized(tmp_path):
    ctx = HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="w1",
        mission_workspace="missions/task_96e6",
        mission_allowed_files=["missions/task_96e6/storage.py",
                               "workspace/missions/task_96e6/tests/test_api.py"],
    )
    assert ctx.mission_allowed_files_set() == frozenset(
        {"storage.py", "tests/test_api.py"})


@pytest.mark.asyncio
async def test_budgetbuddy_repro_worker_can_fill_own_stub(tmp_path):
    # LA repro : owned en forme complète + chemins complets passés aux outils →
    # le worker doit pouvoir remplir SON stub, au BON endroit, sans duplication.
    from src.reasoning.handlers.files import write_file_handler, edit_file_handler
    sub = "missions/task_96e6"
    # le lead (sans périmètre) pose le stub
    lead = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path),
                          is_mission_run=True, runtime_task_id="lead",
                          mission_workspace=sub)
    await write_file_handler(lead, path="storage.py",
                             content="# TODO (worker) : implémenter selon CONTRAT.md")
    worker = HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="w_storage",
        mission_workspace=sub,
        mission_allowed_files=[f"{sub}/storage.py"],   # forme complète (comme au run)
    )
    # edit_file avec le CHEMIN COMPLET (l'appel exact qui était refusé au run)
    r = await edit_file_handler(
        worker, file_path=f"{sub}/storage.py",
        old_content="# TODO (worker) : implémenter selon CONTRAT.md",
        new_content="def add_expense(label, amount, category):\n    return {}")
    assert "✅" in r.output, r.output
    assert "add_expense" in (tmp_path / "missions" / "task_96e6" / "storage.py").read_text(encoding="utf-8")
    assert not (tmp_path / "missions" / "task_96e6" / "missions").exists()   # zéro dup
    # et le périmètre TIENT toujours : un fichier d'un autre worker reste refusé
    blocked = await write_file_handler(worker, path=f"{sub}/app.py", content="x")
    assert "périmètre" in blocked.output or "⛔" in blocked.output
    assert not (tmp_path / "missions" / "task_96e6" / "app.py").exists()


@pytest.mark.asyncio
async def test_worker_relative_path_with_fullform_owned(tmp_path):
    # Chemin RELATIF (app.py) + owned en forme complète → autorisé aussi.
    from src.reasoning.handlers.files import write_file_handler
    sub = "missions/task_96e6"
    worker = HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="w1",
        mission_workspace=sub, mission_allowed_files=[f"{sub}/app.py"],
    )
    r = await write_file_handler(worker, path="app.py", content="ok")
    assert "✅" in r.output, r.output
    assert (tmp_path / "missions" / "task_96e6" / "app.py").exists()


# ── FAIL CLOSED : allowed_files sans mission_workspace = refus propre ────────────

@pytest.mark.asyncio
async def test_fail_closed_allowed_files_without_workspace(tmp_path):
    # Un worker mal métadonné (périmètre défini mais dossier mission absent) ne
    # redevient PAS libre : refus propre tant que le contrat n'est pas vérifiable.
    from src.reasoning.handlers.files import write_file_handler
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path),
                         is_mission_run=True, runtime_task_id="w1",
                         mission_workspace="", mission_allowed_files=["app.py"])
    r = await write_file_handler(ctx, path="app.py", content="x")
    assert "⛔" in r.output and "mission_workspace" in r.output
