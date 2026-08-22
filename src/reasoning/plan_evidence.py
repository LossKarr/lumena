"""
Politique de preuve et de complétion du plan ReAct.

Extrait de react.py pour isoler la logique de décision :
  - détection des tâches de vérification
  - classification des observations (succès / échec)
  - validation de preuve pour les tâches de vérification
  - réconciliation du plan après delegate_task
  - constantes de guard (séquentiel, exploration, métier)

Phase 2 — couche de proof capabilities :
  - ProofCapability (14 capabilities)
  - VerificationKind (7 kinds)
  - mapping catégorie → capabilities par défaut (scalable)
  - overrides par outil pour les exceptions importantes
  - has_sufficient_proof() — preuve kind-aware remplaçant la flat-list Phase 1

Toutes les fonctions publiques sont des fonctions pures :
elles n'ont aucune dépendance sur ReActLoop et sont testables de façon isolée.

Architecture à 3 niveaux :
  Niveau 1 : catégories sémantiques (19) de tool_categories.py
  Niveau 2 : ProofCapability — couche plus fine, mappée depuis les catégories
  Niveau 3 : VerificationKind — détecté depuis la description de la tâche

Ajouter un nouvel outil = en général aucune règle de preuve à écrire,
la catégorie de l'outil détermine ses capabilities.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

from loguru import logger

from .react_config import TaskItem


# ── Guard séquentiel — blocklist ───────────────────────────────────────────────
# Outils purement info/inspection : ne peuvent pas cocher une tâche métier via
# le fallback séquentiel (ex: "config" dans get_lumena_config matcherait
# faussement "Configurer les rôles" sans qu'aucune action métier ait été faite).
_SEQ_FALLBACK_BLOCKLIST: frozenset[str] = frozenset({
    "wait", "memory_add", "read_file", "list_files", "list_dir",
    "search_files", "search_code", "list_directory", "find_files",
    "grep_search", "search_in_code", "view_file_outline",
    "mail_list_accounts", "mail_inbox", "mail_check", "memory_search",
    "mail_account_upsert",
    "get_lumena_config", "get_system_info", "health_check",
    "get_weather", "get_time", "provider_info",
    "list_image_models", "ionos_list_sites", "ionos_list_files",
    "discord_list_guilds",
    "list_document_models",
})

# ── Guard 5 — outils d'exploration ────────────────────────────────────────────
# Ces outils ne peuvent PAS auto-avancer une tâche dont le premier mot est un
# verbe d'action métier.
_EXPLORATION_TOOLS_STRICT: frozenset[str] = frozenset({
    "run_command", "run_shell", "exec_command",
    "list_directory", "find_files", "list_files", "list_dir",
    "grep_search", "search_in_code", "search_files", "search_code",
    "read_file", "view_file_outline", "parallel_tools",
    "list_document_models",
})

# ── Verbes d'action métier ─────────────────────────────────────────────────────
_BUSINESS_ACTION_STARTERS: frozenset[str] = frozenset({
    "déléguer", "deleguer", "delegate",
    "corriger", "correct", "fix",
    "modifier", "modify",
    "envoyer", "send",
    "créer", "creer", "create",
    "générer", "generer", "generate",
    "déployer", "deployer", "deploy",
    "écrire", "ecrire", "write",
    "publier", "publish",
    "supprimer", "delete",
    "construire", "build",
    "installer", "install",
    "implémenter", "implementer", "implement",
})


def _normalize_guard_token(text: str) -> str:
    """Normalise un verbe de tâche pour les guards de progression."""
    token = (text or "").strip().lower()
    if not token:
        return ""
    try:
        repaired = token.encode("latin1").decode("utf-8")
        if repaired:
            token = repaired.lower()
    except Exception:
        pass
    token = unicodedata.normalize("NFKD", token).encode("ascii", "ignore").decode("ascii")
    return token.strip()


_BUSINESS_ACTION_STARTERS_NORMALIZED: frozenset[str] = frozenset(
    _normalize_guard_token(v) for v in _BUSINESS_ACTION_STARTERS
)

# ── Verify-task gate (Phase 1) ─────────────────────────────────────────────────
# Mots-clés qui signalent qu'une tâche exige une preuve réelle d'exécution.
_VERIFY_TASK_KEYWORDS: frozenset[str] = frozenset({
    "vérif", "verif", "fonctionnel", "fonctionne",
    "testé", "testee", "teste", "prêt", "pret",
    "présent", "present", "démontr", "demonstr",
    "valide", "valider", "confirme", "confirmer",
    "accessible", "opérationnel", "operationnel",
})

# Keywords ambigus dont l'expansion par substring matche un NOM de
# livrable plutôt qu'un verbe de vérification. Pour ces keywords, on
# exige une word boundary stricte (mot entier) dans `is_verify_task`.
# Si "Generer le PDF de presentation" arrivait avant la chaîne MCP/post-
# CodeAgent, le substring naïf "present" matchait "presentation" et la
# tâche métier "Generer le PDF" était traitée comme une vérif → ReAct
# pouvait finaliser prématurément.
_VERIFY_KW_STRICT_BOUNDARY: frozenset[str] = frozenset({
    "present", "présent",   # ↔ "presentation", "présentation"
    "valide",               # ↔ "validation" (peut être un livrable)
    "confirme",             # ↔ "confirmation" (peut être un livrable)
})

# Outils Phase 1 (flat-list) — conservés pour backward compat et Garde 3.
_VERIFY_PROOF_TOOLS: frozenset[str] = frozenset({
    "run_command", "run_shell", "exec_command",
    "health_check", "web_fetch", "browser_navigate",
    "browser_get_content", "browser_dom_state", "run_tests",
})

# Marqueurs d'observation Phase 1 — conservés pour backward compat.
_VERIFY_OBS_PROOF_MARKERS: tuple = (
    "200", "listening", "running", "started", "accessible",
    "http", "localhost", "port", "✅", "passed", "success",
    "réussi", "reussi", "succes", "succès",
    "tableau de bord", "dashboard", "bienvenue", "welcome",
)

# ── Observation failure / success markers ─────────────────────────────────────
_FAIL_MARKERS: tuple = (
    "introuvable", "échoué", "echoue", "erreur", "error",
    "failed", "not found", "impossible", "⛔",
    "timeout commande", "timed out", "timeout:",
)
_SUCCESS_OVERRIDE: tuple = (
    "open", "ouvert", "✅", "succès", "succes", "accessible", "réussi", "reussi",
)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Proof Capabilities
# ═══════════════════════════════════════════════════════════════════════════════

class ProofCapability(str, Enum):
    """14 capabilities de preuve, mappées depuis les catégories sémantiques.

    Architecture scalable : un nouvel outil hérite de la capability de sa
    catégorie — aucune règle outil-par-outil à écrire dans la majorité des cas.
    Les overrides (_TOOL_CAPABILITY_OVERRIDES) couvrent uniquement les
    divergences importantes entre catégorie et outil spécifique.
    """
    FILE_READ            = "file_read"            # lecture seule — jamais preuve
    FILE_WRITE           = "file_write"            # création / écriture fichier
    PROJECT_CREATE       = "project_create"        # scaffold projet complet
    PROCESS_LAUNCH       = "process_launch"        # shell / processus
    HTTP_PROBE           = "http_probe"            # HTTP fetch / health check
    BROWSER_PROBE        = "browser_probe"         # navigation Playwright
    TEST_EXECUTION       = "test_execution"        # runner de tests
    DOC_ARTIFACT         = "doc_artifact"          # PDF / DOCX / XLSX générés
    MESSAGE_SEND         = "message_send"          # mail / Discord / Telegram / WA
    PAYMENT_MUTATION     = "payment_mutation"      # Stripe create/update
    DEPLOY_MUTATION      = "deploy_mutation"       # GitHub push / IONOS deploy
    EXT_RESOURCE_CREATE  = "ext_resource_create"   # Notion page, ressource externe
    GENERIC_READONLY     = "generic_readonly"      # lecture / info — jamais preuve
    GENERIC_MUTATION     = "generic_mutation"      # mutations autres catégories


# Capabilities qui ne constituent jamais une preuve de vérification.
_NON_PROOF_CAPABILITIES: frozenset[ProofCapability] = frozenset({
    ProofCapability.FILE_READ,
    ProofCapability.GENERIC_READONLY,
})


# ── Niveau 1 → Niveau 2 : catégorie sémantique → capabilities par défaut ──────
# La clé est le nom de catégorie sémantique (tool_categories._CONTRACTS).
# Ajouter un outil dans une catégorie existante → aucune règle à modifier ici.
_CATEGORY_CAPABILITIES: Dict[str, frozenset] = {
    "files":         frozenset({ProofCapability.FILE_WRITE}),
    "system":        frozenset({ProofCapability.PROCESS_LAUNCH}),
    "web":           frozenset({ProofCapability.HTTP_PROBE}),
    # Phase D : `mcp_loop_integration` fusionne dans `mcp`. Entree conservee
    # en back-compat pour les anciens callers qui passent encore l'ancien nom.
    "mcp_loop_integration": frozenset({ProofCapability.GENERIC_MUTATION}),
    "mcp":           frozenset({ProofCapability.GENERIC_MUTATION}),
    "data":          frozenset({ProofCapability.GENERIC_READONLY}),  # data.gouv/SIRENE/géo/analyse : readonly par défaut ; seuls download/export prouvent (overrides)
    "memory":        frozenset({ProofCapability.GENERIC_READONLY}),
    "browser":       frozenset({ProofCapability.BROWSER_PROBE}),
    "computer_use":  frozenset({ProofCapability.GENERIC_MUTATION}),
    "agents":        frozenset({ProofCapability.GENERIC_MUTATION}),
    "peers":         frozenset({ProofCapability.GENERIC_MUTATION}),
    "communication": frozenset({ProofCapability.MESSAGE_SEND}),
    "documents":     frozenset({ProofCapability.DOC_ARTIFACT}),
    "media":         frozenset({ProofCapability.DOC_ARTIFACT}),
    "project":       frozenset({ProofCapability.PROJECT_CREATE}),
    "git":           frozenset({ProofCapability.GENERIC_MUTATION}),
    "github":        frozenset({ProofCapability.DEPLOY_MUTATION}),
    "autonomy":      frozenset({ProofCapability.GENERIC_READONLY}),
    # missions : create/cancel sont des mutations (création/annulation d'un TaskRecord) → comptent
    # au ledger (preuve avant FINAL). Les lectures (list/status/result) sont overridées ci-dessous.
    "missions":      frozenset({ProofCapability.GENERIC_MUTATION}),
    "security":      frozenset({ProofCapability.GENERIC_READONLY}),
    "network":       frozenset({ProofCapability.HTTP_PROBE}),
    "platform":      frozenset({ProofCapability.GENERIC_MUTATION}),
    "codebase":      frozenset({ProofCapability.GENERIC_READONLY}),
    # ── Phase 2 mono-catégorie : catégories sorties des contrats fourre-tout ──
    # Héritent de la capability du parent (communication/media/platform/system/
    # files) — iso, SAUF spotify (lecture/contrôle audio → mutation, pas un doc).
    # stripe/ionos restent couverts par _MODULE_CAPABILITIES (PAYMENT/DEPLOY).
    "mail":          frozenset({ProofCapability.MESSAGE_SEND}),
    "discord":       frozenset({ProofCapability.MESSAGE_SEND}),
    "social":        frozenset({ProofCapability.MESSAGE_SEND}),
    "image":         frozenset({ProofCapability.DOC_ARTIFACT}),
    "video":         frozenset({ProofCapability.DOC_ARTIFACT}),
    "spotify":       frozenset({ProofCapability.GENERIC_MUTATION}),
    "ide":           frozenset({ProofCapability.GENERIC_MUTATION}),
    "automation":    frozenset({ProofCapability.GENERIC_MUTATION}),
    "notion":        frozenset({ProofCapability.GENERIC_MUTATION}),
    "lsp":           frozenset({ProofCapability.GENERIC_MUTATION}),  # iso platform (Phase 3 ajustera)
    "skills":        frozenset({ProofCapability.PROCESS_LAUNCH}),    # iso system (Phase 3 ajustera)
    "custom":        frozenset({ProofCapability.PROCESS_LAUNCH}),
    "website":       frozenset({ProofCapability.FILE_WRITE}),
}


# ── Niveau 2 override — exceptions outil-spécifiques ─────────────────────────
# UNIQUEMENT pour les outils qui divergent de la capability par défaut de
# leur catégorie. Cette liste doit rester COURTE (< 40 entrées).
# La règle générale est portée par _CATEGORY_CAPABILITIES.
_TOOL_CAPABILITY_OVERRIDES: Dict[str, frozenset] = {
    # missions category — lectures (create/cancel restent MUTATION via la catégorie)
    "list_missions":         frozenset({ProofCapability.GENERIC_READONLY}),
    "mission_status":        frozenset({ProofCapability.GENERIC_READONLY}),
    "mission_result":        frozenset({ProofCapability.GENERIC_READONLY}),
    # files category — lecture vs écriture
    "read_file":             frozenset({ProofCapability.FILE_READ}),
    "list_files":            frozenset({ProofCapability.GENERIC_READONLY}),
    "list_dir":              frozenset({ProofCapability.GENERIC_READONLY}),
    "list_directory":        frozenset({ProofCapability.GENERIC_READONLY}),
    "find_files":            frozenset({ProofCapability.GENERIC_READONLY}),
    "search_files":          frozenset({ProofCapability.GENERIC_READONLY}),
    "search_code":           frozenset({ProofCapability.GENERIC_READONLY}),
    "grep_search":           frozenset({ProofCapability.GENERIC_READONLY}),
    "view_file_outline":     frozenset({ProofCapability.GENERIC_READONLY}),
    # system category — run_command peut aussi sonder des ports/HTTP
    "run_command":           frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE}),
    "run_shell":             frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE}),
    "exec_command":          frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE}),
    "run_tests":             frozenset({ProofCapability.TEST_EXECUTION}),
    "health_check":          frozenset({ProofCapability.HTTP_PROBE}),
    # web category — web_fetch est un HTTP_PROBE
    "web_fetch":             frozenset({ProofCapability.HTTP_PROBE}),
    # browser category — outils de sondage navigateur
    "browser_navigate":      frozenset({ProofCapability.BROWSER_PROBE, ProofCapability.HTTP_PROBE}),
    "browser_dom_state":     frozenset({ProofCapability.BROWSER_PROBE}),
    "browser_get_content":   frozenset({ProofCapability.BROWSER_PROBE, ProofCapability.HTTP_PROBE}),
    "browser_click_index":   frozenset({ProofCapability.GENERIC_MUTATION, ProofCapability.BROWSER_PROBE}),
    "browser_type_index":    frozenset({ProofCapability.GENERIC_MUTATION, ProofCapability.BROWSER_PROBE}),
    # LOT Z19 — choisir dans un <select> est une interaction metier au meme
    # titre que saisir ou cliquer. Sans cette ligne, une mission qui remplit un
    # formulaire a listes deroulantes ne prouverait rien au sens de Z16.
    "browser_select_index":  frozenset({ProofCapability.GENERIC_MUTATION, ProofCapability.BROWSER_PROBE}),
    "browser_click":         frozenset({ProofCapability.GENERIC_MUTATION, ProofCapability.BROWSER_PROBE}),
    "browser_click_smart":   frozenset({ProofCapability.GENERIC_MUTATION, ProofCapability.BROWSER_PROBE}),
    "browser_verify_local_project": frozenset({ProofCapability.BROWSER_PROBE, ProofCapability.HTTP_PROBE}),
    "process_status":        frozenset({ProofCapability.PROCESS_LAUNCH}),
    # git deploy is a real exception from the generic git category
    "git_push_pull":         frozenset({ProofCapability.DEPLOY_MUTATION}),
    # skills / config_manager — module "system" (défaut PROCESS_LAUNCH) mais
    # ces outils sont de la pure introspection read-only.
    "get_lumena_config":     frozenset({ProofCapability.GENERIC_READONLY}),
    "explain_lumena_config": frozenset({ProofCapability.GENERIC_READONLY}),
    "list_skills":           frozenset({ProofCapability.GENERIC_READONLY}),
    "get_my_capabilities":   frozenset({ProofCapability.GENERIC_READONLY}),
    "list_backups":          frozenset({ProofCapability.GENERIC_READONLY}),
    "read_own_code":         frozenset({ProofCapability.GENERIC_READONLY}),
    "read_skill_reference":  frozenset({ProofCapability.GENERIC_READONLY}),
    "pip_check":             frozenset({ProofCapability.GENERIC_READONLY}),
    "search_in_code":        frozenset({ProofCapability.GENERIC_READONLY}),
    # MCP loop integration: capability lookup is pure read-only; ticket request
    # mutates only the approval queue pending state.
    "request_mcp_capability": frozenset({ProofCapability.GENERIC_READONLY}),
    "request_mcp_ticket":     frozenset({ProofCapability.GENERIC_MUTATION}),
    "run_mcp_autonomy":       frozenset({ProofCapability.GENERIC_MUTATION}),
    "resume_mcp_task":        frozenset({ProofCapability.GENERIC_READONLY}),
    # ── IONOS BDD read-only : le module ionos défaut DEPLOY_MUTATION, mais ces
    # outils sont strictement en lecture (config non sensible, statut, listes,
    # ping). Ils ne doivent PAS exiger une mutation pour conclure un FINAL.
    "ionos_db_get_config":            frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_bridge_status":         frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_write_config":      frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_delete_config":     frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_sandbox_config":    frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_restore_config":    frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_react_write_config":  frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_get_react_delete_config": frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_list_tables":           frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_describe_table":        frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_select":                frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_list_snapshots":        frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_db_list_pending_actions":  frozenset({ProofCapability.GENERIC_READONLY}),
    "ionos_test_site_database":       frozenset({ProofCapability.GENERIC_READONLY}),
    # ── catégorie data (data.gouv / SIRENE / géo / workbench) ──────────────
    # Défaut catégorie = GENERIC_READONLY. Seuls les outils qui PRODUISENT un
    # fichier vérifiable sur disque comptent comme preuve (FILE_WRITE).
    "datagouv_download_resource":  frozenset({ProofCapability.FILE_WRITE}),
    "data_export":                 frozenset({ProofCapability.FILE_WRITE}),
    # explicitement read-only (recherche / lecture / analyse en mémoire) :
    "datagouv_search":             frozenset({ProofCapability.GENERIC_READONLY}),
    "datagouv_get_dataset":        frozenset({ProofCapability.GENERIC_READONLY}),
    "sirene_search_company":       frozenset({ProofCapability.GENERIC_READONLY}),
    "sirene_get_by_siret":         frozenset({ProofCapability.GENERIC_READONLY}),
    "geo_search_address":          frozenset({ProofCapability.GENERIC_READONLY}),
    "geo_reverse":                 frozenset({ProofCapability.GENERIC_READONLY}),
    "geo_commune_info":            frozenset({ProofCapability.GENERIC_READONLY}),
    "data_profile_file":           frozenset({ProofCapability.GENERIC_READONLY}),
    "data_filter_rows":            frozenset({ProofCapability.GENERIC_READONLY}),
    "data_aggregate":              frozenset({ProofCapability.GENERIC_READONLY}),
    "data_unique_values":          frozenset({ProofCapability.GENERIC_READONLY}),
    "data_join":                   frozenset({ProofCapability.GENERIC_READONLY}),
    # ── P2P : confier/envoyer une mission à un pair = un ENVOI (DELIVERY) ──
    # Défaut catégorie 'peers' = GENERIC_MUTATION, qui ne satisfait pas une tâche
    # « envoyer la mission… » (kind DELIVERY → MESSAGE_SEND) → le PlanGuard bloquait
    # le FINAL. On accorde MESSAGE_SEND (l'accusé « ✅ Mission lancée » fait preuve).
    "submit_peer_task":        frozenset({ProofCapability.MESSAGE_SEND, ProofCapability.GENERIC_MUTATION}),
    "peer_team_request":       frozenset({ProofCapability.MESSAGE_SEND, ProofCapability.GENERIC_MUTATION}),
    "run_peer_task_sync":      frozenset({ProofCapability.MESSAGE_SEND, ProofCapability.GENERIC_MUTATION}),
    "orchestrate_peer_request": frozenset({ProofCapability.MESSAGE_SEND, ProofCapability.GENERIC_MUTATION}),
    "delegate_to_peer":        frozenset({ProofCapability.MESSAGE_SEND, ProofCapability.GENERIC_MUTATION}),
}


# Module-level exceptions stay explicit and small.
_MODULE_CAPABILITIES: Dict[str, frozenset] = {
    "stripe": frozenset({ProofCapability.PAYMENT_MUTATION}),
    "ionos": frozenset({ProofCapability.DEPLOY_MUTATION}),
}


# ── Niveau 3 : VerificationKind ───────────────────────────────────────────────

class VerificationKind(str, Enum):
    """7 kinds de vérification, détectés depuis la description de tâche.

    Le kind détermine quelles capabilities sont acceptées comme preuve.
    """
    WEB_APP    = "web_app"    # serveur web / app accessible
    API        = "api"        # endpoint HTTP, REST/GraphQL
    SCRIPT     = "script"     # exécution de script / commande
    DOCUMENT   = "document"   # fichier/doc créé et accessible
    DELIVERY   = "delivery"   # message envoyé (mail, Discord, Telegram…)
    PAYMENT    = "payment"    # mutation Stripe / paiement
    DEPLOYMENT = "deployment" # déploiement (IONOS, GitHub push…)
    GENERIC    = "generic"    # fallback strict — toujours appliqué si non détecté


# Mots-clés pour détecter le kind depuis la description de tâche.
# Les checks sont évalués dans l'ordre — le premier match gagne.
_KIND_DETECT: tuple = (
    # Payment — prioritaire (évite que "paiement fonctionnel" → WEB_APP)
    (VerificationKind.PAYMENT,    ("stripe", "paiement", "payment", "facture", "invoice", "checkout", "prix stripe")),
    # Delivery
    (VerificationKind.DELIVERY,   ("envoy", "send", "message envoyé", "telegram", "discord envoy", "mail envoyé", "whatsapp")),
    # Deployment
    (VerificationKind.DEPLOYMENT, ("déploy", "deploy", "production", "github push", "ionos deploy", "mise en prod")),
    # Document
    (VerificationKind.DOCUMENT,   ("document créé", "rapport créé", "pdf créé", "fichier rapport", "docx", "xlsx", "excel créé")),
    # Web app (large overlap — check after more specific ones)
    (VerificationKind.WEB_APP,    ("site", "serveur", "server", "app web", "application web", "localhost", "port", "http accessible", "web app", "dashboard", "jeu web")),
    # API
    (VerificationKind.API,        ("api", "endpoint", "rest", "graphql", "swagger")),
    # Script
    (VerificationKind.SCRIPT,     (" script", "le script", "un script", "commande exécutée", "programme", "exécution du script")),
)


# Capabilities acceptées comme preuve pour chaque kind.
# GENERIC est volontairement large mais reste strict sur les marqueurs.
_KIND_PROOF_CAPABILITIES: Dict[str, frozenset] = {
    VerificationKind.WEB_APP:    frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE, ProofCapability.BROWSER_PROBE, ProofCapability.TEST_EXECUTION}),
    VerificationKind.API:        frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE, ProofCapability.TEST_EXECUTION}),
    VerificationKind.SCRIPT:     frozenset({ProofCapability.PROCESS_LAUNCH, ProofCapability.TEST_EXECUTION}),
    VerificationKind.DOCUMENT:   frozenset({ProofCapability.DOC_ARTIFACT, ProofCapability.FILE_WRITE}),
    VerificationKind.DELIVERY:   frozenset({ProofCapability.MESSAGE_SEND}),
    VerificationKind.PAYMENT:    frozenset({ProofCapability.PAYMENT_MUTATION}),
    VerificationKind.DEPLOYMENT: frozenset({ProofCapability.DEPLOY_MUTATION, ProofCapability.PROCESS_LAUNCH}),
    VerificationKind.GENERIC:    frozenset({
        ProofCapability.PROCESS_LAUNCH, ProofCapability.HTTP_PROBE,
        ProofCapability.BROWSER_PROBE, ProofCapability.TEST_EXECUTION,
        ProofCapability.MESSAGE_SEND, ProofCapability.PAYMENT_MUTATION,
        ProofCapability.DEPLOY_MUTATION, ProofCapability.DOC_ARTIFACT,
        ProofCapability.FILE_WRITE, ProofCapability.PROJECT_CREATE,
        ProofCapability.EXT_RESOURCE_CREATE, ProofCapability.GENERIC_MUTATION,
    }),
}

_NON_RUNTIME_GENERIC_VERIFY_CAPABILITIES: frozenset = frozenset({
    ProofCapability.FILE_WRITE,
    ProofCapability.PROJECT_CREATE,
    ProofCapability.EXT_RESOURCE_CREATE,
    ProofCapability.GENERIC_MUTATION,
})


# Marqueurs d'observation par capability — doit correspondre à une preuve réelle,
# pas juste un accusé de réception. Jamais de marqueurs pour FILE_READ/GENERIC_READONLY.
_CAPABILITY_OBS_MARKERS: Dict[str, tuple] = {
    ProofCapability.PROCESS_LAUNCH: (
        "started", "running", "listening", "port", "launched", "démarré",
        "✅", "exécuté", "exit 0", "code 0",
    ),
    ProofCapability.HTTP_PROBE: (
        "200", "http", "localhost", "accessible", "success", "✅",
        "ok", "réussi", "reussi",
    ),
    ProofCapability.BROWSER_PROBE: (
        "✅", "page", "tableau de bord", "dashboard", "bienvenue",
        "welcome", "loaded", "200", "accessible",
    ),
    ProofCapability.TEST_EXECUTION: (
        "passed", "success", "✅", "tests", "ok", "réussi",
        "0 failed", "all passed",
    ),
    ProofCapability.DOC_ARTIFACT: (
        "créé", "generated", "✅", "fichier", "file",
        "pdf", "docx", "ok", "created", "saved",
    ),
    ProofCapability.FILE_WRITE: (
        "créé", "written", "✅", "fichier", "ok", "saved", "écrit",
    ),
    ProofCapability.PROJECT_CREATE: (
        "✅", "créé", "project", "projet", "scaffold", "generated", "created",
    ),
    ProofCapability.MESSAGE_SEND: (
        "envoyé", "sent", "✅", "delivered", "ok",
        "message_id", "message id", "msg_", "id:",
        # P2P — accusé de délégation de mission à un pair
        "mission bien lancée", "mission lancée", "réf. ta-", "ref. ta-",
    ),
    ProofCapability.PAYMENT_MUTATION: (
        "✅", "créé", "stripe", "success", "created",
        "prod_", "price_", "cs_", "cus_", "sub_", "in_",
    ),
    ProofCapability.DEPLOY_MUTATION: (
        "✅", "pushed", "deployed", "success", "commit",
        "branch", "merged",
    ),
    ProofCapability.EXT_RESOURCE_CREATE: (
        "✅", "created", "id", "success",
    ),
    ProofCapability.GENERIC_MUTATION: (
        "✅", "success", "ok", "done", "completed",
    ),
    # Non-proof — pas de marqueurs
    ProofCapability.FILE_READ:        (),
    ProofCapability.GENERIC_READONLY: (),
}


# ── API publique Phase 2 ───────────────────────────────────────────────────────

def get_tool_capabilities(
    tool_name: str,
    module_category: str = "",
    semantic_category: str = "",
) -> frozenset:
    """Retourne les ProofCapabilities d'un outil.

    Priorite :
    1. Override outil-spécifique (_TOOL_CAPABILITY_OVERRIDES)
    2. Module category explicite (_MODULE_CAPABILITIES)
    3. Catégorie sémantique fournie (_CATEGORY_CAPABILITIES)
    4. Fallback conservateur : GENERIC_READONLY (= pas de preuve possible)

    Ce fallback conservateur garantit que les nouveaux outils non encore
    référencés ne peuvent pas valider une tâche de vérification par erreur.
    """
    if tool_name in _TOOL_CAPABILITY_OVERRIDES:
        return _TOOL_CAPABILITY_OVERRIDES[tool_name]
    if module_category and module_category in _MODULE_CAPABILITIES:
        return _MODULE_CAPABILITIES[module_category]
    if semantic_category:
        return _CATEGORY_CAPABILITIES.get(
            semantic_category,
            frozenset({ProofCapability.GENERIC_READONLY}),
        )
    return frozenset({ProofCapability.GENERIC_READONLY})


def tool_capabilities_are_known_readonly(
    tool_name: str,
    module_category: str = "",
    semantic_category: str = "",
) -> bool:
    """Verdict guard-safe : l'outil est-il un read-only CONNU ?

    Contrairement à `get_tool_capabilities`, qui retourne le fallback
    conservateur GENERIC_READONLY pour tout outil non référencé, ce helper
    distingue « capability connue read-only » de « fallback inconnu ».

    Un outil/catégorie inconnu n'est JAMAIS considéré read-only : il ne doit
    pas pouvoir désarmer un guard anti-hallucination par erreur.

    Retourne True uniquement si une source explicite (override outil, module,
    ou catégorie sémantique) résout l'outil ET que ses capabilities sont
    toutes read-only (⊆ _NON_PROOF_CAPABILITIES).
    """
    if tool_name in _TOOL_CAPABILITY_OVERRIDES:
        caps = _TOOL_CAPABILITY_OVERRIDES[tool_name]
    elif module_category and module_category in _MODULE_CAPABILITIES:
        caps = _MODULE_CAPABILITIES[module_category]
    elif semantic_category and semantic_category in _CATEGORY_CAPABILITIES:
        caps = _CATEGORY_CAPABILITIES[semantic_category]
    else:
        # Aucune source explicite — inconnu, donc non bypassable.
        return False
    return caps <= _NON_PROOF_CAPABILITIES


def detect_verification_kind(task_desc: str) -> VerificationKind:
    """Infère le VerificationKind depuis la description de tâche.

    Retourne GENERIC si aucun kind plus précis n'est détecté.
    """
    desc_l = task_desc.lower()
    for kind, keywords in _KIND_DETECT:
        if any(kw in desc_l for kw in keywords):
            return kind
    return VerificationKind.GENERIC


def _is_runtime_status_task_desc(task_desc: str) -> bool:
    desc_l = (task_desc or "").lower()
    runtime_markers = (
        "serveur", "server ", "server:", "localhost", "127.0.0.1", "::1",
        "port ", "running", "tourne", "arriere-plan", "arrière-plan",
        "processus", "statut",
    )
    return any(marker in desc_l for marker in runtime_markers)


def _requires_browser_probe_task_desc(task_desc: str) -> bool:
    desc_l = (task_desc or "").lower()
    browser_markers = (
        "navigateur", "browser", "playwright", "dom", "screenshot",
        "capture", "interactions", "interaction", "cliquer", "click",
    )
    return any(marker in desc_l for marker in browser_markers)


def has_sufficient_proof(
    tool_name: str,
    observation: str,
    task_desc: str = "",
    module_category: str = "",
    semantic_category: str = "",
) -> bool:
    """Phase 2 — vérification de preuve capability-aware et kind-aware.

    True si l'outil a au moins une capability acceptée pour le kind de vérification
    ET l'observation contient un marqueur confirmant cette capability.

    Args:
        tool_name:        nom de l'outil appelé.
        observation:      texte de l'observation retournée par l'outil.
        task_desc:        description de la tâche à valider (pour détecter le kind).
                          Si vide → kind=GENERIC (comportement Phase 1 conservé).
        semantic_category: catégorie sémantique de l'outil (optionnel, améliore
                           la résolution pour les outils non-overridés).

    Returns:
        True si la preuve est suffisante, False sinon.
    """
    failed, overridden = classify_observation(observation)
    if failed and not overridden:
        return False
    # P2P : une délégation de mission à un pair est prouvée par l'ACCUSÉ du pair
    # (« ✅ Mission lancée, réf. ta-… »), indépendamment du kind détecté. Le travail
    # s'exécute à distance/async : on ne peut pas exiger une preuve d'exécution
    # LOCALE (PROCESS_LAUNCH) alors que l'objectif parle de « coder un script ».
    # Sans ça, le PlanGuard bloque le FINAL → l'agent re-délègue en boucle (doublons).
    if is_peer_delegation_success(tool_name, observation):
        return True
    caps = get_tool_capabilities(tool_name, module_category, semantic_category)
    kind = detect_verification_kind(task_desc)
    if tool_name == "process_status" and not _is_runtime_status_task_desc(task_desc):
        return False
    requires_browser_probe = _requires_browser_probe_task_desc(task_desc)
    allowed_caps = _KIND_PROOF_CAPABILITIES[kind]
    obs_l = (observation or "").lower()

    for cap in caps:
        if cap in _NON_PROOF_CAPABILITIES:
            continue
        if cap not in allowed_caps:
            continue
        if requires_browser_probe and cap != ProofCapability.BROWSER_PROBE:
            continue
        if (
            kind == VerificationKind.GENERIC
            and is_verify_task((task_desc or "").lower())
            and cap in _NON_RUNTIME_GENERIC_VERIFY_CAPABILITIES
        ):
            continue
        markers = _CAPABILITY_OBS_MARKERS.get(cap, ())
        if markers and any(m in obs_l for m in markers):
            return True
    return False


# ── P2P — preuve de délégation de mission à un pair ───────────────────────────
# Le nom de tâche contient souvent « via submit_peer_task » → le mot « submit »
# faisait croire au PlanGuard à une soumission de FORMULAIRE (marqueurs jamais
# réunis → FINAL bloqué → l'agent re-soumet en boucle → 429 → doublon local).
# Ici on prouve la délégation par l'ACCUSÉ du pair, indépendamment de ce mot.
_PEER_DELEGATION_TOOLS: frozenset[str] = frozenset({
    "submit_peer_task", "peer_team_request", "run_peer_task_sync",
    "orchestrate_peer_request", "delegate_to_peer",
})
_PEER_DELEGATION_SUCCESS_MARKERS: tuple = (
    "mission bien lancée", "mission lancée", "réf. ta-", "ref. ta-",
    "mission accomplie", "terminé avec succès", "termine avec succes",
)


def is_peer_delegation_success(tool_name: str, observation: str) -> bool:
    """True si `observation` est l'accusé de SUCCÈS d'une délégation à un pair.

    Fail-closed : un échec (« HTTP 429 », « pair inconnu », « ReadTimeout »…) ne
    contient aucun marqueur de succès → False → la tâche n'est pas cochée.
    """
    if tool_name not in _PEER_DELEGATION_TOOLS:
        return False
    obs_l = (observation or "").lower()
    return any(m in obs_l for m in _PEER_DELEGATION_SUCCESS_MARKERS)


# ── Fonctions pures Phase 1 ────────────────────────────────────────────────────

def classify_observation(observation: str) -> tuple[bool, bool]:
    """Analyse une observation et retourne (has_failure, failure_overridden).

    L'appelant doit interpréter : observation_failed = has_failure and not failure_overridden.
    """
    obs_l = (observation or "").lower()
    has_failure = any(fm in obs_l for fm in _FAIL_MARKERS)
    if not has_failure:
        return False, False
    overridden = any(sm in obs_l for sm in _SUCCESS_OVERRIDE)
    return True, overridden


def is_verify_task(desc: str) -> bool:
    """True si la description de tâche exige une preuve réelle d'exécution.

    Matching substring permissif par défaut : "teste" matche "tester",
    "valide" matche "validez", "fonctionne" matche "fonctionnement", etc.
    Cette permissivité permet de couvrir toutes les conjugaisons d'un
    verbe de vérification sans énumérer chaque forme.

    Mais certains keywords courts ont une expansion qui donne un NOM de
    livrable, pas un verbe de vérification :
      - "present"/"présent" → "presentation"/"présentation" (PDF/slides)
      - "valide" → "validation" (peut être un livrable : doc de validation)
      - "confirme" → "confirmation" (peut être un livrable : email de
        confirmation)

    Pour ces keywords ambigus, on exige une **word boundary** stricte
    pour qu'ils ne matchent que comme mot entier — ce qui évite que
    "Generer le PDF de presentation" soit traité comme une tâche de
    vérification et bloque ReAct de continuer vers les tâches métier
    suivantes après CodeAgent.

    Les autres keywords gardent le matching substring (suffixe permissif
    requis pour couvrir les conjugaisons verbales).
    """
    if not desc:
        return False
    for kw in _VERIFY_TASK_KEYWORDS:
        if kw in _VERIFY_KW_STRICT_BOUNDARY:
            # Mot entier obligatoire : ne matche pas les noms-livrables
            # dérivés comme "presentation", "validation", "confirmation".
            if re.search(r"\b" + re.escape(kw) + r"\b", desc):
                return True
        else:
            # Substring permissif : couvre les conjugaisons verbales
            # ("teste" → "tester", "valider" → "validera", etc.).
            if kw in desc:
                return True
    return False


# #3 (2026-06-30) — outils de LECTURE pure pouvant constituer une preuve de
# VÉRIFICATION, mais UNIQUEMENT s'ils relisent un artefact réellement écrit avant.
_FILE_READER_VERIFY_TOOLS = frozenset({"read_file", "read_document"})

# C0.3a (run FrigoZen) — une tâche de vérification qui exige une EXÉCUTION
# (pytest, servir l'app, navigateur) ne peut JAMAIS être satisfaite par une
# relecture : le lead FrigoZen a vu « Vérifier l'intégration, exécuter les
# tests, servir l'app, tester navigateur » cochée par un simple read_file →
# FINALIZE prématuré (ni pytest, ni serveur, ni navigateur, ni publication).
_EXECUTION_PROOF_RE = re.compile(
    r"pytest|ex[ée]cut|\bservi|serveur|navigateur|browser|\btests?\b|run tests",
    re.IGNORECASE,
)


def verify_satisfied_by_artifact_read(
    tool_name: str, task_desc: str, *, artifact_reread: bool
) -> bool:
    """True si une tâche de VÉRIFICATION est légitimement satisfaite par une LECTURE.

    Condition stricte : la lecture (`read_file`/`read_document`) doit relire un
    artefact RÉELLEMENT écrit avant (`artifact_reread`, prouvé via l'execution_ledger
    côté appelant — lien read→write). Sinon False : le garde-fou « lecture seule ≠
    preuve » (plan_evidence: FILE_READ → aucun marqueur) reste actif pour toute autre
    lecture. Ferme le SKIP éternel de « Vérifier le fichier final » après write_file +
    read_file, sans ouvrir la porte à un crédit de lecture arbitraire.

    C0.3a : si la description exige une preuve d'EXÉCUTION (pytest/servir/
    navigateur…), la relecture ne suffit jamais — seule l'exécution réelle
    (has_sufficient_proof côté appelant) peut créditer la tâche.
    """
    if not artifact_reread:
        return False
    if tool_name not in _FILE_READER_VERIFY_TOOLS:
        return False
    desc_lower = (task_desc or "").lower()
    if _EXECUTION_PROOF_RE.search(desc_lower):
        return False
    # is_verify_task attend une description en minuscules (les call sites react
    # passent déjà desc_lower) → on normalise ici pour être robuste au caller.
    return is_verify_task(desc_lower)


def has_verify_proof(tool_name: str, observation: str) -> bool:
    """API Phase 1 — backward compatible. Délègue à has_sufficient_proof.

    Comportement identique à la Phase 1 : task_desc="" → kind=GENERIC.
    Conservé pour les call sites qui n'ont pas accès à la description de tâche
    (ex : Garde 3 dans _update_plan_progress).
    """
    return has_sufficient_proof(tool_name, observation, "")


def reconcile_delegate_report(
    task_plan: List[TaskItem],
    obs_text: str,
    iteration: int,
) -> int:
    """Réconcilie le plan après un succès delegate_task.

    Matching sémantique : 2+ mots significatifs (>4 chars) de la description
    présents dans l'observation → tâche marquée complétée.
    Les tâches de vérification sont exclues.
    """
    if not task_plan:
        return 0

    obs_lower = obs_text.lower()
    marked = 0

    for task in task_plan:
        if task.completed:
            continue
        desc_lower = task.description.lower()

        if is_verify_task(desc_lower):
            logger.debug(
                "[PLAN] Réconciliation delegate: '{}' ignorée (tâche vérification — preuve insuffisante)",
                task.description,
            )
            continue

        _words = [w for w in desc_lower.split() if len(w) > 4]
        _word_hits = sum(1 for w in _words if w in obs_lower)
        if _word_hits >= 2:
            task.completed = True
            task.completed_at_iteration = iteration
            task.completed_by_tool = "delegate_task:report"
            task.completion_status = TaskCompletionStatus.CREATED
            marked += 1
            logger.debug(
                "[PLAN] Réconciliation delegate: '{}' marquée via rapport ({} mots matchés)",
                task.description, _word_hits,
            )

    if marked:
        logger.info(
            "[PLAN] Réconciliation delegate_task: {} tâche(s) marquée(s) completed (iter {})",
            marked, iteration,
        )

    return marked


# Tâches de RÉCUPÉRATION / FUSION de résultats : réalisées par delegate_and_wait +
# l'écriture du livrable cible (pas une action distincte qui resterait à prouver).
_RESULT_AGGREGATION_KW: FrozenSet[str] = frozenset({
    "récupér", "recuper", "fusionn", "consolid", "rassembl", "regroup",
    "agrég", "agreg", "compil", "assembl",
})
# Side-effects EXTERNES : jamais crédités « gratis » par la livraison d'un fichier.
_EXTERNAL_SIDE_EFFECT_KW: FrozenSet[str] = frozenset({
    "mail", "email", "e-mail", "envoy", "expédi", "expedi", "déploi", "deploi",
    "publi", "push", "slack", "discord", "telegram", "tweet", "poster",
})


def reconcile_plan_on_artifact_delivery(
    task_plan: List[TaskItem], *, has_delegation_success: bool, iteration: int,
) -> int:
    """MUTE `task_plan` : réconcilie le plan quand le livrable CIBLE de la mission
    vient d'être confirmé sur disque (deadline_artifact_written posé sur le fichier
    cible extrait de l'objectif). Doctrine « artefact sur disque ⇒ mission réussie » :
    les tâches de DÉLÉGATION (si une délégation a réussi) et de RÉCUPÉRATION/FUSION
    des résultats sont satisfaites — le livrable tangible le prouve.

    NE crédite JAMAIS : les tâches de VÉRIFICATION (réservées à la vraie relecture
    read_file — cf. verify_satisfied_by_artifact_read, #3) ni les side-effects
    EXTERNES non prouvés (mail, push, déploiement, slack…). Retourne le nb marqué.
    (Mutatif, à l'image de reconcile_delegate_report.)"""
    if not task_plan:
        return 0
    # Import paresseux : plan_progress reste stdlib-only (pas de cycle au load).
    from .plan_progress import delegation_task_fulfilled

    marked = 0
    for task in task_plan:
        if task.completed:
            continue
        desc_lower = (task.description or "").lower()
        if is_verify_task(desc_lower):
            continue  # crédit réservé à la relecture réelle (#3)
        if any(kw in desc_lower for kw in _EXTERNAL_SIDE_EFFECT_KW):
            continue  # side-effect externe → jamais bradé par une livraison fichier
        _is_deleg = has_delegation_success and delegation_task_fulfilled(task.description)
        _is_aggreg = any(kw in desc_lower for kw in _RESULT_AGGREGATION_KW)
        if _is_deleg or _is_aggreg:
            task.completed = True
            task.completed_at_iteration = iteration
            task.completed_by_tool = "deadline_artifact_delivery"
            task.completion_status = TaskCompletionStatus.PARTIALLY_VERIFIED
            task.completion_evidence = "livrable cible confirmé sur disque"
            marked += 1

    if marked:
        logger.info(
            "[PLAN] Réconciliation livraison artefact: {} tâche(s) marquée(s) completed (iter {})",
            marked, iteration,
        )

    return marked


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Vérité des statuts de complétion
# ═══════════════════════════════════════════════════════════════════════════════

class TaskCompletionStatus:
    """Statuts normalisés de complétion d'une tâche du plan.

    Distinctions critiques :
      CREATED ≠ VERIFIED : créer un fichier ne prouve pas qu'il fonctionne.
      SENT    : preuve d'envoi effectif (mail, Discord, Telegram…).
      DEPLOYED: preuve de déploiement (git push, IONOS…).
    """
    CREATED            = "created"
    VERIFIED           = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    SENT               = "sent"
    DEPLOYED           = "deployed"
    FAILED             = "failed"
    BLOCKED            = "blocked"
    CANCELLED          = "cancelled"
    UNKNOWN            = ""


# ── Proof typée ────────────────────────────────────────────────────────────────

from dataclasses import dataclass as _dataclass


@_dataclass
class TaskProofDecision:
    """Décision typée sur la preuve d'une tâche du plan."""
    status: str        # "proved" | "not_proved"
    confidence: str    # "strong" | "medium" | "weak"
    evidence_kind: str # "file_exists" | "tool_success" | "stripe_id" | "email_confirmed" | "text_inference"
    evidence_summary: str


# Kinds stricts : bloquent task.completed si preuve absente.
_STRICT_PROOF_KINDS: frozenset = frozenset({
    VerificationKind.DOCUMENT,
    VerificationKind.DELIVERY,
    VerificationKind.PAYMENT,
})


def evaluate_task_proof(
    task_desc: str,
    tool_name: str,
    observation: str,
) -> TaskProofDecision:
    """Retourne une décision typée sur la preuve d'une tâche.

    Wrapper fin autour de has_sufficient_proof() — le système existant à 500 outils
    reste le seul oracle. Cette fonction ne fait qu'enrichir la décision booléenne
    avec un type structuré (confidence, evidence_kind, evidence_summary).
    Ne bloque jamais directement : les gardes existants (_proof_for_task,
    _requires_strict_proof) font déjà le travail dans react.py.
    """
    proved = has_sufficient_proof(tool_name, observation, task_desc)
    kind = detect_verification_kind(task_desc)

    if proved:
        if kind == VerificationKind.PAYMENT:
            raw = observation or ""
            if "plink_" in raw or "checkout.stripe.com" in raw:
                return TaskProofDecision("proved", "strong", "stripe_id", "ID Stripe présent")
        return TaskProofDecision("proved", "medium", "tool_success", f"{tool_name} preuve suffisante")

    # has_sufficient_proof a retourné False
    if kind in _STRICT_PROOF_KINDS:
        return TaskProofDecision("not_proved", "weak", "text_inference", f"preuve insuffisante ({kind.value})")
    # Kind non-strict : on annote weak mais on ne bloque pas (proved=True côté react)
    return TaskProofDecision("proved", "weak", "text_inference", "preuve par défaut")


# Capabilities dont la présence dans la preuve implique une vérification réelle.
_VERIFIED_CAPABILITIES: frozenset = frozenset({
    ProofCapability.HTTP_PROBE,
    ProofCapability.BROWSER_PROBE,
    ProofCapability.TEST_EXECUTION,
    ProofCapability.PROCESS_LAUNCH,
})


def completion_status_for_proof(
    cap: Optional["ProofCapability"],
    was_verify_task: bool,
) -> str:
    """Statut normalisé depuis la capability de preuve utilisée.

    Args:
        cap:             ProofCapability qui a validé la tâche, ou None.
        was_verify_task: True si la tâche était de type vérifier/fonctionnel/testé.
    """
    if cap is None:
        return TaskCompletionStatus.CREATED
    if cap == ProofCapability.MESSAGE_SEND:
        return TaskCompletionStatus.SENT
    if cap == ProofCapability.DEPLOY_MUTATION:
        return TaskCompletionStatus.DEPLOYED
    if was_verify_task and cap in _VERIFIED_CAPABILITIES:
        return TaskCompletionStatus.VERIFIED
    return TaskCompletionStatus.CREATED


def task_completion_status(
    tool_name: str,
    desc_lower: str,
    semantic_category: str = "",
    module_category: str = "",
) -> str:
    """Détermine le statut normalisé de complétion d'une tâche.

    Appelé par react.py au moment où task.completed est mis à True.
    Ne présuppose PAS que la preuve a été vérifiée (c'est le rôle du verify-gate).
    Infère seulement le TYPE de complétion depuis les capabilities de l'outil.
    """
    caps = get_tool_capabilities(tool_name, module_category, semantic_category)
    was_verify = is_verify_task(desc_lower)

    if ProofCapability.MESSAGE_SEND in caps:
        return TaskCompletionStatus.SENT
    if ProofCapability.DEPLOY_MUTATION in caps:
        return TaskCompletionStatus.DEPLOYED
    if was_verify:
        return TaskCompletionStatus.VERIFIED
    return TaskCompletionStatus.CREATED
