"""
known_mcps.py — Catalogue curated des MCPs officiels et populaires.

Doctrine :
  - Entrée curated = qualité MAX, hand-crafted, schéma vérifié manuellement.
  - Couvre les ~20 MCPs les plus utilisés de l'écosystème (Anthropic officiels
    + communautaires majeurs).
  - Tout autre MCP passe par la cascade Niveau 2-4 (auto-détection).
  - lookup_known_mcp(intent) : normalisation + alias → KnownMCP ou None.

Sources :
  - github.com/modelcontextprotocol/servers (officiels Anthropic)
  - npmjs.com keyword:mcp + topic GitHub mcp-server
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from src.mcp.config_schema import (
    AuthFlow,
    ConfigField,
    ConfigKind,
    MCPConfigSchema,
    Sensitivity,
)


# ──────────────────────────────────────────────────────────────────────────────
# KnownMCP
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KnownMCP:
    """Description complète d'un MCP catalogué."""
    slug: str                            # "slack"
    aliases: Tuple[str, ...]             # ("slack-mcp", "slack-workspace")
    display_name: str                    # "Slack Workspace"
    description: str                     # "Envoyer/lire des messages Slack"
    package_spec: str                    # "npm:@modelcontextprotocol/server-slack"
    transport: str                       # "npm" | "pypi"
    semantic_category: str               # "communication"
    trust_score: int                     # 0-100
    docs_url: Optional[str]              # URL de la doc
    fields: Tuple[ConfigField, ...]      # Champs de config requis
    auth_flows: Tuple[AuthFlow, ...] = ()

    def to_schema(self) -> MCPConfigSchema:
        """Construit un MCPConfigSchema correspondant."""
        return MCPConfigSchema(
            server_id=self.slug,
            fields=self.fields,
            auth_flows=self.auth_flows,
            detected_from="curated",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de construction (réduisent la verbosité du seed)
# ──────────────────────────────────────────────────────────────────────────────


def _secret(name: str, label: str, description: str,
            *, placeholder: str = "", obtained_from: str = "",
            kind: ConfigKind = ConfigKind.SECRET_TOKEN,
            docs_url: Optional[str] = None,
            group: str = "Authentification") -> ConfigField:
    return ConfigField(
        name=name,
        label=label,
        description=description,
        kind=kind,
        sensitivity=Sensitivity.SECRET,
        required=True,
        placeholder=placeholder or None,
        obtained_from=obtained_from or None,
        docs_url=docs_url,
        group=group,
    )


def _string(name: str, label: str, description: str,
            *, required: bool = True, default: Optional[str] = None,
            placeholder: str = "",
            group: str = "Configuration",
            kind: ConfigKind = ConfigKind.STRING) -> ConfigField:
    return ConfigField(
        name=name,
        label=label,
        description=description,
        kind=kind,
        sensitivity=Sensitivity.NORMAL,
        required=required,
        default=default,
        placeholder=placeholder or None,
        group=group,
    )


def _path_list(name: str, label: str, description: str,
               *, required: bool = True,
               placeholder: str = '["C:/Users/me/Desktop"]',
               group: str = "Configuration") -> ConfigField:
    return ConfigField(
        name=name,
        label=label,
        description=description,
        kind=ConfigKind.PATH_LIST,
        sensitivity=Sensitivity.NORMAL,
        required=required,
        placeholder=placeholder,
        group=group,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Catalogue curated (~20 MCPs officiels + populaires)
# ──────────────────────────────────────────────────────────────────────────────


_REGISTRY: Tuple[KnownMCP, ...] = (
    # ── Zero-config ─────────────────────────────────────────────────────────
    KnownMCP(
        slug="memory",
        aliases=("mcp-memory", "memory-mcp"),
        display_name="Mémoire persistante",
        description="Stockage clé/valeur persistant entre sessions.",
        package_spec="npm:@modelcontextprotocol/server-memory",
        transport="npm",
        semantic_category="memory",
        trust_score=95,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        fields=(),
    ),
    KnownMCP(
        slug="fetch",
        aliases=("mcp-fetch", "fetch-mcp"),
        display_name="Fetch HTTP",
        description="Effectue des requêtes HTTP GET simples.",
        package_spec="pypi:mcp-server-fetch",
        transport="pypi",
        semantic_category="web",
        trust_score=95,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        fields=(),
    ),
    KnownMCP(
        slug="time",
        aliases=("mcp-time",),
        display_name="Date & Heure",
        description="Accès à l'heure, fuseaux et conversions.",
        package_spec="pypi:mcp-server-time",
        transport="pypi",
        semantic_category="system",
        trust_score=95,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        fields=(),
    ),
    KnownMCP(
        slug="everything",
        aliases=("mcp-everything", "test-mcp"),
        display_name="MCP Test (everything)",
        description="MCP de test exposant tous les types d'outils.",
        package_spec="npm:@modelcontextprotocol/server-everything",
        transport="npm",
        semantic_category="custom",
        trust_score=90,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
        fields=(),
    ),

    # ── Config-only (paths/strings, pas de secrets) ─────────────────────────
    KnownMCP(
        slug="filesystem",
        aliases=("fs", "files-mcp", "mcp-filesystem"),
        display_name="Système de fichiers",
        description="Accès lecture/écriture restreint à des dossiers autorisés.",
        package_spec="npm:@modelcontextprotocol/server-filesystem",
        transport="npm",
        semantic_category="files",
        trust_score=95,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        fields=(
            _path_list(
                "ALLOWED_PATHS",
                "Dossiers autorisés",
                "Liste JSON des dossiers que ce MCP peut lire/écrire.",
                placeholder='["C:/Users/me/Documents", "C:/projets"]',
            ),
        ),
    ),
    KnownMCP(
        slug="sqlite",
        aliases=("mcp-sqlite", "sqlite-mcp"),
        display_name="SQLite",
        description="Lecture/écriture d'une base SQLite locale.",
        package_spec="pypi:mcp-server-sqlite",
        transport="pypi",
        semantic_category="data",
        trust_score=92,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
        fields=(
            ConfigField(
                name="SQLITE_DB_PATH",
                label="Chemin de la base SQLite",
                description="Fichier .db ou .sqlite à utiliser.",
                kind=ConfigKind.PATH_FILE,
                sensitivity=Sensitivity.NORMAL,
                required=True,
                placeholder="C:/data/mydb.sqlite",
                group="Configuration",
            ),
        ),
    ),

    # ── Avec secrets simples ────────────────────────────────────────────────
    KnownMCP(
        slug="slack",
        aliases=("slack-mcp", "slack-workspace", "mcp-slack"),
        display_name="Slack Workspace",
        description="Lire/envoyer des messages dans un workspace Slack.",
        package_spec="npm:@modelcontextprotocol/server-slack",
        transport="npm",
        semantic_category="communication",
        trust_score=90,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        fields=(
            _secret(
                "SLACK_BOT_TOKEN", "Token Bot Slack",
                "Bot User OAuth Token de ton app Slack.",
                placeholder="xoxb-...",
                obtained_from="api.slack.com/apps → ton app → OAuth & Permissions → Bot User OAuth Token",
                docs_url="https://api.slack.com/authentication/oauth-v2",
            ),
            _string(
                "SLACK_TEAM_ID", "ID Workspace",
                "Identifiant de ton workspace Slack (commence par T).",
                placeholder="T01234ABCDE",
                group="Configuration",
            ),
        ),
    ),
    KnownMCP(
        slug="github",
        aliases=("github-mcp", "gh", "mcp-github"),
        display_name="GitHub",
        description="Lire issues, PRs, repos, contenu de fichiers.",
        package_spec="npm:@modelcontextprotocol/server-github",
        transport="npm",
        semantic_category="github",
        trust_score=95,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        fields=(
            _secret(
                "GITHUB_PERSONAL_ACCESS_TOKEN", "GitHub PAT",
                "Personal Access Token (classic ou fine-grained).",
                placeholder="ghp_...",
                obtained_from="github.com/settings/tokens → Generate new token",
                docs_url="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
            ),
        ),
    ),
    KnownMCP(
        slug="gitlab",
        aliases=("gitlab-mcp", "mcp-gitlab"),
        display_name="GitLab",
        description="Accès aux projets et issues GitLab.",
        package_spec="npm:@modelcontextprotocol/server-gitlab",
        transport="npm",
        semantic_category="git",
        trust_score=90,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab",
        fields=(
            _secret(
                "GITLAB_PERSONAL_ACCESS_TOKEN", "GitLab PAT",
                "Personal Access Token GitLab avec scope api.",
                placeholder="glpat-...",
                obtained_from="gitlab.com/-/profile/personal_access_tokens",
            ),
            _string(
                "GITLAB_API_URL", "URL API GitLab",
                "URL de l'API (par défaut gitlab.com/api/v4).",
                required=False,
                default="https://gitlab.com/api/v4",
                kind=ConfigKind.URL,
            ),
        ),
    ),
    KnownMCP(
        slug="linear",
        aliases=("linear-mcp", "mcp-linear"),
        display_name="Linear",
        description="Issues, projets et cycles Linear.",
        package_spec="npm:@tacticlaunch/mcp-linear",
        transport="npm",
        semantic_category="project",
        trust_score=80,
        docs_url="https://github.com/tacticlaunch/mcp-linear",
        fields=(
            _secret(
                "LINEAR_API_KEY", "Linear API Key",
                "Personal API key Linear.",
                placeholder="lin_api_...",
                obtained_from="linear.app/settings/api",
                kind=ConfigKind.SECRET_API_KEY,
            ),
        ),
    ),
    KnownMCP(
        slug="notion",
        aliases=("notion-mcp", "mcp-notion"),
        display_name="Notion",
        description="Pages, bases de données et blocs Notion.",
        package_spec="npm:@suekou/mcp-notion-server",
        transport="npm",
        semantic_category="notion",
        trust_score=80,
        docs_url="https://github.com/suekou/mcp-notion-server",
        fields=(
            _secret(
                "NOTION_API_TOKEN", "Token Integration Notion",
                "Internal Integration Secret d'une integration Notion.",
                placeholder="secret_...",
                obtained_from="notion.so/profile/integrations",
            ),
        ),
    ),
    KnownMCP(
        slug="brave-search",
        aliases=("brave", "mcp-brave-search"),
        display_name="Brave Search",
        description="Recherche web et locale via Brave Search API.",
        package_spec="npm:@modelcontextprotocol/server-brave-search",
        transport="npm",
        semantic_category="web",
        trust_score=92,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        fields=(
            _secret(
                "BRAVE_API_KEY", "Brave API Key",
                "Clé API Brave Search.",
                placeholder="BSA...",
                obtained_from="api.search.brave.com → S'inscrire et obtenir une clé gratuite",
                kind=ConfigKind.SECRET_API_KEY,
            ),
        ),
    ),
    KnownMCP(
        slug="tavily",
        aliases=("tavily-search", "mcp-tavily"),
        display_name="Tavily Search",
        description="Recherche web spécialisée AI agents.",
        package_spec="npm:@mcptools/mcp-tavily",
        transport="npm",
        semantic_category="web",
        trust_score=80,
        docs_url="https://tavily.com",
        fields=(
            _secret(
                "TAVILY_API_KEY", "Tavily API Key",
                "Clé API Tavily.",
                placeholder="tvly-...",
                obtained_from="tavily.com → Dashboard → API keys",
                kind=ConfigKind.SECRET_API_KEY,
            ),
        ),
    ),
    KnownMCP(
        slug="sentry",
        aliases=("mcp-sentry",),
        display_name="Sentry",
        description="Lire les events et issues Sentry.",
        package_spec="pypi:mcp-server-sentry",
        transport="pypi",
        semantic_category="security",
        trust_score=85,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sentry",
        fields=(
            _secret(
                "SENTRY_AUTH_TOKEN", "Sentry Auth Token",
                "Auth Token avec scope project:read.",
                placeholder="sntrys_...",
                obtained_from="sentry.io/settings/account/api/auth-tokens/",
            ),
        ),
    ),

    # ── Mixte : config + secrets ───────────────────────────────────────────
    KnownMCP(
        slug="postgres",
        aliases=("postgresql", "mcp-postgres"),
        display_name="PostgreSQL",
        description="Lecture/écriture sur une base PostgreSQL.",
        package_spec="npm:@modelcontextprotocol/server-postgres",
        transport="npm",
        semantic_category="data",
        trust_score=92,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        fields=(
            _string(
                "POSTGRES_HOST", "Hôte",
                "Hôte du serveur PostgreSQL.",
                default="localhost", group="Connexion",
            ),
            _string(
                "POSTGRES_PORT", "Port",
                "Port du serveur (par défaut 5432).",
                default="5432", group="Connexion",
                kind=ConfigKind.INTEGER,
            ),
            _string(
                "POSTGRES_USER", "Utilisateur",
                "Nom d'utilisateur PostgreSQL.",
                group="Connexion",
            ),
            _string(
                "POSTGRES_DB", "Base de données",
                "Nom de la base.",
                group="Connexion",
            ),
            _secret(
                "POSTGRES_PASSWORD", "Mot de passe",
                "Mot de passe utilisateur.",
                kind=ConfigKind.SECRET_PASSWORD,
                group="Authentification",
            ),
        ),
    ),

    # ── OAuth flows ─────────────────────────────────────────────────────────
    KnownMCP(
        slug="google-drive",
        aliases=("gdrive", "drive", "mcp-google-drive"),
        display_name="Google Drive",
        description="Accès aux fichiers Google Drive (OAuth requis).",
        package_spec="npm:@modelcontextprotocol/server-gdrive",
        transport="npm",
        semantic_category="files",
        trust_score=90,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive",
        fields=(
            _secret(
                "GDRIVE_CLIENT_ID", "Client ID OAuth Google",
                "OAuth 2.0 Client ID depuis Google Cloud Console.",
                kind=ConfigKind.OAUTH_CLIENT_ID,
                obtained_from="console.cloud.google.com → APIs & Services → Credentials",
            ),
            _secret(
                "GDRIVE_CLIENT_SECRET", "Client Secret OAuth Google",
                "OAuth 2.0 Client Secret.",
                kind=ConfigKind.OAUTH_CLIENT_SECRET,
            ),
        ),
        auth_flows=(
            AuthFlow(
                kind="oauth2_authorization_code",
                provider="google",
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                scopes=("https://www.googleapis.com/auth/drive.readonly",),
                docs_url="https://developers.google.com/identity/protocols/oauth2",
            ),
        ),
    ),

    # ── Outils dev/automation ──────────────────────────────────────────────
    KnownMCP(
        slug="puppeteer",
        aliases=("mcp-puppeteer", "browser-mcp"),
        display_name="Puppeteer (Browser)",
        description="Automatisation Chrome/Chromium headless.",
        package_spec="npm:@modelcontextprotocol/server-puppeteer",
        transport="npm",
        semantic_category="browser",
        trust_score=88,
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
        fields=(),
    ),
)


# ──────────────────────────────────────────────────────────────────────────────
# Index + lookup
# ──────────────────────────────────────────────────────────────────────────────


def _build_index() -> Dict[str, KnownMCP]:
    """Index normalisé : slug + aliases → KnownMCP."""
    out: Dict[str, KnownMCP] = {}
    for mcp in _REGISTRY:
        out[mcp.slug] = mcp
        for alias in mcp.aliases:
            out[alias] = mcp
    return out


_INDEX: Dict[str, KnownMCP] = _build_index()


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Normalise pour matching : lowercase + supprime ponctuation/espaces."""
    if not isinstance(text, str):
        return ""
    return _NORMALIZE_RE.sub("-", text.lower()).strip("-")


def lookup_known_mcp(intent: str) -> Optional[KnownMCP]:
    """Cherche un MCP curated par intent.

    Stratégie :
      1. Match exact normalisé sur slug ou alias
      2. Match contenant le slug normalisé dans l'intent normalisé
         (ex: 'installe slack' → 'slack')
      3. None si aucun match

    Returns:
        KnownMCP ou None.
    """
    if not isinstance(intent, str) or not intent:
        return None
    norm = _normalize(intent)
    if not norm:
        return None
    # Match exact
    direct = _INDEX.get(norm)
    if direct is not None:
        return direct
    # Match contains (intent libre type "installe slack pour moi")
    # On itère sur les slugs canoniques pour éviter les double-match d'alias.
    for mcp in _REGISTRY:
        slug_norm = _normalize(mcp.slug)
        if slug_norm and slug_norm in norm:
            return mcp
        for alias in mcp.aliases:
            alias_norm = _normalize(alias)
            if alias_norm and alias_norm in norm:
                return mcp
    return None


def list_known_mcp_slugs() -> Tuple[str, ...]:
    """Liste les slugs canoniques (utile pour discovery UI / tests)."""
    return tuple(mcp.slug for mcp in _REGISTRY)


def get_known_mcp(slug: str) -> Optional[KnownMCP]:
    """Retourne le KnownMCP par slug exact (sans normalisation)."""
    if not isinstance(slug, str):
        return None
    return _INDEX.get(slug)


def find_known_mcp_by_package_spec(package_spec: str) -> Optional[KnownMCP]:
    """Retourne le KnownMCP curated dont le package_spec matche exactement.

    Utilise par proposal_planner._derive_server_id pour préférer le slug
    canonique curated (ex: "slack") au lieu d'un hash synthétique
    (ex: "proposed_e28f7ba17d") quand le package résolu est un MCP officiel
    présent dans le registry. Sans ça, le bypass auto-approve curated
    de Phase I-7 ne peut jamais matcher.
    """
    if not isinstance(package_spec, str) or not package_spec.strip():
        return None
    target = package_spec.strip()
    for mcp in _REGISTRY:
        if mcp.package_spec == target:
            return mcp
    return None


__all__ = [
    "KnownMCP",
    "lookup_known_mcp",
    "list_known_mcp_slugs",
    "get_known_mcp",
]
