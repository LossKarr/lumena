"""Onboarding #2 — Interrupteur MAÎTRE du réseau Lumena (LUMENA_PEER_ENABLED).

Sémantique OR-fallback : chaque garde P2P = maître OU flag unitaire.
- maître ON  → les 4 capacités sont actives même si les unitaires sont à 0.
- maître OFF → les unitaires commandent (comportement historique préservé).
- les deux à 0 → tout dormant (défaut).

Couvre : collaboration, conscience, autonomie réseau, découverte, + le maître lui-même.
"""
from __future__ import annotations

import pytest

from src.runtime.peer_network_autonomy import (
    is_peer_master_enabled,
    is_peer_network_autonomy_enabled,
)
from src.runtime.peer_awareness import _is_peer_awareness_enabled
from src.runtime.peer_discovery import is_peer_discovery_enabled
from src.reasoning.handlers.peer_tasks import _is_collaboration_enabled as _collab_tasks
from src.reasoning.handlers.peer_orchestrator import _is_collaboration_enabled as _collab_orch
from src.reasoning.handlers.peer_delegation import _is_collaboration_enabled as _collab_deleg


_UNIT_FLAGS = (
    "LUMENA_PEER_COLLABORATION",
    "LUMENA_PEER_AWARENESS",
    "LUMENA_PEER_NETWORK_AUTONOMY",
    "LUMENA_PEER_DISCOVERY",
)
_GATES = (_collab_tasks, _collab_orch, _collab_deleg,
          _is_peer_awareness_enabled, is_peer_network_autonomy_enabled,
          is_peer_discovery_enabled)


def _clear(monkeypatch):
    monkeypatch.delenv("LUMENA_PEER_ENABLED", raising=False)
    for f in _UNIT_FLAGS:
        monkeypatch.delenv(f, raising=False)
    # La découverte utilise la constante boot PEER_DISCOVERY_ENABLED (flag unitaire
    # = restart), pas l'env live. On la fige à False pour les cas « tout éteint ».
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", False)


# ── le maître lui-même ────────────────────────────────────────────────────────

class TestMasterFlag:
    def test_default_off(self, monkeypatch):
        _clear(monkeypatch)
        assert is_peer_master_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "ON", "True"])
    def test_truthy_values(self, monkeypatch, val):
        _clear(monkeypatch)
        monkeypatch.setenv("LUMENA_PEER_ENABLED", val)
        assert is_peer_master_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
    def test_falsy_values(self, monkeypatch, val):
        _clear(monkeypatch)
        monkeypatch.setenv("LUMENA_PEER_ENABLED", val)
        assert is_peer_master_enabled() is False


# ── OR-fallback sur toutes les gardes ─────────────────────────────────────────

class TestOrFallback:
    def test_all_off_by_default(self, monkeypatch):
        _clear(monkeypatch)
        for gate in _GATES:
            assert gate() is False, gate

    def test_master_on_activates_everything(self, monkeypatch):
        """Maître ON → toutes les gardes actives même si unitaires absents."""
        _clear(monkeypatch)
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
        for gate in _GATES:
            assert gate() is True, gate

    def test_unit_flag_alone_still_works(self, monkeypatch):
        """Maître OFF + flag unitaire ON → la garde correspondante est active
        (le réglage fin reste souverain, non-régression)."""
        _clear(monkeypatch)
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        assert _collab_tasks() is True
        assert _collab_orch() is True
        assert _collab_deleg() is True
        # les autres restent dormantes
        assert _is_peer_awareness_enabled() is False
        assert is_peer_network_autonomy_enabled() is False
        assert is_peer_discovery_enabled() is False

    def test_partial_control_master_off(self, monkeypatch):
        """Maître OFF : on peut n'allumer que la découverte sans le reste.
        (Découverte = constante boot ; on la fige à True ici.)"""
        _clear(monkeypatch)
        import src.runtime.peer_discovery as _disc
        monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
        assert is_peer_discovery_enabled() is True
        assert _collab_tasks() is False
        assert is_peer_network_autonomy_enabled() is False


# ── présence dans le schéma config ────────────────────────────────────────────

def test_master_flag_in_config_schema():
    from web.routes.config import _CONFIG_SCHEMA
    keys = {e["key"] for e in _CONFIG_SCHEMA}
    assert "LUMENA_PEER_ENABLED" in keys
    entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_PEER_ENABLED")
    assert entry.get("restart") is True
    assert entry.get("group") == "Instance"
    # les unitaires restent dans le panel
    for f in _UNIT_FLAGS:
        assert f in keys
