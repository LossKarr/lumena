"""Document Studio facade shared by API routes and additive agent tools."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from src.tools.search_hub import SearchHub
from src.utils.paths import DOCUMENT_STUDIO_DIR, TEMPLATES_DIR, WORKSPACE_DIR

from .document_downloader import DocumentDownloader
from .generation_recipe import (
    RECIPE_METADATA_KEY,
    StudioGenerationRecipe,
    merge_document_data,
)
from .brand_assets import BrandAssetStore
from .document_preview import DocumentPreviewService
from .delivery_service import DocumentDeliveryService
from .document_library import DocumentLibrary
from .import_service import DocumentImportService
from .preview_service import PreviewService
from .template_catalog import TemplateCatalog
from .template_renderer import TemplateRenderer
from .template_import_service import TemplateImportService
from .web_document_search import DocumentWebSearch


class DocumentStudio:
    def __init__(
        self,
        root: Path = DOCUMENT_STUDIO_DIR,
        *,
        builtin_root: Path = TEMPLATES_DIR,
        output_root: Path | None = None,
        search_hub: Any | None = None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.output_root = Path(output_root or (WORKSPACE_DIR / "documents"))
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.catalog = TemplateCatalog(self.root, Path(builtin_root))
        self.logos = BrandAssetStore(self.root / "logos")
        self.library = DocumentLibrary(self.root / "documents.sqlite3")
        self.importer = DocumentImportService(self.root / "library", self.library)
        self.previews = PreviewService(self.catalog, self.root / "previews", logo_store=self.logos)
        self.renderer = TemplateRenderer()
        self.template_imports = TemplateImportService(self.root / "template_imports", self.catalog)
        self.downloader = DocumentDownloader(self.root / "downloads", self.importer)
        self.document_previews = DocumentPreviewService(self.root / "document_previews", self.library)
        self.delivery = DocumentDeliveryService(self.output_root / "exports", self.library)
        self.web_search = DocumentWebSearch(search_hub or SearchHub())
        from src.tools.document_hub import DocumentHub
        from .conversion_service import DocumentConversionService
        from .document_edit_service import DocumentEditService
        hub = DocumentHub(self.output_root)
        self.edits = DocumentEditService(
            self.root / "versions", library=self.library, importer=self.importer, document_hub=hub
        )
        self.conversions = DocumentConversionService(
            library=self.library, importer=self.importer, document_hub=hub
        )

    def template_detail(self, template_id: str) -> dict[str, Any]:
        record = self.catalog.get(template_id)
        native = record.manifest.renderer != "html-jinja"
        return {
            **record.to_public_dict(),
            "manifest": record.manifest.to_dict(),
            "source": "" if native else self.catalog.read_source(record),
            "native_source": native,
            "sample_data_value": self.catalog.read_sample_data(record),
            "versions": self.catalog.list_versions(template_id),
            "is_default": bool(
                (default := self.catalog.get_default(record.manifest.kind, record.manifest.format))
                and default.manifest.id == record.manifest.id
            ),
        }

    def resolve_template(
        self, *, template_id: str = "", kind: str = "", output_format: str = "pdf"
    ):
        """Resolve the exact template that a generation call will render."""
        record = self.catalog.get(template_id) if template_id else self.catalog.get_default(kind, output_format)
        if record is None:
            if not kind:
                raise ValueError("template_id or kind is required")
            record = self.catalog.get(kind)
        return record

    async def generate(
        self,
        *,
        template_id: str = "",
        kind: str = "",
        output_format: str = "pdf",
        data: dict[str, Any],
        filename: str = "",
    ) -> dict[str, Any]:
        record = self.resolve_template(
            template_id=template_id, kind=kind, output_format=output_format,
        )
        if record.manifest.renderer == "html-jinja":
            fmt = str(output_format or record.manifest.format).lower().lstrip(".")
        else:
            requested = str(output_format or "").lower().lstrip(".")
            if requested not in {"", "pdf", record.manifest.format}:
                raise ValueError(
                    f"Le modèle natif {record.manifest.id} produit uniquement {record.manifest.format}"
                )
            fmt = record.manifest.format
        safe_stem = self._safe_stem(filename or record.manifest.id)
        logo = self.logos.active_record()
        recipe = StudioGenerationRecipe.create(
            template_id=record.manifest.id,
            template_version=record.manifest.version,
            kind=record.manifest.kind,
            output_format=fmt,
            data=data,
            filename_stem=safe_stem,
            logo_id=logo.id if logo else "",
        )
        return await self._render_and_import(record, recipe, parent_id="", operation="generate")

    def preview_revision(
        self, document_id: str, *, data: dict[str, Any], replace_data: bool = False
    ) -> dict[str, Any]:
        original, recipe, template = self._revision_context(document_id)
        revised_data = merge_document_data(recipe.data, data, replace=replace_data)
        html = self.renderer.render_html(
            template,
            self.catalog.read_source(template),
            revised_data,
            logo_data_uri=self._recipe_logo_data_uri(recipe),
        )
        return {
            "document_id": original.id,
            "template_id": recipe.template_id,
            "template_version": recipe.template_version,
            "data": revised_data,
            "html": html,
        }

    async def revise(
        self,
        document_id: str,
        *,
        data: dict[str, Any],
        replace_data: bool = False,
        output_format: str = "",
        filename: str = "",
    ) -> dict[str, Any]:
        original, recipe, template = self._revision_context(document_id)
        revised_data = merge_document_data(recipe.data, data, replace=replace_data)
        if revised_data == recipe.data and not output_format and not filename:
            raise ValueError("La révision ne contient aucune modification")
        fmt = str(output_format or recipe.output_format).lower().lstrip(".")
        if fmt not in {"pdf", "html"}:
            raise ValueError("Une révision Studio peut produire uniquement PDF ou HTML")
        revision_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        revised_recipe = StudioGenerationRecipe.create(
            template_id=recipe.template_id,
            template_version=recipe.template_version,
            kind=recipe.kind,
            output_format=fmt,
            data=revised_data,
            filename_stem=self._safe_stem(filename or f"{recipe.filename_stem}-revision-{revision_stamp}"),
            logo_id=recipe.logo_id,
        )
        return await self._render_and_import(
            template, revised_recipe, parent_id=original.id, operation="revise"
        )

    async def _render_and_import(
        self,
        record,
        recipe: StudioGenerationRecipe,
        *,
        parent_id: str,
        operation: str,
    ) -> dict[str, Any]:
        fmt = recipe.output_format
        safe_stem = recipe.filename_stem
        target = self.output_root / f"{safe_stem}.{fmt}"
        if record.manifest.renderer == "html-jinja":
            source = self.catalog.read_source(record)
            html = self.renderer.render_html(
                record,
                source,
                recipe.data,
                logo_data_uri=self._recipe_logo_data_uri(recipe),
            )
            if fmt == "html":
                target.write_text(html, encoding="utf-8")
            elif fmt == "pdf":
                await asyncio.to_thread(self.renderer.render_pdf, html, target, base_url=record.directory)
            else:
                raise ValueError(f"Studio renderer cannot generate {fmt} from HTML template")
        else:
            if fmt != record.manifest.format:
                raise ValueError("Le format de sortie doit correspondre au modèle Office natif")
            await asyncio.to_thread(
                self.renderer.render_native,
                record,
                self.catalog.source_path(record),
                recipe.data,
                target,
            )
        imported, duplicate = self.importer.import_file(
            target,
            source_kind="generated",
            source_uri=f"template:{record.manifest.id}",
            metadata={
                "template_id": record.manifest.id,
                "template_version": record.manifest.version,
                RECIPE_METADATA_KEY: recipe.to_dict(),
            },
        )
        if not duplicate:
            imported.template_id = record.manifest.id
            imported.parent_id = parent_id
            self.library.upsert(imported)
        if parent_id and not duplicate:
            parent = self.library.get(parent_id)
            if parent is None:
                raise KeyError(parent_id)
            from .provenance import DocumentTransformation

            transformation = DocumentTransformation.create(
                document_id=parent.id,
                operation=operation,
                input_sha256=parent.sha256,
                output_sha256=imported.sha256,
                details={
                    "output_document_id": imported.id,
                    "template_id": recipe.template_id,
                    "template_version": recipe.template_version,
                },
            )
            self.library.add_transformation(transformation)
            transformation_payload = asdict(transformation)
        else:
            transformation_payload = None
        thumbnail = await self.document_previews.thumbnail(imported.id)
        from .document_validation import validate_document_render

        render_proof = await asyncio.to_thread(
            validate_document_render,
            target,
            thumbnail,
            output_format=fmt,
            logo_id=recipe.logo_id,
            visual_fidelity="exact" if record.manifest.renderer == "html-jinja" else "structural",
        )
        return {
            "path": str(target),
            "record": imported.to_dict(include_content=False),
            "recipe": recipe.to_dict(),
            "render_proof": render_proof,
            "duplicate": duplicate,
            "transformation": transformation_payload,
        }

    def _revision_context(self, document_id: str):
        original = self.library.get(document_id)
        if original is None:
            raise KeyError(document_id)
        recipe = StudioGenerationRecipe.from_metadata(original.metadata)
        try:
            template = self.catalog.get_version(recipe.template_id, recipe.template_version)
        except KeyError as exc:
            raise ValueError(
                f"La version {recipe.template_version} du modèle {recipe.template_id} n'est plus disponible"
            ) from exc
        return original, recipe, template

    def _recipe_logo_data_uri(self, recipe: StudioGenerationRecipe) -> str:
        if not recipe.logo_id:
            return ""
        try:
            return self.logos.data_uri(recipe.logo_id)
        except KeyError as exc:
            raise ValueError(
                f"Le logo {recipe.logo_id} utilisé par ce document n'est plus disponible"
            ) from exc

    @staticmethod
    def _safe_stem(value: str) -> str:
        raw = Path(str(value or "document")).name
        if Path(raw).suffix.lower() in {".pdf", ".html"}:
            raw = Path(raw).stem
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw)
        return safe.strip("-_") or "document"

    @staticmethod
    def parse_json_object(value: Any, *, field: str) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} must be an object")
        return parsed


@lru_cache(maxsize=1)
def get_document_studio() -> DocumentStudio:
    return DocumentStudio()
