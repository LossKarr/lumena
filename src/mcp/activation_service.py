"""
activation_service.py — MCP Activation / Runtime Registration (Phase 19 v3).

Active un serveur MCP déjà INSTALLED (Phase 18) et enregistre ses outils
dynamiquement dans le ToolRegistry via Protocol injecté.

DOCTRINE Phase 19 v3 :
  - Premier vrai câblage runtime de la chaîne MCP.
  - **dry_run sémantique stricte** : quand `dry_run=True` (défaut),
    `activate()` n'exécute que les pré-conditions (kill switch, validation
    server_id, catalog get_server, approval gating). Aucune instanciation
    runner/client, aucune discovery, aucun register_handler, aucune mutation
    Catalog. Garantie ZÉRO effet runtime. Le résultat retourné a
    `success=False`, `dry_run=True`, `reason="dry_run"`,
    `last_step=ActivationStep.NOT_STARTED`.
  - **Aucune touche aux modules existants** : MCPSandboxRunner, MCPClient,
    tool_registry.py, react.py, sub_agent.py, MCPServerCatalog,
    RuntimeWatcher, ApprovalQueue, MCPDiscoveryService, PolicyAttributor,
    PolicyResolver, MCPOrchestrator, MCPInstallOrchestrator, handler_adapter,
    auto_approve, policy. Tout est consommé via Protocols injectés.
  - **Aucun appel à `ApprovalQueue.approve()`** : c'est l'action humaine.
    Le caller fournit un `ApprovalResult` APPROVED obtenu via
    `ApprovalQueue.approve()` hors orchestrator.
  - **Option B pour le subprocess** : MCPSandboxRunner n'expose pas de
    propriété publique pour le subprocess. Phase 19 n'ajoute PAS
    `runner.process`. `client_factory` est strictement injecté par le caller.
    Phase 19 est donc testable et opérationnelle en dry_run + mocks, mais
    le câblage production complet attend une décision design ultérieure.
  - **Discovery configuration obligatoire** : le `MCPDiscoveryService` injecté
    DOIT avoir `require_server_callable=False`. C'est Phase 19 qui fera la
    transition vers ACTIVE en fin de pipeline ; avant, le statut est INSTALLED.
  - **Rollback exhaustif** : toute erreur à n'importe quelle étape annule
    proprement tout ce qui a été créé en cascade (unregister handlers,
    unregister watcher, stop client, stop runner). Catalog reste INSTALLED.
  - **ACTIVE strictement dernier** : après runner started + client initialized
    + discovery OK + handlers registered + watcher registered + re-check
    status INSTALLED.
  - **provenance en dict** : `register_dynamic_handler(..., provenance=Dict)`,
    jamais string. Cohérent avec la signature réelle de Phase 8.
  - **runner.start() / runner.stop()** sans `timeout_s` kwarg. Le timeout
    est configuré à la construction du runner via `runner_factory`.
  - **HandlerAdapter signature réelle** :
    `adapt_tool(*, client, server_name, mcp_tool, category, timeout_s)`.
  - **Pas de persistance des running_contexts** : si Lumena redémarre, drift
    checker (Phase 2) signalera la divergence catalog ACTIVE / aucun runner.

Layout disque :
  DATA_DIR/mcp_activation/audit.jsonl
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from loguru import logger

from src.mcp.approval_queue import (
    ApprovalDecision,
    ApprovalQueue,
    ApprovalResult,
)
from src.mcp.category_inference import infer_semantic_category
from src.mcp.client import MCPTool
from src.mcp.overlap_detector import detect_overlaps, group_overlaps_by_mcp
from src.mcp.discovery import (
    DiscoveryError,
    DiscoveryReport,
    MCPDiscoveryService,
)
from src.mcp.policy import MCPPolicy
from src.mcp.runtime_watcher import RuntimeWatcher
from src.mcp.server_catalog import (
    MCPServerCatalog,
    ServerEntry,
    ServerStatus,
)
from src.utils.paths import DATA_DIR


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_activation"
_AUDIT_FILENAME = "audit.jsonl"
_DEFAULT_ENV_DISABLE_FLAG = "LUMENA_MCP_ACTIVATION_DISABLED"

_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

_VALID_CALLER_KINDS = frozenset(
    {"react", "codeagent", "autonomy", "scheduler", "daemon", "silent"}
)

# Phase I-8 (Fix AY) : détection « entry point = CLI à sous-commandes ».
# Quand le start lance l'entry point console NU et que c'est un multiplexeur
# Click/Typer (ex. windows-mcp 3.4.2 : auth/install/serve/uninstall), le
# process sort immédiatement avec sur stderr :
#   "Usage: windows-mcp [OPTIONS] COMMAND [ARGS]..."
#   "Try 'windows-mcp --help' for help."
#   "Error: Missing command."
# → on re-tente le start avec des sous-commandes serveur candidates, dans
# l'ordre de probabilité, et on PERSISTE la gagnante au catalogue.
_CLI_USAGE_RE = re.compile(r"\busage:", re.IGNORECASE)
_CLI_SUBCOMMAND_HINT_RE = re.compile(
    r"missing command|no such command|missing argument|--help",
    re.IGNORECASE,
)
_ENTRY_SUBCOMMAND_CANDIDATES: tuple = ("serve", "run", "stdio", "start", "server")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions / Enums
# ──────────────────────────────────────────────────────────────────────────────


class ActivationError(Exception):
    """Erreurs globales : kill switch, server_id invalide, approval
    missing/invalid."""


class ActivationStep(Enum):
    """Étapes pour traçabilité du rollback."""
    NOT_STARTED          = "not_started"
    RUNNER_CREATED       = "runner_created"
    RUNNER_STARTED       = "runner_started"
    CLIENT_CREATED       = "client_created"
    CLIENT_INITIALIZED   = "client_initialized"
    DISCOVERY_COMPLETED  = "discovery_completed"
    HANDLERS_REGISTERED  = "handlers_registered"
    WATCHER_REGISTERED   = "watcher_registered"
    CATALOG_ACTIVATED    = "catalog_activated"
    COMPLETED            = "completed"


# ──────────────────────────────────────────────────────────────────────────────
# Protocols
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ToolRegistryWriterLike(Protocol):
    """Protocol attendu du ToolRegistry (Phase 8)."""
    def register_dynamic_handler(
        self,
        handler_def: Any,
        *,
        policy: MCPPolicy,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def unregister_dynamic_handler(self, name: str) -> bool: ...

    def is_dynamic_handler(self, name: str) -> bool: ...


@runtime_checkable
class HandlerAdapterLike(Protocol):
    """Protocol pour handler_adapter.adapt_tool (Phase 7).

    Phase C : kwargs `cached_category`, `llm_callable`,
    `all_tool_descriptions` ajoutés pour cascade sémantique.
    """
    def adapt_tool(
        self,
        *,
        client: Any,
        server_name: str,
        mcp_tool: MCPTool,
        category: str = "mcp",
        timeout_s: Optional[float] = None,
        cached_category: Optional[str] = None,
        llm_callable: Optional[Callable[[str], str]] = None,
        all_tool_descriptions: Optional[List[str]] = None,
    ) -> Any: ...


@runtime_checkable
class RunnerFactoryLike(Protocol):
    """Factory pour créer un MCPSandboxRunner."""
    def __call__(self, server_id: str, entry: ServerEntry) -> Any: ...


@runtime_checkable
class ClientFactoryLike(Protocol):
    """Factory pour créer un MCPClient à partir d'un runner démarré."""
    def __call__(self, runner: Any) -> Any: ...


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActivationProposal:
    server_id: str
    approval_ticket_id: Optional[str]
    proposed_at: str


@dataclass(frozen=True)
class ActivationResult:
    server_id: str
    success: bool
    reason: str
    last_step: ActivationStep
    duration_s: float
    registered_handlers: List[str] = field(default_factory=list)
    discovery_proposed_count: int = 0
    discovery_refused_count: int = 0
    dry_run: bool = False


@dataclass(frozen=True)
class DeactivationResult:
    server_id: str
    success: bool
    reason: str
    last_step: ActivationStep
    duration_s: float
    unregistered_handlers: List[str] = field(default_factory=list)


@dataclass
class _RunningContext:
    """État interne d'un serveur activé."""
    server_id: str
    runner: Any
    client: Any
    registered_handlers: List[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_server_id(server_id: Any) -> bool:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        return False
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        return False
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# MCPActivationService
# ──────────────────────────────────────────────────────────────────────────────


class MCPActivationService:
    """Service d'activation runtime des serveurs MCP.

    activate() :
      Active un serveur INSTALLED → ACTIVE via le pipeline complet
      (runner.start + client.initialize + discovery + register handlers +
      watcher + catalog).

    deactivate() :
      Annule l'activation ACTIVE → INSTALLED (unregister handlers + watcher
      + stop client + stop runner + catalog rollback).

    Pas d'auto-réactivation au boot, pas de persistance des running_contexts.
    """

    def __init__(
        self,
        *,
        catalog: MCPServerCatalog,
        approval_queue: ApprovalQueue,
        discovery: MCPDiscoveryService,
        adapter: HandlerAdapterLike,
        registry_writer: ToolRegistryWriterLike,
        runtime_watcher: RuntimeWatcher,
        runner_factory: RunnerFactoryLike,
        client_factory: ClientFactoryLike,
        audit_log_path: Optional[Path] = None,
        require_approval: bool = True,
        dry_run: bool = True,
        env_disable_flag: str = _DEFAULT_ENV_DISABLE_FLAG,
        handler_call_timeout_s: Optional[float] = None,
        llm_callable: Optional[Callable[[str], str]] = None,
        credentials_service: Optional[Any] = None,
        config_service: Optional[Any] = None,
    ):
        if catalog is None:
            raise ValueError("catalog must not be None")
        if approval_queue is None:
            raise ValueError("approval_queue must not be None")
        if not callable(getattr(approval_queue, "propose", None)):
            raise ValueError("approval_queue must expose .propose()")
        if discovery is None:
            raise ValueError("discovery must not be None")
        if not isinstance(discovery, MCPDiscoveryService):
            raise ValueError("discovery must be MCPDiscoveryService instance")
        # Discovery DOIT être configuré avec require_server_callable=False
        # car Phase 19 active des serveurs INSTALLED (pas encore ACTIVE).
        if discovery.require_server_callable:
            raise ValueError(
                "discovery must be configured with require_server_callable="
                "False for activation flow (status is INSTALLED, not ACTIVE)"
            )
        if adapter is None or not callable(getattr(adapter, "adapt_tool", None)):
            raise ValueError("adapter must expose .adapt_tool()")
        if registry_writer is None:
            raise ValueError("registry_writer must not be None")
        if not (
            callable(getattr(registry_writer, "register_dynamic_handler", None))
            and callable(
                getattr(registry_writer, "unregister_dynamic_handler", None)
            )
            and callable(getattr(registry_writer, "is_dynamic_handler", None))
        ):
            raise ValueError(
                "registry_writer must expose register/unregister/is_dynamic"
            )
        if runtime_watcher is None:
            raise ValueError("runtime_watcher must not be None")
        if not callable(runner_factory):
            raise ValueError("runner_factory must be callable")
        if not callable(client_factory):
            raise ValueError("client_factory must be callable")
        if not isinstance(env_disable_flag, str) or not env_disable_flag:
            raise ValueError("env_disable_flag must be a non-empty string")

        self._catalog = catalog
        self._approval_queue = approval_queue
        self._discovery = discovery
        self._adapter = adapter
        self._registry_writer = registry_writer
        self._watcher = runtime_watcher
        self._runner_factory = runner_factory
        self._client_factory = client_factory
        self._require_approval = bool(require_approval)
        self._dry_run = bool(dry_run)
        self._env_disable_flag = env_disable_flag
        self._handler_call_timeout_s = handler_call_timeout_s
        # Phase C — LLM optionnel pour cascade niveau 3 de classification.
        # Si None, la cascade s'arrête au niveau 2 (heuristique) puis 4 (fallback).
        if llm_callable is not None and not callable(llm_callable):
            raise ValueError("llm_callable must be callable or None")
        self._llm_callable = llm_callable
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Fix Q (Phase I-7) : injection des secrets/config dans l'env du runner.
        # Sans ces deux services, runner.start() est appelé sans runtime_env_secrets
        # → SLACK_BOT_TOKEN jamais injecté → mcp-server-slack crash →
        # client_initialize_failed. Les deux restent optionnels pour rétrocompat
        # avec les tests existants qui n'utilisent pas ce flow.
        self._credentials_service = credentials_service
        self._config_service = config_service

        self._running_contexts: Dict[str, _RunningContext] = {}

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def require_approval(self) -> bool:
        return self._require_approval

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def env_disable_flag(self) -> str:
        return self._env_disable_flag

    def is_running(self, server_id: str) -> bool:
        """True si le serveur a un contexte ET que son process est vivant.

        Fix W (Phase I-7) : l'ancienne version ne vérifiait que la présence
        dans _running_contexts — un process MORT restait « running » à vie :
        Fix S ne self-heal-ait pas (croyait le serveur vivant) et activate()
        refusait already_running. Un crash de process rendait le MCP
        définitivement inutilisable sans reboot.
        """
        ctx = self._running_contexts.get(server_id)
        if ctx is None:
            return False
        # Fix X (Phase I-7) : un client dont le canal stdio est cassé
        # (EOF/exception readline avec process vivant) se marque closed.
        # Le MCP est alors inutilisable même si le process vit → not running
        # → Fix S self-heal (cleanup + réactivation).
        client_closed = getattr(getattr(ctx, "client", None), "is_closed", None)
        if client_closed is True:
            return False
        proc = getattr(getattr(ctx, "runner", None), "process", None)
        if proc is None:
            # Pas d'accès au process (mock/test) → comportement historique.
            return True
        try:
            return proc.poll() is None
        except Exception:  # noqa: BLE001
            return True

    # ── Kill switch ───────────────────────────────────────────────────────

    def _kill_switch_active(self) -> bool:
        value = os.environ.get(self._env_disable_flag)
        if value is None:
            return False
        return value.strip().lower() not in ("", "0", "false", "no")

    # ── Audit ─────────────────────────────────────────────────────────────

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit jsonl.

        Whitelist : server_id, step, reason, ticket_id, status,
        registered_count, refused_count, proposed_count, last_step,
        duration_s, dry_run, ts.
        JAMAIS : stderr/stdout runner, args raw, stringification.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.activation] audit write failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # propose_activation
    # ══════════════════════════════════════════════════════════════════════

    def propose_activation(
        self,
        server_id: Any,
        *,
        caller_kind: str = "silent",
    ) -> ActivationProposal:
        """Propose une activation. Crée un ticket ApprovalQueue.

        N'exécute aucun runner/client. Symétrie avec InstallOrchestrator.
        """
        if self._kill_switch_active():
            self._audit("activation_disabled", reason="activation_disabled")
            raise ActivationError("activation_disabled")

        if not _is_valid_server_id(server_id):
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise ActivationError("server_id_invalid")

        if not isinstance(caller_kind, str) or caller_kind not in _VALID_CALLER_KINDS:
            self._audit(
                "propose_failed",
                server_id=server_id,
                reason="caller_kind_invalid",
            )
            raise ActivationError("caller_kind_invalid")

        entry = self._catalog.get_server(server_id)
        if entry is None:
            self._audit(
                "propose_failed",
                server_id=server_id,
                reason="server_unknown",
            )
            raise ActivationError("server_unknown")

        if entry.status != ServerStatus.INSTALLED:
            self._audit(
                "propose_failed",
                server_id=server_id,
                status=entry.status.value,
                reason="status_not_installed",
            )
            raise ActivationError(f"status_not_installed:{entry.status.value}")

        tool_name = f"mcp_activate:{server_id}"
        risk_summary = f"mcp_activate:{server_id}"
        args = {
            "server_id": server_id,
            "action": "activate",
        }
        ticket_id = self._approval_queue.propose(
            tool_name=tool_name,
            args=args,
            policy=MCPPolicy.LOCAL_WRITE,
            caller_kind=caller_kind,
            risk_summary=risk_summary,
        )
        proposed_at = _now_iso()
        self._audit(
            "activation_proposed",
            server_id=server_id,
            ticket_id=ticket_id,
        )
        return ActivationProposal(
            server_id=server_id,
            approval_ticket_id=ticket_id,
            proposed_at=proposed_at,
        )

    # ══════════════════════════════════════════════════════════════════════
    # activate
    # ══════════════════════════════════════════════════════════════════════

    def activate(
        self,
        server_id: Any,
        approval_result: Optional[ApprovalResult] = None,
    ) -> ActivationResult:
        """Active un serveur INSTALLED → ACTIVE via pipeline complet.

        Voir docstring module pour le pipeline détaillé.
        """
        start_ts = time.monotonic()

        # ── Étape 0.1 : kill switch ──────────────────────────────────────
        if self._kill_switch_active():
            self._audit("activation_disabled", reason="activation_disabled")
            raise ActivationError("activation_disabled")

        # ── Étape 0.2 : validate server_id ───────────────────────────────
        if not _is_valid_server_id(server_id):
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise ActivationError("server_id_invalid")

        # ── Étape 0.3 : catalog get_server + status INSTALLED ────────────
        entry = self._catalog.get_server(server_id)
        if entry is None:
            self._audit(
                "activation_failed_preconditions",
                server_id=server_id,
                reason="server_unknown",
            )
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="server_unknown",
                last_step=ActivationStep.NOT_STARTED,
                duration_s=time.monotonic() - start_ts,
            )

        if entry.status != ServerStatus.INSTALLED:
            reason = f"status_not_installed:{entry.status.value}"
            self._audit(
                "activation_failed_preconditions",
                server_id=server_id,
                status=entry.status.value,
                reason=reason,
            )
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason=reason,
                last_step=ActivationStep.NOT_STARTED,
                duration_s=time.monotonic() - start_ts,
            )

        # ── Étape 0.4 : approval gating ──────────────────────────────────
        if self._require_approval:
            if approval_result is None or not isinstance(
                approval_result, ApprovalResult
            ):
                self._audit(
                    "activation_failed_preconditions",
                    server_id=server_id,
                    reason="approval_required",
                )
                raise ActivationError("approval_required")
            if approval_result.decision != ApprovalDecision.APPROVED:
                reason = f"approval_not_granted:{approval_result.decision.value}"
                self._audit(
                    "activation_failed_preconditions",
                    server_id=server_id,
                    reason=reason,
                )
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason=reason,
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )
            approved_args = approval_result.args
            if not isinstance(approved_args, dict) or not approved_args:
                self._audit(
                    "activation_failed_preconditions",
                    server_id=server_id,
                    reason="approved_args_missing",
                )
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason="approved_args_missing",
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )
            if approved_args.get("server_id") != server_id:
                self._audit(
                    "activation_failed_preconditions",
                    server_id=server_id,
                    reason="approved_server_id_mismatch",
                )
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason="approved_server_id_mismatch",
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )

        # ── Étape 0.5 : déjà running ? ──────────────────────────────────
        if server_id in self._running_contexts:
            # Fix W (Phase I-7) : si le contexte existe mais que le process
            # est MORT (crash silencieux), nettoyer le zombie (unregister
            # handlers + drop context) et continuer l'activation au lieu de
            # refuser. Sinon : un crash rendait le MCP définitivement
            # inutilisable (already_running à vie) sans reboot.
            if self.is_running(server_id):
                self._audit(
                    "activation_failed_preconditions",
                    server_id=server_id,
                    reason="already_running",
                )
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason="already_running",
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )
            self._audit(
                "zombie_context_cleanup",
                server_id=server_id,
                reason="process_dead_context_present",
            )
            try:
                self._cleanup_dead_context(server_id)
            except Exception:  # noqa: BLE001
                # Nettoyage best-effort : on continue l'activation même si
                # une partie du cleanup échoue (handlers déjà partis, etc.)
                self._running_contexts.pop(server_id, None)

        # ── Étape 0.6 : DRY_RUN ZÉRO EFFET ──────────────────────────────
        if self._dry_run:
            self._audit(
                "dry_run_activation",
                server_id=server_id,
                status=entry.status.value,
            )
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="dry_run",
                last_step=ActivationStep.NOT_STARTED,
                duration_s=time.monotonic() - start_ts,
                dry_run=True,
            )

        self._audit("activation_started", server_id=server_id)

        # ── Étape 1 : RUNNER_CREATED ─────────────────────────────────────
        try:
            runner = self._runner_factory(server_id, entry)
        except Exception:  # noqa: BLE001
            self._audit(
                "runner_create_failed",
                server_id=server_id,
                reason="runner_create_failed",
            )
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="runner_create_failed",
                last_step=ActivationStep.NOT_STARTED,
                duration_s=time.monotonic() - start_ts,
            )

        # ── Étape 2 : RUNNER_STARTED ─────────────────────────────────────
        # Fix Q (Phase I-7) : résoudre secrets + config avant spawn du child
        # process Node. Le runner attend `runtime_env_secrets={key: value}`
        # avec clés ⊆ spec.env_keys_allowlist. On merge credentials (Fernet)
        # et config (clair) pour les MCPs qui ont les deux (ex: Slack).
        runtime_env: Dict[str, str] = {}
        try:
            spec_obj = getattr(runner, "spec", None)
            allowlist = list(getattr(spec_obj, "env_keys_allowlist", []) or [])
            if allowlist:
                if self._credentials_service is not None:
                    try:
                        secrets = self._credentials_service.export_for_runtime(
                            server_id, allowlist,
                        )
                        if isinstance(secrets, dict):
                            runtime_env.update(secrets)
                    except Exception:
                        # Secrets indisponibles ≠ blocage. Le serveur Node
                        # ratera lui-même proprement avec un message clair.
                        pass
                if self._config_service is not None:
                    try:
                        cfg = self._config_service.export_for_runtime(
                            server_id, allowlist,
                        )
                        if isinstance(cfg, dict):
                            # config NE doit PAS écraser un secret du même nom
                            for k, v in cfg.items():
                                runtime_env.setdefault(k, v)
                    except Exception:
                        pass
        except Exception:
            runtime_env = {}

        try:
            runner.start(runtime_env_secrets=runtime_env or None)
        except Exception as _start_exc:  # noqa: BLE001
            # Fix N (Phase I-7) : expose le type + message d'exception pour
            # diagnostic (env Fernet pas injectées ? node introuvable ?
            # cwd incorrect ?). Sans ça, "runner_start_failed" est opaque.
            _exc_type = type(_start_exc).__name__
            _exc_msg = str(_start_exc)[:300]
            # Sécurité : on N'EXPOSE PAS exc_msg dans _audit car le message
            # d'exception peut contenir des chemins/markers/secrets accidentels
            # (cf TestAuditForensic). Seul exc_type (nom de classe) est sûr.
            self._audit(
                "runner_start_failed",
                server_id=server_id,
                reason="runner_start_failed",
                exc_type=_exc_type,
            )
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason=f"runner_start_failed:{_exc_type}:{_exc_msg}"[:400],
                last_step=ActivationStep.RUNNER_CREATED,
                duration_s=time.monotonic() - start_ts,
            )

        # ── Étape 3 : CLIENT_CREATED ─────────────────────────────────────
        try:
            client = self._client_factory(runner)
        except Exception:  # noqa: BLE001
            self._audit(
                "client_create_failed",
                server_id=server_id,
                reason="client_create_failed",
            )
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="client_create_failed",
                last_step=ActivationStep.RUNNER_STARTED,
                duration_s=time.monotonic() - start_ts,
            )

        # ── Étape 4 : CLIENT_INITIALIZED ─────────────────────────────────
        try:
            client.initialize()
        except Exception:  # noqa: BLE001
            # Fix AY : si l'entry point est un CLI à sous-commandes (signature
            # « Usage: ... Missing command » sur stderr), tenter les
            # sous-commandes serveur candidates avant d'abandonner.
            recovered_client = self._try_entry_subcommand_recovery(
                server_id, runner, client, runtime_env,
            )
            if recovered_client is None:
                self._audit(
                    "client_initialize_failed",
                    server_id=server_id,
                    reason="client_initialize_failed",
                )
                # Rollback ordre : client close → runner stop
                self._best_effort_client_close(client, server_id)
                self._best_effort_runner_stop(runner, server_id)
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason="client_initialize_failed",
                    last_step=ActivationStep.CLIENT_CREATED,
                    duration_s=time.monotonic() - start_ts,
                )
            client = recovered_client

        # ── Étape 5 : DISCOVERY_COMPLETED ────────────────────────────────
        try:
            report: DiscoveryReport = self._discovery.discover(
                server_id, client, trust_score=entry.trust_score
            )
        except DiscoveryError:
            self._audit(
                "discovery_failed",
                server_id=server_id,
                reason="discovery_failed",
            )
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="discovery_failed",
                last_step=ActivationStep.CLIENT_INITIALIZED,
                duration_s=time.monotonic() - start_ts,
            )
        except Exception:  # noqa: BLE001
            self._audit(
                "discovery_failed",
                server_id=server_id,
                reason="discovery_failed",
            )
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="discovery_failed",
                last_step=ActivationStep.CLIENT_INITIALIZED,
                duration_s=time.monotonic() - start_ts,
            )

        # 5b : second list_tools pour récupérer les MCPTool originaux
        try:
            tools = client.list_tools()
        except Exception:  # noqa: BLE001
            self._audit(
                "list_tools_failed",
                server_id=server_id,
                reason="list_tools_failed",
            )
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="list_tools_failed",
                last_step=ActivationStep.DISCOVERY_COMPLETED,
                duration_s=time.monotonic() - start_ts,
                discovery_proposed_count=report.proposed_count,
                discovery_refused_count=report.refused_count,
            )

        if not isinstance(tools, (list, tuple)):
            self._audit(
                "list_tools_failed",
                server_id=server_id,
                reason="list_tools_failed",
            )
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="list_tools_failed",
                last_step=ActivationStep.DISCOVERY_COMPLETED,
                duration_s=time.monotonic() - start_ts,
                discovery_proposed_count=report.proposed_count,
                discovery_refused_count=report.refused_count,
            )

        tools_by_name: Dict[str, MCPTool] = {}
        for t in tools:
            try:
                tname = getattr(t, "name", None)
            except Exception:  # noqa: BLE001
                tname = None
            if isinstance(tname, str):
                tools_by_name[tname] = t

        # ── Étape 5.5 : cascade catégorie sémantique (Phase C) ───────────
        # Une seule résolution par activation : tous les tools du même
        # serveur reçoivent la même catégorie. Cache hit court-circuite.
        all_tool_descriptions: List[str] = []
        for _tool in tools_by_name.values():
            _desc = getattr(_tool, "description", None)
            if isinstance(_desc, str) and _desc:
                all_tool_descriptions.append(_desc)
        resolved_category, decision_source = infer_semantic_category(
            server_name=server_id,
            tool_descriptions=all_tool_descriptions,
            llm_callable=self._llm_callable,
            cached=entry.semantic_category,
        )

        # ── Étape 6 : HANDLERS_REGISTERED ────────────────────────────────
        registered_handlers: List[str] = []
        ts_iso = _now_iso()

        for proposal in report.proposals:
            if proposal.proposed_policy is None:
                continue
            tool_name_local = proposal.tool_name
            if tool_name_local not in tools_by_name:
                reason = f"tool_mismatch:{tool_name_local}"
                self._audit(
                    "tool_mismatch",
                    server_id=server_id,
                    tool_name=tool_name_local,
                    reason=reason,
                )
                # Rollback ordre : handlers unregister → client close → runner stop
                self._rollback_handlers(registered_handlers, server_id)
                self._best_effort_client_close(client, server_id)
                self._best_effort_runner_stop(runner, server_id)
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason=reason,
                    last_step=ActivationStep.DISCOVERY_COMPLETED,
                    duration_s=time.monotonic() - start_ts,
                    discovery_proposed_count=report.proposed_count,
                    discovery_refused_count=report.refused_count,
                )

            mcp_tool = tools_by_name[tool_name_local]

            try:
                handler_def = self._adapter.adapt_tool(
                    client=client,
                    server_name=server_id,
                    mcp_tool=mcp_tool,
                    category=resolved_category,
                    timeout_s=self._handler_call_timeout_s,
                )
            except Exception:  # noqa: BLE001
                reason = f"adapter_failed:{tool_name_local}"
                self._audit(
                    "adapter_failed",
                    server_id=server_id,
                    tool_name=tool_name_local,
                    reason="adapter_failed",
                )
                self._rollback_handlers(registered_handlers, server_id)
                self._best_effort_client_close(client, server_id)
                self._best_effort_runner_stop(runner, server_id)
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason=reason,
                    last_step=ActivationStep.DISCOVERY_COMPLETED,
                    duration_s=time.monotonic() - start_ts,
                    discovery_proposed_count=report.proposed_count,
                    discovery_refused_count=report.refused_count,
                )

            try:
                self._registry_writer.register_dynamic_handler(
                    handler_def,
                    policy=proposal.proposed_policy,
                    provenance={
                        "source_kind": "mcp",
                        "server_id": server_id,
                        "phase": "19",
                        "activated_at": ts_iso,
                    },
                )
            except Exception:  # noqa: BLE001
                reason = f"register_failed:{tool_name_local}"
                self._audit(
                    "register_failed",
                    server_id=server_id,
                    tool_name=tool_name_local,
                    reason="register_failed",
                )
                self._rollback_handlers(registered_handlers, server_id)
                self._best_effort_client_close(client, server_id)
                self._best_effort_runner_stop(runner, server_id)
                return ActivationResult(
                    server_id=server_id,
                    success=False,
                    reason=reason,
                    last_step=ActivationStep.DISCOVERY_COMPLETED,
                    duration_s=time.monotonic() - start_ts,
                    discovery_proposed_count=report.proposed_count,
                    discovery_refused_count=report.refused_count,
                )

            registered_handlers.append(proposal.namespaced_name)
            self._audit(
                "handler_registered",
                server_id=server_id,
                tool_name=tool_name_local,
                namespaced_name=proposal.namespaced_name,
                policy=proposal.proposed_policy.value,
            )

        # ── Étape 6b : détection overlap natifs ↔ MCP (Phase E) ──────────
        # Best-effort : un échec n'annule pas l'activation. La doctrine
        # cohabitation est appliquée par ToolRegistry.get_tools_description
        # via set_mcp_overlap.
        if registered_handlers:
            try:
                self._apply_phase_e_overlap_detection(
                    server_id=server_id,
                    entry=entry,
                    tools_by_name=tools_by_name,
                    registered_handlers=registered_handlers,
                )
            except Exception:  # noqa: BLE001
                self._audit(
                    "mcp_overlap_detection_failed",
                    server_id=server_id,
                    reason="mcp_overlap_detection_failed",
                )

        # ── Étape 7 : WATCHER_REGISTERED ─────────────────────────────────
        try:
            self._watcher.register_runner(server_id, runner)
        except Exception:  # noqa: BLE001
            self._audit(
                "watcher_register_failed",
                server_id=server_id,
                reason="watcher_register_failed",
            )
            self._rollback_handlers(registered_handlers, server_id)
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="watcher_register_failed",
                last_step=ActivationStep.HANDLERS_REGISTERED,
                duration_s=time.monotonic() - start_ts,
                discovery_proposed_count=report.proposed_count,
                discovery_refused_count=report.refused_count,
            )

        # ── Étape 7b : re-check catalog status ──────────────────────────
        fresh_entry = self._catalog.get_server(server_id)
        if fresh_entry is None or fresh_entry.status != ServerStatus.INSTALLED:
            status_now = (
                fresh_entry.status.value if fresh_entry is not None else "gone"
            )
            reason = f"status_changed_during_activation:{status_now}"
            self._audit(
                "status_changed_during_activation",
                server_id=server_id,
                status=status_now,
                reason=reason,
            )
            # Rollback ordre : watcher unregister → handlers unregister
            #   → client close → runner stop
            self._best_effort_watcher_unregister(server_id)
            self._rollback_handlers(registered_handlers, server_id)
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason=reason,
                last_step=ActivationStep.WATCHER_REGISTERED,
                duration_s=time.monotonic() - start_ts,
                discovery_proposed_count=report.proposed_count,
                discovery_refused_count=report.refused_count,
            )

        # ── Étape 8 : CATALOG_ACTIVATED ──────────────────────────────────
        try:
            self._catalog.update_status(server_id, ServerStatus.ACTIVE)
        except Exception:  # noqa: BLE001
            self._audit(
                "catalog_activate_failed",
                server_id=server_id,
                reason="catalog_activate_failed",
            )
            self._best_effort_watcher_unregister(server_id)
            self._rollback_handlers(registered_handlers, server_id)
            self._best_effort_client_close(client, server_id)
            self._best_effort_runner_stop(runner, server_id)
            return ActivationResult(
                server_id=server_id,
                success=False,
                reason="catalog_activate_failed",
                last_step=ActivationStep.WATCHER_REGISTERED,
                duration_s=time.monotonic() - start_ts,
                discovery_proposed_count=report.proposed_count,
                discovery_refused_count=report.refused_count,
            )

        # ── Étape 8b : persister catégorie sémantique (Phase C) ──────────
        # Best-effort : un échec n'annule pas l'activation, le serveur reste
        # ACTIVE et la cascade re-tournera à la prochaine activation.
        # On NE persiste PAS quand source="cache" (déjà à jour dans catalog).
        if decision_source != "cache":
            try:
                self._catalog.update_semantic_category(
                    server_id, resolved_category, decision_source,
                )
                self._audit(
                    "semantic_category_inferred",
                    server_id=server_id,
                    semantic_category=resolved_category,
                    decision_source=decision_source,
                )
            except Exception:  # noqa: BLE001
                self._audit(
                    "semantic_category_persist_failed",
                    server_id=server_id,
                    semantic_category=resolved_category,
                    decision_source=decision_source,
                    reason="semantic_category_persist_failed",
                )

        # ── Étape 9 : record_event "started" (best-effort) ───────────────
        try:
            self._watcher.record_event(server_id, "started")
        except Exception:  # noqa: BLE001
            self._audit(
                "watcher_record_event_failed",
                server_id=server_id,
                reason="watcher_record_event_failed",
            )
            # On NE rollback PAS : watcher peut être désynchronisé sans
            # casser le runtime.

        # ── Étape 10 : context + return ──────────────────────────────────
        self._running_contexts[server_id] = _RunningContext(
            server_id=server_id,
            runner=runner,
            client=client,
            registered_handlers=registered_handlers,
        )

        duration_s = time.monotonic() - start_ts
        self._audit(
            "activation_completed",
            server_id=server_id,
            registered_count=len(registered_handlers),
            proposed_count=report.proposed_count,
            refused_count=report.refused_count,
            duration_s=duration_s,
        )
        return ActivationResult(
            server_id=server_id,
            success=True,
            reason="activated_ok",
            last_step=ActivationStep.COMPLETED,
            duration_s=duration_s,
            registered_handlers=list(registered_handlers),
            discovery_proposed_count=report.proposed_count,
            discovery_refused_count=report.refused_count,
        )

    # ══════════════════════════════════════════════════════════════════════
    # deactivate
    # ══════════════════════════════════════════════════════════════════════

    def deactivate(self, server_id: Any) -> DeactivationResult:
        """Désactive un serveur ACTIVE → INSTALLED.

        Annule l'activation : unregister handlers, unregister watcher,
        stop client (best-effort), stop runner, catalog rollback.
        """
        start_ts = time.monotonic()

        if not _is_valid_server_id(server_id):
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise ActivationError("server_id_invalid")

        if server_id not in self._running_contexts:
            # Vérifions le catalog : si status==ACTIVE mais pas en map,
            # c'est un état corrompu (drift)
            entry = self._catalog.get_server(server_id)
            if entry is None:
                self._audit(
                    "deactivation_failed",
                    server_id=server_id,
                    reason="server_unknown",
                )
                return DeactivationResult(
                    server_id=server_id,
                    success=False,
                    reason="server_unknown",
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )
            if entry.status == ServerStatus.ACTIVE:
                self._audit(
                    "deactivation_failed",
                    server_id=server_id,
                    status=entry.status.value,
                    reason="not_in_runtime_map",
                )
                return DeactivationResult(
                    server_id=server_id,
                    success=False,
                    reason="not_in_runtime_map",
                    last_step=ActivationStep.NOT_STARTED,
                    duration_s=time.monotonic() - start_ts,
                )
            self._audit(
                "deactivation_failed",
                server_id=server_id,
                status=entry.status.value,
                reason="not_running",
            )
            return DeactivationResult(
                server_id=server_id,
                success=False,
                reason="not_running",
                last_step=ActivationStep.NOT_STARTED,
                duration_s=time.monotonic() - start_ts,
            )

        ctx = self._running_contexts[server_id]
        unregistered_handlers: List[str] = []

        # 1. Unregister tous les handlers
        for handler_name in reversed(list(ctx.registered_handlers)):
            try:
                self._registry_writer.unregister_dynamic_handler(handler_name)
                unregistered_handlers.append(handler_name)
            except Exception:  # noqa: BLE001
                self._audit(
                    "rollback_step_failed",
                    server_id=server_id,
                    step="unregister_handler",
                    reason="unregister_handler_failed",
                )

        # 2. record_event stopped (best-effort)
        try:
            self._watcher.record_event(server_id, "stopped")
        except Exception:  # noqa: BLE001
            self._audit(
                "rollback_step_failed",
                server_id=server_id,
                step="record_event_stopped",
                reason="record_event_failed",
            )

        # 3. Unregister watcher
        try:
            self._watcher.unregister_runner(server_id)
        except Exception:  # noqa: BLE001
            self._audit(
                "rollback_step_failed",
                server_id=server_id,
                step="unregister_watcher",
                reason="watcher_unregister_failed",
            )

        # 4. Stop client (best-effort si méthode close existe)
        close_method = getattr(ctx.client, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:  # noqa: BLE001
                self._audit(
                    "rollback_step_failed",
                    server_id=server_id,
                    step="client_close",
                    reason="client_close_failed",
                )

        # 5. Stop runner
        self._best_effort_runner_stop(ctx.runner, server_id)

        # 6. Catalog ACTIVE → INSTALLED
        try:
            self._catalog.update_status(server_id, ServerStatus.INSTALLED)
        except Exception:  # noqa: BLE001
            self._audit(
                "catalog_deactivate_failed",
                server_id=server_id,
                reason="catalog_deactivate_failed",
            )
            # On retire quand même le context (la map est désynchronisée
            # mais le runtime n'est plus actif)
            del self._running_contexts[server_id]
            return DeactivationResult(
                server_id=server_id,
                success=False,
                reason="catalog_deactivate_failed",
                last_step=ActivationStep.COMPLETED,
                duration_s=time.monotonic() - start_ts,
                unregistered_handlers=unregistered_handlers,
            )

        # 7. Retirer de la map
        del self._running_contexts[server_id]

        duration_s = time.monotonic() - start_ts
        self._audit(
            "deactivation_completed",
            server_id=server_id,
            unregistered_count=len(unregistered_handlers),
            duration_s=duration_s,
        )
        return DeactivationResult(
            server_id=server_id,
            success=True,
            reason="deactivated_ok",
            last_step=ActivationStep.COMPLETED,
            duration_s=duration_s,
            unregistered_handlers=unregistered_handlers,
        )

    def shutdown_all(self) -> Dict[str, bool]:
        """Phase I-8 (Fix AW) : arrête TOUS les serveurs MCP actifs.

        Appelé au shutdown de Lumena (lifespan). Sans cet appel, les
        subprocess MCP (npm/python) deviennent ORPHELINS à la fermeture
        sur Windows (pas de kill automatique des enfants) et le catalogue
        garde des statuts ACTIVE fantômes (réparés au boot par Fix S,
        mais les anciens process traînent en mémoire).

        Best-effort : une désactivation qui échoue n'empêche jamais les
        suivantes ni le shutdown global.

        Returns:
            {server_id: success} pour chaque serveur qui était actif.
        """
        results: Dict[str, bool] = {}
        for sid in list(self._running_contexts.keys()):
            try:
                res = self.deactivate(sid)
                results[sid] = bool(getattr(res, "success", False))
            except Exception:  # noqa: BLE001
                results[sid] = False
                self._audit(
                    "shutdown_deactivate_failed",
                    server_id=sid,
                    reason="shutdown_deactivate_failed",
                )
        self._audit(
            "shutdown_all_completed",
            stopped_count=sum(1 for ok in results.values() if ok),
            failed_count=sum(1 for ok in results.values() if not ok),
        )
        return results

    # ══════════════════════════════════════════════════════════════════════
    # Helpers privés rollback
    # ══════════════════════════════════════════════════════════════════════

    def _cleanup_dead_context(self, server_id: str) -> None:
        """Fix W (Phase I-7) : nettoie un contexte dont le process est MORT.

        Même séquence que deactivate() mais sans précondition de vie :
        unregister handlers → watcher crashed event → unregister watcher →
        client close best-effort → runner stop best-effort → drop context.
        Le statut catalog n'est PAS touché ici — le caller (activate étape
        0.5) enchaîne immédiatement sur une réactivation complète.
        """
        ctx = self._running_contexts.get(server_id)
        if ctx is None:
            return
        for handler_name in reversed(list(ctx.registered_handlers)):
            try:
                self._registry_writer.unregister_dynamic_handler(handler_name)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._watcher.record_event(server_id, "crashed")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._watcher.unregister_runner(server_id)
        except Exception:  # noqa: BLE001
            pass
        close_method = getattr(ctx.client, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:  # noqa: BLE001
                pass
        self._best_effort_runner_stop(ctx.runner, server_id)
        self._running_contexts.pop(server_id, None)

    def _rollback_handlers(
        self, handler_names: List[str], server_id: str
    ) -> None:
        """Best-effort : unregister tous les handlers passés."""
        for name in reversed(list(handler_names)):
            try:
                self._registry_writer.unregister_dynamic_handler(name)
            except Exception:  # noqa: BLE001
                self._audit(
                    "rollback_step_failed",
                    server_id=server_id,
                    step="unregister_handler",
                    reason="unregister_handler_failed",
                )
        if handler_names:
            self._audit(
                "rollback_handlers_unregistered",
                server_id=server_id,
                count=len(handler_names),
            )

    def _try_entry_subcommand_recovery(
        self,
        server_id: str,
        runner: Any,
        failed_client: Any,
        runtime_env: Any,
    ) -> Optional[Any]:
        """Phase I-8 (Fix AY) : récupération réactive « CLI à sous-commandes ».

        Conditions strictes (sinon None, zéro coût au nominal) :
          - le spec n'a NI args explicites NI entry_args déjà posés
          - le runner expose set_entry_args + get_logs
          - le stderr du start raté porte la signature CLI exacte
            (« Usage: » + « Missing command »/« --help »)

        Boucle : pour chaque sous-commande candidate (serve/run/stdio/...),
        restart du runner avec entry_args=[candidate] → nouveau client →
        initialize. Première qui répond au handshake gagne : audit
        `entry_subcommand_recovered` + persistance catalogue (best-effort,
        `start_entry_args`) pour que les boots suivants démarrent juste.
        Tout échec → reset entry_args, retour None (le caller audite
        client_initialize_failed comme avant).
        """
        spec = getattr(runner, "spec", None)
        if spec is None:
            return None
        if getattr(spec, "args", None) or getattr(spec, "entry_args", None):
            # Commande explicite : jamais de bruteforce par-dessus.
            return None
        set_entry_args = getattr(runner, "set_entry_args", None)
        get_logs = getattr(runner, "get_logs", None)
        if not callable(set_entry_args) or not callable(get_logs):
            return None
        try:
            stderr_text = "\n".join(get_logs(lines=50, stream="stderr"))
        except Exception:  # noqa: BLE001
            return None
        if not (
            _CLI_USAGE_RE.search(stderr_text)
            and _CLI_SUBCOMMAND_HINT_RE.search(stderr_text)
        ):
            return None

        # Signature confirmée — fermer le couple raté avant le bruteforce.
        self._best_effort_client_close(failed_client, server_id)
        self._best_effort_runner_stop(runner, server_id)

        for candidate in _ENTRY_SUBCOMMAND_CANDIDATES:
            candidate_client: Any = None
            try:
                # Les candidats ratés crashent (exit immédiat) : sans clear,
                # la quarantaine (3 crashes/300s) tuerait la boucle au 3e.
                clear_q = getattr(runner, "clear_quarantine", None)
                if callable(clear_q):
                    clear_q()
                set_entry_args([candidate])
                runner.start(runtime_env_secrets=runtime_env or None)
                candidate_client = self._client_factory(runner)
                candidate_client.initialize()
            except Exception:  # noqa: BLE001
                if candidate_client is not None:
                    self._best_effort_client_close(candidate_client, server_id)
                self._best_effort_runner_stop(runner, server_id)
                continue
            self._audit(
                "entry_subcommand_recovered",
                server_id=server_id,
                reason=f"entry_subcommand:{candidate}",
            )
            try:
                update = getattr(self._catalog, "update_start_entry_args", None)
                if callable(update):
                    update(server_id, [candidate])
            except Exception:  # noqa: BLE001
                self._audit(
                    "entry_subcommand_persist_failed",
                    server_id=server_id,
                    reason="entry_subcommand_persist_failed",
                )
            return candidate_client

        # Aucun candidat n'a répondu : reset pour ne pas polluer un retry futur.
        try:
            set_entry_args([])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _best_effort_runner_stop(
        self, runner: Any, server_id: str
    ) -> None:
        """Best-effort : stop le runner (signature réelle Phase 5 : stop())."""
        stop_method = getattr(runner, "stop", None)
        if not callable(stop_method):
            return
        try:
            stop_method()
            self._audit(
                "rollback_runner_stopped",
                server_id=server_id,
            )
        except Exception:  # noqa: BLE001
            self._audit(
                "rollback_step_failed",
                server_id=server_id,
                step="runner_stop",
                reason="runner_stop_failed",
            )

    def _best_effort_watcher_unregister(self, server_id: str) -> None:
        """Best-effort : unregister du watcher."""
        try:
            self._watcher.unregister_runner(server_id)
            self._audit(
                "rollback_watcher_unregistered",
                server_id=server_id,
            )
        except Exception:  # noqa: BLE001
            self._audit(
                "rollback_step_failed",
                server_id=server_id,
                step="unregister_watcher",
                reason="watcher_unregister_failed",
            )

    def _apply_phase_e_overlap_detection(
        self,
        *,
        server_id: str,
        entry: ServerEntry,
        tools_by_name: Dict[str, MCPTool],
        registered_handlers: List[str],
    ) -> None:
        """Phase E : detecte overlaps natifs ↔ MCP et pousse au registry.

        Duck-typed sur registry_writer : si les accesseurs Phase E ne sont
        pas exposes (anciens mocks de test), la detection est skippee
        silencieusement (back-compat).
        """
        list_natives = getattr(
            self._registry_writer, "list_native_handler_names", None
        )
        get_desc = getattr(
            self._registry_writer, "get_tool_description", None
        )
        set_overlap = getattr(
            self._registry_writer, "set_mcp_overlap", None
        )
        if not (callable(list_natives) and callable(get_desc)
                and callable(set_overlap)):
            return

        native_names = list(list_natives())
        native_descriptions = {n: get_desc(n) for n in native_names}
        matches = detect_overlaps(
            server_name=server_id,
            mcp_tools=list(tools_by_name.values()),
            native_handler_names=native_names,
            native_descriptions=native_descriptions,
        )
        per_mcp = group_overlaps_by_mcp(matches)
        prefer = bool(getattr(entry, "prefer_over_native", False))

        # On pousse l'etat pour CHAQUE handler enregistre, meme sans overlap :
        # cela garantit un etat coherent (set vide) en cas de re-activation
        # apres modification des natifs.
        for mcp_name in registered_handlers:
            natives_for_this = list(per_mcp.get(mcp_name, frozenset()))
            try:
                set_overlap(
                    mcp_name,
                    natives_for_this,
                    prefer_over_native=prefer,
                )
            except Exception:  # noqa: BLE001
                self._audit(
                    "mcp_overlap_push_failed",
                    server_id=server_id,
                    namespaced_name=mcp_name,
                    reason="mcp_overlap_push_failed",
                )

        if matches:
            self._audit(
                "mcp_overlap_detected",
                server_id=server_id,
                overlap_count=len(matches),
                prefer_over_native=prefer,
            )

    def _best_effort_client_close(self, client: Any, server_id: str) -> None:
        """Best-effort : ferme le client si la méthode close() existe.

        Appelé dans tous les chemins de rollback après CLIENT_CREATED, avant
        runner.stop(). Ordre rollback recommandé :
          watcher unregister → handlers unregister → client close → runner stop
        """
        if client is None:
            return
        close_method = getattr(client, "close", None)
        if not callable(close_method):
            return
        try:
            close_method()
            self._audit(
                "rollback_client_closed",
                server_id=server_id,
            )
        except Exception:  # noqa: BLE001
            self._audit(
                "rollback_step_failed",
                server_id=server_id,
                step="client_close",
                reason="client_close_failed",
            )
