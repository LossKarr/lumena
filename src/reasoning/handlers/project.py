"""
project.py - Handler create_project pour la génération batch de projets.

Au lieu de 15+ itérations ReAct (plan + write_file x N), un seul appel :
  Phase 1: LLM génère l'arborescence des fichiers
  Phase 2: LLM génère chaque fichier en parallèle (appels ciblés, sans ReAct)
  Phase 3: Écriture batch sur disque

Réduit un projet 5 fichiers de ~15 itérations à ~2-3.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

from src.prompts.handlers.project_prompts import (
    _CONTRACT_PREAMBLE,
    _CONTRACT_PREAMBLE_JSON,
    _CONTRACT_JSON_SCHEMA_WEB,
    _CONTRACT_JSON_SCHEMA_PYTHON,
    _CONTRACT_JSON_SCHEMA_API,
    _CONTRACT_JSON_SCHEMA_NODE,
    _CONTRACT_JSON_SCHEMA_GAME,
    _CONTRACT_SPEC_WEB,
    _CONTRACT_SPEC_PYTHON,
    _CONTRACT_SPEC_NODE,
    _CONTRACT_SPEC_GAME,
    _CONTRACT_SPEC_GENERIC,
    _WEB_PLAN_SUPPLEMENT,
    _PYTHON_PLAN_SUPPLEMENT,
    _API_PLAN_SUPPLEMENT,
    _GAME_PLAN_SUPPLEMENT,
    _FIX_SYSTEM_PROMPT,
    _DEPS_UPGRADE_PROMPT,
    _PLAN_COMMON,
    _PLAN_SECTION_WEB,
    _PLAN_SECTION_PYTHON,
    _PLAN_SECTION_API,
    _PLAN_SECTION_GAME,
    _PLAN_SECTION_DESKTOP,
    _PLAN_SECTION_DOCKER,
    _FILE_SYSTEM_PROMPT,
    _PYTHON_DIRECTIVES,
    _API_DIRECTIVES,
    _GAME_DIRECTIVES,
    _NODE_DIRECTIVES,
    _DESKTOP_DIRECTIVES,
    _DATA_DIRECTIVES,
    _TEST_FIX_SYSTEM_PROMPT,
    _LINT_FIX_SYSTEM_PROMPT,
)

try:
    from ...tools.website_builder import (
        build_design_directives as _build_design_directives,
        WEBSITE_GENERATE_PROMPT as _WEBSITE_GENERATE_PROMPT,
    )
    _WEBSITE_BUILDER_AVAILABLE = True
except ImportError:
    _WEBSITE_BUILDER_AVAILABLE = False
    _WEBSITE_GENERATE_PROMPT = ""
    def _build_design_directives(desc: str) -> str:  # type: ignore[misc]
        return ""

try:
    from ...tools.code_validator import validate_project as _validate_project, Severity
    _VALIDATOR_AVAILABLE = True
except ImportError:
    _VALIDATOR_AVAILABLE = False

try:
    from ...agents.sub_agent import delegate_to_agent as _delegate_to_agent
    _CODEAGENT_AVAILABLE = True
except ImportError:
    _CODEAGENT_AVAILABLE = False


# ─── Détection projet web ──────────────────────────────────────────────────

_WEB_KEYWORDS = frozenset([
    "site", "website", "landing", "page", "portfolio", "vitrine", "blog",
    "e-commerce", "ecommerce", "boutique", "saas", "dashboard", "webapp",
    "app web", "web app", "restaurant", "agence", "cabinet", "startup",
    "plateforme", "marketplace", "homepage", "front-end", "frontend",
])

def _is_web_project(description: str) -> bool:
    """Détecte si la description correspond à un projet web/site."""
    desc_lower = description.lower()
    return any(kw in desc_lower for kw in _WEB_KEYWORDS)


# ─── Constantes ────────────────────────────────────────────────────────────

_MAX_FILES = 200  # Sécurité technique — Lumena décide du nombre réel
_MAX_PARALLEL = 8  # Concurrence max pour les appels LLM
_MAX_CONTENT_LEN = 100_000  # Max caractères par fichier généré (post-génération)
_PLAN_MAX_TOKENS = 2000
_CONTRACT_MAX_TOKENS = 1500  # Phase 1.5 — contrat partagé entre fichiers
# Pas de limite de tokens par fichier : le modèle utilise sa capacité maximale.
# _MAX_CONTENT_LEN tronque seulement en mémoire/disque après génération.

# ─── P7: Modèles capables de produire du JSON structuré fiable ──────────
_JSON_CONTRACT_CAPABLE_MODELS = frozenset([
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5-mini",
    "deepseek-v3", "deepseek-chat",
    "claude-3.5", "claude-3-opus", "claude-4", "claude-sonnet-4",
    "o3", "o4-mini",
    "qwen3-235b-a22b",
])

# ─── Détection de troncature post-extraction ──────────────────────────────────

_BRACE_LANGS = frozenset([
    ".css", ".js", ".ts", ".tsx", ".jsx", ".json", ".jsonc",
    ".java", ".go", ".rs", ".cpp", ".c", ".cs", ".swift", ".kt",
    ".scss", ".less", ".sass", ".vue", ".svelte", ".php",
])
_PAREN_LANGS = frozenset([
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".cs", ".swift", ".kt", ".rb", ".lua", ".php",
])

def _looks_truncated(content: str, ext: str) -> bool:
    """Retourne True si le contenu semble tronqué (incomplet).

    Heuristiques multi-langage :
    - HTML : pas de </html> de fermeture
    - Langages à accolades (CSS/JS/Java/Go/Rust/…) : { > }
    - Langages à parenthèses : ( > )
    - Lignes coupées en milieu de token (finit par = ou : ou , sans retour)
    """
    if not content or len(content) < 50:
        return True

    stripped = content.rstrip()
    ext = ext.lower()

    # HTML / XML : doit finir par la balise racine
    if ext in (".html", ".htm", ".xml", ".xhtml", ".svg"):
        low = stripped.lower()
        if "<html" in low and "</html>" not in low:
            return True
        if "<svg" in low and "</svg>" not in low:
            return True
        # <script> ouvert sans </script>
        if low.count("<script") > low.count("</script>"):
            return True

    # Langages à accolades : { doit == }
    if ext in _BRACE_LANGS:
        if content.count("{") > content.count("}"):
            return True

    # CSS : @media/@keyframes ouvert sans fermeture
    if ext in (".css", ".scss", ".less"):
        # Compter les @-rules qui ouvrent un bloc
        at_opens = len(re.findall(r'@(?:media|keyframes|supports|font-face)\b', content))
        # Si plus de @-rules que de blocs fermés proportionnellement
        if at_opens > 0 and content.count("{") > content.count("}"):
            return True

    # Langages à parenthèses : ( doit == )
    if ext in _PAREN_LANGS:
        if content.count("(") - content.count(")") > 2:  # tolérance 2
            return True

    # Python : def/class ouvert sans corps (dernière ligne = def/class)
    if ext == ".py":
        last_lines = stripped.split("\n")[-3:]
        last_meaningful = ""
        for ln in reversed(last_lines):
            if ln.strip():
                last_meaningful = ln.strip()
                break
        if last_meaningful.endswith(":") and (last_meaningful.startswith("def ") or last_meaningful.startswith("class ")):
            return True

    # Dernier caractère suspect : coupé en milieu d'instruction
    if stripped and stripped[-1] in ("=", ",", "(", "{", "[", "\\"):
        return True

    # JS/TS : commentaire non fermé en fin de fichier
    if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
        if "/*" in content and content.rfind("/*") > content.rfind("*/"):
            return True

    return False


def _quick_syntax_check(content: str, ext: str) -> Optional[str]:
    """Validation syntaxique rapide avant écriture.

    Retourne None si OK, un message d'erreur sinon.
    Conçu pour attraper les erreurs grossières AVANT écriture sur disque.
    Ne valide que les extensions connues — retourne None pour les inconnues.
    """
    ext = ext.lower()
    _CHECKABLE = {".py", ".json", ".jsonc", ".html", ".htm", ".js", ".ts", ".jsx", ".tsx",
                  ".mjs", ".cjs", ".css", ".scss", ".less", ".yaml", ".yml"}
    if ext not in _CHECKABLE:
        return None  # Extension non vérifiable → OK par défaut
    if not content or len(content) < 10:
        return "contenu vide ou trop court"

    # Python — ast.parse
    if ext == ".py":
        try:
            ast.parse(content)
        except SyntaxError as e:
            return f"Python SyntaxError: {e.msg} (ligne {e.lineno})"
        return None

    # JSON — json.loads
    if ext in (".json", ".jsonc"):
        stripped = content.strip()
        # Retirer les commentaires // pour .jsonc
        if ext == ".jsonc":
            stripped = re.sub(r'//.*$', '', stripped, flags=re.MULTILINE)
        try:
            json.loads(stripped)
        except json.JSONDecodeError as e:
            return f"JSON invalide: {e.msg} (position {e.pos})"
        return None

    # HTML — balises ouvertes non fermées
    if ext in (".html", ".htm"):
        low = content.lower()
        if "<html" in low and "</html>" not in low:
            return "balise <html> non fermée (</html> manquant)"
        if "<body" in low and "</body>" not in low:
            return "balise <body> non fermée (</body> manquant)"
        # <script> sans </script>
        script_opens = low.count("<script")
        script_closes = low.count("</script>")
        if script_opens > script_closes:
            return f"{script_opens - script_closes} balise(s) <script> non fermée(s)"
        return None

    # JS/TS — accolades et parenthèses équilibrées
    if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
        braces = content.count("{") - content.count("}")
        if braces > 1:
            return f"accolades déséquilibrées ({{:{content.count('{')}, }}:{content.count('}')})"
        parens = content.count("(") - content.count(")")
        if parens > 2:
            return f"parenthèses déséquilibrées ((:{content.count('(')}, ):{content.count(')')})"
        return None

    # CSS/SCSS — accolades équilibrées
    if ext in (".css", ".scss", ".less"):
        braces = content.count("{") - content.count("}")
        if braces > 0:
            return f"CSS accolades déséquilibrées ({{:{content.count('{')}, }}:{content.count('}')})"
        return None

    # YAML — tabs mélangés avec spaces (erreur fréquente)
    if ext in (".yaml", ".yml"):
        lines_with_tabs = [i for i, line in enumerate(content.split("\n"), 1) if line.startswith("\t")]
        lines_with_spaces = [i for i, line in enumerate(content.split("\n"), 1) if line.startswith("  ")]
        if lines_with_tabs and lines_with_spaces:
            return f"YAML mélange tabs (ligne {lines_with_tabs[0]}) et espaces (ligne {lines_with_spaces[0]})"
        return None

    return None  # Extension non vérifiable → OK par défaut


# Extensions binaires que le LLM ne peut pas générer — skip automatique
_BINARY_EXTENSIONS = frozenset([
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".avi", ".mov",
    ".pdf", ".zip", ".tar", ".gz", ".rar",
    ".exe", ".dll", ".so", ".dylib",
])

# ─── Prompts contrat par domaine ───────────────────────────────────────────


_CONTRACT_JSON_SCHEMA_MAP = {
    "web": _CONTRACT_JSON_SCHEMA_WEB,
    "python_api": _CONTRACT_JSON_SCHEMA_API,
    "python_cli": _CONTRACT_JSON_SCHEMA_PYTHON,
    "python_package": _CONTRACT_JSON_SCHEMA_PYTHON,
    "node_express": _CONTRACT_JSON_SCHEMA_NODE,
    "game": _CONTRACT_JSON_SCHEMA_GAME,
    "desktop": _CONTRACT_JSON_SCHEMA_PYTHON,
    "data_science": _CONTRACT_JSON_SCHEMA_PYTHON,
}


_GAME_KEYWORDS = frozenset([
    "jeu", "game", "3d", "unity", "pygame", "godot", "phaser", "arcade",
    "rpg", "fps", "simulation", "simulateur", "moteur", "engine",
])

_API_KEYWORDS = frozenset([
    "api", "rest", "endpoint", "fastapi", "flask", "express", "serveur",
    "server", "backend", "graphql", "microservice",
])

_DESKTOP_KEYWORDS = frozenset([
    "desktop", "gui", "interface graphique", "tkinter", "electron",
    "pyqt", "wxpython", "kivy", "fenêtre",
])

_DATA_KEYWORDS = frozenset([
    "data", "analyse", "csv", "pandas", "numpy", "matplotlib",
    "jupyter", "notebook", "dataset", "scraping", "visualisation",
])


def _detect_project_type(description: str, plan_files: List[str]) -> str:
    """Détecte le type de projet à partir de la description et des fichiers planifiés.

    Retourne l'un de: 'web', 'python_api', 'python_cli', 'python_package',
    'node_express', 'game', 'desktop', 'data_science', 'docker', 'generic'.
    """
    desc_lower = description.lower()
    exts = {("." + f.rsplit(".", 1)[-1].lower()) if "." in f else "" for f in plan_files}
    filenames = {f.rsplit("/", 1)[-1].lower() if "/" in f else f.lower() for f in plan_files}

    has_py = ".py" in exts or any(f.endswith(".py") for f in plan_files)
    has_js = ".js" in exts or ".ts" in exts

    is_web = _is_web_project(description)
    is_api = any(kw in desc_lower for kw in _API_KEYWORDS)
    is_game = any(kw in desc_lower for kw in _GAME_KEYWORDS)
    is_desktop = any(kw in desc_lower for kw in _DESKTOP_KEYWORDS)

    # ── Types spécifiques d'abord (game, desktop) ──
    # Plus spécifiques que "web" — un jeu Phaser HTML5 est un game, pas un web
    if is_game:
        return "game"
    if is_desktop:
        return "desktop"

    # ── Résolution web vs API ──
    # Les descriptions enrichies (WEBSITE_GENERATE_PROMPT) peuvent contenir
    # des mots parasites ("api", "server"). On préfère web SAUF si des
    # indices de backend explicites sont présents (flask, fastapi, express, backend, etc.)
    _BACKEND_EXPLICIT = {"flask", "fastapi", "express", "django", "backend",
                         "graphql", "microservice", "koa", "hapi", "nestjs"}
    has_backend_hint = any(kw in desc_lower for kw in _BACKEND_EXPLICIT)

    if is_web and not has_backend_hint:
        return "web"

    # API détection (Python ou Node)
    if is_api and has_py:
        return "python_api"
    if is_api and has_js:
        return "node_express"
    # Django détection
    if "manage.py" in filenames:
        return "python_api"

    # CLI explicite (avant data_science car "parser csv" ≠ data science)
    _cli_kw = {"cli", "command line", "ligne de commande", "terminal", "argparse", "click"}
    if any(kw in desc_lower for kw in _cli_kw) and has_py:
        return "python_cli"

    # Data science
    if any(kw in desc_lower for kw in _DATA_KEYWORDS):
        return "data_science"

    # Docker
    if "dockerfile" in filenames or "docker-compose.yml" in filenames:
        return "docker"

    # Python package
    if "pyproject.toml" in filenames or "setup.py" in filenames:
        return "python_package"

    # Fichier-based fallback
    if has_py:
        return "python_cli"
    if has_js:
        return "node_express"

    return "generic"


# ─── Plan supplements — contexte architecturel injecté en Phase 1 ────────


_PLAN_SUPPLEMENT_MAP = {
    "web": _WEB_PLAN_SUPPLEMENT,
    "python_api": _API_PLAN_SUPPLEMENT,
    "python_cli": _PYTHON_PLAN_SUPPLEMENT,
    "python_package": _PYTHON_PLAN_SUPPLEMENT,
    "node_express": _API_PLAN_SUPPLEMENT,
    "game": _GAME_PLAN_SUPPLEMENT,
    "desktop": _PYTHON_PLAN_SUPPLEMENT,
    "data_science": _PYTHON_PLAN_SUPPLEMENT,
}


def _is_json_contract_capable(model_name: str) -> bool:
    """Vérifie si le modèle supporte le contrat JSON structuré (prefix match)."""
    if not model_name:
        return False
    mn = model_name.lower()
    return any(mn.startswith(prefix) for prefix in _JSON_CONTRACT_CAPABLE_MODELS)


def _format_json_contract(contract_data: dict) -> str:
    """Formate un contrat JSON parsé en texte lisible pour injection dans les prompts."""
    parts = []
    for key, val in contract_data.items():
        if isinstance(val, dict):
            items = ", ".join(f"{k}: {v}" for k, v in val.items())
            parts.append(f"- {key}: {items}")
        elif isinstance(val, list):
            if val and isinstance(val[0], dict):
                for item in val:
                    parts.append(f"- {key}: {item}")
            else:
                parts.append(f"- {key}: {', '.join(str(v) for v in val)}")
        else:
            parts.append(f"- {key}: {val}")
    return "\n".join(parts)


def _get_contract_prompt(description: str, files_plan: List[Dict[str, str]], model_name: str = "") -> str:
    """Retourne le prompt système contrat adapté au domaine détecté du projet.
    
    Si le modèle est JSON-capable, retourne un prompt demandant du JSON structuré.
    Sinon, retourne le prompt texte libre classique.
    """
    paths = [f.get("path", "") for f in files_plan]
    ptype = _detect_project_type(description, paths)

    if _is_json_contract_capable(model_name):
        schema = _CONTRACT_JSON_SCHEMA_MAP.get(ptype, "")
        if schema:
            return _CONTRACT_PREAMBLE_JSON + "\n" + schema

    _CONTRACT_MAP = {
        "web": _CONTRACT_SPEC_WEB,
        "python_api": _CONTRACT_SPEC_PYTHON,
        "python_cli": _CONTRACT_SPEC_PYTHON,
        "python_package": _CONTRACT_SPEC_PYTHON,
        "node_express": _CONTRACT_SPEC_NODE,
        "game": _CONTRACT_SPEC_GAME,
        "desktop": _CONTRACT_SPEC_PYTHON,
        "data_science": _CONTRACT_SPEC_PYTHON,
        "docker": _CONTRACT_SPEC_GENERIC,
    }
    spec = _CONTRACT_MAP.get(ptype, _CONTRACT_SPEC_GENERIC)
    return _CONTRACT_PREAMBLE + "\n" + spec

# Phase 4 : boucle run → fix
_RUN_FIX_MAX_ITER = 5        # max tentatives de correction automatique
_RUN_FIX_TIMEOUT = 6         # secondes avant de considérer le programme "en cours" (serveur/jeu)
_RUN_FIX_MAX_OUTPUT = 3000   # chars d'output envoyés au LLM pour le fix


_PLAN_SECTION_MAP = {
    "web": _PLAN_SECTION_WEB,
    "python_cli": _PLAN_SECTION_PYTHON,
    "python_package": _PLAN_SECTION_PYTHON,
    "python_api": _PLAN_SECTION_API,
    "node_express": _PLAN_SECTION_API,
    "game": _PLAN_SECTION_GAME,
    "desktop": _PLAN_SECTION_DESKTOP,
    "data_science": _PLAN_SECTION_PYTHON,
    "docker": _PLAN_SECTION_DOCKER,
}


def _build_plan_prompt(project_type: str, max_files: int) -> str:
    """Construit le prompt de planification adapté au type de projet.
    
    Injecte UNIQUEMENT la section pertinente (pas de bruit cross-type).
    """
    prompt = _PLAN_COMMON.format(max_files=max_files)
    section = _PLAN_SECTION_MAP.get(project_type, "")
    if section:
        prompt += section
    return prompt


# ─── Utilitaires ───────────────────────────────────────────────────────────

# Priorité de génération par extension (plus bas = généré en premier)
_DEP_ORDER = {
    ".env": 0, ".toml": 0, ".ini": 0, ".yaml": 1, ".yml": 1,
    ".json": 2, ".jsonc": 2,
    ".css": 10, ".scss": 10, ".less": 10,
    ".py": 20, ".ts": 20, ".js": 25,
    ".jsx": 26, ".tsx": 26, ".vue": 26, ".svelte": 26,
    ".html": 30, ".htm": 30,
    ".md": 40, ".txt": 40,
}
_MAX_DEP_CONTEXT_CHARS = 8000  # Budget max pour l'injection contexte dépendances

# Wave DAG — groupes de dépendances (0=séquentiel premier, 1=séquentiel second, 2=parallèle)
_WAVE_0_EXTS = frozenset([".env", ".toml", ".ini", ".yaml", ".yml", ".json", ".jsonc", ".lock"])
_WAVE_1_EXTS = frozenset([".css", ".scss", ".less", ".sql", ".prisma", ".graphql"])
# Wave 2 = tout le reste (py, ts, js, jsx, tsx, html, md, txt…)


def _dep_sort_key(file_entry: Dict[str, str]) -> int:
    """Clé de tri pour générer les fichiers dans l'ordre de dépendance."""
    p = file_entry.get("path", "")
    ext = "." + p.rsplit(".", 1)[-1].lower() if "." in p else ""
    return _DEP_ORDER.get(ext, 35)


def _dep_wave(file_entry: Dict[str, str]) -> int:
    """Retourne le numéro de vague DAG (0, 1 ou 2) selon l'extension du fichier."""
    p = file_entry.get("path", "")
    ext = "." + p.rsplit(".", 1)[-1].lower() if "." in p else ""
    if ext in _WAVE_0_EXTS:
        return 0
    if ext in _WAVE_1_EXTS:
        return 1
    return 2


def _build_dependency_context(
    current_path: str,
    generated_contents: Dict[str, str],
    all_paths: List[str],
) -> str:

    """Construit le contexte des fichiers déjà générés dont current_path peut dépendre."""
    if not generated_contents:
        return ""
    current_ext = "." + current_path.rsplit(".", 1)[-1].lower() if "." in current_path else ""
    current_dir = current_path.rsplit("/", 1)[0] if "/" in current_path else ""

    # Sélection intelligente des fichiers de contexte
    candidates = []
    for gp, gc in generated_contents.items():
        if gp == current_path:
            continue
        score = 0
        gp_ext = "." + gp.rsplit(".", 1)[-1].lower() if "." in gp else ""
        gp_dir = gp.rsplit("/", 1)[0] if "/" in gp else ""

        # Même répertoire → haute priorité
        if gp_dir == current_dir:
            score += 30
        # HTML dépend de CSS/JS
        if current_ext in (".html", ".htm") and gp_ext in (".css", ".js", ".ts"):
            score += 20
        # JS/TS dépend de HTML (besoin du DOM: IDs, classes, structure)
        if current_ext in (".js", ".ts", ".jsx", ".tsx") and gp_ext in (".html", ".htm"):
            score += 25
        # JS dépend de CSS (variables, classes)
        if current_ext in (".js", ".ts", ".jsx", ".tsx") and gp_ext in (".css", ".scss"):
            score += 20
        # Config files sont utiles pour tous
        if gp_ext in (".json", ".env", ".toml", ".yaml", ".yml"):
            score += 10
        # Même langage → utile pour cohérence
        if gp_ext == current_ext:
            score += 5

        if score > 0:
            candidates.append((score, gp, gc))

    if not candidates:
        return ""

    candidates.sort(key=lambda x: -x[0])
    parts = ["═══ FICHIERS DÉJÀ GÉNÉRÉS (référence pour cohérence) ═══"]
    budget = _MAX_DEP_CONTEXT_CHARS
    for _score, path, content in candidates:
        snippet = content[:3000] if len(content) > 3000 else content
        entry = f"\n── {path} ──\n{snippet}"
        if len(entry) > budget:
            break
        parts.append(entry)
        budget -= len(entry)
    parts.append("═══ FIN DES FICHIERS DE RÉFÉRENCE ═══")
    return "\n".join(parts)


def _extract_json(text: str) -> Optional[dict]:
    """Extrait un objet JSON d'une réponse LLM (gère les blocs ```json)."""
    # Tenter un parse direct
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass  # try code block extraction
    # Chercher un bloc ```json ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass  # malformed JSON in code block

    # Chercher le premier { ... } valide
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _sanitize_path(raw: str) -> Optional[str]:
    """Valide et nettoie un chemin de fichier relatif."""
    raw = raw.strip().replace("\\", "/")
    # Bloquer les traversées de répertoire
    if ".." in raw or raw.startswith("/") or ":" in raw:
        return None
    # Bloquer les chemins trop profonds
    if len(raw.split("/")) > 10:
        return None
    # Bloquer les fichiers cachés système
    if any(part.startswith(".") and part not in (".gitignore", ".env.example", ".eslintrc.json", ".prettierrc") for part in raw.split("/")):
        return None
    return raw


def _strip_code_fences(text: str) -> str:
    """Retire les ```lang ... ``` autour du code si présents."""
    text = text.strip()
    m = re.match(r"^```\w*\n?(.*?)```$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _try_extract_from_truncated_manifest(content: str, file_path: str) -> Optional[str]:
    """
    Tente d'extraire le contenu d'un fichier depuis un JSON manifest TRONQUÉ
    (json.loads() a échoué mais le contenu ressemble à un manifest).
    Utilise une extraction regex sur la chaîne JSON brute.
    """
    fname = file_path.split("/")[-1] if "/" in file_path else file_path

    # Chercher le pattern "files": { ... "chemin": "CONTENU..." dans le JSON brut
    # On essaie d'abord le chemin exact, puis le nom de fichier seul
    for search_key in (file_path, fname):
        escaped_key = re.escape(search_key)
        # Match : "chemin/fichier.ext": "CONTENU_ECHAPPÉ..."
        pattern = rf'"{escaped_key}"\s*:\s*"((?:[^"\\]|\\.)*)'
        m = re.search(pattern, content)
        if m:
            raw_value = m.group(1)
            if len(raw_value) > 50:
                try:
                    # Tenter de décoder les séquences d'échappement JSON (\n, \t, \", etc.)
                    extracted = json.loads(f'"{raw_value}"')
                except (json.JSONDecodeError, ValueError):
                    # Fallback : décodage basique des échappements courants
                    extracted = (
                        raw_value
                        .replace("\\n", "\n")
                        .replace("\\t", "\t")
                        .replace('\\"', '"')
                        .replace("\\\\", "\\")
                    )
                if len(extracted.strip()) > 20:
                    logger.warning(
                        "[manifest-guard] JSON tronqué → extraction regex réussie pour '{}' ({} chars)",
                        file_path, len(extracted),
                    )
                    return extracted.strip()

    # Fallback : chercher le premier gros bloc de valeur string dans "files"
    files_start = content.find('"files"')
    if files_start >= 0:
        # Trouver toutes les valeurs string après "files"
        for m in re.finditer(r':\s*"((?:[^"\\]|\\.){100,})', content[files_start:]):
            raw_value = m.group(1)
            try:
                extracted = json.loads(f'"{raw_value}"')
            except (json.JSONDecodeError, ValueError):
                extracted = (
                    raw_value
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
            if len(extracted.strip()) > 50:
                logger.warning(
                    "[manifest-guard] JSON tronqué → extraction fallback pour '{}' ({} chars, potentiellement incomplet)",
                    file_path, len(extracted),
                )
                return extracted.strip()

    return None


def _try_extract_from_manifest(content: str, file_path: str) -> Optional[str]:
    """
    Détecte si le LLM a retourné un JSON manifest au lieu du contenu d'un fichier.
    Retourne :
      - Le contenu extrait (str non vide) si on a pu récupérer le vrai fichier.
      - "" si c'est un manifest mais sans contenu récupérable (→ retry).
      - None si ce n'est pas un manifest (→ le contenu est légitime).
    """
    stripped = content.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # JSON invalide : peut être un manifest TRONQUÉ par la limite de tokens.
        # Détecter les signatures de manifest dans le texte brut avant d'abandonner.
        _is_likely_manifest = (
            '"files"' in stripped[:500]
            or '"project_name"' in stripped[:500]
        )
        if _is_likely_manifest:
            logger.warning(
                "[manifest-guard] JSON tronqué détecté pour '{}' — tentative d'extraction regex",
                file_path,
            )
            extracted = _try_extract_from_truncated_manifest(stripped, file_path)
            if extracted:
                return extracted
            # Manifest tronqué mais extraction impossible → forcer retry
            return ""
        return None  # Pas un manifest → contenu brut légitime

    if not isinstance(data, dict):
        return None

    # Cas 1 : manifest de plan {"project_name": ..., "files": {path: content} ou [{"path":...}]}
    if "files" in data:
        files = data["files"]
        fname = file_path.split("/")[-1] if "/" in file_path else file_path
        if isinstance(files, dict):
            # {"files": {"index.html": "<!DOCTYPE html>...", ...}}
            for key, val in files.items():
                if isinstance(val, str) and (
                    key == file_path or key == fname
                    or file_path.endswith(key) or key.endswith(fname)
                ):
                    if len(val.strip()) > 20:
                        return val.strip()
            # Pas trouvé par nom : prendre le plus long (heuristique)
            candidates = [(k, v) for k, v in files.items() if isinstance(v, str) and len(v) > 20]
            if candidates:
                best = max(candidates, key=lambda kv: len(kv[1]))
                logger.warning("[manifest-guard] Fichier '{}' non trouvé exactement — fallback vers '{}'", file_path, best[0])
                return best[1].strip()
        elif isinstance(files, list):
            # [{"path": "index.html", "content": "..."}, ...]
            for item in files:
                if isinstance(item, dict):
                    item_path = item.get("path", "")
                    item_content = item.get("content", item.get("code", ""))
                    if isinstance(item_content, str) and len(item_content.strip()) > 20:
                        if (item_path == file_path or item_path == fname
                                or file_path.endswith(item_path) or item_path.endswith(fname)):
                            return item_content.strip()
        return ""  # Manifest reconnu mais contenu non extractable

    # Cas 2 : {"content": "...", "code": "...", "html": "..."}
    for key in ("content", "code", "html", "css", "javascript", "source", "text"):
        val = data.get(key)
        if isinstance(val, str) and len(val.strip()) > 20:
            return val.strip()

    # Cas 3 : clés caractéristiques d'un manifest/plan
    if any(k in data for k in ("project_name", "summary", "explanation", "fixes", "description")):
        return ""

    return None  # JSON légitime (ex: fichier config.json)


async def _scan_project_port() -> Optional[int]:
    """
    Scanne les ports 8700-8750 en parallèle pour trouver un serveur actif.
    Retourne le premier port répondant, ou None.
    """
    async def _probe(port: int) -> Optional[int]:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=0.3
            )
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=0.3)
            except Exception:
                pass  # cleanup socket best-effort
            return port
        except Exception:
            return None

    results = await asyncio.gather(*[_probe(p) for p in range(8700, 8751)])
    return next((p for p in results if p is not None), None)


def _detect_run_command(project_dir: Path, written_files: List[str]) -> Optional[str]:
    """
    Détecte la commande de lancement appropriée pour le projet.
    Retourne None si le projet est statique (HTML pur) ou non reconnu.
    """
    filenames = {Path(f).name.lower() for f in written_files}

    # Docker — docker-compose / Dockerfile en priorité
    if "docker-compose.yml" in filenames or "docker-compose.yaml" in filenames:
        return "docker compose up --build"
    if "dockerfile" in filenames and "docker-compose.yml" not in filenames:
        return "docker build -t project . && docker run --rm project"

    # Rust/Cargo
    if "cargo.toml" in filenames:
        return "cargo run"

    # Go
    if "go.mod" in filenames:
        return "go run ."

    # Node.js — package.json
    if "package.json" in filenames:
        pkg_path = project_dir / "package.json"
        if pkg_path.exists():
            try:
                import json as _json
                data = _json.loads(pkg_path.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                if "start" in scripts:
                    return "npm install --silent 2>&1 && npm start"
                if "dev" in scripts:
                    return "npm install --silent 2>&1 && npm run dev"
                main_file = data.get("main", "index.js")
                return f"npm install --silent 2>&1 && node {main_file}"
            except Exception as e:
                logger.debug(f"Parse package.json main: {e}")
        return "npm install --silent 2>&1 && node index.js"

    # Django — manage.py
    if "manage.py" in filenames:
        prefix = "pip install -r requirements.txt -q 2>&1 && " if "requirements.txt" in filenames else ""
        return f"{prefix}python manage.py runserver 0.0.0.0:8700"

    # Poetry
    if "pyproject.toml" in filenames:
        toml_path = project_dir / "pyproject.toml"
        if toml_path.exists():
            try:
                content = toml_path.read_text(encoding="utf-8")
                if "poetry" in content.lower():
                    return "poetry install -q 2>&1 && poetry run python main.py"
            except Exception:
                pass

    # Python — chercher l'entry point
    for entry in ["main.py", "app.py", "run.py", "game.py", "server.py", "cli.py", "bot.py"]:
        if entry in filenames:
            if "requirements.txt" in filenames:
                return f"pip install -r requirements.txt -q 2>&1 && python {entry}"
            return f"python {entry}"

    # Fallback Python : premier .py trouvé
    py_files = [f for f in written_files if f.endswith(".py") and "/" not in f]
    if py_files:
        entry = py_files[0]
        if "requirements.txt" in filenames:
            return f"pip install -r requirements.txt -q 2>&1 && python {entry}"
        return f"python {entry}"

    # Makefile
    if "makefile" in filenames:
        return "make"

    # Notebooks — pas de run terminal
    if all(f.endswith(".ipynb") for f in written_files if "." in f):
        return None

    # Purely static HTML — pas de run terminal
    return None


def _detect_run_timeout(run_cmd: Optional[str]) -> int:
    """Retourne un timeout adapté au type de commande détectée."""
    if not run_cmd:
        return _RUN_FIX_TIMEOUT
    cmd_lower = run_cmd.lower()
    if "docker" in cmd_lower:
        return 120
    if "poetry install" in cmd_lower:
        return 60
    if "cargo run" in cmd_lower or "go run" in cmd_lower:
        return 30
    if "npm install" in cmd_lower:
        return 30
    if "pip install" in cmd_lower:
        return 20
    return _RUN_FIX_TIMEOUT


# ─── Directives type-spécifiques injectées dans le prompt de génération ──────


_TYPE_DIRECTIVES_MAP = {
    "python_api": _API_DIRECTIVES,
    "python_cli": _PYTHON_DIRECTIVES,
    "python_package": _PYTHON_DIRECTIVES,
    "node_express": _NODE_DIRECTIVES,
    "game": _GAME_DIRECTIVES,
    "desktop": _DESKTOP_DIRECTIVES,
    "data_science": _DATA_DIRECTIVES,
    "docker": "",
    "web": "",  # web has its own design directives already
    "generic": "",
}


def _get_type_directives(project_type: str, description: str) -> str:
    """Retourne les directives spécifiques au type de projet pour injection dans le prompt."""
    directives = _TYPE_DIRECTIVES_MAP.get(project_type, "")
    if not directives:
        return ""
    return "\n" + directives.strip() + "\n"


async def _run_project_cmd(
    cmd: str, cwd: Path, timeout: int = _RUN_FIX_TIMEOUT
) -> tuple:
    """
    Lance une commande dans cwd avec timeout court.
    Retourne (returncode, output_str).
    Timeout == succès pour les programmes interactifs (serveurs, jeux).
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            combined = (stdout + "\n" + stderr).strip()
            rc = proc.returncode if proc.returncode is not None else 0
            return rc, combined[:_RUN_FIX_MAX_OUTPUT]
        except asyncio.TimeoutError:
            # Serveur détecté — sonder les ports 8700-8750 pour vérifier qu'il répond
            await asyncio.sleep(1)  # laisser le temps au serveur de s'initialiser
            port = await _scan_project_port()
            try:
                proc.kill()
            except Exception:
                pass  # process déjà mort
            if port:
                return 0, (
                    f"[✅ Serveur actif sur http://localhost:{port} "
                    f"— timeout {timeout}s normal pour serveur/jeu]"
                )
            return 0, f"[Démarré avec succès — timeout {timeout}s (normal pour serveur/jeu)]"
    except Exception as exc:
        return -1, f"[Erreur lancement: {exc}]"


async def _run_and_fix_loop(
    llm: Any,
    project_dir: Path,
    cmd: str,
    written_files: List[str],
    max_iter: int = _RUN_FIX_MAX_ITER,
    run_timeout: int = _RUN_FIX_TIMEOUT,
) -> tuple:
    """
    Boucle autonome : run → analyse erreur → LLM corrige → run → …
    Retourne (success: bool, rapport: str).
    """
    report: List[str] = []

    for iteration in range(1, max_iter + 1):
        rc, output = await _run_project_cmd(cmd, project_dir, timeout=run_timeout)
        is_success = rc == 0
        is_timeout_ok = rc == 0 and "timeout" in output.lower()

        report.append(f"\n**🔄 Itération {iteration}/{max_iter}** — `{cmd}`")
        snippet = output[:600].strip()
        if snippet:
            report.append(f"```\n{snippet}\n```")

        if is_success:
            report.append("✅ Exécution réussie.")
            return True, "\n".join(report)

        if iteration == max_iter:
            report.append(f"⚠️ {max_iter} tentatives sans succès — livré en l'état.")
            return False, "\n".join(report)

        # ── Demander au LLM de corriger ──────────────────────────────────
        report.append("🔧 Correction automatique en cours…")

        files_content_parts: List[str] = []
        for rel in written_files[:12]:  # max 12 fichiers envoyés au LLM
            fp = project_dir / rel
            if fp.exists() and fp.is_file():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")[:2500]
                    files_content_parts.append(f"=== {rel} ===\n{text}")
                except Exception as e:
                    logger.debug(f"Read project file {rel}: {e}")
        files_blob = "\n\n".join(files_content_parts)[:9000]

        fix_messages = [
            {"role": "system", "content": _FIX_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Commande exécutée : `{cmd}`\n"
                    f"Répertoire : `{project_dir}`\n\n"
                    f"**Erreur :**\n```\n{output[:2000]}\n```\n\n"
                    f"**Fichiers du projet :**\n{files_blob}"
                ),
            },
        ]

        try:
            fix_response = await llm.chat(
                messages=fix_messages, temperature=0.15, max_tokens=6000
            )
            fix_data = _extract_json(fix_response)
        except Exception as exc:
            report.append(f"⚠️ LLM indisponible pour la correction : {exc}")
            return False, "\n".join(report)

        if not fix_data or "fixes" not in fix_data:
            report.append("⚠️ Réponse LLM non parseable — arrêt.")
            return False, "\n".join(report)

        explanation = fix_data.get("explanation", "")
        if explanation:
            report.append(f"  💡 {explanation}")

        applied = 0
        for fix in (fix_data["fixes"] or [])[:5]:
            rel_path = _sanitize_path(fix.get("path", "") or "")
            content = fix.get("content", "") or ""
            if rel_path and content:
                target = project_dir / rel_path
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    report.append(f"  ✏️ {rel_path}")
                    applied += 1
                except Exception as exc:
                    report.append(f"  ❌ {rel_path}: {exc}")

        if applied == 0:
            report.append("⚠️ Aucune correction appliquée — arrêt.")
            return False, "\n".join(report)

    return False, "\n".join(report)


# ─── Handler principal ─────────────────────────────────────────────────────

import os as _os

async def create_project_handler(
    ctx: HandlerContext,
    description: str,
    project_name: str = "",
    output_dir: str = "",
    auto_run: bool = True,
) -> HandlerResult:
    """
    Crée un projet complet via CodeAgent (itératif : écrit → teste → corrige).
    Fallback sur le pipeline batch si CodeAgent indisponible.

    Args:
        description: Description du projet souhaité
        project_name: Nom du projet (optionnel, sera déduit)
        output_dir: Répertoire de sortie (optionnel, utilise workspace)
        auto_run: Lancer automatiquement le projet et corriger les erreurs (défaut: True)
    """
    handler_name = "create_project"

    # ── Vérifier que le LLM est disponible ──
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        return HandlerResult.fail(
            "❌ LLM non disponible pour create_project", handler_name=handler_name,
        )
    llm = lumena.llm

    # ── Mode CodeAgent (défaut) ──
    # Délègue au CodeAgent itératif : écrit un fichier, le relit, valide,
    # écrit le suivant avec le contexte, teste, corrige.
    if _CODEAGENT_AVAILABLE:
        logger.info("[create_project] Mode CodeAgent direct (défaut)")
        _out = output_dir or ""
        _slug = re.sub(r"[^\w\-.]", "_", (project_name or "project").lower())
        if not _out and ctx.runtime_root:
            _today = datetime.now().strftime("%Y-%m-%d")
            _root = ctx.runtime_root
            # Injecter le sous-dossier date sauf si runtime_root le contient déjà
            if _root.name != _today:
                _root = _root / _today
            _out = str(_root / _slug)
        elif _out and _slug:
            # Si output_dir fourni mais le project_name n'est pas dedans, l'ajouter
            _out_lower = _out.replace("\\", "/").lower()
            if _slug not in _out_lower:
                _subfolder = _slug if _slug.startswith("projet-") else f"projet-{_slug}"
                _out = str(Path(_out) / _subfolder)

        # ── Prompt enrichi : instructions précises selon le type de projet ──
        _desc_lower = description.lower()
        _is_web = any(w in _desc_lower for w in (
            "site", "web", "html", "page", "landing", "portfolio", "dashboard",
            "jeu", "game", "snake", "tetris", "app",
        ))

        # Créer le répertoire de sortie AVANT de déléguer au CodeAgent
        _out_path = Path(_out)
        _out_path.mkdir(parents=True, exist_ok=True)

        _ca_prompt = f"Crée un projet complet dans le dossier {_out}.\n"
        _ca_prompt += f"Le dossier {_out} EXISTE DÉJÀ — ne tente PAS de le créer avec mkdir ou run_command.\n"
        _ca_prompt += f"Utilise directement write_file pour créer tes fichiers (les sous-dossiers sont créés automatiquement).\n"
        _ca_prompt += f"Description: {description}\n"
        if project_name:
            _ca_prompt += f"Nom du projet: {project_name}\n"

        if _is_web:
            _ca_prompt += (
                "\n== INSTRUCTIONS WEB ==\n"
                "1. Commence par index.html : structure complète, liens CSS/JS corrects\n"
                "2. Puis styles.css : design moderne, responsive, variables CSS, Google Fonts\n"
                "3. Puis le(s) fichier(s) JS : logique complète, tous les sélecteurs doivent "
                "matcher les id/class du HTML\n"
                "4. Après CHAQUE fichier, relis les fichiers précédents pour vérifier la cohérence "
                "(sélecteurs CSS↔HTML, querySelector JS↔HTML)\n"
                "5. À la fin, relis TOUS les fichiers et corrige toute incohérence\n"
                "\nRègles CRITIQUES :\n"
                "- Chaque fichier doit être COMPLET (pas de placeholder, pas de TODO)\n"
                "- Les chemins dans <link> et <script> doivent être relatifs (./styles.css, ./game.js)\n"
                "- Pas d'images locales : utilise des emojis, SVG inline, ou CSS uniquement\n"
                "- Les event listeners JS doivent cibler des éléments qui EXISTENT dans le HTML\n"
                "- Teste la cohérence : chaque id/class référencé en JS/CSS doit exister dans le HTML\n"
                "- PORTS : si le projet lance un serveur, utilise un port entre 8700 et 8750 (jamais 8080/3000/5000)\n"
            )
        else:
            _ca_prompt += (
                "\nCrée tous les fichiers nécessaires un par un avec write_file. "
                "Chaque fichier doit être complet et syntaxiquement valide. "
                "Après chaque fichier, relis les précédents pour vérifier la cohérence.\n"
                "Si le projet lance un serveur, utilise un port entre 8700 et 8750 (jamais 8080/3000/5000).\n"
            )

        try:
            _current_model = str(getattr(llm, "model_name", "") or "")
            _ca_result = await _delegate_to_agent(
                _ca_prompt,
                agent_type="code",
                context={
                    "workspace_path": _out,
                    "project_dir": _out,
                    **({"_best_model": _current_model} if _current_model else {}),
                },
            )
            # P1 : vérifier si CodeAgent est bloqué → tenter version simplifiée
            if "itérations sans conclusion" in _ca_result:
                import re as _re_simp
                _simple_desc = _re_simp.sub(
                    r"\b(?:animations?|3[Dd]|WebGL|three\.js|canvas|shader|particule|effets?\s+visuels?)\b",
                    "", description, flags=_re_simp.IGNORECASE
                ).strip()
                if _simple_desc != description:
                    try:
                        _min_result = await _delegate_to_agent(
                            f"VERSION MINIMALE: {_simple_desc[:150]}. "
                            f"1 seul fichier, zéro dépendance externe.",
                            agent_type="code",
                            context={"workspace_path": _out, "project_dir": _out},
                        )
                        if "itérations sans conclusion" not in _min_result:
                            return HandlerResult.ok(
                                f"✅ Projet créé (version simplifiée) dans `{_out}`\n\n{_min_result[:1500]}",
                                handler_name=handler_name,
                            )
                    except Exception:
                        pass
                _files = [f.name for f in Path(_out).rglob("*") if f.is_file()][:10]
                return HandlerResult.fail(
                    f"Fichiers partiels créés : {', '.join(_files) or 'aucun'}. "
                    f"Essaie une demande plus courte.",
                    handler_name=handler_name,
                )
            return HandlerResult.ok(
                f"✅ Projet créé via CodeAgent dans `{_out}`\n\n{_ca_result[:2000]}",
                handler_name=handler_name,
            )
        except Exception as e:
            logger.warning("[create_project] CodeAgent échoué ({}), fallback pipeline batch", e)
            # Fallback silencieux vers le pipeline batch ci-dessous

    elif not _CODEAGENT_AVAILABLE:
        logger.info("[create_project] CodeAgent indisponible, fallback pipeline batch")

    # ── Import Plan Manager pour persistence ──
    try:
        from ...tools.plan_manager import (
            handle_plan_create, handle_plan_update,
            handle_plan_done, _find_plan, _parse_tasks_from_content,
        )
        _has_plan_manager = True
    except Exception:
        _has_plan_manager = False

    # ══════════════════════════════════════════════════════════════════
    # PHASE 0 : Détection resume — un plan existant ?
    # ══════════════════════════════════════════════════════════════════
    _resume_plan_id = None
    _resume_valid_files: List[Dict[str, str]] = []
    _resume_base_dir: Optional[Path] = None
    _resumed = False

    if _has_plan_manager and project_name:
        _slug = re.sub(r"[^\w\-.]", "_", project_name.lower())
        _existing = _find_plan(f"project_{_slug}")
        if _existing:
            _content = _existing.read_text(encoding="utf-8")
            _tasks = _parse_tasks_from_content(_content)
            _pending = [(i, t) for i, (done, t) in enumerate(_tasks) if not done]
            if _pending:
                logger.info("[create_project] Plan existant trouvé: {} ({}/{} restants)",
                            _existing.stem, len(_pending), len(_tasks))
                _resume_plan_id = _existing.stem
                # Extraire le output_dir depuis le contenu du plan
                _dir_match = re.search(r"Dossier\s*:\s*`(.+?)`", _content)
                if _dir_match:
                    _resume_base_dir = Path(_dir_match.group(1))
                # Les tâches pending contiennent les chemins de fichiers
                for _, task_text in _pending:
                    clean = _sanitize_path(task_text)
                    if clean:
                        _resume_valid_files.append({"path": clean, "description": "", "language": ""})
                if _resume_valid_files:
                    _resumed = True

    if _resumed and _resume_base_dir:
        # ── Mode resume : sauter Phase 1, utiliser le plan existant ──
        valid_files = _resume_valid_files
        resolved_name = project_name or "project"
        resolved_name = re.sub(r"[^\w\-.]", "_", resolved_name)
        base_dir = _resume_base_dir
        file_tree = "\n".join(f"  {f['path']}" for f in valid_files)
        logger.info("[create_project] ♻️ Resume: {} fichiers restants dans {}",
                    len(valid_files), base_dir)
    else:
        # ══════════════════════════════════════════════════════════════
        # PHASE 1 : Planification — LLM génère l'arborescence
        # ══════════════════════════════════════════════════════════════
        logger.info("[create_project] Phase 1: planification pour '{}'", description[:80])

        # ── Détecter le type pour injecter les contraintes architecturales ──
        _pre_type = _detect_project_type(description, [])
        _plan_supplement = _PLAN_SUPPLEMENT_MAP.get(_pre_type, "")

        _plan_user_content = description
        if _plan_supplement:
            _plan_user_content = f"{description}\n\n{_plan_supplement}"
            logger.info("[create_project] Plan supplement '{}' injecté", _pre_type)

        plan_messages = [
            {
                "role": "system",
                "content": _build_plan_prompt(_pre_type, _MAX_FILES),
            },
            {"role": "user", "content": _plan_user_content},
        ]

        try:
            plan_response = await llm.chat(
                messages=plan_messages,
                temperature=0.4,
                max_tokens=_PLAN_MAX_TOKENS,
            )
        except Exception as e:
            return HandlerResult.fail(
                f"❌ Erreur LLM (phase plan) : {e}", handler_name=handler_name,
            )

        plan = _extract_json(plan_response)
        if not plan or "files" not in plan:
            return HandlerResult.fail(
                f"❌ Plan invalide retourné par le LLM. Réponse brute :\n{plan_response[:500]}",
                handler_name=handler_name,
            )

        files_plan: List[Dict[str, str]] = plan["files"]
        if not files_plan:
            return HandlerResult.fail(
                "❌ Le plan ne contient aucun fichier.", handler_name=handler_name,
            )

        # Limiter le nombre de fichiers
        if len(files_plan) > _MAX_FILES:
            files_plan = files_plan[:_MAX_FILES]
            logger.warning("[create_project] Plan tronqué à {} fichiers", _MAX_FILES)

        # Valider les chemins
        valid_files = []
        _skipped_binary = []
        for entry in files_plan:
            raw_path = entry.get("path", "")
            clean = _sanitize_path(raw_path)
            if clean:
                # Filtrer les fichiers binaires que le LLM ne peut pas générer
                ext = ("." + clean.rsplit(".", 1)[-1].lower()) if "." in clean else ""
                if ext in _BINARY_EXTENSIONS:
                    _skipped_binary.append(clean)
                    logger.warning("[create_project] Fichier binaire ignoré (LLM ne peut pas le générer): {}", clean)
                    continue
                valid_files.append({
                    "path": clean,
                    "description": entry.get("description", ""),
                    "language": entry.get("language", ""),
                })
            else:
                logger.warning("[create_project] Chemin invalide ignoré: {}", raw_path)

        if _skipped_binary:
            logger.info("[create_project] {} fichier(s) binaire(s) filtrés du plan: {}",
                        len(_skipped_binary), ", ".join(_skipped_binary))

        if not valid_files:
            return HandlerResult.fail(
                "❌ Aucun fichier valide dans le plan.", handler_name=handler_name,
            )

        resolved_name = project_name or plan.get("project_name", "project")
        resolved_name = re.sub(r"[^\w\-.]", "_", resolved_name)

        file_tree = "\n".join(f"  {f['path']}" for f in valid_files)
        logger.info(
            "[create_project] Plan: {} fichiers pour '{}'\n{}",
            len(valid_files), resolved_name, file_tree,
        )

        # Résoudre le répertoire de sortie
        # IMPORTANT: output_dir relatif doit rester ancré au runtime_root courant
        # (évite les dérives vers lumena_root et le bug workspace/workspace).
        # BUGFIX: toujours créer un sous-dossier nommé d'après le projet,
        # sinon les fichiers atterrissent à la racine de workspace/YYYY-MM-DD/.
        if output_dir:
            requested = Path(str(output_dir).strip())
            if requested.is_absolute():
                parent_dir = requested.resolve()
            else:
                rel = requested.as_posix()
                if rel == "workspace":
                    rel = ""
                elif rel.startswith("workspace/"):
                    rel = rel[len("workspace/"):]
                # P4.1: éviter duplication date si runtime_root la contient déjà
                # Ex: runtime_root=.../workspace/2026-03-15, rel="2026-03-15" → rel=""
                if rel:
                    runtime_tail = ctx.runtime_root.name  # ex: "2026-03-15"
                    if rel == runtime_tail or rel.startswith(runtime_tail + "/"):
                        rel = rel[len(runtime_tail):].lstrip("/")
                parent_dir = (ctx.runtime_root / rel).resolve() if rel else ctx.runtime_root.resolve()
            base_dir = (parent_dir / resolved_name).resolve()
        else:
            _today_batch = datetime.now().strftime("%Y-%m-%d")
            _root_batch = ctx.runtime_root
            if _root_batch.name != _today_batch:
                _root_batch = _root_batch / _today_batch
            base_dir = (_root_batch / resolved_name).resolve()

        # P4.2: protection path-length Windows (260 chars)
        _longest_file = max((f["path"] for f in valid_files), key=len, default="")
        _full_path_len = len(str(base_dir / _longest_file))
        if _full_path_len > 240:
            # Tronquer le nom du projet pour tenir dans la limite
            _excess = _full_path_len - 230
            if len(resolved_name) > _excess + 5:
                resolved_name = resolved_name[:len(resolved_name) - _excess]
                base_dir = base_dir.parent / resolved_name
                logger.warning("[create_project] Nom tronqué pour path Windows: {}", resolved_name)
            else:
                logger.warning("[create_project] Chemin très long ({} chars), risque d'erreur Windows", _full_path_len)

        # ── Persister le plan via Plan Manager ──
        if _has_plan_manager:
            _tasks_str = "|".join(f["path"] for f in valid_files)
            _plan_title = f"project_{re.sub(r'[^a-z0-9_-]', '_', resolved_name.lower())}"
            await handle_plan_create(
                title=_plan_title,
                tasks=_tasks_str,
                context=f"create_project: {description[:200]}\nDossier: `{base_dir}`",
            )
            _resume_plan_id = _plan_title
            logger.info("[create_project] Plan persisté: {}", _plan_title)

    # ══════════════════════════════════════════════════════════════════
    # PHASE 1.5 : Contrat partagé — cohérence entre fichiers
    # ══════════════════════════════════════════════════════════════════
    _shared_contract = ""
    try:
        _model_name = getattr(llm, "model_name", "") or ""
        _use_json_contract = _is_json_contract_capable(_model_name)
        _contract_prompt = _get_contract_prompt(description, valid_files, model_name=_model_name)
        logger.info("[create_project] Phase 1.5: génération du contrat partagé (json={})", _use_json_contract)
        _contract_user = (
            f"Projet: {resolved_name}\n"
            f"Description: {description[:400]}\n"
            f"Arborescence:\n{file_tree}"
        )
        _contract_resp = await llm.chat(
            messages=[
                {"role": "system", "content": _contract_prompt},
                {"role": "user", "content": _contract_user},
            ],
            temperature=0.2,
            max_tokens=_CONTRACT_MAX_TOKENS,
        )
        _contract_text = _contract_resp.strip()
        # Tenter le parsing JSON si le modèle est JSON-capable
        if _use_json_contract:
            try:
                # Nettoyer les blocs markdown éventuels
                _ct = re.sub(r'^```(?:json)?\n', '', _contract_text)
                _ct = re.sub(r'\n```\s*$', '', _ct)
                _parsed = json.loads(_ct)
                if isinstance(_parsed, dict):
                    _contract_text = _format_json_contract(_parsed)
                    logger.info("[create_project] Contrat JSON parsé OK ({} clés)", len(_parsed))
            except (json.JSONDecodeError, ValueError):
                logger.debug("[create_project] Contrat JSON non parsable → fallback texte libre")
        _shared_contract = (
            "═══ CONTRAT PARTAGÉ (respecter ABSOLUMENT pour cohérence inter-fichiers) ═══\n"
            + _contract_text
            + "\n═══ FIN DU CONTRAT ═══"
        )
        logger.info("[create_project] Contrat généré ({} chars)", len(_shared_contract))
    except Exception as _ce:
        logger.warning("[create_project] Phase 1.5 échouée (non bloquant): {}", _ce)

    # ══════════════════════════════════════════════════════════════════
    # PHASE 2 : Génération par batch avec checkpoint
    # ══════════════════════════════════════════════════════════════════
    logger.info("[create_project] Phase 2: génération de {} fichiers", len(valid_files))

    # Tri par ordre de dépendance (config→CSS→JS→HTML→docs)
    valid_files.sort(key=_dep_sort_key)
    _all_paths = [f["path"] for f in valid_files]
    _generated_contents: Dict[str, str] = {}  # path → content (pour injection contexte)

    # ── Construire les directives web si projet web ──
    _web_directives = ""
    if _is_web_project(description) and _WEBSITE_BUILDER_AVAILABLE:
        _design = _build_design_directives(description)
        _web_directives = f"{_design}\n\n{_WEBSITE_GENERATE_PROMPT}"
        logger.info("[create_project] Projet web détecté → directives de design + prompt web injectés")

    # ── Directives type-spécifiques ──
    _project_type = _detect_project_type(description, _all_paths)
    _type_dirs = _get_type_directives(_project_type, description)
    if _type_dirs:
        logger.info("[create_project] Type '{}' détecté → directives spécifiques injectées", _project_type)

    sem = asyncio.Semaphore(_MAX_PARALLEL)
    _is_deepseek = hasattr(llm, "provider") and "deepseek" in str(getattr(llm, "provider", "")).lower()
    _gen_model: Optional[str] = "deepseek-reasoner" if (_is_deepseek and len(valid_files) > 15) else None
    if _gen_model:
        logger.info("[create_project] {} fichiers > 15 → démarrage avec deepseek-reasoner", len(valid_files))

    # ── Détection modèle léger → trimmer le prompt ──
    _current_model_name = str(getattr(llm, "model", "") or "").lower()
    _is_small_model = any(tag in _current_model_name for tag in ("nano", "mini", "gpt-4o-mini"))
    if _is_small_model:
        # Modèles légers: garder uniquement les design directives, supprimer WEBSITE_GENERATE_PROMPT
        if _web_directives and _WEBSITE_BUILDER_AVAILABLE:
            _design_only = _build_design_directives(description)
            _web_directives = _design_only  # sans WEBSITE_GENERATE_PROMPT (3000+ chars)
            logger.info("[create_project] Modèle léger détecté ({}) → prompt web allégé", _current_model_name)

    # ── Modèle d'upgrade pour retry (même provider, plus capable) ──
    _UPGRADE_MAP: Dict[str, str] = {
        "gpt-5.4-nano": "gpt-5.4-mini",
        "gpt-4.1-nano": "gpt-4.1-mini",
        "gpt-4o-mini": "gpt-4o",
        "gpt-5.4-mini": "gpt-5.4",
        "gpt-4.1-mini": "gpt-4.1",
        "deepseek-chat": "deepseek-reasoner",
    }

    def _get_upgrade_model(current: str) -> Optional[str]:
        """Retourne un modèle plus capable du même provider, ou None."""
        cl = current.lower().strip()
        return _UPGRADE_MAP.get(cl)

    async def _generate_one(file_entry: Dict[str, str], model_override: Optional[str] = None) -> tuple[str, str, Optional[str]]:
        """Génère le contenu d'un fichier. Retourne (path, content, error)."""
        # max_tokens déterminé automatiquement selon le provider/modèle actuel.
        # On utilise llm.max_output_tokens si disponible (MultiProviderLLM),
        # sinon on ne passe rien (le LLM utilisera son propre défaut).
        _fmax: Optional[int] = getattr(llm, "max_output_tokens", None) or 16384
        _ext = "." + file_entry["path"].rsplit(".", 1)[-1].lower() if "." in file_entry["path"] else ""
        # Fichiers purement JSON/YAML/config → le manifest guard ne s'applique pas
        _is_native_json = _ext in (".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".env")
        # Contexte des fichiers déjà générés (dépendances)
        _dep_ctx = _build_dependency_context(
            file_entry["path"], _generated_contents, _all_paths,
        )
        async with sem:
            file_messages = [
                {
                    "role": "system",
                    "content": _FILE_SYSTEM_PROMPT.format(
                        project_name=resolved_name,
                        project_description=description[:500],
                        file_tree=file_tree,
                        file_path=file_entry["path"],
                        file_description=file_entry["description"],
                        file_language=file_entry["language"],
                        web_design_directives=_web_directives,
                        type_directives=_type_dirs,
                        shared_contract=_shared_contract,
                        dependency_context=_dep_ctx,
                    ),
                },
                {
                    "role": "user",
                    "content": f"Génère le contenu complet de {file_entry['path']}",
                },
            ]
            try:
                raw = await llm.chat(
                    messages=file_messages,
                    temperature=0.3,
                    **({"max_tokens": _fmax} if _fmax is not None else {}),
                    **(({"model": model_override}) if model_override else {}),
                )

                # ── Détection de troncature → retry avec deepseek-reasoner (32K) ──
                _meta = getattr(llm, "_last_response_meta", None) or {}
                _was_truncated = _meta.get("text_may_be_incomplete", False)
                _used_model = str(_meta.get("model_used", model_override or "")).lower()

                # Nettoyer les warnings de continuation injectés dans le texte
                if _was_truncated and "⚠️" in raw:
                    raw = re.sub(r'\n\n⚠️ Réponse potentiellement incomplète[^\n]*$', '', raw)
                    raw = re.sub(r'\n\n⚠️ Continuation[^\n]*$', '', raw)
                    raw = re.sub(r'\n\n⚠️ Répétition[^\n]*$', '', raw)
                    raw = re.sub(r'\n\n⚠️ Texte vide[^\n]*$', '', raw)

                # Si tronqué ou vide → retry avec modèle upgrade (provider-agnostic)
                if _was_truncated and (not raw or not raw.strip()):
                    _upgrade = _get_upgrade_model(_used_model) if _used_model else None
                    if _is_deepseek and "reasoner" not in _used_model:
                        _upgrade = "deepseek-reasoner"
                    if _upgrade:
                        logger.warning(
                            "[create_project] 🔄 {} : vide/tronqué sur {} → retry avec {} (32K)",
                            file_entry["path"], _used_model, _upgrade,
                        )
                        raw = await llm.chat(
                            messages=file_messages,
                            temperature=0.3,
                            max_tokens=32768,
                            model=_upgrade,
                        )
                        _meta2 = getattr(llm, "_last_response_meta", None) or {}
                        if _meta2.get("text_may_be_incomplete", False) and "⚠️" in raw:
                            raw = re.sub(r'\n\n⚠️[^\n]*$', '', raw)
                    elif not raw or not raw.strip():
                        # Pas de modèle upgrade dispo → retry simple même modèle
                        logger.warning(
                            "[create_project] 🔄 {} : vide sur {} → retry simple",
                            file_entry["path"], _used_model,
                        )
                        raw = await llm.chat(
                            messages=file_messages,
                            temperature=0.4,
                            **({"max_tokens": _fmax} if _fmax is not None else {}),
                            **(({"model": model_override}) if model_override else {}),
                        )
                elif (
                    _was_truncated
                    and _is_deepseek
                    and "reasoner" not in _used_model
                ):
                    logger.warning(
                        "[create_project] 🔄 {} : tronqué sur {} → retry avec deepseek-reasoner (32K)",
                        file_entry["path"], _used_model,
                    )
                    raw = await llm.chat(
                        messages=file_messages,
                        temperature=0.3,
                        max_tokens=32768,
                        model="deepseek-reasoner",
                    )
                    # Re-nettoyer
                    _meta2 = getattr(llm, "_last_response_meta", None) or {}
                    if _meta2.get("text_may_be_incomplete", False) and "⚠️" in raw:
                        raw = re.sub(r'\n\n⚠️[^\n]*$', '', raw)

                content = _strip_code_fences(raw)

                # ── Guard : détecter si le LLM a retourné un JSON manifest ──
                if not _is_native_json:
                    _extracted = _try_extract_from_manifest(content, file_entry["path"])
                    if _extracted is not None:
                        if _extracted:
                            logger.warning(
                                "[create_project] ⚠️ {} : JSON manifest détecté → contenu extrait ({} chars)",
                                file_entry["path"], len(_extracted),
                            )
                            content = _extracted
                        else:
                            logger.warning(
                                "[create_project] ⚠️ {} : JSON manifest sans contenu extractable → retry",
                                file_entry["path"],
                            )
                            return file_entry["path"], "", (
                                f"LLM a retourné un JSON manifest au lieu du contenu de {file_entry['path']}"
                            )

                # ── Guard : détecter contenu tronqué → retry ──
                if _looks_truncated(content, _ext):
                    _retry_model: Optional[str] = None
                    if _is_deepseek:
                        _retry_model = "deepseek-reasoner"
                    else:
                        _retry_model = _get_upgrade_model(_used_model)
                    logger.warning(
                        "[create_project] ✂️ {} semble tronqué ({} chars, ext={}) → retry {}",
                        file_entry["path"], len(content), _ext,
                        _retry_model or _used_model,
                    )
                    retry_raw = await llm.chat(
                        messages=file_messages,
                        temperature=0.3,
                        max_tokens=32768,
                        **(({"model": _retry_model}) if _retry_model else {}),
                    )
                    retry_content = _strip_code_fences(retry_raw)
                    if not _is_native_json:
                        _retry_ext = _try_extract_from_manifest(retry_content, file_entry["path"])
                        if _retry_ext:
                            retry_content = _retry_ext
                    if retry_content and not _looks_truncated(retry_content, _ext):
                        logger.info(
                            "[create_project] ✅ {} retry OK ({} chars)",
                            file_entry["path"], len(retry_content),
                        )
                        content = retry_content
                    else:
                        logger.warning(
                            "[create_project] ⚠️ {} toujours tronqué après retry ({} chars) — on garde le meilleur",
                            file_entry["path"], len(retry_content) if retry_content else 0,
                        )
                        if retry_content and len(retry_content) > len(content):
                            content = retry_content

                if len(content) > _MAX_CONTENT_LEN:
                    content = content[:_MAX_CONTENT_LEN]
                return file_entry["path"], content, None
            except Exception as e:
                return file_entry["path"], "", str(e)

    # ── Génération par vagues DAG (Phase 4) ──────────────────────────────────
    # Wave 0 (séquentiel) : configs / env / json
    # Wave 1 (séquentiel) : CSS / schémas / fondation
    # Wave 2 (parallèle)  : reste (py, js, html, md…)
    base_dir.mkdir(parents=True, exist_ok=True)
    written = []
    errors = []
    _completed_indices: List[int] = []  # indices dans valid_files pour plan_update

    _COMPLEX_EXTENSIONS = {".py", ".ts", ".tsx", ".rs", ".go", ".java", ".cpp", ".js", ".html"}

    def _pick_model(f: Dict[str, str], current_model: Optional[str]) -> Optional[str]:
        # Fichiers "lourds" → deepseek-reasoner directement pour éviter troncature + retry coûteux
        if _is_deepseek:
            ext = "." + f.get("path", "").rsplit(".", 1)[-1].lower() if "." in f.get("path", "") else ""
            if ext in _COMPLEX_EXTENSIONS:
                return "deepseek-reasoner"
        return current_model

    async def _process_one_and_write(f: Dict[str, str], global_idx: int, current_model: Optional[str]) -> bool:
        """Génère + écrit un fichier. Retourne True si succès."""
        _fmax: Optional[int] = getattr(llm, "max_output_tokens", None) or 16384
        # Garde: skip fichiers binaires (sécurité pour mode resume)
        _ext = ("." + f["path"].rsplit(".", 1)[-1].lower()) if "." in f["path"] else ""
        if _ext in _BINARY_EXTENSIONS:
            logger.warning("[create_project] Skip binaire (resume guard): {}", f["path"])
            return False
        result = await _generate_one(f, model_override=_pick_model(f, current_model))
        if isinstance(result, Exception):
            errors.append(f"{f['path']}: {result}")
            return False
        file_path, content, gen_error = result
        if gen_error or not content.strip():
            errors.append(f"{file_path}: {gen_error or 'contenu vide'}")
            # Retry avec modèle upgrade si disponible, sinon même modèle
            _failed_model = str((getattr(llm, "_last_response_meta", None) or {}).get("model_used", current_model or "")).lower()
            _retry_override = _get_upgrade_model(_failed_model)
            if not _retry_override and _is_deepseek:
                _retry_override = "deepseek-chat"
            logger.info(
                "[create_project] 🔄 {} retry avec {} (après échec {})",
                file_path, _retry_override or "modèle par défaut", _failed_model,
            )
            r2 = await _generate_one(f, model_override=_retry_override)
            if not isinstance(r2, Exception):
                file_path, content, gen_error = r2
                if gen_error or not content.strip():
                    return False
            else:
                return False
        target = base_dir / file_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # ── Validation syntaxique pré-écriture (P4) ──
            _ext = ("." + file_path.rsplit(".", 1)[-1].lower()) if "." in file_path else ""
            _syntax_err = _quick_syntax_check(content, _ext)
            if _syntax_err:
                logger.warning("[create_project] ⚠️ {} syntaxe invalide: {} → retry", file_path, _syntax_err)
                # Retry avec message d'erreur explicite
                _retry_msgs = [
                    {"role": "system", "content": f"Le fichier {file_path} que tu as généré contient une erreur de syntaxe:\n{_syntax_err}\n\nRégénère le fichier COMPLET et syntaxiquement VALIDE."},
                    {"role": "user", "content": f"Génère le contenu COMPLET et VALIDE de {file_path}"},
                ]
                try:
                    _retry_raw = await llm.chat(messages=_retry_msgs, temperature=0.2, **({"max_tokens": _fmax} if _fmax is not None else {}))
                    _retry_content = _strip_code_fences(_retry_raw)
                    _retry_err = _quick_syntax_check(_retry_content, _ext)
                    if not _retry_err and _retry_content.strip():
                        content = _retry_content
                        logger.info("[create_project] ✅ {} syntaxe corrigée au retry", file_path)
                    else:
                        errors.append(f"{file_path}: syntaxe invalide après retry: {_retry_err or _syntax_err}")
                        return False
                except Exception:
                    errors.append(f"{file_path}: syntaxe invalide: {_syntax_err}")
                    return False
            target.write_text(content, encoding="utf-8")
            written.append(file_path)
            _generated_contents[file_path] = content
            _completed_indices.append(global_idx + 1)  # 1-based pour plan_update
            logger.debug("[create_project] ✅ {}", file_path)
            return True
        except Exception as e:
            errors.append(f"{file_path}: écriture échouée: {e}")
            return False

    # Séparer les fichiers en 3 waves
    wave_groups: Dict[int, List[tuple[int, Dict[str, str]]]] = {0: [], 1: [], 2: []}
    for gidx, fe in enumerate(valid_files):
        wave_groups[_dep_wave(fe)].append((gidx, fe))

    # Exécuter les waves dans l'ordre
    for wave_num in (0, 1, 2):
        wave_files = wave_groups[wave_num]
        if not wave_files:
            continue

        _current_model = _gen_model

        if wave_num < 2:
            # Wave 0 et 1 : séquentiel pour maximiser la cohérence contexte
            logger.info("[create_project] Wave {}: {} fichiers (séquentiel)", wave_num, len(wave_files))
            for gidx, fe in wave_files:
                await _process_one_and_write(fe, gidx, _current_model)
        else:
            # Wave 2 : sous-waves séquentielles pour projets web, parallèle sinon
            _is_web = _is_web_project(description)
            if _is_web:
                # Sous-wave 2a : HTML séquentiel (définit la structure DOM)
                _html_files = [(gi, fe) for gi, fe in wave_files
                               if fe.get("path", "").lower().endswith((".html", ".htm"))]
                # Sous-wave 2b : JS/TS séquentiel (voit le HTML via _generated_contents)
                _js_files = [(gi, fe) for gi, fe in wave_files
                             if fe.get("path", "").lower().endswith((".js", ".ts", ".jsx", ".tsx"))]
                # Sous-wave 2c : tout le reste (parallèle)
                _html_js_set = {id(fe) for _, fe in _html_files + _js_files}
                _rest_files = [(gi, fe) for gi, fe in wave_files if id(fe) not in _html_js_set]

                if _html_files:
                    logger.info("[create_project] Wave 2a: {} HTML (séquentiel)", len(_html_files))
                    for gidx, fe in _html_files:
                        await _process_one_and_write(fe, gidx, _current_model)
                if _js_files:
                    logger.info("[create_project] Wave 2b: {} JS/TS (séquentiel)", len(_js_files))
                    for gidx, fe in _js_files:
                        await _process_one_and_write(fe, gidx, _current_model)
                if _rest_files:
                    logger.info("[create_project] Wave 2c: {} autres (parallèle, sem={})", len(_rest_files), _MAX_PARALLEL)
                    tasks = [
                        _process_one_and_write(fe, gidx, _current_model)
                        for gidx, fe in _rest_files
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
            else:
                # Projets non-web : tout Wave 2 en parallèle (pas de dépendance DOM)
                logger.info("[create_project] Wave 2: {} fichiers (parallèle, sem={})", len(wave_files), _MAX_PARALLEL)
                tasks = [
                    _process_one_and_write(fe, gidx, _current_model)
                    for gidx, fe in wave_files
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

        # Checkpoint après chaque wave
        if _has_plan_manager and _resume_plan_id and _completed_indices:
            _indices_str = ",".join(str(i) for i in _completed_indices)
            await handle_plan_update(plan_id=_resume_plan_id, task_index=_indices_str, done=True)
            _completed_indices.clear()

        logger.info("[create_project] Wave {} terminée: {}/{} fichiers OK",
                    wave_num, len(written), len(valid_files))

    # ── Validation inter-fichiers ──
    _validation_text = ""
    if _VALIDATOR_AVAILABLE and written:
        try:
            _all_files: Dict[str, str] = {}
            for fp in written:
                _fp = base_dir / fp
                if _fp.is_file() and _fp.stat().st_size < 500_000:
                    _all_files[fp] = _fp.read_text(encoding="utf-8", errors="replace")
            if _all_files:
                _vreport = _validate_project(_all_files, project_dir=base_dir)
                if not _vreport.is_clean:
                    _validation_text = _vreport.format_for_llm()
                    logger.warning("[create_project] Validation: {}", _vreport.summary())

                    # ── Self-repair loop: LLM corrige les fichiers cassés ──
                    _REPAIR_MAX_ITER = 3
                    for _repair_iter in range(1, _REPAIR_MAX_ITER + 1):
                        _repaired_this_iter: Dict[str, str] = {}  # fichiers déjà corrigés dans cette itération
                        # Identifier les fichiers mentionnés dans les erreurs
                        _broken_paths = {i.file_path for i in _vreport.errors if i.file_path}
                        # Pour XREF errors, ajouter aussi les fichiers cible (ex: HTML quand JS ref une classe manquante)
                        for _vi in _vreport.errors:
                            if _vi.code and _vi.code.startswith("XREF_JS_") and _vi.suggestion:
                                # La suggestion mentionne "dans le HTML" — trouver les fichiers HTML du projet
                                for _af in _all_files:
                                    if _af.endswith((".html", ".htm")) and _af not in _broken_paths:
                                        _broken_paths.add(_af)
                        if not _broken_paths:
                            break
                        logger.info(
                            "[create_project] Self-repair iter {}/{}: {} fichier(s) à corriger",
                            _repair_iter, _REPAIR_MAX_ITER, len(_broken_paths),
                        )
                        _repair_changed = False
                        for bp in list(_broken_paths)[:10]:  # max 10 fichiers par itération
                            if bp not in _all_files:
                                continue
                            _bp_issues = [
                                str(i) for i in _vreport.issues
                                if i.file_path == bp and i.severity == Severity.ERROR
                            ]
                            if not _bp_issues:
                                continue
                            # Phase 6.2 : injecter le contexte des dépendances dans le prompt de repair
                            _repair_dep_ctx = _build_dependency_context(
                                bp, _all_files, list(_all_files.keys()),
                            )
                            # Injecter le contexte des fichiers déjà corrigés dans cette itération
                            _prev_repairs_ctx = ""
                            if _repaired_this_iter:
                                _prev_parts = []
                                _budget = 4000
                                for _rp, _rc in list(_repaired_this_iter.items())[:3]:
                                    _prev_parts.append(f"--- {_rp} (corrigé) ---\n{_rc[:_budget]}")
                                _prev_repairs_ctx = (
                                    f"\n**Fichiers déjà corrigés dans cette itération :**\n"
                                    + "\n".join(_prev_parts) + "\n"
                                )
                            _repair_prompt = (
                                f"Le fichier `{bp}` contient ces erreurs de validation inter-fichiers :\n"
                                + "\n".join(f"- {iss}" for iss in _bp_issues[:10])
                                + f"\n\nFichiers du projet: {', '.join(sorted(_all_files.keys()))}\n"
                                + (f"\n**Contexte des fichiers dont dépend `{bp}` :**\n{_repair_dep_ctx}\n" if _repair_dep_ctx else "")
                                + _prev_repairs_ctx
                                + f"\nContenu actuel de `{bp}`:\n```\n{_all_files[bp][:12000]}\n```\n\n"
                                + "Retourne UNIQUEMENT le contenu corrigé du fichier, sans blocs de code markdown, "
                                + "sans explication. Le fichier doit être complet et fonctionnel."
                            )
                            try:
                                _fixed = await llm.chat(
                                    messages=[
                                        {"role": "system", "content": "Tu es un développeur expert. Corrige le fichier."},
                                        {"role": "user", "content": _repair_prompt},
                                    ],
                                    temperature=0.2,
                                )
                                _fixed = re.sub(r'^```[\w]*\n', '', _fixed.strip())
                                _fixed = re.sub(r'\n```\s*$', '', _fixed)
                                if _fixed.strip() and _fixed.strip() != _all_files[bp].strip():
                                    # Vérification syntaxe multi-langage avant écriture
                                    _syn_err = _quick_syntax_check(_fixed, Path(bp).suffix.lower())
                                    if _syn_err:
                                        logger.debug("[create_project] Self-repair skip {}: {}", bp, _syn_err)
                                        continue
                                    (base_dir / bp).write_text(_fixed, encoding="utf-8")
                                    _all_files[bp] = _fixed
                                    _generated_contents[bp] = _fixed
                                    _repaired_this_iter[bp] = _fixed
                                    _repair_changed = True
                                    logger.info("[create_project] 🔧 Self-repair: {} corrigé", bp)
                            except SyntaxError:
                                logger.debug("[create_project] Self-repair skip {}: syntaxe invalide", bp)
                            except Exception as _re:
                                logger.debug("[create_project] Self-repair skip {}: {}", bp, _re)

                        if not _repair_changed:
                            break
                        # Re-validate après corrections
                        _vreport = _validate_project(_all_files, project_dir=base_dir)
                        if _vreport.is_clean:
                            _validation_text = ""
                            logger.info("[create_project] ✅ Self-repair: tous les problèmes corrigés (iter {})", _repair_iter)
                            break
                        _validation_text = _vreport.format_for_llm()
                        logger.info("[create_project] Self-repair iter {}: {}", _repair_iter, _vreport.summary())

                    # ── P9: CodeAgent fallback si self-repair n'a pas tout corrigé ──
                    if not _vreport.is_clean and _CODEAGENT_AVAILABLE:
                        try:
                            _ca_errors = _vreport.format_for_llm()[:2000]
                            logger.info("[create_project] CodeAgent fallback: {} erreurs restantes", len(_vreport.errors))
                            _ca_result = await _delegate_to_agent(
                                f"Le projet dans {base_dir} a des erreurs de validation après self-repair. "
                                f"Erreurs :\n{_ca_errors}\n\n"
                                f"Corrige les fichiers pour que le projet soit cohérent.",
                                agent_type="code",
                                context={"workspace_path": str(base_dir), "project_files": list(_all_files.keys())},
                            )
                            # Re-lire les fichiers après CodeAgent
                            for fp in list(_all_files.keys()):
                                _fp = base_dir / fp
                                if _fp.is_file():
                                    _all_files[fp] = _fp.read_text(encoding="utf-8", errors="replace")
                            _vreport = _validate_project(_all_files, project_dir=base_dir)
                            if _vreport.is_clean:
                                _validation_text = ""
                                logger.info("[create_project] ✅ CodeAgent a corrigé toutes les erreurs")
                            else:
                                _validation_text = _vreport.format_for_llm()
                                logger.warning("[create_project] CodeAgent: {} erreurs restantes", len(_vreport.errors))
                        except Exception as _cae:
                            logger.debug("[create_project] CodeAgent fallback échoué: {}", _cae)
                else:
                    logger.info("[create_project] Validation OK: {}", _vreport.summary())
        except Exception as _ve:
            logger.debug("[create_project] Validation skip: {}", _ve)

    # ── Résultat ──
    summary_parts = [
        f"✅ Projet **{resolved_name}** créé dans `{base_dir}`",
        f"📁 {len(written)}/{len(valid_files)} fichiers générés",
    ]
    if written:
        summary_parts.append("\n**Fichiers créés :**")
        for w in written:
            summary_parts.append(f"  • {w}")
    if errors:
        summary_parts.append(f"\n⚠️ {len(errors)} erreur(s) :")
        for e in errors[:5]:
            summary_parts.append(f"  • {e}")
    if _validation_text:
        summary_parts.append(f"\n🔍 **Validation inter-fichiers :**\n{_validation_text}")

    output = "\n".join(summary_parts)
    logger.info("[create_project] Terminé: {}/{} fichiers", len(written), len(valid_files))

    if not written:
        return HandlerResult.fail(output, handler_name=handler_name)

    # ── Pré-install jest-environment-jsdom si tests JS détectés ──────────────
    _has_js_tests = any(
        f.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))
        for f in written
    )
    _has_package_json = any(
        f == "package.json" or f.endswith("/package.json")
        for f in written
    )
    if _has_js_tests and _has_package_json:
        logger.info("[create_project] Tests JS détectés — pré-installation jest-environment-jsdom")
        await _run_project_cmd(
            "npm install --save-dev jest-environment-jsdom --silent 2>&1",
            base_dir,
            timeout=60,
        )

    # ══════════════════════════════════════════════════════════════════
    # PHASE 4 : Auto-run + boucle run → fix (si auto_run=True)
    # ══════════════════════════════════════════════════════════════════
    if auto_run:
        run_cmd = _detect_run_command(base_dir, written)
        if run_cmd:
            run_timeout = _detect_run_timeout(run_cmd)
            logger.info("[create_project] Phase 4: auto-run '{}' (timeout={}s)", run_cmd, run_timeout)
            run_success, run_report = await _run_and_fix_loop(
                llm=llm,
                project_dir=base_dir,
                cmd=run_cmd,
                written_files=written,
                run_timeout=run_timeout,
            )
            output += f"\n\n---\n### 🚀 Exécution automatique\n{run_report}"
            if run_success:
                output += f"\n\n✅ Le projet tourne. Commande : `{run_cmd}`"
            else:
                # ── P9: CodeAgent fallback après échec auto-run ──
                if _CODEAGENT_AVAILABLE:
                    try:
                        logger.info("[create_project] CodeAgent fallback: auto-run échoué")
                        _ca_run_result = await _delegate_to_agent(
                            f"Le projet dans {base_dir} a des erreurs runtime. "
                            f"Commande : {run_cmd}\nRapport :\n{run_report[:2000]}\n\n"
                            f"Corrige les fichiers pour que le projet fonctionne.",
                            agent_type="code",
                            context={"workspace_path": str(base_dir), "project_files": written},
                        )
                        # Re-essayer après CodeAgent
                        _ca_ok, _ca_rep = await _run_and_fix_loop(
                            llm=llm, project_dir=base_dir, cmd=run_cmd,
                            written_files=written, run_timeout=run_timeout,
                        )
                        if _ca_ok:
                            output += f"\n\n✅ CodeAgent a corrigé le projet. Commande : `{run_cmd}`"
                        else:
                            output += f"\n\n⚠️ Le projet a été créé mais l'auto-run n'a pas abouti après CodeAgent."
                    except Exception as _cre:
                        logger.debug("[create_project] CodeAgent run-fix fallback échoué: {}", _cre)
                        output += f"\n\n⚠️ Le projet a été créé mais l'auto-run n'a pas abouti après {_RUN_FIX_MAX_ITER} tentatives."
                else:
                    output += f"\n\n⚠️ Le projet a été créé mais l'auto-run n'a pas abouti après {_RUN_FIX_MAX_ITER} tentatives."
        else:
            output += "\n\n📁 Projet statique — pas d'auto-run nécessaire."

    # ── Archiver le plan quand tout est terminé (seulement si 0 erreur) ──
    if _has_plan_manager and _resume_plan_id and len(errors) == 0:
        try:
            await handle_plan_done(plan_id=_resume_plan_id, archive=True)
            logger.info("[create_project] Plan archivé: {}", _resume_plan_id)
        except Exception:
            pass  # non-bloquant

    return HandlerResult.ok(output, handler_name=handler_name)


# ─── Handler dev_run_fix ────────────────────────────────────────────────────

async def dev_run_fix_handler(
    ctx: HandlerContext,
    command: str,
    project_dir: str = "",
    max_attempts: int = 3,
) -> HandlerResult:
    """
    Lance une commande, analyse l'output, et si ça plante,
    lit les fichiers concernés, demande au LLM de corriger, relance.
    Boucle autonome jusqu'à succès ou max_attempts.

    Idéal pour : lancer des tests, compiler, builder, valider du code.
    """
    handler_name = "dev_run_fix"
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        return HandlerResult.fail("❌ LLM non disponible", handler_name=handler_name)

    # Résoudre le répertoire
    if project_dir:
        base_dir = ctx.resolve_path(project_dir, want_dir=True)
    else:
        base_dir = ctx.runtime_root

    if not base_dir.exists():
        return HandlerResult.fail(
            f"❌ Répertoire inexistant : {base_dir}", handler_name=handler_name
        )

    max_attempts = max(1, min(int(max_attempts), 5))

    # Lister les fichiers du projet pour le contexte LLM
    written_files = [
        str(p.relative_to(base_dir)).replace("\\", "/")
        for p in base_dir.rglob("*")
        if p.is_file()
        and not any(part.startswith(".") or part in ("node_modules", "__pycache__", ".git") for part in p.parts)
    ][:30]

    run_success, run_report = await _run_and_fix_loop(
        llm=lumena.llm,
        project_dir=base_dir,
        cmd=command,
        written_files=written_files,
        max_iter=max_attempts,
    )

    label = "✅ Succès" if run_success else "⚠️ Échec après corrections"
    return HandlerResult.ok(
        f"**{label}** — `{command}` dans `{base_dir}`\n\n{run_report}",
        handler_name=handler_name,
    )


# ─── Handlers test_and_fix + lint_and_fix ──────────────────────────────────

# ── Constantes test/lint ────────────────────────────────────────────────────
_TEST_FIX_MAX_ITER = 4
_TEST_FIX_MAX_OUTPUT = 4000
_LINT_FIX_MAX_ITER = 3
_LINT_FIX_MAX_OUTPUT = 3000


def _parse_test_results(output: str) -> dict:
    """
    Parse une sortie de tests multiformat (pytest, jest, cargo, go test…).
    Retourne un dict: {passed, failed, errors, total, framework, failed_names}.
    """
    output_lower = output.lower()

    # ── pytest ──────────────────────────────────────────────────────────────
    m = re.search(r"(\d+) passed", output_lower)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output_lower)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", output_lower)
    errors = int(m.group(1)) if m else 0

    # ── jest / mocha (JavaScript) ───────────────────────────────────────────
    if "tests:" in output_lower or "test suites:" in output_lower:
        m2 = re.search(r"(\d+)\s+passed", output_lower)
        passed = int(m2.group(1)) if m2 else passed
        m2 = re.search(r"(\d+)\s+failed", output_lower)
        failed = int(m2.group(1)) if m2 else failed

    # ── cargo test (Rust) ───────────────────────────────────────────────────
    if "test result:" in output_lower:
        m3 = re.search(r"(\d+) passed", output_lower)
        passed = int(m3.group(1)) if m3 else passed
        m3 = re.search(r"(\d+) failed", output_lower)
        failed = int(m3.group(1)) if m3 else failed

    # ── go test ─────────────────────────────────────────────────────────────
    if "--- fail" in output_lower or "--- pass" in output_lower:
        passed = output_lower.count("--- pass")
        failed = output_lower.count("--- fail")

    # Extraire les noms de tests échoués (pytest/jest)
    failed_names = re.findall(r"FAILED\s+([\w/.::\-]+)", output)
    failed_names += re.findall(r"✕\s+(.*?)(?:\n|$)", output)
    failed_names = [n.strip() for n in failed_names if n.strip()][:20]

    # Détecter le framework
    framework = "unknown"
    if "pytest" in output_lower or "collected" in output_lower:
        framework = "pytest"
    elif "jest" in output_lower or "describe" in output_lower:
        framework = "jest"
    elif "cargo test" in output_lower or "test result:" in output_lower:
        framework = "cargo"
    elif "--- pass" in output_lower or "--- fail" in output_lower:
        framework = "go"
    elif "mocha" in output_lower:
        framework = "mocha"

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": passed + failed + errors,
        "framework": framework,
        "failed_names": failed_names,
        "all_passing": (failed == 0 and errors == 0 and passed > 0),
        "nothing_ran": (passed == 0 and failed == 0 and errors == 0),
    }


def _parse_lint_results(output: str, tool: str) -> dict:
    """
    Parse une sortie de linter multiformat (ruff, flake8, mypy, eslint, pylint…).
    Retourne {error_count, warning_count, files_affected, all_clean, tool}.
    """
    output_lower = output.lower()

    # ── ruff ────────────────────────────────────────────────────────────────
    error_count = 0
    warning_count = 0

    if tool in ("ruff", "flake8", "auto"):
        # Format: "fichier.py:10:5: E302 ..."
        errors = re.findall(r"^\S+\.py:\d+:\d+:\s+[EF]\d+", output, re.MULTILINE)
        warnings = re.findall(r"^\S+\.py:\d+:\d+:\s+W\d+", output, re.MULTILINE)
        error_count = len(errors)
        warning_count = len(warnings)

    # ── mypy ────────────────────────────────────────────────────────────────
    if tool in ("mypy", "auto"):
        mypy_errors = re.findall(r":\s+error:", output_lower)
        mypy_notes = re.findall(r":\s+note:", output_lower)
        if mypy_errors:
            error_count = max(error_count, len(mypy_errors))

    # ── eslint ──────────────────────────────────────────────────────────────
    if tool in ("eslint", "auto"):
        m = re.search(r"(\d+)\s+error", output_lower)
        if m:
            error_count = max(error_count, int(m.group(1)))
        m = re.search(r"(\d+)\s+warning", output_lower)
        if m:
            warning_count = max(warning_count, int(m.group(1)))

    # Fichiers affectés
    file_matches = re.findall(r"([\w/\\.]+\.\w+):\d+:", output)
    files_affected = list(dict.fromkeys(file_matches))[:20]  # dedupliqué

    # Détection "clean"
    clean_signals = [
        "all checks passed", "no issues found", "success",
        "0 error", "0 warning", "found 0",
    ]
    all_clean = any(sig in output_lower for sig in clean_signals)
    if not all_clean:
        all_clean = (error_count == 0 and warning_count == 0 and not output.strip())

    detected_tool = tool
    if tool == "auto":
        if "ruff" in output_lower:
            detected_tool = "ruff"
        elif "mypy" in output_lower:
            detected_tool = "mypy"
        elif "eslint" in output_lower:
            detected_tool = "eslint"
        elif "flake8" in output_lower:
            detected_tool = "flake8"
        elif "pylint" in output_lower:
            detected_tool = "pylint"
        else:
            detected_tool = "linter"

    return {
        "error_count": error_count,
        "warning_count": warning_count,
        "files_affected": files_affected,
        "all_clean": all_clean,
        "tool": detected_tool,
    }


async def _test_and_fix_loop(
    llm: Any,
    project_dir: Path,
    test_cmd: str,
    max_iter: int = _TEST_FIX_MAX_ITER,
) -> tuple:
    """
    Boucle autonome : run tests → parse résultats → LLM fixe les échecs → retry.
    Retourne (all_passing: bool, rapport: str, final_stats: dict).
    """
    report: List[str] = []
    final_stats = {}

    for iteration in range(1, max_iter + 1):
        rc, raw_output = await _run_project_cmd(
            test_cmd, project_dir, timeout=120  # tests: timeout plus long
        )

        stats = _parse_test_results(raw_output)
        final_stats = stats
        snippet = raw_output[:1500].strip()

        report.append(
            f"\n**🧪 Itération {iteration}/{max_iter}** — `{test_cmd}`\n"
            f"Résultats : {stats['passed']} passés / {stats['failed']} échoués / "
            f"{stats['errors']} erreurs — framework: {stats['framework']}"
        )
        if snippet:
            report.append(f"```\n{snippet}\n```")

        # ── Succès ──────────────────────────────────────────────────────────
        if stats["all_passing"]:
            report.append(
                f"✅ Tous les tests passent ! ({stats['passed']}/{stats['total']})"
            )
            return True, "\n".join(report), stats

        # ── Rien n'a tourné (collection error, no tests found) ───────────────
        if stats["nothing_ran"] and rc != 0:
            report.append(
                "⚠️ Aucun test n'a pu être collecté — erreur de configuration ou d'import."
            )
            # Traiter comme une erreur et essayer de corriger
            stats["failed"] = 1  # force la correction

        # ── Dernière tentative ───────────────────────────────────────────────
        if iteration == max_iter:
            report.append(
                f"⚠️ {max_iter} itérations sans succès total — "
                f"{stats['passed']}/{stats['total']} tests passent."
            )
            return stats["all_passing"], "\n".join(report), stats

        # ── Collecter le contenu des fichiers pour le LLM ───────────────────
        report.append("🔧 Analyse des échecs et correction automatique…")

        # ── Extraction intelligente depuis la stacktrace ─────────────────────
        # Pondérer les fichiers par fréquence d'apparition dans les erreurs.
        # Un fichier cité 3x dans la stacktrace est plus suspect qu'un cité 1x.
        file_freq: dict = {}

        # Pattern 1 : File "path/to/file.py", line N  (traceback Python)
        for m in re.finditer(
            r'File\s+["\']([^"\']+\.(?:py|js|ts|rs|go|java|cpp|c|cs))["\']',
            raw_output,
        ):
            raw_path = m.group(1).replace("\\", "/")
            p = Path(raw_path)
            if p.is_absolute():
                try:
                    raw_path = str(p.relative_to(project_dir)).replace("\\", "/")
                except ValueError:
                    continue
            if (project_dir / raw_path).exists():
                file_freq[raw_path] = file_freq.get(raw_path, 0) + 2  # poids fort

        # Pattern 2 : FAILED tests/test_foo.py::Class::method
        for m in re.finditer(
            r"FAILED\s+([\w/\\.-]+\.(?:py|js|ts))(?:::|$)", raw_output
        ):
            raw_path = m.group(1).replace("\\", "/")
            if (project_dir / raw_path).exists():
                file_freq[raw_path] = file_freq.get(raw_path, 0) + 1

        # Pattern 3 : chemins relatifs génériques path/to/file.py:N
        for m in re.finditer(
            r"\b([\w][\w/\\-]*\.(?:py|js|ts|rs|go|java|cpp|c|cs))(?::\d+)?",
            raw_output,
        ):
            raw_path = m.group(1).replace("\\", "/")
            if not raw_path.startswith("/") and (project_dir / raw_path).exists():
                file_freq[raw_path] = file_freq.get(raw_path, 0) + 1

        # Trier : fichiers source (non-test) d'abord, puis par fréquence décroissante
        stacktrace_files = sorted(
            file_freq.keys(),
            key=lambda f: (
                1 if ("test" in f.split("/")[0] or f.startswith("test_")) else 0,
                -file_freq[f],
            ),
        )

        # Compléter avec tous les fichiers source du projet (sous-dossiers inclus)
        _src_exts = {".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp", ".c", ".cs", ".jsx", ".tsx"}
        _ignore_dirs = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv", "venv"}
        all_src_files = sorted(
            str(p.relative_to(project_dir)).replace("\\", "/")
            for p in project_dir.rglob("*")
            if p.is_file()
            and p.suffix in _src_exts
            and not any(part in _ignore_dirs for part in p.parts)
        )

        # Ajouter requirements.txt si ModuleNotFoundError détecté
        extra_files: List[str] = []
        if "ModuleNotFoundError" in raw_output or "ImportError" in raw_output:
            for req_name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "package.json"):
                if (project_dir / req_name).exists():
                    extra_files.append(req_name)

        # Fusionner : stacktrace en premier, extras, puis complétion avec src
        priority = list(dict.fromkeys(stacktrace_files + extra_files + all_src_files))[:20]

        files_blob_parts: List[str] = []
        for rel in priority:
            fp = project_dir / rel
            if fp.exists() and fp.is_file():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")[:4000]
                    files_blob_parts.append(f"=== {rel} ===\n{text}")
                except Exception as e:
                    logger.debug(f"Read file {rel}: {e}")
        files_blob = "\n\n".join(files_blob_parts)[:16000]

        failed_list = "\n".join(f"  - {n}" for n in stats["failed_names"]) or "  (voir output ci-dessus)"

        fix_messages = [
            {"role": "system", "content": _TEST_FIX_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Framework : {stats['framework']}\n"
                    f"Commande : `{test_cmd}`\n"
                    f"Répertoire : `{project_dir}`\n\n"
                    f"**Tests échoués :**\n{failed_list}\n\n"
                    f"**Output complet :**\n```\n{raw_output[:5000]}\n```\n\n"
                    f"**Fichiers du projet :**\n{files_blob}"
                ),
            },
        ]

        try:
            fix_response = await llm.chat(
                messages=fix_messages, temperature=0.1, max_tokens=8000
            )
            fix_data = _extract_json(fix_response)
        except Exception as exc:
            report.append(f"⚠️ LLM indisponible : {exc}")
            return False, "\n".join(report), stats

        if not fix_data or "fixes" not in fix_data:
            report.append("⚠️ Réponse LLM non parseable — arrêt.")
            return False, "\n".join(report), stats

        explanation = fix_data.get("explanation", "")
        root_cause = fix_data.get("root_cause", "")
        if explanation:
            report.append(f"  💡 Cause ({root_cause}) : {explanation}")

        applied = 0
        for fix in (fix_data["fixes"] or [])[:6]:
            rel_path = _sanitize_path(fix.get("path", "") or "")
            content = fix.get("content", "") or ""
            if rel_path and content:
                target = project_dir / rel_path
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    report.append(f"  ✏️ {rel_path}")
                    applied += 1
                except Exception as exc:
                    report.append(f"  ❌ {rel_path}: {exc}")

        if applied == 0:
            report.append("⚠️ Aucune correction appliquée — arrêt.")
            return False, "\n".join(report), stats

    return final_stats.get("all_passing", False), "\n".join(report), final_stats


async def _lint_and_fix_loop(
    llm: Any,
    project_dir: Path,
    lint_cmd: str,
    lint_tool: str = "auto",
    max_iter: int = _LINT_FIX_MAX_ITER,
) -> tuple:
    """
    Boucle autonome : run linter → parse → LLM corrige les violations → retry.
    Retourne (all_clean: bool, rapport: str, final_stats: dict).
    """
    report: List[str] = []
    final_stats = {}

    for iteration in range(1, max_iter + 1):
        rc, raw_output = await _run_project_cmd(lint_cmd, project_dir, timeout=60)

        stats = _parse_lint_results(raw_output, lint_tool)
        final_stats = stats
        snippet = raw_output[:1500].strip()

        report.append(
            f"\n**🔍 Itération {iteration}/{max_iter}** — `{lint_cmd}`\n"
            f"Résultats : {stats['error_count']} erreurs / {stats['warning_count']} warnings — outil: {stats['tool']}"
        )
        if snippet:
            report.append(f"```\n{snippet}\n```")

        if stats["all_clean"]:
            report.append("✅ Code propre — aucune violation détectée !")
            return True, "\n".join(report), stats

        if iteration == max_iter:
            report.append(
                f"⚠️ {max_iter} itérations — {stats['error_count']} erreurs restantes."
            )
            return False, "\n".join(report), stats

        report.append("🔧 Correction des violations en cours…")

        # Collecter les fichiers affectés + leur contenu
        files_to_fix = stats["files_affected"]

        # Si rien parsé, prendre tous les .py/.js du projet
        if not files_to_fix:
            files_to_fix = [
                str(p.relative_to(project_dir)).replace("\\", "/")
                for p in project_dir.rglob("*")
                if p.is_file()
                and p.suffix in {".py", ".js", ".ts", ".jsx", ".tsx"}
                and not any(part in ("node_modules", "__pycache__", ".git") for part in p.parts)
            ][:10]

        files_blob_parts: List[str] = []
        for rel in files_to_fix[:10]:
            fp = project_dir / rel
            if not fp.is_absolute():
                fp = project_dir / rel
            if fp.exists() and fp.is_file():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")[:3000]
                    files_blob_parts.append(f"=== {rel} ===\n{text}")
                except Exception as e:
                    logger.debug(f"Read file {rel}: {e}")
        files_blob = "\n\n".join(files_blob_parts)[:10000]

        fix_messages = [
            {"role": "system", "content": _LINT_FIX_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Outil linter : {stats['tool']}\n"
                    f"Commande : `{lint_cmd}`\n"
                    f"Répertoire : `{project_dir}`\n\n"
                    f"**Output linter (erreurs à corriger) :**\n```\n{raw_output[:3000]}\n```\n\n"
                    f"**Contenu actuel des fichiers :**\n{files_blob}"
                ),
            },
        ]

        try:
            fix_response = await llm.chat(
                messages=fix_messages, temperature=0.05, max_tokens=8000
            )
            fix_data = _extract_json(fix_response)
        except Exception as exc:
            report.append(f"⚠️ LLM indisponible : {exc}")
            return False, "\n".join(report), stats

        if not fix_data or "fixes" not in fix_data:
            report.append("⚠️ Réponse LLM non parseable — arrêt.")
            return False, "\n".join(report), stats

        explanation = fix_data.get("explanation", "")
        if explanation:
            report.append(f"  💡 {explanation}")

        applied = 0
        for fix in (fix_data["fixes"] or [])[:8]:
            rel_path = _sanitize_path(fix.get("path", "") or "")
            content = fix.get("content", "") or ""
            if rel_path and content:
                target = project_dir / rel_path
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    report.append(f"  ✏️ {rel_path}")
                    applied += 1
                except Exception as exc:
                    report.append(f"  ❌ {rel_path}: {exc}")

        if applied == 0:
            report.append("⚠️ Aucune correction appliquée — arrêt.")
            return False, "\n".join(report), stats

    return final_stats.get("all_clean", False), "\n".join(report), final_stats


def _auto_detect_test_command(project_dir: Path) -> Optional[str]:
    """
    Détecte automatiquement la commande de test selon le projet.
    Ordre de priorité : pytest > jest > cargo > go > maven > dotnet.
    """
    filenames = {p.name.lower() for p in project_dir.iterdir() if p.is_file()}

    # Python pytest
    if any(f in filenames for f in ("pytest.ini", "setup.cfg", "pyproject.toml")):
        req = "requirements.txt" in filenames
        pip = "pip install -r requirements.txt -q 2>&1 && " if req else ""
        return f"{pip}pytest -v --tb=short 2>&1"
    if any(project_dir.rglob("test_*.py")):
        return "pytest -v --tb=short 2>&1"

    # Node.js / Jest
    if "package.json" in filenames:
        try:
            import json as _j
            pkg = _j.loads((project_dir / "package.json").read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                return "npm install --silent 2>&1 && npm test 2>&1"
        except Exception as e:
            logger.debug(f"Parse package.json scripts: {e}")

    # Rust / Cargo
    if "cargo.toml" in filenames:
        return "cargo test 2>&1"

    # Go
    if any(p.suffix == ".go" for p in project_dir.rglob("*.go")):
        return "go test ./... 2>&1"

    # Java / Maven
    if "pom.xml" in filenames:
        return "mvn test -q 2>&1"

    # .NET
    if any(p.suffix == ".csproj" for p in project_dir.rglob("*.csproj")):
        return "dotnet test 2>&1"

    return None


def _auto_detect_lint_command(project_dir: Path) -> tuple:
    """
    Détecte automatiquement linter + commande selon le projet.
    Retourne (commande, tool_name).
    """
    filenames = {p.name.lower() for p in project_dir.iterdir() if p.is_file()}

    # Python — ruff en priorité (le plus rapide)
    if any(p.suffix == ".py" for p in project_dir.rglob("*.py")):
        if any(f in filenames for f in ("ruff.toml", ".ruff.toml")):
            return "ruff check . 2>&1", "ruff"
        # Toujours proposer ruff pour Python (disponible via pip)
        return "ruff check . --output-format=concise 2>&1", "ruff"

    # JavaScript / TypeScript — eslint
    if any(p.suffix in (".js", ".ts", ".jsx", ".tsx") for p in project_dir.rglob("*")):
        if any(f in filenames for f in (".eslintrc.json", ".eslintrc.js", ".eslintrc.yml")):
            return "npx eslint . 2>&1", "eslint"

    # Rust — clippy
    if "cargo.toml" in filenames:
        return "cargo clippy 2>&1", "clippy"

    return "", "auto"


async def test_and_fix_handler(
    ctx: HandlerContext,
    project_dir: str = "",
    test_command: str = "",
    max_attempts: int = 4,
) -> HandlerResult:
    """
    Lance les tests du projet, parse les résultats (pytest/jest/cargo/go/mocha…),
    demande au LLM de corriger les échecs fichier par fichier, relance.
    Boucle autonome jusqu'à succès (tous passent) ou max_attempts.

    Args:
        project_dir: Répertoire du projet (optionnel, défaut: workspace courant)
        test_command: Commande de test (optionnel, auto-détectée si vide)
        max_attempts: Nombre max d'itérations run→fix (1–6, défaut: 4)
    """
    handler_name = "test_and_fix"
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        return HandlerResult.fail("❌ LLM non disponible", handler_name=handler_name)

    # Résoudre le répertoire
    if project_dir:
        base_dir = ctx.resolve_path(project_dir, want_dir=True)
    else:
        base_dir = ctx.runtime_root

    if not base_dir.exists():
        return HandlerResult.fail(
            f"❌ Répertoire inexistant : {base_dir}", handler_name=handler_name
        )

    max_attempts = max(1, min(int(max_attempts), 6))

    # Auto-détecter la commande si non fournie
    cmd = test_command.strip() if test_command else ""
    if not cmd:
        cmd = _auto_detect_test_command(base_dir)
        if not cmd:
            return HandlerResult.fail(
                "❌ Impossible de détecter automatiquement la commande de test.\n"
                "Fournis `test_command` explicitement (ex: 'pytest -v', 'npm test', 'cargo test').",
                handler_name=handler_name,
            )
        logger.info("[test_and_fix] Commande auto-détectée : {}", cmd)

    logger.info("[test_and_fix] Lancement : '{}' dans '{}'", cmd, base_dir)

    all_passing, report, stats = await _test_and_fix_loop(
        llm=lumena.llm,
        project_dir=base_dir,
        test_cmd=cmd,
        max_iter=max_attempts,
    )

    # ── Persistance du dernier résultat de test ──────────────────────────────
    try:
        from ...utils.paths import LAST_TEST_RESULT_JSON
        _last_test_path = LAST_TEST_RESULT_JSON
        _last_test_path.parent.mkdir(parents=True, exist_ok=True)
        _last_test_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "project_dir": str(base_dir),
                    "test_command": cmd,
                    "all_passing": all_passing,
                    "passed": stats.get("passed", 0),
                    "failed": stats.get("failed", 0),
                    "errors": stats.get("errors", 0),
                    "total": stats.get("total", 0),
                    "framework": stats.get("framework", "?"),
                    "failed_names": stats.get("failed_names", []),
                    "report_snippet": report[:2000] if isinstance(report, str) else "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as _e:
        logger.debug("[test_and_fix] Impossible de sauvegarder last_test_result.json: {}", _e)

    label = "✅ Tous les tests passent" if all_passing else "⚠️ Certains tests échouent encore"
    summary = (
        f"**{label}**\n"
        f"📊 {stats.get('passed', 0)} passés / {stats.get('failed', 0)} échoués / "
        f"{stats.get('errors', 0)} erreurs — framework: {stats.get('framework', '?')}\n"
        f"📁 Projet : `{base_dir}`\n"
        f"🔧 Commande : `{cmd}`"
    )

    return HandlerResult.ok(
        f"{summary}\n\n---\n{report}",
        handler_name=handler_name,
    )


async def lint_and_fix_handler(
    ctx: HandlerContext,
    project_dir: str = "",
    lint_command: str = "",
    lint_tool: str = "auto",
    max_attempts: int = 3,
    fix_warnings: bool = False,
) -> HandlerResult:
    """
    Lance un linter (ruff, flake8, mypy, eslint, clippy…), parse les violations,
    demande au LLM de corriger chaque fichier en faute, relance.
    Boucle autonome jusqu'à code propre ou max_attempts.

    Args:
        project_dir: Répertoire du projet (optionnel, défaut: workspace courant)
        lint_command: Commande linter (optionnel, auto-détectée si vide)
        lint_tool: Nom du linter pour le parsing (ruff|flake8|mypy|eslint|clippy|auto)
        max_attempts: Nombre max d'itérations lint→fix (1–5, défaut: 3)
        fix_warnings: Corriger aussi les warnings (défaut: False, erreurs seulement)
    """
    handler_name = "lint_and_fix"
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        return HandlerResult.fail("❌ LLM non disponible", handler_name=handler_name)

    if project_dir:
        base_dir = ctx.resolve_path(project_dir, want_dir=True)
    else:
        base_dir = ctx.runtime_root

    if not base_dir.exists():
        return HandlerResult.fail(
            f"❌ Répertoire inexistant : {base_dir}", handler_name=handler_name
        )

    max_attempts = max(1, min(int(max_attempts), 5))

    # Auto-détecter linter + commande
    cmd = lint_command.strip() if lint_command else ""
    detected_tool = lint_tool
    if not cmd:
        cmd, detected_tool = _auto_detect_lint_command(base_dir)
        if not cmd:
            return HandlerResult.fail(
                "❌ Impossible de détecter automatiquement le linter.\n"
                "Fournis `lint_command` explicitement (ex: 'ruff check .', 'eslint src/', 'cargo clippy').",
                handler_name=handler_name,
            )
        logger.info("[lint_and_fix] Linter auto-détecté : {} → '{}'", detected_tool, cmd)

    # Ajouter --select W si fix_warnings activé (ruff/flake8)
    if fix_warnings and detected_tool in ("ruff", "flake8") and "--select" not in cmd:
        cmd = cmd + " --select E,W,F"

    logger.info("[lint_and_fix] Lancement : '{}' dans '{}'", cmd, base_dir)

    all_clean, report, stats = await _lint_and_fix_loop(
        llm=lumena.llm,
        project_dir=base_dir,
        lint_cmd=cmd,
        lint_tool=detected_tool,
        max_iter=max_attempts,
    )

    label = "✅ Code propre" if all_clean else "⚠️ Violations restantes"
    summary = (
        f"**{label}**\n"
        f"🔍 {stats.get('error_count', 0)} erreurs / {stats.get('warning_count', 0)} warnings — outil: {stats.get('tool', '?')}\n"
        f"📁 Projet : `{base_dir}`\n"
        f"🔧 Commande : `{cmd}`"
    )

    return HandlerResult.ok(
        f"{summary}\n\n---\n{report}",
        handler_name=handler_name,
    )


# ─── Handler : dernier résultat de test ─────────────────────────────────────

async def get_last_test_failure_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Lit data/last_test_result.json et retourne un résumé du dernier run test_and_fix."""
    handler_name = "get_last_test_failure"
    from ...utils.paths import LAST_TEST_RESULT_JSON
    path = LAST_TEST_RESULT_JSON
    if not path.exists():
        return HandlerResult.ok(
            "Aucun résultat de test enregistré. Lance `test_and_fix` d'abord.",
            handler_name=handler_name,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return HandlerResult.fail(f"Impossible de lire last_test_result.json : {e}", handler_name=handler_name)

    status = "✅ Tous passent" if data.get("all_passing") else "❌ Échecs détectés"
    failed_names = data.get("failed_names", [])
    failed_str = "\n".join(f"  - {n}" for n in failed_names) if failed_names else "  (aucun nommé)"
    lines = [
        f"**Dernier test_and_fix** — {status}",
        f"📅 {data.get('timestamp', '?')}",
        f"📁 Projet : `{data.get('project_dir', '?')}`",
        f"🔧 Commande : `{data.get('test_command', '?')}`",
        f"📊 Passés: {data.get('passed',0)}  Échoués: {data.get('failed',0)}  Erreurs: {data.get('errors',0)}  Total: {data.get('total',0)}  Framework: {data.get('framework','?')}",
    ]
    if not data.get("all_passing"):
        lines.append(f"\n**Tests en échec :**\n{failed_str}")
        snippet = data.get("report_snippet", "")
        if snippet:
            lines.append(f"\n**Extrait du rapport :**\n```\n{snippet[:1500]}\n```")
    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


# ─── Handlers : Jupyter / notebooks ──────────────────────────────────────────

async def read_notebook_handler(
    ctx: HandlerContext,
    path: str = "",
) -> HandlerResult:
    """Lit un fichier .ipynb (Jupyter) et retourne le contenu de toutes les cellules en texte."""
    handler_name = "read_notebook"
    if not path:
        return HandlerResult.fail("Paramètre `path` requis.", handler_name=handler_name)
    nb_path = Path(path)
    if not nb_path.is_absolute():
        nb_path = (ctx.runtime_root / path).resolve()
    if not nb_path.exists():
        return HandlerResult.fail(f"Fichier introuvable : {nb_path}", handler_name=handler_name)
    try:
        import nbformat  # type: ignore
    except ImportError:
        return HandlerResult.fail(
            "Module `nbformat` non installé. Lance : pip install nbformat",
            handler_name=handler_name,
        )
    try:
        nb = nbformat.read(str(nb_path), as_version=4)
    except Exception as e:
        return HandlerResult.fail(f"Erreur de lecture du notebook : {e}", handler_name=handler_name)

    sections: list[str] = [f"# Notebook : {nb_path.name}  ({len(nb.cells)} cellules)\n"]
    for i, cell in enumerate(nb.cells):
        ctype = cell.cell_type  # code | markdown | raw
        source = cell.source or ""
        tag = "python" if ctype == "code" else ctype
        sections.append(f"## Cellule {i} [{ctype}]")
        sections.append(f"```{tag}\n{source}\n```")
        if ctype == "code" and cell.get("outputs"):
            raw_outputs = []
            for out in cell.outputs:
                if out.get("text"):
                    raw_outputs.append("".join(out["text"]))
                elif out.get("data", {}).get("text/plain"):
                    raw_outputs.append("".join(out["data"]["text/plain"]))
            if raw_outputs:
                combined = "\n".join(raw_outputs)[:1000]
                sections.append(f"**Output :**\n```\n{combined}\n```")
    return HandlerResult.ok("\n".join(sections), handler_name=handler_name)


async def edit_notebook_cell_handler(
    ctx: HandlerContext,
    path: str = "",
    cell_index: int = 0,
    new_source: str = "",
    cell_type: str = "code",
) -> HandlerResult:
    """Modifie le contenu d'une cellule d'un notebook .ipynb.

    Paramètres
    ----------
    path        : chemin vers le .ipynb
    cell_index  : index de la cellule (0-based)
    new_source  : nouveau contenu de la cellule
    cell_type   : 'code' | 'markdown' | 'raw'  (utilisé si on crée une nouvelle cellule)
    """
    handler_name = "edit_notebook_cell"
    if not path:
        return HandlerResult.fail("Paramètre `path` requis.", handler_name=handler_name)
    nb_path = Path(path)
    if not nb_path.is_absolute():
        nb_path = (ctx.runtime_root / path).resolve()
    if not nb_path.exists():
        return HandlerResult.fail(f"Fichier introuvable : {nb_path}", handler_name=handler_name)
    try:
        import nbformat  # type: ignore
    except ImportError:
        return HandlerResult.fail(
            "Module `nbformat` non installé. Lance : pip install nbformat",
            handler_name=handler_name,
        )
    try:
        nb = nbformat.read(str(nb_path), as_version=4)
    except Exception as e:
        return HandlerResult.fail(f"Erreur de lecture : {e}", handler_name=handler_name)

    if cell_index < 0 or cell_index >= len(nb.cells):
        return HandlerResult.fail(
            f"cell_index {cell_index} hors limites (0–{len(nb.cells)-1})",
            handler_name=handler_name,
        )
    nb.cells[cell_index]["source"] = new_source
    try:
        nbformat.write(nb, str(nb_path))
    except Exception as e:
        return HandlerResult.fail(f"Erreur d'écriture : {e}", handler_name=handler_name)
    return HandlerResult.ok(
        f"✅ Cellule {cell_index} du notebook `{nb_path.name}` mise à jour.",
        handler_name=handler_name,
    )


# ─── Registration ───────────────────────────────────────────────────────────

def get_project_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des handlers projet pour le registre V2."""
    return [
        HandlerDef(
            name="create_project",
            description=(
                "Crée un projet complet (site web, jeu, app Python, API, etc.) en une seule opération. "
                "Génère l'arborescence et TOUS les fichiers via LLM batch, puis lance le projet "
                "et corrige automatiquement les erreurs (boucle run→fix). "
                "Utiliser pour toute demande de création de projet multi-fichiers."
            ),
            parameters={
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "Description détaillée du projet à créer "
                            "(technologies, nombre de pages, fonctionnalités)"
                        ),
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Nom du projet / dossier (optionnel, sera déduit)",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Répertoire de sortie (optionnel, défaut: workspace/<project_name>)",
                    },
                    "auto_run": {
                        "type": "boolean",
                        "description": (
                            "Lancer automatiquement le projet après création et corriger les erreurs. "
                            "Défaut true. Mettre false pour projets statiques ou si l'utilisateur veut juste les fichiers."
                        ),
                    },
                },
                "required": ["description"],
            },
            handler=create_project_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="dev_run_fix",
            description=(
                "Lance une commande (tests, compilation, linter, build…), analyse l'output, "
                "et si ça plante lit les fichiers concernés, demande au LLM de corriger, "
                "puis relance. Boucle autonome run→analyze→fix jusqu'à succès ou max_attempts. "
                "Utiliser pour : 'lance les tests et corrige', 'compile et règle les erreurs', "
                "'exécute ce script et fixe les bugs', 'fait tourner mon code'."
            ),
            parameters={
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Commande à exécuter (ex: 'pytest tests/', 'python main.py', 'npm test', 'cargo build')",
                    },
                    "project_dir": {
                        "type": "string",
                        "description": "Dossier du projet (optionnel, défaut: workspace courant)",
                    },
                    "max_attempts": {
                        "type": "integer",
                        "description": "Nombre max de tentatives run→fix (1–5, défaut: 3)",
                    },
                },
                "required": ["command"],
            },
            handler=dev_run_fix_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="test_and_fix",
            description=(
                "Lance les tests du projet (pytest, jest, cargo test, go test, mocha…), "
                "parse les résultats, demande au LLM de corriger les fichiers en échec, "
                "puis relance. Boucle autonome test→analyze→fix jusqu'à succès complet ou max_attempts. "
                "Auto-détecte le framework de test si test_command est vide. "
                "Utiliser pour : 'lance les tests et corrige', 'fais passer tous les tests', "
                "'debug les tests qui échouent', 'règle les erreurs de test'."
            ),
            parameters={
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Répertoire du projet (optionnel, défaut: workspace courant)",
                    },
                    "test_command": {
                        "type": "string",
                        "description": (
                            "Commande de test explicite (optionnel, auto-détectée si vide). "
                            "Exemples: 'pytest -v', 'npm test', 'cargo test', 'go test ./...'"
                        ),
                    },
                    "max_attempts": {
                        "type": "integer",
                        "description": "Nombre max d'itérations test→fix (1–6, défaut: 4)",
                    },
                },
                "required": [],
            },
            handler=test_and_fix_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="lint_and_fix",
            description=(
                "Lance un linter (ruff, flake8, mypy, eslint, cargo clippy…), "
                "parse les violations par fichier et ligne, demande au LLM de corriger "
                "chaque fichier en faute, relance. Boucle autonome lint→fix jusqu'à code propre. "
                "Auto-détecte le linter approprié selon le projet (Python→ruff, JS→eslint, Rust→clippy). "
                "Utiliser pour : 'nettoie le code', 'corrige les erreurs ruff/flake8/mypy', "
                "'règle les warnings eslint', 'lint et corrige tout'."
            ),
            parameters={
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Répertoire du projet (optionnel, défaut: workspace courant)",
                    },
                    "lint_command": {
                        "type": "string",
                        "description": (
                            "Commande linter explicite (optionnel, auto-détectée si vide). "
                            "Exemples: 'ruff check .', 'flake8 src/', 'mypy .', 'eslint src/', 'cargo clippy'"
                        ),
                    },
                    "lint_tool": {
                        "type": "string",
                        "description": "Nom du linter pour le parsing : ruff|flake8|mypy|eslint|clippy|auto (défaut: auto)",
                    },
                    "max_attempts": {
                        "type": "integer",
                        "description": "Nombre max d'itérations lint→fix (1–5, défaut: 3)",
                    },
                    "fix_warnings": {
                        "type": "boolean",
                        "description": "Corriger aussi les warnings (défaut: false, erreurs uniquement)",
                    },
                },
                "required": [],
            },
            handler=lint_and_fix_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="get_last_test_failure",
            description=(
                "Retourne le résumé du dernier run `test_and_fix` : statut global, "
                "nombre de tests passés/échoués, noms des tests en échec, extrait du rapport. "
                "Utile pour consulter l'historique sans relancer les tests. "
                "Utiliser pour : 'quels tests ont échoué ?', 'rappelle-moi le dernier résultat de test', "
                "'montre-moi les échecs précédents'."
            ),
            parameters={"properties": {}, "required": []},
            handler=get_last_test_failure_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="read_notebook",
            description=(
                "Lit un fichier Jupyter (.ipynb) et affiche le contenu de toutes les cellules "
                "(code, markdown, raw) avec leurs outputs. "
                "Utiliser pour : 'lis ce notebook', 'montre-moi le contenu du .ipynb', "
                "'analyse ce notebook Jupyter'."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin vers le fichier .ipynb",
                    },
                },
                "required": ["path"],
            },
            handler=read_notebook_handler,
            category="project",
            source_module="handlers.project",
        ),
        HandlerDef(
            name="edit_notebook_cell",
            description=(
                "Modifie le contenu d'une cellule d'un notebook Jupyter (.ipynb). "
                "Utiliser pour : 'modifie la cellule 3 du notebook', 'mets à jour ce code dans le .ipynb', "
                "'remplace la cellule 0 par ce code'."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin vers le fichier .ipynb",
                    },
                    "cell_index": {
                        "type": "integer",
                        "description": "Index de la cellule à modifier (0-based)",
                    },
                    "new_source": {
                        "type": "string",
                        "description": "Nouveau contenu de la cellule",
                    },
                },
                "required": ["path", "cell_index", "new_source"],
            },
            handler=edit_notebook_cell_handler,
            category="project",
            source_module="handlers.project",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
