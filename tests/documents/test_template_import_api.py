from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.documents.studio import DocumentStudio
from web.routes import document_studio as routes
from web.routes import deps


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    studio = DocumentStudio(
        tmp_path / "studio", builtin_root=ROOT / "assets" / "templates",
        output_root=tmp_path / "output",
    )
    monkeypatch.setattr(routes, "get_document_studio", lambda: studio)
    app = FastAPI()
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    app.include_router(routes.router)
    return TestClient(app)


def test_template_import_api_requires_review_before_publish(client):
    response = client.post(
        "/api/document-studio/template-imports?name=Rapport%20Nova&kind=rapport_nova",
        files={"file": ("nova.html", BytesIO(b"<html><body>Client [[client]]</body></html>"), "text/html")},
    )
    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["status"] == "draft"
    assert client.get("/api/document-studio/templates").json()["templates"][-1]["id"] != "rapport_nova"

    preview = client.post(f"/api/document-studio/template-imports/{draft['id']}/preview")
    assert preview.status_code == 200
    assert "Client client" in preview.json()["html"]

    published = client.post(
        f"/api/document-studio/template-imports/{draft['id']}/publish",
        json={"template_id": "rapport-nova"},
    )
    assert published.status_code == 200
    assert published.json()["template"]["id"] == "rapport-nova"
    assert any(item["id"] == "rapport-nova" for item in client.get("/api/document-studio/templates").json()["templates"])
