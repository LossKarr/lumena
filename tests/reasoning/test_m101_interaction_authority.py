from src.reasoning.final_guards import (
    apply_mission_truth_lock,
    objective_requires_web_interaction_proof,
)
from src.reasoning.react import ReActLoop


class _Ledger:
    def __init__(self, *, browser=True, strict=False):
        self.browser = browser
        self.strict = strict

    def has_browser_action(self):
        return self.browser

    def has_successful_action(self, action):
        return action == "browser_verify_local_project" and self.strict

    def written_basenames(self):
        return {"index.html", "app.js"}


def _loop(*, strict=False, metadata=None):
    loop = ReActLoop.__new__(ReActLoop)
    loop.execution_ledger = _Ledger(strict=strict)
    loop.task_id = None
    loop.task_orchestrator = None
    loop._mission_allowed_files_meta = lambda: []
    loop._original_query = (
        "Dans le navigateur, saisis 175 litres, choisis une date, clique sur "
        "Ajouter puis verifie que le total affiche une nouvelle valeur."
    )
    loop._web_runtime_verified = None if metadata is None else metadata.get(
        "web_runtime_verified"
    )
    return loop


def test_objective_requires_observable_interaction_proof():
    assert objective_requires_web_interaction_proof(
        "Saisis 175, clique sur Ajouter et verifie que le total change dans le navigateur."
    )
    assert objective_requires_web_interaction_proof(
        "Remplis le formulaire puis confirme que le resultat est affiche."
    )
    assert not objective_requires_web_interaction_proof(
        "Ouvre la page et prends une capture d'ecran."
    )
    assert not objective_requires_web_interaction_proof(
        "Ne clique sur rien et ne soumets pas le formulaire."
    )


def test_manual_browser_action_does_not_close_explicit_interaction_gate():
    loop = _loop(strict=False)
    assert loop._mission_browser_verify_pending("Mission terminee", loop._original_query)


def test_strict_runtime_success_closes_explicit_interaction_gate():
    loop = _loop(strict=True, metadata={"web_runtime_verified": True})
    assert loop._mission_browser_verify_pending(
        "Mission terminee", loop._original_query
    ) == ""


def test_browser_open_gets_a_separate_interaction_retry_budget():
    loop = ReActLoop.__new__(ReActLoop)
    loop._interaction_gate_shots = 0
    loop._truth_lock_interaction_flag = lambda: True
    loop._truth_lock_interaction_proven = lambda: False
    loop._current_browser_proof = lambda: True
    loop._mission_browser_verify_pending = lambda note, query: "web contract"

    assert loop._finalize_interaction_gate_pending("done", "click and check") == (
        "web contract"
    )


def test_interaction_retry_is_bounded_and_requires_browser_open():
    loop = ReActLoop.__new__(ReActLoop)
    loop._truth_lock_interaction_flag = lambda: True
    loop._truth_lock_interaction_proven = lambda: False
    loop._mission_browser_verify_pending = lambda note, query: "web contract"

    loop._interaction_gate_shots = 4
    loop._current_browser_proof = lambda: True
    assert loop._finalize_interaction_gate_pending("done", "click and check") == (
        "web contract"
    )

    loop._interaction_gate_shots = 5
    assert loop._finalize_interaction_gate_pending("done", "click and check") == ""

    loop._interaction_gate_shots = 0
    loop._current_browser_proof = lambda: False
    assert loop._finalize_interaction_gate_pending("done", "click and check") == ""


def test_proven_interaction_never_relaunches():
    loop = ReActLoop.__new__(ReActLoop)
    loop._interaction_gate_shots = 0
    loop._truth_lock_interaction_flag = lambda: True
    loop._truth_lock_interaction_proven = lambda: True
    loop._current_browser_proof = lambda: True
    loop._mission_browser_verify_pending = lambda note, query: "web contract"

    assert loop._finalize_interaction_gate_pending("done", "click and check") == ""


def test_generic_interaction_truth_lock_is_not_game_wording():
    text = "Le formulaire a ete rempli et le total a bien change."
    guarded, info = apply_mission_truth_lock(
        text,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        interaction_required=True,
        interaction_proven=False,
    )
    assert guarded.startswith("⚠️ **Interaction de l'interface NON prouvée**")
    assert "dynamique de jeu" not in guarded
    assert info["interaction_unproven_note"] is True


def test_generic_interaction_truth_lock_accepts_strict_proof():
    text = "Le formulaire a ete rempli et le total a bien change."
    guarded, info = apply_mission_truth_lock(
        text,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        interaction_required=True,
        interaction_proven=True,
    )
    assert guarded == text
    assert info["changed"] is False
