"""Read-only builtin and versioned custom template catalog."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from .builtin_templates import (
    BUILTIN_ALIASES,
    BUILTIN_LABELS,
    builtin_compliance,
    builtin_design,
    builtin_sample_data,
)
from .template_models import TemplateManifest, TemplateRecord, TemplateValidationError
from .document_security import inspect_document
from .template_security import (
    atomic_write_json,
    atomic_write_text,
    normalize_template_id,
    resolve_within,
    validate_template_source,
)


class TemplateCatalog:
    """Catalog facade with immutable builtins and versioned user copies."""

    def __init__(self, storage_root: Path, builtin_root: Path):
        self.storage_root = Path(storage_root)
        self.builtin_root = Path(builtin_root)
        self.templates_root = self.storage_root / "templates"
        self.defaults_path = self.storage_root / "defaults.json"
        self.templates_root.mkdir(parents=True, exist_ok=True)
        self._intent_vocabulary_cache: dict[str, tuple[str, ...]] | None = None

    def intent_vocabulary(self) -> dict[str, tuple[str, ...]]:
        """Return model names and aliases grouped by canonical kind.

        The cache is invalidated by every catalog mutation. This keeps normal
        Agent requests free from repeated manifest I/O while making newly
        cloned or edited models routable immediately.
        """
        if self._intent_vocabulary_cache is None:
            grouped: dict[str, list[str]] = {}
            for record in self.list_templates():
                if not record.valid or record.manifest.kind == "invalid":
                    continue
                manifest = record.manifest
                values = grouped.setdefault(manifest.kind, [])
                values.extend((manifest.kind.replace("_", " "), manifest.name, *manifest.aliases))
            self._intent_vocabulary_cache = {
                kind: tuple(dict.fromkeys(value for value in values if value.strip()))
                for kind, values in grouped.items()
            }
        return dict(self._intent_vocabulary_cache)

    def list_templates(self) -> list[TemplateRecord]:
        records: list[TemplateRecord] = []
        records.extend(self._builtin_records())
        for directory in sorted(self.templates_root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                records.append(self._load_custom(directory))
            except Exception as exc:
                fallback = TemplateManifest(
                    schema_version=1,
                    id=normalize_template_id(directory.name),
                    name=directory.name,
                    kind="invalid",
                    format="pdf",
                    renderer="html-jinja",
                )
                records.append(TemplateRecord(fallback, directory, False, False, str(exc)))
        return records

    def get(self, template_id: str) -> TemplateRecord:
        wanted = normalize_template_id(template_id)
        for record in self.list_templates():
            if record.manifest.id == wanted:
                return record
        raise KeyError(wanted)

    def get_version(self, template_id: str, version: int) -> TemplateRecord:
        """Load the exact immutable template version recorded by a document recipe."""
        current = self.get(template_id)
        wanted_version = int(version)
        if current.manifest.version == wanted_version:
            return current
        if current.read_only:
            raise KeyError(f"{current.manifest.id}@{wanted_version}")
        snapshot = resolve_within(current.directory / "versions", str(wanted_version))
        if not snapshot.is_dir():
            raise KeyError(f"{current.manifest.id}@{wanted_version}")
        raw = json.loads(resolve_within(snapshot, "manifest.json").read_text(encoding="utf-8"))
        manifest = TemplateManifest.from_dict(raw)
        if manifest.id != current.manifest.id or manifest.version != wanted_version:
            raise TemplateValidationError("template snapshot identity does not match the recipe")
        source_path = resolve_within(snapshot, manifest.template_file)
        sample_path = resolve_within(snapshot, manifest.sample_data)
        self._validate_template_file(manifest, source_path)
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(sample, dict):
            raise TemplateValidationError("snapshot sample data must be an object")
        return TemplateRecord(manifest, snapshot, False)

    def clone_builtin(self, source_id: str, new_id: str, *, name: str | None = None) -> TemplateRecord:
        source = self.get(source_id)
        if not source.read_only:
            raise TemplateValidationError("source template is not builtin")
        target_id = normalize_template_id(new_id)
        if any(record.manifest.id == target_id for record in self._builtin_records()):
            raise TemplateValidationError("builtin template ids are reserved")
        target = resolve_within(self.templates_root, target_id)
        if target.exists():
            raise FileExistsError(target_id)
        target.mkdir(parents=True)
        source_text = (source.directory / source.manifest.template_file).read_text(encoding="utf-8")
        validate_template_source(source_text)
        manifest = replace(
            source.manifest,
            id=target_id,
            name=(name or f"{source.manifest.name} - copie").strip(),
            origin="custom",
            version=1,
        )
        atomic_write_text(target / manifest.template_file, source_text)
        atomic_write_json(target / manifest.sample_data, builtin_sample_data(source_id))
        atomic_write_json(target / "manifest.json", manifest.to_dict())
        (target / "versions").mkdir()
        self._intent_vocabulary_cache = None
        return TemplateRecord(manifest, target, False)

    def save_custom(
        self,
        template_id: str,
        *,
        manifest_data: dict[str, Any],
        template_source: str,
        sample_data: dict[str, Any],
    ) -> TemplateRecord:
        target_id = normalize_template_id(template_id)
        if any(record.manifest.id == target_id for record in self._builtin_records()):
            raise TemplateValidationError("builtin templates are read-only; clone before editing")
        manifest_raw = dict(manifest_data)
        manifest_raw["id"] = target_id
        manifest_raw["origin"] = "custom"
        existing: TemplateRecord | None = None
        target = resolve_within(self.templates_root, target_id)
        if target.exists():
            existing = self._load_custom(target)
            manifest_raw["version"] = existing.manifest.version + 1
        else:
            manifest_raw.setdefault("version", 1)
            target.mkdir(parents=True)
        manifest = TemplateManifest.from_dict(manifest_raw)
        validate_template_source(template_source)
        if not isinstance(sample_data, dict):
            raise TemplateValidationError("sample data must be an object")
        if existing:
            self._snapshot(existing)
        atomic_write_text(target / manifest.template_file, template_source)
        atomic_write_json(target / manifest.sample_data, sample_data)
        atomic_write_json(target / "manifest.json", manifest.to_dict())
        (target / "versions").mkdir(exist_ok=True)
        self._intent_vocabulary_cache = None
        return TemplateRecord(manifest, target, False)

    def save_custom_native(
        self,
        template_id: str,
        *,
        manifest_data: dict[str, Any],
        source_file: Path,
        sample_data: dict[str, Any],
    ) -> TemplateRecord:
        """Publish an immutable, macro-free Office source as a native template."""
        target_id = normalize_template_id(template_id)
        if any(record.manifest.id == target_id for record in self._builtin_records()):
            raise TemplateValidationError("builtin template ids are reserved")
        target = resolve_within(self.templates_root, target_id)
        if target.exists():
            raise FileExistsError(target_id)
        raw = dict(manifest_data)
        raw.update({"id": target_id, "origin": "custom", "version": 1})
        manifest = TemplateManifest.from_dict(raw)
        if manifest.renderer not in {"docx-native", "xlsx-native", "pptx-native"}:
            raise TemplateValidationError("native templates require a native Office renderer")
        source = Path(source_file)
        self._validate_template_file(manifest, source)
        if not isinstance(sample_data, dict):
            raise TemplateValidationError("sample data must be an object")
        target.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(source, target / manifest.template_file)
            atomic_write_json(target / manifest.sample_data, sample_data)
            atomic_write_json(target / "manifest.json", manifest.to_dict())
            (target / "versions").mkdir()
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        self._intent_vocabulary_cache = None
        return TemplateRecord(manifest, target, False)

    def update_custom_native(
        self,
        template_id: str,
        *,
        manifest_data: dict[str, Any],
        sample_data: dict[str, Any],
    ) -> TemplateRecord:
        """Version native metadata/sample data without rewriting the Office binary."""
        current = self.get(template_id)
        if current.read_only or current.manifest.renderer == "html-jinja":
            raise TemplateValidationError("template is not a custom native Office model")
        raw = dict(manifest_data)
        raw.update(
            {
                "id": current.manifest.id,
                "origin": "custom",
                "version": current.manifest.version + 1,
                "renderer": current.manifest.renderer,
                "format": current.manifest.format,
                "template_file": current.manifest.template_file,
            }
        )
        manifest = TemplateManifest.from_dict(raw)
        self._validate_template_file(manifest, self.source_path(current))
        if not isinstance(sample_data, dict):
            raise TemplateValidationError("sample data must be an object")
        self._snapshot(current)
        atomic_write_json(current.directory / manifest.sample_data, sample_data)
        atomic_write_json(current.directory / "manifest.json", manifest.to_dict())
        self._intent_vocabulary_cache = None
        return TemplateRecord(manifest, current.directory, False)

    def restore(self, template_id: str, version: int) -> TemplateRecord:
        current = self.get(template_id)
        if current.read_only:
            raise TemplateValidationError("builtin templates cannot be restored")
        snapshot = resolve_within(current.directory / "versions", str(int(version)))
        if not snapshot.is_dir():
            raise KeyError(version)
        self._snapshot(current)
        raw = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        raw["version"] = current.manifest.version + 1
        restored = TemplateManifest.from_dict(raw)
        source_path = snapshot / restored.template_file
        self._validate_template_file(restored, source_path)
        sample = json.loads((snapshot / restored.sample_data).read_text(encoding="utf-8"))
        if restored.renderer == "html-jinja":
            atomic_write_text(
                current.directory / restored.template_file,
                source_path.read_text(encoding="utf-8"),
            )
        else:
            shutil.copy2(source_path, current.directory / restored.template_file)
        atomic_write_json(current.directory / restored.sample_data, sample)
        atomic_write_json(current.directory / "manifest.json", restored.to_dict())
        self._intent_vocabulary_cache = None
        return TemplateRecord(restored, current.directory, False)

    def list_versions(self, template_id: str) -> list[int]:
        record = self.get(template_id)
        versions = record.directory / "versions"
        if not versions.is_dir():
            return []
        return sorted(int(p.name) for p in versions.iterdir() if p.is_dir() and p.name.isdigit())

    def set_default(self, kind: str, output_format: str, template_id: str | None) -> None:
        key = self._default_key(kind, output_format)
        data = self._read_defaults()
        if template_id is None:
            data.pop(key, None)
        else:
            record = self.get(template_id)
            if record.manifest.kind != kind or record.manifest.format != output_format:
                raise TemplateValidationError("template kind/format does not match default slot")
            data[key] = record.manifest.id
        atomic_write_json(self.defaults_path, data)

    def get_default(self, kind: str, output_format: str) -> TemplateRecord | None:
        template_id = self._read_defaults().get(self._default_key(kind, output_format))
        if not template_id:
            return None
        try:
            return self.get(template_id)
        except KeyError:
            return None

    def read_source(self, record: TemplateRecord) -> str:
        if record.manifest.renderer != "html-jinja":
            raise TemplateValidationError("native Office templates do not expose an editable text source")
        return (record.directory / record.manifest.template_file).read_text(encoding="utf-8")

    def source_path(self, record: TemplateRecord) -> Path:
        return resolve_within(record.directory, record.manifest.template_file)

    def read_sample_data(self, record: TemplateRecord) -> dict[str, Any]:
        if record.read_only:
            return builtin_sample_data(record.manifest.id)
        path = record.directory / record.manifest.sample_data
        return json.loads(path.read_text(encoding="utf-8"))

    def _builtin_records(self) -> Iterable[TemplateRecord]:
        if not self.builtin_root.is_dir():
            return []
        records: list[TemplateRecord] = []
        for path in sorted(self.builtin_root.glob("*.html.j2")):
            template_id = path.name.removesuffix(".html.j2")
            label, category = BUILTIN_LABELS.get(template_id, (template_id.replace("_", " ").title(), "general"))
            manifest = TemplateManifest(
                schema_version=1,
                id=template_id,
                name=label,
                kind=template_id,
                format="pdf",
                renderer="html-jinja",
                origin="builtin",
                version=1,
                category=category,
                aliases=BUILTIN_ALIASES.get(template_id, ()),
                design=builtin_design(template_id),
                description=f"Modèle intégré {label}",
                template_file=path.name,
                **builtin_compliance(template_id),
            )
            records.append(TemplateRecord(manifest, self.builtin_root, True))
        return records

    def _load_custom(self, directory: Path) -> TemplateRecord:
        manifest_path = resolve_within(directory, "manifest.json")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = TemplateManifest.from_dict(raw)
        if manifest.id != directory.name:
            raise TemplateValidationError("manifest id does not match directory")
        source_path = resolve_within(directory, manifest.template_file)
        sample_path = resolve_within(directory, manifest.sample_data)
        self._validate_template_file(manifest, source_path)
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(sample, dict):
            raise TemplateValidationError("sample data must be an object")
        return TemplateRecord(manifest, directory, False)

    @staticmethod
    def _validate_template_file(manifest: TemplateManifest, source_path: Path) -> None:
        if manifest.renderer == "html-jinja":
            validate_template_source(source_path.read_text(encoding="utf-8"))
            return
        expected = {
            "docx-native": "docx",
            "xlsx-native": "xlsx",
            "pptx-native": "pptx",
        }.get(manifest.renderer)
        if expected is None or manifest.format != expected:
            raise TemplateValidationError("native renderer and output format do not match")
        report = inspect_document(source_path)
        if report.format != expected:
            raise TemplateValidationError("native source format does not match its manifest")
        if "external_relationships" in report.warnings:
            raise TemplateValidationError("native templates cannot contain external relationships")

    def _snapshot(self, record: TemplateRecord) -> None:
        version_dir = resolve_within(record.directory / "versions", str(record.manifest.version))
        version_dir.mkdir(parents=True, exist_ok=False)
        for filename in ("manifest.json", record.manifest.template_file, record.manifest.sample_data):
            src = resolve_within(record.directory, filename)
            if src.is_file():
                shutil.copy2(src, version_dir / filename)
        atomic_write_json(
            version_dir / "snapshot.json",
            {"created_at": datetime.now(timezone.utc).isoformat(), "version": record.manifest.version},
        )

    def _read_defaults(self) -> dict[str, str]:
        if not self.defaults_path.is_file():
            return {}
        try:
            raw = json.loads(self.defaults_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _default_key(kind: str, output_format: str) -> str:
        kind_id = normalize_template_id(kind)
        fmt = str(output_format).strip().lower().lstrip(".")
        if not fmt:
            raise TemplateValidationError("format is required")
        return f"{kind_id}:{fmt}"
