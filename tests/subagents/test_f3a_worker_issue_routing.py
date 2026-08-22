"""F3.a — un blocage hors périmètre remonte au parent comme issue routable.

Cause (phase M3 du plan de clôture, jamais armée ; vrille MiniQuiz) : quand un
worker a besoin d'un fichier qui n'est pas le sien, le garde de périmètre refuse
proprement (fail-closed)… et le signal est **loggué puis perdu**. Le seul canal
vers le lead est le `result_summary` en texte libre, tronqué à la fusion. Le lead
doit deviner — et ne devine pas.

Choix de conception : l'issue est **déduite d'un fait** (le refus connaît le
fichier visé ; `contract.json` connaît son owner), jamais déclarée par le modèle.
Faire déclarer l'issue par le worker reviendrait à refaire confiance au discours,
ce que ce projet combat.
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.handlers import missions as M
from src.reasoning.handlers.files import _record_out_of_scope_attempt
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod
from src.subagents.mission_contract import owner_of_path


# ── owner_of_path : routage déterministe depuis le contrat ───────────────────

_CONTRACT = {
    "files": [
        {"path": "app.py", "owner": "w_backend"},
        {"path": "static/script.js", "owner": "w_frontend"},
        {"path": "tests/test_app.py", "owner": "w_tests"},
    ]
}


def test_exact_path_match():
    assert owner_of_path(_CONTRACT, "static/script.js") == "w_frontend"


def test_basename_match_is_tolerated():
    """Le garde de périmètre accepte les deux formes ; le routage aussi."""
    assert owner_of_path(_CONTRACT, "script.js") == "w_frontend"


def test_windows_separators_and_prefixes_are_normalised():
    assert owner_of_path(_CONTRACT, ".\\static\\script.js") == "w_frontend"


def test_unknown_file_is_not_guessed():
    assert owner_of_path(_CONTRACT, "README.md") == ""


def test_ambiguous_basename_refuses_to_guess():
    """Deux dossiers, même nom de fichier : on ne route pas au hasard."""
    ambiguous = {"files": [
        {"path": "a/util.py", "owner": "w_a"},
        {"path": "b/util.py", "owner": "w_b"},
    ]}
    assert owner_of_path(ambiguous, "util.py") == ""
    # …mais le chemin exact reste routable.
    assert owner_of_path(ambiguous, "b/util.py") == "w_b"


def test_malformed_contract_never_raises():
    assert owner_of_path(None, "app.py") == ""
    assert owner_of_path({"files": ["pas un dict"]}, "app.py") == ""
    assert owner_of_path(_CONTRACT, "") == ""


# ── _record_out_of_scope_attempt : le fait est persisté ──────────────────────

def _ctx_with_task(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="worker", metadata={"kind": "mission"})
    core = types.SimpleNamespace(task_orchestrator=orch)
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=rec.task_id)
    return ctx, orch, rec.task_id


def test_attempt_is_persisted_on_the_worker_task(tmp_path):
    ctx, orch, tid = _ctx_with_task(tmp_path)
    _record_out_of_scope_attempt(ctx, "static/script.js")
    assert orch.get_task(tid)["metadata"]["blocked_out_of_scope"] == ["static/script.js"]


def test_repeated_attempts_are_deduplicated(tmp_path):
    """Un worker retente souvent le même fichier : pas de liste qui enfle."""
    ctx, orch, tid = _ctx_with_task(tmp_path)
    for _ in range(5):
        _record_out_of_scope_attempt(ctx, "app.py")
    assert orch.get_task(tid)["metadata"]["blocked_out_of_scope"] == ["app.py"]


def test_record_is_bounded(tmp_path):
    ctx, orch, tid = _ctx_with_task(tmp_path)
    for i in range(30):
        _record_out_of_scope_attempt(ctx, f"f{i}.py")
    assert len(orch.get_task(tid)["metadata"]["blocked_out_of_scope"]) == 20


def test_record_never_breaks_the_refusal():
    """FAIL-OPEN STRICT : ce mouchard ne doit jamais empêcher un refus de refuser."""
    broken = types.SimpleNamespace(lumena=None, runtime_task_id="x")
    _record_out_of_scope_attempt(broken, "app.py")  # ne lève pas

    class _Explodes:
        @property
        def task_orchestrator(self):
            raise RuntimeError("orchestrateur cassé")

    _record_out_of_scope_attempt(
        types.SimpleNamespace(lumena=_Explodes(), runtime_task_id="x"), "app.py",
    )


def test_no_task_id_is_a_noop(tmp_path):
    """Hors mission (chat, CodeAgent) : aucun effet."""
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    _record_out_of_scope_attempt(
        types.SimpleNamespace(lumena=core, runtime_task_id=None), "app.py",
    )  # ne lève pas, n'écrit rien


# ── delegate_and_wait : l'issue remonte EN ÉVIDENCE ──────────────────────────

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


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id), orch


def _make_lead(orch, depth=1):
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": depth})


@pytest.mark.asyncio
async def test_blocked_file_is_surfaced_to_the_lead(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        o = core_arg.task_orchestrator
        o.mark_running(mission_id)
        # Exactement ce que fait le garde de périmètre pendant le run.
        o.set_task_metadata(mission_id, blocked_out_of_scope=["static/script.js"])
        o.mark_done(mission_id, result_summary="tests écrits")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    res = await M.delegate_and_wait_handler(ctx, ["écris les tests"], timeout=5.0)

    assert res.success
    assert "BLOCAGES INTER-WORKERS" in res.output
    assert "static/script.js" in res.output
    # Sans contrat chargé, on l'annonce honnêtement au lieu d'inventer un owner.
    assert "owner inconnu au contrat" in res.output
    # Et on dit quoi en faire.
    assert "AVANT de conclure" in res.output


@pytest.mark.asyncio
async def test_no_blocking_issue_means_no_extra_section(tmp_path, monkeypatch):
    """Un run sans blocage garde exactement son rendu historique."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = _make_lead(orch)
    ctx.runtime_task_id = lead.task_id

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="fait")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(ctx.lumena)

    res = await M.delegate_and_wait_handler(ctx, ["tâche simple"], timeout=5.0)
    assert res.success
    assert "BLOCAGES INTER-WORKERS" not in res.output
