"""LOT 2.13 — verrous de clôture V1 missions (audit run 2026-07-09, 7 familles).

A — verrou D GÉNÉRALISÉ : mission JEU web (détection sur l'OBJECTIF) + livrable
    web + interaction non prouvée → bannière QUEL QUE SOIT le texte du final
    (cause : puissance4, « jetons tombent / X a gagné » hors regex = seule
    fabrication LIVRÉE du run). Regex élargie en second filet.
B — gate INTENTION CONTRAT : protocole contrat+workers exigé EXPLICITEMENT +
    create_project direct sans write_mission_contract → redirection 1 tir
    (cause : miniblog, 4/7 esquives).
C — le CONTRAT est la SEULE source de spec workers : exports/imports EXACTS
    injectés par owner à delegate_and_wait (cause : bibliapi, objectifs lead
    contradictoires → 4 failed).
D — PYTEST GATE budget-aware : tirs 3-4 si rouge + budget confortable + failed
    décroissant (cause : bibliapi conclu à 4 failed avec ~24 min restantes).
"""

import pytest

from src.reasoning.final_guards import (
    apply_mission_truth_lock,
    interaction_claims_unproven,
    objective_is_web_game,
    objective_requires_contract_protocol,
)
from src.subagents.mission_budget import pytest_gate_extra_shot_allowed
from src.subagents.mission_contract import WORKER_SPEC_MARK, worker_spec_block


# ═══════════════ 2.13.A — verrou D généralisé (le ledger décide) ═══════════════

class TestObjectiveIsWebGame:
    def test_game_objectives_detected(self):
        assert objective_is_web_game("Crée un jeu de puissance 4 jouable dans le navigateur") is True
        assert objective_is_web_game("Un morpion en HTML/JS") is True
        assert objective_is_web_game("Site du pendu avec score et gagnant affiché") is True

    def test_non_game_objectives_silent(self):
        assert objective_is_web_game("Site vitrine pour une boulangerie") is False
        assert objective_is_web_game("API REST de gestion d'inventaire") is False
        # « partie » AMBIGU sans verbe de jeu → jamais compté (anti-bannière abusive)
        assert objective_is_web_game("Dashboard qui affiche une partie des données") is False

    def test_negation_aware(self):
        assert objective_is_web_game("Pas de jeu : un simple formulaire de contact") is False
        assert objective_is_web_game("") is False


class TestDeterministicGameBanner:
    def test_P4_verbatim_neutral_text_banned(self):
        """Le cas puissance4 : final au vocabulaire HORS regex → la couche
        déterministe bannérise quand même (l'objectif était un jeu)."""
        final = "Puissance 4 livré : les jetons tombent avec gravité et la victoire verticale est détectée."
        guarded, meta = apply_mission_truth_lock(
            final,
            has_green_test=True,
            has_browser_proof=True,
            web_deliverable=True,
            interaction_proven=False,
            objective_is_game=True,
        )
        assert "Interaction NON prouvée" in guarded
        assert meta.get("changed") is True

    def test_totally_neutral_final_still_banned_when_game(self):
        """« Quel que soit le texte » : même un final sans AUCUN vocabulaire de
        jeu reçoit la bannière si l'objectif était un jeu web non prouvé."""
        final = "Le livrable est prêt dans workspace/p4. Bonne journée !"
        guarded, _ = apply_mission_truth_lock(
            final,
            has_green_test=True,
            has_browser_proof=True,
            web_deliverable=True,
            interaction_proven=False,
            objective_is_game=True,
        )
        assert "Interaction NON prouvée" in guarded

    def test_proven_interaction_silent(self):
        """Morpion du run 09/07 : interaction PROUVÉE par browser_evaluate →
        aucune bannière, texte intact."""
        final = "Morpion livré : partie jouée et gagnée, reset vérifié."
        guarded, meta = apply_mission_truth_lock(
            final,
            has_green_test=True,
            has_browser_proof=True,
            web_deliverable=True,
            interaction_proven=True,
            objective_is_game=True,
        )
        assert "Interaction NON prouvée" not in guarded
        assert meta.get("changed") is False

    def test_non_game_neutral_final_untouched(self):
        """Pas un jeu + texte neutre → strictement inchangé (zéro bannière
        abusive, leçon monresto 2.12.B/C)."""
        final = "Le site vitrine est prêt dans workspace/resto."
        guarded, meta = apply_mission_truth_lock(
            final,
            has_green_test=True,
            has_browser_proof=True,
            web_deliverable=True,
            interaction_proven=False,
            objective_is_game=False,
        )
        assert "Interaction NON prouvée" not in guarded
        assert meta.get("changed") is False


class TestWidenedInteractionRegex:
    def test_puissance4_vocabulary_now_caught(self):
        assert interaction_claims_unproven("Les jetons tombent correctement dans la grille.") is True
        assert interaction_claims_unproven("Victoire verticale détectée après 4 coups.") is True
        assert interaction_claims_unproven("X a gagné la manche affichée.") is True
        assert interaction_claims_unproven("Le joueur rouge a gagné.") is True

    def test_benign_claims_still_silent(self):
        assert interaction_claims_unproven("Le formulaire est envoyé et la liste se met à jour.") is False
        assert interaction_claims_unproven("Livrable publié, tests 7/7 verts.") is False


# ═══════════════ 2.13.B — gate INTENTION CONTRAT ═══════════════

class TestObjectiveRequiresContractProtocol:
    def test_explicit_protocol_detected(self):
        assert objective_requires_contract_protocol(
            "Crée un blog. Utilise le protocole contrat + workers.") is True
        assert objective_requires_contract_protocol(
            "Mission avec des sous-agents : chaque worker délègue au CodeAgent.") is True
        assert objective_requires_contract_protocol(
            "Pose un contrat de mission puis délègue.") is True
        assert objective_requires_contract_protocol(
            "Utilise write_mission_contract pour répartir le travail.") is True

    def test_no_explicit_requirement_inert(self):
        # create_project direct reste licite (morpion/pwgen/sondage, run 09/07)
        assert objective_requires_contract_protocol("Crée un jeu morpion en HTML/JS.") is False
        assert objective_requires_contract_protocol(
            "API de gestion des workers d'une usine (planning des équipes).") is False
        assert objective_requires_contract_protocol("") is False

    def test_negation_aware(self):
        assert objective_requires_contract_protocol(
            "Fais-le seul, sans contrat ni workers.") is False


class _FakeLedger:
    def __init__(self, has_contract: bool):
        self._has = has_contract

    def has_successful_action(self, action: str) -> bool:
        return self._has and action == "write_mission_contract"


class _GateSelf:
    """Duck-type minimal pour ReActLoop._contract_intent_gate (méthode
    autoportante : ne lit que ces attributs)."""

    def __init__(self, *, mission=True, objective="", has_contract=False,
                 allowed_files=None, parent_id=None):
        self._is_mission_run = mission
        self._original_query = objective
        self.execution_ledger = _FakeLedger(has_contract)
        self.task_id = "t_lot213"
        self._mission_allowed_files_meta = lambda: list(allowed_files or [])
        # H4 — le gate demande désormais « suis-je un worker ? » (périmètre OU
        # parent) et non plus « ai-je des fichiers ? » : un porteur d'EFFETS purs
        # n'a pas d'`allowed_files` et se voyait sommer de poser un contrat.
        self._is_delegated_worker = lambda: bool(parent_id)
        self._is_worker_run = lambda: (
            bool(allowed_files) or bool(parent_id)
        )


def _gate(self_obj, tool="create_project"):
    from src.reasoning.react import ReActLoop
    return ReActLoop._contract_intent_gate(self_obj, tool)


class TestContractIntentGate:
    def test_explicit_requirement_redirects_once(self):
        s = _GateSelf(objective="Utilise le protocole contrat + workers pour ce blog.")
        obs = _gate(s)
        assert obs is not None and obs.success is False
        assert "write_mission_contract" in obs.content
        # 1 tir : le second appel laisse passer (le lead a eu sa consigne)
        assert _gate(s) is None

    def test_no_requirement_inert(self):
        s = _GateSelf(objective="Crée un jeu morpion en HTML/JS.")
        assert _gate(s) is None

    def test_contract_already_written_inert(self):
        s = _GateSelf(objective="Protocole contrat + workers obligatoire.",
                      has_contract=True)
        assert _gate(s) is None

    def test_worker_and_non_mission_inert(self):
        # worker délégué (allowed_files) → le contrat appartient au lead
        s = _GateSelf(objective="contrat + workers", allowed_files=["api.py"])
        assert _gate(s) is None
        # hors mission → jamais de gate
        s2 = _GateSelf(mission=False, objective="contrat + workers")
        assert _gate(s2) is None

    def test_effect_worker_is_never_asked_to_write_the_contract(self):
        """H4 (run veille_python_313) — un porteur d'EFFETS purs n'a PAS
        d'`allowed_files` : le gate le prenait pour le lead et le sommait de poser
        un contrat que le lead avait déjà écrit."""
        s = _GateSelf(objective="contrat + workers", allowed_files=[],
                      parent_id="task_lead")
        assert _gate(s) is None

    def test_the_lead_is_still_redirected(self):
        """Le risque du correctif est d'éteindre le gate pour tout le monde."""
        s = _GateSelf(objective="Utilise le protocole contrat + workers.")
        assert _gate(s) is not None

    def test_other_tools_inert(self):
        s = _GateSelf(objective="contrat + workers obligatoire")
        assert _gate(s, tool="read_file") is None


# ═══════════════ 2.13.C — contrat = seule source de spec ═══════════════

_CONTRACT = {
    "files": [
        {"path": "storage.py", "owner": "w_storage",
         "exports": ["def add_book(title: str) -> dict", "BOOKS = {}"],
         "desc": "stockage en mémoire"},
        {"path": "api.py", "owner": "w_api",
         "exports": ["def create_app()"],
         "imports": ["from storage import add_book"]},
    ]
}


class TestWorkerSpecBlock:
    def test_owner_files_exact_spec(self):
        block = worker_spec_block(_CONTRACT, ["api.py"])
        assert WORKER_SPEC_MARK in block
        assert "def create_app()" in block
        assert "from storage import add_book" in block  # l'import EXACT (cause bibliapi)
        assert "add_book(title: str)" not in block      # fichier d'un AUTRE owner exclu
        assert "CONTRAT prime" in block
        assert "N'invente NI seed" in block

    def test_unknown_perimeter_falls_back_to_full_block(self):
        """Lead ayant délégué sans périmètre reconnaissable → bloc COMPLET
        (déterministe, jamais muet)."""
        block = worker_spec_block(_CONTRACT, ["zz_inconnu.py"])
        assert "def create_app()" in block and "def add_book" in block

    def test_no_files_empty(self):
        assert worker_spec_block({"files": []}, ["api.py"]) == ""
        assert worker_spec_block({}, ["api.py"]) == ""


# ═══════════════ 2.13.D — PYTEST GATE budget-aware ═══════════════

class TestPytestGateExtraShot:
    def test_bibliapi_case_progress_and_budget(self):
        """4 failed, ~24 min restantes, 1er tir rouge (prev inconnu) → tir 3 accordé."""
        assert pytest_gate_extra_shot_allowed(
            shots=2, failed_now=4, failed_prev=None,
            remaining_s=1440, ratio_used=0.4) is True

    def test_progress_decreasing(self):
        assert pytest_gate_extra_shot_allowed(
            shots=3, failed_now=2, failed_prev=4,
            remaining_s=1000, ratio_used=0.3) is True

    def test_stagnation_stops(self):
        assert pytest_gate_extra_shot_allowed(
            shots=3, failed_now=4, failed_prev=4,
            remaining_s=1440, ratio_used=0.2) is False

    def test_short_budget_stops(self):
        assert pytest_gate_extra_shot_allowed(
            shots=2, failed_now=3, failed_prev=None,
            remaining_s=120, ratio_used=0.9) is False

    def test_hard_cap_4(self):
        assert pytest_gate_extra_shot_allowed(
            shots=4, failed_now=1, failed_prev=3,
            remaining_s=2000, ratio_used=0.1) is False

    def test_green_or_zero_failed_stops(self):
        assert pytest_gate_extra_shot_allowed(
            shots=2, failed_now=0, failed_prev=2,
            remaining_s=2000, ratio_used=0.1) is False
        assert pytest_gate_extra_shot_allowed(
            shots=2, failed_now=None, failed_prev=None,
            remaining_s=2000, ratio_used=0.1) is False

    def test_no_deadline_mission_allowed(self):
        """Mission sans échéance (budget inconnu) → pas de pression → autorisé."""
        assert pytest_gate_extra_shot_allowed(
            shots=2, failed_now=2, failed_prev=None,
            remaining_s=None, ratio_used=None) is True
