"""LOT Z8 — le fait n'est plus perdu avant la décision, il est perdu après l'action.

Run « Tanière » (2026-08-15). Z7 a fonctionné exactement comme prévu : la mission
a lu « `index.html` n'a que 4/8 de ses classes stylées », a décidé (« je dois
corriger le style manquant de index.html »), a délégué au CodeAgent, et celui-ci
a corrigé — `edit_lines styles.css`, les 4 classes ajoutées. Puis :

    missions/task_42796022…/styles.css  → 6 occurrences des classes corrigées ✅
    workspace/taniere/styles.css        → 0                                   ❌

La correction est arrivée APRÈS `publish_mission_workspace`, et personne ne
republie. Le travail est fait, il est juste, il est sur le disque — et
l'utilisateur reçoit la version d'avant.

L'historique n'en montrait qu'un cas sur cinq publications. Ce n'est pas ce qui
justifie le lot : c'est que **Z7 vient de créer ce chemin**. Tant que personne ne
corrigeait après publication, le défaut restait théorique ; maintenant que la
mesure déclenche des corrections tardives, il devient le mode normal.
"""

import os
from datetime import datetime
from pathlib import Path

import pytest

from src.subagents.style_coverage import (
    _DOSSIERS_TECHNIQUES,
    publication_perimee,
    publication_perimee_note,
)

_AVANT = "2026-08-15T13:59:20+00:00"
# Dérivé de `_AVANT`, jamais figé : un timestamp écrit en dur se décale d'un an
# sans que rien ne le signale (attrapé en écrivant ces tests).
_SEUIL = datetime.fromisoformat(_AVANT).timestamp()


def _mission(tmp_path: Path, avant: dict, apres: dict = None) -> Path:
    """Un dossier de mission : `avant` publié, `apres` modifié depuis."""
    for decalage, groupe in ((-60, avant), (+180, apres)):
        for nom, contenu in (groupe or {}).items():
            f = tmp_path / nom
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(contenu, encoding="utf-8")
            os.utime(f, (_SEUIL + decalage, _SEUIL + decalage))
    return tmp_path


# ── Le cas Tanière ───────────────────────────────────────────────────────────


def test_le_fichier_corrige_apres_publication_est_signale(tmp_path):
    _mission(tmp_path, {"index.html": "x"}, {"styles.css": ".taniere-bouton{}"})
    assert publication_perimee(tmp_path, _AVANT) == ["styles.css"]


def test_la_note_dit_quoi_faire_et_pas_seulement_quoi_constater(tmp_path):
    """Republier coûte UN appel d'outil : le constat reste actionnable même tard."""
    _mission(tmp_path, {"index.html": "x"}, {"styles.css": "y"})
    note = publication_perimee_note(tmp_path, _AVANT)
    assert "publish_mission_workspace" in note
    assert "`styles.css`" in note


def test_la_note_explique_lenjeu_reel(tmp_path):
    """Le risque n'est pas technique : c'est que l'utilisateur reçoive l'ancien."""
    _mission(tmp_path, {"a.html": "x"}, {"styles.css": "y"})
    assert "version d'avant" in publication_perimee_note(tmp_path, _AVANT)


def test_rien_na_bouge_rien_nest_dit(tmp_path):
    _mission(tmp_path, {"index.html": "x", "styles.css": "y"})
    assert publication_perimee(tmp_path, _AVANT) == []
    assert publication_perimee_note(tmp_path, _AVANT) == ""


def test_plusieurs_fichiers_sont_tous_listes_et_tries(tmp_path):
    _mission(tmp_path, {"a.html": "x"}, {"z.css": "1", "b.js": "2", "m.html": "3"})
    assert publication_perimee(tmp_path, _AVANT) == ["b.js", "m.html", "z.css"]


def test_laccord_du_verbe_suit_le_nombre(tmp_path):
    """Un message d'agent mal accordé se lit mal — et sera relu par un humain."""
    _mission(tmp_path, {"a.html": "x"}, {"styles.css": "y"})
    assert " a changé depuis" in publication_perimee_note(tmp_path, _AVANT)
    _mission(tmp_path, {}, {"autre.css": "z"})
    assert " ont changé depuis" in publication_perimee_note(tmp_path, _AVANT)


def test_au_dela_de_six_fichiers_le_reste_est_compte(tmp_path):
    _mission(tmp_path, {"a.html": "x"}, {f"f{i}.css": "y" for i in range(9)})
    assert "(+3)" in publication_perimee_note(tmp_path, _AVANT)


# ── Le bruit interne ne doit pas noyer le vrai fichier ───────────────────────


def test_les_sauvegardes_internes_sont_ignorees(tmp_path):
    """`.backups/styles.css.20260815_160229` observé sur Tanière : du bruit."""
    _mission(tmp_path, {"a.html": "x"}, {".backups/styles.css.2026": "vieux", "styles.css": "neuf"})
    assert publication_perimee(tmp_path, _AVANT) == ["styles.css"]


@pytest.mark.parametrize("dossier", sorted(_DOSSIERS_TECHNIQUES))
def test_aucun_dossier_technique_nest_signale(tmp_path, dossier):
    _mission(tmp_path, {"a.html": "x"}, {f"{dossier}/truc.txt": "y"})
    assert publication_perimee(tmp_path, _AVANT) == []


def test_les_fichiers_caches_sont_ignores(tmp_path):
    _mission(tmp_path, {"a.html": "x"}, {".env.local": "SECRET=1"})
    assert publication_perimee(tmp_path, _AVANT) == []


# ── Inertie : hors du cas, rien ne change ────────────────────────────────────


def test_sans_date_de_publication_rien_nest_dit(tmp_path):
    """Une mission qui n'a jamais publié n'a pas de livrable à rafraîchir."""
    _mission(tmp_path, {}, {"styles.css": "y"})
    assert publication_perimee(tmp_path, None) == []
    assert publication_perimee(tmp_path, "") == []


def test_une_date_illisible_ne_fait_pas_tomber(tmp_path):
    _mission(tmp_path, {}, {"styles.css": "y"})
    assert publication_perimee(tmp_path, "jamais") == []
    assert publication_perimee(tmp_path, 12345) == []


def test_le_suffixe_zulu_est_accepte(tmp_path):
    """`published_at` est écrit en ISO-8601 UTC — parfois avec `Z`."""
    _mission(tmp_path, {"a.html": "x"}, {"styles.css": "y"})
    assert publication_perimee(tmp_path, "2026-08-15T13:59:20Z") == ["styles.css"]


def test_une_racine_vide_ne_declenche_aucun_scan(tmp_path):
    """`Path("")` vaut `Path(".")` : défaut déjà attrapé deux fois, verrouillé."""
    assert publication_perimee("", _AVANT) == []
    assert publication_perimee(None, _AVANT) == []


def test_un_dossier_inexistant_ne_leve_pas(tmp_path):
    assert publication_perimee(tmp_path / "absent", _AVANT) == []


def test_un_fichier_pile_a_lheure_de_publication_nest_pas_perime(tmp_path):
    """Frontière stricte : `>` et non `>=`, sinon toute publication se
    signalerait elle-même comme périmée."""
    f = tmp_path / "styles.css"
    f.write_text("x", encoding="utf-8")
    os.utime(f, (_SEUIL, _SEUIL))
    assert publication_perimee(tmp_path, _AVANT) == []


# ── Le branchement dans delegate_task ────────────────────────────────────────


def test_delegate_task_porte_la_verification():
    """Le point d'injection : la correction vient d'atterrir, c'est là qu'il faut
    le dire — pas à la clôture, où la publication serait déjà oubliée."""
    src = Path("src/reasoning/handlers/agents.py").read_text(encoding="utf-8")
    assert "publication_perimee_note" in src
    assert "_mission_publication_meta" in src


def test_la_verification_ne_peut_pas_casser_la_delegation():
    """Une note d'appoint ne doit jamais faire échouer un CodeAgent qui a réussi."""
    src = Path("src/reasoning/handlers/agents.py").read_text(encoding="utf-8")
    i = src.index("publication_perimee_note")
    autour = src[i - 500 : i + 300]
    assert "try:" in autour and "except Exception" in autour


def test_la_meta_exige_une_publication_reelle():
    """Sans `mission_published` ET `published_at`, la question ne se pose pas."""
    src = Path("src/reasoning/handlers/agents.py").read_text(encoding="utf-8")
    i = src.index("def _mission_publication_meta")
    corps = src[i : i + 1400]
    assert 'meta.get("mission_published")' in corps
    assert 'meta.get("published_at")' in corps
    assert "is_mission_run" in corps
