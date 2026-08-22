"""Safe, resumable document downloads feeding the unified import boundary."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx

from src.utils.url_safety import assert_url_safe

from .document_security import DocumentSecurityError, sanitize_document_filename
from .import_service import DocumentImportService
from .provenance import DocumentRecord


class DocumentDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteDocumentInfo:
    url: str
    final_url: str
    filename: str
    content_type: str
    size: int | None
    rights_status: str = "unknown"
    rights_evidence: str = ""


class DocumentDownloader:
    def __init__(
        self,
        staging_root: Path,
        importer: DocumentImportService,
        *,
        max_bytes: int = 50 * 1024 * 1024,
        timeout_s: float = 45.0,
        max_redirects: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        url_validator: Callable[[str], None] = assert_url_safe,
    ):
        self.staging_root = Path(staging_root)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.importer = importer
        self.max_bytes = max(1, int(max_bytes))
        self.timeout_s = max(1.0, float(timeout_s))
        self.max_redirects = max(0, int(max_redirects))
        self.transport = transport
        self.url_validator = url_validator

    async def inspect(self, url: str) -> RemoteDocumentInfo:
        async with self._client() as client:
            response, final_url = await self._request_following_redirects(client, "HEAD", url)
            if response.status_code in {405, 501}:
                await response.aclose()
                response, final_url = await self._request_following_redirects(
                    client, "GET", url, headers={"Range": "bytes=0-0"}
                )
            try:
                response.raise_for_status()
                return self._remote_info(url, final_url, response.headers)
            finally:
                await response.aclose()

    async def download(
        self,
        url: str,
        *,
        source_kind: str = "web_download",
        metadata: dict | None = None,
    ) -> tuple[DocumentRecord, bool]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        meta_path = self.staging_root / f"{key}.name"
        existing_name = meta_path.read_text(encoding="utf-8").strip() if meta_path.exists() else ""

        async with self._client() as client:
            headers: dict[str, str] = {}
            part_candidates = list(self.staging_root.glob(f"{key}-*.part"))
            part_path = part_candidates[0] if part_candidates else None
            current_size = part_path.stat().st_size if part_path and part_path.exists() else 0
            if current_size:
                headers["Range"] = f"bytes={current_size}-"
            response, final_url = await self._request_following_redirects(client, "GET", url, headers=headers)
            try:
                response.raise_for_status()
                info = self._remote_info(url, final_url, response.headers, fallback_name=existing_name)
                if info.size is not None and info.size > self.max_bytes:
                    raise DocumentDownloadError(
                        f"Document trop volumineux: {info.size} octets (limite {self.max_bytes})"
                    )
                filename = sanitize_document_filename(info.filename)
                wanted_part = self.staging_root / f"{key}-{filename}.part"
                if part_path and part_path != wanted_part:
                    part_path.replace(wanted_part)
                part_path = wanted_part
                meta_path.write_text(filename, encoding="utf-8")
                append = bool(current_size and response.status_code == 206)
                if not append:
                    current_size = 0
                mode = "ab" if append else "wb"
                with part_path.open(mode) as handle:
                    async for chunk in response.aiter_bytes():
                        current_size += len(chunk)
                        if current_size > self.max_bytes:
                            raise DocumentDownloadError(
                                f"Document trop volumineux (limite {self.max_bytes} octets)"
                            )
                        handle.write(chunk)
                completed = self.staging_root / filename
                part_path.replace(completed)
                try:
                    return self.importer.import_file(
                        completed,
                        source_kind=source_kind,
                        source_uri=final_url,
                        metadata={
                            "requested_url": url,
                            "rights_status": "unknown",
                            "rights_evidence": "",
                            **(metadata or {}),
                        },
                    )
                finally:
                    completed.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
            except DocumentSecurityError:
                if part_path:
                    part_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                raise
            finally:
                await response.aclose()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": "Lumena-DocumentStudio/1.0"},
        )

    async def _request_following_redirects(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, str]:
        current = str(url).strip()
        for redirect_count in range(self.max_redirects + 1):
            self.url_validator(current)
            request = client.build_request(method, current, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, current
            location = response.headers.get("location", "").strip()
            await response.aclose()
            if not location:
                raise DocumentDownloadError("Redirection sans destination")
            if redirect_count >= self.max_redirects:
                raise DocumentDownloadError("Trop de redirections")
            current = urljoin(current, location)
        raise DocumentDownloadError("Trop de redirections")

    @staticmethod
    def _remote_info(
        requested_url: str,
        final_url: str,
        headers: httpx.Headers,
        *,
        fallback_name: str = "",
    ) -> RemoteDocumentInfo:
        disposition = headers.get("content-disposition", "")
        filename = ""
        encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        plain = re.search(r'filename="?([^";]+)', disposition, re.IGNORECASE)
        if encoded:
            filename = unquote(encoded.group(1).strip())
        elif plain:
            filename = plain.group(1).strip()
        if not filename:
            filename = fallback_name or unquote(Path(urlparse(final_url).path).name)
        if not filename:
            raise DocumentDownloadError("Nom de document introuvable dans l'URL ou les en-tetes")
        raw_size = headers.get("content-length", "").strip()
        size = int(raw_size) if raw_size.isdigit() else None
        return RemoteDocumentInfo(
            url=requested_url,
            final_url=final_url,
            filename=filename,
            content_type=headers.get("content-type", "").split(";", 1)[0].strip().lower(),
            size=size,
        )
