"""
install_orchestrator.py — MCP Install Orchestrator (Phase 18 v3).

Orchestrateur d'installation APPROUVÉE pour les serveurs MCP.

DOCTRINE Phase 18 v3 :
  - PROPOSE puis EXÉCUTE séparément :
      1. propose_install(server_id, *, caller_kind) → InstallProposal
         crée un ticket dans ApprovalQueue (Phase 10) avec policy
         EXTERNAL_WRITE_RECOVERABLE (risque dominant = supply chain
         réseau).
      2. Le caller humain/admin/UI obtient un ApprovalResult APPROVED
         via ApprovalQueue.approve(ticket_id).
      3. execute_approved_install(server_id, approval_result)
         exécute l'installation effective via MCPSandboxRunner.install()
         (Phase 5) — JAMAIS d'appel direct à approval_queue.approve().

  - RÉUTILISATION Phase 5 obligatoire :
      Aucun appel subprocess direct côté orchestrator. Toute l'installation
      passe par MCPSandboxRunner.install() (Phase 5) qui implémente déjà :
      npm install --ignore-scripts --no-audit --no-fund, uv venv +
      uv pip install --no-build, env minimal, install lock, sentinelle.

  - Aucun câblage runtime :
      Aucune touche à tool_registry.py, react.py, sub_agent.py,
      MCPSandboxRunner, MCPClient, approval_queue.py, policy.py,
      auto_approve.py, runtime_watcher.py, orchestrator.py,
      server_catalog.py, policy_resolver.py, policy_attributor.py,
      discovery.py, handler_adapter.py.

  - Transitions Catalog atomiques :
      DECLARED → INSTALLED uniquement après succès complet.
      INSTALLED → ACTIVE reste HORS SCOPE Phase 18 (sera Phase 19).

  - dry_run sémantique stricte (v3) :
      dry_run=True retourne InstallResult(success=False, dry_run=True,
      reason="dry_run"). Aucune mise à jour Catalog en dry_run.

  - LOCAL transport :
      Propose accepté, mais execute refusé avec
      reason="transport_unsupported_phase18". Un vrai modèle de source
      locale sera défini en Phase ultérieure.

  - Kill switch :
      Variable d'environnement (par défaut LUMENA_MCP_INSTALL_DISABLED).
      Si présente et truthy → InstallError à propose et execute.

Layout disque :
  DATA_DIR/mcp_install_orchestrator/audit.jsonl
  <install_root>/<server_id>/  (géré par MCPSandboxRunner)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from src.mcp.approval_queue import (
    ApprovalDecision,
    ApprovalQueue,
    ApprovalResult,
)
from src.mcp.local_package import LocalMCPPackageError, resolve_local_mcp_package
from src.mcp.policy import MCPPolicy
from src.mcp.sandbox_runner import (
    MCPInstallSpec,
    MCPSandboxError,
    MCPSandboxRunner,
)
from src.mcp.server_catalog import (
    MCPServerCatalog,
    ServerStatus,
)
from src.utils.paths import DATA_DIR


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_install_orchestrator"
_AUDIT_FILENAME = "audit.jsonl"
_DEFAULT_INSTALL_SUBDIRNAME = "mcp_install"
_SERVERS_SUBDIR = "servers"

_DEFAULT_MIN_TRUST = 70
_DEFAULT_ENV_DISABLE_FLAG = "LUMENA_MCP_INSTALL_DISABLED"

_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions / Enums / Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class InstallError(Exception):
    """Erreur globale du orchestrator (server_id invalide, kill switch,
    pré-conditions Catalog inacceptables côté propose, etc.)."""


class InstallTransport(Enum):
    NPM   = "npm"
    PYPI  = "pypi"
    LOCAL = "local"


@dataclass(frozen=True)
class InstallProposal:
    server_id: str
    transport: InstallTransport
    package_name: str
    package_spec: str
    version: Optional[str]
    trust_score: int
    approval_ticket_id: str
    proposed_at: str


@dataclass(frozen=True)
class InstallResult:
    server_id: str
    success: bool
    transport: InstallTransport
    target_path_relative: Optional[str]
    reason: str
    duration_s: float
    dry_run: bool = False


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


def _parse_package_spec(
    package_spec: Any,
) -> Optional[Tuple[InstallTransport, str]]:
    """Parse "npm:<pkg>" / "pypi:<pkg>" / "local:<slug>".

    Returns (transport, package_name) ou None si parsing échoue.
    """
    if not isinstance(package_spec, str) or not package_spec:
        return None
    if package_spec.startswith("npm:"):
        rest = package_spec[len("npm:"):]
        if not rest:
            return None
        return InstallTransport.NPM, rest
    if package_spec.startswith("pypi:"):
        rest = package_spec[len("pypi:"):]
        if not rest:
            return None
        return InstallTransport.PYPI, rest
    if package_spec.startswith("local:"):
        rest = package_spec[len("local:"):]
        if not rest:
            return None
        return InstallTransport.LOCAL, rest
    return None


def _transport_to_runner_transport(transport: InstallTransport) -> str:
    """Mapping Phase 18/27 -> Phase 5 MCPInstallSpec.transport."""
    if transport == InstallTransport.NPM:
        return "npm"
    if transport == InstallTransport.PYPI:
        return "uv"
    if transport == InstallTransport.LOCAL:
        return "uv"
    raise InstallError(f"Transport {transport.value!r} not mappable to runner")


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────


class MCPInstallOrchestrator:
    """Orchestrateur d'installation MCP approuvée."""

    def __init__(
        self,
        catalog: MCPServerCatalog,
        approval_queue: ApprovalQueue,
        install_root: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
        min_trust_score_for_install: int = _DEFAULT_MIN_TRUST,
        dry_run: bool = True,
        env_disable_flag: str = _DEFAULT_ENV_DISABLE_FLAG,
    ):
        if catalog is None:
            raise ValueError("catalog must not be None")
        if approval_queue is None:
            raise ValueError("approval_queue must not be None")
        if not callable(getattr(approval_queue, "propose", None)):
            raise ValueError("approval_queue must expose .propose()")
        if (
            not isinstance(min_trust_score_for_install, int)
            or isinstance(min_trust_score_for_install, bool)
        ):
            raise ValueError("min_trust_score_for_install must be int")
        if not (0 <= min_trust_score_for_install <= 100):
            raise ValueError("min_trust_score_for_install must be in [0,100]")
        if not isinstance(env_disable_flag, str) or not env_disable_flag:
            raise ValueError("env_disable_flag must be a non-empty string")

        self._catalog = catalog
        self._approval_queue = approval_queue
        self._install_root = install_root or (
            DATA_DIR / _DEFAULT_INSTALL_SUBDIRNAME / _SERVERS_SUBDIR
        )
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._min_trust = int(min_trust_score_for_install)
        self._dry_run = bool(dry_run)
        self._env_disable_flag = env_disable_flag

        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._install_root.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def install_root(self) -> Path:
        return self._install_root

    @property
    def min_trust_score_for_install(self) -> int:
        return self._min_trust

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def env_disable_flag(self) -> str:
        return self._env_disable_flag

    # ── Kill switch ───────────────────────────────────────────────────────

    def _kill_switch_active(self) -> bool:
        value = os.environ.get(self._env_disable_flag)
        if value is None:
            return False
        return value.strip().lower() not in ("", "0", "false", "no")

    # ── Audit ─────────────────────────────────────────────────────────────

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit.

        Whitelist : server_id, transport, trust_score, ticket_id, reason,
        target_path_relative, duration_s, dry_run, status, ts.
        JAMAIS : approved_args raw, ApprovalResult stringification,
        package_spec/version/notes display_name du Catalog,
        stringification du runner/queue/catalog.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.install_orchestrator] audit failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # propose_install
    # ══════════════════════════════════════════════════════════════════════

    def propose_install(
        self,
        server_id: Any,
        *,
        caller_kind: str = "silent",
    ) -> InstallProposal:
        """Propose un install. Crée un ticket ApprovalQueue.

        N'exécute AUCUN subprocess. Le caller obtient le ticket_id et
        doit ensuite faire approuver l'action via la queue avant
        d'appeler execute_approved_install.
        """
        # 1. Kill switch
        if self._kill_switch_active():
            self._audit("install_disabled", reason="install_disabled")
            raise InstallError("install_disabled")

        # 2. Validate server_id
        if not _is_valid_server_id(server_id):
            # Anti-leak : on ne logue PAS server_id potentiellement
            # attacker-controlled
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise InstallError("server_id_invalid")

        # 3. Catalog get_server
        entry = self._catalog.get_server(server_id)
        if entry is None:
            self._audit(
                "propose_failed",
                server_id=server_id,
                reason="server_unknown",
            )
            raise InstallError("server_unknown")

        # 4. Status doit être DECLARED
        if entry.status != ServerStatus.DECLARED:
            self._audit(
                "propose_failed",
                server_id=server_id,
                status=entry.status.value,
                reason="status_not_declared",
            )
            raise InstallError(f"status_not_declared:{entry.status.value}")

        # 5. Parse package_spec
        parsed = _parse_package_spec(entry.package_spec)
        if parsed is None:
            self._audit(
                "propose_failed",
                server_id=server_id,
                reason="transport_unsupported",
            )
            raise InstallError("transport_unsupported")
        transport, package_name = parsed

        # 6. Trust score
        trust_score = entry.trust_score
        if trust_score is None:
            self._audit(
                "propose_failed",
                server_id=server_id,
                transport=transport.value,
                reason="trust_score_missing",
            )
            raise InstallError("trust_score_missing")
        if trust_score < self._min_trust:
            self._audit(
                "propose_failed",
                server_id=server_id,
                transport=transport.value,
                trust_score=trust_score,
                reason="trust_too_low_for_install",
            )
            raise InstallError(f"trust_too_low_for_install:{trust_score}")

        # 7. Build ticket
        tool_name = f"mcp_install:{server_id}"
        risk_summary = f"mcp_install:{transport.value}:{trust_score}"
        args = {
            "server_id": server_id,
            "transport": transport.value,
            "package_name": package_name,
            "package_spec": entry.package_spec,
            "version": entry.version,
            "trust_score": trust_score,
        }

        # 8. ApprovalQueue.propose
        ticket_id = self._approval_queue.propose(
            tool_name=tool_name,
            args=args,
            policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
            caller_kind=caller_kind,
            risk_summary=risk_summary,
        )

        proposed_at = _now_iso()

        # 9. Audit
        self._audit(
            "install_proposed",
            server_id=server_id,
            transport=transport.value,
            trust_score=trust_score,
            ticket_id=ticket_id,
        )

        # 10. Return InstallProposal
        return InstallProposal(
            server_id=server_id,
            transport=transport,
            package_name=package_name,
            package_spec=entry.package_spec,
            version=entry.version,
            trust_score=trust_score,
            approval_ticket_id=ticket_id,
            proposed_at=proposed_at,
        )

    # ══════════════════════════════════════════════════════════════════════
    # execute_approved_install
    # ══════════════════════════════════════════════════════════════════════

    def execute_approved_install(
        self,
        server_id: Any,
        approval_result: Any,
    ) -> InstallResult:
        """Exécute via MCPSandboxRunner.install() après validation humaine.

        Le caller a obtenu l'ApprovalResult APPROVED via
        ApprovalQueue.approve(ticket_id). Cette méthode NE TOUCHE JAMAIS
        à approval_queue.approve() — c'est l'action humaine.
        """
        start_ts = time.monotonic()

        # 1. Kill switch
        if self._kill_switch_active():
            self._audit("install_disabled", reason="install_disabled")
            raise InstallError("install_disabled")

        # 2. Validate server_id format
        if not _is_valid_server_id(server_id):
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise InstallError("server_id_invalid")

        # 3. Validate ApprovalResult
        if approval_result is None or not isinstance(
            approval_result, ApprovalResult
        ):
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="approval_invalid",
            )
            raise InstallError("approval_invalid")

        # 4. decision must be APPROVED
        if approval_result.decision != ApprovalDecision.APPROVED:
            reason = f"approval_not_granted:{approval_result.decision.value}"
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason=reason,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,  # placeholder
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 5. args must be present
        approved_args = approval_result.args
        if approved_args is None or not isinstance(approved_args, dict) or not approved_args:
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="approved_args_missing",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason="approved_args_missing",
                duration_s=time.monotonic() - start_ts,
            )

        # 6. Anti-confused-deputy : args["server_id"] doit matcher
        if approved_args.get("server_id") != server_id:
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="approved_server_id_mismatch",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason="approved_server_id_mismatch",
                duration_s=time.monotonic() - start_ts,
            )

        # 7. Catalog re-check
        entry = self._catalog.get_server(server_id)
        if entry is None:
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="server_unknown",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason="server_unknown",
                duration_s=time.monotonic() - start_ts,
            )

        # 8. status doit être DECLARED
        if entry.status != ServerStatus.DECLARED:
            reason = f"status_not_declared:{entry.status.value}"
            self._audit(
                "execute_failed",
                server_id=server_id,
                status=entry.status.value,
                reason=reason,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 9. Catalog vs args APPROVED : cohérence
        if approved_args.get("package_spec") != entry.package_spec:
            reason = "catalog_changed:package_spec"
            self._audit("execute_failed", server_id=server_id, reason=reason)
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )
        if approved_args.get("version") != entry.version:
            reason = "catalog_changed:version"
            self._audit("execute_failed", server_id=server_id, reason=reason)
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )
        if approved_args.get("trust_score") != entry.trust_score:
            reason = "catalog_changed:trust_score"
            self._audit("execute_failed", server_id=server_id, reason=reason)
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 10. Trust re-check (defensive)
        if entry.trust_score is None or entry.trust_score < self._min_trust:
            reason = "trust_too_low_for_install"
            self._audit("execute_failed", server_id=server_id, reason=reason)
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 11. Parse transport
        parsed = _parse_package_spec(entry.package_spec)
        if parsed is None:
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="transport_unsupported",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=InstallTransport.NPM,
                target_path_relative=None,
                reason="transport_unsupported",
                duration_s=time.monotonic() - start_ts,
            )
        transport, package_name = parsed

        # 11b. Catalog vs args : transport et package_name dérivés
        # (cohérence après parsing depuis Catalog ; on ne logue PAS les
        # valeurs approuvées raw, juste le code court)
        if approved_args.get("transport") != transport.value:
            reason = "catalog_changed:transport"
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason=reason,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )
        if approved_args.get("package_name") != package_name:
            reason = "catalog_changed:package_name"
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason=reason,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 12. dry_run : aucune mutation
        if self._dry_run:
            self._audit(
                "dry_run_install",
                server_id=server_id,
                transport=transport.value,
                target_path_relative=server_id,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=server_id,
                reason="dry_run",
                duration_s=time.monotonic() - start_ts,
                dry_run=True,
            )

        # 13. Construct MCPInstallSpec + run install via Phase 5
        try:
            runner_transport = _transport_to_runner_transport(transport)
        except InstallError as e:
            self._audit(
                "execute_failed",
                server_id=server_id,
                reason="transport_unsupported",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason="transport_unsupported",
                duration_s=time.monotonic() - start_ts,
            )

        try:
            spec_kwargs: Dict[str, Any] = {
                "name": server_id,
                "transport": runner_transport,
                "package": package_name,
                "package_version": entry.version,
                "trust_score": entry.trust_score,
            }
            if transport == InstallTransport.LOCAL:
                local_pkg = resolve_local_mcp_package(server_id)
                spec_kwargs.update({
                    "package": str(local_pkg.package_dir),
                    "args": ["-m", local_pkg.module_name],
                    "package_version": None,
                    "require_wheels_only": False,
                })
            spec = MCPInstallSpec(**spec_kwargs)
        except LocalMCPPackageError:
            self._audit(
                "install_failed",
                server_id=server_id,
                transport=transport.value,
                reason="local_package_missing",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason="local_package_missing",
                duration_s=time.monotonic() - start_ts,
            )
        except (MCPSandboxError, Exception):  # noqa: BLE001
            self._audit(
                "install_failed",
                server_id=server_id,
                transport=transport.value,
                reason="spec_invalid",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason="spec_invalid",
                duration_s=time.monotonic() - start_ts,
            )

        self._audit(
            "install_started",
            server_id=server_id,
            transport=transport.value,
        )

        try:
            runner = MCPSandboxRunner(
                spec,
                mcp_root=self._install_root,
                stdout_mode="client",
            )
            runner.install()
        except Exception as install_exc:  # noqa: BLE001
            # Phase I-8 (Fix AK.2) : l'exception était totalement avalée —
            # `runner_install_failed` muet a coûté une session de debug
            # (runtime 2026-06-11 22:00, échec pypi transitoire intraçable).
            # Règle I-7 : message complet dans le LOG, type court dans le
            # reason (payload) — JAMAIS de message libre dans _audit.
            logger.warning(
                "[mcp.install_orchestrator] runner install failed for "
                "'{}' ({}): {}: {}",
                server_id,
                transport.value,
                type(install_exc).__name__,
                str(install_exc)[:400],
            )
            reason = f"runner_install_failed:{type(install_exc).__name__}"
            self._audit(
                "install_failed",
                server_id=server_id,
                transport=transport.value,
                reason="runner_install_failed",
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        # 15. Catalog update DECLARED → INSTALLED
        try:
            self._catalog.update_status(server_id, ServerStatus.INSTALLED)
        except Exception:  # noqa: BLE001
            reason = "catalog_update_failed"
            self._audit(
                "install_failed",
                server_id=server_id,
                transport=transport.value,
                reason=reason,
            )
            return InstallResult(
                server_id=server_id,
                success=False,
                transport=transport,
                target_path_relative=None,
                reason=reason,
                duration_s=time.monotonic() - start_ts,
            )

        duration_s = time.monotonic() - start_ts
        self._audit(
            "install_completed",
            server_id=server_id,
            transport=transport.value,
            target_path_relative=server_id,
            duration_s=duration_s,
        )
        return InstallResult(
            server_id=server_id,
            success=True,
            transport=transport,
            target_path_relative=server_id,
            reason="installed_ok",
            duration_s=duration_s,
        )
