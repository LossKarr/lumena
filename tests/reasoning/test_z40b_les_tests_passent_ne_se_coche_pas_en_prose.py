"""Z40b — « les tests passent » ne se coche pas en prose.

Defaut observe au run reel du 2026-08-28 (Run B, mission + CodeAgent) :

    [PLAN BILAN] 3/3 taches completees
      [OK] Verifier que les tests passent          <-- cochee par le FINAL

La tache n'a jamais ete prouvee par une execution. Elle a ete auto-cochee par
le FINAL lui-meme.

--- La cause, MESUREE (pas supposee) ---

`final_fulfills_task` rend True parce que `"verifi"` figure dans `_SYNTH_KW`,
et **les cinq bloqueurs de `final_requires_operational_proof` rendent tous
False** :

    Verifier que les tests passent
        fulfills=True  op_proof=False
        tool=False deleg=False pub=False bv=False bi=False

Ce n'etait donc PAS un probleme de preseance entre la reconciliation et le
bilan : aucune reconciliation n'est ecrasee. C'est un TROU.

--- Le pire des deux mondes ---

La machinerie de preuve EXISTE deja : `pytest_execution_task` reconnait une
tache d'execution, et `pytest_plan_task_proven` la credite depuis un verdict de
test reellement parse au ledger.

Mais `pytest_execution_task` exige le mot « pytest » ET un marqueur
d'execution. « Verifier que les tests passent » n'a NI l'un NI l'autre. Donc
la tache :

    - ne peut PAS etre creditee par un vrai run vert  (elle n'est pas reconnue)
    - EST auto-creditee par le FINAL                  (rien ne la bloque)

**Le pire des deux.** C'est le motif de fond du chantier : le moyen de prouver
existait, le run a REELLEMENT produit un verdict de test, et la case a ete
cochee par de la prose.

--- Pourquoi UN SEUL predicat suffit ---

`tool_explicit_task_blocks` (plan_progress l. 240) bloque deja tout crediteur
hors `_PYTEST_ALLOWED_TOOLS`, et `"FINAL"` n'y figure pas. Elargir le seul
`pytest_execution_task` ferme donc les DEUX moities d'un coup :

    - cote FINAL   : `final_requires_operational_proof` se met a bloquer
    - cote preuve  : `react_plan_runtime` se met a accepter le verdict reel

Un sixieme bloqueur serait du code en trop.

--- La lecon Z3b, respectee ---

La docstring de `browser_verify_task_blocks` raconte l'erreur a ne pas
refaire : sa premiere version allongeait une liste de mots (« filtre », « tri »,
« bouton ») et bloquait a tort « trier les resultats du benchmark ».

    « On ne devine pas l'intention avec du vocabulaire. »

La regle de Z40b ne porte donc AUCUN verbe : elle s'ancre sur l'OBJET —
« tests » suivi de leur ISSUE. « Ecrire les tests » ne matche pas. « Trier les
resultats du benchmark » ne matche pas.

--- Le piege que ce lot doit eviter ---

`pytest_plan_task_proven` ne demandait un run VERT que si l'intitule contenait
« vert ». Elargir la reconnaissance sans elargir cette exigence rendrait le
garde PLUS FAIBLE : un run ROUGE crediterait « les tests passent ». Les deux
pieces vont ensemble.
"""

from __future__ import annotations

import pytest

from src.reasoning.plan_progress import (
    final_fulfills_task,
    final_requires_operational_proof,
    pytest_execution_task,
    pytest_plan_task_proven,
    tool_explicit_task_blocks,
)


#: L'intitule exact du run du 2026-08-28.
DU_RUN = "Verifier que les tests passent"

#: Formulations reelles relevees au corpus (651 missions, 3 616 etapes).
DU_CORPUS = [
    "Tous les tests doivent passer au vert",
    "Les tests doivent tous passer au vert",
    "Executer REELLEMENT les tests et s'assurer qu'ils sont VERTS",
    "Assure-toi que tous les tests passent au vert avant de publier",
    "A la fin, les tests doivent etre REELLEMENT executes et VERTS",
]


def _verdict(green: bool = True, ran: bool = True, is_test: bool = True) -> dict:
    return {"is_test_cmd": is_test, "ran_something": ran, "green": green}


# ══════════════════════════════════════════════════════════════════════════
#  1. Le defaut, reproduit tel quel
# ══════════════════════════════════════════════════════════════════════════


def test_la_tache_du_run_est_reconnue_comme_une_EXECUTION():
    """Avant Z40b : `pytest_execution_task` exigeait le mot « pytest », donc
    cette tache n'etait meme pas candidate a une preuve reelle."""
    assert pytest_execution_task(DU_RUN), (
        "la tache n'est pas reconnue comme une execution : aucun run vert ne "
        "pourra jamais la crediter"
    )


@pytest.mark.parametrize("desc", DU_CORPUS)
def test_les_formulations_du_corpus_sont_reconnues(desc):
    assert pytest_execution_task(desc), desc


def test_le_FINAL_ne_peut_plus_s_auto_crediter():
    """LE test du lot. C'est la ligne `[OK] Verifier que les tests passent` du
    bilan qui doit disparaitre."""
    assert final_requires_operational_proof(DU_RUN) is True, (
        "les cinq bloqueurs laissent toujours passer : le FINAL va cocher la "
        "case sans preuve"
    )
    assert final_fulfills_task(DU_RUN) is False, (
        "la prose du FINAL accomplit toujours une tache d'execution"
    )


def test_le_blocage_passe_par_le_chemin_EXISTANT():
    """Le lot n'ajoute pas de sixieme bloqueur : il elargit un predicat, et le
    blocage descend par `tool_explicit_task_blocks`, deja cable."""
    assert tool_explicit_task_blocks("FINAL", DU_RUN) is True
    assert tool_explicit_task_blocks("text_inference", DU_RUN) is True
    assert tool_explicit_task_blocks("run_command", DU_RUN) is False, (
        "l'outil qui execute reellement est bloque : la tache devient "
        "impossible a cocher — c'est le defaut Z23"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. La tache reste COCHABLE — sinon on cree un blocage mortel (Z23)
# ══════════════════════════════════════════════════════════════════════════


def test_un_run_VERT_credite_la_tache():
    """Un garde qui rend une tache impossible a accomplir tue la mission au
    lieu de marquer un constat. Les deux moities du lot vont ensemble."""
    assert pytest_plan_task_proven(DU_RUN, "run_command", _verdict(green=True)) is True


def test_un_run_ROUGE_ne_credite_PAS_la_tache():
    """Le piege du lot.

    `requires_green` ne regardait que le mot « vert ». Elargir la
    reconnaissance sans elargir cette exigence rendrait le garde PLUS FAIBLE
    qu'avant : un run rouge crediterait « les tests passent ».
    """
    assert pytest_plan_task_proven(DU_RUN, "run_command", _verdict(green=False)) is False, (
        "un run ROUGE credite « les tests passent » — le garde est devenu plus "
        "faible qu'avant le lot"
    )


@pytest.mark.parametrize("desc", DU_CORPUS)
def test_aucune_formulation_du_corpus_ne_se_contente_d_un_run_rouge(desc):
    assert pytest_plan_task_proven(desc, "run_command", _verdict(green=False)) is False, desc


def test_une_commande_qui_n_a_rien_execute_ne_credite_pas():
    """Z39 — `exit:0` sur « commande introuvable ». Le verdict doit prouver
    qu'on a REELLEMENT fait tourner quelque chose."""
    assert pytest_plan_task_proven(DU_RUN, "run_command", _verdict(ran=False)) is False
    assert pytest_plan_task_proven(DU_RUN, "run_command", _verdict(is_test=False)) is False


def test_un_outil_qui_n_execute_rien_ne_credite_pas():
    assert pytest_plan_task_proven(DU_RUN, "text_inference", _verdict()) is False
    assert pytest_plan_task_proven(DU_RUN, "FINAL", _verdict()) is False


# ══════════════════════════════════════════════════════════════════════════
#  3. La lecon Z3b : aucun faux positif de vocabulaire
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("desc", [
    # Ecrire des tests n'est pas les executer — distinction deja portee par
    # la docstring d'origine de `pytest_execution_task`.
    "Ecrire les tests unitaires du module",
    "Rediger 4 tests pytest pour analyse.resume()",
    "Creer le fichier tests/test_app.py",
    # Le contre-exemple nomme par Z3b lui-meme.
    "Trier les resultats du benchmark",
    # Synthese pure : doit rester accomplissable par le FINAL.
    "Rediger la synthese finale",
    "Presenter les resultats a l'utilisateur",
    "Fournir le bilan final",
    # « passer » hors du domaine des tests.
    "Passer en revue la documentation",
    "Faire passer le message a l'equipe",
])
def test_aucun_faux_positif(desc):
    assert pytest_execution_task(desc) is False, (
        f"faux positif — Z3b : « on ne devine pas l'intention avec du "
        f"vocabulaire » : {desc!r}"
    )


@pytest.mark.parametrize("desc", [
    "Rediger la synthese finale",
    "Presenter les resultats a l'utilisateur",
    "Fournir le bilan final",
])
def test_les_taches_de_synthese_restent_accomplies_par_le_FINAL(desc):
    """Le garde-fou du lot : si tout devenait operationnel, le FINAL ne
    pourrait plus rien accomplir et le PLAN GUARD boucherait chaque run."""
    assert final_fulfills_task(desc) is True, desc


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("desc", [
    "Executer pytest sur le workspace",
    "Lancer pytest jusqu'au vert",
    "Faire tourner pytest",
    "Relancer pytest apres correction",
])
def test_les_reconnaissances_historiques_sont_intactes(desc):
    """Les 53 taches deja reconnues au corpus ne doivent pas bouger."""
    assert pytest_execution_task(desc) is True, desc


def test_le_vert_reste_exige_la_ou_il_l_etait():
    """« pytest jusqu'au vert » exigeait deja un run vert : inchange."""
    assert pytest_plan_task_proven(
        "Lancer pytest jusqu'au vert", "run_command", _verdict(green=False)
    ) is False


def test_une_execution_sans_exigence_d_issue_reste_permissive():
    """« Faire tourner pytest » ne demande pas le vert : un run rouge la
    credite toujours. Ce lot ne durcit QUE les intitules qui nomment l'issue."""
    assert pytest_plan_task_proven(
        "Faire tourner pytest", "run_command", _verdict(green=False)
    ) is True


def test_les_signatures_sont_inchangees():
    import inspect

    assert list(inspect.signature(pytest_execution_task).parameters) == ["description"]
    assert list(inspect.signature(pytest_plan_task_proven).parameters) == [
        "task_desc", "tool_name", "test_outcome",
    ]
