"""C0 (run FrigoZen 2026-07-04) — les 6 verrous, fix par fix.

1. compaction : write_mission_contract au seuil élevé (l'observation portait les
   objectifs contractuels — compactée à 830 chars, le lead a délégué des objectifs
   divergents du contrat : templates/ vs racine, clés nom/name, days_ahead/days) ;
2. vérité du ledger : un REFUS d'écriture (patch strict, rewrite_reason manquant,
   garde destructif) est un ÉCHEC — plus jamais une « écriture réussie » fantôme
   (la garde A4 a tué w_web sur un faux « déjà écrit », index.html resté stub) ;
3a. verify-gate : une relecture d'artefact ne satisfait JAMAIS une tâche qui exige
   une EXÉCUTION (pytest/servir/navigateur) — read_file avait coché « Vérifier
   l'intégration, exécuter les tests, servir l'app, tester navigateur » ;
3b. garde PUBLISH-ONLY : « Publier le livrable » n'est créditée QUE par
   publish_mission_workspace (le write_file de style.css l'avait cochée) ;
4. FINALIZE branché sur le PYTEST GATE : tests présents sans aucun pytest → relance
   dirigée (bornée 2 tirs) au lieu d'une clôture avec bannière — le lead a été
   coupé avec 23 min de budget, sans pytest, sans navigateur, sans publication ;
5. parse_test_outcome : DERNIÈRE occurrence (résumé pytest en fin de sortie) —
   « assert 0 == 1\\nFAILED tests\\… » donnait failed=1 au lieu des 5 réels ;
6. nom de livrable lisible : dérivation CamelCase depuis l'objectif (« FrigoZen »)
   au lieu du fallback technique livrable_<hex>.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.reasoning.plan_evidence import verify_satisfied_by_artifact_read
from src.reasoning.plan_progress import publish_task_blocks
from src.reasoning.test_proof import parse_test_outcome
from src.subagents.mission_contract import derive_project_name


# ── C0.1 : compaction — write_mission_contract au seuil élevé ────────────────────

def test_compaction_high_threshold_includes_mission_contract():
    # Lot RF-9a : le seuil vit dans `observation_synthesis.py` (feuille
    # « ingestion d'observation », §15) et n'est plus une variable mais un
    # retour. L'assertion devient COMPORTEMENTALE — intention identique,
    # preuve plus forte : elle survivra au prochain deplacement.
    from src.reasoning.observation_synthesis import (
        observation_compact_limit, _OBS_FILE_READ_TOOLS,
    )

    assert observation_compact_limit(
        "write_mission_contract", is_chat_surface=False
    ) == 8000, (
        "l'observation de write_mission_contract PORTE les objectifs contractuels "
        "(allowed_files) — compactée, le lead délègue des objectifs divergents")
    assert "write_mission_contract" in _OBS_FILE_READ_TOOLS, (
        "l'observation de write_mission_contract PORTE les objectifs contractuels "
        "(allowed_files) — compactée, le lead délègue des objectifs divergents")


# ── C0.2 : un refus d'écriture est un ÉCHEC (vérité du ledger) ───────────────────

@pytest.mark.asyncio
async def test_patch_strict_refusal_is_failure(tmp_path: Path, monkeypatch):
    """Le cas w_web figé : write_file sur un stub existant sans force_rewrite.
    Avant : HandlerResult.ok → ledger « écrit » → garde A4 tue le worker avec un
    faux « déjà écrit dans ce run »."""
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "1")
    from src.reasoning.react import ToolRegistry
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    obs1 = await registry.execute("write_file", {"path": "index.html", "content": "<!-- stub -->"})
    assert obs1.success
    obs2 = await registry.execute("write_file", {"path": "index.html", "content": "<html>vrai</html>"})
    assert not obs2.success, "un refus patch-strict ne doit JAMAIS être un succès"
    assert "Patch strict" in obs2.content
    # la guidance reste intacte pour que le modèle se corrige (force_rewrite)
    assert "force_rewrite" in obs2.content


@pytest.mark.asyncio
async def test_missing_rewrite_reason_is_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "1")
    from src.reasoning.react import ToolRegistry
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    await registry.execute("write_file", {"path": "a.txt", "content": "v1"})
    obs = await registry.execute("write_file", {
        "path": "a.txt", "content": "v2", "force_rewrite": True})
    assert not obs.success
    assert "rewrite_reason" in obs.content


@pytest.mark.asyncio
async def test_destructive_refusal_is_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "0")
    from src.reasoning.react import ToolRegistry
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    big = "\n".join(f".cls{i} {{ color: red; }}" for i in range(1400))
    await registry.execute("write_file", {"path": "style.css", "content": big})
    obs = await registry.execute("write_file", {"path": "style.css", "content": ".a{}" * 5})
    assert not obs.success, "le garde destructif est un refus, pas une écriture"


@pytest.mark.asyncio
async def test_forced_rewrite_with_reason_still_succeeds(tmp_path: Path, monkeypatch):
    """La voie légitime reste verte (w_foods l'a empruntée avec succès)."""
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "1")
    from src.reasoning.react import ToolRegistry
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    await registry.execute("write_file", {"path": "foods.py", "content": "# stub"})
    obs = await registry.execute("write_file", {
        "path": "foods.py", "content": "def add(): return 1",
        "force_rewrite": True, "rewrite_reason": "implémentation complète du stub"})
    assert obs.success, obs.content


# ── C0.3a : relecture ≠ preuve d'exécution ───────────────────────────────────────

def test_artifact_read_never_satisfies_execution_verify():
    """La tâche FrigoZen figée : cochée par read_file → FINALIZE prématuré."""
    desc = "vérifier l'intégration, exécuter les tests, servir l'app, tester navigateur"
    assert not verify_satisfied_by_artifact_read("read_file", desc, artifact_reread=True)


def test_artifact_read_still_satisfies_document_verify():
    """#3 préservé : relire le rapport écrit EST la vérification (mission doc)."""
    assert verify_satisfied_by_artifact_read(
        "read_file", "vérifier le fichier final", artifact_reread=True)


@pytest.mark.parametrize("desc", [
    "vérifier que les tests passent",
    "valider la page dans le navigateur",
    "vérifier l'app servie en local",
    "vérifier avec pytest",
])
def test_execution_keywords_block_artifact_read(desc):
    assert not verify_satisfied_by_artifact_read("read_file", desc, artifact_reread=True)


# ── C0.3b : garde PUBLISH-ONLY ───────────────────────────────────────────────────

def test_publish_task_blocked_for_other_tools():
    """Le cas FrigoZen figé : « Publier le livrable » cochée par write_file(style.css)."""
    desc = "publier le livrable et faire le rapport final"
    assert publish_task_blocks("write_file", desc)
    assert publish_task_blocks("read_file", desc)
    assert not publish_task_blocks("publish_mission_workspace", desc)


def test_publish_business_tasks_not_blocked():
    """Publication métier (tweet, article) : créditée par son outil, pas bloquée."""
    assert not publish_task_blocks("twitter_post", "publier le tweet d'annonce")
    assert not publish_task_blocks("write_file", "corriger le bug d'affichage")


def test_publish_delivery_context_words():
    assert publish_task_blocks("edit_file", "publier la livraison finale")
    assert publish_task_blocks("run_command", "publication du livrable dans le workspace")
    assert publish_task_blocks("browser_click_index", "Publier et bilan")


# ── C0.4 : FINALIZE branché sur le PYTEST GATE (structurel) ──────────────────────

def _react_src() -> str:
    import src.reasoning.react as react_mod
    return inspect.getsource(react_mod)


def test_finalize_defers_to_pytest_gate():
    src = _react_src()
    i = src.find("_defer_to_pytest_gate = bool(")
    assert i > 0, "la voie FINALIZE doit vérifier le gate avant de sortir"
    block = src[i:i + 1800]
    assert "_gate_shots_det < 2" in block, "relance BORNÉE (2 tirs, comme le FINAL LLM)"
    assert "self._mission_det_finalized = False" in block, (
        "un report du FINALIZE doit ré-armer la finalisation pour plus tard")
    assert "FINALIZE déterministe intercepté" in block


def test_finalize_gate_before_deterministic_exit():
    src = _react_src()
    i_gate = src.find("_defer_to_pytest_gate = bool(")
    i_exit = src.find("mission_artifact_deterministic_final")
    assert 0 < i_gate < i_exit, "le gate doit précéder la sortie déterministe"


# ── C0.5 : parse_test_outcome — le RÉSUMÉ, pas le corps des échecs ───────────────

_FRIGOZEN_PYTEST_OUT = (
    "============================= test session starts =============================\n"
    "collected 14 items\n\n"
    "tests\\test_alerts.py::test_get_expired_foods FAILED                      [  7%]\n"
    "tests\\test_foods.py::test_get_food_not_found PASSED                      [100%]\n\n"
    "=========================== short test summary info ===========================\n"
    "FAILED tests\\test_alerts.py::test_get_expired_foods - assert 0 == 1\n"
    "FAILED tests\\test_alerts.py::test_get_expiring_soon_foods - assert 0 == 1\n"
    "FAILED tests\\test_foods.py::test_add_food - KeyError: 'nom'\n"
    "FAILED tests\\test_foods.py::test_update_quantity - KeyError: 'quantite'\n"
    "FAILED tests\\test_foods.py::test_get_food - KeyError: 'nom'\n"
    "========================= 5 failed, 9 passed in 0.19s =========================\n"
)


def test_parse_counts_from_summary_not_assert_bodies():
    """Le cas FrigoZen figé : « assert 0 == 1\\nFAILED » matchait AVANT le résumé
    → la bannière honnête affichait « 9 passed, 1 failed » au lieu de 5."""
    o = parse_test_outcome("python -m pytest tests/ -v", _FRIGOZEN_PYTEST_OUT, 1)
    assert o["is_test_cmd"]
    assert o["passed"] == 9
    assert o["failed"] == 5, "compter le RÉSUMÉ final, pas le corps d'un échec"
    assert not o["green"]


def test_parse_green_run_unchanged():
    out = "========================= 12 passed in 0.50s ========================="
    o = parse_test_outcome("python -m pytest tests/", out, 0)
    assert o["passed"] == 12 and o["failed"] == 0
    assert o["green"]


def test_parse_collection_error_unchanged():
    out = (
        "collected 14 items / 1 error\n"
        "!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "============================== 1 error in 0.24s ===============================\n"
    )
    o = parse_test_outcome("python -m pytest tests/ -v", out, 2)
    assert o["errors"] == 1
    assert o["collection_error"]
    assert not o["green"]


# ── C0.6 : nom de projet lisible depuis l'objectif ───────────────────────────────

def test_derive_project_name_frigozen():
    assert derive_project_name(
        "Construis-moi FrigoZen, un assistant anti-gaspillage alimentaire complet."
    ) == "FrigoZen"


def test_derive_project_name_first_camelcase_wins():
    assert derive_project_name("Compare StockPilot et PlantCare") == "StockPilot"


def test_derive_project_name_no_camelcase_gives_empty():
    assert derive_project_name("construis un petit assistant anti-gaspillage") == ""
    assert derive_project_name("") == ""
    assert derive_project_name(None) == ""


def test_derive_project_name_worker_objective():
    """Les objectifs workers contiennent aussi le nom produit."""
    assert derive_project_name(
        "[Worker w_tests] 📜 CONTRAT DE MISSION : implémente les tests de FrigoZen"
    ) == "FrigoZen"
