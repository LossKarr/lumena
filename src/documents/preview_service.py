"""Cached, non-ReAct preview generation for document templates."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from .template_catalog import TemplateCatalog
from .template_models import TemplateRecord
from .template_renderer import TemplateRenderer


class PreviewService:
    def __init__(self, catalog: TemplateCatalog, cache_root: Path, *, logo_store=None):
        self.catalog = catalog
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.renderer = TemplateRenderer()
        self.logo_store = logo_store
        self._locks: dict[str, asyncio.Lock] = {}

    def content_hash(self, record: TemplateRecord, source: str, sample_data: dict[str, Any]) -> str:
        payload = {
            "manifest": record.manifest.to_dict(),
            "source": source,
            "sample_data": sample_data,
            "active_logo": getattr(self.logo_store.active_record(), "sha256", "") if self.logo_store else "",
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def generate(self, template_id: str, *, force: bool = False) -> dict[str, Any]:
        record = self.catalog.get(template_id)
        if record.manifest.renderer == "html-jinja":
            source = self.catalog.read_source(record)
        else:
            from .document_security import sha256_file

            source = sha256_file(self.catalog.source_path(record))
        sample = self.catalog.read_sample_data(record)
        digest = self.content_hash(record, source, sample)
        target_dir = self.cache_root / digest
        html_path = target_dir / "preview.html"
        pdf_path = target_dir / "preview.pdf"
        thumb_path = target_dir / "thumbnail.webp"
        if thumb_path.is_file() and not force:
            return self._result(record, digest, html_path, pdf_path, thumb_path, cached=True)
        lock = self._locks.setdefault(digest, asyncio.Lock())
        async with lock:
            if thumb_path.is_file() and not force:
                return self._result(record, digest, html_path, pdf_path, thumb_path, cached=True)
            target_dir.mkdir(parents=True, exist_ok=True)
            if record.manifest.renderer == "html-jinja":
                html = self.renderer.render_html(
                    record,
                    source,
                    sample,
                    logo_data_uri=self.logo_store.active_data_uri() if self.logo_store else "",
                )
            else:
                native_path = target_dir / f"preview.{record.manifest.format}"
                await asyncio.to_thread(
                    self.renderer.render_native,
                    record,
                    self.catalog.source_path(record),
                    sample,
                    native_path,
                )
                from .template_import_service import TemplateImportService

                html = await asyncio.to_thread(
                    TemplateImportService.structural_preview_html,
                    native_path,
                    record.manifest.format,
                )
            html_path.write_text(html, encoding="utf-8")
            await asyncio.to_thread(self.renderer.render_pdf, html, pdf_path, base_url=record.directory)
            await self._screenshot_html(html, thumb_path)
            return self._result(record, digest, html_path, pdf_path, thumb_path, cached=False)

    async def _screenshot_html(self, html: str, output: Path) -> None:
        from playwright.async_api import async_playwright

        png = output.with_suffix(".png")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1)

                async def block_remote(route):
                    url = route.request.url
                    if url.startswith(("data:", "about:", "file:")):
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", block_remote)
                await page.set_content(html, wait_until="domcontentloaded")
                await page.screenshot(path=str(png), full_page=True)
            finally:
                await browser.close()
        from PIL import Image

        def convert() -> None:
            with Image.open(png) as image:
                image = image.convert("RGB")
                image.thumbnail((420, 594))
                image.save(output, "WEBP", quality=86, method=6)
            png.unlink(missing_ok=True)

        await asyncio.to_thread(convert)

    @staticmethod
    def _result(
        record: TemplateRecord,
        digest: str,
        html_path: Path,
        pdf_path: Path,
        thumb_path: Path,
        *,
        cached: bool,
    ) -> dict[str, Any]:
        return {
            "template_id": record.manifest.id,
            "content_hash": digest,
            "html_path": str(html_path),
            "pdf_path": str(pdf_path),
            "thumbnail_path": str(thumb_path),
            "cached": cached,
        }
