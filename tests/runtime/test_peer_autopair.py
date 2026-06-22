"""Tests A1.5 — auto-jumelage de flotte (peer_network_autonomy).

Sélection des candidats, cooldown, gating par la clé, et flux auto-pair (mock).
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.runtime import peer_network_autonomy as pna


# ── is_fleet_autopair_enabled ────────────────────────────────────────────────

def test_autopair_disabled_without_fleet_key(monkeypatch):
    monkeypatch.delenv("LUMENA_FLEET_KEY", raising=False)
    assert pna.is_fleet_autopair_enabled() is False


def test_autopair_enabled_with_key(monkeypatch):
    monkeypatch.setenv("LUMENA_FLEET_KEY", "k")
    monkeypatch.delenv("LUMENA_FLEET_AUTOPAIR", raising=False)
    assert pna.is_fleet_autopair_enabled() is True


def test_autopair_killswitch(monkeypatch):
    monkeypatch.setenv("LUMENA_FLEET_KEY", "k")
    monkeypatch.setenv("LUMENA_FLEET_AUTOPAIR", "0")
    assert pna.is_fleet_autopair_enabled() is False


# ── _select_autopair_candidates ──────────────────────────────────────────────

def test_select_only_unknown_with_host():
    now = time.time()
    peers = {
        "u1": {"trust": "unknown", "host": "192.168.1.10", "port": 8081},
        "t1": {"trust": "trusted", "host": "192.168.1.11"},   # déjà trusted → skip
        "b1": {"trust": "blocked", "host": "192.168.1.12"},   # blocked → skip
        "nh": {"trust": "unknown"},                            # pas de host → skip
    }
    out = pna._select_autopair_candidates(peers, now, 600)
    assert [c[0] for c in out] == ["u1"]
    assert out[0] == ("u1", "192.168.1.10", 8081)


def test_select_respects_cooldown():
    now = time.time()
    peers = {
        "u1": {"trust": "unknown", "host": "192.168.1.10", "fleet_autopair_failed_at": now - 10},   # récent → skip
        "u2": {"trust": "unknown", "host": "192.168.1.11", "fleet_autopair_failed_at": now - 700},  # expiré → ok
    }
    out = pna._select_autopair_candidates(peers, now, 600)
    assert [c[0] for c in out] == ["u2"]


def test_select_caps_per_cycle():
    now = time.time()
    peers = {f"u{i}": {"trust": "unknown", "host": f"192.168.1.{i}"} for i in range(20)}
    out = pna._select_autopair_candidates(peers, now, 600)
    assert len(out) == pna._MAX_AUTOPAIR_PER_CYCLE


# ── _auto_pair_fleet_peers (flux, mock) ──────────────────────────────────────

@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    reg = tmp_path / "peer_registry.json"
    monkeypatch.setattr(pna, "_PEER_REGISTRY_FILE", reg)
    monkeypatch.setenv("LUMENA_FLEET_KEY", "fleet-test")
    monkeypatch.delenv("LUMENA_FLEET_AUTOPAIR", raising=False)
    return reg


@pytest.mark.asyncio
async def test_autopair_success_counts(isolated_registry):
    pna._save_peers({"u1": {"instance_id": "u1", "trust": "unknown", "host": "192.168.1.20", "port": 8081}})
    with patch("src.runtime.peer_discovery.attempt_fleet_pair", new=AsyncMock(return_value={"ok": True, "instance_id": "u1"})):
        paired = await pna._auto_pair_fleet_peers()
    assert paired == 1


@pytest.mark.asyncio
async def test_autopair_failure_sets_cooldown(isolated_registry):
    pna._save_peers({"u1": {"instance_id": "u1", "trust": "unknown", "host": "192.168.1.20", "port": 8081}})
    with patch("src.runtime.peer_discovery.attempt_fleet_pair", new=AsyncMock(return_value={"ok": False, "error": "peer_proof_invalid"})):
        paired = await pna._auto_pair_fleet_peers()
    assert paired == 0
    data = pna._load_peers()
    assert "fleet_autopair_failed_at" in data["u1"]
    assert data["u1"]["trust"] == "unknown"  # toujours pas trusted (échec)


@pytest.mark.asyncio
async def test_autopair_noop_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(pna, "_PEER_REGISTRY_FILE", tmp_path / "r.json")
    monkeypatch.delenv("LUMENA_FLEET_KEY", raising=False)
    pna._save_peers({"u1": {"trust": "unknown", "host": "192.168.1.20"}})
    paired = await pna._auto_pair_fleet_peers()
    assert paired == 0


@pytest.mark.asyncio
async def test_attempt_fleet_pair_excludes_own_ip(monkeypatch):
    """A ne doit JAMAIS se jumeler avec sa propre IP (anti self-pairing)."""
    from src.runtime import peer_discovery as pd
    monkeypatch.setenv("LUMENA_FLEET_KEY", "k")
    monkeypatch.setattr(
        "src.runtime.network_diagnostics.get_local_lan_ips",
        lambda: ["10.0.0.166", "192.168.80.1"],
    )
    result = await pd.attempt_fleet_pair("10.0.0.166", 8080)
    assert result == {"ok": False, "error": "self_ip"}
