"""Lot CONN-8b - la matrice de compatibilite Lumena / IDE / protocole.

Exigence de CONN-8 : « **matrice de compatibilite Lumena/IDE/protocole** ».

--- Les chiffres, mesures des deux cotes le 24 septembre 2026 ---

    cote Lumena (`ide_protocol.py`)      cote IDE (`ideProtocol.ts`)
    PROTOCOL_VERSION          = 4        IDE_NEGOTIATION_VERSION     = 4
    MIN_PROTOCOL_VERSION      = 3        IDE_MIN_NEGOTIATION_VERSION = 3
    SUPPORTED                 = (3, 4)
    MISSION_SCOPE >= 4                   IDE_CONTROL_PROTOCOL_VERSION = 3
    TRANSPORT_VERSION         = 1        IDE_TRANSPORT_VERSION        = 1
    CATALOGUE_SCHEMA          = 2

--- Ce que ce lot apporte, et pourquoi il ne suffit pas de l'ecrire ---

Une matrice dans un document se perime en silence : les deux depots evoluent
separement, et rien ne signale qu'ils ont diverge. Ces tests la rendent
**verifiable** - ils lisent les constantes des DEUX depots et echouent si elles ne
s'accordent plus.

La regle qui les relie : un client et un serveur ne parlent que si leurs intervalles
`[min, max]` se recoupent. Le protocole retenu est le PLUS HAUT commun, et le
perimetre de mission exige 4 (CONN-5B-2) - une IDE restee en 3 se connecte, mais
aucune mission ne peut l'utiliser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.tools.ide_protocol import (
    CATALOGUE_SCHEMA_VERSION, MIN_PROTOCOL_VERSION, MISSION_SCOPE_PROTOCOL_VERSION,
    PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS, TRANSPORT_VERSION,
)

RACINE = Path(__file__).resolve().parents[2]
IDE = RACINE / "ide"
pytestmark = pytest.mark.skipif(not IDE.exists(), reason="depot IDE absent de cette machine")


def _constante_ts(fichier: str, nom: str) -> int:
    source = (IDE / fichier).read_text(encoding="utf-8")
    trouve = re.search(rf"export const {nom}\s*=\s*(\d+)", source)
    assert trouve, f"{nom} introuvable dans {fichier}"
    return int(trouve.group(1))


# ── 1. Les deux depots annoncent les MEMES bornes ───────────────────────────

def test_le_protocole_le_plus_haut_est_le_meme_des_deux_cotes():
    assert PROTOCOL_VERSION == _constante_ts("electron/ideProtocol.ts", "IDE_NEGOTIATION_VERSION")


def test_le_protocole_le_plus_bas_est_le_meme_des_deux_cotes():
    assert MIN_PROTOCOL_VERSION == _constante_ts(
        "electron/ideProtocol.ts", "IDE_MIN_NEGOTIATION_VERSION")


def test_le_transport_est_le_meme_des_deux_cotes():
    assert TRANSPORT_VERSION == _constante_ts("electron/ideProtocol.ts", "IDE_TRANSPORT_VERSION")


def test_les_intervalles_se_recoupent_vraiment():
    """La condition de compatibilite, ecrite plutot que supposee."""
    lumena = set(SUPPORTED_PROTOCOL_VERSIONS)
    ide = set(range(_constante_ts("electron/ideProtocol.ts", "IDE_MIN_NEGOTIATION_VERSION"),
                    _constante_ts("electron/ideProtocol.ts", "IDE_NEGOTIATION_VERSION") + 1))
    commun = lumena & ide
    assert commun, f"aucun protocole commun : Lumena {sorted(lumena)} vs IDE {sorted(ide)}"
    assert max(commun) == PROTOCOL_VERSION, (
        "le plus haut commun n'est pas le protocole courant : la negociation choisirait autre chose"
    )


# ── 2. Ce que chaque palier permet ──────────────────────────────────────────

def test_le_perimetre_de_mission_exige_le_protocole_4():
    """CONN-5B-2 : une IDE restee en 3 se connecte, mais aucune mission ne l'utilise."""
    assert MISSION_SCOPE_PROTOCOL_VERSION == 4
    assert MISSION_SCOPE_PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS
    assert MIN_PROTOCOL_VERSION < MISSION_SCOPE_PROTOCOL_VERSION, (
        "si le minimum atteignait 4, une IDE en 3 serait refusee au lieu d'etre degradee"
    )


def test_une_version_hors_intervalle_est_refusee():
    """Le refus doit etre franc, jamais une degradation silencieuse."""
    from src.tools.ide_protocol import ProtocolError, negotiate
    from tests.tools.test_conn2b_ide_protocol import hello

    for intervalle in ({"min": 2, "max": 2}, {"min": 5, "max": 6}):
        message = hello()
        message["protocol"] = intervalle
        with pytest.raises(ProtocolError):
            negotiate(message, message["session_id"])


def test_le_schema_de_catalogue_est_fige():
    """Un changement de schema casse la lecture des catalogues deja negocies."""
    assert CATALOGUE_SCHEMA_VERSION == 2


# ── 3. La matrice EXISTE comme document, et reste juste ─────────────────────

def test_la_matrice_est_documentee_et_a_jour():
    """Une matrice qui n'est pas relue se perime en silence.

    Ce test lie le document aux constantes : si l'une bouge sans l'autre, il tombe.
    """
    doc = IDE / "plans" / "MATRICE_COMPATIBILITE.md"
    assert doc.exists(), "la matrice de compatibilite n'est pas ecrite"
    texte = doc.read_text(encoding="utf-8")
    for valeur, libelle in (
        (PROTOCOL_VERSION, "protocole courant"),
        (MIN_PROTOCOL_VERSION, "protocole minimum"),
        (MISSION_SCOPE_PROTOCOL_VERSION, "perimetre de mission"),
        (TRANSPORT_VERSION, "transport"),
        (CATALOGUE_SCHEMA_VERSION, "schema de catalogue"),
    ):
        assert str(valeur) in texte, f"{libelle} ({valeur}) absent de la matrice"
