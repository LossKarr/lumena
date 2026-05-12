"""Lot B Phase 10 — Tests delegate_to_peer handler.

Couvre :
- flag LUMENA_PEER_COLLABORATION=0 → liste vide dans registry
- instance_id vide → refus propre
- peer absent du registre → refus
- peer trust=unknown → refus
- peer trust=blocked → refus
- peer trusted sans peer_token_outbound → refus
- scope inconnu (hors VALID_SCOPES) → refus
- scope connu mais absent de allowed_scopes → refus
- timeout borné (min 10, max 300)
- appel HTTP 200 OK → résultat retourné (aucun token dans sortie)
- appel HTTP != 200 → erreur propre
- réponse pair status=error → erreur propre
- timeout HTTP → erreur timeout propre
- connexion impossible → erreur injoignable propre
- aucun token dans audit, log ou résultat
- tool visible dans registry quand LUMENA_PEER_COLLABORATION=1
- tool absent du registry quand LUMENA_PEER_COLLABORATION=0
- prompt "demande à l'autre Lumena" → handler invocable via mock context
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures de données ────────────────────────────────────────────────────────

TRUSTED_PEER_FULL = {
    "instance_id": "peer-bbb",
    "instance_name": "Lumena Bureau",
    "host": "192.168.1.90",
    "port": 8081,
    "capabilities": ["chat"],
    "trust": "trusted",
    "peer_token_hash": "deadbeef" * 8,
    "peer_token_outbound": "SECRET_OUTBOUND_TOKEN_NEVER_EXPOSE",
    "allowed_scopes": ["chat", "knowledge.query"],
    "last_seen": datetime.now(timezone.utc).isoformat(),
}

TRUSTED_PEER_NO_OUTBOUND = {
    **TRUSTED_PEER_FULL,
    "instance_id": "peer-noout",
    "peer_token_outbound": "",
}

UNKNOWN_PEER = {
    **TRUSTED_PEER_FULL,
    "instance_id": "peer-unknown",
    "trust": "unknown",
}

BLOCKED_PEER = {
    **TRUSTED_PEER_FULL,
    "instance_id": "peer-blocked",
    "trust": "blocked",
}


def _make_registry(tmp_path: Path, peers: dict) -> Path:
    f = tmp_path / "peer_registry.json"
    f.write_text(json.dumps(peers, ensure_ascii=False), encoding="utf-8")
    return f


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ctx() -> MagicMock:
    return MagicMock()


async def _call_handler(monkeypatch, tmp_path, peer_dict=None, instance_id="peer-bbb",
                        prompt="Quel est ton état ?", scope="chat", timeout_sec=120,
                        env_flag="1", http_response=None, http_exc=None):
    """Helper : configure l'env et appelle le handler."""
    registry: dict = {}
    if peer_dict is not None:
        key = peer_dict.get("instance_id", instance_id)
        registry[key] = peer_dict

    reg_file = _make_registry(tmp_path, registry)

    from src.reasoning.handlers import peer_delegation as mod
    monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg_file)
    monkeypatch.setenv("LUMENA_PEER_COLLABORATION", env_flag)
    monkeypatch.setenv("LUMENA_PEER_AWARENESS", "0")

    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")

    if http_exc is not None:
        async def _bad_post(*a, **kw):
            raise http_exc
        monkeypatch.setattr("httpx.AsyncClient.post", _bad_post)
    elif http_response is not None:
        mock_resp = MagicMock()
        mock_resp.status_code = http_response.get("status_code", 200)
        mock_resp.json.return_value = http_response.get("json", {})

        async def _ok_post(*a, **kw):
            return mock_resp
        monkeypatch.setattr("httpx.AsyncClient.post", _ok_post)

    result = await mod.delegate_to_peer_handler(
        _make_ctx(),
        instance_id=instance_id,
        prompt=prompt,
        scope=scope,
        timeout_sec=timeout_sec,
    )
    return result


# ── Tests : feature flag ───────────────────────────────────────────────────────

class TestDelegateFeatureFlag:
    def test_flag_off_returns_empty_handler_defs(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "0")
        from src.reasoning.handlers import peer_delegation as mod
        defs = mod.get_peer_delegation_handler_defs()
        assert defs == []

    def test_flag_on_returns_one_handler_def(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        from src.reasoning.handlers import peer_delegation as mod
        defs = mod.get_peer_delegation_handler_defs()
        assert len(defs) == 1
        assert defs[0].name == "delegate_to_peer"

    @pytest.mark.asyncio
    async def test_flag_off_handler_refuses(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL, env_flag="0")
        assert not result.success
        assert "LUMENA_PEER_COLLABORATION" in result.output

    def test_handler_def_category_peers(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        from src.reasoning.handlers import peer_delegation as mod
        defs = mod.get_peer_delegation_handler_defs()
        assert defs[0].category == "peers"


# ── Tests : validation paramètres ─────────────────────────────────────────────

class TestDelegateParamValidation:
    @pytest.mark.asyncio
    async def test_empty_instance_id_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
                                     instance_id="")
        assert not result.success
        assert "instance_id" in result.output.lower()

    @pytest.mark.asyncio
    async def test_whitespace_instance_id_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
                                     instance_id="   ")
        assert not result.success
        assert "instance_id" in result.output.lower()

    @pytest.mark.asyncio
    async def test_empty_prompt_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
                                     prompt="")
        assert not result.success
        assert "prompt" in result.output.lower()

    @pytest.mark.asyncio
    async def test_whitespace_prompt_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
                                     prompt="   ")
        assert not result.success
        assert "prompt" in result.output.lower()


# ── Tests : scope ──────────────────────────────────────────────────────────────

class TestDelegateScopeValidation:
    @pytest.mark.asyncio
    async def test_unknown_scope_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
                                     scope="nonexistent_scope")
        assert not result.success
        assert "scope" in result.output.lower()

    @pytest.mark.asyncio
    async def test_scope_not_in_allowed_scopes_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "allowed_scopes": ["chat"]}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer,
                                     scope="knowledge.query")
        # knowledge.query est global-valide mais pas dans allowed_scopes du pair
        assert not result.success
        assert "knowledge.query" in result.output

    @pytest.mark.asyncio
    async def test_scope_in_allowed_scopes_passes(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "allowed_scopes": ["chat", "knowledge.query"]}
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=peer, scope="knowledge.query",
            http_response={"status_code": 200, "json": {"status": "ok", "response": "Réponse test"}},
        )
        assert result.success


# ── Tests : trust et token ─────────────────────────────────────────────────────

class TestDelegateTrustAndToken:
    @pytest.mark.asyncio
    async def test_peer_absent_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=None,
                                     instance_id="peer-inexistant")
        assert not result.success
        assert "inconnu" in result.output.lower() or "registre" in result.output.lower()

    @pytest.mark.asyncio
    async def test_peer_unknown_trust_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=UNKNOWN_PEER,
                                     instance_id="peer-unknown")
        assert not result.success
        assert "trusted" in result.output.lower() or "trust" in result.output.lower()

    @pytest.mark.asyncio
    async def test_peer_blocked_trust_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=BLOCKED_PEER,
                                     instance_id="peer-blocked")
        assert not result.success
        assert "bloqué" in result.output.lower() or "blocked" in result.output.lower()

    @pytest.mark.asyncio
    async def test_trusted_no_outbound_token_refused(self, monkeypatch, tmp_path):
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_NO_OUTBOUND,
                                     instance_id="peer-noout")
        assert not result.success
        assert "token" in result.output.lower()


# ── Tests : timeout borné ──────────────────────────────────────────────────────

class TestDelegateTimeoutBound:
    @pytest.mark.asyncio
    async def test_timeout_too_low_clamped(self, monkeypatch, tmp_path):
        # timeout=1 → clamped à 10, la requête est émise (on vérifie juste que ça ne plante pas à la validation)
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL, timeout_sec=1,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "OK"}},
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_timeout_too_high_clamped(self, monkeypatch, tmp_path):
        # timeout=9999 → clamped à 300
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL, timeout_sec=9999,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "OK"}},
        )
        assert result.success


# ── Tests : appel HTTP ─────────────────────────────────────────────────────────

class TestDelegateHttpCall:
    @pytest.mark.asyncio
    async def test_http_200_ok_returns_response(self, monkeypatch, tmp_path):
        response_text = "Voici mon analyse de la situation."
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "ok", "response": response_text}},
        )
        assert result.success
        assert response_text in result.output

    @pytest.mark.asyncio
    async def test_http_200_includes_peer_name(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "Réponse quelconque"}},
        )
        assert result.success
        assert "Lumena Bureau" in result.output

    @pytest.mark.asyncio
    async def test_http_500_returns_error(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 500, "json": {}},
        )
        assert not result.success
        assert "500" in result.output

    @pytest.mark.asyncio
    async def test_http_401_returns_error(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 401, "json": {}},
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_peer_response_status_error(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "error", "response": "Erreur interne"}},
        )
        assert not result.success
        assert "erreur" in result.output.lower()

    @pytest.mark.asyncio
    async def test_peer_response_empty_text(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "ok", "response": ""}},
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_timeout_exception_returns_timeout_message(self, monkeypatch, tmp_path):
        import httpx
        exc = httpx.TimeoutException("timed out")
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_exc=exc,
        )
        assert not result.success
        assert "timeout" in result.output.lower() or "répondu" in result.output.lower()

    @pytest.mark.asyncio
    async def test_connect_error_returns_injoignable_message(self, monkeypatch, tmp_path):
        import httpx
        exc = httpx.ConnectError("connection refused")
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_exc=exc,
        )
        assert not result.success
        assert "injoignable" in result.output.lower() or "démarré" in result.output.lower()


# ── Tests : sécurité token — jamais exposé ────────────────────────────────────

class TestDelegateTokenSecurity:
    @pytest.mark.asyncio
    async def test_outbound_token_not_in_success_message(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "Voici la réponse."}},
        )
        assert "SECRET_OUTBOUND_TOKEN_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_outbound_token_not_in_error_message_http(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 500, "json": {}},
        )
        assert "SECRET_OUTBOUND_TOKEN_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_outbound_token_not_in_timeout_message(self, monkeypatch, tmp_path):
        import httpx
        exc = httpx.TimeoutException("timed out")
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_exc=exc,
        )
        assert "SECRET_OUTBOUND_TOKEN_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_peer_token_hash_not_in_message(self, monkeypatch, tmp_path):
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "Test"}},
        )
        assert (TRUSTED_PEER_FULL["peer_token_hash"] or "")[:8] not in result.output


# ── Tests : registry integration ──────────────────────────────────────────────

class TestDelegateRegistryIntegration:
    def test_tool_registered_when_flag_on(self, monkeypatch):
        """delegate_to_peer visible dans les defs quand LUMENA_PEER_COLLABORATION=1."""
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        from src.reasoning.handlers import peer_delegation as mod
        defs = mod.get_peer_delegation_handler_defs()
        names = [d.name for d in defs]
        assert "delegate_to_peer" in names

    def test_tool_absent_when_flag_off(self, monkeypatch):
        """delegate_to_peer absent des defs quand LUMENA_PEER_COLLABORATION=0."""
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "0")
        from src.reasoning.handlers import peer_delegation as mod
        defs = mod.get_peer_delegation_handler_defs()
        assert defs == []

    def test_module_registered_in_tool_registry(self):
        """peer_delegation est dans _HANDLER_MODULES de tool_registry.py."""
        content = (
            __import__("pathlib").Path(__file__).parents[2]
            / "src/reasoning/tool_registry.py"
        ).read_text(encoding="utf-8")
        assert "peer_delegation" in content
        assert "get_peer_delegation_handler_defs" in content


# ── Tests : scénario conversationnel ──────────────────────────────────────────

class TestDelegateConversationalScenario:
    """Simule 'Demande à l'autre Lumena son avis sur ce problème'."""

    @pytest.mark.asyncio
    async def test_agent_can_delegate_chat_question(self, monkeypatch, tmp_path):
        peer_response = "J'ai analysé le problème : la solution est d'utiliser un cache Redis."
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt="Quel est ton avis sur ce problème de performance ?",
            scope="chat",
            http_response={"status_code": 200, "json": {"status": "ok", "response": peer_response}},
        )
        assert result.success
        assert peer_response in result.output
        assert "Lumena Bureau" in result.output
        assert "SECRET_OUTBOUND_TOKEN_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_agent_can_delegate_knowledge_query(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "allowed_scopes": ["chat", "knowledge.query"]}
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=peer,
            prompt="Cherche dans ta base de connaissances les informations sur Redis.",
            scope="knowledge.query",
            http_response={"status_code": 200, "json": {"status": "ok", "response": "Redis est..."}},
        )
        assert result.success
        assert "Redis" in result.output


# ── Tests : anti-SSRF ─────────────────────────────────────────────────────────

class TestDelegateAntiSSRF:
    @pytest.mark.asyncio
    async def test_public_ip_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": "8.8.8.8"}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "ssrf" in result.output.lower() or "rfc1918" in result.output.lower() or "non autorisée" in result.output.lower()

    @pytest.mark.asyncio
    async def test_loopback_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": "127.0.0.1"}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "rfc1918" in result.output.lower() or "non autorisée" in result.output.lower() or "ssrf" in result.output.lower()

    @pytest.mark.asyncio
    async def test_localhost_string_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": "localhost"}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success

    @pytest.mark.asyncio
    async def test_link_local_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": "169.254.1.1"}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_host_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": ""}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success

    @pytest.mark.asyncio
    async def test_rfc1918_host_passes_ssrf(self, monkeypatch, tmp_path):
        """192.168.1.57 est RFC1918 → l'appel HTTP est émis (pas bloqué par anti-SSRF)."""
        peer = {**TRUSTED_PEER_FULL, "host": "192.168.1.57"}
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=peer,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "OK"}},
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_10_block_rfc1918_passes(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_FULL, "host": "10.0.0.5"}
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=peer,
            http_response={"status_code": 200, "json": {"status": "ok", "response": "OK"}},
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_ssrf_refused_no_http_call_made(self, monkeypatch, tmp_path):
        """Vérifie qu'aucun appel HTTP n'est émis pour un host SSRF."""
        http_called = []

        async def _should_not_be_called(*a, **kw):
            http_called.append(True)
            raise AssertionError("HTTP call émis malgré SSRF refus")

        monkeypatch.setattr("httpx.AsyncClient.post", _should_not_be_called)
        peer = {**TRUSTED_PEER_FULL, "host": "8.8.8.8"}
        result = await _call_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert not http_called


# ── Tests : sanitization obligatoire sur chemin sortant ──────────────────────

class TestDelegateSanitizationEnforced:
    """Le handler doit refuser proprement si la sanitization détecte un secret."""

    @pytest.mark.asyncio
    async def test_prompt_with_bearer_token_refused(self, monkeypatch, tmp_path):
        """Prompt contenant 'Bearer <valeur>' → refus avant HTTP."""
        http_called = []

        async def _must_not_call(*a, **kw):
            http_called.append(True)
            raise AssertionError("HTTP appelé malgré secret dans prompt")

        monkeypatch.setattr("httpx.AsyncClient.post", _must_not_call)
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt="Voici mon token : Bearer eyJhbGci.eyJzdW.supersecret",
        )
        assert not result.success
        assert not http_called

    @pytest.mark.asyncio
    async def test_prompt_with_hex_secret_refused(self, monkeypatch, tmp_path):
        """Prompt contenant une chaîne hex 32+ chars → refus avant HTTP."""
        http_called = []

        async def _must_not_call(*a, **kw):
            http_called.append(True)
            raise AssertionError("HTTP appelé malgré secret dans prompt")

        monkeypatch.setattr("httpx.AsyncClient.post", _must_not_call)
        secret = "a" * 40
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt=f"Regarde ce hash : {secret}",
        )
        assert not result.success
        assert not http_called

    @pytest.mark.asyncio
    async def test_sanitization_failure_audited(self, monkeypatch, tmp_path):
        """Si sanitization échoue, l'audit doit contenir 'refused'."""
        audited = []

        def _fake_audit(event, iid, tid, scope, status, detail=""):
            audited.append({"event": event, "status": status})

        from src.reasoning.handlers import peer_delegation as mod
        monkeypatch.setattr(mod, "_audit", _fake_audit)

        secret = "b" * 40
        await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt=f"mon secret est {secret}",
        )
        assert any(e["status"] == "refused" for e in audited)

    @pytest.mark.asyncio
    async def test_normal_prompt_envelope_present(self, monkeypatch, tmp_path):
        """Prompt normal → envelope présente dans context['peer_message']."""
        captured = {}

        async def _capture_post(self_client, url, *, json=None, headers=None, **kw):
            captured.update(json or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok", "response": "OK"}
            return mock_resp

        monkeypatch.setattr("httpx.AsyncClient.post", _capture_post)
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt="Quel est ton état ?",
            http_response=None,
        )
        assert result.success
        ctx = captured.get("context", {})
        assert "peer_message" in ctx
        assert ctx["peer_message"].get("type") == "chat_delegate"

    @pytest.mark.asyncio
    async def test_sanitization_refusal_no_token_in_output(self, monkeypatch, tmp_path):
        """Le message d'erreur de refus ne doit pas contenir le contenu brut du prompt secret."""
        secret = "c" * 40
        result = await _call_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_FULL,
            prompt=f"mon secret : {secret}",
        )
        assert not result.success
        # Le secret brut ne doit pas apparaître dans le message de refus
        assert secret not in result.output


# ── Tests : .env.example ──────────────────────────────────────────────────────

class TestEnvExample:
    def test_lumena_peer_collaboration_in_env_example(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        assert env_example.exists(), ".env.example introuvable"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_PEER_COLLABORATION" in content

    def test_lumena_peer_collaboration_default_is_zero(self):
        env_example = Path(__file__).parents[2] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("LUMENA_PEER_COLLABORATION"):
                assert line.strip() == "LUMENA_PEER_COLLABORATION=0"
                return
        pytest.fail("LUMENA_PEER_COLLABORATION introuvable dans .env.example")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
