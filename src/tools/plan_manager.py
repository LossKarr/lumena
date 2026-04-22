"""
Lumena — Plan Manager
=====================
Permet à Lumena de créer, suivre et clore des plans/TODO sous forme de
fichiers Markdown avec checkboxes, comme une todo-list persistante.

Stockage : data/plans/  (actifs)  | data/plans/archives/  (clôturés)

Outils exposés :
  plan_create  — Crée un nouveau plan avec une liste de tâches
  plan_update  — Coche ou décoche une tâche dans un plan
  plan_list    — Liste les plans actifs (+ archivés optionnel)
  plan_done    — Clôture un plan (archive ou suppression)
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import ROOT_DIR as _ROOT, PLANS_DIR as _PLANS_DIR
_ARCHIVES_DIR = _PLANS_DIR / "archives"


# ─────────────────────────────────────────────────────────────
# UTILITAIRES INTERNES
# ─────────────────────────────────────────────────────────────

def _ensure_dirs():
    _PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    """Convertit un titre en nom de fichier safe."""
    text = text.lower().strip()
    text = re.sub(r"[àáâãäå]", "a", text)
    text = re.sub(r"[èéêë]", "e", text)
    text = re.sub(r"[ìíîï]", "i", text)
    text = re.sub(r"[òóôõö]", "o", text)
    text = re.sub(r"[ùúûü]", "u", text)
    text = re.sub(r"[ç]", "c", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text[:40].strip("_")


def _find_plan(plan_id: str) -> Optional[Path]:
    """Trouve un fichier plan par ID partiel ou nom exact."""
    _ensure_dirs()
    # Chercher d'abord exact
    for p in _PLANS_DIR.glob("*.md"):
        if p.stem == plan_id or p.name == plan_id or plan_id in p.stem:
            return p
    return None


def _parse_tasks_from_content(content: str) -> List[tuple]:
    """Retourne list[(done: bool, text: str)] pour toutes les lignes checkbox."""
    tasks = []
    for line in content.splitlines():
        m = re.match(r"- \[([ xX])\] (.+)", line)
        if m:
            done = m.group(1).lower() == "x"
            tasks.append((done, m.group(2).strip()))
    return tasks


# ─────────────────────────────────────────────────────────────
# HANDLERS PUBLICS
# ─────────────────────────────────────────────────────────────

async def handle_plan_create(**kwargs) -> str:
    """
    Crée un fichier plan Markdown avec une liste de tâches.

    Paramètres :
    - title   : titre du plan (requis)
    - tasks   : liste de tâches, séparées par '|' ou '\n' (requis)
    - context : contexte/objectif du plan (optionnel)
    """
    title = (kwargs.get("title") or "").strip()
    tasks_raw = (kwargs.get("tasks") or "").strip()
    context = (kwargs.get("context") or "").strip()

    if not title:
        return "✗ title requis (ex: title='Créer projet GitHub du jour')"
    if not tasks_raw:
        return "✗ tasks requis (ex: tasks='Create repo|Write code|Push')"

    tasks = [t.strip() for t in re.split(r"[|\n]+", tasks_raw) if t.strip()]
    if not tasks:
        return "✗ Aucune tâche valide trouvée dans tasks"

    _ensure_dirs()
    now = datetime.now()
    slug = _slugify(title)
    filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}.md"
    plan_path = _PLANS_DIR / filename

    # Guard contre collision horodatée (runs parallèles)
    if plan_path.exists():
        for _i in range(1, 100):
            filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}_{_i}.md"
            plan_path = _PLANS_DIR / filename
            if not plan_path.exists():
                break

    # Construire le contenu Markdown
    lines = [
        f"# 📋 {title}",
        f"",
        f"**Créé le :** {now.strftime('%d/%m/%Y à %H:%M')}  ",
        f"**Statut :** 🔄 en cours  ",
        f"**Fichier :** `{filename}`  ",
    ]
    if context:
        lines += ["", f"**Contexte :** {context}"]
    lines += ["", "## Tâches", ""]
    for task in tasks:
        lines.append(f"- [ ] {task}")
    lines += ["", "---", f"*Plan créé automatiquement par Lumena*"]

    plan_path.write_text("\n".join(lines), encoding="utf-8")

    plan_id = plan_path.stem
    total = len(tasks)
    return (
        f"✓ Plan créé : **{title}**\n"
        f"   Fichier : `data/plans/{filename}`\n"
        f"   ID      : `{plan_id}`\n"
        f"   Tâches  : {total}\n\n"
        + "\n".join(f"  {i+1}. ☐ {t}" for i, t in enumerate(tasks))
    )


async def handle_plan_update(**kwargs) -> str:
    """
    Coche ou décoche une ou plusieurs tâches dans un plan.

    Paramètres :
    - plan_id    : ID ou nom partiel du plan (requis)
    - task_index : numéro(s) de tâche (1-based), ex: '1' ou '1,3,5' (requis)
    - done       : True → cocher (défaut), False → décocher
    """
    plan_id = (kwargs.get("plan_id") or "").strip()
    task_index_raw = str(kwargs.get("task_index") or "").strip()
    done = str(kwargs.get("done", "true")).lower() not in ("false", "0", "non", "no")

    if not plan_id:
        return "✗ plan_id requis"
    if not task_index_raw:
        return "✗ task_index requis (ex: '1' ou '1,3')"

    plan_path = _find_plan(plan_id)
    if plan_path is None:
        active = [p.stem for p in _PLANS_DIR.glob("*.md")]
        return (
            f"✗ Plan '{plan_id}' introuvable.\n"
            f"Plans actifs : {', '.join(active) or 'aucun'}"
        )

    # Parser les index
    indices = set()
    for part in re.split(r"[,\s]+", task_index_raw):
        part = part.strip()
        if part.isdigit():
            indices.add(int(part))

    if not indices:
        return f"✗ task_index invalide : '{task_index_raw}' (exemples: '1', '2,3')"

    content = plan_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    task_counter = 0
    updated = []
    changed = 0

    for line in lines:
        m = re.match(r"(- \[)[ xX](\] .+)", line)
        if m:
            task_counter += 1
            if task_counter in indices:
                mark = "x" if done else " "
                line = f"{m.group(1)}{mark}{m.group(2)}\n"
                changed += 1
        updated.append(line)

    if changed == 0:
        tasks = _parse_tasks_from_content(content)
        return (
            f"✗ Aucune tâche trouvée aux index {sorted(indices)} "
            f"(plan a {len(tasks)} tâche(s) : 1-{len(tasks)})"
        )

    new_content = "".join(updated)

    # Recalculer le statut global
    all_tasks = _parse_tasks_from_content(new_content)
    done_count = sum(1 for d, _ in all_tasks if d)
    total_count = len(all_tasks)
    all_done = done_count == total_count and total_count > 0

    # Mettre à jour la ligne statut
    if all_done:
        new_content = re.sub(
            r"\*\*Statut :\*\* .+",
            "**Statut :** ✅ terminé",
            new_content,
        )
    plan_path.write_text(new_content, encoding="utf-8")

    mark_str = "✅" if done else "☐"
    summary = (
        f"{'✅' if done else '☐'} {changed} tâche(s) mise(s) à jour dans **{plan_path.stem}**\n"
        f"   Progression : {done_count}/{total_count}"
    )
    if all_done:
        summary += "\n\n🎉 Toutes les tâches sont cochées ! Utilise `plan_done` pour clôturer."
    return summary


async def handle_plan_list(**kwargs) -> str:
    """
    Liste les plans en cours (ou archivés).

    Paramètres :
    - include_archives : True pour inclure les archives (défaut: False)
    """
    include_archives = str(kwargs.get("include_archives", "false")).lower() in ("true", "1", "oui", "yes")
    _ensure_dirs()

    active_plans = sorted(_PLANS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    archived_plans = sorted(_ARCHIVES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if include_archives else []

    if not active_plans and not archived_plans:
        return "📋 Aucun plan actif. Utilise `plan_create` pour créer un plan."

    lines = ["📋 **Plans Lumena**\n"]

    if active_plans:
        lines.append("**🔄 En cours :**")
        for p in active_plans[:20]:
            content = p.read_text(encoding="utf-8")
            tasks = _parse_tasks_from_content(content)
            done_count = sum(1 for d, _ in tasks if d)
            total = len(tasks)
            pct = f"{done_count}/{total}" if total else "0/0"
            bar = "█" * done_count + "░" * (total - done_count) if total <= 10 else ""
            title_match = re.search(r"# 📋 (.+)", content)
            title = title_match.group(1) if title_match else p.stem
            lines.append(f"  • `{p.stem}`\n    {title} — {pct} {bar}")
        lines.append("")

    if archived_plans:
        lines.append("**📦 Archives :**")
        for p in archived_plans[:10]:
            lines.append(f"  • `{p.stem}` (archivé)")

    return "\n".join(lines)


async def handle_plan_done(**kwargs) -> str:
    """
    Clôture un plan — l'archive ou le supprime.

    Paramètres :
    - plan_id : ID ou nom partiel du plan (requis)
    - archive : True → déplacer vers archives (défaut), False → supprimer
    """
    plan_id = (kwargs.get("plan_id") or "").strip()
    archive = str(kwargs.get("archive", "true")).lower() not in ("false", "0", "non", "no")

    if not plan_id:
        return "✗ plan_id requis"

    plan_path = _find_plan(plan_id)
    if plan_path is None:
        active = [p.stem for p in _PLANS_DIR.glob("*.md")]
        return (
            f"✗ Plan '{plan_id}' introuvable.\n"
            f"Plans actifs : {', '.join(active) or 'aucun'}"
        )

    content = plan_path.read_text(encoding="utf-8")
    tasks = _parse_tasks_from_content(content)
    done_count = sum(1 for d, _ in tasks if d)
    total = len(tasks)

    title_match = re.search(r"# 📋 (.+)", content)
    title = title_match.group(1) if title_match else plan_path.stem

    if archive:
        _ensure_dirs()
        dest = _ARCHIVES_DIR / plan_path.name
        # Ajouter une ligne de clôture dans le fichier
        closed_content = re.sub(
            r"\*\*Statut :\*\* .+",
            f"**Statut :** 📦 archivé le {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            content,
        )
        dest.write_text(closed_content, encoding="utf-8")
        plan_path.unlink()
        return (
            f"📦 Plan archivé : **{title}**\n"
            f"   Tâches complétées : {done_count}/{total}\n"
            f"   Archive : `data/plans/archives/{plan_path.name}`"
        )
    else:
        plan_path.unlink()
        return (
            f"🗑️ Plan supprimé : **{title}**\n"
            f"   Tâches complétées : {done_count}/{total}"
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
