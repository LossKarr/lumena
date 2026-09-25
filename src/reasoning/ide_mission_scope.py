"""CONN-5A - perimetre de mission des appels IDE, hors de react.py.

Le perimetre est DERIVE de la projection HandlerContext posee avant chaque outil,
puis REVALIDE contre le TaskOrchestrator. La normalisation reutilise les memes
methodes que les gardes natifs `_assert_mission_file_allowed` et G1
(`mission_workspace_subdir`, `mission_allowed_files_set`) : un appel IDE ne peut
pas comprendre un dossier ou une liste de fichiers autrement que la voie native.

Le role vient de `metadata.parent_id`, jamais de `allowed_files`. Mesure du
14 septembre 2026 : sur 476 workers, 179 n'ont pas la cle `allowed_files`. Un
worker d'effets (H4) serait donc pris pour le lead - le piege H4-b.

Une mission n'a pas de RuntimeContext propre (seul le chat en pousse un). Le
`task_id` du RuntimeContext ne suffit donc pas a reconnaitre une mission : le
chat y pose celui de la tache de son tour (`web/routes/chat.py`, l. 1475-1481 et
1825-1843). Il sert seulement a ne jamais traiter une VRAIE mission, dont la
projection manquerait, comme du chat.
"""
from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
from typing import Any, Optional

from .external_tool_registry import ExternalToolError
# LOT L5-3c-1 : la normalisation est descendue dans `tools`, ou le pont et le
# lanceur peuvent l'atteindre sans inverser la dependance. Reexportee ici : les
# 14 appelants existants, tests compris, continuent de l'importer d'ici.
from ..tools.ide_paths import canonical_workspace  # noqa: F401
from .tool_semantics import Confirmation, MissionPolicy, ProofCapability, ToolEffect


class MissionScopeError(ExternalToolError):
    """Refus fail-closed d'un appel IDE en mission. Le message est le code stable."""


@dataclass(frozen=True, slots=True)
class MissionScope:
    task_id: str
    parent_id: Optional[str]
    role: str
    depth: Optional[int]
    mission_workspace: str
    mission_root: str
    allowed_files: Optional[frozenset]
    caller_kind: str



def _orchestrator(handler_ctx: Any) -> Any:
    core = getattr(handler_ctx, "lumena", None)
    return getattr(core, "task_orchestrator", None) if core is not None else None


def _task(orch: Any, task_id: Any) -> Optional[dict]:
    if orch is None or not task_id:
        return None
    try:
        task = orch.get_task(task_id)
    except Exception:
        return None
    return task if isinstance(task, dict) else None


def _depth(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def derive_mission_scope(
    handler_ctx: Any, *, runtime_task_id: Any = None, caller: Any = None,
) -> Optional[MissionScope]:
    """Rend le perimetre de la mission courante, `None` hors mission.

    Leve `MissionScopeError` des qu'une mission est en jeu sans pouvoir etre
    prouvee complete, coherente, active et appelee par un agent identifie.
    """
    orch = _orchestrator(handler_ctx)
    if not bool(getattr(handler_ctx, "is_mission_run", False)):
        if not runtime_task_id:
            return None
        if orch is None:
            # Une tache existe mais son type est inverifiable : ne pas deviner du chat.
            raise MissionScopeError("ide_mission_scope_incomplete")
        task = _task(orch, runtime_task_id)
        if task is None:
            raise MissionScopeError("ide_mission_scope_incomplete")
        if (task.get("metadata") or {}).get("kind") != "mission":
            return None
        # Vraie mission dont la projection n'a pas ete posee.
        raise MissionScopeError("ide_mission_scope_incomplete")

    task_id = getattr(handler_ctx, "runtime_task_id", None)
    task = _task(orch, task_id)
    guardrails = getattr(handler_ctx, "file_guardrails", None)
    if task is None or guardrails is None:
        raise MissionScopeError("ide_mission_scope_incomplete")
    meta = task.get("metadata") or {}
    if meta.get("kind") != "mission":
        raise MissionScopeError("ide_mission_scope_incomplete")
    projected = handler_ctx.mission_workspace_subdir()
    if not projected:
        raise MissionScopeError("ide_mission_scope_incomplete")

    raw_files = meta.get("allowed_files")
    authoritative = replace(
        handler_ctx,
        mission_workspace=str(meta.get("mission_workspace") or ""),
        mission_allowed_files=list(raw_files) if isinstance(raw_files, (list, tuple)) else [],
    )
    if authoritative.mission_workspace_subdir() != projected:
        raise MissionScopeError("ide_mission_scope_divergent")
    allowed_files = handler_ctx.mission_allowed_files_set()
    if authoritative.mission_allowed_files_set() != allowed_files:
        raise MissionScopeError("ide_mission_scope_divergent")

    try:
        cancelled = bool(orch.is_cancel_requested(task_id))
    except Exception:
        raise MissionScopeError("ide_mission_scope_incomplete") from None
    if cancelled:
        raise MissionScopeError("ide_mission_cancelled")

    kind = getattr(caller, "kind", None)
    if not kind or kind == "unknown":
        raise MissionScopeError("ide_mission_caller_unknown")

    try:
        mission_root = canonical_workspace(str(guardrails._workspace_root().resolve() / projected))
    except Exception:
        mission_root = None
    if mission_root is None:
        raise MissionScopeError("ide_mission_scope_incomplete")

    parent = meta.get("parent_id")
    parent_id = parent if isinstance(parent, str) and parent else None
    return MissionScope(
        task_id=task_id, parent_id=parent_id, role="worker" if parent_id else "lead",
        depth=_depth(meta.get("depth")), mission_workspace=projected, mission_root=mission_root,
        allowed_files=allowed_files, caller_kind=kind,
    )


def is_mission_content_write(semantics: Any, required_parameters: Any = frozenset()) -> bool:
    """CONN-5B-1 : ecriture de CONTENU de fichier dont la cible est un parametre.

    Sur le catalogue audite, cela designe `write_file` seule. `editor_save` prend
    sa cible dans l'onglet actif, `sidebar_create_file`/`folder` n'ont ni contenu
    ni verificateur, et suppressions/renommages exigent une confirmation always.
    """
    required = frozenset(required_parameters or ())
    return (semantics.mission_policy is MissionPolicy.SCOPED
            and semantics.effect is ToolEffect.FILE_WRITE
            and semantics.confirmation is Confirmation.POLICY
            and ProofCapability.FILE_WRITE in semantics.proof_capabilities
            and {"path", "content"} <= required)


def authorize_mission_call(
    scope: MissionScope, semantics: Any, ide_workspace_path: Any, *, required_parameters: Any = frozenset(),
) -> None:
    """Porte de mission : lecture de fichier confinee (5A), ecriture de contenu (5B-1),
    listes et execution validee de taches et de tests (5C-1).

    Refus intrinseques a la commande d'abord, situation de l'IDE ensuite.
    """
    from .ide_mission_execution import is_mission_execution, is_mission_listing

    policy = semantics.mission_policy
    if policy is MissionPolicy.FORBIDDEN:
        raise MissionScopeError("ide_mission_policy_forbidden")
    if policy is MissionPolicy.READONLY:
        # Les listes de l'IDE ouverte sur le dossier de mission ; l'historique global
        # des executions (task_runs, test_runs) reste ferme.
        if not is_mission_listing(semantics) and (
                semantics.effect is not ToolEffect.READ_ONLY
                or semantics.confirmation is not Confirmation.NEVER
                or ProofCapability.FILE_READ not in semantics.proof_capabilities):
            raise MissionScopeError("ide_mission_read_unconfined")
    elif not (is_mission_content_write(semantics, required_parameters) or is_mission_execution(semantics)):
        raise MissionScopeError("ide_mission_mutation_not_connected")
    if canonical_workspace(ide_workspace_path) != scope.mission_root:
        raise MissionScopeError("ide_mission_workspace_mismatch")


@dataclass(frozen=True, slots=True)
class MissionWrite:
    target: Path
    lease_key: str
    after_sha256: str
    relative_target: str


_CONTRACT_BASENAMES = ("contract.json", "contrat.md")


def _write_refused(reason: Any) -> MissionScopeError:
    return MissionScopeError(f"ide_mission_write_refused - {reason}")


def prepare_mission_write(scope: MissionScope, handler_ctx: Any, parameters: Any) -> MissionWrite:
    """Meme cible et memes gardes que `write_file` natif (`handlers/files.py`).

    Les fonctions natives sont APPELEES, pas recopiees : une ecriture IDE ne peut
    pas etre jugee autrement qu'une ecriture native sur le meme fichier.
    """
    from ..tools.file_guardrails import PathSecurityError
    from .handlers.files import assert_write_allowed

    path = parameters.get("path") if isinstance(parameters, dict) else None
    content = parameters.get("content") if isinstance(parameters, dict) else None
    if type(path) is not str or not path.strip() or type(content) is not str:
        raise MissionScopeError("ide_mission_write_parameters_invalid")
    # LOT 2.10 : en mission, le contrat se pose par write_mission_contract.
    if Path(path).name.lower() in _CONTRACT_BASENAMES:
        raise _write_refused("en mission, contract.json/CONTRAT.md se posent UNIQUEMENT via write_mission_contract")
    try:
        target, _, _ = handler_ctx.file_guardrails.resolve_write_target(
            path, mission_workspace_subdir=scope.mission_workspace,
        )
    except PathSecurityError as exc:
        raise _write_refused(exc) from None
    canonical = canonical_workspace(str(target))
    root = scope.mission_root
    if canonical is None or not (canonical == root or canonical.startswith(root.rstrip(os.sep) + os.sep)):
        raise MissionScopeError("ide_mission_target_outside")
    try:
        mission_dir = handler_ctx.file_guardrails._workspace_root().resolve() / scope.mission_workspace
        relative_target = Path(target).resolve().relative_to(mission_dir.resolve()).as_posix()
    except (OSError, ValueError, RuntimeError):
        raise MissionScopeError("ide_mission_target_outside") from None
    try:
        # Garde d'ecriture commune native (lot L1c) : frontiere, perimetre, liste
        # noire, code de Lumena.
        assert_write_allowed(target, handler_ctx)
    except PathSecurityError as exc:
        raise _write_refused(exc) from None
    # Patch strict natif, evalue ici AVANT tout controle de transport ; il est rejoue
    # sous le lease juste avant l'envoi (mission_write_guard).
    if _patch_strict(handler_ctx) and Path(target).exists():
        raise _write_refused("Patch strict actif: fichier existant. Utilise edit_file ou apply_patch.")
    return MissionWrite(
        target=Path(canonical), lease_key="files:" + canonical,
        after_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(), relative_target=relative_target,
    )


def mission_envelope_scope(scope: MissionScope, write: MissionWrite) -> dict:
    """CONN-5B-2 : ce que l'IDE revalide avant effet (protocole 4 seulement)."""
    return {"task_id": scope.task_id, "role": scope.role,
            "allowed_files": sorted(scope.allowed_files or ()), "target": write.relative_target}


def _patch_strict(handler_ctx: Any) -> bool:
    enabled = getattr(handler_ctx, "patch_strict_enabled", None)
    try:
        return bool(enabled()) if callable(enabled) else True
    except Exception:
        return True


@asynccontextmanager
async def mission_write_guard(write: Optional[MissionWrite], handler_ctx: Any):
    """Tient `files:<cible canonique>` du controle d'existence jusqu'a la reponse.

    Seule l'ATTENTE du lease devient `ide_mission_write_busy` : une erreur levee
    pendant l'envoi remonte telle quelle.
    """
    if write is None:
        yield
        return
    import asyncio

    from ..subagents.resource_lease import get_resource_lease, lease_wait_timeout

    stack = AsyncExitStack()
    try:
        await stack.enter_async_context(get_resource_lease().hold(write.lease_key, timeout=lease_wait_timeout()))
    except asyncio.TimeoutError:
        raise MissionScopeError("ide_mission_write_busy") from None
    async with stack:
        # Patch strict natif : une ecriture ne remplace jamais un fichier existant.
        if _patch_strict(handler_ctx) and write.target.exists():
            raise _write_refused("Patch strict actif: fichier existant. Utilise edit_file ou apply_patch.")
        yield


def verify_mission_write(execution: Any, semantics: Any, scope: Optional[MissionScope],
                         session: Any, write: Optional[MissionWrite]) -> Any:
    """Producteur de preuve CONN-5B-1, exactement comme la sonde du canari.

    Une preuve invalide rend `None` : jamais de succes prouve par defaut.
    """
    if write is None or scope is None or execution is None:
        return None
    from .execution_evidence import EvidenceError, EvidenceScope, verify_execution

    try:
        evidence_scope = EvidenceScope(
            Path(scope.mission_root), session.workspace_id, session.instance_id, session.catalogue_hash,
            expected_target=write.target, expected_after_sha256=write.after_sha256,
        )
        return verify_execution(execution, semantics, evidence_scope)
    except (EvidenceError, TypeError, ValueError):
        return None
