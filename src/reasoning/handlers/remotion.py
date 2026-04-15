"""
remotion.py — Handlers V2 pour la génération vidéo via Remotion.

4 outils: generate_video, edit_video, preview_video, list_video_projects

Pattern: async (ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

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
    )
    from ...tools.remotion_prompts import (
        VIDEO_PLAN_SYSTEM,
        VIDEO_PLAN_PROMPT,
        SCENE_COMPONENT_SYSTEM,
        SCENE_COMPONENT_PROMPT,
        VIDEO_SEQUENCER_PROMPT,
        ROOT_TSX_TEMPLATE,
        INDEX_TS_TEMPLATE,
    )
    REMOTION_AVAILABLE = True
except ImportError:
    REMOTION_AVAILABLE = False


# ── P3.1 — generate_video_handler ──────────────────────────────────

async def generate_video_handler(
    ctx: HandlerContext,
    description: str = "",
    template: str = "auto",
    duration_sec: int = 30,
    format: str = "landscape",
    output_name: str = "",
    assets: str = "",
) -> HandlerResult:
    """Génère une vidéo complète à partir d'une description textuelle.

    Pipeline:
      Phase 0: Résolution + copie assets (si fournis)
      Phase 1: LLM planifie les scènes (JSON)
      Phase 2: LLM génère chaque composant TSX (par scène)
      Phase 3: Scaffold projet Remotion (fichiers fixes)
      Phase 4: Rendu Docker → MP4
      Phase 5: Copie vers workspace/ + retour métadonnées
    """
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

    # ── Phase 0 : Sélection template + dimensions ──
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

    # ── Phase 0 : Résolution des assets utilisateur ──
    from ...utils.paths import WORKSPACE_DIR
    import datetime

    slug = (output_name or "video").lower()
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:50]
    date_str = datetime.date.today().isoformat()
    project_dir = WORKSPACE_DIR / date_str / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    # Résoudre les assets : liste explicite OU auto-détection récente
    _asset_paths_raw: List[str] = []
    if assets:
        # Accepte: "logo.png, bg.jpg" ou "logo.png|bg.jpg" ou JSON liste
        import re as _re
        if assets.startswith("["):
            try:
                _asset_paths_raw = json.loads(assets)
            except json.JSONDecodeError:
                _asset_paths_raw = [a.strip() for a in _re.split(r"[,|;]", assets) if a.strip()]
        else:
            _asset_paths_raw = [a.strip() for a in _re.split(r"[,|;]", assets) if a.strip()]

    _resolved_assets = resolve_asset_paths(_asset_paths_raw)

    # Si aucun asset explicite et description mentionne "logo", "image", "photo"
    # → tenter auto-détection des fichiers uploadés dans les dernières 2h
    if not _resolved_assets and any(kw in description.lower() for kw in ("logo", "photo", "image", "mon ")):
        _auto = auto_detect_recent_assets(max_age_hours=2)
        if _auto:
            logger.info("[video] {} asset(s) uploadé(s) récemment détecté(s) automatiquement", len(_auto))
            _resolved_assets = _auto

    # Copier assets vers public/ et générer section prompt
    _assets_map: Dict[str, str] = {}
    if _resolved_assets:
        logger.info("[video] Phase 0/4 — Copie {} asset(s) vers public/...", len(_resolved_assets))
        _assets_map = copy_assets_to_project(project_dir, _resolved_assets)

    _assets_prompt = build_assets_prompt_section(_assets_map)

    # ── Phase 1 : Planification des scènes (LLM) ──
    lumena = ctx.lumena
    if not lumena or not hasattr(lumena, "llm"):
        return HandlerResult.fail(
            "❌ Pas de LLM disponible pour générer la vidéo.",
            handler_name="generate_video",
        )
    llm = lumena.llm

    # Auto-upgrade DeepSeek chat → reasoner pour un meilleur rendu TSX
    # Ne s'applique QUE si le modèle actif est deepseek-v3 (deepseek chat).
    # Si l'utilisateur a configuré Claude, Gemini, etc., on respecte son choix.
    _current_model = getattr(llm, "model", "") or ""
    _DEEPSEEK_CHAT_MODELS = {"deepseek-v3", "deepseek-chat", "deepseek-v3-0324"}
    _code_model: str | None = None
    if _current_model in _DEEPSEEK_CHAT_MODELS:
        _code_model = "deepseek-reasoner"
        logger.info("[video] Auto-upgrade LLM: {} → deepseek-reasoner (génération TSX)", _current_model)

    # Résoudre le max_output_tokens du modèle effectif
    from ...llm.providers import get_model_config
    _effective_model = _code_model or _current_model
    _model_cfg = get_model_config(_effective_model)
    _model_cap = _model_cfg.max_output_tokens if _model_cfg else 8192
    if _model_cap < 4096:
        return HandlerResult.fail(
            f"❌ Le modèle '{_effective_model}' a un max_output trop faible ({_model_cap} tokens) "
            "pour générer une vidéo. Utilise un modèle avec au moins 8K tokens de sortie.",
            handler_name="generate_video",
        )
    # Budget tokens par phase (adapté au modèle)
    _plan_tokens = min(8000, _model_cap)
    _plan_tokens_retry = min(16000, _model_cap)
    _scene_tokens = min(16000, _model_cap)
    _sequencer_tokens = min(8000, _model_cap)
    logger.info("[video] Modèle effectif: {} (max_output={}), budget plan={}/scene={}/seq={}",
                _effective_model, _model_cap, _plan_tokens, _scene_tokens, _sequencer_tokens)

    logger.info("[video] Phase 1/4 — Planification des scènes ({} sec, template: {})...", duration_sec, tpl_name)
    plan_prompt = VIDEO_PLAN_PROMPT.format(
        description=description,
        template_name=tpl_name,
        duration_sec=duration_sec,
        width=tpl["width"],
        height=tpl["height"],
        fps=tpl["fps"],
        suggested_scenes=", ".join(tpl["scenes"]) if tpl["scenes"] else "libre",
        total_frames=total_frames,
        assets_section=_assets_prompt,
    )

    import re as _re

    _plan_messages = [
        {"role": "system", "content": VIDEO_PLAN_SYSTEM},
        {"role": "user", "content": plan_prompt},
    ]

    plan: dict | None = None
    for _plan_attempt in range(1, 3):  # max 2 tentatives
        # Augmenter les tokens au retry si le premier a été tronqué
        _plan_max_tokens = _plan_tokens if _plan_attempt == 1 else _plan_tokens_retry
        plan_response = await llm.chat(
            messages=_plan_messages,
            temperature=0.5 + (_plan_attempt - 1) * 0.1,
            max_tokens=_plan_max_tokens,
            stop=["OBSERVATION:"],
            model=_code_model,
        )

        # Tentative 1 : parse directement
        try:
            plan = json.loads(plan_response)
            break
        except json.JSONDecodeError:
            pass

        # Tentative 2 : extraction regex du premier bloc JSON {}
        json_match = _re.search(r'\{[\s\S]*\}', plan_response)
        if json_match:
            try:
                plan = json.loads(json_match.group())
                break
            except json.JSONDecodeError:
                logger.warning("[video] JSON plan malformé (tentative {}/2) — retry...", _plan_attempt)

        if _plan_attempt == 1:
            # Injecter l'erreur dans le contexte pour forcer un meilleur output
            _plan_messages.append({"role": "assistant", "content": plan_response})
            _plan_messages.append({"role": "user", "content": "Le JSON retourné est invalide. Retourne UNIQUEMENT un objet JSON valide, sans backticks, sans texte autour."})

    if not plan:
        return HandlerResult.fail(
            "❌ Le LLM n'a pas retourné un JSON valide pour le plan vidéo après 2 tentatives.",
            handler_name="generate_video",
        )

    scenes = plan.get("scenes", [])
    if not scenes:
        return HandlerResult.fail(
            "❌ Le plan vidéo ne contient aucune scène.",
            handler_name="generate_video",
        )

    logger.info("[video] Plan validé: {} scènes, {} frames @ {}fps", len(scenes), total_frames, tpl["fps"])

    # ── Phase 2 : Génération de chaque composant scène (LLM) ──
    scenes_code: Dict[str, str] = {}
    _n_scenes = len(scenes)
    logger.info("[video] Phase 2/4 — Génération TSX ({} composants)...", _n_scenes)

    for _scene_idx, scene in enumerate(scenes, start=1):
        component_name = scene.get("component_name", scene["id"].title() + "Scene")
        logger.info("[video] Scène {}/{}: {} ({} frames)...", _scene_idx, _n_scenes, component_name, scene.get("duration_frames", "?"))
        scene_prompt = SCENE_COMPONENT_PROMPT.format(
            scene_json=json.dumps(scene, ensure_ascii=False, indent=2),
            palette_json=json.dumps(plan.get("palette", {}), ensure_ascii=False),
            font_family=plan.get("font_family", "Inter"),
            width=tpl["width"],
            height=tpl["height"],
            fps=tpl["fps"],
            component_name=component_name,
            assets_section=_assets_prompt,
            static_file_import_hint=(
                "import { staticFile } from 'remotion'; // Pour les assets locaux fournis"
                if _assets_map else
                "// Pas d'assets locaux — NE PAS importer staticFile"
            ),
            image_constraint=(
                "Images AVEC assets fournis: utilise `<Img src={staticFile(\"nom_fichier.ext\")} />` "
                "pour les intégrer"
                if _assets_map else
                "Images: URL Unsplash DIRECTES UNIQUEMENT — INTERDIT d'utiliser staticFile()"
            ),
            static_file_constraint=(
                "staticFile() autorisé UNIQUEMENT pour ces fichiers: "
                + ", ".join(_assets_map.values())
                if _assets_map else
                "INTERDIT d'utiliser staticFile() ou d'importer quoi que ce soit depuis public/. "
                "Utilise des URLs https:// directement."
            ),
        )

        _scene_msgs = [
            {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
            {"role": "user", "content": scene_prompt},
        ]
        code = await llm.chat(
            messages=_scene_msgs,
            temperature=0.3,
            max_tokens=_scene_tokens,
            model=_code_model,
        )

        # Nettoyer les backticks markdown si présents
        code = _strip_markdown_fences(code)

        # FIX: Valider que le résultat est du TSX, pas du texte descriptif
        if not any(m in code for m in ("import ", "export ", "React")):
            logger.warning("[video] ⚠️ Scène {} — pas de TSX valide, retry...", component_name)
            code = await llm.chat(messages=_scene_msgs, temperature=0.5, max_tokens=_scene_tokens, model=_code_model)
            code = _strip_markdown_fences(code)

        # FIX: Si aucun asset fourni, supprimer tout usage de staticFile() qui causerait un 404
        if not _assets_map and "staticFile" in code:
            import re as _re_fix
            # Supprimer l'import staticFile
            code = _re_fix.sub(r"import\s*\{\s*staticFile\s*\}\s*from\s*'remotion'\s*;\s*\n?", "", code)
            code = _re_fix.sub(r",\s*staticFile\s*(?=[,}])", "", code)
            # Remplacer staticFile("...") par une URL Unsplash de remplacement
            def _replace_static_file(m: "_re_fix.Match[str]") -> str:
                return '"https://images.unsplash.com/photo-1620712943543-bcc4688e7485?auto=format&fit=crop&w=1600&q=80"'
            code = _re_fix.sub(r'staticFile\(["\'][^"\']*["\']\)', _replace_static_file, code)
            logger.warning(
                "[video] ⚠️ {} — staticFile() sans assets détecté et corrigé (URLs Unsplash substituées)",
                component_name,
            )

        scenes_code[f"src/scenes/{component_name}.tsx"] = code
        logger.info("[video] ✅ {}.tsx généré ({} chars)", component_name, len(code))

    # Générer Video.tsx (séquenceur)
    logger.info("[video] Assemblage Video.tsx (séquenceur)...")
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

    # FIX: Valider Video.tsx aussi
    if not any(m in video_tsx_code for m in ("import ", "export ", "Sequence")):
        logger.warning("[video] ⚠️ Video.tsx invalide, retry...")
        video_tsx = await llm.chat(
            messages=[
                {"role": "system", "content": SCENE_COMPONENT_SYSTEM},
                {"role": "user", "content": VIDEO_SEQUENCER_PROMPT.format(
                    scenes_list=scenes_list,
                    total_frames=total_frames,
                )},
            ],
            temperature=0.5,
            max_tokens=_sequencer_tokens,
            model=_code_model,
        )
        video_tsx_code = _strip_markdown_fences(video_tsx)
    scenes_code["src/Video.tsx"] = video_tsx_code
    logger.info("[video] ✅ Video.tsx assemblé")

    # ── Phase 3 : Scaffold projet + écriture fichiers ──
    logger.info("[video] Phase 3/4 — Scaffold projet Remotion...")
    # Affiner le slug depuis le titre du plan (peut être différent de output_name)
    _plan_title_slug = (plan.get("title", "") or output_name or "video").lower()
    _plan_title_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in _plan_title_slug)[:50]
    if _plan_title_slug and _plan_title_slug != slug:
        # Renommer le dossier si le LLM a proposé un meilleur titre
        _new_dir = WORKSPACE_DIR / date_str / _plan_title_slug
        if not _new_dir.exists():
            project_dir.rename(_new_dir)
            project_dir = _new_dir
            project_dir.mkdir(parents=True, exist_ok=True)
            # Recréer public/ si des assets avaient été copiés
            if _assets_map:
                _new_public = project_dir / "public"
                _new_public.mkdir(parents=True, exist_ok=True)

    scaffold_files = scaffold_remotion_project(
        output_dir=project_dir,
        template=tpl,
        composition_id="Main",
    )

    # Écrire les fichiers scaffold
    for rel_path, content in scaffold_files.items():
        fp = project_dir / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")

    # Écrire les fichiers de scènes
    write_scene_files(project_dir, scenes_code)
    logger.info("[video] ✅ Fichiers projet écrits dans {}", project_dir.name)

    # ── Phase 4 : Rendu Docker → MP4 ──
    logger.info("[video] Phase 4/4 — Rendu MP4 en cours ({}×{} @ {}fps)...", tpl["width"], tpl["height"], tpl["fps"])
    try:
        video_path, render_log = await render_video_in_docker(
            project_dir=project_dir,
            timeout_sec=int(os.getenv("LUMENA_VIDEO_RENDER_TIMEOUT", "300")),
        )
    except RuntimeError as e:
        return HandlerResult.fail(
            f"❌ Rendu vidéo échoué: {e}\n\nProjet sauvegardé dans: {project_dir}",
            handler_name="generate_video",
        )

    # ── Phase 5 : Résultat ──
    rel_video = video_path.relative_to(WORKSPACE_DIR)
    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    logger.info("[video] ✅ Rendu terminé: {:.1f} Mo — {}", file_size_mb, rel_video)

    _assets_info = (
        f"**Assets intégrés**: {', '.join(_assets_map.keys())}\n"
        if _assets_map else ""
    )

    return HandlerResult.ok(
        f"✅ Vidéo générée avec succès!\n\n"
        f"**Fichier**: `workspace/{rel_video}`\n"
        f"**Taille**: {file_size_mb:.1f} Mo\n"
        f"**Durée**: {duration_sec}s @ {tpl['fps']}fps\n"
        f"**Résolution**: {tpl['width']}×{tpl['height']}\n"
        f"**Scènes**: {len(scenes)}\n"
        f"{_assets_info}"
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

    project_path = Path(project_dir)
    if not project_path.exists():
        return HandlerResult.fail(
            f"❌ Répertoire non trouvé: {project_dir}",
            handler_name="edit_video",
        )

    try:
        from ...agents.sub_agent import delegate_to_agent
        output = await delegate_to_agent(
            description=f"Modifier le projet vidéo Remotion dans {project_dir}: {instructions}",
            agent_type="code",
            context={"workspace_path": str(project_path)},
        )

        if output and not output.startswith("❌"):
            return HandlerResult.ok(
                f"✅ Vidéo modifiée.\n{output}\n\n"
                f"Re-rendre avec `generate_video` ou prévisualiser avec `preview_video`.",
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
        video_path, _ = await render_video_in_docker(
            project_dir=Path(project_dir),
            timeout_sec=120,
        )
        return HandlerResult.ok(
            f"✅ Preview rendu: `{video_path}`",
            handler_name="preview_video",
        )
    except RuntimeError as e:
        return HandlerResult.fail(
            f"❌ Preview échoué: {e}",
            handler_name="preview_video",
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


# ── P3.5 — get_video_handler_defs() ────────────────────────────────

def get_video_handler_defs() -> List[HandlerDef]:
    """Retourne les 4 définitions de handlers vidéo."""
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
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
