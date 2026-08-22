"""I3 — un final tronqué n'efface pas un livrable réellement produit.

Run « comparatif vectoriel » (2026-08-13). `w_qdrant` a été marqué **`failed`**
sur `final_answer_potentially_incomplete (finish_reason=stop)` — sa phrase de
conclusion était coupée — alors que `rapport_qdrant.md` était écrit, complet et
exact : ses cinq données se retrouvent intégralement dans le comparatif final
(licence Apache 2.0, Rust, standalone/Docker, benchmarks qdrant.tech, v1.19.0).

Le verdict jugeait la FORME du final, jamais le TRAVAIL accompli.

C'est la deuxième occurrence : `w_redaction` avait échoué de la même façon au
run du 13/08 sur `uv`. Conséquence en aval : avec un contrat d'effets, H4.b
aurait déclaré l'effet non prouvé et bloqué la clôture à tort.

`inspect_worker_deliverables()` répondait déjà à la question — chaque fichier
assigné existe, est non vide, et n'est plus le stub — et n'était pas consulté.
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.react import ReActLoop
from src.subagents.mission_contract import generate_stub


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", tmp_path)
    ws = tmp_path / "missions" / "task_x"
    ws.mkdir(parents=True)
    return ws


def _loop(owned, *, kind="mission", mws="missions/task_x"):
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: {"metadata": {
            "kind": kind, "mission_workspace": mws, "allowed_files": owned,
        }}
    )
    return loop


# ── Le cas du run ───────────────────────────────────────────────────────────

def test_a_worker_that_filled_its_file_has_delivered(workspace):
    (workspace / "rapport_qdrant.md").write_text(
        "# Rapport Qdrant\n\nLicence Apache 2.0, Rust, v1.19.0 (4 août 2026).",
        encoding="utf-8",
    )
    assert _loop(["rapport_qdrant.md"])._mission_worker_delivered() is True


def test_all_assigned_files_must_be_filled(workspace):
    """Un seul fichier manquant et le worker n'a pas fini : pas d'échappatoire."""
    (workspace / "a.md").write_text("# A\n\ncontenu réel", encoding="utf-8")
    assert _loop(["a.md", "jamais_ecrit.md"])._mission_worker_delivered() is False


def test_an_empty_file_is_not_a_deliverable(workspace):
    (workspace / "vide.md").write_text("", encoding="utf-8")
    assert _loop(["vide.md"])._mission_worker_delivered() is False


def test_a_missing_file_is_not_a_deliverable(workspace):
    assert _loop(["absent.md"])._mission_worker_delivered() is False


# ── La porte de sécurité : un stub n'est pas un livrable ────────────────────

def test_an_untouched_document_stub_is_not_a_deliverable(workspace):
    """Sans ce verrou, un worker qui n'a RIEN fait serait déclaré « livré ».
    Le stub documentaire (I1) n'a ni `raise NotImplementedError` ni `TODO` :
    il lui fallait son propre marqueur."""
    stub = generate_stub({"path": "rapport.md", "owner": "w",
                          "desc": "Rapport factuel"})
    (workspace / "rapport.md").write_text(stub, encoding="utf-8")
    assert _loop(["rapport.md"])._mission_worker_delivered() is False


def test_a_barely_edited_stub_is_still_a_stub(workspace):
    """La comparaison exacte au stub régénéré cède au moindre espace ;
    le marqueur, lui, tient."""
    stub = generate_stub({"path": "rapport.md", "owner": "w", "desc": "Rapport"})
    (workspace / "rapport.md").write_text(stub + "\n   \n", encoding="utf-8")
    assert _loop(["rapport.md"])._mission_worker_delivered() is False


def test_an_untouched_python_stub_is_not_a_deliverable(workspace):
    stub = generate_stub({"path": "mod.py", "owner": "w",
                          "exports": ["def f() -> int"]})
    (workspace / "mod.py").write_text(stub, encoding="utf-8")
    assert _loop(["mod.py"])._mission_worker_delivered() is False


# ── Portée : rien d'autre ne bascule ────────────────────────────────────────

def test_the_lead_never_benefits_from_this(workspace):
    """Le lead n'a pas de périmètre : la question ne se pose pas pour lui."""
    (workspace / "x.md").write_text("contenu", encoding="utf-8")
    assert _loop([])._mission_worker_delivered() is False
    assert _loop(None)._mission_worker_delivered() is False


def test_outside_a_mission_it_never_applies(workspace):
    (workspace / "a.md").write_text("# A\n\ncontenu réel", encoding="utf-8")
    assert _loop(["a.md"], kind="chat")._mission_worker_delivered() is False


def test_without_a_mission_workspace_it_never_applies(workspace):
    (workspace / "a.md").write_text("# A\n\ncontenu réel", encoding="utf-8")
    assert _loop(["a.md"], mws="")._mission_worker_delivered() is False


def test_a_broken_orchestrator_never_grants_success():
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert loop._mission_worker_delivered() is False


def test_no_orchestrator_no_exemption():
    loop = object.__new__(ReActLoop)
    loop.task_id = None
    loop.task_orchestrator = None
    assert loop._mission_worker_delivered() is False
