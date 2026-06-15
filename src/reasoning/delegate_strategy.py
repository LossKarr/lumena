"""Stratégie delegate / vérification web post-CodeAgent — helpers purs.

Extrait verbatim de react.py (déménagement pur, zéro changement de comportement).
Toutes les fonctions sont pures (aucune ne prend `self`) et ne dépendent que de la
stdlib — pas de react_config, pas de ToolRegistry, pas de ledger → aucun import
circulaire possible.

react.py ré-importe ces noms (point d'import historique des tests).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, List, Optional

_DELEGATE_NOOP_MARKERS = (
    "run_tests : test_path requis",
    "test_path requis pour run_tests",
    "test_path required",
    "aucun test runner detecte",
    "livraison refusee",
    "livraison refusée",
)


def _fold_react_status_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.lower()


def _delegate_report_has_real_work(tool_name: str, obs_text: str) -> bool:
    """True si un rapport delegate_task peut déclencher le FINAL direct."""
    folded = _fold_react_status_text(obs_text)
    if any(marker in folded for marker in _DELEGATE_NOOP_MARKERS):
        return False
    if tool_name == "delegate_task":
        match = re.search(
            r"\((?:n/a|[0-9]+(?:\.[0-9]+)?s),\s*(\?|\d+)\s+it",
            folded,
        )
        return bool(match and match.group(1) != "?" and int(match.group(1)) > 0)
    return True


_WEB_DELIVERY_MARKERS = (
    "index.html", ".html", ".css", ".js", "site", "website", "web app",
    "application web", "jeu", "game", "three.js", "threejs", "canvas",
    "frontend", "vite", "react", "html/css/js",
)

_CANVAS_DELIVERY_MARKERS = (
    "three.js", "threejs", "webgl", "babylon.js", "babylonjs",
    "pixi.js", "pixijs", "<canvas", "canvas html", "html canvas",
    "canvas 2d", "2d canvas", "dessin canvas", "drawing canvas",
    "paint canvas", "canvas drawing", "particle canvas", "particles canvas",
    "particules canvas", "context 2d", "getcontext",
)

_CANVAS_NON_TECHNICAL_MARKERS = (
    "moodboard", "mood board", "zone type canvas", "type canvas",
    "canvas/moodboard", "canvas de travail", "workspace canvas",
    "kanban",
)


def _post_delegate_web_verify_enabled() -> bool:
    raw = os.environ.get("LUMENA_POST_DELEGATE_WEB_VERIFY", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _looks_like_web_delegate_delivery(original_query: str, tool_args: dict, obs_text: str) -> bool:
    payload = " ".join(
        str(part or "") for part in (
            original_query,
            obs_text,
            tool_args.get("description") if isinstance(tool_args, dict) else "",
            tool_args.get("project_path") if isinstance(tool_args, dict) else "",
            tool_args.get("output_dir") if isinstance(tool_args, dict) else "",
            tool_args.get("project_dir") if isinstance(tool_args, dict) else "",
            tool_args.get("project_name") if isinstance(tool_args, dict) else "",
            tool_args.get("path") if isinstance(tool_args, dict) else "",
            json.dumps(tool_args.get("context", {}), ensure_ascii=False, default=str)
            if isinstance(tool_args, dict) else "",
        )
    )
    folded = _fold_react_status_text(payload)
    return any(marker in folded for marker in _WEB_DELIVERY_MARKERS)


def _delegate_delivery_expects_canvas(original_query: str, tool_args: dict, obs_text: str) -> bool:
    payload = " ".join(
        str(part or "") for part in (
            original_query,
            obs_text,
            tool_args.get("description") if isinstance(tool_args, dict) else "",
        )
    )
    folded = _fold_react_status_text(payload)
    if any(marker in folded for marker in _CANVAS_DELIVERY_MARKERS):
        return True
    if bool(
        re.search(
            r"\b(?:jeu|game|open world|monde|scene|sc[eè]ne)\b.{0,32}\b3d\b"
            r"|\b3d\b.{0,32}\b(?:jeu|game|open world|monde|scene|sc[eè]ne)\b",
            folded,
        )
    ):
        return True
    if any(marker in folded for marker in _CANVAS_NON_TECHNICAL_MARKERS):
        return False
    return False


def _is_post_codeagent_synthesis_task(description: str) -> bool:
    """True for plan tasks that are fulfilled by writing the FINAL response."""
    text = _fold_react_status_text(description)
    if any(
        marker in text
        for marker in (
            "email", "mail", "courriel", "telegram", "whatsapp",
            "discord", "pdf", "docx", "xlsx", "zip", "archive",
            "upload", "deployer", "deploi", "publier", "poster",
            "envoyer", "envoie", "envoi", "send", "joindre", "attacher",
        )
    ):
        return False
    stripped = re.sub(
        r"^\s*(?:etape|step)\s*\d+\s*[:\-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if any(
        stripped.startswith(prefix)
        for prefix in (
            "resumer", "recapituler", "synthetiser", "conclure",
            "repondre a l'utilisateur", "repondre a l utilisateur",
            "donner le resume", "donner un resume", "donner le recap",
            "donner le bilan", "faire le resume", "faire un resume",
        )
    ):
        return True
    if any(
        marker in stripped
        for marker in (
            "resume final", "rapport final", "reponse finale",
            "compte rendu final", "synthese finale",
            "donner le resume final", "donner le rapport final",
        )
    ):
        return True
    return (
        ("a l'utilisateur" in stripped or "a l utilisateur" in stripped)
        and any(
            verb in stripped
            for verb in (
                "presenter", "donner", "informer", "communiquer",
                "signaler", "expliquer", "livrer",
            )
        )
    )


def _is_post_codeagent_conditional_correction_task(description: str) -> bool:
    """True for no-op correction tasks covered when the web runtime verify passed."""
    text = _fold_react_status_text(description)
    has_correction = any(
        marker in text
        for marker in (
            "corriger", "correction", "corrige", "reparer",
            "fix", "debugger", "deboguer",
        )
    )
    has_condition = any(
        marker in text
        for marker in (
            "si necessaire", "si besoin", "au besoin",
            "si besoin est", "if needed", "if necessary",
        )
    )
    return has_correction and has_condition


def _is_post_codeagent_closure_task(description: str) -> bool:
    return (
        _is_post_codeagent_synthesis_task(description)
        or _is_post_codeagent_conditional_correction_task(description)
    )


def _candidate_is_web_project(path: Path) -> bool:
    try:
        if path.is_file():
            path = path.parent
        return path.is_dir() and (
            (path / "index.html").is_file()
            or (path / "package.json").is_file()
            or any(path.glob("*.html"))
        )
    except Exception:
        return False


def _extract_existing_web_project_path(
    tool_args: dict,
    obs_text: str,
    *,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Extract an existing web project directory from delegate args/report."""
    base = Path(base_dir or Path.cwd())
    candidates: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        text = str(value).strip().strip("`\"'")
        if not text:
            return
        candidates.append(text)

    if isinstance(tool_args, dict):
        add(tool_args.get("project_path"))
        add(tool_args.get("output_dir"))
        add(tool_args.get("project_dir"))
        add(tool_args.get("path"))
        add(tool_args.get("project_name"))
        context = tool_args.get("context")
        if isinstance(context, dict):
            add(context.get("workspace_path"))
            add(context.get("output_dir"))
            add(context.get("project_dir"))
            add(context.get("path"))
            add(context.get("project_name"))
        add(tool_args.get("description"))

    text_blob = "\n".join([obs_text or ""] + candidates)
    for match in re.finditer(
        r"([A-Za-z]:[\\/][^\n\r`\"<>|]+|\\\\[^\n\r`\"<>|]+|(?:^|[\s`'\"])(workspace[\\/][^\n\r`\"'<>|]+))",
        text_blob,
    ):
        raw = match.group(1) or match.group(2) or ""
        add(raw)

    seen: set[str] = set()
    for raw in candidates:
        cleaned = re.sub(r"^[\s`'\"()]+|[\s`'\"().,;:!?]+$", "", raw)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        probes = [Path(cleaned)]
        if not Path(cleaned).is_absolute():
            probes.append(base / cleaned)
            probes.append(base / "workspace" / cleaned)
        for probe in probes:
            try:
                resolved = probe.resolve()
            except Exception:
                resolved = probe
            if _candidate_is_web_project(resolved):
                return resolved.parent if resolved.is_file() else resolved
    try:
        workspace_root = base / "workspace"
        if workspace_root.is_dir():
            latest_indexes = sorted(
                [p for p in workspace_root.rglob("index.html") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if latest_indexes:
                return latest_indexes[0].parent.resolve()
    except Exception:
        pass
    return None


def _build_post_delegate_web_verify_success_query(
    original_query: str,
    obs_text: str,
    verify_report: str,
) -> str:
    return (
        f"Requête originale: {original_query}\n\n"
        f"Le CodeAgent a terminé avec succès :\n{obs_text[:2200]}\n\n"
        "Vérification navigateur autonome après CodeAgent : OK\n"
        f"{verify_report[:2200]}\n\n"
        "INSTRUCTION : Rédige maintenant ta réponse finale à l'utilisateur en résumant "
        "ce qui a été accompli et vérifié. Utilise OBLIGATOIREMENT :\n"
        "THOUGHT: (1 ligne)\n"
        "ACTION: FINAL\n"
        "ACTION_INPUT: [résumé clair de ce qui a été fait et vérifié]"
    )


def _build_post_delegate_continue_query(
    original_query: str,
    obs_text: str,
    pending_tasks: List[str],
    verify_report: str = "",
) -> str:
    pending = "\n".join(f"- {task}" for task in pending_tasks[:8])
    verify_block = (
        "Vérification navigateur autonome après CodeAgent : OK\n"
        f"{verify_report[:1600]}\n\n"
        if verify_report
        else ""
    )
    return (
        f"Requête originale: {original_query}\n\n"
        f"Le CodeAgent a terminé avec succès :\n{obs_text[:2200]}\n\n"
        f"{verify_block}"
        "Ne finalise pas encore : le CodeAgent a terminé sa sous-tâche, "
        "mais il reste des tâches métier à accomplir.\n"
        f"Tâches restantes:\n{pending}\n\n"
        "INSTRUCTION : continue avec la prochaine tâche métier restante. "
        "Ne produis une reponse finale que quand ces tâches restantes sont réellement faites "
        "ou impossibles avec explication claire."
    )


def _verify_report_has_preview_server_mime_error(verify_report: str) -> bool:
    folded = _fold_react_status_text(verify_report)
    return (
        "preview_server_mime_error" in folded
        or (
            "mime type" in folded
            and "application/json" in folded
            and "javascript" in folded
        )
    )


def _build_post_delegate_web_verify_failure_query(
    original_query: str,
    project_path: Path,
    obs_text: str,
    verify_report: str,
) -> str:
    if _verify_report_has_preview_server_mime_error(verify_report):
        instruction = (
            "INSTRUCTION OBLIGATOIRE : ne finalise pas et ne redemande pas au CodeAgent "
            "de reecrire les fichiers JS uniquement pour ce MIME. Relance d'abord "
            "`browser_verify_local_project` sur ce dossier : le serveur preview Lumena "
            "force les MIME JavaScript. Si la verification echoue encore avec une vraie "
            "erreur applicative distincte, appelle ensuite `delegate_task` pour corriger."
        )
    else:
        instruction = (
            "INSTRUCTION OBLIGATOIRE : ne finalise pas. Appelle maintenant `delegate_task` "
            "avec `agent_type=\"code\"`, `project_path` sur ce dossier, et une description "
            "demandant de corriger les erreurs runtime navigateur ci-dessus. Après correction, "
            "la vérification navigateur sera relancée."
        )
    return (
        f"Requête originale: {original_query}\n\n"
        f"Le CodeAgent a livré ce rapport :\n{obs_text[:1800]}\n\n"
        "VÉRIFICATION NAVIGATEUR AUTONOME ÉCHOUÉE.\n"
        f"Projet vérifié: {project_path}\n"
        f"{verify_report[:2600]}\n\n"
        f"{instruction}"
    )
