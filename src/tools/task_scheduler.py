"""
Lumena — Task Scheduler Tools
==============================
Interface tool pour permettre à Lumena de programmer des tâches depuis une conversation.

Supporte :
- Tâche unique différée : "dans 2h envoie-moi un rapport"
- Tâche récurrente : "tous les matins à 8h"
- Expression CRON complète : "0 8 * * 1-5" (lundi-vendredi 8h)
- Intervalles : "toutes les 30 minutes"
- Annulation, liste, historique

Le handler de chaque tâche créée par conversation reçoit :
  - le prompt/action à exécuter (ex: "génère un rapport crypto")
  - le chat_id Telegram pour répondre automatiquement
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from ..utils.persistence import atomic_write_json, safe_read_json

from src.utils.paths import SCHEDULER_DIR
_CONV_TASKS_PATH = SCHEDULER_DIR / "conversation_tasks.json"

# ── Callbacks injectés au démarrage de l'app ──
_telegram_send_fn: Optional[Callable] = None   # async (chat_id, text) -> None
_whatsapp_send_fn: Optional[Callable] = None   # async (phone, text) -> bool
_lumena_think_fn: Optional[Callable] = None    # async (prompt, chat_id) -> str


def bind_scheduler_callbacks(
    telegram_send: Optional[Callable] = None,
    whatsapp_send: Optional[Callable] = None,
    lumena_think: Optional[Callable] = None,
) -> None:
    """
    Appelé par app.py ou run_telegram.py pour injecter les callbacks.
    - telegram_send(chat_id, text) : envoie un message Telegram
    - whatsapp_send(phone, text) : envoie un message WhatsApp
    - lumena_think(prompt, chat_id) : fait réfléchir Lumena et envoie la réponse
    """
    global _telegram_send_fn, _whatsapp_send_fn, _lumena_think_fn
    if telegram_send is not None:
        _telegram_send_fn = telegram_send
    if whatsapp_send is not None:
        _whatsapp_send_fn = whatsapp_send
    if lumena_think is not None:
        _lumena_think_fn = lumena_think


# ─────────────────────────────────────────────────────────────
# PERSISTANCE DES TÂCHES CONVERSATIONNELLES
# ─────────────────────────────────────────────────────────────

def _load_conv_tasks() -> dict:
    return safe_read_json(_CONV_TASKS_PATH, default={"tasks": {}})


def _save_conv_tasks(data: dict) -> None:
    _CONV_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_CONV_TASKS_PATH, data)


# ─────────────────────────────────────────────────────────────
# PARSER DE DÉLAI EN LANGAGE NATUREL
# ─────────────────────────────────────────────────────────────

def _parse_delay(delay_str: str) -> Optional[timedelta]:
    """
    Parse un délai en langage naturel ou format standard.
    Exemples : "dans 2h", "30min", "1 jour", "3 minutes", "2d"
    """
    s = delay_str.strip().lower()
    s = re.sub(r"dans\s+", "", s)

    patterns = [
        (r"(\d+)\s*(?:semaine|semaines|week|weeks?|w)", lambda m: timedelta(weeks=int(m.group(1)))),
        (r"(\d+)\s*(?:jour|jours|day|days?|d)", lambda m: timedelta(days=int(m.group(1)))),
        (r"(\d+)\s*(?:heure|heures|hour|hours?|h)", lambda m: timedelta(hours=int(m.group(1)))),
        (r"(\d+)\s*(?:minute|minutes|min|m)(?!o|s)", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"(\d+)\s*(?:seconde|secondes|second|seconds?|sec|s)", lambda m: timedelta(seconds=int(m.group(1)))),
    ]

    # Combiner plusieurs composantes : "2h 30min"
    total = timedelta()
    found = False
    for pattern, factory in patterns:
        match = re.search(pattern, s)
        if match:
            total += factory(match)
            found = True
    if found:
        return total

    return None


def _parse_run_at(when_str: str) -> Optional[datetime]:
    """
    Parse une heure absolue ou une date.
    Exemples : "08:00", "2026-03-10 18:00", "demain à 9h", "lundi 8h"
    """
    s = when_str.strip().lower()
    now = datetime.now()

    # Format HH:MM seul → aujourd'hui ou demain si passé
    m = re.match(r"^(\d{1,2})[h:](\d{0,2})$", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # "demain à HH:MM" ou "demain HHh"
    m = re.search(r"demain\s+(?:à\s+)?(\d{1,2})[h:](\d{0,2})", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Jours de la semaine : "lundi 8h", "samedi à 20h"
    DAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    for day_name, day_num in DAYS.items():
        m = re.search(rf"{day_name}\s+(?:à\s+)?(\d{{1,2}})[h:](\d{{0,2}})", s)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            days_ahead = (day_num - now.weekday()) % 7 or 7
            target = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            return target

    # Format ISO datetime complet
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(when_str.strip(), fmt)
        except ValueError:
            pass  # essayer le format suivant

    return None


def _cron_from_natural(expr: str) -> Optional[str]:
    """
    Convertit des expressions naturelles en CRON.
    Ex: "tous les jours à 8h" → "0 8 * * *"
    """
    s = expr.strip().lower()

    # Patterns directs
    NATURAL_CRONS = {
        r"tous les jours?\s+(?:à\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * *",
        r"every day\s+(?:at\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * *",
        r"chaque matin\s+(?:à\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * *",
        r"chaque soir\s+(?:à\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * *",
        r"lundi.vendredi\s+(?:à\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * 1-5",
        r"monday.friday\s+(?:at\s+)?(\d{1,2})[h:](\d{0,2})": lambda m: f"{m.group(2) or '0'} {m.group(1)} * * 1-5",
        r"toutes les\s+(\d+)\s+heures?": lambda m: f"0 */{m.group(1)} * * *",
        r"every\s+(\d+)\s+hours?": lambda m: f"0 */{m.group(1)} * * *",
        r"toutes les\s+(\d+)\s+minutes?": lambda m: f"*/{m.group(1)} * * * *",
        r"every\s+(\d+)\s+minutes?": lambda m: f"*/{m.group(1)} * * * *",
        r"chaque\s+(?:lundi|monday)(?:\s+(?:à|at)\s+(\d{1,2})[h:](\d{0,2}))?": lambda m: f"{m.group(2) or '0'} {m.group(1) or '9'} * * 1",
        r"chaque\s+(?:semaine|week)": lambda _: "0 9 * * 1",
    }

    for pattern, factory in NATURAL_CRONS.items():
        m = re.search(pattern, s)
        if m:
            return factory(m)

    return None


# ─────────────────────────────────────────────────────────────
# CRÉATION DU HANDLER DYNAMIQUE PAR TÂCHE
# ─────────────────────────────────────────────────────────────

def _make_conv_task_handler(task_id: str, action: str, chat_id: str) -> Callable:
    """
    Crée un handler async qui :
    1. Fait réfléchir Lumena sur l'action/prompt demandé
    2. Envoie le résultat par Telegram au bon chat_id
    """
    async def _handler():
        tasks = _load_conv_tasks()
        task_meta = tasks["tasks"].get(task_id, {})
        run_count = task_meta.get("run_count", 0) + 1
        task_meta["run_count"] = run_count
        task_meta["last_run"] = datetime.now().isoformat()
        is_once = task_meta.get("schedule", "").startswith("unique")
        tasks["tasks"][task_id] = task_meta
        _save_conv_tasks(tasks)

        logger.info(f"⏰ Tâche conv. déclenchée: {task_id} (action={action[:60]})")

        result_text = ""

        # Essayer lumena_think (full reasoning)
        if _lumena_think_fn is not None:
            try:
                result_text = await asyncio.wait_for(
                    _lumena_think_fn(action, chat_id),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                result_text = f"⏱️ Tâche planifiée '{action[:40]}...' expirée (timeout 120s)"
            except Exception as e:
                result_text = f"⚠️ Erreur tâche planifiée : {e}"

        # Fallback : envoyer juste le message de rappel
        elif _telegram_send_fn is not None:
            result_text = f"⏰ Rappel planifié : {action}"

        # Envoyer le résultat si on a un sender Telegram et un chat_id
        if chat_id and _telegram_send_fn is not None and result_text:
            try:
                await _telegram_send_fn(chat_id, result_text)
            except Exception as e:
                logger.warning(f"Impossible d'envoyer la réponse Telegram: {e}")

        # Envoyer aussi sur WhatsApp si configuré
        if result_text and _whatsapp_send_fn is not None:
            try:
                owner_phone = os.environ.get("WHATSAPP_OWNER_PHONE", "")
                if owner_phone:
                    await _whatsapp_send_fn(owner_phone, result_text)
            except Exception as e:
                logger.warning(f"Impossible d'envoyer la réponse WhatsApp: {e}")

        # Nettoyage automatique : supprimer la tâche unique du registre JSON après exécution
        if is_once:
            try:
                current = _load_conv_tasks()
                current["tasks"].pop(task_id, None)
                _save_conv_tasks(current)
                logger.debug(f"🧹 Tâche unique {task_id} supprimée du registre après exécution")
            except Exception as e:
                logger.warning(f"Nettoyage conv_tasks échoué: {e}")

        return {"success": True, "run_count": run_count}

    _handler.__name__ = f"conv_task_{task_id}"
    return _handler


def _get_scheduler():
    """Récupère l'instance singleton du scheduler Lumena."""
    try:
        from ..autonomy.scheduler import get_scheduler
        return get_scheduler()
    except Exception as e:
        logger.error(f"Scheduler non disponible: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# HANDLERS PUBLICS
# ─────────────────────────────────────────────────────────────

async def handle_schedule_task(**kwargs) -> str:
    """
    Planifie une tâche à exécuter dans le futur.
    Lumena peut l'appeler quand l'utilisateur demande :
    "dans 2h envoie-moi un rapport", "tous les matins à 8h fais X", etc.

    Paramètres :
    - action : ce que Lumena doit faire quand la tâche se déclenche (prompt/instruction)
    - name : nom lisible de la tâche (défaut: dérivé de l'action)
    - delay : délai avant exécution (ex: "2h", "30min", "1 jour") — pour tâche unique
    - run_at : date/heure précise (ex: "08:00", "demain à 9h", "2026-03-10 18:00")
    - cron : expression CRON ou naturelle (ex: "0 8 * * *", "tous les jours à 8h")
    - interval : intervalle récurrent en minutes (ex: 30 pour toutes les 30min)
    - chat_id : ID Telegram où renvoyer le résultat (optionnel, auto-détecté si possible)
    """
    action = kwargs.get("action", "").strip()
    name = kwargs.get("name", "").strip() or f"Tâche: {action[:40]}"
    delay_str = kwargs.get("delay", "").strip()
    run_at_str = kwargs.get("run_at", "").strip()
    cron_str = kwargs.get("cron", "").strip()
    interval_min = kwargs.get("interval", None)
    chat_id = str(kwargs.get("chat_id", "") or "")

    if not action:
        return "action est requis — ex: 'génère un rapport crypto', 'rappelle-moi de boire de l'eau'"

    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible — l'app doit être démarrée"

    from ..autonomy.scheduler import TaskFrequency
    import uuid

    # Charger les tâches actives pour le check anti-doublon (avant de créer quoi que ce soit)
    _conv_tasks_check = _load_conv_tasks()

    task_id = f"ctask_{uuid.uuid4().hex[:10]}"
    handler_name = f"conv_task_{task_id}"

    # ── Déterminer le mode de planification ──
    scheduled_task = None
    schedule_desc = ""

    if cron_str:
        # Convertir naturel → CRON si besoin
        cron_expr = cron_str if re.match(r"^[\d\*/,\-]+\s+", cron_str) else _cron_from_natural(cron_str)
        if not cron_expr:
            cron_expr = cron_str  # Laisser le scheduler valider

        # ── Guard anti-doublon CRON (hard block) ──
        cron_marker = f"`{cron_expr}`"
        active_cron_dupes = [
            (cid, meta["name"])
            for cid, meta in _conv_tasks_check["tasks"].items()
            if not meta.get("cancelled_at") and cron_marker in meta.get("schedule", "")
        ]
        if active_cron_dupes:
            dupe_list = "\n".join(f"  • `{cid}` — {n}" for cid, n in active_cron_dupes[:5])
            return (
                f"✗ Une tâche active avec le CRON {cron_marker} existe déjà :\n{dupe_list}\n\n"
                f"Utilise modify_task pour la modifier, ou delete_task pour la supprimer avant d'en créer une nouvelle."
            )

        try:
            sched.register_handler(handler_name, _make_conv_task_handler(task_id, action, chat_id))
            scheduled_task = sched.schedule(
                name=name,
                description=f"Tâche conv. — {action[:100]}",
                handler_name=handler_name,
                frequency=TaskFrequency.CRON,
                cron_expr=cron_expr,
                metadata={"action": action, "chat_id": chat_id, "source": "conversation"},
            )
            # Calculer prochaine exécution pour affichage
            next_dt = scheduled_task.next_run.strftime("%d/%m/%Y à %H:%M")
            schedule_desc = f"récurrente (CRON: `{cron_expr}`), prochaine : {next_dt}"
        except ValueError as e:
            return f"✗ Expression CRON invalide : {e}"

    elif interval_min:
        minutes = int(interval_min)

        # ── Guard anti-doublon INTERVAL (hard block) ──
        active_interval_dupes = [
            (cid, meta["name"])
            for cid, meta in _conv_tasks_check["tasks"].items()
            if not meta.get("cancelled_at") and f"toutes les {minutes} minute" in meta.get("schedule", "")
        ]
        if active_interval_dupes:
            dupe_list = "\n".join(f"  • `{cid}` — {n}" for cid, n in active_interval_dupes[:5])
            return (
                f"✗ Une tâche active avec l'intervalle de {minutes} min existe déjà :\n{dupe_list}\n\n"
                f"Utilise modify_task pour la modifier, ou delete_task pour la supprimer avant d'en créer une nouvelle."
            )

        sched.register_handler(handler_name, _make_conv_task_handler(task_id, action, chat_id))
        scheduled_task = sched.schedule(
            name=name,
            description=f"Tâche conv. — {action[:100]}",
            handler_name=handler_name,
            frequency=TaskFrequency.INTERVAL_MS,
            interval_ms=minutes * 60 * 1000,
            metadata={"action": action, "chat_id": chat_id, "source": "conversation"},
        )
        schedule_desc = f"toutes les {minutes} minute(s)"

    elif delay_str:
        delta = _parse_delay(delay_str)
        if delta is None:
            return f"✗ Délai non reconnu : '{delay_str}' (exemples : '2h', '30min', '1 jour')"
        run_dt = datetime.now() + delta
        sched.register_handler(handler_name, _make_conv_task_handler(task_id, action, chat_id))
        scheduled_task = sched.schedule(
            name=name,
            description=f"Tâche conv. — {action[:100]}",
            handler_name=handler_name,
            frequency=TaskFrequency.ONCE,
            run_at=run_dt,
            metadata={"action": action, "chat_id": chat_id, "source": "conversation"},
        )
        next_dt = run_dt.strftime("%d/%m/%Y à %H:%M:%S")
        schedule_desc = f"unique, le {next_dt}"

    elif run_at_str:
        run_dt = _parse_run_at(run_at_str)
        if run_dt is None:
            return f"✗ Date/heure non reconnue : '{run_at_str}' (exemples : '08:00', 'demain à 9h', '2026-03-10 18:00')"
        if run_dt <= datetime.now():
            return f"✗ La date '{run_at_str}' est dans le passé ({run_dt.strftime('%d/%m/%Y %H:%M')})"
        sched.register_handler(handler_name, _make_conv_task_handler(task_id, action, chat_id))
        scheduled_task = sched.schedule(
            name=name,
            description=f"Tâche conv. — {action[:100]}",
            handler_name=handler_name,
            frequency=TaskFrequency.ONCE,
            run_at=run_dt,
            metadata={"action": action, "chat_id": chat_id, "source": "conversation"},
        )
        schedule_desc = f"unique, le {run_dt.strftime('%d/%m/%Y à %H:%M')}"

    else:
        return (
            "✗ Précise quand exécuter la tâche :\n"
            "  • delay='2h'         → dans 2 heures\n"
            "  • run_at='08:00'     → aujourd'hui/demain à 8h\n"
            "  • cron='0 8 * * *'   → tous les jours à 8h\n"
            "  • interval=30        → toutes les 30 minutes"
        )

    # Sauvegarder dans le registre conv_tasks
    conv_tasks = _load_conv_tasks()

    conv_tasks["tasks"][task_id] = {
        "scheduler_task_id": scheduled_task.id,
        "handler_name": handler_name,
        "name": name,
        "action": action,
        "chat_id": chat_id,
        "schedule": schedule_desc,
        "created_at": datetime.now().isoformat(),
        "run_count": 0,
    }
    _save_conv_tasks(conv_tasks)

    return (
        f"✓ Tâche planifiée : **{name}**\n"
        f"   Planification : {schedule_desc}\n"
        f"   Action : {action[:100]}\n"
        f"   ID : `{task_id}`\n\n"
        f"Lumena exécutera automatiquement cette action et vous répondra sur Telegram."
    )


async def handle_list_tasks(**kwargs) -> str:
    """
    Liste toutes les tâches planifiées — système + conversationnelles.
    """
    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible"

    filter_type = (kwargs.get("filter") or "all").strip().lower()  # all | conv | system

    # Tâches conversationnelles
    conv_tasks = _load_conv_tasks()
    conv_ids = {v.get("scheduler_task_id") for v in conv_tasks["tasks"].values()}

    all_tasks = sorted(sched.tasks.values(), key=lambda t: t.next_run)
    stats = sched.get_stats()

    lines = [
        f"⏰ Scheduler — {stats['total_tasks']} tâches | {stats['pending']} en attente | taux succès {stats['success_rate']:.0f}%\n"
    ]

    for task in all_tasks:
        is_conv = task.id in conv_ids
        if filter_type == "conv" and not is_conv:
            continue
        if filter_type == "system" and is_conv:
            continue

        status_icon = {
            "pending": "⏳", "running": "🔄", "completed": "✓",
            "failed": "✗", "cancelled": "⊘"
        }.get(task.status.value, "?")

        enabled_icon = "" if task.enabled else " [désactivée]"
        type_icon = "💬" if is_conv else "⚙️"

        action_preview = ""
        if is_conv:
            meta_action = task.metadata.get("action", "")
            if meta_action:
                action_preview = f"\n     → {meta_action[:80]}"

        freq_str = task.frequency.value
        if task.cron_expr:
            freq_str += f" ({task.cron_expr})"
        elif task.interval_ms:
            freq_str += f" (/{task.interval_ms//60000}min)"

        lines.append(
            f"  {type_icon} {status_icon} [{task.id[:16]}] {task.name}{enabled_icon}\n"
            f"     Handler: {task.handler_name} | {freq_str}\n"
            f"     Prochaine: {task.next_run.strftime('%d/%m %H:%M')} | "
            f"Exécuté: {task.run_count}× | ✓{task.success_count} ✗{task.fail_count}"
            f"{action_preview}"
        )

    if len(lines) == 1:
        lines.append("  (aucune tâche)")

    return "\n".join(lines)


async def handle_cancel_task(**kwargs) -> str:
    """
    Annule une tâche planifiée par son ID.
    - task_id : ID de la tâche (visible dans list_tasks)
    """
    task_id = kwargs.get("task_id", "").strip()
    if not task_id:
        return "task_id requis"

    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible"

    # Chercher par ID exact ou partiel dans les tâches conv
    conv_tasks = _load_conv_tasks()
    found_sched_id = None

    # task_id peut être l'ID conv (ctask_xxx) ou l'ID scheduler (task_xxx)
    if task_id in conv_tasks["tasks"]:
        found_sched_id = conv_tasks["tasks"][task_id].get("scheduler_task_id")
        # Marquer comme annulée dans le registre conv
        conv_tasks["tasks"][task_id]["cancelled_at"] = datetime.now().isoformat()
        _save_conv_tasks(conv_tasks)
    else:
        # Cas : task_id est un handler_name (ex: "conv_task_ctask_270011ae37")
        # list_tasks affiche le handler_name, Lumena peut le copier directement
        matched_conv_key = None
        for conv_key, meta in conv_tasks["tasks"].items():
            if meta.get("handler_name") == task_id:
                matched_conv_key = conv_key
                break
        if matched_conv_key:
            found_sched_id = conv_tasks["tasks"][matched_conv_key].get("scheduler_task_id")
            conv_tasks["tasks"][matched_conv_key]["cancelled_at"] = datetime.now().isoformat()
            _save_conv_tasks(conv_tasks)
        else:
            found_sched_id = task_id  # fallback : laisser le scheduler chercher par match partiel

    # Chercher dans le scheduler (match partiel sur l'ID)
    matched = None
    for sid, stask in sched.tasks.items():
        if sid == found_sched_id or sid.startswith(task_id) or task_id in sid:
            matched = stask
            break

    if matched is None:
        return f"✗ Tâche introuvable : '{task_id}'\nListe tes tâches avec list_tasks"

    sched.cancel_task(matched.id)
    return f"✓ Tâche annulée : {matched.name} [{matched.id[:16]}]"


async def handle_modify_task(**kwargs) -> str:
    """
    Modifie une tâche planifiée existante.
    Peut changer : nom, action, et/ou la planification (cron, interval, delay, run_at).
    Si la planification change, l'ancienne tâche scheduler est annulée et une nouvelle est créée.

    Paramètres :
    - task_id  : ID de la tâche (ctask_xxx, handler_name, ou ID scheduler)
    - name     : nouveau nom lisible (optionnel)
    - action   : nouvelle instruction/prompt (optionnel)
    - cron     : nouvelle expression CRON (ex: "0 9 * * *")
    - interval : nouvel intervalle en minutes
    - delay    : nouveau délai unique (ex: "2h")
    - run_at   : nouvelle heure unique (ex: "08:00", "demain à 9h")
    """
    task_id = kwargs.get("task_id", "").strip()
    new_name = kwargs.get("name", "").strip()
    new_action = kwargs.get("action", "").strip()
    new_cron = kwargs.get("cron", "").strip()
    new_interval = kwargs.get("interval", None)
    new_delay = kwargs.get("delay", "").strip()
    new_run_at = kwargs.get("run_at", "").strip()

    if not task_id:
        return "task_id requis"

    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible"

    conv_tasks = _load_conv_tasks()

    # Résoudre l'ID (supporte ctask_xxx, handler_name, ou ID scheduler)
    resolved_key = None
    if task_id in conv_tasks["tasks"]:
        resolved_key = task_id
    else:
        for conv_key, meta in conv_tasks["tasks"].items():
            if meta.get("handler_name") == task_id:
                resolved_key = conv_key
                break

    if resolved_key is None:
        return f"✗ Tâche introuvable : '{task_id}'\nUtilise list_tasks pour voir les IDs."

    meta = conv_tasks["tasks"][resolved_key]
    if meta.get("cancelled_at"):
        return f"✗ Cette tâche est déjà annulée. Crée-en une nouvelle avec schedule_task."

    changes = []

    # ── Mettre à jour le nom ──
    if new_name:
        meta["name"] = new_name
        sched_id = meta.get("scheduler_task_id")
        if sched_id and sched_id in sched.tasks:
            sched.tasks[sched_id].name = new_name
        changes.append(f"nom → '{new_name}'")

    # ── Mettre à jour l'action (re-register le handler) ──
    if new_action:
        meta["action"] = new_action
        handler_name = meta.get("handler_name", f"conv_task_{resolved_key}")
        chat_id = meta.get("chat_id", "")
        sched.register_handler(handler_name, _make_conv_task_handler(resolved_key, new_action, chat_id))
        changes.append(f"action → '{new_action[:60]}'")

    # ── Changer la planification (cancel + recreate) ──
    if new_cron or new_interval or new_delay or new_run_at:
        from ..autonomy.scheduler import TaskFrequency

        old_sched_id = meta.get("scheduler_task_id")
        if old_sched_id and old_sched_id in sched.tasks:
            sched.cancel_task(old_sched_id)

        handler_name = meta.get("handler_name", f"conv_task_{resolved_key}")
        action_to_use = meta["action"]
        chat_id = meta.get("chat_id", "")
        task_name = meta["name"]

        # S'assurer que le handler est à jour
        sched.register_handler(handler_name, _make_conv_task_handler(resolved_key, action_to_use, chat_id))

        scheduled_task = None
        schedule_desc = ""

        if new_cron:
            cron_expr = new_cron if re.match(r"^[\d\*/,\-]+\s+", new_cron) else _cron_from_natural(new_cron)
            if not cron_expr:
                cron_expr = new_cron
            try:
                scheduled_task = sched.schedule(
                    name=task_name,
                    description=f"[Modifié] {action_to_use[:100]}",
                    handler_name=handler_name,
                    frequency=TaskFrequency.CRON,
                    cron_expr=cron_expr,
                    metadata={"action": action_to_use, "chat_id": chat_id, "source": "modified"},
                )
                next_dt = scheduled_task.next_run.strftime("%d/%m/%Y à %H:%M")
                schedule_desc = f"récurrente (CRON: `{cron_expr}`), prochaine : {next_dt}"
            except ValueError as e:
                return f"✗ Expression CRON invalide : {e}"

        elif new_interval:
            minutes = int(new_interval)
            scheduled_task = sched.schedule(
                name=task_name,
                description=f"[Modifié] {action_to_use[:100]}",
                handler_name=handler_name,
                frequency=TaskFrequency.INTERVAL_MS,
                interval_ms=minutes * 60 * 1000,
                metadata={"action": action_to_use, "chat_id": chat_id, "source": "modified"},
            )
            schedule_desc = f"toutes les {minutes} minute(s)"

        elif new_delay:
            delta = _parse_delay(new_delay)
            if delta is None:
                return f"✗ Délai non reconnu : '{new_delay}' (exemples : '2h', '30min', '1 jour')"
            run_dt = datetime.now() + delta
            scheduled_task = sched.schedule(
                name=task_name,
                description=f"[Modifié] {action_to_use[:100]}",
                handler_name=handler_name,
                frequency=TaskFrequency.ONCE,
                run_at=run_dt,
                metadata={"action": action_to_use, "chat_id": chat_id, "source": "modified"},
            )
            schedule_desc = f"unique, le {run_dt.strftime('%d/%m/%Y à %H:%M:%S')}"

        elif new_run_at:
            run_dt = _parse_run_at(new_run_at)
            if run_dt is None:
                return f"✗ Date/heure non reconnue : '{new_run_at}'"
            if run_dt <= datetime.now():
                return f"✗ La date '{new_run_at}' est dans le passé ({run_dt.strftime('%d/%m/%Y %H:%M')})"
            scheduled_task = sched.schedule(
                name=task_name,
                description=f"[Modifié] {action_to_use[:100]}",
                handler_name=handler_name,
                frequency=TaskFrequency.ONCE,
                run_at=run_dt,
                metadata={"action": action_to_use, "chat_id": chat_id, "source": "modified"},
            )
            schedule_desc = f"unique, le {run_dt.strftime('%d/%m/%Y à %H:%M')}"

        if scheduled_task:
            meta["scheduler_task_id"] = scheduled_task.id
            meta["schedule"] = schedule_desc
            changes.append(f"planification → {schedule_desc}")

    if not changes:
        return (
            "Aucun changement demandé.\n"
            "Paramètres disponibles : name, action, cron, interval, delay, run_at"
        )

    meta["modified_at"] = datetime.now().isoformat()
    conv_tasks["tasks"][resolved_key] = meta
    _save_conv_tasks(conv_tasks)

    changes_str = "\n   ".join(f"• {c}" for c in changes)
    return (
        f"✓ Tâche modifiée : **{meta['name']}** [`{resolved_key}`]\n"
        f"   {changes_str}"
    )


async def handle_delete_task(**kwargs) -> str:
    """
    Supprime définitivement une tâche planifiée (hard delete).
    Contrairement à cancel_task qui la marque annulée et la conserve dans l'historique,
    delete_task la retire complètement du registre JSON.

    Paramètres :
    - task_id : ID de la tâche (ctask_xxx, handler_name, ou ID scheduler)
    """
    task_id = kwargs.get("task_id", "").strip()
    if not task_id:
        return "task_id requis"

    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible"

    conv_tasks = _load_conv_tasks()

    # Résoudre l'ID (supporte ctask_xxx, handler_name, ou ID scheduler)
    resolved_key = None
    if task_id in conv_tasks["tasks"]:
        resolved_key = task_id
    else:
        for conv_key, meta in conv_tasks["tasks"].items():
            if meta.get("handler_name") == task_id:
                resolved_key = conv_key
                break

    if resolved_key is None:
        # Fallback : chercher dans le scheduler directement
        matched = None
        for sid, stask in sched.tasks.items():
            if sid == task_id or sid.startswith(task_id) or task_id in sid:
                matched = stask
                break
        if matched:
            sched.cancel_task(matched.id)
            return f"✓ Tâche supprimée du scheduler (non trouvée dans le registre conv) : {matched.name}"
        return f"✗ Tâche introuvable : '{task_id}'\nUtilise list_tasks pour voir les IDs."

    meta = conv_tasks["tasks"][resolved_key]
    task_name = meta.get("name", resolved_key)

    # Annuler dans le scheduler
    sched_id = meta.get("scheduler_task_id")
    if sched_id and sched_id in sched.tasks:
        sched.cancel_task(sched_id)

    # Hard delete du JSON
    del conv_tasks["tasks"][resolved_key]
    _save_conv_tasks(conv_tasks)

    return f"✓ Tâche supprimée définitivement : **{task_name}** [`{resolved_key}`]"


async def handle_task_history(**kwargs) -> str:
    """
    Affiche l'historique d'exécution des tâches planifiées.
    - task_id : (optionnel) filtrer par tâche spécifique
    - limit : nombre max d'entrées (défaut: 20)
    """
    sched = _get_scheduler()
    if sched is None:
        return "✗ Scheduler non disponible"

    task_id_filter = kwargs.get("task_id", "").strip()
    limit = int(kwargs.get("limit", 20))

    tasks_to_show = list(sched.tasks.values())
    if task_id_filter:
        tasks_to_show = [
            t for t in tasks_to_show
            if task_id_filter in t.id or task_id_filter in t.name.lower()
        ]

    if not tasks_to_show:
        return "Aucune tâche trouvée"

    tasks_to_show = sorted(tasks_to_show, key=lambda t: t.last_run or datetime.min, reverse=True)[:limit]

    lines = [f"📋 Historique des tâches ({len(tasks_to_show)} affichées)\n"]
    for t in tasks_to_show:
        last_run = t.last_run.strftime("%d/%m %H:%M") if t.last_run else "jamais"
        next_run = t.next_run.strftime("%d/%m %H:%M") if t.next_run else "?"
        last_result = t.metadata.get("last_result", {})
        last_status = last_result.get("status", "?") if isinstance(last_result, dict) else "?"
        last_duration = t.metadata.get("last_duration_ms")
        duration_str = f"{last_duration:.0f}ms" if last_duration else ""

        lines.append(
            f"  [{t.id[:16]}] {t.name}\n"
            f"     Dernier run : {last_run} → {last_status} {duration_str}\n"
            f"     Prochain    : {next_run} | Total: {t.run_count}× ✓{t.success_count} ✗{t.fail_count}"
        )

    return "\n".join(lines)


async def handle_schedule_remind(**kwargs) -> str:
    """
    Raccourci rapide pour créer un rappel simple.
    - message : message de rappel
    - delay : délai (ex: "30min", "2h", "1 jour")
    - run_at : heure précise (ex: "08:00", "demain à 9h")
    - chat_id : ID Telegram (optionnel)
    """
    message = kwargs.get("message", "").strip()
    if not message:
        return "message requis"

    # Déléguer à schedule_task avec l'action = le message de rappel
    return await handle_schedule_task(
        action=f"Rappel : {message}",
        name=f"Rappel: {message[:30]}",
        delay=kwargs.get("delay", ""),
        run_at=kwargs.get("run_at", ""),
        chat_id=kwargs.get("chat_id", ""),
    )


# ─────────────────────────────────────────────────────────────
# RESTAURATION DES TÂCHES CONVERSATIONNELLES AU REDÉMARRAGE
# ─────────────────────────────────────────────────────────────

def restore_conv_tasks() -> int:
    """
    Restaure dans le scheduler les tâches conversationnelles persistées
    dans conversation_tasks.json.
    Appelé une fois au démarrage (dans web/server.py).
    Retourne le nombre de tâches restaurées.
    """
    sched = _get_scheduler()
    if sched is None:
        return 0

    conv_tasks = _load_conv_tasks()
    tasks = conv_tasks.get("tasks", {})
    if not tasks:
        return 0

    restored = 0
    to_remove = []

    for task_id, meta in tasks.items():
        # Ignorer les tâches uniques déjà exécutées
        if meta.get("schedule", "").startswith("unique") and meta.get("run_count", 0) > 0:
            to_remove.append(task_id)
            continue

        action = meta.get("action", "")
        chat_id = meta.get("chat_id", "")
        name = meta.get("name", f"Tâche: {action[:40]}")
        schedule = meta.get("schedule", "")

        if not action:
            to_remove.append(task_id)
            continue

        try:
            from ..autonomy.scheduler import TaskFrequency
            import re

            handler_name = f"conv_task_{task_id}"
            handler_fn = _make_conv_task_handler(task_id, action, chat_id)
            sched.register_handler(handler_name, handler_fn)

            # Déterminer le type de planification depuis la description sauvée
            if "CRON" in schedule or re.search(r"\d+ \d+ \*", schedule):
                cron_match = re.search(r"`([^`]+)`", schedule)
                cron_expr = cron_match.group(1) if cron_match else None
                if cron_expr:
                    sched.schedule(
                        name=name,
                        description=f"[Restauré] {action[:100]}",
                        handler_name=handler_name,
                        frequency=TaskFrequency.CRON,
                        cron_expr=cron_expr,
                        metadata={"action": action, "chat_id": chat_id, "source": "restored"},
                    )
                    restored += 1
            elif "toutes les" in schedule:
                min_match = re.search(r"toutes les (\d+) minute", schedule)
                if min_match:
                    minutes = int(min_match.group(1))
                    sched.schedule(
                        name=name,
                        description=f"[Restauré] {action[:100]}",
                        handler_name=handler_name,
                        frequency=TaskFrequency.INTERVAL_MS,
                        interval_ms=minutes * 60 * 1000,
                        metadata={"action": action, "chat_id": chat_id, "source": "restored"},
                    )
                    restored += 1
            # Les tâches "unique" non encore exécutées sont ignorées
            # (on ne peut pas reconstruire la date exacte sans run_at sauvegardée)
        except Exception as e:
            logger.warning(f"Restauration tâche {task_id} échouée: {e}")

    # Nettoyer les tâches expirées du JSON
    if to_remove:
        for tid in to_remove:
            conv_tasks["tasks"].pop(tid, None)
        _save_conv_tasks(conv_tasks)

    if restored:
        logger.info(f"⏰ {restored} tâche(s) conversationnelle(s) restaurée(s) depuis conversation_tasks.json")
    return restored
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
