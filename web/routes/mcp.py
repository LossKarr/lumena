"""
MCP admin routes — read-only (Phase 20A v4) + Approvals UI mutations (Phase 20B-1)
+ Install lifecycle UI mutations (Phase 20B-2) + Activation lifecycle UI mutations
(Phase 20B-3) + Catalog mutations UI (Phase 20B-4) + AutoApprove patterns CRUD UI
(Phase 20B-5) + Trust score manual update UI (Phase 20B-6) + Hardening MCP
(Phase 21 : observabilité consolidée + validation passive clés + audit integrity
+ coherence runtime + readiness report + RBAC mode lecture seule).

Phase 20A v4 (10 routes GET, read-only) :
  - protégées par verify_admin_token (cohérent avec ionos/system/etc.)
  - tolérantes : si un module MCP n'est pas importable, retournent
    {"available": false, "reason": "not_loaded"} au lieu de raise 500
  - aucun déchiffrement des args (clés Fernet jamais lues)
  - aucun appel d'écriture sur le catalog (add / update / remove)
  - aucun enregistrement de handler dynamique (register / unregister)
  - aucune instanciation de InstallOrchestrator / ActivationService / Orchestrator

Phase 20B-1 (2 routes POST, mutations ApprovalQueue UNIQUEMENT) :
  - POST /api/mcp/approvals/{action_id}/approve
  - POST /api/mcp/approvals/{action_id}/reject (reason obligatoire)
  - kill switch global env LUMENA_MCP_LIVE : si absent/falsy, dry_run forcé
    (aucune mutation queue, aucun marker créé)
  - confirmation côté backend obligatoire (body.confirmed == true)
  - cache marker UUID4 backend (one-shot, TTL 5 min, max 256 entrées) :
    helpers internes uniquement, aucune route GET, aucune fuite ApprovalResult
  - audit UI dédié data/mcp_admin_audit/audit.jsonl
    (actor_token_hash SHA256, jamais token clair, jamais reason raw)
  - error_code court en cas d'échec, jamais message brut d'exception

Watcher : snapshots persistés uniquement. Le live (mémoire+runners actifs)
reste reporté à Phase 20B-2/3/21.

Champs Catalog filtrés côté serveur :
  - notes : JAMAIS sérialisé
  - autres champs publics : exposés

Champs ApprovalQueue filtrés côté serveur :
  - args (chiffrés Fernet) : JAMAIS exposés
  - PendingAction (vue lecture sans args) : exposée
  - ApprovalResult brut : JAMAIS exposé (uniquement marker UUID4)

Phase 20B-2 (2 routes POST, mutations Install lifecycle UNIQUEMENT) :
  - POST /api/mcp/install/propose
  - POST /api/mcp/install/execute (consomme un marker UUID4 émis par 20B-1)
  - mêmes kill switch, confirmation backend, audit UI dédié qu'en 20B-1
  - validation server_id : réplique stricte Phase 14 (regex + Windows reserved),
    SANS importer le helper privé `_validate_server_id` (qui lève CatalogError) ;
    on lève HTTPException 400 server_id_invalid_format à la place
  - confirmation_phrase = server_id exact (case-sensitive, saisie texte côté UI)
  - caller_kind whitelist = {"admin_ui"} uniquement (refus 400 caller_kind_invalid)
  - singleton Catalog distinct du singleton ApprovalQueue (aucun accès à
    `_APPROVAL_QUEUE_SINGLETON._catalog` ou autre attribut privé)
  - execute live = _take_marker one-shot AVANT execute_approved_install ;
    le marker reste consommé même si l'install échoue (audit
    marker_consumed_irrecoverable=true)
  - execute dry_run = ZERO call _take_marker / execute_approved_install
  - aucun subprocess direct dans ce module (l'orchestrator/runner Phase 18 gère)
  - InstallResult brut JAMAIS exposé (whitelist : executed, server_id, status, live_mode)

Hors périmètre Phase 20B-2 (reporté 20B-3 / 20B-4 / 20B-5) :
  - Activation lifecycle
  - Catalog mutations (add / quarantine / remove)
  - Auto-Approve patterns CRUD
  - Trust recompute

Phase 20B-3 (3 routes POST, mutations Activation lifecycle UNIQUEMENT) :
  - POST /api/mcp/activation/propose
  - POST /api/mcp/activation/execute (consomme marker UUID4 émis par 20B-1)
  - POST /api/mcp/activation/deactivate (pas de marker, action de protection)
  - réplique stricte de la doctrine 20B-1/20B-2 : kill switch LUMENA_MCP_LIVE,
    confirmation backend + phrase = server_id exact, marker one-shot avant
    activate, validation croisée args, marker consommé irrécouvrable en cas
    d'échec, audit UI dédié sans fuite
  - 2 nouveaux singletons lifespan : MCPRuntimeWatcher (snapshots inter-requêtes)
    + MCPActivationService (state _running_contexts inter-requêtes)
  - réutilisation Catalog + ApprovalQueue + InstallOrchestrator singletons 20B-1/20B-2
  - resolution registry_writer = runtime Lumena (deps.lumena._tool_registry
    ou deps.lumena.tool_system._tool_registry). Aucune instanciation neuve.
  - runner_factory(server_id, entry) construit MCPInstallSpec depuis
    entry.package_spec (npm/pypi→uv). local et inconnu lèvent ValueError —
    aucune construction de spec avec un transport hors whitelist Phase 5
  - mcp_root = install_orchestrator.install_root (cohérence Phase 18)
  - aucun appel register_dynamic_handler / unregister_dynamic_handler direct
    depuis ce module (passe via MCPActivationService Phase 19)
  - aucun lancement direct de processus externe (Phase 5/19 gèrent)

Hors périmètre Phase 20B-3 (reporté 20B-4 / 20B-5) :
  - Catalog mutations (add / quarantine / remove)
  - Auto-Approve patterns CRUD
  - Trust recompute

Phase 20B-4 (4 routes POST, mutations Catalog UNIQUEMENT) :
  - POST /api/mcp/catalog/add
  - POST /api/mcp/catalog/{server_id}/quarantine
  - POST /api/mcp/catalog/{server_id}/restore
  - POST /api/mcp/catalog/{server_id}/remove
  - aucun nouveau singleton (réutilisation _MCP_SERVER_CATALOG_SINGLETON 20B-2)
  - aucun appel ApprovalQueue.propose/approve/reject (mutations Catalog
    non-gated — admin-only)
  - aucun marker UUID4 consommé / émis
  - aucun appel install_orchestrator, activation_service, runtime_watcher
  - réplique stricte des validators Phase 14 (server_id, display_name,
    package_spec, owner_profile, version, trust_score, notes) sans importer
    les helpers privés (lève HTTPException 400 au lieu de CatalogError)
  - restore : target_status whitelist {"installed"} uniquement (pour ACTIVE,
    passer par Phase 20B-3 activate après restore vers INSTALLED)
  - remove : refusé sur status ACTIVE (force chemin propre deactivate 20B-3)
  - audit UI : package_spec réduit au transport (npm/pypi/local/unknown),
    trust_score réduit à trust_score_set (bool). JAMAIS display_name, version,
    notes, ServerEntry brut

Hors périmètre Phase 20B-4 (reporté 20B-5) :
  - Auto-Approve patterns CRUD
  - Trust recompute (update_trust_score)

Phase 20B-5 (4 routes : 2 GET + 2 POST, mutations AutoApprove UNIQUEMENT) :
  - GET /api/mcp/autoapprove/patterns?profile=...&limit=N
  - GET /api/mcp/autoapprove/patterns/{pattern_id}
  - POST /api/mcp/autoapprove/add
  - POST /api/mcp/autoapprove/{pattern_id}/remove

  Doctrine spécifique 20B-5 (mutations de policy future) :
  - Point central : créer un pattern AutoApprove = créer une autorisation
    FUTURE qui pourra court-circuiter ApprovalQueue. Plus strict que 20B-1/2/3/4.
  - Double opt-in obligatoire : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1.
    Sinon dry-run forcé (0 call add_pattern / remove_pattern).
  - Add : phrase fixe "CREATE-AUTOAPPROVE-PATTERN" (saisie texte)
  - Remove : phrase = pattern_id complet (32 chars uuid4 hex)
  - Aucune route update/PATCH/PUT (Phase 11 immutable : remove + add pour modifier)
  - Pré-validation args_constraints côté web (whitelist 10 clés Phase 11 +
    types stricts + taille/profondeur bornées + max 4096 chars sérialisés)
    puis délégation finale à engine.add_pattern (source de vérité Phase 11).
  - error_code unifié args_constraints_invalid (anti canal latéral)
  - GET list/détail : métadonnées agrégées seulement
    (tool_name_pattern raw, args_constraints raw, caller_kinds_allowed raw —
    JAMAIS exposés)
  - Aucun déchiffrement Fernet côté route (patterns chiffrés Phase 11
    restent chiffrés sur disque)
  - Singleton lifespan AutoApproveEngine obligatoire
  - MCPPolicy importé depuis src.mcp.policy (enum public)
  - Aucun appel ApprovalQueue / Install / Activation / Catalog mutation /
    marker / subprocess dans les handlers AutoApprove

Hors périmètre Phase 20B-5 (reporté 20B-6) :
  - Trust recompute (update_trust_score)

Phase 20B-6 (1 route POST, trust_score manual update UI UNIQUEMENT) :
  - POST /api/mcp/catalog/{server_id}/trust/update

  Doctrine spécifique 20B-6 (mutation de seuil de sécurité) :
  - Point central : modifier trust_score peut indirectement débloquer des
    chaînes d'autorisation futures (notamment via patterns AutoApprove 20B-5).
    Donc double opt-in obligatoire et justification obligatoire.
  - Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1.
    Sinon dry-run forcé (0 call update_trust_score).
  - 20B-6 = manual update (pas un recompute automatique). L'admin saisit
    la nouvelle valeur en connaissance de cause.
  - Aucun import score_package / TrustReport / PackageMetadata côté
    production (la chaîne de scoring Phase 6 reste pure et non sollicitée).
  - Confirmation backend + saisie texte = server_id exact (héritage 20B-4)
  - Justification OBLIGATOIRE (10..256 chars trimés, UTF-8 lisible — pas
    de caractères de contrôle). Audit ne logue que justification_length.
  - trust_score strict : None/absent refusé (différent de Catalog.add_server
    Phase 14 qui accepte None pour la création initiale).
  - Idempotent no-op : si trust_score_proposed == trust_score_current,
    AUCUN call update_trust_score. Audit outcome="noop", idempotent=true.
    Réponse {updated: false, idempotent: true}.
  - Status REMOVED refusé. QUARANTINED autorisé. Autres autorisés.
  - Audit UI étendu : trust_score_old/new exposés (signal de policy non
    secret, cohérent 20B-5 quota_max_per_day). JAMAIS justification raw,
    display_name, package_spec, version, notes, ServerEntry brut,
    CatalogError raw, TrustReport.factors[] (ligne rouge même si on
    n'invoque pas score_package).
  - Réutilisation singleton _MCP_SERVER_CATALOG_SINGLETON 20B-2 (aucun
    nouveau singleton).
  - Aucun appel ApprovalQueue / Install / Activation / Catalog mutations
    autres / AutoApprove / marker / subprocess dans le handler Trust.
  - Aucun update/PUT/PATCH (POST /update pour cohérence body uniquement).

Aucune sous-phase 20B au-delà de 20B-6 prévue : la chaîne UI mutative
est désormais complète (Approvals, Install, Activation, Catalog,
AutoApprove patterns, Trust manual update).

Phase 21 (8 routes GET — hardening MCP, AUCUNE nouvelle mutation) :
  - GET /api/mcp/observability/overview
  - GET /api/mcp/observability/events
  - GET /api/mcp/observability/last-runs
  - GET /api/mcp/keys/status
  - GET /api/mcp/audit-integrity/{component}
  - GET /api/mcp/coherence/check
  - GET /api/mcp/readiness
  - GET /api/mcp/rbac/mode

  Doctrine 21 :
  - Hardening, pas de nouvelle mutation. Aucune nouvelle route POST/PUT/PATCH/DELETE.
  - keys/status STRICTEMENT PASSIF : SecretsService.get uniquement, présence +
    format_valid passif. AUCUN cipher neuf, aucune fabrication cipher
    interne, aucune fabrication hmac key interne, AUCUN round-trip,
    AUCUNE lazy-génération.
  - observability/events SANITIZÉ : whitelist stricte _AUDIT_EVENT_SANITIZED_KEYS.
    Aucun args raw, package_spec, notes, justification (avec accents), tool_name_pattern,
    args_constraints, caller_kinds_allowed, marker raw, token clair, path absolu.
  - Composant admin_ui ajouté à _AUDIT_COMPONENTS (data/mcp_admin_audit/audit.jsonl).
  - coherence/check : rapport SANS auto-fix. Aucune mutation, aucun register/unregister.
  - readiness : agrégat de tout, rapport SANS auto-fix, aucun restart, aucune mutation.
  - rbac/mode : retour passif "admin_only" / "planned". AUCUN changement à
    verify_admin_token (RBAC reporté Phase 22+).
  - Aucun nouveau singleton.
  - Aucune modification src/mcp/*.
  - Aucun import score_package / TrustReport / PackageMetadata.

Phase 21.0 (knobs config) : 6 entrées MCP ajoutées à _CONFIG_SCHEMA via
web/routes/config.py. AUCUNE nouvelle route MCP pour les knobs config —
réutilisation GET /api/config + PUT /api/config existants.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Query

from web.routes.deps import verify_admin_token

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Imports optionnels des composants MCP (tolérants)
# ──────────────────────────────────────────────────────────────────────────────

try:
    from src.mcp.server_catalog import (
        MCPServerCatalog,
        ServerEntry,
        ServerStatus,
    )
    _CATALOG_AVAILABLE = True
except Exception:
    _CATALOG_AVAILABLE = False
    MCPServerCatalog = None  # type: ignore
    ServerEntry = None  # type: ignore
    ServerStatus = None  # type: ignore

try:
    from src.mcp.approval_queue import (
        ApprovalQueue,
        PendingAction,
    )
    _APPROVAL_AVAILABLE = True
except Exception:
    _APPROVAL_AVAILABLE = False
    ApprovalQueue = None  # type: ignore
    PendingAction = None  # type: ignore

try:
    from src.mcp.runtime_watcher import (
        RuntimeWatcher,
        RuntimeSnapshot,
    )
    _WATCHER_AVAILABLE = True
except Exception:
    _WATCHER_AVAILABLE = False
    RuntimeWatcher = None  # type: ignore
    RuntimeSnapshot = None  # type: ignore

try:
    from src.mcp.discovery import MCPDiscoveryService
    _DISCOVERY_AVAILABLE = True
except Exception:
    _DISCOVERY_AVAILABLE = False
    MCPDiscoveryService = None  # type: ignore

try:
    from src.mcp.policy_attributor import PolicyAttributor
    _ATTRIBUTOR_AVAILABLE = True
except Exception:
    _ATTRIBUTOR_AVAILABLE = False
    PolicyAttributor = None  # type: ignore

try:
    # Phase 20B-2 : Install lifecycle
    from src.mcp.install_orchestrator import (
        MCPInstallOrchestrator,
        InstallProposal,
        InstallResult,
    )
    _INSTALL_ORCHESTRATOR_AVAILABLE = True
except Exception:
    _INSTALL_ORCHESTRATOR_AVAILABLE = False
    MCPInstallOrchestrator = None  # type: ignore
    InstallProposal = None  # type: ignore
    InstallResult = None  # type: ignore

try:
    # Phase 20B-3 : Activation lifecycle
    from src.mcp.activation_service import MCPActivationService
    _ACTIVATION_SERVICE_AVAILABLE = True
except Exception:
    _ACTIVATION_SERVICE_AVAILABLE = False
    MCPActivationService = None  # type: ignore

try:
    # Phase 20B-5 : AutoApprove patterns CRUD
    from src.mcp.auto_approve import AutoApproveEngine
    _AUTO_APPROVE_ENGINE_AVAILABLE = True
except Exception:
    _AUTO_APPROVE_ENGINE_AVAILABLE = False
    AutoApproveEngine = None  # type: ignore

try:
    # Phase 20B-5 : enum public MCPPolicy (pas un helper privé)
    from src.mcp.policy import MCPPolicy
    _MCP_POLICY_AVAILABLE = True
except Exception:
    _MCP_POLICY_AVAILABLE = False
    MCPPolicy = None  # type: ignore

try:
    from src.mcp.local_creation_executor import MCPLocalCreationExecutor
    _LOCAL_CREATION_EXECUTOR_AVAILABLE = True
except Exception:
    _LOCAL_CREATION_EXECUTOR_AVAILABLE = False
    MCPLocalCreationExecutor = None  # type: ignore

try:
    from src.utils.paths import DATA_DIR
    _DATA_DIR_AVAILABLE = True
except Exception:
    _DATA_DIR_AVAILABLE = False
    DATA_DIR = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Helpers lazy : instanciation à la demande, jamais singleton
# ──────────────────────────────────────────────────────────────────────────────


def _get_catalog() -> Optional[Any]:
    if not _CATALOG_AVAILABLE:
        return None
    try:
        return MCPServerCatalog()
    except Exception:
        return None


def _get_approval_queue() -> Optional[Any]:
    """Phase 20A : instanciation à la demande pour les routes GET.

    Phase 20B-1 : les routes mutatives utilisent _get_approval_queue_singleton()
    qui retourne l'instance lifespan partagée. Si le singleton est absent
    (module non chargé au boot), fallback ici en lecture seule uniquement.
    """
    if not _APPROVAL_AVAILABLE:
        return None
    try:
        return ApprovalQueue()
    except Exception:
        return None


def _get_approval_queue_singleton() -> Optional[Any]:
    """Phase 20B-1 : singleton lifespan partagé pour les mutations.

    Lit deps._MCP_APPROVAL_QUEUE_SINGLETON (initialisé au startup dans
    web/routes/lifespan.py). Si None (module non importable ou singleton
    pas encore initialisé), retourne None ; les routes mutatives répondent
    {"available": false, "error_code": "queue_unavailable"}.
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_APPROVAL_QUEUE_SINGLETON", None)
    except Exception:
        return None


def _get_catalog_add_orchestrator_singleton() -> Optional[Any]:
    """Phase I-7 : singleton lifespan partagé pour le dispatch catalog_add.

    Lit deps._MCP_CATALOG_ADD_ORCHESTRATOR_SINGLETON. Utilisé par la route
    /approvals/{action_id}/approve pour déclencher execute_approved_catalog_add
    immédiatement après queue.approve() succès (ferme la boucle
    add_mcp → ticket → DECLARED dans le catalog).
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_CATALOG_ADD_ORCHESTRATOR_SINGLETON", None)
    except Exception:
        return None


def _get_watcher() -> Optional[Any]:
    """Phase 20A : watcher en mode snapshots persistés UNIQUEMENT.

    Instance vide : on n'invoque que list_persisted_snapshots() et
    load_snapshot_from_disk(). Aucun register_runner / get_report.
    """
    if not _WATCHER_AVAILABLE:
        return None
    try:
        return RuntimeWatcher()
    except Exception:
        return None


def _get_discovery_service() -> Optional[Any]:
    """Phase 20A : utilisé UNIQUEMENT pour exposer reports_dir.

    On n'invoque jamais discover(). Le PolicyAttributor est requis par
    le constructeur ; on en instancie un minimal.
    """
    if not (_DISCOVERY_AVAILABLE and _CATALOG_AVAILABLE and _ATTRIBUTOR_AVAILABLE):
        return None
    try:
        catalog = MCPServerCatalog()
        attributor = PolicyAttributor()
        return MCPDiscoveryService(
            catalog=catalog,
            attributor=attributor,
        )
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Filtres de sérialisation (champs autorisés / interdits)
# ──────────────────────────────────────────────────────────────────────────────


def _server_entry_to_dict(entry: Any) -> Dict[str, Any]:
    """Sérialise un ServerEntry pour l'admin UI.

    Champ `notes` filtré côté serveur (jamais exposé).
    Champ `display_name` exposé (admin authentifié).
    """
    return {
        "server_id": entry.server_id,
        "display_name": entry.display_name,
        "package_spec": entry.package_spec,
        "version": entry.version,
        "owner_profile": entry.owner_profile,
        "trust_score": entry.trust_score,
        "status": entry.status.value if entry.status is not None else None,
        "added_at": entry.added_at,
        "updated_at": entry.updated_at,
        "last_active_at": entry.last_active_at,
        # notes : DÉLIBÉRÉMENT EXCLU
    }


def _pending_action_to_dict(action: Any) -> Dict[str, Any]:
    """Sérialise une PendingAction. Aucun args.

    PendingAction est une vue lecture qui ne contient déjà PAS d'args
    (Phase 10 design). On expose ses champs publics.
    """
    return {
        "id": action.id,
        "tool_name": action.tool_name,
        "policy": action.policy.value if action.policy is not None else None,
        "caller_kind": action.caller_kind,
        "risk_summary": action.risk_summary,
        "proposed_at": action.proposed_at,
    }


def _snapshot_to_dict(snap: Any) -> Dict[str, Any]:
    return {
        "server_id": snap.server_id,
        "process_state": snap.process_state,
        "uptime_seconds": snap.uptime_seconds,
        "restart_count": snap.restart_count,
        "crash_count_window": snap.crash_count_window,
        "last_transition_ts": snap.last_transition_ts,
        "last_error_code": snap.last_error_code,
        "transitions_recent": [
            list(t) for t in snap.transitions_recent
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Audit components whitelist
# ──────────────────────────────────────────────────────────────────────────────

_AUDIT_COMPONENTS = {
    "catalog":             "mcp_server_catalog",
    "approval_queue":      "mcp_approvals",
    "runtime_watcher":     "mcp_runtime_watcher",
    "orchestrator":        "mcp_orchestrator",
    "discovery":           "mcp_discovery",
    "install_orchestrator": "mcp_install_orchestrator",
    "activation":          "mcp_activation",
    "policy_resolver":     "mcp_policy_resolver",
    "policy_attributor":   "mcp_policy_attributor",
    # Phase 21 — audit UI dédié émis par 20B-1 → 20B-6
    "admin_ui":            "mcp_admin_audit",
}


def _audit_path(component: str) -> Optional[Path]:
    if not _DATA_DIR_AVAILABLE:
        return None
    subdir = _AUDIT_COMPONENTS.get(component)
    if subdir is None:
        return None
    return DATA_DIR / subdir / "audit.jsonl"


def _tail_jsonl(path: Path, limit: int, offset: int) -> List[Dict[str, Any]]:
    """Tail d'un .jsonl : retourne les `limit` derniers events (offset
    depuis la fin)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    lines = [ln for ln in lines if ln.strip()]
    if offset < 0:
        offset = 0
    if limit <= 0:
        return []
    end = len(lines) - offset
    start = max(0, end - limit)
    selected = lines[start:end]
    events: List[Dict[str, Any]] = []
    for ln in selected:
        try:
            events.append(json.loads(ln))
        except Exception:
            continue
    return events


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════


# ── 1. /api/mcp/health ────────────────────────────────────────────────────────

@router.get("/api/mcp/health", dependencies=[Depends(verify_admin_token)])
async def mcp_health():
    """État global de la chaîne MCP (composants importables).

    Expose `live_mode` (Phase 20B-1) pour permettre à l'UI d'afficher le
    bandeau dry_run forcé si LUMENA_MCP_LIVE n'est pas actif.
    """
    return {
        "available": True,
        "phase": "21",
        "mode": "hardening_observability_keys_integrity_coherence_readiness",
        "live_mode": _live_mode_enabled(),
        "autoapprove_live_mode": _autoapprove_live_mode_enabled(),
        "trust_live_mode": _trust_live_mode_enabled(),
        "components": {
            "catalog": {"available": _CATALOG_AVAILABLE},
            "approval_queue": {"available": _APPROVAL_AVAILABLE},
            "watcher": {
                "available": _WATCHER_AVAILABLE,
                "mode": "runtime_singleton_plus_persisted_snapshots",
                "runtime_singleton": _get_runtime_watcher_singleton() is not None,
            },
            "discovery": {
                "available": _DISCOVERY_AVAILABLE,
                "mode": "reports_only",
            },
            "install_orchestrator": {
                "available": _INSTALL_ORCHESTRATOR_AVAILABLE,
            },
            "activation_service": {
                "available": _ACTIVATION_SERVICE_AVAILABLE,
            },
            "auto_approve_engine": {
                "available": _AUTO_APPROVE_ENGINE_AVAILABLE,
            },
            "local_creation_executor": {
                "available": _LOCAL_CREATION_EXECUTOR_AVAILABLE,
            },
        },
    }


# ── 2. /api/mcp/catalog ───────────────────────────────────────────────────────

@router.get("/api/mcp/catalog", dependencies=[Depends(verify_admin_token)])
async def mcp_catalog_list(
    status_filter: Optional[str] = Query(None),
    owner_profile_filter: Optional[str] = Query(None),
    include_removed: bool = Query(False),
):
    """Liste les servers du Catalog (champ `notes` filtré côté serveur)."""
    catalog = _get_catalog()
    if catalog is None:
        return {"available": False, "reason": "not_loaded", "servers": []}

    status_enum = None
    if status_filter is not None:
        try:
            status_enum = ServerStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status_filter: {status_filter}",
            )

    try:
        servers = catalog.list_servers(
            status_filter=status_enum,
            owner_profile_filter=owner_profile_filter,
            include_removed=include_removed,
        )
    except Exception as e:
        return {"available": True, "error": str(type(e).__name__), "servers": []}

    return {
        "available": True,
        "servers": [_server_entry_to_dict(s) for s in servers],
        "count": len(servers),
    }


# ── 2.5 /api/mcp/library (Phase G) — vue agrégée user-facing ────────────────

@router.get("/api/mcp/library", dependencies=[Depends(verify_admin_token)])
async def mcp_library():
    """Phase G — vue 'bibliothèque' user-facing.

    Agrège :
      - `installed` : entries du catalog (statuts != REMOVED) résumées en
        format compact (server_id, display_name, status, semantic_category,
        prefer_over_native, trust_score).
      - `curated`  : entrées du cache curated (`DATA_DIR/mcp_curated/`),
        chacune annotée `installed_status` selon le catalog.
      - `counts`   : compteurs actifs/installés/en attente/découverts/curated.

    Lecture pure. Aucune mutation. Toutes les actions passent par le chat.
    """
    # 1) Catalog
    catalog = _get_catalog()
    installed: List[Dict[str, Any]] = []
    if catalog is not None:
        try:
            entries = catalog.list_servers(include_removed=False)
        except Exception:  # noqa: BLE001
            entries = []
        for e in entries:
            installed.append({
                "server_id": getattr(e, "server_id", ""),
                "display_name": getattr(e, "display_name", ""),
                "package_spec": getattr(e, "package_spec", ""),
                "status": getattr(getattr(e, "status", None), "value", ""),
                "trust_score": getattr(e, "trust_score", None),
                "semantic_category": getattr(e, "semantic_category", None),
                "category_decision_source": getattr(
                    e, "category_decision_source", "",
                ),
                "prefer_over_native": bool(
                    getattr(e, "prefer_over_native", False)
                ),
                "last_active_at": getattr(e, "last_active_at", None),
            })
    installed_specs = {
        (i["package_spec"], i.get("version", "latest")) for i in installed
    }

    # 2) Curated cache (lecture defensive — best-effort, jamais raise)
    curated: List[Dict[str, Any]] = []
    try:
        from src.mcp.curated_cache_writer import read_curated_entries
        from src.utils.paths import DATA_DIR
        import os
        env_dir = os.environ.get("LUMENA_MCP_CURATED_CACHE_DIR", "").strip()
        from pathlib import Path as _P
        cache_root = _P(env_dir) if env_dir else _P(DATA_DIR)
        curated_raw = read_curated_entries(cache_root)
    except Exception:  # noqa: BLE001
        curated_raw = []
    for c in curated_raw:
        spec = c.get("package_spec", "")
        ver = c.get("version", "latest")
        annotated = dict(c)
        annotated["installed_status"] = (
            "installed" if (spec, ver) in installed_specs else "available"
        )
        curated.append(annotated)

    # 3) Counts
    by_status: Dict[str, int] = {}
    for i in installed:
        s = i.get("status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "available": True,
        "counts": {
            "active": by_status.get("active", 0),
            "installed": by_status.get("installed", 0),
            "declared": by_status.get("declared", 0),
            "quarantined": by_status.get("quarantined", 0),
            "curated": len(curated),
            "total_known": len(installed),
        },
        "installed": installed,
        "curated": curated,
    }


# ── 2.6 Phase I-6 : Library config dynamique (schema + secrets + config) ──

import re as _re_i6

_I6_SERVER_ID_RE = _re_i6.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_I6_KEY_NAME_RE = _re_i6.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _i6_credentials_singleton():
    try:
        from web.routes import deps as _d
        return getattr(_d, "_MCP_CREDENTIALS_SERVICE_SINGLETON", None)
    except Exception:  # noqa: BLE001
        return None


def _i6_config_singleton():
    try:
        from web.routes import deps as _d
        return getattr(_d, "_MCP_CONFIG_SERVICE_SINGLETON", None)
    except Exception:  # noqa: BLE001
        return None


def _i6_orchestrator_singleton():
    try:
        from web.routes import deps as _d
        return getattr(_d, "_MCP_AUTONOMY_ORCHESTRATOR_SINGLETON", None)
    except Exception:  # noqa: BLE001
        return None


def _i6_validate_server_id(sid: str) -> None:
    if not isinstance(sid, str) or not _I6_SERVER_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid_server_id")


def _i6_validate_key(key: str) -> None:
    if not isinstance(key, str) or not _I6_KEY_NAME_RE.match(key):
        raise HTTPException(status_code=400, detail="invalid_key_name")


def _i6_get_persisted_schema(server_id: str):
    """Retourne le config_schema persisté dans ServerEntry, ou None."""
    catalog = _get_catalog()
    if catalog is None:
        return None
    try:
        entry = catalog.get_server(server_id)
    except Exception:  # noqa: BLE001
        return None
    if entry is None:
        return None
    return getattr(entry, "config_schema", None)


def _i6_build_schema_response(server_id: str) -> Dict[str, Any]:
    """Compose le schéma à renvoyer.

    Priorité :
      1. ServerEntry.config_schema persisté
      2. Niveau 1 KNOWN_MCPS via slug (si server_id catalogué)
      3. None
    """
    persisted = _i6_get_persisted_schema(server_id)
    if persisted is not None:
        return {"server_id": server_id, "schema": persisted, "source": "persisted"}
    try:
        from src.mcp.known_mcps import get_known_mcp
        from src.mcp.config_schema import schema_to_dict
        known = get_known_mcp(server_id)
    except Exception:  # noqa: BLE001
        known = None
    if known is not None:
        try:
            return {
                "server_id": server_id,
                "schema": schema_to_dict(known.to_schema()),
                "source": "curated",
            }
        except Exception:  # noqa: BLE001
            pass
    return {"server_id": server_id, "schema": None, "source": "none"}


# ──── A) GET schema ────────────────────────────────────────────────────────

@router.get(
    "/api/mcp/library/{server_id}/schema",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_schema(server_id: str):
    """Retourne le MCPConfigSchema connu pour ce server (persisté ou curated)."""
    _i6_validate_server_id(server_id)
    return _i6_build_schema_response(server_id)


# ──── B) GET config-status ─────────────────────────────────────────────────

@router.get(
    "/api/mcp/library/{server_id}/config-status",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_config_status(server_id: str):
    """Statut field-par-field : 'set' / 'missing'. Aucune valeur exposée."""
    _i6_validate_server_id(server_id)
    creds = _i6_credentials_singleton()
    config = _i6_config_singleton()
    if creds is None or config is None:
        raise HTTPException(status_code=503, detail="services_unavailable")
    payload = _i6_build_schema_response(server_id)
    schema_dict = payload.get("schema") or {}
    fields = schema_dict.get("fields", []) if isinstance(schema_dict, dict) else []

    secret_required: List[str] = []
    config_required: List[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        if not f.get("required", True):
            continue
        name = f.get("name")
        if not isinstance(name, str):
            continue
        sens = f.get("sensitivity", "normal")
        if sens == "secret":
            secret_required.append(name)
        else:
            config_required.append(name)

    secret_status = creds.status_map(server_id, secret_required) if secret_required else {}
    config_status = config.status_map(server_id, config_required) if config_required else {}
    all_status: Dict[str, str] = {}
    all_status.update(secret_status)
    all_status.update(config_status)
    ready = (
        creds.has_all(server_id, secret_required)
        and config.has_all(server_id, config_required)
    )
    return {
        "server_id": server_id,
        "status": all_status,
        "missing": [k for k, v in all_status.items() if v == "missing"],
        "ready": ready,
    }


# ──── C) PUT secret ────────────────────────────────────────────────────────

@router.put(
    "/api/mcp/library/{server_id}/secrets/{key_name}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_set_secret(
    server_id: str,
    key_name: str,
    body: Dict[str, Any] = Body(default={}),
):
    """Stocke une valeur de SECRET. value="" supprime la clé.
    JAMAIS de log de la valeur."""
    _i6_validate_server_id(server_id)
    _i6_validate_key(key_name)
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(status_code=400, detail="missing_value")
    value = body.get("value")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="invalid_value_type")
    creds = _i6_credentials_singleton()
    if creds is None:
        raise HTTPException(status_code=503, detail="credentials_unavailable")
    try:
        creds.set(server_id, key_name, value)
    except Exception as e:  # noqa: BLE001
        # On ne propage JAMAIS la valeur dans le détail.
        raise HTTPException(
            status_code=400, detail=f"set_failed:{type(e).__name__}",
        ) from None
    return {"ok": True, "is_set": value != ""}


# ──── D) DELETE secret ─────────────────────────────────────────────────────

@router.delete(
    "/api/mcp/library/{server_id}/secrets/{key_name}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_delete_secret(server_id: str, key_name: str):
    _i6_validate_server_id(server_id)
    _i6_validate_key(key_name)
    creds = _i6_credentials_singleton()
    if creds is None:
        raise HTTPException(status_code=503, detail="credentials_unavailable")
    try:
        removed = bool(creds.delete(server_id, key_name))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"delete_failed:{type(e).__name__}",
        ) from None
    return {"ok": True, "removed": removed}


# ──── E) PUT config (non-secret) ───────────────────────────────────────────

@router.put(
    "/api/mcp/library/{server_id}/config/{key_name}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_set_config(
    server_id: str,
    key_name: str,
    body: Dict[str, Any] = Body(default={}),
):
    _i6_validate_server_id(server_id)
    _i6_validate_key(key_name)
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(status_code=400, detail="missing_value")
    value = body.get("value")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="invalid_value_type")
    config = _i6_config_singleton()
    if config is None:
        raise HTTPException(status_code=503, detail="config_unavailable")
    try:
        config.set(server_id, key_name, value)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"set_failed:{type(e).__name__}",
        ) from None
    return {"ok": True, "is_set": value != ""}


# ──── F) DELETE config ─────────────────────────────────────────────────────

@router.delete(
    "/api/mcp/library/{server_id}/config/{key_name}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_delete_config(server_id: str, key_name: str):
    _i6_validate_server_id(server_id)
    _i6_validate_key(key_name)
    config = _i6_config_singleton()
    if config is None:
        raise HTTPException(status_code=503, detail="config_unavailable")
    try:
        removed = bool(config.delete(server_id, key_name))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"delete_failed:{type(e).__name__}",
        ) from None
    return {"ok": True, "removed": removed}


# ──── G) GET ready ─────────────────────────────────────────────────────────

@router.get(
    "/api/mcp/library/{server_id}/ready",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_ready(server_id: str):
    """Bool : tous les champs requis sont set → activable."""
    _i6_validate_server_id(server_id)
    creds = _i6_credentials_singleton()
    config = _i6_config_singleton()
    if creds is None or config is None:
        raise HTTPException(status_code=503, detail="services_unavailable")
    payload = _i6_build_schema_response(server_id)
    schema_dict = payload.get("schema") or {}
    fields = schema_dict.get("fields", []) if isinstance(schema_dict, dict) else []

    sec_req, cfg_req = [], []
    for f in fields:
        if not isinstance(f, dict) or not f.get("required", True):
            continue
        name = f.get("name")
        if not isinstance(name, str):
            continue
        if f.get("sensitivity") == "secret":
            sec_req.append(name)
        else:
            cfg_req.append(name)
    ready = creds.has_all(server_id, sec_req) and config.has_all(server_id, cfg_req)
    missing_sec = creds.missing_keys(server_id, sec_req) if sec_req else []
    missing_cfg = config.missing_keys(server_id, cfg_req) if cfg_req else []
    return {
        "server_id": server_id,
        "ready": ready,
        "missing_secrets": missing_sec,
        "missing_config": missing_cfg,
    }


# ──── H) POST detect-schema ────────────────────────────────────────────────

@router.post(
    "/api/mcp/library/{server_id}/detect-schema",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_library_detect_schema(
    server_id: str,
    body: Dict[str, Any] = Body(default={}),
):
    """Relance la cascade de détection de schéma.

    Body optionnel :
      - intent: str (Niveau 1)
      - package_spec: str (Niveau 2)
      - user_snippet: str (Niveau 4)

    Le schéma trouvé est PERSISTÉ dans ServerEntry.config_schema.
    """
    _i6_validate_server_id(server_id)
    if not isinstance(body, dict):
        body = {}
    intent = body.get("intent")
    package_spec = body.get("package_spec")
    user_snippet = body.get("user_snippet")
    try:
        from src.mcp.schema_cascade import detect_schema
        from src.mcp.config_schema import schema_to_dict
        schema = detect_schema(
            server_id=server_id,
            intent=intent if isinstance(intent, str) else None,
            package_spec=package_spec if isinstance(package_spec, str) else None,
            user_snippet=user_snippet if isinstance(user_snippet, str) else None,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"detect_failed:{type(e).__name__}",
        ) from None
    if schema is None:
        return {"server_id": server_id, "schema": None, "persisted": False}
    schema_d = schema_to_dict(schema)
    # Persistance best-effort dans ServerEntry.config_schema si l'entrée existe.
    persisted = False
    catalog = _get_catalog()
    if catalog is not None:
        try:
            catalog.update_config_schema(server_id, schema_d)
            persisted = True
        except Exception:  # noqa: BLE001
            persisted = False
    return {
        "server_id": server_id,
        "schema": schema_d,
        "persisted": persisted,
        "detected_from": schema.detected_from,
    }


# ── 3. /api/mcp/catalog/{server_id} ───────────────────────────────────────────

@router.get(
    "/api/mcp/catalog/{server_id}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_get(server_id: str):
    """Détail d'un server. Champ `notes` filtré côté serveur."""
    catalog = _get_catalog()
    if catalog is None:
        return {"available": False, "reason": "not_loaded"}

    try:
        entry = catalog.get_server(server_id)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid server_id: {type(e).__name__}",
        )

    if entry is None:
        raise HTTPException(status_code=404, detail="Server not found")

    return {
        "available": True,
        "server": _server_entry_to_dict(entry),
    }


# ── 4. /api/mcp/approvals/pending ─────────────────────────────────────────────

@router.get(
    "/api/mcp/approvals/pending",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_approvals_pending(
    limit: int = Query(50, ge=1, le=500),
):
    """Tickets ApprovalQueue PENDING. Args jamais exposés."""
    queue = _get_approval_queue()
    if queue is None:
        return {"available": False, "reason": "not_loaded", "pending": []}

    try:
        actions = queue.list_pending()
    except Exception:
        return {"available": True, "pending": []}

    sliced = actions[:limit]
    return {
        "available": True,
        "pending": [_pending_action_to_dict(a) for a in sliced],
        "count": len(sliced),
        "total": len(actions),
    }


# ── 5. /api/mcp/approvals/decisions ───────────────────────────────────────────

@router.get(
    "/api/mcp/approvals/decisions",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_approvals_decisions(
    limit: int = Query(50, ge=1, le=500),
):
    """Décisions historiques. Lecture directe du dossier decisions/.

    Aucun args lu / déchiffré. Seulement métadonnées du wrapper.
    """
    queue = _get_approval_queue()
    if queue is None:
        return {"available": False, "reason": "not_loaded", "decisions": []}

    try:
        decisions_dir = queue._decisions_dir  # accès attribut interne
    except Exception:
        return {"available": True, "decisions": []}

    if not isinstance(decisions_dir, Path) or not decisions_dir.exists():
        return {"available": True, "decisions": []}

    items: List[Dict[str, Any]] = []
    files = sorted(decisions_dir.glob("*.json"), reverse=True)[:limit]
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "action_id": path.stem,
            "outcome": data.get("outcome"),
            "ts": data.get("ts"),
            "tool_name": data.get("tool_name"),
            "policy": data.get("policy"),
            "caller_kind": data.get("caller_kind"),
            "risk_summary": data.get("risk_summary"),
            # Aucun champ args
        })
    return {
        "available": True,
        "decisions": items,
        "count": len(items),
    }


# ── 6. /api/mcp/watcher/snapshots ─────────────────────────────────────────────

@router.get(
    "/api/mcp/watcher/snapshots",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_watcher_snapshots_list():
    """Liste les snapshots persistés du RuntimeWatcher.

    Source : disque (`DATA_DIR/mcp_runtime_watcher/snapshots/`).
    live=false. Le watcher live (en mémoire) sera Phase 20B/21.
    """
    watcher = _get_watcher()
    if watcher is None:
        return {
            "available": False, "reason": "not_loaded",
            "source": "persisted", "live": False, "snapshots": [],
        }

    try:
        server_ids = watcher.list_persisted_snapshots()
    except Exception:
        return {
            "available": True, "source": "persisted", "live": False,
            "snapshots": [],
        }

    snapshots: List[Dict[str, Any]] = []
    for sid in server_ids:
        try:
            snap = watcher.load_snapshot_from_disk(sid)
            if snap is not None:
                snapshots.append(_snapshot_to_dict(snap))
        except Exception:
            continue
    return {
        "available": True,
        "source": "persisted",
        "live": False,
        "snapshots": snapshots,
        "count": len(snapshots),
    }


# ── 7. /api/mcp/watcher/snapshots/{server_id} ─────────────────────────────────

@router.get(
    "/api/mcp/watcher/snapshots/{server_id}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_watcher_snapshot_get(server_id: str):
    """Snapshot persisté d'un server."""
    watcher = _get_watcher()
    if watcher is None:
        return {
            "available": False, "reason": "not_loaded",
            "source": "persisted", "live": False,
        }

    try:
        snap = watcher.load_snapshot_from_disk(server_id)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid server_id: {type(e).__name__}",
        )

    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return {
        "available": True,
        "source": "persisted",
        "live": False,
        "snapshot": _snapshot_to_dict(snap),
    }


# ── 8. /api/mcp/discovery/reports ─────────────────────────────────────────────

@router.get(
    "/api/mcp/discovery/reports",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_discovery_reports_list(
    server_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Liste les DiscoveryReports persistés sur disque."""
    service = _get_discovery_service()
    if service is None:
        return {"available": False, "reason": "not_loaded", "reports": []}

    try:
        reports_dir = service.reports_dir
    except Exception:
        return {"available": True, "reports": []}

    if not isinstance(reports_dir, Path) or not reports_dir.exists():
        return {"available": True, "reports": []}

    pattern = f"{server_id}_*.json" if server_id else "*.json"
    files = sorted(reports_dir.glob(pattern), reverse=True)[:limit]

    items: List[Dict[str, Any]] = []
    for path in files:
        # Format nom : <server_id>_<ts_safe>.json
        stem = path.stem
        # Split sur le dernier _ pour récupérer ts
        # mais ts contient lui-même des _ (timestamp safe), donc on split
        # depuis la fin sur " " (ne sera pas le cas). Simpler : on lit
        # juste le contenu pour extraire ts et server_id authentiques.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "filename": path.name,
            "server_id": data.get("server_id"),
            "ts": data.get("ts"),
            "discovered_count": data.get("discovered_count"),
            "proposed_count": data.get("proposed_count"),
            "refused_count": data.get("refused_count"),
            "invalid_count": data.get("invalid_count"),
            "error_count": data.get("error_count"),
        })
    return {
        "available": True,
        "reports": items,
        "count": len(items),
    }


# ── 9. /api/mcp/discovery/reports/{server_id}/{ts} ────────────────────────────

@router.get(
    "/api/mcp/discovery/reports/{server_id}/{ts}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_discovery_report_get(server_id: str, ts: str):
    """Récupère un DiscoveryReport persisté par (server_id, ts)."""
    service = _get_discovery_service()
    if service is None:
        return {"available": False, "reason": "not_loaded"}

    try:
        reports_dir = service.reports_dir
    except Exception:
        raise HTTPException(status_code=404, detail="Reports dir unavailable")

    if not isinstance(reports_dir, Path) or not reports_dir.exists():
        raise HTTPException(status_code=404, detail="Reports dir not found")

    # ts est le ts_safe utilisé dans le nom de fichier
    # Phase 17 utilise: <server_id>_<ts_safe>.json
    # Validation server_id minimale (regex simple côté UI)
    if not server_id or "/" in server_id or "\\" in server_id or ".." in server_id:
        raise HTTPException(status_code=400, detail="Invalid server_id")
    if not ts or "/" in ts or "\\" in ts or ".." in ts:
        raise HTTPException(status_code=400, detail="Invalid ts")

    candidate = reports_dir / f"{server_id}_{ts}.json"
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Report malformed")

    return {"available": True, "report": data}


# ── 10. /api/mcp/audit/{component} ────────────────────────────────────────────

@router.get(
    "/api/mcp/audit/{component}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_audit_tail(
    component: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Tail de l'audit.jsonl d'un composant MCP whitelist."""
    path = _audit_path(component)
    if path is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown component. Valid: {sorted(_AUDIT_COMPONENTS.keys())}",
        )
    events = _tail_jsonl(path, limit=limit, offset=offset)
    return {
        "available": True,
        "component": component,
        "events": events,
        "count": len(events),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-1 — Approvals UI mutations (approve / reject)
# ══════════════════════════════════════════════════════════════════════════════
#
# Garde-fous obligatoires sur les 2 routes POST :
#   1. verify_admin_token (auth admin)
#   2. _live_mode_enabled() — kill switch global env LUMENA_MCP_LIVE
#   3. _assert_confirmed(body) — confirmation côté backend obligatoire
#   4. validation reason (reject) : trim, min 3, max 500
#   5. audit UI dédié data/mcp_admin_audit/audit.jsonl
#   6. cache marker UUID4 one-shot TTL 5 min — JAMAIS de route GET sur le cache
#   7. error_code court — JAMAIS message brut d'exception
#
# Hors périmètre 20B-1 (sera Phase 20B-2/3/4/5) :
#   - Install / Activation / Catalog mutations
#   - AutoApprove patterns CRUD
#   - Trust recompute
# ══════════════════════════════════════════════════════════════════════════════


_REASON_MIN_LEN = 3
_REASON_MAX_LEN = 500

_APPROVAL_CACHE_TTL_S = 300.0
_APPROVAL_CACHE_MAX_SIZE = 256
_APPROVAL_CACHE_LOCK = threading.RLock()
_APPROVAL_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
# Entrée :
#   {"marker": str, "action_id": str, "result": ApprovalResult,
#    "created_at": float (monotonic)}
# One-shot strict : _take_marker pop atomiquement avant retour.
# Aucune méthode publique GET — accessible uniquement via helpers internes.


def _live_mode_enabled() -> bool:
    """Kill switch global Phase 20B-1.

    Si LUMENA_MCP_LIVE absent ou valeur falsy, toutes les actions mutatives
    sont forcées en dry_run (aucune mutation queue, aucun marker créé).
    """
    raw = os.environ.get("LUMENA_MCP_LIVE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _assert_confirmed(body: Optional[Dict[str, Any]]) -> bool:
    """Vérifie body.confirmed == true. Sinon raise 400 confirmation_required."""
    confirmed = bool(body and body.get("confirmed") is True)
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "confirmation_required"},
        )
    return True


def _validate_reason(raw: Any) -> str:
    """Valide la raison du reject. trim + min 3 + max 500.

    Lève 400 reason_invalid si non conforme.
    Retourne la valeur trimée (utilisée pour ApprovalQueue.reject).
    """
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "reason_invalid"},
        )
    trimmed = raw.strip()
    if len(trimmed) < _REASON_MIN_LEN or len(trimmed) > _REASON_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "reason_invalid"},
        )
    return trimmed


_ACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _validate_action_id(action_id: Any) -> str:
    """Valide qu'action_id est un uuid4().hex strict (Phase 10 ApprovalQueue).

    Contrat :
      - regex 32 hex lowercase
      - uuid.UUID(action_id) parseable
      - parsed.version == 4
      - parsed.hex == action_id (interdit toute reformulation)

    Tout écart → 400 action_id_invalid.
    """
    if not isinstance(action_id, str) or not _ACTION_ID_RE.match(action_id):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "action_id_invalid"},
        )
    try:
        parsed = uuid.UUID(action_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "action_id_invalid"},
        )
    if parsed.version != 4 or parsed.hex != action_id:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "action_id_invalid"},
        )
    return action_id


def _action_exists_in_pending(queue: Any, action_id: str) -> bool:
    """Vérifie l'existence d'une action PENDING sans lire .args ni decrypt.

    Utilise list_pending() (Phase 10 design : pas d'accès args).
    """
    try:
        pending = queue.list_pending()
    except Exception:
        return False
    for action in pending or []:
        try:
            if getattr(action, "id", None) == action_id:
                return True
        except Exception:
            continue
    return False


def _hash_actor_token(token: Optional[str]) -> str:
    """Hash SHA256 du token admin pour audit UI.

    - token absent / vide → "sha256:unknown"
    - mode test (LUMENA_TEST_MODE=1) → "sha256:test"
    - sinon → "sha256:<hex 64>"
    Jamais None instable, jamais le token en clair.
    """
    if os.environ.get("LUMENA_TEST_MODE", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return "sha256:test"
    if not token or not isinstance(token, str):
        return "sha256:unknown"
    cleaned = token.replace("Bearer ", "").strip()
    if not cleaned:
        return "sha256:unknown"
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _ui_audit_path() -> Optional[Path]:
    """Chemin du journal d'audit UI dédié Phase 20B-1."""
    if not _DATA_DIR_AVAILABLE:
        return None
    return DATA_DIR / "mcp_admin_audit" / "audit.jsonl"


def _audit_ui_action(
    *,
    event: str,
    action: str,
    target_action_id: str,
    live_mode: bool,
    confirmation_received: bool,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    marker_emitted: Optional[str] = None,
    reason_length: Optional[int] = None,
) -> None:
    """Écrit une entrée dans l'audit UI dédié Phase 20B-1.

    Whitelist stricte : ni reason raw, ni args, ni token clair, ni stack trace.
    Append-only avec verrou fichier best-effort.
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-1",
        "action": action,
        "target_action_id": target_action_id,
        "live_mode": bool(live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if marker_emitted is not None:
        entry["marker_emitted"] = marker_emitted
    if reason_length is not None:
        entry["reason_length"] = int(reason_length)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


def _expire_stale_markers_locked(now: float) -> None:
    """Retire les entrées du cache marker dont le TTL est expiré.

    Doit être appelé avec _APPROVAL_CACHE_LOCK détenu.
    """
    expired_keys = [
        k for k, v in _APPROVAL_RESULT_CACHE.items()
        if (now - float(v.get("created_at", 0.0))) > _APPROVAL_CACHE_TTL_S
    ]
    for k in expired_keys:
        _APPROVAL_RESULT_CACHE.pop(k, None)


def _put_marker(action_id: str, result: Any) -> str:
    """Stocke un ApprovalResult dans le cache et retourne un marker UUID4.

    - Expire les stale d'abord
    - Si capacité dépassée, eviction LRU (la plus ancienne)
    - Helper INTERNE — aucune route GET ne lit le cache
    """
    now = time.monotonic()
    marker = uuid.uuid4().hex
    with _APPROVAL_CACHE_LOCK:
        _expire_stale_markers_locked(now)
        if len(_APPROVAL_RESULT_CACHE) >= _APPROVAL_CACHE_MAX_SIZE:
            oldest_key = min(
                _APPROVAL_RESULT_CACHE.keys(),
                key=lambda k: _APPROVAL_RESULT_CACHE[k].get("created_at", 0.0),
            )
            _APPROVAL_RESULT_CACHE.pop(oldest_key, None)
        _APPROVAL_RESULT_CACHE[marker] = {
            "marker": marker,
            "action_id": action_id,
            "result": result,
            "created_at": now,
        }
    return marker


def _take_marker(marker: str) -> Optional[Any]:
    """One-shot strict : pop atomique avant retour.

    Même si le caller crash après, l'entrée est supprimée du cache.
    Helper INTERNE — aucune route GET ne l'expose.
    """
    now = time.monotonic()
    with _APPROVAL_CACHE_LOCK:
        _expire_stale_markers_locked(now)
        entry = _APPROVAL_RESULT_CACHE.pop(marker, None)
    if entry is None:
        return None
    return entry.get("result")


# ── 11. POST /api/mcp/approvals/{action_id}/approve ───────────────────────────

@router.post(
    "/api/mcp/approvals/{action_id}/approve",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_approval_approve(
    action_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-1 : approve un ticket ApprovalQueue.

    Garde-fous appliqués (ordre strict) :
      1. validation action_id
      2. _assert_confirmed(body)
      3. _live_mode_enabled() — si false : dry_run (would_approve=true)
      4. en live : ApprovalQueue.approve(action_id) → ApprovalResult
      5. _put_marker(action_id, result) → UUID4 retourné à l'UI
      6. _audit_ui_action(requested + completed/simulated/failed)

    Réponse JSON :
      live  : {"approved": true, "action_id": ..., "live_mode": true,
               "marker": "<uuid4 hex>", "marker_ttl_s": 300}
      dry   : {"would_approve": true, "action_id": ..., "live_mode": false,
               "forced_dry_run": true, "marker": null}
      404   : action_id introuvable dans pending
      503   : queue indisponible
    """
    target_id = _validate_action_id(action_id)
    _assert_confirmed(body)

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)
    queue = _get_approval_queue_singleton()
    if queue is None:
        # Fallback lecture seule pour permettre l'instanciation à la demande.
        queue = _get_approval_queue()
    if queue is None:
        _audit_ui_action(
            event="ui_action_failed",
            action="approve",
            target_action_id=target_id,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="queue_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"},
        )

    if not _action_exists_in_pending(queue, target_id):
        _audit_ui_action(
            event="ui_action_failed",
            action="approve",
            target_action_id=target_id,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="approval_not_found",
        )
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "approval_not_found"},
        )

    if not live:
        # Dry-run forcé : aucune mutation queue, aucun marker créé.
        _audit_ui_action(
            event="ui_action_simulated",
            action="approve",
            target_action_id=target_id,
            live_mode=False,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="simulated",
        )
        return {
            "would_approve": True,
            "action_id": target_id,
            "live_mode": False,
            "forced_dry_run": True,
            "marker": None,
        }

    _audit_ui_action(
        event="ui_action_requested",
        action="approve",
        target_action_id=target_id,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        result = queue.approve(target_id)
    except Exception:
        _audit_ui_action(
            event="ui_action_failed",
            action="approve",
            target_action_id=target_id,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="approve_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "approve_failed"},
        )

    marker = _put_marker(target_id, result)

    # ─── Phase I-7 : dispatch catalog_add ───────────────────────────────
    # Si le ticket approuvé est un mcp_catalog_add (action="catalog_add"
    # dans args), on déclenche immédiatement execute_approved_catalog_add
    # pour materializer l'entrée DECLARED dans le catalog. Sinon (install,
    # activation, autre), aucun side-effect : l'execution reste à la charge
    # du flow dédié (InstallOrchestrator / ActivationService).
    catalog_add_outcome: Optional[Dict[str, Any]] = None
    result_args = getattr(result, "args", None)
    if (
        isinstance(result_args, dict)
        and result_args.get("action") == "catalog_add"
        and isinstance(result_args.get("server_id"), str)
        and result_args["server_id"]
    ):
        sid = result_args["server_id"]
        add_orch = _get_catalog_add_orchestrator_singleton()
        if add_orch is None or not callable(
            getattr(add_orch, "execute_approved_catalog_add", None)
        ):
            catalog_add_outcome = {
                "executed": False,
                "server_id": sid,
                "error_code": "orchestrator_unavailable",
            }
        else:
            try:
                exec_result = add_orch.execute_approved_catalog_add(
                    sid, result, dry_run=False,
                )
                catalog_add_outcome = {
                    "executed": True,
                    "server_id": getattr(exec_result, "server_id", sid),
                    "success": bool(getattr(exec_result, "success", False)),
                    "reason": getattr(exec_result, "reason", None),
                    "catalog_status": getattr(exec_result, "catalog_status", None),
                }
            except Exception:
                catalog_add_outcome = {
                    "executed": False,
                    "server_id": sid,
                    "error_code": "execute_failed",
                }

    _audit_ui_action(
        event="ui_action_completed",
        action="approve",
        target_action_id=target_id,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
        outcome="approved",
        duration_s=time.monotonic() - start,
        marker_emitted=marker,
    )
    response: Dict[str, Any] = {
        "approved": True,
        "action_id": target_id,
        "live_mode": True,
        "marker": marker,
        "marker_ttl_s": int(_APPROVAL_CACHE_TTL_S),
    }
    if catalog_add_outcome is not None:
        response["catalog_add"] = catalog_add_outcome
    return response


# ── 12. POST /api/mcp/approvals/{action_id}/reject ────────────────────────────

@router.post(
    "/api/mcp/approvals/{action_id}/reject",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_approval_reject(
    action_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-1 : reject un ticket ApprovalQueue avec raison obligatoire.

    Garde-fous appliqués (ordre strict) :
      1. validation action_id
      2. _assert_confirmed(body)
      3. _validate_reason(body.reason) — trim + 3..500 chars
      4. _live_mode_enabled() — si false : dry_run (would_reject=true)
      5. en live : ApprovalQueue.reject(action_id, reason)
      6. audit UI : reason_length uniquement, JAMAIS reason raw

    Réponse JSON :
      live  : {"rejected": true, "action_id": ..., "live_mode": true}
      dry   : {"would_reject": true, "action_id": ..., "live_mode": false,
               "forced_dry_run": true}
      404   : action_id introuvable
      503   : queue indisponible
    """
    target_id = _validate_action_id(action_id)
    _assert_confirmed(body)
    reason_trimmed = _validate_reason((body or {}).get("reason"))
    reason_len = len(reason_trimmed)

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)
    queue = _get_approval_queue_singleton()
    if queue is None:
        queue = _get_approval_queue()
    if queue is None:
        _audit_ui_action(
            event="ui_action_failed",
            action="reject",
            target_action_id=target_id,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="queue_unavailable",
            reason_length=reason_len,
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"},
        )

    if not _action_exists_in_pending(queue, target_id):
        _audit_ui_action(
            event="ui_action_failed",
            action="reject",
            target_action_id=target_id,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="approval_not_found",
            reason_length=reason_len,
        )
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "approval_not_found"},
        )

    if not live:
        _audit_ui_action(
            event="ui_action_simulated",
            action="reject",
            target_action_id=target_id,
            live_mode=False,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="simulated",
            reason_length=reason_len,
        )
        return {
            "would_reject": True,
            "action_id": target_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    _audit_ui_action(
        event="ui_action_requested",
        action="reject",
        target_action_id=target_id,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
        reason_length=reason_len,
    )
    start = time.monotonic()
    try:
        ok = bool(queue.reject(target_id, reason_trimmed))
    except Exception:
        _audit_ui_action(
            event="ui_action_failed",
            action="reject",
            target_action_id=target_id,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="reject_failed",
            duration_s=time.monotonic() - start,
            reason_length=reason_len,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "reject_failed"},
        )

    if not ok:
        _audit_ui_action(
            event="ui_action_failed",
            action="reject",
            target_action_id=target_id,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="reject_failed",
            duration_s=time.monotonic() - start,
            reason_length=reason_len,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "reject_failed"},
        )

    _audit_ui_action(
        event="ui_action_completed",
        action="reject",
        target_action_id=target_id,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
        outcome="rejected",
        duration_s=time.monotonic() - start,
        reason_length=reason_len,
    )
    return {
        "rejected": True,
        "action_id": target_id,
        "live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-2 — Install lifecycle UI mutations (propose / execute)
# ══════════════════════════════════════════════════════════════════════════════
#
# Garde-fous obligatoires sur les 2 routes POST :
#   1. verify_admin_token (auth admin)
#   2. _validate_server_id_format (réplique Phase 14 + Windows reserved)
#   3. _assert_confirmed(body)
#   4. _validate_caller_kind — whitelist {"admin_ui"} (propose)
#   5. _validate_confirmation_phrase — = server_id exact (execute)
#   6. _validate_marker — regex UUID4 hex 32 chars (execute)
#   7. catalog/orchestrator singletons publics — JAMAIS d'attribut privé
#   8. dry_run : ZERO mutation queue/orchestrator/marker_cache
#   9. live execute : _take_marker AVANT execute_approved_install (one-shot strict)
#  10. validation croisée approval_result.args["server_id"] == body.server_id
#  11. marker consommé reste consommé même si install échoue
#  12. audit UI dédié : aucun package_spec / version / notes / args / phrase raw
#  13. InstallResult brut JAMAIS exposé
# ══════════════════════════════════════════════════════════════════════════════


_INSTALL_TOOL_PREFIX = "mcp_install:"
_CALLER_KIND_WHITELIST = frozenset({"admin_ui"})

# Aligné Phase 14 server_catalog._SERVER_ID_RE
_SERVER_ID_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")

# Aligné Phase 14 server_catalog._WINDOWS_RESERVED_NAMES (réplique stricte,
# sans import du helper privé qui lève CatalogError au lieu de HTTPException)
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})


def _validate_server_id_format(raw: Any) -> str:
    """Validation server_id stricte alignée Phase 14 + Windows-safe.

    Réplique du contrat MCPServerCatalog (Phase 14) côté web :
      - type str non vide
      - regex `^[a-z0-9][a-z0-9_.\\-]{0,63}$` (lowercase strict)
      - refus explicite "..", "/", "\\\\" (déjà couvert par regex mais explicite)
      - refus stem (avant .ext) dans `_WINDOWS_RESERVED`

    Phase 14 utilise déjà cette validation (cf. helper privé du Catalog
    + _WINDOWS_RESERVED_NAMES). On la réplique ici pour lever HTTPException 400
    au lieu de CatalogError, sans importer le helper privé.
    """
    if not isinstance(raw, str) or not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"},
        )
    if "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"},
        )
    if not _SERVER_ID_FORMAT_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"},
        )
    stem = raw.split(".", 1)[0].lower()
    if stem in _WINDOWS_RESERVED:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"},
        )
    return raw


def _extract_install_server_id_from_tool_name(tool_name: Any) -> Optional[str]:
    """Phase 18 contract : tool_name = f"mcp_install:{server_id}".

    Retourne le server_id si tool_name matche, sinon None. Le server_id
    retourné est revalidé par _validate_server_id_format (lève si invalide
    → captured ici en None). Helper utilitaire : non utilisé par les
    routes /install/propose ou /install/execute (qui reçoivent server_id
    directement). Sert aux tests de cohérence du pattern Phase 18.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith(_INSTALL_TOOL_PREFIX):
        return None
    candidate = tool_name[len(_INSTALL_TOOL_PREFIX):]
    if not candidate:
        return None
    try:
        return _validate_server_id_format(candidate)
    except HTTPException:
        return None


def _validate_caller_kind(raw: Any) -> str:
    """Whitelist stricte : seul "admin_ui" est accepté en Phase 20B-2."""
    if not isinstance(raw, str) or raw not in _CALLER_KIND_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "caller_kind_invalid"},
        )
    return raw


def _validate_marker(raw: Any) -> str:
    """Regex UUID4 hex 32 chars (cohérent _put_marker Phase 20B-1)."""
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9a-f]{32}", raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "marker_invalid_format"},
        )
    return raw


def _validate_confirmation_phrase(body: Optional[Dict[str, Any]], expected: str) -> str:
    """Saisie texte = server_id exact (case-sensitive strict).

    Cette friction supplémentaire est exigée pour les actions à blast radius
    élevé (install réel = subprocess npm/pip).
    """
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "confirmation_phrase_invalid"},
        )
    raw = body.get("confirmation_phrase")
    if not isinstance(raw, str) or raw != expected:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "confirmation_phrase_invalid"},
        )
    return raw


def _get_catalog_singleton() -> Optional[Any]:
    """Phase 20B-2 : singleton MCPServerCatalog partagé.

    Singleton lifespan SÉPARÉ du singleton ApprovalQueue. Aucun accès à
    `_APPROVAL_QUEUE_SINGLETON._catalog` ou autre attribut privé.
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_SERVER_CATALOG_SINGLETON", None)
    except Exception:
        return None


def _get_install_orchestrator_singleton() -> Optional[Any]:
    """Phase 20B-2 : singleton MCPInstallOrchestrator lifespan.

    Si None (module non importable au boot ou échec init), les routes
    mutatives Install répondent {"error_code": "orchestrator_unavailable"}.
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_INSTALL_ORCHESTRATOR_SINGLETON", None)
    except Exception:
        return None


def _build_install_orchestrator(
    catalog: Any, queue: Any, dry_run: bool
) -> Optional[Any]:
    """Construit ou réutilise un MCPInstallOrchestrator selon dry_run.

    - Si singleton existe et son dry_run match : réutilise.
    - Sinon : construit ad-hoc avec catalog + queue + dry_run demandé.
    Le coût d'instanciation est négligeable face au coût d'un lancement
    de processus externe par l'orchestrator (npm/pip via runner).
    Aucun accès à des attributs privés du singleton.
    """
    if not _INSTALL_ORCHESTRATOR_AVAILABLE or MCPInstallOrchestrator is None:
        return None
    singleton = _get_install_orchestrator_singleton()
    if singleton is not None:
        try:
            singleton_dry = bool(singleton.dry_run)
        except Exception:
            singleton_dry = True
        if singleton_dry == dry_run:
            return singleton
    try:
        return MCPInstallOrchestrator(
            catalog=catalog,
            approval_queue=queue,
            dry_run=dry_run,
        )
    except Exception:
        return None


def _audit_ui_install_action(
    *,
    event: str,
    action: str,
    target_server_id: str,
    target_action_id: Optional[str],
    caller_kind: Optional[str],
    live_mode: bool,
    confirmation_received: bool,
    confirmation_phrase_received: Optional[bool] = None,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    marker_consumed_irrecoverable: Optional[bool] = None,
) -> None:
    """Audit UI étendu Phase 20B-2.

    Whitelist stricte. Aucun champ package_spec, version, notes, args,
    confirmation_phrase raw, marker raw, raw InstallResult / ApprovalResult.
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-2",
        "action": action,
        "target_server_id": target_server_id,
        "live_mode": bool(live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if target_action_id is not None:
        entry["target_action_id"] = target_action_id
    if caller_kind is not None:
        entry["caller_kind"] = caller_kind
    if confirmation_phrase_received is not None:
        entry["confirmation_phrase_received"] = bool(confirmation_phrase_received)
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if marker_consumed_irrecoverable is not None:
        entry["marker_consumed_irrecoverable"] = bool(marker_consumed_irrecoverable)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


# ── 13. POST /api/mcp/install/propose ─────────────────────────────────────────

@router.post(
    "/api/mcp/install/propose",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_install_propose(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-2 : propose un install via MCPInstallOrchestrator.propose_install.

    Body : {"confirmed": true, "server_id": "...", "caller_kind": "admin_ui"}

    Garde-fous (ordre strict) :
      1. confirmation backend
      2. validation server_id format (Phase 14 + Windows-safe)
      3. validation caller_kind = "admin_ui" uniquement
      4. catalog + queue singletons publics (jamais attribut privé)
      5. catalog.get_server(server_id) → entry présente + status DECLARED
      6. dry_run forcé si LUMENA_MCP_LIVE off : ne crée aucun ticket
      7. live : orchestrator.propose_install(server_id, caller_kind="admin_ui")
         → InstallProposal.approval_ticket_id

    Réponse JSON (whitelist) :
      live : {"proposed": true, "ticket_id": "<uuid4 hex>", "server_id": ...,
              "live_mode": true}
      dry  : {"would_propose": true, "server_id": ..., "live_mode": false,
              "forced_dry_run": true}
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    caller_kind = _validate_caller_kind(body.get("caller_kind"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="catalog_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"},
        )

    # Phase 20B-2 : install propose exige le singleton ApprovalQueue partagé
    # par lifespan. Aucun fallback vers une instance ad-hoc — sinon le ticket
    # pourrait être créé dans une queue différente de celle utilisée par les
    # routes approve/reject UI.
    queue = _get_approval_queue_singleton()
    if queue is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="queue_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"},
        )

    try:
        entry = catalog.get_server(server_id)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="server_not_found",
        )
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "server_not_found"},
        )

    # Status doit être DECLARED pour proposer un install
    status_val = None
    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val != "declared":
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=live,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="server_id_not_declared",
        )
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_not_declared"},
        )

    if not live:
        _audit_ui_install_action(
            event="ui_action_simulated",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=False,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="simulated",
        )
        return {
            "would_propose": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    orchestrator = _build_install_orchestrator(catalog, queue, dry_run=False)
    if orchestrator is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="orchestrator_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "orchestrator_unavailable"},
        )

    _audit_ui_install_action(
        event="ui_action_requested",
        action="install_propose",
        target_server_id=server_id,
        target_action_id=None,
        caller_kind=caller_kind,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        proposal = orchestrator.propose_install(
            server_id=server_id, caller_kind=caller_kind
        )
    except Exception:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="propose_install_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "propose_install_failed"},
        )

    ticket_id = getattr(proposal, "approval_ticket_id", None)
    if not isinstance(ticket_id, str) or not ticket_id:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_propose",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=caller_kind,
            live_mode=True,
            confirmation_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="propose_install_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "propose_install_failed"},
        )

    _audit_ui_install_action(
        event="ui_action_completed",
        action="install_propose",
        target_server_id=server_id,
        target_action_id=ticket_id,
        caller_kind=caller_kind,
        live_mode=True,
        confirmation_received=True,
        actor_token_hash=actor_hash,
        outcome="proposed",
        duration_s=time.monotonic() - start,
    )
    return {
        "proposed": True,
        "ticket_id": ticket_id,
        "server_id": server_id,
        "live_mode": True,
    }


# ── 14. POST /api/mcp/install/execute ─────────────────────────────────────────

@router.post(
    "/api/mcp/install/execute",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_install_execute(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-2 : exécute un install approuvé via execute_approved_install.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>",
            "server_id": "...", "marker": "<uuid4 hex>"}

    Pipeline live :
      1. confirmation backend + phrase = server_id exact
      2. validation server_id format + marker format
      3. catalog/queue singletons
      4. catalog.get_server(server_id) : status != INSTALLED + != REMOVED
      5. _take_marker(marker) one-shot AVANT execute
         → si None : 404 marker_not_found_or_expired
      6. validation croisée : approval_result.args["server_id"] == body.server_id
         → mismatch = marker consommé irrécouvrable + 400
      7. orchestrator.execute_approved_install(server_id, approval_result)
      8. réponse whitelist : {executed, server_id, status, live_mode}

    Pipeline dry_run :
      - ZÉRO call _take_marker
      - ZÉRO call execute_approved_install
      - simulation pure : {would_execute, server_id, live_mode:false, forced_dry_run:true}
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    _validate_confirmation_phrase(body, expected=server_id)
    marker = _validate_marker(body.get("marker"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=live,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="catalog_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"},
        )

    # Phase 20B-2 : install execute exige le singleton ApprovalQueue partagé
    # par lifespan. Aucun fallback ad-hoc autorisé. Si le singleton est
    # absent, on retourne 503 AVANT toute consommation marker (le marker
    # reste dans le cache, l'admin pourra réessayer après reboot ou re-init
    # du singleton).
    queue = _get_approval_queue_singleton()
    if queue is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=live,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="queue_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"},
        )

    try:
        entry = catalog.get_server(server_id)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=live,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="server_not_found",
        )
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "server_not_found"},
        )

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val == "installed" or status_val == "active":
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=live,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="server_already_installed",
        )
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_already_installed"},
        )
    if status_val not in ("declared",):
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=live,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="server_id_invalid_status",
        )
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_status"},
        )

    # ── Dry-run STRICT : ZÉRO call _take_marker / orchestrator ────────────────
    if not live:
        _audit_ui_install_action(
            event="ui_action_simulated",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=False,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="simulated",
        )
        return {
            "would_execute": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    # ── Live : consommer marker AVANT execute_approved_install ────────────────
    approval_result = _take_marker(marker)
    if approval_result is None:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=True,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="marker_not_found_or_expired",
        )
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "marker_not_found_or_expired"},
        )

    # Validation croisée : args["server_id"] == body.server_id
    args = getattr(approval_result, "args", None)
    args_server_id = None
    if isinstance(args, dict):
        args_server_id = args.get("server_id")
    if args_server_id != server_id:
        # Marker consommé irrécouvrable : signaler à l'audit, renvoyer 400
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=True,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="marker_server_id_mismatch",
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "marker_server_id_mismatch"},
        )

    orchestrator = _build_install_orchestrator(catalog, queue, dry_run=False)
    if orchestrator is None:
        # Marker déjà consommé : signaler dans l'audit
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=True,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="orchestrator_unavailable",
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "orchestrator_unavailable"},
        )

    _audit_ui_install_action(
        event="ui_action_requested",
        action="install_execute",
        target_server_id=server_id,
        target_action_id=None,
        caller_kind=None,
        live_mode=True,
        confirmation_received=True,
        confirmation_phrase_received=True,
        actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        install_result = orchestrator.execute_approved_install(
            server_id=server_id, approval_result=approval_result
        )
    except Exception:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=True,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="install_failed",
            duration_s=time.monotonic() - start,
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "install_failed"},
        )

    success = bool(getattr(install_result, "success", False))
    if not success:
        _audit_ui_install_action(
            event="ui_action_failed",
            action="install_execute",
            target_server_id=server_id,
            target_action_id=None,
            caller_kind=None,
            live_mode=True,
            confirmation_received=True,
            confirmation_phrase_received=True,
            actor_token_hash=actor_hash,
            outcome="error",
            error_code="install_failed",
            duration_s=time.monotonic() - start,
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "install_failed"},
        )

    # Re-lit le status depuis le catalog après install (orchestrator l'a muté)
    try:
        new_entry = catalog.get_server(server_id)
        new_status = new_entry.status.value if (new_entry is not None and new_entry.status is not None) else "INSTALLED"
    except Exception:
        new_status = "INSTALLED"

    _audit_ui_install_action(
        event="ui_action_completed",
        action="install_execute",
        target_server_id=server_id,
        target_action_id=None,
        caller_kind=None,
        live_mode=True,
        confirmation_received=True,
        confirmation_phrase_received=True,
        actor_token_hash=actor_hash,
        outcome="installed",
        duration_s=time.monotonic() - start,
    )
    return {
        "executed": True,
        "server_id": server_id,
        "status": str(new_status).upper(),
        "live_mode": True,
    }


@router.post(
    "/api/mcp/local-create/execute",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_local_create_execute(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Materialize an approved mcp_local_create ticket into Catalog."""
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    _validate_confirmation_phrase(body, expected=server_id)
    marker = _validate_marker(body.get("marker"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)
    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_install_action(
            event="ui_action_failed", action="local_create_execute",
            target_server_id=server_id, target_action_id=None, caller_kind=None,
            live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"},
        )
    if not _LOCAL_CREATION_EXECUTOR_AVAILABLE or MCPLocalCreationExecutor is None:
        raise HTTPException(
            status_code=503,
            detail={"error": True, "error_code": "local_creation_executor_unavailable"},
        )
    if not live:
        return {
            "would_execute": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
            "status": "declared",
        }
    approval_result = _take_marker(marker)
    if approval_result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "marker_not_found_or_expired"},
        )
    args = getattr(approval_result, "args", None)
    if not isinstance(args, dict) or args.get("server_id") != server_id:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "marker_server_id_mismatch"},
        )
    result = MCPLocalCreationExecutor(catalog=catalog).execute_approved_local_creation(
        approval_result, server_id=server_id, dry_run=False,
    )
    if not result.success:
        raise HTTPException(
            status_code=500,
            detail={"error": True, "error_code": "local_create_failed"},
        )
    return {
        "executed": True,
        "server_id": server_id,
        "status": result.catalog_status,
        "request_path": result.created_request_path_relative,
        "live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-3 — Activation lifecycle UI mutations (propose / execute / deactivate)
# ══════════════════════════════════════════════════════════════════════════════
#
# 3 routes POST mutatives :
#   13. POST /api/mcp/activation/propose
#   14. POST /api/mcp/activation/execute (consomme marker UUID4 émis par 20B-1)
#   15. POST /api/mcp/activation/deactivate (pas de marker, action de protection)
#
# Garde-fous obligatoires (héritage 20B-1/20B-2 + spécifiques 20B-3) :
#   1. verify_admin_token
#   2. _validate_server_id_format (réplique Phase 14)
#   3. _assert_confirmed(body)
#   4. _validate_caller_kind = "admin_ui" (propose)
#   5. _validate_confirmation_phrase = server_id exact (execute + deactivate)
#   6. _validate_marker UUID4 hex 32 (execute uniquement)
#   7. Singletons publics : catalog + queue + install_orchestrator + runtime_watcher
#      (aucun fallback ad-hoc autorisé pour install ou activation)
#   8. registry_writer = runtime Lumena (deps.lumena._tool_registry OU
#      deps.lumena.tool_system._tool_registry). Aucune instanciation neuve.
#   9. runner_factory(server_id, entry) construit MCPInstallSpec(npm ou uv).
#      local et inconnu lèvent ValueError ; aucune construction de spec hors whitelist.
#  10. mcp_root = install_orchestrator.install_root (cohérence Phase 18)
#  11. Dry-run STRICT : 0 mutation queue/orchestrator/cache marker
#  12. Live execute : _take_marker AVANT activate ; marker consommé reste
#      consommé en cas d'échec (marker_consumed_irrecoverable=true)
#  13. Aucun register_dynamic_handler / unregister_dynamic_handler direct
#  14. Aucun lancement de processus externe direct
#  15. Audit UI dédié : aucun package_spec / version / notes / args / phrase
#      raw / marker raw / install_root / handler names exposés
#  16. error_code générique côté UI (anti canal latéral). Le détail interne
#      reste dans l'audit Phase 19 (data/mcp_activation/audit.jsonl)
# ══════════════════════════════════════════════════════════════════════════════


_ACTIVATE_TOOL_PREFIX = "mcp_activate:"


def _extract_activate_server_id_from_tool_name(tool_name: Any) -> Optional[str]:
    """Phase 19 contract : tool_name = f"mcp_activate:{server_id}".

    Retourne le server_id validé si tool_name matche, sinon None.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith(_ACTIVATE_TOOL_PREFIX):
        return None
    candidate = tool_name[len(_ACTIVATE_TOOL_PREFIX):]
    if not candidate:
        return None
    try:
        return _validate_server_id_format(candidate)
    except HTTPException:
        return None


def _build_install_spec_from_entry(entry: Any) -> Any:
    """Construit MCPInstallSpec depuis ServerEntry Phase 14.

    Phase 5 contract (sandbox_runner.MCPInstallSpec) :
        transport: Literal["npm", "uv"] (strict)

    Mapping autorisé :
      - "npm:<package>"   → MCPInstallSpec(transport="npm",  package=...)
      - "pypi:<package>"  → MCPInstallSpec(transport="uv",   package=...)

    Refus explicite (jamais retourner un spec invalide) :
      - "local:<slug>"    → MCPInstallSpec(transport="uv", package=<local dir>)
      - inconnu/vide      → raise ValueError("transport_unsupported:unknown")
      - package vide      → raise ValueError("transport_unsupported:empty_package")
    """
    from src.mcp.local_package import LocalMCPPackageError, resolve_local_mcp_package
    from src.mcp.sandbox_runner import MCPInstallSpec

    raw = getattr(entry, "package_spec", None) or ""
    if raw.startswith("npm:"):
        chosen_transport = "npm"
        package = raw[len("npm:"):]
    elif raw.startswith("pypi:"):
        chosen_transport = "uv"
        package = raw[len("pypi:"):]
    elif raw.startswith("local:"):
        server_id = getattr(entry, "server_id", None)
        try:
            local_pkg = resolve_local_mcp_package(server_id)
        except LocalMCPPackageError as exc:
            raise ValueError("transport_unsupported:local_missing") from exc
        chosen_transport = "uv"
        package = str(local_pkg.package_dir)
        return MCPInstallSpec(
            name=server_id,
            transport=chosen_transport,
            package=package,
            args=["-m", local_pkg.module_name],
            package_version=None,
            trust_score=getattr(entry, "trust_score", None),
            require_wheels_only=False,
        )
    else:
        raise ValueError("transport_unsupported:unknown")

    if not package:
        raise ValueError("transport_unsupported:empty_package")

    # Fix Q (Phase I-7) : peuple env_keys_allowlist depuis le config_schema
    # de l'entry catalog. Sans ça, runner.start() refuse TOUS les secrets
    # injectés (allowlist vide) → SLACK_BOT_TOKEN jamais passé au Node →
    # mcp-server-slack crash sur "missing token" → client_initialize_failed.
    env_allowlist: list = []
    try:
        cfg_schema = getattr(entry, "config_schema", None)
        if cfg_schema is not None:
            fields = (
                cfg_schema.get("fields", [])
                if isinstance(cfg_schema, dict)
                else getattr(cfg_schema, "fields", [])
            )
            for f in fields or []:
                name = (
                    f.get("name") if isinstance(f, dict)
                    else getattr(f, "name", None)
                )
                if isinstance(name, str) and name:
                    env_allowlist.append(name)
    except Exception:
        env_allowlist = []

    # Fix AY (Phase I-8) : sous-commande serveur persistée au catalogue
    # (entry point console = CLI à sous-commandes, ex. windows-mcp → serve).
    # Découverte réactivement par l'activation, réutilisée à chaque boot.
    raw_entry_args = getattr(entry, "start_entry_args", None)
    entry_args = (
        [str(a) for a in raw_entry_args] if raw_entry_args else []
    )

    return MCPInstallSpec(
        name=getattr(entry, "server_id", None),
        transport=chosen_transport,
        package=package,
        package_version=getattr(entry, "version", None),
        trust_score=getattr(entry, "trust_score", None),
        env_keys_allowlist=env_allowlist,
        entry_args=entry_args,
    )


def _build_runner_factory(install_root: Any):
    """Factory respectant le contrat Phase 19 :
        runner = runner_factory(server_id, entry)

    Construit un MCPInstallSpec depuis entry.package_spec puis instancie
    MCPSandboxRunner avec mcp_root = install_root (cohérence Phase 18).
    Si entry.package_spec est local:/inconnu/vide → ValueError propagée.
    """
    from src.mcp.sandbox_runner import MCPSandboxRunner

    def _factory(server_id: Any, entry: Any):
        spec = _build_install_spec_from_entry(entry)
        return MCPSandboxRunner(
            spec=spec,
            mcp_root=install_root,
            stdout_mode="client",
        )

    return _factory


class _MCPHandlerAdapterFacade:
    """Façade locale : src/mcp/handler_adapter.py expose la fonction adapt_tool,
    pas une classe. MCPActivationService attend un Protocol exposant
    .adapt_tool(**kwargs). On wrappe sans toucher src/mcp/*."""

    def adapt_tool(self, **kwargs):
        from src.mcp.handler_adapter import adapt_tool as _adapt
        return _adapt(**kwargs)


def _resolve_registry_writer() -> Optional[Any]:
    """Résout le ToolRegistry runtime Lumena.

    Ordre strict :
      1. deps.lumena._tool_registry (si Lumena le câble directement)
      2. deps.lumena.tool_system._tool_registry (chemin canonique)
      3. None → caller 503 registry_writer_unavailable

    INTERDIT : instancier un nouveau registre. Le registry_writer DOIT être
    l'instance runtime utilisée par le dispatch ReAct, sinon les handlers
    register par activate ne seront pas visibles par les outils.
    """
    try:
        from web.routes import deps as _deps
        lumena = getattr(_deps, "lumena", None)
        if lumena is None:
            return None
        candidate = getattr(lumena, "_tool_registry", None)
        if candidate is not None:
            return candidate
        tool_system = getattr(lumena, "tool_system", None)
        if tool_system is None:
            return None
        return getattr(tool_system, "_tool_registry", None)
    except Exception:
        return None


def _get_runtime_watcher_singleton() -> Optional[Any]:
    """Phase 20B-3 : accesseur du singleton RuntimeWatcher lifespan.

    Singleton obligatoire (state runners actifs + snapshots inter-requêtes).
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_RUNTIME_WATCHER_SINGLETON", None)
    except Exception:
        return None


def _get_activation_service_singleton() -> Optional[Any]:
    """Phase 20B-3 : accesseur du singleton MCPActivationService lifespan."""
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_ACTIVATION_SERVICE_SINGLETON", None)
    except Exception:
        return None


def _build_activation_service(
    catalog: Any,
    queue: Any,
    install_orchestrator: Any,
    runtime_watcher: Any,
    dry_run: bool,
) -> Optional[Any]:
    """Factory MCPActivationService avec les 8 dépendances Phase 19 câblées.

    - catalog, queue, runtime_watcher : singletons publics
    - install_orchestrator : pour récupérer install_root (cohérence Phase 18)
    - discovery : instance à la demande (lecture seule), require_server_callable=False
    - adapter : façade locale _MCPHandlerAdapterFacade
    - registry_writer : runtime Lumena via _resolve_registry_writer()
    - runner_factory : _build_runner_factory(install_root)
    - client_factory : create_mcp_client_from_runner Phase 19.5

    Si singleton existe et son dry_run match → réutilise. Sinon construit ad-hoc.
    """
    if not _ACTIVATION_SERVICE_AVAILABLE or MCPActivationService is None:
        return None

    singleton = _get_activation_service_singleton()
    if singleton is not None:
        try:
            singleton_dry = bool(singleton.dry_run)
        except Exception:
            singleton_dry = True
        if singleton_dry == dry_run:
            return singleton

    registry_writer = _resolve_registry_writer()
    if registry_writer is None:
        return None
    if not (
        hasattr(registry_writer, "register_dynamic_handler")
        and hasattr(registry_writer, "unregister_dynamic_handler")
    ):
        return None

    install_root = getattr(install_orchestrator, "install_root", None)
    if install_root is None:
        return None

    try:
        from src.mcp.discovery import MCPDiscoveryService
        from src.mcp.policy_attributor import PolicyAttributor
        from src.mcp.client_factory import create_mcp_client_from_runner

        discovery = MCPDiscoveryService(
            catalog=catalog,
            attributor=PolicyAttributor(),
            require_server_callable=False,
        )
        # Fix Q (Phase I-7) : récupérer les singletons credentials + config
        # initialisés en Phase I-6 dans lifespan pour les injecter dans
        # l'ActivationService. Sans eux, runner.start() n'a pas accès au
        # SLACK_BOT_TOKEN → client_initialize_failed garanti.
        # Fix V : `deps` n'est PAS importé au niveau module dans ce fichier
        # (anti-import-circulaire) — la référence nue levait NameError,
        # avalée par le except → factory retournait None → AUCUNE activation
        # possible de toute la session. Pattern local _i6_* réutilisé.
        _creds_singleton = _i6_credentials_singleton()
        _config_singleton = _i6_config_singleton()
        return MCPActivationService(
            catalog=catalog,
            approval_queue=queue,
            discovery=discovery,
            adapter=_MCPHandlerAdapterFacade(),
            registry_writer=registry_writer,
            runtime_watcher=runtime_watcher,
            runner_factory=_build_runner_factory(install_root),
            client_factory=create_mcp_client_from_runner,
            dry_run=dry_run,
            credentials_service=_creds_singleton,
            config_service=_config_singleton,
        )
    except Exception as _build_act_err:
        # Fix V : ne plus avaler silencieusement — ce except a masqué un
        # NameError pendant une session complète (ActivationService mort).
        try:
            from loguru import logger as _lg
            _lg.warning(
                "[MCP] _build_activation_service_default failed: {}: {}",
                type(_build_act_err).__name__,
                _build_act_err,
            )
        except Exception:  # noqa: BLE001
            pass
        return None


def _audit_ui_activate_action(
    *,
    event: str,
    action: str,
    target_server_id: str,
    target_action_id: Optional[str],
    caller_kind: Optional[str],
    live_mode: bool,
    confirmation_received: bool,
    confirmation_phrase_received: Optional[bool] = None,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    marker_consumed_irrecoverable: Optional[bool] = None,
) -> None:
    """Audit UI étendu Phase 20B-3.

    Whitelist stricte. Aucun champ package_spec, version, notes, args,
    confirmation_phrase raw, marker raw, raw ActivationResult / DeactivationResult /
    ApprovalResult, install_root path, handler names register/unregister.
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-3",
        "action": action,
        "target_server_id": target_server_id,
        "live_mode": bool(live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if target_action_id is not None:
        entry["target_action_id"] = target_action_id
    if caller_kind is not None:
        entry["caller_kind"] = caller_kind
    if confirmation_phrase_received is not None:
        entry["confirmation_phrase_received"] = bool(confirmation_phrase_received)
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if marker_consumed_irrecoverable is not None:
        entry["marker_consumed_irrecoverable"] = bool(marker_consumed_irrecoverable)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


# ── 13. POST /api/mcp/activation/propose ──────────────────────────────────────

@router.post(
    "/api/mcp/activation/propose",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_activation_propose(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-3 : propose une activation via MCPActivationService.propose_activation.

    Body : {"confirmed": true, "server_id": "...", "caller_kind": "admin_ui"}

    Pipeline :
      1. confirmation backend
      2. validation server_id (Phase 14 + Windows-safe)
      3. validation caller_kind = "admin_ui"
      4. catalog + queue + install_orchestrator + runtime_watcher singletons publics
      5. catalog.get_server(server_id) → status INSTALLED requis
      6. dry_run → would_propose=true, aucun ticket créé
      7. live → activation_service.propose_activation → ticket_id

    Réponse JSON (whitelist) :
      live : {"proposed": true, "ticket_id": ..., "server_id": ..., "live_mode": true}
      dry  : {"would_propose": true, "server_id": ..., "live_mode": false,
              "forced_dry_run": true}
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    caller_kind = _validate_caller_kind(body.get("caller_kind"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    # Phase 20B-3 : singleton ApprovalQueue obligatoire (héritage 20B-2,
    # pas de fallback ad-hoc).
    queue = _get_approval_queue_singleton()
    if queue is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="queue_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"})

    install_orchestrator = _get_install_orchestrator_singleton()
    if install_orchestrator is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="install_orchestrator_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "install_orchestrator_unavailable"})

    runtime_watcher = _get_runtime_watcher_singleton()
    if runtime_watcher is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="runtime_watcher_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "runtime_watcher_unavailable"})

    try:
        entry = catalog.get_server(server_id)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val != "installed":
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=live, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="server_id_not_installed",
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_not_installed"})

    if not live:
        _audit_ui_activate_action(
            event="ui_action_simulated", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=False, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="simulated",
        )
        return {
            "would_propose": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    activation_service = _build_activation_service(
        catalog, queue, install_orchestrator, runtime_watcher, dry_run=False
    )
    if activation_service is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=True, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="activation_service_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "activation_service_unavailable"})

    _audit_ui_activate_action(
        event="ui_action_requested", action="activation_propose",
        target_server_id=server_id, target_action_id=None,
        caller_kind=caller_kind, live_mode=True, confirmation_received=True,
        actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        proposal = activation_service.propose_activation(
            server_id=server_id, caller_kind=caller_kind
        )
    except Exception:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=True, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="propose_activation_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "propose_activation_failed"})

    ticket_id = getattr(proposal, "approval_ticket_id", None)
    if not isinstance(ticket_id, str) or not ticket_id:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_propose",
            target_server_id=server_id, target_action_id=None,
            caller_kind=caller_kind, live_mode=True, confirmation_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="propose_activation_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "propose_activation_failed"})

    _audit_ui_activate_action(
        event="ui_action_completed", action="activation_propose",
        target_server_id=server_id, target_action_id=ticket_id,
        caller_kind=caller_kind, live_mode=True, confirmation_received=True,
        actor_token_hash=actor_hash, outcome="proposed",
        duration_s=time.monotonic() - start,
    )
    return {
        "proposed": True,
        "ticket_id": ticket_id,
        "server_id": server_id,
        "live_mode": True,
    }


# ── 14. POST /api/mcp/activation/execute ──────────────────────────────────────

@router.post(
    "/api/mcp/activation/execute",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_activation_execute(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-3 : exécute une activation INSTALLED → ACTIVE.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>",
            "server_id": "...", "marker": "<uuid4 hex>"}

    Pipeline live :
      1. confirmation + phrase = server_id exact + marker UUID4 format
      2. catalog + queue + install_orchestrator + runtime_watcher singletons
      3. catalog.get_server(server_id) → status INSTALLED requis
      4. _take_marker(marker) one-shot AVANT activate
      5. validation croisée approval_result.args["server_id"] == body.server_id
      6. activation_service.activate(server_id, approval_result)
      7. Si runner_factory ou activate raise (incluant transport_unsupported:local) :
         marker_consumed_irrecoverable=true + error_code générique activate_failed
         (pas de fuite package_spec via error_code spécialisé)

    Pipeline dry_run :
      - ZERO call _take_marker
      - ZERO call activate
      - simulation : {would_execute, server_id, live_mode:false, forced_dry_run:true}
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    _validate_confirmation_phrase(body, expected=server_id)
    marker = _validate_marker(body.get("marker"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    queue = _get_approval_queue_singleton()
    if queue is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="queue_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"})

    install_orchestrator = _get_install_orchestrator_singleton()
    if install_orchestrator is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="install_orchestrator_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "install_orchestrator_unavailable"})

    runtime_watcher = _get_runtime_watcher_singleton()
    if runtime_watcher is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="runtime_watcher_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "runtime_watcher_unavailable"})

    try:
        entry = catalog.get_server(server_id)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val != "installed":
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_not_installed",
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_not_installed"})

    # ── Dry-run STRICT : ZÉRO call _take_marker / activate ────────────────
    if not live:
        _audit_ui_activate_action(
            event="ui_action_simulated", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=False, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="simulated",
        )
        return {
            "would_execute": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    # ── Live : consommer marker AVANT activate (one-shot strict) ──────────
    approval_result = _take_marker(marker)
    if approval_result is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="marker_not_found_or_expired",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "marker_not_found_or_expired"})

    # Validation croisée args["server_id"]
    args = getattr(approval_result, "args", None)
    args_server_id = None
    if isinstance(args, dict):
        args_server_id = args.get("server_id")
    if args_server_id != server_id:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="marker_server_id_mismatch",
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "marker_server_id_mismatch"})

    activation_service = _build_activation_service(
        catalog, queue, install_orchestrator, runtime_watcher, dry_run=False
    )
    if activation_service is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="activation_service_unavailable",
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "activation_service_unavailable"})

    _audit_ui_activate_action(
        event="ui_action_requested", action="activation_execute",
        target_server_id=server_id, target_action_id=None,
        caller_kind=None, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        activation_result = activation_service.activate(
            server_id=server_id, approval_result=approval_result
        )
    except Exception:
        # Inclut le cas runner_factory levant ValueError("transport_unsupported:...")
        # quand entry.package_spec n'est pas npm:/pypi:. error_code générique
        # (anti canal latéral sur package_spec).
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="activate_failed",
            duration_s=time.monotonic() - start,
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "activate_failed"})

    success = bool(getattr(activation_result, "success", False))
    if not success:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_execute",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="activate_failed",
            duration_s=time.monotonic() - start,
            marker_consumed_irrecoverable=True,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "activate_failed"})

    try:
        new_entry = catalog.get_server(server_id)
        new_status = (
            new_entry.status.value
            if (new_entry is not None and new_entry.status is not None)
            else "ACTIVE"
        )
    except Exception:
        new_status = "ACTIVE"

    _audit_ui_activate_action(
        event="ui_action_completed", action="activation_execute",
        target_server_id=server_id, target_action_id=None,
        caller_kind=None, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        outcome="activated", duration_s=time.monotonic() - start,
    )
    return {
        "activated": True,
        "server_id": server_id,
        "status": str(new_status).upper(),
        "live_mode": True,
    }


# ── 15. POST /api/mcp/activation/deactivate ───────────────────────────────────

@router.post(
    "/api/mcp/activation/deactivate",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_activation_deactivate(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-3 : désactive un server ACTIVE → INSTALLED.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>", "server_id": "..."}
    (pas de marker — action de protection sans approval gate)

    Pipeline live :
      1. confirmation + phrase = server_id exact
      2. catalog + queue + install_orchestrator + runtime_watcher singletons
      3. catalog.get_server(server_id) → status ACTIVE requis
      4. activation_service.deactivate(server_id) (stop runner + unregister handlers)

    Pipeline dry_run :
      - ZERO call deactivate
      - simulation : {would_deactivate, server_id, live_mode:false, forced_dry_run:true}
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    _validate_confirmation_phrase(body, expected=server_id)

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    queue = _get_approval_queue_singleton()
    if queue is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="queue_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "queue_unavailable"})

    install_orchestrator = _get_install_orchestrator_singleton()
    if install_orchestrator is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="install_orchestrator_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "install_orchestrator_unavailable"})

    runtime_watcher = _get_runtime_watcher_singleton()
    if runtime_watcher is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="runtime_watcher_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "runtime_watcher_unavailable"})

    try:
        entry = catalog.get_server(server_id)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val != "active":
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_not_active",
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_not_active"})

    if not live:
        _audit_ui_activate_action(
            event="ui_action_simulated", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=False, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="simulated",
        )
        return {
            "would_deactivate": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    activation_service = _build_activation_service(
        catalog, queue, install_orchestrator, runtime_watcher, dry_run=False
    )
    if activation_service is None:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="activation_service_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "activation_service_unavailable"})

    _audit_ui_activate_action(
        event="ui_action_requested", action="activation_deactivate",
        target_server_id=server_id, target_action_id=None,
        caller_kind=None, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        deactivation_result = activation_service.deactivate(server_id)
    except Exception:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="deactivate_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "deactivate_failed"})

    success = bool(getattr(deactivation_result, "success", False))
    if not success:
        _audit_ui_activate_action(
            event="ui_action_failed", action="activation_deactivate",
            target_server_id=server_id, target_action_id=None,
            caller_kind=None, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="deactivate_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "deactivate_failed"})

    try:
        new_entry = catalog.get_server(server_id)
        new_status = (
            new_entry.status.value
            if (new_entry is not None and new_entry.status is not None)
            else "INSTALLED"
        )
    except Exception:
        new_status = "INSTALLED"

    _audit_ui_activate_action(
        event="ui_action_completed", action="activation_deactivate",
        target_server_id=server_id, target_action_id=None,
        caller_kind=None, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        outcome="deactivated", duration_s=time.monotonic() - start,
    )
    return {
        "deactivated": True,
        "server_id": server_id,
        "status": str(new_status).upper(),
        "live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-4 — Catalog mutations UI (add / quarantine / restore / remove)
# ══════════════════════════════════════════════════════════════════════════════
#
# 4 routes POST mutatives :
#   16. POST /api/mcp/catalog/add
#   17. POST /api/mcp/catalog/{server_id}/quarantine
#   18. POST /api/mcp/catalog/{server_id}/restore
#   19. POST /api/mcp/catalog/{server_id}/remove
#
# Garde-fous obligatoires :
#   1. verify_admin_token
#   2. _assert_confirmed(body)
#   3. add : modal niveau 1 (pas de phrase, mais confirmed=true requis)
#      quarantine/restore/remove : phrase = server_id exact (saisie texte UI)
#   4. validators répliqués Phase 14 sans import privé (lève HTTPException)
#   5. Singleton MCPServerCatalog obligatoire (réutilisation 20B-2)
#   6. restore : target_status whitelist {"installed"} uniquement (v2)
#   7. remove sur status ACTIVE refusé (force deactivate 20B-3 d'abord)
#   8. Dry-run STRICT : 0 call add_server/update_status/remove_server
#   9. Audit UI étendu :
#      - package_spec réduit au transport (npm/pypi/local/unknown)
#      - trust_score réduit à trust_score_set (bool)
#      - JAMAIS display_name, version, notes raw, ServerEntry brut,
#        confirmation_phrase raw, raw body, CatalogError message
#  10. Réponse whitelist : aucun ServerEntry brut exposé
#  11. error_code court whitelist (anti fuite via message d'exception)
#  12. Aucun appel ApprovalQueue / install / activation depuis ces handlers
#  13. Aucun marker UUID4
# ══════════════════════════════════════════════════════════════════════════════


# ── Replicated Phase 14 regexes (sans import privé src/mcp/server_catalog.py) ──

_CATALOG_DISPLAY_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")
_CATALOG_OWNER_PROFILE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_CATALOG_VERSION_RE = re.compile(r"^[a-zA-Z0-9._\-+]{1,64}$")
_CATALOG_NOTES_RE = re.compile(r"^[a-zA-Z0-9 _:.\-]{0,256}$")

# Aligné Phase 14 _PKG_NPM_RE / _PKG_PYPI_RE / _PKG_LOCAL_RE
_CATALOG_PKG_NPM_RE = re.compile(
    r"^npm:(?:@[a-z0-9][a-z0-9\-_.]{0,63}/)?[a-z0-9][a-z0-9\-_.]{0,63}$"
)
_CATALOG_PKG_PYPI_RE = re.compile(r"^pypi:[a-zA-Z][a-zA-Z0-9_\-.]{0,63}$")
_CATALOG_PKG_LOCAL_RE = re.compile(r"^local:[a-z0-9][a-z0-9_\-.]{0,63}$")

_CATALOG_PKG_FORBIDDEN_GLOBAL = (
    " ", "\t", "\n", "\r", "\\", ";", "&", "|", "\x00",
    '"', "'", "`", "$",
)

_CATALOG_TARGET_STATUS_RESTORE = frozenset({"installed"})


def _validate_display_name_format(raw: Any) -> str:
    if not isinstance(raw, str) or not _CATALOG_DISPLAY_NAME_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "display_name_invalid"},
        )
    return raw


def _validate_owner_profile_format(raw: Any) -> str:
    if not isinstance(raw, str) or not _CATALOG_OWNER_PROFILE_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "owner_profile_invalid"},
        )
    return raw


def _validate_version_format(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not _CATALOG_VERSION_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "version_invalid"},
        )
    return raw


def _validate_trust_score_format(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "trust_score_invalid"},
        )
    if not isinstance(raw, int):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "trust_score_invalid"},
        )
    if raw < 0 or raw > 100:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "trust_score_invalid"},
        )
    return raw


def _validate_notes_format(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not _CATALOG_NOTES_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "notes_invalid"},
        )
    return raw


def _validate_package_spec_format(raw: Any) -> str:
    """Réplique stricte Phase 14 _validate_package_spec sans import privé."""
    if not isinstance(raw, str) or not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "package_spec_invalid"},
        )
    for ch in _CATALOG_PKG_FORBIDDEN_GLOBAL:
        if ch in raw:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "package_spec_invalid"},
            )
    if (
        len(raw) >= 2
        and raw[1] == ":"
        and raw[0].isalpha()
        and not raw.startswith(("npm:", "pypi:", "local:"))
    ):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "package_spec_invalid"},
        )
    if ".." in raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "package_spec_invalid"},
        )
    if raw.startswith("npm:"):
        if not _CATALOG_PKG_NPM_RE.match(raw):
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "package_spec_invalid"},
            )
        return raw
    if raw.startswith("pypi:"):
        if not _CATALOG_PKG_PYPI_RE.match(raw):
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "package_spec_invalid"},
            )
        return raw
    if raw.startswith("local:"):
        if not _CATALOG_PKG_LOCAL_RE.match(raw):
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "package_spec_invalid"},
            )
        return raw
    raise HTTPException(
        status_code=400,
        detail={"error": True, "error_code": "package_spec_invalid"},
    )


def _validate_target_status_restore(raw: Any) -> str:
    """Whitelist v2 : restore vers INSTALLED uniquement."""
    if not isinstance(raw, str) or raw not in _CATALOG_TARGET_STATUS_RESTORE:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "target_status_invalid"},
        )
    return raw


def _extract_package_spec_transport(raw: Any) -> str:
    """Retourne le préfixe du transport pour audit UI (anti-fuite).

    Jamais le package complet. Retours possibles : "npm" / "pypi" / "local" / "unknown".
    """
    if not isinstance(raw, str):
        return "unknown"
    if raw.startswith("npm:"):
        return "npm"
    if raw.startswith("pypi:"):
        return "pypi"
    if raw.startswith("local:"):
        return "local"
    return "unknown"


def _audit_ui_catalog_action(
    *,
    event: str,
    action: str,
    target_server_id: str,
    live_mode: bool,
    confirmation_received: bool,
    confirmation_phrase_received: Optional[bool] = None,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    owner_profile: Optional[str] = None,
    trust_score_set: Optional[bool] = None,
    package_spec_transport: Optional[str] = None,
    idempotent: Optional[bool] = None,
) -> None:
    """Audit UI étendu Phase 20B-4.

    Whitelist stricte. Aucun champ display_name, version, notes raw,
    package_spec complet, trust_score valeur, confirmation_phrase raw,
    raw ServerEntry, CatalogError message, raw body.
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-4",
        "action": action,
        "target_server_id": target_server_id,
        "live_mode": bool(live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if confirmation_phrase_received is not None:
        entry["confirmation_phrase_received"] = bool(confirmation_phrase_received)
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if from_status is not None:
        entry["from_status"] = from_status
    if to_status is not None:
        entry["to_status"] = to_status
    if owner_profile is not None:
        entry["owner_profile"] = owner_profile
    if trust_score_set is not None:
        entry["trust_score_set"] = bool(trust_score_set)
    if package_spec_transport is not None:
        entry["package_spec_transport"] = package_spec_transport
    if idempotent is not None:
        entry["idempotent"] = bool(idempotent)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


# ── 16. POST /api/mcp/catalog/add ─────────────────────────────────────────────

@router.post(
    "/api/mcp/catalog/add",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_add(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-4 : ajoute un server au Catalog (status initial DECLARED).

    Body : {"confirmed": true, "server_id", "display_name", "package_spec",
            "owner_profile", "version"?, "trust_score"?, "notes"?}

    Pipeline :
      1. confirmation backend
      2. validation server_id (Phase 14 + Windows-safe)
      3. validation display_name / package_spec / owner_profile / version /
         trust_score / notes (réplique Phase 14)
      4. catalog singleton
      5. dry_run → would_add=true (n'appelle JAMAIS catalog.add_server)
      6. live → catalog.add_server(...) — capture server_already_exists → 409
    """
    body = body or {}
    _assert_confirmed(body)
    server_id = _validate_server_id_format(body.get("server_id"))
    display_name = _validate_display_name_format(body.get("display_name"))
    package_spec = _validate_package_spec_format(body.get("package_spec"))
    owner_profile = _validate_owner_profile_format(body.get("owner_profile"))
    version = _validate_version_format(body.get("version"))
    trust_score = _validate_trust_score_format(body.get("trust_score"))
    notes = _validate_notes_format(body.get("notes"))

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)
    pkg_transport = _extract_package_spec_transport(package_spec)
    trust_set = trust_score is not None

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_add",
            target_server_id=server_id, live_mode=live,
            confirmation_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
            owner_profile=owner_profile, trust_score_set=trust_set,
            package_spec_transport=pkg_transport,
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    if not live:
        _audit_ui_catalog_action(
            event="ui_action_simulated", action="catalog_add",
            target_server_id=server_id, live_mode=False,
            confirmation_received=True, actor_token_hash=actor_hash,
            outcome="simulated",
            owner_profile=owner_profile, trust_score_set=trust_set,
            package_spec_transport=pkg_transport,
        )
        return {
            "would_add": True,
            "server_id": server_id,
            "live_mode": False,
            "forced_dry_run": True,
        }

    _audit_ui_catalog_action(
        event="ui_action_requested", action="catalog_add",
        target_server_id=server_id, live_mode=True,
        confirmation_received=True, actor_token_hash=actor_hash,
        owner_profile=owner_profile, trust_score_set=trust_set,
        package_spec_transport=pkg_transport,
    )
    start = time.monotonic()
    try:
        add_kwargs: Dict[str, Any] = {
            "server_id": server_id,
            "display_name": display_name,
            "package_spec": package_spec,
            "owner_profile": owner_profile,
        }
        if version is not None:
            add_kwargs["version"] = version
        if trust_score is not None:
            add_kwargs["trust_score"] = trust_score
        if notes is not None:
            add_kwargs["notes"] = notes
        catalog.add_server(**add_kwargs)
    except Exception as exc:
        # Distinguer server_already_exists (409) du reste (400 add_failed)
        message = str(getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else "")
        is_exists = "server_already_exists" in message or "already_exists" in message
        code = "server_already_exists" if is_exists else "add_failed"
        http_status = 409 if is_exists else 400
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_add",
            target_server_id=server_id, live_mode=True,
            confirmation_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code=code,
            duration_s=time.monotonic() - start,
            owner_profile=owner_profile, trust_score_set=trust_set,
            package_spec_transport=pkg_transport,
        )
        raise HTTPException(status_code=http_status,
            detail={"error": True, "error_code": code})

    _audit_ui_catalog_action(
        event="ui_action_completed", action="catalog_add",
        target_server_id=server_id, live_mode=True,
        confirmation_received=True, actor_token_hash=actor_hash,
        outcome="added", duration_s=time.monotonic() - start,
        owner_profile=owner_profile, trust_score_set=trust_set,
        package_spec_transport=pkg_transport,
    )
    return {
        "added": True,
        "server_id": server_id,
        "status": "DECLARED",
        "live_mode": True,
    }


# ── 17. POST /api/mcp/catalog/{server_id}/quarantine ──────────────────────────

@router.post(
    "/api/mcp/catalog/{server_id}/quarantine",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_quarantine(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-4 : transition status → QUARANTINED.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>", "server_id": "..."}
    """
    body = body or {}
    sid = _validate_server_id_format(server_id)
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=sid)
    # Cohérence body.server_id si fourni
    body_sid = body.get("server_id")
    if body_sid is not None and body_sid != sid:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"})

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_quarantine",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    try:
        entry = catalog.get_server(sid)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_quarantine",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None

    if status_val == "removed":
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_quarantine",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_invalid_status",
            from_status=status_val,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_status"})

    if status_val == "quarantined":
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_quarantine",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_already_quarantined",
            from_status=status_val,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_already_quarantined"})

    if not live:
        _audit_ui_catalog_action(
            event="ui_action_simulated", action="catalog_quarantine",
            target_server_id=sid, live_mode=False, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="simulated", from_status=status_val, to_status="quarantined",
        )
        return {
            "would_quarantine": True,
            "server_id": sid,
            "live_mode": False,
            "forced_dry_run": True,
        }

    _audit_ui_catalog_action(
        event="ui_action_requested", action="catalog_quarantine",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        from_status=status_val, to_status="quarantined",
    )
    start = time.monotonic()
    try:
        from src.mcp.server_catalog import ServerStatus as _SS
        catalog.update_status(sid, _SS.QUARANTINED)
    except Exception as exc:
        message = str(getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else "")
        code = (
            "status_transition_invalid"
            if "status_transition_invalid" in message
            else "quarantine_failed"
        )
        http_status = 400 if code == "status_transition_invalid" else 500
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_quarantine",
            target_server_id=sid, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code=code,
            duration_s=time.monotonic() - start,
            from_status=status_val,
        )
        raise HTTPException(status_code=http_status,
            detail={"error": True, "error_code": code})

    _audit_ui_catalog_action(
        event="ui_action_completed", action="catalog_quarantine",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        outcome="quarantined", duration_s=time.monotonic() - start,
        from_status=status_val, to_status="quarantined",
    )
    return {
        "quarantined": True,
        "server_id": sid,
        "status": "QUARANTINED",
        "live_mode": True,
    }


# ── 18. POST /api/mcp/catalog/{server_id}/restore ─────────────────────────────

@router.post(
    "/api/mcp/catalog/{server_id}/restore",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_restore(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-4 v2 : restore QUARANTINED → INSTALLED uniquement.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>",
            "server_id": "...", "target_status": "installed"}

    Pour revenir ACTIVE après restore, passer par Phase 20B-3 activate.
    """
    body = body or {}
    sid = _validate_server_id_format(server_id)
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=sid)
    target = _validate_target_status_restore(body.get("target_status"))
    body_sid = body.get("server_id")
    if body_sid is not None and body_sid != sid:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"})

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_restore",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    try:
        entry = catalog.get_server(sid)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_restore",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None

    if status_val != "quarantined":
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_restore",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_not_quarantined",
            from_status=status_val,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_not_quarantined"})

    if not live:
        _audit_ui_catalog_action(
            event="ui_action_simulated", action="catalog_restore",
            target_server_id=sid, live_mode=False, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="simulated", from_status=status_val, to_status=target,
        )
        return {
            "would_restore": True,
            "server_id": sid,
            "target_status": target,
            "live_mode": False,
            "forced_dry_run": True,
        }

    _audit_ui_catalog_action(
        event="ui_action_requested", action="catalog_restore",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        from_status=status_val, to_status=target,
    )
    start = time.monotonic()
    try:
        from src.mcp.server_catalog import ServerStatus as _SS
        _target_enum = _SS(target)
        catalog.update_status(sid, _target_enum)
    except Exception as exc:
        message = str(getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else "")
        code = (
            "status_transition_invalid"
            if "status_transition_invalid" in message
            else "restore_failed"
        )
        http_status = 400 if code == "status_transition_invalid" else 500
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_restore",
            target_server_id=sid, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code=code,
            duration_s=time.monotonic() - start,
            from_status=status_val,
        )
        raise HTTPException(status_code=http_status,
            detail={"error": True, "error_code": code})

    _audit_ui_catalog_action(
        event="ui_action_completed", action="catalog_restore",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        outcome="restored", duration_s=time.monotonic() - start,
        from_status=status_val, to_status=target,
    )
    return {
        "restored": True,
        "server_id": sid,
        "status": target.upper(),
        "live_mode": True,
    }


# ── 19. POST /api/mcp/catalog/{server_id}/remove ──────────────────────────────

@router.post(
    "/api/mcp/catalog/{server_id}/remove",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_remove(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-4 v2 : soft-delete (status → REMOVED).

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>", "server_id": "..."}

    Refusé sur status ACTIVE (force chemin deactivate 20B-3 d'abord).
    Idempotent côté Phase 14 : REMOVED → REMOVED retourne True remonté
    avec idempotent=true.
    """
    body = body or {}
    sid = _validate_server_id_format(server_id)
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=sid)
    body_sid = body.get("server_id")
    if body_sid is not None and body_sid != sid:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"})

    live = _live_mode_enabled()
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_remove",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="catalog_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    try:
        entry = catalog.get_server(sid)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_remove",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_not_found",
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None

    # Refus ACTIVE (force deactivate 20B-3 d'abord)
    if status_val == "active":
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_remove",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="server_id_invalid_status",
            from_status=status_val,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_status"})

    # Idempotent : REMOVED → REMOVED
    if status_val == "removed":
        _audit_ui_catalog_action(
            event="ui_action_completed", action="catalog_remove",
            target_server_id=sid, live_mode=live, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="removed", from_status=status_val, to_status="removed",
            idempotent=True,
        )
        return {
            "removed": True,
            "server_id": sid,
            "status": "REMOVED",
            "live_mode": live,
            "idempotent": True,
        }

    if not live:
        _audit_ui_catalog_action(
            event="ui_action_simulated", action="catalog_remove",
            target_server_id=sid, live_mode=False, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="simulated", from_status=status_val, to_status="removed",
        )
        return {
            "would_remove": True,
            "server_id": sid,
            "live_mode": False,
            "forced_dry_run": True,
        }

    _audit_ui_catalog_action(
        event="ui_action_requested", action="catalog_remove",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        from_status=status_val, to_status="removed",
    )
    start = time.monotonic()
    try:
        catalog.remove_server(sid)
    except Exception:
        _audit_ui_catalog_action(
            event="ui_action_failed", action="catalog_remove",
            target_server_id=sid, live_mode=True, confirmation_received=True,
            confirmation_phrase_received=True, actor_token_hash=actor_hash,
            outcome="error", error_code="remove_failed",
            duration_s=time.monotonic() - start,
            from_status=status_val,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "remove_failed"})

    _audit_ui_catalog_action(
        event="ui_action_completed", action="catalog_remove",
        target_server_id=sid, live_mode=True, confirmation_received=True,
        confirmation_phrase_received=True, actor_token_hash=actor_hash,
        outcome="removed", duration_s=time.monotonic() - start,
        from_status=status_val, to_status="removed",
    )
    return {
        "removed": True,
        "server_id": sid,
        "status": "REMOVED",
        "live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-5 — AutoApprove patterns CRUD UI (mutations de policy future)
# ══════════════════════════════════════════════════════════════════════════════
#
# Point central : créer un pattern AutoApprove = créer une autorisation
# FUTURE qui pourra court-circuiter ApprovalQueue. Doctrine plus stricte
# que 20B-1/2/3/4.
#
# 4 routes :
#   20. GET  /api/mcp/autoapprove/patterns
#   21. GET  /api/mcp/autoapprove/patterns/{pattern_id}
#   22. POST /api/mcp/autoapprove/add
#   23. POST /api/mcp/autoapprove/{pattern_id}/remove
#
# Garde-fous obligatoires :
#   1. verify_admin_token
#   2. Add modal niveau 2 : phrase fixe "CREATE-AUTOAPPROVE-PATTERN"
#      Remove modal niveau 2 : phrase = pattern_id complet 32 chars
#   3. Validators répliqués Phase 11 sans import privé
#   4. Pré-validation args_constraints stricte (10 clés whitelist, types,
#      taille/profondeur, max 4096 chars JSON) puis délégation finale
#      Phase 11 engine.add_pattern
#   5. Singleton AutoApproveEngine obligatoire
#   6. Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1
#      Sinon dry-run forcé (0 call add_pattern / remove_pattern)
#   7. error_code unifié args_constraints_invalid (anti canal latéral)
#   8. GET list/détail : métadonnées agrégées seulement
#   9. JAMAIS tool_name_pattern raw, args_constraints raw,
#      caller_kinds_allowed raw exposés
#  10. Aucun déchiffrement Fernet côté route (le pattern reste chiffré)
#  11. Aucun update/PUT/PATCH (Phase 11 immutable)
#  12. Aucun appel ApprovalQueue/Install/Activation/Catalog mutation/marker
#      dans les handlers AutoApprove
#  13. Aucun subprocess direct
# ══════════════════════════════════════════════════════════════════════════════


# ── Replicated Phase 11 constants (sans import privé) ──

_AUTOAPPROVE_ADD_PHRASE = "CREATE-AUTOAPPROVE-PATTERN"

# Aligné auto_approve._VALID_CALLER_KINDS
_AUTOAPPROVE_VALID_CALLER_KINDS = frozenset(
    {"react", "codeagent", "autonomy", "scheduler", "daemon", "silent"}
)

# Aligné auto_approve._TOOL_NAME_EXACT_RE / _GLOB_RE
_AUTOAPPROVE_TOOL_NAME_MIN_PREFIX_LEN = 8
_AUTOAPPROVE_TOOL_NAME_EXACT_RE = re.compile(
    r"^mcp__[A-Za-z0-9_\-.]+__[A-Za-z0-9_\-.]+$"
)
_AUTOAPPROVE_TOOL_NAME_GLOB_RE = re.compile(
    r"^mcp__[A-Za-z0-9_\-.]+__\*$"
)

# Aligné auto_approve._GLOB_ALLOWED_POLICIES (READ_ONLY, EXTERNAL_READ)
_AUTOAPPROVE_GLOB_ALLOWED_POLICY_VALUES = frozenset({"read_only", "external_read"})

# Aligné auto_approve._KNOWN_CONSTRAINT_KEYS (whitelist 10 clés DSL Phase 11)
_AUTOAPPROVE_CONSTRAINT_KEYS = frozenset({
    "to_allowlist", "channel_allowlist", "url_allowlist",
    "account_allowlist", "recipient_allowlist",
    "subject_max_chars", "body_max_chars",
    "amount_max_eur", "amount_max_usd",
    "attachments_forbidden",
})

_AUTOAPPROVE_CONSTRAINT_LIST_KEYS = frozenset({
    "to_allowlist", "channel_allowlist", "url_allowlist",
    "account_allowlist", "recipient_allowlist",
})

_AUTOAPPROVE_CONSTRAINT_INT_KEYS = frozenset({
    "subject_max_chars", "body_max_chars",
})

_AUTOAPPROVE_CONSTRAINT_NUMBER_KEYS = frozenset({
    "amount_max_eur", "amount_max_usd",
})

_AUTOAPPROVE_CONSTRAINT_BOOL_KEYS = frozenset({"attachments_forbidden"})

# Garde-fous taille/profondeur (anti-DoS + anti-fuite)
_AUTOAPPROVE_CONSTRAINTS_MAX_KEYS = 10
_AUTOAPPROVE_CONSTRAINTS_MAX_LIST_LEN = 64
_AUTOAPPROVE_CONSTRAINTS_MAX_STR_LEN = 256
_AUTOAPPROVE_CONSTRAINTS_MAX_INT = 10_000_000
_AUTOAPPROVE_CONSTRAINTS_MAX_JSON_LEN = 4096

# Whitelist policy values (MCPPolicy enum public src.mcp.policy)
_AUTOAPPROVE_VALID_POLICY_VALUES = frozenset({
    "read_only", "external_read", "external_write_recoverable",
    "local_write", "external_write_irreversible", "secrets_auth",
})

_AUTOAPPROVE_PROFILE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def _autoapprove_live_mode_enabled() -> bool:
    """Opt-in dédié `LUMENA_MCP_AUTOAPPROVE_LIVE` (en plus de LUMENA_MCP_LIVE).

    Le double opt-in est requis pour toute mutation : si l'un des deux env
    est absent/falsy, dry-run forcé (0 call add_pattern / remove_pattern).
    """
    raw = os.environ.get("LUMENA_MCP_AUTOAPPROVE_LIVE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _autoapprove_double_optin() -> bool:
    """Renvoie True ssi LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1."""
    return _live_mode_enabled() and _autoapprove_live_mode_enabled()


def _validate_pattern_id_format(raw: Any) -> str:
    """uuid4 hex strict (réplique 20B-1 / héritage `_validate_action_id`)."""
    if not isinstance(raw, str) or not _ACTION_ID_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "pattern_id_invalid_format"},
        )
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "pattern_id_invalid_format"},
        )
    if parsed.version != 4 or parsed.hex != raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "pattern_id_invalid_format"},
        )
    return raw


def _validate_profile_format(raw: Any) -> str:
    if not isinstance(raw, str) or not _AUTOAPPROVE_PROFILE_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "profile_invalid"},
        )
    return raw


def _validate_kind_format(raw: Any) -> str:
    """Phase 11 `_validate_kind` : non-empty str (pas de whitelist enum)."""
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "kind_invalid"},
        )
    if len(raw) > 64:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "kind_invalid"},
        )
    return raw.strip()


def _validate_policy_format(raw: Any) -> str:
    """Whitelist policy values (MCPPolicy enum public)."""
    if not isinstance(raw, str) or raw not in _AUTOAPPROVE_VALID_POLICY_VALUES:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "policy_invalid"},
        )
    return raw


def _validate_tool_name_pattern_format(raw: Any, policy_value: str) -> str:
    """Réplique Phase 11 `_validate_tool_name_pattern` :
      - exact `mcp__server__tool` autorisé toutes policies
      - glob `mcp__server__*` autorisé seulement READ_ONLY / EXTERNAL_READ
      - `*`, `mcp__*`, `**` interdits
      - longueur min 8
    """
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "tool_name_pattern_invalid"},
        )
    s = raw.strip()
    if len(s) < _AUTOAPPROVE_TOOL_NAME_MIN_PREFIX_LEN:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "tool_name_pattern_invalid"},
        )
    if s in ("*", "mcp__*", "**"):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "tool_name_pattern_invalid"},
        )
    if _AUTOAPPROVE_TOOL_NAME_EXACT_RE.match(s):
        return s
    if _AUTOAPPROVE_TOOL_NAME_GLOB_RE.match(s):
        if policy_value not in _AUTOAPPROVE_GLOB_ALLOWED_POLICY_VALUES:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "tool_name_pattern_invalid"},
            )
        return s
    raise HTTPException(
        status_code=400,
        detail={"error": True, "error_code": "tool_name_pattern_invalid"},
    )


def _validate_caller_kinds_allowed_format(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "caller_kinds_allowed_invalid"},
        )
    if len(raw) > len(_AUTOAPPROVE_VALID_CALLER_KINDS):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "caller_kinds_allowed_invalid"},
        )
    out: List[str] = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "caller_kinds_allowed_invalid"},
            )
        if entry not in _AUTOAPPROVE_VALID_CALLER_KINDS:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "caller_kinds_allowed_invalid"},
            )
        if entry in seen:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "caller_kinds_allowed_invalid"},
            )
        seen.add(entry)
        out.append(entry)
    return out


def _validate_quota_max_per_day_format(raw: Any) -> int:
    if isinstance(raw, bool):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "quota_max_per_day_invalid"},
        )
    if not isinstance(raw, int):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "quota_max_per_day_invalid"},
        )
    if raw <= 0 or raw > 1_000_000:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "quota_max_per_day_invalid"},
        )
    return raw


def _validate_expires_at_format(raw: Any) -> str:
    """ISO 8601 + futur (la validation max_lifetime_days est déléguée Phase 11)."""
    if not isinstance(raw, str) or not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "expires_at_invalid"},
        )
    if len(raw) > 64:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "expires_at_invalid"},
        )
    s = raw
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "expires_at_invalid"},
        )
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "expires_at_invalid"},
        )
    if parsed <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "expires_at_invalid"},
        )
    return raw


def _validate_args_constraints_format(raw: Any) -> Dict[str, Any]:
    """Pré-validation web stricte avant délégation à engine.add_pattern.

    Whitelist 10 clés Phase 11 + types stricts + taille/profondeur bornées
    + max 4096 chars JSON sérialisé. Tous les refus utilisent l'unique
    error_code `args_constraints_invalid` (anti canal latéral).

    Validation finale (sémantique DSL, normalisation) déléguée à
    AutoApproveEngine.add_pattern (source de vérité Phase 11).
    """
    def _fail():
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "args_constraints_invalid"},
        )

    if not isinstance(raw, dict):
        _fail()
    if not raw:
        _fail()
    if len(raw) > _AUTOAPPROVE_CONSTRAINTS_MAX_KEYS:
        _fail()
    for key, value in raw.items():
        if not isinstance(key, str):
            _fail()
        if key not in _AUTOAPPROVE_CONSTRAINT_KEYS:
            _fail()
        if key in _AUTOAPPROVE_CONSTRAINT_LIST_KEYS:
            if not isinstance(value, list):
                _fail()
            if len(value) > _AUTOAPPROVE_CONSTRAINTS_MAX_LIST_LEN:
                _fail()
            for entry in value:
                if isinstance(entry, bool) or not isinstance(entry, (str, int, float)):
                    _fail()
                if isinstance(entry, str):
                    if len(entry) > _AUTOAPPROVE_CONSTRAINTS_MAX_STR_LEN:
                        _fail()
                    for ch in entry:
                        if ord(ch) < 0x20 or ord(ch) == 0x7f:
                            _fail()
        elif key in _AUTOAPPROVE_CONSTRAINT_INT_KEYS:
            if isinstance(value, bool):
                _fail()
            if not isinstance(value, int):
                _fail()
            if value <= 0 or value > _AUTOAPPROVE_CONSTRAINTS_MAX_INT:
                _fail()
        elif key in _AUTOAPPROVE_CONSTRAINT_NUMBER_KEYS:
            if isinstance(value, bool):
                _fail()
            if not isinstance(value, (int, float)):
                _fail()
            if value <= 0 or value > _AUTOAPPROVE_CONSTRAINTS_MAX_INT:
                _fail()
        elif key in _AUTOAPPROVE_CONSTRAINT_BOOL_KEYS:
            if not isinstance(value, bool):
                _fail()
        else:
            _fail()
    try:
        encoded = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        _fail()
        return raw  # unreachable
    if len(encoded) > _AUTOAPPROVE_CONSTRAINTS_MAX_JSON_LEN:
        _fail()
    return raw


def _get_auto_approve_engine_singleton() -> Optional[Any]:
    """Phase 20B-5 : singleton AutoApproveEngine lifespan.

    Si None (module non importable ou init échouée), les routes mutatives
    AutoApprove répondent {"error_code": "engine_unavailable"}.
    """
    try:
        from web.routes import deps as _deps
        return getattr(_deps, "_MCP_AUTO_APPROVE_ENGINE_SINGLETON", None)
    except Exception:
        return None


def _extract_args_constraints_meta(args_constraints: Any) -> Dict[str, Any]:
    """Métadonnées agrégées sans contenu DSL (anti-fuite audit)."""
    if not isinstance(args_constraints, dict):
        return {"keys_count": 0, "allowlists_total_entries": 0}
    keys_count = len(args_constraints)
    allowlists_total = 0
    for k, v in args_constraints.items():
        if isinstance(k, str) and k.endswith("_allowlist") and isinstance(v, list):
            allowlists_total += len(v)
    return {
        "keys_count": keys_count,
        "allowlists_total_entries": allowlists_total,
    }


def _serialize_pattern_safe(pattern: Any) -> Dict[str, Any]:
    """Sérialisation whitelist stricte (GET list / détail).

    Aucun tool_name_pattern raw, aucun args_constraints raw, aucun
    caller_kinds_allowed liste. Seulement métadonnées agrégées.
    """
    args_meta = _extract_args_constraints_meta(getattr(pattern, "args_constraints", None))
    try:
        caller_count = len(getattr(pattern, "caller_kinds_allowed", []) or [])
    except Exception:
        caller_count = 0
    try:
        tool_present = bool(getattr(pattern, "tool_name_pattern", None))
    except Exception:
        tool_present = False
    try:
        policy_val = pattern.policy.value if pattern.policy is not None else None
    except Exception:
        policy_val = None
    return {
        "pattern_id": getattr(pattern, "id", None),
        "profile": getattr(pattern, "profile", None),
        "kind": getattr(pattern, "kind", None),
        "policy": policy_val,
        "quota_max_per_day": getattr(pattern, "quota_max_per_day", None),
        "expires_at": getattr(pattern, "expires_at", None),
        "created_at": getattr(pattern, "created_at", None),
        "caller_kinds_count": caller_count,
        "args_constraints_keys_count": args_meta["keys_count"],
        "args_constraints_allowlists_total_entries": args_meta["allowlists_total_entries"],
        "tool_name_pattern_present": tool_present,
    }


def _audit_ui_autoapprove_action(
    *,
    event: str,
    action: str,
    target_pattern_id: Optional[str],
    profile: Optional[str],
    kind: Optional[str],
    policy: Optional[str],
    live_mode: bool,
    autoapprove_live_mode: bool,
    confirmation_received: bool,
    confirmation_phrase_received: Optional[bool] = None,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    caller_kinds_count: Optional[int] = None,
    args_constraints_keys_count: Optional[int] = None,
    args_constraints_allowlists_total_entries: Optional[int] = None,
    quota_max_per_day: Optional[int] = None,
    expires_at: Optional[str] = None,
    idempotent: Optional[bool] = None,
) -> None:
    """Audit UI Phase 20B-5.

    Whitelist stricte. Aucun args_constraints raw, tool_name_pattern raw,
    caller_kinds_allowed liste, confirmation_phrase raw, raw body,
    raw AutoApprovePattern, AutoApproveError message, stack trace.
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-5",
        "action": action,
        "live_mode": bool(live_mode),
        "autoapprove_live_mode": bool(autoapprove_live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if target_pattern_id is not None:
        entry["target_pattern_id"] = target_pattern_id
    if profile is not None:
        entry["profile"] = profile
    if kind is not None:
        entry["kind"] = kind
    if policy is not None:
        entry["policy"] = policy
    if confirmation_phrase_received is not None:
        entry["confirmation_phrase_received"] = bool(confirmation_phrase_received)
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if caller_kinds_count is not None:
        entry["caller_kinds_count"] = int(caller_kinds_count)
    if args_constraints_keys_count is not None:
        entry["args_constraints_keys_count"] = int(args_constraints_keys_count)
    if args_constraints_allowlists_total_entries is not None:
        entry["args_constraints_allowlists_total_entries"] = int(
            args_constraints_allowlists_total_entries
        )
    if quota_max_per_day is not None:
        entry["quota_max_per_day"] = int(quota_max_per_day)
    if expires_at is not None:
        entry["expires_at"] = expires_at
    if idempotent is not None:
        entry["idempotent"] = bool(idempotent)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


# ── 20. GET /api/mcp/autoapprove/patterns ─────────────────────────────────────

@router.get(
    "/api/mcp/autoapprove/patterns",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_autoapprove_list_patterns(
    profile: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Phase 20B-5 : liste les patterns AutoApprove (métadonnées agrégées).

    Aucun tool_name_pattern raw, args_constraints raw, caller_kinds_allowed
    raw. Le pattern reste chiffré côté backend (jamais déchiffré ici).
    """
    if profile is not None:
        profile = _validate_profile_format(profile)
    engine = _get_auto_approve_engine_singleton()
    if engine is None:
        return {"available": False, "reason": "not_loaded", "patterns": []}
    try:
        patterns = engine.list_patterns(profile=profile)
    except Exception:
        return {"available": True, "patterns": []}
    sliced = patterns[:limit]
    return {
        "available": True,
        "patterns": [_serialize_pattern_safe(p) for p in sliced],
        "count": len(sliced),
        "total": len(patterns),
    }


# ── 21. GET /api/mcp/autoapprove/patterns/{pattern_id} ────────────────────────

@router.get(
    "/api/mcp/autoapprove/patterns/{pattern_id}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_autoapprove_get_pattern(pattern_id: str):
    """Phase 20B-5 : détail d'un pattern (métadonnées agrégées)."""
    pid = _validate_pattern_id_format(pattern_id)
    engine = _get_auto_approve_engine_singleton()
    if engine is None:
        return {"available": False, "reason": "not_loaded"}
    try:
        pattern = engine.get_pattern(pid)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "pattern_not_found"},
        )
    if pattern is None:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "error_code": "pattern_not_found"},
        )
    return {
        "available": True,
        "pattern": _serialize_pattern_safe(pattern),
    }


# ── 22. POST /api/mcp/autoapprove/add ─────────────────────────────────────────

@router.post(
    "/api/mcp/autoapprove/add",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_autoapprove_add(
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-5 : crée un pattern AutoApprove.

    Body : {
      "confirmed": true,
      "confirmation_phrase": "CREATE-AUTOAPPROVE-PATTERN",
      "profile", "kind", "tool_name_pattern", "policy",
      "caller_kinds_allowed", "args_constraints",
      "quota_max_per_day", "expires_at",
    }

    Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1.
    Sinon dry-run forcé (0 call add_pattern).
    """
    body = body or {}
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=_AUTOAPPROVE_ADD_PHRASE)
    profile = _validate_profile_format(body.get("profile"))
    kind = _validate_kind_format(body.get("kind"))
    policy_val = _validate_policy_format(body.get("policy"))
    tool_name_pattern = _validate_tool_name_pattern_format(
        body.get("tool_name_pattern"), policy_val
    )
    caller_kinds = _validate_caller_kinds_allowed_format(
        body.get("caller_kinds_allowed")
    )
    args_constraints = _validate_args_constraints_format(
        body.get("args_constraints")
    )
    quota = _validate_quota_max_per_day_format(body.get("quota_max_per_day"))
    expires_at = _validate_expires_at_format(body.get("expires_at"))

    live = _live_mode_enabled()
    aa_live = _autoapprove_live_mode_enabled()
    double_optin = live and aa_live
    actor_hash = _hash_actor_token(authorization)
    args_meta = _extract_args_constraints_meta(args_constraints)

    engine = _get_auto_approve_engine_singleton()
    if engine is None:
        _audit_ui_autoapprove_action(
            event="ui_action_failed", action="autoapprove_pattern_add",
            target_pattern_id=None, profile=profile, kind=kind, policy=policy_val,
            live_mode=live, autoapprove_live_mode=aa_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="engine_unavailable",
            caller_kinds_count=len(caller_kinds),
            args_constraints_keys_count=args_meta["keys_count"],
            args_constraints_allowlists_total_entries=args_meta["allowlists_total_entries"],
            quota_max_per_day=quota, expires_at=expires_at,
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "engine_unavailable"})

    # Double opt-in strict : si l'un des deux env manque, dry-run forcé
    if not double_optin:
        _audit_ui_autoapprove_action(
            event="ui_action_simulated", action="autoapprove_pattern_add",
            target_pattern_id=None, profile=profile, kind=kind, policy=policy_val,
            live_mode=live, autoapprove_live_mode=aa_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="simulated",
            caller_kinds_count=len(caller_kinds),
            args_constraints_keys_count=args_meta["keys_count"],
            args_constraints_allowlists_total_entries=args_meta["allowlists_total_entries"],
            quota_max_per_day=quota, expires_at=expires_at,
        )
        return {
            "would_add": True,
            "profile": profile,
            "live_mode": live,
            "autoapprove_live_mode": aa_live,
            "forced_dry_run": True,
        }

    # Live : délégation à engine.add_pattern (validation finale Phase 11)
    _audit_ui_autoapprove_action(
        event="ui_action_requested", action="autoapprove_pattern_add",
        target_pattern_id=None, profile=profile, kind=kind, policy=policy_val,
        live_mode=True, autoapprove_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash,
        caller_kinds_count=len(caller_kinds),
        args_constraints_keys_count=args_meta["keys_count"],
        args_constraints_allowlists_total_entries=args_meta["allowlists_total_entries"],
        quota_max_per_day=quota, expires_at=expires_at,
    )
    start = time.monotonic()
    try:
        if not _MCP_POLICY_AVAILABLE or MCPPolicy is None:
            raise RuntimeError("policy_enum_unavailable")
        policy_enum = MCPPolicy(policy_val)
        pattern_id = engine.add_pattern(
            profile=profile,
            kind=kind,
            tool_name_pattern=tool_name_pattern,
            policy=policy_enum,
            caller_kinds_allowed=caller_kinds,
            args_constraints=args_constraints,
            quota_max_per_day=quota,
            expires_at=expires_at,
        )
    except Exception:
        # error_code générique anti canal latéral
        _audit_ui_autoapprove_action(
            event="ui_action_failed", action="autoapprove_pattern_add",
            target_pattern_id=None, profile=profile, kind=kind, policy=policy_val,
            live_mode=True, autoapprove_live_mode=True,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="add_pattern_failed",
            duration_s=time.monotonic() - start,
            caller_kinds_count=len(caller_kinds),
            args_constraints_keys_count=args_meta["keys_count"],
            args_constraints_allowlists_total_entries=args_meta["allowlists_total_entries"],
            quota_max_per_day=quota, expires_at=expires_at,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "add_pattern_failed"})

    if not isinstance(pattern_id, str) or not pattern_id:
        _audit_ui_autoapprove_action(
            event="ui_action_failed", action="autoapprove_pattern_add",
            target_pattern_id=None, profile=profile, kind=kind, policy=policy_val,
            live_mode=True, autoapprove_live_mode=True,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="add_pattern_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "add_pattern_failed"})

    _audit_ui_autoapprove_action(
        event="ui_action_completed", action="autoapprove_pattern_add",
        target_pattern_id=pattern_id, profile=profile, kind=kind, policy=policy_val,
        live_mode=True, autoapprove_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash, outcome="added",
        duration_s=time.monotonic() - start,
        caller_kinds_count=len(caller_kinds),
        args_constraints_keys_count=args_meta["keys_count"],
        args_constraints_allowlists_total_entries=args_meta["allowlists_total_entries"],
        quota_max_per_day=quota, expires_at=expires_at,
    )
    return {
        "added": True,
        "pattern_id": pattern_id,
        "profile": profile,
        "live_mode": True,
        "autoapprove_live_mode": True,
    }


# ── 23. POST /api/mcp/autoapprove/{pattern_id}/remove ─────────────────────────

@router.post(
    "/api/mcp/autoapprove/{pattern_id}/remove",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_autoapprove_remove(
    pattern_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-5 : supprime un pattern AutoApprove (idempotent Phase 11).

    Body : {"confirmed": true, "confirmation_phrase": "<pattern_id complet>",
            "pattern_id": "..."}

    Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1.
    """
    body = body or {}
    pid = _validate_pattern_id_format(pattern_id)
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=pid)
    body_pid = body.get("pattern_id")
    if body_pid is not None and body_pid != pid:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "pattern_id_invalid_format"})

    live = _live_mode_enabled()
    aa_live = _autoapprove_live_mode_enabled()
    double_optin = live and aa_live
    actor_hash = _hash_actor_token(authorization)

    engine = _get_auto_approve_engine_singleton()
    if engine is None:
        _audit_ui_autoapprove_action(
            event="ui_action_failed", action="autoapprove_pattern_remove",
            target_pattern_id=pid, profile=None, kind=None, policy=None,
            live_mode=live, autoapprove_live_mode=aa_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="engine_unavailable",
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "engine_unavailable"})

    if not double_optin:
        _audit_ui_autoapprove_action(
            event="ui_action_simulated", action="autoapprove_pattern_remove",
            target_pattern_id=pid, profile=None, kind=None, policy=None,
            live_mode=live, autoapprove_live_mode=aa_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="simulated",
        )
        return {
            "would_remove": True,
            "pattern_id": pid,
            "live_mode": live,
            "autoapprove_live_mode": aa_live,
            "forced_dry_run": True,
        }

    _audit_ui_autoapprove_action(
        event="ui_action_requested", action="autoapprove_pattern_remove",
        target_pattern_id=pid, profile=None, kind=None, policy=None,
        live_mode=True, autoapprove_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash,
    )
    start = time.monotonic()
    try:
        was_removed = bool(engine.remove_pattern(pid))
    except Exception:
        _audit_ui_autoapprove_action(
            event="ui_action_failed", action="autoapprove_pattern_remove",
            target_pattern_id=pid, profile=None, kind=None, policy=None,
            live_mode=True, autoapprove_live_mode=True,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="remove_pattern_failed",
            duration_s=time.monotonic() - start,
        )
        raise HTTPException(status_code=500,
            detail={"error": True, "error_code": "remove_pattern_failed"})

    idempotent = not was_removed
    _audit_ui_autoapprove_action(
        event="ui_action_completed", action="autoapprove_pattern_remove",
        target_pattern_id=pid, profile=None, kind=None, policy=None,
        live_mode=True, autoapprove_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash, outcome="removed",
        duration_s=time.monotonic() - start,
        idempotent=idempotent,
    )
    return {
        "removed": True,
        "pattern_id": pid,
        "idempotent": idempotent,
        "live_mode": True,
        "autoapprove_live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B-6 — Trust score manual update UI (mutation de seuil de sécurité)
# ══════════════════════════════════════════════════════════════════════════════
#
# 1 route POST :
#   24. POST /api/mcp/catalog/{server_id}/trust/update
#
# Doctrine : modifier trust_score peut indirectement débloquer des chaînes
# d'autorisation futures. Double opt-in obligatoire + justification obligatoire.
# 20B-6 = manual update (pas un recompute automatique). Aucun import
# score_package / TrustReport / PackageMetadata côté production.
#
# Garde-fous :
#   1. verify_admin_token
#   2. _assert_confirmed
#   3. _validate_confirmation_phrase = server_id exact (héritage 20B-4)
#   4. _validate_trust_score_strict_format (None/absent refusé)
#   5. _validate_justification_required (10..256 chars trimés, UTF-8 lisible,
#      pas de caractères de contrôle)
#   6. body.server_id == path server_id
#   7. Singleton catalog (réutilisation 20B-2)
#   8. Status REMOVED refusé. QUARANTINED autorisé. Autres autorisés.
#   9. Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1.
#  10. Dry-run STRICT : 0 call update_trust_score
#  11. Idempotent no-op live : 0 call update_trust_score si valeur identique
#  12. Audit UI étendu : trust_score_old/new exposés (signal policy non secret),
#      justification_length only (jamais texte brut), aucun ServerEntry brut,
#      aucun TrustReport.factors, aucun raw CatalogError
#  13. Réponse whitelist : aucun ServerEntry brut
#  14. error_code court whitelist
#  15. Aucun appel ApprovalQueue / Install / Activation / AutoApprove / marker
#  16. Aucun lancement direct de processus externe
#  17. Aucun import score_package / TrustReport / PackageMetadata
# ══════════════════════════════════════════════════════════════════════════════


_JUSTIFICATION_MIN_LEN = 10
_JUSTIFICATION_MAX_LEN = 256


def _trust_live_mode_enabled() -> bool:
    """Opt-in dédié `LUMENA_MCP_TRUST_LIVE` (parallèle 20B-5).

    Double opt-in requis pour toute mutation trust_score : si l'un des deux
    env (LUMENA_MCP_LIVE / LUMENA_MCP_TRUST_LIVE) est absent ou falsy,
    dry-run forcé (0 call update_trust_score).
    """
    raw = os.environ.get("LUMENA_MCP_TRUST_LIVE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _trust_double_optin() -> bool:
    """LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1."""
    return _live_mode_enabled() and _trust_live_mode_enabled()


def _validate_trust_score_strict_format(raw: Any) -> int:
    """Validation stricte pour 20B-6 (None/absent refusé).

    Différent de `_validate_trust_score_format` 20B-4 qui accepte None
    pour Catalog.add_server (champ optionnel à la création initiale).
    Ici la mutation impose une valeur explicite.
    """
    if raw is None:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "trust_score_invalid"},
        )
    return _validate_trust_score_format(raw)


def _validate_justification_required(raw: Any) -> tuple[str, int]:
    """Justification obligatoire pour 20B-6.

    Type str ; trim ; non-vide après trim ; longueur 10..256 chars ;
    UTF-8 lisible (les accents français sont autorisés) ; refus
    caractères de contrôle (C0 0x00-0x1f + DEL 0x7f).

    Retourne (trimmed, length). Le texte trimé est consommé uniquement
    par le handler (jamais loggué) ; seule la longueur entre dans
    l'audit UI.
    """
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "justification_required"},
        )
    trimmed = raw.strip()
    if not trimmed:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "justification_required"},
        )
    n = len(trimmed)
    if n < _JUSTIFICATION_MIN_LEN or n > _JUSTIFICATION_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "justification_invalid"},
        )
    for ch in trimmed:
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7f:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "error_code": "justification_invalid"},
            )
    return trimmed, n


def _audit_ui_trust_action(
    *,
    event: str,
    action: str,
    target_server_id: str,
    live_mode: bool,
    trust_live_mode: bool,
    confirmation_received: bool,
    confirmation_phrase_received: Optional[bool] = None,
    actor_token_hash: str,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_s: Optional[float] = None,
    trust_score_old: Optional[int] = None,
    trust_score_new: Optional[int] = None,
    idempotent: Optional[bool] = None,
    justification_length: Optional[int] = None,
    owner_profile: Optional[str] = None,
) -> None:
    """Audit UI étendu Phase 20B-6.

    Whitelist stricte. Aucun champ justification raw, display_name,
    package_spec, version, notes, ServerEntry brut, CatalogError message,
    TrustReport.factors[].
    """
    path = _ui_audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "phase": "20B-6",
        "action": action,
        "target_server_id": target_server_id,
        "live_mode": bool(live_mode),
        "trust_live_mode": bool(trust_live_mode),
        "confirmation_received": bool(confirmation_received),
        "actor_token_hash": actor_token_hash,
    }
    if confirmation_phrase_received is not None:
        entry["confirmation_phrase_received"] = bool(confirmation_phrase_received)
    if outcome is not None:
        entry["outcome"] = outcome
    if error_code is not None:
        entry["error_code"] = error_code
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 6)
    if trust_score_old is not None or "trust_score_old" not in entry:
        # On expose explicitement même si None (signal : "pas de score précédent")
        entry["trust_score_old"] = trust_score_old
    if trust_score_new is not None or "trust_score_new" not in entry:
        entry["trust_score_new"] = trust_score_new
    if idempotent is not None:
        entry["idempotent"] = bool(idempotent)
    if justification_length is not None:
        entry["justification_length"] = int(justification_length)
    if owner_profile is not None:
        entry["owner_profile"] = owner_profile
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


# ── 24. POST /api/mcp/catalog/{server_id}/trust/update ────────────────────────

@router.post(
    "/api/mcp/catalog/{server_id}/trust/update",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_catalog_trust_update(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    authorization: Optional[str] = Header(None),
):
    """Phase 20B-6 : trust_score manual update.

    Body : {"confirmed": true, "confirmation_phrase": "<server_id>",
            "server_id": "...", "trust_score": <int 0..100>,
            "justification": "<texte 10..256 chars>"}

    Double opt-in obligatoire : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1.
    Sinon dry-run forcé (0 call update_trust_score).

    Idempotent no-op : si trust_score_proposed == trust_score_current,
    aucun call update_trust_score, audit outcome="noop", idempotent=true.
    """
    body = body or {}
    sid = _validate_server_id_format(server_id)
    _assert_confirmed(body)
    _validate_confirmation_phrase(body, expected=sid)
    body_sid = body.get("server_id")
    if body_sid is not None and body_sid != sid:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_format"})

    # trust_score : strict (None/absent refusé)
    if "trust_score" not in body:
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "trust_score_invalid"})
    new_score = _validate_trust_score_strict_format(body.get("trust_score"))

    # justification : obligatoire (10..256 chars UTF-8 lisible)
    _, justification_length = _validate_justification_required(
        body.get("justification")
    )

    live = _live_mode_enabled()
    trust_live = _trust_live_mode_enabled()
    double_optin = live and trust_live
    actor_hash = _hash_actor_token(authorization)

    catalog = _get_catalog_singleton()
    if catalog is None:
        _audit_ui_trust_action(
            event="ui_action_failed", action="trust_score_manual_update",
            target_server_id=sid, live_mode=live, trust_live_mode=trust_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="catalog_unavailable",
            justification_length=justification_length,
            trust_score_new=new_score,
        )
        raise HTTPException(status_code=503,
            detail={"error": True, "error_code": "catalog_unavailable"})

    try:
        entry = catalog.get_server(sid)
    except Exception:
        entry = None
    if entry is None:
        _audit_ui_trust_action(
            event="ui_action_failed", action="trust_score_manual_update",
            target_server_id=sid, live_mode=live, trust_live_mode=trust_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="server_not_found",
            justification_length=justification_length,
            trust_score_new=new_score,
        )
        raise HTTPException(status_code=404,
            detail={"error": True, "error_code": "server_not_found"})

    try:
        status_val = entry.status.value if entry.status is not None else None
    except Exception:
        status_val = None
    if status_val == "removed":
        _audit_ui_trust_action(
            event="ui_action_failed", action="trust_score_manual_update",
            target_server_id=sid, live_mode=live, trust_live_mode=trust_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error",
            error_code="server_id_invalid_status",
            justification_length=justification_length,
            trust_score_new=new_score,
        )
        raise HTTPException(status_code=400,
            detail={"error": True, "error_code": "server_id_invalid_status"})

    trust_score_old: Optional[int]
    try:
        ts_raw = entry.trust_score
    except Exception:
        ts_raw = None
    trust_score_old = ts_raw if isinstance(ts_raw, int) and not isinstance(ts_raw, bool) else None

    owner_profile = None
    try:
        op = entry.owner_profile
        if isinstance(op, str):
            owner_profile = op
    except Exception:
        owner_profile = None

    # Dry-run STRICT : aucun call update_trust_score
    if not double_optin:
        _audit_ui_trust_action(
            event="ui_action_simulated", action="trust_score_manual_update",
            target_server_id=sid, live_mode=live, trust_live_mode=trust_live,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="simulated",
            trust_score_old=trust_score_old, trust_score_new=new_score,
            justification_length=justification_length,
            owner_profile=owner_profile,
        )
        return {
            "would_update_trust_score": True,
            "server_id": sid,
            "trust_score_old": trust_score_old,
            "trust_score_proposed": new_score,
            "live_mode": live,
            "trust_live_mode": trust_live,
            "forced_dry_run": True,
        }

    # Idempotent no-op : si valeur identique, aucun call update_trust_score
    if trust_score_old == new_score:
        _audit_ui_trust_action(
            event="ui_action_completed", action="trust_score_manual_update",
            target_server_id=sid, live_mode=True, trust_live_mode=True,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="noop",
            trust_score_old=trust_score_old, trust_score_new=trust_score_old,
            idempotent=True,
            justification_length=justification_length,
            owner_profile=owner_profile,
        )
        return {
            "updated": False,
            "server_id": sid,
            "trust_score_old": trust_score_old,
            "trust_score_new": trust_score_old,
            "idempotent": True,
            "live_mode": True,
            "trust_live_mode": True,
        }

    # Live mutation : update_trust_score uniquement après TOUTES les validations
    _audit_ui_trust_action(
        event="ui_action_requested", action="trust_score_manual_update",
        target_server_id=sid, live_mode=True, trust_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash,
        trust_score_old=trust_score_old, trust_score_new=new_score,
        justification_length=justification_length,
        owner_profile=owner_profile,
    )
    start = time.monotonic()
    try:
        catalog.update_trust_score(sid, new_score)
    except Exception as exc:
        message = str(getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else "")
        if "trust_score" in message:
            code = "trust_score_invalid"
            http_status = 400
        else:
            code = "update_trust_score_failed"
            http_status = 500
        _audit_ui_trust_action(
            event="ui_action_failed", action="trust_score_manual_update",
            target_server_id=sid, live_mode=True, trust_live_mode=True,
            confirmation_received=True, confirmation_phrase_received=True,
            actor_token_hash=actor_hash, outcome="error", error_code=code,
            duration_s=time.monotonic() - start,
            trust_score_old=trust_score_old, trust_score_new=new_score,
            justification_length=justification_length,
            owner_profile=owner_profile,
        )
        raise HTTPException(status_code=http_status,
            detail={"error": True, "error_code": code})

    _audit_ui_trust_action(
        event="ui_action_completed", action="trust_score_manual_update",
        target_server_id=sid, live_mode=True, trust_live_mode=True,
        confirmation_received=True, confirmation_phrase_received=True,
        actor_token_hash=actor_hash, outcome="updated",
        duration_s=time.monotonic() - start,
        trust_score_old=trust_score_old, trust_score_new=new_score,
        idempotent=False,
        justification_length=justification_length,
        owner_profile=owner_profile,
    )
    return {
        "updated": True,
        "server_id": sid,
        "trust_score_old": trust_score_old,
        "trust_score_new": new_score,
        "idempotent": False,
        "live_mode": True,
        "trust_live_mode": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 21 — Hardening MCP (observabilité, validation passive clés,
#           audit integrity, coherence runtime, readiness, RBAC mode)
# ══════════════════════════════════════════════════════════════════════════════
#
# 8 routes GET nouvelles (AUCUNE mutation, AUCUN POST/PUT/PATCH/DELETE) :
#   25. GET /api/mcp/observability/overview
#   26. GET /api/mcp/observability/events
#   27. GET /api/mcp/observability/last-runs
#   28. GET /api/mcp/keys/status
#   29. GET /api/mcp/audit-integrity/{component}
#   30. GET /api/mcp/coherence/check
#   31. GET /api/mcp/readiness
#   32. GET /api/mcp/rbac/mode
#
# Lignes rouges Phase 21 :
#   - Aucun cipher neuf, aucun _get_cipher_helper, aucun _get_hmac_key_helper,
#     aucun decrypt/encrypt, aucun SecretsService.set, aucun round-trip
#   - observability/events sanitization stricte par whitelist
#   - readiness/coherence : rapports purs, AUCUN auto-fix
#   - Aucun appel mutatif (approve/reject/install/activate/deactivate/
#     add_pattern/remove_pattern/update_trust_score/add_server/etc.)
#   - Aucun helper marker (lecture/écriture) utilisé
#   - Aucun nouveau singleton
#   - rbac/mode = lecture seule, AUCUN changement à verify_admin_token
# ══════════════════════════════════════════════════════════════════════════════


# Whitelist sanitization des events audit (cohérente doctrine 20B-1 → 20B-6).
# Tous les autres champs sont droppés silencieusement par _sanitize_audit_event_safe.
_AUDIT_EVENT_SANITIZED_KEYS = frozenset({
    # Communs
    "ts", "event", "phase", "action",
    "live_mode", "autoapprove_live_mode", "trust_live_mode",
    "confirmation_received", "confirmation_phrase_received",
    "actor_token_hash",
    "outcome", "error_code", "duration_s",
    # Identifiants whitelist (jamais d'IDs sensibles ailleurs)
    "target_server_id", "target_action_id", "target_pattern_id",
    # 20B-1
    "reason_length", "marker_emitted",
    # 20B-2/3
    "caller_kind", "marker_consumed_irrecoverable",
    # 20B-4
    "from_status", "to_status", "owner_profile",
    "trust_score_set", "package_spec_transport", "idempotent",
    # 20B-5
    "profile", "kind", "policy",
    "caller_kinds_count",
    "args_constraints_keys_count",
    "args_constraints_allowlists_total_entries",
    "quota_max_per_day", "expires_at",
    # 20B-6
    "trust_score_old", "trust_score_new", "justification_length",
})


_AUDIT_SIZE_WARNING_BYTES = 50 * 1024 * 1024   # 50 MB


def _sanitize_audit_event_safe(raw: Any) -> Dict[str, Any]:
    """Phase 21 : whitelist stricte sur un event audit avant exposition.

    Tous les champs hors whitelist sont droppés silencieusement.
    Garantit que JAMAIS un event sortant ne contient args, package_spec,
    notes, justification raw (avec accents UTF-8), tool_name_pattern,
    args_constraints, caller_kinds_allowed, marker raw, token clair,
    raw entry, path absolu, stack trace.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in _AUDIT_EVENT_SANITIZED_KEYS:
        if k in raw:
            out[k] = raw[k]
    return out


def _read_audit_jsonl_sanitized(
    path: Optional[Path], limit: int, offset: int, since: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Tail jsonl + sanitization par event. Aucune ligne brute exposée."""
    if path is None or not path.exists():
        return []
    try:
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    lines = [ln for ln in text_lines if ln.strip()]
    if offset < 0:
        offset = 0
    if limit <= 0:
        return []
    end = len(lines) - offset
    start = max(0, end - limit)
    selected = lines[start:end]
    events: List[Dict[str, Any]] = []
    for ln in selected:
        try:
            raw = json.loads(ln)
        except Exception:
            continue
        if since is not None:
            ts = raw.get("ts") if isinstance(raw, dict) else None
            if not isinstance(ts, str) or ts < since:
                continue
        events.append(_sanitize_audit_event_safe(raw))
    return events


def _audit_file_metadata(path: Optional[Path]) -> Dict[str, Any]:
    """Metadata-only sur un audit jsonl. Aucune ligne brute exposée."""
    if path is None:
        return {
            "file_present": False, "size_bytes": 0, "line_count": 0,
            "valid_json_lines": 0, "malformed_lines": 0,
            "first_ts": None, "last_ts": None, "size_warning": False,
        }
    if not path.exists():
        return {
            "file_present": False, "size_bytes": 0, "line_count": 0,
            "valid_json_lines": 0, "malformed_lines": 0,
            "first_ts": None, "last_ts": None, "size_warning": False,
        }
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    try:
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        text_lines = []
    lines = [ln for ln in text_lines if ln.strip()]
    line_count = len(lines)
    valid = 0
    malformed = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            malformed += 1
            continue
        valid += 1
        if isinstance(obj, dict):
            ts = obj.get("ts")
            if isinstance(ts, str):
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
    return {
        "file_present": True,
        "size_bytes": int(size_bytes),
        "line_count": int(line_count),
        "valid_json_lines": int(valid),
        "malformed_lines": int(malformed),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "size_warning": bool(size_bytes >= _AUDIT_SIZE_WARNING_BYTES),
    }


# ── 25. GET /api/mcp/observability/overview ───────────────────────────────────

@router.get(
    "/api/mcp/observability/overview",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_observability_overview():
    """Phase 21 : agrégat de l'état global MCP (lecture pure)."""
    catalog = _get_catalog_singleton()
    queue = _get_approval_queue_singleton()
    if queue is None:
        queue = _get_approval_queue()

    catalog_counts: Dict[str, int] = {
        "declared": 0, "installed": 0, "active": 0,
        "quarantined": 0, "removed": 0,
    }
    if catalog is not None:
        try:
            entries = catalog.list_servers(include_removed=True)
        except Exception:
            entries = []
        for e in entries or []:
            try:
                sv = e.status.value if e.status is not None else None
            except Exception:
                sv = None
            if sv in catalog_counts:
                catalog_counts[sv] += 1

    pending_count = 0
    if queue is not None:
        try:
            pending = queue.list_pending()
            pending_count = len(pending or [])
        except Exception:
            pending_count = 0

    watcher = _get_runtime_watcher_singleton()
    persisted_snapshots = 0
    if watcher is not None:
        try:
            sids = watcher.list_persisted_snapshots()
            persisted_snapshots = len(sids or [])
        except Exception:
            persisted_snapshots = 0

    admin_ui_path = _ui_audit_path()
    last_admin_events = _read_audit_jsonl_sanitized(
        admin_ui_path, limit=5, offset=0
    )

    return {
        "available": True,
        "catalog_counts": catalog_counts,
        "approvals_pending_count": pending_count,
        "watcher_persisted_snapshots": persisted_snapshots,
        "last_admin_events": last_admin_events,
        "modes": {
            "live_mode": _live_mode_enabled(),
            "autoapprove_live_mode": _autoapprove_live_mode_enabled(),
            "trust_live_mode": _trust_live_mode_enabled(),
        },
        "rbac_mode": "admin_only",
    }


# ── 26. GET /api/mcp/observability/events ─────────────────────────────────────

@router.get(
    "/api/mcp/observability/events",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_observability_events(
    component: str = Query("admin_ui"),
    since: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Phase 21 : tail unifié SANITIZÉ d'un audit jsonl. Aucune ligne raw."""
    if component not in _AUDIT_COMPONENTS:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "component_unknown"},
        )
    if component == "admin_ui":
        path = _ui_audit_path()
    else:
        path = _audit_path(component)
    events = _read_audit_jsonl_sanitized(
        path, limit=limit, offset=offset, since=since
    )
    return {
        "available": True,
        "component": component,
        "events": events,
        "count": len(events),
    }


# ── 27. GET /api/mcp/observability/last-runs ──────────────────────────────────

@router.get(
    "/api/mcp/observability/last-runs",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_observability_last_runs(
    server_id: Optional[str] = Query(None),
):
    """Phase 21 : dernières opérations install/activate/deactivate par server.

    Lit le catalog en lecture seule + les snapshots persistés. Aucune
    invocation d'audit raw côté caller — métadonnées agrégées only.
    """
    if server_id is not None:
        server_id = _validate_server_id_format(server_id)
    catalog = _get_catalog_singleton()
    if catalog is None:
        return {"available": False, "reason": "not_loaded", "servers": []}
    try:
        entries = catalog.list_servers(include_removed=True)
    except Exception:
        entries = []
    if server_id is not None:
        entries = [e for e in entries if getattr(e, "server_id", None) == server_id]
    out = []
    for e in entries or []:
        try:
            sv = e.status.value if e.status is not None else None
        except Exception:
            sv = None
        out.append({
            "server_id": getattr(e, "server_id", None),
            "status": sv,
            "updated_at": getattr(e, "updated_at", None),
            "last_active_at": getattr(e, "last_active_at", None),
            "owner_profile": getattr(e, "owner_profile", None),
        })
    return {"available": True, "servers": out, "count": len(out)}


# ── 28. GET /api/mcp/keys/status ──────────────────────────────────────────────

@router.get(
    "/api/mcp/keys/status",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_keys_status():
    """Phase 21 STRICTEMENT PASSIF : SecretsService.get uniquement.

    AUCUNE génération, AUCUN cipher neuf, aucune fabrication cipher
    interne, aucune fabrication hmac key interne, AUCUN round-trip test,
    AUCUN dechiffrement.
    """
    try:
        from src.services.secrets_service import get_secrets_service
        svc = get_secrets_service()
    except Exception:
        return {"available": False, "reason": "secrets_service_not_loaded"}

    def _check(scope: str, name: str) -> Dict[str, Any]:
        try:
            value = svc.get(scope, name)
            if not isinstance(value, str) or not value:
                return {"present": False, "format_valid": False}
            looks_like_fernet = len(value) == 44
            looks_like_hmac_hex = (
                len(value) == 64
                and all(ch in "0123456789abcdefABCDEF" for ch in value)
            )
            return {
                "present": True,
                "format_valid": bool(looks_like_fernet or looks_like_hmac_hex),
            }
        except Exception:
            return {"present": False, "format_valid": False}

    return {
        "available": True,
        "keys": {
            "auto_approve_fernet":   _check("mcp_auto_approve",   "fernet_key"),
            "auto_approve_hmac":     _check("mcp_auto_approve",   "hmac_key"),
            "approval_queue_fernet": _check("mcp_approval_queue", "fernet_key"),
            "catalog_hmac":          _check("mcp_server_catalog", "hmac_key"),
        },
        "rotation_check": "deferred_to_phase_22_or_cli",
    }


# ── 29. GET /api/mcp/audit-integrity/{component} ──────────────────────────────

@router.get(
    "/api/mcp/audit-integrity/{component}",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_audit_integrity(component: str):
    """Phase 21 : metadata-only sur un audit jsonl. AUCUNE ligne raw."""
    if component not in _AUDIT_COMPONENTS:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "error_code": "component_unknown"},
        )
    if component == "admin_ui":
        path = _ui_audit_path()
    else:
        path = _audit_path(component)
    meta = _audit_file_metadata(path)
    return {
        "available": True,
        "component": component,
        **meta,
    }


# ── 30. GET /api/mcp/coherence/check ──────────────────────────────────────────

@router.get(
    "/api/mcp/coherence/check",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_coherence_check():
    """Phase 21 : 5 checks de cohérence inter-composants. AUCUN auto-fix.

    Rapport pur. Aucune mutation, aucun register/unregister,
    aucun start/stop.
    """
    checks: List[Dict[str, Any]] = []

    # Check (a) : Catalog ACTIVE entries
    catalog = _get_catalog_singleton()
    active_ids: List[str] = []
    if catalog is not None:
        try:
            entries = catalog.list_servers(include_removed=False)
            for e in entries or []:
                try:
                    sv = e.status.value if e.status is not None else None
                except Exception:
                    sv = None
                if sv == "active":
                    sid = getattr(e, "server_id", None)
                    if isinstance(sid, str):
                        active_ids.append(sid)
        except Exception:
            pass
    checks.append({
        "name": "catalog_active_count",
        "status": "ok",
        "details_count": len(active_ids),
    })

    # Check (b) : Watcher snapshots persistés vs Catalog
    watcher = _get_runtime_watcher_singleton()
    watcher_sids: List[str] = []
    if watcher is not None:
        try:
            ws = watcher.list_persisted_snapshots()
            for s in ws or []:
                if isinstance(s, str):
                    watcher_sids.append(s)
        except Exception:
            pass
    orphan_watcher = [s for s in watcher_sids if s not in {sid for sid in active_ids}]
    checks.append({
        "name": "watcher_snapshots_vs_active",
        "status": "ok" if not orphan_watcher else "warn",
        "details_count": len(orphan_watcher),
    })

    # Check (c) : registry_writer présent (Phase 20B-3 _resolve_registry_writer)
    registry_writer = _resolve_registry_writer()
    checks.append({
        "name": "registry_writer_resolvable",
        "status": "ok" if registry_writer is not None else "fail",
        "details_count": 0 if registry_writer is None else 1,
    })

    # Check (d) : Approvals pending action_ids tous valides UUID4
    queue = _get_approval_queue_singleton()
    if queue is None:
        queue = _get_approval_queue()
    invalid_pending = 0
    if queue is not None:
        try:
            pending = queue.list_pending() or []
            for p in pending:
                aid = getattr(p, "id", None)
                if not isinstance(aid, str) or not _ACTION_ID_RE.match(aid):
                    invalid_pending += 1
                    continue
                try:
                    parsed = uuid.UUID(aid)
                    if parsed.version != 4 or parsed.hex != aid:
                        invalid_pending += 1
                except Exception:
                    invalid_pending += 1
        except Exception:
            pass
    checks.append({
        "name": "approvals_pending_action_id_valid",
        "status": "ok" if invalid_pending == 0 else "warn",
        "details_count": invalid_pending,
    })

    # Check (e) : AutoApprove patterns expirés
    engine = _get_auto_approve_engine_singleton()
    expired = 0
    if engine is not None:
        try:
            patterns = engine.list_patterns(profile=None) or []
            now = datetime.now(timezone.utc)
            for p in patterns:
                ea = getattr(p, "expires_at", None)
                if isinstance(ea, str) and ea:
                    s = ea
                    if s.endswith("Z"):
                        s = s[:-1] + "+00:00"
                    try:
                        dt = datetime.fromisoformat(s)
                        if dt < now:
                            expired += 1
                    except Exception:
                        continue
        except Exception:
            pass
    checks.append({
        "name": "autoapprove_patterns_expired",
        "status": "ok" if expired == 0 else "warn",
        "details_count": expired,
    })

    # Overall
    if any(c["status"] == "fail" for c in checks):
        overall = "fail"
    elif any(c["status"] == "warn" for c in checks):
        overall = "warn"
    else:
        overall = "ok"

    return {
        "available": True,
        "checks": checks,
        "overall_status": overall,
        "last_check_ts": datetime.now(timezone.utc).isoformat(),
        "auto_fix_applied": False,
    }


# ── 31. GET /api/mcp/readiness ────────────────────────────────────────────────

@router.get(
    "/api/mcp/readiness",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_readiness():
    """Phase 21 : rapport agrégé de production readiness. AUCUN auto-fix."""
    # Singletons
    singletons = {
        "catalog": _get_catalog_singleton() is not None,
        "approval_queue": _get_approval_queue_singleton() is not None,
        "install_orchestrator": _get_install_orchestrator_singleton() is not None,
        "runtime_watcher": _get_runtime_watcher_singleton() is not None,
        "activation_service": _get_activation_service_singleton() is not None,
        "auto_approve_engine": _get_auto_approve_engine_singleton() is not None,
    }
    singletons_all_loaded = all(singletons.values())

    # Keys (réutilise la logique passive)
    keys_status_ok = True
    try:
        from src.services.secrets_service import get_secrets_service
        svc = get_secrets_service()
        for scope, name in (
            ("mcp_auto_approve",   "fernet_key"),
            ("mcp_auto_approve",   "hmac_key"),
            ("mcp_approval_queue", "fernet_key"),
            ("mcp_server_catalog", "hmac_key"),
        ):
            v = svc.get(scope, name)
            if not isinstance(v, str) or not v:
                keys_status_ok = False
                break
    except Exception:
        keys_status_ok = False

    # Audit integrity sur tous les composants whitelist
    audit_integrity_ok = True
    for comp_key in _AUDIT_COMPONENTS.keys():
        if comp_key == "admin_ui":
            p = _ui_audit_path()
        else:
            p = _audit_path(comp_key)
        meta = _audit_file_metadata(p)
        if meta["file_present"] and meta["malformed_lines"] > 0:
            audit_integrity_ok = False
            break

    # Coherence overall (réutilise les checks individuels)
    try:
        from fastapi import Response as _R  # noqa: F401
        coh = await mcp_coherence_check()  # type: ignore
        coherence_overall = coh.get("overall_status", "unknown")
    except Exception:
        coherence_overall = "unknown"

    # Modes
    modes = {
        "live_mode": _live_mode_enabled(),
        "autoapprove_live_mode": _autoapprove_live_mode_enabled(),
        "trust_live_mode": _trust_live_mode_enabled(),
    }

    if (
        singletons_all_loaded
        and keys_status_ok
        and audit_integrity_ok
        and coherence_overall == "ok"
    ):
        overall = "ready"
    elif coherence_overall == "fail" or not singletons_all_loaded:
        overall = "not_ready"
    else:
        overall = "degraded"

    return {
        "available": True,
        "overall": overall,
        "singletons_loaded": singletons,
        "singletons_all_loaded": singletons_all_loaded,
        "keys_status_ok": keys_status_ok,
        "audit_integrity_ok": audit_integrity_ok,
        "coherence_overall": coherence_overall,
        "modes": modes,
        "auto_fix_applied": False,
        "last_evaluated_ts": datetime.now(timezone.utc).isoformat(),
    }


# ── 32. GET /api/mcp/rbac/mode ────────────────────────────────────────────────

@router.get(
    "/api/mcp/rbac/mode",
    dependencies=[Depends(verify_admin_token)],
)
async def mcp_rbac_mode():
    """Phase 21 v2 : RBAC reporté.

    Retour passif. AUCUN changement à verify_admin_token. Phase 22+ pourra
    introduire `LUMENA_MCP_RBAC_ENABLED` et des tokens scoped read/write.
    """
    return {
        "mode": "admin_only",
        "evolution_planned_for": "phase_22_or_later",
    }
