"""Filesystem and Jinja safety helpers for user-editable templates."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .template_models import TemplateValidationError


_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
_FORBIDDEN_JINJA_RE = re.compile(
    r"\{%-?\s*(?:include|import|from|extends)\b", re.IGNORECASE
)


def normalize_template_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(normalized):
        raise TemplateValidationError("template id must contain 2-80 lowercase safe characters")
    return normalized


def resolve_within(root: Path, relative: str | Path) -> Path:
    base = Path(root).resolve()
    candidate = (base / Path(relative)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise TemplateValidationError("path escapes Document Studio storage") from exc
    return candidate


def validate_template_source(source: str, *, max_chars: int = 500_000) -> None:
    if not isinstance(source, str) or not source.strip():
        raise TemplateValidationError("template source must be non-empty text")
    if len(source) > max_chars:
        raise TemplateValidationError("template source is too large")
    if _FORBIDDEN_JINJA_RE.search(source):
        raise TemplateValidationError("template contains forbidden Jinja constructs")
    try:
        from jinja2 import nodes
        from jinja2.sandbox import SandboxedEnvironment

        tree = SandboxedEnvironment(autoescape=True).parse(source)
        forbidden_nodes = (nodes.Include, nodes.Import, nodes.FromImport, nodes.Extends)
        if any(any(True for _ in tree.find_all(forbidden)) for forbidden in forbidden_nodes):
            raise TemplateValidationError("template contains forbidden Jinja imports")
        if any(node.attr.startswith("_") for node in tree.find_all(nodes.Getattr)):
            raise TemplateValidationError("template accesses a private attribute")
        if any(node.name.startswith("_") for node in tree.find_all(nodes.Name)):
            raise TemplateValidationError("template accesses a private name")
    except TemplateValidationError:
        raise
    except Exception as exc:
        raise TemplateValidationError(f"invalid Jinja template: {exc}") from exc


def atomic_write_text(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
