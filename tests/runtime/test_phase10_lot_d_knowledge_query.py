"""Lot D Phase 10 — Tests Knowledge Query read-only.

Couvre :
Route POST /api/peer/knowledge/query :
- peer unknown refusé (token sans correspondance)
- peer blocked refusé
- trusted sans scope knowledge.query refusé (403)
- trusted avec scope knowledge.query accepté
- token lié à from_instance_id — usurpation refusée
- query avec secret pattern refusée (422)
- réponse ne contient pas mémoire brute (contenu tronqué)
- réponse ne contient aucun token
- user_id respecté (multi-user mock)
- max_summary_chars respecté
- source_count correct
- audit knowledge_query_started / completed / refused écrit
- aucun import mémoire automatique (no write call)
- Lumena non initialisée → 500

Tool query_peer_knowledge (handler ReAct) :
- flag off → liste vide
- flag on → handler enregistré
- peer absent → refus
- peer blocked → refus
- trusted sans scope → refus
- token outbound absent → refus
- SSRF → refus avant HTTP
- secret dans query → refus
- HTTP 200 → résultat retourné, aucun token
- timeout → erreur propre
- source_count et confidence présents dans sortie
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


def _write_registry(path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


TRUSTED_PEER_KQ = {
    "instance_id": "peer-kq",
    "instance_name": "Lumena Labo",
    "host": "192.168.1.88",
    "port": 8081,
    "capabilities": ["knowledge"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "peer_token_hash": "deadbeef" * 8,
    "peer_token_outbound": "tok-kq-out",
    "allowed_scopes": ["chat", "knowledge.query"],
}

TRUSTED_PEER_NO_KQ = {**TRUSTED_PEER_KQ, "instance_id": "peer-nokq",
                      "allowed_scopes": ["chat"]}
BLOCKED_PEER = {**TRUSTED_PEER_KQ, "instance_id": "peer-blk", "trust": "blocked"}


def _make_lumena_mock(memories: list | None = None) -> MagicMock:
    """Construit un mock LumenaCore avec get_user_memory."""
    mem_mock = MagicMock()
    mem_mock.recall = MagicMock(return_value=memories or [])
    lumena = MagicMock()
    lumena.get_user_memory = MagicMock(return_value=mem_mock)
    return lumena


def _make_memory(content: str, score: float = 0.8,
                 memory_type: str = "semantic") -> MagicMock:
    m = MagicMock()
    m.content = content
    m.score = score
    m.memory_type = memory_type
    m.timestamp = datetime.now(timezone.utc)
    m.importance = 0.7
    return m


def _client_with_peer(tmp_path, monkeypatch, peer: dict,
                      lumena_mock=None) -> TestClient:
    reg_file = tmp_path / "peer_registry.json"
    _write_registry(reg_file, {peer["instance_id"]: peer})
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    app = _make_app()
    app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
    if lumena_mock is not None:
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", lumena_mock)
    return TestClient(app, raise_server_exceptions=False)


PAYLOAD_BASE = {
    "query": "Qu'est-ce que Redis ?",
    "from_instance_id": "peer-kq",
    "from_user_id": "local:owner",
    "actor_id": "lumena_agent",
    "max_results": 3,
    "max_summary_chars": 500,
}


# ── Tests : authentification et trust ────────────────────────────────────────

class TestKnowledgeQueryAuth:

    def test_usurpation_refusee(self, tmp_path, monkeypatch):
        """Token appartient à peer-kq mais from_instance_id prétend être peer-autre."""
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-autre"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 403
        assert "peer-autre" in r.json()["detail"] or "Usurpation" in r.json()["detail"]

    def test_peer_blocked_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, BLOCKED_PEER,
                                   _make_lumena_mock())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-blk"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 403
        assert "bloquée" in r.json()["detail"].lower() or "blocked" in r.json()["detail"].lower()

    def test_trusted_sans_scope_kq_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_NO_KQ,
                                   _make_lumena_mock())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-nokq"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 403
        assert "knowledge.query" in r.json()["detail"]

    def test_trusted_avec_scope_kq_ok(self, tmp_path, monkeypatch):
        memories = [_make_memory("Redis est une base de données clé-valeur.")]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        data = r.json()
        assert data["source_count"] == 1
        assert "Redis" in data["answer_summary"]


# ── Tests : isolation user_id ─────────────────────────────────────────────────

class TestKnowledgeQueryUserIsolation:
    """Le peer distant ne peut jamais choisir le user_id local."""

    def test_multi_user_from_user_id_ignored(self, tmp_path, monkeypatch):
        """MULTI_USER_ENABLED=1 + from_user_id arbitraire → get_user_memory("local:owner")."""
        monkeypatch.setattr("src.runtime.user_profile.MULTI_USER_ENABLED", True,
                            raising=False)
        called_with = []
        mem_mock = MagicMock()
        mem_mock.recall = MagicMock(return_value=[])
        lumena = MagicMock()

        def _capture_get_user_memory(user_id="local:owner"):
            called_with.append(user_id)
            return mem_mock

        lumena.get_user_memory = _capture_get_user_memory

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ, lumena)
        payload = {**PAYLOAD_BASE, "from_user_id": "telegram:42"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 200
        assert all(uid == "local:owner" for uid in called_with), (
            f"get_user_memory appelé avec {called_with!r} au lieu de local:owner"
        )

    def test_arbitrary_from_user_id_cannot_access_other_memory(self, tmp_path, monkeypatch):
        """from_user_id="admin" ou autre valeur → toujours local:owner, jamais admin."""
        called_with = []
        mem_mock = MagicMock()
        mem_mock.recall = MagicMock(return_value=[])
        lumena = MagicMock()

        def _capture(user_id="local:owner"):
            called_with.append(user_id)
            return mem_mock

        lumena.get_user_memory = _capture
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ, lumena)
        for from_uid in ("admin", "root", "system", "peer-kq", "telegram:99"):
            called_with.clear()
            payload = {**PAYLOAD_BASE, "from_user_id": from_uid}
            client.post("/api/peer/knowledge/query", json=payload)
            assert called_with == ["local:owner"], (
                f"from_user_id={from_uid!r} a fait appeler get_user_memory({called_with!r})"
            )

    def test_audit_peut_mentionner_from_user_id_sans_utiliser_comme_autorite(
        self, tmp_path, monkeypatch
    ):
        """Le from_user_id peut apparaître dans les logs mais ne conditionne pas l'accès."""
        mem_mock = MagicMock()
        mem_mock.recall = MagicMock(return_value=[_make_memory("Test")])
        lumena = MagicMock()
        lumena.get_user_memory = MagicMock(return_value=mem_mock)

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ, lumena)
        payload = {**PAYLOAD_BASE, "from_user_id": "telegram:42"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        # La requête doit réussir (la mémoire est celle de local:owner)
        assert r.status_code == 200
        lumena.get_user_memory.assert_called_with(user_id="local:owner")

    def test_single_user_fallback_local_owner(self, tmp_path, monkeypatch):
        """En mode single-user, from_user_id ignoré, local:owner utilisé."""
        called_with = []
        mem_mock = MagicMock()
        mem_mock.recall = MagicMock(return_value=[])
        lumena = MagicMock()

        def _capture(user_id="local:owner"):
            called_with.append(user_id)
            return mem_mock

        lumena.get_user_memory = _capture
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ, lumena)
        payload = {**PAYLOAD_BASE, "from_user_id": "local:owner"}
        client.post("/api/peer/knowledge/query", json=payload)
        assert called_with == ["local:owner"]


# ── Tests : sécurité query ────────────────────────────────────────────────────

class TestKnowledgeQuerySecurity:

    def test_query_avec_bearer_token_refusee(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock())
        payload = {**PAYLOAD_BASE,
                   "query": "Voici mon token : Bearer eyJhbGci.eyJzdW.secret"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 422
        assert "secret" in r.json()["detail"].lower()

    def test_query_avec_hex_secret_refusee(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock())
        secret = "f" * 40
        payload = {**PAYLOAD_BASE, "query": f"mon hash : {secret}"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 422

    def test_reponse_sans_token_brut(self, tmp_path, monkeypatch):
        memories = [_make_memory("Contenu normal sans secret.")]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        body = r.text
        assert "tok-kq-out" not in body
        assert "deadbeef" not in body
        assert TRUSTED_PEER_KQ.get("peer_token_hash", "")[:8] not in body

    def test_aucun_import_memoire_automatique(self, tmp_path, monkeypatch):
        """recall() appelé mais jamais save()/store()/add()."""
        mem_mock = MagicMock()
        mem_mock.recall = MagicMock(return_value=[_make_memory("Test")])
        mem_mock.save = MagicMock()
        mem_mock.store = MagicMock()
        lumena = MagicMock()
        lumena.get_user_memory = MagicMock(return_value=mem_mock)

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ, lumena)
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        mem_mock.save.assert_not_called()
        mem_mock.store.assert_not_called()


# ── Tests : contenu de la réponse ─────────────────────────────────────────────

class TestKnowledgeQueryResponse:

    def test_source_count_correct(self, tmp_path, monkeypatch):
        memories = [_make_memory(f"Fait #{i}") for i in range(3)]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert r.json()["source_count"] == 3

    def test_max_summary_chars_respecte(self, tmp_path, monkeypatch):
        long_content = "A" * 2000
        memories = [_make_memory(long_content)]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        payload = {**PAYLOAD_BASE, "max_summary_chars": 200}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 200
        assert len(r.json()["answer_summary"]) <= 201  # 200 + "…"

    def test_aucun_resultat_retourne_message_vide(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock([]))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        data = r.json()
        assert data["source_count"] == 0
        assert data["confidence"] == 0.0
        assert "aucune" in data["answer_summary"].lower()

    def test_confidence_est_moyenne_scores(self, tmp_path, monkeypatch):
        memories = [
            _make_memory("Fait A", score=0.8),
            _make_memory("Fait B", score=0.6),
        ]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert abs(r.json()["confidence"] - 0.7) < 0.01

    def test_tags_contient_types_memoire(self, tmp_path, monkeypatch):
        memories = [
            _make_memory("Épisode 1", memory_type="episodic"),
            _make_memory("Fait 1", memory_type="semantic"),
        ]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        tags = r.json()["tags"]
        assert "episodic" in tags
        assert "semantic" in tags

    def test_origin_instance_id_present(self, tmp_path, monkeypatch):
        import src.utils.paths as _paths
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock([_make_memory("Test")]))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert r.json()["origin_instance_id"] == "self-001"

    def test_contenu_brut_tronque(self, tmp_path, monkeypatch):
        """Chaque fragment de mémoire est tronqué à 300 chars max."""
        long_mem = _make_memory("X" * 1000)
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock([long_mem]))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        # Le contenu brut de 1000 chars ne doit pas apparaître intégralement
        assert "X" * 400 not in r.json()["answer_summary"]

    def test_lumena_non_initialisee_retourne_500(self, tmp_path, monkeypatch):
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", None)
        app = _make_app()
        reg_file = tmp_path / "peer_registry.json"
        _write_registry(reg_file, {"peer-kq": TRUSTED_PEER_KQ})
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        app.dependency_overrides[peers_module.verify_peer_token] = lambda: TRUSTED_PEER_KQ
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 500


# ── Tests : audit ─────────────────────────────────────────────────────────────

class TestKnowledgeQueryAudit:

    def test_audit_started_and_completed(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        memories = [_make_memory("Réponse audit test")]
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock(memories))
        r = client.post("/api/peer/knowledge/query", json=PAYLOAD_BASE)
        assert r.status_code == 200
        events = [e["event"] for e in audited]
        assert "knowledge_query_started" in events
        assert "knowledge_query_completed" in events

    def test_audit_refused_on_scope_missing(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_NO_KQ,
                                   _make_lumena_mock())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-nokq"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 403
        events = [e["event"] for e in audited]
        assert "knowledge_query_refused" in events

    def test_audit_refused_on_secret_query(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_KQ,
                                   _make_lumena_mock())
        secret = "a" * 40
        payload = {**PAYLOAD_BASE, "query": f"token : {secret}"}
        r = client.post("/api/peer/knowledge/query", json=payload)
        assert r.status_code == 422
        events = [e["event"] for e in audited]
        assert "knowledge_query_refused" in events


# ── Tests : handler ReAct query_peer_knowledge ────────────────────────────────

TRUSTED_PEER_DICT = {
    "instance_id": "peer-kq",
    "instance_name": "Lumena Labo",
    "host": "192.168.1.88",
    "port": 8081,
    "trust": "trusted",
    "peer_token_hash": "deadbeef" * 8,
    "peer_token_outbound": "SECRET_OUTBOUND_NEVER_EXPOSE",
    "allowed_scopes": ["chat", "knowledge.query"],
}


async def _call_kq_handler(monkeypatch, tmp_path, peer_dict=None,
                           instance_id="peer-kq", query="Qu'est-ce que Redis ?",
                           env_flag="1", http_response=None, http_exc=None):
    registry = {}
    if peer_dict is not None:
        registry[peer_dict["instance_id"]] = peer_dict

    reg_file = tmp_path / "peer_registry.json"
    reg_file.write_text(json.dumps(registry), encoding="utf-8")

    from src.reasoning.handlers import peer_knowledge as mod
    monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg_file)
    monkeypatch.setenv("LUMENA_PEER_COLLABORATION", env_flag)

    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")

    if http_exc is not None:
        async def _bad(*a, **kw): raise http_exc
        monkeypatch.setattr("httpx.AsyncClient.post", _bad)
    elif http_response is not None:
        mock_resp = MagicMock()
        mock_resp.status_code = http_response.get("status_code", 200)
        mock_resp.json.return_value = http_response.get("json", {})

        async def _ok(*a, **kw): return mock_resp
        monkeypatch.setattr("httpx.AsyncClient.post", _ok)

    return await mod.query_peer_knowledge_handler(
        MagicMock(), instance_id=instance_id, query=query,
    )


async def _call_propose_handler(
    monkeypatch,
    tmp_path,
    peer_dict=None,
    env_flag="1",
    title="Redis cache strategy",
    summary="Use Redis for short-lived cache entries.",
    tags=None,
    source_refs=None,
    http_response=None,
):
    registry = {}
    if peer_dict is not None:
        registry[peer_dict["instance_id"]] = peer_dict

    reg_file = tmp_path / "peer_registry.json"
    reg_file.write_text(json.dumps(registry), encoding="utf-8")

    from src.reasoning.handlers import peer_knowledge as mod
    monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg_file)
    monkeypatch.setenv("LUMENA_PEER_COLLABORATION", env_flag)

    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")

    calls = []
    if http_response is not None:
        mock_resp = MagicMock()
        mock_resp.status_code = http_response.get("status_code", 200)
        mock_resp.json.return_value = http_response.get("json", {})

        async def _ok(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return mock_resp
        monkeypatch.setattr("httpx.AsyncClient.post", _ok)

    result = await mod.propose_peer_knowledge_handler(
        MagicMock(),
        instance_id=(peer_dict or TRUSTED_PEER_DICT)["instance_id"],
        title=title,
        summary=summary,
        tags=tags,
        source_refs=source_refs,
    )
    return result, calls


class TestQueryPeerKnowledgeHandler:

    def test_flag_off_returns_empty_defs(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "0")
        from src.reasoning.handlers import peer_knowledge as mod
        assert mod.get_peer_knowledge_handler_defs() == []

    def test_flag_on_returns_peer_knowledge_defs(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        from src.reasoning.handlers import peer_knowledge as mod
        defs = mod.get_peer_knowledge_handler_defs()
        names = {d.name for d in defs}
        assert "query_peer_knowledge" in names
        assert "propose_peer_knowledge" in names
        assert all(d.category == "peers" for d in defs)

    @pytest.mark.asyncio
    async def test_flag_off_handler_refuses(self, monkeypatch, tmp_path):
        result = await _call_kq_handler(monkeypatch, tmp_path,
                                        peer_dict=TRUSTED_PEER_DICT, env_flag="0")
        assert not result.success
        assert "LUMENA_PEER_COLLABORATION" in result.output

    @pytest.mark.asyncio
    async def test_peer_absent_refused(self, monkeypatch, tmp_path):
        result = await _call_kq_handler(monkeypatch, tmp_path, peer_dict=None,
                                        instance_id="peer-inexistant")
        assert not result.success

    @pytest.mark.asyncio
    async def test_peer_blocked_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "trust": "blocked"}
        result = await _call_kq_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "bloqué" in result.output.lower()

    @pytest.mark.asyncio
    async def test_trusted_sans_scope_kq_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "allowed_scopes": ["chat"]}
        result = await _call_kq_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "knowledge.query" in result.output

    @pytest.mark.asyncio
    async def test_no_outbound_token_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "peer_token_outbound": ""}
        result = await _call_kq_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "token" in result.output.lower()

    @pytest.mark.asyncio
    async def test_ssrf_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "host": "8.8.8.8"}
        result = await _call_kq_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "rfc1918" in result.output.lower() or "non autorisée" in result.output.lower()

    @pytest.mark.asyncio
    async def test_secret_in_query_refused(self, monkeypatch, tmp_path):
        secret = "f" * 40
        result = await _call_kq_handler(monkeypatch, tmp_path,
                                        peer_dict=TRUSTED_PEER_DICT,
                                        query=f"token : {secret}")
        assert not result.success
        assert secret not in result.output

    @pytest.mark.asyncio
    async def test_http_200_returns_summary(self, monkeypatch, tmp_path):
        result = await _call_kq_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_DICT,
            http_response={"status_code": 200, "json": {
                "answer_summary": "Redis est une base de données clé-valeur.",
                "confidence": 0.85,
                "source_count": 2,
                "tags": ["semantic"],
            }},
        )
        assert result.success
        assert "Redis" in result.output
        assert "0.85" in result.output
        assert "2" in result.output

    @pytest.mark.asyncio
    async def test_http_200_no_token_in_output(self, monkeypatch, tmp_path):
        result = await _call_kq_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_DICT,
            http_response={"status_code": 200, "json": {
                "answer_summary": "Réponse normale.",
                "confidence": 0.7, "source_count": 1, "tags": [],
            }},
        )
        assert result.success
        assert "SECRET_OUTBOUND_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_http_500_returns_error(self, monkeypatch, tmp_path):
        result = await _call_kq_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_DICT,
            http_response={"status_code": 500, "json": {}},
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_timeout_returns_proper_message(self, monkeypatch, tmp_path):
        import httpx
        result = await _call_kq_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_DICT,
            http_exc=httpx.TimeoutException("timed out"),
        )
        assert not result.success
        assert "timeout" in result.output.lower() or "répondu" in result.output.lower()

    @pytest.mark.asyncio
    async def test_module_registered_in_tool_registry(self):
        content = (
            Path(__file__).parents[2] / "src/reasoning/tool_registry.py"
        ).read_text(encoding="utf-8")
        assert "peer_knowledge" in content
        assert "get_peer_knowledge_handler_defs" in content


class TestProposePeerKnowledgeHandler:

    @pytest.mark.asyncio
    async def test_success_posts_controlled_summary_without_token_in_output(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "allowed_scopes": ["chat", "knowledge.share"]}
        result, calls = await _call_propose_handler(
            monkeypatch,
            tmp_path,
            peer_dict=peer,
            http_response={"status_code": 200, "json": {"knowledge_id": "k-remote"}},
        )

        assert result.success
        assert "k-remote" in result.output
        assert "SECRET_OUTBOUND_NEVER_EXPOSE" not in result.output
        assert calls
        kwargs = calls[0]["kwargs"]
        assert kwargs["headers"]["Authorization"] == "Bearer SECRET_OUTBOUND_NEVER_EXPOSE"
        assert kwargs["json"]["title"] == "Redis cache strategy"

    @pytest.mark.asyncio
    async def test_missing_knowledge_share_scope_refused_before_http(self, monkeypatch, tmp_path):
        result, calls = await _call_propose_handler(
            monkeypatch,
            tmp_path,
            peer_dict=TRUSTED_PEER_DICT,
            http_response={"status_code": 200, "json": {"knowledge_id": "k-remote"}},
        )

        assert not result.success
        assert "knowledge.share" in result.output
        assert calls == []

    @pytest.mark.asyncio
    async def test_secret_in_summary_refused_without_leaking_secret(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "allowed_scopes": ["chat", "knowledge.share"]}
        secret = "a" * 40
        result, calls = await _call_propose_handler(
            monkeypatch,
            tmp_path,
            peer_dict=peer,
            summary=f"do not send {secret}",
            http_response={"status_code": 200, "json": {"knowledge_id": "k-remote"}},
        )

        assert not result.success
        assert secret not in result.output
        assert calls == []

    @pytest.mark.asyncio
    async def test_secret_in_source_refs_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_DICT, "allowed_scopes": ["chat", "knowledge.share"]}
        result, calls = await _call_propose_handler(
            monkeypatch,
            tmp_path,
            peer_dict=peer,
            source_refs=["token:" + ("b" * 40)],
            http_response={"status_code": 200, "json": {"knowledge_id": "k-remote"}},
        )

        assert not result.success
        assert calls == []
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
