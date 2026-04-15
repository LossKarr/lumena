"""
pipeline_router.py — Exécution directe de séquences d'outils connues.

Évite la boucle ReAct pour les tâches à workflow connu (edit+deploy, deploy seul,
generate document, etc.).  Le LLM n'est PAS appelé entre les étapes — seuls les
outils sont exécutés dans l'ordre déclaré.

Si un pipeline échoue, le caller (react.py) peut fallback sur la boucle ReAct.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class PipelineStep:
    """Un pas atomique dans un pipeline : appel à un outil du ToolRegistry."""
    tool: str
    build_args: Callable[[Dict[str, Any]], Dict[str, Any]]
    optional: bool = False


@dataclass
class Pipeline:
    """Workflow linéaire : détection (matchers) → étapes (steps)."""
    name: str
    matchers: List[Callable[[str], bool]]
    steps: List[PipelineStep]
    pre_resolve: bool = True          # résoudre le workspace avant d'exécuter
    extract_extras: Optional[Callable[[str], Dict[str, Any]]] = None  # extraction depuis query


@dataclass
class PipelineResult:
    """Résultat d'exécution d'un pipeline."""
    success: bool
    message: str
    pipeline_name: str = ""
    steps_executed: int = 0


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_IONOS_DOMAIN_RE = re.compile(
    r"\b(openlumena\.com|lumena\.fr|[\w.-]+\.(?:com|fr|net|org|io|dev))\b",
    re.IGNORECASE,
)


def _extract_deploy_extras(query: str) -> Dict[str, Any]:
    """Extrait le domaine IONOS cible depuis la query."""
    m = _IONOS_DOMAIN_RE.search(query)
    return {"deploy_site": m.group(1)} if m else {}


# ---------------------------------------------------------------------------
# Arg builders — chaque fonction reçoit le contexte pipeline et retourne les
# args pour ToolRegistry.execute(name, args).
# ---------------------------------------------------------------------------

def _build_edit_website_args(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Construit les args pour edit_website."""
    return {
        "modifications": ctx.get("original_query", ""),
        "directory": str(ctx.get("project_dir", "")),
    }


def _build_deploy_args(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Construit les args pour deploy_to_ionos."""
    site = ctx.get("deploy_site", "")
    if not site:
        site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
    return {
        "site": site,
        "project_dir": str(ctx.get("project_dir", "")),
    }


# ---------------------------------------------------------------------------
# Exclusion : si la requête mentionne un keyword de skill spécifique
# (video, pdf, discord...), les pipelines web ne capturent PAS.
# ---------------------------------------------------------------------------

_SKILL_EXCLUSION_RE = re.compile(
    r"\b(video|vid[eé]o|remotion|reel|short|tiktok|animation|pdf|docx|xlsx|pptx|"
    r"facture|devis|contrat|rapport|document|email|mail|discord|telegram|"
    r"whatsapp|spotify|notion|github|stripe|agent|sous-agent)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Matchers — fonctions booléennes testées dans l'ordre.
# Un seul match suffit pour déclencher le pipeline.
# ---------------------------------------------------------------------------

def _match_edit_and_deploy(query: str) -> bool:
    """Détecte 'modifie/améliore/complète un site web ET déploie/upload'."""
    q = query.lower()
    has_edit = bool(re.search(
        r"\b(am[eé]lior\w*|modifi\w*|compl[eè]t\w*|met[sz]?\s+[àa]\s+jour|chang\w*|refai[st]\w*|"
        r"corrig\w*|r[eé]par\w*|refond\w*|redesign\w*|update\w*|edit\w*|improv\w*|upgrad\w*)\b",
        q,
    ))
    has_site = bool(re.search(
        r"\b(site\s+web|site|website|page\s+web|vitrine|landing)\b",
        q,
    ))
    has_deploy = bool(re.search(
        r"\b(deploy\w*|d[eé]ploi\w*|upload\w*|publi[eé]\w*|met[sz]?\s+en\s+ligne|"
        r"ionos|openlumena|h[eé]berg\w*|sftp)\b",
        q,
    ))
    has_skill_kw = bool(_SKILL_EXCLUSION_RE.search(q))
    return has_edit and has_site and has_deploy and not has_skill_kw


def _match_edit_website_only(query: str) -> bool:
    """Détecte 'modifie/améliore un site web' SANS demande de deploy."""
    q = query.lower()
    has_edit = bool(re.search(
        r"\b(am[eé]lior\w*|modifi\w*|compl[eè]t\w*|met[sz]?\s+[àa]\s+jour|chang\w*|refai[st]\w*|"
        r"corrig\w*|r[eé]par\w*|refond\w*|redesign\w*|update\w*|edit\w*|improv\w*|upgrad\w*)\b",
        q,
    ))
    has_site = bool(re.search(
        r"\b(site\s+web|site|website|page\s+web|vitrine|landing)\b",
        q,
    ))
    has_deploy = bool(re.search(
        r"\b(deploy\w*|d[eé]ploi\w*|upload\w*|publi[eé]\w*|met[sz]?\s+en\s+ligne|"
        r"ionos|openlumena|h[eé]berg\w*|sftp)\b",
        q,
    ))
    has_skill_kw = bool(_SKILL_EXCLUSION_RE.search(q))
    return has_edit and has_site and not has_deploy and not has_skill_kw


def _match_deploy_only(query: str) -> bool:
    """Détecte 'déploie/upload le site' sans demande de modification."""
    q = query.lower()
    has_edit = bool(re.search(
        r"\b(am[eé]lior\w*|modifi\w*|compl[eè]t\w*|met[sz]?\s+[àa]\s+jour|chang\w*|refai[st]\w*|"
        r"corrig\w*|r[eé]par\w*|refond\w*|redesign\w*|update\w*|edit\w*|improv\w*|upgrad\w*)\b",
        q,
    ))
    has_deploy = bool(re.search(
        r"\b(deploy\w*|d[eé]ploi\w*|upload\w*|publi[eé]\w*|met[sz]?\s+en\s+ligne|"
        r"ionos|h[eé]berg\w*|sftp)\b",
        q,
    ))
    # Pas de verbe d'édition → deploy only
    return has_deploy and not has_edit


# ---------------------------------------------------------------------------
# Pipelines déclarés
# ---------------------------------------------------------------------------

PIPELINES: List[Pipeline] = [
    Pipeline(
        name="edit_and_deploy",
        matchers=[_match_edit_and_deploy],
        steps=[
            PipelineStep(tool="edit_website", build_args=_build_edit_website_args),
            PipelineStep(tool="deploy_to_ionos", build_args=_build_deploy_args),
        ],
        extract_extras=_extract_deploy_extras,
    ),
    Pipeline(
        name="edit_website_only",
        matchers=[_match_edit_website_only],
        steps=[
            PipelineStep(tool="edit_website", build_args=_build_edit_website_args),
        ],
    ),
    Pipeline(
        name="deploy_only",
        matchers=[_match_deploy_only],
        steps=[
            PipelineStep(tool="deploy_to_ionos", build_args=_build_deploy_args),
        ],
        extract_extras=_extract_deploy_extras,
    ),
]


# ---------------------------------------------------------------------------
# Moteur d'exécution
# ---------------------------------------------------------------------------

def match_pipeline(query: str) -> Optional[Pipeline]:
    """Retourne le premier pipeline dont un matcher est positif, ou None."""
    for pipe in PIPELINES:
        for matcher in pipe.matchers:
            try:
                if matcher(query):
                    return pipe
            except Exception:
                continue
    return None


async def run_pipeline(
    pipeline: Pipeline,
    query: str,
    tool_registry,  # ToolRegistry
    *,
    plan_callback: Optional[Callable[[List[Dict], str], None]] = None,
) -> PipelineResult:
    """Exécute un pipeline séquentiellement.

    Args:
        pipeline: Le pipeline sélectionné.
        query: Requête utilisateur brute.
        tool_registry: ToolRegistry pour exécuter les outils.
        plan_callback: Optionnel — émet l'état du plan (pour le SSE frontend).

    Returns:
        PipelineResult
    """
    logger.info("[pipeline] Exécution '{}' ({} steps)", pipeline.name, len(pipeline.steps))

    # ── 1. Résolution du workspace ──
    ctx: Dict[str, Any] = {"original_query": query}
    if pipeline.pre_resolve:
        try:
            from ..utils.project_registry import resolve_workspace
            ws = resolve_workspace(query, allow_create=False)
            if ws.path:
                ctx["project_dir"] = ws.path
                logger.info("[pipeline] Workspace résolu: {} (conf={:.2f})", ws.path, ws.confidence)
        except Exception as e:
            logger.debug("[pipeline] resolve_workspace échoué: {}", e)

    # ── 2. Extraction extras (domaine IONOS, etc.) ──
    if pipeline.extract_extras:
        try:
            extras = pipeline.extract_extras(query)
            ctx.update(extras)
        except Exception:
            pass

    # ── 3. Émission du plan initial (SSE) ──
    plan_items = [
        {"id": idx + 1, "title": step.tool, "status": "not-started"}
        for idx, step in enumerate(pipeline.steps)
    ]
    if plan_callback:
        try:
            plan_callback(plan_items, "")
        except Exception:
            pass

    # ── 4. Exécution séquentielle ──
    results: List[str] = []
    steps_done = 0

    for idx, step in enumerate(pipeline.steps):
        # Vérifier que le projet a été résolu si l'outil en a besoin
        if not ctx.get("project_dir") and step.tool in ("edit_website", "deploy_to_ionos"):
            if step.optional:
                logger.info("[pipeline] Step {}/{} '{}' skipped (pas de project_dir)", idx + 1, len(pipeline.steps), step.tool)
                continue
            return PipelineResult(
                success=False,
                message="❌ Aucun projet résolu pour cette requête.",
                pipeline_name=pipeline.name,
                steps_executed=steps_done,
            )

        # Construire les args
        try:
            args = step.build_args(ctx)
        except Exception as e:
            if step.optional:
                continue
            return PipelineResult(
                success=False,
                message=f"❌ Erreur construction args pour '{step.tool}': {e}",
                pipeline_name=pipeline.name,
                steps_executed=steps_done,
            )

        # Émettre step in-progress
        if plan_callback:
            plan_items[idx]["status"] = "in-progress"
            try:
                plan_callback(plan_items, step.tool)
            except Exception:
                pass

        logger.info("[pipeline] Step {}/{}: {} args={}", idx + 1, len(pipeline.steps), step.tool, list(args.keys()))

        # Exécuter l'outil
        try:
            obs = await tool_registry.execute(step.tool, args)
        except Exception as e:
            if step.optional:
                logger.warning("[pipeline] Step optionnelle '{}' échouée: {}", step.tool, e)
                plan_items[idx]["status"] = "completed"
                continue
            return PipelineResult(
                success=False,
                message=f"❌ Erreur '{step.tool}': {e}",
                pipeline_name=pipeline.name,
                steps_executed=steps_done,
            )

        if not obs.success and not step.optional:
            return PipelineResult(
                success=False,
                message=f"❌ '{step.tool}' a échoué:\n{obs.content}",
                pipeline_name=pipeline.name,
                steps_executed=steps_done,
            )

        results.append(obs.content)
        steps_done += 1

        # Émettre step completed
        plan_items[idx]["status"] = "completed"
        if plan_callback:
            try:
                plan_callback(plan_items, "")
            except Exception:
                pass

    # ── 5. Synthèse finale ──
    final_message = "\n\n".join(results)
    logger.info("[pipeline] '{}' terminé: {}/{} steps OK", pipeline.name, steps_done, len(pipeline.steps))
    return PipelineResult(
        success=True,
        message=final_message,
        pipeline_name=pipeline.name,
        steps_executed=steps_done,
    )
