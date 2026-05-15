"""Tests Phase 8.3 (anti-SSRF), 8.4+8.5 (pairing code + peer token), 8.2 (firewall assisté)."""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

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
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_PORT", "8080")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


AUTH = {"Authorization": "Bearer tok"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.3 — Anti-SSRF
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiSSRF:

    def test_validate_private_ok(self):
        from web.routes.peers import _validate_peer_host
        for ip in ("192.168.1.10", "10.0.0.1", "172.16.5.5", "172.31.255.254"):
            _validate_peer_host(ip)  # ne doit pas lever

    def test_validate_loopback_raises(self):
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("127.0.0.1")
        assert exc.value.status_code == 422

    def test_validate_public_raises(self):
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("8.8.8.8")
        assert exc.value.status_code == 422

    def test_validate_link_local_raises(self):
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("169.254.0.1")
        assert exc.value.status_code == 422

    def test_validate_domain_name_raises(self):
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("evil.example.com")
        assert exc.value.status_code == 422

    def test_validate_multicast_raises(self):
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("224.0.0.1")
        assert exc.value.status_code == 422

    # P2 — Régression : plages que ip.is_private accepte mais RFC1918 refuse
    def test_validate_cgnat_raises(self):
        """100.64.0.0/10 (CGNAT, RFC 6598) : is_private=True mais pas RFC1918."""
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("100.64.0.1")
        assert exc.value.status_code == 422

    def test_validate_ietf_special_raises(self):
        """192.0.0.0/24 (IETF Protocol) : is_private=True mais pas RFC1918."""
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("192.0.0.1")
        assert exc.value.status_code == 422

    def test_validate_172_32_raises(self):
        """172.32.x.x : hors de 172.16.0.0/12, must be rejected."""
        from fastapi import HTTPException
        from web.routes.peers import _validate_peer_host
        with pytest.raises(HTTPException) as exc:
            _validate_peer_host("172.32.0.1")
        assert exc.value.status_code == 422

    def test_probe_peer_rejects_public_ip(self, authed):
        r = authed.post("/api/peer/probe",
                        json={"host": "8.8.8.8", "port": 8080}, headers=AUTH)
        assert r.status_code == 422

    def test_accept_pairing_rejects_public_ip(self, authed):
        r = authed.post("/api/peer/accept-pairing",
                        json={"host": "8.8.8.8", "port": 8080, "code": "ABC123"}, headers=AUTH)
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.4 — Codes de jumelage
# ─────────────────────────────────────────────────────────────────────────────

class TestPairingCode:

    def test_requires_auth(self, authed):
        r = authed.post("/api/peer/pairing-code")
        assert r.status_code in (401, 403)

    def test_generates_code(self, authed):
        r = authed.post("/api/peer/pairing-code", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert "code" in d
        assert len(d["code"]) == 6
        assert "expires_in" in d
        assert d["expires_in"] == 300

    def test_code_is_alphanumeric(self, authed):
        r = authed.post("/api/peer/pairing-code", headers=AUTH)
        code = r.json()["code"]
        assert code.isalnum(), f"Code non alphanumeric : {code!r}"

    def test_two_codes_are_different(self, authed):
        c1 = authed.post("/api/peer/pairing-code", headers=AUTH).json()["code"]
        c2 = authed.post("/api/peer/pairing-code", headers=AUTH).json()["code"]
        assert c1 != c2


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.5 — Échange de peer tokens
# ─────────────────────────────────────────────────────────────────────────────

class TestPeerTokenModule:

    def test_generate_token_length(self):
        from src.runtime.peer_tokens import generate_peer_token
        token = generate_peer_token()
        assert len(token) >= 40  # base64url(32 bytes) = 43 chars

    def test_hash_is_deterministic(self):
        from src.runtime.peer_tokens import hash_peer_token
        assert hash_peer_token("abc") == hash_peer_token("abc")

    def test_hash_differs_from_token(self):
        from src.runtime.peer_tokens import generate_peer_token, hash_peer_token
        t = generate_peer_token()
        assert hash_peer_token(t) != t

    def test_verify_correct(self):
        from src.runtime.peer_tokens import generate_peer_token, hash_peer_token, verify_peer_token
        t = generate_peer_token()
        h = hash_peer_token(t)
        assert verify_peer_token(t, h) is True

    def test_verify_wrong_token(self):
        from src.runtime.peer_tokens import generate_peer_token, hash_peer_token, verify_peer_token
        t = generate_peer_token()
        h = hash_peer_token(t)
        assert verify_peer_token("wrong-token", h) is False

    def test_pairing_code_length(self):
        from src.runtime.peer_tokens import generate_pairing_code
        code = generate_pairing_code()
        assert len(code) == 6

    def test_pairing_code_no_ambiguous_chars(self):
        from src.runtime.peer_tokens import generate_pairing_code
        for _ in range(100):
            code = generate_pairing_code()
            assert "0" not in code and "O" not in code
            assert "1" not in code and "I" not in code


class TestValidatePairingCode:

    def _gen_code(self, client, headers) -> str:
        r = client.post("/api/peer/pairing-code", headers=headers)
        assert r.status_code == 200
        return r.json()["code"]

    def test_invalid_code_returns_403(self, authed):
        r = authed.post("/api/peer/validate-pairing-code", json={
            "code": "XXXXXX",
            "from_instance_id": "remote-001",
            "from_instance_name": "Remote",
            "from_host": "192.168.1.20",
            "from_port": 8081,
            "peer_token_for_host": "tok",
        })
        assert r.status_code == 403

    def test_ssrf_rejected_in_validate(self, authed):
        code = self._gen_code(authed, AUTH)
        r = authed.post("/api/peer/validate-pairing-code", json={
            "code": code,
            "from_instance_id": "evil",
            "from_instance_name": "Evil",
            "from_host": "8.8.8.8",  # IP publique
            "from_port": 80,
            "peer_token_for_host": "tok",
        })
        assert r.status_code == 422

    def test_valid_code_returns_token(self, authed, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        monkeypatch.setenv("LUMENA_PORT", "8080")
        code = self._gen_code(authed, AUTH)
        r = authed.post("/api/peer/validate-pairing-code", json={
            "code": code,
            "from_instance_id": "remote-001",
            "from_instance_name": "Lumena Remote",
            "from_host": "192.168.1.20",
            "from_port": 8081,
            "peer_token_for_host": "token-remote-generated-for-us",
        })
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert "peer_token_for_requester" in d
        assert len(d["peer_token_for_requester"]) >= 40

    def test_code_is_single_use(self, authed, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        code = self._gen_code(authed, AUTH)
        payload = {
            "code": code,
            "from_instance_id": "r1",
            "from_instance_name": "R",
            "from_host": "192.168.1.20",
            "from_port": 8081,
            "peer_token_for_host": "tok",
        }
        r1 = authed.post("/api/peer/validate-pairing-code", json=payload)
        assert r1.status_code == 200
        # Deuxième tentative avec le même code → 403
        r2 = authed.post("/api/peer/validate-pairing-code", json=payload)
        assert r2.status_code == 403

    def test_validate_stores_peer_as_trusted(self, authed, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        code = self._gen_code(authed, AUTH)
        authed.post("/api/peer/validate-pairing-code", json={
            "code": code,
            "from_instance_id": "remote-999",
            "from_instance_name": "Remote",
            "from_host": "192.168.1.20",
            "from_port": 8081,
            "peer_token_for_host": "tok-for-host",
        })
        reg = json.loads((tmp_path / "peer_registry.json").read_text(encoding="utf-8"))
        peer = reg.get("remote-999", {})
        assert peer.get("trust") == "trusted"
        assert peer.get("peer_token_hash") is not None
        assert peer.get("peer_token_outbound") == "tok-for-host"
        assert peer.get("allowed_scopes") == ["chat"]


class TestRevokeToken:

    def _seed_trusted_peer(self, tmp_path, iid="r1"):
        reg = tmp_path / "peer_registry.json"
        reg.write_text(json.dumps({iid: {
            "instance_id": iid, "trust": "trusted",
            "peer_token_hash": "abc", "peer_token_outbound": "xyz",
        }}), encoding="utf-8")

    def test_requires_auth(self, authed):
        r = authed.post("/api/peer/revoke-token/r1")
        assert r.status_code in (401, 403)

    def test_unknown_peer_returns_404(self, authed):
        r = authed.post("/api/peer/revoke-token/ghost", headers=AUTH)
        assert r.status_code == 404

    def test_revoke_clears_tokens_and_sets_unknown(self, authed, tmp_path, monkeypatch):
        self._seed_trusted_peer(tmp_path)
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        with TestClient(_make_app()) as c:
            monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
            monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
            r = c.post("/api/peer/revoke-token/r1", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["trust"] == "unknown"
        reg = json.loads((tmp_path / "peer_registry.json").read_text(encoding="utf-8"))
        assert "peer_token_hash" not in reg["r1"]
        assert "peer_token_outbound" not in reg["r1"]


class TestVerifyPeerToken:

    def _make_registry(self, tmp_path, token: str) -> Path:
        from src.runtime.peer_tokens import hash_peer_token
        reg = tmp_path / "peer_registry.json"
        reg.write_text(json.dumps({"r1": {
            "instance_id": "r1", "trust": "trusted",
            "peer_token_hash": hash_peer_token(token),
            "host": "192.168.1.20", "port": 8081,
        }}), encoding="utf-8")
        return reg

    def test_no_token_returns_401(self, authed):
        r = authed.post("/api/peer/delegate", json={
            "task_id": "t1", "from_instance_id": "r1",
            "from_user_id": "u", "actor_id": "a",
            "scope": "chat", "prompt": "hello",
        })
        assert r.status_code == 401

    def test_wrong_token_returns_401(self, authed, tmp_path, monkeypatch):
        self._make_registry(tmp_path, "correct-token")
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        with TestClient(_make_app()) as c:
            monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
            monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
            r = c.post("/api/peer/delegate",
                       json={"task_id": "t1", "from_instance_id": "r1",
                             "from_user_id": "u", "actor_id": "a",
                             "scope": "chat", "prompt": "hi"},
                       headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401

    def test_admin_token_rejected_on_delegate(self, authed):
        """Le token admin ne doit pas être accepté sur /api/peer/delegate."""
        r = authed.post("/api/peer/delegate",
                        json={"task_id": "t1", "from_instance_id": "r1",
                              "from_user_id": "u", "actor_id": "a",
                              "scope": "chat", "prompt": "hi"},
                        headers=AUTH)
        assert r.status_code == 401

    def test_token_must_match_from_instance_id(self, tmp_path, monkeypatch):
        """P1 — Régression : token du pair A ne doit pas permettre de se déclarer pair B."""
        from src.runtime.peer_tokens import hash_peer_token, generate_peer_token
        token_a = generate_peer_token()
        # Registre : pair-A trusted avec token_a, pair-B trusted sans token
        reg = tmp_path / "peer_registry.json"
        reg.write_text(json.dumps({
            "pair-A": {
                "instance_id": "pair-A", "trust": "trusted",
                "peer_token_hash": hash_peer_token(token_a),
                "host": "192.168.1.10", "port": 8081,
            },
            "pair-B": {
                "instance_id": "pair-B", "trust": "trusted",
                "host": "192.168.1.20", "port": 8082,
            },
        }), encoding="utf-8")
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        with TestClient(_make_app()) as c:
            # Pair A utilise son token mais prétend être pair B → doit être refusé
            r = c.post("/api/peer/delegate",
                       json={"task_id": "t-spoof", "from_instance_id": "pair-B",
                             "from_user_id": "u", "actor_id": "a",
                             "scope": "chat", "prompt": "usurpation"},
                       headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 403, f"Usurpation non bloquée : HTTP {r.status_code}"
        assert "pair-A" in r.json()["detail"] or "pair-B" in r.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.2 — Pare-feu assisté
# ─────────────────────────────────────────────────────────────────────────────

class TestFirewallCommand:

    def test_requires_auth(self, authed):
        r = authed.get("/api/instance/firewall-command")
        assert r.status_code in (401, 403)

    def test_returns_command_fields(self, authed):
        r = authed.get("/api/instance/firewall-command", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        for field in ("platform", "command", "port", "rule_name", "description"):
            assert field in d, f"Champ manquant : {field}"

    def test_command_contains_port(self, authed, monkeypatch):
        monkeypatch.setenv("LUMENA_PORT", "9090")
        r = authed.get("/api/instance/firewall-command", headers=AUTH)
        d = r.json()
        assert "9090" in d["command"]
        assert d["port"] == 9090


class TestFirewallApply:

    def test_requires_auth(self, authed):
        r = authed.post("/api/instance/firewall-apply", json={"confirmed": True})
        assert r.status_code in (401, 403)

    def test_no_confirmation_returns_422(self, authed):
        r = authed.post("/api/instance/firewall-apply",
                        json={"confirmed": False}, headers=AUTH)
        assert r.status_code == 422

    def test_missing_confirmed_field_returns_422(self, authed):
        r = authed.post("/api/instance/firewall-apply",
                        json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.skipif(platform.system() == "Windows", reason="Non-Windows CI : teste le rejet")
    def test_non_windows_returns_422(self, authed):
        r = authed.post("/api/instance/firewall-apply",
                        json={"confirmed": True}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows uniquement")
    def test_windows_runs_netsh(self, authed):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK.", stderr="")
            r = authed.post("/api/instance/firewall-apply",
                            json={"confirmed": True}, headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert "ok" in d
        assert "port" in d
# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.10 — Multi-réseaux : GET /api/instance/network-interfaces
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkInterfaces:

    def test_requires_auth(self, authed):
        r = authed.get("/api/instance/network-interfaces")
        assert r.status_code in (401, 403)

    def test_returns_list(self, authed):
        r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert "interfaces" in d
        assert isinstance(d["interfaces"], list)

    def test_each_interface_has_required_fields(self, authed):
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["192.168.1.42"],
        ):
            r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        d = r.json()
        assert len(d["interfaces"]) >= 1
        iface = d["interfaces"][0]
        for field in ("ip", "network", "prefix_len", "label"):
            assert field in iface, f"Champ manquant : {field}"

    def test_network_is_slash24(self, authed):
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["10.0.5.77"],
        ):
            r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        ifaces = r.json()["interfaces"]
        assert any(iface["network"] == "10.0.5.0/24" for iface in ifaces)

    def test_no_duplicate_networks(self, authed):
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["192.168.1.10", "192.168.1.20"],
        ):
            r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        ifaces = r.json()["interfaces"]
        networks = [i["network"] for i in ifaces]
        assert len(networks) == len(set(networks)), "Sous-réseaux dupliqués"

    def test_multiple_subnets(self, authed):
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["192.168.1.10", "10.50.0.3"],
        ):
            r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        ifaces = r.json()["interfaces"]
        networks = {i["network"] for i in ifaces}
        assert "192.168.1.0/24" in networks
        assert "10.50.0.0/24" in networks

    def test_empty_when_no_lan(self, authed):
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=[],
        ):
            r = authed.get("/api/instance/network-interfaces", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["interfaces"] == []


class TestGetNetworkInterfacesUnit:
    """Tests unitaires de get_network_interfaces() sans HTTP."""

    def test_derives_slash24(self):
        from src.runtime.network_diagnostics import get_network_interfaces
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["172.16.3.200"],
        ):
            result = get_network_interfaces()
        assert len(result) == 1
        assert result[0]["network"] == "172.16.3.0/24"
        assert result[0]["prefix_len"] == 24

    def test_deduplicates_same_subnet(self):
        from src.runtime.network_diagnostics import get_network_interfaces
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["192.168.0.10", "192.168.0.50", "192.168.0.100"],
        ):
            result = get_network_interfaces()
        assert len(result) == 1

    def test_label_contains_ip_and_network(self):
        from src.runtime.network_diagnostics import get_network_interfaces
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=["10.1.2.3"],
        ):
            result = get_network_interfaces()
        label = result[0]["label"]
        assert "10.1.2.3" in label
        assert "10.1.2.0/24" in label

    def test_empty_when_no_lan(self):
        from src.runtime.network_diagnostics import get_network_interfaces
        with patch(
            "src.runtime.network_diagnostics.get_local_lan_ips",
            return_value=[],
        ):
            result = get_network_interfaces()
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Régression UI Phase 8.8 — Bouton "Détails" vue simple
# Tests statiques : vérifie que le HTML et le JS sont correctement câblés
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkDiagnosticUiWiring:
    """Régression statique : le diagnostic inline doit cibler net-diag-detail en vue simple."""

    _HTML = Path("web/index.html").read_text(encoding="utf-8")
    _JS   = Path("web/static/js/panels.js").read_text(encoding="utf-8")
    _MAIN = Path("web/static/js/main.js").read_text(encoding="utf-8")

    def test_net_diag_detail_exists_in_html(self):
        """#net-diag-detail doit être présent dans la vue simple (panel-infra-network)."""
        assert 'id="net-diag-detail"' in self._HTML, \
            "#net-diag-detail introuvable dans index.html"

    def test_net_diag_detail_inside_simple_status_card(self):
        """#net-diag-detail doit être dans la même carte que #net-simple-status."""
        # Les deux IDs doivent apparaître entre les mêmes balises card
        idx_status = self._HTML.index('id="net-simple-status"')
        idx_detail = self._HTML.index('id="net-diag-detail"')
        # detail vient après status dans le DOM
        assert idx_detail > idx_status, \
            "#net-diag-detail doit apparaître après #net-simple-status dans le HTML"
        # Et avant la prochaine carte de la vue simple (Liste des pairs)
        idx_peers_card = self._HTML.index('id="net-simple-peers"')
        assert idx_detail < idx_peers_card, \
            "#net-diag-detail doit être dans la carte statut, pas après la carte pairs"

    def test_load_network_diagnostic_targets_net_diag_detail_in_simple(self):
        """loadNetworkDiagnostic() doit référencer net-diag-detail pour la vue simple."""
        assert "net-diag-detail" in self._JS, \
            "loadNetworkDiagnostic() ne cible pas #net-diag-detail dans panels.js"

    def test_load_network_diagnostic_detects_simple_view(self):
        """loadNetworkDiagnostic() doit tester net-simple-view pour choisir le conteneur."""
        assert "net-simple-view" in self._JS, \
            "loadNetworkDiagnostic() ne détecte pas la vue simple"

    def test_hide_network_diagnostic_exported(self):
        """hideNetworkDiagnostic doit être exportée et exposée sur window."""
        assert "export function hideNetworkDiagnostic" in self._JS, \
            "hideNetworkDiagnostic non exportée dans panels.js"
        assert "hideNetworkDiagnostic" in self._MAIN, \
            "hideNetworkDiagnostic non exposée dans main.js"

    def test_back_button_simple_calls_hide(self):
        """En vue simple, le bouton retour appelle hideNetworkDiagnostic(), pas loadInstancesNetwork()."""
        # La logique doit différencier backFn selon la vue
        assert "hideNetworkDiagnostic()" in self._JS, \
            "Bouton Masquer (vue simple) ne référence pas hideNetworkDiagnostic()"

    def test_back_button_advanced_calls_load_instances(self):
        """En vue avancée, le bouton retour appelle loadInstancesNetwork()."""
        assert "loadInstancesNetwork()" in self._JS, \
            "Bouton Retour (vue avancée) ne référence pas loadInstancesNetwork()"

    def test_net_diag_detail_hidden_by_default(self):
        """#net-diag-detail doit être masqué par défaut (display:none)."""
        # Trouve la ligne contenant net-diag-detail
        for line in self._HTML.splitlines():
            if 'net-diag-detail' in line:
                assert 'display:none' in line, \
                    "#net-diag-detail doit avoir display:none par défaut"
                break

# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
