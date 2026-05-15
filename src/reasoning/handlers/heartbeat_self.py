"""
heartbeat_self.py - Handler d'auto-modification du heartbeat par Lumena.

Permet à Lumena de gérer ses propres tâches watchdog et son planning
de manière autonome, sans intervention humaine.

Pattern inspiré de ToolSelf (arXiv:2602.07883, fév 2026) :
  la reconfiguration = un outil natif dans l'espace d'action.

Handler: heartbeat_manage(action, task, schedule, reason)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers ───────────────────────────────────────────────────────────────

def _log_change(workspace_dir: Path, action: str, detail: str, reason: str) -> None:
    """Journalise toute modification du heartbeat pour traçabilité."""
    from ...utils.paths import OPS_DIR
    log_dir = OPS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "heartbeat_changes.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {action.upper()} | {detail} | raison: {reason}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.debug(f"💓 Impossible d'écrire heartbeat_changes.log: {e}")


# ─── Handler ───────────────────────────────────────────────────────────────

async def heartbeat_manage_handler(
    ctx: HandlerContext,
    action: str,
    task: str = "",
    schedule: str = "",
    reason: str = "",
) -> HandlerResult:
    """
    Permet à Lumena de gérer son propre heartbeat autonomiquement.

    actions:
      list         → affiche les tâches actuelles et le planning
      add          → ajoute une tâche watchdog (task requis)
      remove       → supprime une tâche watchdog (task requis)
      set_schedule → modifie les heures du heartbeat (schedule requis, ex: "6,18")
    """
    try:
        from ...autonomy.heartbeat import get_heartbeat
        hb = get_heartbeat(workspace_dir=ctx.lumena_root)

        action = action.strip().lower()

        # ── LIST ──────────────────────────────────────────────────────────
        if action == "list":
            tasks = hb.config.tasks
            if not tasks:
                tasks_txt = "  (aucune tâche configurée)"
            else:
                tasks_txt = "\n".join(
                    f"  {'✅' if t.enabled else '⏸️'} {t.description}"
                    for t in tasks
                )

            if hb.config.scheduled_hours:
                hours_str = ", ".join(f"{h:02d}h00" for h in sorted(hb.config.scheduled_hours))
                sched_txt = f"planifié: {hours_str} ({len(hb.config.scheduled_hours)}×/jour)"
            else:
                sched_txt = f"interval: {hb.config.interval_minutes} min"

            result = (
                f"💓 **Heartbeat actuel** ({sched_txt})\n\n"
                f"**Tâches** ({len(tasks)}):\n{tasks_txt}\n\n"
                f"Heartbeats exécutés: #{hb.heartbeat_count} | "
                f"Dernier: {hb.last_heartbeat.strftime('%d/%m %H:%M') if hb.last_heartbeat else 'jamais'}"
            )
            return HandlerResult.ok(result, handler_name="heartbeat_manage")

        # ── ADD ───────────────────────────────────────────────────────────
        elif action == "add":
            if not task.strip():
                return HandlerResult.fail(
                    "❌ heartbeat_manage(add) : paramètre 'task' obligatoire",
                    handler_name="heartbeat_manage",
                )
            task = task.strip()
            # Éviter les doublons
            existing = [t.description.lower() for t in hb.config.tasks]
            if task.lower() in existing:
                return HandlerResult.ok(
                    f"⚠️ Tâche déjà présente : « {task} »",
                    handler_name="heartbeat_manage",
                )
            success = hb.add_task(task)
            if success:
                _log_change(ctx.lumena_root, "add", task, reason or "non spécifiée")
                logger.info(f"💓 Lumena a ajouté une tâche heartbeat: {task}")
                return HandlerResult.ok(
                    f"✅ Tâche ajoutée au heartbeat : « {task} »\n"
                    f"Raison : {reason or 'non spécifiée'}\n"
                    f"Total tâches : {len(hb.config.tasks)}",
                    handler_name="heartbeat_manage",
                )
            return HandlerResult.fail("❌ Échec ajout tâche", handler_name="heartbeat_manage")

        # ── REMOVE ────────────────────────────────────────────────────────
        elif action == "remove":
            if not task.strip():
                return HandlerResult.fail(
                    "❌ heartbeat_manage(remove) : paramètre 'task' obligatoire",
                    handler_name="heartbeat_manage",
                )
            task = task.strip()
            success = hb.remove_task(task)
            if success:
                _log_change(ctx.lumena_root, "remove", task, reason or "non spécifiée")
                logger.info(f"💓 Lumena a supprimé une tâche heartbeat: {task}")
                return HandlerResult.ok(
                    f"🗑️ Tâche supprimée du heartbeat : « {task} »\n"
                    f"Raison : {reason or 'non spécifiée'}\n"
                    f"Tâches restantes : {len(hb.config.tasks)}",
                    handler_name="heartbeat_manage",
                )
            # Tentative de correspondance partielle
            matches = [t.description for t in hb.config.tasks if task.lower() in t.description.lower()]
            hint = f" Tâches proches : {matches}" if matches else ""
            return HandlerResult.fail(
                f"❌ Tâche introuvable : « {task} ».{hint}",
                handler_name="heartbeat_manage",
            )

        # ── SET_SCHEDULE ──────────────────────────────────────────────────
        elif action == "set_schedule":
            if not schedule.strip():
                return HandlerResult.fail(
                    "❌ heartbeat_manage(set_schedule) : paramètre 'schedule' obligatoire (ex: '6,18')",
                    handler_name="heartbeat_manage",
                )
            # Parser les heures
            try:
                hours: List[int] = []
                for part in schedule.replace(" ", "").split(","):
                    h = int(part)
                    if not (0 <= h <= 23):
                        raise ValueError(f"Heure invalide: {h}")
                    hours.append(h)
                if not hours:
                    raise ValueError("Aucune heure valide")
                hours = sorted(set(hours))
            except ValueError as e:
                return HandlerResult.fail(
                    f"❌ Format invalide pour schedule : {e}. Exemple valide : '6,18' ou '8,12,20'",
                    handler_name="heartbeat_manage",
                )

            old_schedule = sorted(hb.config.scheduled_hours) if hb.config.scheduled_hours else [f"interval {hb.config.interval_minutes}min"]
            hb.set_schedule(hours)
            _log_change(
                ctx.lumena_root,
                "set_schedule",
                f"{old_schedule} → {hours}",
                reason or "non spécifiée",
            )
            hours_str = ", ".join(f"{h:02d}h00" for h in hours)
            logger.info(f"💓 Lumena a modifié le schedule heartbeat: {hours}")
            return HandlerResult.ok(
                f"⏰ Schedule heartbeat mis à jour : {hours_str} ({len(hours)}×/jour)\n"
                f"Raison : {reason or 'non spécifiée'}\n"
                f"⚠️ Le nouveau schedule sera actif au prochain redémarrage du heartbeat.",
                handler_name="heartbeat_manage",
            )

        else:
            return HandlerResult.fail(
                f"❌ Action inconnue : '{action}'. "
                "Actions valides : list, add, remove, set_schedule",
                handler_name="heartbeat_manage",
            )

    except Exception as e:
        logger.error(f"💓 Erreur heartbeat_manage: {e}")
        return HandlerResult.fail(f"❌ Erreur heartbeat_manage: {e}", handler_name="heartbeat_manage")


# ─── Handler : read_logs ───────────────────────────────────────────────────

# Catalogue des sources de logs connues (chemins absolus via paths.py)
from ...utils.paths import OPS_DIR as _OPS, LOGS_DIR as _LOGS, ALERTS_DIR as _ALERTS, ROOT_DIR as _ROOT, OPS_STATE_JSON as _OPS_STATE

_LOG_SOURCES = {
    "heartbeat":  (_OPS / "heartbeat_changes.log",  "text"),
    "app":        (_LOGS,                            "dir_latest"),
    "app_hidden": (_ROOT / ".lumena" / "logs",       "dir_latest"),
    "ops":        (_OPS / "metrics.jsonl",            "jsonl"),
    "micro_eval": (_OPS / "micro_eval_log.jsonl",     "jsonl"),
    "alerts":     (_ALERTS,                           "dir_all"),
    "ops_state":  (_OPS_STATE,                        "json"),
}


async def read_logs_handler(
    ctx: HandlerContext,
    source: str = "heartbeat",
    lines: int = 50,
) -> HandlerResult:
    """
    Lit les logs de Lumena.

    sources disponibles :
      heartbeat  → historique des auto-modifications du heartbeat
      app        → dernier log applicatif (data/logs/lumena_YYYY-MM-DD.log)
      app_hidden → dernier log dans .lumena/logs/
      ops        → métriques ops (metrics.jsonl)
      micro_eval → évaluations micro (micro_eval_log.jsonl)
      alerts     → tous les fichiers d'alertes (data/alerts/)
      ops_state  → état ops courant (ops_state.json)
    """
    try:
        root = ctx.lumena_root
        source = source.strip().lower()

        if source not in _LOG_SOURCES:
            available = ", ".join(_LOG_SOURCES.keys())
            return HandlerResult.fail(
                f"❌ Source inconnue : '{source}'. Disponibles : {available}",
                handler_name="read_logs",
            )

        target, kind = _LOG_SOURCES[source]

        # ── Fichier texte simple ──────────────────────────────────────────
        if kind == "text":
            if not target.exists():
                return HandlerResult.ok(
                    f"📋 **{source}** : aucun log pour l'instant (fichier non créé).",
                    handler_name="read_logs",
                )
            content = target.read_text(encoding="utf-8")
            tail = "\n".join(content.splitlines()[-lines:]) if content.strip() else "(vide)"
            return HandlerResult.ok(
                f"📋 **{source}** (dernières {lines} lignes — {target.name}):\n\n```\n{tail}\n```",
                handler_name="read_logs",
            )

        # ── Dernier fichier d'un dossier ──────────────────────────────────
        elif kind in ("dir_latest", "dir_all"):
            if not target.exists():
                return HandlerResult.ok(
                    f"📋 **{source}** : dossier introuvable ({target}).",
                    handler_name="read_logs",
                )
            files = sorted(target.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
            log_files = [f for f in files if f.is_file()]
            if not log_files:
                return HandlerResult.ok(
                    f"📋 **{source}** : dossier vide.",
                    handler_name="read_logs",
                )
            if kind == "dir_latest":
                chosen = [log_files[0]]
            else:
                chosen = log_files[:5]  # max 5 fichiers d'alertes

            parts = []
            for f in chosen:
                raw = f.read_text(encoding="utf-8", errors="replace")
                tail = "\n".join(raw.splitlines()[-lines:]) if raw.strip() else "(vide)"
                parts.append(f"**{f.name}** :\n```\n{tail}\n```")
            return HandlerResult.ok(
                f"📋 **{source}** :\n\n" + "\n\n".join(parts),
                handler_name="read_logs",
            )

        # ── JSONL : dernières N lignes ────────────────────────────────────
        elif kind == "jsonl":
            if not target.exists():
                return HandlerResult.ok(
                    f"📋 **{source}** : fichier non créé.",
                    handler_name="read_logs",
                )
            raw_lines = target.read_text(encoding="utf-8").splitlines()
            tail_lines = raw_lines[-lines:]
            entries = []
            for ln in tail_lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    import json as _json
                    obj = _json.loads(ln)
                    entries.append(_json.dumps(obj, ensure_ascii=False))
                except Exception:
                    entries.append(ln)
            text = "\n".join(entries) if entries else "(vide)"
            return HandlerResult.ok(
                f"📋 **{source}** (derniers {lines} enregistrements):\n\n```json\n{text}\n```",
                handler_name="read_logs",
            )

        # ── JSON simple ───────────────────────────────────────────────────
        elif kind == "json":
            if not target.exists():
                return HandlerResult.ok(
                    f"📋 **{source}** : fichier non créé.",
                    handler_name="read_logs",
                )
            import json as _json
            content = _json.loads(target.read_text(encoding="utf-8"))
            text = _json.dumps(content, indent=2, ensure_ascii=False)
            if len(text) > 4000:
                text = text[:4000] + "\n… (tronqué)"
            return HandlerResult.ok(
                f"📋 **{source}**:\n\n```json\n{text}\n```",
                handler_name="read_logs",
            )

        return HandlerResult.fail("❌ Type de log non géré", handler_name="read_logs")

    except Exception as e:
        logger.error(f"read_logs error: {e}")
        return HandlerResult.fail(f"❌ Erreur read_logs: {e}", handler_name="read_logs")


# ─── Registration ──────────────────────────────────────────────────────────

def get_heartbeat_self_handler_defs() -> list:
    """Retourne la définition du handler heartbeat_manage pour le registre V2."""
    return [
        HandlerDef(
            name="heartbeat_manage",
            description=(
                "Gère le heartbeat autonome de Lumena : liste les tâches watchdog, "
                "en ajoute ou supprime, et modifie le planning (heures d'exécution). "
                "N'utiliser que si une observation factuellement justifie le changement."
            ),
            parameters={
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "remove", "set_schedule"],
                        "description": (
                            "list=afficher les tâches et schedule actuels ; "
                            "add=ajouter une tâche watchdog ; "
                            "remove=supprimer une tâche watchdog ; "
                            "set_schedule=modifier les heures d'exécution"
                        ),
                    },
                    "task": {
                        "type": "string",
                        "description": "Description de la tâche (requis pour add/remove)",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "Heures séparées par virgule pour set_schedule (ex: '6,18' ou '8,20'). "
                            "Format 24h, entre 0 et 23."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Justification factuelle et observable du changement. "
                            "Obligatoire moralement : toujours expliquer pourquoi."
                        ),
                    },
                },
                "required": ["action"],
            },
            handler=heartbeat_manage_handler,
            category="autonomy",
            source_module="handlers.heartbeat_self",
        ),
        HandlerDef(
            name="read_logs",
            description=(
                "Lit les logs et fichiers de diagnostic de Lumena. "
                "Sources : heartbeat (historique auto-modifications), app (log applicatif du jour), "
                "app_hidden (.lumena/logs/), ops (métriques), micro_eval, alerts, ops_state."
            ),
            parameters={
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["heartbeat", "app", "app_hidden", "ops", "micro_eval", "alerts", "ops_state"],
                        "description": "Source de logs à lire (défaut: heartbeat)",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Nombre de lignes/entrées à retourner (défaut: 50, max recommandé: 100)",
                    },
                },
                "required": [],
            },
            handler=read_logs_handler,
            category="autonomy",
            source_module="handlers.heartbeat_self",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
