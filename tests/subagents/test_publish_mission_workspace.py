"""A2 (Phase A, run FitLog) — publish_mission_workspace : publication déterministe.

Le run FitLog est mort en recopiant le livrable à la main (Copy-Item bloqué →
read/write LLM fichier par fichier → style.css −38 %, storage.py réinventé,
tests JAMAIS copiés → pytest impossible → anti-boucle → lead tué).
Ici : UNE copie code-side, tests inclus, caches exclus, cible bornée au workspace.
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.reasoning.handlers import missions as M
from src.tools.file_guardrails import WorkspaceFileGuardrails


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    fg = WorkspaceFileGuardrails(tmp_path)
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id,
                                file_guardrails=fg)
    return ctx, orch


def _make_lead(orch, depth=1):
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": depth})


def _seed_mission(tmp_path, lead_id, *, with_contract=True):
    """Peuple le dossier mission comme après un run de workers."""
    d = tmp_path / "missions" / lead_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "storage.py").write_text("def add(): return 1\n", encoding="utf-8")
    (d / "test_storage.py").write_text("def test_add(): assert True\n", encoding="utf-8")
    (d / "static").mkdir(exist_ok=True)
    (d / "static" / "style.css").write_text("body{}", encoding="utf-8")
    (d / ".backups").mkdir(exist_ok=True)
    (d / ".backups" / "old.bak").write_text("x", encoding="utf-8")
    (d / "__pycache__").mkdir(exist_ok=True)
    (d / "__pycache__" / "m.pyc").write_text("x", encoding="utf-8")
    if with_contract:
        (d / "contract.json").write_text(
            '{"project": "fitlog", "files": [{"path": "storage.py", "owner": "w"}]}',
            encoding="utf-8")
    return d


@pytest.mark.asyncio
async def test_refused_at_chat(tmp_path):
    ctx, orch = _ctx(tmp_path)  # pas de runtime_task_id → depth 0
    r = await M.publish_mission_workspace_handler(ctx, target="out")
    assert not r.success
    assert "DANS une mission" in r.output


@pytest.mark.asyncio
async def test_publish_copies_everything_tests_included(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)

    r = await M.publish_mission_workspace_handler(ctx, target="fitlog")
    assert r.success, r.output
    dest = tmp_path / "fitlog"
    # tests INCLUS (leur absence a rendu pytest impossible sur le run FitLog)
    assert (dest / "test_storage.py").is_file()
    assert (dest / "storage.py").read_text(encoding="utf-8") == "def add(): return 1\n"
    assert (dest / "static" / "style.css").is_file()  # arborescence préservée
    # caches/backups EXCLUS
    assert not (dest / ".backups").exists()
    assert not (dest / "__pycache__").exists()
    assert "Livrable publié" in r.output
    assert "pytest" in r.output  # guidance étape suivante


@pytest.mark.asyncio
async def test_default_target_from_contract_project(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id, with_contract=True)

    r = await M.publish_mission_workspace_handler(ctx)
    assert r.success, r.output
    assert (tmp_path / "fitlog" / "storage.py").is_file()


@pytest.mark.asyncio
async def test_workspace_prefix_stripped(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)

    r = await M.publish_mission_workspace_handler(ctx, target="workspace/fitlog")
    assert r.success, r.output
    assert (tmp_path / "fitlog" / "storage.py").is_file()
    assert not (tmp_path / "workspace" / "fitlog").exists()


@pytest.mark.asyncio
async def test_traversal_and_absolute_refused(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)

    for bad in ("../evil", "a/../../evil", "C:/evil", "/evil"):
        r = await M.publish_mission_workspace_handler(ctx, target=bad)
        assert not r.success, bad
    assert not (tmp_path.parent / "evil").exists()


@pytest.mark.asyncio
async def test_missions_tree_and_self_refused(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)

    r = await M.publish_mission_workspace_handler(ctx, target=f"missions/{lead.task_id}")
    assert not r.success
    r = await M.publish_mission_workspace_handler(ctx, target="missions/autre")
    assert not r.success


@pytest.mark.asyncio
async def test_republish_updates_files(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    d = _seed_mission(tmp_path, lead.task_id)

    r1 = await M.publish_mission_workspace_handler(ctx, target="fitlog")
    assert r1.success
    (d / "storage.py").write_text("def add(): return 2\n", encoding="utf-8")
    r2 = await M.publish_mission_workspace_handler(ctx, target="fitlog")
    assert r2.success
    assert (tmp_path / "fitlog" / "storage.py").read_text(encoding="utf-8") == "def add(): return 2\n"


@pytest.mark.asyncio
async def test_publish_includes_declared_external_document_artifact(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)
    external = tmp_path / "documents" / "aquawatch.pdf"
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_bytes(b"%PDF-aquawatch")
    orch.set_task_metadata(lead.task_id, artifacts=[str(external.resolve())])

    r = await M.publish_mission_workspace_handler(ctx, target="aquawatch")

    assert r.success, r.output
    assert (tmp_path / "aquawatch" / "aquawatch.pdf").read_bytes() == b"%PDF-aquawatch"
    assert "aquawatch.pdf" in r.output


@pytest.mark.asyncio
async def test_publish_rejects_external_artifact_outside_workspace(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    _seed_mission(tmp_path, lead.task_id)
    outside = tmp_path.parent / "outside-secret.pdf"
    outside.write_bytes(b"secret")
    try:
        orch.set_task_metadata(lead.task_id, artifacts=[str(outside.resolve())])
        r = await M.publish_mission_workspace_handler(ctx, target="safe")
        assert r.success, r.output
        assert not (tmp_path / "safe" / outside.name).exists()
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_publish_rejects_contractual_web_bundle_drift(tmp_path):
    import json

    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id
    mission_dir = tmp_path / "missions" / lead.task_id
    static = mission_dir / "static"
    static.mkdir(parents=True)
    contract = {
        "project": "runway",
        "files": [
            {"path": "static/index.html", "owner": "frontend", "desc": "UI"},
            {"path": "static/style.css", "owner": "frontend", "desc": "Styles"},
            {"path": "static/app.js", "owner": "frontend", "desc": "Interactions"},
        ],
    }
    (mission_dir / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (static / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><script src="script.js"></script>',
        encoding="utf-8",
    )
    (static / "style.css").write_text("body {}", encoding="utf-8")
    (static / "app.js").write_text("console.log('canonical')", encoding="utf-8")
    (static / "script.js").write_text("console.log('drift')", encoding="utf-8")

    result = await M.publish_mission_workspace_handler(ctx, target="runway")

    assert not result.success
    assert "bundle web contractuel incoherent" in result.output
    assert "static/script.js" in result.output
    assert not (tmp_path / "runway").exists()


def test_registered_and_classified():
    from src.reasoning.handlers.missions import get_missions_handler_defs
    from src.reasoning.hallucination_guard import _HC_TOOLS_MISSION, _HC_TOOLS_ANY_ACTION

    names = {d.name for d in get_missions_handler_defs()}
    assert "publish_mission_workspace" in names
    assert "publish_mission_workspace" in _HC_TOOLS_MISSION
    assert "publish_mission_workspace" in _HC_TOOLS_ANY_ACTION
