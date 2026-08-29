"""2026-08-29 — la liste d'idempotence du LOT 2.7 est tenue A LA MAIN.

`apply_mission_truth_lock` sort immediatement si le texte porte deja une
banniere : sans ca, le verrou relirait un texte ecrit par un garde. C'est
indispensable, parce que les bannieres contiennent le vocabulaire que les
detecteurs cherchent — mesure : **11 bannieres, 29 detecteurs, 32 collisions**.
La banniere « Non publie » contient « publie » ; « DOM non observe » contient
« apparait / s'affiche » ; « Interaction NON prouvee » contient « score
augmente ».

Le garde 2.7 les neutralise TOUTES — mais par une liste de chaines ecrites a la
main, et cette liste avait DERIVE : `_DOCUMENT_RIGHTS_UNKNOWN_BANNER`, posee
plus tot sur le meme `action.answer` (react.py:5887), n'y figurait pas.

⚠️ HONNETETE SUR LA PORTEE : trou LATENT. Mesure sur les 592 finals reels du
corpus : **0 occurrence** de la banniere droits. Ce n'est pas un bug observe,
c'est une derive de maintenance — corrigee, et desormais sous garde.

Le test qui compte ici est le STRUCTUREL : toute banniere ajoutee demain qui
n'entre pas dans la liste fait rougir la suite. C'est ce qui empeche la classe
de se rouvrir.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import src.reasoning.final_guards as G

RACINE = pathlib.Path(__file__).resolve().parents[2]
SOURCE = RACINE / "src" / "reasoning" / "final_guards.py"


def _motifs_du_garde() -> list:
    """Les chaines comparees a `final_text` dans le bloc d'idempotence 2.7."""
    src = SOURCE.read_text(encoding="utf-8")
    debut = src.index("LOT 2.7 — IDEMPOTENCE")
    fin = src.index("already_locked", debut)
    return re.findall(r'"([^"]{8,})" in final_text', src[debut:fin])


def _bannieres_constantes() -> dict:
    return {n: v for n, v in vars(G).items()
            if n.endswith("_BANNER") and isinstance(v, str) and v.strip()}


def _bannieres_dynamiques() -> dict:
    return {
        "_unpublished_writes_banner": G._unpublished_writes_banner(["a.py"]),
        "_honest_test_status_line(vide)": G._honest_test_status_line(None),
        "_honest_test_status_line(rouge)": G._honest_test_status_line(
            {"is_test_cmd": True, "passed": 3, "failed": 2, "errors": 0}),
        "_honest_test_status_line(collecte)": G._honest_test_status_line(
            {"is_test_cmd": True, "collection_error": True}),
    }


# ══════════════════════════════════════════════════════════════════════════
#  1. LE GARDE STRUCTUREL — aucune banniere ne peut echapper a la liste
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(_bannieres_constantes()))
def test_chaque_banniere_est_couverte_par_le_garde_d_idempotence(nom):
    """Une banniere ajoutee demain SANS entree dans la liste 2.7 fait rougir ici.

    C'est le seul test qui empeche la derive de se reproduire : le correctif de
    ce jour ferme UN cas, celui-ci ferme la CLASSE.
    """
    banniere = _bannieres_constantes()[nom]
    motifs = _motifs_du_garde()
    assert any(m in banniere for m in motifs), (
        f"{nom} n'est couverte par AUCUNE entree du garde d'idempotence 2.7 : "
        f"le verrou relira un texte qu'un garde a ecrit. Ajoute une chaine "
        f"distinctive de cette banniere dans le bloc « LOT 2.7 — IDEMPOTENCE »."
    )


@pytest.mark.parametrize("nom", sorted(_bannieres_dynamiques()))
def test_chaque_banniere_DYNAMIQUE_est_couverte(nom):
    """Les deux bannieres construites a partir de donnees comptent aussi."""
    banniere = _bannieres_dynamiques()[nom]
    motifs = _motifs_du_garde()
    assert any(m in banniere for m in motifs), f"{nom} non couverte"


def test_le_garde_couvre_bien_TOUTES_les_bannieres_du_module():
    """Compte global : aucune banniere orpheline, aucun motif mort."""
    motifs = _motifs_du_garde()
    toutes = list(_bannieres_constantes().values()) + list(_bannieres_dynamiques().values())
    orphelins = [m for m in motifs if not any(m in b for b in toutes)]
    assert not orphelins, (
        f"motifs du garde 2.7 ne correspondant a AUCUNE banniere existante "
        f"(banniere renommee ou supprimee ?) : {orphelins}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. Le trou comble — la banniere des droits
# ══════════════════════════════════════════════════════════════════════════


def test_la_banniere_des_droits_sort_desormais_en_already_locked():
    texte, info = G.apply_mission_truth_lock(
        G._DOCUMENT_RIGHTS_UNKNOWN_BANNER + "\n\nLe document est livre.",
        has_green_test=False,
    )
    assert info.get("already_locked") is True
    assert info.get("changed") is False


# ══════════════════════════════════════════════════════════════════════════
#  3. Non-regression : l'idempotence tient sur TOUTES les bannieres
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(_bannieres_constantes()))
def test_un_texte_deja_bannerise_ressort_INTACT(nom):
    """Le verrou ne doit ni rebannieriser, ni reecrire un texte deja verrouille."""
    entree = _bannieres_constantes()[nom] + "\n\nLe livrable est produit."
    sortie, info = G.apply_mission_truth_lock(entree, has_green_test=False)
    assert sortie == entree, f"{nom} : le texte a ete modifie une 2e fois"
    assert info.get("already_locked") is True


def test_un_final_ORDINAIRE_n_est_pas_pris_pour_deja_verrouille():
    """Innocuite : sans banniere, le verrou travaille normalement."""
    _, info = G.apply_mission_truth_lock(
        "Le livrable est produit et publie.", has_green_test=True,
    )
    assert info.get("already_locked") is not True


def test_un_texte_vide_reste_inerte():
    sortie, info = G.apply_mission_truth_lock("", has_green_test=False)
    assert sortie == "" and info.get("changed") is False
