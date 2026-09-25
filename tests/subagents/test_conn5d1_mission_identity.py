"""CONN-5D-1 - identite d'execution des missions.

Defaut de production (audit du 15 septembre 2026) : les missions tournent dans
`mission_worker_loop`, demarre au `lifespan`, donc SANS `RuntimeContext`. Le
provider IDE exige un contexte proprietaire : tout appel IDE d'une vraie mission
etait refuse (`ide_owner_context_required`), alors que les tests de 5A a
5C-2 poussaient eux-memes ce contexte.

Correctif : l'identite du DEMANDEUR est relevee a la creation (jamais depuis les
arguments du modele), heritee par les workers, et `run_mission` pose un contexte
construit uniquement depuis elle. Sans demandeur : aucun contexte, comme avant.
"""
from __future__ import annotations

import types

import pytest

from src.runtime.context import (
    RuntimeContext, get_current_runtime_context, pop_runtime_context, push_runtime_context,
)
from src.runtime.permissions import is_owner
from src.runtime.task_orchestrator import TaskOrchestrator
from src.reasoning.handlers import missions as M
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import runner as runner_mod
from src.subagents import worker as worker_mod
from src.subagents.mission_identity import (
    capture_requester, mission_runtime_context, requester_for_worker, validated_requester,
)
from src.reasoning.caller_context import REACT
from tests.reasoning.test_conn3c_live_catalogue import registry as registry
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur
from tests.reasoning.test_conn5a_ide_mission_gate import _mission as _mission_ide
from tests.tools.test_conn3c_ide_capabilities import service as service

PROPRIETAIRE = {"user_role": "owner", "user_id": "local:owner", "owner_user_id": "local:owner", "channel": "web"}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


def _contexte(role="owner", channel="web", user_id="local:owner"):
    return RuntimeContext(channel=channel, client="test", request_id="requete", conversation_id="conversation",
                          message_id="message", user_id=user_id, owner_user_id="local:owner", user_role=role)


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id), orch


def _missions(orch):
    return orch.get_conversation_tasks("__missions__", limit=100)


# ══════════════════════════════════════════════════════════════════════════
#  1. Parties pures
# ══════════════════════════════════════════════════════════════════════════


def test_sans_contexte_aucun_demandeur():
    assert capture_requester(None) is None


def test_demandeur_releve_du_contexte():
    assert capture_requester(_contexte()) == PROPRIETAIRE
    assert capture_requester(_contexte(role="guest", channel="telegram", user_id="tg:42")) == {
        "user_role": "guest", "user_id": "tg:42", "owner_user_id": "local:owner", "channel": "telegram"}


@pytest.mark.parametrize("valeur", [
    None, "owner", [], {},
    {**PROPRIETAIRE, "extra": "x"},
    {k: v for k, v in PROPRIETAIRE.items() if k != "channel"},
    {**PROPRIETAIRE, "user_role": "root"},
    {**PROPRIETAIRE, "user_role": 3},
    {**PROPRIETAIRE, "user_id": ""},
])
def test_demandeur_falsifie_ou_incomplet_refuse(valeur):
    assert validated_requester(valeur) is None


def test_demandeur_valide_rendu_en_copie():
    copie = validated_requester(PROPRIETAIRE)
    assert copie == PROPRIETAIRE and copie is not PROPRIETAIRE


def test_contexte_de_mission():
    assert mission_runtime_context("m1", None) is None
    contexte = mission_runtime_context("m1", PROPRIETAIRE)
    assert (contexte.channel, contexte.task_id, contexte.conversation_id) == ("mission", "m1", "__missions__")
    assert contexte.user_role == "owner" and is_owner(contexte.user_role)
    assert contexte.workspace_path is None and contexte.resolved_workspace is None
    invite = mission_runtime_context("m1", {**PROPRIETAIRE, "user_role": "guest"})
    assert not is_owner(invite.user_role)
    assert mission_runtime_context("m1", {**PROPRIETAIRE, "user_role": "root"}) is None


def test_heritage_du_worker(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    avec = orch.start_task(conversation_id="__missions__", channel="mission", message_preview="lead",
                           metadata={"kind": "mission", "depth": 1, "requester": dict(PROPRIETAIRE)})
    sans = orch.start_task(conversation_id="__missions__", channel="mission", message_preview="lead",
                           metadata={"kind": "mission", "depth": 1})
    faux = orch.start_task(conversation_id="__missions__", channel="mission", message_preview="lead",
                           metadata={"kind": "mission", "depth": 1, "requester": {"user_role": "owner"}})
    assert requester_for_worker(orch, avec.task_id) == PROPRIETAIRE
    assert requester_for_worker(orch, sans.task_id) is None
    assert requester_for_worker(orch, faux.task_id) is None
    assert requester_for_worker(orch, "inconnue") is None
    assert requester_for_worker(None, avec.task_id) is None


# ══════════════════════════════════════════════════════════════════════════
#  2. Creation de mission
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_creation_sans_contexte_aucun_demandeur(tmp_path):
    ctx, orch = _ctx(tmp_path)
    res = await M.create_mission_handler(ctx, "faire X")
    assert res.success
    assert "requester" not in _missions(orch)[0]["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,canal", [("owner", "web"), ("guest", "telegram"), ("owner", "ide")])
async def test_creation_releve_le_demandeur(tmp_path, role, canal):
    ctx, orch = _ctx(tmp_path)
    jeton = push_runtime_context(_contexte(role=role, channel=canal))
    try:
        res = await M.create_mission_handler(ctx, "faire X")
    finally:
        pop_runtime_context(jeton)
    assert res.success
    assert _missions(orch)[0]["metadata"]["requester"] == {
        "user_role": role, "user_id": "local:owner", "owner_user_id": "local:owner", "channel": canal}


@pytest.mark.asyncio
async def test_creation_vocale_garde_son_canal_source(tmp_path):
    ctx, orch = _ctx(tmp_path)
    jeton = push_runtime_context(_contexte(channel="voice"))
    try:
        await M.create_mission_handler(ctx, "faire X")
    finally:
        pop_runtime_context(jeton)
    meta = _missions(orch)[0]["metadata"]
    assert meta["source_channel"] == "voice" and meta["requester"]["channel"] == "voice"


@pytest.mark.asyncio
@pytest.mark.parametrize("lead_requester", [dict(PROPRIETAIRE), None])
async def test_les_workers_heritent_du_demandeur_du_lead(tmp_path, monkeypatch, lead_requester):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    meta = {"kind": "mission", "depth": 1}
    if lead_requester is not None:
        meta["requester"] = lead_requester
    lead = orch.start_task(conversation_id="__missions__", channel="mission", message_preview="lead", metadata=meta)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="fait")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)
    await M.delegate_and_wait_handler(ctx, ["A", "B"], timeout=5.0)
    enfants = orch.get_children(lead.task_id)
    assert len(enfants) == 2
    for enfant in enfants:
        if lead_requester is None:
            assert "requester" not in enfant["metadata"]
        else:
            assert enfant["metadata"]["requester"] == PROPRIETAIRE


# ══════════════════════════════════════════════════════════════════════════
#  3. Execution : run_mission pose puis retire le contexte
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def _registre_factice(monkeypatch):
    sentinelle = object()
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: sentinelle)
    return sentinelle


def _mission(orch, requester=None):
    meta = {"kind": "mission"}
    if requester is not None:
        meta["requester"] = requester
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="x", metadata=meta).task_id


def _coeur(orch, silent):
    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    return core


@pytest.mark.asyncio
async def test_run_mission_pose_le_contexte_du_demandeur(tmp_path, _registre_factice):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    vu = {}

    async def silent(objective, **kw):
        vu["contexte"] = get_current_runtime_context()
        return "ok"

    mid = _mission(orch, dict(PROPRIETAIRE))
    out = await runner_mod.run_mission(_coeur(orch, silent), mission_id=mid, objective="x")
    assert out["status"] == "done"
    contexte = vu["contexte"]
    assert contexte is not None and contexte.channel == "mission" and contexte.task_id == mid
    assert is_owner(contexte.user_role)
    assert get_current_runtime_context() is None


@pytest.mark.asyncio
async def test_run_mission_sans_demandeur_aucun_contexte(tmp_path, _registre_factice):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    vu = {}

    async def silent(objective, **kw):
        vu["contexte"] = get_current_runtime_context()
        return "ok"

    mid = _mission(orch)
    await runner_mod.run_mission(_coeur(orch, silent), mission_id=mid, objective="x")
    assert vu["contexte"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("erreur", [RuntimeError("boom"), SystemExit("task_cancelled")])
async def test_contexte_retire_sur_echec_et_annulation(tmp_path, _registre_factice, erreur):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    vu = {}

    async def silent(objective, **kw):
        vu["contexte"] = get_current_runtime_context()
        raise erreur

    mid = _mission(orch, dict(PROPRIETAIRE))
    out = await runner_mod.run_mission(_coeur(orch, silent), mission_id=mid, objective="x")
    assert out["status"] in {"failed", "cancelled"}
    assert vu["contexte"] is not None
    assert get_current_runtime_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("role,admis", [("owner", True), ("guest", False)])
async def test_le_rail_ide_de_mission_depend_du_demandeur(registry, service, role, admis):
    """Chaine reelle : sans contexte, la regle proprietaire refusait TOUTE mission ;
    avec le contexte pose par la mission, seul un demandeur proprietaire passe."""
    orch, root = _mission_ide(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="x")
    refus = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert refus.content == "IDE: ide_owner_context_required"
    orch.set_task_metadata("task_worker", requester={**PROPRIETAIRE, "user_role": role})
    from src.subagents.mission_identity import mission_runtime_scope
    with mission_runtime_scope(orch, "task_worker"):
        observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert get_current_runtime_context() is None
    if admis:
        assert observation.success is True, observation.content
        send.assert_awaited_once()
    else:
        assert observation.content == "IDE: ide_owner_context_required"
        send.assert_not_awaited()


@pytest.mark.asyncio
async def test_contexte_existant_restaure_apres_la_mission(tmp_path, _registre_factice):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        return "ok"

    avant = _contexte(role="guest", channel="telegram")
    jeton = push_runtime_context(avant)
    try:
        await runner_mod.run_mission(_coeur(orch, silent), mission_id=_mission(orch, dict(PROPRIETAIRE)),
                                     objective="x")
        assert get_current_runtime_context() is avant
    finally:
        pop_runtime_context(jeton)
