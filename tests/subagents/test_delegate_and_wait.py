"""Lot 5.2 — delegate_and_wait : lead crée N workers, attend (borné), fusionne.

Couvre : enregistrement + capability MUTATION (ledger) + anti-dérive classé ;
bout-en-bout via le worker réel (2 workers en // → fusion) ; refus au chat (depth 0) ;
garde de profondeur ; timeout → partiel ; lead annulé → sort proprement.
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.reasoning.handlers import missions as M
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
    monkeypatch.delenv("LUMENA_TASK_RESULT_MAX_CHARS", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_FUSION_EXCERPT_CHARS", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id), orch


def _make_lead(orch, depth=1):
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": depth})


# ── enregistrement + gardes ─────────────────────────────────────────────────────

def test_registered_and_classified():
    from src.reasoning.tool_registry import ToolRegistry
    from src.reasoning.plan_evidence import get_tool_capabilities, ProofCapability
    from src.reasoning.hallucination_guard import _HC_TOOLS_ANY_ACTION

    reg = ToolRegistry(lumena=None)
    assert "delegate_and_wait" in reg.tools
    assert reg._tool_modules["delegate_and_wait"] == "missions"
    # MUTATION (hérité de la catégorie) → compte au ledger « preuve avant FINAL »
    assert ProofCapability.GENERIC_MUTATION in get_tool_capabilities(
        "delegate_and_wait", "missions", "missions")
    # anti-dérive : classé comme action
    assert "delegate_and_wait" in _HC_TOOLS_ANY_ACTION


# ── bout-en-bout (worker réel) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegate_fuses_two_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")

    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    core = ctx.lumena

    async def fake_run(core_arg, *, mission_id, objective, **k):
        # Priorité 1 : en délégation parallèle, l'objectif porte désormais une note de
        # steering navigateur (append). Le livrable réel d'un worker est indépendant de
        # cette note → on dérive l'artefact de la 1re ligne (le sujet : « A », « B »).
        topic = (objective or "").splitlines()[0].strip()
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.set_task_metadata(mission_id, artifacts=[f"out/{topic}.md"])
        core_arg.task_orchestrator.mark_done(mission_id, result_summary=f"fait: {topic}")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(core)  # worker app-lifetime → exécute les workers

    res = await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=5.0)
    assert res.success
    assert "2/2" in res.output
    assert "fait: A" in res.output and "fait: B" in res.output
    assert "out/A.md" in res.output and "out/B.md" in res.output
    # (le steering anti-contention est couvert par test_parallel_browser_steering_helper —
    #  res.output ne montre qu'un APERÇU tronqué de l'objectif, pas la note complète)

    children = orch.get_children(lead.task_id)
    assert len(children) == 2
    assert all(c["state"] == "done" for c in children)
    assert all(c["metadata"]["depth"] == 2 for c in children)
    # le lead garde la trace de ses enfants
    assert set((orch.get_task(lead.task_id)["metadata"]).get("children")) == {c["task_id"] for c in children}


@pytest.mark.asyncio
async def test_workers_inherit_lead_deadline(tmp_path, monkeypatch):
    # Lot 5.7.3b — propagation du budget : les workers héritent du deadline_ts du lead.
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, deadline="ce soir", deadline_ts="2026-06-29T20:00:00")
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary=f"fait: {objective}")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=5.0)
    children = orch.get_children(lead.task_id)
    assert len(children) == 2
    assert all(c["metadata"].get("deadline_ts") == "2026-06-29T20:00:00" for c in children)
    assert all(c["metadata"].get("deadline") == "ce soir" for c in children)


@pytest.mark.asyncio
async def test_workers_no_deadline_when_lead_has_none(tmp_path, monkeypatch):
    # Sans échéance lead → aucun deadline_ts propagé (comportement identique).
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)
    await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    for c in orch.get_children(lead.task_id):
        assert "deadline_ts" not in c["metadata"]


# ── LOT 2.1 : dossier de mission ISOLÉ hérité par les workers ────────────────────

@pytest.mark.asyncio
async def test_workers_inherit_mission_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, objective="Site de sondage PollApp")
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)
    await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=5.0)

    lead_ws = orch.get_task(lead.task_id)["metadata"].get("mission_workspace")
    assert lead_ws and lead_ws.startswith("missions/") and lead.task_id in lead_ws
    children = orch.get_children(lead.task_id)
    assert len(children) == 2
    # tous les workers partagent LE dossier du lead
    assert all(c["metadata"].get("mission_workspace") == lead_ws for c in children)


@pytest.mark.asyncio
async def test_existing_mission_workspace_is_reused(tmp_path, monkeypatch):
    # Si le lead a déjà un mission_workspace → on ne l'écrase pas.
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/deja_la")
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)
    await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)

    assert orch.get_task(lead.task_id)["metadata"]["mission_workspace"] == "missions/deja_la"
    for c in orch.get_children(lead.task_id):
        assert c["metadata"].get("mission_workspace") == "missions/deja_la"


# ── LOT 2.3 : allowed_files par worker (objectifs structurés) ────────────────────

@pytest.mark.asyncio
async def test_workers_receive_allowed_files_from_structured_objectives(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary=f"fait: {objective.splitlines()[0]}")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    res = await M.delegate_and_wait_handler(ctx, [
        {"objective": "Backend Flask", "allowed_files": ["app.py"]},
        {"objective": "Tests API", "allowed_files": ["test_app.py", "conftest.py"]},
        "Documentation libre",   # chaîne simple → PAS de restriction
    ], timeout=5.0)
    assert res.success and "3/3" in res.output

    children = orch.get_children(lead.task_id)
    by_obj = {c["metadata"]["objective"].splitlines()[0]: c["metadata"] for c in children}
    assert by_obj["Backend Flask"].get("allowed_files") == ["app.py"]
    assert by_obj["Tests API"].get("allowed_files") == ["test_app.py", "conftest.py"]
    assert "allowed_files" not in by_obj["Documentation libre"]   # rétro-compatible
    assert by_obj["Backend Flask"].get("routing_objective") == "Backend Flask"
    assert by_obj["Tests API"].get("routing_objective") == "Tests API"
    assert by_obj["Documentation libre"].get("routing_objective") == "Documentation libre"


def test_normalize_objectives_struct_preserves_allowed_files():
    out = M._normalize_objectives_struct([
        {"objective": "X", "allowed_files": ["a.py", "b.py"]},
        {"objective": "Y", "files": ["c.py"]},     # alias
        "Z",
    ])
    assert out[0] == {"text": "X", "allowed_files": ["a.py", "b.py"]}
    assert out[1] == {"text": "Y", "allowed_files": ["c.py"]}
    assert out[2] == {"text": "Z", "allowed_files": []}


def test_normalize_objectives_struct_from_serialized_string():
    # le LLM passe parfois la liste sérialisée en CHAÎNE → structure préservée
    out = M._normalize_objectives_struct(
        "[{'objective': 'X', 'allowed_files': ['a.py']}, 'Y']")
    assert out[0]["allowed_files"] == ["a.py"]
    assert out[1] == {"text": "Y", "allowed_files": []}


def test_contract_delegation_specs_are_canonical_and_stable():
    contract = {
        "project": "demo",
        "files": [
            {"path": "app.py", "owner": "backend", "exports": ["def create_app():"]},
            {"path": "static/app.js", "owner": "frontend", "desc": "UI browser"},
        ],
    }
    specs, fingerprint = M._contract_delegation_specs(contract)

    assert [s["owner"] for s in specs] == ["backend", "frontend"]
    assert specs[0]["allowed_files"] == ["app.py"]
    assert specs[1]["allowed_files"] == ["static/app.js"]
    assert len(fingerprint) == 64
    assert M._contract_delegation_specs(contract)[1] == fingerprint


@pytest.mark.asyncio
async def test_contract_delegation_ignores_unscoped_lead_rewrite(tmp_path, monkeypatch):
    import json
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/contract_scope")
    mission_dir = tmp_path / "missions" / "contract_scope"
    mission_dir.mkdir(parents=True)
    (mission_dir / "contract.json").write_text(json.dumps({
        "project": "demo",
        "files": [
            {"path": "app.py", "owner": "backend", "exports": ["def create_app():"]},
            {"path": "static/app.js", "owner": "frontend", "desc": "UI browser"},
        ],
    }), encoding="utf-8")
    ctx.runtime_task_id = lead.task_id
    ctx.file_guardrails = WorkspaceFileGuardrails(tmp_path)

    monkeypatch.setattr(worker_mod, "run_mission", _fake_run_ok)
    worker_mod.start_mission_worker(ctx.lumena)
    result = await M.delegate_and_wait_handler(
        ctx, ["Fais tout le backend", "Fais tout le frontend"], timeout=5.0,
    )

    assert result.success
    children = orch.get_children(lead.task_id)
    by_owner = {(c["metadata"] or {}).get("delegation_owner"): c for c in children}
    assert set(by_owner) == {"backend", "frontend"}
    assert by_owner["backend"]["metadata"]["allowed_files"] == ["app.py"]
    assert by_owner["frontend"]["metadata"]["allowed_files"] == ["static/app.js"]


@pytest.mark.asyncio
async def test_contract_delegation_is_idempotent(tmp_path, monkeypatch):
    import json
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/contract_idempotent")
    mission_dir = tmp_path / "missions" / "contract_idempotent"
    mission_dir.mkdir(parents=True)
    (mission_dir / "contract.json").write_text(json.dumps({
        "project": "demo",
        "files": [
            {"path": "app.py", "owner": "backend", "exports": ["def create_app():"]},
            {"path": "tests/test_app.py", "owner": "tests", "desc": "API tests"},
        ],
    }), encoding="utf-8")
    ctx.runtime_task_id = lead.task_id
    ctx.file_guardrails = WorkspaceFileGuardrails(tmp_path)
    run_count = {"value": 0}

    async def counted_run(core_arg, *, mission_id, objective, **kwargs):
        run_count["value"] += 1
        return await _fake_run_ok(
            core_arg, mission_id=mission_id, objective=objective, **kwargs
        )

    monkeypatch.setattr(worker_mod, "run_mission", counted_run)
    worker_mod.start_mission_worker(ctx.lumena)

    first = await M.delegate_and_wait_handler(ctx, ["backend", "tests"], timeout=5.0)
    first_ids = [c["task_id"] for c in orch.get_children(lead.task_id)]
    second = await M.delegate_and_wait_handler(
        ctx, ["refais backend", "refais tests"], timeout=5.0,
    )
    second_ids = [c["task_id"] for c in orch.get_children(lead.task_id)]

    assert first.success and second.success
    assert second_ids == first_ids
    assert run_count["value"] == 2
    assert set((orch.get_task(lead.task_id)["metadata"] or {})["children"]) == set(first_ids)


# ── LOT 2.5 : consigne d'intégration (pytest avant FINAL) dans la fusion ─────────

async def _fake_run_ok(core_arg, *, mission_id, objective, **k):
    core_arg.task_orchestrator.mark_running(mission_id)
    core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
    return {"status": "done"}


@pytest.mark.asyncio
async def test_fusion_adds_pytest_hint_when_tests_on_disk(tmp_path, monkeypatch):
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/x_m1")
    (tmp_path / "missions" / "x_m1").mkdir(parents=True)
    (tmp_path / "missions" / "x_m1" / "test_api.py").write_text("x", encoding="utf-8")
    ctx.runtime_task_id = lead.task_id
    ctx.file_guardrails = WorkspaceFileGuardrails(tmp_path)

    monkeypatch.setattr(worker_mod, "run_mission", _fake_run_ok)
    worker_mod.start_mission_worker(ctx.lumena)
    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert "INTÉGRATION OBLIGATOIRE" in res.output and "pytest" in res.output
    assert "missions/x_m1" in res.output   # dit OÙ lancer


@pytest.mark.asyncio
async def test_fusion_adds_pytest_hint_from_contract_only(tmp_path, monkeypatch):
    # Le contrat DÉCLARE tests/test_api.py mais le fichier n'existe PAS encore sur
    # disque → la source contractuelle suffit (note de revue 2.5).
    import json as _json
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/x_m2")
    d = tmp_path / "missions" / "x_m2"; d.mkdir(parents=True)
    (d / "contract.json").write_text(_json.dumps(
        {"files": [{"path": "app.py", "owner": "b"},
                   {"path": "tests/test_api.py", "owner": "t"}]}), encoding="utf-8")
    ctx.runtime_task_id = lead.task_id
    ctx.file_guardrails = WorkspaceFileGuardrails(tmp_path)

    monkeypatch.setattr(worker_mod, "run_mission", _fake_run_ok)
    worker_mod.start_mission_worker(ctx.lumena)
    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert "INTÉGRATION OBLIGATOIRE" in res.output


@pytest.mark.asyncio
async def test_fusion_no_hint_without_tests(tmp_path, monkeypatch):
    # Mission sans tests (recherche/rédaction) → footer strictement inchangé.
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/x_m3")
    (tmp_path / "missions" / "x_m3").mkdir(parents=True)
    ctx.runtime_task_id = lead.task_id
    ctx.file_guardrails = WorkspaceFileGuardrails(tmp_path)

    monkeypatch.setattr(worker_mod, "run_mission", _fake_run_ok)
    worker_mod.start_mission_worker(ctx.lumena)
    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert "INTÉGRATION OBLIGATOIRE" not in res.output
    assert "Fusionne-les DIRECTEMENT" in res.output   # footer anti-scavenge intact


@pytest.mark.asyncio
async def test_fusion_hint_from_worker_artifacts(tmp_path, monkeypatch):
    # Pas de dossier mission scannable mais un worker déclare un artefact de test.
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.set_task_metadata(
            mission_id, artifacts=["C:/ws/pollapp/test_api.py"])
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)
    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert "INTÉGRATION OBLIGATOIRE" in res.output


# ── flux lead↔workers : le lead reçoit les LIVRABLES, pas des miettes ────────────

def test_orchestrator_result_cap_keeps_long_result(tmp_path, monkeypatch):
    # Garde-fou 1 : result_summary conserve un livrable > 1000 car. (avant : tronqué à 1000)
    monkeypatch.delenv("LUMENA_TASK_RESULT_MAX_CHARS", raising=False)
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="w", metadata={"kind": "mission", "depth": 2})
    long_report = "X" * 6000
    orch.mark_done(rec.task_id, result_summary=long_report)
    stored = orch.get_task(rec.task_id)["result_summary"]
    assert len(stored) == 6000  # < cap défaut 8000 → conservé entier (avant : 1000)


def test_orchestrator_result_cap_configurable(tmp_path, monkeypatch):
    # le cap a un plancher à 1000 (jamais minuscule) → on teste au-dessus
    monkeypatch.setenv("LUMENA_TASK_RESULT_MAX_CHARS", "2000")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="w", metadata={"kind": "mission"})
    orch.mark_done(rec.task_id, result_summary="Y" * 5000)
    assert len(orch.get_task(rec.task_id)["result_summary"]) == 2000


def test_orchestrator_result_cap_survives_reload(tmp_path, monkeypatch):
    # Garde-fou reviewer : le résultat long ne doit PAS être re-tronqué au reload JSON.
    monkeypatch.delenv("LUMENA_TASK_RESULT_MAX_CHARS", raising=False)
    path = str(tmp_path / "s.json")
    orch = TaskOrchestrator(persistence_path=path)
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="w", metadata={"kind": "mission"})
    orch.mark_done(rec.task_id, result_summary="X" * 6000)
    # reload depuis le JSON persisté (simule un reboot)
    orch2 = TaskOrchestrator(persistence_path=path)
    assert len(orch2.get_task(rec.task_id)["result_summary"]) == 6000  # avant : 1000


@pytest.mark.asyncio
async def test_fusion_carries_ids_excerpt_and_anti_scavenge(tmp_path, monkeypatch):
    # Garde-fou 2 : la fusion inclut les IDs enfants + la consigne de fusion directe,
    # et un aperçu RICHE (> 300) du livrable de chaque worker.
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    core = ctx.lumena

    big = "Contenu détaillé du guide. " * 60  # ~1600 car.

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary=f"{objective}: {big}")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(core)

    res = await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=5.0)
    assert res.success
    out = res.output
    # IDs enfants présents (le lead peut faire mission_result dessus)
    children = orch.get_children(lead.task_id)
    for c in children:
        assert f"(id: {c['task_id']})" in out
    # consigne anti-scavenge
    low = out.lower()
    assert "ne va pas chercher de fichiers" in low and "fusionne" in low
    # aperçu RICHE (l'ancien cap 300 aurait coupé bien avant)
    assert out.count("Contenu détaillé du guide.") > 10


@pytest.mark.asyncio
async def test_fusion_excerpt_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    monkeypatch.setenv("LUMENA_MISSION_FUSION_EXCERPT_CHARS", "400")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    core = ctx.lumena

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="Z" * 5000)
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(core)

    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert res.success
    # un seul worker, excerpt 400 → la longue suite de Z est coupée à 400
    assert "Z" * 400 in res.output and "Z" * 401 not in res.output


# ── refus au chat (chat libre) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refused_at_chat_depth0(tmp_path):
    ctx, orch = _ctx(tmp_path, runtime_task_id=None)  # chat
    res = await M.delegate_and_wait_handler(ctx, ["A"])
    assert not res.success
    assert "create_mission" in res.error
    # rien créé
    assert orch.get_conversation_tasks("__missions__") == []


# ── garde de profondeur ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_depth_guard_blocks_when_max(tmp_path):
    ctx, orch = _ctx(tmp_path)  # MAX_DEPTH défaut 1
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    res = await M.delegate_and_wait_handler(ctx, ["A"])
    assert not res.success
    assert "profondeur" in res.error.lower()
    assert orch.get_children(lead.task_id) == []


# ── timeout → partiel (sans worker → enfants restent queued) ─────────────────────

@pytest.mark.asyncio
async def test_timeout_returns_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    # pas de worker démarré → les enfants restent `queued` → on doit timeouter, pas hang
    res = await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=0.3)
    assert res.success  # renvoie un PARTIEL, ne lève jamais
    assert "délai dépassé" in res.output.lower() or "partiel" in res.output.lower()
    assert "0/2" in res.output


# ── lead annulé → sort proprement ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_lead_breaks_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id
    orch.cancel_task(lead.task_id)  # annulation demandée AVANT
    res = await M.delegate_and_wait_handler(ctx, ["A"], timeout=5.0)
    assert res.success
    assert "annul" in res.output.lower()


# ── #3 normalisation : le LLM passe des dicts (objective/context), pas des strings ──

def test_normalize_objectives_dicts():
    out = M._normalize_objectives([
        {"objective": "Recherche Next.js", "context": "React full-stack"},
        {"description": "Recherche Astro"},
        "Recherche SvelteKit",
    ])
    assert out[0].startswith("Recherche Next.js") and "React full-stack" in out[0]
    assert out[1] == "Recherche Astro"
    assert out[2] == "Recherche SvelteKit"


def test_normalize_objectives_serialized_string_list():
    # le LLM passe parfois la liste comme une CHAÎNE repr → on la parse
    out = M._normalize_objectives("[{'objective': 'X'}, {'objective': 'Y'}]")
    assert out == ["X", "Y"]


def test_normalize_objectives_plain_string():
    assert M._normalize_objectives("juste un objectif") == ["juste un objectif"]
