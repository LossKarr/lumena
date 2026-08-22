"""Sandboxed rendering for Document Studio templates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .template_models import TemplateRecord, TemplateValidationError
from .template_security import validate_template_source
from .document_design import inject_document_design
from .native_template_renderer import NativeTemplateRenderer


class TemplateRenderer:
    def __init__(self, *, max_output_chars: int = 2_000_000):
        self.max_output_chars = max_output_chars
        self.native = NativeTemplateRenderer()

    def render_html(
        self,
        record: TemplateRecord,
        source: str,
        data: dict[str, Any],
        *,
        design: dict[str, Any] | None = None,
        logo_data_uri: str = "",
    ) -> str:
        if record.manifest.renderer != "html-jinja":
            raise TemplateValidationError(f"renderer {record.manifest.renderer} cannot produce HTML")
        if not isinstance(data, dict):
            raise TemplateValidationError("render data must be an object")
        validate_template_source(source)
        from jinja2 import StrictUndefined, select_autoescape
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment(
            autoescape=select_autoescape(default_for_string=True, default=True),
            undefined=StrictUndefined,
            enable_async=False,
        )
        template = env.from_string(source)
        html = template.render(**data)
        html = inject_document_design(
            html,
            design if design is not None else record.manifest.design,
            logo_data_uri=logo_data_uri,
        )
        if len(html) > self.max_output_chars:
            raise TemplateValidationError("rendered document is too large")
        return html

    def render_pdf(self, html: str, output_path: Path, *, base_url: Path | None = None) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            import weasyprint

            weasyprint.HTML(string=html, base_url=str(base_url) if base_url else None).write_pdf(str(output))
        except Exception:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 794, "height": 1123})
                    page.set_content(html, wait_until="domcontentloaded")
                    page.pdf(path=str(output), format="A4", print_background=True)
                finally:
                    browser.close()
        return output

    def render_native(
        self,
        record: TemplateRecord,
        source_path: Path,
        data: dict[str, Any],
        output_path: Path,
    ) -> Path:
        expected = {
            "docx-native": "docx",
            "xlsx-native": "xlsx",
            "pptx-native": "pptx",
        }.get(record.manifest.renderer)
        if not expected or record.manifest.format != expected:
            raise TemplateValidationError("template is not a valid native Office model")
        return self.native.render(
            source_path,
            output_path,
            data,
            renderer=record.manifest.renderer,
        )
