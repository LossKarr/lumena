"""Lot 5 — D : profil « lead » du sous-agent de mission.

Quand la délégation est POSSIBLE (depth < MAX_DEPTH), `run_mission` cadre l'objectif
en « lead » → le sous-agent SAIT qu'il peut déléguer (delegate_and_wait). Sinon
(flag défaut = 1), objectif brut → zéro régression. Steering, pas forçage.
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as R


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
    yield


def _core(orch, captured=None):
    async def fake_silent(task, **k):
        if captured is not None:
            captured["task"] = task
        return "ok"
    return types.SimpleNamespace(task_orchestrator=orch, think_and_act_silent=fake_silent)


def _mission(orch, depth=1):
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="obj", metadata={"kind": "mission", "depth": depth})


# ── _delegation_possible ─────────────────────────────────────────────────────────

def test_delegation_possible_depth1_max2(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=1)
    assert R._delegation_possible(_core(orch), rec.task_id) is True


def test_delegation_blocked_default_flag(tmp_path):
    # MAX_DEPTH défaut = 1, mission depth 1 → 1 < 1 faux → pas de délégation
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=1)
    assert R._delegation_possible(_core(orch), rec.task_id) is False


def test_delegation_blocked_for_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=2)  # un worker (depth 2) ne re-délègue pas
    assert R._delegation_possible(_core(orch), rec.task_id) is False


def test_delegation_no_orch():
    assert R._delegation_possible(types.SimpleNamespace(), "x") is False


# ── injection du préfixe lead dans run_mission ───────────────────────────────────

@pytest.mark.asyncio
async def test_run_mission_injects_lead_prefix_when_possible(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=1)
    captured = {}
    res = await R.run_mission(_core(orch, captured), mission_id=rec.task_id, objective="faire X")
    assert res["status"] == "done"
    assert captured["task"].startswith(R._LEAD_PREFIX)
    assert "delegate_and_wait" in captured["task"]
    assert "faire X" in captured["task"]


@pytest.mark.asyncio
async def test_run_mission_no_prefix_at_default_flag(tmp_path):
    # flag défaut = 1 → pas de délégation possible → objectif BRUT (zéro régression)
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=1)
    captured = {}
    res = await R.run_mission(_core(orch, captured), mission_id=rec.task_id, objective="faire X")
    assert res["status"] == "done"
    assert captured["task"] == "faire X"


# ── #2 anti-cascade : un WORKER (depth≥2) ne reçoit PAS le profil lead ────────────

@pytest.mark.asyncio
async def test_no_lead_prefix_for_worker_even_high_maxdepth(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "3")  # délégation techniquement possible
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=2)  # un worker
    assert R._is_top_lead(_core(orch), rec.task_id) is False
    captured = {}
    res = await R.run_mission(_core(orch, captured), mission_id=rec.task_id, objective="boulot")
    assert res["status"] == "done"
    assert captured["task"] == "boulot"  # PAS de prefix → il ne re-délègue pas


# ── #1 crash : un SystemExit (cancel coopératif) est absorbé en `cancelled` ───────

@pytest.mark.asyncio
async def test_run_mission_absorbs_systemexit_as_cancelled(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = _mission(orch, depth=1)

    async def boom(task, **k):
        raise SystemExit("task_orchestrator_cancel")

    core = types.SimpleNamespace(task_orchestrator=orch, think_and_act_silent=boom)
    res = await R.run_mission(core, mission_id=rec.task_id, objective="x")  # ne doit PAS lever
    assert res["status"] == "cancelled"
