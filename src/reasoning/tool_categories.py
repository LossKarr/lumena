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

from dataclasses import dataclass, field
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
# Registre des 18 catégories sémantiques
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
    "memory":        "memory",
    "browser":       "browser",
    "computer_use":  "computer_use",
    "agents":        "agents",
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
