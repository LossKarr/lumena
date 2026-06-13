"""
target_resolver.py — Phase F : resolution universelle d'une cible MCP.

L'utilisateur peut dire `add_mcp(<n'importe quoi>)` et Lumena resout :
  - URL GitHub  → fetch README et extrait install command
  - package_spec → npm:/pypi:/local: deja Phase 14 valide
  - snippet JSON (claude_desktop style) → extrait command+args
  - chemin local → kind="local_path"
  - texte libre → kind="intent" (fallback boucle autonomy)

Aucun appel HTTP direct : web_fetch est INJECTE pour testabilite.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Regexes deterministes (compilees une fois)
# ──────────────────────────────────────────────────────────────────────────────

# Phase 14 transports valides : npm:/pypi:/local:
_PACKAGE_SPEC_RE = re.compile(
    r"^(?:npm|pypi|local):[A-Za-z0-9@/_.\-]+$"
)

# GitHub repo URL : https://github.com/<owner>/<repo>[/...]
_GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/([A-Za-z0-9][A-Za-z0-9._\-]*)/"
    r"([A-Za-z0-9][A-Za-z0-9._\-]*)(?:/.*)?$"
)

# Detection install command dans README :
# `npm install -g @scope/pkg` / `npm i @scope/pkg` / `npx @scope/pkg`
# Phase I-8 (Fix AS) : `npx -y <pkg>` (LA forme standard des READMEs MCP)
# et `--global`/`-g` en position quelconque — l'ancienne regex ratait le
# flag `-y` et ne matchait donc presque aucun README MCP réel.
_NPM_INSTALL_RE = re.compile(
    r"(?:npm\s+(?:install|i|add)|npx)(?:\s+(?:-[a-zA-Z]+|--[a-z\-]+))*\s+"
    r"(@[a-z0-9][a-z0-9\-_.]*/[a-z0-9][a-z0-9\-_.]*|"
    r"[a-z0-9][a-z0-9\-_.]+)",
    re.IGNORECASE,
)
# `pip install <name>` / `pip3 install <name>` / `uvx <name>`
# Phase I-8 (Fix AS) : flags (`-U`, `--upgrade`...) + `uv tool install`
# + `uvx --from <pkg>`.
_PYPI_INSTALL_RE = re.compile(
    r"(?:pip3?\s+install|uvx(?:\s+--from)?|uv\s+tool\s+install)"
    r"(?:\s+(?:-[a-zA-Z]+|--[a-z\-]+))*\s+"
    r"([a-zA-Z][a-zA-Z0-9_\-.]*)",
)

# URL ou chemin local Windows/POSIX (heuristique simple) :
# - Windows: C:\... ou C:/...
# - POSIX absolute: /... (mais on n'invente pas, on teste l'existence si possible)
_LOCAL_PATH_RE = re.compile(
    r"^(?:[A-Za-z]:[\\/]|/|\\\\|\./|\.\\)"
)


# ──────────────────────────────────────────────────────────────────────────────
# Dataclass
# ──────────────────────────────────────────────────────────────────────────────


_VALID_KINDS = frozenset({
    "intent", "github_url", "package_spec", "config_snippet",
    "local_path", "unknown",
    "known_mcp",  # Phase I-1 : match curated KNOWN_MCPS
})


@dataclass(frozen=True)
class ResolvedTarget:
    """Resultat de la resolution d'une cible MCP user.

    Attributs:
        kind: type detecte (cf _VALID_KINDS).
        package_spec: spec resolue (npm:/pypi:/local:) si applicable.
        version: version specifique si extraite ("latest" si non spec).
        source_url: URL d'origine (GitHub uniquement, pour audit).
        raw_input: chaine brute fournie par l'utilisateur (cap a 512 chars).
        slug: identifiant canonique (Phase I-1, pour kind="known_mcp").
        display_name: nom user-friendly (Phase I-1).
        description: courte description (Phase I-1).
        semantic_category: categorie semantique (Phase I-1).
        trust_score: trust_score curated (Phase I-1).
        docs_url: URL doc officielle (Phase I-1).
        config_schema_dict: schema de config persistable (Phase I-1).
            Forme dict serialisable JSON, ou None.
    """
    kind: str
    package_spec: Optional[str]
    version: Optional[str]
    source_url: Optional[str]
    raw_input: str
    # Phase I-1 — enrichissement curated (defaults pour back-compat tests).
    slug: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    semantic_category: Optional[str] = None
    trust_score: Optional[int] = None
    docs_url: Optional[str] = None
    config_schema_dict: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers prive
# ──────────────────────────────────────────────────────────────────────────────


_MAX_RAW_LEN = 512


def _normalize_input(target: str) -> str:
    """Normalisation safe : strip + truncate."""
    if not isinstance(target, str):
        return ""
    return target.strip()[:_MAX_RAW_LEN]


def _looks_like_json(s: str) -> bool:
    s = s.lstrip()
    return s.startswith("{") or s.startswith("[")


def _extract_from_config_snippet(raw: str) -> Optional[ResolvedTarget]:
    """Detecte un snippet claude_desktop / mcp.json et extrait package_spec.

    Format attendu (un parmi) :
      {"command": "npx", "args": ["-y", "@scope/pkg"]}
      {"command": "uvx", "args": ["pkg"]}
      {"mcpServers": {"name": {"command": "npx", "args": [...]}}}
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    # Si dict racine avec mcpServers : prendre la 1ere entree.
    if isinstance(data, dict) and "mcpServers" in data:
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and servers:
            data = next(iter(servers.values()))
    if not isinstance(data, dict):
        return None
    command = data.get("command")
    args = data.get("args")
    if not isinstance(command, str) or not isinstance(args, list):
        return None
    command_lc = command.strip().lower()
    pkg_name: Optional[str] = None
    if command_lc in ("npx", "npm"):
        # 1er arg non-flag → package
        for a in args:
            if isinstance(a, str) and a and not a.startswith("-"):
                pkg_name = a
                break
        if pkg_name:
            return ResolvedTarget(
                kind="config_snippet",
                package_spec=f"npm:{pkg_name}",
                version="latest",
                source_url=None,
                raw_input=raw[:_MAX_RAW_LEN],
            )
    elif command_lc in ("uvx", "pipx"):
        for a in args:
            if isinstance(a, str) and a and not a.startswith("-"):
                pkg_name = a
                break
        if pkg_name:
            return ResolvedTarget(
                kind="config_snippet",
                package_spec=f"pypi:{pkg_name}",
                version="latest",
                source_url=None,
                raw_input=raw[:_MAX_RAW_LEN],
            )
    return None


# Phase I-8 (Fix AS) : noms d'outils génériques qui ne sont JAMAIS le
# serveur MCP recherché (faux candidats fréquents dans les READMEs).
_GENERIC_TOOL_NAMES = frozenset({
    "uv", "pip", "pip3", "node", "npx", "uvx", "python", "python3",
    "npm", "yarn", "pnpm", "run", "tool", "install",
})

# Snippets JSON de config claude_desktop dans les READMEs :
# `"command": "npx", "args": ["-y", "@scope/pkg"]` — souvent la SEULE
# mention du vrai package (observé : context7).
_JSON_ARGS_RE = re.compile(r'"args"\s*:\s*\[([^\]]{0,400})\]')
_JSON_COMMAND_RE = re.compile(r'"command"\s*:\s*"([a-z0-9.\-_]+)"', re.IGNORECASE)
_JSON_PKG_TOKEN_RE = re.compile(
    r'"(@[a-z0-9][a-z0-9\-_.]*/[a-z0-9][a-z0-9\-_.]*|'
    r'[a-z0-9][a-z0-9\-_.]{2,})"',
    re.IGNORECASE,
)


def _extract_json_config_candidates(readme: str) -> List[Tuple[int, str]]:
    """Extrait les packages des snippets JSON de config MCP du README."""
    out: List[Tuple[int, str]] = []
    for m in _JSON_ARGS_RE.finditer(readme):
        window = readme[max(0, m.start() - 250):m.end() + 250]
        cm = _JSON_COMMAND_RE.search(window)
        cmd = cm.group(1).lower() if cm else ""
        for tm in _JSON_PKG_TOKEN_RE.finditer(m.group(1)):
            tok = tm.group(1)
            low = tok.lower()
            if low in _GENERIC_TOOL_NAMES or low.endswith((".py", ".js")):
                continue
            if tok.startswith("@") or cmd == "npx":
                out.append((m.start(), f"npm:{tok}"))
            elif cmd in ("uvx", "uv", "pip", "pip3", "pipx"):
                out.append((m.start(), f"pypi:{tok}"))
            break  # premier token package-like du bloc seulement
    return out


# Mentions PASSIVES d'un package scoped contenant « mcp » (badges npm/
# smithery, prose) — certains READMEs vitrine (observé : context7) ne
# montrent leur vrai package QUE là. Malus vs une commande active.
_SCOPED_MCP_MENTION_RE = re.compile(
    r"(@[a-z0-9][a-z0-9\-_.]*/[a-z0-9\-_.]*mcp[a-z0-9\-_.]*)",
    re.IGNORECASE,
)


def _score_readme_candidate(
    spec: str, repo_hint: Optional[str], occurrences: int,
    *, is_mention: bool = False,
) -> int:
    """Score de plausibilité « c'est LE serveur MCP de ce repo »."""
    name = spec.split(":", 1)[1].lower()
    score = -1 if is_mention else 0
    if "mcp" in name:
        score += 2
    if repo_hint:
        repo_tokens = {
            t for t in re.split(r"[-_.\s]+", repo_hint.lower()) if len(t) >= 3
        }
        name_tokens = {
            t for t in re.split(r"[-_./@]+", name) if len(t) >= 3
        }
        if repo_tokens & name_tokens:
            score += 2
    if occurrences >= 2:
        score += 1
    return score


def _extract_install_from_readme(
    readme: str, repo_hint: Optional[str] = None,
) -> Optional[str]:
    """Cherche un package_spec resolu depuis le README.

    Phase I-8 (Fix AS) : collecte TOUS les candidats (commandes shell +
    snippets JSON de config) puis scoring (« mcp » dans le nom, overlap
    avec le nom du repo, fréquence) — tie-break par position. Renvoie
    "npm:<name>" / "pypi:<name>" / None.
    """
    if not isinstance(readme, str) or not readme:
        return None
    candidates: List[Tuple[int, str]] = []
    for m in _NPM_INSTALL_RE.finditer(readme):
        candidates.append((m.start(), f"npm:{m.group(1)}"))
    for m in _PYPI_INSTALL_RE.finditer(readme):
        candidates.append((m.start(), f"pypi:{m.group(1)}"))
    candidates.extend(_extract_json_config_candidates(readme))
    candidates = [
        (pos, spec) for pos, spec in candidates
        if spec.split(":", 1)[1].lower() not in _GENERIC_TOOL_NAMES
    ]
    active_specs = {spec for _, spec in candidates}
    # Source 4 : mentions passives @scope/...mcp... (badges, prose) —
    # uniquement si pas déjà vues comme commande active.
    mention_specs: set = set()
    for m in _SCOPED_MCP_MENTION_RE.finditer(readme):
        spec = f"npm:{m.group(1)}"
        if spec not in active_specs:
            mention_specs.add(spec)
            candidates.append((m.start(), spec))
    if not candidates:
        return None
    # Phase I-8 (Fix AS) : scoring au lieu de « premier match gagne ».
    # Calibré sur de VRAIS READMEs (2026-06-12) : Windows-MCP mentionne
    # `npx @anthropic-ai/mcpb` (outil de packaging) avant les 6 occurrences
    # de `pip install windows-mcp` ; context7 ne montre son vrai package
    # (@upstash/context7-mcp) QUE dans ses badges npm/smithery. L'ancien
    # « premier match npm gagne » élisait le mauvais package dans les
    # deux cas. La sonde d'existence (Fix AB) et le ticket humain restent
    # les filets en aval.
    first_pos: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for pos, spec in candidates:
        counts[spec] = counts.get(spec, 0) + 1
        if spec not in first_pos or pos < first_pos[spec]:
            first_pos[spec] = pos
    return min(
        counts.keys(),
        key=lambda s: (
            -_score_readme_candidate(
                s, repo_hint, counts[s], is_mention=s in mention_specs,
            ),
            first_pos[s],
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Phase I-8 (Fix AB) : sonde d'existence registry npm/PyPI
# ──────────────────────────────────────────────────────────────────────────────

_PROBE_TIMEOUT_S = 4.0


def _default_registry_head(url: str) -> Optional[int]:
    """GET minimal stdlib → status code HTTP, None si réseau indisponible."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "lumena-mcp-probe"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:  # noqa: BLE001 — timeout/DNS/offline
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Phase I-8 (Fix AS) : fetch README GitHub par défaut
# ──────────────────────────────────────────────────────────────────────────────

_README_TIMEOUT_S = 5.0
_README_MAX_BYTES = 256_000  # un README MCP fait quelques Ko ; cap défensif


def _default_github_readme_fetch(repo_url: str) -> str:
    """Télécharge le README brut d'un repo GitHub (stdlib, best-effort).

    Vise raw.githubusercontent.com (texte brut, fiable en urllib) plutôt
    que la page github.com (HTML lourd, parfois bloquée pour les clients
    non-navigateurs — ERR_ABORTED observés runtime côté Playwright).
    Essaie HEAD (branche par défaut) puis les variantes de casse usuelles.

    Returns "" si introuvable/réseau indisponible — ne lève JAMAIS.
    """
    m = _GITHUB_URL_RE.match(repo_url if isinstance(repo_url, str) else "")
    if not m:
        return ""
    owner, repo = m.group(1), m.group(2)
    import urllib.request
    candidates = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/readme.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.rst",
    )
    for url in candidates:
        req = urllib.request.Request(
            url, headers={"User-Agent": "lumena-mcp-readme"}
        )
        try:
            with urllib.request.urlopen(
                req, timeout=_README_TIMEOUT_S
            ) as resp:
                data = resp.read(_README_MAX_BYTES)
                return data.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 404/timeout/DNS → candidat suivant
            continue
    return ""


def probe_package_exists(
    package_spec: str,
    *,
    http_status_callable: Optional[Callable[[str], Optional[int]]] = None,
) -> Optional[bool]:
    """Vérifie qu'un package npm/PyPI existe réellement sur son registry.

    Garde-fou anti-hallucination (observé runtime 2026-06-11 00:14 :
    add_mcp a accepté `npm:@nicholaschen/weather-mcp`, package INVENTÉ
    par le LLM, créant une entrée catalogue fantôme).

    Retourne :
      True  → le registry confirme l'existence (HTTP 200)
      False → le registry confirme l'absence (HTTP 404)
      None  → indéterminé (réseau indisponible, transport local:, statut
              inattendu) — l'appelant ne doit PAS bloquer sur None.
    """
    if not isinstance(package_spec, str):
        return None
    probe = http_status_callable or _default_registry_head
    if package_spec.startswith("npm:"):
        name = package_spec[4:].strip()
        if not name:
            return False
        # Scoped packages : le `/` doit être encodé pour le registry.
        encoded = name.replace("/", "%2F")
        status = probe(f"https://registry.npmjs.org/{encoded}")
    elif package_spec.startswith("pypi:"):
        name = package_spec[5:].strip()
        if not name:
            return False
        status = probe(f"https://pypi.org/pypi/{name}/json")
    else:
        return None  # local:/inconnu — pas de registry à interroger
    if status is None:
        return None
    if status == 200:
        return True
    if status == 404:
        return False
    return None


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────


def resolve_target(
    target: str,
    *,
    web_fetch_callable: Optional[Callable[[str], str]] = None,
) -> ResolvedTarget:
    """Resout une cible MCP brute fournie par l'utilisateur.

    Ordre de detection (premier match gagne, Phase I-1) :
      0. KNOWN_MCPS curated (slug + alias + match contains)
      1. package_spec npm/pypi/local (Phase 14 valide)
      2. URL GitHub → web_fetch README → extract install command
      3. snippet JSON claude_desktop style
      4. chemin local
      5. fallback intent (texte libre) → boucle autonomy

    Args:
        target: chaine brute fournie par l'user.
        web_fetch_callable: callable optionnel `(url: str) -> str` qui
            retourne le contenu HTTP. Injecte pour testabilite — JAMAIS
            d'appel HTTP direct dans cette fonction.

    Returns:
        ResolvedTarget. kind="unknown" si la chaine est vide/invalide,
        kind="intent" pour tout le reste non identifie (fallback safe).
    """
    raw = _normalize_input(target)
    if not raw:
        return ResolvedTarget(
            kind="unknown",
            package_spec=None, version=None,
            source_url=None, raw_input="",
        )

    # 0) Phase I-1 : KNOWN_MCPS curated PRIORITAIRE.
    # On evite la lookup curated si l'input est deja un package_spec/URL
    # explicite — l'utilisateur l'a deja resolu lui-meme.
    if not _PACKAGE_SPEC_RE.match(raw) and not _GITHUB_URL_RE.match(raw) \
            and not _looks_like_json(raw) and not _LOCAL_PATH_RE.match(raw):
        try:
            from src.mcp.known_mcps import lookup_known_mcp
            from src.mcp.config_schema import schema_to_dict
            known = lookup_known_mcp(raw)
        except Exception:  # noqa: BLE001
            known = None
        if known is not None:
            try:
                schema_dict = schema_to_dict(known.to_schema())
            except Exception:  # noqa: BLE001
                schema_dict = None
            return ResolvedTarget(
                kind="known_mcp",
                package_spec=known.package_spec,
                version="latest",
                source_url=known.docs_url,
                raw_input=raw,
                slug=known.slug,
                display_name=known.display_name,
                description=known.description,
                semantic_category=known.semantic_category,
                trust_score=known.trust_score,
                docs_url=known.docs_url,
                config_schema_dict=schema_dict,
            )

    # 1) package_spec direct
    if _PACKAGE_SPEC_RE.match(raw):
        # Extraction version optionnelle : on garde "latest" par defaut.
        return ResolvedTarget(
            kind="package_spec",
            package_spec=raw,
            version="latest",
            source_url=None,
            raw_input=raw,
        )

    # 2) GitHub URL
    gh = _GITHUB_URL_RE.match(raw)
    if gh:
        owner, repo = gh.group(1), gh.group(2)
        package_spec: Optional[str] = None
        # Phase I-8 (Fix AS) : fetcher par défaut quand aucun n'est
        # injecté. Avant, les appelants réels (handle_add_mcp,
        # autonomy_orchestrator) n'injectaient JAMAIS de fetcher → le
        # README n'était jamais lu → package_spec=None → toute URL
        # GitHub finissait en `mcp_action_failed` générique.
        fetcher = (
            web_fetch_callable
            if web_fetch_callable is not None and callable(web_fetch_callable)
            else _default_github_readme_fetch
        )
        try:
            readme = fetcher(raw)
        except Exception:  # noqa: BLE001
            readme = ""
        if isinstance(readme, str):
            package_spec = _extract_install_from_readme(
                readme, repo_hint=repo,
            )
        return ResolvedTarget(
            kind="github_url",
            package_spec=package_spec,
            version="latest" if package_spec else None,
            source_url=raw,
            raw_input=raw,
        )

    # 3) Snippet JSON
    if _looks_like_json(raw):
        snippet = _extract_from_config_snippet(raw)
        if snippet is not None:
            return snippet

    # 4) Chemin local — on detecte uniquement le pattern, on ne resout pas.
    if _LOCAL_PATH_RE.match(raw):
        # On essaie de slugifier le nom de dossier pour proposer un local:slug
        try:
            stem = Path(raw).name.lower()
            slug = re.sub(r"[^a-z0-9_.\-]", "-", stem).strip("-")
            if slug and re.match(r"^[a-z0-9][a-z0-9_.\-]{0,63}$", slug):
                pkg = f"local:{slug}"
            else:
                pkg = None
        except Exception:  # noqa: BLE001
            pkg = None
        return ResolvedTarget(
            kind="local_path",
            package_spec=pkg,
            version="latest" if pkg else None,
            source_url=None,
            raw_input=raw,
        )

    # 5) fallback intent (boucle autonomy)
    return ResolvedTarget(
        kind="intent",
        package_spec=None,
        version=None,
        source_url=None,
        raw_input=raw,
    )
