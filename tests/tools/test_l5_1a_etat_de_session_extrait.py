"""Lot L5-1a - l'etat de session du pont IDE vit dans un objet dedie.

Refactoring a COMPORTEMENT CONSTANT. Sa preuve principale n'est pas ce fichier :
ce sont les **20 fichiers de tests du pont qui passent SANS la moindre retouche**.
Ce fichier-ci ne fige que l'invariant de STRUCTURE, comme RF-4 le fait pour
l'extraction de la progression de plan.

--- Pourquoi ce lot (mesure du 16 septembre 2026) ---

`IDEBridge` se declare « Manages a single WebSocket connection » et porte **huit**
attributs d'etat de session : `_ws`, `_pending`, `_commands`, `_sequence`,
`_operation_counter`, `_connected`, `_session`, `_negotiated`. Seuls `_server`,
`_pairing`, `_pairing_store` et `_dispatcher` sont reellement globaux au pont.
`_send_command_on_owner` lit `_negotiated` pour la session, les commandes negociees
et le prefixe des `operation_id` ; `handle_message` resout les futures dans un
`_pending` UNIQUE, sans connexion emettrice. La voie A (une IDE par mission) exige
donc de rendre cet etat multiple - ce que L5-1b et L5-1c feront ENSUITE.

--- Les deux contraintes mesurees, qui dictent la forme ---

1. **Le gel CONN-0A compte les methodes async** :
   `test_conn0a_ide_connection_baseline.py` exige
   `EXPECTED_BRIDGE_ASYNC_METHODS = 41` sur la classe `IDEBridge`. Deplacer une
   methode `async` (par exemple `unregister`) vers le nouvel objet ferait tomber le
   compte a 40. L'extraction deplace donc l'ETAT, jamais les METHODES.
2. **Les tests REASSIGNENT les attributs, ils ne les mutent jamais par index** :
   `_negotiated` 11 reassignations, `_connected` 11, `_pending` 7, `_ws` 3,
   `_session` 3 ; zero mutation indexee (`bridge._pending[x] = ...`) cote tests.
   `tests/tools/test_conn3c_ide_snapshot_binding.py` ecrit `bridge._ws = object()`
   et se parametre sur `("_ws", None)`. Les proprietes de compatibilite exigent
   donc un **setter**, et doivent rendre l'objet REEL (le code de `src/` mute bien
   `self._pending[request_id]` en place).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
PONT = RACINE / "src" / "tools" / "ide_bridge.py"

# Les huit attributs d'etat de session, mesures dans `__init__` le 16/09.
ETAT_DE_SESSION = (
    "_ws", "_pending", "_commands", "_sequence",
    "_operation_counter", "_connected", "_session", "_negotiated",
)
# Ce qui reste global au pont : serveur, appairage, repartiteur de boucle.
RESTE_GLOBAL = ("_server", "_pairing", "_pairing_store", "_dispatcher")


def _pont_neuf():
    from src.tools.ide_bridge import IDEBridge

    return IDEBridge()


# ── 1. L'objet de connexion existe et porte l'etat ──────────────────────────

def test_l_objet_de_connexion_porte_l_etat_de_session():
    from src.tools.ide_bridge import IDEConnection

    connexion = IDEConnection()
    for nom in ETAT_DE_SESSION:
        assert hasattr(connexion, nom), f"{nom} n'est pas porte par IDEConnection"


def test_le_pont_detient_une_connexion_courante():
    pont = _pont_neuf()
    from src.tools.ide_bridge import IDEConnection

    assert isinstance(getattr(pont, "_connexion", None), IDEConnection)


# ── 2. Compatibilite : lecture ET ecriture inchangees ───────────────────────

@pytest.mark.parametrize("nom", ETAT_DE_SESSION)
def test_chaque_attribut_reste_lisible_sur_le_pont(nom):
    """~90 occurrences dans les tests lisent ces attributs sur le pont."""
    pont = _pont_neuf()
    getattr(pont, nom)  # ne doit pas lever


@pytest.mark.parametrize("nom", ETAT_DE_SESSION)
def test_chaque_attribut_reste_assignable_sur_le_pont(nom):
    """Les doublures REASSIGNENT (`bridge._ws = object()`), d'ou le setter."""
    pont = _pont_neuf()
    temoin = object()
    setattr(pont, nom, temoin)
    assert getattr(pont, nom) is temoin
    assert getattr(pont._connexion, nom) is temoin, (
        f"{nom} ecrit sur le pont n'atteint pas la connexion courante"
    )


def test_les_dictionnaires_sont_l_objet_reel_pas_une_copie():
    """`src/` mute en place : `self._pending[request_id] = fut`."""
    pont = _pont_neuf()
    pont._pending["abc"] = "sentinelle"
    assert pont._connexion._pending["abc"] == "sentinelle"
    assert pont._pending is pont._connexion._pending


# ── 3. Ce que l'extraction ne doit PAS changer ──────────────────────────────

def test_le_gel_conn0a_des_methodes_async_reste_satisfait():
    """41 methodes async sur `IDEBridge` : deplacer l'etat, jamais les methodes."""
    arbre = ast.parse(PONT.read_text(encoding="utf-8"))
    for noeud in arbre.body:
        if isinstance(noeud, ast.ClassDef) and noeud.name == "IDEBridge":
            asynchrones = [
                enfant.name for enfant in noeud.body
                if isinstance(enfant, ast.AsyncFunctionDef)
            ]
            assert len(asynchrones) == 41
            assert len(asynchrones) == len(set(asynchrones))
            return
    raise AssertionError("classe IDEBridge introuvable")


def test_ce_qui_est_global_au_pont_le_reste():
    """Serveur, appairage et repartiteur ne sont PAS par connexion."""
    pont = _pont_neuf()
    from src.tools.ide_bridge import IDEConnection

    connexion = IDEConnection()
    for nom in RESTE_GLOBAL:
        assert hasattr(pont, nom), f"{nom} a quitte le pont"
        assert not hasattr(connexion, nom), f"{nom} ne doit pas etre par connexion"


def test_un_pont_neuf_est_deconnecte():
    """Caracterisation : l'etat initial ne bouge pas."""
    pont = _pont_neuf()
    assert pont._ws is None
    assert pont._connected is False
    assert pont._negotiated is None
    assert pont._session is None
    assert pont._pending == {}
    assert pont._commands == {}
    assert pont._sequence == 0
    assert pont._operation_counter == 0
    assert pont.connected is False
    assert pont.authenticated is False
