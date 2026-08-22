"""Versioned recipes used to reproduce and revise Studio documents."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping


RECIPE_METADATA_KEY = "studio_generation"


@dataclass(frozen=True)
class StudioGenerationRecipe:
    schema_version: int
    template_id: str
    template_version: int
    kind: str
    output_format: str
    data: dict[str, Any]
    filename_stem: str
    logo_id: str = ""

    @classmethod
    def create(
        cls,
        *,
        template_id: str,
        template_version: int,
        kind: str,
        output_format: str,
        data: Mapping[str, Any],
        filename_stem: str,
        logo_id: str = "",
    ) -> "StudioGenerationRecipe":
        if not isinstance(data, Mapping):
            raise ValueError("Studio generation data must be an object")
        recipe = cls(
            schema_version=1,
            template_id=str(template_id),
            template_version=int(template_version),
            kind=str(kind),
            output_format=str(output_format).lower().lstrip("."),
            data=deepcopy(dict(data)),
            filename_stem=str(filename_stem),
            logo_id=str(logo_id or ""),
        )
        recipe.validate()
        return recipe

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "StudioGenerationRecipe":
        raw = dict(metadata or {}).get(RECIPE_METADATA_KEY)
        if not isinstance(raw, Mapping):
            raise ValueError(
                "Ce document ne contient pas de recette Studio révisable. "
                "Les documents générés avant cette fonctionnalité doivent être régénérés une fois."
            )
        try:
            recipe = cls(
                schema_version=int(raw.get("schema_version", 0)),
                template_id=str(raw.get("template_id", "")),
                template_version=int(raw.get("template_version", 0)),
                kind=str(raw.get("kind", "")),
                output_format=str(raw.get("output_format", "")),
                data=deepcopy(dict(raw.get("data") or {})),
                filename_stem=str(raw.get("filename_stem", "")),
                logo_id=str(raw.get("logo_id", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("La recette Studio du document est invalide") from exc
        recipe.validate()
        return recipe

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported Studio recipe version: {self.schema_version}")
        if not self.template_id or self.template_version < 1:
            raise ValueError("Studio recipe template identity is incomplete")
        if self.output_format not in {"pdf", "html", "docx", "xlsx", "pptx"}:
            raise ValueError(f"Studio recipe format is not revisable: {self.output_format}")
        if not isinstance(self.data, dict):
            raise ValueError("Studio recipe data must be an object")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))


def merge_document_data(
    original: Mapping[str, Any], patch: Mapping[str, Any], *, replace: bool = False
) -> dict[str, Any]:
    """Apply a recursive object patch without mutating either input."""
    if not isinstance(original, Mapping) or not isinstance(patch, Mapping):
        raise ValueError("Document data and patch must be objects")
    if replace:
        return deepcopy(dict(patch))
    merged = deepcopy(dict(original))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = merge_document_data(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def changed_document_data(
    original: Mapping[str, Any], revised: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only leaf values added or changed in the revised document data."""
    if not isinstance(original, Mapping) or not isinstance(revised, Mapping):
        raise ValueError("Document data snapshots must be objects")
    changes: dict[str, Any] = {}
    for key, value in revised.items():
        previous = original.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            nested = changed_document_data(previous, value)
            if nested:
                changes[str(key)] = nested
        elif key not in original or previous != value:
            changes[str(key)] = deepcopy(value)
    return changes


__all__ = [
    "RECIPE_METADATA_KEY",
    "StudioGenerationRecipe",
    "changed_document_data",
    "merge_document_data",
]
