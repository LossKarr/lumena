from __future__ import annotations

import asyncio
from pathlib import Path
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from PIL import Image

from src.documents.studio import DocumentStudio
from web.routes import document_studio as routes
from web.routes import deps


@pytest.fixture
def studio(tmp_path):
    builtin = Path(__file__).parents[2] / "assets" / "templates"
    return DocumentStudio(tmp_path / "studio", builtin_root=builtin, output_root=tmp_path / "output")


@pytest.fixture
def client(studio, monkeypatch):
    monkeypatch.setattr(routes, "get_document_studio", lambda: studio)
    app = FastAPI()
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    app.include_router(routes.router)
    return TestClient(app)


def test_templates_api_lists_real_builtins(client):
    response = client.get("/api/document-studio/templates")
    assert response.status_code == 200
    templates = response.json()["templates"]
    assert len(templates) == 30
    assert all(item["read_only"] for item in templates)


def test_clone_edit_version_restore_and_default(client):
    cloned = client.post(
        "/api/document-studio/templates/facture/clone", json={"id": "facture-lumena", "name": "Facture Lumena"}
    )
    assert cloned.status_code == 200
    detail = client.get("/api/document-studio/templates/facture-lumena").json()
    detail["manifest"]["name"] = "Facture Lumena V2"
    saved = client.put(
        "/api/document-studio/templates/facture-lumena",
        json={"manifest": detail["manifest"], "source": detail["source"] + "\n<!-- version 2 -->", "sample_data": detail["sample_data_value"]},
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 2
    assert client.get("/api/document-studio/templates/facture-lumena/versions").json()["versions"] == [1]
    default = client.put(
        "/api/document-studio/defaults/facture/pdf", json={"template_id": "facture-lumena"}
    )
    assert default.status_code == 200
    assert client.get("/api/document-studio/templates/facture-lumena").json()["is_default"] is True
    restored = client.post("/api/document-studio/templates/facture-lumena/restore/1")
    assert restored.status_code == 200
    assert restored.json()["version"] == 3


def test_preview_is_real_webp_and_pdf(client):
    response = client.post("/api/document-studio/templates/facture/preview")
    assert response.status_code == 200
    payload = response.json()
    image = client.get(payload["thumbnail_url"])
    pdf = client.get(payload["pdf_url"])
    assert image.status_code == 200 and image.content.startswith(b"RIFF")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_logo_api_upload_activate_list_serve_and_delete(client):
    first = BytesIO(); Image.new("RGB", (80, 30), "#D97706").save(first, "PNG")
    second = BytesIO(); Image.new("RGB", (80, 30), "#0F766E").save(second, "PNG")
    uploaded_a = client.post(
        "/api/document-studio/logos?name=Marque%20A",
        files={"file": ("brand-a.png", first.getvalue(), "image/png")},
    )
    uploaded_b = client.post(
        "/api/document-studio/logos?name=Marque%20B",
        files={"file": ("brand-b.png", second.getvalue(), "image/png")},
    )
    assert uploaded_a.status_code == uploaded_b.status_code == 200
    logo_b = uploaded_b.json()
    assert client.put(f"/api/document-studio/logos/{logo_b['id']}/active").status_code == 200
    listed = client.get("/api/document-studio/logos").json()
    assert listed["active_id"] == logo_b["id"]
    assert sum(item["active"] for item in listed["logos"]) == 1
    content = client.get(f"/api/document-studio/logos/{logo_b['id']}/content")
    assert content.status_code == 200 and content.content.startswith(b"\x89PNG")
    assert client.delete(f"/api/document-studio/logos/{logo_b['id']}").status_code == 200


def test_upload_library_search_and_download(client):
    upload = client.post(
        "/api/document-studio/import",
        files={"file": ("note.txt", b"contenu studio retrouvable", "text/plain")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["record"]["id"]
    search = client.get("/api/document-studio/library/search", params={"q": "retrouvable"})
    assert [item["id"] for item in search.json()["documents"]] == [document_id]
    download = client.get(f"/api/document-studio/library/{document_id}/download")
    assert download.content == b"contenu studio retrouvable"


def test_builtin_cannot_be_overwritten(client):
    detail = client.get("/api/document-studio/templates/facture").json()
    response = client.put(
        "/api/document-studio/templates/facture",
        json={"manifest": detail, "source": detail["source"], "sample_data": detail["sample_data_value"]},
    )
    assert response.status_code == 400


def test_library_transactional_edit_endpoint(client, tmp_path):
    from docx import Document

    source = tmp_path / "original.docx"
    doc = Document(); doc.add_paragraph("Texte ancien"); doc.save(source)
    with source.open("rb") as handle:
        upload = client.post(
            "/api/document-studio/import",
            files={"file": ("original.docx", handle.read(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    document_id = upload.json()["record"]["id"]
    operations = [{"op": "replace_text", "find": "ancien", "replace": "nouveau"}]
    preview = client.post(f"/api/document-studio/library/{document_id}/edit/preview", json={"operations": operations})
    applied = client.post(f"/api/document-studio/library/{document_id}/edit/apply", json={"operations": operations})
    assert preview.status_code == 200
    assert applied.status_code == 200
    assert applied.json()["record"]["parent_id"] == document_id
    assert Path(upload.json()["record"]["path"]).is_file()


def test_generated_document_can_be_previewed_and_revised_from_library(client, studio):
    template = studio.catalog.get("devis")
    generated = asyncio.run(studio.generate(
        kind="devis",
        output_format="html",
        data=studio.catalog.read_sample_data(template),
        filename="devis-api",
    ))
    document_id = generated["record"]["id"]
    preview = client.post(
        f"/api/document-studio/library/{document_id}/revise/preview",
        json={"data": {"numero": "API-PREVIEW"}},
    )
    revised = client.post(
        f"/api/document-studio/library/{document_id}/revise",
        json={"data": {"numero": "API-REVISION"}},
    )
    assert preview.status_code == 200 and "API-PREVIEW" in preview.json()["html"]
    assert revised.status_code == 200
    assert revised.json()["record"]["parent_id"] == document_id


def test_library_search_exposes_provenance_filters(client):
    upload = client.post(
        "/api/document-studio/import",
        files={"file": ("source.txt", b"contenu filtre", "text/plain")},
    )
    assert upload.status_code == 200
    found = client.get(
        "/api/document-studio/library/search",
        params={"q": "filtre", "source": "studio_upload"},
    ).json()["documents"]
    missing = client.get(
        "/api/document-studio/library/search",
        params={"q": "filtre", "source": "mail"},
    ).json()["documents"]
    assert len(found) == 1
    assert missing == []


def test_all_studio_routes_require_admin_token_when_configured(studio, monkeypatch):
    monkeypatch.setattr(routes, "get_document_studio", lambda: studio)
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "secret")
    app = FastAPI(); app.include_router(routes.router)
    protected = TestClient(app)
    assert protected.get("/api/document-studio/templates").status_code == 401
    assert protected.post("/api/document-studio/templates/facture/preview").status_code == 401
    assert protected.get(
        "/api/document-studio/templates",
        headers={"Authorization": "Bearer secret"},
    ).status_code == 200
    assert protected.post(
        "/api/document-studio/templates/facture/preview",
        headers={"Authorization": "Bearer secret"},
    ).status_code == 200
