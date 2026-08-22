"""A1 (Phase A, run FitLog) — chemins RELATIFS pour les workers de mission.

Le run FitLog a montré : les workers recopient le chemin long missions/<task_id>/
(32 hex) → identifiant halluciné (afee→af1e) → « non trouvé » → désaxage complet.
Verrous :
  1. règle chemins relatifs injectée DÉTERMINISTIQUEMENT par delegate_and_wait
     dans chaque objectif enfant (survit à la paraphrase du lead), idempotente ;
  2. préambule contrat durci (worker_objectives) ;
  3. alias court `mission/` accepté par le strip défensif 2.8 ;
  4. hint de réparation sur « non trouvé » en mission quand le chemin commence
     par missions/… (cas id halluciné).
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.reasoning.handlers import missions as M
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod
from src.subagents.mission_contract import WORKER_CONTRACT_PREAMBLE, worker_objectives
from src.tools.file_guardrails import WorkspaceFileGuardrails, strip_mission_workspace_prefix


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
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


# ── 1. injection déterministe dans delegate_and_wait ────────────────────────────

@pytest.mark.asyncio
async def test_paths_rule_injected_into_every_child(tmp_path, monkeypatch):
    """Même si le lead paraphrase (objectifs SANS la règle), chaque enfant la reçoit."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id

    seen: dict = {}

    async def fake_run(core_arg, *, mission_id, objective, **k):
        seen[mission_id] = objective
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    res = await M.delegate_and_wait_handler(
        ctx, ["Implémente storage.py", "Implémente stats.py"], timeout=5.0)
    assert res.success
    assert len(seen) == 2
    for objective in seen.values():
        assert "chemins RELATIFS" in objective
        assert "📍 Chemins" in objective


@pytest.mark.asyncio
async def test_paths_rule_idempotent(tmp_path, monkeypatch):
    """Objectif portant déjà la règle (généré par write_mission_contract) → pas de doublon."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch, depth=1)
    ctx.runtime_task_id = lead.task_id

    seen: dict = {}

    async def fake_run(core_arg, *, mission_id, objective, **k):
        seen[mission_id] = objective
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    objective_with_rule = "Implémente storage.py\n\n" + M._MISSION_PATHS_RULE
    res = await M.delegate_and_wait_handler(ctx, [objective_with_rule], timeout=5.0)
    assert res.success
    objective = next(iter(seen.values()))
    assert objective.count("chemins RELATIFS") == 1


# ── 2. préambule contrat + worker_objectives ─────────────────────────────────────

def test_preamble_forbids_long_paths():
    assert "RELATIFS" in WORKER_CONTRACT_PREAMBLE
    assert "missions/<id>" in WORKER_CONTRACT_PREAMBLE


def test_worker_objectives_never_contain_mission_path():
    contract = {"project": "demo", "files": [
        {"path": "storage.py", "owner": "w_storage", "api": ["def add() -> int"]},
        {"path": "tests/test_storage.py", "owner": "w_tests"},
    ]}
    for obj in worker_objectives(contract):
        assert "missions/task" not in obj["objective"]
        assert "RELATIFS" in obj["objective"]  # via le préambule


# ── 3. alias court `mission/` ────────────────────────────────────────────────────

def test_strip_alias_mission_singular():
    sub = "missions/task_abc123"
    assert strip_mission_workspace_prefix("mission/storage.py", sub) == "storage.py"
    assert strip_mission_workspace_prefix("workspace/mission/storage.py", sub) == "storage.py"
    # combiné avec le chemin long déjà géré par 2.8
    assert strip_mission_workspace_prefix(f"{sub}/mission/storage.py", sub) == "storage.py"


def test_strip_alias_inactive_hors_mission():
    # hors mission (sub vide) : aucun strip — un dossier réel nommé mission/ reste adressable
    assert strip_mission_workspace_prefix("mission/notes.txt", "") == "mission/notes.txt"


def test_alias_resolves_into_mission_dir(tmp_path):
    from src.reasoning.handlers.context import HandlerContext

    mission_dir = tmp_path / "missions" / "task_a1"
    mission_dir.mkdir(parents=True)
    (mission_dir / "notes.txt").write_text("contenu A1", encoding="utf-8")
    ctx = HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True,
        runtime_task_id="task_a1",
        mission_workspace="missions/task_a1",
    )
    resolved = ctx.resolve_path("mission/notes.txt")
    assert resolved == (mission_dir / "notes.txt").resolve()


# ── 4. hint de réparation sur « non trouvé » ─────────────────────────────────────

def _mission_ctx(tmp_path):
    from src.reasoning.handlers.context import HandlerContext

    (tmp_path / "missions" / "task_a1").mkdir(parents=True)
    return HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True,
        runtime_task_id="task_a1",
        mission_workspace="missions/task_a1",
    )


@pytest.mark.asyncio
async def test_read_not_found_hints_relative_in_mission(tmp_path):
    from src.reasoning.handlers.files import read_file_handler

    ctx = _mission_ctx(tmp_path)
    # le cas FitLog : identifiant recopié avec une faute → non trouvé
    r = await read_file_handler(ctx, path="missions/task_WRONGID/CONTRAT.md")
    assert "Tu es en MISSION" in r.output
    assert "chemin RELATIF" in r.output


@pytest.mark.asyncio
async def test_list_not_found_hints_relative_in_mission(tmp_path):
    from src.reasoning.handlers.files import list_directory_handler

    ctx = _mission_ctx(tmp_path)
    r = await list_directory_handler(ctx, path="missions/task_WRONGID")
    assert "Tu es en MISSION" in r.output


@pytest.mark.asyncio
async def test_no_hint_out_of_mission(tmp_path):
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.files import read_file_handler

    ctx = HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
    )
    r = await read_file_handler(ctx, path="missions/task_x/CONTRAT.md")
    assert "Tu es en MISSION" not in r.output


@pytest.mark.asyncio
async def test_no_hint_for_plain_relative_path(tmp_path):
    from src.reasoning.handlers.files import read_file_handler

    ctx = _mission_ctx(tmp_path)
    r = await read_file_handler(ctx, path="inexistant.txt")
    assert "Tu es en MISSION" not in r.output
