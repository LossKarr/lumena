"""Tests Phase 8.11 — mDNS/Zeroconf discovery.

Couvre :
- dépendance absente (zeroconf non installé) : Lumena continue sans erreur
- annonce service : TXT records corrects, aucun secret
- découverte service : instances retournées en unknown/non-trusted
- auto-exclusion : propre instance_id ignorée
- filtrage TXT : seuls les champs autorisés passent
- caps_hash : non-secret, reproductible
- feature flag désactivé : aucune route ne plante
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-mdns-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena mDNS Test")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_PORT", "8080")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


AUTH = {"Authorization": "Bearer tok"}


# ─────────────────────────────────────────────────────────────────────────────
# Module — is_mdns_available / is_mdns_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestMdnsAvailability:

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("LUMENA_MDNS_DISCOVERY", raising=False)
        from src.runtime.mdns_discovery import is_mdns_available, is_mdns_enabled
        assert not is_mdns_enabled()
        assert not is_mdns_available()

    def test_flag_enabled_but_lib_absent(self, monkeypatch):
        import sys
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        # Simule zeroconf absent (robuste que la lib soit installée ou non).
        monkeypatch.setitem(sys.modules, "zeroconf", None)
        from src.runtime.mdns_discovery import is_mdns_available
        # Sans lib → False (ImportError capturé)
        assert not is_mdns_available()

    def test_flag_off_returns_false_regardless(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        from src.runtime.mdns_discovery import is_mdns_available
        assert not is_mdns_available()

    def test_is_mdns_enabled_with_flag(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        from src.runtime.mdns_discovery import is_mdns_enabled
        assert is_mdns_enabled()

    def test_is_mdns_enabled_without_flag(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        from src.runtime.mdns_discovery import is_mdns_enabled
        assert not is_mdns_enabled()


# ─────────────────────────────────────────────────────────────────────────────
# Module — build_txt_records (aucun secret)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTxtRecords:

    def test_returns_all_allowed_fields(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt = build_txt_records("inst-001", "Lumena Test", "standalone", "1.0.0", ["chat", "vision"], 8080)
        assert set(txt.keys()) == {"instance_id", "instance_name", "role", "version", "caps_hash", "port"}

    def test_all_values_are_bytes(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt = build_txt_records("id", "name", "role", "v", ["chat"], 8080)
        for k, v in txt.items():
            assert isinstance(v, bytes), f"{k} devrait être bytes"

    def test_no_secret_fields(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt = build_txt_records("id", "name", "role", "v", ["chat"], 8080)
        forbidden = {"token", "peer_token", "admin_token", "peer_token_hash",
                     "peer_token_outbound", "password", "secret", "key"}
        assert not (set(txt.keys()) & forbidden), "Un champ secret a été détecté dans les TXT records"

    def test_caps_hash_deterministic(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt1 = build_txt_records("id", "n", "r", "v", ["chat", "vision"], 8080)
        txt2 = build_txt_records("id", "n", "r", "v", ["vision", "chat"], 8080)
        assert txt1["caps_hash"] == txt2["caps_hash"], "caps_hash doit être identique quel que soit l'ordre"

    def test_caps_hash_is_compact(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt = build_txt_records("id", "n", "r", "v", ["chat"], 8080)
        h = txt["caps_hash"].decode()
        assert len(h) == 8, f"caps_hash doit être 8 chars hex, got {len(h)}"
        assert all(c in "0123456789abcdef" for c in h)

    def test_port_encoded_correctly(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt = build_txt_records("id", "n", "r", "v", [], 9090)
        assert txt["port"] == b"9090"

    def test_different_caps_give_different_hash(self):
        from src.runtime.mdns_discovery import build_txt_records
        txt1 = build_txt_records("id", "n", "r", "v", ["chat"], 8080)
        txt2 = build_txt_records("id", "n", "r", "v", ["chat", "vision"], 8080)
        assert txt1["caps_hash"] != txt2["caps_hash"]


# ─────────────────────────────────────────────────────────────────────────────
# Module — parse_service_info (fonction pure, testable sans zeroconf)
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_info(
    instance_id: str,
    instance_name: str = "Test",
    role: str = "standalone",
    version: str = "1.0",
    caps_hash: str = "aabbccdd",
    port: int = 8080,
    host_bytes: bytes = b"\xc0\xa8\x01\x0a",  # 192.168.1.10
    extra_props: Optional[dict] = None,
) -> MagicMock:
    """Construit un ServiceInfo mock pour les tests de parse_service_info."""
    props: dict = {
        b"instance_id": instance_id.encode(),
        b"instance_name": instance_name.encode(),
        b"role": role.encode(),
        b"version": version.encode(),
        b"caps_hash": caps_hash.encode(),
        b"port": str(port).encode(),
    }
    if extra_props:
        props.update(extra_props)
    info = MagicMock()
    info.properties = props
    info.addresses = [host_bytes]
    info.port = port
    return info


try:
    from typing import Optional
except ImportError:
    pass


class TestParseServiceInfo:

    def test_returns_unknown_trust(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("remote-001")
        entry = parse_service_info(info)
        assert entry is not None
        assert entry["trust"] == "unknown", "mDNS ne doit jamais accorder 'trusted'"

    def test_returns_mdns_source(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("remote-001")
        entry = parse_service_info(info)
        assert entry["source"] == "mdns"

    def test_self_exclusion_returns_none(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("self-001")
        result = parse_service_info(info, self_instance_id="self-001")
        assert result is None, "Auto-exclusion : propre instance doit retourner None"

    def test_different_id_not_excluded(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("remote-002")
        result = parse_service_info(info, self_instance_id="self-001")
        assert result is not None
        assert result["instance_id"] == "remote-002"

    def test_no_secret_fields_in_result(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info(
            "remote-001",
            extra_props={
                b"peer_token_hash": b"should_not_appear",
                b"admin_token": b"top_secret",
                b"peer_token_outbound": b"also_secret",
            }
        )
        entry = parse_service_info(info)
        assert entry is not None
        forbidden = {"peer_token_hash", "admin_token", "peer_token_outbound", "token", "secret"}
        assert not (set(entry.keys()) & forbidden), f"Champs secrets dans l'entrée : {entry}"

    def test_txt_filtering_strict(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info(
            "remote-001",
            extra_props={b"custom_field": b"value", b"internal_data": b"xyz"}
        )
        entry = parse_service_info(info)
        assert entry is not None
        # custom_field et internal_data ne doivent pas apparaître directement dans le résultat
        assert "custom_field" not in entry
        assert "internal_data" not in entry

    def test_host_decoded_from_bytes(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("remote-001", host_bytes=b"\xc0\xa8\x01\x14")  # 192.168.1.20
        entry = parse_service_info(info)
        assert entry["host"] == "192.168.1.20"

    def test_correct_fields_present(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = _make_mock_info("remote-001")
        entry = parse_service_info(info)
        for field in ("instance_id", "instance_name", "host", "port", "role", "version",
                      "caps_hash", "trust", "source"):
            assert field in entry, f"Champ manquant : {field}"

    def test_none_on_missing_properties(self):
        from src.runtime.mdns_discovery import parse_service_info
        info = MagicMock()
        info.properties = None
        info.addresses = []
        info.port = 8080
        # Ne doit pas planter, peut retourner une entrée vide ou None
        result = parse_service_info(info)
        # Si instance_id est vide et pas de self_instance_id, ça retourne un résultat
        assert result is None or isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# Module — advertise_service / stop_service
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvertiseService:

    def test_returns_none_when_flag_off(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        from src.runtime.mdns_discovery import advertise_service
        result = advertise_service("id", "name", "standalone", "1.0", ["chat"], 8080)
        assert result is None

    def test_returns_none_when_lib_absent(self, monkeypatch):
        import sys
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        # Simule zeroconf absent → is_mdns_available() = False → None
        monkeypatch.setitem(sys.modules, "zeroconf", None)
        from src.runtime.mdns_discovery import advertise_service
        result = advertise_service("id", "name", "standalone", "1.0", ["chat"], 8080)
        assert result is None

    def test_stop_service_noop_on_none(self):
        from src.runtime.mdns_discovery import stop_service
        stop_service(None)  # ne doit pas planter

    def test_stop_service_calls_unregister(self):
        from src.runtime.mdns_discovery import stop_service
        mock_zc = MagicMock()
        mock_info = MagicMock()
        stop_service((mock_zc, mock_info))
        mock_zc.unregister_service.assert_called_once_with(mock_info)
        mock_zc.close.assert_called_once()

    def test_stop_service_resilient_to_errors(self):
        from src.runtime.mdns_discovery import stop_service
        mock_zc = MagicMock()
        mock_zc.unregister_service.side_effect = Exception("zeroconf gone")
        mock_info = MagicMock()
        stop_service((mock_zc, mock_info))  # ne doit pas planter


# ─────────────────────────────────────────────────────────────────────────────
# Module — browse_services (lib absente ou flag off)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowseServices:

    def test_returns_empty_when_flag_off(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        from src.runtime.mdns_discovery import browse_services
        assert browse_services(timeout=0.01) == []

    def test_returns_empty_when_lib_absent(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        # zeroconf pas installé → is_mdns_available() = False
        from src.runtime.mdns_discovery import browse_services
        assert browse_services(timeout=0.01) == []


# ─────────────────────────────────────────────────────────────────────────────
# Routes HTTP — /api/mdns/status
# ─────────────────────────────────────────────────────────────────────────────

class TestMdnsStatusRoute:

    def test_requires_auth(self, authed):
        r = authed.get("/api/mdns/status")
        assert r.status_code in (401, 403)

    def test_returns_status_fields(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        r = authed.get("/api/mdns/status", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        for field in ("enabled", "available", "service_type", "note"):
            assert field in d, f"Champ manquant : {field}"

    def test_disabled_when_flag_off(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        r = authed.get("/api/mdns/status", headers=AUTH)
        d = r.json()
        assert d["enabled"] is False
        assert d["available"] is False

    def test_service_type_correct(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        r = authed.get("/api/mdns/status", headers=AUTH)
        d = r.json()
        assert d["service_type"] == "_lumena._tcp.local."


# ─────────────────────────────────────────────────────────────────────────────
# Routes HTTP — /api/mdns/browse
# ─────────────────────────────────────────────────────────────────────────────

class TestMdnsBrowseRoute:

    def test_requires_auth(self, authed):
        r = authed.post("/api/mdns/browse", json={"timeout": 1.0})
        assert r.status_code in (401, 403)

    def test_returns_error_when_unavailable(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        r = authed.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert "error" in d
        assert d["discovered"] == 0

    def test_with_mocked_discovery(self, authed, tmp_path, monkeypatch):
        """Browse retourne les pairs découverts en unknown/non-trusted."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")

        fake_peer = {
            "instance_id": "remote-mdns-peer",
            "instance_name": "Lumena Remote",
            "host": "192.168.1.50",
            "port": 8081,
            "role": "standalone",
            "version": "1.0",
            "caps_hash": "deadbeef",
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    r = c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["discovered"] == 1
        peer = d["peers"][0]
        assert peer["trust"] == "unknown", "mDNS ne doit jamais retourner trust='trusted'"
        assert peer["source"] == "mdns"

    def test_discovered_peer_added_to_registry(self, authed, tmp_path, monkeypatch):
        """Un pair mDNS découvert doit être enregistré avec trust=unknown."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "mdns-new-peer",
            "instance_name": "Nouveau",
            "host": "192.168.5.5",
            "port": 8082,
            "role": "standalone",
            "version": "1.0",
            "caps_hash": "cafebabe",
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        assert reg_file.exists()
        reg = json.loads(reg_file.read_text(encoding="utf-8"))
        assert "mdns-new-peer" in reg
        assert reg["mdns-new-peer"]["trust"] == "unknown"
        assert "peer_token_hash" not in reg["mdns-new-peer"]

    def test_existing_trusted_peer_not_overwritten(self, authed, tmp_path, monkeypatch):
        """Un pair déjà trusted ne doit pas être réécrit en unknown par mDNS."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        reg_file.write_text(json.dumps({
            "already-trusted": {
                "instance_id": "already-trusted",
                "trust": "trusted",
                "peer_token_hash": "somehash",
                "host": "192.168.1.99",
                "port": 8080,
            }
        }), encoding="utf-8")
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "already-trusted",
            "instance_name": "Pair déjà connu",
            "host": "192.168.1.99",
            "port": 8080,
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        reg = json.loads(reg_file.read_text(encoding="utf-8"))
        assert reg["already-trusted"]["trust"] == "trusted", "Un pair trusted ne doit pas être réécrasé"
        assert reg["already-trusted"]["peer_token_hash"] == "somehash"

    def test_public_host_rejected_before_registry(self, authed, tmp_path, monkeypatch):
        """Un host hors RFC1918 découvert par mDNS ne doit pas être intégré au registre."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "evil-peer",
            "instance_name": "Evil",
            "host": "8.8.8.8",  # IP publique — doit être rejeté
            "port": 8080,
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    r = c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        assert r.status_code == 200
        d = r.json()
        assert d["discovered"] == 0, "Host public ne doit pas être compté comme découvert"
        assert d["peers"] == [], "Host public ne doit pas apparaître dans la réponse"
        # Le registre ne doit pas exister ou ne pas contenir le pair
        if reg_file.exists():
            reg = json.loads(reg_file.read_text(encoding="utf-8"))
            assert "evil-peer" not in reg

    def test_loopback_host_rejected(self, authed, tmp_path, monkeypatch):
        """Un host loopback (127.x) découvert par mDNS ne doit pas être intégré."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "loopback-peer",
            "host": "127.0.0.1",
            "port": 8080,
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    r = c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        d = r.json()
        assert d["discovered"] == 0
        if reg_file.exists():
            reg = json.loads(reg_file.read_text(encoding="utf-8"))
            assert "loopback-peer" not in reg

    def test_rfc1918_host_accepted(self, authed, tmp_path, monkeypatch):
        """Un host RFC1918 valide doit passer le filtre et être intégré."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "lan-peer",
            "instance_name": "Lumena LAN",
            "host": "192.168.1.42",
            "port": 8080,
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    r = c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        d = r.json()
        assert d["discovered"] == 1
        assert d["peers"][0]["host"] == "192.168.1.42"

    def test_discovered_peer_has_no_token_in_registry(self, authed, tmp_path, monkeypatch):
        """Le registre ne doit pas contenir de token pour un pair découvert par mDNS."""
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

        fake_peer = {
            "instance_id": "mdns-candidate",
            "host": "192.168.1.77",
            "port": 8080,
            "trust": "unknown",
            "source": "mdns",
        }

        with patch("src.runtime.mdns_discovery.browse_services", return_value=[fake_peer]):
            with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
                with TestClient(_make_app()) as c:
                    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
                    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
                    c.post("/api/mdns/browse", json={"timeout": 1.0}, headers=AUTH)

        reg = json.loads(reg_file.read_text(encoding="utf-8"))
        entry = reg.get("mdns-candidate", {})
        assert "peer_token_hash" not in entry
        assert "peer_token_outbound" not in entry


# ─────────────────────────────────────────────────────────────────────────────
# Routes HTTP — /api/mdns/advertise
# ─────────────────────────────────────────────────────────────────────────────

class TestMdnsAdvertiseRoute:

    def test_requires_auth(self, authed):
        r = authed.post("/api/mdns/advertise", json={})
        assert r.status_code in (401, 403)

    def test_returns_error_when_unavailable(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "0")
        r = authed.post("/api/mdns/advertise", json={}, headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["advertising"] is False
        assert "error" in d

    def test_advertise_calls_module(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        mock_handle = MagicMock()

        with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
            with patch("src.runtime.mdns_discovery.advertise_service", return_value=mock_handle):
                r = authed.post("/api/mdns/advertise", json={"stop": False}, headers=AUTH)

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["advertising"] is True

    def test_stop_advertise(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_MDNS_DISCOVERY", "1")
        mock_handle = MagicMock()

        with patch("src.runtime.mdns_discovery.is_mdns_available", return_value=True):
            with patch("src.runtime.mdns_discovery.stop_service") as mock_stop:
                # Injecte un handle existant directement sur la fonction route
                from web.routes.peers import mdns_advertise
                mdns_advertise._handle = mock_handle  # type: ignore[attr-defined]
                r = authed.post("/api/mdns/advertise", json={"stop": True}, headers=AUTH)

        assert r.status_code == 200
        d = r.json()
        assert d["advertising"] is False
        mock_stop.assert_called_once_with(mock_handle)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
