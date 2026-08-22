"""Background lifecycle adapter for ChatGPT Codex CodeAgent runs.

This module deliberately reuses the historical ``ca_*`` registry so
``bg_status``, ``bg_list`` and ``bg_cancel`` keep the same public contract.
It does not select a model or an API provider; the supplied runner is already
bound to the connected Codex subscription.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from loguru import logger

from .sub_agent import (
    _register_bg_agent,
    _unregister_bg_agent,
    get_orchestrator,
)
from ..llm.codex_codeagent import CodexCodeAgentResult


CodexBackgroundRunner = Callable[[], Awaitable[CodexCodeAgentResult]]


async def start_codex_codeagent_bg(
    description: str,
    *,
    agent_type: str,
    context: Mapping[str, Any] | None,
    runner: CodexBackgroundRunner,
    progress_callback: Callable[[str], Any] | None = None,
) -> str:
    """Start a Codex subscription run using the existing agent BG contract."""

    orchestrator = get_orchestrator()
    orchestrator.task_counter += 1
    task_id = f"ca_{orchestrator.task_counter}_{datetime.now().strftime('%H%M%S')}"
    orchestrator.pending_tasks[task_id] = {
        "task_id": task_id,
        "description": description,
        "agent_type": agent_type,
        "context": dict(context or {}),
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "engine": "codex_subscription",
    }
    orchestrator._save_to_disk()

    async def _run_and_store() -> None:
        try:
            result = await runner()
            meta = dict(result.meta or {})
            meta.setdefault("engine", "codex_subscription")
            model = str(meta.get("model", "") or "")
            orchestrator.pending_tasks[task_id].update(
                {
                    "status": "done" if result.success else "failed",
                    "status_code": result.status_code,
                    "output": str(result.output or "")[:2000],
                    "success": bool(result.success),
                    "meta": meta,
                    "engine": "codex_subscription",
                    "model": model,
                    "artifacts": list(result.artifacts or []),
                    "duration_ms": int(result.duration_ms or 0),
                    "finished_at": datetime.now().isoformat(),
                }
            )
            orchestrator._save_to_disk()
            if progress_callback:
                try:
                    message = (
                        f"✅ **Tâche terminée** (`{task_id}`)\n"
                        f"{str(result.output or '')[:300]}"
                        if result.success
                        else (
                            f"❌ **Tâche échouée** (`{task_id}`)\n"
                            f"{str(result.output or '')[:300]}"
                        )
                    )
                    callback_result = progress_callback(message)
                    if asyncio.iscoroutine(callback_result):
                        await callback_result
                except Exception:
                    logger.debug(
                        "[CodeAgent/Codex] callback bg ignoré pour {}",
                        task_id,
                    )
        except asyncio.CancelledError:
            orchestrator.pending_tasks[task_id].update(
                {
                    "status": "cancelled",
                    "success": False,
                    "finished_at": datetime.now().isoformat(),
                }
            )
            orchestrator._save_to_disk()
            logger.info("[CodeAgent/Codex] bg task {} cancelled", task_id)
            raise
        except Exception as exc:
            logger.error("[CodeAgent/Codex] bg task {} failed: {}", task_id, exc)
            orchestrator.pending_tasks[task_id].update(
                {
                    "status": "failed",
                    "status_code": "codex_background_error",
                    "output": f"Erreur CodeAgent Codex: {exc}",
                    "success": False,
                    "finished_at": datetime.now().isoformat(),
                }
            )
            orchestrator._save_to_disk()
            if progress_callback:
                try:
                    callback_result = progress_callback(
                        f"❌ **Erreur CodeAgent Codex** (`{task_id}`): {exc}"
                    )
                    if asyncio.iscoroutine(callback_result):
                        await callback_result
                except Exception:
                    logger.debug(
                        "[CodeAgent/Codex] callback d'erreur bg ignoré pour {}",
                        task_id,
                    )
        finally:
            _unregister_bg_agent(task_id)

    background_task = asyncio.create_task(_run_and_store())
    _register_bg_agent(task_id, background_task)
    logger.info(
        "[CodeAgent/Codex] tâche bg lancée: {} ({})",
        task_id,
        agent_type,
    )
    # Laisse la coroutine entrer dans son bloc try avant de rendre l'ID. Une
    # annulation immédiate via bg_cancel passe ainsi toujours par le cleanup et
    # persiste le statut ``cancelled`` au lieu de laisser un faux ``running``.
    await asyncio.sleep(0)
    return task_id
