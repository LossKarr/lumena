"""
remotion.py — Handlers V3 pour la génération vidéo via Remotion.

7 outils: generate_video, edit_video, preview_video, retry_video_render,
list_video_projects, get_video_job, cancel_video_job

Pattern: async (ctx: HandlerContext, **kwargs) -> HandlerResult

V3 — Système auto-améliorant:
  - Boucle itérative self-repair (validation TSX → correction → retry)
  - Adaptation au modèle (small/medium/large)
  - Mémoire vidéo persistante (leçons erreurs + patterns succès)
  - Error parser intelligent pour erreurs de rendu
  - Télémétrie complète
  - Fallback simplification forcée pour petits modèles
"""

from __future__ import annotations

import json
import os
import re as _re
import time
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

try:
    from ...tools.remotion_engine import (
        VIDEO_TEMPLATES,
        select_template,
        scaffold_remotion_project,
        write_scene_files,
        render_video_in_docker,
        resolve_asset_paths,
        copy_assets_to_project,
        build_assets_prompt_section,
        auto_detect_recent_assets,
        resolve_video_project_path,
        VideoCancelledError,
        VideoInfrastructureError,
        VideoRenderError,
    )
    from ...tools.remotion_spec import (
        choose_generation_mode,
        compile_safe_video,
        normalize_video_spec,
    )
    from ...tools.remotion_quality import inspect_rendered_video
    from ...tools.remotion_jobs import (
        VideoJobTracker,
        clear_video_cancel,
        read_video_job,
        request_video_cancel,
    )
    from ...tools.remotion_prompts import (
        VIDEO_PLAN_SYSTEM,
        SCENE_COMPONENT_SYSTEM,
        VIDEO_SEQUENCER_PROMPT,
        build_plan_prompt,
        build_scene_prompt,
        build_fix_prompt,
        build_simplify_prompt,
        build_render_fix_prompt,
        RENDER_FIX_SYSTEM,
    )
    from ...tools.remotion_validator import (
        validate_project,
        validate_single_component,
        attempt_auto_fix,
        parse_render_errors,
        format_errors_for_llm,
    )
    from ...learning.video_memory import (
        get_video_reflexion_store,
        get_video_success_store,
        classify_model_family,
        enrich_prompt_with_memory,
        VideoTelemetry,
    )
    REMOTION_AVAILABLE = True
except ImportError as _import_err:
    logger.debug(f"[video] Import partiel: {_import_err}")
    REMOTION_AVAILABLE = False


# ── Constantes de la boucle self-repair ───────────────────────────────────────

_MAX_TSX_RETRIES = 3          # Tentatives par scène
_MAX_RENDER_RETRIES = 3       # Tentatives de rendu
_MAX_PLAN_RETRIES = 2         # Tentatives de planification


# ── P3.1 — generate_video_handler ──────────────────────────────────

async def generate_video_handler(
    ctx: HandlerContext,
    description: str = "",
    template: str = "auto",
    duration_sec: int = 30,
    format: str = "landscape",
    output_name: str = "",
    assets: str = "",
    creation_mode: str = "auto",
) -> HandlerResult:
    """Génère une vidéo complète à partir d'une description textuelle.

    Pipeline V3 (auto-améliorant):
      Phase 0: Résolution assets + classification modèle
      Phase 1: LLM planifie les scènes (JSON) — enrichi par mémoire vidéo
      Phase 2: LLM génère chaque composant TSX — boucle self-repair itérative
      Phase 3: Scaffold projet Remotion (fichiers fixes)
      Phase 4: Rendu Docker → MP4 — boucle error-parse-fix-retry
      Phase 5: Apprentissage (succès → VideoSuccess, échec → VideoReflexion)
      Phase 6: Résultat + télémétrie
    """
    _t0 = time.time()
    telemetry = VideoTelemetry(description=description[:200])

    if not description:
        return HandlerResult.fail(
            "❌ Paramètre 'description' requis pour générer une vidéo.",
            handler_name="generate_video",
        )

    if not REMOTION_AVAILABLE:
        return HandlerResult.fail(
            "❌ Module remotion_engine non disponible. Vérifier l'installation.",
            handler_name="generate_video",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 0 : Sélection template + dimensions + classification modèle
    # ══════════════════════════════════════════════════════════════════════════

    if template == "auto":
        tpl_name, tpl = select_template(description)
    elif template in VIDEO_TEMPLATES:
        tpl_name, tpl = template, VIDEO_TEMPLATES[template]
    else:
        return HandlerResult.fail(
            f"❌ Template inconnu: {template}. Valides: {', '.join(VIDEO_TEMPLATES.keys())}",
            handler_name="generate_video",
        )

    # Override dimensions selon format
    if format == "portrait":
        tpl = {**tpl, "width": 1080, "height": 1920}
    elif format == "square":
        tpl = {**tpl, "width": 1080, "height": 1080}

    tpl["duration_sec"] = duration_sec
    total_frames = tpl["fps"] * duration_sec
    telemetry.template_type = tpl_name

    # ── Résolution des assets utilisateur ──
    from ...utils.paths import WORKSPACE_DIR
    import datetime
    import uuid

    slug = (output_name or "video").lower()
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:50]
    date_str = datetime.date.today().isoformat()
    _job_suffix = f"{datetime.datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    project_dir = WORKSPACE_DIR / date_str / f"{slug}-{_job_suffix}"
    project_dir.mkdir(parents=True, exist_ok=True)
    job_tracker = VideoJobTracker(project_dir, _job_suffix)

    _asset_paths_raw: List[str] = []
    if assets:
        if assets.startswith("["):
            try:
                _asset_paths_raw = json.loads(assets)
            except json.JSONDecodeError:
                _asset_paths_raw = [a.strip() for a in _re.split(r"[,|;]", assets) if a.strip()]
        else:
            _asset_paths_raw = [a.strip() for a in _re.split(r"[,|;]", assets) if a.strip()]

    _resolved_assets = resolve_asset_paths(_asset_paths_raw)

    if not _resolved_assets and any(kw in description.lower() for kw in ("logo", "photo", "image", "mon ")):
        _auto = auto_detect_recent_assets(max_age_hours=2)
        if _auto:
            logger.info("[video] {} asset(s) uploadé(s) récemment détecté(s)", len(_auto))
            _resolved_assets = _auto

    _assets_map: Dict[str, str] = {}
    if _resolved_assets:
        logger.info("[video] Phase 0 — Copie {} asset(s) vers public/...", len(_resolved_assets))
        _assets_map = copy_assets_to_project(project_dir, _resolved_assets)

    _has_assets = bool(_assets_map)
    _assets_prompt = build_assets_prompt_section(_assets_map)

    # ── Classification du modèle ──
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        job_tracker.update("failed", 100, "Aucun LLM disponible", status="failed")
        return HandlerResult.fail(
            "❌ Pas de LLM disponible pour générer la vidéo.",
            handler_name="generate_video",
        )
    llm = lumena.llm

    _current_model = getattr(llm, "model", "") or ""
    _code_model: str | None = None
    _effective_model = _current_model
    _model_family = classify_model_family(_effective_model)
    telemetry.model = _effective_model

    logger.info("[video] Modèle: {} (famille: {})", _effective_model, _model_family)

    # Budget tokens adapté
    from ...llm.providers import get_model_config
    _model_cfg = get_model_config(_effective_model)
    _model_cap = _model_cfg.max_output_tokens if _model_cfg else 8192
    if _model_cap < 4096:
        job_tracker.update(
            "failed",
            100,
            f"Budget de sortie insuffisant: {_model_cap} tokens",
            status="failed",
        )
        return HandlerResult.fail(
            f"❌ Le modèle '{_effective_model}' a un max_output trop faible ({_model_cap} tokens).",
            handler_name="generate_video",
        )
    _plan_tokens = min(8000, _model_cap)
    _scene_tokens = min(16000, _model_cap)
    _sequencer_tokens = min(8000, _model_cap)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1 : Planification des scènes (LLM + mémoire vidéo)
    # ══════════════════════════════════════════════════════════════════════════

    _t_plan = time.time()
    job_tracker.ensure_active()
    job_tracker.update("planning", 10, "Planification des scènes")
    logger.info("[video] Phase 1 — Planification ({} sec, template: {})...", duration_sec, tpl_name)

    # Enrichir avec mémoire vidéo
    _memory_block = enrich_prompt_with_memory(
        "", description, _effective_model, tpl_name
    )
    if _memory_block:
        telemetry.reflexions_applied += 1

    plan_prompt = build_plan_prompt(
        description=description,
        template_name=tpl_name,
        duration_sec=duration_sec,
        width=tpl["width"],
        height=tpl["height"],
        fps=tpl["fps"],
        suggested_scenes=", ".join(tpl["scenes"]) if tpl["scenes"] else "libre",
        total_frames=total_frames,
        model_family=_model_family,
        assets_section=_assets_prompt,
        memory_block=_memory_block,
    )

    _plan_messages = [
        {"role": "system", "content": VIDEO_PLAN_SYSTEM},
        {"role": "user", "content": plan_prompt},
    ]

    plan: dict | None = None
    for _plan_attempt in range(1, _MAX_PLAN_RETRIES + 1):
        _plan_max_tokens = _plan_tokens if _plan_attempt == 1 else min(16000, _model_cap)
        plan_response = await llm.chat(
            messages=_plan_messages,
            temperature=0.5 + (_plan_attempt - 1) * 0.1,
            max_tokens=_plan_max_tokens,
            stop=["OBSERVATION:"],
            model=_code_model,
        )

        try:
            plan = json.loads(plan_response)
            break
        except json.JSONDecodeError:
            pass

        json_match = _re.search(r'\{[\s\S]*\}', plan_response)
        if json_match:
            try:
                plan = json.loads(json_match.group())
                break
            except json.JSONDecodeError:
                logger.warning("[video] JSON plan malformé (tentative {}/{})...", _plan_attempt, _MAX_PLAN_RETRIES)

        if _plan_attempt < _MAX_PLAN_RETRIES:
            _plan_messages.append({"role": "assistant", "content": plan_response})
            _plan_messages.append({"role": "user", "content": "Le JSON retourné est invalide. Retourne UNIQUEMENT un objet JSON valide, sans backticks, sans texte autour."})

    if not plan:
        _learn_reflexion("plan", "LLM n'a pas produit de JSON valide", "Toujours valider la structure JSON du plan avant de continuer", tpl_name, _model_family)
        job_tracker.update("failed", 100, "Plan JSON invalide", status="failed")
        return HandlerResult.fail(
            "❌ Le LLM n'a pas retourné un JSON valide pour le plan vidéo.",
            handler_name="generate_video",
        )

    try:
        video_spec = normalize_video_spec(
            plan,
            total_frames=total_frames,
            fps=tpl["fps"],
            width=tpl["width"],
            height=tpl["height"],
        )
        plan = video_spec.to_dict()
        _last_meta = llm.get_last_response_meta() if hasattr(llm, "get_last_response_meta") else {}
        if isinstance(_last_meta, dict) and _last_meta.get("model_used"):
            _effective_model = str(_last_meta["model_used"])
            _model_family = classify_model_family(_effective_model)
            telemetry.model = _effective_model
        _generation_mode = choose_generation_mode(creation_mode, _model_family)
    except (TypeError, ValueError) as spec_error:
        _learn_reflexion(
            "plan",
            str(spec_error),
            "Produire un plan conforme au VideoSpec avant de générer du TSX.",
            tpl_name,
            _model_family,
        )
        job_tracker.update("failed", 100, f"VideoSpec invalide: {spec_error}", status="failed")
        return HandlerResult.fail(
            f"❌ Plan vidéo invalide: {spec_error}",
            handler_name="generate_video",
        )

    scenes = plan["scenes"]
    telemetry.generation_mode = _generation_mode
    logger.info(
        "[video] VideoSpec v{} validé — mode {} — modèle réel {}",
        video_spec.schema_version,
        _generation_mode,
        _effective_model,
    )

    telemetry.scenes_count = len(scenes)
    telemetry.planning_duration_s = time.time() - _t_plan
    logger.info("[video] ✅ Plan: {} scènes, {} frames @ {}fps ({:.1f}s)",
                len(scenes), total_frames, tpl["fps"], telemetry.planning_duration_s)
    job_tracker.update("planning", 30, f"VideoSpec validé: {len(scenes)} scènes")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 : Génération TSX itérative avec self-repair
    # ══════════════════════════════════════════════════════════════════════════

    _t_tsx = time.time()
    job_tracker.ensure_active()
    job_tracker.update("composition", 35, f"Composition { _generation_mode }")
    if _generation_mode == "safe":
        scenes_code = compile_safe_video(video_spec)
        telemetry.tsx_generation_duration_s = time.time() - _t_tsx
        logger.info(
            "[video] ✅ Phase 2 compilée par le rail sûr ({} scènes, {:.1f}s)",
            len(video_spec.scenes),
            telemetry.tsx_generation_duration_s,
        )
    else:
        scenes_code: Dict[str, str] = {}
        _n_scenes = len(scenes)
        logger.info("[video] Phase 2 — Génération TSX ({} composants, self-repair activé)...", _n_scenes)

        for _scene_idx, scene in enumerate(scenes, start=1):
            component_name = scene.get("component_name", scene["id"].title() + "Scene")
            logger.info("[video] Scène {}/{}: {}...", _scene_idx, _n_scenes, component_name)

            # Construire prompt adapté au modèle + enrichi par mémoire
            _scene_memory = enrich_prompt_with_memory(
                "", f"{description} {component_name} {scene.get('text_title', '')}",
                _effective_model, tpl_name
            )
            scene_prompt = build_scene_prompt(
                scene_json=json.dumps(scene, ensure_ascii=False, indent=2),
                palette_json=json.dumps(plan.get("palette", {}), ensure_ascii=False),
                font_family=plan.get("font_family", "Inter"),
                width=tpl["width"],
                height=tpl["height"],
                fps=tpl["fps"],
                component_name=component_name,
                model_family=_model_family,
                has_assets=_has_assets,
                memory_block=_scene_memory,
            )

            code = await _generate_scene_with_repair(
                llm=llm,
                scene_prompt=scene_prompt,
                scene_json=json.dumps(scene, ensure_ascii=False, indent=2),
                palette_json=json.dumps(plan.get("palette", {}), ensure_ascii=False),
                component_name=component_name,
                model_family=_model_family,
                code_model=_code_model,
                scene_tokens=_scene_tokens,
                has_assets=_has_assets,
                tpl=tpl,
                telemetry=telemetry,
            )

            # Un asset inventé ne doit jamais devenir un accès réseau silencieux.
            # Si le modèle utilise staticFile sans manifeste, on publie un composant
            # déterministe sûr plutôt qu'une URL externe non autorisée.
            if not _assets_map and "staticFile" in code:
                logger.warning("[video] staticFile sans asset dans {}; fallback sûr", component_name)
                code = _fallback_minimal_component(component_name, tpl)
                telemetry.auto_fixes_applied += 1

            scenes_code[f"src/scenes/{component_name}.tsx"] = code
            logger.info("[video] ✅ {}.tsx ({} chars)", component_name, len(code))

        # Générer Video.tsx (séquenceur)
        logger.info("[video] Assemblage Video.tsx...")
        scenes_list = "\n".join(
            f"- {s['component_name']} ({s['duration_frames']} frames, from={sum(sc['duration_frames'] for sc in scenes[:i])})"
            for i, s in enumerate(scenes)
        )
        video_tsx = await llm.chat(
            messages=[
                {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
                {"role": "user", "content": VIDEO_SEQUENCER_PROMPT.format(
                    scenes_list=scenes_list,
                    total_frames=total_frames,
                )},
            ],
            temperature=0.3,
            max_tokens=_sequencer_tokens,
            model=_code_model,
        )
        video_tsx_code = _strip_markdown_fences(video_tsx)

        if not any(m in video_tsx_code for m in ("import ", "export ", "Sequence")):
            logger.warning("[video] ⚠️ Video.tsx invalide, retry...")
            video_tsx = await llm.chat(
                messages=[
                    {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
                    {"role": "user", "content": VIDEO_SEQUENCER_PROMPT.format(
                        scenes_list=scenes_list, total_frames=total_frames,
                    )},
                ],
                temperature=0.5, max_tokens=_sequencer_tokens, model=_code_model,
            )
            video_tsx_code = _strip_markdown_fences(video_tsx)

        # Filet déterministe : les scènes utilisent `export default` (cf prompt),
        # donc Video.tsx DOIT les importer en default. Un import named `{ X }`
        # résout à `undefined` → React error #130 au rendu. On normalise chaque
        # import de scène vers la forme default, quel que soit ce que le LLM a généré.
        import re as _re_imp
        for _sc in scenes:
            _cn = _sc.get("component_name", "")
            if not _cn:
                continue
            video_tsx_code = _re_imp.sub(
                r"import\s*\{\s*" + _re_imp.escape(_cn) + r"\s*\}\s*from\s*"
                r"(['\"])([^'\"]*scenes/" + _re_imp.escape(_cn) + r")\1",
                r"import " + _cn + r" from \1\2\1",
                video_tsx_code,
            )
        scenes_code["src/Video.tsx"] = video_tsx_code

        telemetry.tsx_generation_duration_s = time.time() - _t_tsx
        logger.info("[video] ✅ Phase 2 terminée ({:.1f}s)", telemetry.tsx_generation_duration_s)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 3 : Scaffold projet + écriture fichiers
    # ══════════════════════════════════════════════════════════════════════════

    logger.info("[video] Phase 3 — Scaffold projet Remotion...")
    job_tracker.ensure_active()
    job_tracker.update("scaffold", 62, "Construction du projet Remotion")
    scaffold_files = scaffold_remotion_project(
        output_dir=project_dir, template=tpl, composition_id="Main",
    )
    scaffold_files["lumena-video.json"] = json.dumps(
        {
            "video_spec": video_spec.to_dict(),
            "generation": {
                "mode": _generation_mode,
                "model": _effective_model,
                "template": tpl_name,
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    for rel_path, content in scaffold_files.items():
        fp = project_dir / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    write_scene_files(project_dir, scenes_code)

    # ── Validation pré-rendu ──
    _validation = validate_project(
        project_dir=str(project_dir),
        expected_total_frames=total_frames,
        has_assets=_has_assets,
    )
    if not _validation.valid:
        logger.warning("[video] ⚠️ Validation pré-rendu: {}", _validation.summary())
        telemetry.tsx_validation_failures += _validation.errors_count
        _learn_reflexion(
            "validation",
            _validation.errors_for_llm(),
            "Corriger toutes les erreurs de validation avant tout rendu.",
            tpl_name,
            _model_family,
        )
        job_tracker.update(
            "failed",
            100,
            _validation.summary()[:500],
            status="failed",
        )
        return HandlerResult.fail(
            "❌ Projet Remotion invalide avant rendu.\n"
            f"{_validation.summary()}\n"
            f"Projet conservé pour diagnostic: `{project_dir}`",
            handler_name="generate_video",
        )

    logger.info("[video] ✅ Projet écrit dans {}", project_dir.name)
    job_tracker.update("validation", 70, "Projet validé avant rendu")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 4 : Rendu Docker/local — boucle error-parse-fix-retry
    # ══════════════════════════════════════════════════════════════════════════

    _t_render = time.time()
    job_tracker.ensure_active()
    job_tracker.update("render", 75, "Rendu vidéo isolé")
    logger.info("[video] Phase 4 — Rendu MP4 ({}×{} @ {}fps)...", tpl["width"], tpl["height"], tpl["fps"])

    video_path: Optional[Path] = None
    _infrastructure_failure = False
    _render_timeout = int(os.getenv("LUMENA_VIDEO_RENDER_TIMEOUT", "300"))

    for _render_attempt in range(1, _MAX_RENDER_RETRIES + 1):
        telemetry.render_attempts += 1
        try:
            video_path, render_log = await render_video_in_docker(
                project_dir=project_dir,
                timeout_sec=_render_timeout,
            )
            logger.info("[video] ✅ Rendu réussi (tentative {})", _render_attempt)
            break
        except VideoCancelledError as e:
            _err_str = str(e)
            telemetry.render_errors.append(_err_str[:300])
            telemetry.total_duration_s = time.time() - _t0
            telemetry.failure_reason = _err_str
            job_tracker.update(
                "cancelled",
                job_tracker.job.progress,
                _err_str[:500],
                status="cancelled",
            )
            return HandlerResult.fail(
                f"⏹️ Génération vidéo annulée. Projet conservé: `{project_dir}`",
                handler_name="generate_video",
            )
        except VideoInfrastructureError as e:
            _err_str = str(e)
            _infrastructure_failure = True
            logger.warning("[video] Infrastructure de rendu indisponible: {}", _err_str[:300])
            telemetry.render_errors.append(_err_str[:300])
            break
        except VideoRenderError as e:
            _err_str = str(e)
            logger.warning("[video] ❌ Rendu échoué (tentative {}/{}): {}",
                           _render_attempt, _MAX_RENDER_RETRIES, _err_str[:200])
            telemetry.render_errors.append(_err_str[:300])

            if _render_attempt >= _MAX_RENDER_RETRIES:
                break

            # ── Error parsing + correction itérative ──
            _fixed = await _attempt_render_fix(
                llm=llm,
                project_dir=project_dir,
                error_str=_err_str,
                scenes=[s["component_name"] for s in scenes],
                total_frames=total_frames,
                tpl=tpl,
                code_model=_code_model,
                scene_tokens=_scene_tokens,
            )
            if _fixed:
                telemetry.auto_fixes_applied += 1
                post_fix_validation = validate_project(
                    project_dir=str(project_dir),
                    expected_total_frames=total_frames,
                    has_assets=_has_assets,
                )
                if not post_fix_validation.valid:
                    telemetry.render_errors.append(post_fix_validation.errors_for_llm()[:300])
                    logger.warning(
                        "[video] Correction refusée par la validation: {}",
                        post_fix_validation.summary(),
                    )
                    break
                logger.info("[video] 🔧 Correction validée, re-tentative rendu...")
            else:
                logger.warning("[video] Pas de correction trouvée, retry brut...")

    telemetry.render_duration_s = time.time() - _t_render

    quality_report = None
    if video_path and video_path.exists():
        job_tracker.ensure_active()
        job_tracker.update("quality", 92, "Contrôle de l'artefact vidéo")
        quality_report = await inspect_rendered_video(
            video_path,
            project_dir=project_dir,
            expected_duration_sec=float(duration_sec),
            expected_width=tpl["width"],
            expected_height=tpl["height"],
        )
        if not quality_report.passed:
            _quality_error = quality_report.summary()
            telemetry.render_errors.append(_quality_error)
            logger.warning("[video] Contrôle qualité refusé: {}", _quality_error)
            video_path = None

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 5 : Apprentissage (succès ou échec)
    # ══════════════════════════════════════════════════════════════════════════

    telemetry.total_duration_s = time.time() - _t0

    if video_path and video_path.exists():
        telemetry.success = True
        telemetry.output_path = str(video_path)
        _learn_success(
            template_type=tpl_name,
            description=description,
            approach=f"{len(scenes)} scènes, {telemetry.render_attempts} render attempts, modèle {_model_family}",
            scenes_count=len(scenes),
            model=_effective_model,
            model_family=_model_family,
            iterations=telemetry.tsx_generation_attempts,
        )
        job_tracker.update(
            "complete",
            100,
            "Vidéo rendue et contrôlée",
            status="complete",
            output_path=str(video_path),
        )
    else:
        telemetry.success = False
        telemetry.failure_reason = telemetry.render_errors[-1] if telemetry.render_errors else "unknown"
        # Une panne Docker, réseau ou npm n'enseigne rien sur la qualité du TSX.
        # L'enregistrer comme leçon créative ferait répéter une mauvaise
        # réparation lors des générations suivantes.
        if not _infrastructure_failure:
            _learn_reflexion(
                error_type="render",
                trigger=telemetry.failure_reason[:200],
                lesson=(
                    f"Rendu échoué après {telemetry.render_attempts} tentative(s) "
                    f"pour template={tpl_name}, modèle={_model_family}. "
                    f"Erreur: {telemetry.failure_reason[:100]}"
                ),
                template_type=tpl_name,
                model_family=_model_family,
            )
        job_tracker.update(
            "failed",
            100,
            telemetry.failure_reason[:500],
            status="failed",
        )
        return HandlerResult.fail(
            f"❌ {'Infrastructure vidéo indisponible' if _infrastructure_failure else 'Rendu vidéo échoué'} "
            f"après {telemetry.render_attempts} tentative(s).\n\n"
            f"**Dernière erreur**: {telemetry.failure_reason[:300]}\n"
            f"**Projet sauvegardé**: `workspace/{project_dir.relative_to(WORKSPACE_DIR)}`\n\n"
            + (
                "Relance le rendu après correction de Docker ou du réseau ; le code vidéo n'a pas été modifié."
                if _infrastructure_failure
                else "Tu peux corriger manuellement avec `edit_video` puis `preview_video`."
            ),
            handler_name="generate_video",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 6 : Résultat + télémétrie
    # ══════════════════════════════════════════════════════════════════════════

    rel_video = video_path.relative_to(WORKSPACE_DIR)
    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    logger.info("[video] {}", telemetry.summary())

    _assets_info = f"**Assets intégrés**: {', '.join(_assets_map.keys())}\n" if _assets_map else ""
    _learning_info = ""
    if telemetry.reflexions_applied or telemetry.auto_fixes_applied:
        _learning_info = f"**Apprentissage**: {telemetry.reflexions_applied} leçon(s) appliquée(s), {telemetry.auto_fixes_applied} auto-fix(s)\n"

    return HandlerResult.ok(
        f"✅ Vidéo générée avec succès!\n\n"
        f"**Fichier**: `workspace/{rel_video}`\n"
        f"**Taille**: {file_size_mb:.1f} Mo\n"
        f"**Durée**: {duration_sec}s @ {tpl['fps']}fps\n"
        f"**Résolution**: {tpl['width']}×{tpl['height']}\n"
        f"**Scènes**: {len(scenes)}\n"
        f"**Mode de création**: {_generation_mode}\n"
        f"**Modèle**: {_effective_model} ({_model_family})\n"
        f"**Contrôle qualité**: {quality_report.summary() if quality_report else 'non disponible'}\n"
        f"**Temps total**: {telemetry.total_duration_s:.1f}s (plan: {telemetry.planning_duration_s:.1f}s, TSX: {telemetry.tsx_generation_duration_s:.1f}s, rendu: {telemetry.render_duration_s:.1f}s)\n"
        f"{_assets_info}{_learning_info}"
        f"**Projet Remotion**: `workspace/{project_dir.relative_to(WORKSPACE_DIR)}`\n\n"
        f"Tu peux modifier la vidéo avec `edit_video` ou la prévisualiser avec `preview_video`.",
        handler_name="generate_video",
    )


def _strip_markdown_fences(code: str) -> str:
    """Retire les ```tsx / ``` du code LLM si présents."""
    code = code.strip()
    if code.startswith("```"):
        first_newline = code.index("\n") if "\n" in code else len(code)
        code = code[first_newline + 1:]
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()


# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS SELF-REPAIR + LEARNING
# ══════════════════════════════════════════════════════════════════════════════


async def _generate_scene_with_repair(
    llm: Any,
    scene_prompt: str,
    scene_json: str,
    palette_json: str,
    component_name: str,
    model_family: str,
    code_model: Optional[str],
    scene_tokens: int,
    has_assets: bool,
    tpl: Dict[str, Any],
    telemetry: "VideoTelemetry",
) -> str:
    """Génère un composant TSX avec boucle de validation/correction itérative.

    Stratégie:
      1. Génération initiale
      2. Validation pré-rendu
      3. Si erreurs: auto-fix + retry LLM avec injection erreurs
      4. Si 3 échecs: simplification forcée (fallback minimal)
    """
    from ...tools.remotion_prompts import build_fix_prompt, build_simplify_prompt

    _scene_msgs = [
        {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
        {"role": "user", "content": scene_prompt},
    ]

    best_code = ""
    for attempt in range(1, _MAX_TSX_RETRIES + 1):
        telemetry.tsx_generation_attempts += 1
        temp = 0.3 + (attempt - 1) * 0.15  # Augmenter température progressive

        code = await llm.chat(
            messages=_scene_msgs,
            temperature=temp,
            max_tokens=scene_tokens,
            model=code_model,
        )
        code = _strip_markdown_fences(code)

        # Vérification basique: est-ce du TSX?
        if not any(marker in code for marker in ("import ", "export ", "React")):
            logger.warning("[video] ⚠️ {} — pas de TSX (tentative {})", component_name, attempt)
            if attempt < _MAX_TSX_RETRIES:
                _scene_msgs.append({"role": "assistant", "content": code})
                _scene_msgs.append({"role": "user", "content": "Ce n'est pas du code TSX valide. Génère UNIQUEMENT le code du composant React/Remotion, sans texte, sans markdown."})
                continue
            # Dernier recours: simplification
            break

        # Validation structurelle
        validation = validate_single_component(code, f"{component_name}.tsx", has_assets)

        if validation.valid:
            logger.info("[video] ✅ {} validé (tentative {})", component_name, attempt)
            return code

        # Auto-fix des problèmes simples
        fixed_code, fixes = attempt_auto_fix(code, validation.issues)
        if fixes:
            telemetry.auto_fixes_applied += len(fixes)
            logger.info("[video] 🔧 {} — auto-fix: {}", component_name, ", ".join(fixes))
            # Re-valider après auto-fix
            re_validation = validate_single_component(fixed_code, f"{component_name}.tsx", has_assets)
            if re_validation.valid:
                return fixed_code
            code = fixed_code  # Garder les fixes partiels

        best_code = code

        # Si encore des erreurs et tentatives restantes: demander correction au LLM
        if attempt < _MAX_TSX_RETRIES:
            error_desc = validation.errors_for_llm()
            fix_prompt = build_fix_prompt(code, error_desc)
            _scene_msgs = [
                {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
                {"role": "user", "content": fix_prompt},
            ]
            logger.warning("[video] ⚠️ {} — {} erreur(s), correction itérative...", component_name, validation.errors_count)
            telemetry.tsx_validation_failures += 1

            # Apprendre de l'erreur
            _learn_reflexion(
                error_type="tsx_validation",
                trigger=f"Composant {component_name} invalide: {error_desc[:100]}",
                lesson=f"Erreur fréquente sur {component_name}: {validation.issues[0].message if validation.issues else 'unknown'}",
                template_type="",
                model_family=model_family,
            )

    # ── Fallback: simplification forcée (dernier recours) ──
    if model_family == "small" or (best_code and not any(m in best_code for m in ("export ", "import "))):
        logger.warning("[video] ⚠️ {} — simplification forcée (fallback)", component_name)
        # Extraire couleur de fond depuis palette
        try:
            palette = json.loads(palette_json) if isinstance(palette_json, str) else palette_json
            bg = f"linear-gradient(135deg, {palette.get('primary', '#667eea')}, {palette.get('secondary', '#764ba2')})"
            text_color = palette.get("text", "#ffffff")
        except Exception:
            bg = "linear-gradient(135deg, #667eea, #764ba2)"
            text_color = "#ffffff"

        simplify_prompt = build_simplify_prompt(
            scene_json=scene_json,
            palette_json=palette_json,
            width=tpl["width"],
            height=tpl["height"],
            fps=tpl["fps"],
            component_name=component_name,
            bg_gradient=bg,
            text_color=text_color,
        )
        code = await llm.chat(
            messages=[
                {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
                {"role": "user", "content": simplify_prompt},
            ],
            temperature=0.2,
            max_tokens=scene_tokens,
            model=code_model,
        )
        code = _strip_markdown_fences(code)
        if any(m in code for m in ("export ", "import ")):
            return code

    # Si tout échoue, retourner le meilleur code obtenu
    return best_code or _fallback_minimal_component(component_name, tpl)


def _fallback_minimal_component(component_name: str, tpl: Dict[str, Any]) -> str:
    """Composant TSX minimal garanti fonctionnel (ultime fallback)."""
    return f"""import {{ useCurrentFrame, interpolate, AbsoluteFill }} from 'remotion';

export default function {component_name}() {{
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 20], [0, 1], {{ extrapolateRight: 'clamp' }});
  return (
    <AbsoluteFill style={{{{ background: 'linear-gradient(135deg, #667eea, #764ba2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}}}>
      <h1 style={{{{ fontSize: 64, color: '#fff', fontFamily: 'Inter', opacity, textAlign: 'center' }}}}>
        {component_name.replace('Scene', '')}
      </h1>
    </AbsoluteFill>
  );
}}
"""


async def _attempt_render_fix(
    llm: Any,
    project_dir: Path,
    error_str: str,
    scenes: List[str],
    total_frames: int,
    tpl: Dict[str, Any],
    code_model: Optional[str],
    scene_tokens: int,
) -> bool:
    """Tente de corriger un projet après échec de rendu.

    1. Parse l'erreur pour identifier le fichier fautif
    2. Demande au LLM de corriger le fichier
    3. Écrit la correction sur disque

    Returns True si une correction a été appliquée.
    """
    try:
        errors = parse_render_errors(error_str)
        if not errors:
            return False

        # Identifier le fichier à corriger
        target_file = ""
        for err in errors:
            if err.file:
                target_file = err.file
                break

        if not target_file:
            # Heuristique: chercher le premier fichier TSX mentionné dans l'erreur
            tsx_match = _re.search(r'([A-Z]\w+(?:Scene)?\.tsx)', error_str)
            if tsx_match:
                target_file = tsx_match.group(1)

        if not target_file:
            # Si impossible d'identifier, cibler Video.tsx (souvent le coupable)
            target_file = "src/Video.tsx"

        # Résoudre le chemin absolu
        project_root = project_dir.resolve()
        file_path = (project_root / target_file).resolve()
        try:
            file_path.relative_to(project_root)
        except ValueError:
            logger.warning("[video] Correction refusée hors projet: {}", target_file)
            return False
        if not file_path.exists():
            # Chercher dans src/scenes/
            for candidate in project_dir.rglob(Path(target_file).name):
                resolved_candidate = candidate.resolve()
                try:
                    resolved_candidate.relative_to(project_root)
                except ValueError:
                    continue
                file_path = resolved_candidate
                break

        if not file_path.exists():
            return False

        file_content = file_path.read_text(encoding="utf-8", errors="replace")
        error_desc = format_errors_for_llm(errors)

        fix_prompt = build_render_fix_prompt(
            render_error=error_desc,
            filename=target_file,
            file_content=file_content,
            scene_names=scenes,
            total_frames=total_frames,
            width=tpl["width"],
            height=tpl["height"],
            fps=tpl["fps"],
        )

        fixed_code = await llm.chat(
            messages=[
                {"role": "system", "content": RENDER_FIX_SYSTEM},
                {"role": "user", "content": fix_prompt},
            ],
            temperature=0.2,
            max_tokens=scene_tokens,
            model=code_model,
        )
        fixed_code = _strip_markdown_fences(fixed_code)

        # Validation minimale
        if not any(m in fixed_code for m in ("import ", "export ")):
            return False

        # Écrire la correction
        file_path.write_text(fixed_code, encoding="utf-8")
        logger.info("[video] 🔧 Correction écrite: {}", file_path.name)
        return True

    except Exception as exc:
        logger.debug("[video] _attempt_render_fix failed: {}", exc)
        return False


def _learn_reflexion(
    error_type: str,
    trigger: str,
    lesson: str,
    template_type: str = "",
    model_family: str = "",
) -> None:
    """Enregistre une leçon d'erreur dans le VideoReflexionStore (best-effort)."""
    try:
        store = get_video_reflexion_store()
        store.add(
            error_type=error_type,
            trigger=trigger,
            lesson=lesson,
            apply_when=f"{error_type} {template_type} {model_family}",
            template_type=template_type,
            model_family=model_family,
            tags=[error_type, template_type, model_family],
        )
    except Exception as exc:
        logger.debug("[video] learn_reflexion failed: {}", exc)


def _learn_success(
    template_type: str,
    description: str,
    approach: str,
    scenes_count: int,
    model: str,
    model_family: str,
    iterations: int,
) -> None:
    """Enregistre un pattern de succès dans le VideoSuccessStore (best-effort)."""
    try:
        store = get_video_success_store()
        store.add(
            template_type=template_type,
            description_hint=description[:200],
            approach=approach,
            scenes_count=scenes_count,
            model_used=model,
            model_family=model_family,
            iterations_needed=iterations,
            tags=[template_type, model_family],
        )
    except Exception as exc:
        logger.debug("[video] learn_success failed: {}", exc)


# ── P3.2 — edit_video_handler ──────────────────────────────────────

async def edit_video_handler(
    ctx: HandlerContext,
    project_dir: str = "",
    instructions: str = "",
) -> HandlerResult:
    """Modifie un projet vidéo Remotion existant via CodeAgent."""
    if not instructions:
        return HandlerResult.fail(
            "❌ Paramètre 'instructions' requis.",
            handler_name="edit_video",
        )

    if not project_dir:
        from ...utils.paths import WORKSPACE_DIR
        candidates = sorted(
            [d for d in WORKSPACE_DIR.rglob("render.mjs") if d.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return HandlerResult.fail(
                "❌ Aucun projet vidéo trouvé. Précise project_dir.",
                handler_name="edit_video",
            )
        project_dir = str(candidates[0].parent)

    try:
        project_path = resolve_video_project_path(project_dir)
    except ValueError as path_error:
        return HandlerResult.fail(
            f"❌ Projet vidéo refusé: {path_error}",
            handler_name="edit_video",
        )

    try:
        from ...agents.sub_agent import delegate_to_agent

        # Enrichir la description avec le contexte Remotion pour le CodeAgent
        _remotion_ctx = (
            "CONTEXTE IMPORTANT — c'est un projet vidéo Remotion (React):\n"
            "- Les fichiers de scènes sont dans src/scenes/*.tsx\n"
            "- Le séquenceur est src/Video.tsx\n"
            "- Les animations utilisent useCurrentFrame(), interpolate(), spring() de 'remotion'\n"
            "- Chaque scène DOIT avoir export default et utiliser <AbsoluteFill>\n"
            "- PAS de useState/useEffect, PAS de CSS @keyframes, PAS de window/document\n"
            "- Après modification, valide que les imports sont cohérents\n\n"
        )

        output = await delegate_to_agent(
            description=f"{_remotion_ctx}Modifier le projet vidéo Remotion dans {project_dir}: {instructions}",
            agent_type="code",
            context={"workspace_path": str(project_path)},
        )

        if output and not output.startswith("❌"):
            validation = validate_project(str(project_path))
            if not validation.valid:
                return HandlerResult.fail(
                    "❌ Modification appliquée mais validation Remotion refusée.\n"
                    f"{validation.summary()}",
                    handler_name="edit_video",
                )
            rendered_path, _ = await render_video_in_docker(project_path, timeout_sec=120)
            return HandlerResult.ok(
                f"✅ Vidéo modifiée, validée et rendue.\n{output}\n\n"
                f"**Fichier**: `{rendered_path}`",
                handler_name="edit_video",
            )
        return HandlerResult.fail(
            f"❌ Modification échouée: {output}",
            handler_name="edit_video",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur edit_video: {e}",
            handler_name="edit_video",
        )


# ── P3.3 — preview_video_handler ───────────────────────────────────

async def preview_video_handler(
    ctx: HandlerContext,
    project_dir: str = "",
) -> HandlerResult:
    """Prévisualise un projet vidéo en effectuant un rendu rapide."""
    if not project_dir:
        from ...utils.paths import WORKSPACE_DIR
        candidates = sorted(
            [d for d in WORKSPACE_DIR.rglob("render.mjs") if d.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return HandlerResult.fail(
                "❌ Aucun projet vidéo trouvé.",
                handler_name="preview_video",
            )
        project_dir = str(candidates[0].parent)

    try:
        project_path = resolve_video_project_path(project_dir)
        validation = validate_project(str(project_path))
        if not validation.valid:
            return HandlerResult.fail(
                f"❌ Preview refusée: {validation.summary()}",
                handler_name="preview_video",
            )
        existing_output = project_path / "output.mp4"
        source_mtime = max(
            (path.stat().st_mtime for path in project_path.rglob("*.tsx")),
            default=0.0,
        )
        if existing_output.exists() and existing_output.stat().st_mtime >= source_mtime:
            video_path = existing_output
        else:
            video_path, _ = await render_video_in_docker(
                project_dir=project_path,
                timeout_sec=120,
            )
        preview_path = project_path / "preview.html"
        preview_path.write_text(
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(project_path.name)} — Lumena</title>"
            "<style>html,body{margin:0;height:100%;background:#070b12;color:#fff;font-family:system-ui}"
            "main{height:100%;display:grid;place-items:center}video{max-width:96vw;max-height:92vh;box-shadow:0 24px 80px #000;border-radius:12px}</style>"
            f"<main><video controls autoplay src='{escape(video_path.name)}'></video></main></html>",
            encoding="utf-8",
        )
        return HandlerResult.ok(
            f"✅ Prévisualisation prête: `{preview_path}`\n**Vidéo**: `{video_path}`",
            handler_name="preview_video",
        )
    except (RuntimeError, ValueError) as e:
        return HandlerResult.fail(
            f"❌ Preview échoué: {e}",
            handler_name="preview_video",
        )


def _video_manifest_expectations(project_path: Path) -> Tuple[Optional[int], float, int, int]:
    """Lit les dimensions attendues sans faire confiance à des chemins du manifeste."""
    manifest_path = project_path / "lumena-video.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = payload.get("video_spec", {})
        fps = max(1, int(spec.get("fps", 30)))
        total_frames = max(1, int(spec.get("total_frames", 1)))
        duration_sec = total_frames / fps
        width = max(1, int(spec.get("width", 1920)))
        height = max(1, int(spec.get("height", 1080)))
        return total_frames, duration_sec, width, height
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, 30.0, 1920, 1080


async def retry_video_render_handler(
    ctx: HandlerContext,
    project_dir: str = "",
) -> HandlerResult:
    """Reprend un rendu échoué sans redemander ni réécrire la création au LLM."""
    from ...utils.paths import WORKSPACE_DIR

    if not project_dir:
        candidates = sorted(
            WORKSPACE_DIR.rglob("render.mjs"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return HandlerResult.fail(
                "❌ Aucun projet vidéo à reprendre.",
                handler_name="retry_video_render",
            )
        project_dir = str(candidates[0].parent)

    try:
        project = resolve_video_project_path(project_dir)
    except ValueError as exc:
        return HandlerResult.fail(
            f"❌ Reprise refusée: {exc}",
            handler_name="retry_video_render",
        )

    clear_video_cancel(project)
    tracker = VideoJobTracker(project, f"retry-{int(time.time())}")
    total_frames, duration_sec, width, height = _video_manifest_expectations(project)
    validation = validate_project(
        str(project),
        expected_total_frames=total_frames,
    )
    if not validation.valid:
        tracker.update("failed", 100, validation.summary(), status="failed")
        return HandlerResult.fail(
            f"❌ Reprise refusée par la validation: {validation.summary()}",
            handler_name="retry_video_render",
        )

    try:
        tracker.update("render", 55, "Reprise du rendu vidéo isolé")
        output = project / "output.mp4"
        source_files = list((project / "src").rglob("*.tsx"))
        source_files.extend((project / "src").rglob("*.ts"))
        source_files.extend([project / "render.mjs", project / "package-lock.json"])
        newest_source = max(
            (path.stat().st_mtime for path in source_files if path.is_file()),
            default=0.0,
        )
        if not output.is_file() or output.stat().st_mtime < newest_source:
            output, _ = await render_video_in_docker(project)

        tracker.update("quality", 90, "Contrôle du rendu repris")
        quality = await inspect_rendered_video(
            output,
            project_dir=project,
            expected_duration_sec=duration_sec,
            expected_width=width,
            expected_height=height,
        )
        if not quality.passed:
            tracker.update("failed", 100, quality.summary(), status="failed")
            return HandlerResult.fail(
                f"❌ Rendu repris mais refusé par le contrôle qualité: {quality.summary()}",
                handler_name="retry_video_render",
            )

        tracker.update(
            "complete",
            100,
            "Rendu repris et contrôlé",
            status="complete",
            output_path=str(output),
        )
        return HandlerResult.ok(
            f"✅ Rendu repris et contrôlé.\n**Fichier**: `{output}`\n"
            f"**Qualité**: {quality.summary()}",
            handler_name="retry_video_render",
        )
    except VideoCancelledError:
        tracker.update("cancelled", tracker.job.progress, "Reprise annulée", status="cancelled")
        return HandlerResult.fail(
            "⏹️ Reprise du rendu annulée.",
            handler_name="retry_video_render",
        )
    except (VideoInfrastructureError, VideoRenderError, RuntimeError) as exc:
        tracker.update("failed", 100, str(exc), status="failed")
        return HandlerResult.fail(
            f"❌ Reprise du rendu échouée: {exc}",
            handler_name="retry_video_render",
        )


# ── P3.4 — list_video_projects_handler ─────────────────────────────

async def list_video_projects_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Liste tous les projets vidéo Remotion dans le workspace."""
    from ...utils.paths import WORKSPACE_DIR

    projects: List[Dict[str, Any]] = []
    for render_mjs in WORKSPACE_DIR.rglob("render.mjs"):
        proj_dir = render_mjs.parent
        pkg_json = proj_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                if "remotion" in json.dumps(pkg.get("dependencies", {})):
                    output_mp4 = proj_dir / "output.mp4"
                    projects.append({
                        "dir": str(proj_dir.relative_to(WORKSPACE_DIR)),
                        "name": pkg.get("name", proj_dir.name),
                        "rendered": output_mp4.exists(),
                        "size_mb": f"{output_mp4.stat().st_size / 1024 / 1024:.1f}" if output_mp4.exists() else "—",
                    })
            except (json.JSONDecodeError, OSError):
                continue

    if not projects:
        return HandlerResult.ok(
            "Aucun projet vidéo Remotion trouvé dans le workspace.",
            handler_name="list_video_projects",
        )

    lines = ["## Projets vidéo Remotion\n"]
    for p in projects:
        status = "✅ Rendu" if p["rendered"] else "⏳ Non rendu"
        lines.append(f"- **{p['name']}** — `{p['dir']}` — {status} ({p['size_mb']} Mo)")

    return HandlerResult.ok("\n".join(lines), handler_name="list_video_projects")


async def get_video_job_handler(
    ctx: HandlerContext,
    project_dir: str = "",
) -> HandlerResult:
    """Retourne la progression persistée d'un projet vidéo."""
    from ...utils.paths import WORKSPACE_DIR

    if not project_dir:
        candidates = sorted(WORKSPACE_DIR.rglob(".lumena-video-job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return HandlerResult.fail("❌ Aucune tâche vidéo trouvée.", handler_name="get_video_job")
        project_dir = str(candidates[0].parent)
    try:
        project = resolve_video_project_path(project_dir)
        state = read_video_job(project)
        if not state:
            return HandlerResult.fail("❌ Aucun état de tâche pour ce projet.", handler_name="get_video_job")
        return HandlerResult.ok(
            "## Tâche vidéo\n"
            f"- État: **{state.get('status', 'unknown')}**\n"
            f"- Phase: `{state.get('phase', 'unknown')}`\n"
            f"- Progression: {state.get('progress', 0)}%\n"
            f"- Message: {state.get('message', '')}\n"
            f"- Projet: `{project}`",
            handler_name="get_video_job",
        )
    except ValueError as exc:
        return HandlerResult.fail(f"❌ Tâche vidéo refusée: {exc}", handler_name="get_video_job")


async def cancel_video_job_handler(
    ctx: HandlerContext,
    project_dir: str = "",
) -> HandlerResult:
    """Demande l'annulation coopérative d'une tâche vidéo en cours."""
    from ...utils.paths import WORKSPACE_DIR

    if not project_dir:
        candidates = sorted(WORKSPACE_DIR.rglob(".lumena-video-job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return HandlerResult.fail("❌ Aucune tâche vidéo trouvée.", handler_name="cancel_video_job")
        project_dir = str(candidates[0].parent)
    try:
        project = resolve_video_project_path(project_dir)
        request_video_cancel(project)
        return HandlerResult.ok(
            f"✅ Annulation demandée pour `{project}`. Le moteur s'arrêtera au prochain point sûr.",
            handler_name="cancel_video_job",
        )
    except ValueError as exc:
        return HandlerResult.fail(f"❌ Annulation refusée: {exc}", handler_name="cancel_video_job")


# ── P3.5 — get_video_handler_defs() ────────────────────────────────

def get_video_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers vidéo."""
    return [
        HandlerDef(
            name="generate_video",
            description=(
                "Génère une vidéo professionnelle (présentation, reel, explainer, social) "
                "à partir d'une description textuelle. Utilise Remotion (React) + Docker. "
                "Produit un fichier .mp4 dans le workspace. "
                "Utilise pour: faire une vidéo, créer une animation, générer un reel, faire une pub."
            ),
            parameters={
                "properties": {
                    "description": {"type": "string", "description": "Description détaillée de la vidéo souhaitée"},
                    "template": {"type": "string", "description": "Type: presentation, social_short, explainer, square_social, custom, auto (défaut: auto)", "default": "auto"},
                    "duration_sec": {"type": "integer", "description": "Durée en secondes (défaut: 30)", "default": 30},
                    "format": {"type": "string", "description": "landscape (16:9), portrait (9:16), square (1:1) — défaut: landscape", "default": "landscape"},
                    "output_name": {"type": "string", "description": "Nom de la vidéo (optionnel, déduit)", "default": ""},
                    "assets": {"type": "string", "description": "Assets à intégrer dans la vidéo: chemins fichiers séparés par virgule (images, vidéos, audio). Ex: 'logo.png, fond.jpg'. Accepte aussi les noms de fichiers uploadés récemment.", "default": ""},
                    "creation_mode": {"type": "string", "enum": ["auto", "safe", "expert"], "description": "auto adapte le rail au modèle; safe compile des composants bornés; expert autorise le TSX libre sandboxé", "default": "auto"},
                },
                "required": ["description"],
            },
            handler=generate_video_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="edit_video",
            description=(
                "Modifie un projet vidéo Remotion existant (texte, couleurs, durée, scènes, animations). "
                "Utilise le CodeAgent pour modifier chirurgicalement les fichiers TSX."
            ),
            parameters={
                "properties": {
                    "project_dir": {"type": "string", "description": "Chemin du projet vidéo Remotion", "default": ""},
                    "instructions": {"type": "string", "description": "Instructions de modification"},
                },
                "required": ["instructions"],
            },
            handler=edit_video_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="preview_video",
            description="Prévisualise un projet vidéo Remotion en effectuant un rendu rapide.",
            parameters={
                "properties": {
                    "project_dir": {"type": "string", "description": "Chemin du projet vidéo (optionnel, prend le dernier)", "default": ""},
                },
                "required": [],
            },
            handler=preview_video_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="list_video_projects",
            description="Liste tous les projets vidéo Remotion dans le workspace avec leur statut de rendu.",
            parameters={"properties": {}, "required": []},
            handler=list_video_projects_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="retry_video_render",
            description=(
                "Reprend le rendu d'un projet Remotion existant après une panne Docker, "
                "npm ou réseau, sans réécrire les scènes, puis contrôle le MP4."
            ),
            parameters={
                "properties": {
                    "project_dir": {"type": "string", "description": "Chemin du projet vidéo (optionnel, prend le dernier)", "default": ""},
                },
                "required": [],
            },
            handler=retry_video_render_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="get_video_job",
            description="Affiche l'état et la progression persistée de la dernière tâche vidéo ou d'un projet précis.",
            parameters={"properties": {"project_dir": {"type": "string", "default": ""}}, "required": []},
            handler=get_video_job_handler,
            category="video",
            source_module="handlers.remotion",
        ),
        HandlerDef(
            name="cancel_video_job",
            description="Demande l'annulation sûre d'une génération ou d'un rendu vidéo Remotion en cours.",
            parameters={"properties": {"project_dir": {"type": "string", "default": ""}}, "required": []},
            handler=cancel_video_job_handler,
            category="video",
            source_module="handlers.remotion",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
