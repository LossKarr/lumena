"""
Classification des extensions de fichiers par catégorie de traitement.

Détermine si une mutation sur un fichier donné doit être traitée par :
- CodeAgent  : code source ou config build (peut casser le projet)
- ReAct      : documentation, binaires, assets (édition directe OK)

Règle centrale : `requires_codeagent(path) -> bool`
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

# ─── Extensions ──────────────────────────────────────────────────────

CODE_EXTENSIONS: frozenset[str] = frozenset({
    # Python
    ".py", ".pyi", ".pyx",
    # JavaScript / TypeScript
    ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
    # Web front
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".vue", ".svelte", ".astro",
    # JVM
    ".java", ".kt", ".kts", ".scala", ".groovy",
    # Systems
    ".rs", ".go", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".cs", ".fs", ".vb",
    # Scripting
    ".rb", ".php", ".pl", ".pm", ".lua", ".r",
    # Mobile
    ".swift", ".m", ".mm", ".dart",
    # Shell
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
    # Data/query languages
    ".sql", ".graphql", ".gql", ".proto",
})

CONFIG_EXTENSIONS: frozenset[str] = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".lock",
    ".xml",           # pom.xml, web.xml, etc. (build/config)
})

# Noms de fichiers à traiter comme config (sans extension reconnaissable)
CONFIG_FILENAMES: frozenset[str] = frozenset({
    "Dockerfile", "Containerfile",
    "Makefile", "GNUmakefile", "makefile",
    "Rakefile", "Gemfile", "Procfile",
    "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json",
    "pyproject.toml", "poetry.lock", "setup.py", "setup.cfg",
    "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum",
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "tsconfig.json", "jsconfig.json",
    "webpack.config.js", "vite.config.js", "vite.config.ts",
    "rollup.config.js", "esbuild.config.js",
    "next.config.js", "nuxt.config.js", "svelte.config.js",
    "tailwind.config.js", "postcss.config.js",
    ".gitignore", ".gitattributes", ".gitmodules",
    ".dockerignore", ".npmignore",
    "docker-compose.yml", "docker-compose.yaml",
    ".env", ".env.local", ".env.production", ".env.development",
    "requirements.txt", "requirements-dev.txt",
})

DOC_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc",
    ".tex", ".rtf",
})

BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".odt", ".ods", ".odp",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff",
    ".mp3", ".wav", ".ogg", ".flac",
    ".mp4", ".mov", ".avi", ".webm", ".mkv",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
})

ASSET_EXTENSIONS: frozenset[str] = frozenset({
    ".svg", ".ico", ".icns",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
})


# ─── API ─────────────────────────────────────────────────────────────

def requires_codeagent(path: str | Path) -> bool:
    """True si une mutation sur ce path doit passer par le CodeAgent.

    Logique :
    - nom de fichier dans `CONFIG_FILENAMES` → True (ex: Dockerfile, Makefile)
    - extension dans `CODE_EXTENSIONS ∪ CONFIG_EXTENSIONS` → True
    - autre (doc, binaire, asset, sans extension) → False

    Args:
        path: chemin du fichier (string ou Path).

    Returns:
        True = réservé CodeAgent ; False = ReAct peut éditer directement.
    """
    p = Path(path) if not isinstance(path, Path) else path
    # 1. Nom de fichier spécial (Dockerfile, Makefile, package.json…)
    if p.name in CONFIG_FILENAMES:
        return True
    # 2. Extension code ou config
    ext = p.suffix.lower()
    if ext in CODE_EXTENSIONS:
        return True
    if ext in CONFIG_EXTENSIONS:
        return True
    # 3. Autre (doc, binaire, asset, extension inconnue) → ReAct
    return False


# ─── Détection de tâche "création de document" (langage naturel) ─────
#
# Sert à empêcher qu'une tâche de création de document (PDF, DOCX, rapport…)
# soit déléguée au CodeAgent, qui est réservé au développement.
# La création de documents passe par les outils directs ReAct
# (create_pdf / create_docx / write_file).

# Signaux indiquant une création/édition de document bureautique ou texte.
_DOCUMENT_SIGNALS: frozenset[str] = frozenset({
    "pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp",
    "document", "rapport", "lettre", "facture", "devis", "contrat",
    "présentation", "presentation", "diaporama", "tableur", "feuille de calcul",
    "compte rendu", "compte-rendu", "courrier", "attestation", "fiche",
    "en pdf", "en word", "en excel", "en powerpoint",
})

# Signaux indiquant une vraie tâche de développement (annule la détection doc).
_CODE_SIGNALS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java", ".go",
    ".rs", ".rb", ".php", ".c", ".cpp", ".sql", ".sh",
    "script", "fonction", "function", "classe", "class", "méthode", "methode",
    "bug", "traceback", "erreur", "exception", "refactor", "refacto",
    "test", "tests", "api", "endpoint", "compile", "build", "déploie", "deploy",
    "site web", "page web", "composant", "component", "module", "package",
    "repo", "dépôt", "depot", "import", "librairie", "library", "framework",
    "backend", "frontend", "serveur", "server", "base de données", "database",
})


def looks_like_document_creation(description: str) -> bool:
    """True si la description ressemble à une création/édition de document
    bureautique ou texte (PDF/DOCX/rapport…) plutôt qu'à du développement.

    Heuristique conservatrice : exige au moins un signal document ET aucun
    signal code. Ainsi "crée un script Python qui génère un PDF" n'est PAS
    détecté comme tâche document (le signal code l'emporte).

    Args:
        description: la description de la tâche déléguée.

    Returns:
        True = tâche de document (à garder en ReAct, pas CodeAgent).
    """
    if not description:
        return False
    text = description.lower()
    has_code_signal = any(sig in text for sig in _CODE_SIGNALS)
    if has_code_signal:
        return False
    return any(sig in text for sig in _DOCUMENT_SIGNALS)


def categorize(path: str | Path) -> str:
    """Retourne la catégorie d'un fichier : 'code', 'config', 'doc', 'binary',
    'asset' ou 'unknown'."""
    p = Path(path) if not isinstance(path, Path) else path
    if p.name in CONFIG_FILENAMES:
        return "config"
    ext = p.suffix.lower()
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in CONFIG_EXTENSIONS:
        return "config"
    if ext in DOC_EXTENSIONS:
        return "doc"
    if ext in BINARY_EXTENSIONS:
        return "binary"
    if ext in ASSET_EXTENSIONS:
        return "asset"
    return "unknown"


def all_codeagent_extensions() -> Iterable[str]:
    """Toutes les extensions qui exigent le CodeAgent (utile pour tests/docs)."""
    return sorted(CODE_EXTENSIONS | CONFIG_EXTENSIONS)
