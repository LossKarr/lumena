"""Lot L5-1b - deux IDE authentifiees peuvent etre connectees en meme temps.

Voie A : une mission ouvre SA fenetre IDE sans toucher a celle de l'utilisateur.
Aujourd'hui c'est impossible, et pas par accident : `register()` fait
`previous = self._ws` -> `unregister()` -> `previous.close()`. Une seconde IDE
valide **evince et FERME** la premiere. En l'etat, une IDE de mission
deconnecterait celle de Charles.

--- Ce que la mesure du 16 septembre autorise (verifie, pas suppose) ---

1. **L'appairage tolere DEJA plusieurs sessions simultanees.**
   `PairingAuthority.is_current(session)` (`ide_pairing.py:135`) ne compare pas a
   une session courante unique : il verifie seulement que la cle n'a pas tourne
   (`key.key_id == session.key_id`). Deux sessions issues de la meme cle sont donc
   TOUTES DEUX courantes. Et `begin()` gere un **dictionnaire** de challenges
   (`max_pending=64`, TTL) : les appairages concurrents sont prevus a l'origine.
   **L5-1b n'a donc rien a changer a l'authentification.**
2. **Les deux gels existants ne s'y opposent pas.** `test_invalid_peer_cannot_
   replace_live_session` et `test_incompatible_peer_cannot_replace_ready_peer`
   protegent contre un pair **invalide** ou **incompatible** ; aucun n'exige qu'un
   pair VALIDE evince le precedent. Ils sont rejoues ici en non-regression.
3. **Le gel CONN-0A compte 41 methodes `async`** sur `IDEBridge` : L5-1b ne doit ni
   en ajouter ni en retirer. `unregister()` recoit donc un parametre **nomme**
   optionnel (regle CONN-5B-2 : jamais un positionnel, les doublures casseraient) -
   il n'est appele que dans `ide_bridge.py`, jamais par un test.

L5-1c fera le routage (commande et resultat par connexion emettrice) ; ici, on
prouve seulement que les deux connexions COEXISTENT.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import websockets

from src.tools import ide_bridge as module
from src.tools.ide_pairing import PairingAuthority, PairingSession
from src.tools.ide_pairing_store import PairingStore
from tests.tools.test_conn2a_ide_bridge_auth import paired_client

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")]

RACINE = Path(__file__).resolve().parents[2]
PONT = RACINE / "src" / "tools" / "ide_bridge.py"

INSTANCE_MISSION = "0a" * 16


def instance_de_mission(message: dict) -> None:
    """Un second IDE, identite distincte — comme une instance de mission."""
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

async def test_deux_pairs_valides_restent_connectes_ensemble(pont):
    """Aujourd'hui le second EVINCE et FERME le premier."""
    value, store = pont
    utilisateur = await paired_client(value, store)
    try:
        assert value.authenticated
        mission = await paired_client(value, store, transform=instance_de_mission)
        try:
            # La fermeture decidee par le serveur est ASYNCHRONE : lire
            # `close_code` juste apres rend None et ne prouve RIEN (premiere
            # version de ce test : vert sans rien mesurer). Il faut l'OBSERVER.
            # Aujourd'hui `previous.close()` ferme -> ConnectionClosed (rouge) ;
            # une fois L5-1b fait, la connexion survit -> TimeoutError (vert).
            import asyncio

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(utilisateur.recv(), timeout=2)
            assert value.authenticated
        finally:
            await mission.close()
    finally:
        await utilisateur.close()


async def test_le_registre_indexe_les_connexions_par_instance(pont):
    value, store = pont
    utilisateur = await paired_client(value, store)
    try:
        mission = await paired_client(value, store, transform=instance_de_mission)
        try:
            assert set(value._connexions) == {value._connexion._negotiated.instance_id,
                                              INSTANCE_MISSION}
        finally:
            await mission.close()
    finally:
        await utilisateur.close()


async def test_le_proprietaire_reste_la_premiere_instance_connectee(pont):
    """Decision de conception : la fenetre de l'utilisateur garde la main."""
    value, store = pont
    utilisateur = await paired_client(value, store)
    try:
        proprietaire = value._connexion
        mission = await paired_client(value, store, transform=instance_de_mission)
        try:
            assert value._connexion is proprietaire
            assert value._negotiated.instance_id != INSTANCE_MISSION
        finally:
            await mission.close()
    finally:
        await utilisateur.close()


# ── 2. Le fait mesure qui rend le lot possible (pur, sans websocket) ────────

@pytest.mark.asyncio
async def test_l_appairage_declare_courantes_deux_sessions_de_la_meme_cle(tmp_path):
    """`is_current` juge la CLE, pas l'unicite de la session."""
    store = PairingStore(tmp_path / "store")
    store.initialize()
    autorite = PairingAuthority(store.load)
    cle = store.load().key_id
    premiere = PairingSession("aa" * 16, cle)
    seconde = PairingSession("bb" * 16, cle)
    assert autorite.is_current(premiere) and autorite.is_current(seconde)


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

async def test_un_pair_invalide_ne_remplace_toujours_pas_le_vivant(pont):
    """Non-regression du gel CONN-2A : seul un pair VALIDE peut coexister."""
    value, store = pont
    bon = await paired_client(value, store)
    try:
        origine = value._ws
        port = value._server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as mauvais:
            import json
            from tests.tools.test_conn2a_ide_pairing import response
            challenge = json.loads(await mauvais.recv())
            await mauvais.send(json.dumps(response(challenge, b"wrong".ljust(32, b"x"))))
            with pytest.raises(websockets.ConnectionClosed):
                await mauvais.recv()
        assert value.authenticated and value._ws is origine
        assert bon.close_code is None
    finally:
        await bon.close()


async def test_le_gel_conn0a_reste_a_41_methodes_async():
    arbre = ast.parse(PONT.read_text(encoding="utf-8"))
    for noeud in arbre.body:
        if isinstance(noeud, ast.ClassDef) and noeud.name == "IDEBridge":
            asynchrones = [enfant.name for enfant in noeud.body
                           if isinstance(enfant, ast.AsyncFunctionDef)]
            assert len(asynchrones) == 41
            return
    raise AssertionError("classe IDEBridge introuvable")
