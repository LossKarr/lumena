"""
ToolRegistry — Registre central des outils pour LUMENA.

Extrait de react.py pour ameliorer la lisibilite et la maintenabilite.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path
from loguru import logger
import asyncio
import json
import os
import re
import unicodedata
import difflib
from time import perf_counter

from .react_config import (
    IS_WINDOWS, OS_NAME,
    WorkspaceFileGuardrails, Observation,
    TELEMETRY_AVAILABLE, publish_trace,
    current_trace_context, get_file_edits_store,
    compute_workspace_relative, get_current_runtime_context,
)
from .caller_context import CallerContext, UNKNOWN as _CALLER_UNKNOWN
from .file_categories import requires_codeagent as _requires_codeagent, CONFIG_FILENAMES as _CONFIG_FILENAMES
from .tool_categories import get_category_contract, get_semantic_category


# ──────────────────────────────────────────────────────────────────────
# Policy : délégation forcée vers CodeAgent pour mutations de code/config
# ──────────────────────────────────────────────────────────────────────

# Outils qui MUTENT l'état (écriture fichier, exécution shell, suppression).
# Leur appel par ReAct sur un fichier code/config de projet doit être refusé.
_MUTATE_TOOLS_CODE: frozenset[str] = frozenset({
    "write_file", "edit_file", "multi_edit_file", "apply_patch", "apply_patches",
    "insert_at_anchor", "edit_by_lines", "str_replace",
    "delete_file", "delete_directory", "create_directory",
    "run_command", "run_shell", "exec_command",
    "write_website_files",
})


def _strict_mode() -> str:
    """Lit le flag env `LUMENA_STRICT_CODE_DELEGATION` : enforce|warn|off."""
    val = (os.getenv("LUMENA_STRICT_CODE_DELEGATION", "enforce") or "enforce").strip().lower()
    if val not in ("enforce", "warn", "off"):
        val = "enforce"
    return val


def _react_allow_project_shell() -> bool:
    """Autorise ReAct à exécuter des commandes shell dans un projet suivi. Défaut : activé."""
    raw = (os.getenv("LUMENA_REACT_ALLOW_PROJECT_SHELL", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


_PEER_TEAM_INTENT_KEYWORDS: frozenset[str] = frozenset({
    "autre lumena", "l'autre lumena", "lautre lumena", "lumena salon",
    "autre instance", "instance salon", "pair lumena", "pairs lumena",
    "collègue lumena", "collegue lumena", "équipe lumena", "equipe lumena",
    "demande lui", "demande-lui", "demande à l'autre", "demande a l'autre",
    "fais vérifier", "fais verifier", "fait vérifier", "fait verifier",
    "répartis", "repartis", "délègue", "delegue", "déléguer", "deleguer",
    "inter-lumena", "inter instance", "inter-instance",
})

_PEER_RAW_NETWORK_TOOLS: frozenset[str] = frozenset({
    "http_request", "web_fetch", "browser_navigate", "browser_open",
    "browser_open_tab", "run_command",
})

_IONOS_DB_BYPASS_TOOLS: frozenset[str] = frozenset({
    "find_files", "list_directory", "list_files", "list_dir",
    "read_file", "read_files_batch", "grep_search", "search_files", "search_code",
    "write_file", "create_file", "edit_file", "multi_edit_file", "apply_patch",
    "apply_patches", "insert_at_anchor", "edit_by_lines", "str_replace",
    "delete_file", "delete_directory", "create_directory",
    "run_command", "run_shell", "exec_command",
    "delegate_task", "delegate_task_bg", "delegate_to_codeagent",
})

_IONOS_DB_ALLOWED_NON_DB_TOOLS: frozenset[str] = frozenset({
    "final_answer", "ask_user",
})


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _has_db_word(low: str, terms: tuple[str, ...]) -> bool:
    """True si l'un des termes apparait comme MOT ENTIER (pluriel toléré).

    Évite les faux positifs en sous-chaîne : « table » ne doit pas matcher
    « tableau » ni « comptable ». `low` est supposé déjà sans accents.
    """
    for term in terms:
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"s?(?![a-z0-9])", low):
            return True
    return False


# Extensions de fichiers (code/web/doc) signalant une édition de fichier plutôt
# qu'une opération BDD. Les TLD de domaines (.com/.fr/.io) en sont volontairement
# absents pour ne pas confondre un nom de site avec un fichier.
_FILE_EXT_SIGNAL = re.compile(
    r"\.(?:html?|css|scss|sass|less|js|mjs|cjs|ts|tsx|jsx|vue|svelte|astro|"
    r"php|py|rb|go|rs|java|json|md|mdx|txt|xml|yml|yaml|toml|ini|svg)"
    r"(?![a-z0-9])"
)


def _configured_ionos_site_terms() -> tuple[str, ...]:
    """Return configured IONOS site names without exposing credentials."""
    terms: set[str] = set()
    default_site = (os.getenv("LUMENA_IONOS_DEFAULT_SITE", "") or "").strip().lower()
    if default_site:
        terms.add(default_site)

    try:
        sites_path = Path("data/ionos_sites.json")
        if sites_path.exists():
            raw = json.loads(sites_path.read_text(encoding="utf-8"))
            sites = raw.get("sites", {}) if isinstance(raw, dict) else {}
            if isinstance(sites, dict):
                for domain in sites.keys():
                    if domain:
                        terms.add(str(domain).strip().lower())
    except Exception:
        # Detection is best-effort; never block normal tooling because config
        # metadata cannot be read.
        pass

    return tuple(sorted(t for t in terms if t))


def _mentions_configured_ionos_site(low: str, configured_sites: tuple[str, ...] | None = None) -> bool:
    sites = configured_sites if configured_sites is not None else _configured_ionos_site_terms()
    if not sites:
        return False

    tokens = re.findall(r"[a-z0-9][a-z0-9.-]{3,}", low)
    for site in sites:
        site_low = _strip_accents(site.lower())
        label = site_low.split(".", 1)[0]
        if site_low and site_low in low:
            return True
        if label and len(label) >= 5 and label in low:
            return True
        for token in tokens:
            token_label = token.split(".", 1)[0]
            if len(token_label) >= 5 and len(label) >= 5:
                if difflib.SequenceMatcher(None, token_label, label).ratio() >= 0.86:
                    return True
    return False


def _ionos_reference_is_exclusion_only(low: str) -> bool:
    """True when IONOS is mentioned only as an explicit boundary to avoid."""
    if "ionos" not in low and not _mentions_configured_ionos_site(low):
        return False

    exclusion_patterns = (
        r"\bne\s+(?:touche|touches|lis|lire|utilise|utiliser|connecte|deploie|deploye|publie)"
        r"[^.\n!?]{0,100}\bionos\b",
        r"\bne\s+touche\s+pas\s+aux?\s+bdd\s+ionos\b",
        r"\bsans\s+(?:rien\s+)?(?:faire\s+sur\s+)?ionos\b",
        r"\bpas\s+(?:de\s+)?(?:bdd\s+)?ionos\b",
        r"\bhors\s+ionos\b",
    )
    if not any(re.search(pattern, low) for pattern in exclusion_patterns):
        return False

    positive_patterns = (
        r"\b(?:sur|via|chez|dans|depuis)\s+ionos\b",
        r"\bhosting-data\.io\b",
        r"\bwebspace-host\.com\b",
    )
    return not any(re.search(pattern, low) for pattern in positive_patterns)


def _looks_like_ionos_db_intent(text: str, configured_sites: tuple[str, ...] | None = None) -> bool:
    """Detecte une demande BDD IONOS explicite pour forcer le bridge securise."""
    if not text:
        return False
    low = _strip_accents(str(text).lower())
    if _ionos_reference_is_exclusion_only(low):
        return False
    db_terms = (
        "bdd", "base de donnees", "base de donner", "database",
        "mysql", "mariadb", "schema", "table",
    )
    ionos_terms = ("ionos", "hosting-data.io", "webspace-host.com")
    strong_db_terms = (
        "bdd", "base de donnees", "base de donner", "database", "mysql", "mariadb",
    )
    table_mutation_terms = (
        "cree", "creer", "ajoute", "ajouter", "rajoute", "rajouter",
        "nouvelle table", "create table", "supprime", "modifier", "modifie",
    )
    # Matching par MOT ENTIER : « table » ne doit pas matcher « tableau » /
    # « comptable » (faux positifs qui bloquaient l'édition web du site).
    has_db = _has_db_word(low, db_terms)
    has_ionos = any(term in low for term in ionos_terms)
    has_configured_site = _mentions_configured_ionos_site(low, configured_sites)
    has_strong_db = _has_db_word(low, strong_db_terms)
    has_table_mutation = _has_db_word(low, ("table",)) and _has_db_word(low, table_mutation_terms)

    # Un fichier explicitement nommé (.html/.css/.php/.py…) signale une édition
    # de fichier, pas une opération BDD — sauf si un terme BDD FORT est présent
    # (« table mysql dans index.html » reste une intention BDD). L'accès aux
    # fichiers de config sensibles (config.php/.env) reste couvert séparément
    # par _looks_like_ionos_config_access.
    if _FILE_EXT_SIGNAL.search(low) and not has_strong_db:
        return False

    return (has_db or has_strong_db or has_table_mutation) and (has_ionos or has_configured_site)


def _looks_like_ionos_config_access(text: str, configured_sites: tuple[str, ...] | None = None) -> bool:
    """Detecte un acces fichier/shell visant la config d'un site IONOS configure."""
    if not text:
        return False
    low = _strip_accents(str(text).lower().replace("\\", "/"))
    if not _mentions_configured_ionos_site(low, configured_sites):
        return False
    config_terms = (
        ".env", "config.php", "config.local.php", "config.local", "bootstrap.php",
        "db_host", "db_name", "db_user", "db_pass", "mysql", "mariadb",
        "hosting-data.io", "database", "bdd",
    )
    return any(term in low for term in config_terms)


def _is_peer_team_query(query_lower: str) -> bool:
    """Détecte les demandes naturelles de collaboration entre Lumena."""
    if not query_lower:
        return False
    if any(kw in query_lower for kw in _PEER_TEAM_INTENT_KEYWORDS):
        return True
    # Formulation courte fréquente après un tour où "l'autre Lumena" est déjà en contexte.
    return (
        ("demande" in query_lower or "dit lui" in query_lower or "dis lui" in query_lower)
        and ("lui" in query_lower or "l'autre" in query_lower or "lautre" in query_lower)
    )


# ── Exception micro-fix : éditions locales et bornées autorisées pour ReAct ──
# Outils d'édition ciblés uniquement — write_file et delete_file exclus.
_MICRO_FIX_TOOLS: frozenset[str] = frozenset({
    "edit_file", "str_replace", "edit_by_lines", "apply_patch",
})
_MICRO_FIX_MAX_LINES: int = 30  # budget maximum de lignes modifiées


def _estimate_change_lines(name: str, args: Dict[str, Any]) -> Optional[int]:
    """Estime le nombre de lignes modifiées par un appel outil. None = non estimable."""
    if not isinstance(args, dict):
        return None
    if name == "str_replace":
        old = str(args.get("old_str", "") or "")
        new = str(args.get("new_str", "") or "")
        return max(len(old.splitlines()), len(new.splitlines()), 1)
    if name == "edit_by_lines":
        changes = args.get("changes", [])
        if not isinstance(changes, list):
            return None
        total = sum(
            len(str(c.get("content", "")).splitlines()) or 1
            for c in changes
            if isinstance(c, dict)
        )
        return total or len(changes)
    if name == "apply_patch":
        patch = str(args.get("patch", "") or "")
        added = sum(1 for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        return max(added, removed, 1)
    if name == "edit_file":
        content = str(args.get("content", "") or "")
        return len(content.splitlines()) or 1
    return None


def _is_react_micro_fix(name: str, args: Dict[str, Any], path_str: str) -> bool:
    """True si l'opération est un micro-fix local borné autorisé pour ReAct.

    Critères stricts :
    - Outil d'édition ciblé (edit_file / str_replace / edit_by_lines / apply_patch)
    - Contenu modifié ≤ _MICRO_FIX_MAX_LINES lignes
    - Pas un fichier build/config critique (Dockerfile, package.json, pyproject.toml, …)

    Si l'un des critères n'est pas satisfait → False → délégation CodeAgent inchangée.
    """
    if name not in _MICRO_FIX_TOOLS:
        return False
    if Path(path_str).name in _CONFIG_FILENAMES:
        return False
    size = _estimate_change_lines(name, args)
    if size is None or size > _MICRO_FIX_MAX_LINES:
        return False
    return True


def _extract_path_from_args(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Extrait le chemin cible depuis les arguments d'un outil muteur.

    Selon l'outil, le path vit sous des clés différentes (path, file_path, cwd,
    command…). On retourne la meilleure candidate, ou None si rien d'exploitable.
    """
    if not isinstance(args, dict):
        return None
    # Chemins directs
    for key in ("path", "file_path", "target", "destination", "filepath"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Listes de chemins (multi_edit_file, apply_patch)
    for key in ("paths", "files", "file_paths"):
        v = args.get(key)
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for sub in ("path", "file_path"):
                    if isinstance(first.get(sub), str):
                        return first[sub]
    # Shell : cwd d'abord, puis premier token-fichier de la commande
    if tool_name in ("run_command", "run_shell", "exec_command"):
        cwd = args.get("cwd") or args.get("working_dir") or args.get("directory")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
        cmd = args.get("command") or args.get("cmd") or args.get("shell")
        if isinstance(cmd, str) and cmd.strip():
            # Rechercher un chemin de type workspace/... ou slug/ dans la commande
            m = re.search(r"([A-Za-z]:[\\/][^\s;&|<>]+|workspace[\\/][\w\-./\\]+)", cmd)
            if m:
                return m.group(0)
            # Sinon : premier argument qui ressemble à un chemin fichier
            for token in cmd.split():
                if ("/" in token or "\\" in token or "." in token) and not token.startswith("-"):
                    return token
    return None



class _FallbackToolSearch:
    """Keyword search fallback si chromadb n'est pas installé."""

    def __init__(self, tools):
        self._tools = tools

    def query(self, query_texts, n_results=5):
        query_lower = query_texts[0].lower()
        keywords = query_lower.split()
        scored = []
        for name, tool in self._tools.items():
            desc = tool.get("description", "").lower()
            score = sum(1 for kw in keywords if kw in name or kw in desc)
            if score > 0:
                scored.append((score, name, tool.get("description", "")[:120]))
        scored.sort(key=lambda x: -x[0])
        results = scored[:n_results]
        return {
            "documents": [[desc for _, _, desc in results]],
            "metadatas": [[{"name": n} for _, n, _ in results]],
        }


class ToolRegistry:
    """
    Registre des outils disponibles pour LUMENA.
    """
    
    def __init__(
        self,
        lumena=None,
        lumena_root: Optional[Path] = None,
        ide_context: Optional[Dict[str, Any]] = None,
    ):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.lumena = lumena  # Reference a LumenaCore pour acces a memory, etc.
        self.lumena_root = (lumena_root or Path(__file__).parent.parent.parent).resolve()
        self.default_workspace_root = self._resolve_default_workspace_root()
        self.default_workspace_root.mkdir(parents=True, exist_ok=True)
        self.ide_context = self._normalize_ide_context(ide_context or {})
        self.runtime_root = self._get_effective_root()
        # BUGFIX: utiliser lumena_root (lumena/) comme racine des guardrails, pas runtime_root
        # (lumena/workspace/). Sinon lumena_root / "workspace" = lumena/workspace/workspace/
        # ce qui provoque le bug du double-workspace dans write_file et list_directory.
        self.file_guardrails = WorkspaceFileGuardrails(self.lumena_root)
        self._mail_hub_instance = None
        self._critical_alert_hub_instance = None
        self._web_crawler_instance = None
        self._document_hub_instance = None
        self._search_hub_instance = None
        self._spotify_hub_instance = None
        self._notion_hub_instance = None
        self._opened_apps_history: List[str] = []
        # Cache d'observations intra-session : évite les appels redondants (même outil + mêmes args)
        self._observation_cache: Dict[str, str] = {}
        self._OBS_CACHE_MAX = 200  # LRU simple — évite la fuite mémoire sur daemon 24/7
        # P2: Compteur de hits par clé — force une invalidation après N hits pour éviter
        # qu'un LLM voie la même réponse en boucle et se convainque qu'un fichier est vide.
        self._observation_cache_hits: Dict[str, int] = {}
        self._OBS_CACHE_MAX_HITS = 2  # Au-delà de 2 hits consécutifs → relecture fraîche forcée
        self._CACHEABLE_TOOLS = {
            "list_directory", "get_time", "read_file",
            "memory_search", "memory_stats", "memory_get", "read_journal",
            "list_journal_dates", "search_journal",
            "view_outline", "get_agents_status", "get_my_capabilities",
            # OSINT (lecture seule, résultats stables en session)
            "ip_info", "osint_scan", "domain_recon", "email_check",
            "whois_lookup", "ssl_check", "subdomain_enum", "http_headers_check",
            "threat_check", "port_scan", "reverse_dns", "tech_detect", "wayback_check",
        }
        # Cache de description des outils (rebuild coûteux sur 241 tools, invalidé sur register())
        self._tools_desc_cache: Optional[str] = None
        # Cache de signatures handlers — pre-populé dans _load_v2_handlers() pour éviter
        # inspect.signature() dans la boucle hot execute() : (has_var_keyword, valid_params)
        self._sig_cache: Dict[str, tuple] = {}
        # [Phase 7] Legacy handlers supprimés — V2 est la seule source de tools

        # Phase 4.3: Mapping tool_name → module_category pour filtrage contextuel
        self._tool_modules: Dict[str, str] = {}
        # Filtre optionnel : si défini, seuls ces outils apparaissent dans get_tools_description()
        # et sont exécutables. Utilisé par think_and_act_silent pour alléger le prompt.
        self._allowed_tools: Optional[set] = None
        # True quand _allowed_tools a été défini par l'appelant (pas le filtre contextuel)
        self._caller_set_allowed: bool = False
        # Contexte issu du filtre: une demande BDD IONOS doit rester sur ionos_db_*.
        self._ionos_db_context: bool = False
        self._ionos_db_context_query: str = ""
        # Compteur de hard-blocks IONOS pour escalader le message (anti-boucle de contournement).
        self._ionos_db_block_count: int = 0
        # P0: Modules handlers dont l'import ou l'exécution du getter a échoué
        self._failed_modules: List[str] = []

        # ── Phase 7: Chargement handlers fragmentés V2 (obligatoire) ─────
        self._load_v2_handlers()

    # ── Phase 7 + P0: chargement résilient des handlers V2 ────────────────
    def _load_v2_handlers(self) -> None:
        """Charge tous les handlers V2 — résilient aux imports/getters cassés."""
        # ── INFRA CRITIQUE — si ça échoue, Lumena ne peut pas démarrer ──
        try:
            from .handlers.context import HandlerContext
            from .handlers.registry_v2 import HandlerRegistryV2
        except Exception as exc:
            logger.critical(f"[FATAL] Handler infrastructure import failed: {exc}")
            raise RuntimeError(f"Impossible de charger l'infra handlers: {exc}") from exc

        # ── MODULES HANDLERS — chargement individuel, skip on failure ──
        _HANDLER_MODULES = [
            (".handlers.files",          "get_file_handler_defs",          "files"),
            (".handlers.system",         "get_system_handler_defs",        "system"),
            (".handlers.web",            "get_web_handler_defs",           "web"),
            (".handlers.memory",         "get_memory_handler_defs",        "memory"),
            (".handlers.browser",        "get_browser_handler_defs",       "browser"),
            (".handlers.computer_use",   "get_computer_use_handler_defs",  "computer_use"),
            (".handlers.skills",         "get_skills_handler_defs",        "skills"),
            (".handlers.agents",         "get_agents_handler_defs",        "agents"),
            (".handlers.mail",           "get_mail_handler_defs",          "mail"),
            (".handlers.documents",      "get_documents_handler_defs",     "documents"),
            (".handlers.spotify",        "get_spotify_handler_defs",       "spotify"),
            (".handlers.notion",         "get_notion_handler_defs",        "notion"),
            (".handlers.project",        "get_project_handler_defs",       "project"),
            (".handlers.git",            "get_git_handler_defs",           "git"),
            (".handlers.github",         "get_github_handler_defs",        "github"),
            (".handlers.autonomy",       "get_autonomy_handler_defs",      "autonomy"),
            (".handlers.security",       "get_security_handler_defs",      "security"),
            (".handlers.custom",         "get_custom_tool_handler_defs",   "custom"),
            (".handlers.heartbeat_self", "get_heartbeat_self_handler_defs","autonomy"),
            (".handlers.discord_admin",  "get_discord_admin_handler_defs", "discord"),
            (".handlers.perception",     "get_perception_handler_defs",    "documents"),
            (".handlers.osint",          "get_osint_handler_defs",         "security"),
            (".handlers.network",        "get_network_handler_defs",       "network"),
            (".handlers.http_api",       "get_http_api_handler_defs",      "web"),
            (".handlers.plans",          "get_plans_handler_defs",         "autonomy"),
            (".handlers.website",        "get_website_handler_defs",       "website"),
            (".handlers.lsp",            "get_lsp_handler_defs",           "lsp"),
            (".handlers.codebase",       "get_codebase_handler_defs",      "codebase"),
            (".handlers.ide",            "get_ide_handler_defs",           "ide"),
            (".handlers.config_manager", "get_config_manager_handler_defs","system"),
            (".handlers.twitter",        "get_twitter_handler_defs",       "social"),
            (".handlers.stripe_api",     "get_stripe_api_handler_defs",    "stripe"),
            (".handlers.n8n",            "get_n8n_handler_defs",           "automation"),
            (".handlers.remotion",       "get_video_handler_defs",         "video"),
            (".handlers.ionos",          "get_ionos_handler_defs",         "ionos"),
            (".handlers.image_gen",     "get_image_gen_handler_defs",     "image"),
            (".handlers.batch",          "get_batch_handler_defs",         "files"),
            (".handlers.peer_delegation","get_peer_delegation_handler_defs","peers"),
            (".handlers.peer_knowledge", "get_peer_knowledge_handler_defs",  "peers"),
            (".handlers.peer_tasks",    "get_peer_tasks_handler_defs",      "peers"),
            (".handlers.peer_orchestrator","get_peer_orchestrator_handler_defs","peers"),
            (".handlers.datagouv",      "get_datagouv_handler_defs",      "data"),
            (".handlers.data_workbench","get_data_workbench_handler_defs","data"),
            (".handlers.sirene",        "get_sirene_handler_defs",        "data"),
            (".handlers.geo_gouv",      "get_geo_gouv_handler_defs",      "data"),
        ]

        import importlib
        _loaded_getters: Dict[str, tuple] = {}  # getter_name → (callable, category)

        for mod_path, getter_name, category in _HANDLER_MODULES:
            try:
                mod = importlib.import_module(mod_path, package=__package__)
                getter = getattr(mod, getter_name)
                _loaded_getters[getter_name] = (getter, category)
            except Exception as exc:
                self._failed_modules.append(mod_path)
                logger.error(f"[Handler] Skip {mod_path}.{getter_name}: {exc}")

        if self._failed_modules:
            logger.warning(
                f"[Phase7] {len(self._failed_modules)}/{len(_HANDLER_MODULES)} "
                f"handler modules failed to import: {self._failed_modules}"
            )

        # ── Playwright conditionnel (préserve la logique existante) ──
        _pw_available = False
        try:
            from ..tools.playwright_browser import PLAYWRIGHT_AVAILABLE
            _pw_available = PLAYWRIGHT_AVAILABLE
        except Exception:
            pass

        # ── Construction du module_map ──
        ctx = HandlerContext.from_tool_registry(self)
        v2 = HandlerRegistryV2()

        _module_map = []
        for getter_name, (getter, category) in _loaded_getters.items():
            if category == "browser" and not _pw_available:
                logger.info("🌐 Playwright non disponible — outils browser masqués du prompt")
                continue
            _module_map.append((getter, category))

        # ── Enregistrement (catch Exception — un getter crashant ne tue pas le loading) ──
        for getter, module_cat in _module_map:
            try:
                defs = getter()
                v2.register_many(defs)
                for hdef in defs:
                    self._tool_modules[hdef.name] = module_cat
            except ValueError:
                # Doublon attendu (screenshot dans system + computer_use)
                for hdef in defs:
                    if not v2.has(hdef.name):
                        v2.register(hdef)
                    self._tool_modules[hdef.name] = module_cat
            except Exception as exc:
                # Getter importé OK mais exécution échoue (AttributeError, TypeError, etc.)
                self._failed_modules.append(f"{getter.__module__}.{getter.__name__}")
                logger.error(f"[Handler] Getter {getter.__name__} crashed: {exc}")

        # ── Legacy dict ──
        legacy_v2 = v2.to_legacy_tools_dict(ctx)
        self.tools.update(legacy_v2)

        # ── Pre-populer le cache de signatures (évite inspect.signature() dans execute() hot-path) ──
        import inspect as _inspect_sig
        for _tname, _tdef in self.tools.items():
            _h = _tdef.get("handler")
            if _h is None:
                self._sig_cache[_tname] = (True, None)
                continue
            try:
                _sig = _inspect_sig.signature(_h)
                _hv = any(
                    p.kind == _inspect_sig.Parameter.VAR_KEYWORD
                    for p in _sig.parameters.values()
                )
                _vp = None if _hv else frozenset(_sig.parameters.keys())
                self._sig_cache[_tname] = (_hv, _vp)
            except Exception:
                self._sig_cache[_tname] = (True, None)

        # ── parallel_tools wrapper ──
        if "parallel_tools" in self.tools:
            from .handlers.system import parallel_tools_handler as _pt_handler
            _self_execute = self.execute

            async def _parallel_tools_wrapper(**kw):
                filtered = {}
                if "tool_calls" in kw:
                    filtered["tool_calls"] = kw["tool_calls"]
                elif len(kw) == 1:
                    only_key = next(iter(kw))
                    val = kw[only_key]
                    if isinstance(val, list):
                        logger.warning(f"parallel_tools: arg '{only_key}' interprété comme tool_calls")
                        filtered["tool_calls"] = val
                if "tool_calls" not in filtered and kw:
                    _bad_args = [k for k in kw.keys() if k != "execute_fn"]
                    logger.warning(f"parallel_tools: args directs détectés ({_bad_args}) — format tool_calls requis")
                    _err = (
                        f"Erreur: parallel_tools attend tool_calls=[{{\"name\": \"outil\", \"args\": {{...}}}}]. "
                        f"Tu as envoyé des args directs ({_bad_args}). "
                        f"Appelle l'outil directement (ex: ACTION: discord_send) au lieu de parallel_tools."
                    )
                    return Observation(content=_err, success=False)
                filtered["execute_fn"] = _self_execute
                result = await _pt_handler(ctx, **filtered)
                # Transmettre l'Observation structurée directement (sub_results peuplés)
                return Observation(
                    content=result.output,
                    success=result.success,
                    sub_results=result.sub_results,
                )

            self.tools["parallel_tools"]["handler"] = _parallel_tools_wrapper

        self._v2_registry = v2
        self._v2_context = ctx

        logger.info(
            f"loaded: {v2.count} handlers "
            f"({len(legacy_v2)} tools registered)"
            + (f" ({len(self._failed_modules)} modules skipped)" if self._failed_modules else "")
        )

        # ── P1.5: discover_tools — recherche sémantique d'outils (ChromaDB) ──
        self._tool_collection = None  # ChromaDB collection, lazy init

        async def _discover_tools_handler(query: str, max_results: int = 5) -> str:
            """Recherche sémantique dans TOUS les outils disponibles par description."""
            if self._tool_collection is None:
                self._init_tool_index()

            results = self._tool_collection.query(
                query_texts=[query],
                n_results=min(max_results, 10),
            )

            if not results["documents"] or not results["documents"][0]:
                return "Aucun outil trouvé pour cette recherche."

            found = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                name = meta["name"]
                found.append(f"- {name}: {doc[:120]}")
                if self._allowed_tools is not None:
                    self._allowed_tools.add(name)
                    self._tools_desc_cache = None

            return f"{len(found)} outils trouvés et ajoutés:\n" + "\n".join(found)

        self.tools["discover_tools"] = {
            "name": "discover_tools",
            "description": "Recherche sémantique dans tous les outils disponibles. "
                           "Utilise cet outil quand tu as besoin d'une capability non listée.",
            "parameters": {
                "query": {"type": "string", "description": "Description de ce que tu veux faire"},
                "max_results": {"type": "integer", "description": "Nombre max de résultats (défaut: 5)"},
            },
            "required": ["query"],
            "handler": _discover_tools_handler,
        }
        self._tool_modules["discover_tools"] = "system"

    # ── P1.6: Index vectoriel des outils ──
    def _init_tool_index(self) -> None:
        """Construit un index ChromaDB des descriptions d'outils (one-shot au 1er appel)."""
        try:
            import chromadb
        except ImportError:
            logger.warning("[discover_tools] chromadb non installé — fallback keyword")
            self._tool_collection = _FallbackToolSearch(self.tools)
            return

        client = chromadb.Client()  # In-memory, pas persistent
        collection = client.get_or_create_collection(
            name="lumena_tools",
            metadata={"hnsw:space": "cosine"},
        )

        ids, docs, metas = [], [], []
        for name, tool in self.tools.items():
            if name == "discover_tools":
                continue
            desc = tool.get("description", name)
            params = tool.get("parameters", {})
            param_names = ", ".join(params.keys()) if params else ""
            full_text = f"{name}({param_names}): {desc}"
            ids.append(name)
            docs.append(full_text)
            metas.append({"name": name, "category": self._tool_modules.get(name, "unknown")})

        collection.add(ids=ids, documents=docs, metadatas=metas)
        self._tool_collection = collection
        logger.info(f"[discover_tools] Tool index built: {len(ids)} tools indexed (in-memory ChromaDB)")

    def _resolve_default_workspace_root(self) -> Path:
        raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = (self.lumena_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            return candidate
        from ..utils.paths import WORKSPACE_DIR
        return WORKSPACE_DIR.resolve()

    @staticmethod
    def _normalize_existing_dir(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        try:
            p = Path(str(path)).expanduser().resolve()
        except Exception:
            return None
        return str(p) if p.exists() and p.is_dir() else None

    @staticmethod
    def _normalize_existing_file(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        try:
            p = Path(str(path)).expanduser().resolve()
        except Exception:
            return None
        return str(p) if p.exists() and p.is_file() else None

    @staticmethod
    def _normalize_existing_parent_dir(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        try:
            p = Path(str(path)).expanduser().resolve()
        except Exception:
            return None

        current = p if p.is_dir() else p.parent
        for _ in range(8):
            if current.exists() and current.is_dir():
                return str(current)
            if current.parent == current:
                break
            current = current.parent
        return None

    @staticmethod
    def _infer_workspace_from_file_paths(
        active_file_path: Optional[str],
        open_files: List[str],
    ) -> Optional[str]:
        markers = (
            ".git",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "composer.json",
        )
        candidates: List[str] = []
        if active_file_path:
            candidates.append(active_file_path)
        candidates.extend(open_files[:30])

        for raw in candidates:
            normalized = ToolRegistry._normalize_existing_file(raw)
            if not normalized:
                continue
            current = Path(normalized).parent
            for _ in range(10):
                if any((current / marker).exists() for marker in markers):
                    return str(current)
                if current.parent == current:
                    break
                current = current.parent
            return str(Path(normalized).parent)
        return None

    def _normalize_ide_context(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        workspace_path = self._normalize_existing_dir(raw.get("workspace_path"))
        workspace_hint_parent = self._normalize_existing_parent_dir(raw.get("workspace_path"))
        active_file_path = self._normalize_existing_file(raw.get("active_file_path"))
        open_files: List[str] = []
        for item in (raw.get("open_files") or [])[:30]:
            normalized = self._normalize_existing_file(item)
            if normalized:
                open_files.append(normalized)

        if not workspace_path:
            workspace_path = self._infer_workspace_from_file_paths(active_file_path, open_files)
        if not workspace_path:
            workspace_path = workspace_hint_parent

        return {
            "workspace_path": workspace_path,
            "active_file_path": active_file_path,
            "open_files": open_files,
        }

    def _is_ide_runtime(self) -> bool:
        return bool(self.ide_context.get("workspace_path"))

    def _get_effective_root(self) -> Path:
        workspace = self.ide_context.get("workspace_path")
        if workspace:
            try:
                p = Path(workspace).resolve()
                if p.exists() and p.is_dir():
                    return p
            except Exception:
                pass  # chemin invalide, on essaie le suivant
        self.default_workspace_root.mkdir(parents=True, exist_ok=True)
        return self.default_workspace_root

    @staticmethod
    def _get_env_int(key: str, default: int, minimum: int = 1) -> int:
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
            return max(minimum, value)
        except (TypeError, ValueError):
            return default

    def _ide_read_page_size(self) -> int:
        return self._get_env_int("LUMENA_IDE_READ_LINES", 200000, minimum=1000)

    def _ide_list_max_items(self) -> int:
        return self._get_env_int("LUMENA_IDE_LIST_ITEMS", 20000, minimum=200)

    def _ide_find_max_results(self) -> int:
        return self._get_env_int("LUMENA_IDE_FIND_RESULTS", 20000, minimum=200)

    def _ide_command_timeout_sec(self) -> Optional[int]:
        raw = os.getenv("LUMENA_IDE_COMMAND_TIMEOUT_SEC")
        if raw is None:
            return 3600
        try:
            value = int(str(raw).strip())
        except Exception:
            return 3600
        if value <= 0:
            return None
        return max(30, value)

    def _ide_command_output_limit(self) -> int:
        return self._get_env_int("LUMENA_IDE_OUTPUT_LIMIT", 2000000, minimum=20000)

    def _patch_strict_enabled(self) -> bool:
        if self._is_ide_runtime():
            return False
        raw = os.getenv("LUMENA_PATCH_STRICT", "1")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _file_cards_enabled(self) -> bool:
        raw = os.getenv("LUMENA_CHAT_FILE_CARDS", "1")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _trace_ids(self) -> tuple[Optional[str], Optional[str]]:
        if not TELEMETRY_AVAILABLE or current_trace_context is None:
            return None, None
        try:
            ctx = current_trace_context() or {}
            return ctx.get("trace_id"), ctx.get("turn_id")
        except Exception:
            return None, None

    def _record_file_edit(
        self,
        *,
        tool_name: str,
        action: str,
        file_path: Path,
        before_content: Optional[str],
        after_content: Optional[str],
        existed_before: bool,
        summary: str,
        workspace_relative: Optional[str] = None,
    ) -> None:
        if not self._file_cards_enabled():
            return
        if not TELEMETRY_AVAILABLE or get_file_edits_store is None:
            return

        trace_id, turn_id = self._trace_ids()
        if not trace_id:
            return

        try:
            store = get_file_edits_store()
            store.start_edit_session(trace_id=trace_id, turn_id=turn_id)
            if workspace_relative is None and compute_workspace_relative is not None:
                workspace_relative = compute_workspace_relative(file_path, self.runtime_root)
            task_id = None
            if callable(get_current_runtime_context):
                try:
                    runtime_ctx = get_current_runtime_context()
                    task_id = getattr(runtime_ctx, "task_id", None) if runtime_ctx else None
                except Exception:
                    task_id = None
            store.record_edit(
                trace_id=trace_id,
                turn_id=turn_id,
                task_id=task_id,
                tool_name=tool_name,
                action=action,
                file_path=str(file_path),
                workspace_relative=workspace_relative,
                before_content=before_content,
                after_content=after_content,
                existed_before=existed_before,
                summary=summary,
            )
        except Exception as exc:
            logger.debug("file_edit record skipped: {}", exc)

    # ── Filtrage contextuel des outils ────────────────────────────────────
    # Catégories toujours injectées quel que soit le contexte
    _ALWAYS_INCLUDE_CATEGORIES: set = {"system"}
    # Catégories de fallback quand aucune règle ne matche
    _FALLBACK_CATEGORIES: set = {"files", "system", "web", "memory"}

    # Mots-clés français + anglais → catégories pertinentes
    # v2 — 26 packs sémantiques, BROWSER/SEARCH séparés, CODE isolé, DATA dédié
    _CONTEXT_RULES: list = [

        # ═══ PACK 01 — SEARCH (SANS navigation Chrome) ═══
        (
            {"recherche", "trouve", "news", "actualité", "actualite",
             "cherche", "info sur", "wikipedia", "internet", "en ligne",
             "article", "que sait-on", "online", "information",
             "search", "find", "web", "wiki", "résumé de", "resume de",
             "explique-moi", "qu'est-ce que", "quoi de neuf",
             "rapport sur", "synthèse de", "synthese de"},
            {"web"},
        ),

        # ═══ PACK 01b — DATA (data.gouv / SIRENE / géo — mots-clés SPÉCIFIQUES) ═══
        # Volontairement resserré : pas de termes larges seuls (entreprise/csv/
        # statistiques) qui ajouteraient du bruit. Variantes avec/sans accent car
        # le filtre matche sur query.lower() sans normalisation d'accents.
        (
            {"data.gouv", "datagouv", "data gouv", "open data", "opendata",
             "dataset", "jeu de données", "jeu de donnees", "jeux de données",
             "jeux de donnees", "siret", "siren", "sirene", "insee",
             "code insee", "code commune", "géocodage", "geocodage", "commune"},
            {"data"},
        ),

        # ═══ PACK 02 — BROWSER (Chrome profil utilisateur, SANS web_search) ═══
        (
            {"chrome", "chromium", "firefox", "navigateur", "browser",
             "onglet", "tab", "page web", "webpage", "url", "lien",
             "click", "cliquer", "formulaire", "form", "bouton", "button",
             "scraping", "leboncoin", "le bon coin", "google", "bing",
             "youtube", "ouvre", "va sur", "navigue", "connecte-toi",
             "affiche la page", "remplis le formulaire", "télécharge depuis"},
            {"browser"},
        ),

        # ═══ PACK 03 — CODE ⭐ (delegate_task = SEUL point d'entrée code) ═══
        (
            {"crée un site", "cree un site", "crée un projet", "cree un projet",
             "développe", "developpe", "génère", "genere",
             "site web", "website", "application web", "webapp",
             "landing page", "portfolio", "dashboard",
             "frontend", "backend", "fullstack",
             "react", "vue", "angular", "html", "css",
             "programme", "écris le code", "ecris le code",
             "implémente", "implemente", "ajoute une feature",
             "corrige l'erreur", "répare le bug", "repare le bug",
             "bug", "débogu", "debogu", "debug", "refactor",
             "variable", "function", "class", "compile",
             "build", "npm", "pip", "package", "dépendance", "dependance",
             "dev", "code source", "écris le code", "ecris le code",
             "génère le code", "genere le code", "script", "python",
             "code", "coder", "code moi", "code-moi", "cree moi",
             "jeu", "game", "appli", "application", "api",
             "serveur", "server", "bot", "chatbot", "outil",
             "automatise", "automatisation", "flappy", "snake", "tetris",
             "morpion", "calculatrice", "todo", "todolist"},
            {"agents", "project"},
        ),

        # ═══ PACK 04 — GIT ═══
        (
            {"git", "commit", "branch", "branche", "merge", "pull", "push",
             "rebase", "stash", "diff", "log", "clone", "checkout", "repo",
             "repository", "dépôt", "depot"},
            {"git", "files"},
        ),
        (
            {"github", "pull request", "issue", "pr", "gist", "fork",
             "github.com", "actions", "release", "tag"},
            {"github", "git"},
        ),

        # ═══ PACK 05 — IDE ═══
        (
            {"ide", "éditeur", "editeur", "editor", "vscode", "cursor",
             "code source", "lsp", "autocomplétion", "autocompletion",
             "navigate", "symbole",
             "codebase", "base de code", "analyse de code", "dépendances",
             "imports", "architecture", "structure du code", "index",
             "search code", "cherche dans le code",
             "goto definition", "références", "references"},
            {"ide", "lsp", "codebase", "files"},
        ),

        # ═══ PACK 06 — FILES (fichiers purs, pas code projet) ═══
        (
            {"fichier", "file", "dossier", "folder", "directory",
             "répertoire", "repertoire", "écrire", "ecrire",
             "write", "lire", "read", "créer", "creer", "create",
             "supprimer", "delete", "renommer", "rename", "copier",
             "copy", "déplacer", "deplacer", "move", "patch",
             "zip", "archive", "liste les fichiers", "quel contenu"},
            {"files", "skills"},
        ),

        # ═══ PACK 07 — COMPUTER ═══
        (
            {"souris", "mouse", "clavier", "keyboard", "écran", "ecran",
             "screen", "fenêtre", "fenetre", "window", "bureau", "desktop",
             "clic droit", "right click", "drag", "scroll", "défilement",
             "defilement", "application", "notepad", "paint",
             "terminal", "double-clique", "glisse", "open_app",
             "ferme la fenêtre", "prend la main", "contrôle l'écran",
             "automatise le bureau"},
            {"computer_use"},
        ),

        # ═══ PACK 08 — DOCUMENTS ═══
        (
            {"document", "rapport", "pdf", "docx", "xlsx", "pptx",
             "facture", "devis", "invoice", "contrat", "compte rendu",
             "compte-rendu", "synthèse", "synthese", "bon de commande",
             "note de frais", "proforma", "avoir", "tableur", "csv",
             "présentation", "presentation", "diaporama", "slides",
             "word", "excel", "powerpoint", "spreadsheet",
             "réunion", "reunion", "modèle", "modele", "template",
             "analyse ce document", "lis ce pdf", "résume ce fichier"},
            {"documents", "files"},
        ),

        # ═══ PACK 09 — MAIL & MESSAGING ═══
        (
            {"mail", "email", "courriel", "courrier", "envoyer un mail",
             "send mail", "inbox", "boîte de réception", "boite de reception",
             "imap", "telegram", "whatsapp", "envoyer", "envoi",
             "notification critique", "sms urgent", "appel urgent",
             "réponds au mail", "reponds au mail", "lis mes mails",
             "message whatsapp"},
            {"mail", "files"},
        ),

        # ═══ PACK 10 — IMAGE ═══
        (
            {"image", "photo", "logo", "illustration", "svg",
             "thumbnail", "miniature", "vignette", "bannière", "banniere",
             "affiche", "poster", "portrait", "dessin", "icône", "icone",
             "fond d'écran", "upscale", "recadre", "supprime le fond",
             "remove background", "génère une image", "genere une image",
             "crée une image", "cree une image", "dessine", "illustre",
             "visualise", "imagine", "dalle", "stable diffusion",
             "midjourney", "flux", "generate image", "screenshot",
             "capture", "ocr", "texte dans", "reconnaissance",
             "perception", "analyse d'image"},
            {"image", "files"},
        ),

        # ═══ PACK 11 — MUSIC ═══
        (
            {"spotify", "musique", "music", "chanson", "song", "playlist",
             "album", "artiste", "artist", "écouter", "ecouter", "play",
             "pause", "suivant", "précédent", "precedent",
             "mets de la musique", "volume spotify", "ajoute à la file"},
            {"spotify"},
        ),

        # ═══ PACK 12 — VIDEO ⚠️ ISOLÉ (agents ABSENT = delegate_task invisible) ═══
        (
            {"video", "vidéo", "remotion", "reel", "short",
             "tiktok", "animation", "motion", "clip", "montage", "ffmpeg",
             "render", "composition", "captions", "sous-titres",
             "sous titres", "edite la vidéo", "edite la video",
             "modifie la vidéo", "modifie la video", "coupe la vidéo",
             "découpe le clip", "decoupe le clip", "accélère la vidéo",
             "ralentis la vidéo", "assemble les plans",
             "intro vidéo", "intro video", "outro",
             "générer une vidéo", "generer une video", "aperçu vidéo"},
            {"video", "files"},
        ),

        # ═══ PACK 13 — MEMORY ═══
        (
            {"mémoire", "memoire", "memory", "souvenir", "rappelle",
             "remember", "oublie", "forget", "journal", "apprends",
             "learn", "connaissance", "knowledge",
             "retiens", "note ça", "note ca", "écris dans le journal",
             "qu'as-tu appris"},
            {"memory"},
        ),

        # ═══ PACK 14 — AUTONOMY ═══
        (
            {"autonome", "autonomy", "daemon", "heartbeat", "planification",
             "schedule", "tâche planifiée", "tache planifiee",
             "cron", "automatique", "routine", "proactif",
             "surveillance", "tâche", "tache", "tâches", "taches",
             "rappel", "rappelle-moi", "tous les jours", "chaque jour",
             "quotidien", "planifie", "programme pour", "enregistre",
             "récurrent", "recurren", "bg_start", "plan",
             "chaque matin", "chaque soir", "toutes les heures",
             "chaque semaine", "planifier", "schedule",
             "tu as fait quoi", "tu a fait quoi", "qu'as-tu fait",
             "qu as tu fait", "tu na rien fait", "depuis minuit",
             "de 00h", "activite autonome", "activite daemon",
             "quoi faire", "que faire", "prochaine action",
             "next best action", "qu'aurais tu du faire",
             "tu aurais du", "prends une initiative", "sois autonome"},
            {"autonomy"},
        ),

        # ═══ PACK 15 — SERVICES ═══
        ({"notion", "notion.so", "page notion",
          "base de données notion", "database notion"}, {"notion"}),
        ({"discord", "serveur discord", "bot discord", "salon",
          "channel", "modération", "moderation", "ban", "kick",
          "rôle", "role", "envoyer sur discord", "message discord",
          "notifie discord", "canal discord", "webhook discord"}, {"discord"}),
        ({"twitter", "tweet", "x.com", "poster", "publier",
          "timeline", "retweet", "rt", "follow", "unfollow",
          "hashtag", "mention", "réseaux sociaux", "reseaux sociaux",
          "réseau social", "reseau social"}, {"social", "web"}),
        ({"stripe", "paiement", "payment", "abonnement",
          "subscription", "prix", "price", "checkout",
          "coupon", "remboursement", "refund", "customer",
          "solde stripe", "encaisser", "facturer",
          "monétis", "monetiz", "vendre", "sell",
          "lien de paiement", "payment link",
          "produit stripe", "facture stripe"}, {"stripe"}),
        ({"n8n", "workflow", "automatiser", "automate", "scénario",
          "scenario", "trigger", "webhook n8n", "node", "intégration",
          "integration", "automatisation", "créer une routine",
          "flux de travail", "pipeline automatique",
          "zap", "zapier", "make.com", "no-code", "low-code",
          "déclencher", "declencher", "si alors"}, {"automation", "web"}),

        # ═══ PACK 16 — DEPLOY ═══
        (
            {"ionos", "hébergement", "hebergement", "hébergeur", "hebergeur",
             "sftp", "ftp", "deploy", "déploie", "deploie",
             "déployer", "deployer", "déploiement", "deploiement",
             "hosting", "mettre en ligne", "mise en ligne",
             "upload", "serveur web", "publie le site", "envoie le site"},
            {"ionos", "website"},
        ),

        # ═══ PACK 17 — SECURITY ═══
        (
            {"sécurité", "securite", "security", "osint", "reconnaissance",
             "vulnérabilité", "vulnerability", "audit", "pentest",
             "whois", "shodan", "exploit", "cve", "hash",
             "encrypt", "decrypt", "chiffr",
             "réseau", "reseau", "network", "ping", "ip", "dns",
             "port", "scan", "traceroute", "nmap", "connexion",
             "bandwidth", "latence", "firewall", "proxy", "vpn",
             "socket", "tcp", "udp"},
            {"security", "network"},
        ),

        # ═══ PACK 18 — SKILLS ═══
        (
            {"skill", "compétence", "competence", "installer", "install",
             "plugin", "extension", "module", "activer", "désactiver",
             "desactiver", "custom tool", "outil custom", "skill installé",
             "installed skill", "extension custom", "capacités",
             "mes outils", "reload skills"},
            {"skills", "custom"},
        ),

        # ═══ PACK API / HTTP ═══
        (
            {"api", "http", "rest", "endpoint", "requête http",
             "requete http", "json", "webhook", "curl", "fetch"},
            {"web", "network"},
        ),

        # ═══ PACK AGENTS (délégation explicite) ═══
        (
            {"agent", "sub-agent", "sous-agent", "délègue", "delegate",
             "spécialisé", "specialise", "expert", "multi-agent"},
            {"agents"},
        ),
    ]

    # ── Phase 1.1-1.4: Filtrage contextuel des outils ──────────────────────
    def apply_context_filter(self, query: str, intent: Optional[str] = None) -> None:
        """Filtre les outils disponibles en fonction du contexte de la requête.

        Matche *query* contre _CONTEXT_RULES, collecte les catégories pertinentes,
        puis set _allowed_tools = union des outils dont la catégorie est sélectionnée.
        Invalide le cache de descriptions.

        Args:
            query: Requête utilisateur.
            intent: Intent classifié (chat/tool_direct/project/react). Si "chat",
                    restreint aux catégories memory/system pour économiser le
                    contexte d'outils.
        """
        if not query or not getattr(self, "_tool_modules", None):
            # Pas de modules mappés → on ne peut pas filtrer, tout reste ouvert
            return

        query_lower = query.lower()
        ionos_db_context = _looks_like_ionos_db_intent(query_lower)
        peer_team_query = _is_peer_team_query(query_lower)
        matched_categories: set = set()

        for keywords, categories in self._CONTEXT_RULES:
            for kw in keywords:
                if kw in query_lower:
                    matched_categories |= categories
                    break  # un match suffit pour cette règle

        # ── BDD d'un site IONOS → catégorie ionos ──────────────────────────
        # On expose les outils ionos_db_* dès qu'une intention BDD est claire, pour
        # éviter que le modèle ne lise config.php / lance mysql/php/node en direct.
        _db_kw = ("bdd", "base de données", "base de donnees", "base de donner",
                  "database", "mysql", "mariadb", "table", "schema", "schéma")
        _site_kw = ("site", "site web", "openlumena", "ionos", "héberg", "heberg")
        # Termes BDD non ambigus (suffisent seuls : "à la bdd", "dans la base de données").
        _db_strong = ("bdd", "base de données", "base de donnees", "base de donner",
                      "database", "mysql", "mariadb")
        # Verbes de mutation de table (couvre "crée/rajoute une table test").
        _table_verb = ("crée", "creer", "créer", "cree", "ajoute", "ajouter",
                       "rajoute", "rajouter", "nouvelle table", "create table",
                       "supprime", "modifie")
        if (ionos_db_context
                or (any(k in query_lower for k in _db_kw) and any(k in query_lower for k in _site_kw))
                or any(k in query_lower for k in _db_strong)
                or ("table" in query_lower and any(v in query_lower for v in _table_verb))):
            matched_categories |= {"ionos"}

        # Toujours inclure les catégories obligatoires
        matched_categories |= self._ALWAYS_INCLUDE_CATEGORIES
        if peer_team_query:
            # Phase 11A : une demande naturelle "demande à l'autre Lumena" doit
            # rendre visibles les outils peer, même si le classifier la voit comme
            # un simple chat.
            matched_categories.discard("agents")
            matched_categories |= {"peers", "network", "web", "memory"}

        # Override intent-based : CHAT pur → restreindre à memory+system (réponse directe)
        if intent == "chat":
            if peer_team_query:
                matched_categories = {"peers", "network", "web", "memory", "system"} | self._ALWAYS_INCLUDE_CATEGORIES
            elif "autonomy" in matched_categories:
                matched_categories = {"autonomy", "memory", "system"} | self._ALWAYS_INCLUDE_CATEGORIES
            else:
                matched_categories = {"memory", "system"} | self._ALWAYS_INCLUDE_CATEGORIES
        elif intent == "tool_direct" and matched_categories != self._ALWAYS_INCLUDE_CATEGORIES:
            # tool_direct : garder uniquement les catégories matchées (pas de fallback large)
            pass
        elif matched_categories == self._ALWAYS_INCLUDE_CATEGORIES:
            # Fallback : aucune règle spécifique → catégories par défaut
            matched_categories |= self._FALLBACK_CATEGORIES

        # Construire le set d'outils autorisés à partir des catégories matchées
        allowed: set = set()
        for tool_name, tool_cat in self._tool_modules.items():
            if tool_cat in matched_categories:
                allowed.add(tool_name)

        # Toujours inclure final_answer, ask_user et les outils plan_*
        for tool_name in self.tools:
            if tool_name in ("final_answer", "ask_user") or tool_name.startswith("plan_"):
                allowed.add(tool_name)

        # P1.3: Guard — si le filtre ne matche aucun outil réel, ne pas filtrer
        if not allowed:
            return

        # Appliquer le filtre et invalider le cache
        old_allowed = self._allowed_tools
        self._allowed_tools = allowed
        self._ionos_db_context = ionos_db_context
        self._ionos_db_context_query = query_lower if ionos_db_context else ""
        self._ionos_db_block_count = 0  # nouveau contexte → compteur de blocage réinitialisé
        if old_allowed != allowed:
            self._tools_desc_cache = None

    def clear_context_filter(self) -> None:
        """Retire le filtre contextuel — tous les outils redeviennent disponibles."""
        if self._allowed_tools is not None:
            self._allowed_tools = None
            self._tools_desc_cache = None
        self._ionos_db_context = False
        self._ionos_db_context_query = ""
        self._ionos_db_block_count = 0

    def register(
        self, 
        name: str, 
        description: str, 
        parameters: Dict[str, Any],
        handler: callable
    ):
        """Enregistre un nouvel outil."""
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler
        }
        self._tools_desc_cache = None  # invalider le cache de description
    
    def get_tools_description(self) -> str:
        """Retourne une description compacte des outils (1 ligne chacun). Résultat mis en cache."""
        if self._tools_desc_cache is not None:
            return self._tools_desc_cache
        # Certains tests construisent ToolRegistry via object.__new__ sans appeler __init__.
        # On tolère ce mode pour éviter une régression de compatibilité.
        allowed_tools = getattr(self, "_allowed_tools", None)
        descriptions = []
        for name, tool in self.tools.items():
            if allowed_tools is not None and name not in allowed_tools:
                continue
            params = tool["parameters"]
            required_params = set(tool.get("required", []))
            if not params:
                descriptions.append(f"- {name}(): {tool['description']}")
            else:
                param_list = ", ".join(
                    f"{p}" if p in required_params else f"{p}?"
                    for p in params
                )
                descriptions.append(f"- {name}({param_list}): {tool['description']}")
        # Directive contextuelle BDD IONOS : injectée UNIQUEMENT quand le filtre a
        # détecté une intention BDD IONOS (coût nul sinon). Évite que le modèle
        # raisonne à lire config.php / lancer mysql-php-node avant d'être bloqué.
        if getattr(self, "_ionos_db_context", False):
            descriptions.insert(0,
                "⛔ RÈGLE BDD IONOS : pour toute action sur une base IONOS, n'ouvre JAMAIS "
                "config.php/.env, ne lance ni mysql/php/node ni delegate_task/CodeAgent. "
                "Utilise directement les outils `ionos_db_*` (le bridge gère secrets, audit, "
                "snapshots). Recopie EXACTEMENT les valeurs des observations, y compris les "
                "champs masqués (ex. `db50****...`, `dbu****776`) — ne les reconstitue/complète "
                "JAMAIS. N'invente AUCUNE table : ne cite que celles renvoyées par "
                "`ionos_db_list_tables` dans ce tour. AVANT un DROP/CLEAR, vérifie l'état RÉEL "
                "(table vide ?) via `ionos_db_select` — NE déduis JAMAIS le contenu depuis "
                "`ionos_db_describe_table` (schéma seulement) ni depuis la mémoire/le contexte. "
                "Si aucune info BDD n'est requise, réponds avec final_answer.")
        self._tools_desc_cache = "\n".join(descriptions)
        return self._tools_desc_cache
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Retourne le schéma des outils pour l'API."""
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": {
                        "type": "object",
                        "properties": tool["parameters"],
                        "required": list(tool["parameters"].keys())
                    }
                }
            }
            for name, tool in self.tools.items()
        ]

    # ──────────────────────────────────────────────────────────────
    # Policy middleware : délégation forcée CodeAgent pour mutations
    # ──────────────────────────────────────────────────────────────
    def _policy_check(
        self,
        name: str,
        args: Dict[str, Any],
        caller: CallerContext,
    ) -> Optional[Observation]:
        """Retourne une Observation de refus si la mutation doit être déléguée.

        Autorise :
        - Tous les outils non-muteurs (read_file, grep, send_email, …)
        - Les callers non-ReAct (CodeAgent, scheduler, autonomy)
        - Les mutations hors projet connu (data/, reports/, logs/, bureau)
        - Les mutations dans un projet sur des fichiers doc/binaire/asset
          (.md, .pdf, .png, .svg…)

        Refuse uniquement :
        - Mutation par caller=REACT sur un fichier code/config d'un projet
          du registry (.py, .js, .json, Dockerfile, package.json, etc.)

        Flag env `LUMENA_STRICT_CODE_DELEGATION` :
        - enforce (défaut) : refuse durement
        - warn             : log warning mais laisse passer (transition)
        - off              : aucun check
        """
        mode = _strict_mode()
        if mode == "off":
            return None
        if caller.kind != "react":
            return None
        if name not in _MUTATE_TOOLS_CODE:
            return None

        # Extraire le path cible
        path_str = _extract_path_from_args(name, args or {})
        if not path_str:
            return None  # pas de path identifiable → laisser passer (ex: run_command "curl …")

        # Le path appartient-il à un projet du registry ?
        try:
            from ..utils.project_registry import find_project_by_path
            proj = find_project_by_path(path_str)
        except Exception as e:
            logger.debug("[policy] find_project_by_path a échoué ({}) → allow", e)
            return None
        if proj is None:
            return None  # hors projet : ReAct peut éditer

        # Shell tools dans un projet : toujours refuser (commande peut toucher
        # n'importe quel fichier, on ne peut pas se fier à l'extension du cwd).
        _is_shell = name in ("run_command", "run_shell", "exec_command")
        if _is_shell:
            if _react_allow_project_shell():
                logger.warning(
                    "[policy] REACT shell autorise par flag: {} sur {} (projet {})",
                    name,
                    path_str,
                    proj.get("slug", "?") if isinstance(proj, dict) else "?",
                )
                return None
            _cmd = str((args or {}).get("command", "")).strip().lower()
            _readonly_prefixes = (
                "python -m http.server", "python3 -m http.server",
                "node --check", "node -c",
                "python -m py_compile", "python3 -m py_compile",
                "pytest", "python -m pytest", "python3 -m pytest",
                "npm test", "npm run test", "npm run lint", "npm run build",
                "git status", "git log", "git diff", "git show", "git branch",
                "ls", "dir", "pwd", "whoami",
                "curl", "wget",
                "python --version", "node --version", "npm --version",
            )
            if any(_cmd.startswith(p) for p in _readonly_prefixes):
                return None  # commande read-only / test / serveur → ReAct autorisé
        if not _is_shell and not _requires_codeagent(path_str):
            return None  # .md / .pdf / .svg dans projet → ReAct autorisé

        # Exception micro-fix : édition bornée sur fichier code/config léger
        if not _is_shell and _is_react_micro_fix(name, args, path_str):
            logger.info(
                "[policy] REACT micro-fix autorisé: {} sur {} (~{} lignes)",
                name, path_str[:60], _estimate_change_lines(name, args) or "?",
            )
            return None

        # Mutation refusée
        slug = proj.get("slug", "?") if isinstance(proj, dict) else "?"
        msg = (
            f"⛔ Mutation refusée : l'outil '{name}' tente de modifier "
            f"'{path_str}', un fichier code/config du projet suivi '{slug}'. "
            f"Cette opération doit passer par le CodeAgent. "
            f"Utilise `delegate_to_codeagent(task)` avec une description claire."
        )
        if mode == "warn":
            logger.warning("[policy] (warn) {} sur {} (projet {}) — autorisé par flag", name, path_str, slug)
            return None
        logger.warning("[policy] REACT mutation refusée: {} sur {} (projet {}) → délègue CodeAgent", name, path_str, slug)
        # Télémétrie minimale si disponible
        if TELEMETRY_AVAILABLE:
            try:
                publish_trace(
                    stage="policy_refuse",
                    status="blocked",
                    mode="agent",
                    tool_name=name,
                    summary=f"project={slug} path={path_str}",
                )
            except Exception:
                pass
        # Reliability metrics
        try:
            from ..utils.reliability_metrics import get_metrics as _get_rm
            _get_rm().record_policy_refuse(tool=name, path=path_str, project=str(slug))
        except Exception:
            pass
        return Observation(content=msg, success=False)

    def _category_contract_check(
        self,
        name: str,
        args: Dict[str, Any],
        caller: CallerContext,
    ) -> Optional[Observation]:
        """Vérifie les préconditions du contrat de catégorie.

        Retourne une Observation de refus explicable si une précondition
        critique n'est pas satisfaite. None = tout est OK.

        Actuellement vérifié :
        - Catégories requires_workspace=True : workspace_path requis dans le runtime context
        - Catégorie "communication" en mode autonomie : refus hard
        - Catégorie "agents" (delegate_task) : description trop vague refusée
        """
        module_cat = self._tool_modules.get(name, "")
        if not module_cat:
            return None
        semantic = get_semantic_category(module_cat)
        contract = get_category_contract(semantic)
        if contract is None:
            return None

        if (
            contract.requires_workspace
            and (
                # En mode autonomie : on vérifie toutes les catégories requires_workspace
                caller.kind in ("autonomy", "scheduler", "daemon")
                # En mode react : uniquement la catégorie "agents" (delegate_task)
                # — les fichiers sont déjà contrôlés par WorkspaceFileGuardrails
                or (caller.kind == "react" and semantic == "agents")
            )
        ):
            workspace_candidates: List[tuple[str, bool]] = []

            def _append_candidate(
                value: Any,
                *,
                parent_for_file: bool = False,
                trusted: bool = False,
            ) -> None:
                if not isinstance(value, str):
                    return
                text = value.strip()
                if not text:
                    return
                if parent_for_file:
                    try:
                        path_obj = Path(text).expanduser()
                        if path_obj.is_absolute():
                            target = path_obj if path_obj.suffix == "" else path_obj.parent
                            workspace_candidates.append((str(target), trusted))
                    except Exception:
                        return
                else:
                    workspace_candidates.append((text, trusted))

            try:
                runtime_ctx = get_current_runtime_context()
            except Exception:
                runtime_ctx = None

            if runtime_ctx is not None:
                _append_candidate(
                    getattr(runtime_ctx, "resolved_workspace", None),
                    trusted=True,
                )
                _append_candidate(
                    getattr(runtime_ctx, "workspace_path", None),
                    trusted=True,
                )

            _append_candidate((getattr(self, "ide_context", {}) or {}).get("workspace_path"), trusted=True)

            explicit_workspace_keys = (
                "workspace_path",
                "project_path",
                "project_dir",
                "cwd",
                "directory",
            )
            for key in explicit_workspace_keys:
                _append_candidate(args.get(key), trusted=True)

            nested_context = args.get("context")
            if isinstance(nested_context, dict):
                for key in explicit_workspace_keys:
                    _append_candidate(nested_context.get(key), trusted=True)

            for key in ("path", "file_path", "target", "destination", "filepath"):
                _append_candidate(args.get(key), parent_for_file=True)

            has_workspace = False
            for candidate, trusted in workspace_candidates:
                if trusted:
                    has_workspace = True
                    break
                try:
                    candidate_path = Path(candidate).expanduser()
                    if candidate_path.exists() or candidate_path.parent.exists():
                        has_workspace = True
                        break
                except Exception:
                    continue

            if not has_workspace:
                msg = (
                    f"[category:{semantic}] Refus - workspace_path requis pour la categorie "
                    f"'{semantic}'. Fournissez un workspace/project explicite ou un runtime "
                    f"context avec workspace resolu."
                )
                logger.warning("[category_contract] {} caller={}", msg, caller.kind)
                return Observation(content=msg, success=False)

        # ── Autonomie sur catégorie non autorisée ──
        if caller.kind in ("autonomy", "scheduler", "daemon") and not contract.autonomy_allowed:
            msg = (
                f"[category:{semantic}] Refus autonomie — catégorie '{semantic}' "
                f"ne peut pas être déclenchée sans interaction utilisateur. "
                f"Raisons : {'; '.join(contract.refusal_reasons[:2]) or 'interaction requise'}"
            )
            logger.warning("[category_contract] {} caller={}", msg, caller.kind)
            return Observation(content=msg, success=False)

        # ── delegate_task : description trop vague ──
        if name == "delegate_task" and caller.kind == "react":
            desc = str(args.get("description", "") or "").strip()
            if len(desc) < 20:
                msg = (
                    "[category:agents] delegate_task refusé — description trop vague "
                    f"({len(desc)} chars < 20 requis). Précisez la tâche."
                )
                logger.warning("[category_contract] {}", msg)
                return Observation(content=msg, success=False)

        return None

    def get_tool_module_category(self, tool_name: str) -> str:
        """Return the registered module category for a tool name."""
        return self._tool_modules.get(tool_name, "")

    def get_tool_semantic_category(self, tool_name: str) -> str:
        """Return the semantic category for a tool name."""
        module_cat = self.get_tool_module_category(tool_name)
        if not module_cat:
            return ""
        return get_semantic_category(module_cat)

    def _known_callable_peer_targets(self) -> List[Dict[str, Any]]:
        """Retourne les pairs Lumena trusted appelables, sans exposer de token."""
        if (
            os.getenv("LUMENA_PEER_COLLABORATION", "0").strip() != "1"
            and os.getenv("LUMENA_PEER_AWARENESS", "0").strip() != "1"
        ):
            return []
        try:
            from src.utils import paths as _paths
            registry = _paths.DATA_DIR / "peer_registry.json"
            if not registry.exists():
                return []
            data = json.loads(registry.read_text(encoding="utf-8"))
        except Exception:
            return []

        peers: List[Dict[str, Any]] = []
        for peer in data.values() if isinstance(data, dict) else []:
            if not isinstance(peer, dict):
                continue
            if peer.get("trust") != "trusted" or not peer.get("peer_token_outbound"):
                continue
            host = str(peer.get("host") or "").strip()
            if not host:
                continue
            try:
                port = int(peer.get("port") or 8080)
            except Exception:
                port = 8080
            peers.append({
                "instance_id": str(peer.get("instance_id") or ""),
                "instance_name": str(peer.get("instance_name") or peer.get("instance_id") or "Lumena"),
                "host": host,
                "port": port,
            })
        return peers

    def _peer_raw_network_refusal(self, tool_name: str, args: Dict[str, Any]) -> Optional[Observation]:
        """Empêche ReAct de contourner le protocole peer avec HTTP/browser/curl.

        Les logs ont montré que l'agent essayait /api/chat, curl ou browser vers
        une IP Lumena trusted. En Phase 11A, ces appels doivent être redirigés
        vers les outils peer, qui gèrent tokens, scopes, audit et anti-boucle.
        """
        if tool_name not in _PEER_RAW_NETWORK_TOOLS:
            return None
        try:
            raw = json.dumps(args or {}, ensure_ascii=False).lower()
        except Exception:
            raw = str(args or {}).lower()
        if tool_name == "run_command" and not any(k in raw for k in ("http://", "https://", "curl", "invoke-webrequest")):
            return None

        for peer in self._known_callable_peer_targets():
            host = peer["host"].lower()
            port = peer["port"]
            target_markers = (
                f"http://{host}:{port}",
                f"https://{host}:{port}",
                f"{host}:{port}",
            )
            if not any(marker in raw for marker in target_markers):
                continue
            peer_label = peer["instance_name"]
            peer_id = peer["instance_id"]
            return Observation(
                content=(
                    f"Refus Phase 11A: {tool_name} ne doit pas contacter directement "
                    f"la Lumena trusted {peer_label} ({host}:{port}). "
                    "Utilise le protocole inter-instance: `peer_team_request` "
                    "par défaut, ou `orchestrate_peer_request`, `delegate_to_peer`, "
                    "`run_peer_task_sync`, `query_peer_knowledge` selon le besoin. "
                    f"instance_id cible: {peer_id}."
                ),
                success=False,
            )
        return None

    def _ionos_db_context_refusal(
        self,
        tool_name: str,
        args: Dict[str, Any],
        caller: CallerContext,
    ) -> Optional[Observation]:
        """Force les demandes BDD IONOS a passer par le bridge ionos_db_*.

        Le filtre contextuel reste souple pour les autres domaines, mais une BDD
        IONOS ne doit jamais etre traitee via config.php, shell, scripts PHP/Node
        ou CodeAgent. Le bridge gere les secrets, l'audit, les snapshots et les
        confirmations.
        """
        if caller.kind != "react":
            return None
        if tool_name.startswith("ionos_db_"):
            return None
        if tool_name in _IONOS_DB_ALLOWED_NON_DB_TOOLS or tool_name.startswith("plan_"):
            return None

        try:
            raw_args = json.dumps(args or {}, ensure_ascii=False)
        except Exception:
            raw_args = str(args or {})
        in_ionos_db_context = bool(getattr(self, "_ionos_db_context", False))
        if (
            not in_ionos_db_context
            and not _looks_like_ionos_db_intent(raw_args)
            and not _looks_like_ionos_config_access(raw_args)
        ):
            return None
        if tool_name not in _IONOS_DB_BYPASS_TOOLS:
            return None

        # Escalade : à partir du 2e blocage du même intent, on ordonne de cesser
        # de retenter et de conclure (anti-boucle de contournement observée).
        self._ionos_db_block_count = int(getattr(self, "_ionos_db_block_count", 0)) + 1
        if self._ionos_db_block_count >= 2:
            msg = (
                "⛔ Action BDD IONOS bloquée (2e tentative). ARRÊTE de contourner : "
                "n'essaie PAS un autre outil fichier/shell/PHP/Node/MySQL/CodeAgent. "
                "Soit tu utilises un outil `ionos_db_*` (ex: `ionos_db_get_config`, "
                "`ionos_db_test`/`ionos_test_site_database`, `ionos_db_create_sandbox_table`), "
                "soit tu réponds immédiatement avec `final_answer` en expliquant que "
                "l'accès BDD IONOS passe uniquement par le bridge sécurisé."
            )
        else:
            msg = (
                "Action BDD IONOS détectée : utilise le bridge sécurisé via les outils "
                "`ionos_db_*` (ex: `ionos_db_get_config`, `ionos_db_bridge_status`, "
                "`ionos_db_create_sandbox_table`, `ionos_db_propose_write`). "
                "N'utilise PAS les fichiers config, le shell, PHP/Node/MySQL ni le "
                "CodeAgent pour cette action, et ne retente pas avec un autre outil "
                "fichier/shell — si aucune info BDD n'est nécessaire, conclus avec `final_answer`."
            )
        logger.warning(
            "[policy] IONOS DB hard-block (#{}) : tool={} context_query={}",
            self._ionos_db_block_count,
            tool_name,
            getattr(self, "_ionos_db_context_query", ""),
        )
        return Observation(content=msg, success=False)

    async def execute(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        caller: Optional[CallerContext] = None,
    ) -> Observation:
        """Exécute un outil.

        Args:
            name: Nom de l'outil.
            args: Arguments (dict).
            caller: Identité de l'agent appelant (REACT, CODEAGENT, …).
                Si None, considéré UNKNOWN (permissif pour rétrocompat).
                ReAct doit passer caller=REACT pour que la policy bloque
                les mutations de code projet.
        """
        caller = caller or _CALLER_UNKNOWN

        # ── Policy middleware : délégation forcée vers CodeAgent ──
        # Bloque les mutations de code/config de projet quand l'appelant est ReAct.
        _refusal = self._policy_check(name, args or {}, caller)
        if _refusal is not None:
            return _refusal

        # ── Contrat de catégorie : préconditions formelles ──
        _cat_refusal = self._category_contract_check(name, args or {}, caller)
        if _cat_refusal is not None:
            return _cat_refusal

        _peer_refusal = self._peer_raw_network_refusal(name, args or {})
        if _peer_refusal is not None:
            return _peer_refusal

        _ionos_db_refusal = self._ionos_db_context_refusal(name, args or {}, caller)
        if _ionos_db_refusal is not None:
            return _ionos_db_refusal

        if name not in self.tools:
            # Auto-fix: normalisation + fuzzy strict (cutoff=0.75) avant d'échouer
            from src.llm.output_normalizer import auto_fix_action_name
            _fixed = auto_fix_action_name(name, set(self.tools.keys()))
            if _fixed != name and _fixed in self.tools:
                logger.info(f"🔧 Auto-fix nom d'outil: '{name}' → '{_fixed}'")
                name = _fixed
            else:
                # Phase 4.2: Suggestion fuzzy du nom d'outil le plus proche
                _pool = list(self.tools.keys())  # P1.1: Toujours suggérer depuis le pool complet
                close = difflib.get_close_matches(name, _pool, n=3, cutoff=0.5)
                hint = f" Outils similaires: {', '.join(close)}" if close else ""
                return Observation(
                    content=f"Outil '{name}' non trouvé.{hint}",
                    success=False
                )
        if self._allowed_tools is not None and name not in self._allowed_tools:
            # P1.1: Soft filter — log + auto-expand, jamais de blocage
            logger.info(f"🔧 Tool '{name}' hors filtre prompt — exécution soft-filter")
            self._allowed_tools.add(name)
            self._tools_desc_cache = None
        started = perf_counter()
        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="tool_exec_start",
                status="start",
                mode="agent",
                tool_name=name,
                summary=str(args),
            )
        
        try:
            handler = self.tools[name]["handler"]
            
            # Si args est vide ou None, passer un dict vide
            if args is None:
                args = {}
            
            # --- Unwrap wrappers courants (LLM envoie souvent {"input": {args...}} au lieu de {args...}) ---
            _WRAPPER_KEYS = ("input", "parameters", "arguments", "params", "data", "payload")
            if len(args) == 1:
                _only_key = next(iter(args))
                if _only_key in _WRAPPER_KEYS and isinstance(args[_only_key], dict):
                    logger.info(f"🔧 Outil {name}: unwrap '{_only_key}' wrapper → {list(args[_only_key].keys())}")
                    args = args[_only_key]
            
            # --- Mapping d'aliases courants pour les params (LLM envoie souvent de mauvais noms) ---
            _PARAM_ALIASES = {
                "edit_file": {"path": "file_path", "file": "file_path", "content": "new_content", "new": "new_content", "old": "old_content", "original": "old_content", "replacement": "new_content"},
                "write_file": {"file_path": "path", "text": "content"},
                "delegate_task": {"path": "project_path", "files": "context", "content": "description"},
                "write_website_files": {"files": "json_data", "data": "json_data"},
                "parallel_tools": {"input": "tool_calls", "tools": "tool_calls", "calls": "tool_calls"},
                "run_command": {"input": "command", "cmd": "command", "shell": "command"},
                "type_text": {"input": "text", "content": "text", "message": "text", "value": "text"},
                "open_app": {"input": "name", "app": "name", "application": "name", "program": "name"},
            }
            if name in _PARAM_ALIASES:
                alias_map = _PARAM_ALIASES[name]
                remapped = {}
                for k, v in args.items():
                    remapped[alias_map.get(k, k)] = v
                if remapped != args:
                    logger.info(f"🔧 Outil {name}: aliases remappés: {set(args.keys()) - set(remapped.keys())} → params corrigés")
                args = remapped
            
            # Filtrer les arguments via le cache de signatures (buildé à l'init — O(1) ici)
            # Exception: parallel_tools gère ses propres args (wrapper avec recovery)
            has_var_keyword, valid_params = self._sig_cache.get(name, (True, None))
            if has_var_keyword and name != "parallel_tools":
                # Wrapper V2 avec **kw — utiliser le schéma déclaré pour filtrer
                _raw_params = self.tools[name].get("parameters", {})
                tool_params = set(_raw_params.get("properties", _raw_params).keys())
                if tool_params:
                    filtered_args = {k: v for k, v in args.items() if k in tool_params}
                    if len(filtered_args) != len(args):
                        removed = set(args.keys()) - set(filtered_args.keys())
                        logger.warning(f"🔧 Outil {name}: args inconnus retirés: {removed} (valides: {tool_params})")
                    args = filtered_args
            elif valid_params:
                filtered_args = {k: v for k, v in args.items() if k in valid_params}
                if len(filtered_args) != len(args):
                    removed = set(args.keys()) - set(filtered_args.keys())
                    logger.debug(f"🔧 Outil {name}: args filtrés, retirés: {removed}")
                args = filtered_args
            
            # --- Coercion de types courants (int↔str, str→JSON) ---
            tool_schema = self.tools[name].get("parameters", {})

            # --- Validation des paramètres requis ---
            _tool_meta = self.tools[name]
            _required_params: list = _tool_meta.get("required", [])
            if not _required_params and isinstance(tool_schema, dict):
                # Fallback: paramètres sans "default" dans le schema
                _required_params = [
                    k for k, v in tool_schema.items()
                    if isinstance(v, dict) and v.get("required", False)
                ]
            # --- Alias communs LLM→handler (anti-frustration : task↔description, etc.) ---
            _GENERIC_PARAM_ALIASES = {
                "task": "description",
                "instruction": "description",
                "instructions": "description",
                "query": "description",
                "prompt": "description",
                "msg": "message",
                "text": "message",
                "filepath": "path",
                "file_path": "path",
                "filename": "path",
            }
            for _alias, _canonical in _GENERIC_PARAM_ALIASES.items():
                if (
                    _alias in args
                    and _canonical in _required_params
                    and _canonical not in args
                ):
                    args[_canonical] = args.pop(_alias)
            _missing = [p for p in _required_params if p not in args or args[p] is None]
            if _missing:
                # Construire un mini-exemple depuis le schema pour aider le modèle à récupérer
                _props = tool_schema.get("properties", tool_schema) if isinstance(tool_schema, dict) else {}
                _ex_parts = []
                for _mp in _missing:
                    _pschema = _props.get(_mp, {}) if isinstance(_props, dict) else {}
                    _ptype = _pschema.get("type", "string") if isinstance(_pschema, dict) else "string"
                    if _ptype == "array":
                        _ex_parts.append(f'"{_mp}": ["valeur1"]')
                    elif _ptype == "integer":
                        _ex_parts.append(f'"{_mp}": 1')
                    elif _ptype == "boolean":
                        _ex_parts.append(f'"{_mp}": true')
                    else:
                        _ex_parts.append(f'"{_mp}": "valeur"')
                _hint = f' — exemple: {{{", ".join(_ex_parts)}}}' if _ex_parts else ""
                return Observation(
                    content=f"Paramètre(s) requis manquant(s) pour '{name}': {', '.join(_missing)}{_hint}",
                    success=False,
                )

            # --- Validation de type basique (string vide pour requis = erreur) ---
            for _pname in _required_params:
                _val = args.get(_pname)
                if isinstance(_val, str) and not _val.strip():
                    return Observation(
                        content=f"Paramètre '{_pname}' de '{name}' est vide (string vide).",
                        success=False,
                    )

            for _pname, _pval in list(args.items()):
                _pschema = tool_schema.get(_pname, {})
                _expected_type = _pschema.get("type", "") if isinstance(_pschema, dict) else ""
                if _expected_type == "string" and isinstance(_pval, (int, float)):
                    args[_pname] = str(_pval)
                elif _expected_type == "integer" and isinstance(_pval, str):
                    try:
                        args[_pname] = int(_pval, 0) if _pval.startswith("0x") or _pval.startswith("0X") else int(_pval)
                    except (ValueError, TypeError):
                        pass
                elif _expected_type in ("array", "object") and isinstance(_pval, str):
                    try:
                        _parsed = json.loads(_pval)
                        if isinstance(_parsed, (list, dict)):
                            args[_pname] = _parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Log debug pour voir les arguments reçus
            _arg_preview = {}
            for _k, _v in (args or {}).items():
                _s = str(_v)
                _arg_preview[_k] = _s[:80] + "…" if len(_s) > 80 else _s
            logger.debug(f"🔧 Outil {name} appelé avec args: {_arg_preview if _arg_preview else 'VIDE'}")
            
            # Cache d'observations : eviter les appels redondants pour les outils de lecture
            cache_key = None
            if name in self._CACHEABLE_TOOLS:
                try:
                    cache_key = f"{name}::{json.dumps(args, sort_keys=True, default=str)}"
                except Exception:
                    cache_key = f"{name}::{args}"
                if cache_key in self._observation_cache:
                    # P2: Compte les hits pour éviter la "boucle du fichier figé"
                    _hits = self._observation_cache_hits.get(cache_key, 0) + 1
                    self._observation_cache_hits[cache_key] = _hits
                    if _hits > self._OBS_CACHE_MAX_HITS:
                        logger.warning(
                            "♻️ Cache invalidé: {} servi {}× — relecture fraîche forcée",
                            name, _hits,
                        )
                        self._observation_cache.pop(cache_key, None)
                        self._observation_cache_hits.pop(cache_key, None)
                        # Ne pas retourner : laisse tomber vers l'exécution réelle
                    else:
                        logger.debug(f"Cache hit: {name} (#{_hits})")
                        return Observation(content=self._observation_cache[cache_key], success=True)
            
            try:
                result = await handler(**args)
            except TypeError as _te:
                # Le LLM a passé des arguments invalides (param inconnu, type incorrect)
                logger.warning(f"TypeError dans outil {name}: {_te}")
                return Observation(
                    content=f"\u2717 Erreur d'arguments pour {name}: {_te}",
                    success=False,
                )

            # ── Invalidation du cache après opération d'écriture ──
            _WRITE_TOOLS = {
                "write_file", "edit_file", "edit_by_lines", "apply_patch", "apply_patches",
                "run_command", "create_file", "delete_file",
            }
            if name in _WRITE_TOOLS and self._observation_cache:
                _stale = [k for k in self._observation_cache
                          if k.startswith(("list_directory::", "read_file::"))]
                for _sk in _stale:
                    del self._observation_cache[_sk]
                    self._observation_cache_hits.pop(_sk, None)
                if _stale:
                    logger.debug("Cache invalidé: {} entrées (après {})", len(_stale), name)

            # Observation structurée directe (ex: parallel_tools avec sub_results)
            if isinstance(result, Observation):
                return result

            if isinstance(result, str):
                raw = result.strip()
                # Stocker dans le cache si applicable (limite 12000 chars)
                # Ne pas cacher les résultats paginés (contiennent "SUITE DISPONIBLE")
                if cache_key is not None and len(raw) < 12000 and "SUITE DISPONIBLE" not in raw:
                    if len(self._observation_cache) >= self._OBS_CACHE_MAX:
                        _evicted = next(iter(self._observation_cache))
                        self._observation_cache.pop(_evicted)
                        self._observation_cache_hits.pop(_evicted, None)
                    self._observation_cache[cache_key] = raw
                    self._observation_cache_hits[cache_key] = 0  # P2: reset du compteur à l'insertion
                variants = {raw.lower()}
                try:
                    repaired = raw.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore").strip()
                    if repaired:
                        variants.add(repaired.lower())
                except Exception as e:
                    logger.debug(f"Tool name repair: {e}")

                def _fold_status_text(value: str) -> str:
                    folded = unicodedata.normalize("NFKD", value)
                    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
                    folded = re.sub(r"[^a-z0-9:/._\-\s]", " ", folded)
                    return re.sub(r"\s+", " ", folded).strip()

                folded_variants = [_fold_status_text(v) for v in variants]

                # FIX: n'analyser que le PRÉFIXE (200 premiers chars) pour éviter
                # les faux positifs sur un payload légitime (doc, code) qui
                # contient par hasard les mots "validation/failed/invalid/…".
                # Un vrai message d'erreur commence toujours par l'erreur.
                folded_prefixes = [text[:200] for text in folded_variants]

                # FIX: sortie volumineuse (>1500 chars) = payload de données, pas
                # une erreur (les erreurs sont courtes). On skip la détection par
                # mots-clés pour ces cas.
                is_large_payload = len(raw) >= 1500

                starts_with_error = any(
                    text.startswith(prefix)
                    for text in folded_prefixes
                    for prefix in ("error", "erreur", "echec", "failed", "failure", "timeout")
                )
                validation_failure = (not is_large_payload) and any(
                    "validation" in text
                    and any(
                        token in text
                        for token in (
                            "echec",
                            "echou",
                            "failed",
                            "failure",
                            "invalid",
                            "invalide",
                            "interdit",
                        )
                    )
                    for text in folded_prefixes
                )
                missing_param_error = (not is_large_payload) and any(
                    ("parametre" in text or "parameter" in text or "argument" in text)
                    and any(token in text for token in ("missing", "manquant", "required", "requis"))
                    for text in folded_prefixes
                )

                # Vérifier aussi les marqueurs unicode d'erreur dans le texte brut
                starts_with_unicode_error = (
                    raw.startswith("✗") or raw.startswith("❌") or raw.startswith("✖")
                )

                if starts_with_error or starts_with_unicode_error or validation_failure or missing_param_error:
                    if TELEMETRY_AVAILABLE:
                        publish_trace(
                            stage="tool_exec_done",
                            status="error",
                            mode="agent",
                            tool_name=name,
                            duration_ms=(perf_counter() - started) * 1000.0,
                            error=result,
                        )
                    return Observation(content=result, success=False)
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="tool_exec_done",
                    status="ok",
                    mode="agent",
                    tool_name=name,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    summary=(result[:220] if isinstance(result, str) else None),
                )
            return Observation(content=result, success=True)
        except TypeError as e:
            # Erreur de paramètres manquants
            error_msg = str(e)
            if "required" in error_msg or "missing" in error_msg:
                logger.warning(f"⚠️ Outil {name}: paramètres manquants - args reçus: {args}")
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="tool_exec_done",
                        status="error",
                        mode="agent",
                        tool_name=name,
                        duration_ms=(perf_counter() - started) * 1000.0,
                        error=error_msg,
                    )
                # Retrouver les vrais paramètres requis depuis le schéma du tool
                _params = self.tools.get(name, {}).get("parameters", {})
                if "properties" in _params:
                    # Format JSON Schema (V2 handlers)
                    _required = _params.get("required", list(_params["properties"].keys()))
                else:
                    # Format flat (tool_system) : requis = ceux sans "default"
                    _required = [k for k, v in _params.items() if isinstance(v, dict) and "default" not in v] or list(_params.keys())
                _hint = f"Paramètres requis: {', '.join(_required)}" if _required else "voir schéma de l'outil"
                return Observation(
                    content=f"❌ Paramètres manquants pour {name}. Reçu: {list(args.keys()) if args else 'aucun'}. {_hint}",
                    success=False
                )
            raise
        except Exception as e:
            logger.error(f"Erreur exécution outil {name}: {e}")
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="tool_exec_done",
                    status="error",
                    mode="agent",
                    tool_name=name,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    error=str(e),
                )
            return Observation(
                content=f"Erreur: {str(e)}",
                success=False
            )
    
    async def execute_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        *,
        caller: Optional[CallerContext] = None,
    ) -> List[Observation]:
        """
        🚀 PHASE 2: Exécute plusieurs outils en parallèle.
        
        Gros gain de performance quand plusieurs outils indépendants
        doivent être exécutés (ex: lire 3 fichiers, faire 2 recherches).
        
        Args:
            tool_calls: Liste de {name: str, args: dict}
            
        Returns:
            Liste d'Observations dans le même ordre
            
        Exemple:
            results = await registry.execute_parallel([
                {"name": "read_file", "args": {"path": "a.py"}},
                {"name": "read_file", "args": {"path": "b.py"}},
                {"name": "web_search", "args": {"query": "python async"}}
            ])
        """
        if not tool_calls:
            return []
        
        # Créer les tâches
        tasks = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            tasks.append(self.execute(name, args, caller=caller))
        
        # Exécuter en parallèle
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convertir les exceptions en Observations
        observations = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                observations.append(Observation(
                    content=f"❌ Erreur parallèle: {result}",
                    success=False
                ))
            else:
                observations.append(result)
        
        return observations

    def _get_mail_hub(self):
        if self._mail_hub_instance is None:
            from ..tools.mail_hub import MailHub
            from ..utils.paths import MAIL_DIR
            self._mail_hub_instance = MailHub(MAIL_DIR)
        return self._mail_hub_instance

    def _get_critical_alert_hub(self):
        if self._critical_alert_hub_instance is None:
            from ..tools.critical_alert_hub import CriticalAlertHub
            from ..utils.paths import ALERTS_DIR
            self._critical_alert_hub_instance = CriticalAlertHub(ALERTS_DIR)
        return self._critical_alert_hub_instance

    def _get_web_crawler(self):
        if self._web_crawler_instance is None:
            from ..tools.web_crawler import WebCrawler
            from ..utils.paths import CRAWLER_DIR
            self._web_crawler_instance = WebCrawler(CRAWLER_DIR)
        return self._web_crawler_instance

    def _get_document_hub(self):
        if self._document_hub_instance is None:
            from ..tools.document_hub import DocumentHub
            # On sauvegarde les documents directement dans le workspace
            self._document_hub_instance = DocumentHub(self.runtime_root)
        return self._document_hub_instance

    def _get_search_hub(self):
        if self._search_hub_instance is None:
            from ..tools.search_hub import SearchHub
            self._search_hub_instance = SearchHub()
        return self._search_hub_instance

    def _get_spotify_hub(self):
        if self._spotify_hub_instance is None:
            from ..tools.spotify_hub import SpotifyHub
            self._spotify_hub_instance = SpotifyHub()
        return self._spotify_hub_instance

    def _get_notion_hub(self):
        if self._notion_hub_instance is None:
            from ..tools.notion_hub import NotionHub
            self._notion_hub_instance = NotionHub()
        return self._notion_hub_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
