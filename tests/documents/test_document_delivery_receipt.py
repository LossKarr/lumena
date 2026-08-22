from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.documents.delivery_manifest import DocumentDeliveryProof
from src.documents.delivery_receipt import (
    MAX_DELIVERY_DOCUMENTS,
    build_open_delivery_final,
    load_delivery_receipt,
    save_delivery_receipt,
)
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.documents import open_document_delivery_handler


def _proof(path: Path, *, kind: str = "facture") -> DocumentDeliveryProof:
    content = path.read_bytes()
    return DocumentDeliveryProof(
        kind=kind,
        document_id=f"doc-{path.stem}",
        filename=path.name,
        path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        template_id=kind,
        format=path.suffix.lstrip("."),
        size=len(content),
        logo_id="logo-main",
        render_status="verified",
        render_verified=True,
        page_count=1,
    )


def test_receipt_is_content_addressed_idempotent_and_tamper_evident(tmp_path):
    output = tmp_path / "documents"
    output.mkdir()
    document = output / "facture.pdf"
    document.write_bytes(b"exact invoice")
    directory = tmp_path / "receipts"

    first = save_delivery_receipt(directory, [_proof(document)], requested_count=1)
    second = save_delivery_receipt(directory, [_proof(document)], requested_count=1)

    assert first == second
    assert first["id"].startswith("doclot_")
    path = directory / f"{first['id']}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["documents"][0]["path"] = str(output / "stale.pdf")
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_delivery_receipt(directory, first["id"])


def test_receipt_accepts_full_30_document_batch_and_rejects_31(tmp_path):
    output = tmp_path / "documents"
    output.mkdir()
    paths = []
    for index in range(MAX_DELIVERY_DOCUMENTS + 1):
        path = output / f"document-{index:02d}.pdf"
        path.write_bytes(f"document {index}".encode())
        paths.append(path)

    receipt = save_delivery_receipt(
        tmp_path / "receipts",
        [_proof(path, kind=f"kind-{index}") for index, path in enumerate(paths[:30])],
        requested_count=30,
    )
    assert len(receipt["documents"]) == 30
    assert load_delivery_receipt(tmp_path / "receipts", receipt["id"])["requested_count"] == 30

    with pytest.raises(ValueError, match="between 1 and 30"):
        save_delivery_receipt(
            tmp_path / "receipts",
            [_proof(path) for path in paths],
            requested_count=31,
        )


@pytest.mark.asyncio
async def test_open_delivery_opens_exact_13_and_never_stale(monkeypatch, tmp_path):
    from src.documents import studio as studio_module
    from src.reasoning.handlers import files as files_module

    root = tmp_path / "studio"
    output = tmp_path / "documents"
    output.mkdir()
    paths = []
    for index in range(13):
        path = output / f"generated-{index:02d}.pdf"
        path.write_bytes(f"document {index}".encode())
        paths.append(path)
    stale = output / "lettre_officielle.pdf"
    stale.write_bytes(b"old unrelated file")
    receipt = save_delivery_receipt(
        root / "delivery_receipts",
        [_proof(path, kind=f"kind-{index}") for index, path in enumerate(paths)],
        requested_count=13,
    )
    monkeypatch.setattr(
        studio_module,
        "get_document_studio",
        lambda: SimpleNamespace(root=root, output_root=output),
    )
    opened = []

    async def _open(_ctx, path=None, file_path=None):
        opened.append(Path(path or file_path).resolve())
        return HandlerResult.ok("opened", handler_name="open_file")

    monkeypatch.setattr(files_module, "open_file_handler", _open)
    result = await open_document_delivery_handler(None, receipt["id"])
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["opened"] == 13
    assert payload["failed"] == 0
    assert [item["page_count"] for item in payload["files"]] == [1] * 13
    assert [item["document_id"] for item in payload["files"]] == [
        f"doc-generated-{index:02d}" for index in range(13)
    ]
    assert [item["kind"] for item in payload["files"]] == [
        f"kind-{index}" for index in range(13)
    ]
    assert opened == [path.resolve() for path in paths]
    assert stale.resolve() not in opened


@pytest.mark.asyncio
async def test_open_delivery_rejects_changed_file(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    root = tmp_path / "studio"
    output = tmp_path / "documents"
    output.mkdir()
    path = output / "devis.pdf"
    path.write_bytes(b"delivered")
    receipt = save_delivery_receipt(
        root / "delivery_receipts", [_proof(path, kind="devis")], requested_count=1,
    )
    path.write_bytes(b"changed after delivery")
    monkeypatch.setattr(
        studio_module,
        "get_document_studio",
        lambda: SimpleNamespace(root=root, output_root=output),
    )

    result = await open_document_delivery_handler(None, receipt["id"])
    payload = json.loads(result.output)

    assert result.success is False
    assert payload["opened"] == 0
    assert "change" in payload["failures"][0]["error"]


@pytest.mark.asyncio
async def test_open_file_accepts_file_path_alias(monkeypatch, tmp_path):
    from src.reasoning.handlers.files import open_file_handler
    import src.reasoning.handlers.files as files_module

    path = tmp_path / "document.pdf"
    path.write_bytes(b"pdf")
    opened = []
    monkeypatch.setattr(files_module.os, "startfile", lambda value: opened.append(value))
    ctx = SimpleNamespace(resolve_path=lambda _value: path)

    result = await open_file_handler(ctx, file_path=str(path))

    assert result.success is True
    assert opened == [str(path)]


def test_open_delivery_final_is_exact_and_honest():
    text = build_open_delivery_final({
        "receipt_id": "doclot_0123456789abcdef01234567",
        "requested": 2,
        "opened": 1,
        "failed": 1,
        "files": [{
            "filename": "facture.pdf",
            "path": "C:/docs/facture.pdf",
            "page_count": 3,
        }],
    })

    assert "1/2" in text
    assert "facture.pdf" in text
    assert "C:/docs/facture.pdf" in text
    assert "3 page(s)" in text
    assert "1 fichier(s)" in text
