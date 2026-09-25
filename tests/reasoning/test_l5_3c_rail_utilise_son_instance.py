"""Lot L5-3c-4 - preuve de bout en bout : le rail parle a l'instance de la mission.

L5-3c-1 designe l'instance d'un dossier ; L5-3c-2 la fait observer par le lanceur ;
L5-3c-3 en tire un catalogue. Il reste a ce que le RAIL le demande.

Aujourd'hui `execute` (`ide_tool_runtime.py` l.150 et 177) appelle `self.catalog()`
sans dossier, donc le catalogue de la PROPRIETAIRE. Meme avec son instance vivante
et enregistree, une mission recoit le `workspace_path` du projet de l'utilisateur,
et `authorize_mission_call` refuse par `ide_mission_workspace_mismatch`.

C'est le refus exact que L5-3b voulait lever, et qui survivait a L5-3b-bis : ouvrir
l'instance ne servait a rien tant que personne ne la REGARDAIT.

--- Ce que ce fichier prouve ---

1. Une mission dont l'instance vit dans le registre travaille : la commande part,
   et elle part sur SON socket.
2. La commande ne part JAMAIS chez l'utilisateur.
3. Sans instance sur le dossier de mission, le refus reste franc : aucune retombee
   sur la fenetre de l'utilisateur (regle du lot L5-2).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.reasoning.caller_context import REACT
from src.tools.ide_protocol import negotiate
from src.tools.ide_semantics import audited_ide_commands
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401
from tests.tools.test_conn2b_ide_protocol import hello
from tests.tools.test_conn3b_ide_semantics import command

INSTANCE_MISSION = "0a" * 16


@pytest.fixture(autouse=True)
def _lanceur_de_mission_borne(monkeypatch):
    """Le rail est teste sans demarrer une vraie application Electron.

    Les contrats L5-3b couvrent le lancement dedie. Ce lot verifie seulement le
    routage vers une instance deja enregistree et le refus lorsqu'elle manque.
    """
    lanceur = SimpleNamespace(ensure_ready=AsyncMock(
        return_value=SimpleNamespace(available=False, state="unavailable")
    ))
    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: lanceur)
    return lanceur


def _instance_de_mission(service, dossier):
    """Une seconde IDE appairee, ouverte sur le dossier de mission."""
    from src.tools.ide_bridge import IDEConnection

    message = hello([command(name) for name in audited_ide_commands()])
    message["ide"]["instance_id"] = INSTANCE_MISSION
    message["workspace"] = {"id": "0b" * 32, "path": str(dossier)}
    session, _ = negotiate(message, message["session_id"])

    connexion = IDEConnection()
    connexion._negotiated = session
    connexion._session = service.bridge._session
    connexion._ws = object()
    connexion._connected = True
    service.bridge._connexions[INSTANCE_MISSION] = connexion
    return session


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_une_mission_travaille_dans_son_instance(registry, service, owner, tmp_path):
    """Aujourd'hui : `ide_mission_workspace_mismatch`, malgre l'instance vivante."""
    _, racine = _mission(registry)
    projet_de_charles = tmp_path / "projet-de-charles"
    projet_de_charles.mkdir()
    _ide_ouverte_sur(service, projet_de_charles)
    vue_mission = _instance_de_mission(service, racine)
    envoi = _envoi(service, content="# Herbier")

    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert observation.success is True, observation.content
    envoi.assert_awaited_once()
    assert envoi.await_args.args[:2] == ("read_file", {"path": "README.md"})
    assert envoi.await_args.kwargs.get("expected_snapshot") is vue_mission, (
        "la commande part vers la fenetre de l'utilisateur"
    )


@pytest.mark.asyncio
async def test_la_fenetre_de_l_utilisateur_ne_recoit_rien(registry, service, owner, tmp_path):
    """Regle L5-2, verifiee par l'identite du snapshot adresse."""
    _, racine = _mission(registry)
    projet = tmp_path / "projet"
    projet.mkdir()
    _ide_ouverte_sur(service, projet)
    vue_proprietaire = service.bridge.catalogue_snapshot()
    _instance_de_mission(service, racine)
    envoi = _envoi(service, content="x")

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert envoi.await_args.kwargs.get("expected_snapshot") is not vue_proprietaire


# ── 2. La regle qui ne se negocie pas ───────────────────────────────────────

@pytest.mark.asyncio
async def test_sans_instance_la_mission_echoue_franchement(registry, service, owner, tmp_path):
    """Pas de retombee sur la fenetre de l'utilisateur : refus, et rien d'envoye."""
    _mission(registry)
    projet = tmp_path / "projet"
    projet.mkdir()
    _ide_ouverte_sur(service, projet)
    envoi = AsyncMock(return_value={"success": True})
    service.bridge.send_command = envoi

    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert observation.success is False
    envoi.assert_not_awaited()


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
async def test_une_mission_sur_l_instance_proprietaire_travaille_toujours(
        registry, service, owner):
    """Non-regression : c'est le cas que le canari produit par un `navigate` externe."""
    _, racine = _mission(registry)
    _ide_ouverte_sur(service, racine)
    envoi = _envoi(service, content="# Herbier")

    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert observation.success is True, observation.content
    envoi.assert_awaited_once()
