"""Lot G Phase 10 - controlled shared knowledge."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


TRUSTED_SHARE_PEER = {
    "instance_id": "peer-share",
    "instance_name": "Lumena Share",
    "host": "192.168.1.50",
    "port": 8081,
    "capabilities": ["knowledge"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "peer_token_hash": "abc123" * 10 + "abcd",
    "peer_token_outbound": "tok-share-out",
    "allowed_scopes": ["chat", "knowledge.share"],
}

TRUSTED_NO_SHARE = {
    **TRUSTED_SHARE_PEER,
    "instance_id": "peer-no-share",
    "allowed_scopes": ["chat"],
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


def _write_registry(path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


def _client(tmp_path, monkeypatch, peer: dict = TRUSTED_SHARE_PEER, lumena=None) -> TestClient:
    reg_file = tmp_path / "peer_registry.json"
    store_file = tmp_path / "shared_knowledge.json"
    _write_registry(
        reg_file,
        {
            TRUSTED_SHARE_PEER["instance_id"]: TRUSTED_SHARE_PEER,
            TRUSTED_NO_SHARE["instance_id"]: TRUSTED_NO_SHARE,
        },
    )
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)

    import src.runtime.shared_knowledge as sk
    monkeypatch.setattr(sk, "SHARED_KNOWLEDGE_FILE", store_file)

    import src.utils.paths as paths
    monkeypatch.setattr(paths, "INSTANCE_ID", "self-share")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    import web.routes.deps as deps_mod
    if lumena is not None:
        monkeypatch.setattr(deps_mod, "lumena", lumena)

    app = _make_app()
    app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
    return TestClient(app, raise_server_exceptions=False)


def _admin_headers() -> dict:
    return {"Authorization": "Bearer tok"}


def _create_payload() -> dict:
    return {
        "title": "Redis cache strategy",
        "summary": "Use Redis for short-lived cache entries and invalidate on writes.",
        "tags": ["redis", "cache"],
        "confidence": 0.9,
        "source_refs": ["note:redis"],
    }


class TestSharedKnowledgeRuntime:

    def test_create_share_revoke_runtime(self, tmp_path, monkeypatch):
        import src.runtime.shared_knowledge as sk
        store = tmp_path / "shared.json"

        record = sk.create_knowledge_record(
            title="A",
            summary="B",
            owner_instance_id="self",
            origin_instance_id="self",
        )
        sk.add_knowledge(record, path=store)
        assert record["visibility"] == "private"

        shared = sk.share_knowledge(record["knowledge_id"], "peer-1", path=store)
        assert shared["visibility"] == "shared_with_peer"
        assert shared["shared_with_peer_id"] == "peer-1"
        assert sk.list_knowledge_for_peer("peer-1", path=store)[0]["knowledge_id"] == record["knowledge_id"]

        revoked = sk.revoke_knowledge(record["knowledge_id"], path=store)
        assert revoked["visibility"] == "private"
        assert sk.list_knowledge_for_peer("peer-1", path=store) == []

    def test_secret_like_summary_refused(self):
        import src.runtime.shared_knowledge as sk

        with pytest.raises(ValueError, match="summary"):
            sk.create_knowledge_record(
                title="secret",
                summary="Bearer eyJhbGci.eyJzdW.secret",
                owner_instance_id="self",
                origin_instance_id="self",
            )

    def test_import_marks_source_peer_metadata(self):
        import src.runtime.shared_knowledge as sk

        record = sk.create_knowledge_record(
            title="T",
            summary="Useful shared knowledge.",
            owner_instance_id="self",
            origin_instance_id="peer-x",
            confidence=0.75,
        )
        memory = MagicMock()
        memory.vector_store.add = MagicMock(return_value="mem-1")

        mem_id = sk.import_knowledge_to_memory(memory, record)

        assert mem_id == "mem-1"
        _, kwargs = memory.vector_store.add.call_args
        assert kwargs["metadata"]["source"] == "peer"
        assert kwargs["metadata"]["origin_instance_id"] == "peer-x"


class TestSharedKnowledgeRoutes:

    def test_admin_create_private_record(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        r = client.post("/api/shared-knowledge", json=_create_payload(), headers=_admin_headers())

        assert r.status_code == 200
        data = r.json()
        assert data["visibility"] == "private"
        assert data["origin_instance_id"] == "self-share"

    def test_share_requires_peer_scope(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        created = client.post("/api/shared-knowledge", json=_create_payload(), headers=_admin_headers()).json()

        r = client.post(
            f"/api/shared-knowledge/{created['knowledge_id']}/share",
            json={"peer_id": TRUSTED_NO_SHARE["instance_id"]},
            headers=_admin_headers(),
        )

        assert r.status_code == 403
        assert "knowledge.share" in r.json()["detail"]

    def test_share_then_peer_can_list_it(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        created = client.post("/api/shared-knowledge", json=_create_payload(), headers=_admin_headers()).json()
        shared = client.post(
            f"/api/shared-knowledge/{created['knowledge_id']}/share",
            json={"peer_id": TRUSTED_SHARE_PEER["instance_id"]},
            headers=_admin_headers(),
        )
        listed = client.get("/api/peer/knowledge/shared")

        assert shared.status_code == 200
        assert listed.status_code == 200
        data = listed.json()
        assert data["count"] == 1
        assert data["items"][0]["knowledge_id"] == created["knowledge_id"]

    def test_peer_propose_does_not_import_memory(self, tmp_path, monkeypatch):
        lumena = MagicMock()
        lumena.get_user_memory = MagicMock()
        client = _client(tmp_path, monkeypatch, lumena=lumena)

        r = client.post(
            "/api/peer/knowledge/propose",
            json={**_create_payload(), "from_instance_id": TRUSTED_SHARE_PEER["instance_id"]},
        )
        listed = client.get("/api/shared-knowledge", headers=_admin_headers())

        assert r.status_code == 200
        assert r.json()["origin_instance_id"] == TRUSTED_SHARE_PEER["instance_id"]
        assert r.json()["visibility"] == "private"
        assert listed.json()["count"] == 1
        lumena.get_user_memory.assert_not_called()

    def test_admin_import_uses_local_owner_memory(self, tmp_path, monkeypatch):
        mem = MagicMock()
        mem.vector_store.add = MagicMock(return_value="mem-imported")
        lumena = MagicMock()
        lumena.get_user_memory = MagicMock(return_value=mem)
        client = _client(tmp_path, monkeypatch, lumena=lumena)
        created = client.post("/api/shared-knowledge", json=_create_payload(), headers=_admin_headers()).json()

        r = client.post(f"/api/shared-knowledge/{created['knowledge_id']}/import", headers=_admin_headers())

        assert r.status_code == 200
        assert r.json()["imported_memory_id"] == "mem-imported"
        lumena.get_user_memory.assert_called_once_with(user_id="local:owner")
        _, kwargs = mem.vector_store.add.call_args
        assert kwargs["metadata"]["source"] == "peer"


class TestPhase11DKnowledgeImportReview:

    def test_import_candidates_recommend_peer_origin_high_confidence(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        proposed = client.post(
            "/api/peer/knowledge/propose",
            json={**_create_payload(), "from_instance_id": TRUSTED_SHARE_PEER["instance_id"]},
        )

        r = client.get("/api/shared-knowledge/import-candidates", headers=_admin_headers())

        assert proposed.status_code == 200
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["recommended"] == 1
        assert data["items"][0]["assessment"]["import_recommended"] is True

    def test_low_confidence_candidate_not_recommended(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post(
            "/api/peer/knowledge/propose",
            json={
                **_create_payload(),
                "confidence": 0.2,
                "from_instance_id": TRUSTED_SHARE_PEER["instance_id"],
            },
        )

        r = client.get("/api/shared-knowledge/import-candidates", headers=_admin_headers())

        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["assessment"]["import_recommended"] is False
        assert "low_confidence" in item["assessment"]["reasons"]

    def test_duplicate_candidate_needs_force_to_import(self, tmp_path, monkeypatch):
        mem = MagicMock()
        mem.vector_store.add = MagicMock(return_value="mem-forced")
        lumena = MagicMock()
        lumena.get_user_memory = MagicMock(return_value=mem)
        client = _client(tmp_path, monkeypatch, lumena=lumena)
        client.post("/api/shared-knowledge", json=_create_payload(), headers=_admin_headers())
        proposed = client.post(
            "/api/peer/knowledge/propose",
            json={**_create_payload(), "from_instance_id": TRUSTED_SHARE_PEER["instance_id"]},
        ).json()

        refused = client.post(
            f"/api/shared-knowledge/{proposed['knowledge_id']}/import",
            headers=_admin_headers(),
        )
        forced = client.post(
            f"/api/shared-knowledge/{proposed['knowledge_id']}/import",
            json={"force": True},
            headers=_admin_headers(),
        )

        assert refused.status_code == 409
        assert "duplicate" in refused.json()["detail"]["assessment"]["reasons"]
        assert forced.status_code == 200
        assert forced.json()["imported_memory_id"] == "mem-forced"

    def test_dismiss_candidate_marks_review_state(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        proposed = client.post(
            "/api/peer/knowledge/propose",
            json={**_create_payload(), "from_instance_id": TRUSTED_SHARE_PEER["instance_id"]},
        ).json()

        dismissed = client.post(
            f"/api/shared-knowledge/{proposed['knowledge_id']}/dismiss",
            json={"reason": "not relevant"},
            headers=_admin_headers(),
        )
        candidates = client.get("/api/shared-knowledge/import-candidates", headers=_admin_headers())

        assert dismissed.status_code == 200
        assert dismissed.json()["dismiss_reason"] == "not relevant"
        item = candidates.json()["items"][0]
        assert "dismissed" in item["assessment"]["reasons"]
