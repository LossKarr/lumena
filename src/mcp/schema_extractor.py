"""
schema_extractor.py — Phase I-3 Niveau 2 : extraction de schéma depuis package metadata.

Doctrine :
  - Pure logic + fetch JSON injecté (testabilité).
  - Sources : npm registry + PyPI JSON endpoint (read-only).
  - Heuristiques de parsing du README pour extraire les variables d'env :
      1. Blocs JSON `claude_desktop_config.json` (priorité maxi)
      2. Sections markdown "Environment Variables", "Configuration", "Setup"
      3. Blocs bash `export KEY=value`
      4. Patterns inline `<KEY>` ou `${KEY}` dans les blocs code
  - Heuristique de classification kind/sensitivity sur le NOM du champ
    (TOKEN/SECRET/PASSWORD → SECRET, PATH → PATH_DIR, etc.).
  - Aucun appel HTTP direct — un callable `fetch_json` est injecté.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.mcp.config_schema import (
    AuthFlow,
    ConfigField,
    ConfigKind,
    MCPConfigSchema,
    Sensitivity,
)


# ──────────────────────────────────────────────────────────────────────────────
# Patterns regex (compilés une fois)
# ──────────────────────────────────────────────────────────────────────────────

# Bloc code multi-ligne (markdown)
_CODE_BLOCK_RE = re.compile(
    r"```(?:json|jsonc|json5)?\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_BASH_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell|console|zsh)?\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# Patterns variables (extraction)
_EXPORT_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,62})\s*=\s*([^\n#]*)",
    re.MULTILINE,
)
_INLINE_VAR_RE = re.compile(
    r"\$\{?([A-Z][A-Z0-9_]{2,62})\}?",
)
# Tables markdown : | KEY | description |
_MD_TABLE_RE = re.compile(
    r"^\s*\|\s*[`]?([A-Z][A-Z0-9_]{2,62})[`]?\s*\|",
    re.MULTILINE,
)
# Sections "Environment Variables", "Configuration" — pour focaliser le parsing
_ENV_SECTION_RE = re.compile(
    r"(?im)^[#]{1,4}\s*(?:environment\s+variables?|configuration|setup|env\s+vars?|"
    r"variables\s+d'?env(?:ironnement)?|installation)\b",
)

# Heuristique du kind par nom de champ.
# IMPORTANT : ordre du plus spécifique au plus général.
_KIND_HEURISTICS: Tuple[Tuple[re.Pattern, ConfigKind], ...] = (
    # OAuth — patterns spécifiques d'abord
    (re.compile(r"OAUTH.*REFRESH_TOKEN$|REFRESH_TOKEN$"), ConfigKind.OAUTH_REFRESH_TOKEN),
    (re.compile(r"OAUTH.*ACCESS_TOKEN$|ACCESS_TOKEN$"), ConfigKind.OAUTH_ACCESS_TOKEN),
    (re.compile(r"CLIENT_SECRET$"), ConfigKind.OAUTH_CLIENT_SECRET),
    (re.compile(r"CLIENT_ID$"), ConfigKind.OAUTH_CLIENT_ID),
    # Connection strings — AVANT les patterns _URL génériques
    (re.compile(r"CONNECTION_STRING$|DATABASE_URL$|DB_URL$|^DSN$|_DSN$"), ConfigKind.CONNECTION_STRING),
    # Webhook URL — AVANT le pattern _URL général
    (re.compile(r"WEBHOOK_URL$|WEBHOOK$"), ConfigKind.WEBHOOK_URL),
    # Password / secret
    (re.compile(r"PASSWORD$|PASSWD$|PWD$"), ConfigKind.SECRET_PASSWORD),
    (re.compile(r"API_KEY$|APIKEY$"), ConfigKind.SECRET_API_KEY),
    (re.compile(r"_TOKEN$"), ConfigKind.SECRET_TOKEN),
    (re.compile(r"_SECRET$|_KEY$"), ConfigKind.SECRET_API_KEY),
    # Path / file / dir
    (re.compile(r"_PATHS?$|_DIR$|_DIRECTORY$|_FOLDER$"), ConfigKind.PATH_DIR),
    (re.compile(r"_FILE$|_PATH$"), ConfigKind.PATH_FILE),
    # Numeric
    (re.compile(r"_PORT$"), ConfigKind.INTEGER),
    # URL (générique — après les patterns plus spécifiques)
    (re.compile(r"_URL$|_URI$|_ENDPOINT$"), ConfigKind.URL),
    # Email
    (re.compile(r"_EMAIL$"), ConfigKind.EMAIL),
    # Host
    (re.compile(r"_HOST$|HOSTNAME$"), ConfigKind.STRING),
)


# ──────────────────────────────────────────────────────────────────────────────
# Heuristiques de classification
# ──────────────────────────────────────────────────────────────────────────────


def _infer_kind(name: str) -> ConfigKind:
    """Devine le kind d'après le NOM du champ."""
    upper = name.upper()
    for pat, kind in _KIND_HEURISTICS:
        if pat.search(upper):
            return kind
    return ConfigKind.STRING


def _infer_sensitivity(kind: ConfigKind, name: str) -> Sensitivity:
    """Devine la sensibilité. Tout ce qui sent le secret → SECRET."""
    from src.mcp.config_schema import default_sensitivity_for
    base = default_sensitivity_for(kind)
    # Heuristique additionnelle : noms forts en SECRET même si kind est STRING.
    upper = name.upper()
    if base != Sensitivity.SECRET and any(
        marker in upper for marker in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE")
    ):
        return Sensitivity.SECRET
    return base


def _humanize_label(name: str) -> str:
    """SLACK_BOT_TOKEN → 'Slack Bot Token'."""
    parts = name.replace("_", " ").lower().split()
    return " ".join(p.capitalize() for p in parts) if parts else name


def _build_field(name: str, *, description: str = "") -> ConfigField:
    """Construit un ConfigField avec heuristiques."""
    kind = _infer_kind(name)
    sensitivity = _infer_sensitivity(kind, name)
    return ConfigField(
        name=name,
        label=_humanize_label(name),
        description=description or f"Variable d'environnement {name}.",
        kind=kind,
        sensitivity=sensitivity,
        required=True,
        autonomy_resolvable=False,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Parsing du README
# ──────────────────────────────────────────────────────────────────────────────

# Noms à exclure (faux positifs courants)
_FORBIDDEN_NAMES: frozenset = frozenset({
    # Variables système trop génériques
    "PATH", "HOME", "USER", "PWD", "SHELL", "TERM", "LANG", "LC_ALL",
    "TMPDIR", "TEMP", "TMP", "EDITOR", "PAGER", "DISPLAY",
    # Variables docker/node trop génériques
    "NODE_ENV", "PYTHONPATH", "PYTHONUNBUFFERED", "DEBIAN_FRONTEND",
    "CI", "GITHUB_ACTIONS", "RUNNER_OS",
    # Markdown / placeholders fréquents
    "TODO", "FIXME", "XXX", "YOUR_KEY_HERE",
    "EXAMPLE", "EXAMPLE_KEY", "EXAMPLE_TOKEN",
    "OK", "ERR", "NONE", "NULL", "TRUE", "FALSE",
})


_SHORT_ACRONYMS_ALLOWED: frozenset = frozenset({
    "DSN", "URL", "URI", "API", "AWS", "GCP", "JWT", "PEM",
})


def _is_valid_var_name(name: str) -> bool:
    """Filtre les faux positifs.

    Règles :
      - 3 à 64 caractères
      - pattern A-Z[A-Z0-9_]+ strict
      - PAS dans la blacklist _FORBIDDEN_NAMES
      - Si len == 3 : doit être dans _SHORT_ACRONYMS_ALLOWED (DSN, URL, ...)
      - Si pas d'underscore et len 3 : non-acronyme rejeté
      - Sinon accepté
    """
    if not name or len(name) < 3 or len(name) > 64:
        return False
    if name in _FORBIDDEN_NAMES:
        return False
    if not re.match(r"^[A-Z][A-Z0-9_]+$", name):
        return False
    if len(name) == 3 and name not in _SHORT_ACRONYMS_ALLOWED:
        return False
    return True


def _extract_from_claude_desktop_json(readme: str) -> Set[str]:
    """Cherche les blocs JSON claude_desktop_config dans le README.

    Format typique :
    ```json
    { "mcpServers": { "slack": { "command": "npx", "args": [...],
       "env": { "SLACK_BOT_TOKEN": "xoxb-...", "SLACK_TEAM_ID": "T01..." } } } }
    ```
    """
    found: Set[str] = set()
    for match in _CODE_BLOCK_RE.finditer(readme):
        body = match.group(1).strip()
        # On tente même les blocs non-tagués (souvent les README ne mettent
        # pas le tag json)
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            continue
        for env_vars in _walk_for_env_dict(data):
            for k in env_vars:
                if _is_valid_var_name(k):
                    found.add(k)
    return found


def _walk_for_env_dict(obj: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """Cherche récursivement les clés 'env' à valeur dict."""
    if depth > 6:
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        env = obj.get("env")
        if isinstance(env, dict):
            out.append(env)
        for v in obj.values():
            out.extend(_walk_for_env_dict(v, depth + 1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_for_env_dict(v, depth + 1))
    return out


def _extract_from_bash_blocks(readme: str) -> Set[str]:
    """Cherche `export KEY=value` dans les blocs bash."""
    found: Set[str] = set()
    for match in _BASH_BLOCK_RE.finditer(readme):
        body = match.group(1)
        for m in _EXPORT_RE.finditer(body):
            key = m.group(1)
            if _is_valid_var_name(key):
                found.add(key)
    return found


def _extract_from_markdown_tables(readme: str) -> Set[str]:
    """Cherche les variables dans les tables markdown (| `KEY` | desc |)."""
    found: Set[str] = set()
    # On limite aux sections "env / config / setup" pour réduire le bruit
    sections = _ENV_SECTION_RE.split(readme)
    if len(sections) > 1:
        scope = "\n".join(sections[1:])  # tout ce qui suit la 1ère section pertinente
    else:
        scope = readme
    for m in _MD_TABLE_RE.finditer(scope):
        key = m.group(1)
        if _is_valid_var_name(key):
            found.add(key)
    return found


def _extract_from_inline_refs(readme: str) -> Set[str]:
    """Cherche les références `$VAR` ou `${VAR}` dans les blocs code."""
    found: Set[str] = set()
    for match in _CODE_BLOCK_RE.finditer(readme):
        body = match.group(1)
        for m in _INLINE_VAR_RE.finditer(body):
            key = m.group(1)
            if _is_valid_var_name(key):
                found.add(key)
    # Aussi dans les blocs bash
    for match in _BASH_BLOCK_RE.finditer(readme):
        body = match.group(1)
        for m in _INLINE_VAR_RE.finditer(body):
            key = m.group(1)
            if _is_valid_var_name(key):
                found.add(key)
    return found


def _extract_from_plain_export_lines(text: str) -> Set[str]:
    """Cherche `export KEY=value` ou `KEY=value` dans le texte plat HORS
    code blocks (utile pour les sorties --help et les snippets users)."""
    found: Set[str] = set()
    # On enlève les blocs code pour ne pas matcher 2x.
    cleaned = _CODE_BLOCK_RE.sub("", text)
    cleaned = _BASH_BLOCK_RE.sub("", cleaned)
    for m in _EXPORT_RE.finditer(cleaned):
        key = m.group(1)
        if _is_valid_var_name(key):
            found.add(key)
    return found


def _extract_env_vars(readme: str) -> Set[str]:
    """Combine toutes les sources d'extraction."""
    if not isinstance(readme, str) or not readme:
        return set()
    found: Set[str] = set()
    found |= _extract_from_claude_desktop_json(readme)
    found |= _extract_from_bash_blocks(readme)
    found |= _extract_from_markdown_tables(readme)
    found |= _extract_from_inline_refs(readme)
    found |= _extract_from_plain_export_lines(readme)
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Sources HTTP (injectables)
# ──────────────────────────────────────────────────────────────────────────────


JSONFetcher = Callable[[str, float], Dict[str, Any]]


def _build_npm_url(package_name: str) -> str:
    """https://registry.npmjs.org/<package>"""
    import urllib.parse
    # On laisse @scope/pkg encodé sauf le /
    if package_name.startswith("@"):
        # registry npm accepte @scope%2Fpkg
        return f"https://registry.npmjs.org/{urllib.parse.quote(package_name, safe='@/')}"
    return f"https://registry.npmjs.org/{urllib.parse.quote(package_name)}"


def _build_pypi_url(package_name: str) -> str:
    """https://pypi.org/pypi/<package>/json"""
    import urllib.parse
    return f"https://pypi.org/pypi/{urllib.parse.quote(package_name)}/json"


def _extract_readme_from_npm(metadata: Dict[str, Any]) -> str:
    """npm registry → champ 'readme' au top ou dans versions[latest]."""
    if not isinstance(metadata, dict):
        return ""
    top = metadata.get("readme")
    if isinstance(top, str) and top.strip():
        return top
    versions = metadata.get("versions")
    if isinstance(versions, dict):
        latest_tag = metadata.get("dist-tags", {}).get("latest")
        if isinstance(latest_tag, str):
            v = versions.get(latest_tag)
            if isinstance(v, dict):
                r = v.get("readme")
                if isinstance(r, str):
                    return r
    return ""


def _extract_readme_from_pypi(metadata: Dict[str, Any]) -> str:
    """PyPI JSON → info.description."""
    if not isinstance(metadata, dict):
        return ""
    info = metadata.get("info")
    if isinstance(info, dict):
        desc = info.get("description")
        if isinstance(desc, str):
            return desc
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────


def _strip_transport_prefix(package_spec: str) -> Tuple[Optional[str], Optional[str]]:
    """('npm:@x/y', 'pypi:foo') → ('npm', '@x/y') / ('pypi', 'foo')."""
    if not isinstance(package_spec, str):
        return None, None
    if package_spec.startswith("npm:"):
        return "npm", package_spec[4:].strip()
    if package_spec.startswith("pypi:"):
        return "pypi", package_spec[5:].strip()
    return None, None


def extract_schema_from_package(
    *,
    server_id: str,
    package_spec: str,
    fetch_json: Optional[JSONFetcher] = None,
    timeout_s: float = 8.0,
    readme_override: Optional[str] = None,
) -> Optional[MCPConfigSchema]:
    """Extrait un MCPConfigSchema depuis les metadata du package.

    Args:
        server_id: id du serveur catalog (utilisé dans MCPConfigSchema).
        package_spec: 'npm:@x/y' ou 'pypi:foo'.
        fetch_json: callable (url, timeout) → dict. Injecté pour testabilité.
            Si None et readme_override absent → tente urllib (best-effort).
        timeout_s: timeout HTTP.
        readme_override: si fourni, skippe le fetch et parse ce contenu
            directement. Utilisé par les niveaux 3-4 ou pour les tests.

    Returns:
        MCPConfigSchema avec detected_from="package" si extraction OK,
        None si pas de champs trouvés OU package introuvable.
    """
    if not isinstance(server_id, str) or not server_id:
        return None

    # Récupération du README
    readme: str = ""
    if isinstance(readme_override, str):
        readme = readme_override
    else:
        transport, name = _strip_transport_prefix(package_spec)
        if not transport or not name:
            return None
        if fetch_json is None:
            fetch_json = _default_fetch_json
        try:
            if transport == "npm":
                meta = fetch_json(_build_npm_url(name), timeout_s)
                readme = _extract_readme_from_npm(meta)
            elif transport == "pypi":
                meta = fetch_json(_build_pypi_url(name), timeout_s)
                readme = _extract_readme_from_pypi(meta)
        except Exception:  # noqa: BLE001
            return None

    if not readme:
        return None

    # Extraction
    found_vars = _extract_env_vars(readme)
    if not found_vars:
        return None

    # Construction du schéma — tri stable pour déterminisme
    fields = tuple(_build_field(name) for name in sorted(found_vars))
    return MCPConfigSchema(
        server_id=server_id,
        fields=fields,
        auth_flows=(),
        detected_from="package",
        detected_at=_now_iso(),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_fetch_json(url: str, timeout_s: float) -> Dict[str, Any]:
    """Fetcher par défaut (urllib). Best-effort — utilisé seulement si
    aucun fetch_json n'est injecté."""
    import urllib.request
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Lumena-MCP-SchemaExtractor/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read(2_000_000)  # cap 2 MB
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


__all__ = [
    "extract_schema_from_package",
    "JSONFetcher",
]
