"""
Phase A — Cascade de classification sémantique des outils MCP.

Composant pur testable, sans side effect, sans import vers d'autres modules
src/mcp/* ni src/reasoning/*. Toutes les dépendances externes (LLM) sont
injectées en argument pour rester testable en isolation.

Doctrine :
  - Mappe n'importe quel serveur MCP vers UNE catégorie sémantique parmi
    les 37 catégories Lumena existantes (cf. tool_categories.py).
  - Cascade à 4 niveaux : cache > static > heuristic > LLM > fallback.
  - Fallback safe sur "mcp" (le contrat unifié Phase D).
  - HUMAN_FRIENDLY_LABELS et HUMAN_TO_CATEGORY pour les interactions
    utilisateur en langage naturel (jamais de jargon technique exposé).
  - Aucune mutation, fonctions pures.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════════
# Catégories Lumena valides (sync avec src/reasoning/tool_categories.py)
# ══════════════════════════════════════════════════════════════════════════════

VALID_CATEGORIES: FrozenSet[str] = frozenset({
    "agents", "automation", "autonomy", "browser", "codebase",
    "communication", "computer_use", "custom", "data", "discord",
    "documents", "files", "git", "github", "ide", "image", "ionos",
    "lsp", "mail", "mcp", "media", "memory", "network", "notion",
    "peers", "platform", "project", "security", "skills", "social",
    "spotify", "stripe", "system", "video", "web", "website",
})


# ══════════════════════════════════════════════════════════════════════════════
# Niveau 1 — Table statique : server_name → catégorie sémantique
# ══════════════════════════════════════════════════════════════════════════════

_MCP_SERVER_NAME_TO_SEMANTIC: Dict[str, str] = {
    # Mail
    "gmail": "mail", "outlook": "mail", "imap": "mail",
    "smtp": "mail", "mailgun": "mail", "sendgrid": "mail",
    # Messaging / Social
    "slack": "social", "discord": "discord", "telegram": "communication",
    "whatsapp": "communication", "twitter": "social", "x-twitter": "social",
    "linkedin": "social", "messenger": "communication",
    # Code / Git
    "github": "github", "gitlab": "github", "bitbucket": "github",
    "git": "git",
    # Web / Search
    "brave": "web", "tavily": "web", "fetch": "web", "duckduckgo": "web",
    "google-search": "web", "kagi": "web", "perplexity": "web",
    "exa": "web",
    # Files / Storage
    "filesystem": "files", "gdrive": "files", "dropbox": "files",
    "onedrive": "files", "s3": "files", "fs": "files",
    # Browser
    "puppeteer": "browser", "playwright": "browser", "selenium": "browser",
    "chrome": "browser", "firefox": "browser",
    # Data / DB
    "postgres": "data", "postgresql": "data", "sqlite": "data",
    "mysql": "data", "mongodb": "data", "redis": "data",
    "weather": "data", "stocks": "data", "datagouv": "data",
    "insee": "data", "sirene": "data",
    # Memory
    "memory": "memory", "knowledge": "memory", "kb": "memory",
    # Platform
    "notion": "notion", "stripe": "stripe", "n8n": "automation",
    "zapier": "automation", "airtable": "platform", "asana": "platform",
    "trello": "platform", "monday": "platform", "linear": "platform",
    "jira": "platform", "ionos": "ionos",
    # Image / Video
    "image": "image", "dalle": "image", "midjourney": "image",
    "stable-diffusion": "image", "flux": "image",
    "video": "video", "remotion": "video",
    # Time / Calendar
    "time": "communication", "calendar": "communication",
    "gcal": "communication", "google-calendar": "communication",
    # Spotify / Music
    "spotify": "spotify", "music": "spotify",
    # IDE
    "ide": "ide", "vscode": "ide", "lsp": "lsp",
    # Documents
    "pdf": "documents", "docx": "documents", "xlsx": "documents",
    "pptx": "documents", "office": "documents",
    # Network
    "network": "network", "nmap": "network", "ssh": "network",
    # Security
    "osint": "security", "security": "security",
    # System
    "shell": "system", "command": "system",
}


# ══════════════════════════════════════════════════════════════════════════════
# Niveau 2 — Heuristique sur descriptions des tools (vote majoritaire)
# ══════════════════════════════════════════════════════════════════════════════

_TOOL_DESC_KEYWORDS_TO_CATEGORY: Dict[FrozenSet[str], str] = {
    frozenset({"email", "mail", "smtp", "imap", "inbox", "message"}): "mail",
    frozenset({"file", "directory", "folder", "path", "read", "write", "filesystem"}): "files",
    frozenset({"http", "fetch", "url", "scrape", "html", "webpage", "request"}): "web",
    frozenset({"sql", "query", "table", "database", "row", "column"}): "data",
    frozenset({"image", "png", "jpg", "render", "generate image", "draw"}): "image",
    frozenset({"video", "render video", "frame", "encode", "mp4"}): "video",
    frozenset({"calendar", "event", "schedule", "meeting", "appointment"}): "communication",
    frozenset({"repo", "repository", "commit", "branch", "pull request", "issue", "git"}): "github",
    frozenset({"slack", "channel", "post message"}): "social",
    frozenset({"discord channel", "guild", "discord message"}): "discord",
    frozenset({"browser", "playwright", "puppeteer", "navigate", "click"}): "browser",
    frozenset({"memory", "remember", "recall", "knowledge", "embedding"}): "memory",
    frozenset({"notion", "page", "block", "database notion"}): "notion",
    frozenset({"stripe", "payment", "customer", "subscription", "invoice"}): "stripe",
    frozenset({"shell", "command", "subprocess", "execute"}): "system",
    frozenset({"playwright", "browser", "dom", "click", "type"}): "browser",
    frozenset({"document", "pdf", "docx", "xlsx", "pptx"}): "documents",
    frozenset({"music", "playlist", "spotify", "track"}): "spotify",
}

# Seuil minimal de votes pour qu'une catégorie soit retenue par le niveau 2.
_HEURISTIC_MIN_VOTES = 1


# ══════════════════════════════════════════════════════════════════════════════
# HUMAN-FRIENDLY LABELS (catégorie technique → mots humains)
# Utilisés par les outils LLM Phase F pour parler à l'utilisateur sans jargon.
# ══════════════════════════════════════════════════════════════════════════════

HUMAN_FRIENDLY_LABELS: Dict[str, str] = {
    "mail": "messages et emails",
    "communication": "communication / calendrier",
    "social": "réseaux sociaux",
    "discord": "Discord",
    "github": "code et dépôts GitHub",
    "git": "Git",
    "files": "fichiers sur ton ordinateur",
    "web": "recherche et lecture web",
    "browser": "navigateur web",
    "data": "données et bases de données",
    "memory": "mémoire et connaissances",
    "notion": "Notion",
    "stripe": "paiements Stripe",
    "platform": "plateformes professionnelles",
    "image": "images",
    "video": "vidéos",
    "spotify": "musique Spotify",
    "documents": "documents (PDF, Word, Excel)",
    "system": "système et commandes",
    "ionos": "hébergement IONOS",
    "ide": "éditeur de code",
    "lsp": "analyse de code",
    "network": "réseau",
    "security": "sécurité",
    "agents": "sous-agents",
    "autonomy": "autonomie / planification",
    "automation": "automatisation",
    "codebase": "analyse du code",
    "computer_use": "contrôle de l'ordinateur",
    "custom": "outils personnalisés",
    "media": "médias",
    "peers": "collaboration entre Lumena",
    "project": "projets et travail",
    "skills": "compétences spécifiques",
    "website": "sites web",
    "mcp": "outils MCP génériques",
}


# ══════════════════════════════════════════════════════════════════════════════
# HUMAN_TO_CATEGORY (mots humains → catégorie technique)
# Utilisé quand l'utilisateur dit "boulot" ou "messagerie" — Lumena traduit.
# ══════════════════════════════════════════════════════════════════════════════

_HUMAN_TO_CATEGORY: Dict[str, str] = {
    # Mail / messagerie
    "email": "mail", "emails": "mail", "mail": "mail",
    "mails": "mail", "messagerie": "mail", "courrier": "mail",
    "courriel": "mail", "courriels": "mail", "boite mail": "mail",
    # Communication
    "calendrier": "communication", "agenda": "communication",
    "rdv": "communication", "rendez-vous": "communication",
    "evenement": "communication", "communication": "communication",
    # Social
    "social": "social", "reseaux sociaux": "social",
    "twitter": "social", "slack": "social",
    "linkedin": "social",
    # Discord
    "discord": "discord", "salon discord": "discord",
    # GitHub / Code
    "github": "github", "code": "github", "depot": "github",
    "repo": "github", "pull request": "github", "issue": "github",
    "ticket": "github", "tickets": "github",
    "git": "git",
    # Files
    "fichier": "files", "fichiers": "files",
    "documents locaux": "files", "stockage": "files",
    "dossier": "files", "dossiers": "files",
    # Web
    "web": "web", "recherche": "web", "internet": "web",
    "recherche web": "web", "navigation": "web",
    # Browser
    "navigateur": "browser", "browser": "browser",
    "automation navigateur": "browser",
    # Data
    "donnees": "data", "donnée": "data", "données": "data",
    "base de donnees": "data", "database": "data", "sql": "data",
    "table": "data", "tables": "data",
    # Memory
    "memoire": "memory", "mémoire": "memory",
    "souvenir": "memory", "souvenirs": "memory",
    "connaissances": "memory", "notes": "memory",
    # Platform
    "notion": "notion", "stripe": "stripe",
    "platforme": "platform", "plateforme": "platform",
    "outil pro": "platform", "outils pro": "platform",
    "boulot": "project", "travail": "project",
    "projet": "project", "projets": "project",
    # Image / Video
    "image": "image", "images": "image",
    "photo": "image", "photos": "image",
    "video": "video", "videos": "video", "vidéo": "video", "vidéos": "video",
    # Spotify
    "musique": "spotify", "spotify": "spotify",
    "playlist": "spotify",
    # Documents
    "pdf": "documents", "docx": "documents", "word": "documents",
    "excel": "documents", "xlsx": "documents", "powerpoint": "documents",
    "document": "documents",
    # System
    "shell": "system", "commande": "system", "terminal": "system",
    # IONOS
    "ionos": "ionos", "hebergement": "ionos", "hébergement": "ionos",
    # IDE / LSP
    "ide": "ide", "editeur": "ide", "éditeur": "ide",
    "lsp": "lsp", "analyse code": "lsp",
    # Network / Security
    "reseau": "network", "réseau": "network",
    "securite": "security", "sécurité": "security",
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers internes
# ══════════════════════════════════════════════════════════════════════════════


def _normalize_for_match(text: str) -> str:
    """NFC + lowercase + retrait des accents pour matching tolérant."""
    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFC", text).lower()
    # Retrait accents
    decomposed = unicodedata.normalize("NFKD", normalized)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_valid_category(category: Optional[str]) -> bool:
    return isinstance(category, str) and category in VALID_CATEGORIES


# ══════════════════════════════════════════════════════════════════════════════
# Niveau 1 — Table statique
# ══════════════════════════════════════════════════════════════════════════════


def infer_static(server_name: str) -> Optional[str]:
    """Niveau 1 — Lookup dans la table statique des MCPs connus.

    Args:
        server_name: Nom du serveur MCP (ex: "gmail-srv", "@scope/github").

    Returns:
        Catégorie sémantique si match, sinon None.
    """
    if not isinstance(server_name, str) or not server_name:
        return None
    name_lower = _normalize_for_match(server_name)
    # Tri par longueur décroissante pour matcher les keywords les plus
    # spécifiques d'abord (ex: "google-calendar" avant "google-search").
    sorted_keywords = sorted(
        _MCP_SERVER_NAME_TO_SEMANTIC.keys(),
        key=len, reverse=True,
    )
    for keyword in sorted_keywords:
        if keyword in name_lower:
            category = _MCP_SERVER_NAME_TO_SEMANTIC[keyword]
            if _is_valid_category(category):
                return category
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Niveau 2 — Heuristique vote majoritaire sur descriptions
# ══════════════════════════════════════════════════════════════════════════════


def infer_heuristic(tool_descriptions: List[str]) -> Optional[str]:
    """Niveau 2 — Vote majoritaire sur les keywords des descriptions tools.

    Pour chaque description, on regarde quelles catégories matchent
    (via _TOOL_DESC_KEYWORDS_TO_CATEGORY). La catégorie avec le plus de
    votes gagne. Si égalité ou aucun vote ≥ seuil, retourne None.
    """
    if not isinstance(tool_descriptions, list) or not tool_descriptions:
        return None

    votes: Dict[str, int] = {}
    for desc in tool_descriptions:
        if not isinstance(desc, str) or not desc:
            continue
        desc_lower = _normalize_for_match(desc)
        for keywords, category in _TOOL_DESC_KEYWORDS_TO_CATEGORY.items():
            if not _is_valid_category(category):
                continue
            if any(kw in desc_lower for kw in keywords):
                votes[category] = votes.get(category, 0) + 1

    if not votes:
        return None

    # Catégorie avec le plus de votes, seuil minimal
    sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    top_category, top_votes = sorted_votes[0]
    if top_votes < _HEURISTIC_MIN_VOTES:
        return None

    # Si égalité au premier rang, retourner None (ambigu)
    if len(sorted_votes) > 1 and sorted_votes[1][1] == top_votes:
        return None

    return top_category


# ══════════════════════════════════════════════════════════════════════════════
# Niveau 3 — Classification par LLM (injecté)
# ══════════════════════════════════════════════════════════════════════════════


_LLM_PROMPT_TEMPLATE = (
    "Classifie ce serveur MCP dans UNE des catégories suivantes :\n"
    "{categories}\n\n"
    "Serveur : {server_name}\n"
    "Outils exposés :\n{tools_summary}\n\n"
    "Réponds UNIQUEMENT avec le nom d'une catégorie de la liste. "
    "Si aucune ne convient bien, réponds \"mcp\"."
)


def infer_with_llm(
    server_name: str,
    tool_descriptions: List[str],
    llm_callable: Callable[[str], str],
    *,
    max_tools: int = 10,
) -> Optional[str]:
    """Niveau 3 — Classification par LLM injecté.

    Args:
        server_name: Nom du serveur MCP.
        tool_descriptions: Liste des descriptions des tools (limitée à
            max_tools pour limiter les tokens).
        llm_callable: Fonction qui prend un prompt str et retourne la
            réponse str du LLM. Injectée pour testabilité.
        max_tools: Max tools listés dans le prompt.

    Returns:
        Catégorie sémantique si LLM retourne une valeur valide, sinon None.
    """
    if not callable(llm_callable):
        return None
    if not isinstance(server_name, str) or not server_name:
        return None

    safe_tools = []
    for desc in (tool_descriptions or [])[:max_tools]:
        if isinstance(desc, str) and desc:
            safe_tools.append(f"- {desc[:200]}")
    if not safe_tools:
        return None

    categories_str = ", ".join(sorted(VALID_CATEGORIES))
    prompt = _LLM_PROMPT_TEMPLATE.format(
        categories=categories_str,
        server_name=server_name[:128],
        tools_summary="\n".join(safe_tools),
    )

    try:
        response = llm_callable(prompt)
    except Exception:
        return None

    if not isinstance(response, str):
        return None

    # Parser : on cherche la première catégorie valide dans la réponse
    response_clean = response.strip().lower()
    # Retirer ponctuation et guillemets potentiels
    response_clean = re.sub(r"[\"'.,!?;:()\[\]{}]", "", response_clean)

    # Match exact d'abord
    if response_clean in VALID_CATEGORIES:
        return response_clean

    # Sinon chercher une catégorie valide dans les mots de la réponse
    for word in response_clean.split():
        if word in VALID_CATEGORIES:
            return word

    return None


# ══════════════════════════════════════════════════════════════════════════════
# Cascade complète : cache > static > heuristic > LLM > fallback
# ══════════════════════════════════════════════════════════════════════════════


def infer_semantic_category(
    server_name: str,
    tool_descriptions: Optional[List[str]] = None,
    llm_callable: Optional[Callable[[str], str]] = None,
    cached: Optional[str] = None,
) -> Tuple[str, str]:
    """Cascade complète à 5 niveaux pour classifier un MCP.

    Ordre de priorité :
      1. Cache (cached fourni et valide) → ("X", "cache")
      2. Static table sur server_name → ("X", "static")
      3. Heuristique sur tool descriptions → ("X", "heuristic")
      4. LLM call si llm_callable fourni → ("X", "llm")
      5. Fallback safe sur "mcp" → ("mcp", "fallback")

    Args:
        server_name: Nom du serveur MCP.
        tool_descriptions: Descriptions des tools exposés (optionnel).
        llm_callable: Fonction LLM injectée (optionnelle).
        cached: Catégorie déjà décidée précédemment (cache hit).

    Returns:
        Tuple (category, decision_source).
        decision_source ∈ {"cache", "static", "heuristic", "llm", "fallback"}.
    """
    # Niveau 0 — Cache hit
    if cached is not None and _is_valid_category(cached):
        return cached, "cache"

    # Niveau 1 — Static
    static_match = infer_static(server_name)
    if static_match is not None:
        return static_match, "static"

    # Niveau 2 — Heuristic
    heuristic_match = infer_heuristic(tool_descriptions or [])
    if heuristic_match is not None:
        return heuristic_match, "heuristic"

    # Niveau 3 — LLM
    if llm_callable is not None:
        llm_match = infer_with_llm(
            server_name, tool_descriptions or [], llm_callable,
        )
        if llm_match is not None and llm_match != "mcp":
            return llm_match, "llm"

    # Niveau 4 — Fallback safe
    return "mcp", "fallback"


# ══════════════════════════════════════════════════════════════════════════════
# Traduction langage humain → catégorie technique (niveau 4 chat)
# ══════════════════════════════════════════════════════════════════════════════


def translate_human_to_category(human_phrase: str) -> Optional[str]:
    """Convertit une phrase humaine de l'utilisateur en catégorie technique.

    L'utilisateur dit "messagerie" ou "boulot", Lumena retrouve "mail" ou
    "project". Permet aux outils LLM Phase F de mapper sans jargon.

    Args:
        human_phrase: Mot ou phrase humaine de l'utilisateur.

    Returns:
        Catégorie technique si match dans HUMAN_TO_CATEGORY, sinon None.
    """
    if not isinstance(human_phrase, str) or not human_phrase:
        return None
    phrase_norm = _normalize_for_match(human_phrase).strip()
    if not phrase_norm:
        return None

    # Match exact d'abord
    if phrase_norm in _HUMAN_TO_CATEGORY:
        category = _HUMAN_TO_CATEGORY[phrase_norm]
        if _is_valid_category(category):
            return category

    # Match par mot dans la phrase (longueur décroissante pour spécificité)
    sorted_humans = sorted(_HUMAN_TO_CATEGORY.keys(), key=len, reverse=True)
    for human_kw in sorted_humans:
        if human_kw in phrase_norm:
            category = _HUMAN_TO_CATEGORY[human_kw]
            if _is_valid_category(category):
                return category

    return None


def category_to_human_label(category: str) -> str:
    """Convertit une catégorie technique en label humain pour le chat.

    L'inverse de translate_human_to_category : Lumena peut parler à
    l'utilisateur en disant "messages et emails" au lieu de "mail".

    Args:
        category: Catégorie technique.

    Returns:
        Label humain si dans HUMAN_FRIENDLY_LABELS, sinon le category brut.
    """
    if not isinstance(category, str):
        return ""
    return HUMAN_FRIENDLY_LABELS.get(category, category)
