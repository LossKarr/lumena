"""B0 (run PlantCare 2026-07-03) — les 6 murs de plomberie, fix par fix.

1. runner : timeout aligné sur la deadline de la mission (le lead mourait à 600 s
   avec 20 min de budget) ;
2. run_command : en mission, le shell démarre DANS le dossier mission (cohérence
   avec le préambule A1) et les cwd relatifs se résolvent mission-first ;
3. compaction : read_files_batch/parallel_tools au seuil élevé (830 chars tuaient
   w_tests en boucles de relecture) ;
4a. sanitizer : les backslashes d'un chemin quoté ne sont plus mangés par shlex ;
4b. bannière déterministe « Tests NON certifiés verts » quand un pytest a tourné
   sans vert (le « Tests passés au vert » de w_schedule après exit 4) ;
4c/4d. plancher timeout delegate en mission code + steer sans dossier cible.
"""
from __future__ import annotations

import inspect
import types
from datetime import datetime, timedelta, timezone

import pytest

from src.reasoning.final_guards import apply_mission_truth_lock
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.system import _resolve_cwd, run_command_handler
from src.tools.file_guardrails import WorkspaceFileGuardrails
from src.utils.command_sanitizer import sanitize_command


# ── B0.1 : le runner respecte la deadline ────────────────────────────────────────

@pytest.mark.asyncio
async def test_runner_timeout_follows_mission_deadline(tmp_path, monkeypatch):
    from src.runtime.task_orchestrator import TaskOrchestrator
    from src.subagents import runner as runner_mod

    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    task = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead",
                           metadata={"kind": "mission", "depth": 1, "deadline_ts": deadline})

    captured = {}

    async def fake_silent(prompt, *, timeout, **kwargs):
        captured["timeout"] = timeout
        return "fini"

    core = types.SimpleNamespace(task_orchestrator=orch, think_and_act_silent=fake_silent)
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda c: None)

    await runner_mod.run_mission(core, mission_id=task.task_id, objective="obj")
    # ~20 min restantes + marge 120 s → bien au-delà des 600 s par défaut
    assert captured["timeout"] > 600.0
    assert captured["timeout"] <= 3600.0


@pytest.mark.asyncio
async def test_runner_timeout_default_without_deadline(tmp_path, monkeypatch):
    """B0.1 n'ajoute AUCUN uplift sans échéance. Testé sur un SOUS-WORKER (depth 2) :
    le plancher top-lead H.2 ne s'y applique pas → on isole bien l'absence d'uplift
    B0.1 (un top-lead sans échéance, lui, est désormais planché à 1800 s — cf.
    tests/subagents/test_h_lead_budget.py)."""
    from src.runtime.task_orchestrator import TaskOrchestrator
    from src.subagents import runner as runner_mod

    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    task = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="worker", metadata={"kind": "mission", "depth": 2})

    captured = {}

    async def fake_silent(prompt, *, timeout, **kwargs):
        captured["timeout"] = timeout
        return "fini"

    core = types.SimpleNamespace(task_orchestrator=orch, think_and_act_silent=fake_silent)
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda c: None)

    await runner_mod.run_mission(core, mission_id=task.task_id, objective="obj")
    assert captured["timeout"] == 600.0  # sous-worker sans échéance : strictement inchangé


# ── B0.2 : run_command démarre dans le dossier mission ──────────────────────────

def _mission_ctx(tmp_path):
    d = tmp_path / "missions" / "task_b02"
    (d / "tests").mkdir(parents=True, exist_ok=True)
    return HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        is_mission_run=True, runtime_task_id="task_b02",
        mission_workspace="missions/task_b02",
    )


@pytest.mark.asyncio
async def test_run_command_defaults_to_mission_dir(tmp_path):
    ctx = _mission_ctx(tmp_path)
    r = await run_command_handler(ctx, command="cd")  # cmd.exe : affiche le cwd
    assert r.success
    assert "task_b02" in r.output


@pytest.mark.asyncio
async def test_run_command_dot_cwd_means_mission_dir(tmp_path):
    ctx = _mission_ctx(tmp_path)
    r = await run_command_handler(ctx, command="cd", cwd=".")
    assert r.success
    assert "task_b02" in r.output


@pytest.mark.asyncio
async def test_run_command_out_of_mission_unchanged(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    r = await run_command_handler(ctx, command="cd")
    assert r.success
    assert "task_b02" not in r.output


def test_resolve_cwd_mission_first(tmp_path):
    """`cwd='tests'` en mission → tests/ de la MISSION, pas celui de Lumena."""
    (tmp_path / "tests").mkdir()  # homonyme côté racine
    mission_tests = tmp_path / "missions" / "task_b02" / "tests"
    mission_tests.mkdir(parents=True)
    resolved = _resolve_cwd("tests", tmp_path, tmp_path / "missions" / "task_b02")
    assert resolved == str(mission_tests.resolve())
    # hors mission : comportement 2.11 inchangé
    resolved2 = _resolve_cwd("tests", tmp_path, None)
    assert resolved2 == str((tmp_path / "tests").resolve())


# ── B0.3 : compaction — les lecteurs batch au seuil élevé ────────────────────────

def test_compaction_high_threshold_includes_batch_readers():
    # Lot RF-9a : le seuil vit dans `observation_synthesis.py` (feuille
    # « ingestion d'observation », §15) et n'est plus une variable mais un
    # retour. L'assertion devient COMPORTEMENTALE — l'invariante est
    # inchangée : ces lecteurs mènent bien au seuil 8000.
    from src.reasoning.observation_synthesis import (
        observation_compact_limit, _OBS_FILE_READ_TOOLS,
    )

    assert observation_compact_limit(
        "read_files_batch", is_chat_surface=False
    ) == 8000, "read_files_batch compacté à 830 chars = mort de w_tests"
    assert observation_compact_limit(
        "parallel_tools", is_chat_surface=False
    ) == 8000
    assert "read_files_batch" in _OBS_FILE_READ_TOOLS
    assert "parallel_tools" in _OBS_FILE_READ_TOOLS


# ── B0.4a : sanitizer — chemins Windows quotés ───────────────────────────────────

def test_sanitizer_quoted_windows_python_allowed():
    ok, reason = sanitize_command(
        '"C:\\Users\\charl\\Desktop\\lumena\\venv\\Scripts\\python.exe" -m pytest tests/ -v')
    assert ok, reason


def test_sanitizer_still_blocks_unknown_exe():
    ok, reason = sanitize_command('"C:\\evil\\malware.exe" --run')
    assert not ok


# ── B0.4b : bannière déterministe « pytest NON vert » ───────────────────────────

def test_w_schedule_lie_gets_honest_status():
    """Le cas PlantCare figé : « Tests passés au vert » après un exit 4 (0 collecté)."""
    out, info = apply_mission_truth_lock(
        "✅ Mission accomplie - schedule.py prêt et validé !\n"
        "🧪 Tests passés au vert — pytest valide tous les cas.",
        has_green_test=False,
        last_test_outcome={"is_test_cmd": True, "passed": 0, "failed": 0, "errors": 0})
    assert info["changed"]
    assert "Tests NON certifiés verts" in out


def test_green_run_untouched():
    out, info = apply_mission_truth_lock(
        "Tests verts : 12 passed.", has_green_test=True,
        last_test_outcome={"is_test_cmd": True, "passed": 12, "failed": 0, "errors": 0})
    assert not info["changed"]


def test_not_green_banner_idempotent():
    out1, _ = apply_mission_truth_lock(
        "Travail terminé.", has_green_test=False,
        last_test_outcome={"is_test_cmd": True, "passed": 0, "failed": 3, "errors": 0})
    out2, info2 = apply_mission_truth_lock(
        out1, has_green_test=False,
        last_test_outcome={"is_test_cmd": True, "passed": 0, "failed": 3, "errors": 0})
    assert out2 == out1
    assert info2.get("already_locked")


def test_no_test_ran_no_not_green_banner():
    """Aucun test lancé → pas de bannière « NON certifiés » (c'est le domaine
    de « présents mais NON exécutés », disjoint)."""
    out, info = apply_mission_truth_lock(
        "Analyse rendue.", has_green_test=False, last_test_outcome=None)
    assert not info["changed"]


# ── B0.4c / B0.4d : structurels ──────────────────────────────────────────────────

def test_delegate_timeout_floor_under_contract():
    import src.reasoning.handlers.missions as missions_mod
    src = inspect.getsource(missions_mod)
    i = src.find("timeout < 600.0")
    assert i > 0
    assert "_contract_preamble" in src[max(0, i - 300):i + 100]


def test_mission_intent_steer_forbids_target_dir():
    import src.reasoning.react as react_mod
    src = inspect.getsource(react_mod)
    assert "N'impose PAS de dossier cible" in src
