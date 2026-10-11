from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.training.personal.contracts import LearningExperienceV1, new_id, utc_now
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.learning_cycle import PersonalLearningCycle
from src.training.personal.policy import PersonalLearningPolicy, PersonalLearningPolicyStore


def test_learning_cycle_curates_and_judges_without_chat_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "judge-model")
    policy = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True, judge_mode="default")
    PersonalLearningPolicyStore(tmp_path / "config" / "policy.json").save(policy)
    exp = LearningExperienceV1(
        experience_id=new_id("exp"), owner_scope="owner:local", source_surface="web", mode="chat",
        created_at=utc_now(), goal="expliquer", public_context={},
        messages=({"role": "user", "content": "explique"}, {"role": "assistant", "content": "une réponse suffisamment longue et claire"}),
        result={"success": True}, privacy_state="redacted", license_policy="user_owned",
    )
    ExperienceStore(tmp_path).append(exp)
    calls = []
    report = PersonalLearningCycle(tmp_path, judge_call=lambda model, request: calls.append((model, request)) or {"score": 8, "reason_codes": ["clear"]}).run()
    assert report.curated == 1 and report.accepted == 1
    assert calls[0][0] == "judge-model"
    assert "history" not in calls[0][1] and "memory" not in calls[0][1]


def test_learning_cycle_is_inert_without_consent(tmp_path):
    assert PersonalLearningCycle(tmp_path, judge_call=lambda *_: {"score": 10}).run().curated == 0


def test_un_verdict_invalide_ne_bloque_pas_les_candidats_suivants(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "judge-model")
    policy = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True, judge_mode="default")
    PersonalLearningPolicyStore(tmp_path / "config" / "policy.json").save(policy)
    store = ExperienceStore(tmp_path)
    contenus = {
        "a": ("écrire une fonction python de tri", "voici une fonction Python complète avec tests unitaires"),
        "b": ("préparer un budget de voyage", "voici un budget détaillé avec transport hôtel et repas"),
    }
    for suffixe, (question, reponse) in contenus.items():
        store.append(LearningExperienceV1(
            experience_id=f"exp-{suffixe}", owner_scope="owner:local", source_surface="web", mode="chat",
            created_at=utc_now(), goal=f"expliquer {suffixe}", public_context={},
            messages=(
                {"role": "user", "content": question},
                {"role": "assistant", "content": reponse},
            ),
            result={"success": True}, privacy_state="redacted", license_policy="user_owned",
        ))

    appels = []

    def juge(_model, _request):
        appels.append(1)
        if len(appels) == 1:
            raise ValueError("judge_response_invalid_json")
        return {"score": 8, "reason_codes": ["clear"]}

    report = PersonalLearningCycle(tmp_path, judge_call=juge).run()
    index = store._load_index("owner:local")

    assert report.candidates == 2
    assert report.accepted == 1
    assert report.quarantined == 1
    assert report.failed == 1
    assert report.failure_codes == {"judge_response_invalid_json": 1}
    assert report.completed_judgments == 1
    assert report.deferred == 0
    assert index["records"]["exp-a"]["state"] == "quarantined"
    assert index["records"]["exp-b"]["state"] == "accepted"
    annotations = (tmp_path / "index" / "annotations.jsonl").read_text(encoding="utf-8")
    assert '"kind":"judge_error"' in annotations
    assert '"error_code":"judge_response_invalid_json"' in annotations


@pytest.mark.asyncio
async def test_le_handler_conserve_un_code_d_erreur_exploitable(monkeypatch):
    import src.autonomy.ops_handlers as ops

    def echoue(_self):
        raise ValueError("judge_response_invalid_json")

    monkeypatch.setattr(PersonalLearningCycle, "run", echoue)
    metrique = AsyncMock()
    monkeypatch.setattr(ops, "_aappend_metric", metrique)

    result = await ops.handler_personal_learning_cycle()

    assert result == {"status": "error", "error_code": "judge_response_invalid_json"}
    metrique.assert_awaited_once()


def test_deux_echecs_identiques_ouvrent_le_coupe_circuit_du_lot(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "judge-model")
    policy = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True, judge_mode="default")
    PersonalLearningPolicyStore(tmp_path / "config" / "policy.json").save(policy)
    store = ExperienceStore(tmp_path)
    contenus = [
        ("ecrire une fonction Python de tri", "implementation Python avec tests unitaires complets"),
        ("preparer un budget de voyage", "budget transport hotel repas et marge detaillee"),
        ("diagnostiquer un moteur automobile", "controle batterie allumage injection et compression"),
        ("planifier un potager annuel", "calendrier semis rotations arrosage et recoltes"),
        ("securiser un serveur Linux", "pare feu comptes mises a jour sauvegardes et audit"),
    ]
    for index, (question, reponse) in enumerate(contenus):
        store.append(LearningExperienceV1(
            experience_id=f"exp-{index}", owner_scope="owner:local", source_surface="web", mode="chat",
            created_at=utc_now(), goal=f"evaluer {index}", public_context={},
            messages=(
                {"role": "user", "content": question},
                {"role": "assistant", "content": reponse},
            ),
            result={"success": True}, privacy_state="redacted", license_policy="user_owned",
        ))

    calls = []

    def juge(_model, _request):
        calls.append(1)
        raise ValueError("judge_response_invalid_json")

    report = PersonalLearningCycle(tmp_path, judge_call=juge).run()
    states = store._load_index("owner:local")["records"]

    assert len(calls) == 2
    assert report.failed == 2
    assert report.completed_judgments == 0
    assert report.deferred == 3
    assert states["exp-0"]["state"] == "quarantined"
    assert states["exp-1"]["state"] == "quarantined"
    assert [states[f"exp-{index}"]["state"] for index in range(2, 5)] == ["candidate"] * 3


@pytest.mark.asyncio
async def test_le_handler_ne_declare_pas_un_lot_entierement_echoue_comme_reussi(monkeypatch):
    import src.autonomy.ops_handlers as ops
    from src.training.personal.learning_cycle import LearningCycleReport

    report = LearningCycleReport(
        curated=5,
        candidates=5,
        accepted=0,
        rejected=0,
        quarantined=2,
        skipped=0,
        failed=2,
        failure_codes={"judge_response_invalid_json": 2},
        completed_judgments=0,
        deferred=3,
    )
    monkeypatch.setattr(PersonalLearningCycle, "run", lambda _self: report)
    metrique = AsyncMock()
    monkeypatch.setattr(ops, "_aappend_metric", metrique)

    result = await ops.handler_personal_learning_cycle()

    assert result["status"] == "degraded"
    assert result["success"] is False
    assert result["reason"] == "judge_response_invalid_json"
    assert result["deferred"] == 3
