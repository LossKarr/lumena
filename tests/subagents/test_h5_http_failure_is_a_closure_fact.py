"""H5 — un échec HTTP same-origin vu au navigateur devient un fait de clôture.

Trouvé par le TEST RÉEL du 2026-08-13 (mission `task_4a62de84…`), pas par un test
unitaire. La mission a été clôturée `completed` avec sa page d'accueil en **404** :

    ✅ Navigué vers: 404 Not Found (http://localhost:8085/)
    ⚠️ RESSOURCES EN ÉCHEC sur cette page : / (404)

Le lead avait servi la preview avec `serve_website` et vérifié avec
`browser_navigate` — **jamais** `browser_verify_local_project`, seul pourvoyeur de
`web_runtime_failed`. La porte web (F2) est donc restée inerte : elle dépendait du
bon vouloir du lead quant à l'outil choisi.

Et le plus parlant : `browser_navigate` **avait vu le défaut et l'avait affiché**.
Le fait était produit, montré, puis jeté — le motif de tous les lots précédents.

Désormais le fait est persisté sur la mission, quel que soit l'outil qui l'observe.
"""
from __future__ import annotations

import types

from src.reasoning.handlers.browser import (
    _record_mission_http_failures,
    critical_page_failures,
)
from src.subagents.runner import closure_decision


# ── Le filtre : ce qui condamne vraiment une page ────────────────────────────

def test_the_exact_failure_of_the_run():
    assert critical_page_failures(["/ (404)"]) == ["/ (404)"]


def test_favicon_is_cosmetic_and_ignored():
    """Son absence ne casse aucune page — même exclusion que le vérificateur runtime."""
    assert critical_page_failures(["/favicon.ico (404)"]) == []
    assert critical_page_failures(["/static/favicon.ico (404)"]) == []


def test_favicon_does_not_hide_a_real_failure():
    out = critical_page_failures(["/favicon.ico (404)", "/api/data (500)"])
    assert out == ["/api/data (500)"]


def test_empty_and_garbage_never_raise():
    assert critical_page_failures(None) == []
    assert critical_page_failures([]) == []
    assert critical_page_failures(["", "   ", None]) == []


# ── La persistance sur la mission ────────────────────────────────────────────

class _Orch:
    def __init__(self, meta=None):
        self.meta = dict(meta or {})
        self.writes = []

    def get_task(self, _id):
        return {"metadata": self.meta}

    def set_task_metadata(self, _id, **kv):
        self.meta.update(kv)
        self.writes.append(kv)


def _ctx(orch, *, mission=True, task_id="task_x"):
    return types.SimpleNamespace(
        is_mission_run=mission,
        runtime_task_id=task_id,
        lumena=types.SimpleNamespace(task_orchestrator=orch),
    )


def test_failure_is_persisted_on_the_mission():
    orch = _Orch()
    _record_mission_http_failures(_ctx(orch), ["/ (404)"])
    assert orch.meta["web_http_failures"] == ["/ (404)"]


def test_failures_accumulate_without_duplicates():
    orch = _Orch()
    _record_mission_http_failures(_ctx(orch), ["/ (404)"])
    _record_mission_http_failures(_ctx(orch), ["/ (404)", "/api (500)"])
    assert orch.meta["web_http_failures"] == ["/ (404)", "/api (500)"]


def test_nothing_is_written_when_the_page_is_healthy():
    orch = _Orch()
    _record_mission_http_failures(_ctx(orch), [])
    assert orch.writes == []


def test_outside_a_mission_nothing_is_recorded():
    orch = _Orch()
    _record_mission_http_failures(_ctx(orch, mission=False), ["/ (404)"])
    assert orch.writes == []


def test_recording_never_breaks_navigation():
    class _Boom:
        @property
        def is_mission_run(self):
            raise RuntimeError("contexte cassé")

    _record_mission_http_failures(_Boom(), ["/ (404)"])  # ne lève pas


# ── La clôture en tient compte ───────────────────────────────────────────────

def test_http_failure_alone_blocks_a_clean_closure():
    """Le cas du run : aucun `web_runtime_failed`, mais la racine en 404."""
    code, detail = closure_decision(
        overclaim=False, web_failed=False, web_http_failed=True
    )
    assert code == "completed_web_unverified"
    assert "verification runtime a echoue" in detail


def test_official_verifier_still_works_alone():
    code, _ = closure_decision(overclaim=False, web_failed=True, web_http_failed=False)
    assert code == "completed_web_unverified"


def test_a_healthy_web_run_stays_completed():
    """Le risque du lot est de sur-bloquer : une page saine reste `completed`."""
    code, _ = closure_decision(
        overclaim=False, web_failed=False, web_http_failed=False
    )
    assert code == "completed"


def test_http_failure_and_overclaim_report_both():
    code, detail = closure_decision(
        overclaim=True, web_failed=False, web_http_failed=True
    )
    assert code == "completed_web_unverified"
    assert "retrogradee" in detail


def test_signature_stays_backward_compatible():
    """Les appelants existants ne passent pas `web_http_failed`."""
    assert closure_decision(overclaim=False, web_failed=False)[0] == "completed"
