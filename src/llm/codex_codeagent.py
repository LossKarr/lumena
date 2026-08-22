"""Opt-in CodeAgent execution through a connected Codex subscription.

The historical CodeAgent remains authoritative unless the explicit
``chatgpt_codex`` + ``codeagent`` configuration gate is enabled. This adapter
never starts App Server, never logs in, and never falls back to an API.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger

from src.llm.codex_app_server import (
    CodexAppServerError,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    codex_turn_execution_lock,
)
from src.llm.codex_subscription import (
    CodexSurface,
    CodexSubscriptionGateway,
    CodexSubscriptionSettings,
)
from src.utils.syntax_check import check_syntax


THREAD_START_METHOD = "thread/start"
TURN_START_METHOD = "turn/start"
TURN_INTERRUPT_METHOD = "turn/interrupt"

_CODE_AGENT_KINDS = frozenset({"code", "debug", "refactor"})
_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
    }
)
_TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py)$", re.I)
_TEST_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:pytest|python(?:\.exe)?\s+-m\s+pytest|py\s+-m\s+pytest|"
    r"npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test|"
    r"vitest|jest|cargo\s+test|go\s+test)(?:\s|$)",
    re.I,
)

@dataclass
class CodexCodeAgentResult:
    task_id: str
    success: bool
    output: str
    status_code: str = "success"
    meta: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0


def should_route_codeagent_to_codex(
    agent_type: str,
    settings: CodexSubscriptionSettings,
) -> bool:
    """Return true only for the explicit S4 CodeAgent pilot surface."""

    return (
        (agent_type or "").strip().lower() in _CODE_AGENT_KINDS
        and settings.surface_requested(CodexSurface.CODEAGENT)
    )


def _normalise_allowed_files(values: Sequence[str] | None) -> frozenset[str] | None:
    if not values:
        return None
    normalised: set[str] = set()
    for value in values:
        raw = str(value or "").strip().replace("\\", "/")
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute() or ":" in raw.split("/", 1)[0]:
            raise ValueError(f"allowed_files must be relative: {raw}")
        clean = Path(os.path.normpath(raw)).as_posix()
        if clean in {".", ".."} or clean.startswith("../"):
            raise ValueError(f"allowed_files escapes the workspace: {raw}")
        normalised.add(clean.lstrip("./"))
    if not normalised:
        raise ValueError("allowed_files cannot be empty after normalization")
    return frozenset(normalised)


def _relative_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if name not in _IGNORED_NAMES and not (current_path / name).is_symlink()
        ]
        for name in names:
            path = current_path / name
            if path.is_symlink():
                continue
            try:
                rel = path.relative_to(root).as_posix()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                continue
            files[rel] = digest
    return files


def _copy_workspace(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for current, dirs, names in os.walk(source, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if name not in _IGNORED_NAMES and not (current_path / name).is_symlink()
        ]
        rel_dir = current_path.relative_to(source)
        out_dir = target / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            src = current_path / name
            if src.is_symlink():
                continue
            shutil.copy2(src, out_dir / name)


def _changed_files(before: Mapping[str, str], root: Path) -> list[str]:
    after = _relative_files(root)
    return sorted(
        rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)
    )


def _tests_exist(root: Path) -> bool:
    return any(_TEST_FILE_RE.search(rel) for rel in _relative_files(root))


def _command_text(item: Mapping[str, Any]) -> str:
    command = item.get("command", "")
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _is_green_test_item(item: Mapping[str, Any]) -> bool:
    return (
        str(item.get("type", "")) == "commandExecution"
        and str(item.get("status", "")).lower() == "completed"
        and item.get("exitCode") == 0
        and bool(_TEST_COMMAND_RE.search(_command_text(item)))
    )


def _id_from_result(result: Any, key: str) -> str:
    if not isinstance(result, Mapping):
        return ""
    nested = result.get(key)
    if isinstance(nested, Mapping):
        return str(nested.get("id", "") or "")
    return str(result.get(f"{key}Id", "") or "")


def _event_matches(params: Any, *, thread_id: str, turn_id: str) -> bool:
    if not isinstance(params, Mapping):
        return False
    event_thread = str(params.get("threadId", "") or "")
    event_turn = str(params.get("turnId", "") or "")
    turn = params.get("turn")
    if isinstance(turn, Mapping):
        event_turn = event_turn or str(turn.get("id", "") or "")
    return (not event_thread or event_thread == thread_id) and (
        not event_turn or event_turn == turn_id
    )


def _build_task_prompt(
    description: str,
    *,
    context: Mapping[str, Any] | None,
    allowed_files: frozenset[str] | None,
) -> str:
    lines = [
        "Tu es le moteur CodeAgent de Lumena. Termine la tache de code ci-dessous ",
        "dans le workspace courant. Inspecte le code reel, modifie les fichiers, ",
        "execute les validations pertinentes et rapporte uniquement des faits prouves.",
        "",
        f"TACHE:\n{description.strip()}",
    ]
    if allowed_files:
        lines.extend(
            [
                "",
                "PERIMETRE D'ECRITURE STRICT (aucun autre fichier):",
                *[f"- {path}" for path in sorted(allowed_files)],
            ]
        )
    if context:
        original = str(context.get("user_original_request", "") or "").strip()
        skills = str(context.get("skills_context", "") or "").strip()
        if original:
            lines.extend(["", f"DEMANDE UTILISATEUR ORIGINALE:\n{original[:6000]}"])
        if skills:
            lines.extend(["", f"DISCIPLINE/SKILLS LUMENA:\n{skills[:6000]}"])
    return "\n".join(lines)


async def _interrupt_turn(
    supervisor: CodexAppServerSupervisor,
    thread_id: str,
    turn_id: str,
) -> None:
    if not thread_id or not turn_id or not supervisor.is_running:
        return
    try:
        await supervisor.request(
            TURN_INTERRUPT_METHOD,
            {"threadId": thread_id, "turnId": turn_id},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[CodeAgent/Codex] interruption non confirmee: {}", exc)


async def _run_turn(
    supervisor: CodexAppServerSupervisor,
    *,
    workspace: Path,
    prompt: str,
    model: str,
    timeout_s: float,
) -> tuple[str, list[dict[str, Any]], str, str]:
    thread_params: dict[str, Any] = {
        "cwd": str(workspace),
        "approvalPolicy": "never",
        "sandbox": "workspace-write",
        "serviceName": "lumena-codeagent",
    }
    if model:
        thread_params["model"] = model
    thread_result = await supervisor.request(
        THREAD_START_METHOD, thread_params, timeout=30
    )
    thread_id = _id_from_result(thread_result, "thread")
    if not thread_id:
        raise CodexAppServerError("thread/start returned no thread id")

    turn_params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "cwd": str(workspace),
        "approvalPolicy": "never",
        "sandboxPolicy": {
            "type": "workspaceWrite",
            "writableRoots": [str(workspace)],
            "networkAccess": False,
        },
    }
    if model:
        turn_params["model"] = model
    turn_result = await supervisor.request(TURN_START_METHOD, turn_params, timeout=30)
    turn_id = _id_from_result(turn_result, "turn")
    if not turn_id:
        raise CodexAppServerError("turn/start returned no turn id")

    items: list[dict[str, Any]] = []
    final_text = ""
    try:
        async with asyncio.timeout(timeout_s):
            while True:
                notification = await supervisor.next_notification(timeout=30)
                if not _event_matches(
                    notification.params, thread_id=thread_id, turn_id=turn_id
                ):
                    continue
                params = notification.params if isinstance(notification.params, Mapping) else {}
                if notification.method == "turn/plan/updated":
                    plan = params.get("plan") or []
                    logger.info("[CodeAgent/Codex] plan: {} etape(s)", len(plan))
                elif notification.method == "item/completed":
                    item = params.get("item")
                    if isinstance(item, Mapping):
                        item_dict = dict(item)
                        items.append(item_dict)
                        if item_dict.get("type") == "agentMessage":
                            final_text = str(item_dict.get("text", "") or final_text)
                        elif item_dict.get("type") == "commandExecution":
                            logger.info(
                                "[CodeAgent/Codex] commande terminee: status={} exit={}",
                                item_dict.get("status"),
                                item_dict.get("exitCode"),
                            )
                        elif item_dict.get("type") == "fileChange":
                            logger.info("[CodeAgent/Codex] modification fichier terminee")
                elif notification.method == "turn/completed":
                    turn = params.get("turn")
                    turn = turn if isinstance(turn, Mapping) else {}
                    status = str(turn.get("status", "") or "")
                    if status != "completed":
                        error = turn.get("error")
                        raise CodexAppServerError(
                            f"Codex turn ended with status={status}: {error}"
                        )
                    return final_text, items, thread_id, turn_id
    except (asyncio.CancelledError, TimeoutError, CodexAppServerTimeout):
        await _interrupt_turn(supervisor, thread_id, turn_id)
        raise


async def run_codeagent_with_codex_subscription(
    description: str,
    *,
    agent_type: str,
    context: Mapping[str, Any] | None,
    workspace_path: str | Path,
    allowed_files: Sequence[str] | None,
    settings: CodexSubscriptionSettings,
    supervisor: CodexAppServerSupervisor,
    timeout_s: float = 900.0,
) -> CodexCodeAgentResult:
    """Run one bounded CodeAgent task through the connected ChatGPT account."""

    started = time.monotonic()
    task_id = f"codex_{uuid.uuid4().hex[:12]}"
    workspace = Path(workspace_path).resolve()
    if not workspace.is_dir():
        return CodexCodeAgentResult(
            task_id=task_id,
            success=False,
            output=f"Workspace CodeAgent introuvable: {workspace}",
            status_code="invalid_workspace",
        )
    try:
        owned = _normalise_allowed_files(allowed_files)
    except ValueError as exc:
        return CodexCodeAgentResult(
            task_id=task_id,
            success=False,
            output=f"Perimetre CodeAgent invalide: {exc}",
            status_code="invalid_scope",
        )

    async with codex_turn_execution_lock():
        gateway = CodexSubscriptionGateway(supervisor)
        try:
            await gateway.require_chatgpt_account()
            models = await gateway.list_models()
        except Exception as exc:
            return CodexCodeAgentResult(
                task_id=task_id,
                success=False,
                output=f"Session ChatGPT Codex inutilisable: {exc}",
                status_code="account_unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        available = {item.model_id for item in models}
        model = settings.default_model if settings.default_model in available else ""
        if not model:
            model = next((item.model_id for item in models if item.is_default), "")

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        execution_root = workspace
        try:
            if owned is not None:
                temp_dir = tempfile.TemporaryDirectory(prefix="lumena-codex-scope-")
                execution_root = Path(temp_dir.name) / "workspace"
                _copy_workspace(workspace, execution_root)
            before = _relative_files(execution_root)
            prompt = _build_task_prompt(
                description, context=context, allowed_files=owned
            )
            logger.info(
                "[CodeAgent/Codex] demarrage task={} workspace={} model={} scope={}",
                task_id,
                workspace,
                model or "server-default",
                len(owned or ()),
            )
            final_text, items, thread_id, turn_id = await _run_turn(
                supervisor,
                workspace=execution_root,
                prompt=prompt,
                model=model,
                timeout_s=timeout_s,
            )
            changed = _changed_files(before, execution_root)
            unauthorized = sorted(set(changed) - set(owned or changed))
            if unauthorized:
                return CodexCodeAgentResult(
                    task_id=task_id,
                    success=False,
                    output=(
                        "Codex a modifie des fichiers hors du perimetre autorise; "
                        "aucune modification n'a ete appliquee au workspace reel: "
                        + ", ".join(unauthorized)
                    ),
                    status_code="scope_violation",
                    meta={"thread_id": thread_id, "turn_id": turn_id},
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            # LOT Z35 — un CodeAgent qui n'a RIEN ecrit n'a pas reussi.
            #
            # Run « gobelet motion » (2026-08-21), au log :
            #   14:27:31  [create_project] Mode CodeAgent route=codex_subscription
            #   14:27:33  [CodeAgent/Codex] demarrage workspace=workspace\gobelet-motion
            #   14:27:43  commande terminee: status=failed exit=1
            #   14:27:48  commande terminee: status=completed exit=0
            # Sur le disque : `workspace/gobelet-motion` = 0 fichier. Et pourtant
            # cette fonction renvoyait `success=True`.
            #
            # `changed` etait CALCULE trois lignes plus haut et jamais consulte :
            # le diff vide et le diff plein donnaient le meme verdict. La chaine
            # au-dessus partait sur une fausse bonne nouvelle — l'agent croyait
            # son site fait, le decouvrait absent, se rabattait sur `create_html`
            # et enchainait deux scripts PowerShell de reparation. Les ~4 minutes
            # perdues de ce run viennent toutes de la.
            #
            # POURQUOI LA ROUTE API N'A PAS CE TROU : le CodeAgent classique
            # ecrit outil par outil (`write_file` → registre → ledger), donc
            # « rien ecrit » y est une ABSENCE visible. Ici on travaille en boite
            # noire puis diff : « rien ecrit » devient un RESULTAT qu'il faut
            # regarder. Le principe reste bon, il lui manquait ce regard.
            if not changed:
                _cmd_failures = [
                    f"exit={item.get('exitCode')}"
                    for item in items
                    if isinstance(item, Mapping)
                    and item.get("type") == "commandExecution"
                    and (
                        item.get("status") == "failed"
                        or (item.get("exitCode") not in (0, None))
                    )
                ]
                _detail = (
                    f" ({len(_cmd_failures)} commande(s) en echec : "
                    + ", ".join(_cmd_failures[:4]) + ")"
                    if _cmd_failures else ""
                )
                logger.warning(
                    "[Z35] CodeAgent Codex termine SANS aucun fichier ecrit "
                    "(task={}, {} commande(s) executee(s)){}",
                    task_id,
                    sum(
                        1 for i in items
                        if isinstance(i, Mapping) and i.get("type") == "commandExecution"
                    ),
                    _detail,
                )
                return CodexCodeAgentResult(
                    task_id=task_id,
                    success=False,
                    output=(
                        "Le CodeAgent Codex s'est termine sans ecrire AUCUN "
                        "fichier dans le workspace" + _detail + ". Le travail "
                        "n'a pas ete fait : reprends la tache autrement (ecris "
                        "les fichiers toi-meme avec les outils Lumena) plutot "
                        "que de considerer ce projet comme cree."
                    ),
                    status_code="no_change",
                    meta={
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "changed_files": [],
                        "command_failures": _cmd_failures,
                    },
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            syntax_errors: list[str] = []
            for rel in changed:
                candidate = execution_root / rel
                if candidate.is_file():
                    error = await check_syntax(candidate)
                    if error:
                        syntax_errors.append(f"{rel}: {error}")
            green_test = any(_is_green_test_item(item) for item in items)
            tests_expected = _tests_exist(execution_root)
            if syntax_errors or (tests_expected and not green_test):
                reason = "; ".join(syntax_errors)
                if tests_expected and not green_test:
                    reason = (reason + "; " if reason else "") + (
                        "tests presents mais aucune execution verte prouvee"
                    )
                return CodexCodeAgentResult(
                    task_id=task_id,
                    success=False,
                    output=f"Validation CodeAgent refusee: {reason}",
                    status_code="validation_failed",
                    meta={
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "changed_files": changed,
                        "green_test": green_test,
                    },
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            if owned is not None:
                for rel in changed:
                    src = execution_root / rel
                    dst = workspace / rel
                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                    elif dst.exists():
                        dst.unlink()
            artifacts = [str((workspace / rel).resolve()) for rel in changed]
            return CodexCodeAgentResult(
                task_id=task_id,
                success=True,
                output=final_text or "Codex a termine la tache et les validations.",
                meta={
                    "iterations": len(items),
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "model": model or "server-default",
                    "green_test": green_test,
                    "engine": "codex_subscription",
                },
                artifacts=artifacts,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return CodexCodeAgentResult(
                task_id=task_id,
                success=False,
                output=f"CodeAgent Codex interrompu apres {timeout_s:.0f}s.",
                status_code="timeout",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except CodexAppServerError as exc:
            return CodexCodeAgentResult(
                task_id=task_id,
                success=False,
                output=f"Echec Codex App Server: {exc}",
                status_code="codex_error",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()
