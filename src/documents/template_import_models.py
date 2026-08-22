"""Persistent contracts for user-supplied template drafts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import re


_DRAFT_ID = re.compile(r"^draft_[0-9a-f]{16}$")


@dataclass(frozen=True)
class TemplateImportDraft:
    id: str
    created_at: str
    updated_at: str
    status: str
    source_filename: str
    source_format: str
    source_sha256: str
    source_file: str
    name: str
    kind: str
    category: str = "custom"
    aliases: tuple[str, ...] = field(default_factory=tuple)
    renderer: str = "html-jinja"
    output_format: str = "pdf"
    fidelity: str = "semantic"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    detected_fields: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    template_source: str = ""
    sample_data: dict[str, Any] = field(default_factory=dict)
    published_template_id: str = ""

    @classmethod
    def create(cls, *, draft_id: str, source_filename: str, source_format: str,
               source_sha256: str, source_file: str, name: str, kind: str,
               template_source: str, sample_data: dict[str, Any],
               detected_fields: list[dict[str, Any]], fidelity: str,
               warnings: list[str]) -> "TemplateImportDraft":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=draft_id, created_at=now, updated_at=now, status="draft",
            source_filename=source_filename, source_format=source_format,
            source_sha256=source_sha256, source_file=source_file,
            name=name, kind=kind, template_source=template_source,
            sample_data=dict(sample_data), detected_fields=tuple(detected_fields),
            fidelity=fidelity, warnings=tuple(warnings),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TemplateImportDraft":
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown template draft fields: {', '.join(sorted(unknown))}")
        data = dict(raw)
        data["aliases"] = tuple(str(value) for value in data.get("aliases", ()))
        data["warnings"] = tuple(str(value) for value in data.get("warnings", ()))
        data["detected_fields"] = tuple(dict(value) for value in data.get("detected_fields", ()))
        data["sample_data"] = dict(data.get("sample_data", {}))
        draft = cls(**data)
        draft.validate()
        return draft

    def validate(self) -> None:
        if not _DRAFT_ID.fullmatch(self.id):
            raise ValueError("invalid template draft id")
        if self.status not in {"draft", "published"}:
            raise ValueError("invalid template draft status")
        valid_pairs = {
            ("html-jinja", "pdf"),
            ("html-jinja", "html"),
            ("docx-native", "docx"),
            ("xlsx-native", "xlsx"),
            ("pptx-native", "pptx"),
        }
        if (self.renderer, self.output_format) not in valid_pairs:
            raise ValueError("unsupported template draft renderer")
        if not self.name.strip() or not self.kind.strip():
            raise ValueError("template draft name and kind are required")
        if not isinstance(self.sample_data, dict):
            raise ValueError("template draft sample_data must be an object")

    def to_dict(self, *, include_source: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        data["warnings"] = list(self.warnings)
        data["detected_fields"] = list(self.detected_fields)
        if not include_source:
            data.pop("template_source", None)
            data.pop("sample_data", None)
        return data
