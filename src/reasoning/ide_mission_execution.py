"""CONN-5C-1 - execution validee en mission : taches et tests lances par l'IDE.

Meme pouvoir, memes gardes, une preuve :

- la commande REELLE est jugee par les gardes natifs de `run_command`
  (`sanitize_chained_command` puis G1 `_mission_destructive_target_violation`), sur
  la ligne ET sur le corps du script `package.json` : `npm run build` execute ce
  corps, que la mission peut ecrire (mesure du 15 septembre : l'enveloppe
  `cmd.exe /d /s /c call npm run build` passe le sanitizer) ;
- l'empreinte de ce qui a ete valide voyage dans `mission_scope.execution` ;
  Electron la recalcule sur la tache redecouverte au lancement ;
- l'execution ne compte que par une preuve verifiee, obtenue par le suivi interne
  `operation_get` (jamais expose au modele).

Le code d'un test ou d'un script valide s'execute sans confinement : c'est la
parite avec `run_command pytest` natif, pas un bac a sable.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Optional

from .ide_mission_scope import MissionScope, MissionScopeError, canonical_workspace
from .tool_semantics import Confirmation, MissionPolicy, ProofCapability, ToolEffect

TASK_SOURCES = frozenset({"package", "lumena", "pytest", "cargo", "go"})
TEST_ADAPTERS = frozenset({"pytest", "vitest", "jest"})
MISSION_LISTINGS = frozenset({"ide__task_list", "ide__test_list"})
POLL_INTERVAL_S = 0.5
_MAX_WAIT_S = 1800


@dataclass(frozen=True, slots=True)
class MissionExecution:
    kind: str
    item_id: str
    command_sha256: Optional[str]
    executable: Optional[str] = None
    args: Optional[tuple] = None


def canonical_command(*, source: str, task_id: str, executable: str, args: Any,
                      cwd_relative: str, script: Optional[str]) -> str:
    """Forme canonique partagee avec Electron (`missionCommandCanonical`)."""
    fields = {"source": source, "id": task_id, "executable": executable, "args": list(args),
              "cwd_relative": cwd_relative, "script": script}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def command_digest(**fields: Any) -> str:
    return hashlib.sha256(canonical_command(**fields).encode("utf-8")).hexdigest()


def is_mission_listing(semantics: Any) -> bool:
    return (semantics.tool_name in MISSION_LISTINGS
            and semantics.mission_policy is MissionPolicy.READONLY
            and semantics.effect is ToolEffect.READ_ONLY
            and semantics.confirmation is Confirmation.NEVER)


def is_mission_execution(semantics: Any) -> bool:
    """`task_run` (lancement) ou `test_run` (tests), avec leur verificateur 4B."""
    if semantics.mission_policy is not MissionPolicy.SCOPED or semantics.confirmation is not Confirmation.POLICY:
        return False
    if semantics.tool_name in ("ide__task_run", "ide__command_run"):
        return (semantics.effect is ToolEffect.PROCESS_LAUNCH
                and ProofCapability.PROCESS_LAUNCH in semantics.proof_capabilities)
    if semantics.tool_name == "ide__test_run":
        return (semantics.effect is ToolEffect.TEST_EXECUTION
                and ProofCapability.TEST_EXECUTION in semantics.proof_capabilities)
    return False


def listing_action(semantics: Any) -> str:
    return "task_list" if semantics.tool_name == "ide__task_run" else "test_list"


def _inside(scope: MissionScope, path: Any) -> bool:
    canonical = canonical_workspace(path)
    root = scope.mission_root
    return canonical is not None and (canonical == root or canonical.startswith(root.rstrip(os.sep) + os.sep))


def _command_refused(reason: Any) -> MissionScopeError:
    return MissionScopeError(f"ide_mission_command_refused - {reason}")


def _judge(handler_ctx: Any, command: str) -> None:
    """Exactement les deux gardes de `run_command`, dans le meme ordre."""
    from ..utils.command_sanitizer import sanitize_chained_command
    from .handlers.system import _mission_destructive_target_violation

    extra = getattr(handler_ctx, "_discovered_executables", None) or None
    allowed, reason = sanitize_chained_command(command, extra_allowed=extra)
    if not allowed:
        raise _command_refused(reason)
    violation = _mission_destructive_target_violation(handler_ctx, command)
    if violation:
        raise _command_refused(f"commande destructive hors du dossier de mission : {violation}")


def _package_script(cwd: Path, name: str) -> Optional[str]:
    try:
        data = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    value = scripts.get(name) if isinstance(scripts, dict) else None
    return value if type(value) is str else None


def prepare_mission_execution(scope: MissionScope, handler_ctx: Any, semantics: Any,
                              parameters: Any, listing: Any) -> MissionExecution:
    """Retrouve la commande reelle dans la liste de l'IDE et la juge comme `run_command`."""
    if type(listing) is not dict or listing.get("success") is not True:
        raise MissionScopeError("ide_mission_listing_unavailable")
    if semantics.tool_name == "ide__test_run":
        item_id = parameters.get("itemId") if isinstance(parameters, dict) else None
        items = listing.get("items")
        item = next((i for i in items if isinstance(i, dict) and i.get("id") == item_id), None) \
            if type(items) is list and type(item_id) is str else None
        if item is None:
            raise MissionScopeError("ide_mission_test_not_found")
        if item.get("adapter") not in TEST_ADAPTERS:
            raise MissionScopeError("ide_mission_test_adapter_forbidden")
        if not _inside(scope, item.get("path")):
            raise MissionScopeError("ide_mission_test_outside")
        return MissionExecution("test", item_id, None)

    task_id = parameters.get("taskId") if isinstance(parameters, dict) else None
    tasks = listing.get("tasks")
    task = next((t for t in tasks if isinstance(t, dict) and t.get("id") == task_id), None) \
        if type(tasks) is list and type(task_id) is str else None
    if task is None:
        raise MissionScopeError("ide_mission_task_not_found")
    source, executable, args, cwd = task.get("source"), task.get("executable"), task.get("args"), task.get("cwd")
    if source not in TASK_SOURCES:
        raise MissionScopeError("ide_mission_task_source_forbidden")
    if task.get("dependsOn"):
        raise MissionScopeError("ide_mission_task_pipeline_unsupported")
    if (type(executable) is not str or not executable or type(args) is not list
            or any(type(a) is not str for a in args)):
        raise MissionScopeError("ide_mission_task_invalid")
    if not _inside(scope, cwd):
        raise MissionScopeError("ide_mission_task_outside")
    try:
        mission_dir = (handler_ctx.file_guardrails._workspace_root() / scope.mission_workspace).resolve()
        cwd_relative = Path(cwd).resolve().relative_to(mission_dir).as_posix()
    except (OSError, ValueError, RuntimeError, AttributeError):
        raise MissionScopeError("ide_mission_task_outside") from None
    script = _package_script(Path(cwd), task_id[len("package:"):]) if source == "package" else None
    if source == "package" and script is None:
        raise MissionScopeError("ide_mission_task_not_found")
    _judge(handler_ctx, subprocess.list2cmdline([executable, *args]))
    if script is not None:
        _judge(handler_ctx, script)
    digest = command_digest(source=source, task_id=task_id, executable=executable, args=args,
                            cwd_relative=cwd_relative, script=script)
    return MissionExecution("task", task_id, digest, executable, tuple(args))


_MAX_COMMAND = 8192


def mission_command_shell(command: str, platform: Optional[str] = None) -> tuple:
    """CONN-5C-2 : tache synthetique du shell de la plateforme, identique cote Electron
    (`missionCommandTask`). Sous Windows, `/s` retire les guillemets exterieurs."""
    if (platform or sys.platform) == "win32":
        return "cmd.exe", ["/d", "/s", "/c", f'"{command}"']
    return "/bin/sh", ["-c", command]


def prepare_mission_command(scope: MissionScope, handler_ctx: Any, parameters: Any) -> MissionExecution:
    """CONN-5C-2 : commande unique, jugee comme `run_command` et sans lancement interactif."""
    from ..utils.interactive_commands import interactive_launch

    command = parameters.get("command") if isinstance(parameters, dict) else None
    if (type(command) is not str or not command.strip() or len(command) > _MAX_COMMAND
            or any(char in command for char in "\r\n\x00")):
        raise MissionScopeError("ide_mission_command_invalid")
    if interactive_launch(command):
        raise MissionScopeError("ide_mission_command_interactive")
    _judge(handler_ctx, command)
    executable, args = mission_command_shell(command)
    digest = command_digest(source="command", task_id="command", executable=executable, args=args,
                            cwd_relative=".", script=None)
    return MissionExecution("command", "command", digest, executable, tuple(args))


def mission_execution_envelope(scope: MissionScope, execution: MissionExecution) -> dict:
    return {"task_id": scope.task_id, "role": scope.role, "allowed_files": sorted(scope.allowed_files or ()),
            "execution": {"kind": execution.kind, "id": execution.item_id,
                          "command_sha256": execution.command_sha256}}


def _wait_seconds(handler_ctx: Any) -> float:
    """Delai IDE natif (`LUMENA_IDE_COMMAND_TIMEOUT_SEC`, 30 s minimum), borne au
    plafond de `run_command` ; « sans limite » native devient ce plafond."""
    getter = getattr(handler_ctx, "ide_command_timeout_sec", None)
    try:
        value = getter() if callable(getter) else 120
    except Exception:
        value = 120
    if value is None:
        return float(_MAX_WAIT_S)
    return float(min(value, _MAX_WAIT_S)) if isinstance(value, (int, float)) and value > 0 else 120.0


def _same_command(completion: Any, execution: MissionExecution) -> bool:
    if execution.kind == "test":
        return True
    process = completion.proof.get("process") if isinstance(completion.proof, dict) else None
    return (isinstance(process, dict) and process.get("executable") == execution.executable
            and process.get("args") == list(execution.args or ()))


async def follow_mission_execution(dispatch: Any, record: Any, semantics: Any, scope: MissionScope,
                                   session: Any, execution: MissionExecution, handler_ctx: Any):
    """Suivi interne borne. Rend `(completion_brute, preuve_verifiee, meme_commande, expire)`.

    La preuve verifiee libere le cache d'effets externes (fin OBSERVEE) meme quand
    le processus lance n'est pas celui qui a ete valide ; elle ne compte alors pas
    comme preuve pour le modele. `dispatch(action, params)` rend
    `(brut, ToolExecutionResult)` pour `operation_get`.
    """
    from .execution_evidence import EvidenceError, EvidenceScope, verify_execution

    if record is None:
        return None, None, False, False
    evidence_scope = EvidenceScope(Path(scope.mission_root), session.workspace_id, session.instance_id,
                                   session.catalogue_hash)
    reference = "transport:" + record.operation_id
    deadline = time.monotonic() + _wait_seconds(handler_ctx)
    raw = None
    while True:
        raw, completion = await dispatch("operation_get", {"operationId": reference})
        if completion is not None:
            try:
                evidence = verify_execution(record, semantics, evidence_scope, completion=completion)
            except (EvidenceError, TypeError, ValueError):
                return raw, None, False, False
            if evidence is not None:
                return raw, evidence, _same_command(completion, execution), False
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    # Parite avec run_command : un processus qui depasse son delai est arrete.
    try:
        await dispatch("operation_cancel", {"operationId": reference, "confirmed": True})
    except Exception:
        pass
    return raw, None, False, True
