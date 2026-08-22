"""Bloc C3-shadow — Moteur d'initiative + tick en mode ombre.

Couvre :
- moteur : propose une délégation pour un pair éligible dont la capacité colle ;
- 0 proposition si aucun pair délégable / aucun candidat / pair non-mission ;
- bonus capacité (browser pour une tâche web) ;
- tick : `off` → no-op ; `shadow` → logge/SSE mais N'EXÉCUTE PAS ;
- flag `peer_autonomy_mode` + présence schéma config.
"""
from __future__ import annotations

import pytest

from src.runtime.peer_cooperation_engine import propose_delegations
from src.runtime import peer_network_autonomy as pna


def _map(*peers):
    return {"peers": list(peers), "count": len(peers), "delegable_count": sum(1 for p in peers if p.get("delegable"))}


def _peer(pid, *, delegable=True, scopes=("chat", "task.delegate"), level="mission", caps=("chat",), ago=10):
    return {
        "instance_id": pid, "name": f"Lumena {pid}", "capabilities": list(caps),
        "allowed_scopes": list(scopes), "capability_level": level,
        "delegable": delegable, "reachable": True, "quarantined": False, "seen_seconds_ago": ago,
    }


# ── moteur ────────────────────────────────────────────────────────────────────

class TestEngine:
    def test_proposes_for_eligible_peer(self):
        props = propose_delegations([{"objective": "Rédiger un rapport"}], _map(_peer("p1")))
        assert len(props) == 1
        assert props[0]["peer_id"] == "p1"
        assert "mission" in props[0]["reason"].lower()

    def test_no_candidate_no_proposal(self):
        assert propose_delegations([], _map(_peer("p1"))) == []

    def test_no_eligible_peer_no_proposal(self):
        # pair non délégable
        assert propose_delegations([{"objective": "x"}], _map(_peer("p1", delegable=False))) == []
        # pair sans task.delegate
        assert propose_delegations([{"objective": "x"}], _map(_peer("p2", scopes=("chat",)))) == []
        # pair pas en mission
        assert propose_delegations([{"objective": "x"}], _map(_peer("p3", level="chat"))) == []

    def test_capability_bonus_prefers_matching_peer(self):
        generic = _peer("gen", caps=("chat",), ago=5)
        webby = _peer("web", caps=("chat", "browser"), ago=300)
        props = propose_delegations([{"objective": "scraper un site web"}], _map(generic, webby))
        assert props[0]["peer_id"] == "web"  # capacité browser qui colle l'emporte

    def test_string_tasks_accepted(self):
        props = propose_delegations(["Faire un truc utile"], _map(_peer("p1")))
        assert len(props) == 1


# ── flag + tick ───────────────────────────────────────────────────────────────

class TestFlagAndTick:
    def test_mode_default_off(self, monkeypatch):
        monkeypatch.delenv("LUMENA_PEER_AUTONOMY", raising=False)
        assert pna.peer_autonomy_mode() == "off"

    @pytest.mark.parametrize("v,exp", [("shadow", "shadow"), ("LIVE", "live"), ("bidon", "off")])
    def test_mode_values(self, monkeypatch, v, exp):
        monkeypatch.setenv("LUMENA_PEER_AUTONOMY", v)
        assert pna.peer_autonomy_mode() == exp

    def test_tick_off_is_noop(self, monkeypatch):
        monkeypatch.delenv("LUMENA_PEER_AUTONOMY", raising=False)
        out = pna.run_peer_cooperation_shadow_tick(candidate_tasks=[{"objective": "x"}])
        assert out["mode"] == "off" and out["proposals"] == 0

    def test_tick_shadow_logs_but_does_not_execute(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_AUTONOMY", "shadow")
        # carte avec un pair délégable mission
        monkeypatch.setattr(pna, "build_capability_map", lambda **k: _map(_peer("p1")), raising=False)
        import src.runtime.peer_awareness as pa
        monkeypatch.setattr(pa, "build_capability_map", lambda **k: _map(_peer("p1")))
        logged = []
        import src.autonomy.activity_ledger as ledger
        monkeypatch.setattr(ledger, "append_autonomy_event", lambda *a, **k: logged.append(k))
        out = pna.run_peer_cooperation_shadow_tick(candidate_tasks=[{"objective": "Rédiger un rapport"}])
        assert out["mode"] == "shadow"
        assert out["proposals"] == 1
        # décision tracée en shadow
        assert any(k.get("decision") == "shadow" for k in logged)


def test_autonomy_flag_in_config_schema():
    from web.routes.config import _CONFIG_SCHEMA
    entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_PEER_AUTONOMY"), None)
    assert entry is not None
    assert entry["type"] == "select"
    assert entry["default"] == "off"
    assert set(["off", "shadow", "live"]).issubset(set(entry["options"]))
