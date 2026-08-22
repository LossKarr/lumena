from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.documents.document_downloader import DocumentDownloadError, DocumentDownloader
from src.documents.document_library import DocumentLibrary
from src.documents.import_service import DocumentImportService


@pytest.fixture
def importer(tmp_path):
    library = DocumentLibrary(tmp_path / "documents.sqlite")
    return DocumentImportService(tmp_path / "library", library), library


@pytest.mark.asyncio
async def test_download_streams_imports_and_deduplicates(tmp_path, importer):
    service, library = importer
    payload = b"rapport telecharge et indexe"

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-disposition": 'attachment; filename="rapport.txt"'},
            content=payload,
        )

    downloader = DocumentDownloader(
        tmp_path / "staging", service, transport=httpx.MockTransport(handler), url_validator=lambda _url: None
    )
    first, duplicate_1 = await downloader.download("https://docs.example/rapport")
    second, duplicate_2 = await downloader.download("https://docs.example/rapport")
    assert duplicate_1 is False
    assert duplicate_2 is True
    assert first.id == second.id
    assert first.source_uri == "https://docs.example/rapport"
    assert first.metadata["rights_status"] == "unknown"
    assert first.metadata["rights_evidence"] == ""
    assert library.search("indexe")[0].id == first.id
    assert not list((tmp_path / "staging").glob("*.part"))


@pytest.mark.asyncio
async def test_every_redirect_is_revalidated(tmp_path, importer):
    service, _ = importer
    checked = []

    def validate(url: str):
        checked.append(url)
        if "private.invalid" in url:
            raise ValueError("SSRF")

    def handler(request: httpx.Request):
        return httpx.Response(302, headers={"location": "http://private.invalid/secret.pdf"})

    downloader = DocumentDownloader(
        tmp_path / "staging", service, transport=httpx.MockTransport(handler), url_validator=validate
    )
    with pytest.raises(ValueError, match="SSRF"):
        await downloader.download("https://public.example/start")
    assert checked == ["https://public.example/start", "http://private.invalid/secret.pdf"]


@pytest.mark.asyncio
async def test_content_length_limit_rejects_before_body(tmp_path, importer):
    service, _ = importer
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-length": "5000", "content-disposition": 'attachment; filename="large.pdf"'},
            content=b"%PDF-1.7",
        )
    )
    downloader = DocumentDownloader(
        tmp_path / "staging", service, max_bytes=100, transport=transport, url_validator=lambda _url: None
    )
    with pytest.raises(DocumentDownloadError, match="volumineux"):
        await downloader.download("https://docs.example/large.pdf")


@pytest.mark.asyncio
async def test_interrupted_download_resumes_with_range(tmp_path, importer):
    service, _ = importer
    calls = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"document "
            raise httpx.ReadError("connection lost")

    def handler(request: httpx.Request):
        calls.append(request.headers.get("range", ""))
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"content-disposition": 'attachment; filename="resume.txt"'},
                stream=BrokenStream(),
            )
        return httpx.Response(
            206,
            headers={"content-disposition": 'attachment; filename="resume.txt"'},
            content=b"repris",
        )

    transport = httpx.MockTransport(handler)
    downloader = DocumentDownloader(
        tmp_path / "staging", service, max_bytes=100, transport=transport, url_validator=lambda _url: None
    )
    with pytest.raises(httpx.ReadError, match="connection lost"):
        await downloader.download("https://docs.example/resume.txt")
    assert list((tmp_path / "staging").glob("*.part"))
    record, _ = await downloader.download("https://docs.example/resume.txt")
    assert calls == ["", "bytes=9-"]
    assert Path(record.path).read_bytes() == b"document repris"


@pytest.mark.asyncio
async def test_inspect_uses_headers_without_import(tmp_path, importer):
    service, library = importer
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": "42",
                "content-disposition": "attachment; filename*=UTF-8''contrat%20sign%C3%A9.pdf",
            },
        )
    )
    downloader = DocumentDownloader(
        tmp_path / "staging", service, transport=transport, url_validator=lambda _url: None
    )
    info = await downloader.inspect("https://docs.example/item")
    assert info.filename == "contrat signé.pdf"
    assert info.content_type == "application/pdf"
    assert info.size == 42
    assert info.rights_status == "unknown"
    assert info.rights_evidence == ""
    assert library.list() == []
