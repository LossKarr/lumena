"""F1.b — la clôture d'une mission reflète les PREUVES, pas le discours.

Deux trous fermés ici, tous deux mesurés par le benchmark du 9 août 2026 :

1. **AUD-012 / AUD-008** — sur le chemin de SUCCÈS, un `answer` vide devenait
   « Je n'ai pas trouvé de réponse pertinente. ». Cette chaîne est NON VIDE : elle
   franchissait donc la porte `empty_result` du runner et la mission sortait `done`
   avec une phrase de politesse, alors que le ledger prouvait le travail (trois
   artefacts réels sur M01_DATA_DOCS ; navigateur réussi puis résultat détruit par
   les repairs sur A14_browser).

2. **AUD-014** — le truth-lock rétrogradait déjà les affirmations non prouvées dans
   le TEXTE, puis jetait l'information. L'ÉTAT restait `completed` sans réserve.

Invariant transverse : **hors mission, rien ne change** — le chat garde sa formule.
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.react import ReActLoop
from src.runtime.execution_ledger import ExecutionLedger
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as runner_mod


# ── _note_truth_lock_outcome : l'issue du verrou est mémorisée ───────────────

def _fake_react(run_meta=None):
    return types.SimpleNamespace(_run_meta=run_meta if run_meta is not None else {})


def test_overclaim_is_recorded():
    r = _fake_react()
    ReActLoop._note_truth_lock_outcome(r, {"changed": True, "overclaim": True})
    assert r._run_meta["mission_truth_lock_overclaim"] is True
    assert r._run_meta["mission_truth_lock_applied"] is True


def test_honest_note_is_not_an_overclaim():
    """Une note honnête (« tests non exécutés ») modifie le texte sans être une
    faute de clôture : `changed` sans `overclaim` ne doit pas salir l'état."""
    r = _fake_react()
    ReActLoop._note_truth_lock_outcome(r, {"changed": True, "overclaim": False})
    assert r._run_meta["mission_truth_lock_overclaim"] is False
    assert r._run_meta["mission_truth_lock_applied"] is True


def test_overclaim_flag_is_cumulative_and_never_erased():
    """Un site aval qui ne détecte rien ne doit pas effacer un overclaim vu en
    amont — le texte a pu être neutralisé entre-temps (idempotence du verrou)."""
    r = _fake_react()
    ReActLoop._note_truth_lock_outcome(r, {"changed": True, "overclaim": True})
    ReActLoop._note_truth_lock_outcome(r, {"changed": False, "overclaim": False})
    assert r._run_meta["mission_truth_lock_overclaim"] is True


def test_note_truth_lock_outcome_never_raises():
    r = _fake_react()
    ReActLoop._note_truth_lock_outcome(r, None)
    ReActLoop._note_truth_lock_outcome(r, "pas un dict")
    assert r._run_meta.get("mission_truth_lock_overclaim") is None


# ── _empty_final_fallback : un FINAL vide n'est jamais une politesse ─────────

_HISTORICAL = "Je n'ai pas trouvé de réponse pertinente."


def _react_for_fallback(*, mission: bool, ledger: ExecutionLedger):
    failed: list = []
    return types.SimpleNamespace(
        _is_mission_run=mission,
        execution_ledger=ledger,
        task_id="mission-1",
        _mark_task_failed=lambda reason: failed.append(reason),
        _current_green_test_proof=lambda: False,
        _tests_present_but_not_run=lambda: False,
        _failed_calls=failed,
    )


def _ledger_with_artifacts():
    led = ExecutionLedger()
    led.append(iteration=1, action="write_file",
               target="workspace/demo/rapport.md", success=True, proof="ok")
    return led


def test_chat_keeps_the_historical_sentence():
    """Hors mission : zéro changement de comportement."""
    r = _react_for_fallback(mission=False, ledger=ExecutionLedger())
    assert ReActLoop._empty_final_fallback(r) == _HISTORICAL


def test_mission_with_evidence_gets_a_deterministic_report():
    r = _react_for_fallback(mission=True, ledger=_ledger_with_artifacts())
    out = ReActLoop._empty_final_fallback(r)

    assert out != _HISTORICAL
    assert "rapport.md" in out
    # Le bilan est déterministe : aucun échec marqué, le travail existe.
    assert r._failed_calls == []


def test_mission_without_evidence_fails_honestly():
    """Ni livrable ni trace : ce n'est pas une livraison. La mission doit échouer
    au lieu de sortir `done` avec une phrase de politesse."""
    r = _react_for_fallback(mission=True, ledger=ExecutionLedger())
    out = ReActLoop._empty_final_fallback(r)

    assert out != _HISTORICAL
    assert "empty_final_without_evidence" in r._failed_calls
    assert "Rien n'a été livré" in out


def test_fallback_never_raises_on_broken_ledger():
    """Ce garde-fou ne doit jamais transformer une mission réussie en exception."""
    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("ledger corrompu")

    r = types.SimpleNamespace(_is_mission_run=True, execution_ledger=_Boom(),
                              task_id="m", _mark_task_failed=lambda *_: None)
    assert ReActLoop._empty_final_fallback(r) == _HISTORICAL


# ── runner : l'état terminal dit la vérité ───────────────────────────────────

@pytest.fixture
def _stub_registry(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: sentinel)
    return sentinel


def _core_with(orch, silent):
    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    return core


def _new_mission(orch, objective="do X"):
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview=objective, metadata={"kind": "mission"})
    return rec.task_id


@pytest.mark.asyncio
async def test_runner_passes_proof_out(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    captured = {}

    async def silent(objective, **kw):
        captured.update(kw)
        return "livrable"

    await runner_mod.run_mission(
        _core_with(orch, silent), mission_id=_new_mission(orch), objective="x",
    )
    assert isinstance(captured.get("proof_out"), dict)


@pytest.mark.asyncio
async def test_clean_run_stays_completed(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        return "livrable honnête"

    mid = _new_mission(orch)
    out = await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")

    assert out["status"] == "done"
    meta = orch.get_task(mid)["metadata"]
    assert meta["terminal_reason_code"] == "completed"


@pytest.mark.asyncio
async def test_overclaim_marks_closure_unproven(tmp_path, _stub_registry):
    """AUD-014 : le message était honnête (bannière), l'état ne l'était pas."""
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        kw["proof_out"]["mission_truth_lock_overclaim"] = True
        return "Publié dans workspace/demo (rétrogradé par le verrou)"

    mid = _new_mission(orch)
    out = await runner_mod.run_mission(_core_with(orch, silent), mission_id=mid, objective="x")

    # Le travail existe : la mission reste consultable en `done`…
    assert out["status"] == "done"
    meta = orch.get_task(mid)["metadata"]
    # …mais la clôture n'est plus déclarée prouvée.
    assert meta["terminal_reason_code"] == "completed_with_unproven_claims"
    assert meta["completion_proven"] is False
