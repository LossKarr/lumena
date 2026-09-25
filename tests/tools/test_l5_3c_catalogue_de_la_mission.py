"""Lot L5-3c-3 - le catalogue d'une mission decrit SON instance, jamais la proprietaire.

Dernier maillon de la voie A. L5-3c-1 sait DESIGNER l'instance d'un dossier ;
L5-3c-2 la fait OBSERVER par le lanceur. Mais `IDECapabilityService` reste ancre
sur `catalogue_snapshot()` en trois points (`ide_capabilities.py` l.62, 79, 91),
dont la docstring dit « decrit la PROPRIETAIRE ».

Consequence mesuree : meme avec son instance lancee et vivante, une mission recoit
le catalogue de la fenetre de l'utilisateur. `expected_session(...).workspace_path`
vaut alors le projet de Charles, et `authorize_mission_call` refuse par
`ide_mission_workspace_mismatch` - le refus meme que L5-3b voulait lever.

--- La regle qui ne se negocie pas ---

Sans instance sur le dossier de mission, le service rend `launch_only`. Il ne
retombe JAMAIS sur la proprietaire : ce serait rendre a la mission la fenetre de
l'utilisateur, ce que L5-2 interdit. Une mission qui n'a pas son IDE echoue
proprement.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.tools.ide_protocol import negotiate
from src.tools.ide_semantics import audited_ide_commands
from tests.tools.test_conn2b_ide_protocol import hello
from tests.tools.test_conn3b_ide_semantics import command
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401


def _instance_de_mission(service, dossier, *, instance="0a" * 16):
    """Une seconde IDE, appairee, ouverte sur `dossier`, dans le registre du pont."""
    from src.tools.ide_bridge import IDEConnection

    message = hello([command(name) for name in audited_ide_commands()])
    message["ide"]["instance_id"] = instance
    message["workspace"] = {"id": "0b" * 32, "path": str(dossier)}
    session, _ = negotiate(message, message["session_id"])

    connexion = IDEConnection()
    connexion._negotiated = session
    connexion._session = service.bridge._session
    connexion._ws = object()
    connexion._connected = True
    service.bridge._connexions[instance] = connexion
    return session


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

def test_le_catalogue_d_un_dossier_decrit_l_instance_de_ce_dossier(service, tmp_path):
    """Aujourd'hui il decrit la fenetre de l'utilisateur : rouge."""
    mission = tmp_path / "dossier-de-mission"
    mission.mkdir()
    vue_mission = _instance_de_mission(service, mission)

    snapshot = service.capture(workspace=str(mission))

    assert snapshot.state == "ready"
    assert snapshot.binding.session is vue_mission
    assert service.expected_session(snapshot).workspace_path == str(mission)


def test_le_workspace_annonce_est_celui_de_la_mission_pas_du_projet(service, tmp_path):
    """C'est CE champ que lit `authorize_mission_call`."""
    mission = tmp_path / "mission"
    mission.mkdir()
    _instance_de_mission(service, mission)
    proprietaire = service.bridge.catalogue_snapshot().workspace_path

    annonce = service.expected_session(service.capture(workspace=str(mission))).workspace_path

    assert annonce == str(mission) and annonce != proprietaire


# ── 2. La regle qui ne se negocie pas ───────────────────────────────────────

def test_sans_instance_sur_ce_dossier_la_mission_ne_recoit_pas_celle_de_l_utilisateur(
        service, tmp_path):
    """L5-2 : une mission ne se rabat JAMAIS sur la fenetre de l'utilisateur."""
    ailleurs = tmp_path / "dossier-sans-ide"
    ailleurs.mkdir()
    assert service.bridge.catalogue_snapshot() is not None

    snapshot = service.capture(workspace=str(ailleurs))

    assert snapshot.state == "launch_only"
    assert [tool.name for tool in snapshot.tools] == ["ide_launch"]
    with pytest.raises(Exception):
        service.expected_session(snapshot)


def test_un_snapshot_de_mission_devient_perime_si_son_instance_tombe(service, tmp_path):
    mission = tmp_path / "mission"
    mission.mkdir()
    _instance_de_mission(service, mission)
    snapshot = service.capture(workspace=str(mission))
    assert service.is_current(snapshot)

    service.bridge._connexions["0a" * 16]._connected = False

    assert not service.is_current(snapshot)


def test_un_snapshot_de_mission_n_est_pas_valide_par_la_proprietaire(service, tmp_path):
    """La revalidation doit interroger LA MEME instance, pas n'importe laquelle."""
    mission = tmp_path / "mission"
    mission.mkdir()
    _instance_de_mission(service, mission)
    snapshot = service.capture(workspace=str(mission))

    del service.bridge._connexions["0a" * 16]

    assert service.bridge.catalogue_snapshot() is not None
    assert not service.is_current(snapshot)


def test_la_generation_forgee_est_refusee_comme_pour_la_proprietaire(service, tmp_path):
    """Identite, pas egalite - meme regle que le gel conn3c."""
    mission = tmp_path / "mission"
    mission.mkdir()
    vue = _instance_de_mission(service, mission)
    snapshot = service.capture(workspace=str(mission))

    service.bridge._connexions["0a" * 16]._negotiated = replace(vue)

    assert not service.is_current(snapshot)


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

def test_sans_workspace_le_catalogue_reste_celui_de_la_proprietaire(service, tmp_path):
    """Non-regression : le chat et l'utilisateur voient exactement la meme chose."""
    mission = tmp_path / "mission"
    mission.mkdir()
    _instance_de_mission(service, mission)

    snapshot = service.capture()

    assert snapshot.binding.session is service.bridge.catalogue_snapshot()
    assert snapshot.state == "ready"
    assert service.is_current(snapshot)


@pytest.mark.parametrize("valeur", ["", "   ", "relatif/dossier", None])
def test_un_workspace_sans_forme_ne_promeut_pas_la_proprietaire(service, tmp_path, valeur):
    """Un chemin illisible ne doit pas se degrader en « catalogue du proprietaire »."""
    if valeur is None:
        pytest.skip("None signifie explicitement « la proprietaire » (defaut)")
    assert service.capture(workspace=valeur).state == "launch_only"
