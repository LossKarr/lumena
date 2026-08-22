"""Deterministic coherence checks for contractual mission web bundles."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict, Iterable


_SOURCE_EXTENSIONS = frozenset({
    ".py", ".html", ".htm", ".css", ".js", ".mjs", ".jsx", ".ts", ".tsx",
})
_ASSET_EXTENSIONS = frozenset({".css", ".js", ".mjs"})


def _clean_path(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        return ""
    return str(path)


class _BundleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if values.get("id"):
            self.ids.add(values["id"])
        if tag.lower() == "script" and values.get("src"):
            self.assets.append(values["src"])
        if tag.lower() == "link" and values.get("href"):
            rel = values.get("rel", "").lower()
            if "stylesheet" in rel or values["href"].lower().split("?", 1)[0].endswith(".css"):
                self.assets.append(values["href"])


def _resolve_asset(html_path: str, reference: str) -> str:
    ref = str(reference or "").strip().split("?", 1)[0].split("#", 1)[0]
    if not ref or ref.startswith(("http://", "https://", "//", "data:")):
        return ""
    if "{{" in ref or "{%" in ref:
        return ""
    if ref.startswith("/"):
        return _clean_path(ref)
    return _clean_path(str(PurePosixPath(html_path).parent / ref))


def _declared_paths(contract_data: Any) -> set[str]:
    if not isinstance(contract_data, dict):
        return set()
    return {
        path
        for entry in (contract_data.get("files") or [])
        if isinstance(entry, dict)
        for path in [_clean_path(entry.get("path"))]
        if path
    }


def validate_contract_web_bundle(
    project_dir: str | Path, contract_data: Any,
) -> Dict[str, list[str]]:
    """Validate a web bundle without executing it or mutating the workspace."""
    root = Path(project_dir).resolve()
    declared = _declared_paths(contract_data)
    html_paths = sorted(p for p in declared if PurePosixPath(p).suffix.lower() in {".html", ".htm"})
    if not html_paths:
        return {"errors": [], "warnings": []}

    errors: list[str] = []
    warnings: list[str] = []
    discovered: set[str] = set()
    for item in root.rglob("*") if root.is_dir() else ():
        if not item.is_file() or any(part in {".backups", "__pycache__", ".pytest_cache"} for part in item.parts):
            continue
        rel = item.relative_to(root).as_posix()
        if item.suffix.lower() in _SOURCE_EXTENSIONS:
            discovered.add(rel)

    undeclared = sorted(discovered - declared)
    if undeclared:
        errors.append("sources_non_declarees: " + ", ".join(undeclared))

    linked_assets: set[str] = set()
    html_ids: set[str] = set()
    for html_path in html_paths:
        full = root / html_path
        if not full.is_file():
            errors.append(f"html_manquant: {html_path}")
            continue
        parser = _BundleHTMLParser()
        try:
            parser.feed(full.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            errors.append(f"html_illisible: {html_path}: {type(exc).__name__}")
            continue
        html_ids.update(parser.ids)
        for ref in parser.assets:
            asset = _resolve_asset(html_path, ref)
            if not asset:
                continue
            linked_assets.add(asset)
            if not (root / asset).is_file():
                errors.append(f"asset_manquant: {html_path} -> {asset}")
            elif PurePosixPath(asset).suffix.lower() in _ASSET_EXTENSIONS and asset not in declared:
                errors.append(f"asset_non_declare: {html_path} -> {asset}")

    declared_assets = {
        path for path in declared if PurePosixPath(path).suffix.lower() in _ASSET_EXTENSIONS
    }
    for asset in sorted(declared_assets - linked_assets):
        errors.append(f"asset_non_lie: {asset}")

    js_id_pattern = re.compile(
        r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)|"
        r"querySelector\(\s*['\"]#([A-Za-z_][\w:.-]*)['\"]\s*\)"
    )
    for js_path in sorted(p for p in declared if PurePosixPath(p).suffix.lower() in {".js", ".mjs"}):
        full = root / js_path
        if not full.is_file():
            errors.append(f"javascript_manquant: {js_path}")
            continue
        source = full.read_text(encoding="utf-8", errors="replace")
        referenced_ids = {a or b for a, b in js_id_pattern.findall(source)}
        missing_ids = sorted(referenced_ids - html_ids)
        if missing_ids:
            errors.append(f"ids_html_manquants: {js_path} -> {', '.join(missing_ids)}")

    return {"errors": list(dict.fromkeys(errors)), "warnings": warnings}


__all__ = ["validate_contract_web_bundle"]
