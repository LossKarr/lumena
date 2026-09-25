"""Lot CONN-7a - une erreur IDE dit sa cause et le geste qui repare.

Exigence de CONN-7 : « **diagnostic reparateur, jamais simple message mort** ».

--- Ce que l'audit du 23 septembre 2026 etablit ---

**62 codes d'erreur techniques remontent tels quels a l'utilisateur**, sous la forme
`IDE: ide_mission_workspace_mismatch`. Charles l'a vu dans ses propres journaux :
`IDE: ide_host_authorization_not_connected`.

Recherche faite dans les DEUX depots, Python et TypeScript : **aucune table de
traduction n'existe nulle part**. Le seul module qui pourrait y ressembler,
`src/utils/safe_errors.py`, ne contient qu'un resumeur d'exception generique.

Or ces codes ne sont pas opaques par nature - chacun a une cause CONNUE et un geste
PRECIS :

    ide_mission_workspace_mismatch      -> l'IDE n'est pas ouverte sur le bon dossier
    ide_host_authorization_not_connected -> cette action n'est pas permise en chat
    ide_owner_context_required          -> l'appel vient d'un contexte sans demandeur

L'utilisateur, lui, recoit un identifiant de code source.

--- La forme retenue ---

Le code technique est **conserve** : il est diagnosticable, et les journaux comme les
tests en dependent. Il est desormais SUIVI de sa cause et du geste.

Le gel d'exhaustivite est le coeur du lot : il parcourt les codes reellement leves
dans `src/reasoning/` et echoue si l'un n'est pas couvert. Un code ajoute demain
casse le test - meme motif que la liste exhaustive des champs de CONN-6d, qui a joue
des le lendemain.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.reasoning.ide_error_guidance import expliquer_erreur_ide, GUIDES_ERREURS_IDE

RACINE = Path(__file__).resolve().parents[2]


def _codes_leves_dans_le_source() -> set:
    """Les codes que `src/reasoning/` leve reellement, lus dans le source."""
    codes = set()
    motif = re.compile(r'(?:ExternalToolError|MissionScopeError)\(\s*"([a-z0-9_]+)"')
    for fichier in (RACINE / "src" / "reasoning").rglob("*.py"):
        for trouve in motif.finditer(fichier.read_text(encoding="utf-8", errors="replace")):
            if trouve.group(1).startswith(("ide_", "external_")):
                codes.add(trouve.group(1))
    return codes


# ── 1. Le gel qui fonde le lot ──────────────────────────────────────────────

def test_aucun_code_d_erreur_ne_reste_sans_explication():
    """Un code ajoute demain doit casser CE test, pas atterrir brut chez l'utilisateur."""
    manquants = sorted(_codes_leves_dans_le_source() - set(GUIDES_ERREURS_IDE))
    assert manquants == [], (
        f"{len(manquants)} code(s) sans cause ni geste : {manquants}"
    )


def test_le_source_leve_bien_au_moins_les_62_codes_audites():
    """Si ce compte s'effondre, c'est l'extraction qui est cassee, pas le code."""
    assert len(_codes_leves_dans_le_source()) >= 62


@pytest.mark.parametrize("code", sorted(GUIDES_ERREURS_IDE))
def test_chaque_guide_donne_une_cause_ET_un_geste(code):
    """Une cause sans geste reste un message mort : elle nomme le probleme sans issue."""
    cause, geste = GUIDES_ERREURS_IDE[code]
    assert len(cause.strip()) >= 15, f"{code} : cause trop courte"
    assert len(geste.strip()) >= 15, f"{code} : geste trop court"
    # Un guide qui recopie le code n'explique rien.
    assert code not in cause and code not in geste, f"{code} : le guide recopie le code"


# ── 2. Ce que voit l'utilisateur ────────────────────────────────────────────

def test_l_explication_conserve_le_code_technique():
    """Les journaux et les tests existants s'appuient dessus : il ne disparait pas."""
    texte = expliquer_erreur_ide("ide_mission_workspace_mismatch")
    assert texte.startswith("ide_mission_workspace_mismatch")


def test_l_explication_ajoute_la_cause_et_le_geste():
    texte = expliquer_erreur_ide("ide_host_authorization_not_connected")
    assert len(texte) > len("ide_host_authorization_not_connected") + 30
    cause, geste = GUIDES_ERREURS_IDE["ide_host_authorization_not_connected"]
    assert cause in texte and geste in texte


def test_un_code_inconnu_passe_sans_rien_inventer():
    """Mieux vaut le code nu qu'une explication fabriquee."""
    assert expliquer_erreur_ide("ide_code_jamais_vu") == "ide_code_jamais_vu"


@pytest.mark.parametrize("valeur", ["", "   ", None])
def test_une_entree_vide_ne_leve_pas(valeur):
    assert isinstance(expliquer_erreur_ide(valeur), str)


# ── 3. Le branchement au point de sortie UNIQUE ─────────────────────────────

def test_le_rail_explique_ses_refus():
    """Un seul point de sortie construit `IDE: ...` - le branchement y tient."""
    source = (RACINE / "src" / "reasoning" / "ide_tool_runtime.py").read_text(encoding="utf-8")
    assert "guidance=expliquer_erreur_ide" in source, (
        "le rail rend encore le code brut sans cause ni geste"
    )


def test_le_contenu_de_l_observation_reste_le_CODE():
    """`content` est un CONTRAT : 37 egalites strictes le figent, dont les gels CONN-5.

    Mesure du 23 septembre : enrichir `content` cassait **72 tests** d'un coup.
    L'explication voyage donc dans `guidance`, champ ADDITIF, comme `origin` avant
    elle. Ce test empeche de revenir a l'enrichissement par commodite.
    """
    source = (RACINE / "src" / "reasoning" / "ide_tool_runtime.py").read_text(encoding="utf-8")
    assert 'content=f"IDE: {exc}"' in source, (
        "le contenu du refus n'est plus le code seul : le contrat est rompu"
    )


def test_le_modele_recoit_bien_la_guidance():
    """Une explication que personne ne lit ne repare rien."""
    from src.reasoning.react_config import Observation, observation_lisible

    rendu = observation_lisible(Observation(content="IDE: code", guidance="cause. À faire : geste."))
    assert "cause" in rendu and "geste" in rendu and rendu.startswith("IDE: code")
    # Sans guidance, le rendu est le contenu, a l'identique.
    assert observation_lisible(Observation(content="rien")) == "rien"

    source = (RACINE / "src" / "reasoning" / "react.py").read_text(encoding="utf-8")
    assert "observation_lisible(step.observation)" in source, (
        "le ReAct n'utilise pas le rendu : l'explication existe sans lecteur"
    )


def test_react_py_n_a_PAS_grossi_pour_ce_lot():
    """Deux gels protegent la taille de `react.py`, dont un nomme `only_shrink`.

    Le rendu vit donc dans `react_config.py`, a cote du champ, et remplace une
    assignation EXISTANTE : zero ligne ajoutee. Premiere tentative : +3 lignes, deux
    gels tombes - la voie a ete refaite plutot que les gels releves.
    """
    source = (RACINE / "src" / "reasoning" / "react.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 9718
