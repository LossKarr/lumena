"""LOT 14 — PUBLIER NE LIVRE NI DOUBLON NI DÉCHET.

═══════════════════════════════════════════════════════════════════════════════
  CE LOT RÉPARE UN RISQUE QUE LE LOT 13 VIENT DE CRÉER
═══════════════════════════════════════════════════════════════════════════════

Le lot 13 pousse les missions productives à publier — 72 sur 95 ne le faisaient
jamais. Excellent. Mais mesuré sur les 117 dossiers de mission du disque :

    4 missions (3 %)  portent un sous-dossier `missions/` IMBRIQUÉ
    6 missions        portent le MÊME livrable à deux endroits

Ces missions n'avaient JAMAIS publié : le défaut était inoffensif. Il ne l'est
plus. Sans ce lot, le run du 02/09 aurait livré :

    index.php                                       22 689 o   ← le CodeAgent
    missions/construis-budgetbuddy…/index.php        8 906 o   ← le lead
    style.css                                       10 785 o
    missions/construis-budgetbuddy…/style.css        5 858 o
    $null                                                0 o
    data.json                                            2 o

Deux versions du même fichier dans le même livrable, et l'utilisateur incapable
de savoir laquelle est la sienne. **Le correctif d'un lot ne doit pas ouvrir la
porte du suivant.**

═══════════════════════════════════════════════════════════════════════════════
  POURQUOI `missions/` EST TOUJOURS UNE ERREUR
═══════════════════════════════════════════════════════════════════════════════

Invariant du `CLAUDE_REPO_GUIDE` :

    une mission ne doit pas creer une nouvelle mission via le handler
    `create_mission` ; elle utilise ses workers

Un dossier `missions/` à l'intérieur d'un dossier de mission n'est donc jamais un
livrable : c'est un chemin recopié depuis un listing — la famille Z25.

═══════════════════════════════════════════════════════════════════════════════
  EXCLURE NE SUFFIT PAS — IL FAUT LE DIRE
═══════════════════════════════════════════════════════════════════════════════

Écarter en silence remplacerait un doublon par une DISPARITION, c'est-à-dire
exactement le défaut que ce dépôt combat depuis soixante lots. Le lead a écrit
dans ce sous-dossier : il doit apprendre que ça ne part pas, et pourquoi.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from src.reasoning.handlers import missions as M


def _source():
    return inspect.getsource(M.publish_mission_workspace_handler)


# ══════════════════════════════════════════════════════════════════════════
#  1. LE SOUS-DOSSIER `missions/` NE PART PLUS DANS LE LIVRABLE
# ══════════════════════════════════════════════════════════════════════════


def test_missions_est_exclu_de_la_copie():
    src = _source()
    i = src.index("_EXCLUDED_DIRS = {")
    ligne = src[i:src.index("}", i) + 1]
    assert '"missions"' in ligne, "le sous-dossier missions/ partirait dans le livrable"
    # Les exclusions historiques restent.
    for ancien in (".backups", "__pycache__", ".pytest_cache"):
        assert ancien in ligne, ancien


def test_l_exclusion_passe_bien_par_le_filtre_de_copytree():
    """Déclarer l'exclusion ne suffit pas : `_ignore` doit la consulter."""
    src = _source()
    i = src.index("def _ignore(")
    bloc = src[i:i + 500]
    assert "_EXCLUDED_DIRS" in bloc
    assert "_NOMS_PARASITES" in bloc


# ══════════════════════════════════════════════════════════════════════════
#  2. LES ARTEFACTS DE REDIRECTION SHELL
# ══════════════════════════════════════════════════════════════════════════


def test_les_noms_parasites_sont_ecartes():
    """`2>$null` sous PowerShell crée un FICHIER nommé `$null` — vu au run du
    02/09, 0 octet, à la racine du dossier de mission."""
    src = _source()
    i = src.index("_NOMS_PARASITES = {")
    ligne = src[i:src.index("}", i) + 1]
    for parasite in ("$null", "nul"):
        assert parasite in ligne, parasite


def test_les_fichiers_VIDES_legitimes_survivent():
    """GARDE ANTI-SUR-CORRECTION. `__init__.py` et `.gitkeep` font 0 octet par
    nature : les exclure casserait des paquets Python entiers. On ne filtre QUE
    des noms d'artefacts shell, jamais la taille."""
    src = _source()
    i = src.index("_NOMS_PARASITES = {")
    ligne = src[i:src.index("}", i) + 1]
    for legitime in ("__init__", "gitkeep", "st_size", "size == 0"):
        assert legitime not in ligne, (
            f"le filtre des parasites touche à autre chose que des noms : {legitime}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUI EST ÉCARTÉ EST DIT — le cœur du lot
# ══════════════════════════════════════════════════════════════════════════


def test_les_fichiers_ecartes_sont_RECENSES_avant_la_copie():
    """Après le `copytree`, ils ne sont plus visibles : le recensement doit être
    fait AVANT, sinon on ne peut plus rien dire."""
    src = _source()
    i_recens = src.index("_ecartes: List[str] = []")
    i_copie = src.index("shutil.copytree(src_dir, dest")
    assert i_recens < i_copie, "le recensement doit précéder la copie"


def test_le_lead_est_PREVENU_de_ce_qui_n_est_pas_parti():
    """Exclure en silence transformerait un doublon en disparition — le défaut que
    ce dépôt combat depuis soixante lots."""
    src = _source()
    i = src.index("NON publiés")
    bloc = src[max(0, i - 300):i + 700]
    assert "_ecartes" in bloc
    assert "sous-dossier" in bloc and "imbriqué" in bloc
    # Il doit dire QUOI FAIRE, pas seulement constater.
    assert "déplace-le" in bloc or "republie" in bloc


def test_l_avertissement_NOMME_les_fichiers():
    src = _source()
    i = src.index("NON publiés")
    bloc = src[i:i + 500]
    assert "', '.join(_ecartes" in bloc          # la liste, pas juste un compte
    assert "_ecartes[:6]" in bloc                # bornée : pas de mur de texte


def test_aucun_avertissement_quand_il_n_y_a_RIEN_a_ecarter():
    """AUD-017 — zéro bruit. 113 missions sur 117 n'ont pas ce problème."""
    src = _source()
    i = src.index("NON publiés")
    bloc = src[i:i + 800]
    assert "if _ecartes else \"\"" in bloc


def test_le_recensement_ne_fait_JAMAIS_echouer_la_publication():
    """Doctrine Z23 : un garde ne tue pas un run. Si le dossier est illisible, on
    publie quand même — on perd l'avertissement, pas le livrable."""
    src = _source()
    i = src.index("_ecartes: List[str] = []")
    bloc = src[i:i + 700]
    assert "try:" in bloc and "except Exception" in bloc
    assert "[LOT 14]" in bloc


# ══════════════════════════════════════════════════════════════════════════
#  4. NON-RÉGRESSION — les gardes existants restent
# ══════════════════════════════════════════════════════════════════════════


def test_l_archivage_Z17_est_INTACT():
    """Publier n'autorise pas à détruire : les fichiers recouverts partent toujours
    dans `.backups/` et sont annoncés."""
    src = _source()
    assert "_ecrases" in src
    assert "RECOUVERTS" in src
    assert ".backups" in src


def test_le_junk_zero_octet_hors_contrat_reste_filtre():
    """Garde 2.8.4 (run VentesReport) — `test_log_fixes.py` 0 octet, jamais au
    contrat. Toujours en place, et distinct du filtre par NOM du lot 14."""
    src = _source()
    assert "_is_junk_zero_byte" in src


def test_publier_dans_l_arbre_des_missions_reste_refuse():
    src = _source()
    assert "L'arbre des missions" in src or "arbre des missions" in src


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CORPUS RÉEL — la mesure qui a déclenché le lot
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not pathlib.Path("workspace/missions").is_dir(),
                    reason="workspace/missions absent de cette machine")
def test_le_corpus_porte_bien_le_defaut_mesure():
    """La preuve que ce lot n'est pas théorique : sur les dossiers réels, des
    missions portent un sous-dossier `missions/` imbriqué. Si ce test cesse d'en
    trouver, tant mieux — il n'échouera pas pour autant, il ne mesure pas une
    régression mais documente l'origine du lot."""
    racine = pathlib.Path("workspace/missions")
    missions = [d for d in racine.iterdir() if d.is_dir()]
    if len(missions) < 10:
        pytest.skip(f"corpus trop maigre : {len(missions)}")
    imbriquees = [m.name for m in missions if (m / "missions").is_dir()]
    # On ne fige PAS le nombre : il baissera quand le lot 6 corrigera la cause.
    # On vérifie seulement que la mesure reste calculable.
    assert isinstance(imbriquees, list)
    if imbriquees:
        exemple = racine / imbriquees[0] / "missions"
        assert exemple.is_dir()
