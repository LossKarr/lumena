"""P5 — la description de peer_team_request oriente vers submit_peer_task pour les
missions à livrables (évite la voie sync sans rapatriement). Zéro refonte : on
guide l'agent par la description, sans changer l'exécution.
"""
from __future__ import annotations

import pytest

import src.reasoning.handlers.peer_orchestrator as orch
from src.reasoning.handlers.peer_orchestrator import get_peer_orchestrator_handler_defs


@pytest.fixture(autouse=True)
def _enable_collab(monkeypatch):
    monkeypatch.setattr(orch, "_is_collaboration_enabled", lambda: True)


def _desc(name):
    for d in get_peer_orchestrator_handler_defs():
        if d.name == name:
            return d.description
    raise AssertionError(f"handler {name} introuvable")


def test_peer_team_request_steers_to_submit_for_files():
    desc = _desc("peer_team_request")
    assert "submit_peer_task" in desc
    # mentionne explicitement les fichiers / livrables
    assert "fichier" in desc.lower() or "livrable" in desc.lower()


def test_peer_team_request_warns_not_for_status_check():
    desc = _desc("peer_team_request")
    assert "vérifier" in desc.lower() or "état" in desc.lower()
