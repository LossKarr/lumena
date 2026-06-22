"""Phase 10 Lot H — UI collaboration légère.

Vérifie que le panneau réseau expose les briques utiles sans transformer
l'interface en cockpit lourd :
- connaissances partagées
- tâches inter-Lumena locales
- activation simple de scopes
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "index.html"
PANELS = ROOT / "web" / "static" / "js" / "panels.js"
MAIN = ROOT / "web" / "static" / "js" / "main.js"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture(autouse=True)
def _clean_task_state():
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()
    old_recovery = peers_module._task_recovery_done
    peers_module._task_recovery_done = False
    yield
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()
    peers_module._task_recovery_done = old_recovery


class TestCollaborationUiStatic:
    def test_simple_view_has_history_targets(self):
        """Refonte 2026-06-17 : la vue simple expose l'HISTORIQUE des échanges
        (maître-détail), qui remplace les widgets bruts connaissances/tâches."""
        html = INDEX.read_text(encoding="utf-8")
        assert 'id="net-history-list"' in html
        assert 'id="net-history-detail"' in html
        assert 'id="net-history-search"' in html
        assert 'id="net-history-type-filter"' in html
        assert 'loadPeerHistory()' in html
        # Les anciens widgets bruts ne sont plus dans la vue simple.
        assert 'id="net-knowledge-title"' not in html
        assert 'id="net-task-list"' not in html

    def test_panels_loads_shared_knowledge_and_local_tasks(self):
        js = PANELS.read_text(encoding="utf-8")
        assert "export async function loadCollaborationPanel()" in js
        assert "/api/shared-knowledge" in js
        assert "/api/peer/local-tasks?limit=20" in js
        assert "createSharedKnowledgeFromUi" in js
        assert "shareKnowledgeFromUi" in js
        assert "revokeKnowledgeFromUi" in js
        assert "importKnowledgeFromUi" in js

    def test_scope_toggle_is_connected_to_existing_scope_routes(self):
        js = PANELS.read_text(encoding="utf-8")
        assert "export async function setPeerScope" in js
        assert "/api/peers/${encodeURIComponent(instanceId)}/scopes" in js
        assert "knowledge.share" in js
        assert "task.delegate" in js

    def test_main_exports_new_ui_functions(self):
        js = MAIN.read_text(encoding="utf-8")
        for name in (
            "loadCollaborationPanel",
            "createSharedKnowledgeFromUi",
            "shareKnowledgeFromUi",
            "revokeKnowledgeFromUi",
            "importKnowledgeFromUi",
            "setPeerScope",
        ):
            assert name in js

    def test_history_functions_wired(self):
        """L'historique read-only est branché : fonctions exportées (panels) et
        exposées au window (main). L'« envoi » se fait depuis le chat normal —
        plus de formulaire « Demander à l'équipe » dans le panel."""
        html = INDEX.read_text(encoding="utf-8")
        js = PANELS.read_text(encoding="utf-8")
        main = MAIN.read_text(encoding="utf-8")
        assert "export async function loadPeerHistory()" in js
        assert "export function filterPeerHistory()" in js
        assert "export function selectPeerExchange(" in js
        for name in ("loadPeerHistory", "filterPeerHistory", "selectPeerExchange"):
            assert name in main
        # Le formulaire « Demander à l'équipe » a été retiré de la vue simple.
        assert 'id="net-team-prompt"' not in html


class TestLocalPeerTasksAdminRoute:
    def test_local_tasks_requires_admin_auth(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        monkeypatch.setattr(peers_module, "_PEER_TASK_EVENTS_FILE", tmp_path / "task_events.jsonl")
        with peers_module._async_tasks_lock:
            peers_module._async_task_store.clear()

        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/api/peer/local-tasks")
        assert r.status_code in (401, 403)

    def test_local_tasks_returns_sanitized_live_entries(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        monkeypatch.setattr(peers_module, "_PEER_TASK_EVENTS_FILE", tmp_path / "task_events.jsonl")
        now = datetime.now(timezone.utc).isoformat()
        with peers_module._async_tasks_lock:
            peers_module._async_task_store.clear()
            peers_module._async_task_store["task-ui-1"] = {
                "status": "running",
                "result": "x" * 2000,
                "duration_ms": None,
                "from_instance_id": "peer-ui",
                "origin_instance_id": "self-ui",
                "created_at": now,
                "_created_mono": time.monotonic(),
                "_asyncio_task": object(),
            }

        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/api/peer/local-tasks", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        item = data["items"][0]
        assert item["task_id"] == "task-ui-1"
        assert item["status"] == "running"
        assert item["from_instance_id"] == "peer-ui"
        assert "_asyncio_task" not in json.dumps(item)
        assert len(item["result"]) <= 1200

    def test_local_tasks_includes_event_only_interrupted_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        events_file = tmp_path / "task_events.jsonl"
        monkeypatch.setattr(peers_module, "_PEER_TASK_EVENTS_FILE", events_file)
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(
            json.dumps({
                "ts": "2026-05-07T12:00:00+00:00",
                "task_id": "task-old",
                "from_instance_id": "peer-old",
                "origin_instance_id": "self-ui",
                "event": "task_async_interrupted",
                "status": "interrupted",
                "detail": "restart",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with peers_module._async_tasks_lock:
            peers_module._async_task_store.clear()
        monkeypatch.setattr(peers_module, "_task_recovery_done", True)

        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/api/peer/local-tasks", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["items"][0]["task_id"] == "task-old"
        assert data["items"][0]["status"] == "interrupted"
