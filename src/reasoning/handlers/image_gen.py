"""
image_gen.py — Handlers de génération/édition d'images.

Handlers: generate_image, edit_image, generate_thumbnail, generate_logo,
          upscale_image, remove_background, replace_background,
          sketch_to_image, generate_svg, list_image_models.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

# Import lazy pour éviter import circulaire
_svc = None


def _get_service():
    global _svc
    if _svc is None:
        from src.services.image_gen import ImageGenService
        _svc = ImageGenService.get_instance()
    return _svc


def _record(ctx: HandlerContext, *, tool_name: str, action: str,
            file_path: str, summary: str, workspace_relative: str = "") -> None:
    """Enregistre l'édition dans la télémétrie file_edits."""
    try:
        from .files import _record_file_edit
        _record_file_edit(
            ctx,
            tool_name=tool_name,
            action=action,
            file_path=file_path,
            before_content="",
            after_content="[binary image data]",
            existed_before=False,
            summary=summary,
            workspace_relative=workspace_relative,
        )
    except Exception as e:
        logger.debug(f"_record_file_edit skipped: {e}")


def _slugify_prompt(prompt: str) -> str:
    """Prompt → slug fichier court."""
    from src.services.image_gen import _slugify
    return _slugify(prompt)


# ─── Handlers ──────────────────────────────────────────────────────────────

async def generate_image_handler(
    ctx: HandlerContext,
    prompt: str,
    model: str = "auto",
    size: str = "1024x1024",
    quality: str = "hd",
    style: str = "",
    template: str = "",
    enrich_prompt: str = "false",
) -> HandlerResult:
    """Génère une image depuis un prompt texte."""
    try:
        svc = _get_service()

        # Enrichir le prompt si demandé
        final_prompt = prompt
        if enrich_prompt.lower() in ("true", "1", "yes", "oui"):
            final_prompt = await svc.enrich_prompt(prompt, style=style)

        result = await svc.generate(
            final_prompt, model=model, size=size, quality=quality,
            style=style, template=template,
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, slug)

        _record(
            ctx, tool_name="generate_image", action="create",
            file_path=str(filepath), summary=f"Image générée: {prompt[:80]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Image générée avec succès!\n"
            f"📁 Fichier: {filepath}\n"
            f"🧠 Modèle: {result.model} ({result.provider})\n"
            f"📐 Taille: {result.width}x{result.height}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms\n"
            f"💰 Coût: ${result.cost_estimate:.3f}",
            handler_name="generate_image",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur génération image: {e}",
            handler_name="generate_image",
        )


async def edit_image_handler(
    ctx: HandlerContext,
    image_path: str,
    prompt: str,
    mode: str = "inpaint",
    mask_prompt: str = "",
    model: str = "auto",
) -> HandlerResult:
    """Édite une image existante (inpaint, outpaint, erase, search-replace)."""
    try:
        svc = _get_service()
        result = await svc.edit(
            image_path, prompt, mode=mode, mask_prompt=mask_prompt, model=model,
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, slug)

        _record(
            ctx, tool_name="edit_image", action="edit",
            file_path=str(filepath), summary=f"Image éditée ({mode}): {prompt[:80]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Image éditée avec succès ({mode})!\n"
            f"📁 Fichier: {filepath}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="edit_image",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur édition image: {e}", handler_name="edit_image")


async def generate_thumbnail_handler(
    ctx: HandlerContext,
    prompt: str,
    model: str = "auto",
    text_overlay: str = "",
) -> HandlerResult:
    """Génère une miniature YouTube/social media optimisée CTR."""
    try:
        svc = _get_service()
        full_prompt = prompt
        if text_overlay:
            full_prompt += f' with bold text overlay saying "{text_overlay}"'

        result = await svc.generate(
            full_prompt, model=model, size="1792x1024",
            quality="hd", template="thumbnail",
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, f"thumb_{slug}")

        _record(
            ctx, tool_name="generate_thumbnail", action="create",
            file_path=str(filepath), summary=f"Miniature générée: {prompt[:80]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Miniature YouTube générée (16:9)!\n"
            f"📁 Fichier: {filepath}\n"
            f"🧠 Modèle: {result.model}",
            handler_name="generate_thumbnail",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur miniature: {e}", handler_name="generate_thumbnail")


async def generate_logo_handler(
    ctx: HandlerContext,
    prompt: str,
    model: str = "auto",
    svg: str = "false",
) -> HandlerResult:
    """Génère un logo professionnel."""
    try:
        svc = _get_service()

        # Si SVG demandé et Recraft disponible, utiliser recraft-v4-svg
        effective_model = model
        if svg.lower() in ("true", "1", "yes", "oui") and model == "auto":
            if svc._has_api_key("recraft"):
                effective_model = "recraft-v4-svg"

        result = await svc.generate(
            prompt, model=effective_model, size="1024x1024",
            quality="hd", template="logo",
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, f"logo_{slug}")

        _record(
            ctx, tool_name="generate_logo", action="create",
            file_path=str(filepath), summary=f"Logo généré: {prompt[:80]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Logo généré!\n"
            f"📁 Fichier: {filepath}\n"
            f"📐 Format: {result.format.upper()}\n"
            f"🧠 Modèle: {result.model}",
            handler_name="generate_logo",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur logo: {e}", handler_name="generate_logo")


async def upscale_image_handler(
    ctx: HandlerContext,
    image_path: str,
    scale: str = "4",
    mode: str = "fast",
) -> HandlerResult:
    """Agrandit une image (upscale) en conservant la qualité."""
    try:
        svc = _get_service()
        result = await svc.upscale(image_path, scale=int(scale), mode=mode)
        filepath = svc.save_to_workspace(result, "upscaled")

        _record(
            ctx, tool_name="upscale_image", action="create",
            file_path=str(filepath), summary=f"Image upscalée ({mode}, {scale}x)",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Image upscalée ({mode}, {scale}x)!\n"
            f"📁 Fichier: {filepath}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="upscale_image",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur upscale: {e}", handler_name="upscale_image")


async def remove_background_handler(
    ctx: HandlerContext,
    image_path: str,
) -> HandlerResult:
    """Supprime le fond d'une image (arrière-plan → transparent)."""
    try:
        svc = _get_service()
        result = await svc.remove_background(image_path)
        filepath = svc.save_to_workspace(result, "nobg")

        _record(
            ctx, tool_name="remove_background", action="create",
            file_path=str(filepath), summary="Fond supprimé",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Fond supprimé avec succès!\n"
            f"📁 Fichier: {filepath}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="remove_background",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur suppression fond: {e}", handler_name="remove_background")


async def replace_background_handler(
    ctx: HandlerContext,
    image_path: str,
    background_prompt: str,
) -> HandlerResult:
    """Remplace le fond d'une image par un nouveau fond généré."""
    try:
        svc = _get_service()
        result = await svc.replace_background(image_path, background_prompt)
        slug = _slugify_prompt(background_prompt)
        filepath = svc.save_to_workspace(result, f"rebg_{slug}")

        _record(
            ctx, tool_name="replace_background", action="create",
            file_path=str(filepath), summary=f"Fond remplacé: {background_prompt[:60]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Fond remplacé avec succès!\n"
            f"📁 Fichier: {filepath}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="replace_background",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur remplacement fond: {e}", handler_name="replace_background")


async def sketch_to_image_handler(
    ctx: HandlerContext,
    image_path: str,
    prompt: str,
    style: str = "",
) -> HandlerResult:
    """Transforme un croquis/dessin en image réaliste."""
    try:
        svc = _get_service()
        result = await svc.sketch_to_image(image_path, prompt, style=style)
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, f"sketch_{slug}")

        _record(
            ctx, tool_name="sketch_to_image", action="create",
            file_path=str(filepath), summary=f"Croquis→image: {prompt[:60]}",
            workspace_relative=str(filepath),
        )

        return HandlerResult.ok(
            f"✅ Croquis transformé en image!\n"
            f"📁 Fichier: {filepath}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="sketch_to_image",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur sketch→image: {e}", handler_name="sketch_to_image")


async def generate_svg_handler(
    ctx: HandlerContext,
    prompt: str,
    model: str = "auto",
) -> HandlerResult:
    """Génère un fichier SVG vectoriel."""
    try:
        svc = _get_service()
        effective_model = model
        if model == "auto" and svc._has_api_key("recraft"):
            effective_model = "recraft-v4-svg"

        result = await svc.generate(
            prompt, model=effective_model, size="1024x1024", quality="hd",
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, f"svg_{slug}")

        _record(
            ctx, tool_name="generate_svg", action="create",
            file_path=str(filepath), summary=f"SVG généré: {prompt[:60]}",
            workspace_relative=str(filepath),
        )

        fmt_label = "SVG vectoriel" if result.format == "svg" else result.format.upper()
        return HandlerResult.ok(
            f"✅ Image vectorielle générée ({fmt_label})!\n"
            f"📁 Fichier: {filepath}\n"
            f"🧠 Modèle: {result.model}",
            handler_name="generate_svg",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur SVG: {e}", handler_name="generate_svg")


async def compose_image_handler(
    ctx: HandlerContext,
    prompt: str,
    assets: str = "[]",
    platform: str = "",
    model: str = "auto",
    size: str = "",
) -> HandlerResult:
    """Compose une image à partir de plusieurs assets avec rôles (subject, object, background, foreground, sky)."""
    try:
        svc = _get_service()
        from src.services.image_gen import ComposeAsset

        # Parser les assets JSON
        try:
            raw_assets = json.loads(assets) if isinstance(assets, str) else assets
        except json.JSONDecodeError:
            return HandlerResult.fail(
                "Format assets invalide. Attendu: JSON array [{path, role, description}, ...]",
                handler_name="compose_image",
            )

        if not isinstance(raw_assets, list) or not raw_assets:
            return HandlerResult.fail(
                "Au moins un asset requis. Format: [{\"path\": \"/chemin/image.png\", \"role\": \"subject\", \"description\": \"...\"}]",
                handler_name="compose_image",
            )

        compose_assets = []
        valid_roles = {"subject", "object", "background", "foreground", "sky"}
        for a in raw_assets:
            role = a.get("role", "subject")
            if role not in valid_roles:
                return HandlerResult.fail(
                    f"Rôle invalide: '{role}'. Valides: {', '.join(sorted(valid_roles))}",
                    handler_name="compose_image",
                )
            compose_assets.append(ComposeAsset(
                path=a.get("path", ""),
                role=role,
                description=a.get("description", ""),
            ))

        result = await svc.compose(
            compose_assets, prompt, platform=platform, model=model, size=size,
        )
        slug = _slugify_prompt(prompt)
        filepath = svc.save_to_workspace(result, f"composed_{slug}")

        _record(
            ctx, tool_name="compose_image", action="create",
            file_path=str(filepath),
            summary=f"Composition {len(compose_assets)} assets: {prompt[:60]}",
            workspace_relative=str(filepath),
        )

        roles_summary = ", ".join(f"{a.role}" for a in compose_assets)
        return HandlerResult.ok(
            f"✅ Image composée avec succès ({len(compose_assets)} assets)!\n"
            f"📁 Fichier: {filepath}\n"
            f"🎭 Rôles: {roles_summary}\n"
            f"🧠 Modèle: {result.model} ({result.provider})\n"
            f"⏱️ Temps: {result.generation_time_ms}ms",
            handler_name="compose_image",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur composition: {e}", handler_name="compose_image")


async def generate_thumbnail_pro_handler(
    ctx: HandlerContext,
    topic: str,
    platform: str = "youtube_thumbnail",
    assets: str = "[]",
    model: str = "auto",
    style: str = "",
) -> HandlerResult:
    """Pipeline pro miniature : planification LLM (headlines, couleurs, composition) → génération image."""
    try:
        svc = _get_service()

        # Parser assets optionnels
        compose_assets = None
        try:
            raw = json.loads(assets) if isinstance(assets, str) else assets
            if isinstance(raw, list) and raw:
                from src.services.image_gen import ComposeAsset
                compose_assets = [
                    ComposeAsset(
                        path=a.get("path", ""),
                        role=a.get("role", "subject"),
                        description=a.get("description", ""),
                    )
                    for a in raw
                ]
        except (json.JSONDecodeError, TypeError):
            pass

        result, plan = await svc.generate_thumbnail_pro(
            topic, platform=platform, assets=compose_assets, model=model, style=style,
        )
        slug = _slugify_prompt(topic)
        filepath = svc.save_to_workspace(result, f"thumb_pro_{slug}")

        _record(
            ctx, tool_name="generate_thumbnail_pro", action="create",
            file_path=str(filepath),
            summary=f"Miniature pro: {topic[:60]}",
            workspace_relative=str(filepath),
        )

        headlines_str = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(plan.headlines))
        colors_str = " ".join(plan.colors)

        return HandlerResult.ok(
            f"✅ Miniature pro générée (pipeline 2 étapes)!\n"
            f"📁 Fichier: {filepath}\n"
            f"🧠 Modèle: {result.model} ({result.provider})\n"
            f"📐 Taille: {result.width}x{result.height}\n"
            f"⏱️ Temps: {result.generation_time_ms}ms\n\n"
            f"📝 **Titres proposés:**\n{headlines_str}\n\n"
            f"🎨 **Palette:** {colors_str}\n"
            f"😀 **Émotion:** {plan.emotion}\n"
            f"📋 **Composition:** {plan.composition_notes}",
            handler_name="generate_thumbnail_pro",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur miniature pro: {e}", handler_name="generate_thumbnail_pro")


async def generate_headlines_handler(
    ctx: HandlerContext,
    topic: str,
    platform: str = "youtube",
    count: str = "5",
    style: str = "",
) -> HandlerResult:
    """Génère des titres/headlines viraux optimisés pour une plateforme."""
    try:
        svc = _get_service()
        headlines = await svc.generate_headlines(
            topic, platform=platform, count=int(count), style=style,
        )

        lines = [f"📝 **{len(headlines)} titres viraux ({platform}):**\n"]
        for i, h in enumerate(headlines):
            lines.append(f"  {i+1}. {h}")

        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="generate_headlines",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur headlines: {e}", handler_name="generate_headlines")


async def list_image_models_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Liste tous les modèles de génération d'images avec leurs caractéristiques."""
    try:
        svc = _get_service()
        models = svc.get_available_models()

        available = sorted(
            [m for m in models if m["available"]],
            key=lambda m: (-m.get("quality", 0), m.get("cost_per_image", 999)),
        )
        unavailable = [m for m in models if not m["available"]]

        lines = ["📋 **Modèles de génération d'images:**\n"]
        if available:
            lines.append("✅ **Disponibles (classés par qualité) :**")
            for m in available:
                tag = " 🆓" if m.get("free") else ""
                q = m.get("quality", "?")
                spd = m.get("speed", "?")
                cost = "gratuit" if m.get("free") else f"${m.get('cost_per_image', '?')}"
                name = m.get("display_name", m["name"])
                lines.append(f"  • **{name}** (`{m['name']}`) — ★{q}/10 ⚡{spd}/10 {cost}{tag}")
                if m.get("strengths"):
                    lines.append(f"    Forces: {m['strengths']}")
                if m.get("best_for"):
                    lines.append(f"    Idéal pour: {m['best_for']}")
                if m.get("capabilities"):
                    caps = ", ".join(m["capabilities"])
                    lines.append(f"    Capacités: {caps}")
                if m.get("max_resolution"):
                    lines.append(f"    Résolution max: {m['max_resolution']}")

        if unavailable:
            lines.append("\n❌ **Non configurés (clé API manquante) :**")
            for m in unavailable:
                from src.services.image_gen import _PROVIDER_API_KEY, _MODEL_CATALOG
                env_var = _PROVIDER_API_KEY.get(m["provider"], "?")
                info = _MODEL_CATALOG.get(m["name"])
                extra = f" — {info.best_for}" if info else ""
                lines.append(f"  - {m['name']} ({m['provider']}) → {env_var}{extra}")

        return HandlerResult.ok("\n".join(lines), handler_name="list_image_models")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="list_image_models")


# ─── Définitions ───────────────────────────────────────────────────────────

def get_image_gen_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des handlers de génération d'images."""
    return [
        HandlerDef(
            name="generate_image",
            description=(
                "Génère une image depuis un texte (prompt). 40+ modèles classés par qualité.\n"
                "GUIDE DE CHOIX RAPIDE:\n"
                "• Texte/typographie dans l'image → ideogram-v3-quality ou gpt-image-1.5\n"
                "• Logo/icône/vectoriel → recraft-v4 ou recraft-v4-svg\n"
                "• Photoréalisme haut de gamme → flux-2-max ou imagen-4-ultra\n"
                "• Bon rapport qualité/prix → flux-2-pro ou gpt-image-1.5\n"
                "• Gratuit → gemini-3.1-flash-image ou huggingface-sdxl\n"
                "• Édition/variation d'image → flux-kontext-pro ou stable-image-ultra\n"
                "• Rapide pas cher → flux-schnell ou flux-2-klein-4b\n"
                "Mode 'auto' = fallback du meilleur au moins bon parmi les providers configurés. "
                "Templates: thumbnail, logo, product, portrait, illustration, icon, banner, infographic."
            ),
            parameters={
                "properties": {
                    "prompt": {"type": "string", "description": "Description détaillée de l'image à générer. Plus le prompt est précis, meilleur sera le résultat."},
                    "model": {"type": "string", "description": "Modèle à utiliser (auto = meilleur disponible). Exemples: gemini-3.1-flash-image, gpt-image-1.5, flux-2-pro, stable-image-ultra, ideogram-v3-quality.", "default": "auto"},
                    "size": {"type": "string", "description": "Taille de l'image (ex: 1024x1024, 1792x1024, 512x512).", "default": "1024x1024"},
                    "quality": {"type": "string", "description": "Qualité: 'hd' ou 'standard'.", "default": "hd"},
                    "style": {"type": "string", "description": "Style libre (ex: 'photorealistic', 'watercolor', 'pixel art').", "default": ""},
                    "template": {"type": "string", "description": "Template prédéfini: thumbnail, logo, product, portrait, illustration, icon, banner, infographic.", "default": ""},
                    "enrich_prompt": {"type": "string", "description": "Si 'true', enrichit le prompt via LLM avant génération.", "default": "false"},
                },
                "required": ["prompt"],
            },
            handler=generate_image_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="edit_image",
            description=(
                "Édite une image existante. Modes: inpaint (modifier zone), outpaint (étendre), "
                "erase (effacer zone), search-replace (remplacer objet)."
            ),
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin absolu de l'image source."},
                    "prompt": {"type": "string", "description": "Description de l'édition souhaitée."},
                    "mode": {"type": "string", "description": "Mode d'édition: inpaint, outpaint, erase, search-replace.", "default": "inpaint"},
                    "mask_prompt": {"type": "string", "description": "Pour search-replace: description de l'objet à remplacer.", "default": ""},
                    "model": {"type": "string", "description": "Modèle (auto = Stability AI si dispo, sinon Gemini).", "default": "auto"},
                },
                "required": ["image_path", "prompt"],
            },
            handler=edit_image_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="generate_thumbnail",
            description=(
                "Génère une miniature YouTube/social media optimisée CTR. "
                "Format 16:9 (1792x1024), éclairage dramatique, couleurs vibrantes."
            ),
            parameters={
                "properties": {
                    "prompt": {"type": "string", "description": "Sujet de la miniature (ex: 'homme surpris devant un ordinateur')."},
                    "model": {"type": "string", "description": "Modèle (auto = meilleur disponible).", "default": "auto"},
                    "text_overlay": {"type": "string", "description": "Texte à superposer en gros (ex: '10 ASTUCES').", "default": ""},
                },
                "required": ["prompt"],
            },
            handler=generate_thumbnail_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="generate_logo",
            description=(
                "Génère un logo professionnel (minimaliste, vectoriel si SVG). "
                "Utilise Recraft V4 SVG natif si disponible."
            ),
            parameters={
                "properties": {
                    "prompt": {"type": "string", "description": "Description du logo (ex: 'logo minimaliste pour une app de fitness, couleurs bleu et vert')."},
                    "model": {"type": "string", "description": "Modèle (auto = recraft SVG si dispo).", "default": "auto"},
                    "svg": {"type": "string", "description": "Si 'true', génère en SVG vectoriel (nécessite Recraft).", "default": "false"},
                },
                "required": ["prompt"],
            },
            handler=generate_logo_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="upscale_image",
            description=(
                "Agrandit une image (upscale) tout en conservant/améliorant la qualité. "
                "Modes: fast (rapide), conservative (fidèle), creative (amélioré IA)."
            ),
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin absolu de l'image à agrandir."},
                    "scale": {"type": "string", "description": "Facteur d'agrandissement (2 ou 4).", "default": "4"},
                    "mode": {"type": "string", "description": "Mode: fast, conservative, creative.", "default": "fast"},
                },
                "required": ["image_path"],
            },
            handler=upscale_image_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="remove_background",
            description="Supprime le fond d'une image, rendant l'arrière-plan transparent (PNG).",
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin absolu de l'image."},
                },
                "required": ["image_path"],
            },
            handler=remove_background_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="replace_background",
            description="Remplace le fond d'une image par un nouveau fond généré depuis un prompt.",
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin absolu de l'image."},
                    "background_prompt": {"type": "string", "description": "Description du nouveau fond (ex: 'plage tropicale au coucher du soleil')."},
                },
                "required": ["image_path", "background_prompt"],
            },
            handler=replace_background_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="sketch_to_image",
            description="Transforme un croquis/dessin en image réaliste (Stability AI sketch-to-image).",
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin du croquis/dessin."},
                    "prompt": {"type": "string", "description": "Description de l'image finale souhaitée."},
                    "style": {"type": "string", "description": "Style visuel (ex: 'photorealistic', 'anime').", "default": ""},
                },
                "required": ["image_path", "prompt"],
            },
            handler=sketch_to_image_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="generate_svg",
            description=(
                "Génère un fichier SVG vectoriel (scalable). "
                "Utilise Recraft V4 SVG natif si disponible, sinon le meilleur provider PNG."
            ),
            parameters={
                "properties": {
                    "prompt": {"type": "string", "description": "Description de l'image vectorielle à générer."},
                    "model": {"type": "string", "description": "Modèle (auto = recraft-v4-svg si dispo).", "default": "auto"},
                },
                "required": ["prompt"],
            },
            handler=generate_svg_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="list_image_models",
            description=(
                "Liste tous les modèles image avec caractéristiques détaillées: "
                "qualité, vitesse, coût, résolution max, forces, faiblesses, cas d'usage idéal. "
                "Classés par qualité décroissante."
            ),
            parameters={
                "properties": {},
                "required": [],
            },
            handler=list_image_models_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="compose_image",
            description=(
                "Compose une image à partir de PLUSIEURS images sources avec des rôles "
                "(subject, object, background, foreground, sky). "
                "Idéal pour créer des miniatures/thumbnails avec un sujet détouré sur un fond custom. "
                "Utilise Gemini multimodal (chaque asset est envoyé avec son rôle). "
                "Supporte les plateformes: youtube_thumbnail, tiktok_cover, instagram_post, etc."
            ),
            parameters={
                "properties": {
                    "prompt": {"type": "string", "description": "Directive de composition (ex: 'Combine le sujet sur le fond de plage avec une expression surprise')."},
                    "assets": {"type": "string", "description": 'JSON array d\'assets: [{"path": "/chemin/img.png", "role": "subject", "description": "homme surpris"}, {"path": "/chemin/bg.jpg", "role": "background", "description": "plage tropicale"}]'},
                    "platform": {"type": "string", "description": "Plateforme cible: youtube_thumbnail, youtube_banner, tiktok_cover, instagram_post, instagram_story, linkedin_post, twitter_post, podcast_cover.", "default": ""},
                    "model": {"type": "string", "description": "Modèle Gemini (auto = gemini-3.1-flash-image).", "default": "auto"},
                    "size": {"type": "string", "description": "Taille de sortie (auto-détectée depuis platform si vide).", "default": ""},
                },
                "required": ["prompt", "assets"],
            },
            handler=compose_image_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="generate_thumbnail_pro",
            description=(
                "Pipeline PRO de création de miniature en 2 étapes:\n"
                "1) Le LLM analyse le sujet et planifie: titres viraux, prompt visuel, palette, composition, émotion\n"
                "2) Le prompt visuel structuré est envoyé au générateur d'images\n"
                "Peut intégrer des assets (photos sujet) pour composition multi-images. "
                "Plateformes: youtube_thumbnail (défaut), tiktok_cover, instagram_post, etc."
            ),
            parameters={
                "properties": {
                    "topic": {"type": "string", "description": "Sujet de la miniature (ex: '10 astuces Python que personne ne connaît')."},
                    "platform": {"type": "string", "description": "Plateforme cible (youtube_thumbnail, tiktok_cover, instagram_post...).", "default": "youtube_thumbnail"},
                    "assets": {"type": "string", "description": 'JSON array d\'assets optionnels: [{"path": "/photo.png", "role": "subject"}]', "default": "[]"},
                    "model": {"type": "string", "description": "Modèle image (auto = meilleur disponible).", "default": "auto"},
                    "style": {"type": "string", "description": "Style souhaité (ex: 'dramatique', 'minimaliste', 'néon').", "default": ""},
                },
                "required": ["topic"],
            },
            handler=generate_thumbnail_pro_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
        HandlerDef(
            name="generate_headlines",
            description=(
                "Génère des titres/headlines viraux optimisés pour une plateforme. "
                "YouTube: max 60 chars, curiosity gap. TikTok: courts, trend hooks. "
                "Instagram: storytelling. LinkedIn: pro, data-driven. Twitter: percutants."
            ),
            parameters={
                "properties": {
                    "topic": {"type": "string", "description": "Sujet du contenu (ex: 'les dangers du sucre', 'tutoriel React hooks')."},
                    "platform": {"type": "string", "description": "Plateforme: youtube, tiktok, instagram, linkedin, twitter.", "default": "youtube"},
                    "count": {"type": "string", "description": "Nombre de titres (1-10).", "default": "5"},
                    "style": {"type": "string", "description": "Style: clickbait, informatif, question, listicle, choquant...", "default": ""},
                },
                "required": ["topic"],
            },
            handler=generate_headlines_handler,
            category="image",
            source_module="handlers.image_gen",
        ),
    ]
