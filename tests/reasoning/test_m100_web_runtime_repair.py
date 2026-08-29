from pathlib import Path

from src.reasoning.final_guards import apply_mission_truth_lock
from src.reasoning.react import ReActLoop, _web_runtime_repair_allowed


class _Orchestrator:
    def __init__(self, metadata=None):
        self.record = {"metadata": dict(metadata or {})}

    def get_task(self, _task_id):
        return self.record

    def set_task_metadata(self, _task_id, **values):
        self.record["metadata"].update(values)


def _loop(metadata=None):
    loop = ReActLoop.__new__(ReActLoop)
    loop.task_id = "task_web"
    loop.task_orchestrator = _Orchestrator(metadata)
    loop._mission_allowed_files_meta = lambda: []
    return loop


def test_runtime_repair_is_bounded_and_reserves_iterations():
    assert _web_runtime_repair_allowed(
        failed=True, shots=0, iteration=3, max_iterations=20
    )
    assert _web_runtime_repair_allowed(
        failed=True, shots=1, iteration=15, max_iterations=20
    )
    assert not _web_runtime_repair_allowed(
        failed=True, shots=2, iteration=3, max_iterations=20
    )
    assert not _web_runtime_repair_allowed(
        failed=True, shots=0, iteration=17, max_iterations=20
    )
    assert not _web_runtime_repair_allowed(
        failed=False, shots=0, iteration=3, max_iterations=20
    )


def test_runtime_failure_is_persisted_for_recovery():
    loop = _loop()
    loop._set_web_runtime_verification_state(
        failed=True,
        report="http_405: POST http://localhost:8081/api/empreinte",
    )

    meta = loop.task_orchestrator.record["metadata"]
    assert meta["web_runtime_failed"] is True
    assert meta["web_runtime_verified"] is False
    assert "http_405" in meta["web_runtime_failure_report"]

    resumed = _loop(meta)
    assert resumed._browser_runtime_failed_for_truth_lock() is True


def test_runtime_success_clears_previous_failure():
    loop = _loop({"web_runtime_failed": True, "web_runtime_failure_report": "old"})
    loop._set_web_runtime_verification_state(failed=False)

    assert loop._browser_runtime_failed_for_truth_lock() is False
    assert loop.task_orchestrator.record["metadata"]["web_runtime_failed"] is False
    assert loop.task_orchestrator.record["metadata"]["web_runtime_verified"] is True
    assert loop.task_orchestrator.record["metadata"]["web_runtime_failure_report"] == ""


def test_worker_scope_does_not_inherit_lead_runtime_failure():
    loop = _loop({"web_runtime_failed": True})
    loop._mission_allowed_files_meta = lambda: ["app.js"]
    assert loop._browser_runtime_failed_for_truth_lock() is False


def test_truth_lock_reports_latest_runtime_failure_even_with_old_browser_proof():
    text = "Mission terminee. L'application est disponible."
    guarded, info = apply_mission_truth_lock(
        text,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        browser_runtime_failed=True,
    )

    assert guarded.startswith("> ⚠️ **Intégration web en échec**")
    assert text in guarded
    assert info["browser_runtime_failed_note"] is True
    assert "Navigateur NON vérifié" not in guarded


def test_truth_lock_runtime_failure_is_idempotent():
    once, _ = apply_mission_truth_lock(
        "Livrable disponible.",
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        browser_runtime_failed=True,
    )
    twice, info = apply_mission_truth_lock(
        once,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        browser_runtime_failed=True,
    )

    assert twice == once
    assert info.get("already_locked") is True


def test_all_mission_truth_lock_sites_receive_runtime_verdict():
    # Lot RF-8 : les arguments du verrou vivent desormais dans
    # `final_delivery_runtime.py` (methode `_truth_lock_mission_message`
    # extraite). Le test lit les DEUX fichiers : son intention — « un site
    # oublie = un chemin de sortie qui ment par omission » — est inchangee.
    _base = Path(__file__).resolve().parents[2] / "src" / "reasoning"
    source = ((_base / "react.py").read_text(encoding="utf-8")
              + (_base / "final_delivery_runtime.py").read_text(encoding="utf-8"))
    # Lot RF-8 : le site extrait dit `etat.X()` la ou react disait
    # `self._X()`. Les NOMS suivent le rebindage, l'intention est
    # inchangee : les trois sites recoivent bien le verdict.
    assert (source.count(
        "browser_runtime_failed=self._browser_runtime_failed_for_truth_lock()")
        + source.count("browser_runtime_failed=etat.navigateur_en_panne()")) == 3
