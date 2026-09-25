"""Lot L5-1c - chaque commande part sur SA connexion, chaque resultat revient a SA future.

L5-1b a fait coexister deux IDE. Mais `_send_command_on_owner` envoie encore sur
`self._ws` (la proprietaire) et `handle_message` resout le `request_id` dans le
`_pending` de celle-ci. Deux IDE connectees, une seule adressable : une mission
enverrait ses commandes dans la fenetre de l'utilisateur.

--- Ce que la mesure du 16 septembre etablit (verifie, pas suppose) ---

1. **L'instance visee est DEJA portee par l'appel.** Le rail passe
   `expected_snapshot=<NegotiatedSession>` a chaque commande
   (`ide_tool_runtime.py` l.104, 164, 177) et `_send_command_on_owner` compare deja
   `self.catalogue_snapshot() is not expected_snapshot`. **Aucun parametre n'est a
   ajouter** : il suffit de resoudre la connexion dont le `_negotiated` EST cet
   objet, au lieu de la comparer a la seule proprietaire.
2. **La resolution doit regarder la proprietaire EN PLUS du registre.**
   `tests/tools/test_conn3c_ide_snapshot_binding.py::unit_bridge` construit un pont
   en ecrivant directement `_negotiated`/`_ws`/`_session` : `_connexions` y reste
   **vide**. Une resolution qui ne consulterait que le registre casserait ces
   doublures.
3. **Le controle est une IDENTITE, pas une egalite.** Le scenario `"forged"` de ce
   meme fichier fabrique un objet EGAL mais distinct (`replace(snapshot)`) et exige
   le refus. Le `is` reste.
4. **Un refus « stale » ne consomme rien** : ni `_sequence`, ni
   `_operation_counter`, ni `_pending` (test existant). La resolution echoue donc
   AVANT toute allocation.
5. **Gel CONN-0A** : 41 methodes `async` sur `IDEBridge`. L'aide de resolution est
   SYNCHRONE et `handle_message` recoit un parametre **nomme** (regle CONN-5B-2) -
   un seul appelant dans `src/` et une seule doublure dans les tests.
"""
from __future__ import annotations

import asyncio
import ast
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")]

RACINE = Path(__file__).resolve().parents[2]
PONT = RACINE / "src" / "tools" / "ide_bridge.py"

INSTANCE_MISSION = "0a" * 16


def instance_de_mission(message: dict) -> None:
    message["ide"]["instance_id"] = INSTANCE_MISSION


@pytest_asyncio.fixture
async def pont(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "store")
    value = module.IDEBridge(pairing_store=store)
    await value.start_server()
    try:
        yield value, store
    finally:
        await value.stop_server()


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

async def test_une_commande_adressee_a_la_mission_part_sur_son_socket(pont):
    """Aujourd'hui elle part sur la fenetre de l'utilisateur."""
    value, store = pont
    utilisateur = await paired_client(value, store)
    mission = await paired_client(value, store, transform=instance_de_mission)
    tache = None
    try:
        vue_mission = value._connexions[INSTANCE_MISSION]._negotiated
        tache = asyncio.create_task(
            value.send_command("get_status", expected_snapshot=vue_mission))
        # La commande doit arriver chez la MISSION...
        commande = json.loads(await asyncio.wait_for(mission.recv(), 3))
        assert commande["instance_id"] == INSTANCE_MISSION
        # ...et surtout PAS chez l'utilisateur.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(utilisateur.recv(), 0.2)
        await mission.send(json.dumps(result_for(commande, success=True)))
        assert (await asyncio.wait_for(tache, 3))["success"]
    finally:
        if tache is not None and not tache.done():
            tache.cancel()
            await asyncio.gather(tache, return_exceptions=True)
        await mission.close()
        await utilisateur.close()


async def test_le_resultat_de_la_mission_resout_sa_propre_future(pont):
    """Le `request_id` vit dans le `_pending` de SA connexion, pas dans un commun."""
    value, store = pont
    utilisateur = await paired_client(value, store)
    mission = await paired_client(value, store, transform=instance_de_mission)
    tache = None
    try:
        vue_mission = value._connexions[INSTANCE_MISSION]._negotiated
        tache = asyncio.create_task(
            value.send_command("get_status", expected_snapshot=vue_mission))
        commande = json.loads(await asyncio.wait_for(mission.recv(), 3))
        assert value._connexions[INSTANCE_MISSION]._pending, (
            "la future attend dans le _pending de la proprietaire, pas dans celui de la mission"
        )
        assert value._connexion._pending == {}
        await mission.send(json.dumps(result_for(commande, success=True)))
        assert (await asyncio.wait_for(tache, 3))["success"]
    finally:
        if tache is not None and not tache.done():
            tache.cancel()
            await asyncio.gather(tache, return_exceptions=True)
        await mission.close()
        await utilisateur.close()


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

async def test_sans_snapshot_la_commande_part_toujours_chez_la_proprietaire(pont):
    """Non-regression : le defaut reste la fenetre de l'utilisateur."""
    value, store = pont
    utilisateur = await paired_client(value, store)
    mission = await paired_client(value, store, transform=instance_de_mission)
    tache = None
    try:
        tache = asyncio.create_task(value.send_command("get_status"))
        commande = json.loads(await asyncio.wait_for(utilisateur.recv(), 3))
        await utilisateur.send(json.dumps(result_for(commande, success=True)))
        assert (await asyncio.wait_for(tache, 3))["success"]
    finally:
        if tache is not None and not tache.done():
            tache.cancel()
            await asyncio.gather(tache, return_exceptions=True)
        await mission.close()
        await utilisateur.close()


async def test_un_snapshot_inconnu_est_refuse_sans_rien_consommer(pont):
    """Meme verdict que le gel existant : « stale », et aucune allocation."""
    from dataclasses import replace

    value, store = pont
    utilisateur = await paired_client(value, store)
    try:
        etranger = replace(value._connexion._negotiated)  # egal mais PAS le meme objet
        resultat = await value.send_command("get_status", expected_snapshot=etranger)
        assert resultat == {"success": False, "error": "IDE catalogue snapshot stale",
                            "outcome": "not_sent"}
        assert value._connexion._sequence == 0
        assert value._connexion._operation_counter == 0
        assert value._connexion._pending == {}
    finally:
        await utilisateur.close()


async def test_le_gel_conn0a_reste_a_41_methodes_async():
    arbre = ast.parse(PONT.read_text(encoding="utf-8"))
    for noeud in arbre.body:
        if isinstance(noeud, ast.ClassDef) and noeud.name == "IDEBridge":
            asynchrones = [enfant.name for enfant in noeud.body
                           if isinstance(enfant, ast.AsyncFunctionDef)]
            assert len(asynchrones) == 41
            return
    raise AssertionError("classe IDEBridge introuvable")
