"""
🤖 LUMENA - Système de Sub-Agents

Permet à Lumena de déléguer des tâches à des agents spécialisés.
"""

import os
import re
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field, replace
from enum import Enum
from datetime import datetime
from pathlib import Path
import asyncio
import json
import time
from loguru import logger
from ..utils.persistence import atomic_write_text

try:
    from ..telemetry import publish_trace
    _TELEMETRY_AVAILABLE = True
except Exception:
    _TELEMETRY_AVAILABLE = False  # telemetry non critique
from src.prompts.agents.sub_agent_prompts import (
    _CODE_AGENT_SYSTEM,
    _SHORT_EXAMPLE,
    _LONG_EXAMPLE,
    _PROMPT_WEB_SECTION,
    _PROMPT_PYTHON_SECTION,
    _PROMPT_GENERAL_SECTION,
    _MODIFICATION_INSTRUCTIONS,
    _CREATION_INSTRUCTIONS,
    _ARCHITECT_PROMPT,
    _DEBUG_SYSTEM_PROMPT,
    _REFACTOR_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    _load_provider_prompt,
    _load_tool_descriptions,
)


def get_lumena():
    """Alias legacy pour faciliter le patching en tests."""
    from ..core import get_lumena as _get_lumena
    return _get_lumena()


# ── Exceptions de délégation ─────────────────────────────────

class DelegationDepthExceeded(RuntimeError):
    """Levée quand la profondeur max de délégation est atteinte."""
    pass


class DelegationCycleDetected(RuntimeError):
    """Levée quand un cycle de délégation est détecté (A→B→A)."""
    pass


# ── Contexte de délégation (immuable, thread-safe) ──────────

@dataclass(frozen=True)
class DelegationContext:
    """
    Contexte de délégation immuable.

    Supérieur à un simple compteur depth car :
    - frozen=True → thread-safe par construction, zéro race condition
    - agent_chain tuple → détecte les cycles (A→B→A), pas seulement la profondeur

    Pattern supérieur à un simple recursion_limit ou DAG statique.
    """
    agent_chain: tuple = ()           # Tuple immuable des agents traversés
    depth: int = 0
    max_depth: int = 3                # orchestrateur → worker → sous-worker
    root_task_id: str = ""
    parent_task_id: Optional[str] = None
    mcp_capability_snapshot: Optional[tuple] = None

    def delegate_to(self, agent_name: str, new_task_id: Optional[str] = None) -> "DelegationContext":
        """
        Crée un nouveau contexte pour une délégation enfant.

        Raises:
            DelegationDepthExceeded: si depth >= max_depth
            DelegationCycleDetected: si agent_name déjà dans la chaîne
        """
        if self.depth >= self.max_depth:
            raise DelegationDepthExceeded(
                f"Profondeur max ({self.max_depth}) atteinte. "
                f"Chaîne: {' → '.join(self.agent_chain)} → {agent_name}"
            )
        if agent_name in self.agent_chain:
            raise DelegationCycleDetected(
                f"Cycle détecté: {' → '.join(self.agent_chain)} → {agent_name}"
            )
        return replace(
            self,
            agent_chain=self.agent_chain + (agent_name,),
            depth=self.depth + 1,
            parent_task_id=new_task_id or self.parent_task_id,
        )

    @property
    def chain_str(self) -> str:
        """Chaîne de délégation lisible."""
        return " → ".join(self.agent_chain) if self.agent_chain else "(root)"


class AgentType(Enum):
    """Types d'agents spécialisés."""
    CODE = "code"           # Agent pour le code
    RESEARCH = "research"   # Agent pour la recherche
    FILE = "file"           # Agent pour les fichiers
    BROWSER = "browser"     # Agent pour le navigateur
    DEBUG = "debug"         # Agent pour debug
    REFACTOR = "refactor"   # Agent pour refactor
    PLANNER = "planner"     # Agent planificateur
    GENERAL = "general"     # Agent général


_PHASE26_MCP_LOOP_TOOLS_BY_AGENT_TYPE = {
    AgentType.RESEARCH: frozenset({"request_mcp_capability"}),
    AgentType.FILE: frozenset({"request_mcp_capability"}),
    AgentType.BROWSER: frozenset({"request_mcp_capability"}),
    AgentType.PLANNER: frozenset({"request_mcp_capability"}),
    AgentType.GENERAL: frozenset({"request_mcp_capability"}),
    AgentType.CODE: frozenset(),
    AgentType.DEBUG: frozenset(),
    AgentType.REFACTOR: frozenset(),
}


def _phase26_filter_tools_for_agent_type(
    agent_type: AgentType,
    tools: List[str],
) -> List[str]:
    phase26_tools = {
        "request_mcp_capability",
        "request_mcp_ticket",
        "run_mcp_autonomy",
        "resume_mcp_task",
    }
    allowed_phase26 = _PHASE26_MCP_LOOP_TOOLS_BY_AGENT_TYPE.get(
        agent_type, frozenset()
    )
    return [
        tool
        for tool in tools
        if tool not in phase26_tools or tool in allowed_phase26
    ]


# ── Model Router : AgentType → domaine MODEL_SKILLS ──────────────────────
# Chaque type d'agent a un domaine prioritaire pour sélectionner le meilleur LLM.
AGENT_DOMAIN: Dict[str, str] = {
    "code":     "code",       # Agent code → top SWE-bench
    "research": "research",   # Agent research → top LongBench/FRAMES
    "file":     "speed",      # Agent fichiers → vitesse (peu de raisonnement)
    "browser":  "research",   # Browser/fetch → analyse long-context
    "debug":    "code",       # Debug → même que code
    "refactor": "code",       # Refactor → code
    "planner":  "reasoning",  # Planificateur → top raisonnement
    "general":  "reasoning",  # Général → raisonnement par défaut
}


class AgentStatus(Enum):
    """Statut d'un agent."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass
class AgentTask:
    """Tâche assignée à un agent."""
    task_id: str
    description: str
    agent_type: AgentType
    priority: int = 5  # 1-10, 10 = urgent
    context: Dict[str, Any] = field(default_factory=dict)
    delegation_ctx: Optional[DelegationContext] = None  # Contexte de délégation cross-agent
    runtime_ctx: Optional[Any] = None  # RuntimeContext snapshot (Phase 2)
    task_ctx: Optional[Any] = None  # TaskContext typé (Phase 2 — priorité sur context dict)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        # Garantir que context est toujours un dict (le LLM peut passer un str)
        if not isinstance(self.context, dict):
            self.context = {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "agent_type": self.agent_type.value,
            "priority": self.priority,
            "context": self.context,
            "delegation_ctx": {
                "agent_chain": list(self.delegation_ctx.agent_chain),
                "depth": self.delegation_ctx.depth,
                "root_task_id": self.delegation_ctx.root_task_id,
            } if self.delegation_ctx else None,
            "created_at": self.created_at.isoformat()
        }


class StatusCode:
    """Codes de statut structurés pour les résultats d'agents."""
    SUCCESS = "success"           # Tâche accomplie
    NEEDS_INPUT = "needs_input"   # Contexte manquant, champs requis dans missing_fields
    PARTIAL = "partial"           # Résultat partiel, peut continuer
    ERROR = "error"               # Erreur technique
    TIMEOUT = "timeout"           # Timeout dépassé
    AMBIGUOUS = "ambiguous"       # Tâche trop vague pour agir


def _consume_architect_reread_budget(
    budget: Dict[str, int],
    path_key: str,
    *,
    default_budget: int = 2,
) -> tuple[bool, int]:
    """Autorise un petit nombre de relectures ciblées sur un fichier injecté.

    Le guard "déjà injecté par l'Architect" est utile pour éviter les relectures
    compulsives, mais il devient contre-productif si l'agent doit vérifier une
    zone précise du fichier principal. On autorise donc un micro-budget local
    par fichier et par tâche.
    """
    if not path_key:
        return False, 0
    remaining = int(budget.get(path_key, default_budget))
    if remaining <= 0:
        return False, 0
    remaining -= 1
    budget[path_key] = remaining
    return True, remaining


def _run_command_looks_mutating(command: str, observation: str = "") -> bool:
    """Retourne True si un run_command reussi ressemble a une mutation fichier.

    Le compteur anti-relecture ne voit que les actions CodeAgent natives. Or un
    run_command qui deplace/copie des fichiers est une vraie progression et ne
    doit pas mener a un faux "15 lectures sans modification".
    """
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    obs = (observation or "").lower()[:800]
    failure_markers = (
        "exit:1", "exit code 1", "non autorise", "non autorisé",
        "bloquee", "bloquée", "blocked", "erreur", "error",
        "failed", "filenotfounderror", "access denied",
    )
    if any(marker in obs for marker in failure_markers):
        return False
    mutating_patterns = (
        r"\bmove(?:-item)?\b",
        r"\bmv\b",
        r"\bcopy(?:-item)?\b",
        r"\bcp\b",
        r"\bxcopy\b",
        r"\brobocopy\b",
        r"\bren(?:ame)?\b",
        r"\bmkdir\b",
        r"\bnew-item\b",
        r"\bset-content\b",
        r"\badd-content\b",
        r"\bout-file\b",
        r"\bshutil\.(move|copy|copy2|copytree|rmtree)\b",
        r"\bos\.(rename|replace|remove|unlink|rmdir|removedirs|makedirs)\b",
        r"\bpathlib\.path\(",
    )
    return any(re.search(pattern, cmd, re.IGNORECASE) for pattern in mutating_patterns)


@dataclass
class AgentResult:
    """Résultat d'une tâche d'agent."""
    task_id: str
    success: bool
    output: str                                              # Texte lisible (passé au LLM)
    status_code: str = StatusCode.SUCCESS                    # Code machine-readable
    missing_fields: List[str] = field(default_factory=list)  # Champs contexte manquants
    next_action: Optional[str] = None                        # Action suggérée pour relance
    meta: Dict[str, Any] = field(default_factory=dict)       # Données structurées machine-readable
    artifacts: List[str] = field(default_factory=list)       # Fichiers créés
    duration_ms: int = 0
    completed_at: datetime = field(default_factory=datetime.now)


def _failure_meta(
    *,
    report: list,
    blocked_at: str,
    task_description: str,
    next_step: str,
    **extra: Any,
) -> dict:
    """Meta structuré et exploitable pour les retours d'échec du sub-agent.

    Fournit 4 champs cohérents sur tous les points d'abandon :
    - attempted   : dernières actions tentées (trace lisible)
    - blocked_at  : raison précise du blocage
    - remaining   : description de ce qui reste à faire
    - next_step   : recommandation concrète pour la reprise
    """
    return {
        "attempted": list(report[-5:]) if report else [],
        "blocked_at": blocked_at,
        "remaining": task_description,
        "next_step": next_step,
        **extra,
    }


# LOT I.2 — actions d'ÉCRITURE du CodeAgent (les lectures ne sont jamais filtrées).
_CODEAGENT_WRITE_ACTIONS = frozenset({
    "str_replace", "edit_lines", "apply_patch", "write_file", "edit_file", "insert_at_anchor",
    # M3b-clôture (run MiniQuiz 2026-07-06) : `apply_patches` (pluriel) manquait —
    # la voie PRÉFÉRÉE du CodeAgent contournait le périmètre I.2 (le CodeAgent de
    # w_frontend a patché app.py, fichier du backend).
    "apply_patches",
})


def _action_write_paths(action: Any) -> list:
    """M3b-clôture — TOUS les chemins qu'une action d'écriture CodeAgent vise :
    `path`/`file` simples + la liste `patches[*].file/path` d'`apply_patches`
    (liste, ou chaîne repr/JSON — formes réellement émises par les LLM). Pur."""
    paths: list = []
    if not isinstance(action, dict):
        return paths
    for key in ("path", "file", "file_path"):
        v = action.get(key)
        if isinstance(v, str) and v.strip():
            paths.append(v.strip())
    patches = action.get("patches")
    if isinstance(patches, str) and patches.strip():
        try:
            import json as _json
            patches = _json.loads(patches)
        except Exception:
            try:
                import ast as _ast
                patches = _ast.literal_eval(patches)
            except Exception:
                patches = None
    if isinstance(patches, (list, tuple)):
        for p in patches:
            if isinstance(p, dict):
                v = p.get("file") or p.get("path") or p.get("file_path")
                if isinstance(v, str) and v.strip():
                    paths.append(v.strip())
    # LOT J-a (run NoteFlow 2026-08-13) — `apply_patch` (SINGULIER) ne porte aucun
    # chemin en clé : le CodeAgent lit `action["patch"]` (cf. dispatch `act ==
    # "apply_patch"`) et le chemin vit DANS le texte, sous `*** Add File:`.
    # L'extracteur ne lisait que les clés → il rendait [] → le garde de périmètre
    # I.2, pourtant branché sur `apply_patch`, n'avait rien à refuser. C'est par là
    # que `test_run_desktop.py` a été créé hors périmètre, fichier non déclaré qui a
    # ensuite fait échouer la publication de toute la mission.
    _patch_text = action.get("patch")
    if isinstance(_patch_text, str) and _patch_text.strip():
        for _line in _patch_text.splitlines():
            _s = _line.strip()
            for _marker in ("*** Add File:", "*** Update File:", "*** Delete File:",
                            "*** Move to:"):
                if _s.startswith(_marker):
                    _p = _s[len(_marker):].strip()
                    if _p:
                        paths.append(_p)
                    break
    return paths


# ── LOT Z36 — « je n'ai rien ecrit » n'est pas « projet cree » ───────────────
#
# Z35 (2026-08-23) a ferme ce defaut sur la route Codex autonome :
#
#     if not changed:  return CodexCodeAgentResult(success=False, ...)
#
# Mais Z35 ne gardait QUE ce rail. Depuis qu'il est retire et que l'abonnement
# passe par la boucle historique, la garantie disparaissait — la boucle
# historique n'a jamais eu l'equivalent. Sa porte `done` :
#
#     _edited_now = list(getattr(self, "_edited_files", []))
#     if _edited_now:              # <- zero fichier => porte SAUTEE
#         ... run_gate(...) ...
#     return AgentResult(success=True, ...)
#
# Le fait existe, il est calcule trois lignes plus haut, et il n'est jamais
# consulte pour la decision. Meme motif que les 53 lots.
#
# PERIMETRE VOLONTAIREMENT ETROIT : on ferme le ZERO, pas le « un sur vingt ».
# Une tache d'analyse ou de lecture ne produit legitimement aucun fichier ;
# exiger une ecriture pour tout casserait la moitie des usages du CodeAgent.
_CREATION_VERBS = (
    "cree", "créé", "crée", "creer", "créer", "genere", "génère", "generer",
    "générer", "construis", "construire", "developpe", "développe",
    # « fais » couvre « fais-moi », « fais moi » et « fais un jeu 3D » : la
    # forme nue existe dans le corpus reel et manquait aux trois autres.
    "fais ", "fais-", "ecris-moi un", "écris-moi un", "implemente", "implémente",
    "build", "create", "generate", "make ",
)
_READING_VERBS = (
    "analyse", "explique", "cherche", "recherche", "resume", "résume", "liste",
    "audit", "compare", "verifie", "vérifie", "lis ", "montre", "trouve",
)

_CREATION_WROTE_NOTHING_MESSAGE = (
    "Tu as demande `done` sans avoir ecrit AUCUN fichier, alors que la tache "
    "demande une creation. Un projet qui n'existe pas sur le disque n'est pas "
    "un projet cree.\n"
    "Utilise `write_file` pour produire les fichiers demandes, puis `done`. "
    "Si la creation est impossible, dis precisement ce qui bloque au lieu de "
    "conclure."
)


def _creation_task_wrote_nothing(
    *,
    intent: Any,
    edited_files: Any,
    description: Any,
) -> bool:
    """Vrai quand une tache de CREATION se termine sans le moindre fichier.

    Les trois conditions doivent tenir ensemble :
      * l'intention resolue est `create` ;
      * aucun fichier n'a ete ecrit ;
      * la description demande bien de produire quelque chose, et ne demande pas
        seulement de lire ou d'analyser.

    La troisieme evite le faux positif qui rendrait ce garde inutilisable :
    « analyse ce projet » arrive avec `intent=create` par defaut et n'ecrit
    rien — c'est normal, et ce n'est pas ce qu'on attrape ici.
    """
    if str(intent or "").strip().lower() != "create":
        return False
    if list(edited_files or []):
        return False
    texte = str(description or "").strip().lower()
    if not texte:
        return False
    # Une demande de lecture prime : elle est explicite, la creation est le defaut.
    if any(verbe in texte for verbe in _READING_VERBS):
        return False
    return any(verbe in texte for verbe in _CREATION_VERBS)


def _write_within_perimeter(path_str: Any, allowed_files: Any, workspace_root: Any = None) -> bool:
    """LOT I.2 — une écriture CodeAgent est-elle DANS le périmètre du worker ?

    `allowed_files` vide/None → toujours True (hors mission : ZÉRO effet, comportement
    CodeAgent identique). Sinon match par chemin relatif normalisé OU par basename
    (même logique que le garde de périmètre mission 2.3). Un chemin vide → True (on
    laisse les autres gardes gérer)."""
    if not allowed_files:
        return True
    wp = str(path_str or "").replace("\\", "/").strip().lstrip("./").strip("/")
    if not wp:
        return True
    try:
        p = Path(wp)
        if p.is_absolute() and workspace_root:
            wp = p.resolve().relative_to(Path(str(workspace_root)).resolve()).as_posix()
    except Exception:
        pass
    allowed = {str(a).replace("\\", "/").strip().strip("/") for a in allowed_files if str(a).strip()}
    if wp in allowed:
        return True
    # LOT J-b (run NoteFlow 2026-08-13) — la tolérance par BASENAME acceptait le bon
    # nom dans N'IMPORTE QUEL dossier : `static/style.css` passait parce que
    # `noteflow/static/style.css` était autorisé. Le CodeAgent a ainsi écrit un
    # doublon à la racine de la mission ; ce fichier non déclaré a fait REFUSER la
    # publication, et comme aucune voie de suppression n'existe, la mission est morte
    # avec un livrable pourtant vert (12/12 tests).
    # La tolérance reste NÉCESSAIRE pour un nom NU (`app.py` pour `noteflow/app.py`,
    # forme réellement émise par les workers) — mais dès que le chemin porte un
    # dossier, il désigne un emplacement PRÉCIS : on exige alors l'égalité stricte.
    if "/" in wp:
        return False
    bases = {a.rsplit("/", 1)[-1] for a in allowed}
    return wp in bases


# LOT N2 (run HuffPack 2026-08-14) — le périmètre était défini par une LISTE
# D'OUTILS (`_CODEAGENT_WRITE_ACTIONS`). Tout outil hors liste le contourne : le
# CodeAgent, après un refus explicite sur `test_structured_state.py`, a écrit le
# fichier via `run_command` — et l'a annoncé : « seul canal non intercepté ».
# Trois fichiers créés ainsi, deux publiés dans le livrable final.
#
# Analyser la commande (`write_text`, `>`, `tee`…) ne suffirait pas : une commande
# peut écrire de mille façons (`open(p,'w')`, `shutil.copy`, un script tiers). On
# regarde donc le DISQUE, pas la commande — exhaustif par construction, et ça
# survit à la prochaine astuce.
_SNAPSHOT_IGNORED_DIRS: frozenset = frozenset({
    "__pycache__", ".backups", ".lumena_backups", ".pytest_cache", ".git",
})


def snapshot_mission_files(root: Any) -> frozenset:
    """Chemins relatifs POSIX des fichiers présents sous `root` (bruit exclu).

    frozenset() si `root` est absent/illisible : un instantané impossible ne doit
    JAMAIS faire échouer une action — la garde est un filet, pas un obstacle.
    """
    # Une racine VIDE résoudrait vers le répertoire courant : on scannerait alors
    # tout le dépôt Lumena (258 321 fichiers mesurés) à chaque action. Refus net.
    raw_root = str(root or "").strip()
    if not raw_root:
        return frozenset()
    try:
        base = Path(raw_root).resolve()
        if not base.is_dir():
            return frozenset()
    except Exception:
        return frozenset()
    found = set()
    try:
        for path in base.rglob("*"):
            try:
                if not path.is_file():
                    continue
                rel = path.resolve().relative_to(base)
                if any(part in _SNAPSHOT_IGNORED_DIRS for part in rel.parts):
                    continue
                found.add(rel.as_posix())
            except Exception:
                continue
    except Exception:
        return frozenset()
    return frozenset(found)


def files_created_outside_perimeter(
    before: Any, after: Any, allowed_files: Any, workspace_root: Any = None
) -> list:
    """Fichiers APPARUS entre deux instantanés et hors du périmètre du worker.

    Pur/testable. `allowed_files` vide/None → [] (hors mission : zéro effet).
    Ne considère QUE les apparitions : un fichier modifié en place est déjà
    couvert par le garde d'écriture, et une DISPARITION n'est jamais signalée ici
    (ce helper ne supprime rien et ne juge rien d'autre).
    """
    if not allowed_files:
        return []
    try:
        new_paths = set(after or ()) - set(before or ())
    except TypeError:
        return []
    return sorted(
        p for p in new_paths
        if not _write_within_perimeter(p, allowed_files, workspace_root)
    )


class SubAgent:
    """
    Agent spécialisé de base.

    Chaque sub-agent a:
    - Un type spécifique
    - Un contexte limité (pas accès à tout)
    - Des outils restreints
    """
    
    def __init__(
        self, 
        agent_type: AgentType = AgentType.GENERAL,
        name: str = "SubAgent",
        tools: Optional[List[str]] = None,
        llm_provider: Optional[str] = None,
        **legacy_kwargs: Any,
    ):
        # Compat legacy tests: accepter description/task sans effet bloquant.
        self.description = str(legacy_kwargs.get("description", "") or "")
        self.default_task = str(legacy_kwargs.get("task", "") or "")
        self.agent_type = agent_type
        self.name = name
        self.status = AgentStatus.IDLE
        self.allowed_tools = _phase26_filter_tools_for_agent_type(
            agent_type,
            list(tools or []),
        )
        self.capabilities = list(self.allowed_tools)
        self.llm_provider = llm_provider  # Optionnel: utiliser un LLM différent
        self.current_task: Optional[AgentTask] = None
        self.history: List[AgentResult] = []
        self._history_max: int = 100  # garde-fou memory leak sur daemon 24/7
        self._task_workspace_root: Optional[Path] = None  # Root pour write_file si workspace_path dans context
        self._allowed_files: Optional[frozenset] = None  # LOT I.2 \u2014 p\u00e9rim\u00e8tre mission (None hors mission)
        # LOT 2.12.A \u2014 le SubAgent est un singleton partag\u00e9 ; deux workers de mission
        # qui d\u00e9l\u00e8guent EN PARALL\u00c8LE (delegate_and_wait) ex\u00e9cutent sur la M\u00caME instance
        # \u2192 tout l'\u00e9tat task-scoped self. (_allowed_files, current_task\u2026) se clobbe.
        # Ce verrou s\u00e9rialise execute() : une t\u00e2che \u00e0 la fois, chacune r\u00e9arm\u00e9e propre.
        self._exec_lock = asyncio.Lock()

        logger.info(f"\U0001f916 SubAgent cr\u00e9\u00e9: {name} ({agent_type.value})")

    @staticmethod
    def _project_root() -> Path:
        """Racine globale du projet Lumena."""
        return Path(__file__).parent.parent.parent

    # Mots-clés indiquant une tâche de création d'artefact (fichiers dans workspace)
    _WEB_CREATION_KW = (
        "crée", "créer", "create", "génère", "générer", "generate",
        "fais", "faire", "make", "build", "construis",
        "html", "css", "javascript", "js", "jeu", "game", "site",
        "application", "app", "démineur", "minesweeper", "snake",
        "memory", "quiz", "portfolio", "landing",
    )

    _REPAIR_KW = (
        "corrige", "corriger", "fix", "repare", "répare", "reparer",
        "continue", "continuer", "reprend", "reprendre",
        "modifie", "modifier", "améliore", "ameliore", "améliorer",
        "termine", "terminer", "finis", "finir", "achève", "achever",
        "debug", "update", "upgrade", "improve", "restructur",
        "complète", "compléter", "complete",
        "casser", "cassé", "broken", "bug",
    )

    def _is_web_creation_task(self, description: str) -> bool:
        """Retourne True si la tâche est une création d'artefacts web/jeux standalone.
        Retourne False si c'est une tâche de réparation/modification (même si 'site' est mentionné)."""
        _desc_lower = description.lower()
        # Exclure les tâches de réparation/modification
        if any(kw in _desc_lower for kw in self._REPAIR_KW):
            return False
        return any(kw in _desc_lower for kw in self._WEB_CREATION_KW)

    # Fichiers/dossiers source Lumena — ne JAMAIS lire en mode workspace projet
    _LUMENA_SOURCE_PREFIXES = (
        "src/", "src\\", "web/", "web\\", "tests/", "tests\\",
        "scripts/", "scripts\\", "docs/", "docs\\",
    )
    _LUMENA_SOURCE_FILES = (
        "lumena_ultime.py", "run_daemon.py", "run_telegram.py", "run_whatsapp.py",
        "run_twitter.py", "core.py", "Dockerfile", "docker-compose.yml",
    )

    def _resolve_path(self, file_path: str) -> Path:
        """Résout un chemin de fichier. Si un workspace_path est actif, les chemins
        relatifs sont résolus depuis ce workspace. Sinon, depuis la racine Lumena."""
        p = Path(file_path)
        if p.is_absolute():
            return p
        _ws_root = getattr(self, "_task_workspace_root", None)
        root = _ws_root or self._project_root()
        # Si workspace actif et le chemin ne commence PAS déjà par le workspace relatif,
        # résoudre directement depuis le workspace root
        if _ws_root:
            _fp_clean = file_path.replace("\\", "/")
            # Guard: path traversal — empêcher de sortir du workspace
            _normalized = (_ws_root / file_path).resolve()
            try:
                _normalized.relative_to(_ws_root.resolve())
            except ValueError:
                logger.warning(
                    f"[CodeAgent] BLOCKED path traversal '{file_path}' — "
                    f"sort du workspace {_ws_root}"
                )
                return _ws_root / Path(file_path).name  # Fichier seul, pas le chemin traversal
            # Guard: chemin relatif au vocabulaire source Lumena (src/, tests/…) →
            # RÉSOLU dans le workspace mission (rien n'est bloqué : le préfixe est
            # simplement ré-ancré). 2.13.E (M8) : l'ancien warning « BLOCKED read
            # of Lumena source » criait des dizaines de fois par run pour un
            # comportement NORMAL → faux signal qui polluait tout diagnostic.
            if (any(_fp_clean.startswith(pfx.replace("\\", "/")) for pfx in self._LUMENA_SOURCE_PREFIXES)
                    or _fp_clean in self._LUMENA_SOURCE_FILES):
                logger.debug(
                    f"[CodeAgent] chemin '{file_path}' résolu dans le workspace "
                    f"mission (préfixe Lumena ré-ancré) — workspace actif: {_ws_root}"
                )
                return _ws_root / file_path
            try:
                ws_rel = _ws_root.relative_to(self._project_root())
                ws_rel_str = str(ws_rel).replace("\\", "/")
                if _fp_clean.startswith(ws_rel_str + "/") or _fp_clean == ws_rel_str:
                    # Le path contient déjà le prefix workspace → résoudre depuis la racine Lumena
                    return self._project_root() / file_path
                # Strip redundant "workspace/" prefix when ws_root is already inside workspace/
                if ws_rel_str.startswith("workspace/") and _fp_clean.startswith("workspace/"):
                    stripped = _fp_clean[len("workspace/"):]
                    if stripped:
                        return _ws_root / stripped
                ws_name = _ws_root.name.replace("\\", "/")
                if ws_name and (_fp_clean.startswith(ws_name + "/") or _fp_clean == ws_name):
                    stripped = _fp_clean[len(ws_name):].lstrip("/")
                    if stripped:
                        return _ws_root / stripped
            except ValueError:
                pass  # workspace est un path absolu hors Lumena
        return root / file_path

    def _get_llm(self, task: Optional["AgentTask"] = None):
        """
        Retourne le LLM optimal pour ce type d'agent selon MODEL_SKILLS.

        Si le meilleur mod\u00e8le est diff\u00e9rent du mod\u00e8le actif, cr\u00e9e une instance
        temporaire MultiProviderLLM avec ce mod\u00e8le. Fallback sur core.llm si erreur.
        """
        from ..llm.multi_provider import MultiProviderLLM

        core = get_lumena()

        # ── Cerveau Codex : un CALLBACK MODELE, pas un moteur parallele ──────
        # Le rail precedent (`run_codeagent_with_codex_subscription`) remplacait
        # TOUTE cette boucle par un tour Codex autonome : prompts, outils, tests,
        # reprises et garde-fous de Lumena etaient contournes, et chaque
        # delegation rouvrait une session (`account/read` puis `model/list`,
        # 30 s chacun au timeout — mesure sur un run reel).
        #
        # Le marqueur vit dans `task.context`, comme `_best_model` ci-dessous :
        # un dict SERIALISABLE, donc il survit au lancement en arriere-plan la
        # ou un objet passe en memoire serait perdu.
        if task is not None and (task.context or {}).get("_codex_brain"):
            try:
                from ..llm.execution_router import (
                    CodexReActUnavailable,
                    get_active_codex_codeagent_brain,
                )

                brain = get_active_codex_codeagent_brain()
                if brain is None:
                    raise CodexReActUnavailable(
                        "Le marqueur Codex CodeAgent est actif sans scope d'execution"
                    )
                logger.debug(
                    "\U0001f9e0 [{}] cerveau Codex partage par la tache "
                    "(boucle CodeAgent historique)",
                    self.name,
                )
                return brain
            except Exception as exc:
                # Pas de repli silencieux vers DeepSeek : l'abonnement a ete
                # choisi explicitement, une panne doit se voir.
                from ..llm.execution_router import CodexReActUnavailable

                raise CodexReActUnavailable(
                    f"Cerveau Codex CodeAgent indisponible: {exc}"
                ) from exc

        # ── Override per-agent (panel config / env) ──
        # LUMENA_AGENT_{CODE|RESEARCH|FILE|BROWSER|DEBUG|REFACTOR|PLANNER|GENERAL}_MODEL
        # "auto" (défaut) = comportement identique à aujourd'hui (routing orchestrateur + DeepSeek logic)
        # Valeur explicite = force ce modèle pour cet AgentType uniquement.
        try:
            _agent_key = self.agent_type.value.upper()
        except Exception:
            _agent_key = ""
        if _agent_key:
            _override = (os.getenv(f"LUMENA_AGENT_{_agent_key}_MODEL", "auto") or "auto").strip()
            if _override and _override.lower() != "auto":
                try:
                    logger.debug(
                        "\U0001f3af [{}] LLM override per-agent: {} \u2192 {} (LUMENA_AGENT_{}_MODEL)",
                        self.name, core.llm.model_name, _override, _agent_key,
                    )
                    return MultiProviderLLM(model_name=_override)
                except Exception as exc:
                    logger.warning(
                        "\U0001f916 [{}] Override per-agent '{}' a \u00e9chou\u00e9, fallback core.llm: {}",
                        self.name, _override, exc,
                    )
                    return core.llm

        # Utiliser le mod\u00e8le d\u00e9j\u00e0 r\u00e9solu par l'orchestrateur (mis dans context)
        best = (task.context or {}).get("_best_model") if task else None

        if not best or best == core.llm.model_name:
            return core.llm

        try:
            logger.debug(f"\U0001f3af [{self.name}] LLM router: {core.llm.model_name} \u2192 {best}")
            return MultiProviderLLM(model_name=best)
        except Exception as exc:
            logger.warning(f"\U0001f916 [{self.name}] LLM router fallback ({best} \u2192 core.llm): {exc}")
            return core.llm

    def _reset_task_scoped_state(self) -> None:
        """LOT 2.10 — remet à zéro l'état de mission AVANT chaque tâche.

        Le SubAgent est un singleton persistant réutilisé pour TOUTES les tâches (chat,
        delegate_task d'un worker de mission, fanout_tasks, …). Une contrainte posée par
        une tâche (périmètre d'écriture `_allowed_files`, racine workspace) DOIT mourir
        avec elle : sinon elle fuit silencieusement sur la tâche SUIVANTE, quel que soit
        le type de travail. `_build_initial_messages` ré-arme ces champs depuis le
        contexte de la nouvelle tâche quand il en fournit un (worker de mission) ; sans
        contexte (fanout / chat) on repart propre au lieu d'hériter d'un vieux périmètre.
        """
        self._allowed_files = None
        self._task_workspace_root = None
        # LOT 2.11.A — MÊME invariant : le rapport de fin (« 📝 Fichiers modifiés »,
        # via _session_memory['edits_done']) et les erreurs vues sont task-scoped. Sans
        # ce reset ils SURVIVENT entre missions sur le CodeAgent singleton → une mission
        # (ex. genere_notes.py) affiche les fichiers d'une mission d'avant (ex. app.py) :
        # rapport périmé qui MENT sur ce qui a été touché. On garde files_read/grep déjà
        # gérés ailleurs ; ici on purge ce qui alimente le rapport rendu au parent.
        _mem = getattr(self, "_session_memory", None)
        if isinstance(_mem, dict):
            _mem["edits_done"] = []
            _mem["errors_seen"] = []

    async def execute(self, task: AgentTask) -> AgentResult:
        """Exécute une tâche — SÉRIALISÉE par instance (LOT 2.12.A).

        Le CodeAgent singleton est partagé entre workers de mission concurrents.
        Sans ce verrou, deux `execute()` en parallèle se clobbent l'état task-scoped
        (`_allowed_files` : run tasksapi = le CodeAgent de w_api hérite du périmètre
        ['test_api.py'] de w_tests → refuse d'écrire app.py). Le verrou garantit
        « une tâche à la fois » : l'état est réarmé proprement (2.10) pour chacune.
        Pas de ré-entrance possible (le CodeAgent ne se délègue jamais à lui-même).
        """
        async with self._exec_lock:
            return await self._execute_locked(task)

    async def _execute_locked(self, task: AgentTask) -> AgentResult:
        """Corps réel d'execute(), exécuté sous `_exec_lock` (voir execute())."""
        import os
        start_time = datetime.now()
        self.status = AgentStatus.RUNNING
        self.current_task = task

        # LOT 2.10 — INVARIANT GÉNÉRAL : l'état de mission est STRICTEMENT task-scoped.
        self._reset_task_scoped_state()

        # Timeout configurable — 0 = pas de limite (défaut, comme Copilot)
        timeout_seconds = int(os.getenv("LUMENA_SUBAGENT_TIMEOUT", "0"))
        
        logger.info(f"🤖 [{self.name}] Exécute: {task.description[:50]}...")
        
        try:
            if timeout_seconds > 0:
                raw = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=timeout_seconds
                )
            else:
                raw = await self._execute_task(task)

            elapsed_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # _execute_task peut retourner str (legacy) ou AgentResult (enrichi)
            if isinstance(raw, AgentResult):
                result = raw
                result.duration_ms = elapsed_ms
            else:
                result = AgentResult(
                    task_id=task.task_id,
                    success=True,
                    output=str(raw),
                    status_code=StatusCode.SUCCESS,
                    duration_ms=elapsed_ms,
                )
        
        except asyncio.TimeoutError:
            logger.error(f"🤖 [{self.name}] Timeout après {timeout_seconds}s")
            result = AgentResult(
                task_id=task.task_id,
                success=False,
                output=f"⏱️ Timeout: la tâche a pris plus de {timeout_seconds}s",
                status_code=StatusCode.TIMEOUT,
                duration_ms=timeout_seconds * 1000,
                meta=_failure_meta(
                    report=[],
                    blocked_at=f"timeout après {timeout_seconds}s",
                    task_description=getattr(task, "description", ""),
                    next_step="Découper la tâche en sous-tâches plus courtes ou augmenter le timeout",
                ),
            )
            
        except Exception as e:
            logger.error(f"🤖 [{self.name}] Erreur: {e}")
            result = AgentResult(
                task_id=task.task_id,
                success=False,
                output=f"Erreur: {e}",
                status_code=StatusCode.ERROR,
                meta={"error_type": type(e).__name__, "error_detail": str(e)},
                duration_ms=int((datetime.now() - start_time).total_seconds() * 1000),
            )
        
        self.status = AgentStatus.COMPLETED if result.success else AgentStatus.FAILED
        self.current_task = None
        self.history.append(result)
        if len(self.history) > self._history_max:
            self.history = self.history[-self._history_max:]
        
        return result
    
    # --- Helpers pour construire des AgentResult structurés ---

    def _result_success(self, task: AgentTask, output: str, **meta_kw: Any) -> AgentResult:
        """Construit un résultat succès."""
        return AgentResult(
            task_id=task.task_id, success=True, output=output,
            status_code=StatusCode.SUCCESS, meta=dict(meta_kw),
        )

    def _result_needs_input(
        self, task: AgentTask, output: str,
        missing_fields: List[str], next_action: Optional[str] = None,
    ) -> AgentResult:
        """Construit un résultat 'contexte manquant'."""
        return AgentResult(
            task_id=task.task_id, success=False, output=output,
            status_code=StatusCode.NEEDS_INPUT,
            missing_fields=missing_fields,
            next_action=next_action or f"Relancer avec context contenant: {', '.join(missing_fields)}",
            meta={"missing": missing_fields},
        )

    def _result_ambiguous(self, task: AgentTask, output: str, suggestions: Optional[List[str]] = None) -> AgentResult:
        """Construit un résultat 'tâche ambiguë'."""
        return AgentResult(
            task_id=task.task_id, success=False, output=output,
            status_code=StatusCode.AMBIGUOUS,
            meta={"suggestions": suggestions or []},
        )

    def _result_error(self, task: AgentTask, output: str, error_type: str = "unknown") -> AgentResult:
        """Construit un résultat erreur."""
        return AgentResult(
            task_id=task.task_id, success=False, output=output,
            status_code=StatusCode.ERROR,
            meta={"error_type": error_type},
        )

    async def _execute_task(self, task: AgentTask) -> "str | AgentResult":
        """Méthode à surcharger par les sous-classes. Peut retourner str ou AgentResult."""
        return f"Tâche '{task.description}' traitée par {self.name}"

    async def delegate(
        self,
        description: str,
        agent_type: AgentType = AgentType.GENERAL,
        context: Optional[Dict[str, Any]] = None,
        priority: int = 5,
    ) -> AgentResult:
        """
        Délégation cross-agent : permet à un agent d'en invoquer un autre
        via l'orchestrateur, avec détection de cycles et limite de profondeur.

        Le DelegationContext est propagé automatiquement :
        - Si la tâche courante a un ctx, on l'étend
        - Sinon, on en crée un nouveau (root)

        Raises:
            DelegationDepthExceeded: profondeur max atteinte
            DelegationCycleDetected: cycle A→B→A détecté
        """
        # Récupérer l'orchestrateur
        orch = get_orchestrator()

        # Construire ou hériter le DelegationContext
        current_ctx = None
        if self.current_task and self.current_task.delegation_ctx:
            current_ctx = self.current_task.delegation_ctx
        
        if current_ctx is None:
            # Première délégation — créer un contexte root
            task_id = self.current_task.task_id if self.current_task else "unknown"
            current_ctx = DelegationContext(
                agent_chain=(self.name,),
                depth=0,
                max_depth=3,
                root_task_id=task_id,
            )

        # Résoudre le nom de l'agent cible
        effective_type = agent_type
        if effective_type == AgentType.GENERAL:
            effective_type = orch._infer_agent_type(description, context)
        target_agent = orch.get_agent(effective_type)
        target_name = target_agent.name if target_agent else f"agent_{effective_type.value}"

        # Générer l'ID de la tâche enfant AVANT delegate_to pour traçabilité complète
        orch.task_counter += 1
        child_task_id = f"dlg_{orch.task_counter}_{datetime.now().strftime('%H%M%S')}"

        # delegate_to lève si cycle ou depth max
        child_ctx = current_ctx.delegate_to(target_name, new_task_id=child_task_id)

        logger.info(
            f"🔗 [{self.name}] délègue à {target_name} "
            f"(depth={child_ctx.depth}, chain={child_ctx.chain_str})"
        )

        child_task = AgentTask(
            task_id=child_task_id,
            description=description,
            agent_type=agent_type,
            priority=priority,
            context=context or {},
            delegation_ctx=child_ctx,
        )

        return await orch.execute_task(child_task)

    async def _call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        """
        Appelle un tool Lumena de manière robuste.
        
        - Vérifie le scope (allowed_tools) — log warning si hors scope, exécute quand même.
        - Backup automatique avant actions destructives.
        - Audit log de chaque appel.
        """
        from ..tools.tool_system import get_tool_system
        from .audit_log import get_audit_log

        args = arguments or {}
        audit = get_audit_log()

        # LOT J-c (run NoteFlow 2026-08-13) — `run_tests` appelé avec un `test_path`
        # VIDE échoue toujours (« ❌ test_path requis »), et ce message est listé comme
        # marqueur de NON-EXÉCUTION par `_suspicious_delegate_success_reason` : la
        # livraison entière est alors refusée. Au run, le CodeAgent est ainsi mort en
        # 0.0s, trois fois de suite, ce qui a supprimé le DERNIER recours de la mission
        # pour se débloquer. Plusieurs sites peuvent produire cet appel (raccourci
        # explicite, auto-test) : on résout au point de passage UNIQUE.
        # Le workspace de la tâche est le chemin naturel ; sans lui, on ne fabrique
        # rien — l'appelant reçoit l'erreur claire d'origine.
        if tool_name == "run_tests" and not str(args.get("test_path") or "").strip():
            _ws_j = str(getattr(self, "_task_workspace_root", "") or "").strip()
            if _ws_j:
                args = {**args, "test_path": _ws_j}
                logger.info(
                    "🤖 [{}] run_tests sans test_path → workspace de la tâche: {}",
                    self.name, _ws_j,
                )

        # Scope check — pour la propreté architecturale, pas pour bloquer
        if self.allowed_tools and tool_name not in self.allowed_tools:
            logger.warning(
                f"🤖 [{self.name}] tool '{tool_name}' hors scope "
                f"(allowed: {self.allowed_tools}) — exécution autorisée"
            )

        # Backup automatique avant action destructive
        backup_path = audit.backup_before_destructive(tool_name, args, self.name)

        # Exécution
        task_id = self.current_task.task_id if self.current_task else None
        try:
            _phase26_token = None
            try:
                from src.mcp.react_integration import MCPReActIntegration

                _phase26_token = MCPReActIntegration.set_caller_context(
                    f"{self.agent_type.value}_agent"
                )
            except Exception:
                _phase26_token = None
            tool_system = get_tool_system()
            try:
                result = await tool_system.execute_tool_by_name(tool_name, args)
            finally:
                if _phase26_token is not None:
                    try:
                        MCPReActIntegration.reset_caller_context(_phase26_token)
                    except Exception:
                        pass
            _result_str = str(result) if result is not None else ""
            _outcome = _classify_tool_outcome(_result_str.lower())
            audit.log_action(
                self.name, tool_name, args,
                task_id=task_id, outcome=_outcome,
                result_summary=_result_str[:200],
            )
            return result
        except asyncio.TimeoutError:
            audit.log_action(
                self.name, tool_name, args,
                task_id=task_id, outcome="timeout",
                result_summary="asyncio.TimeoutError",
            )
            raise
        except PermissionError as e:
            audit.log_action(
                self.name, tool_name, args,
                task_id=task_id, outcome="policy_denied",
                result_summary=str(e),
            )
            raise
        except Exception as e:
            audit.log_action(
                self.name, tool_name, args,
                task_id=task_id, outcome="exception",
                result_summary=str(e),
            )
            raise

    async def _execute_explicit_tool(self, task: AgentTask) -> Optional[str]:
        """Permet d'imposer un tool via context (tool + args)."""
        tool_name = str(task.context.get("tool", "") or "").strip()
        if not tool_name:
            return None

        args = task.context.get("args", {})
        if not isinstance(args, dict):
            args = {}

        return await self._call_tool(tool_name, args)

    # ── Phase 0.5 — Filtre anti-pollution SuccessStore ────────────────────────

    @staticmethod
    def _normalize_text_for_filter(s: str) -> str:
        """Phase 0.5 — Normalise un texte : lowercase + retrait accents.

        Permet un matching robuste insensible à la casse et aux accents
        ("CASSÉ", "Cassé", "casse" → "casse" ; "abîmer", "ABIMER" → "abimer").
        """
        import unicodedata as _ud_norm
        if not s:
            return ""
        nfkd = _ud_norm.normalize("NFKD", str(s).lower())
        return "".join(c for c in nfkd if not _ud_norm.combining(c))

    @staticmethod
    def _should_block_success_capture(
        task_description: str,
        outcome_summary: str = "",
        current_task: Any = None,
    ) -> tuple[bool, str]:
        """Phase 0.5 — Filtre anti-pollution SuccessStore.

        Bloque la capture d'un pattern de succès si le contexte contient des
        signaux destructeurs ou ambigus ("casser", "bugs subtils", "saboter"...),
        SAUF si l'utilisateur a explicitement indiqué une intention de test
        ("volontairement", "test de bug", "chaos engineering"...).

        Contexte du bug (DIAGNOSTIC_PROD.md §13 — session 15:55 du 16/05) :
        La tâche "corriger le site et casser à certain endroit à toi de trouver"
        a fait capturer un pattern `Introduire des bugs subtils en modifiant
        des constantes numériques` comme un succès — Lumena apprenait donc à
        casser intentionnellement les projets utilisateur sur demande ambiguë.

        Inspecte un maximum de sources pour ne pas être contournable par une
        reformulation de description en aval :
          - task_description (paramètre direct)
          - outcome_summary (paramètre direct)
          - current_task.description (si dispo)
          - current_task.context (si dispo, str-ifié)

        Returns:
            (blocked, reason) : (True, "destructif:<mot>") si bloqué, (False, "") sinon.
        """
        import re as _re_filter

        # 1. Collecte du contexte texte disponible
        parts: list[str] = [
            str(task_description or ""),
            str(outcome_summary or ""),
        ]
        if current_task is not None:
            parts.append(str(getattr(current_task, "description", "") or ""))
            ctx_val = getattr(current_task, "context", None)
            if ctx_val:
                try:
                    parts.append(str(ctx_val))
                except Exception:
                    pass
        combined = " | ".join(p for p in parts if p)
        if not combined:
            return False, ""

        # 2. Normalisation : case + accent insensitive
        normalized = SubAgent._normalize_text_for_filter(combined)

        # 3. Patterns destructifs (regex avec word boundary pour éviter
        # les faux positifs : "casse" ne matche pas "casserole", "bug" ne
        # matche pas "debugger", etc.)
        destructive_patterns = (
            r"\bcasse[rs]?\b",        # casse, casser, casses, cassé (après NFKD)
            r"\bbuguer\b",
            r"\bbugs?\b",             # bug, bugs (peut bloquer "fix le bug" — strict volontaire)
            r"\bdetruire\b",          # détruire → detruire
            r"\bsaboter\b",
            r"\babimer\b",            # abîmer → abimer
            r"\bfoirer\b",
            r"\bpourrir\b",
            r"\bendommager\b",
            r"\bdegrader\b",          # dégrader → degrader
            r"\bregression\b",        # régression → regression
        )
        matched: list[str] = []
        for pat in destructive_patterns:
            m = _re_filter.search(pat, normalized)
            if m:
                matched.append(m.group(0))

        if not matched:
            return False, ""

        # 4. Intention explicite annule le blocage
        intention_markers = (
            "volontairement",
            "intentionnellement",
            "test de bug",
            "simuler",
            "chaos engineering",
            "pour tester",
            "test destructif",
        )
        if any(m in normalized for m in intention_markers):
            return False, ""

        return True, f"destructif:{matched[0]}"


# ── Registre des délégations actives (cancel coopératif) ────────────────────
# parent_task_id (TaskOrchestrator) → asyncio.Task en cours d'exécution.
# Permet d'annuler une délégation depuis n'importe quel composant externe.
import threading as _threading_delegate
_active_delegates: Dict[str, "asyncio.Task[Any]"] = {}
_active_delegates_lock: _threading_delegate.Lock = _threading_delegate.Lock()


def _register_active_delegate(parent_task_id: str, task: "asyncio.Task[Any]") -> None:
    with _active_delegates_lock:
        _active_delegates[parent_task_id] = task


def _unregister_active_delegate(parent_task_id: str) -> None:
    with _active_delegates_lock:
        _active_delegates.pop(parent_task_id, None)


def cancel_active_delegate(parent_task_id: str) -> bool:
    """Annule la délégation active associée à parent_task_id. Thread-safe."""
    with _active_delegates_lock:
        t = _active_delegates.get(parent_task_id)
    if t and not t.done():
        t.cancel()
        return True
    return False


def is_delegate_active(parent_task_id: str) -> bool:
    with _active_delegates_lock:
        return parent_task_id in _active_delegates


# ── Registre des tâches agent background ────────────────────────────────────
# task_id (ca_*) → asyncio.Task — permet l'annulation réelle et le cleanup.
import threading as _threading_bg
import builtins as _builtins_bg
if not hasattr(_builtins_bg, "_lumena_bg_agent_tasks"):
    _builtins_bg._lumena_bg_agent_tasks = {}
if not hasattr(_builtins_bg, "_lumena_bg_agent_tasks_lock"):
    _builtins_bg._lumena_bg_agent_tasks_lock = _threading_bg.Lock()
_bg_agent_tasks: Dict[str, "asyncio.Task[Any]"] = _builtins_bg._lumena_bg_agent_tasks
_bg_agent_tasks_lock: _threading_bg.Lock = _builtins_bg._lumena_bg_agent_tasks_lock


def _register_bg_agent(task_id: str, task: "asyncio.Task[Any]") -> None:
    with _bg_agent_tasks_lock:
        _bg_agent_tasks[task_id] = task


def _unregister_bg_agent(task_id: str) -> None:
    with _bg_agent_tasks_lock:
        _bg_agent_tasks.pop(task_id, None)


def cancel_bg_agent_task(task_id: str) -> bool:
    """Annule une tâche agent background par son task_id. Thread-safe.

    Retourne True si la tâche était active et a été annulée,
    False si introuvable ou déjà terminée.
    """
    with _bg_agent_tasks_lock:
        t = _bg_agent_tasks.get(task_id)
    if t and not t.done():
        t.cancel()
        return True
    return False


def is_bg_agent_active(task_id: str) -> bool:
    """True si la tâche bg est enregistrée et non terminée."""
    with _bg_agent_tasks_lock:
        t = _bg_agent_tasks.get(task_id)
    return t is not None and not t.done()


_CODE_AGENT_MAX_ITER = int(os.environ.get("LUMENA_CODE_AGENT_MAX_ITER", "50"))
_CODE_AGENT_MAX_OUTER_RETRIES = int(os.environ.get("LUMENA_CODE_AGENT_MAX_OUTER_RETRIES", "3"))  # Boucle externe

# ── Classification structurée des outcomes d'appels outils ───────────────────
_OUTCOME_NOT_FOUND: frozenset = frozenset({
    "non trouvé", "not found", "introuvable", "aucun outil", "unknown tool",
})
_OUTCOME_POLICY: frozenset = frozenset({
    "permission denied", "accès refusé", "policy denied",
    "interdit", "non autorisé", "non autorise", "forbidden", "access denied",
    "bloqué", "bloquee", "bloquée", "blocked", "whitelist",
})
_OUTCOME_EXCEPTION: frozenset = frozenset({
    "nameerror", "attributeerror", "keyerror", "typeerror",
})


def _classify_tool_outcome(result_lower: str) -> str:
    """Retourne l'outcome structuré d'un appel outil à partir du résultat texte.

    Ordre de priorité : tool_not_found > policy_denied > exception > success.
    N'est utilisé que lorsque l'outil retourne une chaîne sans lever d'exception.
    """
    if any(p in result_lower for p in _OUTCOME_NOT_FOUND):
        return "tool_not_found"
    if any(p in result_lower for p in _OUTCOME_POLICY):
        return "policy_denied"
    if any(p in result_lower for p in _OUTCOME_EXCEPTION):
        return "exception"
    return "success"


@dataclass
class ActionResult:
    """Observation structurée retournée par _execute_loop_action."""
    summary: str
    detail: str = ""

    def __str__(self) -> str:
        return self.summary

    def __contains__(self, item: str) -> bool:
        return item in self.summary or item in self.detail

    def __iadd__(self, other: str) -> "ActionResult":
        self.detail += other
        return self

    def __getitem__(self, key):
        return str(self.summary)[key]

    def full(self, max_detail: int = 16000) -> str:
        """Retourne summary + detail pour injection dans les messages LLM."""
        if not self.detail:
            return self.summary
        d = self.detail
        if len(d) > max_detail:
            head = d[:int(max_detail * 0.6)]
            tail = d[len(d) - int(max_detail * 0.4):]
            d = f"{head}\n\n[... {len(self.detail) - max_detail} chars tronqués ...]\n\n{tail}"
        return f"{self.summary}\n---\n{d}"


_COMPLEX_KEYWORDS = {"test", "debug", "fix", "erreur", "refactor", "bug", "error", "crash", "fail"}


# ── P2 Supreme: Table de normalisation Unicode typographique → ASCII ──
_UNICODE_PUNCT_MAP = {
    # Tirets Unicode → ASCII minus
    '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-',
    '\u2014': '-', '\u2015': '-', '\u2212': '-',
    # Guillemets simples → apostrophe ASCII
    '\u2018': "'", '\u2019': "'", '\u201A': "'", '\u201B': "'",
    # Guillemets doubles → quote ASCII
    '\u201C': '"', '\u201D': '"', '\u201E': '"', '\u201F': '"',
    # Espaces spéciaux → espace ASCII
    '\u00A0': ' ', '\u2002': ' ', '\u2003': ' ', '\u2004': ' ',
    '\u2005': ' ', '\u2006': ' ', '\u2007': ' ', '\u2008': ' ',
    '\u2009': ' ', '\u200A': ' ', '\u202F': ' ', '\u205F': ' ',
    '\u3000': ' ',
}
_UNICODE_PUNCT_TABLE = str.maketrans(_UNICODE_PUNCT_MAP)


def _normalize_punctuation(text: str) -> str:
    """Normalise les caractères Unicode typographiques en ASCII."""
    return text.translate(_UNICODE_PUNCT_TABLE)


# ── P5 Supreme: Classification des erreurs LLM ──
def _classify_llm_error(exc: Exception) -> tuple[str, str]:
    """
    Classifie une erreur LLM. Retourne (category, action).
    Categories: rate_limit, overload, auth, timeout, format, unknown
    Actions: retry_wait, timeout_recovery, abort, retry
    """
    msg = str(exc).lower()
    status = getattr(exc, "status_code", 0) or getattr(exc, "status", 0)

    if status == 429 or "rate" in msg or "too many" in msg:
        return "rate_limit", "retry_wait"
    if status == 503 or "overloaded" in msg or "capacity" in msg:
        return "overload", "retry_wait"
    if status in (401, 403) or "unauthorized" in msg or "api key" in msg:
        return "auth", "abort"
    if "timeout" in msg or "timed out" in msg or isinstance(exc, asyncio.TimeoutError):
        return "timeout", "timeout_recovery"
    if status == 400 or "invalid" in msg:
        return "format", "retry"
    return "unknown", "retry"


# ── P1 Supreme: Détection boucles multi-niveaux ──
class LoopDetector:
    """5 détecteurs : repeat, ping-pong, no-progress, circuit breaker, cmd-fail streak."""

    __slots__ = (
        "history", "history_size", "repeat_threshold",
        "pingpong_threshold", "noprogress_threshold",
        "circuit_breaker", "total_failures",
        "consecutive_cmd_failures", "node_check_passed",
    )

    def __init__(
        self,
        history_size: int = 30,
        repeat_threshold: int = 3,
        pingpong_threshold: int = 6,
        noprogress_threshold: int = 5,
        circuit_breaker: int = 25,
    ):
        self.history: list[dict] = []
        self.history_size = history_size
        self.repeat_threshold = repeat_threshold
        self.pingpong_threshold = pingpong_threshold
        self.noprogress_threshold = noprogress_threshold
        self.circuit_breaker = circuit_breaker
        self.total_failures = 0
        self.consecutive_cmd_failures = 0  # run_command failures in a row
        self.node_check_passed = False      # JS syntax already confirmed OK

    @staticmethod
    def _hash(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def record(self, action: dict, result: str, is_error: bool = False) -> None:
        action_sig = json.dumps(action, sort_keys=True, default=str)
        action_type = action.get("action", "")
        result_lower = result.lower()[:200]
        self.history.append({
            "action_sig": self._hash(action_sig),
            "action_type": action_type,
            "result_hash": self._hash(result[:500]),
            "is_error": is_error,
        })
        if is_error:
            self.total_failures += 1
        # Track consecutive run_command failures
        if action_type == "run_command":
            if is_error or "exit:1" in result_lower or "n'est pas reconnu" in result_lower or "non autorise" in result_lower or "invalide" in result_lower:
                self.consecutive_cmd_failures += 1
            else:
                self.consecutive_cmd_failures = 0
                # Detect node --check passing
                cmd = (action.get("command", "") or "").lower()
                if "node" in cmd and "--check" in cmd and "exit:0" in result_lower:
                    self.node_check_passed = True
        else:
            # Non-run_command action resets cmd failure streak but not node_check_passed
            self.consecutive_cmd_failures = 0
        if len(self.history) > self.history_size:
            self.history.pop(0)

    def check(self) -> tuple[bool, str]:
        if not self.history:
            return False, ""

        # Détecteur 1: Generic repeat (même action N fois consécutives)
        streak = 1
        for i in range(len(self.history) - 2, -1, -1):
            if self.history[i]["action_sig"] == self.history[-1]["action_sig"]:
                streak += 1
            else:
                break
        if streak >= self.repeat_threshold:
            return True, f"même action {streak}x consécutives"

        # Détecteur 2: Ping-pong (A→B→A→B alternance)
        if len(self.history) >= self.pingpong_threshold:
            tail = self.history[-self.pingpong_threshold:]
            sigs = [h["action_sig"] for h in tail]
            unique = set(sigs)
            if len(unique) == 2:
                is_alternating = all(
                    sigs[i] != sigs[i + 1] for i in range(len(sigs) - 1)
                )
                if is_alternating:
                    types = list(set(h["action_type"] for h in tail))
                    return True, f"ping-pong {'↔'.join(types)} ({self.pingpong_threshold}x)"

        # Détecteur 3: No-progress (même erreur N fois, actions différentes)
        if len(self.history) >= self.noprogress_threshold:
            tail = self.history[-self.noprogress_threshold:]
            result_hashes = [h["result_hash"] for h in tail]
            if len(set(result_hashes)) == 1 and all(h["is_error"] for h in tail):
                return True, f"no-progress: même erreur {self.noprogress_threshold}x"

        # Détecteur 4: Circuit breaker (trop d'échecs total)
        if self.total_failures >= self.circuit_breaker:
            return True, f"circuit breaker: {self.total_failures} échecs total"

        # Détecteur 5: run_command failures en rafale (5+ consécutifs)
        if self.consecutive_cmd_failures >= 5:
            return True, f"run_command: {self.consecutive_cmd_failures} échecs consécutifs (utilise read_file + done)"

        return False, ""


# ── P3 Supreme: Estimation tokens + seuils compaction ──
_CONTEXT_WINDOW_TOKENS = int(os.environ.get("LUMENA_CODE_AGENT_CONTEXT_WINDOW", "65536"))
_COMPACTION_RATIO = 0.85
_MIN_COMPACTION_MESSAGES = 8


def _estimate_tokens(messages: list[dict]) -> int:
    """Estimation rapide tokens (chars/4 + overhead par message)."""
    total = 0
    for m in messages:
        total += len(m.get("content", "")) // 4 + 4
    return total


# ── P4 Supreme: Sections de prompt par domaine ──


_WEB_SIGNALS = (".html", ".css", ".js", ".ts", "website", "site web", "page web",
                "frontend", "landing", "bootstrap", "tailwind", "react", "vue")
_PY_SIGNALS = (".py", "python", "pytest", "django", "flask", "fastapi", "pip")


def _extract_explicit_code_targets(task_description: str) -> set[str]:
    """Extrait les basenames de fichiers explicitement mentionnés dans une tâche."""
    if not task_description:
        return set()
    return {
        m.group(0).lower()
        for m in re.finditer(
            r"\b[\w.\-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|ya?ml|toml)\b",
            task_description,
            re.IGNORECASE,
        )
    }


def _is_local_bugfix_task(
    task_description: str,
    *,
    workspace_path: Any = None,
    resolved_intent: str = "auto",
) -> bool:
    """Détecte les correctifs locaux qui supportent mal les garde-fous lourds."""
    desc = (task_description or "").lower()
    if not desc:
        return False
    broad_scope_markers = (
        "refonte", "rewrite", "réécris", "reécris", "from scratch",
        "fusionne", "merge tout", "tout le projet", "whole project",
        "architecture", "restructure", "migration", "migre",
    )
    if any(k in desc for k in broad_scope_markers):
        return False

    # Phase 0.3 — Bypass si l'utilisateur demande une INVESTIGATION
    # multi-fichiers. Ces tâches ressemblent à des correctifs ("corrige",
    # "bug") mais sont en réalité des chantiers d'analyse qui bénéficient
    # massivement de la phase Architect.
    # Cas observé en prod (session 15:26 du 16/05) : "corrige le site cassé,
    # à toi de trouver" → faussement classé bugfix local → Architect skip
    # → 14 itérations sans plan, dont 2 perdues sur cache hit identique.
    investigation_markers = (
        "à toi de trouver", "a toi de trouver", "trouve et corrige",
        "analyse et corrige", "identifie les", "cherche les bugs",
        "audit", "analyse complète", "analyse complete",
        "tous les bugs", "tous les problèmes", "tous les problemes",
    )
    if any(k in desc for k in investigation_markers):
        return False

    file_targets = _extract_explicit_code_targets(task_description)

    # Phase 0.4 — Heuristique de cible globale (sans fichier précis)
    # Cas observé en prod (session 15:44 du 16/05) : ReAct a reformulé
    # "à toi de trouver" en "Corriger les problèmes du site" → les markers
    # Phase 0.3 ne matchent plus → Architect skip à nouveau.
    # Solution : si la description vise une cible GLOBALE ("site", "projet",
    # "page", "app") SANS mentionner de fichier précis, c'est un chantier
    # multi-fichiers qui bénéficie d'Architect, même si "corriger" est présent.
    if not file_targets and workspace_path:
        global_target_markers = (
            "le site", "du site", "ce site", "tout le site",
            "le projet", "du projet", "ce projet",
            "la page", "de la page", "les pages",
            "l'app", "l app", "l'application", "l application",
        )
        if any(k in desc for k in global_target_markers):
            return False

    # Une erreur console qui cite plusieurs fichiers n'est pas un micro-fix :
    # l'ordre de chargement, les globals JS ou les contrats entre modules doivent
    # etre planifies avant edition.
    if workspace_path and resolved_intent == "modify":
        if len(file_targets) >= 2:
            return False
        runtime_error_markers = (
            "uncaught", "referenceerror", "syntaxerror", "typeerror",
            "not defined", "already been declared", "failed to load",
            "traceback", "exception",
        )
        runtime_hits = sum(1 for marker in runtime_error_markers if marker in desc)
        project_markers = (
            "projet", "site", "app", "application", "jeu", "workspace",
            "serveur", "console",
        )
        if file_targets and runtime_hits >= 2 and any(k in desc for k in project_markers):
            return False

    local_fix_markers = (
        "corrige", "correct", "fix", "bug", "erreur", "crash", "plante",
        "marche pas", "ne marche pas", "cassé", "casse", "bloque",
        "touche", "entrée", "enter", "bouton", "click", "clic",
        "répare", "repare",
    )
    has_workspace_anchor = bool(workspace_path)
    intent_ok = resolved_intent in {"modify", "read"}
    local_signal = any(k in desc for k in local_fix_markers) or bool(file_targets)
    return intent_ok and local_signal and (has_workspace_anchor or bool(file_targets))


def _build_system_prompt(
    task_description: str,
    workspace_files: list[str] | None = None,
    *,
    mode: str = "auto",
    model_name: str = "",
) -> str:
    """Compose le prompt système avec exemples conditionnels + section domaine.
    mode = 'create' | 'modify' | 'auto'
    model_name : si fourni et flag PROVIDER_PROMPTS actif, prepend le prompt provider-specific.
    """
    if mode == "auto":
        mode = "modify" if workspace_files else "create"

    # ── P0 Plan Suprême : prompt provider-specific (additif, opt-out via flag) ──
    _provider_prefix = _load_provider_prompt(model_name) if model_name else ""
    # ── P8.ENV_CONTEXT : injection du contexte environnement (OS, Python, cwd, git) ──
    try:
        from src.utils.env_context import build_env_context_block
        _env_block = build_env_context_block()
    except Exception:
        _env_block = ""
    _env_prefix = (_env_block + "\n") if _env_block else ""
    if _provider_prefix:
        prompt = (
            _env_prefix
            + "== PROVIDER-SPECIFIC HINTS ==\n"
            + _provider_prefix.rstrip()
            + "\n\n== CORE INSTRUCTIONS ==\n"
            + _CODE_AGENT_SYSTEM
            + _SHORT_EXAMPLE
        )
    else:
        prompt = _env_prefix + _CODE_AGENT_SYSTEM + _SHORT_EXAMPLE
    desc_lower = task_description.lower()
    if any(kw in desc_lower for kw in _COMPLEX_KEYWORDS):
        prompt += _LONG_EXAMPLE

    # ── P0b Plan Suprême : guide des outils (when/when-not/good/bad) ──
    _tool_hints = _load_tool_descriptions()
    if _tool_hints:
        prompt += _tool_hints

    # Injecter les instructions spécifiques au mode
    if mode == "modify":
        prompt += _MODIFICATION_INSTRUCTIONS
    else:
        prompt += _CREATION_INSTRUCTIONS

    # Détection domaine automatique
    web_score = sum(1 for s in _WEB_SIGNALS if s in desc_lower)
    py_score = sum(1 for s in _PY_SIGNALS if s in desc_lower)
    if workspace_files:
        _files_str = " ".join(workspace_files).lower()
        web_score += sum(1 for s in (".html", ".css", ".js") if s in _files_str)
        py_score += sum(1 for s in (".py", "requirements") if s in _files_str)
    if web_score > py_score:
        prompt += _PROMPT_WEB_SECTION
    elif py_score > web_score:
        prompt += _PROMPT_PYTHON_SECTION
    else:
        prompt += _PROMPT_GENERAL_SECTION

    return prompt


def _parse_action_json(text: str) -> dict | None:
    """Extrait un objet JSON action depuis une réponse LLM.

    DS-1.3 — si la réponse est au format DSML natif de deepseek
    (`<｜｜DSML｜｜invoke name="X">…`), on en extrait la PREMIÈRE action complète
    (nom + paramètres) au lieu de la perdre : même filet que le parser ReAct."""
    from src.llm.output_normalizer import dsml_first_action, extract_json_object
    action = extract_json_object(text)
    if isinstance(action, dict) and action:
        return action
    return dsml_first_action(text)


# ── F4: Bracket counting aware of strings/comments ──────────────────────────
import re as _re_brk

_RE_STRINGS_BRK = _re_brk.compile(
    r'"(?:[^"\\]|\\.)*"'   # double quotes
    r"|'(?:[^'\\]|\\.)*'"  # single quotes
    r"|`(?:[^`\\]|\\.)*`"  # template literals
)
_RE_LINE_COMMENTS_BRK = _re_brk.compile(r'//[^\n]*')
_RE_BLOCK_COMMENTS_BRK = _re_brk.compile(r'/\*[\s\S]*?\*/')

_WEB_BRACKET_EXTS = frozenset(
    (".js", ".ts", ".jsx", ".tsx", ".vue", ".html", ".htm", ".css")
)


def _count_brackets_clean(code: str) -> tuple[int, int]:
    """Compte brackets en ignorant strings et commentaires.
    Retourne (net_braces, net_parens)."""
    clean = _RE_STRINGS_BRK.sub('""', code)
    clean = _RE_BLOCK_COMMENTS_BRK.sub('', clean)
    clean = _RE_LINE_COMMENTS_BRK.sub('', clean)
    return (
        clean.count("{") - clean.count("}"),
        clean.count("(") - clean.count(")"),
    )


def _locate_bracket_errors(code: str, ext: str = ".js") -> str:
    """Localise les lignes où les brackets se déséquilibrent.
    Retourne un message lisible avec les numéros de ligne, ou '' si OK."""
    clean = _RE_STRINGS_BRK.sub('""', code)
    clean = _RE_BLOCK_COMMENTS_BRK.sub('', clean)
    clean = _RE_LINE_COMMENTS_BRK.sub('', clean)
    lines = clean.split("\n")
    depth = 0
    paren_depth = 0
    last_open_lines: list[int] = []  # stack de lignes d'ouverture {
    errors: list[str] = []
    for i, line in enumerate(lines, 1):
        for ch in line:
            if ch == "{":
                depth += 1
                last_open_lines.append(i)
            elif ch == "}":
                depth -= 1
                if last_open_lines:
                    last_open_lines.pop()
                if depth < 0:
                    errors.append(f"L{i}: accolade fermante '}}' en trop (profondeur négative)")
                    depth = 0
            elif ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
                if paren_depth < 0:
                    errors.append(f"L{i}: parenthèse fermante ')' en trop")
                    paren_depth = 0
    if depth > 0 and last_open_lines:
        for ln in last_open_lines[-min(3, len(last_open_lines)):]:
            errors.append(f"L{ln}: accolade ouvrante '{{' jamais fermée (depth restant: {depth})")
    if paren_depth > 0:
        errors.append(f"Fin de fichier: {paren_depth} parenthèse(s) non fermée(s)")
    if not errors:
        return ""
    return "ERREURS DE BRACKETS DÉTECTÉES:\n" + "\n".join(errors[:8])


class CodeAgent(SubAgent):
    """Agent specialise pour les taches de code - boucle iterative LLM."""

    def __init__(self):
        super().__init__(
            agent_type=AgentType.CODE,
            name="CodeAgent",
            tools=["read_own_code", "edit_own_code", "run_tests", "run_command", "grep_search"]
        )
        # P8: session memory inter-tâches (singleton → persiste entre appels)
        self._session_memory: dict = {
            "files_read": {},    # path → summary (200 chars)
            "errors_seen": [],   # erreurs clés
            "edits_done": [],    # "path: action"
            "grep_zero_results": {},  # (pattern, path) → count (tracking 0-result repeats)
        }
        self._session_memory_last_used: float = 0.0
        self._SESSION_MEMORY_TTL = 4 * 3600  # 4 heures
        # P5: compteur d'échecs d'édition par fichier (réinitialisé à chaque tentative)
        self._edit_fail_for_path: dict[str, int] = {}
        # Phase 0.2 — Compteur d'échecs run_command par signature normalisée
        # (clé = tête de commande + indice d'erreur, pas chemin complet)
        # Déclenche une Reflexion à la 3e répétition pour aider à réveiller
        # l'apprentissage sur les patterns d'errance shell observés en prod.
        self._cmd_fail_count: dict[str, int] = {}
        # P6: compteur de self-repair syntaxe par fichier + snapshot dernier état valide
        self._self_repair_count: int = 0
        self._self_repair_count_per_file: dict[str, int] = {}
        self._syntax_clean_snapshot: dict[str, str] = {}  # path → dernier contenu syntaxiquement valide
        self._edit_restricted_files: set[str] = set()     # fichiers restaurés → rappel persistant edit_lines/str_replace
        # P_ANTI_REREAD: nb de read_file par chemin (reset par tâche, pas par tentative)
        self._read_count_per_file: dict[str, int] = {}
        # OBS: compteurs pour metrics.jsonl (reset par tâche)
        self._reflexion_generated_count: int = 0
        self._grep_zero_repeats: int = 0
        self._applied_reflexion_ids: list[str] = []
        # P1/P2 : SuccessStore + auto-eval post-succès
        self._applied_success_ids: list[str] = []
        self._success_generated_count: int = 0
        self._tools_used_this_task: list[str] = []
        self._auto_eval_triggered: bool = False
        self._edited_files: set[str] = set()
        self._done_soft_gate_used: int = 0
        try:
            from src.agents.codeagent_todo import CodeAgentTodoState
            self._codeagent_todo = CodeAgentTodoState()
        except Exception:
            self._codeagent_todo = None

    async def _execute_task(self, task: AgentTask) -> "str | AgentResult":
        """Exécute une tâche de code."""
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        description = task.description.lower()

        # ── Fast-paths pour les actions simples (pas de LLM requis) ──
        if _is_simple_read(description, task.context):
            file_path = task.context.get("file_path", "core.py")
            return await self._call_tool("read_own_code", {"file_path": file_path})

        if _is_simple_grep(description):
            pattern = str(task.context.get("pattern", "") or task.context.get("query", "")).strip()
            if not pattern:
                pattern = task.description
            path = str(task.context.get("path", "src/")).strip() or "src/"
            return await self._call_tool(
                "grep_search",
                {"pattern": pattern, "path": path, "ignore_case": True, "is_regex": False, "max_results": 80},
            )

        if _is_simple_edit(description, task.context):
            file_path = str(task.context.get("file_path", "")).strip()
            search = str(task.context.get("search", "")).strip()
            replace = str(task.context.get("replace", "")).strip()
            if file_path and search:
                return await self._call_tool(
                    "edit_own_code",
                    {
                        "file_path": file_path,
                        "old_content": search,
                        "new_content": replace,
                    },
                )
            missing = [f for f in ["file_path", "search", "replace"] if not task.context.get(f)]
            return self._result_needs_input(
                task,
                "CodeAgent: édition automatique impossible — contexte incomplet.",
                missing_fields=missing,
                next_action="Relancer avec context={file_path, search, replace}",
            )

        if _is_simple_test(description):
            return await self._call_tool(
                "run_tests",
                {"test_path": task.context.get("test_path", "")}
            )

        # ── Boucle itérative pour les tâches complexes ──
        return await self._execute_iterative_with_brain(task)

    async def _execute_iterative_with_brain(self, task: AgentTask) -> AgentResult:
        """Lance la boucle historique avec le cerveau sélectionné pour la tâche.

        DebugAgent et RefactorAgent enrichissent la tâche avant d'entrer dans la
        même boucle. Le scope doit donc vivre dans ce point partagé, sinon ces
        deux variantes voient le marqueur Codex sans cerveau actif et échouent.
        Hors abonnement Codex, l'appel reste exactement celui d'avant.
        """
        if (task.context or {}).get("_codex_brain"):
            from ..llm.execution_router import codex_codeagent_brain_scope

            async with codex_codeagent_brain_scope(self) as (active, brain):
                if not active or brain is None:
                    return AgentResult(
                        task_id=task.task_id,
                        success=False,
                        output=(
                            "Le CodeAgent demande l'abonnement Codex, mais cette "
                            "surface n'est plus active. Aucun fallback API n'a ete utilise."
                        ),
                        status_code=StatusCode.ERROR,
                        meta={
                            "engine": "codex_subscription",
                            "fallback_used": False,
                        },
                    )
                # Les captures SuccessStore et auto-eval sont historiquement
                # lancees en fire-and-forget depuis `_iterative_code_loop`.
                # Avec un cerveau prive, elles doivent finir AVANT sa fermeture;
                # sinon elles voient le marqueur Codex mais un cerveau deja ferme
                # et tentent ensuite de retomber sur le modele global. Le tableau
                # reste task-scoped sous `_exec_lock`; le chemin API ne le cree pas.
                post_tasks: list[asyncio.Task] = []
                self._codex_post_success_tasks = post_tasks
                try:
                    result = await self._iterative_code_loop(task)
                    if post_tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*post_tasks, return_exceptions=True),
                                timeout=120.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[Codex/CodeAgent brain] apprentissage post-succes "
                                "interrompu apres 120 s"
                            )
                            for pending in post_tasks:
                                if not pending.done():
                                    pending.cancel()
                    result.meta = {
                        **(result.meta or {}),
                        "engine": "codex_subscription",
                        "model": brain.model_name,
                        "fallback_used": False,
                    }
                    return result
                finally:
                    self._codex_post_success_tasks = None
        return await self._iterative_code_loop(task)

    # ── Boucle itérative (outer retry + inner loop) ──────────────────────────

    async def _iterative_code_loop(self, task: AgentTask) -> AgentResult:
        """
        Boucle externe (retry si bloqué) + boucle interne (LLM->action->obs).
        Outer loop jusqu'à _CODE_AGENT_MAX_OUTER_RETRIES,
        boucle interne jusqu'à _CODE_AGENT_MAX_ITER.
        """
        import time as _time_metrics
        _metrics_start = _time_metrics.perf_counter()
        _metrics_attempts = 0
        llm = self._get_llm(task)

        # ── Pattern Architect+Executor (best-in-class, cf. Aider/Cline) ──
        # • Boucle d'exécution = deepseek-chat (rapide, tool-calling natif, 50 iters)
        # • Phase Architect (dans _single_code_attempt) = deepseek-reasoner (1 appel, CoT long)
        # Raison : Reasoner perd son CoT entre tours (doc officielle) → mauvais pour boucles,
        # mais excellent pour planifier UNE fois. Chat exécute ensuite en suivant le plan.
        _model = getattr(llm, "model_name", "") or ""
        # P5 — cap d'itérations adapté au profil comportemental du modèle
        try:
            from ..llm.model_profile import get_model_profile as _get_profile
            _model_profile = _get_profile(_model)
            _profile_iter_cap = _model_profile.sub_agent_iter_cap
            if _profile_iter_cap > 0:
                _effective_max_iter = min(_CODE_AGENT_MAX_ITER, _profile_iter_cap)
                if _effective_max_iter < _CODE_AGENT_MAX_ITER:
                    logger.debug(
                        "[P5] iter cap profil '{}': {} → {} iters max",
                        _model, _CODE_AGENT_MAX_ITER, _effective_max_iter,
                    )
            else:
                _effective_max_iter = _CODE_AGENT_MAX_ITER
        except Exception:
            _effective_max_iter = _CODE_AGENT_MAX_ITER
        if "deepseek" in _model.lower() and "reasoner" in _model.lower():
            # L'utilisateur a explicitement demandé reasoner → on le ramène à chat pour la boucle
            try:
                from ..llm.multi_provider import MultiProviderLLM
                llm = MultiProviderLLM(model_name="deepseek-chat")
                logger.info(
                    "🎯 [CodeAgent] Boucle exec → deepseek-chat (rapide). "
                    "Architect utilisera reasoner 1× pour planifier.",
                )
            except Exception as exc:
                logger.warning("[CodeAgent] Swap chat échoué ({}), garde {}", exc, _model)

        prior_failures: list[str] = []
        last_result: AgentResult | None = None
        # P7 — telemetry : début de tâche CodeAgent
        if _TELEMETRY_AVAILABLE:
            try:
                publish_trace(
                    stage="codeagent_start",
                    status="start",
                    mode="codeagent",
                    model=_model,
                    task_id=getattr(task, "task_id", None),
                    summary=str(getattr(task, "description", ""))[:120],
                )
            except Exception:
                pass
        # Reset compteur anti-relecture pour cette tâche (survit aux outer retries
        # → si attempt 1 a déjà lu un fichier 3×, attempt 2 ne le relira pas non plus)
        self._read_count_per_file = {}
        # FIX garde session : reset files_read à chaque nouvelle tâche.
        # Sinon les fichiers lus en tâche N bloquent le read_file en tâche N+1
        # (cf. log 20:41:51 "BLOQUÉ iter=1: read_file sur index.html refusé").
        # On garde edits_done/errors_seen/grep_zero_results qui restent utiles
        # comme contexte inter-tâches sans bloquer.
        try:
            if isinstance(self._session_memory, dict):
                self._session_memory["files_read"] = {}
        except Exception:
            pass
        # Reset WorldModel pour ce workspace (structure repart à zéro par tâche)
        try:
            from src.context.world_model import reset_world_model
            _wm_ws = self._task_workspace_root or Path.cwd()
            reset_world_model(_wm_ws)
        except Exception:
            pass
        # OBS: reset compteurs observabilité pour cette tâche
        self._reflexion_generated_count = 0
        self._grep_zero_repeats = 0
        self._applied_reflexion_ids = []
        # Phase 0.2 — Reset compteur cmd fail à chaque tâche
        self._cmd_fail_count = {}
        # P1/P2 : SuccessStore + auto-eval
        self._applied_success_ids = []
        self._success_generated_count = 0
        self._tools_used_this_task = []
        self._auto_eval_triggered = False

        for attempt in range(1, _CODE_AGENT_MAX_OUTER_RETRIES + 1):
            # P2 : Au dernier retry, simplifier la description pour aider à converger
            if attempt == _CODE_AGENT_MAX_OUTER_RETRIES:
                import re as _re_simplify
                task = replace(task, description=(
                    _re_simplify.sub(
                        r"\b(?:animations?|3[Dd]|WebGL|particule|canvas|effets?\s+visuels?|responsive|mobile[\s-]first)\b",
                        "", task.description[:120], flags=_re_simplify.IGNORECASE
                    ).strip() + " (VERSION MINIMALE)"
                ))
            last_result, is_stuck = await self._single_code_attempt(
                task, llm, prior_failures, attempt,
                max_iter=_effective_max_iter,
            )
            if last_result.success:
                # P1 : capture pattern de réussite (fire-and-forget)
                try:
                    _iter_count = int((getattr(last_result, "meta", {}) or {}).get("iterations", 0))
                    _outcome = str(getattr(last_result, "output", "") or "")[:600]
                    _success_task = asyncio.create_task(
                        self._maybe_generate_success_pattern(
                            task_description=getattr(task, "description", "") or "",
                            tools_used=list(self._tools_used_this_task),
                            iterations=_iter_count,
                            outcome_summary=_outcome,
                        )
                    )
                    _codex_post = getattr(self, "_codex_post_success_tasks", None)
                    if isinstance(_codex_post, list):
                        _codex_post.append(_success_task)
                except Exception:
                    pass
                # P2 : auto-évaluation critique post-succès (fire-and-forget)
                try:
                    _auto_eval_task = asyncio.create_task(
                        self._maybe_auto_evaluate_success(
                            task_description=getattr(task, "description", "") or "",
                            edits_done=list(self._session_memory.get("edits_done", [])),
                        )
                    )
                    _codex_post = getattr(self, "_codex_post_success_tasks", None)
                    if isinstance(_codex_post, list):
                        _codex_post.append(_auto_eval_task)
                except Exception:
                    pass
                # Git commit initial après succès CodeAgent (optionnel — jamais bloquant)
                if self._task_workspace_root and self._task_workspace_root.exists():
                    try:
                        import subprocess as _gc_sp
                        _git_dir = self._task_workspace_root / ".git"
                        if _git_dir.exists():
                            _gc_sp.run(
                                ["git", "-C", str(self._task_workspace_root), "add", "-A"],
                                capture_output=True, timeout=5,
                            )
                            _commit_msg = f"Initial: {task.description[:60]}"
                            _gc_sp.run(
                                ["git", "-C", str(self._task_workspace_root), "commit",
                                 "-m", _commit_msg, "--allow-empty"],
                                capture_output=True, timeout=5,
                            )
                    except Exception:
                        pass  # git optionnel
                # Enregistrer le projet dans le registre persistant
                if self._task_workspace_root and self._task_workspace_root.exists():
                    try:
                        from ..utils.project_registry import register_project
                        register_project(
                            self._task_workspace_root,
                            description=task.description[:200],
                        )
                    except Exception:
                        pass  # registre optionnel
                # Sauvegarder dans la mémoire projet persistante (cross-session)
                if self._task_workspace_root:
                    try:
                        from src.agents.project_memory import get_project_memory
                        get_project_memory().save_session(
                            project_id=str(self._task_workspace_root),
                            summary={
                                "task": task.description[:200],
                                "files_modified": list(self._session_memory.get("edits_done", [])),
                                "errors_resolved": list(self._session_memory.get("errors_seen", [])),
                                "workspace": str(self._task_workspace_root),
                            },
                        )
                    except Exception:
                        pass  # jamais bloquant
                # P7 — telemetry succès CodeAgent
                if _TELEMETRY_AVAILABLE:
                    try:
                        import time as _t7
                        publish_trace(
                            stage="codeagent_success",
                            status="ok",
                            mode="codeagent",
                            model=_model,
                            task_id=getattr(task, "task_id", None),
                            duration_ms=(_t7.perf_counter() - _metrics_start) * 1000,
                            summary=str(getattr(last_result, "output", ""))[:120],
                        )
                    except Exception:
                        pass
                return self._finalize_metrics(last_result, task, llm, _metrics_start, attempt)
            if not is_stuck or attempt >= _CODE_AGENT_MAX_OUTER_RETRIES:
                # P7 — telemetry échec final CodeAgent
                if _TELEMETRY_AVAILABLE:
                    try:
                        import time as _t7
                        publish_trace(
                            stage="codeagent_failure",
                            status="error",
                            mode="codeagent",
                            model=_model,
                            task_id=getattr(task, "task_id", None),
                            duration_ms=(_t7.perf_counter() - _metrics_start) * 1000,
                            error=str(getattr(last_result, "status_code", ""))[:80],
                            summary=str(getattr(last_result, "output", ""))[:120],
                        )
                    except Exception:
                        pass
                return self._finalize_metrics(last_result, task, llm, _metrics_start, attempt)
            # P6 — reprise intelligente : timeout/stuck → changement de stratégie, pas répétition
            _stuck_output = last_result.output[:300]
            _stuck_status = last_result.status_code
            # Identifier la cause du blocage pour guider la stratégie de reprise
            _is_timeout = _stuck_status == StatusCode.TIMEOUT or "timeout" in _stuck_output.lower()
            _is_loop = "bloqué" in _stuck_output.lower() or "boucle" in _stuck_output.lower()
            if _is_timeout:
                _strategy_hint = (
                    "La tentative précédente a timeout. NOUVELLE STRATÉGIE OBLIGATOIRE : "
                    "décompose la tâche en étapes plus petites, commence par la partie la plus simple, "
                    "évite les gros fichiers en une seule passe. Priorise un résultat partiel fonctionnel "
                    "plutôt qu'un résultat complet bloqué."
                )
            elif _is_loop:
                _strategy_hint = (
                    "La tentative précédente a tourné en boucle sans converger. NOUVELLE STRATÉGIE OBLIGATOIRE : "
                    "change complètement d'approche — si tu utilisais str_replace, essaie write_file complet. "
                    "Si tu modifiais un fichier existant, recrée-le depuis zéro. "
                    "N'applique JAMAIS deux fois la même action qui a échoué."
                )
            else:
                _strategy_hint = (
                    "La tentative précédente a échoué. NOUVELLE STRATÉGIE : "
                    "analyse l'erreur précise, change de méthode, simplifie si nécessaire."
                )
            prior_failures.append(
                f"Tentative {attempt} [{_stuck_status}] : {_stuck_output}\n→ {_strategy_hint}"
            )
            logger.warning(
                "[P6] Tentative {} bloquée ({}), reprise #{} avec stratégie différente",
                attempt, _stuck_status, attempt + 1,
            )
            # P7 — telemetry reprise (stuck → outer retry)
            if _TELEMETRY_AVAILABLE:
                try:
                    publish_trace(
                        stage="codeagent_stuck_retry",
                        status="retry",
                        mode="codeagent",
                        model=_model,
                        task_id=getattr(task, "task_id", None),
                        error=f"attempt={attempt} status={_stuck_status}",
                        summary=_stuck_output[:120],
                    )
                except Exception:
                    pass

        return self._finalize_metrics(last_result, task, llm, _metrics_start, _CODE_AGENT_MAX_OUTER_RETRIES)  # type: ignore[return-value]

    def _finalize_metrics(self, result, task, llm, start_t: float, attempt: int):
        """P10 — enregistre les métriques CodeAgent (best-effort)."""
        try:
            import time as _t
            from src.utils.metrics import record_task_metrics
            # OBS: enrichir avec stats WorldModel + Reflexion + grep
            _wm_files = 0
            try:
                from src.context.world_model import get_world_model
                _wm_ws = self._task_workspace_root or Path.cwd()
                _wm_files = len(get_world_model(_wm_ws).active_files())
            except Exception:
                pass
            _refl_applied = len(getattr(self, "_applied_reflexion_ids", []) or [])
            _refl_generated = int(getattr(self, "_reflexion_generated_count", 0) or 0)
            _grep_repeats = int(getattr(self, "_grep_zero_repeats", 0) or 0)
            _succ_applied = len(getattr(self, "_applied_success_ids", []) or [])
            _succ_generated = int(getattr(self, "_success_generated_count", 0) or 0)
            _auto_eval = bool(getattr(self, "_auto_eval_triggered", False))
            record_task_metrics(
                task_id=getattr(task, "task_id", "") or "",
                model_name=str(getattr(llm, "model_name", "") or ""),
                attempt=int(attempt),
                iterations=int((getattr(result, "meta", {}) or {}).get("iterations", 0)),
                success=bool(getattr(result, "success", False)),
                status_code=str(getattr(result, "status_code", "")),
                duration_s=_t.perf_counter() - start_t,
                extra={
                    "stuck": bool((getattr(result, "meta", {}) or {}).get("stuck", False)),
                    "reflexions_applied": _refl_applied,
                    "reflexions_generated": _refl_generated,
                    "world_model_files": _wm_files,
                    "grep_zero_repeats": _grep_repeats,
                    "successes_applied": _succ_applied,
                    "successes_generated": _succ_generated,
                    "auto_eval": _auto_eval,
                },
            )
        except Exception:
            pass
        return result

    def _refresh_session_memory(self) -> None:
        """Reset session memory si TTL expiré."""
        import time
        now = time.time()
        if self._session_memory_last_used and (now - self._session_memory_last_used) > self._SESSION_MEMORY_TTL:
            self._session_memory = {
                "files_read": {}, "errors_seen": [], "edits_done": [],
                "grep_zero_results": {},
            }
        self._session_memory_last_used = now

    def _get_session_memory_text(self) -> str:
        """Retourne le texte de la session memory pour injection dans le prompt."""
        self._refresh_session_memory()
        mem = self._session_memory
        if not mem["files_read"] and not mem["errors_seen"] and not mem["edits_done"]:
            return ""
        parts: list[str] = []
        if mem["files_read"]:
            parts.append("=== FICHIERS EN MÉMOIRE (contenu actuel — NE PAS RELIRE, édite directement) ===")
            for p, content in list(mem["files_read"].items())[-12:]:
                parts.append(f"--- {p} ---\n{content[:10000]}")
        if mem["edits_done"]:
            parts.append("Edits:\n  " + "\n  ".join(mem["edits_done"][-10:]))
        if mem["errors_seen"]:
            parts.append("Erreurs vues:\n  " + "\n  ".join(mem["errors_seen"][-5:]))
        text = "\n".join(parts)
        # Budget: ~8000 tokens max (~32000 chars)
        if len(text) > 32000:
            text = text[:32000]
        return text

    async def _summarize_for_compaction(self, messages_to_compact: list[dict], llm) -> str | None:
        """P0 Supreme: Résumé LLM des messages compactés. None = fallback troncature."""
        _COMPACT_INSTRUCTIONS = (
            "Résume cette conversation de travail en gardant :\n"
            "- Les tâches en cours et leur statut (en cours / bloqué / terminé)\n"
            "- Le progrès batch (ex: '5/17 fichiers modifiés')\n"
            "- Les décisions prises et pourquoi\n"
            "- Les erreurs rencontrées et solutions tentées\n"
            "- Les TODOs et questions ouvertes\n"
            "PRÉSERVE EXACTEMENT tous les noms de fichiers, chemins, identifiants.\n"
            "Sois concis (max 800 mots)."
        )
        text_parts = []
        for m in messages_to_compact:
            role = m.get("role", "?")
            content = m.get("content", "")[:2000]
            text_parts.append(f"[{role}]: {content}")
        conversation_text = "\n---\n".join(text_parts)
        try:
            # Timeout généreux (30s) : sur modèles "thinking" (Kimi K2, o1, R1…)
            # un résumé de contexte long dépasse régulièrement 15s — fallback troncature
            # perd tout le résumé structuré, préférable d'attendre un peu plus.
            summary = await asyncio.wait_for(
                llm.chat(
                    messages=[
                        {"role": "system", "content": _COMPACT_INSTRUCTIONS},
                        {"role": "user", "content": conversation_text},
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                ),
                timeout=30.0,
            )
            return str(summary).strip()[:4000]
        except Exception as exc:
            logger.warning("[CodeAgent] Compaction LLM failed ({}), fallback troncature", exc)
            return None

    def _record_session_read(self, path: str, content: str) -> None:
        """Stocke le contenu du fichier pour éviter les relectures.
        Cache: 120k chars max (couvre ~2000 lignes). Injection prompt: 12k chars.
        Éviction LRU : le fichier le moins récemment accédé est supprimé.
        N'enregistre PAS les lectures de sauvegarde (.backups/) — ce ne sont
        pas des fichiers réels du projet, et les confondre avec l'original par
        basename bloque à tort les relectures légitimes.
        """
        # Filtre : ignorer .backups/, .git/, node_modules/, __pycache__/
        _p_norm = str(path).replace("\\", "/").lower()
        if any(_seg in _p_norm for _seg in ("/.backups/", "/.git/", "/node_modules/", "/__pycache__/", "/.venv/")):
            return
        files = self._session_memory["files_read"]
        # LRU: si déjà présent, supprimer pour réinsérer en fin (= accès récent)
        if path in files:
            del files[path]
        # Stocker le contenu COMPLET (pas tronqué) pour servir les plages correctement.
        # L'injection dans le prompt est tronquée à 12000 chars, mais le cache garde tout
        # pour que start_line/end_line puissent être servis depuis le cache sans relecture.
        # Le LRU à 12 fichiers empêche l'explosion mémoire.
        _MAX_CACHE_CHARS = 120_000  # ~2000 lignes de code, suffisant pour la majorité des fichiers
        files[path] = content[:_MAX_CACHE_CHARS]
        # Éjecter le moins récemment accédé si >12
        while len(files) > 12:
            oldest = next(iter(files))
            del files[oldest]

    def _record_session_edit(self, path: str, action: str) -> None:
        entry = f"{path}: {action}"
        edits = self._session_memory["edits_done"]
        if len(edits) > 20:
            edits.pop(0)
        edits.append(entry)

    def _record_session_error(self, error: str) -> None:
        errors = self._session_memory["errors_seen"]
        short = error[:200]
        if short not in errors:
            if len(errors) > 10:
                errors.pop(0)
            errors.append(short)

    def _record_grep_zero_result(self, pattern: str, path: str) -> int:
        """Enregistre un grep 0-result. Retourne le nombre d'occurrences pour ce (pattern, path).

        Utilisé dans _post_action_hooks pour détecter les répétitions de grep
        qui ne trouvent rien — ce qui indique un pattern inadapté et gaspille
        des itérations LLM (vu en prod sur recherches de commentaires CSS).
        """
        if not pattern:
            return 0
        # S'assure que la clé existe (session memory ancienne pourrait en manquer)
        store = self._session_memory.setdefault("grep_zero_results", {})
        key = f"{pattern}|{path or '.'}"
        store[key] = int(store.get(key, 0)) + 1
        return store[key]

    @staticmethod
    def _normalize_cmd_fail_signature(command: str, observation: str) -> str:
        """Phase 0.2 — Réduit une commande shell + obs à une signature stable.

        Objectif : grouper les variantes d'une même tentative ("node --check X",
        "cd Y && node --check X", "node --check \"Z\"") sous la même clé pour
        détecter les patterns d'errance shell (vu en prod : 5 tentatives
        node --check avec chemins différents → 1 seule lesson à apprendre).

        Format clé : "<verbe_principal>|<indice_erreur>"
        Exemple : "node|exit:1", "python|exit:2", "npm|chemin_introuvable"
        """
        import re as _re_cmd
        cmd = (command or "").strip().lower()
        # Retire les préfixes cd / Set-Location / cmd /c qui varient
        cmd = _re_cmd.sub(r"^(cd\s+/d\s+\".*?\"\s*&&\s*|cd\s+\S+\s*&&\s*|set-location\s+\S+\s*[;&]+\s*)", "", cmd)
        # Premier mot exécutable significatif
        first_word = cmd.split()[0] if cmd.split() else "cmd"
        first_word = _re_cmd.sub(r"[^a-z0-9_.-]", "", first_word)[:24] or "cmd"
        # Indice d'erreur depuis l'observation (tronquée et lowered)
        obs = (observation or "").lower()[:400]
        err_hint = "fail"
        # Capture le code exit EXACT (regex avec word boundary pour ne pas
        # confondre exit:1 et exit:128)
        m = _re_cmd.search(r"exit[:\s]\s*(\d+)\b", obs)
        if m:
            err_hint = f"exit:{m.group(1)}"
        elif "exit code" in obs:
            m2 = _re_cmd.search(r"exit code\s+(\d+)\b", obs)
            err_hint = f"exit:{m2.group(1)}" if m2 else "exit:?"
        elif "introuvable" in obs or "not found" in obs or "n'est pas reconnu" in obs:
            err_hint = "not_found"
        elif "denied" in obs or "permission" in obs or "non autoris" in obs:
            err_hint = "permission"
        elif "bloqu" in obs or "blocked" in obs:
            err_hint = "blocked"
        return f"{first_word}|{err_hint}"

    # ─────────────────────────────────────────────────────────────────────
    # Architect Plan → TODO_STATE SSE (affichage UI style ReAct)
    # ─────────────────────────────────────────────────────────────────────
    def _parse_architect_plan(self, text: str) -> list[dict]:
        """Parse un plan Architect en items TODO_STATE.

        Stratégie :
        1. Chercher un bloc <plan>...</plan> JSON (format canonique demandé au LLM).
           Si présent et valide → retourne items structurés {id, title, file, action, status}.
        2. Fallback : regex tolérante multi-format + filtre anti-commentaire strict.
        """
        if not text or not str(text).strip():
            return []
        import re as _re_plan
        import json as _json_plan
        text_str = str(text)

        # ── 1. Bloc <plan>...</plan> (JSON strict) ──
        _m_block = _re_plan.search(r"<plan>\s*(\{.*?\})\s*</plan>", text_str, _re_plan.DOTALL | _re_plan.IGNORECASE)
        if not _m_block:
            # Tolérance : ```json ... ``` avec "steps"
            _m_block = _re_plan.search(r"```(?:json)?\s*(\{[^`]*?\"steps\"[^`]*?\})\s*```", text_str, _re_plan.DOTALL)
        if _m_block:
            try:
                _obj = _json_plan.loads(_m_block.group(1))
                _steps = _obj.get("steps") if isinstance(_obj, dict) else None
                if isinstance(_steps, list) and _steps:
                    items: list[dict] = []
                    seen_titles: set[str] = set()
                    for _s in _steps[:12]:
                        if not isinstance(_s, dict):
                            continue
                        _title = str(_s.get("title") or "").strip()
                        if not _title or len(_title) < 4:
                            continue
                        _key = _title.lower()
                        if _key in seen_titles:
                            continue
                        seen_titles.add(_key)
                        items.append({
                            "id": len(items) + 1,
                            "title": _title[:120],
                            "file": str(_s.get("file") or "").strip().replace("\\", "/").lower(),
                            "action": str(_s.get("action") or "").strip().lower(),
                            "status": "not-started",
                        })
                    if items:
                        return items
            except (ValueError, TypeError):
                pass  # Fallback regex

        # ── 2. Fallback regex + filtre anti-commentaire ──
        items: list[dict] = []
        seen_titles: set[str] = set()

        patterns = [
            _re_plan.compile(r"^\s*#{1,4}\s*(?:[ÉéEe]tape\s+)?(\d+)\s*[\.\):\-]\s*(.+?)\s*$"),
            _re_plan.compile(r"^\s*\*\*\s*(?:[ÉéEe]tape\s+)?(\d+)\s*[\.\):\-]\s*\*\*\s*(.+?)\s*$"),
            _re_plan.compile(r"^\s*(\d+)\s*[\.\)\/:\-]\s+(.+?)\s*$"),
            _re_plan.compile(r"^\s*[-•*→]\s+(.+?)\s*$"),
        ]

        # Filtres anti-commentaire : rejeter les lignes qui ressemblent à du raisonnement
        # plutôt qu'à une action concrète.
        _COMMENTARY_STARTS = (
            "votre ", "ton ", "ta ", "vos ", "tes ",
            "une ", "un ", "cela ", "ça ", "on ",
            "actuellement", "résultat", "résultats", "resultat",
            "sur ", "dans ", "pour ", "car ", "parce ",
            "note :", "note:", "remarque", "attention",
            "exemple", "par exemple",
        )
        _CODE_MARKERS = ("document.", "getelementbyid", "queryselector", "href=", "id=\"", "class=\"", "<div", "<span", "<p ", "<a ")

        # Verbes d'action acceptés en début de title (forme impérative FR/EN)
        _ACTION_VERBS = (
            "créer", "creer", "créé", "cree", "ajouter", "ajoute", "modifier", "modifie",
            "mettre", "mets", "mise à jour", "mise a jour", "mettre à jour", "mettre a jour",
            "supprimer", "supprime", "renommer", "renomme", "déplacer", "deplacer",
            "corriger", "corrige", "refactor", "refactoriser", "refactorise",
            "tester", "teste", "vérifier", "verifier", "valider", "remplacer", "remplace",
            "implémenter", "implementer", "implemente", "installer", "installe",
            "configurer", "configure", "initialiser", "initialise", "extraire", "extrait",
            "transformer", "transforme", "finaliser", "finalise", "optimiser", "optimise",
            "create", "add", "update", "remove", "delete", "rename", "move", "fix",
            "refactor", "test", "verify", "replace", "implement", "install", "configure",
            "extract", "transform", "finalize", "optimize",
        )

        def _is_commentary(title: str) -> bool:
            _t = title.lower().strip()
            if len(_t) < 8 or len(_t) > 160:
                return True
            if _t.startswith(_COMMENTARY_STARTS):
                return True
            if any(_c in _t for _c in _CODE_MARKERS):
                return True
            # Lignes terminées par ":" = souvent titre de section, pas action
            if title.endswith(":") and len(title) < 40:
                return True
            # Vérif verbe d'action dans les 3 premiers mots
            _first_words = " ".join(_t.split()[:3])
            if not any(_v in _first_words for _v in _ACTION_VERBS):
                return True
            return False

        for line in text_str.splitlines():
            line = line.strip()
            if not line or len(line) < 6:
                continue
            matched = False
            for pat in patterns:
                m = pat.match(line)
                if m:
                    title = m.group(m.lastindex).strip()
                    title = _re_plan.sub(r"\*\*(.+?)\*\*", r"\1", title)
                    title = _re_plan.sub(r"\*(.+?)\*", r"\1", title)
                    title = _re_plan.sub(r"`(.+?)`", r"\1", title)
                    title = title.rstrip(":.-").strip()
                    if _is_commentary(title):
                        matched = True
                        break
                    if title.lower() not in seen_titles:
                        seen_titles.add(title.lower())
                        items.append({
                            "id": len(items) + 1,
                            "title": title[:120],
                            "file": "",
                            "action": "",
                            "status": "not-started",
                        })
                    matched = True
                    break
            if matched and len(items) >= 10:
                break

        return items

    def _emit_architect_plan(self, current_tool: str = "") -> None:
        """Émet le plan Architect au format TODO_STATE pour l'affichage SSE."""
        items = getattr(self, "_architect_plan_items", None)
        if not items:
            return
        cursor = getattr(self, "_architect_plan_cursor", 0)
        total = len(items)
        payload = []
        for idx, it in enumerate(items):
            if idx < cursor:
                status = "completed"
            elif idx == cursor and cursor < total:
                status = "in-progress"
            else:
                status = "not-started"
            entry = {"id": it["id"], "title": it["title"], "status": status}
            if status == "in-progress" and current_tool:
                entry["current_tool"] = current_tool
            payload.append(entry)
        try:
            import json as _json_plan
            state = _json_plan.dumps(payload, ensure_ascii=False)
            # Déduplication
            if getattr(self, "_architect_plan_last_state", None) == state:
                return
            self._architect_plan_last_state = state
            logger.info("TODO_STATE:" + state)
        except Exception:
            pass

    def _advance_architect_plan(self, current_tool: str = "", file_path: str = "") -> None:
        """Avance le cursor du plan Architect.

        File-aware : si ``file_path`` est fourni et qu'un step (non-terminé) cible
        ce fichier, on saute DIRECTEMENT à ce step (marque les précédents completed),
        puis on le laisse "in-progress". Sinon, avance naïvement de 1.
        """
        items = getattr(self, "_architect_plan_items", None)
        if not items:
            return
        cursor = getattr(self, "_architect_plan_cursor", 0)
        total = len(items)
        if cursor >= total:
            return

        # ── File-aware : chercher un step pas encore completed dont le file match ──
        if file_path:
            _fp_norm = file_path.strip().replace("\\", "/").lower()
            _fp_basename = _fp_norm.rsplit("/", 1)[-1]
            target_idx: Optional[int] = None
            for idx in range(cursor, total):
                _step_file = (items[idx].get("file") or "").strip().lower()
                if not _step_file:
                    continue
                if _step_file == _fp_norm or _step_file == _fp_basename:
                    target_idx = idx
                    break
                # Match par basename si l'un est un path relatif
                _step_basename = _step_file.rsplit("/", 1)[-1]
                if _step_basename and _step_basename == _fp_basename:
                    target_idx = idx
                    break
            if target_idx is not None:
                # Marquer les steps [cursor..target_idx-1] comme completed,
                # mettre target_idx en "in-progress". Au prochain appel sur un
                # autre file, target_idx passera completed naturellement.
                self._architect_plan_cursor = target_idx
                self._emit_architect_plan(current_tool=current_tool)
                # Ensuite avancer de 1 pour que ce step soit comptabilisé completed
                # au prochain émit (sauf si c'est le dernier → on le laisse in-progress).
                if target_idx < total - 1:
                    self._architect_plan_cursor = target_idx + 1
                return

        # ── Fallback naïf : avance de 1 (laisse dernier in-progress jusqu'à done) ──
        if cursor < total - 1:
            self._architect_plan_cursor = cursor + 1
            self._emit_architect_plan(current_tool=current_tool)

    def _finalize_architect_plan(self) -> None:
        """Marque toutes les étapes du plan Architect comme completed."""
        items = getattr(self, "_architect_plan_items", None)
        if not items:
            return
        self._architect_plan_cursor = len(items)  # au-delà du dernier → tout completed
        self._emit_architect_plan()

    def _enrich_summary(self, llm_summary: str) -> str:
        """Enrichit le résumé du LLM avec la liste concrète des actions effectuées.

        Le LLM fournit souvent un résumé vague ("C'est fait"). Cette méthode
        ajoute automatiquement les fichiers modifiés, lus et erreurs rencontrées
        depuis _session_memory pour que le parent (ReAct loop) sache exactement
        ce qui a été fait — et puisse le rapporter à l'utilisateur.
        """
        # Finaliser le plan Architect (affichage UI) — tous steps → completed
        try:
            self._finalize_architect_plan()
        except Exception:
            pass

        parts = [llm_summary.rstrip()]
        mem = self._session_memory

        edits = mem.get("edits_done", [])
        if edits:
            parts.append("\n📝 Fichiers modifiés:")
            for entry in edits[-20:]:
                parts.append(f"  - {entry}")

        reads = mem.get("files_read", {})
        if reads:
            names = list(reads.keys())[-10:]
            parts.append(f"\n📖 Fichiers lus: {', '.join(names)}")

        errors = mem.get("errors_seen", [])
        if errors:
            parts.append(f"\n⚠️ Erreurs rencontrées ({len(errors)}):")
            for err in errors[-5:]:
                parts.append(f"  - {err[:120]}")

        result = "\n".join(parts)
        return result[:3000]

    async def _maybe_generate_reflexion(
        self,
        signal: str,
        context_tail: str,
        task_hint: str = "",
    ) -> None:
        """Déclenche la génération async d'une Reflexion (leçon apprise).

        Fire-and-forget : toute exception est avalée. Appelé depuis
        _post_action_hooks quand un pattern d'échec répété est détecté.
        Utilise un LLM léger (température basse, max_tokens court) pour
        extraire une leçon actionnable et la persister dans le store.
        """
        # Anti-spam : max 1 génération par 60 s et par session
        try:
            now = time.time()
            last = getattr(self, "_last_reflexion_ts", 0.0)
            if now - last < 60.0:
                return
            self._last_reflexion_ts = now
        except Exception:
            pass

        try:
            from src.learning.reflexion_store import (
                build_reflexion_prompt,
                parse_reflexion_llm_response,
                get_reflexion_store,
            )
        except Exception as exc:
            logger.debug(f"[Reflexion] import failed: {exc}")
            return

        ctx = f"Task: {task_hint[:300]}\n\nTrace récente:\n{context_tail[:1500]}"
        messages = build_reflexion_prompt(signal=signal, context=ctx)

        try:
            # FIX Phase 0: utiliser l'API standard Lumena (await llm.chat) au lieu
            # du client OpenAI inexistant. `self.client` n'a jamais été défini sur
            # SubAgent → bail out silencieux qui désactivait l'apprentissage.
            # `current_task` peut être None (fire-and-forget post-tâche) ;
            # `_get_llm(None)` retombe alors gracieusement sur core.llm (cf. L.410).
            llm = self._get_llm(getattr(self, "current_task", None))
            if llm is None:
                return

            raw = await llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=350,
            )
            raw = str(raw or "").strip()
            parsed = parse_reflexion_llm_response(raw)
            if not parsed:
                logger.debug("[Reflexion] LLM response could not be parsed")
                return
            store = get_reflexion_store()
            r = store.add(
                triggered_by=parsed["triggered_by"],
                root_cause=parsed["root_cause"],
                lesson=parsed["lesson"],
                apply_when=parsed["apply_when"],
                confidence=parsed.get("confidence", 0.7),
                tags=parsed.get("tags", []),
            )
            try:
                self._reflexion_generated_count += 1
            except Exception:
                pass
            logger.info(f"[Reflexion] nouvelle leçon apprise: {r.id} — {r.lesson[:80]}")
        except Exception as exc:
            logger.debug(f"[Reflexion] generation skipped: {exc}")

    async def _maybe_generate_success_pattern(
        self,
        task_description: str,
        tools_used: List[str],
        iterations: int,
        outcome_summary: str,
    ) -> None:
        """P1 — Capture un pattern de réussite dans le SuccessStore (fire-and-forget).

        Anti-spam : 1 seule génération par tâche, skip si description trop courte.
        Toute exception est avalée (best-effort).
        """
        try:
            if not task_description or len(task_description.strip()) < 12:
                return
            if getattr(self, "_success_generated_count", 0) > 0:
                return
            # Phase 0.5 — Filtre anti-pollution SuccessStore
            # Bloque la capture si le contexte contient un signal destructeur
            # ("casser", "bugs subtils", "saboter"...) sans intention explicite.
            # Cf. DIAGNOSTIC_PROD.md §13 — bug observé session 15:55 du 16/05 :
            # "corriger ET casser le site" → pattern pollué enregistré.
            try:
                blocked, reason = self._should_block_success_capture(
                    task_description=task_description,
                    outcome_summary=outcome_summary,
                    current_task=getattr(self, "current_task", None),
                )
                if blocked:
                    logger.info(
                        "[Success] capture BLOQUÉE (Phase 0.5 anti-pollution): {}",
                        reason,
                    )
                    return
            except Exception as _filter_exc:
                # Le filtre ne doit jamais casser le flow ; en cas d'erreur,
                # on laisse passer (fail-open pour ne pas perdre d'apprentissage légitime).
                logger.debug(f"[Success] anti-pollution filter error (fail-open): {_filter_exc}")
        except Exception:
            return

        try:
            from src.learning.success_store import (
                build_success_prompt,
                parse_success_llm_response,
                get_success_store,
            )
        except Exception as exc:
            logger.debug(f"[Success] import failed: {exc}")
            return

        try:
            # FIX Phase 0: API standard Lumena. `current_task` peut être None
            # car fonction lancée en fire-and-forget après fin de tâche.
            # `_get_llm(None)` retombe sur core.llm (cf. L.410).
            llm = self._get_llm(getattr(self, "current_task", None))
            if llm is None:
                return
            messages = build_success_prompt(
                task_description=task_description,
                tools_used=list(tools_used or []),
                iterations=int(iterations or 0),
                outcome_summary=outcome_summary or "",
            )

            raw = await llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=350,
            )
            raw = str(raw or "").strip()
            parsed = parse_success_llm_response(raw)
            if not parsed:
                logger.debug("[Success] LLM response could not be parsed")
                return
            store = get_success_store()
            p = store.add(
                task_type=parsed.get("task_type", "other"),
                summary=parsed["summary"],
                approach=parsed["approach"],
                tools_used=list(tools_used or [])[:10],
                iterations=int(iterations or 0),
                apply_when=parsed.get("apply_when", ""),
                confidence=parsed.get("confidence", 0.7),
                tags=parsed.get("tags", []),
            )
            try:
                self._success_generated_count += 1
            except Exception:
                pass
            logger.info(f"[Success] nouveau pattern capturé: {p.id} — {p.summary[:80]}")
        except Exception as exc:
            logger.debug(f"[Success] generation skipped: {exc}")

    async def _maybe_auto_evaluate_success(
        self,
        task_description: str,
        edits_done: List[str],
    ) -> None:
        """P2 — Auto-évaluation critique post-succès (fire-and-forget).

        Relit un diff résumé des fichiers modifiés et demande au LLM de chercher
        dette / edge cases / style. Si un problème non trivial est identifié,
        une Reflexion préventive est générée. Anti-spam : 1 fois par tâche.
        """
        try:
            if getattr(self, "_auto_eval_triggered", False):
                return
            self._auto_eval_triggered = True
            if not edits_done:
                return
        except Exception:
            return

        try:
            # FIX Phase 0: API standard Lumena. `current_task` peut être None
            # (fire-and-forget post-succès). `_get_llm(None)` retombe sur core.llm.
            llm = self._get_llm(getattr(self, "current_task", None))
            if llm is None:
                return
            # Résumé ultra-compact des fichiers modifiés (chemin + extrait fin)
            snippets: list[str] = []
            for _p in list(edits_done)[-5:]:
                try:
                    _abs = self._resolve_path(_p) if hasattr(self, "_resolve_path") else Path(_p)
                    if _abs.exists() and _abs.is_file():
                        _content = _abs.read_text(encoding="utf-8", errors="replace")
                        _excerpt = _content[-1200:] if len(_content) > 1200 else _content
                        snippets.append(f"### {_p}\n```\n{_excerpt}\n```")
                except Exception:
                    continue
            if not snippets:
                return
            system = (
                "Tu es un reviewer de code senior. On vient de résoudre une tâche avec succès "
                "(tests passent). Examine RAPIDEMENT le diff ci-dessous pour détecter UN SEUL "
                "problème non-trivial restant : edge case ignoré, dette technique introduite, "
                "ou violation de style/sécurité.\n\n"
                "Format JSON STRICT, rien d'autre :\n"
                "{\n"
                '  "has_issue":   <true|false>,\n'
                '  "issue":       "<1 phrase, vide si has_issue=false>",\n'
                '  "severity":    "<low|medium|high>",\n'
                '  "lesson":      "<leçon générale réutilisable, vide si has_issue=false>"\n'
                "}\n"
                "Sois SÉVÈRE sur la pertinence : ne signale RIEN si le code est simplement correct."
            )
            user = f"TÂCHE :\n{task_description[:400]}\n\nFICHIERS MODIFIÉS :\n" + "\n\n".join(snippets)

            raw = await llm.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=300,
            )
            raw = str(raw or "").strip()
            import json as _json_ae
            import re as _re_ae
            _m = _re_ae.search(r"\{.*\}", raw, _re_ae.DOTALL) if raw else None
            if not _m:
                return
            try:
                data = _json_ae.loads(_m.group(0))
            except Exception:
                return
            if not data.get("has_issue"):
                return
            issue = str(data.get("issue", "")).strip()
            severity = str(data.get("severity", "medium")).lower().strip()
            lesson = str(data.get("lesson", "")).strip()
            if not issue:
                return
            logger.warning(
                "[AutoEval] problème détecté post-succès ({}): {}",
                severity, issue[:120],
            )
            # Si sévérité medium/high et leçon valide ⇒ Reflexion préventive
            if lesson and severity in ("medium", "high"):
                try:
                    from src.learning.reflexion_store import get_reflexion_store
                    _rs = get_reflexion_store()
                    _rs.add(
                        triggered_by=f"auto-eval post-succès ({severity})",
                        root_cause=issue[:400],
                        lesson=lesson[:400],
                        apply_when=(task_description or "")[:200],
                        confidence=0.6 if severity == "medium" else 0.75,
                        tags=["auto-eval", severity],
                    )
                    try:
                        self._reflexion_generated_count += 1
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as exc:
            logger.debug(f"[AutoEval] skipped: {exc}")

    def _build_actions_recap(self) -> str:
        """Récap court de ce que l'agent a déjà fait (injecté quand il boucle sur les reads).
        L'objectif: lui rappeler ce qu'il connaît déjà pour qu'il évite de relire."""
        mem = getattr(self, "_session_memory", None) or {}
        files_read = mem.get("files_read") or {}
        edits_done = mem.get("edits_done") or []
        lines: list[str] = ["📋 RÉCAP de ce que tu as déjà fait :"]
        if files_read:
            lines.append(f"📖 Fichiers déjà lus (en cache, ne les relis PAS) — {len(files_read)} :")
            for p, content in list(files_read.items())[-10:]:
                try:
                    nb_lines = content.count("\n") + 1
                    lines.append(f"  • {p} ({nb_lines} lignes, {len(content)} chars)")
                except Exception:
                    lines.append(f"  • {p}")
        else:
            lines.append("📖 Aucun fichier lu encore.")
        if edits_done:
            lines.append(f"✏️ Modifications déjà faites — {len(edits_done)} :")
            for entry in edits_done[-8:]:
                lines.append(f"  • {entry}")
        else:
            lines.append("✏️ Aucune modification encore.")
        return "\n".join(lines)

    def _gather_project_context(self, task_description: str, target_files: list[str] | None = None) -> str:
        """
        Inject repo map + semantic code context + import skeletons into the CodeAgent prompt.
        """
        parts: list[str] = []
        try:
            from src.context.repo_map import get_repo_map
            rmap = get_repo_map()
            compact = rmap.get_compact_map(max_tokens=800)
            if compact:
                parts.append(f"--- Structure du projet ---\n{compact}")
        except Exception:
            pass
        try:
            from src.context.code_index import get_code_index
            cidx = get_code_index()
            relevant = cidx.get_context_for_query(task_description, max_tokens=1500)
            if relevant:
                parts.append(f"--- Code pertinent ---\n{relevant}")
        except Exception:
            pass
        # P2: inject import skeletons for target files
        if target_files:
            try:
                from src.context.ast_parser import get_import_graph, get_ast_parser
                parser = get_ast_parser()
                import_entries: list[str] = []
                budget = 2500
                seen_imports: set[str] = set()
                for tf in target_files[:5]:
                    graph = get_import_graph(tf)
                    for imp_path in graph[:10]:
                        if imp_path in seen_imports:
                            continue
                        seen_imports.add(imp_path)
                        sigs = parser.parse_file(Path(imp_path))
                        if sigs.is_valid and sigs.symbols:
                            entry = sigs.to_map_entry(max_symbols=15)
                            if entry:
                                cost = len(entry) // 4
                                if cost > budget:
                                    break
                                budget -= cost
                                import_entries.append(entry)
                if import_entries:
                    parts.append("--- Imports du fichier cible ---\n" + "\n".join(import_entries))
            except Exception:
                pass
        # P8: inject session memory
        session_mem = self._get_session_memory_text()
        if session_mem:
            parts.append(f"--- Mémoire de session ---\n{session_mem}")
        # WorldModel : structure live des fichiers modifiés cette session
        try:
            from src.context.world_model import get_world_model
            _ws_root = self._task_workspace_root or Path.cwd()
            _wm_text = get_world_model(_ws_root).get_compact(max_files=10, max_tokens=800)
            if _wm_text:
                parts.append(_wm_text)
        except Exception:
            pass
        # Reflexion Store : leçons apprises des sessions précédentes (RAG léger)
        try:
            from src.learning.reflexion_store import get_reflexion_store
            _rstore = get_reflexion_store()
            if len(_rstore) > 0:
                _query_refl = task_description
                if target_files:
                    _query_refl += " " + " ".join(target_files[:3])
                _hits = _rstore.retrieve(_query_refl, k=3, min_score=0.12)
                if _hits:
                    parts.append(_rstore.format_for_prompt(_hits))
                    # Marque ces réflexions comme appliquées (best-effort)
                    try:
                        self._applied_reflexion_ids = [r.id for r in _hits]
                        logger.info(
                            "[Reflexion] {} leçon(s) appliquée(s): {}",
                            len(_hits), ", ".join(r.id for r in _hits),
                        )
                        # Incrémente l'usage (persisté sur disque)
                        for _r in _hits:
                            try:
                                _rstore.increment_uses(_r.id)
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass
        # Success Store : patterns de réussite historiques (RAG léger)
        try:
            from src.learning.success_store import get_success_store
            _sstore = get_success_store()
            if len(_sstore) > 0:
                _query_succ = task_description
                if target_files:
                    _query_succ += " " + " ".join(target_files[:3])
                _shits = _sstore.retrieve(_query_succ, k=2, min_score=0.12)
                if _shits:
                    parts.append(_sstore.format_for_prompt(_shits))
                    try:
                        self._applied_success_ids = [p.id for p in _shits]
                        logger.info(
                            "[Success] {} pattern(s) appliqué(s): {}",
                            len(_shits), ", ".join(p.id for p in _shits),
                        )
                        for _p in _shits:
                            try:
                                _sstore.increment_uses(_p.id)
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass
        # P9: inject project memory (persistent cross-session)
        try:
            from src.agents.project_memory import get_project_memory
            _proj_id = str(self._task_workspace_root) if self._task_workspace_root else ""
            if _proj_id:
                _proj_ctx = get_project_memory().get_context(_proj_id, task_description)
                if _proj_ctx:
                    parts.append(_proj_ctx)
        except Exception:
            pass
        return "\n\n".join(parts)

    async def _single_code_attempt(
        self,
        task: AgentTask,
        llm,
        prior_failures: list[str],
        attempt: int,
        max_iter: int = 30,
    ) -> tuple["AgentResult", bool]:
        """
        Une tentative de la boucle interne (orchestrateur).
        Retourne (result, is_stuck) ou is_stuck=True -> retry externe souhaite.
        """
        _model_name_for_prompt = str(getattr(llm, "model_name", "") or "")
        messages, _target_files_seen, _project_files = self._build_initial_messages(
            task, prior_failures, attempt, model_name=_model_name_for_prompt,
        )

        # P5 — signaux comportementaux : parser_severity, tool_call_quality, sub_agent_stability, file_section_threshold
        try:
            from ..llm.model_profile import get_model_profile as _gmp
            _attempt_profile = _gmp(_model_name_for_prompt)
            _parser_severity = _attempt_profile.parser_severity   # "strict"|"lenient"|"forgiving"
            _tool_call_quality = _attempt_profile.tool_call_quality  # "excellent"|"good"|"moderate"|"poor"
            _sub_stability = _attempt_profile.sub_agent_stability     # "stable"|"moderate"|"unstable"
            _file_thresh = _attempt_profile.file_section_threshold    # chars; 0 = désactivé
            if _tool_call_quality in ("moderate", "poor"):
                # Injecter un rappel de format strict pour les modèles moins fiables
                _fmt_hint = (
                    "⚠️ FORMAT STRICT REQUIS : chaque appel d'outil doit utiliser exactement "
                    "le schéma JSON attendu. Vérifiez les noms des paramètres avant d'appeler."
                )
                messages.append({"role": "user", "content": _fmt_hint})
                logger.debug("[P5] tool_call_quality={} → rappel format injecté", _tool_call_quality)
            if _parser_severity == "forgiving":
                logger.debug("[P5] parser_severity=forgiving pour '{}' — tolérance max activée", _model_name_for_prompt)
            if _sub_stability == "unstable":
                logger.debug("[P5] sub_agent_stability=unstable pour '{}' — escalade timeout accélérée", _model_name_for_prompt)
            # Stocker le profil pour accès dans _execute_loop_action (file_section_threshold)
            self._attempt_profile = _attempt_profile
        except Exception:
            _parser_severity = "lenient"
            _tool_call_quality = "good"
            _sub_stability = "stable"
            _file_thresh = 0
            self._attempt_profile = None

        # Réinitialiser les compteurs d'état par tentative
        self._edit_fail_for_path = {}
        self._self_repair_count = 0
        self._self_repair_count_per_file = {}
        self._syntax_clean_snapshot = {}
        self._edit_restricted_files = set()
        self._gate_retries_used = 0
        self._edited_files = set()
        self._done_soft_gate_used = 0
        try:
            if getattr(self, "_codeagent_todo", None):
                self._codeagent_todo.reset()
        except Exception:
            pass

        # ── P8 : CodeFileWatcher — bridge IDE ────────────────────────────────
        _code_watcher = None
        try:
            from src.config.codeagent_flags import FILE_WATCHER_BRIDGE
            if FILE_WATCHER_BRIDGE and getattr(self, '_task_workspace_root', None):
                from src.memory.code_file_watcher import start_code_watcher
                _code_watcher = start_code_watcher(self._task_workspace_root)
        except Exception:
            pass

        report: list[str] = []
        loop_detector = LoopDetector()
        edits_since_last_test: int = 0
        reads_since_last_edit: int = 0
        _session_snapshots: dict[str, str | None] = {}
        _context_cache: dict[str, str] = {}
        _llm_retries: int = 0
        _timeout_count: int = 0   # P6 — timeouts dans cette tentative (bifurcation stratégie)
        temperature = 0.15 + (attempt - 1) * 0.05
        # DeepSeek suit mieux les instructions strictes à basse température
        _model_name_lc = str(getattr(llm, "model_name", "") or "").lower()
        if "deepseek" in _model_name_lc:
            temperature = min(temperature, 0.1)
        # ── P8.MODEL_TEMPERATURES : override par modèle si flag actif ──
        try:
            from src.utils.model_temperatures import get_model_temperature
            _model_full = str(getattr(llm, "model_name", "") or "")
            _override = get_model_temperature(_model_full, fallback=temperature)
            # On respecte la progression attempt (≥2) en ajoutant +0.05 par retry
            if attempt > 1:
                _override = min(1.0, _override + (attempt - 1) * 0.05)
            temperature = _override
        except Exception:
            pass

        # ── Phase Architect (UNIQUEMENT sur attempt 1, utilise Reasoner pour planifier) ──
        # Pattern industry-standard : Reasoner réfléchit 1× (CoT long, 64K output), Chat exécute.
        # Skip sur retries (re-plan inutile si la 1ère exec a produit des observations concrètes).
        _mode_attempt = getattr(self, '_resolved_intent', 'auto')
        _workspace_path = getattr(self, '_task_workspace_root', None)
        _local_bugfix_mode = _is_local_bugfix_task(
            task.description,
            workspace_path=_workspace_path,
            resolved_intent=_mode_attempt,
        )
        _explicit_code_targets = _extract_explicit_code_targets(task.description)
        _files_listing = "\n".join(f"  - {f}" for f in (_project_files or [])[:50]) or "(vide)"
        _architect_injected_keys: set[str] = set()  # fichiers dont le contenu est déjà dans les messages
        # Déclenchement Architect :
        # - intent "modify" classique (corrige, refactor, fix...)
        # - pas de phase Architect sur creation pure : l'executor peut produire
        #   directement les fichiers, puis verifier.
        # - intent "modify" sur projet existant avec description longue
        # - seuil description à 40 chars pour couvrir les requêtes courtes
        _project_exists = bool(_project_files and len(_project_files) > 2)
        _is_complex_modify = (
            _mode_attempt == "modify"
            and _project_exists
            and len(task.description) > 40
            and not _local_bugfix_mode
        )
        if attempt == 1 and _mode_attempt in ("modify", "create") and not _is_complex_modify:
            logger.debug(
                "[CodeAgent] Architect skip: intent={}, project_exists={}, desc_len={}, local_bugfix={}, files={}",
                _mode_attempt,
                _project_exists,
                len(task.description or ""),
                _local_bugfix_mode,
                len(_project_files or []),
            )
        if _is_complex_modify and attempt == 1:
            try:
                # ── Injection du CONTENU des fichiers cibles dans le prompt Architect ──
                # Sans ça, l'Architect hallucine les classes CSS, les textes, les chiffres.
                # On cible :
                #   1) Les fichiers mentionnés explicitement dans la description (blog.html, etc.)
                #   2) Sinon, les 3 fichiers web/code les plus pertinents du projet
                import re as _re_arch
                _desc_lower = task.description.lower()
                _candidates: list[str] = []
                _ARCH_EXCLUDED = ("/.backups/", "/.git/", "/node_modules/", "/__pycache__/", "/.venv/")
                _project_files_clean = [
                    _pf for _pf in (_project_files or [])
                    if not any(_seg in _pf.replace("\\", "/").lower() for _seg in _ARCH_EXCLUDED)
                ]
                for _pf in _project_files_clean:
                    _pf_lower = _pf.lower()
                    _stem = _pf_lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    if _stem and _stem in _desc_lower:
                        _candidates.append(_pf)
                # Heuristique sémantique : si mention "blog/article" → inclure blog.html et les articles
                _semantic_hints = {
                    "blog": ("blog", "article"),
                    "newsletter": ("blog", "newsletter", "index", "footer"),
                    "catégorie": ("blog", "category"),
                    "categorie": ("blog", "category"),
                    "index": ("index", "home", "accueil"),
                    "accueil": ("index", "home", "accueil"),
                    "navigation": ("index", "nav", "header"),
                    "menu": ("index", "nav", "header"),
                    "pricing": ("pricing", "price", "tarif"),
                    "tarif": ("pricing", "price", "tarif"),
                }
                _hint_keys: set[str] = set()
                for _trigger, _hints in _semantic_hints.items():
                    if _trigger in _desc_lower:
                        _hint_keys.update(_hints)
                if _hint_keys:
                    for _pf in _project_files_clean:
                        _pf_low = _pf.lower()
                        if _pf in _candidates:
                            continue
                        if any(_h in _pf_low for _h in _hint_keys):
                            _candidates.append(_pf)
                # ── Max fichiers injectés pour l'Architect (configurable via panel/env) ──
                try:
                    _arch_max_files = int(os.getenv("LUMENA_ARCHITECT_MAX_FILES", "4"))
                except (TypeError, ValueError):
                    _arch_max_files = 4
                _arch_max_files = max(1, min(_arch_max_files, 20))
                if not _candidates:
                    # Fallback : heuristique par extension pertinente — priorité aux noms communs
                    _preferred_ext = (".html", ".css", ".js", ".py", ".ts", ".tsx", ".jsx", ".vue")
                    _all_matching = [f for f in _project_files_clean if f.lower().endswith(_preferred_ext)]
                    # Tri : entry points en premier (index, main, app, blog, home)
                    _priority_names = ("index", "main", "app", "home", "blog")
                    _all_matching.sort(key=lambda f: (
                        0 if any(p in f.lower().rsplit("/", 1)[-1] for p in _priority_names) else 1,
                        f.lower(),
                    ))
                    _candidates = _all_matching[:_arch_max_files]
                # ── LOT Z14 — un CSS se style sur les HTML du DISQUE ──────────
                # Run « Verdure » (2026-08-16), à la milliseconde :
                #   17:48:47.363  le CodeAgent FINIT index.html  → les vraies
                #                 classes (.prestation-card, .tarif) sont sur le
                #                 disque
                #   17:48:47.560  il COMMENCE styles.css — 200 ms plus tard
                #   17:48:47.949  « 4 fichier(s) cible(s) injecté(s) »
                # Il avait donc le bon HTML sous les yeux. Il a écrit
                # `.prestations-list` et `.card`. Pourquoi ? Parce que le worker
                # CSS lui avait dicté, 76 s plus tôt, une liste de classes
                # « possibles » devinée sur des STUBS vides — les autres workers
                # tournent en parallèle et n'avaient encore rien écrit. Le worker
                # le dit lui-même au log : « Les HTML sont encore des stubs vides.
                # Je ne peux pas relever les classes réelles. »
                #
                # Deux failles se cumulaient :
                #   1. `_candidates` ne retient un fichier que si son nom est cité
                #      dans la description — une page non nommée n'entrait même
                #      pas dans le contexte ;
                #   2. rien ne disait au modèle que le FICHIER prime sur la
                #      consigne quand les deux se contredisent.
                # D'où l'aléa mesuré sur 29 missions, de 0 % à 100 % de couverture
                # sans logique apparente : tout dépendait de l'ordre de passage
                # dans le CodeAgent sérialisé.
                _styling_task = ".css" in _desc_lower
                if _styling_task:
                    _pages = [
                        _pf for _pf in _project_files_clean
                        if _pf.lower().endswith((".html", ".htm"))
                    ]
                    if _pages:
                        # Les pages passent DEVANT : la troncature à
                        # `_arch_max_files` ne doit jamais les évincer, c'est
                        # d'elles que viennent les sélecteurs.
                        _candidates = _pages + [c for c in _candidates if c not in _pages]
                _target_content_blocks: list[str] = []
                _ws_for_read = _workspace_path
                for _cand in _candidates[:_arch_max_files]:
                    try:
                        _cand_path = Path(str(_ws_for_read)) / _cand if _ws_for_read else Path(_cand)
                        if _cand_path.exists() and _cand_path.is_file():
                            _txt = _cand_path.read_text(encoding="utf-8", errors="replace")
                            _lines_c = _txt.split("\n")
                            _numbered = [f"{i+1:4d} | {l}" for i, l in enumerate(_lines_c[:400])]
                            _truncated = len(_lines_c) > 400
                            _target_content_blocks.append(
                                f"=== {_cand} ({len(_lines_c)} lignes"
                                f"{', tronqué aux 400 premières' if _truncated else ''}) ===\n"
                                + "\n".join(_numbered)
                            )
                    except Exception:
                        continue
                _content_section = ""
                if _target_content_blocks:
                    # LOT Z14 — le fichier prime sur la consigne. Injecter la page
                    # ne suffisait pas : sur Verdure, le CodeAgent avait le vrai
                    # `index.html` en contexte et a suivi la liste de classes que
                    # le worker avait devinée sur des stubs. Il faut lui dire
                    # laquelle des deux sources fait foi quand elles divergent.
                    _styling_rule = (
                        "\n⚠️ TÂCHE DE STYLE — les sélecteurs de ton CSS se lisent dans les "
                        "fichiers HTML ci-dessus, JAMAIS dans la description de la tâche. "
                        "Celle-ci a pu être rédigée avant que les pages soient écrites : si "
                        "elle nomme des classes absentes du HTML, elle a tort et le fichier a "
                        "raison. N'invente aucun sélecteur « au cas où » — un `.maclasse` qui "
                        "ne figure dans aucune page ci-dessus est du CSS mort.\n"
                    ) if _styling_task else ""
                    _content_section = (
                        "\n\n## CONTENU EXACT DES FICHIERS CIBLES (avec numéros de ligne)\n"
                        "Utilise CE contenu pour citer le code à remplacer — ne devine rien.\n"
                        + _styling_rule
                        + "\n"
                        + "\n\n".join(_target_content_blocks)
                        + "\n"
                    )
                # ── P3 : Fail-to-Pass — injecter le contexte test runner ──
                _test_runner_section = ""
                try:
                    from src.config.codeagent_flags import FAIL_TO_PASS
                    if FAIL_TO_PASS and _workspace_path:
                        from src.tools.test_runner_detector import detect_test_runner, format_for_architect
                        _tr_info = detect_test_runner(
                            Path(str(_workspace_path)),
                            list(getattr(self, "_edited_files", [])),
                        )
                        _tr_fmt = format_for_architect(_tr_info)
                        if _tr_fmt:
                            _test_runner_section = f"\n\n{_tr_fmt}\n"
                except Exception:
                    pass
                # Phase 0.6 : si la demande utilisateur originale a été transmise
                # par ReAct (cf. delegate_task_handler), on l'injecte explicitement
                # dans le prompt Architect AVANT la reformulation.
                # Règle : LA DEMANDE ORIGINALE PRIME SUR LA REFORMULATION en cas
                # de divergence (l'utilisateur a toujours raison sur son intent).
                _user_orig = ""
                try:
                    _ctx_dict = (task.context or {}) if isinstance(task.context, dict) else {}
                    _user_orig = str(_ctx_dict.get("user_original_request", "") or "").strip()
                except Exception:
                    _user_orig = ""

                _user_orig_block = ""
                if _user_orig and _user_orig.lower() != (task.description or "").lower().strip():
                    _user_orig_block = (
                        f"\n=== DEMANDE UTILISATEUR ORIGINALE (verbatim — PRIME sur la reformulation) ===\n"
                        f"{_user_orig[:1500]}\n"
                        f"=== FIN DEMANDE ORIGINALE ===\n\n"
                        f"⚠️ Si la 'Tâche reformulée' ci-dessous diverge de la demande originale, "
                        f"BASE TON PLAN SUR LA DEMANDE ORIGINALE en priorité.\n"
                    )

                _architect_messages = [
                    {"role": "system", "content": _ARCHITECT_PROMPT},
                    {"role": "user", "content": (
                        f"Contexte : modification du projet dans {_workspace_path}.\n"
                        f"{_user_orig_block}"
                        f"Fichiers du projet :\n{_files_listing}\n\n"
                        f"Tâche {'reformulée par ReAct' if _user_orig_block else ''} : {task.description}\n"
                        f"{_content_section}"
                        f"{_test_runner_section}\n"
                        "Décris PRÉCISÉMENT quelles modifications faire dans quels fichiers. "
                        "Pour chaque modification, cite le code EXACT à remplacer (avec 3-5 lignes de contexte). "
                        "Liste-les comme un plan d'action numéroté que l'exécuteur pourra suivre étape par étape."
                    )},
                ]
                # Architect = Reasoner UNIQUEMENT si le modèle par défaut est DeepSeek.
                # Sinon (Opus, GPT-5, Gemini, etc.) → utiliser le modèle courant pour architecter
                # (ces modèles n'ont pas besoin de swap reasoner, ils raisonnent nativement).
                try:
                    _arch_max_tokens = int(os.getenv("LUMENA_ARCHITECT_MAX_TOKENS", "12000"))
                except (TypeError, ValueError):
                    _arch_max_tokens = 12000
                _current_model_name = (getattr(llm, "model_name", "") or "").lower()
                # Swap Architect → deepseek-reasoner UNIQUEMENT si le modèle courant est deepseek-v3
                # (model_id = "deepseek-chat", V3.2 non-thinking).
                # Les modèles V4 (deepseek-v4-flash, deepseek-v4-pro) raisonnent nativement → pas de swap.
                _current_model_id = (getattr(llm, "model", "") or "").lower()
                _is_deepseek_v3_chat = (_current_model_id == "deepseek-chat")
                _llm_for_arch = None
                if _is_deepseek_v3_chat:
                    try:
                        from ..llm.multi_provider import MultiProviderLLM
                        _llm_for_arch = MultiProviderLLM(model_name="deepseek-reasoner")
                        logger.info(
                            "[CodeAgent] Architect = deepseek-reasoner (CoT 1×, max_tokens={}, {} fichier(s) cible(s) injecté(s))",
                            _arch_max_tokens, len(_target_content_blocks),
                        )
                    except Exception:
                        _llm_for_arch = llm
                else:
                    _llm_for_arch = llm
                    logger.info(
                        "[CodeAgent] Architect = {} (max_tokens={}, {} fichier(s) cible(s) injecté(s))",
                        _current_model_name or "modèle courant", _arch_max_tokens, len(_target_content_blocks),
                    )
                # ── Timeout Architect (configurable via panel/env) ──
                try:
                    _arch_timeout = float(os.getenv("LUMENA_ARCHITECT_TIMEOUT", "600"))
                except (TypeError, ValueError):
                    _arch_timeout = 600.0
                _arch_timeout = max(60.0, min(_arch_timeout, 3600.0))
                _architect_plan = await asyncio.wait_for(
                    _llm_for_arch.chat(messages=_architect_messages, temperature=0.1, max_tokens=_arch_max_tokens),
                    timeout=_arch_timeout,  # Reasoner CoT : jusqu'à 10min pour requêtes vagues multi-fichiers complexes
                )
                if not (_architect_plan and str(_architect_plan).strip()):
                    logger.warning("[CodeAgent] Architect réponse vide — retry 1/2 avec le même modèle")
                    _architect_plan = await asyncio.wait_for(
                        _llm_for_arch.chat(messages=_architect_messages, temperature=0.3, max_tokens=_arch_max_tokens),
                        timeout=_arch_timeout,
                    )
                if not (_architect_plan and str(_architect_plan).strip()):
                    logger.warning("[CodeAgent] Architect réponse vide — retry 2/2 (temperature=0.5)")
                    _architect_plan = await asyncio.wait_for(
                        _llm_for_arch.chat(messages=_architect_messages, temperature=0.5, max_tokens=_arch_max_tokens),
                        timeout=_arch_timeout,
                    )
                if _architect_plan and str(_architect_plan).strip():
                    # ── Injection du CONTENU brut dans les messages du Chat (pas seulement le plan) ──
                    # Sans ça, le Chat relit blog.html, le cache kicks in, et il boucle en pensant
                    # que "le cache est corrompu". On lui donne le contenu directement + on le
                    # pré-enregistre dans la session cache pour que la résolution de path marche.
                    if _target_content_blocks:
                        messages.append({
                            "role": "user",
                            "content": (
                                "📄 CONTENU ACTUEL DES FICHIERS CIBLES (déjà lu pour toi — "
                                "NE LANCE PAS read_file dessus) :\n\n"
                                + "\n\n".join(_target_content_blocks[:_arch_max_files])
                            ),
                        })
                        # Pré-remplir la session cache pour que les éventuels read_file tombent
                        # directement sur du contenu frais (pas de warning "cache corrompu").
                        for _cand in _candidates[:_arch_max_files]:
                            try:
                                _cand_path = Path(str(_ws_for_read)) / _cand if _ws_for_read else Path(_cand)
                                if _cand_path.exists() and _cand_path.is_file():
                                    _txt_seed = _cand_path.read_text(encoding="utf-8", errors="replace")
                                    _norm_seed = str(_cand_path.resolve()).replace("\\", "/")
                                    if hasattr(self, "_record_session_read"):
                                        self._record_session_read(_norm_seed, _txt_seed)
                            except Exception:
                                pass
                    # ── Garde-fou dur : tracker les fichiers injectés pour bloquer read_file/grep ──
                    # au début de la boucle si le LLM ignore les règles (cf. log 2026-04-19 13:50).
                    for _cand in _candidates[:_arch_max_files]:
                        try:
                            _cand_path = Path(str(_ws_for_read)) / _cand if _ws_for_read else Path(_cand)
                            _architect_injected_keys.add(_cand.lower().replace("\\", "/"))
                            _architect_injected_keys.add(_cand.lower().rsplit("/", 1)[-1])
                            if _cand_path.exists():
                                _architect_injected_keys.add(str(_cand_path.resolve()).lower().replace("\\", "/"))
                                _architect_injected_keys.add(_cand_path.name.lower())
                        except Exception:
                            continue
                    # Strip <think>...</think> avant injection — le raisonnement interne
                    # du reasoner ne doit pas consommer le budget d'injection de l'executor.
                    import re as _re_think
                    _plan_raw = str(_architect_plan)
                    _plan_text = _re_think.sub(r'<think>.*?</think>', '', _plan_raw, flags=_re_think.DOTALL).strip()
                    if not _plan_text:
                        _plan_text = _plan_raw  # fallback si tout était <think>
                    messages.append({
                        "role": "user",
                        "content": (
                            f"📋 PLAN DE L'ARCHITECTE (analyse préalable avec le contenu réel — suis-le étape par étape) :\n\n"
                            f"{_plan_text[:32000]}\n\n"
                            "⚠️ RÈGLES STRICTES POUR TOI :\n"
                            "1. Le contenu des fichiers cibles est DÉJÀ dans tes messages précédents.\n"
                            "2. NE RELIS PAS ces fichiers (read_file interdit sur eux au 1er tour).\n"
                            "3. Commence DIRECTEMENT par edit_lines ou str_replace selon le plan.\n"
                            "4. NE RÉÉCRIS PAS les fichiers complets avec write_file.\n"
                            "5. Quand toutes les étapes sont faites, appelle `done` avec un summary."
                        ),
                    })
                    logger.info(
                        "[CodeAgent] Phase Architect injectée ({} chars plan (stripped: {}) + {} fichier(s) cible(s) en contexte)",
                        len(_plan_text), len(_plan_raw) - len(_plan_text), len(_target_content_blocks),
                    )
                    # ── Plan Architect → TODO_STATE SSE (affichage UI) ──
                    try:
                        _plan_items_ui = self._parse_architect_plan(str(_architect_plan))
                        if _plan_items_ui:
                            self._architect_plan_items = _plan_items_ui
                            self._architect_plan_cursor = 0
                            self._architect_plan_last_state = None
                            self._emit_architect_plan()
                            logger.info(
                                "[CodeAgent] Plan UI émis: {} étapes",
                                len(_plan_items_ui),
                            )
                        else:
                            # Diagnostic : montrer les 20 premières lignes du plan pour
                            # comprendre le format et ajuster le parser si nécessaire.
                            _preview = "\n".join(str(_architect_plan).splitlines()[:20])
                            logger.warning(
                                "[CodeAgent] Plan UI: 0 étapes parsées. Preview du plan:\n{}",
                                _preview[:1500],
                            )
                    except Exception as _plan_ui_exc:
                        logger.debug(
                            "[CodeAgent] Plan UI emit échoué: {}",
                            _plan_ui_exc,
                        )
            except Exception as _arch_exc:
                logger.warning(
                    "[CodeAgent] Phase Architect échouée ({}: {!r}), fallback salvage content-injection",
                    type(_arch_exc).__name__, str(_arch_exc) or "no message",
                )
                # ── SALVAGE : même si l'Architect timeout, on injecte le contenu des fichiers cibles
                # + pré-seed session cache + on marque les clés pour le guard anti-relecture.
                # Bénéfice : le Chat executor évite la relecture en boucle, économisant 3-10 iter.
                try:
                    if _target_content_blocks:
                        messages.append({
                            "role": "user",
                            "content": (
                                "⚠️ Architect indisponible (timeout). Voici le CONTENU ACTUEL DES FICHIERS CIBLES "
                                "(déjà lu pour toi — NE LANCE PAS read_file dessus) :\n\n"
                                + "\n\n".join(_target_content_blocks[:_arch_max_files])
                                + "\n\nAnalyse directement ce contenu et applique les modifications via edit_lines/str_replace."
                            ),
                        })
                        _ws_for_read2 = _workspace_path
                        for _cand in _candidates[:_arch_max_files]:
                            try:
                                _cand_path = Path(str(_ws_for_read2)) / _cand if _ws_for_read2 else Path(_cand)
                                if _cand_path.exists() and _cand_path.is_file():
                                    _txt_seed = _cand_path.read_text(encoding="utf-8", errors="replace")
                                    _norm_seed = str(_cand_path.resolve()).replace("\\", "/")
                                    if hasattr(self, "_record_session_read"):
                                        self._record_session_read(_norm_seed, _txt_seed)
                                _architect_injected_keys.add(_cand.lower().replace("\\", "/"))
                                _architect_injected_keys.add(_cand.lower().rsplit("/", 1)[-1])
                                if _cand_path.exists():
                                    _architect_injected_keys.add(str(_cand_path.resolve()).lower().replace("\\", "/"))
                                    _architect_injected_keys.add(_cand_path.name.lower())
                            except Exception:
                                continue
                        logger.info(
                            "[CodeAgent] Salvage post-timeout : {} fichier(s) injecté(s) + cache pré-seedé",
                            len(_target_content_blocks[:_arch_max_files]),
                        )
                except Exception as _salvage_exc:
                    logger.debug("[CodeAgent] Salvage failed: {}", _salvage_exc)

        _architect_reread_budget: Dict[str, int] = {}

        for iteration in range(1, max_iter + 1):
            # ── WorldModel : exposer l'itération courante aux hooks ──
            self._current_iter = iteration
            # ── P5: escalation warning à 80% du budget ──
            try:
                from src.config.codeagent_flags import MAX_STEPS_GRACEFUL
                if MAX_STEPS_GRACEFUL:
                    _warn_threshold = max(1, int(max_iter * 0.8))
                    if iteration == _warn_threshold:
                        _last_user_msg = next(
                            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                            "",
                        )
                        if "BUDGET ITÉRATIONS" not in str(_last_user_msg):
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"⚠️ BUDGET ITÉRATIONS : tu as utilisé {iteration}/{max_iter} "
                                    f"itérations ({int(iteration*100/max_iter)}%). "
                                    "Concentre-toi maintenant sur la convergence : "
                                    "finalise la tâche puis utilise `done` avec un summary clair. "
                                    "Si la tâche est trop vaste, livre ce qui est fait et explique ce qui reste."
                                ),
                            })
            except Exception:
                pass
            # ── ANTI-RELECTURE PRÉEMPTIVE (utile surtout pour DeepSeek) ──
            # Avant chaque appel LLM, on rappelle CONCRÈTEMENT quels fichiers sont déjà
            # en mémoire. DeepSeek suit le contexte récent mieux que les règles abstraites
            # du system prompt. On n'injecte qu'à partir de l'iter 2 (sinon contexte initial OK)
            # et on évite le spam en ne ré-injectant pas si le dernier user-message le contient déjà.
            if iteration >= 2:
                try:
                    _mem_files = (self._session_memory or {}).get("files_read") or {}
                    if _mem_files:
                        _cached_list = []
                        for _p, _c in list(_mem_files.items())[-8:]:
                            _nl = (_c or "").count("\n") + 1 if _c else 0
                            _cached_list.append(f"  • {_p} ({_nl} lignes en cache)")
                        if _cached_list:
                            _reminder = (
                                "📂 FICHIERS DÉJÀ EN MÉMOIRE (NE PAS les relire avec read_file) :\n"
                                + "\n".join(_cached_list)
                                + "\n\n→ Pour MODIFIER : edit_lines (numéros de ligne) ou str_replace (texte exact)"
                                + "\n→ Pour CHERCHER une section : grep (pattern + path)"
                                + "\n→ Pour MULTI-FICHIERS : apply_patches (atomique) / read_files_batch (si nouveaux)"
                            )
                            # Ne pas dupliquer si déjà présent dans le dernier message user
                            _last_user = next(
                                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                                "",
                            )
                            if "FICHIERS DÉJÀ EN MÉMOIRE" not in str(_last_user):
                                messages.append({"role": "user", "content": _reminder})
                except Exception:
                    pass
            try:
                # ── P8.SSE_TIMEOUT : wrapper timeout sur l'appel LLM ──
                # Auto-switch autorisé : deepseek-chat peut basculer vers reasoner
                # si le routeur heuristique détecte code_task (plus de tokens/contexte).
                # Les garde-fous (Architect injection, anti-relecture, re-injection post-edit)
                # sont model-agnostic et restent actifs.
                _chat_coro = llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=getattr(llm, "max_output_tokens", 65536),
                )
                try:
                    from src.config.codeagent_flags import SSE_TIMEOUT
                    if SSE_TIMEOUT:
                        _sse_timeout_s = float(os.environ.get("LUMENA_SSE_TIMEOUT_SECONDS", "300"))
                        raw = await asyncio.wait_for(_chat_coro, timeout=_sse_timeout_s)
                    else:
                        raw = await _chat_coro
                except ImportError:
                    raw = await _chat_coro
                raw_text = str(raw).strip()
            except Exception as exc:
                category, action_class = _classify_llm_error(exc)
                report.append(f"[iter {iteration}] Erreur LLM ({category}): {exc}")
                logger.warning("[CodeAgent] LLM error cat={} action={}: {}", category, action_class, exc)
                if action_class == "abort":
                    break
                elif action_class == "retry_wait":
                    await asyncio.sleep(3)
                    continue
                elif action_class == "timeout_recovery":
                    # P6 — machine de reprise à 3 niveaux selon les timeouts consécutifs
                    _timeout_count += 1
                    # P7 — telemetry timeout recovery
                    if _TELEMETRY_AVAILABLE:
                        try:
                            publish_trace(
                                stage="codeagent_timeout_recovery",
                                status="retry",
                                mode="codeagent",
                                task_id=getattr(task, "task_id", None),
                                error=f"timeout_level={_timeout_count} iter={iteration}",
                                summary=f"P6 timeout #{_timeout_count}",
                            )
                        except Exception:
                            pass
                    if _timeout_count == 1:
                        # 1er timeout → compaction + hint décomposition
                        if len(messages) > 6:
                            messages = messages[:2] + messages[-4:]
                        messages.append({
                            "role": "user",
                            "content": (
                                "[TIMEOUT #1] Génération trop longue. NOUVELLE STRATÉGIE : "
                                "décompose en étapes plus petites, commence par la partie la plus simple. "
                                "Évite les gros write_file, préfère des éditions ciblées str_replace."
                            ),
                        })
                        report.append(f"[iter {iteration}] Timeout #1 → compaction + hint décomposition")
                        logger.warning("[P6] Timeout #1 — compaction + hint décomposition (iter {})", iteration)
                        continue
                    elif _timeout_count == 2 and _sub_stability != "unstable":
                        # 2e timeout → scope minimal, réponse courte forcée
                        # (pour unstable: déjà escaladé au niveau 1, on abandonne directement)
                        if len(messages) > 4:
                            messages = messages[:2] + messages[-2:]
                        messages.append({
                            "role": "user",
                            "content": (
                                "[TIMEOUT #2] Deuxième timeout. STRATÉGIE OBLIGATOIRE : "
                                "réduis au minimum viable. Une seule action simple par tour. "
                                "Pas de write_file > 50 lignes. Livre quelque chose de partiel et fonctionnel."
                            ),
                        })
                        temperature = min(temperature + 0.1, 0.5)
                        report.append(f"[iter {iteration}] Timeout #2 → scope minimal + réponse courte")
                        logger.warning("[P6] Timeout #2 — scope minimal forcé (iter {})", iteration)
                        continue
                    else:
                        # Abandon contrôlé — stable: ≥3 timeouts ; unstable: ≥2 timeouts
                        _abandon_msg = (
                            f"CodeAgent bloqué ({_timeout_count} timeouts"
                            + (" — abandon accéléré, modèle instable" if _sub_stability == "unstable" else " consécutifs")
                            + "):\n" + "\n".join(report[-5:])
                        )
                        report.append(f"[iter {iteration}] Timeout #{_timeout_count} → abandon contrôlé")
                        logger.warning("[P6] Timeout #{} — abandon, reprise boucle externe (iter {}, stability={})", _timeout_count, iteration, _sub_stability)
                        return AgentResult(
                            task_id=task.task_id,
                            success=False,
                            output=self._enrich_summary(_abandon_msg),
                            status_code=StatusCode.TIMEOUT,
                            meta=_failure_meta(
                                report=report,
                                blocked_at=f"{_timeout_count} timeouts consécutifs sur l'appel LLM (iter {iteration})",
                                task_description=getattr(task, "description", ""),
                                next_step="Réduire la taille du contexte ou découper la tâche en étapes plus courtes",
                                iterations=iteration,
                                stuck=True,
                                attempt=attempt,
                                timeout_count=_timeout_count,
                            ),
                        ), True
                else:
                    _llm_retries += 1
                    if _llm_retries >= 3:
                        break
                    continue

            # ── Parse réponse LLM ──
            tag, payload = self._process_llm_response(raw_text, iteration, messages, report)
            if tag == "continue":
                continue
            if tag == "success_text":
                if self._task_workspace_root and list(getattr(self, "_edited_files", [])):
                    messages.append({
                        "role": "user",
                        "content": (
                            "Tu as modifie des fichiers. Pour terminer, reponds avec "
                            '{"action":"done","summary":"..."} afin de lancer la validation finale.'
                        ),
                    })
                    report.append(f"[iter {iteration}] success_text refused after edits")
                    continue
                return self._result_success(task, self._enrich_summary(raw_text), iterations=iteration), False
            if tag == "done":
                action = payload
                summary = action.get("summary", "Tâche terminée.")
                # Vérification visuelle avant de valider le done (projets web)
                if self._task_workspace_root:
                    _html_files = list(self._task_workspace_root.rglob("*.html"))
                    if _html_files:
                        try:
                            from src.agents.visual_verifier import VisualVerifier
                            _visual_issues = await VisualVerifier().verify(self._task_workspace_root, llm)
                            if _visual_issues:
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        f"VÉRIFICATION VISUELLE — problèmes détectés :\n{_visual_issues}\n\n"
                                        "Corrige ces problèmes visuels puis utilise done à nouveau."
                                    ),
                                })
                                report.append(f"[iter {iteration}] visual check failed, continuing")
                                continue
                        except Exception:
                            pass  # jamais bloquant
                report.append(f"[iter {iteration}] done")

                # ── LOT Z36 — le ZERO fichier, AVANT la gate soft ─────────
                # La gate ci-dessous ne tourne que `if _edited_now` : sur un
                # diff vide elle est sautee et `done` passait droit en succes.
                # Le controle doit donc venir ICI, pas dedans. Meme place que
                # dans Z35 : avant la validation, jamais apres.
                if _creation_task_wrote_nothing(
                    intent=getattr(self, "_resolved_intent", "auto"),
                    edited_files=getattr(self, "_edited_files", None),
                    description=getattr(task, "description", ""),
                ):
                    _z36_used = int(getattr(self, "_z36_refusals", 0) or 0)
                    # Le refus RELANCE la boucle, il ne tue pas la tache : c'est
                    # la lecon de Z23. Il reste des dizaines d'iterations.
                    if _z36_used < 2:
                        self._z36_refusals = _z36_used + 1
                        messages.append(
                            {"role": "user", "content": _CREATION_WROTE_NOTHING_MESSAGE}
                        )
                        report.append(f"[iter {iteration}] done refuse - aucun fichier ecrit")
                        logger.info(
                            "[CodeAgent] Z36 iter={} done refuse : aucun fichier ecrit",
                            iteration,
                        )
                        continue
                    # Budget epuise : on rend un ECHEC honnete plutot qu'un
                    # succes demenit par le disque.
                    return AgentResult(
                        task_id=task.task_id,
                        success=False,
                        output=(
                            "Le CodeAgent s'est termine sans ecrire AUCUN fichier "
                            "alors que la tache demandait une creation.\n"
                            + _CREATION_WROTE_NOTHING_MESSAGE
                        ),
                        status_code=StatusCode.ERROR,
                        meta=_failure_meta(
                            report=report,
                            blocked_at="aucun fichier ecrit sur une tache de creation",
                            task_description=getattr(task, "description", ""),
                            next_step="Produire les fichiers demandes avec write_file",
                            iterations=iteration,
                            attempt=attempt,
                        ),
                    ), False

                # ── Gate soft : validation courte avant de conclure ───────
                try:
                    from src.config.codeagent_flags import CODEAGENT_DONE_GATE_SOFT
                    if CODEAGENT_DONE_GATE_SOFT and self._task_workspace_root:
                        _soft_used = int(getattr(self, "_done_soft_gate_used", 0) or 0)
                        _edited_now = list(getattr(self, "_edited_files", []))
                        try:
                            _soft_gate_max = int(os.getenv("LUMENA_DONE_GATE_MAX_RETRIES", "2"))
                        except (TypeError, ValueError):
                            _soft_gate_max = 2
                        _soft_gate_max = max(1, _soft_gate_max)
                        if _edited_now:
                            self._done_soft_gate_used = _soft_used + 1
                            from src.tools.verification_gate import run_gate
                            _soft_gate = await run_gate(
                                self._task_workspace_root,
                                _edited_now,
                                task_id=str(task.task_id),
                                timeout=15.0,
                            )
                            if not _soft_gate.passed:
                                if _soft_used >= _soft_gate_max:
                                    report.append(f"[iter {iteration}] done gate failed - budget exhausted")
                                    return AgentResult(
                                        task_id=task.task_id,
                                        success=False,
                                        output=(
                                            f"Validation avant fin echouee apres {_soft_gate_max} tentative(s).\n"
                                            + "\n".join(_soft_gate.errors[:5])
                                        ),
                                        status_code=StatusCode.ERROR,
                                        meta=_failure_meta(
                                            report=report,
                                            blocked_at=(
                                                "validation avant done echouee : "
                                                + "; ".join(_soft_gate.errors[:2])
                                            ),
                                            task_description=getattr(task, "description", ""),
                                            next_step=(
                                                "Corriger les erreurs de validation : "
                                                + "; ".join(_soft_gate.errors[:2])
                                            ),
                                            iterations=iteration,
                                            attempt=attempt,
                                            trace=report,
                                        ),
                                    ), False
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        "VALIDATION AVANT DONE — probleme detecte:\n"
                                        f"{_soft_gate.format_feedback()}\n\n"
                                        "Corrige puis appelle `done` a nouveau. Si la validation est impossible, "
                                        "explique clairement la limite dans le summary."
                                    ),
                                })
                                report.append(f"[iter {iteration}] soft done gate failed")
                                continue
                except Exception as _soft_gate_exc:
                    logger.debug("[CodeAgent] soft done gate fail-open: {}", _soft_gate_exc)
                # ── Verification Gate (P2 Upgrade Final) ──────────────────
                try:
                    from src.config.codeagent_flags import VERIFICATION_GATE
                    if VERIFICATION_GATE and self._task_workspace_root:
                        from src.tools.verification_gate import run_gate
                        from src.utils.gate_metrics import record_gate_retry, record_rollback
                        # Budget de retries gate : env var, défaut 2
                        try:
                            _gate_max_retries = int(os.getenv("LUMENA_GATE_MAX_RETRIES", "2"))
                        except (TypeError, ValueError):
                            _gate_max_retries = 2
                        _gate_retries_used = getattr(self, "_gate_retries_used", 0)
                        _gate = await run_gate(
                            self._task_workspace_root,
                            list(getattr(self, "_edited_files", [])),
                            task_id=str(task.task_id),
                        )
                        if not _gate.passed:
                            if _gate_retries_used < _gate_max_retries:
                                # Retry autorisé
                                self._gate_retries_used = _gate_retries_used + 1
                                record_gate_retry(task_id=str(task.task_id))
                                # ── P9 : escalade SWE Pipeline si tests en échec ──
                                _swe_note = ""
                                try:
                                    from src.config.codeagent_flags import SWE_PIPELINE
                                    if SWE_PIPELINE:
                                        from src.agents.swe_pipeline import run_swe_pipeline
                                        _swe = await run_swe_pipeline(
                                            self._task_workspace_root,
                                            task.description,
                                            task_id=str(task.task_id),
                                            timeout_per_step=60.0,
                                        )
                                        report.append(f"[iter {iteration}] SWE pipeline: {'ok' if _swe.success else 'fail'}")
                                        _swe_note = (
                                            "\n\nℹ️ SWE Pipeline a corrigé les régressions automatiquement."
                                            if _swe.success
                                            else f"\n\nSWE Pipeline résultat : {_swe.format_report()}"
                                        )
                                except Exception:
                                    pass
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        _gate.format_feedback() + _swe_note
                                        + f"\n\n(Tentative gate {self._gate_retries_used}/{_gate_max_retries})"
                                    ),
                                })
                                report.append(f"[iter {iteration}] gate failed — retry {self._gate_retries_used}/{_gate_max_retries}")
                                continue
                            else:
                                # Budget épuisé — rollback des fichiers modifiés
                                record_rollback(task_id=str(task.task_id))
                                _rolled_back: list[str] = []
                                for _ef in list(getattr(self, "_edited_files", [])):
                                    try:
                                        _snap = (_session_snapshots or {}).get(str(_ef))
                                        if _snap is not None:
                                            _ef_abs = self._resolve_path(str(_ef))
                                            _ef_abs.write_text(_snap, encoding="utf-8")
                                            _rolled_back.append(str(_ef))
                                    except Exception:
                                        pass
                                _rb_note = f" (rollback: {', '.join(_rolled_back)})" if _rolled_back else ""
                                report.append(f"[iter {iteration}] gate budget épuisé — rollback{_rb_note}")
                                return AgentResult(
                                    task_id=task.task_id,
                                    success=False,
                                    output=(
                                        f"❌ Verification Gate : budget de {_gate_max_retries} retries épuisé.\n"
                                        + "\n".join(_gate.errors[:5])
                                        + (_rb_note or "")
                                    ),
                                    status_code=StatusCode.ERROR,
                                    meta=_failure_meta(
                                        report=report,
                                        blocked_at=f"gate échec après {_gate_max_retries} retries : " + "; ".join(_gate.errors[:2]),
                                        task_description=getattr(task, "description", ""),
                                        next_step="Corriger les erreurs de vérification : " + "; ".join(_gate.errors[:2]),
                                        iterations=iteration,
                                        attempt=attempt,
                                        trace=report,
                                        gate_rollback=_rolled_back,
                                    ),
                                ), False
                except Exception:
                    pass  # gate jamais bloquante
                # ── fin gate ──────────────────────────────────────────────
                # P3: sortie du plan mode si actif
                if getattr(self, "_plan_mode_read_only", False):
                    self._plan_mode_read_only = False
                try:
                    _todo = getattr(self, "_codeagent_todo", None)
                    if _todo:
                        _todo.observe_action("done", success=True)
                except Exception:
                    pass
                return AgentResult(
                    task_id=task.task_id,
                    success=True,
                    output=self._enrich_summary(summary),
                    status_code=StatusCode.SUCCESS,
                    meta={"iterations": iteration, "attempt": attempt, "trace": report},
                ), False

            action = payload
            action_type = action["action"]
            # P1 : track des outils utilisés cette tâche (pour SuccessStore)
            try:
                if action_type and action_type not in self._tools_used_this_task:
                    self._tools_used_this_task.append(action_type)
            except Exception:
                pass
            # ── Garde-fou dur : bloquer les re-lectures du même fichier ──
            # Deux niveaux :
            #   (a) Fichiers injectés par Architect → refus dès la 1re relecture
            #   (b) Tout fichier déjà lu ≥ 1 fois dans la session → refus dès la 2e tentative
            # Se lève dès qu'un edit réussit sur le fichier (le nouveau contenu est re-injecté
            # dans l'observation, cf. bloc plus bas).
            # Évite la boucle "je lis, je re-lis" (logs 2026-04-19 13:50 & 14:28, 15 iter gaspillées).
            if action_type in ("read_file", "grep", "cat", "read_files_batch"):
                _probe_path = ""
                if isinstance(action, dict):
                    _probe_path = str(action.get("path") or action.get("file") or "")
                    if not _probe_path and action_type == "read_files_batch":
                        _pl = action.get("paths") or action.get("files") or []
                        if isinstance(_pl, list) and _pl:
                            _probe_path = str(_pl[0])
                _pk = _probe_path.lower().replace("\\", "/")
                _pk_base = _pk.rsplit("/", 1)[-1]

                # Ignorer les probes sur .backups/ — ce sont des fichiers de
                # sauvegarde, pas les fichiers réels du projet.
                _is_backup_probe = any(
                    _seg in _pk for _seg in ("/.backups/", "/.git/", "/node_modules/")
                )

                # Résolution absolue du probe (pour matching strict par path, pas basename)
                _pk_abs = ""
                if _pk and not _is_backup_probe:
                    try:
                        _ws_root = self._task_workspace_root or Path.cwd()
                        _probe_p = Path(_probe_path)
                        if not _probe_p.is_absolute():
                            _probe_p = _ws_root / _probe_p
                        _pk_abs = str(_probe_p.resolve()).replace("\\", "/").lower()
                    except Exception:
                        _pk_abs = _pk

                _blocked_reason = ""
                # (a) Architect-injected check
                if _architect_injected_keys and _pk and (
                    _pk in _architect_injected_keys or _pk_base in _architect_injected_keys
                ) and not _is_backup_probe:
                    _arch_budget_key = _pk_abs or _pk_base
                    _arch_default_budget = 5 if (_local_bugfix_mode and _pk_base in _explicit_code_targets) else 2
                    _allowed_arch_reread, _arch_remaining = _consume_architect_reread_budget(
                        _architect_reread_budget,
                        _arch_budget_key,
                        default_budget=_arch_default_budget,
                    )
                    if _allowed_arch_reread:
                        logger.info(
                            "[CodeAgent] Guard Architect assoupli: {} sur {} ré-autorisé "
                            "(budget restant {}/{})",
                            action_type,
                            _probe_path,
                            _arch_remaining,
                            _arch_default_budget,
                        )
                    else:
                        _blocked_reason = "contenu déjà injecté par l'Architect au début de la session"
                # (b) Session cache check : match STRICT par chemin absolu résolu.
                # On ne match PLUS par basename seul, car `.backups/contact.html`
                # et `contact.html` sont deux fichiers différents (cf. logs 2026-04-19 20:09).
                elif _pk and _pk_abs and not _is_backup_probe:
                    # ── P_REREAD_AFTER_EDIT: si le fichier a été édité/écrit dans cette
                    # session, le cache est PÉRIMÉ → autoriser la relecture pour que
                    # l'agent voie le contenu actuel au lieu de corriger à l'aveugle.
                    _was_edited_this_session = False
                    _edits_done = self._session_memory.get("edits_done", [])
                    for _ed_entry in _edits_done:
                        # Format: "path: action" — extraire le chemin
                        _ed_path = _ed_entry.split(":", 1)[0].strip().replace("\\", "/").lower()
                        try:
                            _ed_p = Path(_ed_entry.split(":", 1)[0].strip())
                            if not _ed_p.is_absolute():
                                _ed_p = (self._task_workspace_root or Path.cwd()) / _ed_p
                            _ed_abs = str(_ed_p.resolve()).replace("\\", "/").lower()
                        except Exception:
                            _ed_abs = _ed_path
                        if _ed_abs == _pk_abs:
                            _was_edited_this_session = True
                            break

                    if _was_edited_this_session:
                        # Fichier modifié → cache périmé, autoriser la relecture
                        # et invalider le cache pour que le contenu frais soit enregistré
                        _stale_keys = []
                        _files_read = self._session_memory.get("files_read", {})
                        for _cached_key in _files_read.keys():
                            _ck_raw = str(_cached_key).replace("\\", "/")
                            _ck = _ck_raw.lower()
                            try:
                                _ck_p = Path(_ck_raw)
                                if not _ck_p.is_absolute():
                                    _ck_p = (self._task_workspace_root or Path.cwd()) / _ck_p
                                _ck_abs = str(_ck_p.resolve()).replace("\\", "/").lower()
                            except Exception:
                                _ck_abs = _ck
                            if _ck_abs == _pk_abs:
                                _stale_keys.append(_cached_key)
                        for _sk in _stale_keys:
                            del _files_read[_sk]
                        logger.info(
                            "[CodeAgent] Guard read_file levé (fichier édité dans cette session): "
                            "{} sur {} ré-autorisé (cache invalidé)",
                            action_type, _probe_path,
                        )
                        # _blocked_reason reste "" → lecture autorisée
                    else:
                        _files_read = self._session_memory.get("files_read", {})
                        for _cached_key in _files_read.keys():
                            _ck_raw = str(_cached_key).replace("\\", "/")
                            _ck = _ck_raw.lower()
                            # Ignorer les clés de backup dans le cache
                            if any(_seg in _ck for _seg in ("/.backups/", "/.git/", "/node_modules/")):
                                continue
                            # Résolution absolue de la clé cache
                            try:
                                _ck_p = Path(_ck_raw)
                                if not _ck_p.is_absolute():
                                    _ws_root2 = self._task_workspace_root or Path.cwd()
                                    _ck_p = _ws_root2 / _ck_p
                                _ck_abs = str(_ck_p.resolve()).replace("\\", "/").lower()
                            except Exception:
                                _ck_abs = _ck
                            if _ck_abs == _pk_abs:
                                _blocked_reason = f"déjà lu dans cette session ({_cached_key})"
                                break

                # Sur retry (attempt ≥ 2), lever le guard "cache session" : la tentative
                # précédente a pu partir sur une mauvaise piste, la nouvelle doit pouvoir relire.
                # On garde le guard Architect-injected (contenu toujours dans le contexte initial).
                if (
                    _blocked_reason
                    and attempt >= 2
                    and _blocked_reason.startswith("déjà lu dans cette session")
                ):
                    logger.info(
                        "[CodeAgent] Guard read_file levé (attempt={}): {} sur {} ré-autorisé",
                        attempt, action_type, _probe_path,
                    )
                    _blocked_reason = ""

                if _blocked_reason:
                    # Tenter de récupérer le contenu déjà en cache
                    _cached_txt = ""
                    try:
                        _files_read = self._session_memory.get("files_read", {})
                        for _ck, _cv in _files_read.items():
                            _cks = str(_ck).lower().replace("\\", "/")
                            if _cks == _pk or _cks.endswith("/" + _pk_base):
                                _cached_txt = str(_cv)
                                break
                    except Exception:
                        pass

                    # ── CACHE-AS-SUCCESS: servir le contenu en cache comme lecture réussie
                    # au lieu de bloquer, pour éviter les boucles de relecture (3-9 iter perdues).
                    # Le LLM reçoit le fichier et peut avancer directement.
                    #
                    # FIX: si le LLM demande une plage (start_line) au-delà du contenu
                    # en cache (tronqué à 12000 chars), on LAISSE PASSER la lecture
                    # pour qu'il puisse voir la fin du fichier. Sinon le cache tronqué
                    # est servi en boucle et le LLM ne voit jamais les lignes >932.
                    _cache_covers_range = True
                    if _cached_txt and isinstance(action, dict):
                        _req_sl = action.get("start_line")
                        if _req_sl is not None:
                            try:
                                _cached_line_count = _cached_txt.count("\n") + 1
                                if int(_req_sl) > _cached_line_count:
                                    _cache_covers_range = False
                                    logger.info(
                                        "[CodeAgent] CACHE-BYPASS iter={}: {} demande L{}+ mais cache ne couvre que {} lignes → lecture autorisée",
                                        iteration, action_type, _req_sl, _cached_line_count,
                                    )
                            except (ValueError, TypeError):
                                pass

                    if _cached_txt and _cache_covers_range:
                        # Si le LLM demande une plage spécifique, servir cette plage
                        # depuis le cache complet au lieu de toujours tronquer à 12k.
                        _serve_txt = _cached_txt
                        if isinstance(action, dict):
                            _sl = action.get("start_line")
                            _el = action.get("end_line")
                            if _sl is not None:
                                try:
                                    _all_lines = _cached_txt.split("\n")
                                    _s = max(1, int(_sl))
                                    _e = min(len(_all_lines), int(_el)) if _el is not None else len(_all_lines)
                                    _slice = _all_lines[_s - 1 : _e]
                                    _numbered = [f"{_s + i:4d} | {l}" for i, l in enumerate(_slice)]
                                    _serve_txt = "\n".join(_numbered)
                                except (ValueError, TypeError, IndexError):
                                    pass
                        if _serve_txt is _cached_txt:
                            _serve_txt = _cached_txt[:12000]
                        logger.info(
                            "[CodeAgent] CACHE-AS-SUCCESS iter={}: {} sur {} servi depuis le cache ({} chars) — {}",
                            iteration, action_type, _probe_path, len(_serve_txt), _blocked_reason,
                        )
                        report.append(f"[iter {iteration}] {action_type} servi depuis cache ({_probe_path})")
                        messages.append({"role": "assistant", "content": raw_text})
                        messages.append({"role": "user", "content":
                            f"📄 Contenu de `{_probe_path}` (depuis le cache — ce fichier n'a pas changé depuis ta dernière lecture):\n"
                            f"{_serve_txt}"
                        })
                        continue

                    # Pas de contenu en cache → refus classique (rare)
                    logger.warning(
                        "[CodeAgent] BLOQUÉ iter={}: {} sur {} refusé ({})",
                        iteration, action_type, _probe_path, _blocked_reason,
                    )
                    report.append(f"[iter {iteration}] {action_type} bloqué ({_probe_path}: {_blocked_reason})")
                    messages.append({"role": "assistant", "content": raw_text})
                    _refusal = (
                        f"⛔ ACTION REFUSÉE : `{action_type}` sur `{_probe_path}` est bloqué.\n\n"
                        f"Raison : {_blocked_reason}.\n\n"
                        "ACTION ATTENDUE : utilise DIRECTEMENT `edit_lines` ou `str_replace` en te basant sur "
                        "le contenu déjà en contexte. Si tu ne trouves pas la chaîne à remplacer, "
                        "c'est qu'elle n'existe pas sous cette forme — adapte ta recherche au contenu réel ci-dessous. "
                        "Si tu as fini, appelle `done` avec un summary."
                    )
                    messages.append({"role": "user", "content": _refusal})
                    continue
            # LOT I.2 — périmètre mission : refuser toute ÉCRITURE hors des fichiers assignés
            # au worker. Hors mission (_allowed_files None) → aucun effet. Lectures jamais
            # concernées. Ferme le cas « le CodeAgent d'un worker écrit le fichier d'un autre ».
            if (
                getattr(self, "_allowed_files", None)
                and action_type in _CODEAGENT_WRITE_ACTIONS
                and isinstance(action, dict)
            ):
                # M3b-clôture : UN SEUL chemin hors périmètre suffit à refuser
                # (apply_patches peut viser plusieurs fichiers en un appel).
                _wp_bad = [
                    _wp for _wp in _action_write_paths(action)
                    if not _write_within_perimeter(_wp, self._allowed_files, self._task_workspace_root)
                ]
                if _wp_bad:
                    logger.warning(
                        "[CodeAgent] écriture hors périmètre refusée: {} (autorisés: {})",
                        _wp_bad, sorted(self._allowed_files),
                    )
                    report.append(f"[iter {iteration}] {action_type} hors périmètre refusé ({_wp_bad})")
                    messages.append({"role": "assistant", "content": raw_text})
                    messages.append({"role": "user", "content": (
                        f"⛔ ÉCRITURE HORS PÉRIMÈTRE : {_wp_bad} n'est pas dans tes fichiers "
                        f"assignés ({sorted(self._allowed_files)}). Écris UNIQUEMENT ces "
                        "fichiers — les autres appartiennent à d'autres workers. Corrige ta "
                        "cible et réessaie."
                    )})
                    continue

            # LOT N2 — instantané AVANT toute action non couverte par le garde
            # d'écriture (typiquement `run_command`) : c'est par là que le
            # périmètre a été contourné dans HuffPack.
            _snap_before_n2 = frozenset()
            _watch_disk_n2 = bool(
                getattr(self, "_allowed_files", None)
                and getattr(self, "_task_workspace_root", None)
                and action_type not in _CODEAGENT_WRITE_ACTIONS
            )
            if _watch_disk_n2:
                _snap_before_n2 = snapshot_mission_files(self._task_workspace_root)

            observation = await self._execute_loop_action(action, snapshots=_session_snapshots)

            if _watch_disk_n2:
                _created_out = files_created_outside_perimeter(
                    _snap_before_n2,
                    snapshot_mission_files(self._task_workspace_root),
                    self._allowed_files,
                    self._task_workspace_root,
                )
                if _created_out:
                    logger.warning(
                        "[CodeAgent] N2 — fichier(s) créé(s) HORS périmètre par '{}' : {} "
                        "(autorisés: {})",
                        action_type, _created_out, sorted(self._allowed_files),
                    )
                    self._out_of_scope_created = sorted(
                        set(getattr(self, "_out_of_scope_created", []) or []) | set(_created_out)
                    )
                    report.append(
                        f"[iter {iteration}] {action_type} a créé hors périmètre {_created_out}"
                    )
                    messages.append({"role": "user", "content": (
                        f"⛔ HORS PÉRIMÈTRE — ton action `{action_type}` a créé {_created_out}, "
                        f"qui n'est PAS dans tes fichiers assignés ({sorted(self._allowed_files)}).\n\n"
                        "Le périmètre vaut pour TOUTES les voies d'écriture, y compris les "
                        "commandes shell — il n'existe pas de canal non intercepté. Ces fichiers "
                        "ne feront pas partie du livrable.\n\n"
                        "Reviens à tes fichiers assignés. Si tu es bloqué par un mécanisme "
                        "interne, dis-le dans ton `done` plutôt que de le contourner."
                    )})

            # ── Lever le garde-fou + re-injecter le contenu après un edit réussi ──
            # Dès qu'un edit (str_replace/edit_lines/apply_patch/write_file) réussit sur un
            # fichier précédemment injecté par l'Architect, on retire sa clé du set (le LLM
            # pourra alors relire s'il veut voir la version modifiée) ET on ré-injecte
            # directement le nouveau contenu dans l'observation pour éviter la re-lecture.
            if (
                _architect_injected_keys
                and action_type in ("str_replace", "edit_lines", "apply_patch", "write_file", "edit_file", "insert_at_anchor")
                and isinstance(action, dict)
            ):
                _edit_path_clear = str(action.get("path") or action.get("file") or "")
                _obs_head = str(observation)[:20]
                if _edit_path_clear and "✅" in _obs_head:
                    _ek = _edit_path_clear.lower().replace("\\", "/")
                    _ek_base = _ek.rsplit("/", 1)[-1]
                    _was_tracked = _ek in _architect_injected_keys or _ek_base in _architect_injected_keys
                    if _was_tracked:
                        # Retirer toutes les variantes de clé pour ce fichier
                        for _k in list(_architect_injected_keys):
                            if _k == _ek or _k == _ek_base or _k.endswith("/" + _ek_base):
                                _architect_injected_keys.discard(_k)
                        # Re-injecter le contenu frais dans l'observation
                        try:
                            _abs = Path(str(_ws_for_read)) / _edit_path_clear if _ws_for_read else Path(_edit_path_clear)
                            if _abs.exists() and _abs.is_file():
                                _new_txt = _abs.read_text(encoding="utf-8", errors="replace")
                                _new_lines = _new_txt.split("\n")
                                _new_numbered = "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(_new_lines[:400]))
                                _more = f"\n... ({len(_new_lines) - 400} lignes supplémentaires non affichées)" if len(_new_lines) > 400 else ""
                                _fresh_block = (
                                    f"\n\n📄 NOUVEAU CONTENU de {_edit_path_clear} ({len(_new_lines)} lignes) — "
                                    f"utilise CE contenu pour les edits suivants, NE RELIS PAS :\n"
                                    f"{_new_numbered}{_more}"
                                )
                                # Append au résultat de l'observation
                                if isinstance(observation, ActionResult):
                                    observation = ActionResult(observation.summary, (observation.detail or "") + _fresh_block)
                                else:
                                    observation = str(observation) + _fresh_block
                                # Mettre à jour la session cache
                                if hasattr(self, "_record_session_read"):
                                    self._record_session_read(str(_abs.resolve()).replace("\\", "/"), _new_txt)
                                logger.info(
                                    "[CodeAgent] Post-edit: {} retiré du guard + nouveau contenu injecté ({} lignes)",
                                    _edit_path_clear, len(_new_lines),
                                )
                        except Exception as _ri_exc:
                            logger.debug("[CodeAgent] Re-injection post-edit skipped: {}", _ri_exc)

            # ── Détection boucle (post-exécution) ──
            _obs_for_loop = str(observation)[:500]
            _is_err = _obs_for_loop.startswith("❌") or "erreur" in _obs_for_loop.lower()[:100]
            loop_detector.record(action, _obs_for_loop, is_error=_is_err)
            is_stuck, stuck_reason = loop_detector.check()
            if is_stuck:
                report.append(f"[iter {iteration}] Bloqué: {stuck_reason}")
                if _session_snapshots:
                    n = self._rollback_session(_session_snapshots)
                    report.append(f"[rollback] {n} fichier(s) restauré(s)")
                # P3 : déclenche génération de leçon sur boucle détectée (fire-and-forget)
                try:
                    _trace_tail = "\n".join(
                        f"[{m.get('role','?')}] {str(m.get('content',''))[:180]}"
                        for m in messages[-4:]
                    )
                    asyncio.create_task(
                        self._maybe_generate_reflexion(
                            signal=f"LoopDetector stuck: {stuck_reason}",
                            context_tail=_trace_tail,
                            task_hint=getattr(task, "description", "") or "",
                        )
                    )
                except Exception:
                    pass
                return AgentResult(
                    task_id=task.task_id,
                    success=False,
                    output=self._enrich_summary(f"CodeAgent bloqué ({stuck_reason}):\n" + "\n".join(report[-5:])),
                    status_code=StatusCode.PARTIAL,
                    meta=_failure_meta(
                        report=report,
                        blocked_at=stuck_reason,
                        task_description=getattr(task, "description", ""),
                        next_step="Changer d'approche : utiliser grep_search pour cibler, ou delegate_task vers un agent spécialisé",
                        iterations=iteration,
                        stuck=True,
                        attempt=attempt,
                    ),
                ), True

            # ── Post-action hooks ──
            observation, edits_since_last_test, reads_since_last_edit = await self._post_action_hooks(
                action=action, action_type=action_type, observation=observation,
                messages=messages, task=task,
                session_snapshots=_session_snapshots, target_files_seen=_target_files_seen,
                edits_since_last_test=edits_since_last_test,
                reads_since_last_edit=reads_since_last_edit,
                context_cache=_context_cache,
                local_bugfix_mode=_local_bugfix_mode,
            )

            # ── Break immédiat si _post_action_hooks a levé _force_done ──
            if getattr(self, '_force_done', False):
                self._force_done = False  # reset pour la prochaine tentative
                report.append(f"[iter {iteration}] ARRÊT FORCÉ — trop de lectures sans édition")
                return AgentResult(
                    task_id=task.task_id,
                    success=False,
                    output=self._enrich_summary(
                        "CodeAgent arrêté : trop de lectures consécutives sans édition. "
                        "Aucune modification effectuée.\n" + "\n".join(report[-8:])
                    ),
                    status_code=StatusCode.PARTIAL,
                    meta=_failure_meta(
                        report=report,
                        blocked_at="trop de lectures consécutives sans édition",
                        task_description=getattr(task, "description", ""),
                        next_step="Commencer à éditer les fichiers identifiés, ou utiliser grep_search pour cibler l'information exacte",
                        iterations=iteration,
                        force_done=True,
                        attempt=attempt,
                    ),
                ), True

            # ── Compaction ──
            messages = await self._maybe_compact(messages, llm, report)

            report.append(f"[iter {iteration}] {action_type} -> {str(observation)[:120]}")
            _action_path = (action.get("path", "") or action.get("test_path", "") or action.get("command", "")[:60]) if isinstance(action, dict) else ""
            _action_detail = f"{action_type}"
            if _action_path:
                _action_detail += f" {_action_path}"
            # Stream thought si présent dans la réponse JSON
            _iter_thought = action.get("thought", "") if isinstance(action, dict) else ""
            if _iter_thought:
                logger.info("[CodeAgent] 💭 {}", _iter_thought[:800])
            logger.info("[CodeAgent] iter={}/{} {} (attempt {})", iteration, max_iter, _action_detail, attempt)

            # ── Progression temps réel (v2) ──
            _progress_cb = task.context.get("_progress_callback")
            try:
                _pct = int(iteration * 100 / max_iter)
                _progress_data = {
                    "iteration": iteration,
                    "max_iter": max_iter,
                    "pct": _pct,
                    "last_action": action_type,
                    "last_path": _action_path[:120] if _action_path else "",
                }
                # Stocker dans pending_tasks pour bg_status
                _orch = getattr(self, "_orchestrator_ref", None)
                if _orch is None:
                    try:
                        _orch = get_orchestrator()
                    except Exception:
                        pass
                if _orch and task.task_id in _orch.pending_tasks:
                    _orch.pending_tasks[task.task_id]["progress"] = _progress_data
                # Callback push vers le chat
                if _progress_cb and iteration % 5 == 0:
                    _bar = "█" * (_pct // 10) + "░" * (10 - _pct // 10)
                    _msg = f"[{_bar}] {_pct}% — iter {iteration}/{max_iter} — `{_action_detail}`"
                    try:
                        _r = _progress_cb(_msg)
                        if asyncio.iscoroutine(_r):
                            await _r
                    except Exception:
                        pass
            except Exception:
                pass

            messages.append({"role": "assistant", "content": raw_text})
            obs_text = observation.full() if isinstance(observation, ActionResult) else str(observation)
            try:
                from src.agents.codeagent_observation import compact_observation
                _obs_view = compact_observation(
                    obs_text,
                    task_id=str(task.task_id),
                    iteration=iteration,
                    action_type=action_type,
                )
                obs_text = _obs_view.text
                if _obs_view.failed:
                    report.append(f"[iter {iteration}] tool status={_obs_view.status}")
                    if isinstance(observation, ActionResult) and not observation.summary.startswith(("❌", "⛔")):
                        observation = ActionResult(f"❌ {_obs_view.status}: {observation.summary}", observation.detail)
            except Exception as _obs_exc:
                logger.debug("[CodeAgent] observation compact skipped: {}", _obs_exc)
            # ── P11.FRENCH_ERRORS : traduire messages d'erreur techniques ──
            try:
                from src.utils.french_errors import translate_error
                obs_text = translate_error(obs_text)
            except Exception:
                pass
            # ── Hint contextuel anti-boucle ──
            _hint = ""
            if loop_detector.node_check_passed and action_type == "run_command":
                _hint = "\n\n⚠️ HINT: `node --check` a déjà confirmé que la syntaxe JS est valide. Tu n'as plus besoin de vérifier le code. Utilise l'action `done` avec un summary maintenant."
            elif loop_detector.consecutive_cmd_failures >= 3 and action_type == "run_command":
                _hint = "\n\n⚠️ HINT: Les commandes shell échouent à répétition sur cet environnement Windows. Utilise `read_file` pour vérifier les fichiers directement, puis l'action `done`."
            elif getattr(self, '_redirect_count', 0) > 5 and action_type == "run_command":
                _hint = (
                    "\n\n⚠️ HINT: Trop de commandes shell redirigées vers read_file. "
                    "Utilise directement read_file(path, start_line, end_line) pour lire des plages, "
                    "et grep(path, pattern) pour chercher. "
                    "run_command est réservé à l'EXÉCUTION (node, python, npm)."
                )
                self._redirect_count = 0
            # ── P4: Truncation save (dynamique, calibré par modèle actif) ──
            # Seuils calculés via src/reasoning/history_formatter (paliers 2k…48k).
            # Les outils "lecteurs" (read_file, grep, web_fetch…) ne sont pas
            # tronqués tant que la taille reste sous 4× le budget, pour garder
            # les faits complets dans le raisonnement.
            try:
                from src.utils.truncation_save import save_and_truncate
                from src.reasoning.history_formatter import (
                    compute_obs_limit as _compute_obs_limit,
                    should_protect_observation as _should_protect,
                )
                _max_ctx = int(getattr(llm, "context_window", 0) or 0)
                _budget = _compute_obs_limit(_max_ctx)
                _is_reader = _should_protect(action_type)
                _threshold = _budget * 4 if _is_reader else _budget
                _head = int(_budget * 0.55)
                _tail = max(500, _budget - _head - 60)
                _obs_injected = save_and_truncate(
                    obs_text,
                    task_id=task.task_id,
                    iteration=iteration,
                    threshold=_threshold,
                    head_chars=_head,
                    tail_chars=_tail,
                )
            except Exception:
                _obs_injected = obs_text[:8000]
            messages.append({
                "role": "user",
                "content": f"Résultat de l'action:\n{_obs_injected}{_hint}",
            })

        # ── P5: résumé final gracieux plutôt qu'un abort brutal ──
        _graceful_output = (
            f"CodeAgent: {max_iter} itérations sans conclusion "
            f"(tentative {attempt}).\n" + "\n".join(report[-5:])
        )
        try:
            from src.config.codeagent_flags import MAX_STEPS_GRACEFUL
            if MAX_STEPS_GRACEFUL:
                messages.append({
                    "role": "user",
                    "content": (
                        "⛔ BUDGET ÉPUISÉ : aucune nouvelle action ne peut être exécutée.\n"
                        "Produit MAINTENANT, en texte libre (pas de JSON), un résumé final :\n"
                        "1) Ce qui a été accompli\n"
                        "2) Ce qu'il reste à faire\n"
                        "3) Recommandation concrète pour la suite\n"
                        "Sois factuel et concis (max 15 lignes)."
                    ),
                })
                try:
                    _final_raw = await llm.chat(
                        messages=messages,
                        temperature=0.0,
                        max_tokens=1024,
                    )
                    _final_text = str(_final_raw).strip()
                    if _final_text:
                        _graceful_output = (
                            f"CodeAgent — budget épuisé ({max_iter} itérations).\n\n"
                            f"Résumé final :\n{_final_text}\n\n"
                            f"Trace (5 dernières étapes) :\n" + "\n".join(report[-5:])
                        )
                except Exception as _exc:
                    logger.debug("[CodeAgent] graceful final summary failed: {}", _exc)
        except Exception:
            pass

        return AgentResult(
            task_id=task.task_id,
            success=False,
            output=self._enrich_summary(_graceful_output),
            status_code=StatusCode.PARTIAL,
            meta=_failure_meta(
                report=report,
                blocked_at=f"limite de {max_iter} itérations atteinte sans conclusion",
                task_description=getattr(task, "description", ""),
                next_step="Découper la tâche en étapes plus petites ou déléguer à un agent spécialisé",
                iterations=max_iter,
                attempt=attempt,
                trace=report,
            ),
        ), True

    # ── F2: Méthodes extraites de _single_code_attempt ──

    def _build_initial_messages(
        self, task: AgentTask, prior_failures: list[str], attempt: int,
        *, model_name: str = "",
    ) -> tuple[list[dict[str, str]], list[str], list[str] | None]:
        """Construit les messages initiaux + target_files. Extrait du setup de _single_code_attempt."""
        # Lire depuis task_ctx (typé, priorité) ou depuis le dict legacy
        _tc = task.task_ctx
        _ws = (
            str(_tc.workspace_path) if _tc and _tc.workspace_path
            else (task.context or {}).get("workspace_path") or (task.context or {}).get("project_dir")
        )
        _ctx_intent = None
        _ctx_intent_from_tc = _tc.intent if _tc and _tc.intent not in ("auto", None) else None
        if _ws:
            self._task_workspace_root = Path(str(_ws))
            # LOT I.2 (mission) — périmètre OPTIONNEL : le worker de mission passe la liste de
            # SES fichiers via context['allowed_files']. Le CodeAgent ne pourra ÉCRIRE que
            # ceux-là. Clé absente (chat / vraie Lumena) → None → comportement inchangé.
            try:
                _perim_i2 = (task.context or {}).get("allowed_files")
                if _perim_i2:
                    self._allowed_files = frozenset(
                        str(p).replace("\\", "/").strip().strip("/")
                        for p in _perim_i2 if str(p).strip()
                    )
            except Exception:
                self._allowed_files = None
            # Intent déjà résolu par l'appelant (ReAct) ? On le récupère pour que la phase
            # Architect puisse se déclencher (sinon getattr('_resolved_intent', 'auto')
            # renvoie 'auto' et _is_complex_modify reste toujours False).
            # 'read' = nouveau mode lecture (intent_router v2) : skip Architect + Reasoner.
            _ctx_intent = _ctx_intent_from_tc or (task.context or {}).get("intent")
            if _ctx_intent in ("create", "modify", "unknown", "read"):
                self._resolved_intent = _ctx_intent
            elif not _ctx_intent:
                # Fallback : si le handler n'a pas injecté d'intent, on le déduit
                # du workspace. Si le dossier existe et contient des fichiers → "modify",
                # sinon → "create". Sans ça, _resolved_intent reste 'auto' et
                # l'Architect ne se déclenche JAMAIS quand workspace_path est fourni.
                _ws_path = Path(str(_ws))
                if _ws_path.is_dir() and any(_ws_path.iterdir()):
                    self._resolved_intent = "modify"
                else:
                    self._resolved_intent = "create"
                logger.debug(
                    "[CodeAgent] Intent déduit du workspace: {} ({})",
                    self._resolved_intent, _ws_path,
                )
        else:
            # ── Résolution centralisée via resolve_workspace ──
            # Plus AUCUNE création à l'aveugle ici — tout passe par le registre.
            from ..utils.project_registry import resolve_workspace
            _resolution = resolve_workspace(task.description, context=task.context)
            if _resolution.path:
                self._task_workspace_root = _resolution.path
                self._resolved_intent = _resolution.intent  # 'create' | 'modify' | 'unknown'
                logger.info(
                    "[CodeAgent] Workspace résolu: {} (source={}, intent={}, conf={:.2f})",
                    _resolution.path, _resolution.source, _resolution.intent, _resolution.confidence,
                )
                # Git auto-init pour les projets créés (optionnel)
                if _resolution.source == "created":
                    try:
                        import subprocess as _git_sp
                        _git_r = _git_sp.run(
                            ["git", "init"], cwd=str(_resolution.path),
                            capture_output=True, timeout=5,
                        )
                        if _git_r.returncode == 0:
                            (_resolution.path / ".gitignore").write_text(
                                "node_modules/\n__pycache__/\n*.pyc\n.env\ndist/\nbuild/\n",
                                encoding="utf-8",
                            )
                    except Exception:
                        pass
            else:
                self._task_workspace_root = None

        user_content = f"Tâche : {task.description}"
        ctx = {k: v for k, v in (task.context or {}).items() if not k.startswith("_")}

        # ── Mode lecture (intent_router v2) : bannière explicite no-write ──
        # Quand intent='read', l'utilisateur a explicitement demandé une analyse
        # SANS modification (ex: "analyse juste, ne modifie rien").  On force
        # le CodeAgent à rester en lecture + raisonnement, sans write_file ni
        # edit_file.  La phase Architect (Reasoner 12k tokens) est naturellement
        # skippée car _is_complex_modify exige intent == 'modify'.
        if getattr(self, "_resolved_intent", None) == "read":
            user_content = (
                "🔒 MODE LECTURE SEULE — l'utilisateur a demandé une ANALYSE, pas une modification.\n"
                "    • NE PAS utiliser write_file, edit_file, apply_patch, shell, run_python.\n"
                "    • Utiliser read_file, list_files, grep_search uniquement.\n"
                "    • Ta réponse finale est une analyse/opinion claire et structurée en français.\n\n"
                + user_content
            )

        # ── Contexte conversationnel : ce qui a été discuté avant cette tâche
        _conv_hist = (task.context or {}).get("conversation_history", "")
        if _conv_hist:
            user_content += (
                "\n\n## Contexte de conversation précédent\n"
                "L'utilisateur a discuté de ce qui suit JUSTE AVANT de te demander cette tâche. "
                "Utilise ces informations pour comprendre le contexte et les attentes :\n"
                f"{_conv_hist}\n"
            )

        # ── Mémoire pertinente (souvenirs ChromaDB)
        _mem_ctx = (task.context or {}).get("memory_context", "")
        if _mem_ctx:
            user_content += f"\n\n## Souvenirs pertinents\n{_mem_ctx}\n"

        # ── Skills actifs (transmis par delegate_task_handler) ──
        _skills_ctx = (task.context or {}).get("skills_context", "")
        if _skills_ctx:
            user_content += f"\n\n## Instructions spécifiques à appliquer dans ton code\n⚠️ Ce sont des GUIDELINES de style/qualité, PAS des outils appelables. Intègre-les dans le code que tu produis.\n{_skills_ctx}\n"

        # Exclure les clés déjà rendues du dump JSON contexte
        _rendered_keys = {"conversation_history", "memory_context"}
        ctx = {k: v for k, v in ctx.items() if k not in _rendered_keys}
        if ctx:
            user_content += f"\nContexte : {json.dumps(ctx, ensure_ascii=False, default=str)}"

        _project_files: list[str] = []
        if self._task_workspace_root:
            _ws_str = str(self._task_workspace_root).replace("\\", "/")
            _project_files = (task.context or {}).get("project_files", [])
            if not _project_files:
                try:
                    _ws_path = Path(str(self._task_workspace_root))
                    if _ws_path.exists():
                        _project_files = []
                        for _item in sorted(_ws_path.rglob("*")):
                            if _item.is_file() and not any(p in str(_item) for p in ["__pycache__", ".git", "node_modules"]):
                                _project_files.append(str(_item.relative_to(_ws_path)).replace("\\", "/"))
                except Exception:
                    pass
            _files_listing = "\n".join(f"  - {f}" for f in _project_files[:50]) if _project_files else "(vide)"
            _workspace_has_files = bool(_project_files)
            _workspace_creation_mode = (
                not _workspace_has_files
                or getattr(self, "_resolved_intent", None) == "create"
            )
            if _workspace_creation_mode and getattr(self, "_resolved_intent", None) != "read":
                self._resolved_intent = "create"
            _workspace_action_hint = (
                "MODE CREATION : commence DIRECTEMENT par write_file pour creer les fichiers demandes. "
                "N'utilise read_file/grep/list_files que si tu dois inspecter un fichier existant."
                if _workspace_creation_mode
                else "MODE MODIFICATION : commence DIRECTEMENT par read_file sur le fichier a modifier."
            )
            user_content += (
                f"\n\n⚠️ WORKSPACE ACTIF : {_ws_str}\n"
                "Tous tes chemins relatifs (read_file, write_file, edit_file, list_files) "
                "sont résolus depuis CE dossier — PAS depuis la racine Lumena.\n"
                f"\n📁 FICHIERS DU PROJET (déjà listés — PAS BESOIN de list_files) :\n{_files_listing}\n\n"
                f"{_workspace_action_hint}\n"
                "NE LIS PAS README.md, src/core.py, lumena_ultime.py — ce sont des fichiers Lumena, pas ton projet."
            )

        import re as _re
        _target_files_seen: list[str] = list(
            _re.findall(r'(?:src|tests)/[\w/]+\.py', task.description)
        )
        if ctx.get("file_path"):
            fp = ctx["file_path"]
            if fp not in _target_files_seen:
                _target_files_seen.append(fp)

        project_ctx = self._gather_project_context(task.description, target_files=_target_files_seen or None)
        if project_ctx:
            user_content += f"\n\n{project_ctx}"

        if prior_failures:
            user_content += (
                "\n\n--- Tentatives précédentes (LIRE ATTENTIVEMENT) ---\n"
                + "\n".join(prior_failures)
                + "\n\nNOTE: Adopte une approche DIFFÉRENTE des tentatives précédentes."
            )

        # P5: hint si dépendances manquantes dans le workspace
        if _project_files:
            _has_pkg = any("package.json" in f for f in _project_files)
            _has_req = any("requirements.txt" in f for f in _project_files)
            _has_js = any(f.endswith(".js") or f.endswith(".ts") for f in _project_files)
            _has_py = any(f.endswith(".py") for f in _project_files)
            if _has_js and not _has_pkg:
                user_content += "\n⚠️ package.json absent — génère-le si tu utilises des libs npm"
            if _has_py and not _has_req:
                user_content += "\n⚠️ requirements.txt absent — génère-le si tu utilises des libs tierces"

        _ws_files = _project_files if self._task_workspace_root else None
        _mode = getattr(self, '_resolved_intent', 'auto')
        if _mode not in ('create', 'modify'):
            _mode = 'auto'
        # Support prompt override pour DebugAgent / RefactorAgent
        _prompt_override = (task.context or {}).get("_system_prompt_override", "")
        if _prompt_override:
            _system_content = _CODE_AGENT_SYSTEM + _prompt_override
        else:
            _system_content = _build_system_prompt(
                task.description, workspace_files=_ws_files, mode=_mode, model_name=model_name,
            )
        # ── P6 : Convention scanning ──────────────────────────────────────────
        try:
            from src.config.codeagent_flags import CONVENTION_SCAN
            if CONVENTION_SCAN and _ws:
                from src.tools.convention_scanner import scan_conventions
                _conv_block = scan_conventions(Path(str(_ws)))
                if _conv_block:
                    _system_content = _system_content + "\n\n" + _conv_block
        except Exception:
            pass  # jamais bloquant
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _system_content},
            {"role": "user", "content": user_content},
        ]
        return messages, _target_files_seen, _project_files or None

    def _process_llm_response(
        self, raw_text: str, iteration: int, messages: list[dict], report: list[str],
    ) -> tuple[str, dict | None]:
        """
        Parse la réponse LLM. Retourne (tag, payload):
        - ("action", action_dict) — action valide à exécuter
        - ("continue", None) — vide/tronqué, messages mis à jour, caller continue
        - ("success_text", None) — texte non-parseable, caller retourne succès
        - ("done", action_dict) — action done, caller retourne succès
        """
        if not raw_text or raw_text in ("None", "null", ""):
            report.append(f"[iter {iteration}] LLM empty response, retry")
            logger.warning("[CodeAgent] Réponse vide à l'iter {} — retry", iteration)
            messages.append({"role": "assistant", "content": "{}"})
            messages.append({
                "role": "user",
                "content": "Ta réponse était VIDE. Réponds avec une action JSON valide.",
            })
            return ("continue", None)

        action = _parse_action_json(raw_text)
        _invalid_tool_catch_enabled = False

        # ── P8.INVALID_TOOL_CATCH : récupérer depuis clés alias (tool/name/function) ──
        try:
            from src.config.codeagent_flags import INVALID_TOOL_CATCH
            _invalid_tool_catch_enabled = bool(INVALID_TOOL_CATCH)
            if _invalid_tool_catch_enabled and isinstance(action, dict) and "action" not in action:
                for _alias in ("tool", "name", "function", "tool_name", "command_name"):
                    _val = action.get(_alias)
                    if isinstance(_val, str) and _val.strip():
                        action["action"] = _val.strip()
                        logger.debug("[CodeAgent] INVALID_TOOL_CATCH: '{}' → action", _alias)
                        break
        except Exception:
            pass

        # ── Stabilisation CodeAgent : contrat strict avant execution ───────
        if isinstance(action, dict):
            try:
                from src.config.codeagent_flags import CODEAGENT_ACTION_SCHEMA
                if CODEAGENT_ACTION_SCHEMA and ("action" in action or _invalid_tool_catch_enabled):
                    from src.agents.codeagent_action_schema import validate_codeagent_action
                    _validation = validate_codeagent_action(action)
                    if not _validation.valid:
                        report.append(f"[iter {iteration}] action schema invalid")
                        logger.warning("[CodeAgent] Action invalide: {}", _validation.message[:300])
                        # CA-1 — observabilité : sans le brut, ce refus est
                        # indiagnosticable (run démineur : 4 refus aveugles).
                        logger.debug(
                            "[CodeAgent] réponse brute refusée (400 1ers chars): {}",
                            raw_text[:400],
                        )
                        messages.append({"role": "assistant", "content": raw_text[:800]})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"{_validation.message}\n"
                                "Reponds avec UNE action JSON valide, sans prose, sans markdown.\n"
                                "⚠️ Les champs vont au NIVEAU RACINE du JSON, PAS sous "
                                "\"args\"/\"parameters\". Exemple correct : "
                                '{"action": "str_replace", "path": "script.js", '
                                '"old_str": "<texte exact présent dans le fichier>", '
                                '"new_str": "<remplacement>"}'
                            ),
                        })
                        return ("continue", None)
                    action = _validation.action or action
            except Exception as _schema_exc:
                logger.debug("[CodeAgent] action schema skipped: {}", _schema_exc)

        if not action or "action" not in action:
            _looks_truncated = (
                (raw_text.lstrip().startswith("{") and '"action"' in raw_text[:300])
                or ('"write_file"' in raw_text[:300])
                or ('"edit_file"' in raw_text[:300])
            )
            if _looks_truncated and iteration < _CODE_AGENT_MAX_ITER:
                report.append(f"[iter {iteration}] JSON tronqué, retry shorter")
                logger.warning("[CodeAgent] JSON tronqué à l'iter {} — demande plus court", iteration)
                messages.append({"role": "assistant", "content": raw_text[:500] + "..."})
                messages.append({
                    "role": "user",
                    "content": (
                        "⚠️ Ta réponse JSON a été TRONQUÉE par la limite de tokens du modèle.\n"
                        "Recommence en t'assurant que le JSON est COMPLET et VALIDE.\n"
                        "Si le contenu est très long, écris une base solide avec write_file "
                        "puis enrichis avec edit_file ou edit_lines.\n"
                        "IMPORTANT: cette fois, fais une action beaucoup plus petite: "
                        "thought < 160 caracteres, content < 8000 caracteres, un seul fichier."
                    ),
                })
                return ("continue", None)
            return ("success_text", None)

        action_type = action["action"]
        if action_type == "done":
            return ("done", action)

        return ("action", action)

    async def _post_action_hooks(
        self, *, action: dict, action_type: str, observation,
        messages: list[dict], task: AgentTask,
        session_snapshots: dict, target_files_seen: list[str],
        edits_since_last_test: int, reads_since_last_edit: int,
        context_cache: dict[str, str],
        local_bugfix_mode: bool = False,
    ) -> tuple:
        """Hooks post-exécution: session memory, context, auto-reread, tests, nudge.
        Retourne (observation, edits_since_last_test, reads_since_last_edit)."""
        # ── P8: enregistrer dans session memory ──
        obs_summary = str(observation)
        if action_type == "read_file":
            _read_content = observation.detail if isinstance(observation, ActionResult) else str(observation)
            self._record_session_read(action.get("path", ""), _read_content)
        elif action_type in ("edit_file", "edit_lines", "write_file", "str_replace", "insert_at_anchor", "apply_patch", "apply_patches"):
            self._record_session_edit(action.get("path", ""), action_type)
            try:
                _edited = getattr(self, "_edited_files", None)
                if not isinstance(_edited, set):
                    _edited = set()
                    self._edited_files = _edited
                _paths: list[str] = []
                if action.get("path"):
                    _paths.append(str(action.get("path")))
                if action.get("file"):
                    _paths.append(str(action.get("file")))
                if action_type == "apply_patches":
                    for _p in action.get("patches") or []:
                        if isinstance(_p, dict):
                            _pf = _p.get("file") or _p.get("path")
                            if _pf:
                                _paths.append(str(_pf))
                for _p in _paths:
                    if _p:
                        _edited.add(_p)
            except Exception:
                pass
            # ── Plan Architect UI : avancer le cursor à chaque édition réussie ──
            if "❌" not in str(observation)[:10]:
                try:
                    self._advance_architect_plan(
                        current_tool=action_type,
                        file_path=action.get("path", "") or "",
                    )
                except Exception:
                    pass
                # ── P5 : LSP pre-edit — noter les fichiers dépendants ──────────
                try:
                    from src.config.codeagent_flags import LSP_PRE_EDIT
                    if LSP_PRE_EDIT and self._task_workspace_root:
                        _lsp_file = action.get("path", "") or ""
                        if _lsp_file:
                            from src.tools.lsp_pre_edit import get_related_files, format_related_files_note
                            _related = await get_related_files(
                                self._task_workspace_root,
                                _lsp_file,
                                task_id=str(task.task_id),
                            )
                            _note = format_related_files_note(_related, _lsp_file)
                            if _note:
                                obs_str = str(observation) + _note
                except Exception:
                    pass  # jamais bloquant
        elif action_type == "run_tests" and "❌" in obs_summary:
            self._record_session_error(obs_summary[:200])

        # ── Todo interne CodeAgent : avance courte et non bloquante ───────
        try:
            _todo = getattr(self, "_codeagent_todo", None)
            if _todo:
                _obs_full = observation.full() if isinstance(observation, ActionResult) else str(observation)
                _success = not any(tok in _obs_full[:500].lower() for tok in (
                    "❌", "⛔", "statut_outil=error", "statut_outil=blocked",
                    "traceback", "syntaxerror", "erreur",
                ))
                _todo_note = _todo.observe_action(
                    action_type,
                    path=str(action.get("path") or action.get("file") or ""),
                    success=_success,
                )
                if _todo_note:
                    if isinstance(observation, ActionResult):
                        observation = ActionResult(
                            observation.summary,
                            ((observation.detail or "") + "\n\n" + _todo_note).strip(),
                        )
                    else:
                        observation = ActionResult(str(observation), _todo_note)
        except Exception:
            pass

        # ── WorldModel : maintenir la structure live des fichiers édités ──
        if (
            action_type in ("write_file", "edit_file", "str_replace", "edit_lines", "apply_patch")
            and "❌" not in str(observation)[:10]
        ):
            _wm_path = action.get("path", "") or ""
            if _wm_path:
                try:
                    from src.context.world_model import get_world_model
                    from src.context.ast_parser import get_ast_parser
                    _ws_root = self._task_workspace_root or Path.cwd()
                    _wm = get_world_model(_ws_root)
                    _cur_iter = int(getattr(self, "_current_iter", 0) or 0)
                    # Récupère le contenu actuel pour reparser la structure
                    _content_after: Optional[str] = None
                    try:
                        _wm_abs = self._resolve_path(_wm_path)
                        if _wm_abs.exists() and _wm_abs.is_file():
                            _content_after = _wm_abs.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        _content_after = None
                    if action_type == "write_file" and _content_after is not None:
                        _wm.update_from_write(_wm_path, _content_after, iter_num=_cur_iter, action=action_type)
                    else:
                        _wm.update_from_edit(_wm_path, iter_num=_cur_iter,
                                              content_after=_content_after, action=action_type)
                    # Invalide caches AST/RepoMap pour ce fichier
                    try:
                        get_ast_parser().invalidate(Path(_wm_path))
                    except Exception:
                        pass
                except Exception as _wm_exc:
                    logger.debug(f"[WorldModel] skip update {_wm_path}: {_wm_exc}")

        # ── P2: enrichir contexte dynamiquement après read_file ──
        if action_type == "read_file":
            read_path = action.get("path", "")
            if read_path and read_path.endswith(".py") and read_path not in target_files_seen:
                target_files_seen.append(read_path)
                if read_path not in context_cache:
                    delta_ctx = self._gather_project_context("", target_files=[read_path])
                    context_cache[read_path] = delta_ctx or ""
                if context_cache[read_path] and "Imports du fichier cible" in context_cache[read_path]:
                    observation += f"\n\n{context_cache[read_path]}"

        # ── Grep 0-result : tracker et warner en cas de répétition ──
        _obs_for_grep = str(observation)
        if action_type in ("grep", "grep_search") and ("Aucun résultat" in _obs_for_grep or "aucun resultat" in _obs_for_grep.lower() or "no results" in _obs_for_grep.lower()):
            _gp = str(action.get("pattern", ""))
            _gpath = str(action.get("path", ".") or ".")
            if _gp:
                _gcount = self._record_grep_zero_result(_gp, _gpath)
                if _gcount >= 2:
                    self._grep_zero_repeats += 1
                    logger.info(
                        "[GrepTrack] pattern répété sans résultat ({}×): {!r} dans {}",
                        _gcount, _gp[:60], _gpath,
                    )
                    observation += (
                        f"\n\n⚠️ STRATÉGIE À CHANGER : le pattern `{_gp[:80]}` a déjà renvoyé "
                        f"0 résultat {_gcount}× dans `{_gpath}`. N'insiste pas. Essaye :\n"
                        "  • un pattern plus court / plus générique\n"
                        "  • un autre fichier ou élargir le path\n"
                        "  • `read_file` direct si tu connais la structure (voir WorldModel ci-dessus)"
                    )
                    # P3 : 3ème répétition ⇒ déclenche une leçon Reflexion (fire-and-forget)
                    if _gcount >= 3:
                        try:
                            _trace_tail = "\n".join(
                                f"[{m.get('role','?')}] {str(m.get('content',''))[:180]}"
                                for m in messages[-4:]
                            )
                            asyncio.create_task(
                                self._maybe_generate_reflexion(
                                    signal=f"grep pattern {_gp[:60]!r} 0 résultat {_gcount}× dans {_gpath}",
                                    context_tail=_trace_tail,
                                    task_hint=getattr(task, "description", "") or "",
                                )
                            )
                        except Exception:
                            pass

        # ── Auto-reread si edit_file/str_replace echoue (contenu non trouve) ──
        obs_str = str(observation)
        if action_type in ("edit_file", "str_replace") and ("non trouv" in obs_str or "not found" in obs_str.lower() or "no match" in obs_str.lower()):
            re_path = action.get("path", "")
            if re_path:
                # P5.1 : Comptabiliser les échecs par fichier
                self._edit_fail_for_path[re_path] = self._edit_fail_for_path.get(re_path, 0) + 1
                _fail_count = self._edit_fail_for_path[re_path]
                reread = await self._execute_loop_action({"action": "read_file", "path": re_path}, snapshots=session_snapshots)
                _reread_budget = 8000 if getattr(self, '_resolved_intent', 'auto') == "modify" else 4000
                observation += f"\n\n[Auto-reread de {re_path}]:\n{reread.full(_reread_budget)}"
                # P5.2 : Escalade automatique si 2+ échecs sur le même fichier
                if _fail_count >= 2:
                    try:
                        _esc_content = self._resolve_path(re_path).read_text(encoding="utf-8", errors="replace")
                        messages.append({
                            "role": "user",
                            "content": (
                                f"⚠️ ESCALADE : str_replace/edit_file a échoué {_fail_count}× sur {re_path}.\n"
                                "CHANGE D'APPROCHE OBLIGATOIRE. Voici le contenu EXACT avec numéros de ligne :\n"
                                f"```\n{chr(10).join(f'{i+1:4d} | {l}' for i, l in enumerate(_esc_content.split(chr(10))))}\n```\n"
                                "Utilise MAINTENANT edit_lines avec les numéros de ligne ci-dessus (jamais de problème de matching)."
                            ),
                        })
                        self._edit_fail_for_path[re_path] = 0  # reset après escalade
                    except Exception:
                        pass
                    # Reflexion : déclenche génération async d'une leçon (fire-and-forget)
                    try:
                        _trace_tail = "\n".join(
                            f"[{m.get('role','?')}] {str(m.get('content',''))[:200]}"
                            for m in messages[-4:]
                        )
                        _signal = f"str_replace échoué {_fail_count}× sur {re_path} ({action_type})"
                        asyncio.create_task(
                            self._maybe_generate_reflexion(
                                signal=_signal,
                                context_tail=_trace_tail,
                                task_hint=getattr(task, "description", "") or "",
                            )
                        )
                    except Exception:
                        pass

        # ── Phase 0.2 — Trigger Reflexion : run_command qui échoue en boucle ──
        # Observé en prod (session 09:35-09:37) : 5 tentatives consécutives
        # `node --check` avec chemins différents sans qu'aucune lesson ne soit
        # générée. Les triggers existants (grep zero, str_replace fail) ne
        # couvraient pas ce pattern.
        # Signature normalisée pour grouper les variantes de la même cause
        # (ex. "node|exit:1" indépendamment du chemin testé).
        if action_type == "run_command":
            _obs_run = str(observation)[:600].lower()
            _is_run_error = (
                "exit:1" in _obs_run or "exit:2" in _obs_run
                or "introuvable" in _obs_run or "not found" in _obs_run
                or "n'est pas reconnu" in _obs_run or "denied" in _obs_run
                or "bloqu" in _obs_run or "❌" in str(observation)[:10]
            )
            if _is_run_error:
                _cmd_text = str(action.get("command", "") or "")
                _sig = self._normalize_cmd_fail_signature(_cmd_text, _obs_run)
                _cmd_store = getattr(self, "_cmd_fail_count", None)
                if not isinstance(_cmd_store, dict):
                    _cmd_store = {}
                    self._cmd_fail_count = _cmd_store
                _cmd_store[_sig] = _cmd_store.get(_sig, 0) + 1
                _cmd_count = _cmd_store[_sig]
                # 3e échec sur la même signature → leçon Reflexion (fire-and-forget)
                if _cmd_count >= 3:
                    logger.info(
                        "[CmdFailTrack] signature répétée ({}×): {} — déclenche reflexion",
                        _cmd_count, _sig,
                    )
                    try:
                        _trace_tail = "\n".join(
                            f"[{m.get('role','?')}] {str(m.get('content',''))[:200]}"
                            for m in messages[-4:]
                        )
                        asyncio.create_task(
                            self._maybe_generate_reflexion(
                                signal=f"run_command échoué {_cmd_count}× sur signature {_sig} (cmd préfix: {_cmd_text[:80]})",
                                context_tail=_trace_tail,
                                task_hint=getattr(task, "description", "") or "",
                            )
                        )
                        # Reset pour éviter spam sur même signature dans la tâche
                        _cmd_store[_sig] = 0
                    except Exception:
                        pass

        # ── Compteur edits pour auto-run tests ──
        _SINGLE_EDIT_ACTIONS = ("edit_file", "apply_patch", "write_file", "edit_lines", "str_replace", "insert_at_anchor")
        _BATCH_EDIT_ACTIONS = ("apply_patches", "batch_patch", "multi_patch")
        # Reset compteur anti-relecture pour TOUS les fichiers modifiés (single ou batch)
        _edited_paths: list[str] = []
        if action_type in _SINGLE_EDIT_ACTIONS:
            _sp = action.get("path", "")
            if _sp:
                _edited_paths.append(_sp)
        elif action_type in _BATCH_EDIT_ACTIONS:
            for _p in (action.get("patches") or []):
                if isinstance(_p, dict):
                    _fp = _p.get("file") or _p.get("path") or ""
                    if _fp:
                        _edited_paths.append(_fp)
        if _edited_paths and "❌" not in str(observation)[:10]:
            _rc = getattr(self, "_read_count_per_file", None)
            if isinstance(_rc, dict):
                for _ep in _edited_paths:
                    _ed_key = str(_ep).replace("\\", "/").strip()
                    if _ed_key in _rc:
                        _rc[_ed_key] = 0
        if action_type in _SINGLE_EDIT_ACTIONS or action_type in _BATCH_EDIT_ACTIONS:
            edits_since_last_test += 1
            edit_path = action.get("path", "") or (_edited_paths[0] if _edited_paths else "")
            if edit_path and "❌" not in str(observation)[:10]:
                related = self._find_related_tests(edit_path)
                if related:
                    observation += f"\n\nTests probablement impactés : {', '.join(related)}"
                try:
                    _refreshed = await self._execute_loop_action(
                        {"action": "read_file", "path": edit_path}, snapshots=session_snapshots
                    )
                    if isinstance(_refreshed, ActionResult) and _refreshed.detail:
                        self._record_session_read(edit_path, _refreshed.detail)
                except Exception:
                    pass
        if action_type == "run_tests":
            edits_since_last_test = 0

        # ── P_BONUS_C : Tests ciblés immédiat post-edit Python + auto-revert si fail ──
        _edit_path_bonus = action.get("path", "")
        _task_desc_lower_bonus = (task.description or "").lower()
        _is_workspace_project_bonus = "workspace/" in _task_desc_lower_bonus or "workspace\\" in _task_desc_lower_bonus
        _is_py_edit_bonus = (
            action_type in ("edit_file", "str_replace", "edit_lines", "apply_patch")
            and _edit_path_bonus.endswith(".py")
            and "✅" in str(observation)[:10]
            and not _is_workspace_project_bonus
        )
        if _is_py_edit_bonus:
            _related_bonus = self._find_related_tests(_edit_path_bonus)
            if _related_bonus:
                try:
                    _quick_test = await self._execute_loop_action(
                        {"action": "run_tests", "test_path": " ".join(_related_bonus[:3])},
                        snapshots=session_snapshots,
                    )
                    _test_out = _quick_test.full()[:2000]
                    observation = str(observation) + f"\n\n[Tests ciblés post-edit: {', '.join(_related_bonus[:3])}]\n{_test_out}"
                    # Auto-revert si les tests cassent (aucun passed + FAILED/ERROR présents)
                    if ("FAILED" in _test_out or "ERROR" in _test_out) and "passed" not in _test_out.lower():
                        _reverted = self._rollback_session(session_snapshots)
                        observation = str(observation) + (
                            f"\n\n🔄 AUTO-REVERT : {_reverted} fichier(s) restauré(s) — les tests ont cassé.\n"
                            "Tu DOIS corriger ton approche : relis le fichier et tente une stratégie différente."
                        )
                    edits_since_last_test = 0
                except Exception:
                    pass

        # ── P6 : Self-repair syntaxe — tracker les erreurs répétées post-edit ──
        _edit_path_p6 = action.get("path", "")
        # Capturer l'état de restriction AVANT que P6 le traite (le else peut faire discard)
        _p6_key_pre = str(_edit_path_p6).replace("\\", "/").strip() if _edit_path_p6 else ""
        _was_restricted_pre = bool(_p6_key_pre and _p6_key_pre in self._edit_restricted_files)
        if (
            action_type in ("edit_file", "apply_patch", "write_file", "edit_lines", "str_replace")
            and "✅" in str(observation)[:10]
            and _edit_path_p6
        ):
            _p6_syntax = ""
            # observation.full() capture summary + detail (str() ne retourne que le summary)
            _p6_obs_full = (observation.full() if isinstance(observation, ActionResult) else str(observation)).lower()
            _SYNTAX_PATTERNS = (
                "erreur de syntaxe python", "syntaxeerror", "⚠️ syntaxe",
                "unexpected token", "parse error", "failed to compile",
                "unterminated", "invalid syntax",
            )
            _WEB_PATTERNS = (
                "erreur web", "⚠️ web", "bracket imbalance", "js syntaxerror",
                "js/ts bracket", "js erreur",
            )
            if any(p in _p6_obs_full for p in _SYNTAX_PATTERNS):
                _p6_syntax = "syntaxe Python"
            elif any(p in _p6_obs_full for p in _WEB_PATTERNS):
                _p6_syntax = "syntaxe web (JS/HTML/CSS)"
            _p6_key = str(_edit_path_p6).replace("\\", "/").strip()
            if _p6_syntax:
                self._self_repair_count_per_file[_p6_key] = self._self_repair_count_per_file.get(_p6_key, 0) + 1
                self._self_repair_count += 1
                _p6_file_count = self._self_repair_count_per_file[_p6_key]
                if _p6_file_count >= 3:
                    # Seuil dépassé → restaurer depuis le dernier état syntaxiquement valide
                    _restored_clean = False
                    # Priorité : dernier état valide connu > snapshot pré-session
                    _clean_snap = self._syntax_clean_snapshot.get(_p6_key) or (
                        (session_snapshots or {}).get(_p6_key) or
                        (session_snapshots or {}).get(_edit_path_p6)
                    )
                    if _clean_snap:
                        try:
                            _p6_abs = self._resolve_path(_edit_path_p6)
                            _p6_abs.write_text(_clean_snap, encoding="utf-8")
                            _restored_clean = True
                            self._self_repair_count_per_file[_p6_key] = 0
                            self._self_repair_count = 0
                            self._edit_restricted_files.add(_p6_key)
                            logger.warning(
                                "[P6] {} erreurs {} sur {} → restauration dernier état valide",
                                _p6_file_count, _p6_syntax, _edit_path_p6,
                            )
                        except Exception as _p6_exc:
                            logger.warning("[P6] Restauration snapshot échouée: {}", _p6_exc)
                    if _restored_clean:
                        messages.append({
                            "role": "user",
                            "content": (
                                f"🔄 RESTAURATION AUTOMATIQUE après {_p6_file_count} erreurs de {_p6_syntax} "
                                f"non résolues sur `{_edit_path_p6}`.\n"
                                f"Le fichier a été remis dans son **dernier état syntaxiquement valide** "
                                f"(les edits valides précédents sont conservés, seuls les edits cassés sont annulés).\n\n"
                                f"⛔ NOUVELLE STRATÉGIE OBLIGATOIRE pour `{_edit_path_p6}` :\n"
                                f"1. Relis le fichier COMPLET avec `read_file` pour voir l'état actuel\n"
                                f"2. Utilise `write_file` pour réécrire un bloc entier — PAS `edit_lines` fragmenté\n"
                                f"3. Modifie 1 fonction à la fois maximum\n"
                                f"4. Vérifie mentalement l'équilibre des accolades avant d'écrire"
                            ),
                        })
                    else:
                        # Fallback : undo_edit si aucun snapshot disponible
                        try:
                            _undo_r = await self._execute_loop_action(
                                {"action": "undo_edit", "path": _edit_path_p6}, snapshots=session_snapshots
                            )
                            self._self_repair_count_per_file[_p6_key] = 0
                            self._self_repair_count = 0
                            self._edit_restricted_files.add(_p6_key)
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"🔄 AUTO-UNDO après {_p6_file_count} erreurs de {_p6_syntax} non résolues.\n"
                                    f"Résultat undo: {_undo_r}\n"
                                    "Relis le fichier COMPLET avec read_file et recommence avec `write_file` "
                                    "bloc entier plutôt qu'`edit_lines` fragmenté."
                                ),
                            })
                        except Exception:
                            pass
            else:
                # Edit réussi sans erreur syntaxique → mettre à jour le dernier état valide connu
                try:
                    _p6_abs_ok = self._resolve_path(_edit_path_p6)
                    if _p6_abs_ok.exists():
                        self._syntax_clean_snapshot[_p6_key] = _p6_abs_ok.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
                self._self_repair_count_per_file[_p6_key] = 0
                self._self_repair_count = 0
                # Retirer la restriction si l'edit a réussi proprement
                self._edit_restricted_files.discard(_p6_key)

        # ── P6 : Rappel persistant pour fichiers post-restauration ──
        # Utilise _was_restricted_pre (capturé avant P6) pour que le rappel survive
        # même quand le else-branch de P6 vient de faire discard (edit valide)
        if (
            _was_restricted_pre
            and action_type in ("edit_lines", "str_replace", "edit_file")
        ):
            observation += (
                f"\n\n⚠️ RAPPEL : `{_edit_path_p6}` vient d'être restauré après des erreurs syntaxiques répétées. "
                "Préfère `write_file` pour réécrire un bloc entier plutôt qu'`edit_lines` ligne par ligne. "
                "Vérifie l'équilibre des accolades avant de valider."
            )

        # ── Compteur read-only : détecter boucles de lecture sans écriture ──
        _passive_actions = ("read_file", "list_files", "grep", "search_in_files", "think",
                            "read_files_batch", "read_batch", "batch_read")
        _active_actions = ("edit_file", "write_file", "edit_lines", "apply_patch", "str_replace",
                           "insert_at_anchor", "apply_patches", "batch_patch", "multi_patch")
        if local_bugfix_mode:
            _READS_BEFORE_NUDGE = 8
            _READS_BEFORE_FORCE = 16
            _READS_BEFORE_HARD_STOP = 24
        else:
            _READS_BEFORE_NUDGE = 5
            _READS_BEFORE_FORCE = 10
            _READS_BEFORE_HARD_STOP = 15  # très permissif : seulement coupe les vraies boucles infinies
        if action_type in _passive_actions:
            reads_since_last_edit += 1
        elif (
            action_type in _active_actions
            or action_type == "done"
            or (
                action_type == "run_command"
                and _run_command_looks_mutating(
                    str(action.get("command", "") if isinstance(action, dict) else ""),
                    str(observation),
                )
            )
        ):
            reads_since_last_edit = 0
        if reads_since_last_edit >= _READS_BEFORE_HARD_STOP:
            # Seulement pour couper les vraies boucles infinies après 2 warnings ignorés
            logger.error(
                "[CodeAgent] {} reads consécutifs — ARRÊT FORCÉ (2 warnings ignorés)",
                reads_since_last_edit,
            )
            self._force_done = True  # flag lu par _single_code_attempt pour sortir
            messages.append({
                "role": "user",
                "content": (
                    f"🚫 ARRÊT FORCÉ — {reads_since_last_edit} lectures sans modification, "
                    "2 warnings ignorés. La boucle est terminée. "
                    "Tu n'as RIEN modifié pendant toute la tâche : c'est un échec."
                ),
            })
        elif reads_since_last_edit >= _READS_BEFORE_FORCE:
            # Rappel ferme mais non bloquant — on injecte un récap de ce qui a déjà été fait
            logger.warning(
                "[CodeAgent] {} reads consécutifs sans edit — rappel récap",
                reads_since_last_edit,
            )
            _recap = self._build_actions_recap() if hasattr(self, "_build_actions_recap") else ""
            messages.append({
                "role": "user",
                "content": (
                    f"🛑 {reads_since_last_edit} lectures consécutives sans modification.\n\n"
                    f"{_recap}\n"
                    "Prends ton temps, mais souviens-toi de ce que tu as déjà lu avant de relire.\n"
                    "Quand tu es prêt, utilise :\n"
                    "• `edit_lines` / `str_replace` pour modifier\n"
                    "• `insert_at_anchor` pour insérer autour d'un repère\n"
                    "• `write_file` pour créer un fichier\n"
                    "• `done` si tu as terminé ou si tu ne trouves rien."
                ),
            })
        elif reads_since_last_edit >= _READS_BEFORE_NUDGE:
            _recap = self._build_actions_recap() if hasattr(self, "_build_actions_recap") else ""
            messages.append({
                "role": "user",
                "content": (
                    f"ℹ️ Tu as fait {reads_since_last_edit} lectures consécutives sans modifier.\n\n"
                    f"{_recap}\n"
                    "Tu peux prendre ton temps — mais évite de relire les mêmes fichiers.\n"
                    "Quand tu sais quoi faire : edit_lines / str_replace / insert_at_anchor / write_file."
                ),
            })

        # ── Forcer run_tests apres N edits consecutifs ──
        _EDITS_BEFORE_FORCED_TEST = 3
        _task_desc_lower = task.description.lower() if task.description else ""
        _lumena_root_str = str(getattr(self, "_lumena_root", "")).rstrip("/\\")
        _task_ws_str = str(getattr(self, "_task_workspace_root", "") or "").rstrip("/\\")
        _is_workspace_project = (
            ("workspace/" in _task_desc_lower or "workspace\\" in _task_desc_lower)
            or (_task_ws_str and _lumena_root_str and _task_ws_str != _lumena_root_str)
        )
        if edits_since_last_test >= _EDITS_BEFORE_FORCED_TEST and not _is_workspace_project:
            test_obs = await self._execute_loop_action({"action": "run_tests", "test_path": ""}, snapshots=session_snapshots)
            observation += f"\n\n[Auto-test apres {edits_since_last_test} edits]:\n{test_obs.full()[:5000]}"
            edits_since_last_test = 0

        return observation, edits_since_last_test, reads_since_last_edit

    async def _maybe_compact(self, messages: list[dict], llm, report: list[str]) -> list[dict]:
        """Compaction token-aware avec résumé LLM si le seuil est dépassé.
        Mode modify → seuil plus bas (0.60) pour compacter plus tôt et éviter de noyer les éditions.
        Mode create → seuil standard (0.85).
        """
        _mode_compact = getattr(self, '_resolved_intent', 'auto')
        _compact_ratio = 0.60 if _mode_compact == "modify" else _COMPACTION_RATIO
        _est_tokens = _estimate_tokens(messages)
        if len(messages) > _MIN_COMPACTION_MESSAGES and _est_tokens > _CONTEXT_WINDOW_TOKENS * _compact_ratio:
            # ── P2 Plan Suprême : pruning progressif des observations anciennes ──
            # Pass 1 (cheap) : truncate old tool outputs head+tail. Si ça suffit, on évite le LLM summary.
            try:
                from src.tools.compaction import prune_large_observations
                pruned_messages, pruned_n = prune_large_observations(messages)
                if pruned_n > 0:
                    new_est = _estimate_tokens(pruned_messages)
                    if new_est <= _CONTEXT_WINDOW_TOKENS * _compact_ratio:
                        report.append(f"[compact] {pruned_n} observation(s) pruned ({_est_tokens}→{new_est} tokens)")
                        return pruned_messages
                    # Pruning pas suffisant — continuer sur le LLM summary avec les messages pruned
                    messages = pruned_messages
            except Exception as _prune_exc:
                logger.warning("[CodeAgent] prune_large_observations failed: {}", _prune_exc)

            kept_head = messages[:2]
            kept_tail = messages[-6:]
            msgs_to_compact = messages[2:-6]
            dropped = len(msgs_to_compact)

            # ── P8.COMPACTION_REPLAY : sauvegarde pré-compaction pour audit/replay ──
            try:
                from src.config.codeagent_flags import COMPACTION_REPLAY
                if COMPACTION_REPLAY and dropped > 0:
                    import json
                    import time
                    from src.utils.paths import LOGS_DIR
                    _task_id = getattr(self, "_current_task_id", "session")
                    _safe_id = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(_task_id))[:80] or "session"
                    _replay_dir = LOGS_DIR / "codeagent" / _safe_id
                    _replay_dir.mkdir(parents=True, exist_ok=True)
                    _replay_file = _replay_dir / f"compaction_{int(time.time() * 1000)}.json"
                    _replay_file.write_text(
                        json.dumps({
                            "dropped_count": dropped,
                            "tokens_before": _est_tokens,
                            "head_count": len(kept_head),
                            "tail_count": len(kept_tail),
                            "messages": msgs_to_compact,
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    report.append(f"[compact] replay save: {_replay_file.name}")
            except Exception as _rep_exc:
                logger.debug("[CodeAgent] compaction_replay save failed: {}", _rep_exc)

            llm_summary = await self._summarize_for_compaction(msgs_to_compact, llm)
            if llm_summary:
                summary_content = f"[Résumé des {dropped} messages précédents]\n{llm_summary}\n\n"
            else:
                summary_content = (
                    f"[{dropped} messages compactés]\n"
                    f"Actions précédentes: {', '.join(r[:60] for r in report[:-3])}\n\n"
                )

            _ws_parts: list[str] = []
            for _fp, _fc in self._session_memory["files_read"].items():
                _ws_parts.append(f"--- {_fp} ---\n{_fc[:8000]}")
            _workspace_state = "\n".join(_ws_parts)[:24000]
            if _workspace_state:
                summary_content += (
                    f"=== FICHIERS EN MÉMOIRE (contenu actuel — NE PAS RELIRE) ===\n"
                    f"{_workspace_state}\n\n"
                    f"Tu as DÉJÀ LU ces fichiers. Édite directement."
                )
            return kept_head + [{"role": "user", "content": summary_content}] + kept_tail
        return messages

    async def _check_python_syntax(self, file_path: str) -> str:
        """
        Lint un fichier Python via ruff (fallback py_compile).
        Detecte variables non definies, imports inutiles, erreurs de syntaxe, etc.
        Utilise le sandbox Docker si disponible.
        Retourne "" si OK, message d'erreur sinon.
        """
        import subprocess
        if not file_path or not file_path.endswith(".py"):
            return ""
        try:
            root = Path(__file__).parent.parent.parent
            abs_path = self._resolve_path(file_path)
            if not abs_path.exists():
                return ""

            # Essayer le sandbox Docker d'abord
            try:
                from ..utils.docker_sandbox import is_docker_available, run_lint_in_sandbox
                if await is_docker_available():
                    return await run_lint_in_sandbox(str(abs_path), str(root))
            except Exception:
                pass  # Fallback local

            # Essayer ruff d'abord (plus riche que py_compile)
            ruff_exe = root / "venv" / "Scripts" / "ruff.exe"
            if not ruff_exe.exists():
                ruff_exe = root / "venv" / "bin" / "ruff"  # Linux/macOS
            if ruff_exe.exists():
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [
                        str(ruff_exe), "check", "--select",
                        "E,F,W",  # Errors + pyflakes (undef vars, unused imports) + warnings
                        "--no-fix", "--output-format", "concise",
                        str(abs_path),
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                if proc.returncode != 0 and proc.stdout.strip():
                    return proc.stdout.strip()[:600]
                return ""
            # Fallback: py_compile (syntaxe uniquement)
            proc = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "py_compile", str(abs_path)],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return (proc.stderr or proc.stdout).strip()[:500]
        except Exception:
            pass
        return ""

    async def _check_python_types(self, file_path: str) -> str:
        """
        Type check un fichier Python via mypy (fichier isolé).
        Ne garde que les erreurs haute valeur: arg-type, return-value, assignment, call-overload.
        Fallback silencieux si mypy absent.
        """
        import subprocess
        import shutil
        if not file_path or not file_path.endswith(".py"):
            return ""
        mypy_exe = shutil.which("mypy")
        if not mypy_exe:
            return ""
        try:
            abs_path = self._resolve_path(file_path)
            if not abs_path.exists():
                return ""
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    mypy_exe,
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    "--show-error-codes",
                    "--hide-error-context",
                    str(abs_path),
                ],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0:
                return ""
            # Filtrer: ne garder que les codes haute valeur
            _HIGH_VALUE = {"[arg-type]", "[return-value]", "[assignment]", "[call-overload]"}
            lines = proc.stdout.strip().split("\n")
            filtered = [l for l in lines if any(code in l for code in _HIGH_VALUE)]
            if not filtered:
                return ""
            return "\n".join(filtered[:10])[:800]
        except Exception:
            return ""

    async def _check_web_syntax(self, file_path: str) -> str:
        """
        Valide la syntaxe d'un fichier web (JS/HTML/CSS).
        - JS: node --check si disponible, sinon bracket balance
        - HTML: bracket balance (<tag> vs </tag>) + balises structurelles
        - CSS: bracket balance { vs }
        Retourne "" si OK, message d'erreur sinon.
        """
        import subprocess
        if not file_path:
            return ""
        abs_path = self._resolve_path(file_path)
        if not abs_path.exists():
            return ""
        ext = abs_path.suffix.lower()
        if ext not in (".js", ".ts", ".html", ".htm", ".css"):
            return ""
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

        errors: list[str] = []

        if ext in (".js", ".ts"):
            # 1) Essayer node --check (validation syntaxique réelle)
            if ext == ".js":
                import shutil
                node_exe = shutil.which("node")
                if node_exe:
                    try:
                        proc = await asyncio.to_thread(
                            subprocess.run,
                            [node_exe, "--check", str(abs_path)],
                            capture_output=True, text=True, timeout=10,
                        )
                        if proc.returncode != 0:
                            err = (proc.stderr or proc.stdout).strip()
                            # Extraire la première ligne d'erreur pertinente
                            for line in err.split("\n"):
                                if "SyntaxError" in line or "Unexpected" in line:
                                    errors.append(f"JS SyntaxError: {line.strip()}")
                                    break
                            if not errors and err:
                                errors.append(f"JS erreur: {err[:200]}")
                    except Exception:
                        pass  # node absent ou timeout → fallback bracket check
            # 2) Bracket balance check (string-aware)
            # Skip si node --check a déjà validé le fichier (pas d'erreurs de syntaxe)
            _node_validated = ext == ".js" and node_exe and not errors
            if not _node_validated:
                _brk, _prn = _count_brackets_clean(content)
                if abs(_brk) > 1:
                    errors.append(f"JS/TS bracket imbalance: {_brk:+d} net accolades")
                if abs(_prn) > 1:
                    errors.append(f"JS/TS parenthèses: {_prn:+d} net parenthèses")

        elif ext in (".html", ".htm"):
            # Bracket balance des balises structurelles
            import re as _re_web
            # Compter les balises ouvrantes/fermantes structurelles
            _structural_tags = ["div", "section", "main", "header", "footer", "nav", "article", "aside", "ul", "ol", "li", "table", "tr", "td", "th", "form", "script", "style"]
            for tag in _structural_tags:
                opens = len(_re_web.findall(rf'<{tag}[\s>]', content, _re_web.IGNORECASE))
                closes = len(_re_web.findall(rf'</{tag}\s*>', content, _re_web.IGNORECASE))
                if opens > closes + 1:
                    errors.append(f"HTML: {opens} <{tag}> ouvrantes mais seulement {closes} </{tag}> fermantes")
            # Vérifier que html/head/body sont fermés
            if "<html" in content.lower() and "</html>" not in content.lower():
                errors.append("HTML: </html> manquant")
            if "<body" in content.lower() and "</body>" not in content.lower():
                errors.append("HTML: </body> manquant")

        elif ext == ".css":
            # Bracket balance (string-aware)
            _brk_css, _ = _count_brackets_clean(content)
            if abs(_brk_css) > 0:
                errors.append(f"CSS bracket imbalance: {_brk_css:+d} net accolades")
            # Détection de propriétés dupliquées consécutives (ex: display: flex; display: flex;)
            lines = content.split("\n")
            prev_prop = ""
            for i, line in enumerate(lines):
                stripped = line.strip()
                if ":" in stripped and stripped.endswith(";"):
                    prop = stripped.split(":")[0].strip()
                    if prop and prop == prev_prop:
                        errors.append(f"CSS L{i+1}: propriété dupliquée consécutive '{prop}'")
                    prev_prop = prop
                elif stripped in ("{", "}", ""):
                    prev_prop = ""
                else:
                    prev_prop = ""

        if errors:
            return "\n".join(errors[:5])[:600]
        return ""

    def _find_related_tests(self, modified_file: str) -> list[str]:
        """Trouve les fichiers tests qui importent le module modifié."""
        root = Path(__file__).parent.parent.parent
        tests_dir = root / "tests"
        if not tests_dir.exists():
            return []
        # Extraire le nom du module (src/tools/apply_patch.py → apply_patch)
        mod_name = Path(modified_file).stem
        # Aussi le chemin d'import (src.tools.apply_patch)
        mod_import = modified_file.replace("/", ".").replace("\\", ".").removesuffix(".py")
        results: list[str] = []
        for tf in tests_dir.glob("test_*.py"):
            try:
                content = tf.read_text(encoding="utf-8", errors="ignore")
                if mod_name in content or mod_import in content:
                    results.append(tf.name)
            except Exception:
                pass
        return results[:10]

    async def _write_file_action(self, file_path: str, content: str) -> str:
        """Crée ou écrase un fichier avec le contenu donné."""
        if not file_path:
            return "❌ Chemin de fichier manquant."
        if not content or not content.strip():
            return "❌ Contenu vide."

        # ── Anti-rewrite: compter les write_file consécutifs sur le même fichier ──
        if not hasattr(self, "_write_counts"):
            self._write_counts: dict[str, int] = {}
        self._write_counts[file_path] = self._write_counts.get(file_path, 0) + 1
        _wc = self._write_counts[file_path]

        try:
            abs_path = self._resolve_path(file_path)
            _ext = abs_path.suffix.lower()

            # ── Anti-rewrite nudge: après 2 rewrites, forcer edit_lines ──
            if _wc >= 3 and _ext in _WEB_BRACKET_EXTS:
                _bracket_info = _locate_bracket_errors(content, _ext)
                if _bracket_info:
                    return (
                        f"⛔ STOP — tu as déjà réécrit {file_path} {_wc} fois et les brackets "
                        f"sont TOUJOURS déséquilibrés.\n\n{_bracket_info}\n\n"
                        f"OBLIGATION: utilise read_file pour lire le fichier actuel, puis "
                        f"str_replace ou edit_lines pour corriger UNIQUEMENT les lignes problématiques. "
                        f"Ne réécris PLUS le fichier entier."
                    )
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            # Détection de contenu tronqué (HTML/JS/CSS incomplets)
            _trunc_warn = ""
            if _ext == ".html":
                if content.count("<") > content.count(">") + 2:
                    _trunc_warn = "\n⚠️ HTML potentiellement tronqué (balises non fermées). Vérifie le fichier."
                if "<html" in content.lower() and "</html>" not in content.lower():
                    _trunc_warn = "\n⚠️ HTML tronqué: </html> manquant. Ajoute la fin avec edit_file."
            elif _ext in (".js", ".ts", ".jsx", ".tsx", ".css"):
                _bracket_detail = _locate_bracket_errors(content, _ext)
                if _bracket_detail:
                    _trunc_warn = f"\n{_bracket_detail}\nUtilise str_replace ou edit_lines pour corriger les lignes indiquées ci-dessus."
            # ── P8.CRLF_NORMALIZE : normaliser les fins de ligne pour fichiers texte ──
            _crlf_normalized = False
            try:
                from src.config.codeagent_flags import CRLF_NORMALIZE
                if CRLF_NORMALIZE and _ext in (
                    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".html",
                    ".json", ".md", ".yml", ".yaml", ".txt", ".sh",
                ):
                    content = content.replace("\r\n", "\n").replace("\r", "\n")
                    _crlf_normalized = True
            except Exception:
                pass
            if _crlf_normalized:
                # Écrire en binaire pour préserver les LF (write_text sur Windows traduit en CRLF)
                abs_path.write_bytes(content.encode("utf-8"))
            else:
                abs_path.write_text(content, encoding="utf-8")
            syntax_err = await self._check_python_syntax(file_path)
            web_err = await self._check_web_syntax(file_path)
            msg = f"✅ Fichier écrit: {file_path} ({len(content)} chars)"
            if syntax_err:
                msg += f"\n⚠️ Erreur de syntaxe détectée:\n{syntax_err}\nCorrige avant de continuer."
            if web_err:
                msg += f"\n⚠️ Erreur web détectée:\n{web_err}\nCorrige avant de continuer."
            msg += _trunc_warn
            return msg
        except Exception as exc:
            return f"❌ Impossible d'écrire {file_path}: {exc}"

    async def _list_files_action(self, dir_path: str) -> str:
        """Liste les fichiers d'un répertoire (profondeur 1)."""
        try:
            abs_path = self._resolve_path(dir_path)
            if not abs_path.exists():
                return f"❌ Répertoire introuvable: {dir_path}"
            entries: list[str] = []
            for item in sorted(abs_path.iterdir()):
                prefix = "d " if item.is_dir() else "f "
                entries.append(prefix + item.name)
            return (
                f"Contenu de {dir_path} ({len(entries)} entrées):\n"
                + "\n".join(entries[:100])
            )
        except Exception as exc:
            return f"❌ Erreur list_files: {exc}"

    def _snapshot_file(self, snapshots: dict, file_path: str) -> None:
        """Sauvegarde le contenu original d'un fichier (une seule fois par session)."""
        if file_path in snapshots:
            return
        try:
            abs_path = self._resolve_path(file_path)
            if abs_path.exists():
                snapshots[file_path] = abs_path.read_text(encoding="utf-8")
            else:
                snapshots[file_path] = None  # fichier n'existait pas
        except Exception as exc:
            logger.warning(f"[CodeAgent] Snapshot failed for {file_path}: {exc}")

    def _rollback_session(self, snapshots: dict) -> int:
        """Restaure tous les fichiers depuis les snapshots. Retourne le nombre restauré."""
        restored = 0
        for file_path, original in snapshots.items():
            try:
                abs_path = self._resolve_path(file_path)
                if original is None:
                    # Fichier n'existait pas → supprimer s'il a été créé
                    if abs_path.exists():
                        abs_path.unlink()
                        restored += 1
                else:
                    abs_path.write_text(original, encoding="utf-8")
                    restored += 1
            except Exception as exc:
                logger.error(f"[CodeAgent] Rollback FAILED for {file_path}: {exc}")
        return restored

    async def _execute_loop_action(self, action: dict, snapshots: dict | None = None) -> ActionResult:
        """Exécute une action unitaire de la boucle itérative."""
        from src.llm.output_normalizer import normalize_action_name, normalize_file_path
        act = normalize_action_name(action.get("action", ""))
        # Normaliser le chemin si présent
        if "path" in action:
            action["path"] = normalize_file_path(action["path"])
        # Snapshot automatique avant toute modification de fichier
        if snapshots is not None and act in ("edit_file", "edit_lines", "write_file", "apply_patch", "str_replace"):
            path = action.get("path", "")
            if path:
                self._snapshot_file(snapshots, path)
        try:
            # ── P3: Plan mode read-only gate ──
            # Si plan_mode_read_only est actif, on bloque toute action mutante
            # jusqu'à ce qu'un nouveau plan (read_only=false) ou un "done" l'annule.
            try:
                from src.config.codeagent_flags import PLAN_MODE
            except Exception:
                PLAN_MODE = False
            _MUTATING = {"edit_file", "edit_lines", "write_file", "apply_patch",
                         "apply_patches", "str_replace", "insert_at_anchor",
                         "run_command", "run_tests"}
            if PLAN_MODE and getattr(self, "_plan_mode_read_only", False) and act in _MUTATING:
                return ActionResult(
                    f"🔒 Plan mode read-only actif — action `{act}` bloquée. "
                    "Rappelle un plan avec `read_only=false` pour reprendre l'édition, "
                    "ou utilise `done` pour sortir.",
                )
            if act == "plan":
                steps = action.get("steps", [])
                # P3: gère l'entrée/sortie du read-only mode
                if PLAN_MODE:
                    _ro = bool(action.get("read_only", False))
                    self._plan_mode_read_only = _ro
                    _ro_note = " [mode lecture seule activé]" if _ro else ""
                else:
                    _ro_note = ""
                _todo_note = ""
                try:
                    _todo = getattr(self, "_codeagent_todo", None)
                    if _todo:
                        _rendered = _todo.set_plan(steps)
                        if _rendered:
                            _todo_note = "\n---\n" + _rendered
                except Exception:
                    pass
                return ActionResult(
                    f"Plan noté ({len(steps)} étapes).{_ro_note} Commence par l'étape 1.",
                    _todo_note,
                )
            elif act == "read_file":
                _raw_path = action.get("path", "")
                _norm_key = str(_raw_path or "").replace("\\", "/").strip()
                # Compteur peut ne pas exister sur les sub-classes hors CodeAgent
                _read_counts = getattr(self, "_read_count_per_file", None)
                if _read_counts is None:
                    _read_counts = {}
                    try:
                        self._read_count_per_file = _read_counts
                    except Exception:
                        pass
                _prev_reads = _read_counts.get(_norm_key, 0)
                _req_start = action.get("start_line")
                _req_end = action.get("end_line")
                # ── ANTI-RELECTURE intelligente :
                #   • 1ère lecture : normale (respecte start/end_line si fournis)
                #   • 2ème lecture (même fichier, n'importe quelle plage) : AUTO-PROMOTION
                #     → on lit le fichier ENTIER une fois, on met en cache, on renvoie tout
                #   • 3ème+ lecture : on renvoie le cache (toutes lignes déjà dispo) +
                #     consigne d'action concrète (le LLM a tout, qu'il édite ou grep)
                abs_path = self._resolve_path(_raw_path)
                # ── HARD STOP : 5+ lectures IDENTIQUES SANS modification du fichier entre-temps
                # • gros fichiers OK : nouvelles plages = clés différentes
                # • petits fichiers OK : après un edit, le mtime change → compteur reset auto
                # • seuil 3 : bloque dès la 4ème lecture identique sans modif
                _args_sig = f"{_norm_key}::{_req_start}::{_req_end}"
                _identical_args_state = getattr(self, "_read_identical_args_state", None)
                if _identical_args_state is None:
                    _identical_args_state = {}
                    try:
                        self._read_identical_args_state = _identical_args_state
                    except Exception:
                        pass
                try:
                    _current_mtime = abs_path.stat().st_mtime_ns if abs_path.exists() else 0
                except Exception:
                    _current_mtime = 0
                _prev_state = _identical_args_state.get(_args_sig)
                if _prev_state and _prev_state.get("mtime") == _current_mtime:
                    _identical_count = _prev_state.get("count", 0)
                else:
                    _identical_count = 0  # mtime changé (édition) ou première fois → reset
                if _identical_count >= 3:
                    logger.error(
                        "[CodeAgent] read_file({} L{}-{}) BLOQUÉ — {}× lectures identiques sans modif",
                        _raw_path, _req_start, _req_end, _identical_count,
                    )
                    return ActionResult(
                        f"🛑 REFUS: {_identical_count}× lectures identiques sans modif entre-temps",
                        (
                            f"Tu as lu `{_raw_path}` (lignes {_req_start}-{_req_end}) "
                            f"{_identical_count} fois et le fichier n'a pas été modifié depuis. "
                            "Le contenu est IDENTIQUE. ARRÊTE DE RELIRE.\n\n"
                            "👉 Actions possibles MAINTENANT (choisis-en UNE) :\n"
                            "  • `edit_lines` ou `str_replace` pour MODIFIER le fichier\n"
                            "  • `grep` pour chercher une section précise\n"
                            "  • Lire une AUTRE plage (start_line / end_line différents)\n"
                            "  • Passer à un AUTRE fichier\n"
                            "  • Donner ta réponse FINALE si la tâche est terminée\n\n"
                            "Note : si tu édites le fichier, le compteur se réinitialise automatiquement."
                        ),
                    )
                _identical_args_state[_args_sig] = {
                    "count": _identical_count + 1,
                    "mtime": _current_mtime,
                }
                if _prev_reads >= 2:
                    # ── FIX: si le fichier a été édité dans cette session,
                    # le cache est périmé → reset le compteur et relire depuis le disque.
                    # Sans ça, Guard 1 (CACHE-AS-SUCCESS) laisse passer mais Guard 2 ici
                    # sert le vieux cache car _read_count_per_file n'est jamais reset post-edit.
                    _was_edited = False
                    _edits_done = getattr(self, "_session_memory", {}).get("edits_done", [])
                    for _ed in _edits_done:
                        _ed_file = _ed.split(":", 1)[0].strip().replace("\\", "/")
                        if _ed_file == _norm_key or _ed_file.rsplit("/", 1)[-1] == _norm_key.rsplit("/", 1)[-1]:
                            try:
                                _ed_p = Path(_ed.split(":", 1)[0].strip())
                                if not _ed_p.is_absolute():
                                    _ed_p = (getattr(self, "_task_workspace_root", None) or Path.cwd()) / _ed_p
                                _ed_abs = str(_ed_p.resolve()).replace("\\", "/").lower()
                                _probe_abs = str(abs_path.resolve()).replace("\\", "/").lower() if abs_path.exists() else _norm_key.lower()
                                if _ed_abs == _probe_abs:
                                    _was_edited = True
                                    break
                            except Exception:
                                pass
                    if _was_edited:
                        # Reset compteur + invalider cache → la lecture fraîche passera
                        _read_counts[_norm_key] = 0
                        _sess_mem_ed = getattr(self, "_session_memory", None)
                        if isinstance(_sess_mem_ed, dict):
                            _files_read = _sess_mem_ed.get("files_read", {})
                            _stale = [k for k in _files_read if k.replace("\\", "/") == _norm_key]
                            for k in _stale:
                                del _files_read[k]
                        logger.info(
                            "[CodeAgent] Guard2 read_file({}) reset (fichier édité) → lecture fraîche",
                            _raw_path,
                        )
                        # _prev_reads est maintenant 0 → on sort du bloc ">=2" et on lit normalement
                    else:
                        _sess_mem = getattr(self, "_session_memory", None)
                        _cached_content = ""
                        if isinstance(_sess_mem, dict):
                            _cached_content = _sess_mem.get("files_read", {}).get(_norm_key, "")
                        if not _cached_content and abs_path.exists() and abs_path.is_file():
                            # cache vide (LRU éjecté ?) → recharger UNE fois
                            try:
                                _cached_content = abs_path.read_text(encoding="utf-8", errors="replace")
                                if hasattr(self, "_record_session_read"):
                                    try:
                                        self._record_session_read(_norm_key, _cached_content)
                                    except Exception:
                                        pass
                            except Exception:
                                _cached_content = ""
                        if _cached_content:
                            _read_counts[_norm_key] = _prev_reads + 1
                            _cached_lines = _cached_content.count("\n") + 1
                            logger.warning(
                                "[CodeAgent] read_file({}) → cache servi (lecture #{})",
                                _raw_path, _prev_reads + 1,
                            )
                            # Si le LLM a précisé une plage, on la lui sert depuis le cache
                            if _req_start is not None and _req_end is not None:
                                try:
                                    _all_lines = _cached_content.split("\n")
                                    _s = max(1, int(_req_start))
                                    _e = min(len(_all_lines), int(_req_end))
                                    _slice = _all_lines[_s - 1 : _e]
                                    _numbered = [f"{_s + i:4d} | {l}" for i, l in enumerate(_slice)]
                                    return ActionResult(
                                        f"📦 Cache: {_raw_path} L{_s}-{_e} (déjà lu {_prev_reads}× — sers le cache)",
                                        "\n".join(_numbered) + (
                                            "\n\n💡 Tu as déjà ce fichier en mémoire complète. "
                                            "Préfère edit_lines / str_replace / grep plutôt que de relire."
                                        ),
                                    )
                                except Exception:
                                    pass
                            # Pas de plage : on renvoie le fichier complet + consigne
                            return ActionResult(
                                f"📦 Cache complet: {_raw_path} ({_cached_lines} lignes, lecture #{_prev_reads + 1})",
                                (
                                    f"=== {_raw_path} (cache session) ===\n{_cached_content}\n\n"
                                    "💡 Ce fichier est en mémoire. Pour ne pas le relire :\n"
                                    "  • `edit_lines` (start_line/end_line + new_content) pour modifier\n"
                                    "  • `str_replace` (search/replace exact) pour modifier\n"
                                    "  • `grep` (pattern + path) si tu cherches une section précise"
                                ),
                            )
                if not abs_path.exists():
                    return ActionResult(f"❌ Fichier non trouvé: {_raw_path} (résolu: {abs_path})")
                # 2e lecture : AUTO-PROMOTION en lecture complète + cache
                _auto_full = (_prev_reads == 1)
                _read_counts[_norm_key] = _prev_reads + 1
                if abs_path.is_dir():
                    # Le LLM a demandé read_file sur un dossier → rediriger vers list_files
                    result = await self._list_files_action(_raw_path)
                    return ActionResult(result.split("\n")[0] if "\n" in result else result, result)
                try:
                    raw = abs_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    return ActionResult(f"❌ Impossible de lire {_raw_path}: {exc}")
                # Ajouter numéros de ligne pour faciliter edit_lines
                lines = raw.split("\n")
                _start_line = None if _auto_full else action.get("start_line")
                _end_line = None if _auto_full else action.get("end_line")
                if _start_line is not None and _end_line is not None:
                    _s = max(1, int(_start_line))
                    _e = min(len(lines), int(_end_line))
                    lines_slice = lines[_s - 1 : _e]
                    numbered = [f"{_s + i:4d} | {l}" for i, l in enumerate(lines_slice)]
                    detail = "\n".join(numbered)
                    return ActionResult(f"✅ Lu: {_raw_path} L{_s}-{_e} ({len(lines_slice)} lignes)", detail)
                numbered = [f"{i+1:4d} | {l}" for i, l in enumerate(lines)]
                detail = "\n".join(numbered)
                # Stocker dans la session memory pour les prochaines lectures
                if hasattr(self, "_record_session_read"):
                    try:
                        self._record_session_read(_norm_key, raw)
                    except Exception:
                        pass
                if _auto_full:
                    return ActionResult(
                        f"✅ Lu COMPLET: {_raw_path} ({len(lines)} lignes) — auto-promotion 2e lecture, cache activé",
                        detail + (
                            "\n\n💡 Le fichier est maintenant en mémoire complète. "
                            "Toute prochaine read_file servira le cache : préfère edit_lines / str_replace / grep."
                        ),
                    )
                # P5 — file_section_threshold : couper les gros fichiers pour les modèles instables
                _fst = getattr(getattr(self, "_attempt_profile", None), "file_section_threshold", 0)
                if _fst > 0 and len(raw) > _fst and not action.get("start_line") and not _auto_full:
                    _section_lines = raw[:_fst].split("\n")
                    _numbered_sec = [f"{i+1:4d} | {l}" for i, l in enumerate(_section_lines)]
                    _remaining = len(lines) - len(_section_lines)
                    return ActionResult(
                        f"✅ Lu: {_raw_path} section 1 ({len(_section_lines)} lignes) — {_remaining} lignes restantes",
                        "\n".join(_numbered_sec) + (
                            f"\n\n[SECTIONNEMENT actif: fichier {len(raw)//1000}k chars > seuil {_fst//1000}k. "
                            f"Utilise read_file(path, start_line={len(_section_lines)+1}, end_line=...) pour la suite.]"
                        ),
                    )
                if len(lines) > 300 and not action.get("start_line"):
                    return ActionResult(
                        f"✅ Lu: {_raw_path} ({len(lines)} lignes — GROS FICHIER, utilise "
                        f"read_file(path, start_line=N, end_line=M) pour cibler des plages)",
                        detail,
                    )
                return ActionResult(f"✅ Lu: {_raw_path} ({len(lines)} lignes)", detail)
            elif act == "write_file":
                result = await self._write_file_action(
                    action.get("path", ""), action.get("content", "")
                )
                return ActionResult(result)
            elif act == "list_files":
                result = await self._list_files_action(action.get("path", "."))
                return ActionResult(result.split("\n")[0] if "\n" in result else result, result)
            elif act == "edit_file":
                _raw_epath = action.get("path", "")
                _abs_epath = self._resolve_path(_raw_epath)
                if not _abs_epath.exists():
                    result = f"❌ Fichier non trouvé: {_raw_epath}"
                else:
                    _old = action.get("search", "")
                    _new = action.get("replace", "")
                    try:
                        _content = _abs_epath.read_text(encoding="utf-8")
                        # Sauvegarder le bracket balance AVANT l'edit
                        _ext_edit = _abs_epath.suffix.lower()
                        _pre_brackets = _count_brackets_clean(_content) if _ext_edit in _WEB_BRACKET_EXTS else None
                        if _old not in _content:
                            # Fuzzy matching multi-niveaux :
                            # 1) Stripped exact match sur tout le texte
                            # 2) Ligne par ligne (single-line search)
                            # 3) Multi-ligne stripped (search multi-lignes normalisé)
                            _old_stripped = _old.strip()
                            _content_stripped = "\n".join(l.rstrip() for l in _content.split("\n"))
                            _old_norm = "\n".join(l.rstrip() for l in _old_stripped.split("\n"))
                            _found = False

                            # Pass 1: stripped content match
                            if _old_norm and _old_norm in _content_stripped:
                                # Retrouver la position dans le contenu original
                                _norm_idx = _content_stripped.index(_old_norm)
                                _content = _content[:_norm_idx] + _new + _content[_norm_idx + len(_old_norm):]
                                _abs_epath.write_text(_content, encoding="utf-8")
                                result = f"✅ Modifié {_raw_epath} (fuzzy stripped match)"
                                _found = True

                            # Pass 2: single-line match
                            if not _found and "\n" not in _old_stripped:
                                _lines = _content.split("\n")
                                for _line_idx, _line in enumerate(_lines):
                                    if _old_stripped and _old_stripped in _line.strip():
                                        _lines[_line_idx] = _lines[_line_idx].replace(
                                            _old_stripped, _new.strip(), 1
                                        )
                                        _content = "\n".join(_lines)
                                        _abs_epath.write_text(_content, encoding="utf-8")
                                        result = f"✅ Modifié {_raw_epath} L{_line_idx + 1} (fuzzy line match)"
                                        _found = True
                                        break

                            if not _found:
                                # Pass 3: Unicode normalization match (guillemets, tirets, espaces)
                                _old_punct = _normalize_punctuation(_old_norm)
                                _content_punct = _normalize_punctuation(_content_stripped)
                                if _old_punct and _old_punct in _content_punct:
                                    _punct_idx = _content_punct.index(_old_punct)
                                    _content = _content[:_punct_idx] + _new + _content[_punct_idx + len(_old_punct):]
                                    _abs_epath.write_text(_content, encoding="utf-8")
                                    result = f"✅ Modifié {_raw_epath} (fuzzy Unicode normalization)"
                                    _found = True

                            if not _found:
                                result = f"❌ Texte non trouvé dans {_raw_epath}. Relis le fichier pour obtenir le contenu exact."
                        else:
                            _content = _content.replace(_old, _new, 1)
                            _abs_epath.write_text(_content, encoding="utf-8")
                            result = f"✅ Modifié {_raw_epath}"
                        # Bracket corruption guard: vérifier que l'edit n'a pas
                        # introduit un déséquilibre de brackets
                        if _pre_brackets is not None and not result.startswith("❌"):
                            _post_content = _abs_epath.read_text(encoding="utf-8")
                            _post_brackets = _count_brackets_clean(_post_content)
                            _brace_delta = abs(_post_brackets[0]) - abs(_pre_brackets[0])
                            _paren_delta = abs(_post_brackets[1]) - abs(_pre_brackets[1])
                            if _brace_delta > 1 or _paren_delta > 1:
                                result += (
                                    f"\n⚠️ CORRUPTION PROBABLE: l'edit a déséquilibré les brackets"
                                    f" (accolades: {'+' if _brace_delta > 0 else ''}{_brace_delta},"
                                    f" parenthèses: {'+' if _paren_delta > 0 else ''}{_paren_delta})."
                                    f"\nRelis le fichier pour vérifier et corriger."
                                )
                    except Exception as exc:
                        result = f"❌ Erreur edit_file: {exc}"
                summary = result.split("\n")[0] if result else "edit_file"
                detail = result
                # Auto-verif syntaxe Python (equivalent tsc --noEmit)
                if not result.startswith("❌"):
                    syntax_err = await self._check_python_syntax(action.get("path", ""))
                    if syntax_err:
                        detail += (
                            f"\n\n⚠️ Erreur de syntaxe Python détectée:\n{syntax_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                        summary += " ⚠️ syntaxe"
                    # P5: type checking
                    type_err = await self._check_python_types(action.get("path", ""))
                    if type_err:
                        detail += f"\n\n⚠️ Type errors:\n{type_err}"
                    # Validation web (JS/HTML/CSS)
                    web_err = await self._check_web_syntax(action.get("path", ""))
                    if web_err:
                        detail += (
                            f"\n\n⚠️ Erreur web détectée:\n{web_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                        summary += " ⚠️ web"
                    # ── P6 Plan Suprême : auto-format (ruff) si Python OK ──
                    if not syntax_err and not web_err:
                        try:
                            from src.utils.auto_format import auto_format_file
                            fmt_msg = await auto_format_file(
                                action.get("path", ""), self._task_workspace_root
                            )
                            if fmt_msg:
                                detail += f"\n{fmt_msg}"
                        except Exception:
                            pass
                return ActionResult(summary, detail)
            elif act == "str_replace":
                # Outil principal de modification : old_str/new_str
                # Stratégie 4-pass :
                #   1. Délégation à edit_file (3-pass fuzzy interne)
                #   2. Si échec → seek_sequence 4-pass depuis apply_patch (plus robuste)
                _sr_path = action.get("path", "")
                _old_str = action.get("old_str", "")
                _new_str = action.get("new_str", "")
                if not _sr_path or not _old_str:
                    return ActionResult("❌ str_replace: path et old_str sont requis")
                # Tentative via edit_file (3-pass fuzzy)
                _sr_result = await self._execute_loop_action({
                    "action": "edit_file",
                    "path": _sr_path,
                    "search": _old_str,
                    "replace": _new_str,
                }, snapshots=snapshots)
                # Si edit_file échoue → escalader seek_sequence 4-pass
                if isinstance(_sr_result, ActionResult) and _sr_result.summary.startswith("❌"):
                    try:
                        from src.tools.apply_patch import seek_sequence as _seek_seq
                        _sr_full = self._resolve_path(_sr_path)
                        _sr_content = _sr_full.read_text(encoding="utf-8")
                        _sr_lines = _sr_content.split("\n")
                        _old_lines = _old_str.split("\n")
                        _new_lines_r = _new_str.split("\n")
                        _idx = _seek_seq(_sr_lines, _old_lines)
                        _new_content = "\n".join(
                            _sr_lines[:_idx] + _new_lines_r + _sr_lines[_idx + len(_old_lines):]
                        )
                        _sr_full.write_text(_new_content, encoding="utf-8")
                        _sr_msg = f"✅ str_replace (seek_sequence 4-pass) réussi dans {_sr_path}"
                        _sr_syn = await self._check_python_syntax(_sr_path)
                        if _sr_syn:
                            _sr_msg += f"\n\n⚠️ Syntaxe Python:\n{_sr_syn}\nCorrige avant de continuer."
                        _sr_web = await self._check_web_syntax(_sr_path)
                        if _sr_web:
                            _sr_msg += f"\n\n⚠️ Erreur web:\n{_sr_web}\nCorrige avant de continuer."
                        return ActionResult(_sr_msg.split("\n")[0], _sr_msg)
                    except ValueError:
                        # ── P1 Plan Suprême : fuzzy_replace ultime fallback ──
                        try:
                            from src.tools.fuzzy_replace import fuzzy_replace as _fuzzy_repl
                            _sr_full2 = self._resolve_path(_sr_path)
                            _sr_content2 = _sr_full2.read_text(encoding="utf-8")
                            _fm = _fuzzy_repl(_sr_content2, _old_str, _new_str)
                            if _fm is not None:
                                _sr_full2.write_text(_fm.new_content, encoding="utf-8")
                                _msg = f"✅ str_replace (fuzzy_replace {_fm.method}) réussi dans {_sr_path}"
                                _sr_syn2 = await self._check_python_syntax(_sr_path)
                                if _sr_syn2:
                                    _msg += f"\n\n⚠️ Syntaxe Python:\n{_sr_syn2}\nCorrige avant de continuer."
                                _sr_web2 = await self._check_web_syntax(_sr_path)
                                if _sr_web2:
                                    _msg += f"\n\n⚠️ Erreur web:\n{_sr_web2}\nCorrige avant de continuer."
                                return ActionResult(_msg.split("\n")[0], _msg)
                        except Exception:
                            pass
                        return ActionResult(
                            f"❌ str_replace: old_str non trouvé dans {_sr_path} (même avec seek_sequence 4-pass + fuzzy_replace 8-pass).\n"
                            "Relis le fichier avec read_file et copie les lignes EXACTES à modifier, "
                            "puis utilise edit_lines avec les numéros de ligne."
                        )
                    except Exception as _sr_exc:
                        return ActionResult(f"❌ str_replace seek_sequence erreur: {_sr_exc}")
                return _sr_result
            elif act == "undo_edit":
                _undo_path = action.get("path", "")
                _undo_full = self._resolve_path(_undo_path)
                if snapshots is not None and _undo_path in snapshots and snapshots[_undo_path] is not None:
                    _undo_full.write_text(snapshots[_undo_path], encoding="utf-8")
                    return ActionResult(f"✅ Undo: {_undo_path} restauré à la version précédente")
                return ActionResult(f"❌ Aucun snapshot disponible pour {_undo_path} — impossible d'annuler")
            elif act == "think":
                _thought = action.get("thought", "")
                logger.info("[CodeAgent] think: {}", _thought[:200])
                return ActionResult("💭 Pensée enregistrée. Continue avec ton action suivante.")
            elif act == "edit_lines":
                from src.tools.apply_patch import edit_by_lines
                _raw_el_path = action.get("path", "")
                _abs_el_path = str(self._resolve_path(_raw_el_path))
                start = int(action.get("start_line", 0))
                end = int(action.get("end_line", 0))
                content = action.get("content", "")
                result = edit_by_lines(_abs_el_path, start, end, content)
                summary = result.split("\n")[0] if result else "edit_lines"
                detail = result
                if not result.startswith("❌") and _raw_el_path.endswith(".py"):
                    syntax_err = await self._check_python_syntax(_raw_el_path)
                    if syntax_err:
                        detail += (
                            f"\n\n⚠️ Erreur de syntaxe Python détectée:\n{syntax_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                        summary += " ⚠️ syntaxe"
                    # P5: type checking
                    type_err = await self._check_python_types(_raw_el_path)
                    if type_err:
                        detail += f"\n\n⚠️ Type errors:\n{type_err}"
                # Validation web (JS/HTML/CSS) — indépendant de l'extension Python
                if not result.startswith("❌"):
                    web_err = await self._check_web_syntax(_raw_el_path)
                    if web_err:
                        detail += (
                            f"\n\n⚠️ Erreur web détectée:\n{web_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                        summary += " ⚠️ web"
                    # ── P6 Plan Suprême : auto-format post-edit_lines ──
                    if not web_err and _raw_el_path.endswith(".py"):
                        try:
                            from src.utils.auto_format import auto_format_file
                            fmt_msg = await auto_format_file(
                                _raw_el_path, self._task_workspace_root
                            )
                            if fmt_msg:
                                detail += f"\n{fmt_msg}"
                        except Exception:
                            pass
                return ActionResult(summary, detail)
            elif act == "insert_at_anchor":
                # Action 1-shot : insère du contenu autour d'une ancre textuelle.
                # Remplace grep+read_file+str_replace pour des insertions HTML/Python/JS/Java.
                from src.reasoning.handlers.files import insert_at_anchor_core
                _ia_raw = action.get("path", "")
                _ia_anchor = action.get("anchor", "")
                _ia_content = action.get("content", "")
                _ia_position = action.get("position", "before")
                _ia_occurrence = action.get("occurrence", "first")
                if not _ia_raw or not _ia_anchor:
                    return ActionResult("❌ insert_at_anchor: path et anchor sont requis")
                _ia_full = self._resolve_path(_ia_raw)
                if not _ia_full.exists():
                    return ActionResult(f"❌ Fichier introuvable: {_ia_raw}")
                try:
                    _ia_before = _ia_full.read_text(encoding="utf-8")
                    _ia_after = insert_at_anchor_core(
                        file_text=_ia_before,
                        anchor=_ia_anchor,
                        content=_ia_content,
                        position=_ia_position,
                        occurrence=_ia_occurrence,
                    )
                except ValueError as _ia_ve:
                    return ActionResult(
                        f"❌ insert_at_anchor: {_ia_ve}. Relis le fichier et vérifie l'ancre exacte."
                    )
                except Exception as _ia_exc:
                    return ActionResult(f"❌ insert_at_anchor erreur: {_ia_exc}")
                if _ia_after == _ia_before:
                    return ActionResult(f"⚠️ insert_at_anchor: aucune modification (contenu identique)")
                _ia_full.write_text(_ia_after, encoding="utf-8")
                result = f"✅ insert_at_anchor({_ia_position}) OK dans {_ia_raw} (ancre: {_ia_anchor[:40]!r})"
                detail = result
                # Auto-vérif syntaxe (py + web)
                syntax_err = await self._check_python_syntax(_ia_raw)
                if syntax_err:
                    detail += f"\n\n⚠️ Erreur de syntaxe Python:\n{syntax_err}\nCorrige avant de continuer."
                    result += " ⚠️ syntaxe"
                web_err = await self._check_web_syntax(_ia_raw)
                if web_err:
                    detail += f"\n\n⚠️ Erreur web:\n{web_err}\nCorrige avant de continuer."
                    result += " ⚠️ web"
                return ActionResult(result.split("\n")[0], detail)
            elif act == "apply_patch":
                from src.tools.apply_patch import apply_patch as _do_patch
                patch_text = action.get("patch", "")
                patch_result = await _do_patch(patch_text)
                result = patch_result.summary()
                detail = result
                # Auto-verif syntaxe sur les fichiers Python modifiés/créés
                py_files = [
                    f for f in (patch_result.modified + patch_result.added)
                    if f.endswith(".py")
                ]
                for f in py_files[:3]:
                    syntax_err = await self._check_python_syntax(f)
                    if syntax_err:
                        detail += (
                            f"\n\n⚠️ Syntaxe [{f}]:\n{syntax_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                # Auto-verif web sur les fichiers modifiés/créés
                web_files = [
                    f for f in (patch_result.modified + patch_result.added)
                    if any(f.endswith(e) for e in (".js", ".ts", ".html", ".htm", ".css"))
                ]
                for f in web_files[:3]:
                    web_err = await self._check_web_syntax(f)
                    if web_err:
                        detail += (
                            f"\n\n⚠️ Web [{f}]:\n{web_err}"
                            f"\nCorrige cette erreur avant de continuer."
                        )
                return ActionResult(result.split("\n")[0] if result else "apply_patch", detail)
            elif act == "run_command":
                _cmd = action.get("command", "").strip()

                # ── P11.DESTRUCTIVE_CONFIRM : bloquer commandes destructives ──
                try:
                    from src.config.codeagent_flags import DESTRUCTIVE_CONFIRM
                    if DESTRUCTIVE_CONFIRM and _cmd:
                        import re as _re_destr
                        _destructive_patterns = (
                            r"\brm\s+-rf?\b",
                            r"\brmdir\s+/[sq]",
                            r"\bRemove-Item\b.*-Recurse.*-Force",
                            r"\bgit\s+push\s+.*--force\b",
                            r"\bgit\s+reset\s+--hard\b",
                            r"\bgit\s+clean\s+-[a-zA-Z]*f",
                            r"\bdrop\s+table\b",
                            r"\bdrop\s+database\b",
                            r"\bdel\s+/[sq]",
                            r"\bformat\s+[a-z]:",
                            r":>\s*/dev/[a-z]+",
                            r"\bdd\s+.*of=/dev/",
                            r"\bmkfs\b",
                        )
                        for _pat in _destructive_patterns:
                            if _re_destr.search(_pat, _cmd, _re_destr.IGNORECASE):
                                return ActionResult(
                                    f"🔒 Commande destructive bloquée (DESTRUCTIVE_CONFIRM actif) : `{_cmd[:120]}`.\n"
                                    f"Si vraiment voulue, l'utilisateur doit la lancer manuellement ou désactiver "
                                    f"LUMENA_DESTRUCTIVE_CONFIRM=false."
                                )
                except Exception:
                    pass

                # ── Smart redirect: commandes de lecture → read_file (plus rapide) ──
                import re as _re_cmd

                # ── Smart redirect: findstr/Select-String/grep → grep action (UTF-8 safe) ──
                # findstr casse sur Windows avec du texte UTF-8 (accents français)
                # Select-String est mieux mais plus lent → rediriger vers grep Python
                _grep_pattern = None
                _grep_path = None

                # findstr /I "pattern" file
                _m_findstr = _re_cmd.match(
                    r'^findstr\s+(?:/[a-zA-Z]+\s+)*["\']?(.+?)["\']?\s+["\']?([^"\']+?)["\']?\s*$',
                    _cmd, _re_cmd.IGNORECASE,
                )
                if _m_findstr:
                    _grep_pattern = _m_findstr.group(1).strip()
                    _grep_path = _m_findstr.group(2).strip()

                # Select-String -Pattern "pattern" -Path file
                if not _grep_pattern:
                    _m_selstr = _re_cmd.match(
                        r'^(?:powershell\s+)?(?:Select-String|sls)\s+(?:-Pattern\s+)?["\']?(.+?)["\']?\s+'
                        r'(?:-Path\s+)?["\']?([^"\']+?)["\']?\s*$',
                        _cmd, _re_cmd.IGNORECASE,
                    )
                    if _m_selstr:
                        _grep_pattern = _m_selstr.group(1).strip()
                        _grep_path = _m_selstr.group(2).strip()

                # Get-Content file | Select-String pattern
                if not _grep_pattern:
                    _m_pipe_ss = _re_cmd.match(
                        r'^(?:powershell\s+)?(?:Get-Content|gc|type)\s+["\']?([^|"\']+?)["\']?\s*'
                        r'\|\s*(?:Select-String|sls)\s+(?:-Pattern\s+)?["\']?(.+?)["\']?\s*$',
                        _cmd, _re_cmd.IGNORECASE,
                    )
                    if _m_pipe_ss:
                        _grep_path = _m_pipe_ss.group(1).strip()
                        _grep_pattern = _m_pipe_ss.group(2).strip()

                # grep -i "pattern" file (Linux style via run_command)
                if not _grep_pattern:
                    _m_grep_linux = _re_cmd.match(
                        r'^grep\s+(?:-[a-zA-Z]+\s+)*["\']?(.+?)["\']?\s+["\']?([^"\']+?)["\']?\s*$',
                        _cmd, _re_cmd.IGNORECASE,
                    )
                    if _m_grep_linux:
                        _grep_pattern = _m_grep_linux.group(1).strip()
                        _grep_path = _m_grep_linux.group(2).strip()

                if _grep_pattern and _grep_path:
                    self._redirect_count = getattr(self, '_redirect_count', 0) + 1
                    logger.info(
                        "[CodeAgent] Smart redirect #{} search cmd → grep('{}' in '{}')",
                        self._redirect_count, _grep_pattern[:40], _grep_path,
                    )
                    return await self._execute_loop_action(
                        {"action": "grep", "pattern": _grep_pattern, "path": _grep_path},
                        snapshots=snapshots,
                    )

                # Smart redirect: Get-Content file | Select-Object -Index X..Y → read_file(plage)
                _m_gc_select = _re_cmd.match(
                    r'^(?:powershell\s+)?(?:Get-Content|gc)\s+["\']?([^|"\']+?)["\']?\s*'
                    r'\|\s*Select-Object\s+-Index\s+(\d+)\.\.(\d+)',
                    _cmd, _re_cmd.IGNORECASE
                )
                if _m_gc_select:
                    _path = _m_gc_select.group(1).strip()
                    _start = int(_m_gc_select.group(2)) + 1  # Select-Object 0-based → read_file 1-based
                    _end = int(_m_gc_select.group(3)) + 1
                    self._redirect_count = getattr(self, '_redirect_count', 0) + 1
                    logger.info("[CodeAgent] Smart redirect #{} GC|Select-Object → read_file('{}', {}-{})", self._redirect_count, _path, _start, _end)
                    return await self._execute_loop_action(
                        {"action": "read_file", "path": _path, "start_line": _start, "end_line": _end},
                        snapshots=snapshots
                    )

                # Smart redirect: Get-Content file | Select-Object -First N → read_file(1..N)
                _m_gc_first = _re_cmd.match(
                    r'^(?:powershell\s+)?(?:Get-Content|gc)\s+["\']?([^|"\']+?)["\']?\s*'
                    r'\|\s*Select-Object\s+-First\s+(\d+)',
                    _cmd, _re_cmd.IGNORECASE
                )
                if _m_gc_first:
                    _path = _m_gc_first.group(1).strip()
                    _count = int(_m_gc_first.group(2))
                    self._redirect_count = getattr(self, '_redirect_count', 0) + 1
                    logger.info("[CodeAgent] Smart redirect #{} GC|Select-First → read_file('{}', 1-{})", self._redirect_count, _path, _count)
                    return await self._execute_loop_action(
                        {"action": "read_file", "path": _path, "start_line": 1, "end_line": _count},
                        snapshots=snapshots
                    )

                # Redirect simple: lecture sans pipe/redirection (ancre $ = fin de chaîne)
                _read_patterns = [
                    (r'^(?:cat|less|more)\s+([^|><&;]+)$', 1),
                    (r'^type\s+([^|><&;]+)$', 1),
                    (r'^(?:powershell\s+)?(?:Get-Content|gc)\s+["\']?([^|><&;"\']+)["\']?\s*$', 1),
                ]
                for _rp, _grp in _read_patterns:
                    _m = _re_cmd.match(_rp, _cmd, _re_cmd.IGNORECASE)
                    if _m:
                        _redirect_path = _m.group(1).strip().strip("'\"")
                        self._redirect_count = getattr(self, '_redirect_count', 0) + 1
                        logger.info("[CodeAgent] Redirect #{} run_command('{}') → read_file('{}')", self._redirect_count, _cmd[:60], _redirect_path)
                        return await self._execute_loop_action(
                            {"action": "read_file", "path": _redirect_path}, snapshots=snapshots
                        )

                # ── Auto-conversion: commandes Linux → Windows ──
                import sys as _sys_rc
                if _sys_rc.platform == "win32":
                    _linux_map = {
                        r'^ls\b': 'dir',
                        r'^cp\s': 'copy ',
                        r'^mv\s': 'move ',
                        r'^rm\s+-rf?\s+': 'rmdir /s /q ',
                        r'^rm\s+': 'del ',
                        r'^touch\s+': 'type nul > ',
                        r'^chmod\s+\S+\s+': '',  # pas d'équivalent, on skip
                        r'^mkdir\s+-p\s+': 'mkdir ',
                    }
                    for _pat, _repl in _linux_map.items():
                        if _re_cmd.match(_pat, _cmd, _re_cmd.IGNORECASE):
                            _old_cmd = _cmd
                            _cmd = _re_cmd.sub(_pat, _repl, _cmd, count=1, flags=_re_cmd.IGNORECASE).strip()
                            if _cmd:
                                logger.info("[CodeAgent] Linux→Windows: '{}' → '{}'", _old_cmd[:50], _cmd[:50])
                            else:
                                # chmod sans équivalent → skip silencieux
                                return ActionResult("⏭️ Commande ignorée (pas d'équivalent Windows).")
                            break

                # ── Auto-fix: retirer le & bash (backgrounding) ──
                if _cmd.rstrip().endswith("&") and not _cmd.rstrip().endswith("&&"):
                    _cmd = _cmd.rstrip().rstrip("&").rstrip()
                    logger.info("[CodeAgent] Retiré '&' (bash background): {}", _cmd[:60])

                # ── Guard: bloquer les serveurs HTTP (bloquent la boucle indéfiniment) ──
                _server_patterns = [
                    r'http\.server', r'live-server', r'serve\b', r'SimpleHTTPServer',
                    r'npx\s+serve', r'php\s+-S', r'python.*\s+-m\s+http',
                    r'flask\s+run', r'uvicorn\b', r'gunicorn\b', r'node\s+.*server',
                ]
                if any(_re_cmd.search(sp, _cmd, _re_cmd.IGNORECASE) for sp in _server_patterns):
                    logger.warning("[CodeAgent] Serveur HTTP bloqué (bloquerait la boucle): {}", _cmd[:80])
                    return ActionResult(
                        "⏭️ Lancement de serveur HTTP interdit dans CodeAgent (commande bloquante). "
                        "Les fichiers du projet sont déjà accessibles via le workspace Lumena. "
                        "Ne lance pas de serveur ici: ReAct/Playwright fera la vérification navigateur. "
                        "Si les fichiers attendus existent, appelle `done` avec un résumé clair."
                    )

                # Préfixer cd vers le workspace root pour que les commandes
                # s'exécutent dans le bon répertoire projet
                if self._task_workspace_root and _cmd:
                    import sys as _sys_rc
                    if _sys_rc.platform == "win32":
                        _cmd = f'cd /d "{self._task_workspace_root}" && {_cmd}'
                    else:
                        _cmd = f'cd "{self._task_workspace_root}" && {_cmd}'
                result = await self._call_tool(
                    "run_command", {"command": _cmd}
                )

                # ── P0 : Auto-correction bibliothèques incompatibles ──
                import re as _re_p0
                import sys as _sys_p0

                _LIB_FIXES: dict[str, tuple[str, str]] = {
                    "gobject": (
                        r"(?:from|import)\s+weasyprint\b[^\n]*",
                        "# weasyprint → reportlab (auto-fix)\nfrom reportlab.lib.pagesizes import A4\nfrom reportlab.platypus import SimpleDocTemplate",
                    ),
                    "No module named 'cv2'": (r"import cv2\b", "from PIL import Image as cv2_compat"),
                    "No module named 'gtk'": (r"import gi\b[^\n]*|from gi\b[^\n]*", "import tkinter as gtk_compat"),
                }

                _stderr = result or ""  # result est une str ici
                for _trigger, (_pat, _repl) in _LIB_FIXES.items():
                    if _trigger in _stderr:
                        _ws = self._task_workspace_root or Path(".")
                        for _f in _ws.rglob("*.py"):
                            try:
                                _src = _f.read_text(encoding="utf-8")
                                _new = _re_p0.sub(_pat, _repl, _src)
                                if _new != _src:
                                    _f.write_text(_new, encoding="utf-8")
                                    logger.info("[CodeAgent] Auto-fix {} dans {}", _trigger, _f.name)
                            except Exception:
                                pass
                        break

                _mod = _re_p0.search(r"No module named '([^']+)'", _stderr)
                if _mod and not any(_mod.group(1) in k for k in _LIB_FIXES):
                    import subprocess as _sp
                    _pkg = _mod.group(1).split(".")[0]
                    _sp.run([_sys_p0.executable, "-m", "pip", "install", _pkg], capture_output=True, timeout=30)
                # ── Fin P0 ──

                summary = result.split("\n")[0][:120] if result else "run_command"
                return ActionResult(summary, result or "")
            elif act == "run_tests":
                _test_path = action.get("test_path", "")
                # Quand workspace actif, ne jamais lancer la suite Lumena
                _ws = getattr(self, "_task_workspace_root", None)
                # P3 : auto-détection du test runner si test_path vide et workspace connu
                if not _test_path and _ws:
                    try:
                        from src.tools.test_runner_detector import detect_test_runner
                        _tr = detect_test_runner(Path(str(_ws)), list(getattr(self, "_edited_files", [])))
                        if _tr.runner != "unknown" and _tr.command:
                            # Exécuter directement via run_command
                            import asyncio as _asyncio_rt
                            _proc = await _asyncio_rt.create_subprocess_shell(
                                _tr.command,
                                cwd=str(_ws),
                                stdout=_asyncio_rt.subprocess.PIPE,
                                stderr=_asyncio_rt.subprocess.STDOUT,
                            )
                            _out, _ = await _asyncio_rt.wait_for(_proc.communicate(), timeout=120)
                            _out_str = (_out or b"").decode("utf-8", errors="replace")
                            _last = _out_str.strip().split("\n")[-1] if _out_str.strip() else "no output"
                            _icon = "✅" if _proc.returncode == 0 else "❌"
                            return ActionResult(f"{_icon} {_last}", _out_str[-3000:])
                    except Exception:
                        pass
                    return ActionResult("⏭️ Aucun test runner détecté pour ce projet. Spécifie un test_path.")
                if not _test_path:
                    return ActionResult("❌ test_path requis pour run_tests.")
                # Résoudre le chemin dans le workspace
                if _ws:
                    _test_path = str(self._resolve_path(_test_path))
                result = await self._call_tool(
                    "run_tests", {"test_path": _test_path}
                )
                # Extraire le summary line (dernière ligne de pytest)
                lines = (result or "").strip().split("\n")
                summary_line = lines[-1] if lines else "run_tests"
                return ActionResult(summary_line, result or "")
            elif act == "grep":
                _grep_path = action.get("path", ".")
                # Résoudre le path depuis le workspace actif
                _grep_abs = str(self._resolve_path(_grep_path))
                result = await self._call_tool(
                    "grep_search",
                    {
                        "pattern": action.get("pattern", ""),
                        "path": _grep_abs,
                        "ignore_case": True,
                        "is_regex": False,
                        "max_results": 80,
                    },
                )
                n_matches = result.count("\n") if result else 0
                return ActionResult(f"grep: {n_matches} résultats", result or "")
            elif act == "lint":
                lint_result = await self._check_python_syntax(action.get("path", ""))
                if lint_result:
                    return ActionResult(f"⚠️ Erreurs lint détectées", lint_result)
                return ActionResult("✅ Aucune erreur détectée.")
            elif act in ("read_files_batch", "read_batch", "batch_read"):
                # Levier 4: lecture parallèle de N fichiers via handler V2.
                _paths = action.get("paths") or action.get("path") or []
                if isinstance(_paths, str):
                    _paths = [p.strip() for p in _paths.split(",") if p.strip()]
                if not _paths or not isinstance(_paths, list):
                    return ActionResult("❌ read_files_batch: 'paths' doit être une liste non vide")
                # Résoudre tous les chemins relativement au workspace actif.
                _resolved_paths = [str(self._resolve_path(str(p))) for p in _paths]
                _args: dict = {"paths": _resolved_paths}
                if action.get("start_line") is not None:
                    _args["start_line"] = int(action["start_line"])
                if action.get("end_line") is not None:
                    _args["end_line"] = int(action["end_line"])
                if action.get("max_chars_per_file") is not None:
                    _args["max_chars_per_file"] = int(action["max_chars_per_file"])
                try:
                    result = await self._call_tool("read_files_batch", _args)
                except Exception as exc:
                    return ActionResult(f"❌ read_files_batch: {exc}")
                _first_line = result.split("\n", 1)[0] if result else "read_files_batch"
                return ActionResult(_first_line, result or "")
            elif act in ("apply_patches", "batch_patch", "multi_patch"):
                # Levier 4: edits multi-fichiers ATOMIQUES via handler V2.
                _patches = action.get("patches") or []
                if not isinstance(_patches, list) or not _patches:
                    return ActionResult(
                        "❌ apply_patches: 'patches' doit être une liste non vide "
                        "[{file, old, new}, ...]"
                    )
                # Snapshot automatique avant patches (rollback manuel possible via undo_edit).
                if snapshots is not None:
                    for _p in _patches:
                        if isinstance(_p, dict):
                            _pf = _p.get("file") or _p.get("path") or ""
                            if _pf:
                                self._snapshot_file(snapshots, _pf)
                # Résoudre tous les chemins relatifs.
                _normalized: list = []
                for _p in _patches:
                    if not isinstance(_p, dict):
                        continue
                    _pf = _p.get("file") or _p.get("path") or ""
                    if not _pf:
                        continue
                    _normalized.append({
                        "file": str(self._resolve_path(str(_pf))),
                        "old": _p.get("old") or _p.get("old_str") or "",
                        "new": _p.get("new") or _p.get("new_str") or "",
                    })
                if not _normalized:
                    return ActionResult("❌ apply_patches: aucun patch valide (chaque patch requiert 'file' et 'old')")
                try:
                    result = await self._call_tool("apply_patches", {"patches": _normalized})
                except Exception as exc:
                    return ActionResult(f"❌ apply_patches: {exc}")
                # Auto-lint sur fichiers Python modifiés (max 3).
                _py_files = [p["file"] for p in _normalized if p["file"].endswith(".py")][:3]
                _extra = ""
                if not result.startswith("❌"):
                    for _pyf in _py_files:
                        _syn = await self._check_python_syntax(_pyf)
                        if _syn:
                            _extra += f"\n⚠️ Syntaxe [{_pyf}]:\n{_syn}\n"
                _first = result.split("\n", 1)[0] if result else "apply_patches"
                _detail = (result or "") + (_extra if _extra else "")
                return ActionResult(_first, _detail)
            else:
                # ── P8.DID_YOU_MEAN : suggérer une action proche ──
                _suggestion = ""
                try:
                    from src.config.codeagent_flags import DID_YOU_MEAN
                    if DID_YOU_MEAN:
                        import difflib
                        _known = [
                            "read_file", "write_file", "list_files", "edit_file",
                            "edit_lines", "str_replace", "insert_at_anchor",
                            "apply_patch", "apply_patches", "read_files_batch",
                            "run_command", "run_tests", "grep", "lint",
                            "think", "plan", "undo_edit", "done",
                        ]
                        _matches = difflib.get_close_matches(str(act), _known, n=1, cutoff=0.5)
                        if _matches:
                            _suggestion = f" — voulais-tu dire `{_matches[0]}` ?"
                except Exception:
                    pass
                return ActionResult(f"❌ Action inconnue: {act}{_suggestion}")
        except Exception as exc:
            return ActionResult(f"❌ Erreur lors de {act}: {exc}")


def _is_simple_read(description: str, context: dict) -> bool:
    return ("lire" in description or "read" in description) and not any(
        kw in description for kw in ["modifier", "edit", "fix", "implement", "corrig"]
    )

def _is_simple_grep(description: str) -> bool:
    return any(kw in description for kw in ["cherche", "search", "grep"]) and not any(
        kw in description for kw in ["modifier", "edit", "fix", "implement", "corrig"]
    )

def _is_simple_edit(description: str, context: dict) -> bool:
    return any(kw in description for kw in ["modifier", "edit", "patch", "replace"]) and \
           context.get("file_path") and context.get("search") is not None

def _is_simple_test(description: str) -> bool:
    import re as _re_test
    import unicodedata as _ud_test

    folded = "".join(
        char for char in _ud_test.normalize("NFKD", description or "")
        if not _ud_test.combining(char)
    ).lower()
    return bool(_re_test.search(r'\btest(s|er|ez|ing)?\b', folded)) and not any(
        kw in folded for kw in [
            "modifier", "edit", "fix", "implement", "corrig", "debug",
            "cree", "creer", "create", "build", "genere", "generate",
            "ecri", "rempli", "redig", "coder", "developper",
            "tetris", "contest", "latest", "protest", "attest",
        ]
    )


class ResearchAgent(SubAgent):
    """Agent spécialisé pour la recherche."""
    
    def __init__(self):
        super().__init__(
            agent_type=AgentType.RESEARCH,
            name="ResearchAgent",
            tools=["web_search", "web_fetch", "memory_search"]
        )
    
    async def _execute_task(self, task: AgentTask) -> "str | AgentResult":
        """Exécute une tâche de recherche."""
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        query = task.context.get("query", task.description)
        url = str(task.context.get("url", "")).strip()

        if url:
            fetched = await self._call_tool("web_fetch", {"url": url})
            return self._result_success(task, f"🌐 Page analysée ({url}):\n{fetched}", source="web_fetch", url=url)
        
        # D'abord chercher dans la mémoire
        memory_result = await self._call_tool(
            "memory_search",
            {"query": query}
        )
        
        # Si pas de résultats en mémoire, chercher sur le web
        if "Aucun" in memory_result or len(memory_result) < 50:
            web_result = await self._call_tool(
                "web_search",
                {"query": query}
            )
            return self._result_success(
                task,
                f"📚 Mémoire:\n{memory_result}\n\n🌐 Web:\n{web_result}",
                source="memory+web", query=query,
            )
        
        return self._result_success(task, f"📚 Trouvé en mémoire:\n{memory_result}", source="memory", query=query)


class FileAgent(SubAgent):
    """Agent spécialisé pour les fichiers."""
    
    def __init__(self):
        super().__init__(
            agent_type=AgentType.FILE,
            name="FileAgent",
            tools=["read_file", "write_file", "run_command"]
        )
    
    async def _execute_task(self, task: AgentTask) -> str:
        """Exécute une tâche de fichier."""
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        description = task.description.lower()
        
        if "lire" in description or "read" in description:
            path = task.context.get("path", "")
            if not path:
                return self._result_needs_input(
                    task, "❌ Chemin de fichier manquant",
                    missing_fields=["path"],
                )
            result = await self._call_tool(
                "read_file",
                {"path": path}
            )
            return result
        
        elif "écrire" in description or "write" in description:
            path = task.context.get("path", "")
            content = task.context.get("content", "")
            missing = [f for f in ["path", "content"] if not task.context.get(f)]
            if missing:
                return self._result_needs_input(
                    task, "❌ Chemin ou contenu manquant",
                    missing_fields=missing,
                )
            result = await self._call_tool(
                "write_file",
                {"path": path, "content": content}
            )
            return result
        
        elif "liste" in description or "list" in description:
            path = task.context.get("path", ".")
            result = await self._call_tool(
                "list_directory",
                {"path": path}
            )
            return result

        elif "supprimer" in description or "delete" in description:
            path = str(task.context.get("path", "")).strip()
            if not path:
                return self._result_needs_input(
                    task, "❌ Chemin de fichier manquant",
                    missing_fields=["path"],
                )
            return await self._call_tool("delete_file", {"path": path})

        elif "chercher" in description or "find" in description:
            pattern = str(task.context.get("pattern", "*")).strip() or "*"
            directory = str(task.context.get("directory", ".")).strip() or "."
            return await self._call_tool(
                "find_files",
                {"pattern": pattern, "directory": directory}
            )
        
        return self._result_ambiguous(
            task,
            f"FileAgent: Action non déterminée pour '{task.description}'",
            suggestions=["lire/read", "écrire/write", "liste/list", "supprimer/delete", "chercher/find"],
        )


class DebugAgent(CodeAgent):
    """Agent spécialisé pour le debugging — boucle LLM itérative complète."""

    def __init__(self):
        super().__init__()
        self.agent_type = AgentType.DEBUG
        self.name = "DebugAgent"

    async def _execute_task(self, task: AgentTask) -> "str | AgentResult":
        """Debug itératif : analyse stack trace → corrige → vérifie tests."""
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        # Enrichir la description avec le contexte debug
        error_msg = str(task.context.get("error", "")).strip()
        file_path = str(task.context.get("file_path", "")).strip()
        test_path = str(task.context.get("test_path", "")).strip()

        debug_ctx = task.description
        if error_msg and error_msg not in debug_ctx:
            debug_ctx += f"\n\nErreur à corriger :\n{error_msg}"
        if file_path:
            debug_ctx += f"\n\nFichier concerné : {file_path}"
        if test_path:
            debug_ctx += f"\n\nTests de validation : {test_path}"

        from dataclasses import replace as _replace
        enriched = _replace(
            task,
            description=debug_ctx,
            context={
                **task.context,
                "_debug_mode": True,
                "_system_prompt_override": _DEBUG_SYSTEM_PROMPT,
            },
        )
        return await self._execute_iterative_with_brain(enriched)


class RefactorAgent(CodeAgent):
    """Agent spécialisé pour le refactoring — boucle LLM itérative complète."""

    def __init__(self):
        super().__init__()
        self.agent_type = AgentType.REFACTOR
        self.name = "RefactorAgent"

    async def _execute_task(self, task: AgentTask) -> "str | AgentResult":
        """Refactoring itératif : analyse → modifie → vérifie tests."""
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        # Enrichir la description avec le contexte refactoring
        file_path = str(task.context.get("file_path", "")).strip()
        refactor_type = str(task.context.get("type", "general")).strip()
        old_name = str(task.context.get("old_name", "")).strip()
        new_name = str(task.context.get("new_name", "")).strip()

        refactor_ctx = task.description
        if refactor_type and refactor_type != "general":
            refactor_ctx += f"\n\nType de refactoring : {refactor_type}"
        if file_path:
            refactor_ctx += f"\nFichier principal : {file_path}"
        if old_name and new_name:
            refactor_ctx += f"\nRenommer : '{old_name}' → '{new_name}'"

        from dataclasses import replace as _replace
        enriched = _replace(
            task,
            description=refactor_ctx,
            context={
                **task.context,
                "_refactor_mode": True,
                "_system_prompt_override": _REFACTOR_SYSTEM_PROMPT,
            },
        )
        return await self._execute_iterative_with_brain(enriched)


class BrowserAgent(SubAgent):
    """Agent spécialisé pour navigation et extraction web."""

    def __init__(self):
        super().__init__(
            agent_type=AgentType.BROWSER,
            name="BrowserAgent",
            tools=[
                "browser_navigate",
                "browser_get_text",
                "browser_click",
                "browser_type",
                "browser_screenshot",
                "web_fetch",
            ],
        )

    async def _execute_task(self, task: AgentTask) -> str:
        explicit = await self._execute_explicit_tool(task)
        if explicit is not None:
            return explicit

        description = (task.description or "").lower()
        url = str(task.context.get("url", "")).strip()

        if url and ("fetch" in description or "extra" in description or "résumé" in description or "resume" in description):
            return await self._call_tool("web_fetch", {"url": url})

        if url:
            navig = await self._call_tool("browser_navigate", {"url": url})
            selector = str(task.context.get("selector", "")).strip()
            if selector:
                text = await self._call_tool("browser_get_text", {"selector": selector})
                return f"{navig}\n\n{text}"
            page_text = await self._call_tool("browser_get_text", {"selector": "body"})
            return f"{navig}\n\n{page_text}"

        return self._result_needs_input(
            task,
            "BrowserAgent: impossible d'agir sans URL cible.",
            missing_fields=["url"],
            next_action="Relancer avec context={url} (et optionnellement selector)",
        )


class PlannerAgent(SubAgent):
    """
    Agent planificateur : décompose un objectif en plan structuré.
    
    Produit un JSON de steps avec dépendances, consommable par
    SubAgentOrchestrator.execute_plan().
    
    Utilise le LLM pour raisonner sur la décomposition.
    """


    def __init__(self):
        super().__init__(
            agent_type=AgentType.PLANNER,
            name="PlannerAgent",
            tools=[],  # Le planner ne call pas de tools directement
        )

    async def _execute_task(self, task: AgentTask) -> AgentResult:
        """Génère un plan structuré via LLM."""
        objective = task.description
        extra_context = task.context.get("planning_context", "")

        prompt = f"Objectif : {objective}"
        if extra_context:
            prompt += f"\n\nContexte additionnel : {extra_context}"

        try:
            llm = self._get_llm(task)

            raw_text = await llm.chat(
                messages=[
                    {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=8000,
            )

            raw_text = str(raw_text)

            # Parser le JSON du plan
            plan = self._parse_plan(raw_text)

            if not plan:
                return self._result_error(
                    task,
                    f"PlannerAgent: impossible de parser le plan.\nRaw:\n{raw_text[:500]}",
                    error_type="parse_error",
                )

            return AgentResult(
                task_id=task.task_id,
                success=True,
                output=f"Plan généré : {len(plan)} étapes",
                status_code=StatusCode.SUCCESS,
                meta={"plan": plan, "steps_count": len(plan)},
            )

        except Exception as e:
            return self._result_error(
                task,
                f"PlannerAgent: erreur LLM — {e}",
                error_type="llm_error",
            )

    @staticmethod
    def _parse_plan(raw: str) -> Optional[List[Dict[str, Any]]]:
        """Extrait un plan JSON depuis la réponse LLM."""
        # Essayer le parse direct
        text = raw.strip()
        # Retirer markdown code blocks si présents
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            plan = json.loads(text)
            if isinstance(plan, list) and all(isinstance(s, dict) and "id" in s for s in plan):
                return plan
        except json.JSONDecodeError:
            pass  # try regex fallback

        # Fallback: chercher le premier [...] dans le texte
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                plan = json.loads(match.group())
                if isinstance(plan, list) and all(isinstance(s, dict) and "id" in s for s in plan):
                    return plan
            except json.JSONDecodeError:
                pass  # invalid JSON, return None

        return None


class SubAgentOrchestrator:
    """
    Orchestrateur de sub-agents.
    
    Gère:
    - La création et la gestion des agents
    - L'assignation des tâches
    - La queue de tâches
    - L'agrégation des résultats
    - Persistance des tâches (Phase 15)
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.agents: Dict[str, SubAgent] = {}
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.results: Dict[str, AgentResult] = {}
        self.task_counter = 0
        
        # Persistance du registre de sous-agents
        from src.utils.paths import DATA_DIR
        self.data_dir = data_dir or DATA_DIR
        self.registry_file = self.data_dir / "subagent_registry.json"
        self.pending_tasks: Dict[str, Dict[str, Any]] = {}
        self.archive_after_minutes = 60
        
        # Créer le dossier si nécessaire
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Restaurer les tâches persistées
        self._restore_from_disk()
        
        # Créer les agents par défaut
        self._create_default_agents()
        
        logger.info("🎭 SubAgentOrchestrator initialisé")
    
    def _create_default_agents(self):
        """Crée les agents par défaut."""
        self.register_agent(CodeAgent())
        self.register_agent(ResearchAgent())
        self.register_agent(FileAgent())
        self.register_agent(BrowserAgent())
        self.register_agent(DebugAgent())
        self.register_agent(RefactorAgent())
        self.register_agent(PlannerAgent())
        try:
            from .forking_agent import ForkingAgent
            self.register_agent(ForkingAgent())
        except Exception as e:
            logger.debug(f"ForkingAgent non chargé: {e}")

    def _infer_agent_type(self, description: str, context: Optional[Dict[str, Any]] = None) -> AgentType:
        """Détermine automatiquement le meilleur agent quand type=general."""
        context = context or {}

        forced = str(context.get("agent_type", "")).strip().lower()
        if forced:
            mapping = {
                "code": AgentType.CODE,
                "research": AgentType.RESEARCH,
                "file": AgentType.FILE,
                "browser": AgentType.BROWSER,
                "debug": AgentType.DEBUG,
                "refactor": AgentType.REFACTOR,
                "planner": AgentType.PLANNER,
                "general": AgentType.GENERAL,
            }
            if forced in mapping:
                return mapping[forced]

        text = (description or "").lower()

        # ── Code-modification intent (haute priorité) ──
        # Des mots comme "corriger", "fix", "modifier le code", "bug dans script.js"
        # indiquent une tâche CODE même si "site" ou "navig" apparaissent aussi.
        _code_edit_signals = [
            "corriger", "corrige", "fix ", "fixer", "modifier le code", "modifier le script",
            "modifier le fichier", "bug ", "patch", "script.js", "script.py",
            "style.css", "styles.css", "index.html", ".js", ".py", ".css", ".html",
            "fonction", "function", "navigateTo", "querySelectorAll",
            "syntaxe", "syntax", "accolade", "bracket", "parenthès",
        ]
        _has_code_edit_intent = any(k in text for k in _code_edit_signals)

        if any(k in text for k in ["debug", "erreur", "traceback", "exception", "stack"]):
            return AgentType.DEBUG
        if any(k in text for k in ["refactor", "rename", "renommer", "simplifier", "clean code"]):
            return AgentType.REFACTOR
        # ── Code editing prend priorité sur Browser quand l'intent est clair ──
        if _has_code_edit_intent:
            return AgentType.CODE
        if any(k in text for k in ["browser", "navig", "url", "site", "page web", "crawl"]):
            return AgentType.BROWSER
        if any(k in text for k in ["fichier", "file", "dossier", "read_file", "write_file", "delete", "supprimer"]):
            return AgentType.FILE
        if any(k in text for k in ["code", "fonction", "classe", "test", "python", "bug"]):
            return AgentType.CODE
        if any(k in text for k in ["recherche", "search", "web", "documentation", "doc", "analyse"]):
            return AgentType.RESEARCH

        if "query" in context:
            return AgentType.RESEARCH
        if any(key in context for key in ["path", "file_path", "content"]):
            return AgentType.FILE
        if "url" in context:
            return AgentType.BROWSER

        return AgentType.CODE
    
    def _restore_from_disk(self):
        """Restaure les tâches persistées depuis le disque."""
        try:
            if self.registry_file.exists():
                data = json.loads(self.registry_file.read_text(encoding='utf-8'))
                self.pending_tasks = data.get("pending_tasks", {})
                self.task_counter = data.get("task_counter", 0)
                
                # Nettoyer les anciennes tâches
                self._cleanup_old_tasks()
                
                if self.pending_tasks:
                    logger.info(f"📂 {len(self.pending_tasks)} tâches restaurées depuis le disque")
        except Exception as e:
            logger.debug(f"Impossible de restaurer les tâches: {e}")
    
    def _save_to_disk(self):
        """Sauvegarde les tâches sur disque."""
        try:
            data = {
                "pending_tasks": self.pending_tasks,
                "task_counter": self.task_counter,
                "saved_at": datetime.now().isoformat()
            }
            atomic_write_text(self.registry_file, json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.debug(f"Impossible de sauvegarder les tâches: {e}")
    
    def _cleanup_old_tasks(self):
        """Nettoie les tâches plus anciennes que archive_after_minutes."""
        now = datetime.now()
        to_remove = []
        
        for task_id, task_data in self.pending_tasks.items():
            # Ne jamais archiver une tâche encore en cours : bg_status doit toujours répondre.
            if task_data.get("status") not in ("completed", "failed", "cancelled"):
                continue
            created_at = task_data.get("created_at")
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at)
                    age_minutes = (now - created).total_seconds() / 60
                    if age_minutes > self.archive_after_minutes:
                        to_remove.append(task_id)
                except (ValueError, TypeError):
                    pass  # Invalid datetime format, skip cleanup
        
        for task_id in to_remove:
            del self.pending_tasks[task_id]
            logger.debug(f"🗑️ Tâche archivée: {task_id}")
        
        if to_remove:
            self._save_to_disk()
    
    def register_agent(self, agent: SubAgent):
        """Enregistre un agent."""
        self.agents[agent.name] = agent
        logger.debug(f"🤖 Agent enregistré: {agent.name}")
    
    def get_agent(self, agent_type: AgentType) -> Optional[SubAgent]:
        """Récupère un agent par type."""
        for agent in self.agents.values():
            if agent.agent_type == agent_type:
                return agent
        return None
    
    def get_agent_by_name(self, name: str) -> Optional[SubAgent]:
        """Récupère un agent par nom."""
        return self.agents.get(name)
    
    async def spawn_task(
        self, 
        description: str,
        agent_type: AgentType = AgentType.GENERAL,
        context: Optional[Dict[str, Any]] = None,
        priority: int = 5
    ) -> str:
        """
        Crée et assigne une tâche à un agent.
        
        Returns:
            task_id pour suivre la tâche
        """
        self.task_counter += 1
        task_id = f"task_{self.task_counter}_{datetime.now().strftime('%H%M%S')}"
        
        task = AgentTask(
            task_id=task_id,
            description=description,
            agent_type=agent_type,
            priority=priority,
            context=context or {}
        )
        
        # Persister la tâche (Phase 15)
        self.pending_tasks[task_id] = task.to_dict()
        self._save_to_disk()
        
        await self.task_queue.put(task)
        logger.info(f"📝 Tâche créée: {task_id} -> {agent_type.value}")
        
        return task_id

    async def dispatch_parallel(
        self,
        tasks: List[AgentTask],
        max_concurrent: int = 3,
        max_retries: int = 1,
    ) -> List[AgentResult]:
        """Exécute N tâches en parallèle avec limitation de concurrence (Levier 3).

        - Utilise un Semaphore pour borner le nombre d'agents simultanés.
        - Chaque échec est converti en AgentResult(success=False) au lieu de lever.
        - Retourne la liste ordonnée (même index que `tasks`).
        """
        if not tasks:
            return []
        max_concurrent = max(1, min(int(max_concurrent or 1), 10))
        sem = asyncio.Semaphore(max_concurrent)

        async def _run(t: AgentTask) -> AgentResult:
            async with sem:
                try:
                    return await self.execute_task(t, max_retries=max_retries)
                except Exception as exc:
                    logger.warning("dispatch_parallel: tâche {} échouée: {}", t.task_id, exc)
                    return AgentResult(
                        task_id=t.task_id,
                        success=False,
                        output=f"❌ Erreur: {exc}",
                        status_code=StatusCode.ERROR,
                        meta={"error_type": "fanout_exception"},
                    )

        logger.info(
            "⚡ Fanout: dispatch_parallel({} tâches, max_concurrent={})",
            len(tasks), max_concurrent,
        )
        results = await asyncio.gather(*(_run(t) for t in tasks))
        return list(results)

    async def execute_task(
        self,
        task: AgentTask,
        max_retries: int = 2,
        _attempt: int = 0,
    ) -> AgentResult:
        """
        Exécute une tâche avec l'agent approprié.
        
        Retry intelligent :
        - needs_input → relance avec les missing_fields documentés (le LLM peut enrichir)
        - error/timeout → retry direct (max max_retries)
        - success/ambiguous → pas de retry
        
        Delegation enforcement :
        - Si delegation_ctx présent, vérifie profondeur et cycles
        - Log la chaîne de délégation pour traçabilité
        """
        # ── Enforcement délégation ──────────────────────
        if task.delegation_ctx:
            ctx = task.delegation_ctx
            if ctx.depth > ctx.max_depth:
                return AgentResult(
                    task_id=task.task_id,
                    success=False,
                    output=f"❌ Délégation refusée: profondeur {ctx.depth}/{ctx.max_depth}. Chaîne: {ctx.chain_str}",
                    status_code=StatusCode.ERROR,
                    meta={"error_type": "delegation_depth_exceeded", "chain": list(ctx.agent_chain)},
                )
            logger.debug(f"🔗 Delegation chain: {ctx.chain_str} (depth={ctx.depth}/{ctx.max_depth})")

        effective_type = task.agent_type
        if effective_type == AgentType.GENERAL:
            effective_type = self._infer_agent_type(task.description, task.context)

        agent = self.get_agent(effective_type)

        if not agent:
            agent = list(self.agents.values())[0] if self.agents else None

        if not agent:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                output="Aucun agent disponible",
                status_code=StatusCode.ERROR,
                meta={"error_type": "no_agent"},
            )

        # ── Model Router : escalade progressive ──────────────────────────────
        # Attempt 0 : garder le modèle utilisateur (deepseek-chat → auto-swap
        #             deepseek-reasoner dans _iterative_code_loop)
        # Attempt 1 : escalade OpenAI par grade (gpt-5.4-mini → gpt-5.4)
        # Attempt 2 : escalade Anthropic par grade (claude-sonnet → claude-opus)
        _user_locked_model = task.context.get("_best_model")
        _codex_brain_locked = bool(task.context.get("_codex_brain"))
        if _attempt == 0:
            # Premier essai : on ne touche PAS au modèle, laisser l'auto-swap
            # deepseek→reasoner se faire dans _iterative_code_loop
            if not _user_locked_model:
                logger.debug(
                    f"\U0001f3af Model router: attempt=0 → conserve modèle par défaut"
                )
            else:
                logger.debug(
                    f"\U0001f3af Model router: attempt=0 → conserve {_user_locked_model}"
                )
        elif _attempt > 0 and not _codex_brain_locked:
            # Escalade progressive par provider
            _ESCALATION_CHAIN = [
                # attempt=1 : OpenAI par grade
                ["gpt-5.4-mini", "gpt-5.4"],
                # attempt=2+ : Anthropic par grade
                ["claude-sonnet-4.6", "claude-opus-4.6"],
            ]
            chain_idx = min(_attempt - 1, len(_ESCALATION_CHAIN) - 1)
            chain = _ESCALATION_CHAIN[chain_idx]
            escalated = None
            try:
                from ..llm.providers import AVAILABLE_MODELS, check_api_key
                for candidate in chain:
                    cfg = AVAILABLE_MODELS.get(candidate)
                    if cfg and check_api_key(cfg.provider):
                        escalated = candidate
                        break
            except Exception as _esc_exc:
                logger.debug(f"Escalation import error: {_esc_exc}")

            if escalated:
                prev = _user_locked_model or "(default)"
                task.context["_best_model"] = escalated
                logger.info(
                    f"🔄 Model escalation: {prev} → {escalated} (retry {_attempt})"
                )
            else:
                logger.debug(
                    f"\U0001f3af Model router: escalation chain {chain_idx} — aucun modèle disponible"
                )
        elif _attempt > 0:
            logger.info(
                "\U0001f512 Model escalation API ignoree: abonnement Codex verrouille "
                "(retry {})",
                _attempt,
            )

        result = await agent.execute(task)

        # --- Retry logic ---
        should_retry = (
            _attempt < max_retries
            and result.status_code in (StatusCode.ERROR, StatusCode.TIMEOUT, StatusCode.NEEDS_INPUT)
        )

        if should_retry:
            logger.info(
                f"🔄 [{agent.name}] Retry {_attempt + 1}/{max_retries} "
                f"(status={result.status_code})"
            )

            # Enrichir le contexte pour needs_input
            if result.status_code == StatusCode.NEEDS_INPUT and result.missing_fields:
                enriched_ctx = dict(task.context)
                enriched_ctx["_retry_reason"] = result.status_code
                enriched_ctx["_missing_fields"] = result.missing_fields
                enriched_ctx["_previous_output"] = result.output[:300]
                task = AgentTask(
                    task_id=task.task_id,
                    description=task.description,
                    agent_type=task.agent_type,
                    priority=task.priority,
                    context=enriched_ctx,
                )

            return await self.execute_task(task, max_retries=max_retries, _attempt=_attempt + 1)

        # --- Résultat final ---
        self.results[task.task_id] = result

        # Log structuré pour diagnostic
        if result.status_code != StatusCode.SUCCESS:
            logger.warning(
                f"🤖 [{agent.name}] {result.status_code} | "
                f"missing={result.missing_fields} | "
                f"next_action={result.next_action} | "
                f"attempts={_attempt + 1}"
            )
        
        # Supprimer du registre persistant
        if task.task_id in self.pending_tasks:
            del self.pending_tasks[task.task_id]
            self._save_to_disk()
        
        return result
    
    async def run_task_sync(
        self,
        description: str,
        agent_type: AgentType = AgentType.GENERAL,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentResult:
        """
        Crée et exécute une tâche de manière synchrone.
        Utile pour les cas simples.
        """
        self.task_counter += 1
        task_id = f"task_{self.task_counter}_{datetime.now().strftime('%H%M%S')}"
        task = AgentTask(
            task_id=task_id,
            description=description,
            agent_type=agent_type,
            context=context or {}
        )

        self.pending_tasks[task_id] = task.to_dict()
        self._save_to_disk()

        return await self.execute_task(task)

    async def run_task_bg(
        self,
        description: str,
        agent_type: AgentType = AgentType.GENERAL,
        context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        """
        Lance une tâche en arrière-plan et retourne immédiatement le task_id.
        Le résultat sera disponible dans pending_tasks une fois terminé.
        """
        self.task_counter += 1
        task_id = f"ca_{self.task_counter}_{datetime.now().strftime('%H%M%S')}"
        task = AgentTask(
            task_id=task_id,
            description=description,
            agent_type=agent_type,
            context=context or {},
        )
        self.pending_tasks[task_id] = {
            **task.to_dict(),
            "status": "running",
            "started_at": datetime.now().isoformat(),
        }
        self._save_to_disk()

        async def _run_and_store():
            try:
                if progress_callback:
                    task.context["_progress_callback"] = progress_callback
                result = await self.execute_task(task)
                self.pending_tasks[task_id].update({
                    "status": "done" if result.success else "failed",
                    "output": result.output[:2000],
                    "success": result.success,
                    "meta": result.meta or {},
                    "finished_at": datetime.now().isoformat(),
                })
                self._save_to_disk()
                if progress_callback:
                    try:
                        _msg = (
                            f"✅ **Tâche terminée** (`{task_id}`)\n"
                            f"{result.output[:300]}"
                        )
                        _r = progress_callback(_msg)
                        if asyncio.iscoroutine(_r):
                            await _r
                    except Exception:
                        pass
            except asyncio.CancelledError:
                # Annulation coopérative : mise à jour du statut, pas de callback
                self.pending_tasks[task_id].update({
                    "status": "cancelled",
                    "finished_at": datetime.now().isoformat(),
                })
                self._save_to_disk()
                logger.info("[SubAgent] bg task {} cancelled", task_id)
                raise
            except Exception as exc:
                logger.error("[SubAgent] bg task {} failed: {}", task_id, exc)
                self.pending_tasks[task_id].update({
                    "status": "failed",
                    "output": f"Erreur: {exc}",
                    "success": False,
                    "finished_at": datetime.now().isoformat(),
                })
                self._save_to_disk()
                if progress_callback:
                    try:
                        _r = progress_callback(f"❌ **Erreur** (`{task_id}`): {exc}")
                        if asyncio.iscoroutine(_r):
                            await _r
                    except Exception:
                        pass
            finally:
                _unregister_bg_agent(task_id)

        _bg_task = asyncio.create_task(_run_and_store())
        _register_bg_agent(task_id, _bg_task)
        logger.info("[SubAgent] 🚀 Tâche bg lancée: {} ({})", task_id, agent_type.value)
        return task_id

    async def execute_plan(
        self,
        steps: List[Dict[str, Any]],
        delegation_ctx: Optional[DelegationContext] = None,
    ) -> Dict[str, AgentResult]:
        """
        Exécute un plan multi-étapes avec dépendances.
        
        Format des steps :
        [
            {"id": "s1", "description": "...", "agent_type": "code", "context": {...}},
            {"id": "s2", "description": "...", "agent_type": "file", "context": {...}, "depends_on": ["s1"]},
        ]
        
        Args:
            steps: Liste des étapes du plan
            delegation_ctx: Contexte de délégation optionnel pour propager la chaîne
        
        Returns:
            Dict step_id -> AgentResult
        """
        results: Dict[str, AgentResult] = {}
        completed: set = set()
        steps_by_id = {s["id"]: s for s in steps}

        # Ordre topologique simple
        remaining = list(steps)
        max_iterations = len(steps) * 2  # Protection boucle infinie

        for _ in range(max_iterations):
            if not remaining:
                break

            # Trouver les étapes prêtes (toutes deps complétées)
            ready = []
            still_waiting = []
            for step in remaining:
                deps = step.get("depends_on", [])
                if all(d in completed for d in deps):
                    ready.append(step)
                else:
                    # Vérifier si une dep a échoué définitivement
                    failed_deps = [d for d in deps if d in results and not results[d].success]
                    if failed_deps:
                        # Dépendance échouée — propager l'échec
                        results[step["id"]] = AgentResult(
                            task_id=step["id"],
                            success=False,
                            output=f"Skipped: dépendance(s) échouée(s): {failed_deps}",
                            status_code=StatusCode.ERROR,
                            meta={"skipped_due_to": failed_deps},
                        )
                        completed.add(step["id"])
                    else:
                        still_waiting.append(step)

            remaining = still_waiting

            if not ready:
                # Plus rien à exécuter — les restants sont bloqués
                break

            # Exécuter les étapes prêtes — en parallèle si plusieurs indépendantes
            _PLAN_MAX_PARALLEL = int(os.environ.get("LUMENA_PLAN_MAX_PARALLEL", "3"))
            _plan_sem = asyncio.Semaphore(_PLAN_MAX_PARALLEL)

            async def _run_one_step(step: dict) -> tuple:
                """Exécute une step avec concurrence limitée par semaphore."""
                async with _plan_sem:
                    ctx = dict(step.get("context", {}))
                    for dep_id in step.get("depends_on", []):
                        if dep_id in results:
                            ctx[f"_dep_{dep_id}_output"] = results[dep_id].output[:500]
                            ctx[f"_dep_{dep_id}_success"] = results[dep_id].success

                    type_map = {
                        "code": AgentType.CODE, "research": AgentType.RESEARCH,
                        "file": AgentType.FILE, "browser": AgentType.BROWSER,
                        "debug": AgentType.DEBUG, "refactor": AgentType.REFACTOR,
                        "planner": AgentType.PLANNER, "general": AgentType.GENERAL,
                    }
                    agent_type = type_map.get(step.get("agent_type", "general"), AgentType.GENERAL)

                    step_delegation_ctx = None
                    if delegation_ctx:
                        target = self.get_agent(agent_type)
                        target_name = target.name if target else f"agent_{agent_type.value}"
                        try:
                            step_delegation_ctx = delegation_ctx.delegate_to(target_name, step["id"])
                        except (DelegationDepthExceeded, DelegationCycleDetected) as e:
                            return step["id"], AgentResult(
                                task_id=step["id"],
                                success=False,
                                output=f"Delegation refused: {e}",
                                status_code=StatusCode.ERROR,
                                meta={"error_type": type(e).__name__},
                            )

                    task = AgentTask(
                        task_id=step["id"],
                        description=step["description"],
                        agent_type=agent_type,
                        priority=step.get("priority", 5),
                        context=ctx,
                        delegation_ctx=step_delegation_ctx,
                    )

                    result = await self.execute_task(task)
                    return step["id"], result

            # Lancer toutes les steps "ready" en parallèle (semaphore limite la concurrence)
            step_outcomes = await asyncio.gather(
                *[_run_one_step(s) for s in ready],
                return_exceptions=True,
            )
            for item in step_outcomes:
                if isinstance(item, Exception):
                    logger.error("[execute_plan] Step exception: {}", item)
                    continue
                step_id, result = item
                results[step_id] = result
                completed.add(step_id)
                logger.info(
                    f"📋 Plan step '{step_id}': {result.status_code} "
                    f"({result.duration_ms}ms)"
                )

        # Marquer les étapes encore en attente comme bloquées
        for step in remaining:
            if step["id"] not in results:
                results[step["id"]] = AgentResult(
                    task_id=step["id"],
                    success=False,
                    output="Blocked: dépendances non résolues",
                    status_code=StatusCode.ERROR,
                    meta={"blocked": True},
                )

        return results
    
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut de tous les agents avec métriques enrichies."""
        core_agents = {"CodeAgent", "ResearchAgent", "FileAgent"}
        legacy_total = sum(1 for name in self.agents.keys() if name in core_agents)

        # Calculer les stats par status_code
        status_counts: Dict[str, int] = {}
        for r in self.results.values():
            sc = r.status_code if hasattr(r, 'status_code') else "unknown"
            status_counts[sc] = status_counts.get(sc, 0) + 1

        return {
            "total_agents": legacy_total,
            "total_agents_all": len(self.agents),
            "agents": [
                {
                    "name": agent.name,
                    "type": agent.agent_type.value,
                    "status": agent.status.value,
                    "tasks_completed": len(agent.history),
                    "last_result": (
                        {
                            "status_code": agent.history[-1].status_code,
                            "duration_ms": agent.history[-1].duration_ms,
                        }
                        if agent.history else None
                    ),
                }
                for agent in self.agents.values()
            ],
            "pending_tasks": len(self.pending_tasks),
            "completed_tasks": len(self.results),
            "status_breakdown": status_counts,
        }
    
    def format_status(self) -> str:
        """Retourne le statut formaté pour affichage."""
        status = self.get_status()
        
        lines = ["🤖 **Sub-Agents Status**\n"]
        
        for agent in status["agents"]:
            emoji = "🟢" if agent["status"] == "idle" else "🔵" if agent["status"] == "running" else "⚪"
            last = agent.get("last_result")
            suffix = ""
            if last:
                suffix = f" | last: {last['status_code']} ({last['duration_ms']}ms)"
            lines.append(f"{emoji} {agent['name']} ({agent['type']}) - {agent['tasks_completed']} tâches{suffix}")
        
        lines.append(f"\n📋 En attente: {status['pending_tasks']}")
        lines.append(f"✅ Complétées: {status['completed_tasks']}")
        
        breakdown = status.get("status_breakdown", {})
        if breakdown:
            parts = [f"{k}={v}" for k, v in breakdown.items()]
            lines.append(f"📊 Breakdown: {', '.join(parts)}")
        
        return "\n".join(lines)

    def get_mission_summary(self) -> Dict[str, Any]:
        """
        Résumé complet d'une session — ce que les agents ont fait.
        Utile pour la visibilité et le debriefing.
        """
        total_duration = sum(r.duration_ms for r in self.results.values())
        successes = [r for r in self.results.values() if r.success]
        failures = [r for r in self.results.values() if not r.success]

        # Grouper par agent
        by_agent: Dict[str, List[AgentResult]] = {}
        for agent in self.agents.values():
            if agent.history:
                by_agent[agent.name] = list(agent.history)

        return {
            "total_tasks": len(self.results),
            "successes": len(successes),
            "failures": len(failures),
            "success_rate": round(len(successes) / max(len(self.results), 1) * 100, 1),
            "total_duration_ms": total_duration,
            "by_agent": {
                name: {
                    "count": len(results),
                    "success": sum(1 for r in results if r.success),
                    "avg_duration_ms": int(sum(r.duration_ms for r in results) / max(len(results), 1)),
                }
                for name, results in by_agent.items()
            },
            "recent_failures": [
                {
                    "task_id": r.task_id,
                    "status_code": r.status_code,
                    "output": r.output[:150],
                    "missing_fields": r.missing_fields,
                }
                for r in failures[-5:]  # 5 derniers échecs
            ],
        }

    def format_mission_summary(self) -> str:
        """Résumé de mission lisible."""
        s = self.get_mission_summary()
        lines = [
            "📋 **Mission Summary**\n",
            f"Tâches: {s['total_tasks']} | ✅ {s['successes']} | ❌ {s['failures']} | {s['success_rate']}%",
            f"Durée totale: {s['total_duration_ms']}ms",
        ]

        if s["by_agent"]:
            lines.append("\nPar agent:")
            for name, stats in s["by_agent"].items():
                lines.append(f"  {name}: {stats['count']} tâches ({stats['success']} ok, ~{stats['avg_duration_ms']}ms)")

        if s["recent_failures"]:
            lines.append("\nDerniers échecs:")
            for f in s["recent_failures"]:
                lines.append(f"  ❌ {f['task_id']}: {f['status_code']} — {f['output'][:80]}")

        return "\n".join(lines)


# Instance globale avec lock thread-safe (Phase 2.1)
import threading
_orchestrator: Optional[SubAgentOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_orchestrator() -> SubAgentOrchestrator:
    """Retourne l'orchestrateur global (thread-safe)."""
    global _orchestrator
    
    # Double-check locking pattern
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = SubAgentOrchestrator()
    return _orchestrator


# Fonctions utilitaires pour l'intégration
async def delegate_to_agent(
    description: str,
    agent_type: str = "general",
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Fonction simple pour déléguer une tâche à un agent.
    Utilisée par les outils.
    """
    orchestrator = get_orchestrator()
    
    # Mapper le type string vers l'enum
    type_map = {
        "code": AgentType.CODE,
        "research": AgentType.RESEARCH,
        "file": AgentType.FILE,
        "browser": AgentType.BROWSER,
        "debug": AgentType.DEBUG,
        "refactor": AgentType.REFACTOR,
        "planner": AgentType.PLANNER,
        "general": AgentType.GENERAL
    }
    
    agent_enum = type_map.get(agent_type.lower(), AgentType.GENERAL)
    
    result = await orchestrator.run_task_sync(description, agent_enum, context)
    
    return result.output


async def delegate_to_agent_full(
    description: str,
    agent_type: str = "general",
    context: Optional[Dict[str, Any]] = None,
) -> "AgentResult":
    """
    Comme delegate_to_agent mais retourne l'AgentResult complet
    (output, meta, artifacts, duration_ms, success, status_code).
    """
    orchestrator = get_orchestrator()
    type_map = {
        "code": AgentType.CODE, "research": AgentType.RESEARCH,
        "file": AgentType.FILE, "browser": AgentType.BROWSER,
        "debug": AgentType.DEBUG, "refactor": AgentType.REFACTOR,
        "planner": AgentType.PLANNER, "general": AgentType.GENERAL,
    }
    agent_enum = type_map.get(agent_type.lower(), AgentType.GENERAL)
    return await orchestrator.run_task_sync(description, agent_enum, context)


async def delegate_to_agent_bg(
    description: str,
    agent_type: str = "code",
    context: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable] = None,
) -> str:
    """
    Lance une tâche agent en arrière-plan et retourne immédiatement le task_id.
    Le résultat sera consultable via bg_status(task_id).
    """
    orchestrator = get_orchestrator()

    type_map = {
        "code": AgentType.CODE,
        "research": AgentType.RESEARCH,
        "file": AgentType.FILE,
        "browser": AgentType.BROWSER,
        "debug": AgentType.DEBUG,
        "refactor": AgentType.REFACTOR,
        "planner": AgentType.PLANNER,
        "general": AgentType.GENERAL,
    }

    agent_enum = type_map.get(agent_type.lower(), AgentType.GENERAL)

    task_id = await orchestrator.run_task_bg(
        description, agent_enum, context,
        progress_callback=progress_callback,
    )
    return task_id


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
