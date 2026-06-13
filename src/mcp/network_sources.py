"""Network-backed MCP search sources.

These sources are read-only. They never install, execute, mutate the catalog,
or expose raw registry payloads. They return Phase 23-compatible search-entry
dicts that MCPProposalPlanner will normalize and score.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence


_NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
_PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
_MAX_LIMIT = 20
_MCP_HINT_RE = re.compile(
    r"\b(mcp|model\s+context\s+protocol|modelcontextprotocol)\b",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NPM_SCOPED_RE = re.compile(r"^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NPM_UNSCOPED_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PYPI_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_NPM_SPEC_RE = re.compile(r"\bnpm:(?P<pkg>@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+)")
_PYPI_SPEC_RE = re.compile(r"\bpypi:(?P<pkg>[A-Za-z][A-Za-z0-9_.-]{0,63})")
_NPM_INSTALL_RE = re.compile(
    r"\b(?:npx(?:\s+-y)?|npm\s+(?:install|i)(?:\s+-g)?|pnpm\s+(?:add|dlx)|yarn\s+(?:add|dlx))\s+"
    r"(?P<pkg>@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.@-]+)",
    re.IGNORECASE,
)
_PYPI_INSTALL_RE = re.compile(
    r"\b(?:uvx|pip\s+install|python\s+-m\s+pip\s+install)\s+"
    r"(?P<pkg>[A-Za-z][A-Za-z0-9_.-]{0,63})",
    re.IGNORECASE,
)


def _clean_text(raw: Any, *, max_len: int = 300) -> str:
    if not isinstance(raw, str):
        return ""
    text = _CONTROL_RE.sub("", raw).strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _is_mcp_related(name: str, description: str, keywords: Any) -> bool:
    haystack = f"{name} {description}"
    if isinstance(keywords, list):
        haystack += " " + " ".join(
            kw for kw in keywords if isinstance(kw, str)
        )
    return bool(_MCP_HINT_RE.search(haystack))


def _safe_int(raw: Any) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if value > 0 else 0
    return 0


def _is_valid_npm_package_name(raw: str) -> bool:
    if not raw:
        return False
    if raw.startswith("@"):
        return bool(_NPM_SCOPED_RE.fullmatch(raw))
    return bool(_NPM_UNSCOPED_RE.fullmatch(raw))


def _is_valid_pypi_package_name(raw: str) -> bool:
    return bool(_PYPI_NAME_RE.fullmatch(raw or ""))


def _package_tools_hint(name: str) -> List[str]:
    parts = re.split(r"[^A-Za-z0-9]+", name.replace("@", ""))
    return [p.lower() for p in parts if len(p) >= 3][:12]


def _matches_query(name: str, text: str, query_tokens: set[str]) -> bool:
    if not query_tokens:
        return False
    haystack = name.lower()
    for tok in query_tokens:
        if isinstance(tok, str) and len(tok) >= 3 and tok.lower() in haystack:
            return True
    return False


def _dedupe_entries(entries: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        spec = entry.get("package_spec")
        if not isinstance(spec, str) or spec in seen:
            continue
        seen.add(spec)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _approx_downloads_from_score(obj: Dict[str, Any]) -> int:
    score = obj.get("score")
    if not isinstance(score, dict):
        return 0
    detail = score.get("detail")
    if not isinstance(detail, dict):
        return 0
    popularity = detail.get("popularity")
    if isinstance(popularity, (int, float)) and not isinstance(popularity, bool):
        return max(0, min(50_000, int(float(popularity) * 50_000)))
    return 0


class NpmRegistrySearchSource:
    """Read-only search source for public npm packages related to MCP."""

    def __init__(
        self,
        *,
        network_enabled: bool,
        timeout_s: float = 4.0,
        fetch_json: Optional[Callable[[str, float], Dict[str, Any]]] = None,
    ) -> None:
        if not isinstance(network_enabled, bool):
            raise TypeError("network_enabled must be bool")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self._network_enabled = network_enabled
        self._timeout_s = float(timeout_s)
        self._fetch_json = fetch_json

    @property
    def name(self) -> str:
        return "npm_registry"

    @property
    def is_network(self) -> bool:
        return True

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(self, query_tokens: set[str], *, limit: int) -> List[Dict[str, Any]]:
        if not self._network_enabled:
            return []
        if not isinstance(query_tokens, set):
            return []
        limit_i = max(1, min(_MAX_LIMIT, _safe_int(limit) or _MAX_LIMIT))
        query = " ".join(sorted(query_tokens | {"mcp"}))
        if not query.strip():
            return []
        params = urllib.parse.urlencode({"text": query, "size": limit_i})
        url = f"{_NPM_SEARCH_URL}?{params}"
        try:
            data = (
                self._fetch_json(url, self._timeout_s)
                if self._fetch_json is not None
                else self._default_fetch_json(url, self._timeout_s)
            )
        except Exception:
            return []
        objects = data.get("objects") if isinstance(data, dict) else None
        if not isinstance(objects, list):
            return []
        out: List[Dict[str, Any]] = []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            pkg = obj.get("package")
            if not isinstance(pkg, dict):
                continue
            name = _clean_text(pkg.get("name"), max_len=120)
            if not name:
                continue
            description = _clean_text(pkg.get("description"), max_len=300)
            keywords = pkg.get("keywords", [])
            if not _is_mcp_related(name, description, keywords):
                continue
            links = pkg.get("links")
            if not isinstance(links, dict):
                links = {}
            license_id = _clean_text(pkg.get("license"), max_len=64) or None
            tools_hint = [name.replace("@", "").replace("/", "_")]
            if isinstance(keywords, list):
                tools_hint.extend(
                    _clean_text(kw, max_len=48)
                    for kw in keywords
                    if isinstance(kw, str)
                )
            out.append(
                {
                    "source": self.name,
                    "package_name": name,
                    "package_spec": "npm:" + name,
                    "version": _clean_text(pkg.get("version"), max_len=64),
                    "package_transport": "npm",
                    "mcp_transport_hint": "stdio",
                    "description": description,
                    "tools_hint": tools_hint[:20],
                    "downloads_count": _approx_downloads_from_score(obj),
                    "last_publish_date": _clean_text(pkg.get("date"), max_len=64),
                    "has_repo": bool(links.get("repository")),
                    "has_license": bool(license_id),
                    "license_id": license_id,
                }
            )
            if len(out) >= limit_i:
                break
        return out

    @staticmethod
    def _default_fetch_json(url: str, timeout_s: float) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Lumena-MCP-Search/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(512_000)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}


class PyPIProjectLookupSource:
    """Read-only PyPI lookup source using deterministic package-name guesses.

    PyPI does not expose a stable JSON search endpoint. This source therefore
    checks a bounded set of common MCP package-name shapes via the official
    per-project JSON API and only returns projects whose metadata contains an
    MCP signal.
    """

    def __init__(
        self,
        *,
        network_enabled: bool,
        timeout_s: float = 4.0,
        fetch_json: Optional[Callable[[str, float], Dict[str, Any]]] = None,
    ) -> None:
        if not isinstance(network_enabled, bool):
            raise TypeError("network_enabled must be bool")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self._network_enabled = network_enabled
        self._timeout_s = float(timeout_s)
        self._fetch_json = fetch_json

    @property
    def name(self) -> str:
        return "pypi_project_lookup"

    @property
    def is_network(self) -> bool:
        return True

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(self, query_tokens: set[str], *, limit: int) -> List[Dict[str, Any]]:
        if not self._network_enabled:
            return []
        if not isinstance(query_tokens, set):
            return []
        limit_i = max(1, min(_MAX_LIMIT, _safe_int(limit) or _MAX_LIMIT))
        candidates = self._candidate_names(query_tokens)
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for name in candidates:
            if len(out) >= limit_i:
                break
            url = _PYPI_JSON_URL.format(name=urllib.parse.quote(name))
            try:
                data = (
                    self._fetch_json(url, self._timeout_s)
                    if self._fetch_json is not None
                    else self._default_fetch_json(url, self._timeout_s)
                )
            except Exception:
                continue
            item = self._entry_from_project(data)
            if item is None:
                continue
            pkg_name = item["package_name"]
            if pkg_name in seen:
                continue
            seen.add(pkg_name)
            out.append(item)
        return out

    @staticmethod
    def _candidate_names(query_tokens: set[str]) -> List[str]:
        clean = [
            re.sub(r"[^a-z0-9_-]+", "", str(tok).lower())
            for tok in sorted(query_tokens)
        ]
        clean = [tok.strip("-_") for tok in clean if len(tok.strip("-_")) >= 3]
        out: List[str] = []
        for tok in clean[:8]:
            out.extend([
                f"mcp-{tok}",
                f"{tok}-mcp",
                f"mcp-server-{tok}",
                f"{tok}-mcp-server",
            ])
        # Broad known naming surfaces. Kept bounded and deterministic.
        if "model" in clean or "context" in clean or "protocol" in clean:
            out.append("model-context-protocol")
        deduped: List[str] = []
        seen: set[str] = set()
        for name in out:
            if name and name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped[:24]

    def _entry_from_project(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        info = data.get("info")
        if not isinstance(info, dict):
            return None
        name = _clean_text(info.get("name"), max_len=120)
        if not name:
            return None
        summary = _clean_text(info.get("summary"), max_len=300)
        description = _clean_text(info.get("description"), max_len=600)
        classifiers = info.get("classifiers")
        keywords_raw = info.get("keywords")
        keywords: List[str] = []
        if isinstance(keywords_raw, str):
            keywords.extend(re.split(r"[\s,;]+", keywords_raw))
        if isinstance(classifiers, list):
            keywords.extend(x for x in classifiers if isinstance(x, str))
        if not _is_mcp_related(name, f"{summary} {description}", keywords):
            return None
        urls = info.get("project_urls")
        if not isinstance(urls, dict):
            urls = {}
        releases = data.get("releases")
        latest_release = None
        version = _clean_text(info.get("version"), max_len=64)
        if isinstance(releases, dict) and version in releases:
            files = releases.get(version)
            if isinstance(files, list) and files:
                first = files[0]
                if isinstance(first, dict):
                    latest_release = _clean_text(first.get("upload_time_iso_8601"), max_len=64)
        return {
            "source": self.name,
            "package_name": name,
            "package_spec": "pypi:" + name,
            "version": version,
            "package_transport": "pypi",
            "mcp_transport_hint": "stdio",
            "description": summary or description[:300],
            "tools_hint": [name.replace("-", "_")],
            "downloads_count": 0,
            "last_publish_date": latest_release or "",
            "has_repo": bool(urls.get("Repository") or urls.get("Source")),
            "has_license": bool(_clean_text(info.get("license"), max_len=64)),
            "license_id": _clean_text(info.get("license"), max_len=64) or None,
        }

    @staticmethod
    def _default_fetch_json(url: str, timeout_s: float) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Lumena-MCP-Search/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(1_000_000)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}


class MCPDirectorySearchSource:
    """Read-only extractor for public MCP directory/search pages.

    It only returns candidates when the fetched page contains an explicit npm
    or PyPI install/package snippet. It never guesses package names from prose.
    """

    def __init__(
        self,
        *,
        name: str,
        url_templates: Sequence[str],
        network_enabled: bool,
        timeout_s: float = 5.0,
        fetch_text: Optional[Callable[[str, float], str]] = None,
    ) -> None:
        safe_name = _clean_text(name, max_len=64)
        if not safe_name or not re.fullmatch(r"[a-z0-9_.-]{1,64}", safe_name):
            raise ValueError("name must be a safe source id")
        if not isinstance(network_enabled, bool):
            raise TypeError("network_enabled must be bool")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        templates = tuple(t for t in url_templates if isinstance(t, str) and "{query}" in t)
        if not templates:
            raise ValueError("url_templates must contain at least one {query} template")
        self._name = safe_name
        self._url_templates = templates
        self._network_enabled = network_enabled
        self._timeout_s = float(timeout_s)
        self._fetch_text = fetch_text

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_network(self) -> bool:
        return True

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(self, query_tokens: set[str], *, limit: int) -> List[Dict[str, Any]]:
        if not self._network_enabled:
            return []
        if not isinstance(query_tokens, set):
            return []
        clean_tokens = {
            re.sub(r"[^a-z0-9_-]+", "", str(tok).lower()).strip("-_")
            for tok in query_tokens
        }
        clean_tokens = {tok for tok in clean_tokens if len(tok) >= 3}
        if not clean_tokens:
            return []
        limit_i = max(1, min(_MAX_LIMIT, _safe_int(limit) or _MAX_LIMIT))
        query = urllib.parse.quote_plus(" ".join(sorted(clean_tokens | {"mcp", "server"})))
        entries: List[Dict[str, Any]] = []
        for tmpl in self._url_templates:
            if len(entries) >= limit_i:
                break
            url = tmpl.format(query=query)
            try:
                text = (
                    self._fetch_text(url, self._timeout_s)
                    if self._fetch_text is not None
                    else self._default_fetch_text(url, self._timeout_s)
                )
            except Exception:
                continue
            if not isinstance(text, str):
                continue
            page = _clean_text(text, max_len=256_000)
            if not _MCP_HINT_RE.search(page):
                continue
            entries.extend(self._extract_entries(page, clean_tokens, url))
        return _dedupe_entries(entries, limit=limit_i)

    def _extract_entries(
        self, text: str, query_tokens: set[str], source_url: str
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for pkg in self._extract_npm_packages(text):
            if not _matches_query(pkg, text, query_tokens):
                continue
            entries.append(self._entry(
                package_name=pkg,
                package_spec="npm:" + pkg,
                package_transport="npm",
                text=text,
                source_url=source_url,
            ))
        for pkg in self._extract_pypi_packages(text):
            if not _matches_query(pkg, text, query_tokens):
                continue
            entries.append(self._entry(
                package_name=pkg,
                package_spec="pypi:" + pkg,
                package_transport="pypi",
                text=text,
                source_url=source_url,
            ))
        return entries

    @staticmethod
    def _extract_npm_packages(text: str) -> List[str]:
        out: List[str] = []
        for regex in (_NPM_SPEC_RE, _NPM_INSTALL_RE):
            for match in regex.finditer(text):
                pkg = _clean_text(match.group("pkg"), max_len=120)
                if _is_valid_npm_package_name(pkg):
                    out.append(pkg)
        return out

    @staticmethod
    def _extract_pypi_packages(text: str) -> List[str]:
        out: List[str] = []
        for regex in (_PYPI_SPEC_RE, _PYPI_INSTALL_RE):
            for match in regex.finditer(text):
                pkg = _clean_text(match.group("pkg"), max_len=120)
                if _is_valid_pypi_package_name(pkg):
                    out.append(pkg)
        return out

    def _entry(
        self,
        *,
        package_name: str,
        package_spec: str,
        package_transport: str,
        text: str,
        source_url: str,
    ) -> Dict[str, Any]:
        lower = text.lower()
        return {
            "source": self.name,
            "package_name": package_name,
            "package_spec": package_spec,
            "version": "",
            "package_transport": package_transport,
            "mcp_transport_hint": "stdio",
            "description": "MCP package discovered from an external directory page.",
            "tools_hint": _package_tools_hint(package_name),
            "downloads_count": 50_000 if self.name in {"smithery_directory", "pulsemcp_directory"} else 0,
            "last_publish_date": "",
            "has_repo": "github.com" in lower or "github.com" in source_url.lower(),
            "has_license": "license" in lower or "mit" in lower or "apache" in lower,
            "license_id": None,
        }

    @staticmethod
    def _default_fetch_text(url: str, timeout_s: float) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html, text/plain;q=0.9, */*;q=0.5",
                "User-Agent": "Lumena-MCP-Search/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(512_000)
        return raw.decode("utf-8", errors="replace")


__all__ = [
    "MCPDirectorySearchSource",
    "NpmRegistrySearchSource",
    "PyPIProjectLookupSource",
]
