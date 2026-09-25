"""Lot IDE-6 - un catalogue PERIME n'est pas un catalogue stable.

--- Ce que le run du 24/09 a 20 h 04 a mesure ---

Charles : « prouve moi que tu sais l'utiliser ». **32 iterations, 3 min 58, et
28 refus `ide_snapshot_stale`** sur TOUS les outils de l'IDE : `get_status`,
`list_files`, `git_status`, `open_file`, `get_state`, `command_palette_show`.

Le pont allait bien. `lumena_ide(status)` repondait, a 20 h 06 50, apres une instance
neuve : `connected=True, handshake=True, authenticated=True`. Ce n'est pas l'IDE qui
etait cassee - c'est la PHOTO du catalogue qui etait morte, et que rien ne reprenait.

Sequence exacte :

    20:04:51  lumena_ide(ensure_workspace)  -> reussi, la session change
    20:05:03  ide__git_status               -> ide_snapshot_stale
    ... 27 iterations mortes ...
    20:08:04  ide__get_state                -> ide_snapshot_stale

--- La cause, mesuree au code ---

`ExternalToolRun.capture` memorise le catalogue d'une cle pour toute la duree du tour
(invariant CONN-3C : une seule revision par tour). `is_current()` compare la session
**par identite d'objet** (`ide_capabilities.py` l.112). Quand `lumena_ide` change la
session, le snapshot en cache devient perime - et **rien ne le jette**.

L'invalidation EXISTE pourtant : `oublier_catalogue_externe`, ecrite au lot L5-4a. Elle
n'est appelee qu'a UN endroit, avec `scope.mission_root` (`ide_tool_runtime.py` l.142).
Cote chat, la cle est `(lumena_root, "lumena.ide")` et personne ne l'oublie jamais.
**C'est `lumena_ide` lui-meme qui empoisonnait le tour** : il change la session sans
jeter le catalogue.

--- Le choix de conception ---

On ne pose pas un oubli dans `lumena_ide`, qui ne fermerait que la cause connue. On
traite le FAIT : quand le catalogue en main est perime, on le jette et on le reprend
**une seule fois**. CONN-3C est respecte - le catalogue reste stable tant qu'il est
VALIDE ; ce qu'on refuse, c'est de servir une photo morte jusqu'au bout du tour. Un
seul renouvellement : si la seconde photo est encore perimee, l'IDE bouge vraiment
sous nos pieds et le refus est legitime.

L'ancrage ne change pas : meme cle, donc meme workspace. Les bornes de L5-2 et L5-3c
(une mission ne recoit jamais la fenetre de l'utilisateur) sont intactes.
"""
from __future__ import annotations

import pytest

from src.reasoning.external_tool_scope import ExternalToolRun, external_tool_run
from src.reasoning.ide_tool_runtime import RegistryIDEProvider
from src.reasoning.external_tool_registry import ExternalToolError
from src.tools.ide_protocol import negotiate
from src.tools.ide_semantics import audited_ide_commands
from tests.tools.test_conn2b_ide_protocol import hello
from tests.tools.test_conn3b_ide_semantics import command
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401


class _RegistreMinimal:
    """Le provider ne lit que `lumena_root` pour sa cle de catalogue."""

    def __init__(self, racine):
        self.lumena_root = racine


@pytest.fixture
def provider(service, tmp_path):
    p = RegistryIDEProvider(_RegistreMinimal(tmp_path / "lumena"))
    p._service = service          # le vrai service, avec un pont de test
    return p


def _changer_de_session(service):
    """Ce que fait `lumena_ide(ensure_workspace)` : une NOUVELLE session negociee.

    L'objet change, donc `is_current` - qui compare par identite - rend False. C'est
    exactement l'etat du run a 20 h 05.
    """
    message = hello([command(name) for name in audited_ide_commands()])
    service.bridge._negotiated, _ = negotiate(message, message["session_id"])
    return service.bridge._negotiated


def _un_outil(service):
    return next(t.name for t in service.capture().tools)


# -- 1. Le fait qui fonde le lot ---------------------------------------------

def test_apres_un_changement_de_session_le_catalogue_se_reprend(provider, service):
    """Le mur du run : aujourd'hui, `prepare` leve `ide_snapshot_stale` - rouge."""
    outil = _un_outil(service)
    with external_tool_run(ExternalToolRun()):
        provider.catalog()                       # photo prise avec la session S1
        nouvelle = _changer_de_session(service)  # lumena_ide -> S2

        snapshot, prepared = provider._resoudre_frais(outil, {})

    assert prepared is not None
    assert snapshot.binding.session is nouvelle, "le snapshot decrit encore S1"


def test_le_renouvellement_est_UNIQUE(provider, service, monkeypatch):
    """Pas de boucle : une photo toujours perimee au second tir doit refuser.

    Sinon un pont qui renegocie en continu ferait tourner le rail indefiniment.
    """
    outil = _un_outil(service)
    appels = {"n": 0}

    def prepare_toujours_perime(snapshot, name, parameters):
        appels["n"] += 1
        raise ExternalToolError("ide_snapshot_stale")

    monkeypatch.setattr(service, "prepare", prepare_toujours_perime)
    with external_tool_run(ExternalToolRun()):
        provider.catalog()
        with pytest.raises(ExternalToolError, match="ide_snapshot_stale"):
            provider._resoudre_frais(outil, {})

    assert appels["n"] == 2, "le renouvellement doit etre unique"


def test_le_chemin_reel_passe_par_le_renouvellement(provider, service, monkeypatch):
    """Un correctif qu'`execute` n'emprunte pas ne repare rien.

    On compte les appels a `prepare` en faisant echouer le premier : deux appels
    prouvent que le renouvellement est bien sur le chemin de `_resoudre_frais`, celui
    qu'`execute` utilise.
    """
    outil = _un_outil(service)
    etats = {"n": 0}
    vrai = service.prepare

    def prepare_perime_une_fois(snapshot, name, parameters):
        etats["n"] += 1
        if etats["n"] == 1:
            raise ExternalToolError("ide_snapshot_stale")
        return vrai(snapshot, name, parameters)

    monkeypatch.setattr(service, "prepare", prepare_perime_une_fois)
    with external_tool_run(ExternalToolRun()):
        provider.catalog()
        snapshot, prepared = provider._resoudre_frais(outil, {})

    assert etats["n"] == 2
    assert prepared is not None


# -- 2. Ce que le lot ne doit PAS changer ------------------------------------

def test_un_catalogue_VALIDE_reste_stable_pendant_le_tour(provider, service):
    """Invariant CONN-3C : sans peremption, une seule capture par tour."""
    outil = _un_outil(service)
    captures = {"n": 0}
    vrai_capture = provider._capture

    def compter(workspace=None):
        captures["n"] += 1
        return vrai_capture(workspace)

    provider._capture = compter
    with external_tool_run(ExternalToolRun()):
        provider._resoudre_frais(outil, {})
        provider._resoudre_frais(outil, {})

    assert captures["n"] == 1, "un catalogue sain a ete recapture sans raison"


def test_une_autre_erreur_n_est_pas_avalee(provider, service, monkeypatch):
    """Seule la peremption declenche le renouvellement : un nom inconnu doit garder
    son vrai motif, sinon on masquerait un defaut derriere une reprise inutile."""
    outil = _un_outil(service)

    def prepare_autre_erreur(snapshot, name, parameters):
        raise ExternalToolError("external_tool_not_in_snapshot")

    monkeypatch.setattr(service, "prepare", prepare_autre_erreur)
    with external_tool_run(ExternalToolRun()):
        provider.catalog()
        with pytest.raises(ExternalToolError, match="external_tool_not_in_snapshot"):
            provider._resoudre_frais(outil, {})


def test_hors_run_le_renouvellement_est_un_non_evenement(provider, service):
    """Sans scope de run, il n'y a pas de cache : rien a oublier, rien a casser."""
    outil = _un_outil(service)
    snapshot, prepared = provider._resoudre_frais(outil, {})
    assert prepared is not None and snapshot.state == "ready"


# -- 3. IDE-8 - un conseil qui envoie dans le mur coute plus cher que rien ---
#
# Ma guidance d'IDE-1 disait : « redemande l'etat (`ide__get_status`) ». Or
# `ide__get_status` est LUI-MEME derriere le snapshot : il levait le meme refus.
#
# Le run l'a prouve a la lettre. Lumena a lu le conseil, l'a compris — « le message
# est enfin explicite [...] la consigne est claire : ne pas relancer, mais redemander
# l'etat » — l'a applique, et a recu `ide_snapshot_stale` une fois de plus. Puis :
# « j'ai fait exactement ce que le message demandait et c'est ENCORE stale. »

def test_le_geste_ne_renvoie_pas_vers_un_outil_qui_depend_du_snapshot():
    """Le gel du lot. TOUS les outils `ide__*` passent par `prepare`, donc par
    `is_current`. En nommer un dans le geste d'une erreur de snapshot, c'est envoyer
    le modele sur la porte qui vient de se fermer."""
    from src.reasoning.ide_error_guidance import GUIDES_ERREURS_IDE

    _cause, geste = GUIDES_ERREURS_IDE["ide_snapshot_stale"]
    assert "ide__" not in geste, (
        f"le geste nomme un outil snapshot-gated, donc inatteignable : {geste!r}"
    )


def test_le_geste_nomme_la_voie_qui_NE_depend_pas_du_snapshot():
    """`lumena_ide` est un handler natif : il ne passe ni par le catalogue externe ni
    par `is_current`. C'est la seule facon de regarder l'IDE quand la photo est morte —
    et c'est ce que Lumena a fini par trouver seule, apres 28 refus."""
    from src.reasoning.ide_error_guidance import GUIDES_ERREURS_IDE

    _cause, geste = GUIDES_ERREURS_IDE["ide_snapshot_stale"]
    assert "lumena_ide" in geste, geste


def test_la_cause_dit_que_la_reprise_a_eu_lieu():
    """Sans cela, le modele croit encore devoir agir sur la photo — et la seule action
    qu'il connaisse est de relancer, ce que le geste lui interdit par ailleurs."""
    from src.reasoning.ide_error_guidance import GUIDES_ERREURS_IDE

    cause, geste = GUIDES_ERREURS_IDE["ide_snapshot_stale"]
    assert "reprise" in cause.lower() or "renouvel" in (cause + geste).lower(), cause
