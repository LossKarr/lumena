"""Fix — résolution flexible d'identifiant de pair (UUID / host:port / host / nom).

Régression du bug runtime : l'agent visait `192.0.2.57:8081` (host:port) mais le
registre est indexé par UUID → « pair inconnu » → la voie mission async ne démarrait pas.
"""
from __future__ import annotations

from src.runtime.peer_awareness import resolve_peer_identifier

_PEERS = {
    "76fdb352-uuid": {"instance_id": "76fdb352-uuid", "instance_name": "Lumena-B",
                      "host": "192.0.2.57", "port": 8081},
    "other-uuid": {"instance_id": "other-uuid", "instance_name": "Lumena-C",
                   "host": "192.0.2.99", "port": 8080},
}


def test_resolve_uuid_exact():
    assert resolve_peer_identifier(_PEERS, "76fdb352-uuid") == "76fdb352-uuid"


def test_resolve_host_port():
    assert resolve_peer_identifier(_PEERS, "192.0.2.57:8081") == "76fdb352-uuid"


def test_resolve_host_only():
    assert resolve_peer_identifier(_PEERS, "192.0.2.57") == "76fdb352-uuid"


def test_resolve_host_wrong_port_none():
    # host bon mais port qui ne correspond à aucun pair → None
    assert resolve_peer_identifier(_PEERS, "192.0.2.57:9999") is None


def test_resolve_instance_name_case_insensitive():
    assert resolve_peer_identifier(_PEERS, "lumena-b") == "76fdb352-uuid"


def test_resolve_unknown():
    assert resolve_peer_identifier(_PEERS, "192.0.2.200:8081") is None
    assert resolve_peer_identifier(_PEERS, "") is None
    assert resolve_peer_identifier(_PEERS, None) is None


def test_resolve_empty_registry():
    assert resolve_peer_identifier({}, "192.0.2.57:8081") is None


# ── Identifiant « brouillon » du LLM (nom collé à l'adresse) ──────────────────

def test_resolve_hybrid_name_hostport():
    # Régression log A 04:17:32 : l'agent a visé « Lumena-192.0.2.57:8081 »
    # (nom + host:port collés) → doit résoudre vers l'UUID (sinon fallback sync
    # texte-seul → fichiers jamais rapatriés).
    assert resolve_peer_identifier(_PEERS, "Lumena-192.0.2.57:8081") == "76fdb352-uuid"


def test_resolve_uuid_embedded():
    assert resolve_peer_identifier(_PEERS, "peer/76fdb352-uuid") == "76fdb352-uuid"


def test_resolve_hybrid_wrong_port_still_none():
    # host correct mais port erroné, même noyé dans du texte → reste inconnu
    assert resolve_peer_identifier(_PEERS, "Lumena-192.0.2.57:9999") is None
