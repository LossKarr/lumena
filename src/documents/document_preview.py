"""Truthful thumbnails for library documents; unsupported formats return no preview."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess

from PIL import Image

from .document_library import DocumentLibrary


class DocumentPreviewService:
    def __init__(self, cache_root: Path, library: DocumentLibrary):
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.library = library

    async def thumbnail(self, document_id: str) -> Path | None:
        record = self.library.get(document_id)
        if record is None:
            raise KeyError(document_id)
        source = Path(record.path)
        target = self.cache_root / f"{record.sha256}.webp"
        if target.is_file():
            return target
        if record.format == "pdf":
            await asyncio.to_thread(self._pdf_thumbnail, source, target)
            if not target.is_file():
                await self._pdf_browser_thumbnail(source, target)
        elif record.format == "html":
            await self._html_thumbnail(source, target)
        elif record.format in {"docx", "xlsx", "pptx"}:
            from .template_import_service import TemplateImportService

            structural_html = await asyncio.to_thread(
                TemplateImportService.structural_preview_html,
                source,
                record.format,
            )
            html_path = target.with_suffix(".html")
            html_path.write_text(structural_html, encoding="utf-8")
            try:
                await self._html_thumbnail(html_path, target)
            finally:
                html_path.unlink(missing_ok=True)
        else:
            return None
        return target if target.is_file() else None

    @staticmethod
    def _pdf_thumbnail(source: Path, target: Path) -> None:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(source))
            try:
                page = document[0]
                bitmap = page.render(scale=1.5)
                image = bitmap.to_pil().convert("RGB")
                image.thumbnail((420, 594))
                image.save(target, "WEBP", quality=84, method=6)
                return
            finally:
                document.close()
        except Exception:
            pass
        executable = shutil.which("pdftoppm")
        if not executable:
            return
        png_base = target.with_suffix("")
        args = [executable, "-f", "1", "-singlefile", "-scale-to-x", "420", "-scale-to-y", "-1", "-png", str(source), str(png_base)]
        if Path(executable).suffix.lower() in {".cmd", ".bat"}:
            args = ["cmd", "/c", *args]
        proc = subprocess.run(
            args,
            capture_output=True, timeout=45, check=False,
        )
        png = png_base.with_suffix(".png")
        if proc.returncode != 0 or not png.is_file():
            return
        try:
            with Image.open(png) as image:
                image.convert("RGB").save(target, "WEBP", quality=84, method=6)
        finally:
            png.unlink(missing_ok=True)

    @staticmethod
    async def _html_thumbnail(source: Path, target: Path) -> None:
        from playwright.async_api import async_playwright

        png = target.with_suffix(".png")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 794, "height": 1123})
                await page.goto(source.resolve().as_uri(), wait_until="domcontentloaded")
                await page.screenshot(path=str(png), full_page=True)
            finally:
                await browser.close()
        try:
            with Image.open(png) as image:
                image = image.convert("RGB")
                image.thumbnail((420, 594))
                image.save(target, "WEBP", quality=84, method=6)
        finally:
            png.unlink(missing_ok=True)

    @staticmethod
    async def _pdf_browser_thumbnail(source: Path, target: Path) -> None:
        from playwright.async_api import async_playwright

        png = target.with_suffix(".png")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 794, "height": 1000})
                await page.goto(source.resolve().as_uri(), wait_until="load")
                await page.wait_for_timeout(500)
                await page.screenshot(path=str(png))
            finally:
                await browser.close()
        try:
            with Image.open(png) as image:
                image = image.convert("RGB")
                image.thumbnail((420, 594))
                image.save(target, "WEBP", quality=84, method=6)
        finally:
            png.unlink(missing_ok=True)
