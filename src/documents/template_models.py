"""Strict data contracts used by the Document Studio catalog."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
import re


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
SUPPORTED_FORMATS = frozenset(
    {"pdf", "html", "docx", "xlsx", "pptx", "csv", "md", "ics", "vcf"}
)
SUPPORTED_RENDERERS = frozenset(
    {"html-jinja", "docx-native", "xlsx-native", "pptx-native", "structured"}
)


class TemplateValidationError(ValueError):
    """Raised when a manifest cannot be trusted or interpreted."""


@dataclass(frozen=True)
class TemplateManifest:
    schema_version: int
    id: str
    name: str
    kind: str
    format: str
    renderer: str
    origin: str = "custom"
    version: int = 1
    editable_fields: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    design: dict[str, Any] = field(default_factory=dict)
    sample_data: str = "sample-data.json"
    template_file: str = "template.html.j2"
    thumbnail: str = "thumbnail.webp"
    description: str = ""
    category: str = "general"
    aliases: tuple[str, ...] = field(default_factory=tuple)
    locale: str = "fr-FR"
    scope: str = "universal"
    compliance_level: str = "structure"
    jurisdictions: tuple[str, ...] = field(default_factory=tuple)
    legal_notice: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TemplateManifest":
        if not isinstance(raw, Mapping):
            raise TemplateValidationError("manifest must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise TemplateValidationError(f"unknown manifest fields: {', '.join(unknown)}")
        required = {"schema_version", "id", "name", "kind", "format", "renderer"}
        missing = sorted(k for k in required if raw.get(k) in (None, ""))
        if missing:
            raise TemplateValidationError(f"missing manifest fields: {', '.join(missing)}")
        fields = raw.get("editable_fields", ())
        if not isinstance(fields, (list, tuple)) or not all(isinstance(v, dict) for v in fields):
            raise TemplateValidationError("editable_fields must be a list of objects")
        design = raw.get("design", {})
        if not isinstance(design, dict):
            raise TemplateValidationError("design must be an object")
        aliases = raw.get("aliases", ())
        if not isinstance(aliases, (list, tuple)) or not all(
            isinstance(value, str) and value.strip() for value in aliases
        ):
            raise TemplateValidationError("aliases must be a list of non-empty strings")
        jurisdictions = raw.get("jurisdictions", ())
        if not isinstance(jurisdictions, (list, tuple)) or not all(
            isinstance(value, str) and value.strip() for value in jurisdictions
        ):
            raise TemplateValidationError("jurisdictions must be a list of non-empty strings")
        manifest = cls(
            schema_version=int(raw["schema_version"]),
            id=str(raw["id"]).strip().lower(),
            name=str(raw["name"]).strip(),
            kind=str(raw["kind"]).strip().lower(),
            format=str(raw["format"]).strip().lower().lstrip("."),
            renderer=str(raw["renderer"]).strip().lower(),
            origin=str(raw.get("origin", "custom")).strip().lower(),
            version=int(raw.get("version", 1)),
            editable_fields=tuple(dict(v) for v in fields),
            design=dict(design),
            sample_data=str(raw.get("sample_data", "sample-data.json")),
            template_file=str(raw.get("template_file", "template.html.j2")),
            thumbnail=str(raw.get("thumbnail", "thumbnail.webp")),
            description=str(raw.get("description", "")),
            category=str(raw.get("category", "general")).strip().lower(),
            aliases=tuple(str(value).strip() for value in aliases),
            locale=str(raw.get("locale", "fr-FR")).strip() or "fr-FR",
            scope=str(raw.get("scope", "universal")).strip().lower(),
            compliance_level=str(raw.get("compliance_level", "structure")).strip().lower(),
            jurisdictions=tuple(str(value).strip() for value in jurisdictions),
            legal_notice=str(raw.get("legal_notice", "")),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != 1:
            raise TemplateValidationError(f"unsupported schema_version: {self.schema_version}")
        if not _ID_RE.fullmatch(self.id):
            raise TemplateValidationError("invalid template id")
        if not self.name or len(self.name) > 120:
            raise TemplateValidationError("invalid template name")
        if not _ID_RE.fullmatch(self.kind):
            raise TemplateValidationError("invalid template kind")
        if self.format not in SUPPORTED_FORMATS:
            raise TemplateValidationError(f"unsupported format: {self.format}")
        if self.renderer not in SUPPORTED_RENDERERS:
            raise TemplateValidationError(f"unsupported renderer: {self.renderer}")
        native_pairs = {
            "docx-native": "docx",
            "xlsx-native": "xlsx",
            "pptx-native": "pptx",
        }
        if self.renderer in native_pairs and self.format != native_pairs[self.renderer]:
            raise TemplateValidationError("native renderer and output format must match")
        if self.origin not in {"builtin", "custom"}:
            raise TemplateValidationError("origin must be builtin or custom")
        if self.scope not in {"universal", "localized"}:
            raise TemplateValidationError("scope must be universal or localized")
        if self.compliance_level not in {"reference", "structure", "controlled"}:
            raise TemplateValidationError("invalid compliance_level")
        if self.scope == "localized" and not self.jurisdictions:
            raise TemplateValidationError("localized templates require jurisdictions")
        if self.version < 1:
            raise TemplateValidationError("version must be >= 1")
        for value in (self.sample_data, self.template_file, self.thumbnail):
            p = Path(value)
            if p.is_absolute() or ".." in p.parts:
                raise TemplateValidationError("manifest file paths must stay relative")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["editable_fields"] = list(self.editable_fields)
        data["aliases"] = list(self.aliases)
        data["jurisdictions"] = list(self.jurisdictions)
        return data


@dataclass(frozen=True)
class TemplateRecord:
    manifest: TemplateManifest
    directory: Path
    read_only: bool
    valid: bool = True
    error: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        data = self.manifest.to_dict()
        data.update(
            {
                "read_only": self.read_only,
                "valid": self.valid,
                "error": self.error,
                "has_thumbnail": (self.directory / self.manifest.thumbnail).is_file(),
            }
        )
        return data
