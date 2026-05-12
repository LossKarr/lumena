"""
autonomy.py - Handlers V2 pour la planification de tâches.

Expose schedule_task, list_tasks, cancel_task, task_history, remind,
autonomy_activity_summary, autonomy_next_best_action
au ReAct loop via le registre V2, en wrappant les handlers existants
dans src/tools/task_scheduler.py.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, List, Optional

from loguru import logger

from ...utils.paths import JOURNAL_JSON, OPS_DIR, OPS_STATE_JSON
from ...autonomy.activity_ledger import read_autonomy_events
from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


_ACTIVITY_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
_ACTIVITY_CACHE_TTL_SECONDS = 30
_MAX_ACTIVITY_READ_BYTES = 2_000_000


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_task_handlers():
    """Import lazy pour éviter les dépendances circulaires."""
    from ...tools.task_scheduler import (
        handle_schedule_task,
        handle_list_tasks,
        handle_cancel_task,
        handle_task_history,
        handle_schedule_remind,
    )
    return handle_schedule_task, handle_list_tasks, handle_cancel_task, handle_task_history, handle_schedule_remind


def _get_task_modify_handlers():
    """Import lazy pour modify_task et delete_task."""
    from ...tools.task_scheduler import handle_modify_task, handle_delete_task
    return handle_modify_task, handle_delete_task


def _read_recent_jsonl(path, *, max_bytes: int = _MAX_ACTIVITY_READ_BYTES) -> list[dict[str, Any]]:
    """Read recent JSONL entries without scanning large files in the hot path."""
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
                f.readline()  # discard partial line
            raw = f.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("autonomy_activity_summary: read jsonl failed for {}: {}", path, exc)
        return []

    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                entries.append(item)
        except json.JSONDecodeError:
            continue
    return entries


def _load_json_list(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("autonomy_activity_summary: read json failed for {}: {}", path, exc)
        return []
    return data if isinstance(data, list) else []


def _load_json_dict(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("autonomy_activity_summary: read json failed for {}: {}", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _time_part(ts: str) -> str:
    if not ts:
        return "??:??"
    if "T" in ts:
        return ts.split("T", 1)[1][:8]
    return ts[:19]


def _compact_reason(data: dict[str, Any]) -> str:
    for key in ("reason", "summary", "status"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:220]
    alerts = data.get("alerts")
    if isinstance(alerts, list) and alerts:
        return "; ".join(str(a) for a in alerts[:3])[:220]
    if "score_percent" in data:
        return f"score={data.get('score_percent')}%"
    if "dedup_count" in data:
        return f"dedup={data.get('dedup_count')}, purge={data.get('purge_count')}"
    if "new_items" in data:
        return f"new_items={data.get('new_items')}, duplicates={data.get('duplicates')}"
    if "candidates_found" in data:
        return f"candidates={data.get('candidates_found')}, rejected={data.get('rejected')}"
    return "ok" if data.get("success", True) else "failed"


def _build_autonomy_activity_summary(date: str, limit: int) -> str:
    metrics = [
        e for e in _read_recent_jsonl(OPS_DIR / "metrics.jsonl")
        if str(e.get("timestamp", "")).startswith(date)
    ]
    daemon_entries = [
        e for e in _load_json_list(JOURNAL_JSON)
        if str(e.get("timestamp", "")).startswith(date)
        and str(e.get("type", "")).lower() == "action"
    ]
    decision_events = read_autonomy_events(date=date, limit=max(50, limit * 4))
    ops_state = _load_json_dict(OPS_STATE_JSON)

    latest_by_handler: dict[str, dict[str, Any]] = {}
    counts_by_handler: dict[str, int] = {}
    failures = 0
    alerts: list[str] = []
    for entry in metrics:
        handler = str(entry.get("handler") or entry.get("name") or "").strip()
        if not handler:
            continue
        counts_by_handler[handler] = counts_by_handler.get(handler, 0) + 1
        latest_by_handler[handler] = entry
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        if not data.get("success", True):
            failures += 1
        for alert in data.get("alerts") or []:
            alert_text = str(alert).strip()
            if alert_text and alert_text not in alerts:
                alerts.append(alert_text)

    lines = [f"Rapport factuel autonomie - {date}"]
    lines.append("Sources: data/ops/metrics.jsonl, data/ops/ops_state.json, data/journal.json")
    lines.append("")

    if metrics:
        lines.append(f"Taches scheduler/ops: {len(metrics)} evenement(s), {len(latest_by_handler)} handler(s), {failures} echec(s).")
        recent_metrics = sorted(metrics, key=lambda e: str(e.get("timestamp", "")), reverse=True)[:limit]
        for entry in recent_metrics:
            handler = str(entry.get("handler") or entry.get("name") or "?")
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            status = "OK" if data.get("success", True) else "PROBLEME"
            lines.append(f"- [{_time_part(str(entry.get('timestamp', '')))}] {handler}: {status} - {_compact_reason(data)}")
    else:
        lines.append("Taches scheduler/ops: aucune entree trouvee pour cette date.")

    lines.append("")
    if daemon_entries:
        lines.append(f"Actions autonomes daemon/reflection: {len(daemon_entries)} entree(s).")
        for entry in daemon_entries[-limit:]:
            content = str(entry.get("content") or entry.get("summary") or "").replace("\n", " ").strip()
            lines.append(f"- [{_time_part(str(entry.get('timestamp', '')))}] {content[:260]}")
    else:
        lines.append("Actions autonomes daemon/reflection: aucune entree trouvee pour cette date.")

    if decision_events:
        lines.append("")
        lines.append(f"Decisions autonomes tracees: {len(decision_events)} evenement(s).")
        for entry in decision_events[-limit:]:
            event_type = str(entry.get("event_type") or "?")
            action_type = str(entry.get("action_type") or "?")
            decision = str(entry.get("decision") or "")
            reason = str(entry.get("reason") or "").strip()
            suffix = f" - {reason[:180]}" if reason else ""
            lines.append(
                f"- [{_time_part(str(entry.get('timestamp', '')))}] "
                f"{event_type}/{action_type}: {decision or 'recorded'}{suffix}"
            )
    else:
        lines.append("")
        lines.append("Decisions autonomes tracees: aucune decision candidate/bloquee/executed dans le ledger.")

    incidents = ops_state.get("incidents_today") if isinstance(ops_state, dict) else []
    if alerts or incidents:
        lines.append("")
        lines.append("Alertes/problemes detectes:")
        for alert in alerts[:8]:
            lines.append(f"- {alert}")
        if isinstance(incidents, list):
            for incident in incidents[:5]:
                lines.append(f"- incident: {str(incident)[:220]}")

    if not metrics and not daemon_entries and not decision_events:
        lines.append("")
        lines.append("Conclusion: aucune preuve factuelle d'activite autonome pour cette date dans les journaux structures.")
    else:
        lines.append("")
        lines.append(
            "Conclusion: ne dis pas 'je n'ai rien fait' si des routines scheduler/ops existent. "
            "Dis plutot: 'aucune initiative spontanee prouvee' ou 'initiative bloquee', "
            "puis distingue routines, initiatives daemon, demandes utilisateur, alertes et blocages."
        )

    return "\n".join(lines)


def _has_disk_pressure(text: str) -> bool:
    low = text.lower()
    return "disque critique" in low or "disk_guard" in low or "free disk" in low


def _trusted_peer_count() -> int:
    path = OPS_DIR.parent / "peer_registry.json"
    data = _load_json_dict(path)
    peers = data.get("peers") if isinstance(data, dict) else None
    if isinstance(peers, dict):
        values = peers.values()
    elif isinstance(data, dict):
        values = data.values()
    else:
        values = []
    count = 0
    for peer in values:
        if isinstance(peer, dict) and str(peer.get("trust", "")).strip() == "trusted":
            count += 1
    return count


def _build_autonomy_next_best_action(date: str) -> str:
    metrics = [
        e for e in _read_recent_jsonl(OPS_DIR / "metrics.jsonl")
        if str(e.get("timestamp", "")).startswith(date)
    ]
    decision_events = read_autonomy_events(date=date, limit=100)
    ops_state = _load_json_dict(OPS_STATE_JSON)

    alerts: list[str] = []
    failures: list[str] = []
    for entry in metrics:
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        handler = str(entry.get("handler") or entry.get("name") or "?")
        if not data.get("success", True):
            failures.append(f"{handler}: {_compact_reason(data)}")
        for alert in data.get("alerts") or []:
            alert_text = str(alert).strip()
            if alert_text and alert_text not in alerts:
                alerts.append(alert_text)

    blocked = [e for e in decision_events if str(e.get("event_type")) == "action_blocked"]
    completed = [e for e in decision_events if str(e.get("event_type")) == "action_completed"]
    disk_pressure = any(_has_disk_pressure(a) for a in alerts)
    disk_pressure = disk_pressure or any(_has_disk_pressure(str(e.get("reason", ""))) for e in blocked)

    incidents = ops_state.get("incidents_today") if isinstance(ops_state, dict) else []
    if isinstance(incidents, list):
        for incident in incidents:
            if _has_disk_pressure(str(incident)):
                disk_pressure = True

    peer_count = _trusted_peer_count()

    if disk_pressure:
        priority = "critical"
        action = "stabilize_disk_and_report"
        reason = "Disk pressure detected in runtime health or autonomy ledger."
        safe = "yes"
        confirm = "no for diagnostic/report, yes before destructive cleanup"
    elif failures:
        priority = "warning"
        action = "investigate_failed_ops"
        reason = failures[-1][:220]
        safe = "yes"
        confirm = "no"
    elif blocked:
        priority = "normal"
        action = "explain_blocked_autonomy_then_choose_safe_small_action"
        reason = str(blocked[-1].get("reason", "blocked autonomy action"))[:220]
        safe = "yes"
        confirm = "no"
    elif not completed:
        priority = "normal"
        action = "run_low_risk_learning_or_reflection"
        reason = "No spontaneous completed initiative recorded today."
        safe = "yes"
        confirm = "no"
    elif peer_count > 0:
        priority = "normal"
        action = "use_trusted_peer_for_parallel_research_when_relevant"
        reason = f"{peer_count} trusted peer(s) available."
        safe = "yes"
        confirm = "no"
    else:
        priority = "low"
        action = "continue_monitoring"
        reason = "No urgent issue detected."
        safe = "yes"
        confirm = "no"

    lines = [
        f"Next best autonomous action - {date}",
        f"Priority: {priority}",
        f"Action: {action}",
        f"Reason: {reason}",
        f"Safe to execute: {safe}",
        f"Requires user confirmation: {confirm}",
        f"Trusted peers available: {peer_count}",
        "",
        "Instruction: answer from this recommendation. If priority is critical, do not launch heavy autonomous work before stabilization.",
    ]
    return "\n".join(lines)


# ─── Handlers ──────────────────────────────────────────────────────────────

async def schedule_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        fn, _, _, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="schedule_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur schedule_task: {e}", handler_name="schedule_task")


async def list_tasks_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, fn, _, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="list_tasks")
    except Exception as e:
        return HandlerResult.fail(f"Erreur list_tasks: {e}", handler_name="list_tasks")


async def cancel_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, fn, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="cancel_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur cancel_task: {e}", handler_name="cancel_task")


async def task_history_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, _, fn, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="task_history")
    except Exception as e:
        return HandlerResult.fail(f"Erreur task_history: {e}", handler_name="task_history")


async def autonomy_activity_summary_handler(
    ctx: HandlerContext,
    date: str = "",
    limit: int = 12,
) -> HandlerResult:
    """Summarize factual autonomy activity from existing structured logs only."""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        date = str(date).strip()[:10]
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return HandlerResult.fail(
                "Erreur autonomy_activity_summary: date attendue au format YYYY-MM-DD.",
                handler_name="autonomy_activity_summary",
            )

        try:
            limit = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            limit = 12

        cache_key = (date, limit)
        now = time.monotonic()
        cached = _ACTIVITY_CACHE.get(cache_key)
        if cached and now - cached[0] < _ACTIVITY_CACHE_TTL_SECONDS:
            return HandlerResult.ok(cached[1], handler_name="autonomy_activity_summary")

        output = _build_autonomy_activity_summary(date, limit)
        _ACTIVITY_CACHE[cache_key] = (now, output)
        return HandlerResult.ok(output, handler_name="autonomy_activity_summary")
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur autonomy_activity_summary: {e}",
            handler_name="autonomy_activity_summary",
        )


async def autonomy_next_best_action_handler(
    ctx: HandlerContext,
    date: str = "",
) -> HandlerResult:
    """Recommend the safest next autonomous action from structured state."""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        date = str(date).strip()[:10]
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return HandlerResult.fail(
                "Erreur autonomy_next_best_action: date attendue au format YYYY-MM-DD.",
                handler_name="autonomy_next_best_action",
            )
        return HandlerResult.ok(
            _build_autonomy_next_best_action(date),
            handler_name="autonomy_next_best_action",
        )
    except Exception as e:
        logger.warning("autonomy_next_best_action failed: {}", e)
        return HandlerResult.fail(
            f"Erreur autonomy_next_best_action: {e}",
            handler_name="autonomy_next_best_action",
        )


async def remind_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, _, _, fn = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="remind")
    except Exception as e:
        return HandlerResult.fail(f"Erreur remind: {e}", handler_name="remind")


async def modify_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Modifie une tâche planifiée existante (action, nom, horaire)."""
    try:
        fn, _ = _get_task_modify_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="modify_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur modify_task: {e}", handler_name="modify_task")


async def delete_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Supprime définitivement une tâche planifiée du scheduler."""
    try:
        _, fn = _get_task_modify_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="delete_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur delete_task: {e}", handler_name="delete_task")


# ─── Registry ──────────────────────────────────────────────────────────────

def get_autonomy_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers autonomy pour le registre V2."""
    return [
        HandlerDef(
            name="schedule_task",
            description=(
                "⚡⚡ PREMIER OUTIL À APPELER quand l'utilisateur veut enregistrer une tâche récurrente "
                "ou un rappel. Mots-clés déclencheurs OBLIGATOIRES : 'enregistre une tâche', "
                "'rappelle-moi', 'tous les jours à X', 'chaque matin', 'toutes les heures', "
                "'planifie', 'programme', 'tu vas devoir faire X à Xh', 'tâche automatique'. "
                "Ne jamais utiliser plan_create, create_skill ou un fichier Python à la place — "
                "seul schedule_task crée une vraie tâche dans le scheduler. "
                "Le paramètre 'action' est un PROMPT textuel que Lumena exécutera à chaque déclenchement. "
                "Exemples : cron='0 10 * * *' pour tous les jours à 10h, interval=60 pour toutes les heures."
            ),
            parameters={
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Prompt/instruction que Lumena exécutera au déclenchement (peut utiliser mail_send, web_search, memory_add, etc.)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Nom lisible de la tâche",
                        "default": "",
                    },
                    "delay": {
                        "type": "string",
                        "description": "Délai avant exécution unique (ex: '2h', '30min', '1 jour')",
                        "default": "",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "Date/heure précise (ex: '08:00', 'demain à 9h', '2026-03-10 18:00')",
                        "default": "",
                    },
                    "cron": {
                        "type": "string",
                        "description": "Expression CRON (ex: '0 8 * * *' = tous les jours à 8h, '0 12 * * 1-5' = lundi-vendredi à midi)",
                        "default": "",
                    },
                    "interval": {
                        "type": "integer",
                        "description": "Intervalle récurrent en minutes (ex: 30 pour toutes les 30min)",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID canal (Telegram chat_id ou WhatsApp phone) où renvoyer le résultat (auto-détecté si possible)",
                        "default": "",
                    },
                },
                "required": ["action"],
            },
            handler=schedule_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="list_tasks",
            description=(
                "📋 OUTIL OBLIGATOIRE quand l'utilisateur demande 'as-tu des tâches?', "
                "'qu'est-ce qui est planifié?', 'montre tes tâches', 'tu as des rappels?'. "
                "NE PAS lire des fichiers Markdown — ils ne reflètent pas l'état réel du scheduler. "
                "Retourne l'état live de toutes les tâches CRON système et conversationnelles."
            ),
            parameters={
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Filtrer : all | conv (conversationnelles) | system",
                        "default": "all",
                    },
                },
                "required": [],
            },
            handler=list_tasks_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="list_scheduled_tasks",
            description="Alias de list_tasks — liste toutes les tâches planifiées actives.",
            parameters={
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Filtrer : all | conv | system",
                        "default": "all",
                    },
                },
                "required": [],
            },
            handler=list_tasks_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="cancel_task",
            description="Annule une tâche planifiée par son ID (visible dans list_tasks).",
            parameters={
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID de la tâche à annuler",
                    },
                },
                "required": ["task_id"],
            },
            handler=cancel_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="task_history",
            description="Affiche l'historique d'exécution des tâches planifiées (dernière run, statut, durée, erreurs).",
            parameters={
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Filtrer par ID ou nom de tâche (optionnel)",
                        "default": "",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max d'entrées à afficher",
                        "default": 20,
                    },
                },
                "required": [],
            },
            handler=task_history_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="autonomy_activity_summary",
            description=(
                "OUTIL FACTUEL OBLIGATOIRE quand l'utilisateur demande ce que Lumena a fait "
                "en autonomie, aujourd'hui, depuis minuit, ou via le daemon/scheduler. "
                "Lit uniquement les journaux structures existants (metrics.jsonl, ops_state.json, journal.json) "
                "et ne scanne pas ChromaDB ni le repo."
            ),
            parameters={
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date YYYY-MM-DD a analyser (defaut: aujourd'hui).",
                        "default": "",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max d'evenements recents a afficher (1-50, defaut: 12).",
                        "default": 12,
                    },
                },
                "required": [],
            },
            handler=autonomy_activity_summary_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="autonomy_next_best_action",
            description=(
                "OUTIL FACTUEL pour choisir la prochaine action autonome la plus sure. "
                "A utiliser quand l'utilisateur demande quoi faire, pourquoi Lumena n'a pas agi, "
                "ou comment devenir plus autonome. Lit metrics.jsonl, ops_state.json, ledger autonomie et pairs trusted."
            ),
            parameters={
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date YYYY-MM-DD a analyser (defaut: aujourd'hui).",
                        "default": "",
                    },
                },
                "required": [],
            },
            handler=autonomy_next_best_action_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="remind",
            description="Crée un rappel simple — Lumena contacte l'utilisateur sur Telegram ou WhatsApp au moment spécifié.",
            parameters={
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message du rappel",
                    },
                    "delay": {
                        "type": "string",
                        "description": "Dans combien de temps (ex: '30min', '2h', '1 jour')",
                        "default": "",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "Heure précise (ex: '08:00', 'demain à 9h')",
                        "default": "",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID canal Telegram/WhatsApp (optionnel)",
                        "default": "",
                    },
                },
                "required": ["message"],
            },
            handler=remind_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="modify_task",
            description="Modifie une tâche planifiée existante (change l'action, le nom, l'horaire ou l'intervalle).",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche à modifier"},
                    "action": {"type": "string", "description": "Nouvelle instruction/prompt à exécuter", "default": ""},
                    "name": {"type": "string", "description": "Nouveau nom de la tâche", "default": ""},
                    "cron": {"type": "string", "description": "Nouvelle expression CRON", "default": ""},
                    "interval": {"type": "integer", "description": "Nouvel intervalle en minutes"},
                    "run_at": {"type": "string", "description": "Nouvelle date/heure précise", "default": ""},
                },
                "required": ["task_id"],
            },
            handler=modify_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="delete_task",
            description="Supprime définitivement une tâche planifiée du scheduler (irréversible).",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche à supprimer"},
                },
                "required": ["task_id"],
            },
            handler=delete_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
