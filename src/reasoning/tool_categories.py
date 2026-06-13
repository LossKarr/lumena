"""
tool_categories.py — Contrats formels des catégories d'outils Lumena.

Chaque catégorie définit :
  - role        : ce que les outils de la catégorie font
  - preconditions : ce qui doit être vrai avant d'appeler un outil de la catégorie
  - allowed_effects : mutations autorisées
  - refusal_reasons : quand refuser (causes explicables)
  - autonomy_allowed : l'autonomie peut-elle déclencher ces outils sans interaction ?
  - requires_workspace : workspace_path résolu requis pour les mutations
  - delegate_code_threshold : nombre de fichiers mutés au-delà duquel → delegate_task(code)

Usage :
    contract = get_category_contract("files")
    if contract.requires_workspace and not workspace_path:
        raise PreconditionError("workspace_path manquant pour category=files")
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions de contrat
# ──────────────────────────────────────────────────────────────────────────────

class PreconditionError(Exception):
    """Précondition de catégorie non satisfaite — refus explicable."""


# ──────────────────────────────────────────────────────────────────────────────
# Contrat de catégorie
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCategoryContract:
    """
    Contrat formel d'une catégorie d'outils.

    Immuable — une seule définition par catégorie, jamais modifiée à l'exécution.
    """

    name: str
    role: str
    preconditions: List[str] = field(default_factory=list)
    allowed_effects: List[str] = field(default_factory=list)
    refusal_reasons: List[str] = field(default_factory=list)
    autonomy_allowed: bool = False
    requires_workspace: bool = False
    delegate_code_threshold: int = 0


# ──────────────────────────────────────────────────────────────────────────────
# Registre des 19 catégories sémantiques
# ──────────────────────────────────────────────────────────────────────────────

_CONTRACTS: Dict[str, ToolCategoryContract] = {

    "files": ToolCategoryContract(
        name="files",
        role="Lecture, écriture et édition de fichiers dans le workspace.",
        preconditions=[
            "workspace_path résolu",
            "chemin cible dans le workspace ou dans un répertoire autorisé",
        ],
        allowed_effects=["lecture fichier", "écriture fichier", "création fichier", "suppression fichier"],
        refusal_reasons=[
            "workspace_path absent ou ambigu",
            "chemin cible hors workspace (traversal)",
            "fichier code/config → délégation CodeAgent si chantier complexe",
        ],
        autonomy_allowed=True,
        requires_workspace=True,
        delegate_code_threshold=3,
    ),

    "system": ToolCategoryContract(
        name="system",
        role="Informations système, processus, variables d'environnement.",
        preconditions=[],
        allowed_effects=["lecture état système", "exécution commande shell"],
        refusal_reasons=[
            "commande dangereuse (rm -rf, format, etc.)",
            "accès root non autorisé",
        ],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "web": ToolCategoryContract(
        name="web",
        role="Recherche web, requêtes HTTP, crawling de pages.",
        preconditions=["accès réseau disponible"],
        allowed_effects=["lecture URL externe", "requête HTTP"],
        refusal_reasons=["URL interne ou privée", "bande passante excessive"],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "mcp": ToolCategoryContract(
        name="mcp",
        role="Outils MCP (Model Context Protocol) : serveurs externes actives dynamiquement.",
        preconditions=[
            "serveur MCP ACTIVE dans le Catalog Phase 14",
            "handler dynamique enregistre via Phase 19 activation_service",
            "policy MCP resolue Phase 15",
            "trust_score >= seuil applicable selon policy",
            "runtime non quarantined (Phase 12 watcher)",
        ],
        allowed_effects=[
            "appel JSON-RPC stdio vers serveur MCP isole",
            "lecture externe (READ_ONLY/EXTERNAL_READ)",
            "ecriture locale reversible (LOCAL_WRITE/EXTERNAL_WRITE_RECOVERABLE)",
            "ecriture externe irreversible UNIQUEMENT si approval humain (Phase 10)",
        ],
        refusal_reasons=[
            "serveur non ACTIVE (status DECLARED/INSTALLED/QUARANTINED)",
            "policy bloquee par PolicyResolver Phase 15",
            "trust_score insuffisant",
            "runtime crash_loop ou unhealthy (Phase 12)",
            "approval en attente (Phase 10)",
        ],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "data": ToolCategoryContract(
        name="data",
        role="Données publiques (data.gouv, SIRENE, géo) : recherche, téléchargement, analyse de jeux de données.",
        preconditions=["accès réseau disponible"],
        allowed_effects=[
            "recherche dataset", "téléchargement ressource", "lecture/analyse fichier de données",
        ],
        refusal_reasons=["URL interne ou privée", "volume excessif"],
        autonomy_allowed=True,
        # Pas de workspace imposé au niveau contrat : datagouv_search/sirene/geo n'en
        # ont pas besoin. Les outils qui écrivent/lisent des fichiers
        # (datagouv_download_resource, data_profile_file, data_export, data_join)
        # conservent leurs propres checks workspace/path internes.
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "memory": ToolCategoryContract(
        name="memory",
        role="Lecture et écriture de la mémoire à long terme de Lumena.",
        preconditions=[],
        allowed_effects=["lecture mémoire", "écriture mémoire", "suppression mémoire"],
        refusal_reasons=["contenu interdit (secrets, données personnelles non consenties)"],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "browser": ToolCategoryContract(
        name="browser",
        role="Contrôle navigateur Playwright pour navigation web avancée.",
        preconditions=["Playwright disponible", "accès réseau"],
        allowed_effects=["navigation URL", "clic", "saisie formulaire", "screenshot"],
        refusal_reasons=["site interdit", "action destructrice non réversible"],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "computer_use": ToolCategoryContract(
        name="computer_use",
        role="Contrôle du bureau : souris, clavier, captures d'écran.",
        preconditions=["mode computer_use activé", "consentement utilisateur"],
        allowed_effects=["clic souris", "saisie clavier", "screenshot bureau"],
        refusal_reasons=[
            "action destructrice (suppression, formatage)",
            "accès application sensible sans consentement",
        ],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "agents": ToolCategoryContract(
        name="agents",
        role="Délégation à des sous-agents, tâches en arrière-plan, processus.",
        preconditions=[
            "description de tâche suffisamment précise (>20 caractères)",
            "workspace_path résolu si la tâche mute des fichiers",
        ],
        allowed_effects=["lancement sous-agent", "tâche background", "processus fils"],
        refusal_reasons=[
            "description trop vague pour délégation fiable",
            "delegate_task(code) réservé aux chantiers code complexes (≥3 fichiers ou architecture entière)",
            "confiance routing < 0.90 sans project_path explicite",
        ],
        autonomy_allowed=True,
        requires_workspace=True,
        delegate_code_threshold=0,
    ),

    "peers": ToolCategoryContract(
        name="peers",
        role="Collaboration inter-Lumena : délégation réseau, connaissances partagées, tâches peer.",
        preconditions=[
            "peer trusted",
            "peer token sortant disponible",
            "scope autorisé dans allowed_scopes",
            "host pair validé RFC1918",
        ],
        allowed_effects=[
            "appel pair Lumena trusted",
            "délégation chat",
            "requête connaissance read-only",
            "tâche peer bornée et auditée",
        ],
        refusal_reasons=[
            "pair non trusted ou bloqué",
            "peer token absent",
            "scope absent",
            "host refusé par anti-SSRF",
            "secret détecté dans le payload",
        ],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "communication": ToolCategoryContract(
        name="communication",
        role="Mail, Discord, notifications, Twitter/X.",
        preconditions=["destinataire valide", "credentials configurés"],
        allowed_effects=["envoi message", "envoi email"],
        refusal_reasons=[
            "contenu interdit (spam, harcèlement)",
            "envoi en masse sans consentement explicite",
            "mode autonomie : confirmation utilisateur requise avant envoi",
        ],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "documents": ToolCategoryContract(
        name="documents",
        role="Génération et lecture de documents (PDF, DOCX, XLSX, PPTX).",
        preconditions=["workspace_path résolu pour écriture"],
        allowed_effects=["lecture document", "création document", "export document"],
        refusal_reasons=["format non supporté", "chemin hors workspace"],
        autonomy_allowed=True,
        requires_workspace=True,
        delegate_code_threshold=0,
    ),

    "media": ToolCategoryContract(
        name="media",
        role="Génération image, vidéo, audio, contrôle Spotify.",
        preconditions=["service externe disponible"],
        allowed_effects=["génération image", "génération vidéo", "lecture musique"],
        refusal_reasons=["contenu illicite", "quota dépassé"],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "project": ToolCategoryContract(
        name="project",
        role="Création et gestion de projets dans le registry Lumena.",
        preconditions=["intent résolu (create|modify|read)"],
        allowed_effects=["création projet", "accès projet", "mise à jour registry"],
        refusal_reasons=[
            "intent ambigu sans workspace_path explicite",
            "modification sur projet ambigu (confiance <0.80)",
        ],
        autonomy_allowed=True,
        requires_workspace=True,
        delegate_code_threshold=0,
    ),

    "git": ToolCategoryContract(
        name="git",
        role="Opérations Git locales (commit, branch, status, diff).",
        preconditions=["workspace_path est un dépôt git"],
        allowed_effects=["lecture git", "commit", "création branche", "merge local"],
        refusal_reasons=[
            "push force sans confirmation",
            "reset --hard sans confirmation",
            "workspace_path absent",
        ],
        autonomy_allowed=False,
        requires_workspace=True,
        delegate_code_threshold=0,
    ),

    "github": ToolCategoryContract(
        name="github",
        role="API GitHub : issues, PRs, releases.",
        preconditions=["GITHUB_TOKEN configuré"],
        allowed_effects=["lecture repo", "création PR", "commentaire issue"],
        refusal_reasons=[
            "push force sur main",
            "suppression branche protégée",
            "mode autonomie : confirmation avant push",
        ],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "autonomy": ToolCategoryContract(
        name="autonomy",
        role="Gestion des objectifs, plans, scheduler et heartbeat Lumena.",
        preconditions=["daemon autonomie actif"],
        allowed_effects=["lecture goals", "planification tâche", "mise à jour plan"],
        refusal_reasons=[
            "mutation goal sans risk_level défini",
            "tâche autonome sans task_envelope complet",
        ],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "security": ToolCategoryContract(
        name="security",
        role="OSINT, scan réseau, analyse de sécurité défensive.",
        preconditions=["cible dans le périmètre autorisé (own systems or explicit pentest scope)"],
        allowed_effects=["scan passif", "enumération DNS", "analyse SSL"],
        refusal_reasons=[
            "cible hors périmètre sans autorisation explicite",
            "techniques destructrices (DoS, exploit actif)",
        ],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "network": ToolCategoryContract(
        name="network",
        role="Outils réseau : ping, traceroute, DNS, HTTP headers.",
        preconditions=["accès réseau"],
        allowed_effects=["requête réseau passive"],
        refusal_reasons=["scan masse non autorisé", "IP réservées"],
        autonomy_allowed=True,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "platform": ToolCategoryContract(
        name="platform",
        role="Intégrations plateformes tierces : Stripe, N8N, IONOS, Notion, IDE, LSP.",
        preconditions=["credentials plateforme configurés"],
        allowed_effects=["lecture données plateforme", "action plateforme"],
        refusal_reasons=[
            "credentials absents",
            "action financière (Stripe) sans confirmation",
        ],
        autonomy_allowed=False,
        requires_workspace=False,
        delegate_code_threshold=0,
    ),

    "codebase": ToolCategoryContract(
        name="codebase",
        role="Analyse statique de code : outline, symboles, dépendances.",
        preconditions=["workspace_path résolu"],
        allowed_effects=["lecture structure code", "analyse symboles"],
        refusal_reasons=["workspace_path absent"],
        autonomy_allowed=True,
        requires_workspace=True,
        delegate_code_threshold=0,
    ),
}

# Alias : mappe les noms de modules handler vers les catégories sémantiques
_MODULE_TO_SEMANTIC: Dict[str, str] = {
    "files":         "files",
    "system":        "system",
    "web":           "web",
    "mcp":           "mcp",
    # Phase D : `mcp_loop_integration` est fusionne dans le contrat `mcp` unifie.
    # Les 4 outils Phase 26 (request_mcp_capability, etc.) heritent maintenant
    # de la categorie sematique "mcp" via _register_phase26_native_handler.
    "mcp_loop_integration": "mcp",
    "data":          "data",
    "memory":        "memory",
    "browser":       "browser",
    "computer_use":  "computer_use",
    "agents":        "agents",
    "peers":         "peers",
    "mail":          "communication",
    "discord":       "communication",
    "social":        "communication",
    "documents":     "documents",
    "spotify":       "media",
    "image":         "media",
    "video":         "media",
    "project":       "project",
    "git":           "git",
    "github":        "github",
    "autonomy":      "autonomy",
    "security":      "security",
    "osint":         "security",
    "network":       "network",
    "website":       "files",
    "lsp":           "platform",
    "codebase":      "codebase",
    "ide":           "platform",
    "stripe":        "platform",
    "automation":    "platform",
    "ionos":         "platform",
    "notion":        "platform",
    "skills":        "system",
    "custom":        "system",
    "batch":         "files",
    "perception":    "documents",
    "http_api":      "web",
    "plans":         "autonomy",
    "config_manager": "system",
    "heartbeat_self": "autonomy",
    "uncategorized": "system",
    # Cohérence — catégories utilisées dans certains HandlerDef.category
    # mais absentes de la table principale.
    "code":               "codebase",
    "security_offensive": "security",
}


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Mono-catégorie (1 catégorie = 1 contrat)
# ──────────────────────────────────────────────────────────────────────────────
# Ces 15 catégories étaient regroupées sous un contrat parent (communication /
# media / platform / system / files). On leur donne leur propre contrat, par
# CLONAGE iso du parent (mêmes preconditions/effets/autonomy/workspace) afin de
# ne rien changer au comportement. Les ajustements de gouvernance (lsp, skills)
# sont faits séparément en Phase 3 — PAS ici.
_MONO_FROM_PARENT: Dict[str, str] = {
    "mail": "communication", "discord": "communication", "social": "communication",
    "image": "media", "spotify": "media", "video": "media",
    "ionos": "platform", "ide": "platform", "stripe": "platform",
    "automation": "platform", "notion": "platform", "lsp": "platform",
    "skills": "system", "custom": "system",
    "website": "files",
}

for _child, _parent in _MONO_FROM_PARENT.items():
    # 1) contrat mono cloné du parent (iso)
    if _child not in _CONTRACTS and _parent in _CONTRACTS:
        _CONTRACTS[_child] = replace(_CONTRACTS[_parent], name=_child)
    # 2) mapping en identité : la catégorie pointe vers son propre contrat
    _MODULE_TO_SEMANTIC[_child] = _child


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Ajustements de gouvernance (les 2 SEULS changements de comportement)
# ──────────────────────────────────────────────────────────────────────────────
# lsp : diagnostics / goto / references / hover = lecture de code pure. Sûr en
# autonome, mais nécessite un workspace résolu pour cibler le bon projet.
_CONTRACTS["lsp"] = replace(
    _CONTRACTS["lsp"], autonomy_allowed=True, requires_workspace=True
)
# skills : contient edit_own_code / rollback / sync_skills_main (auto-modification
# du code de Lumena). NE doit JAMAIS tourner sans interaction → non-autonome.
_CONTRACTS["skills"] = replace(
    _CONTRACTS["skills"], autonomy_allowed=False
)


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────

def get_category_contract(category: str) -> Optional[ToolCategoryContract]:
    """Retourne le contrat d'une catégorie par son nom de module ou sémantique."""
    semantic = _MODULE_TO_SEMANTIC.get(category, category)
    return _CONTRACTS.get(semantic)


def get_semantic_category(module_category: str) -> str:
    """Mappe un nom de module handler vers la catégorie sémantique."""
    return _MODULE_TO_SEMANTIC.get(module_category, module_category)


def requires_workspace(category: str) -> bool:
    """True si la catégorie exige un workspace_path résolu."""
    contract = get_category_contract(category)
    return contract.requires_workspace if contract else False


def autonomy_allowed(category: str) -> bool:
    """True si l'autonomie peut déclencher des outils de cette catégorie."""
    contract = get_category_contract(category)
    return contract.autonomy_allowed if contract else False


def delegate_code_threshold(category: str) -> int:
    """Seuil de fichiers mutés au-delà duquel → delegate_task(code)."""
    contract = get_category_contract(category)
    return contract.delegate_code_threshold if contract else 0


def all_contracts() -> List[ToolCategoryContract]:
    """Retourne tous les contrats définis."""
    return list(_CONTRACTS.values())


def get_category_summary() -> str:
    """Résumé lisible des catégories pour les prompts LLM."""
    lines = []
    for c in _CONTRACTS.values():
        auto = "✅ autonomie" if c.autonomy_allowed else "❌ autonomie"
        ws = " | workspace requis" if c.requires_workspace else ""
        lines.append(f"  [{c.name}] {c.role} — {auto}{ws}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Fonction pure pour audit (Phase 2 drift check)
# ──────────────────────────────────────────────────────────────────────────────
#
# Cette fonction est UNIQUEMENT utilisée par l'audit runtime (drift check).
# Elle reflète la logique de `ToolRegistry._category_contract_check()` mais
# SANS aucun side effect : pas de logger, pas de TraceBus, pas de metrics,
# pas de lecture ContextVar ni de ide_context.
#
# Garanties :
#   - Pure : même entrée → même sortie, toujours
#   - Déterministe : ne lit aucun état global
#   - Zéro side effect : aucune mutation, aucune télémétrie
#
# Couverture (reflète exactement _category_contract_check dans tool_registry.py) :
#   - requires_workspace s'applique uniquement à :
#       * caller in {autonomy, scheduler, daemon} pour toutes catégories
#         requires_workspace=True
#       * caller=react UNIQUEMENT pour semantic == "agents"
#       (les fichiers REACT sont contrôlés par WorkspaceFileGuardrails,
#        pas par cette règle)
#   - autonomy_allowed=False bloque caller in {autonomy, scheduler, daemon}
#
# Hors scope :
#   - Cas delegate_task description vague (args-dependent, pas testable
#     en audit sans args réels)
#   - _policy_check (couche orthogonale, dépend du path concret)

def check_contract_for_audit(
    semantic_category: str,
    caller_kind: str,
    has_workspace: bool,
) -> Optional[str]:
    """Évalue si un outil de la catégorie sémantique passe le contrat
    pour ce couple (caller, état workspace).

    Args:
        semantic_category: catégorie sémantique (ex: "files", "agents", "web")
        caller_kind: identité agent ("react", "codeagent", "autonomy",
                     "scheduler", "daemon", "silent", "unknown")
        has_workspace: True si un workspace est fourni dans le contexte

    Returns:
        None si le contrat passe (outil callable).
        str décrivant la raison du refus sinon.

    Cette fonction est PURE — aucun side effect, aucun log, aucune métrique.
    Voir docstring du module pour la sémantique exacte.
    """
    contract = _CONTRACTS.get(semantic_category)
    if contract is None:
        # Catégorie inconnue : aucun contrat ne s'applique, callable par défaut
        return None

    # ── Règle 1 : requires_workspace ──
    if contract.requires_workspace:
        # autonomy/scheduler/daemon : toutes catégories requires_workspace
        if caller_kind in ("autonomy", "scheduler", "daemon"):
            if not has_workspace:
                return (
                    f"requires_workspace=True for caller={caller_kind} "
                    f"and category={semantic_category}"
                )
        # react : UNIQUEMENT semantic == "agents"
        elif caller_kind == "react" and semantic_category == "agents":
            if not has_workspace:
                return (
                    f"requires_workspace=True for caller=react "
                    f"and category=agents"
                )
        # codeagent, silent, unknown, react+autres catégories : pas bloqués
        # par cette règle (fichiers REACT contrôlés par WorkspaceFileGuardrails)

    # ── Règle 2 : autonomy_allowed ──
    if caller_kind in ("autonomy", "scheduler", "daemon"):
        if not contract.autonomy_allowed:
            return (
                f"autonomy_allowed=False for category={semantic_category} "
                f"(caller={caller_kind})"
            )

    # Toutes les règles passent
    return None
