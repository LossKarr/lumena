"""Lot L5-4a - le catalogue d'une mission n'est pas fige sur l'absence de son IDE.

**Defaut trouve par le canari reel du 23 septembre 2026, invisible aux 22 750 tests
verts de L5-3c.** Le run a prouve que la voie A fonctionne physiquement :
`missionInstanceExists: true`, `missionInstanceIsDistinct: true`,
`connectionCount: 2`, `ownerWindowUntouched: true` - deux IDE vivantes, isolees, la
fenetre de l'utilisateur intacte. Et pourtant :
`missionWroteWithoutNavigate: false`, `IDE: ide_mission_workspace_mismatch`.

--- La cause, mesuree ---

`ExternalToolRun.capture` (`external_tool_scope.py` l.18-23) memorise le premier
catalogue rendu pour une cle, pour toute la duree du tour. Or le rail interroge
`catalog(workspace=mission_root)` **avant** d'ouvrir l'instance : il capture donc
`launch_only`. Apres l'ouverture, la seconde interrogation relit ce cache et
continue de croire qu'aucune IDE n'existe sur ce dossier.

Le cache reste indispensable (CONN-3C : un catalogue stable pendant un tour). Ce
qui est faux, c'est de garder une entree que l'on vient soi-meme de rendre
obsolete. L'oubli est donc CIBLE sur le dossier de la mission ouverte : ni le
catalogue de la proprietaire, ni celui du chat ne sont touches.
"""
from __future__ import annotations

import pytest

from src.reasoning.external_tool_registry import ExternalProviderSnapshot, ExternalToolCatalog
from src.reasoning.external_tool_scope import (
    ExternalToolRun, external_tool_run, oublier_catalogue_externe, run_external_catalog,
)


def _catalogue(etat):
    return ExternalToolCatalog((ExternalProviderSnapshot("lumena.ide", etat, ()),))


def _etat(catalogue):
    return catalogue.providers[0].state


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

def test_une_cle_oubliee_est_recapturee():
    """Aujourd'hui le cache rend `launch_only` a jamais : rouge."""
    etats = iter(["launch_only", "ready"])
    fabrique = lambda: _catalogue(next(etats))  # noqa: E731
    cle = ("racine", "lumena.ide", "C:/mission")

    with external_tool_run(ExternalToolRun()):
        assert _etat(run_external_catalog(cle, fabrique)) == "launch_only"
        oublier_catalogue_externe(cle)
        assert _etat(run_external_catalog(cle, fabrique)) == "ready"


def test_l_oubli_ne_touche_que_la_cle_demandee():
    """Le catalogue de la proprietaire et celui du chat restent stables."""
    mission = ("racine", "lumena.ide", "C:/mission")
    proprietaire = ("racine", "lumena.ide")
    appels = {"mission": 0, "proprietaire": 0}

    def fabrique(nom):
        def interne():
            appels[nom] += 1
            return _catalogue("ready")
        return interne

    with external_tool_run(ExternalToolRun()):
        run_external_catalog(mission, fabrique("mission"))
        run_external_catalog(proprietaire, fabrique("proprietaire"))
        oublier_catalogue_externe(mission)
        run_external_catalog(mission, fabrique("mission"))
        run_external_catalog(proprietaire, fabrique("proprietaire"))

    assert appels == {"mission": 2, "proprietaire": 1}


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

def test_sans_oubli_le_catalogue_reste_stable_pendant_le_tour():
    """Invariant CONN-3C : une seule revision par tour."""
    appels = []

    def fabrique():
        appels.append(1)
        return _catalogue("ready")

    cle = ("racine", "lumena.ide")
    with external_tool_run(ExternalToolRun()):
        run_external_catalog(cle, fabrique)
        run_external_catalog(cle, fabrique)
    assert len(appels) == 1


@pytest.mark.parametrize("cle", [("racine", "lumena.ide"), ("inconnue",)])
def test_oublier_une_cle_absente_ne_leve_pas(cle):
    with external_tool_run(ExternalToolRun()):
        oublier_catalogue_externe(cle)


def test_oublier_hors_de_tout_run_ne_leve_pas():
    """Le chat hors run n'a pas de cache : l'oubli est un non-evenement."""
    oublier_catalogue_externe(("racine", "lumena.ide", "C:/mission"))
