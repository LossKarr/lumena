"""LOT 2.12 — tests dédiés A / B / C / D (audit 8 runs, 2026-07-08→09).

A : le CodeAgent singleton sérialise execute() → deux workers PARALLÈLES ne se
    clobbent plus le périmètre `_allowed_files` (run tasksapi : w_api héritait de
    ['test_api.py'] et refusait app.py).
B : « Pas d'interface web / uniquement JSON » = négation → pas de gate navigateur
    (run tasksapi : bannière navigateur abusive sur une API pure).
C : livrable écrit DIRECT dans workspace/X (existe sur disque) → pas de faux
    « Non publié » (run monresto).
D : claim d'interaction jouée (« jeu démarré / serpent redirigé ») sans preuve
    browser_evaluate → bannière « Interaction NON prouvée » (run snake).
"""

from __future__ import annotations

import asyncio

import pytest

from src.agents.sub_agent import SubAgent, AgentTask, AgentType
from src.reasoning.react import _objective_wants_browser
from src.reasoning.final_guards import (
    apply_mission_truth_lock,
    published_target_present_on_disk,
    interaction_claims_unproven,
)


# ─────────────────────── LOT 2.12.A — sérialisation ─────────────────────────
def test_A_execute_serialized_no_perimeter_clobber():
    """Deux execute() concurrents sur la MÊME instance : chacun garde SON périmètre
    du début à la fin (le verrou empêche l'entrelacement qui clobbait)."""
    agent = SubAgent(agent_type=AgentType.CODE, name="CodeAgent")
    seen: dict = {}

    async def _fake_execute_task(task):
        # Mime l'armement du périmètre depuis le contexte de la tâche.
        agent._allowed_files = frozenset(task.context["allowed_files"])
        entry = set(agent._allowed_files)
        await asyncio.sleep(0.05)  # fenêtre d'entrelacement
        exit_ = set(agent._allowed_files)
        seen[task.task_id] = (entry, exit_)
        return "ok"

    agent._execute_task = _fake_execute_task  # type: ignore[assignment]

    task_a = AgentTask("A", "remplir a", AgentType.CODE, context={"allowed_files": ["a.py"]})
    task_b = AgentTask("B", "remplir b", AgentType.CODE, context={"allowed_files": ["b.py"]})

    async def _run():
        await asyncio.gather(agent.execute(task_a), agent.execute(task_b))

    asyncio.run(_run())

    # Sans le verrou, l'un verrait le périmètre de l'autre en sortie → clobber.
    assert seen["A"] == ({"a.py"}, {"a.py"})
    assert seen["B"] == ({"b.py"}, {"b.py"})


def test_A_exec_lock_exists():
    agent = SubAgent(agent_type=AgentType.CODE, name="CodeAgent")
    assert isinstance(agent._exec_lock, asyncio.Lock)


# ─────────────────────── LOT 2.12.B — négations web ─────────────────────────
def test_B_pas_d_interface_web_is_negation():
    assert _objective_wants_browser("Crée une API Flask, Pas d'interface web, uniquement JSON.") is False


def test_B_sans_aucune_interface_web():
    assert _objective_wants_browser(
        "API Flask de gestion de tâches en JSON uniquement, sans aucune interface web."
    ) is False


def test_B_json_uniquement():
    assert _objective_wants_browser("Une API qui répond en JSON uniquement.") is False


def test_B_positive_web_still_true():
    assert _objective_wants_browser("Fais un site web et vérifie dans le navigateur.") is True
    # Le vrai prompt monresto : « … sers-le et vérifie dans le navigateur ».
    assert _objective_wants_browser("mini-site 3 pages, sers-le et vérifie dans le navigateur") is True


def test_B_cli_no_web():
    assert _objective_wants_browser("un outil en ligne de commande, validation par les tests") is False


# ─────────────────── LOT 2.12.C — « Non publié » disk-grounded ──────────────
def test_C_present_on_disk_true(tmp_path):
    d = tmp_path / "workspace" / "monresto"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    assert published_target_present_on_disk("Publié dans workspace/monresto/.", tmp_path) is True


def test_C_present_on_disk_missing_dir(tmp_path):
    assert published_target_present_on_disk("Publié dans workspace/monresto/.", tmp_path) is False


def test_C_no_false_non_publie_when_on_disk(tmp_path):
    d = tmp_path / "workspace" / "monresto"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    final = "Mission accomplie ! Le mini-site est publié dans workspace/monresto/."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        has_published=False,   # le lead a écrit direct, jamais publish_mission_workspace
        project_root=tmp_path,
    )
    assert "Non publié" not in guarded


def test_C_non_publie_still_fires_when_missing(tmp_path):
    final = "Le livrable est publié dans workspace/fantome/."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        has_published=False,
        project_root=tmp_path,
    )
    assert "Non publié" in guarded


# ─────────────────── LOT 2.12.D — claim d'interaction ───────────────────────
def test_D_interaction_claim_detected():
    assert interaction_claims_unproven("Touche Espace → jeu démarré, Flèche Basse → serpent redirigé.") is True
    assert interaction_claims_unproven("Le jeu est jouable, le score augmente.") is True


def test_D_no_interaction_claim():
    assert interaction_claims_unproven("Livrable publié, tests 7/7 verts.") is False
    assert interaction_claims_unproven('"Pommes" apparaît dans la liste.') is False  # DOM (2.7.4), pas jeu


def test_D_banner_when_web_and_unproven():
    final = "Le jeu Snake est prêt : Espace → jeu démarré, Flèche Basse → serpent redirigé."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        interaction_proven=False,
    )
    assert "Interaction NON prouvée" in guarded


def test_D_silent_when_interaction_proven():
    final = "Le jeu Snake fonctionne : le serpent se déplace, le score augmente."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
        interaction_proven=True,   # un browser_evaluate a lu l'état JS réel
    )
    assert "Interaction NON prouvée" not in guarded


def test_D_silent_when_not_web():
    final = "Le jeu démarré, serpent redirigé."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=False,     # pas un livrable web → hors périmètre
        interaction_proven=False,
    )
    assert "Interaction NON prouvée" not in guarded


def test_D_default_interaction_proven_inert():
    """Appelants existants (interaction_proven non passé = True) → inerte."""
    final = "Le jeu est jouable, le score augmente."
    guarded, _meta = apply_mission_truth_lock(
        final,
        has_green_test=True,
        has_browser_proof=True,
        web_deliverable=True,
    )
    assert "Interaction NON prouvée" not in guarded
