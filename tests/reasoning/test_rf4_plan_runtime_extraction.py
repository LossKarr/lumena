"""RF-4 — matrice d'ETAT de la progression du plan, et fermeture de dependances.

Lot RF-4 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md` :
`_update_plan_progress` (862 lignes) quitte `react.py` pour
`src/reasoning/react_plan_runtime.py`.

--- Pourquoi une matrice d'ETAT et non de TEXTE ---

RF-3 comparait un prompt : une chaine, hachable. RF-4 ne le peut pas. La
fonction **ne retourne rien** — un seul `return` nu, en premiere instruction —
et son effet entier est une MUTATION. La preuve compare donc ce qui est mute :

- les 7 champs de chaque `TaskItem` (qui sont exactement les 6 champs ecrits
  par la fonction, plus `description` : l'instantane est l'etat COMPLET de la
  tache, pas un echantillon) ;
- la valeur finale de `_last_auto_advance_iter` ;
- le nombre d'appels a `_emit_plan_state`.

Ce dernier compteur n'est pas decoratif : le scenario `26_plan_vide` vaut 0 la
ou tous les autres valent 1. C'est lui qui prouve que la sortie anticipee et
l'emission restent dans le bon ordre dans la coquille.

--- Pourquoi ce test est FAIL-CLOSED ---

Aucun `try/except` n'entoure l'appel. Au premier essai de RF-3, un
`except Exception` dans le harnais avait transforme un `NameError` en texte
capture : les comparaisons etaient marquees en ecart et j'ai lu « identique ».
Une exception doit tuer le scenario, tout de suite et bruyamment.

--- La reference ---

Les 26 empreintes ci-dessous ont ete capturees sur le code AVANT extraction.
Une evolution VOLONTAIRE de la progression du plan les fera echouer : c'est
voulu. La mise a jour doit alors etre explicite et justifiee, pas subie.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "react_plan_runtime.py"


# ══════════════════════════════════════════════════════════════════════════
#  Les scenarios
# ══════════════════════════════════════════════════════════════════════════

SCENARIOS: Dict[str, Tuple[List[str], str, Dict[str, Any], str, int, bool, Dict[str, Any]]] = {

    "01_ecriture_prouvee": (
        ["Creer le fichier index.html"], "create_file",
        {"path": "index.html", "content": "<html></html>"},
        "✅ Fichier cree : index.html (231 octets)", 1, True, {}),

    "02_lecture_ne_credite_pas_une_creation": (
        ["Creer le fichier index.html"], "read_file",
        {"path": "index.html"}, "<html></html>", 1, True, {}),

    "03_readonly_ne_credite_pas_une_correction": (
        ["Corriger le bug de calcul dans facture.py"], "read_file",
        {"path": "facture.py"}, "def total(): return 0", 1, True, {}),

    "04_readonly_grep_ne_credite_pas_une_correction": (
        ["Corriger le bug de calcul dans facture.py"], "grep_search",
        {"pattern": "total"}, "facture.py:3: def total():", 1, True, {}),

    "05_edition_credite_une_correction": (
        ["Corriger le bug de calcul dans facture.py"], "edit_file",
        {"path": "facture.py", "old": "return 0", "new": "return s"},
        "✅ facture.py modifie (1 remplacement)", 2, True, {}),

    "06_outil_nomme_respecte": (
        ["Utiliser create_pdf pour produire le rapport"], "create_pdf",
        {"title": "Rapport"}, "✅ PDF cree : rapport.pdf", 1, True, {}),

    "07_outil_nomme_par_un_autre_outil": (
        ["Utiliser create_pdf pour produire le rapport"], "create_docx",
        {"title": "Rapport"}, "✅ DOCX cree : rapport.docx", 1, True, {}),

    "08_submit_non_marque_par_type": (
        ["Soumettre le formulaire de contact"], "browser_type_index",
        {"index": "4", "text": "Mon message"},
        "✅ Texte saisi dans le champ message", 1, True, {}),

    "09_submit_marque_par_clic": (
        ["Soumettre le formulaire de contact"], "browser_click_index",
        {"index": "5"},
        "✅ Clic sur [5] button 'Submit' — formulaire soumis", 1, True, {}),

    "10_navigateur_passif_sur_tache_de_verification": (
        ["Verifier que la page contact s'affiche"], "browser_snapshot",
        {}, "[1] heading 'Contact'\n[2] textbox 'Email'", 1, True, {}),

    "11_observation_en_echec_ne_coche_rien": (
        ["Creer le fichier index.html"], "create_file",
        {"path": "index.html"}, "❌ Erreur : permission refusee", 1, True, {}),

    "12_tache_pytest_sans_preuve": (
        ["Lancer pytest et obtenir des tests verts"], "run_command",
        {"command": "dir"}, "index.html\nstyle.css", 1, True, {}),

    "13_tache_pytest_avec_sortie_verte": (
        ["Lancer pytest et obtenir des tests verts"], "run_command",
        {"command": "pytest -q"}, "12 passed in 1.20s", 2, True, {}),

    "14_tache_publication_sans_publish": (
        ["Publier le livrable dans le workspace"], "create_file",
        {"path": "a.txt"}, "✅ Fichier cree : a.txt", 1, True, {}),

    "15_recherche_web_sourcee": (
        ["Rechercher les tarifs 2026 avec sources"], "web_search",
        {"query": "tarifs 2026"},
        "1. https://exemple.fr/tarifs — Tarifs 2026\n2. https://autre.fr/prix — Prix",
        1, True, {}),

    "16_fallback_autorise": (
        ["Analyser le besoin", "Rediger la synthese"], "create_file",
        {"path": "synthese.md"}, "✅ Fichier cree : synthese.md", 3, True, {}),

    "17_fallback_interdit": (
        ["Analyser le besoin", "Rediger la synthese"], "create_file",
        {"path": "synthese.md"}, "✅ Fichier cree : synthese.md", 3, False, {}),

    "18_deux_taches_une_seule_par_iteration": (
        ["Creer index.html", "Creer style.css"], "create_file",
        {"path": "index.html"}, "✅ Fichier cree : index.html", 5, True, {}),

    "19_plan_partiellement_complete": (
        ["Creer index.html", "Creer style.css", "Verifier le rendu"],
        "create_file", {"path": "style.css"},
        "✅ Fichier cree : style.css", 4, True, {"pre_completer": [0]}),

    "20_delegation_reussie": (
        ["Deleguer la page d'accueil a un worker"], "delegate_task",
        {"description": "page d'accueil"},
        "✅ Sous-agent termine : index.html cree", 2, True, {}),

    "21_tache_final_only": (
        ["Repondre a l'utilisateur"], "read_file",
        {"path": "a.txt"}, "contenu", 1, True, {}),

    "22_studio_credite_une_tache_document": (
        ["Generer la facture du client Dupont"], "generate_studio_document",
        {"kind": "facture"},
        "✅ Document genere : facture_dupont.pdf", 1, True, {}),

    "23_create_pdf_sur_tache_facture": (
        ["Generer la facture du client Dupont"], "create_pdf",
        {"title": "facture"}, "✅ PDF cree : facture.pdf", 1, True, {}),

    "24_observation_vide": (
        ["Creer le fichier index.html"], "create_file",
        {"path": "index.html"}, "", 1, True, {}),

    "25_outil_inconnu": (
        ["Creer le fichier index.html"], "outil_qui_n_existe_pas",
        {}, "✅ fait", 1, True, {}),

    "26_plan_vide": (
        [], "create_file", {"path": "a.txt"},
        "✅ Fichier cree : a.txt", 1, True, {}),
}


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario sur une boucle neuve et retourne l'ETAT MUTE.

    FAIL-CLOSED : aucune exception n'est rattrapee.
    """
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    descriptions, outil, args, obs, iteration, fallback, cfg = SCENARIOS[nom]
    boucle = ReActLoop(llm_chat_func=lambda *a, **kw: None)
    boucle._task_plan = [TaskItem(description=d) for d in descriptions]
    boucle._plan_emitted = True

    for i in cfg.get("pre_completer", []):
        boucle._task_plan[i].completed = True
        boucle._task_plan[i].completed_by_tool = "create_file"
        boucle._task_plan[i].completed_at_iteration = 1

    emissions = {"n": 0}
    vrai = boucle._emit_plan_state

    def espion(*a, _v=vrai, _c=emissions, **kw):
        _c["n"] += 1
        return _v(*a, **kw)

    boucle._emit_plan_state = espion
    boucle._update_plan_progress(outil, args, obs, iteration, allow_fallback=fallback)

    return {
        "taches": [
            {
                "description": t.description,
                "completed": t.completed,
                "completed_at_iteration": t.completed_at_iteration,
                "completed_by_tool": t.completed_by_tool,
                "completion_status": str(t.completion_status),
                "completion_evidence": str(t.completion_evidence),
                "completion_confidence": t.completion_confidence,
            }
            for t in boucle._task_plan
        ],
        "last_auto_advance_iter": boucle._last_auto_advance_iter,
        "emissions_plan_state": emissions["n"],
    }



# ══════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ══════════════════════════════════════════════════════════════════════════

BASELINE = {
    "01_ecriture_prouvee": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_file",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Creer le fichier index.html"
            }
        ]
    },
    "02_lecture_ne_credite_pas_une_creation": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Creer le fichier index.html"
            }
        ]
    },
    "03_readonly_ne_credite_pas_une_correction": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Corriger le bug de calcul dans facture.py"
            }
        ]
    },
    "04_readonly_grep_ne_credite_pas_une_correction": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Corriger le bug de calcul dans facture.py"
            }
        ]
    },
    "05_edition_credite_une_correction": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 2,
                "completed_by_tool": "edit_file",
                "completion_confidence": "weak",
                "completion_evidence": "preuve insuffisante (payment)",
                "completion_status": "created",
                "description": "Corriger le bug de calcul dans facture.py"
            }
        ]
    },
    "06_outil_nomme_respecte": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_pdf",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Utiliser create_pdf pour produire le rapport"
            }
        ]
    },
    "07_outil_nomme_par_un_autre_outil": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_docx:seq",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Utiliser create_pdf pour produire le rapport"
            }
        ]
    },
    "08_submit_non_marque_par_type": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Soumettre le formulaire de contact"
            }
        ]
    },
    "09_submit_marque_par_clic": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "browser_click_index",
                "completion_confidence": "medium",
                "completion_evidence": "browser_click_index preuve suffisante",
                "completion_status": "created",
                "description": "Soumettre le formulaire de contact"
            }
        ]
    },
    "10_navigateur_passif_sur_tache_de_verification": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier que la page contact s'affiche"
            }
        ]
    },
    "11_observation_en_echec_ne_coche_rien": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Creer le fichier index.html"
            }
        ]
    },
    "12_tache_pytest_sans_preuve": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": 1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "run_command:auto",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Lancer pytest et obtenir des tests verts"
            }
        ]
    },
    "13_tache_pytest_avec_sortie_verte": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": 2,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 2,
                "completed_by_tool": "run_command:auto",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Lancer pytest et obtenir des tests verts"
            }
        ]
    },
    "14_tache_publication_sans_publish": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Publier le livrable dans le workspace"
            }
        ]
    },
    "15_recherche_web_sourcee": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Rechercher les tarifs 2026 avec sources"
            }
        ]
    },
    "16_fallback_autorise": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": 3,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 3,
                "completed_by_tool": "create_file:auto",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Analyser le besoin"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Rediger la synthese"
            }
        ]
    },
    "17_fallback_interdit": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Analyser le besoin"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Rediger la synthese"
            }
        ]
    },
    "18_deux_taches_une_seule_par_iteration": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 5,
                "completed_by_tool": "create_file",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Creer index.html"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Creer style.css"
            }
        ]
    },
    "19_plan_partiellement_complete": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_file",
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Creer index.html"
            },
            {
                "completed": True,
                "completed_at_iteration": 4,
                "completed_by_tool": "create_file",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Creer style.css"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier le rendu"
            }
        ]
    },
    "20_delegation_reussie": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": 2,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 2,
                "completed_by_tool": "delegate_task:auto",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Deleguer la page d'accueil a un worker"
            }
        ]
    },
    "21_tache_final_only": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Repondre a l'utilisateur"
            }
        ]
    },
    "22_studio_credite_une_tache_document": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "generate_studio_document",
                "completion_confidence": "weak",
                "completion_evidence": "preuve insuffisante (payment)",
                "completion_status": "created",
                "description": "Generer la facture du client Dupont"
            }
        ]
    },
    "23_create_pdf_sur_tache_facture": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_pdf",
                "completion_confidence": "weak",
                "completion_evidence": "preuve insuffisante (payment)",
                "completion_status": "created",
                "description": "Generer la facture du client Dupont"
            }
        ]
    },
    "24_observation_vide": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 1,
                "completed_by_tool": "create_file",
                "completion_confidence": "weak",
                "completion_evidence": "preuve par défaut",
                "completion_status": "created",
                "description": "Creer le fichier index.html"
            }
        ]
    },
    "25_outil_inconnu": {
        "emissions_plan_state": 1,
        "last_auto_advance_iter": -1,
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Creer le fichier index.html"
            }
        ]
    },
    "26_plan_vide": {
        "emissions_plan_state": 0,
        "last_auto_advance_iter": -1,
        "taches": []
    }
}


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 26 comparaisons d'etat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(SCENARIOS))
def test_l_etat_mute_est_identique_a_la_reference(nom):
    """La seule question qui compte : le plan finit-il dans le MEME etat ?"""
    obtenu = instantane(nom)
    attendu = BASELINE[nom]

    assert obtenu["taches"] == attendu["taches"], (
        f"{nom} : l'etat des taches a change\n"
        f"  attendu : {json.dumps(attendu['taches'], ensure_ascii=False)}\n"
        f"  obtenu  : {json.dumps(obtenu['taches'], ensure_ascii=False)}"
    )
    assert obtenu["last_auto_advance_iter"] == attendu["last_auto_advance_iter"], (
        f"{nom} : _last_auto_advance_iter a change"
    )
    assert obtenu["emissions_plan_state"] == attendu["emissions_plan_state"], (
        f"{nom} : le nombre d'emissions de l'etat du plan a change"
    )


def test_la_matrice_discrimine_vraiment():
    """Une matrice dont tous les scenarios donnent le meme etat ne prouve rien.

    Celle-ci produit 22 instantanes distincts sur 26, coche 15 taches au total
    et exerce les quatre valeurs distinctes de `_last_auto_advance_iter`.
    """
    distincts = {json.dumps(v, sort_keys=True) for v in BASELINE.values()}
    assert len(distincts) >= 20, f"matrice trop pauvre : {len(distincts)} etats distincts"

    coches = sum(1 for v in BASELINE.values() for t in v["taches"] if t["completed"])
    assert coches >= 12, f"trop peu de taches cochees : {coches}"

    avances = {v["last_auto_advance_iter"] for v in BASELINE.values()}
    assert len(avances) >= 3, f"le chemin d'auto-avancement n'est pas exerce : {avances}"


def test_la_sortie_anticipee_n_emet_pas_l_etat_du_plan():
    """Sur plan vide, `_emit_plan_state` ne doit PAS partir.

    C'est ce qui force l'ordre dans la coquille : la sortie anticipee d'abord,
    l'emission ensuite. Inverser les deux passerait tous les autres scenarios.
    """
    assert BASELINE["26_plan_vide"]["emissions_plan_state"] == 0
    assert instantane("26_plan_vide")["emissions_plan_state"] == 0
    for nom, v in BASELINE.items():
        if nom != "26_plan_vide":
            assert v["emissions_plan_state"] == 1, nom


# ══════════════════════════════════════════════════════════════════════════
#  2. Fermeture de dependances — les QUATRE familles
# ══════════════════════════════════════════════════════════════════════════


def _noms_libres(chemin: Path) -> list[str]:
    """Noms charges jamais lies : fonctions, constantes, imports, ET les noms
    lies par une signature — y compris celles des fonctions IMBRIQUEES.

    C'est cette derniere famille qui manquait au premier essai de RF-3 (le
    parametre `query`), et c'est elle qui a produit deux faux positifs pendant
    l'audit de RF-4 (`_desc_lower`, `_obs_lower`, parametres de fonctions
    internes).
    """
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))

    def args_de(f):
        a = f.args
        noms = {x.arg for x in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs)}
        if a.vararg:
            noms.add(a.vararg.arg)
        if a.kwarg:
            noms.add(a.kwarg.arg)
        return noms

    lies = set(dir(builtins))
    for n in ast.walk(arbre):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                lies.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lies.add(n.name)
            lies |= args_de(n)
        elif isinstance(n, ast.Lambda):
            lies |= args_de(n)
        elif isinstance(n, ast.ClassDef):
            lies.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            lies.add(n.id)
        elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            lies.add(n.target.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            lies.add(n.name)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            lies.add(n.target.id)

    charges = {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(charges - lies)


def test_le_module_extrait_n_a_aucun_nom_global_non_resolu():
    assert _noms_libres(NOUVEAU) == []


def test_le_module_extrait_ne_reference_jamais_self():
    """Trois formes, pas une : `self.X`, `getattr(self, ...)` et
    `Classe.methode(self, ...)`."""
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [
        ast.unparse(n) for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id == "self"
    ]
    assert fautes == [], f"`self` a fui dans le module extrait : {fautes[:5]}"


def test_le_module_extrait_ne_reference_jamais_la_classe_ReActLoop():
    """La famille que le balayage de `self` ne voit PAS.

    `ReActLoop._document_plan_required_kinds(task.description)` est un appel sur
    la CLASSE, sans `self` du tout. Trois sites. Il a ete trouve par la
    fermeture de noms libres, pas par le balayage d'attributs — et il aurait
    produit un `NameError` a l'execution, comme
    `_build_model_specific_hints` en RF-3.
    """
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [
        ast.unparse(n) for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id == "ReActLoop"
    ]
    assert fautes == [], f"appel sur la classe reste dans le module : {fautes}"


def test_le_module_extrait_n_importe_pas_react():
    """Invariant 2 : aucun nouveau module n'importe `react.py`."""
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom):
            mod = (n.module or "")
            if mod == "react" or mod.endswith(".react") or mod == "src.reasoning.react":
                fautes.append(ast.unparse(n))
        elif isinstance(n, ast.Import):
            for al in n.names:
                if al.name.endswith(".react") or al.name == "react":
                    fautes.append(ast.unparse(n))
    assert fautes == [], f"import vers react.py : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que la coquille doit garder
# ══════════════════════════════════════════════════════════════════════════


def test_la_coquille_porte_la_sortie_anticipee_la_mutation_et_l_emission():
    from src.reasoning.react import ReActLoop

    source = inspect.getsource(ReActLoop._update_plan_progress)
    assert "if not self._task_plan:" in source, "la sortie anticipee a quitte la coquille"
    assert "self._last_auto_advance_iter = valeur" in source, "la mutation a quitte ReActLoop"
    assert "self._emit_plan_state(context_tool=tool_name)" in source, "l'emission a quitte la coquille"
    assert "appliquer_progression_plan" in source, "la coquille n'appelle pas le module extrait"


def test_la_signature_publique_est_inchangee():
    """`execution_router.py` et une vingtaine de tests appellent cette methode
    directement : sa signature fait partie du contrat (invariant 13/14)."""
    from src.reasoning.react import ReActLoop

    sig = inspect.signature(ReActLoop._update_plan_progress)
    assert list(sig.parameters) == [
        "self", "tool_name", "tool_args", "observation_content",
        "iteration", "allow_fallback",
    ]
    assert sig.parameters["allow_fallback"].default is True
    assert sig.return_annotation is None or sig.return_annotation == "None"
    assert not inspect.iscoroutinefunction(ReActLoop._update_plan_progress)


def test_document_plan_required_kinds_reste_un_staticmethod():
    """Invariant 13 : la forme du descripteur fait partie du contrat. Le lot
    passe cette fonction en appelable, il ne la transforme pas."""
    from src.reasoning.react import ReActLoop

    brut = inspect.getattr_static(ReActLoop, "_document_plan_required_kinds")
    assert isinstance(brut, staticmethod), f"forme changee : {type(brut)}"


# ══════════════════════════════════════════════════════════════════════════
#  4. Paresse et descripteurs — ce que des valeurs pre-calculees auraient casse
# ══════════════════════════════════════════════════════════════════════════


def test_les_acces_a_l_orchestrateur_sont_en_nombre_inchange():
    """`_is_mission_run` est une `property` qui interroge
    `task_orchestrator.get_task(task_id)`. Elle est lue derriere un
    court-circuit, donc son cout depend des branches atteintes.

    La passer en VALEUR pre-calculee changerait ce nombre : elle serait evaluee
    a chaque appel de `_update_plan_progress`, branche atteinte ou non. Le lot
    la passe en APPELABLE, et ce test fige le nombre d'acces MESURE SUR LE CODE
    D'ORIGINE avant extraction.

    (Une premiere version de ce test affirmait 0 acces. C'etait faux : la
    branche EST atteinte sur ces scenarios. Mesurer avant d'affirmer.)
    """
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    # nombres releves sur `react.py` AVANT le lot RF-4
    REFERENCE = {
        ("Creer le fichier index.html", "create_file"): 2,
        ("Soumettre le formulaire de contact", "browser_type_index"): 2,
        ("Corriger le bug dans facture.py", "read_file"): 2,
    }

    for (description, outil), attendu in REFERENCE.items():
        boucle = ReActLoop(llm_chat_func=lambda *a, **kw: None)
        boucle._task_plan = [TaskItem(description=description)]
        boucle._plan_emitted = True

        appels = {"n": 0}

        class _OrchestrateurEspion:
            def get_task(self, _tid):
                appels["n"] += 1
                return None

        boucle.task_orchestrator = _OrchestrateurEspion()
        boucle.task_id = "t-1"
        boucle._update_plan_progress(outil, {}, "✅ fait", 1)

        assert appels["n"] == attendu, (
            f"{description!r} + {outil} : {appels['n']} acces orchestrateur "
            f"au lieu de {attendu} mesures avant extraction"
        )


def test_est_run_mission_est_un_appelable_pas_une_valeur():
    """Le garde-fou du test precedent : si quelqu'un « simplifie » l'appelable
    en booleen pre-calcule, l'entree ne serait plus appelable."""
    import inspect as _inspect

    from src.reasoning.react import ReActLoop
    from src.reasoning.react_plan_runtime import EntreeProgressionPlan

    champs = EntreeProgressionPlan.__dataclass_fields__
    for nom in ("est_run_mission", "orchestrateur_actif", "lire_derniere_avance",
                "definir_derniere_avance", "obtenir_route_document",
                "types_documents_requis", "obtenir_outils", "obtenir_task_id",
                "obtenir_ledger", "obtenir_ledger_optionnel",
                "obtenir_orchestrateur"):
        assert nom in champs, f"champ appelable disparu : {nom}"
        assert "Callable" in str(champs[nom].type), (
            f"{nom} n'est plus declare appelable : {champs[nom].type}"
        )

    source = _inspect.getsource(ReActLoop._update_plan_progress)
    assert "est_run_mission=lambda:" in source, (
        "est_run_mission a ete remplace par une valeur pre-calculee"
    )


def test_lire_et_ecrire_la_derniere_avance_passe_par_le_descripteur():
    """`_last_auto_advance_iter` est une `property` dont le getter ET le setter
    appellent `_ensure_exec_state()`. Le lot doit passer par eux, pas contourner
    vers `exec_state.guards`."""
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    boucle = ReActLoop(llm_chat_func=lambda *a, **kw: None)
    boucle._task_plan = [
        TaskItem(description="Analyser le besoin"),
        TaskItem(description="Rediger la synthese"),
    ]
    boucle._plan_emitted = True

    passages = {"ensure": 0}
    vrai = boucle._ensure_exec_state

    def espion(_v=vrai, _c=passages):
        _c["ensure"] += 1
        return _v()

    boucle._ensure_exec_state = espion
    boucle._update_plan_progress(
        "create_file", {"path": "synthese.md"},
        "✅ Fichier cree : synthese.md", 3,
    )
    assert passages["ensure"] > 0, "le descripteur a ete contourne"
    assert boucle._last_auto_advance_iter == 3, "l'ecriture n'a pas atteint l'etat"


def test_la_route_documentaire_est_appelee_par_la_coquille_pas_precalculee():
    """`_document_route_for_run` reste un appelable : il est evalue au meme
    point qu'avant, a l'interieur du meme `try`."""
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    boucle = ReActLoop(llm_chat_func=lambda *a, **kw: None)
    boucle._task_plan = [TaskItem(description="Creer le fichier index.html")]
    boucle._plan_emitted = True

    appels = {"n": 0}
    vrai = ReActLoop._document_route_for_run

    def espion(instance, _v=vrai, _c=appels):
        _c["n"] += 1
        return _v(instance)

    import src.reasoning.react as react_mod
    ancien = react_mod.ReActLoop._document_route_for_run
    react_mod.ReActLoop._document_route_for_run = espion
    try:
        boucle._update_plan_progress(
            "create_file", {"path": "index.html"},
            "✅ Fichier cree : index.html", 1,
        )
    finally:
        react_mod.ReActLoop._document_route_for_run = ancien

    assert appels["n"] == 1, (
        f"route documentaire appelee {appels['n']} fois (attendu 1) : l'ordre "
        "d'evaluation a change"
    )


def test_une_boucle_sans_init_reste_utilisable():
    """LE defaut du premier essai de RF-4, ferme par un test.

    Une vingtaine de tests du depot construisent la boucle par
    `object.__new__(ReActLoop)` : `__init__` n'est JAMAIS appele, et `tools`,
    `task_id`, `execution_ledger`, `task_orchestrator` sont reellement absents.
    Le corps d'origine ne les touchait jamais sur ces scenarios — toutes leurs
    lectures sont au fond de branches gardees.

    Une premiere version du lot passait ces quatre lectures en VALEURS, lues a
    la construction de l'entree : `AttributeError` avant tout garde, et
    54 tests cibles sont tombes. Elles sont desormais PARESSEUSES, chacune
    gardant la forme exacte de son site d'origine.
    """
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    boucle = object.__new__(ReActLoop)
    boucle._task_plan = [TaskItem(description="Creer le fichier index.html")]
    boucle._plan_emitted = True
    boucle._iterations_without_progress = 0
    boucle._last_completed_task_count = 0
    boucle._plan_last_emit_state = ""
    boucle._last_auto_advance_iter = -1

    assert not hasattr(boucle, "tools")
    assert not hasattr(boucle, "task_id")
    assert not hasattr(boucle, "execution_ledger")
    assert not hasattr(boucle, "task_orchestrator")

    boucle._update_plan_progress(
        "create_file", {"path": "index.html"},
        "✅ Fichier cree : index.html", 1,
    )
    assert boucle._task_plan[0].completed, (
        "la tache n'a pas ete cochee sur une boucle sans __init__"
    )


def test_les_deux_formes_de_lecture_du_ledger_sont_preservees():
    """Le corps lisait `execution_ledger` sous DEUX formes : `self.execution_ledger`
    (qui leve si l'attribut manque) et `getattr(self, ..., None)` (qui donne
    None). Une valeur unique ne peut pas reproduire les deux.

    Le lot garde les deux, chacune derriere son propre appelable.
    """
    import inspect as _inspect

    from src.reasoning.react import ReActLoop
    from src.reasoning.react_plan_runtime import EntreeProgressionPlan

    champs = EntreeProgressionPlan.__dataclass_fields__
    assert "obtenir_ledger" in champs
    assert "obtenir_ledger_optionnel" in champs

    source = _inspect.getsource(ReActLoop._update_plan_progress)
    assert "obtenir_ledger=lambda: self.execution_ledger" in source
    assert 'obtenir_ledger_optionnel=lambda: getattr(self, "execution_ledger", None)' in source

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.obtenir_ledger()" in module, "la forme directe a disparu"
    assert "e.obtenir_ledger_optionnel()" in module, "la forme gardee a disparu"


# ══════════════════════════════════════════════════════════════════════════
#  5. Le module ne recree aucune regle
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_extrait_ne_duplique_pas_les_moteurs_purs():
    """Regle propre a RF-4 : `plan_progress.py` et `plan_evidence.py` restent
    les moteurs. Le nouveau module les ORCHESTRE, il ne redefinit aucun de
    leurs symboles."""
    import src.reasoning.plan_evidence as pe
    import src.reasoning.plan_progress as pp

    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    importes = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom):
            for al in n.names:
                importes.add(al.asname or al.name)

    definis = {
        n.name for n in arbre.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    moteurs = {x for x in dir(pp) if not x.startswith("__")} | {
        x for x in dir(pe) if not x.startswith("__")
    }
    collisions = sorted((definis & moteurs) - importes)
    assert collisions == [], f"le module redefinit des regles des moteurs : {collisions}"

    assert "plan_progress" in NOUVEAU.read_text(encoding="utf-8")
    assert "plan_evidence" in NOUVEAU.read_text(encoding="utf-8")


def test_le_module_extrait_ne_declare_aucun_import_de_react():
    """CONSTAT mesure, et pourquoi ce test ne dit pas « importable sans react ».

    Importer `src.reasoning.react_plan_runtime` charge bel et bien `react.py` —
    mais PAS a cause de ce module. `src/reasoning/__init__.py` ligne 7 fait
    `from .react import ReActLoop, ...`, donc importer n'IMPORTE QUEL sous-module
    du paquet charge `react.py`. C'est antérieur a RF-4 et cela vaut aussi pour
    `browser_reasoning` (RF-1) et `observation_synthesis` (RF-2).

    L'invariant 2 porte sur ce que le module DECLARE, pas sur ce que le paquet
    tire. C'est donc cela qui est verifie ici — et c'est verifiable.
    """
    import subprocess
    import sys

    # 1. le paquet lui-meme charge react : le constat, mesure et non suppose
    res = subprocess.run(
        [sys.executable, "-c",
         "import sys; import src.reasoning.plan_progress; "
         "print('react' if 'src.reasoning.react' in sys.modules else 'propre')"],
        cwd=str(RACINE), capture_output=True, text=True, timeout=180,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    assert res.stdout.strip().splitlines()[-1] == "react", (
        "le paquet src.reasoning ne charge plus react.py : ce test doit etre "
        "reecrit, l'invariant peut desormais etre verifie plus strictement"
    )

    # 2. le module extrait s'importe et expose son entree a 16 champs
    res = subprocess.run(
        [sys.executable, "-c",
         "import src.reasoning.react_plan_runtime as m; "
         "print(len(m.EntreeProgressionPlan.__dataclass_fields__))"],
        cwd=str(RACINE), capture_output=True, text=True, timeout=180,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    assert res.stdout.strip().splitlines()[-1] == "17"


def test_comportement_le_garde_browser_only_refuse_par_la_chaine_principale():
    """Preuve COMPORTEMENTALE adossee au repointage R2 de
    `tests/reasoning/test_e_browser_leg.py::test_browser_verify_guard_wired_both_chains`.

    Ce test-la compte deux occurrences de `browser_verify_task_blocks(tool_name`
    dans le texte source ; les deux ont suivi le corps vers
    `react_plan_runtime.py`. Celui-ci ne compte rien : il verifie que le garde
    REFUSE vraiment, en passant par `ReActLoop._update_plan_progress` — ce qui
    en fait aussi la preuve d'integration.
    """
    from loguru import logger

    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    captures: list[str] = []
    poignee = logger.add(captures.append, level="DEBUG", format="{message}")
    try:
        boucle = ReActLoop(llm_chat_func=lambda *a, **kw: None)
        boucle._task_plan = [
            TaskItem(description="Verifier dans le navigateur que la page contact s'affiche")
        ]
        boucle._plan_emitted = True
        boucle._update_plan_progress(
            "read_file", {"path": "contact.html"}, "<html>Contact</html>", 1,
        )
    finally:
        logger.remove(poignee)

    assert not boucle._task_plan[0].completed, (
        "une tache « verifier dans le navigateur » a ete cochee par read_file"
    )
    assert any("[PLAN] Guard BROWSER-ONLY" in c for c in captures), (
        f"le garde BROWSER-ONLY n'a pas trace son refus ; captures = {captures[-3:]}"
    )


def test_le_garde_browser_only_reste_branche_aux_deux_chaines():
    """Meme affirmation que le test d'origine, sur le nouveau proprietaire.

    La chaine principale est prouvee comportementalement juste au-dessus. La
    chaine de repli (« (auto) ») n'a pas de scenario simple qui l'atteigne — le
    garde principal refuse avant. Plutot que de fabriquer un scenario artificiel,
    on conserve ici l'assertion STRUCTURELLE d'origine, deplacee avec le code
    qu'elle surveille.
    """
    module = NOUVEAU.read_text(encoding="utf-8")
    assert module.count("browser_verify_task_blocks(tool_name") == 2, (
        "le garde BROWSER-ONLY doit rester branche aux 2 chaines "
        "(principale + fallback)"
    )
    assert "[PLAN] Guard BROWSER-ONLY" in module
    assert "[PLAN] Guard BROWSER-ONLY (auto)" in module
