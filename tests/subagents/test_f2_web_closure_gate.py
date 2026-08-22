"""F2 — un livrable web non vérifié ne se clôt plus en `completed` silencieux.

Cause (AUD-013, `M02_WEB` du benchmark 2026-08-09) : trois workers terminés,
trois tests pytest verts, `/api/habits` en 200 — mais la route principale `/` en
404, preview et vérification navigateur en échec, et pourtant mission `done` avec
un extrait d'analyse de `app.py` en guise de livraison.

Le vérificateur runtime a pourtant fait son travail : il a échoué et le verdict a
été persisté (`web_runtime_failed`) par `_set_web_runtime_verification_state`.
Personne ne le lisait à la clôture — et `mission_terminal_facts` n'exposait que le
SUCCÈS (« navigateur=prouve »), jamais l'échec. F2 ferme ce câblage.

Ce lot ne touche NI le vérificateur, NI le truth-lock, NI le gate navigateur : la
détection fonctionnait déjà, seule la clôture était sourde.
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.handlers.missions import mission_terminal_facts, _mission_facts_text
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as runner_mod
from src.subagents.runner import closure_decision


# ── La décision de clôture est une fonction PURE ─────────────────────────────

def test_clean_run_is_completed():
    code, detail = closure_decision(overclaim=False, web_failed=False)
    assert code == "completed"
    assert "autorise le resultat" in detail


def test_overclaim_alone_flags_unproven_claims():
    code, _ = closure_decision(overclaim=True, web_failed=False)
    assert code == "completed_with_unproven_claims"


def test_web_failure_alone_flags_web_unverified():
    code, detail = closure_decision(overclaim=False, web_failed=True)
    assert code == "completed_web_unverified"
    assert "verification runtime a echoue" in detail


def test_web_failure_outranks_overclaim_but_detail_says_both():
    """Un vérificateur qui a réellement échoué est un fait plus dur qu'une
    formulation rétrogradée — mais on ne perd pas l'information."""
    code, detail = closure_decision(overclaim=True, web_failed=True)
    assert code == "completed_web_unverified"
    assert "verification runtime a echoue" in detail
    assert "retrogradee" in detail


# ── Le runner applique la décision ───────────────────────────────────────────

@pytest.fixture
def _stub_registry(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: sentinel)
    return sentinel


def _core_with(orch, silent):
    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    return core


def _new_mission(orch, objective="construis un site"):
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview=objective, metadata={"kind": "mission"})
    return rec.task_id


@pytest.mark.asyncio
async def test_failed_web_runtime_blocks_clean_closure(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        # Exactement ce que fait `_set_web_runtime_verification_state` pendant le run.
        kw["task_orchestrator"].set_task_metadata(kw["task_id"], web_runtime_failed=True)
        return "Site livré. L'API répond correctement."

    mid = _new_mission(orch)
    out = await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")

    # Le travail existe : la mission reste consultable…
    assert out["status"] == "done"
    meta = orch.get_task(mid)["metadata"]
    # …mais elle n'est plus déclarée prouvée.
    assert meta["terminal_reason_code"] == "completed_web_unverified"
    assert meta["completion_proven"] is False


@pytest.mark.asyncio
async def test_verified_web_runtime_stays_completed(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        kw["task_orchestrator"].set_task_metadata(
            kw["task_id"], web_runtime_failed=False, web_runtime_verified=True,
        )
        return "Site livré et vérifié au navigateur."

    mid = _new_mission(orch)
    await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")
    assert orch.get_task(mid)["metadata"]["terminal_reason_code"] == "completed"


@pytest.mark.asyncio
async def test_non_web_mission_is_unaffected(tmp_path, _stub_registry):
    """Une mission sans livrable web ne doit subir aucun changement."""
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        return "Rapport de veille rédigé."

    mid = _new_mission(orch, objective="rédige une veille")
    await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")
    meta = orch.get_task(mid)["metadata"]
    assert meta["terminal_reason_code"] == "completed"
    assert "completion_proven" not in meta


@pytest.mark.asyncio
async def test_web_failure_and_overclaim_together(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        kw["task_orchestrator"].set_task_metadata(kw["task_id"], web_runtime_failed=True)
        kw["proof_out"]["mission_truth_lock_overclaim"] = True
        return "Publié et vérifié au navigateur."

    mid = _new_mission(orch)
    await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")
    meta = orch.get_task(mid)["metadata"]
    assert meta["terminal_reason_code"] == "completed_web_unverified"
    assert "retrogradee" in meta["terminal_reason_detail"]


# ── Symétrie des faits exposés au chat ───────────────────────────────────────

def _record(**meta):
    return {"state": "done", "metadata": meta}


def test_facts_expose_web_failure_not_only_success():
    facts = mission_terminal_facts(_record(web_runtime_failed=True))
    assert facts["web_runtime_failed"] is True
    assert facts["web_runtime_verified"] is False


def test_facts_text_reports_runtime_failure():
    text = _mission_facts_text(_record(
        terminal_reason_code="completed_web_unverified", web_runtime_failed=True,
    ))
    assert "navigateur=echec_runtime" in text


def test_facts_text_still_reports_proof_when_verified():
    """Le succès garde exactement sa formulation historique."""
    text = _mission_facts_text(_record(
        terminal_reason_code="completed", web_runtime_verified=True,
    ))
    assert "navigateur=prouve" in text
    assert "echec_runtime" not in text
