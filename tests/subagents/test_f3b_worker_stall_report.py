"""F3.b (M9) — un worker non terminal n'est plus un bloc indistinct « en cours ».

Cause (phase M9 du plan de clôture, jamais armée) : à l'expiration de l'attente du
lead, tous les enfants restants étaient rendus d'un même « les workers continuent ».
L'UI n'affichait qu'« en cours », y compris quand le vrai état était « jamais
démarré, aucun créneau » ou « plus aucune progression depuis vingt minutes ».

Règle que ce lot s'impose : **ne jamais mentir sur l'état**. Un worker qui progresse
encore n'est pas déclaré mort, et AUCUN nouvel état de tâche n'est introduit — la
machine à états reste intacte, on joint un CONSTAT fondé sur `state` + `updated_at`.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest

from src.reasoning.handlers import missions as M
from src.reasoning.handlers.missions import classify_pending_workers
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod


_NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


def _rec(task_id, state, *, idle_s=0):
    return {
        "task_id": task_id,
        "state": state,
        "updated_at": (_NOW - timedelta(seconds=idle_s)).isoformat(),
    }


def _now_iso():
    return _NOW.isoformat()


# ── classify_pending_workers : le constat est un fait, pas une opinion ───────

def test_terminal_workers_are_ignored():
    records = [_rec("a", "done"), _rec("b", "failed"), _rec("c", "cancelled")]
    assert classify_pending_workers(records, _now_iso(), 300.0) == []


def test_queued_worker_never_started():
    out = classify_pending_workers([_rec("a", "queued")], _now_iso(), 300.0)
    assert out[0]["kind"] == "queued"


def test_recently_updated_worker_is_still_working():
    """Il progresse : on ne l'accuse pas."""
    out = classify_pending_workers([_rec("a", "running", idle_s=10)], _now_iso(), 300.0)
    assert out[0]["kind"] == "working"


def test_idle_worker_is_reported_as_stalled():
    out = classify_pending_workers([_rec("a", "running", idle_s=1200)], _now_iso(), 300.0)
    assert out[0]["kind"] == "stalled"
    assert out[0]["idle_s"] == 1200


def test_threshold_is_respected_at_the_boundary():
    just_under = classify_pending_workers([_rec("a", "running", idle_s=299)], _now_iso(), 300.0)
    just_over = classify_pending_workers([_rec("a", "running", idle_s=300)], _now_iso(), 300.0)
    assert just_under[0]["kind"] == "working"
    assert just_over[0]["kind"] == "stalled"


def test_checkpointed_worker_can_be_stalled_too():
    out = classify_pending_workers([_rec("a", "checkpointed", idle_s=999)], _now_iso(), 300.0)
    assert out[0]["kind"] == "stalled"


def test_unreadable_timestamp_falls_back_to_working():
    """On préfère taire un doute qu'accuser à tort."""
    out = classify_pending_workers(
        [{"task_id": "a", "state": "running", "updated_at": "pas une date"}],
        _now_iso(), 300.0,
    )
    assert out[0]["kind"] == "working"
    assert out[0]["idle_s"] is None


def test_garbage_input_never_raises():
    assert classify_pending_workers(None, _now_iso(), 300.0) == []
    assert classify_pending_workers(["pas un dict"], _now_iso(), 300.0) == []
    assert classify_pending_workers([_rec("a", "running")], "pas une date", 300.0) == []


def test_threshold_env_has_a_floor(monkeypatch):
    """Un seuil trop bas transformerait chaque worker actif en « bloqué »."""
    monkeypatch.setenv("LUMENA_WORKER_STALL_S", "1")
    assert M._stall_threshold_s() == 30.0
    monkeypatch.setenv("LUMENA_WORKER_STALL_S", "n'importe quoi")
    assert M._stall_threshold_s() == 300.0


# ── bout-en-bout : pas d'alarme quand les workers avancent ───────────────────

@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
    monkeypatch.delenv("LUMENA_WORKER_STALL_S", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


@pytest.mark.asyncio
async def test_timeout_with_progressing_worker_raises_no_false_alarm(tmp_path, monkeypatch):
    """Le lead expire son attente, mais le worker vient de progresser : la section
    « état réel » ne doit PAS l'accuser d'être bloqué."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": 1})
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=lead.task_id)

    async def never_finishes(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        import asyncio as _a
        await _a.sleep(30)  # dépasse largement le timeout d'attente du lead

    monkeypatch.setattr(worker_mod, "run_mission", never_finishes)
    worker_mod.start_mission_worker(core)

    res = await M.delegate_and_wait_handler(ctx, ["tâche longue"], timeout=1.0)

    assert res.success
    assert "RÉSULTAT PARTIEL" in res.output          # steering historique préservé
    assert "AUCUNE progression" not in res.output    # pas de fausse accusation
