"""Strict, shell-free validation for local model references."""

from __future__ import annotations

import re

from .contracts import LocalModelSource, ModelReference

_MAX_REFERENCE = 384
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")


class IdentifierError(ValueError):
    """Stable validation failure with no secret or filesystem disclosure."""


def _clean(value: object) -> str:
    if type(value) is not str:
        raise IdentifierError("model_reference_type_invalid")
    if not value or value != value.strip() or len(value) > _MAX_REFERENCE:
        raise IdentifierError("model_reference_invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise IdentifierError("model_reference_control_character")
    if "\\" in value or "://" in value or value.startswith(("-", ".", "/")):
        raise IdentifierError("model_reference_unsafe")
    return value


def _split_tag(value: str) -> tuple[str, str]:
    leaf = value.rsplit("/", 1)[-1]
    if ":" not in leaf:
        return value, ""
    prefix, tag = value.rsplit(":", 1)
    if not tag or not _TAG.fullmatch(tag):
        raise IdentifierError("model_tag_invalid")
    return prefix, tag


def _validate_path(value: str, *, parts: int | None = None) -> list[str]:
    components = value.split("/")
    if parts is not None and len(components) != parts:
        raise IdentifierError("model_reference_shape_invalid")
    if not components or any(not _COMPONENT.fullmatch(part) or part in {".", ".."} for part in components):
        raise IdentifierError("model_reference_component_invalid")
    return components


def parse_model_reference(value: object, source: str | LocalModelSource | None = None) -> ModelReference:
    """Return one canonical Ollama pull reference.

    Hugging Face references are accepted only as ``owner/repo[:quant]`` or
    ``hf.co/owner/repo[:quant]``. Arbitrary URLs and filenames are rejected.
    """
    raw = _clean(value)
    explicit = LocalModelSource(source) if source is not None else None
    is_hf = raw.lower().startswith("hf.co/") or explicit is LocalModelSource.HUGGINGFACE

    if is_hf:
        without_host = raw[6:] if raw.lower().startswith("hf.co/") else raw
        path, tag = _split_tag(without_host)
        owner, repo = _validate_path(path, parts=2)
        repository = f"{owner}/{repo}"
        canonical = repository + (f":{tag}" if tag else "")
        return ModelReference(
            source=LocalModelSource.HUGGINGFACE,
            canonical=canonical,
            pull_reference=f"hf.co/{canonical}",
            repository=repository,
            quantization=tag,
        )

    if explicit not in {None, LocalModelSource.OLLAMA}:
        raise IdentifierError("model_source_invalid")
    path, tag = _split_tag(raw)
    _validate_path(path)
    canonical = path + (f":{tag}" if tag else ":latest")
    return ModelReference(
        source=LocalModelSource.OLLAMA,
        canonical=canonical,
        pull_reference=canonical,
        quantization=tag or "latest",
    )
