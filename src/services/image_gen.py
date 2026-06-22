"""
🖼️ ImageGenService — Service unifié de génération/édition d'images multi-provider.

Providers supportés :
  - Google Gemini (generateContent, responseModalities IMAGE)
  - OpenAI (Images API — gpt-image-1.5, gpt-image-1-mini)
  - BFL FLUX (flux-2-max/pro/flex/klein, flux-schnell, flux-kontext)
  - Stability AI (stable-image-ultra/core, sd3.5-*)
  - Ideogram (ideogram-v3-quality/balanced/turbo)
  - Recraft (recraft-v4, recraft-v4-svg)
  - Replicate (seedream, wan, qwen, hunyuan, etc.)
  - HuggingFace Inference (sdxl, flux-schnell — gratuit, lent)
  - xAI (grok-imagine-image)
  - MiniMax (image-01)
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from src.utils.paths import WORKSPACE_DIR

# ── Constants ──────────────────────────────────────────────────────────────

GENERATED_IMAGES_DIR: Path = Path(
    os.getenv("LUMENA_GENERATED_IMAGES_DIR", str(WORKSPACE_DIR / "images"))
)

_HTTPX_TIMEOUT = float(os.getenv("LUMENA_IMAGE_GEN_TIMEOUT", "180"))


def _safe_error_summary(exc: Exception) -> str:
    """Résumé d'erreur sans URL, bearer token ni clé API."""
    if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        reason = getattr(response, "reason_phrase", "") or ""
        return f"http_status:{status_code}:{reason}".rstrip(":")
    if httpx is not None and isinstance(exc, httpx.RequestError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            return f"http_request_error:{status_code}"
        return type(exc).__name__
    text = str(exc) or type(exc).__name__
    text = re.sub(
        r"([?&](?:key|api_key|token|access_token|client_secret)=)[^&\s]+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return text[:240]


_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "webp", "svg", "gif"})


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImageResult:
    """Résultat d'une génération/édition d'image."""
    data: bytes
    format: str  # "png", "jpeg", "webp", "svg"
    width: int  # 0 pour SVG
    height: int  # 0 pour SVG
    provider: str
    model: str
    cost_estimate: float  # USD, 0.0 pour gratuits
    generation_time_ms: int
    prompt_used: str
    seed: Optional[int] = None


@dataclass(frozen=True)
class ComposeAsset:
    """Image à intégrer dans une composition multi-images.

    Rôles:
      - subject  : personnage/objet principal (doit être reconnaissable)
      - object   : accessoire/élément secondaire à intégrer naturellement
      - background : décor/ambiance de fond
      - foreground : élément de premier plan (devant le sujet)
      - sky      : ciel/arrière-plan supérieur à remplacer
    """
    path: str
    role: str  # subject | object | background | foreground | sky
    description: str = ""


@dataclass(frozen=True)
class ThumbnailPlan:
    """Plan structuré pour une miniature pro (pipeline 2 étapes)."""
    headlines: List[str]
    visual_prompt: str
    colors: List[str]
    composition_notes: str
    emotion: str


class ImageGenError(Exception):
    """Erreur de génération d'images."""
    pass


# ── Model → provider mapping ──────────────────────────────────────────────

_MODEL_PROVIDER: Dict[str, str] = {
    # Google Gemini
    "gemini-3.1-flash-image": "gemini",
    "gemini-3-pro-image": "gemini",
    "gemini-2.5-flash-image": "gemini",
    # Z.AI
    "cogview-4": "zai",
    "glm-image": "zai",
    # OpenAI
    "gpt-image-2": "openai",
    "gpt-image-1.5": "openai",
    "gpt-image-1-mini": "openai",
    # BFL FLUX
    "flux-2-max": "flux",
    "flux-2-pro": "flux",
    "flux-2-flex": "flux",
    "flux-2-klein-4b": "flux",
    "flux-2-klein-9b": "flux",
    "flux-schnell": "flux",
    "flux-kontext-pro": "flux",
    "flux-kontext-max": "flux",
    "flux-1.1-pro-ultra": "flux",
    # Stability AI
    "stable-image-ultra": "stability",
    "stable-image-core": "stability",
    "sd3.5-large": "stability",
    "sd3.5-large-turbo": "stability",
    "sd3.5-medium": "stability",
    "sd3.5-flash": "stability",
    # Google Imagen
    "imagen-4-ultra": "imagen",
    "imagen-4": "imagen",
    "imagen-4-fast": "imagen",
    # Ideogram
    "ideogram-v3-quality": "ideogram",
    "ideogram-v3-balanced": "ideogram",
    "ideogram-v3-turbo": "ideogram",
    "ideogram-v4-quality": "ideogram",
    "ideogram-v4": "ideogram",
    "ideogram-v4-turbo": "ideogram",
    # Recraft
    "recraft-v4": "recraft",
    "recraft-v4-svg": "recraft",
    # xAI
    "grok-imagine-image":         "xai",
    "grok-imagine-image-quality": "xai",
    "grok-imagine-image-pro":     "xai",
    # Replicate hosted
    "seedream-5-lite": "replicate",
    "seedream-4.5": "replicate",
    "wan-2.7-image-pro": "replicate",
    "qwen-image": "replicate",
    "hunyuan-image-3": "replicate",
    # MiniMax
    "minimax-image-01": "minimax",
    # HuggingFace
    "huggingface-sdxl": "huggingface",
}

# ── Model catalog — métadonnées complètes pour chaque modèle ──────────────

@dataclass(frozen=True)
class ModelInfo:
    """Fiche technique d'un modèle de génération d'images."""
    name: str
    provider: str
    quality: int        # 1-10 (10 = meilleur rendu)
    speed: int          # 1-10 (10 = plus rapide)
    cost_per_image: float  # USD estimé par génération 1024x1024
    free: bool
    max_resolution: str  # ex "4096x4096"
    styles: List[str]   # points forts stylistiques
    strengths: str      # phrase courte: ce qu'il fait le mieux
    weaknesses: str     # phrase courte: ses limites
    capabilities: List[str]  # ["text-to-image", "image-edit", "inpaint", "svg", "upscale", ...]
    best_for: str       # cas d'usage idéal en 1 phrase


_MODEL_CATALOG: Dict[str, ModelInfo] = {
    # ── Google Gemini (gratuit, multimodal) ──
    "gemini-3.1-flash-image": ModelInfo(
        name="Gemini 3.1 Flash Image", provider="gemini", quality=7, speed=9,
        cost_per_image=0.0, free=True, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "conceptuel"],
        strengths="Rapide, gratuit, bon en multimodal (edit + texte), comprend le contexte",
        weaknesses="Moins de détails fins que FLUX/SDXL, pas de contrôle seed",
        capabilities=["text-to-image", "image-edit"],
        best_for="Prototypage rapide, édition légère, images conceptuelles",
    ),
    "gemini-3-pro-image": ModelInfo(
        name="Gemini 3 Pro Image", provider="gemini", quality=8, speed=7,
        cost_per_image=0.0, free=True, max_resolution="2048x2048",
        styles=["photoréaliste", "artistique", "conceptuel"],
        strengths="Meilleure qualité que Flash, gratuit, bonne compréhension prompt",
        weaknesses="Plus lent que Flash, rate-limited",
        capabilities=["text-to-image", "image-edit"],
        best_for="Visuels de qualité sans frais, édition contextuelle",
    ),
    "gemini-2.5-flash-image": ModelInfo(
        name="Gemini 2.5 Flash Image", provider="gemini", quality=6, speed=9,
        cost_per_image=0.0, free=True, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Ultra rapide, gratuit, bon fallback",
        weaknesses="Qualité inférieure aux modèles v3",
        capabilities=["text-to-image", "image-edit"],
        best_for="Fallback rapide et gratuit",
    ),
    # ── Z.AI ──
    "cogview-4": ModelInfo(
        name="CogView-4", provider="zai", quality=7, speed=7,
        cost_per_image=0.01, free=False, max_resolution="2048x2048",
        styles=["illustration", "publicite", "bilingue"],
        strengths="Bon suivi de prompt en anglais et chinois a faible cout",
        weaknesses="Pas de retouche image et moins precise que GLM-Image pour les affiches",
        capabilities=["text-to-image"],
        best_for="Illustrations et visuels publicitaires a petit budget",
    ),
    "glm-image": ModelInfo(
        name="GLM-Image", provider="zai", quality=8, speed=6,
        cost_per_image=0.015, free=False, max_resolution="2048x2048",
        styles=["affiche", "diagramme", "typographie", "illustration"],
        strengths="Excellent suivi des instructions et rendu de texte dans les images",
        weaknesses="Generation uniquement, pas de retouche image ni de seed exposee",
        capabilities=["text-to-image"],
        best_for="Affiches, diagrammes, visuels explicatifs et contenus avec texte",
    ),
    # ── OpenAI GPT-Image ──
    "gpt-image-2": ModelInfo(
        name="GPT Image 2", provider="openai", quality=10, speed=5,
        cost_per_image=0.12, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "typographie", "graphisme", "3D"],
        strengths="Dernier modèle OpenAI image, excellente qualité générale et typographie",
        weaknesses="Payant et à réserver aux rendus finaux quand les modèles gratuits/cheap ne suffisent pas",
        capabilities=["text-to-image"],
        best_for="Rendus premium, marketing, visuels finaux avec texte ou consignes complexes",
    ),
    "gpt-image-1.5": ModelInfo(
        name="GPT Image 1.5", provider="openai", quality=9, speed=6,
        cost_per_image=0.08, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "typographie", "graphisme", "3D"],
        strengths="Excellente qualité, très bon en texte/typographie dans l'image, cohérence stylistique",
        weaknesses="Payant (~$0.08/img), plus lent que FLUX",
        capabilities=["text-to-image"],
        best_for="Images marketing/pro avec texte incrusté, visuels haute qualité",
    ),
    "gpt-image-1-mini": ModelInfo(
        name="GPT Image 1 Mini", provider="openai", quality=7, speed=8,
        cost_per_image=0.02, free=False, max_resolution="1024x1024",
        styles=["photoréaliste", "illustration", "typographie"],
        strengths="Bon rapport qualité/prix, texte dans l'image correct",
        weaknesses="Résolution max 1024, moins de détails que v1.5",
        capabilities=["text-to-image"],
        best_for="Génération rapide pas chère avec texte",
    ),
    # ── BFL FLUX ──
    "flux-2-max": ModelInfo(
        name="FLUX 2 Max", provider="flux", quality=10, speed=4,
        cost_per_image=0.10, free=False, max_resolution="4096x4096",
        styles=["photoréaliste", "cinématique", "artistique", "mode", "architecture"],
        strengths="Qualité state-of-the-art, détails incroyables, textures ultra réalistes",
        weaknesses="Lent (~30s), le plus cher du lineup FLUX",
        capabilities=["text-to-image"],
        best_for="Visuels haut de gamme, portfolio, photographie d'art",
    ),
    "flux-2-pro": ModelInfo(
        name="FLUX 2 Pro", provider="flux", quality=9, speed=6,
        cost_per_image=0.05, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "cinématique", "artistique"],
        strengths="Excellent équilibre qualité/vitesse, très polyvalent",
        weaknesses="Payant, pas aussi détaillé que Max en 4K",
        capabilities=["text-to-image"],
        best_for="Usage quotidien haute qualité, meilleur rapport qualité/prix FLUX",
    ),
    "flux-2-flex": ModelInfo(
        name="FLUX 2 Flex", provider="flux", quality=8, speed=7,
        cost_per_image=0.03, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "variété"],
        strengths="Flexible sur les styles, bonne vitesse, prix modéré",
        weaknesses="Un cran en dessous de Pro pour le photoréalisme pur",
        capabilities=["text-to-image"],
        best_for="Contenus variés, exploration de styles",
    ),
    "flux-2-klein-4b": ModelInfo(
        name="FLUX 2 Klein 4B", provider="flux", quality=6, speed=9,
        cost_per_image=0.01, free=False, max_resolution="1024x1024",
        styles=["photoréaliste", "illustration"],
        strengths="Très rapide et très bon marché, modèle distillé compact",
        weaknesses="Qualité réduite, max 1024px",
        capabilities=["text-to-image"],
        best_for="Prototypage rapide, vignettes, brouillons",
    ),
    "flux-2-klein-9b": ModelInfo(
        name="FLUX 2 Klein 9B", provider="flux", quality=7, speed=8,
        cost_per_image=0.02, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Bon compromis Klein: meilleure qualité que 4B, encore rapide",
        weaknesses="Pas aussi détaillé que Pro/Max",
        capabilities=["text-to-image"],
        best_for="Génération modérée avec budget limité",
    ),
    "flux-schnell": ModelInfo(
        name="FLUX Schnell", provider="flux", quality=5, speed=10,
        cost_per_image=0.003, free=False, max_resolution="1024x1024",
        styles=["photoréaliste", "illustration"],
        strengths="Le plus rapide (~1-2s), ultra bon marché",
        weaknesses="Qualité la plus basse de la gamme FLUX, artefacts possibles",
        capabilities=["text-to-image"],
        best_for="Tests rapides, miniatures, usage à haut volume",
    ),
    "flux-kontext-pro": ModelInfo(
        name="FLUX Kontext Pro", provider="flux", quality=9, speed=5,
        cost_per_image=0.06, free=False, max_resolution="2048x2048",
        styles=["édition contextuelle", "style transfer", "personnage"],
        strengths="Édition contextuelle d'images, maintien de cohérence sujet/personnage",
        weaknesses="Nécessite image de référence pour être pleinement utile",
        capabilities=["text-to-image", "image-edit"],
        best_for="Variation d'images existantes, maintien de personnage cohérent",
    ),
    "flux-kontext-max": ModelInfo(
        name="FLUX Kontext Max", provider="flux", quality=10, speed=4,
        cost_per_image=0.10, free=False, max_resolution="4096x4096",
        styles=["édition contextuelle", "style transfer", "personnage"],
        strengths="Meilleure qualité d'édition contextuelle au monde",
        weaknesses="Lent et cher, overkill pour de la génération simple",
        capabilities=["text-to-image", "image-edit"],
        best_for="Édition pro d'images de référence, cohérence maximale",
    ),
    "flux-1.1-pro-ultra": ModelInfo(
        name="FLUX 1.1 Pro Ultra", provider="flux", quality=8, speed=5,
        cost_per_image=0.06, free=False, max_resolution="4096x4096",
        styles=["photoréaliste", "ultra haute résolution"],
        strengths="4K natif, bonne qualité, ancienne génération éprouvée",
        weaknesses="Remplacé par FLUX 2 en qualité globale",
        capabilities=["text-to-image"],
        best_for="Images 4K natives",
    ),
    # ── Stability AI ──
    "stable-image-ultra": ModelInfo(
        name="Stable Image Ultra", provider="stability", quality=9, speed=5,
        cost_per_image=0.08, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "cinématique", "concept art", "3D"],
        strengths="Flagship Stability, excellente qualité, styles variés, edit/upscale intégrés",
        weaknesses="Payant, API de la génération précédente",
        capabilities=["text-to-image", "image-edit", "inpaint", "outpaint", "upscale", "remove-background", "replace-background", "sketch-to-image"],
        best_for="Pipeline complet (gen→edit→upscale), meilleur si on a aussi besoin d'édition",
    ),
    "stable-image-core": ModelInfo(
        name="Stable Image Core", provider="stability", quality=7, speed=7,
        cost_per_image=0.03, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration", "graphisme"],
        strengths="Bon rapport qualité/prix Stability, rapide",
        weaknesses="Moins détaillé que Ultra",
        capabilities=["text-to-image", "image-edit", "inpaint", "upscale", "remove-background"],
        best_for="Génération Stability abordable",
    ),
    "sd3.5-large": ModelInfo(
        name="Stable Diffusion 3.5 Large", provider="stability", quality=8, speed=6,
        cost_per_image=0.065, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "artistique", "abstrait", "concept art"],
        strengths="Très polyvalent, bon en styles artistiques, compositing complexe",
        weaknesses="Plus lent que Core, consomme plus de crédits",
        capabilities=["text-to-image"],
        best_for="Illustrations artistiques, concept art, compositions complexes",
    ),
    "sd3.5-large-turbo": ModelInfo(
        name="SD 3.5 Large Turbo", provider="stability", quality=7, speed=8,
        cost_per_image=0.04, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="SD 3.5 accéléré, bon compromis vitesse/qualité",
        weaknesses="Slightly moins détaillé que le Large standard",
        capabilities=["text-to-image"],
        best_for="SD 3.5 quand la vitesse compte",
    ),
    "sd3.5-medium": ModelInfo(
        name="SD 3.5 Medium", provider="stability", quality=6, speed=8,
        cost_per_image=0.035, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Bon pour le volume, décent en qualité",
        weaknesses="Pas aussi détaillé que Large",
        capabilities=["text-to-image"],
        best_for="Usage volume modéré",
    ),
    "sd3.5-flash": ModelInfo(
        name="SD 3.5 Flash", provider="stability", quality=5, speed=9,
        cost_per_image=0.02, free=False, max_resolution="1024x1024",
        styles=["photoréaliste", "illustration"],
        strengths="Le plus rapide de la gamme SD 3.5",
        weaknesses="Qualité la plus basse Stability",
        capabilities=["text-to-image"],
        best_for="Brouillons rapides avec Stability",
    ),
    # ── Google Imagen ──
    "imagen-4-ultra": ModelInfo(
        name="Imagen 4 Ultra", provider="imagen", quality=10, speed=4,
        cost_per_image=0.06, free=False, max_resolution="4096x4096",
        styles=["photoréaliste", "cinématique", "typographie", "3D"],
        strengths="State-of-the-art Google, texte dans image excellent, photoréalisme top",
        weaknesses="Lent, nécessite Google Cloud billing",
        capabilities=["text-to-image"],
        best_for="Visuels photoréalistes premium, images avec texte",
    ),
    "imagen-4": ModelInfo(
        name="Imagen 4", provider="imagen", quality=9, speed=6,
        cost_per_image=0.04, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "typographie"],
        strengths="Très bonne qualité, bon texte, prix modéré",
        weaknesses="Nécessite Google Cloud billing",
        capabilities=["text-to-image"],
        best_for="Usage quotidien haute qualité chez Google",
    ),
    "imagen-4-fast": ModelInfo(
        name="Imagen 4 Fast", provider="imagen", quality=7, speed=8,
        cost_per_image=0.02, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Rapide et pas cher pour du Google Imagen",
        weaknesses="Qualité en dessous de Imagen 4 standard",
        capabilities=["text-to-image"],
        best_for="Génération rapide dans l'écosystème Google",
    ),
    # ── Ideogram ──
    "ideogram-v4-quality": ModelInfo(
        name="Ideogram V4 Quality", provider="ideogram", quality=10, speed=5,
        cost_per_image=0.08, free=False, max_resolution="2048x2048",
        styles=["typographie", "logo", "poster", "graphisme", "illustration"],
        strengths="Excellent sur la typographie, logos et compositions graphiques",
        weaknesses="Payant, moins orienté édition photo que FLUX Kontext/Stability",
        capabilities=["text-to-image"],
        best_for="Affiches, textes dans l'image, logos et visuels graphiques premium",
    ),
    "ideogram-v4": ModelInfo(
        name="Ideogram V4", provider="ideogram", quality=9, speed=7,
        cost_per_image=0.05, free=False, max_resolution="2048x2048",
        styles=["typographie", "logo", "poster", "graphisme"],
        strengths="Très bon compromis qualité/prix pour images avec texte",
        weaknesses="Payant, moins rapide que Turbo",
        capabilities=["text-to-image"],
        best_for="Visuels avec typographie en production courante",
    ),
    "ideogram-v4-turbo": ModelInfo(
        name="Ideogram V4 Turbo", provider="ideogram", quality=7, speed=9,
        cost_per_image=0.03, free=False, max_resolution="1536x1536",
        styles=["typographie", "graphisme", "illustration"],
        strengths="Rapide pour brouillons typographiques et variations",
        weaknesses="Qualité inférieure au mode Quality",
        capabilities=["text-to-image"],
        best_for="Brouillons rapides avec texte ou logo",
    ),
    "ideogram-v3-quality": ModelInfo(
        name="Ideogram V3 Quality", provider="ideogram", quality=9, speed=5,
        cost_per_image=0.08, free=False, max_resolution="2048x2048",
        styles=["typographie", "logo", "graphisme", "affiche", "photoréaliste"],
        strengths="MEILLEUR pour le texte dans les images, typographie parfaite, logos",
        weaknesses="Plus cher, lent",
        capabilities=["text-to-image"],
        best_for="Logos, affiches, tout visuel avec du texte lisible",
    ),
    "ideogram-v3-balanced": ModelInfo(
        name="Ideogram V3 Balanced", provider="ideogram", quality=8, speed=7,
        cost_per_image=0.04, free=False, max_resolution="2048x2048",
        styles=["typographie", "logo", "graphisme", "photoréaliste"],
        strengths="Bon équilibre qualité/vitesse, bon texte dans image",
        weaknesses="Pas aussi détaillé que Quality mode",
        capabilities=["text-to-image"],
        best_for="Usage quotidien avec texte intégré",
    ),
    "ideogram-v3-turbo": ModelInfo(
        name="Ideogram V3 Turbo", provider="ideogram", quality=6, speed=9,
        cost_per_image=0.02, free=False, max_resolution="1024x1024",
        styles=["typographie", "graphisme"],
        strengths="Rapide avec du bon texte incrusté",
        weaknesses="Résolution limitée, moins détaillé",
        capabilities=["text-to-image"],
        best_for="Brouillons rapides avec texte",
    ),
    # ── Recraft ──
    "recraft-v4": ModelInfo(
        name="Recraft V4", provider="recraft", quality=9, speed=6,
        cost_per_image=0.04, free=False, max_resolution="2048x2048",
        styles=["vectoriel", "illustration", "icône", "brand", "flat design"],
        strengths="MEILLEUR pour le design vectoriel, illustrations pro, style cohérent, brand design",
        weaknesses="Moins bon en photoréalisme pur",
        capabilities=["text-to-image"],
        best_for="Illustrations, icônes, assets de design, branding",
    ),
    "recraft-v4-svg": ModelInfo(
        name="Recraft V4 SVG", provider="recraft", quality=9, speed=6,
        cost_per_image=0.06, free=False, max_resolution="scalable",
        styles=["vectoriel", "SVG", "icône", "logo"],
        strengths="UNIQUE: génère de vrais SVG vectoriels, scalables à l'infini",
        weaknesses="Limité au style vectoriel, pas de photoréalisme",
        capabilities=["text-to-image", "svg"],
        best_for="Logos SVG, icônes vectorielles, assets scalables",
    ),
    # ── xAI ──
    "grok-imagine-image": ModelInfo(
        name="Grok Imagine", provider="xai", quality=7, speed=7,
        cost_per_image=0.02, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "créatif", "mème"],
        strengths="Bon en créativité et humour, peu de restrictions contenu",
        weaknesses="Qualité générale en dessous de FLUX/Imagen",
        capabilities=["text-to-image"],
        best_for="Images créatives, mèmes, contenu décalé",
    ),
    "grok-imagine-image-quality": ModelInfo(
        name="Grok Imagine Quality", provider="xai", quality=8, speed=6,
        cost_per_image=0.05, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "créatif", "haute qualité"],
        strengths="Qualité supérieure, peu de restrictions contenu",
        weaknesses="Plus lent que la version standard",
        capabilities=["text-to-image"],
        best_for="Images haute qualité, illustrations détaillées",
    ),
    "grok-imagine-image-pro": ModelInfo(
        name="Grok Imagine Pro", provider="xai", quality=9, speed=4,
        cost_per_image=0.07, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "professionnel", "artistique"],
        strengths="Meilleure qualité xAI, résultats pro",
        weaknesses="Lent, rate limit faible (30 RPM)",
        capabilities=["text-to-image"],
        best_for="Images professionnelles, qualité maximale xAI",
    ),
    # ── Replicate ──
    "seedream-5-lite": ModelInfo(
        name="Seedream 5 Lite (ByteDance)", provider="replicate", quality=8, speed=6,
        cost_per_image=0.03, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "artistique"],
        strengths="Bonne qualité open-source hébergé, style asiatique raffiné",
        weaknesses="Via Replicate (cold start possible)",
        capabilities=["text-to-image"],
        best_for="Alternative open-source de qualité",
    ),
    "seedream-4.5": ModelInfo(
        name="Seedream 4.5 (ByteDance)", provider="replicate", quality=7, speed=6,
        cost_per_image=0.03, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Bonne qualité, ancienne version éprouvée",
        weaknesses="Remplacé par Seedream 5",
        capabilities=["text-to-image"],
        best_for="Fallback Replicate fiable",
    ),
    "wan-2.7-image-pro": ModelInfo(
        name="Wan 2.7 Image Pro (Alibaba)", provider="replicate", quality=7, speed=5,
        cost_per_image=0.04, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "illustration", "style chinois"],
        strengths="Bonne qualité Alibaba, bon en paysages et architecture",
        weaknesses="Lent, via Replicate",
        capabilities=["text-to-image"],
        best_for="Paysages, architecture, illustrations culturelles",
    ),
    "qwen-image": ModelInfo(
        name="Qwen Image (Alibaba)", provider="replicate", quality=7, speed=6,
        cost_per_image=0.03, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration"],
        strengths="Bonne compréhension du prompt, style varié",
        weaknesses="Via Replicate, cold start",
        capabilities=["text-to-image"],
        best_for="Génération variée via Replicate",
    ),
    "hunyuan-image-3": ModelInfo(
        name="Hunyuan Image 3 (Tencent)", provider="replicate", quality=8, speed=5,
        cost_per_image=0.04, free=False, max_resolution="2048x2048",
        styles=["photoréaliste", "cinématique", "concept art"],
        strengths="Haute qualité Tencent, bon en cinématique et concept art",
        weaknesses="Lent via Replicate",
        capabilities=["text-to-image"],
        best_for="Concept art, visuels cinématiques",
    ),
    # ── MiniMax ──
    "minimax-image-01": ModelInfo(
        name="MiniMax Image 01", provider="minimax", quality=7, speed=7,
        cost_per_image=0.02, free=False, max_resolution="1536x1536",
        styles=["photoréaliste", "illustration", "manga"],
        strengths="Bon marché, style varié, bon en anime/manga",
        weaknesses="Qualité générale en dessous des leaders",
        capabilities=["text-to-image"],
        best_for="Images manga/anime, usage pas cher",
    ),
    # ── HuggingFace (gratuit, lent) ──
    "huggingface-sdxl": ModelInfo(
        name="HuggingFace SDXL", provider="huggingface", quality=6, speed=3,
        cost_per_image=0.0, free=True, max_resolution="1024x1024",
        styles=["photoréaliste", "illustration", "artistique"],
        strengths="Gratuit, SDXL open-source, pas de limites de contenu",
        weaknesses="Lent (queue HF Inference), résolution 1024 max, file d'attente",
        capabilities=["text-to-image"],
        best_for="Fallback gratuit quand tout le reste échoue",
    ),
}


def get_model_info(model: str) -> Optional[ModelInfo]:
    """Retourne la fiche technique d'un modèle, ou None."""
    return _MODEL_CATALOG.get(model)


def get_model_catalog_summary() -> str:
    """Résumé formaté du catalogue pour injection dans le prompt LLM."""
    lines = ["Modèles image disponibles (qualité ★/10, vitesse ⚡/10, $ = coût/image):"]
    # Trier par qualité décroissante
    for name, info in sorted(_MODEL_CATALOG.items(), key=lambda x: (-x[1].quality, x[1].cost_per_image)):
        price = "GRATUIT" if info.free else f"${info.cost_per_image:.3f}"
        lines.append(
            f"  • {name}: ★{info.quality} ⚡{info.speed} {price} — {info.strengths}"
        )
    return "\n".join(lines)


# Fallback auto cost-first: local (when a real local backend exists) -> free
# -> paid models in ascending cost order. Lumena has no local image generator
# in this service yet, so the executable cascade starts with cloud free tiers.
_PROVIDER_FALLBACK_ORDER: List[str] = [
    # Tier 0 — free tiers.
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "huggingface-sdxl",
    # Paid, strictly ascending by current per-image list price.
    "flux-schnell",
    "cogview-4",
    "flux-2-klein-4b",
    "glm-image",
    "flux-2-klein-9b",
    "gpt-image-1-mini",
    "ideogram-v3-turbo",
    "sd3.5-flash",
    "minimax-image-01",
    "imagen-4-fast",
    "grok-imagine-image",
    "ideogram-v4-turbo",
    "stable-image-core",
    "seedream-5-lite",
    "seedream-4.5",
    "qwen-image",
    "flux-2-flex",
    "sd3.5-medium",
    "sd3.5-large-turbo",
    "imagen-4",
    "ideogram-v3-balanced",
    "recraft-v4",
    "wan-2.7-image-pro",
    "hunyuan-image-3",
    "flux-2-pro",
    "ideogram-v4",
    "grok-imagine-image-quality",
    "recraft-v4-svg",
    "flux-kontext-pro",
    "flux-1.1-pro-ultra",
    "imagen-4-ultra",
    "sd3.5-large",
    "grok-imagine-image-pro",
    "gpt-image-1.5",
    "ideogram-v4-quality",
    "ideogram-v3-quality",
    "stable-image-ultra",
    "flux-kontext-max",
    "flux-2-max",
    "gpt-image-2",
]

# Provider → env var clé API
_PROVIDER_API_KEY: Dict[str, str] = {
    "gemini": "GOOGLE_API_KEY",
    "imagen": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "flux": "BFL_API_KEY",
    "stability": "STABILITY_API_KEY",
    "ideogram": "IDEOGRAM_API_KEY",
    "recraft": "RECRAFT_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "huggingface": "HUGGINGFACE_TOKEN",
    "xai": "XAI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "zai": "ZAI_API_KEY",
}

# Stability AI edit endpoints
_STABILITY_EDIT_ENDPOINTS: Dict[str, str] = {
    "inpaint": "/v2beta/stable-image/edit/inpaint",
    "outpaint": "/v2beta/stable-image/edit/outpaint",
    "erase": "/v2beta/stable-image/edit/erase",
    "search-replace": "/v2beta/stable-image/edit/search-and-replace",
    "search-recolor": "/v2beta/stable-image/edit/search-and-recolor",
    "remove-background": "/v2beta/stable-image/edit/remove-background",
    "replace-background": "/v2beta/stable-image/edit/replace-background-and-relight",
    "sketch": "/v2beta/stable-image/control/sketch",
    "style-transfer": "/v2beta/stable-image/control/style-transfer",
    "upscale-fast": "/v2beta/stable-image/upscale/fast",
    "upscale-conservative": "/v2beta/stable-image/upscale/conservative",
    "upscale-creative": "/v2beta/stable-image/upscale/creative",
}

# Stability AI generation endpoints
_STABILITY_GEN_ENDPOINTS: Dict[str, str] = {
    "stable-image-ultra": "/v2beta/stable-image/generate/ultra",
    "stable-image-core": "/v2beta/stable-image/generate/core",
    "sd3.5-large": "/v2beta/stable-image/generate/sd3",
    "sd3.5-large-turbo": "/v2beta/stable-image/generate/sd3",
    "sd3.5-medium": "/v2beta/stable-image/generate/sd3",
    "sd3.5-flash": "/v2beta/stable-image/generate/sd3",
}

# BFL FLUX model → API path
_FLUX_API_PATHS: Dict[str, str] = {
    "flux-2-max": "/v1/flux-2-max",
    "flux-2-pro": "/v1/flux-2-pro",
    "flux-2-flex": "/v1/flux-2-flex",
    "flux-2-klein-4b": "/v1/flux-2-klein-4b",
    "flux-2-klein-9b": "/v1/flux-2-klein-9b",
    "flux-schnell": "/v1/flux-schnell",
    "flux-kontext-pro": "/v1/flux-kontext-pro",
    "flux-kontext-max": "/v1/flux-kontext-max",
    "flux-1.1-pro-ultra": "/v1/flux-pro-1.1-ultra",
}

# Replicate model → version mapping
_REPLICATE_VERSIONS: Dict[str, str] = {
    "seedream-5-lite": "bytedance/seedream-5-lite",
    "seedream-4.5": "bytedance/seedream-4.5",
    "wan-2.7-image-pro": "alibaba/wan-2.7-image-pro",
    "qwen-image": "qwen/qwen-image",
    "hunyuan-image-3": "tencent/hunyuan-image-3",
}


# ── Prompt enrichment templates (P6 + P8) ─────────────────────────────────

_PROMPT_TEMPLATES: Dict[str, str] = {
    "thumbnail": (
        "YouTube thumbnail, high CTR, dramatic lighting, strong emotion, "
        "bold large text overlay, vibrant colors, cinematic. "
    ),
    "logo": (
        "Minimalist logo design, clean lines, scalable vector style, "
        "professional brand identity, simple geometric shapes. "
    ),
    "product": (
        "Product photography, studio lighting, white background, "
        "high detail, professional catalog shot. "
    ),
    "portrait": (
        "Professional portrait, Rembrandt lighting, shallow depth of field, "
        "studio quality, sharp focus. "
    ),
    "illustration": (
        "Digital illustration, cohesive color palette, clean design, "
        "stylized art, professional quality. "
    ),
    "icon": (
        "App icon design, rounded corners, 3D colorful style, "
        "simple recognizable shape, white background. "
    ),
    "banner": (
        "Web banner 16:9, hero image, space for text overlay, "
        "professional marketing visual. "
    ),
    "infographic": (
        "Clean infographic layout, data visualization, structured layout, "
        "readable text, professional design. "
    ),
}


# ── Platform-specific prompt rules (inspiré Webcraft Minia) ──────────────

_PLATFORM_PROMPTS: Dict[str, Dict[str, Any]] = {
    "youtube_thumbnail": {
        "dimensions": "1280x720",
        "aspect": "16:9",
        "rules": (
            "MANDATORY: DO NOT render any text, title, or typography in the image. "
            "Leave 30% safe margin at top and right for text overlay. "
            "High contrast, vibrant saturated colors, dramatic lighting. "
            "Close-up facial expression or single focal point. "
            "Clean uncluttered composition. Maximum visual impact at small size. "
            "Bold color blocking, no fine details that disappear at thumbnail size."
        ),
    },
    "youtube_banner": {
        "dimensions": "2560x1440",
        "aspect": "16:9",
        "rules": (
            "Channel banner, centered safe zone 1546x423 for mobile crop. "
            "Clean design, brand colors, no small text. "
            "Professional, polished, visually cohesive with channel identity."
        ),
    },
    "tiktok_cover": {
        "dimensions": "1080x1920",
        "aspect": "9:16",
        "rules": (
            "Vertical composition, bold and eye-catching. "
            "DO NOT render text in the image. "
            "Leave top 20% and bottom 30% clear for UI overlays. "
            "Bright colors, high energy, trendy aesthetic. "
            "Single clear focal point, works at small preview size."
        ),
    },
    "instagram_post": {
        "dimensions": "1080x1080",
        "aspect": "1:1",
        "rules": (
            "Square format, Instagram aesthetic. "
            "Clean composition, balanced colors, visually appealing. "
            "DO NOT render text. Works as standalone visual. "
            "Consider grid harmony — clean edges, consistent style."
        ),
    },
    "instagram_story": {
        "dimensions": "1080x1920",
        "aspect": "9:16",
        "rules": (
            "Vertical 9:16, immersive full-screen visual. "
            "DO NOT render text. Leave top 15% and bottom 25% for stickers/text. "
            "Bold, colorful, attention-grabbing in first 0.5s."
        ),
    },
    "linkedin_post": {
        "dimensions": "1200x627",
        "aspect": "1.91:1",
        "rules": (
            "Professional, corporate-friendly aesthetic. "
            "Clean design, muted or brand-aligned colors. "
            "DO NOT render text in the image. "
            "Business context, credible and authoritative feel."
        ),
    },
    "twitter_post": {
        "dimensions": "1200x675",
        "aspect": "16:9",
        "rules": (
            "Horizontal, scroll-stopping visual. "
            "High contrast, clear subject, works at small preview. "
            "DO NOT render text. Bold colors, simple composition."
        ),
    },
    "podcast_cover": {
        "dimensions": "3000x3000",
        "aspect": "1:1",
        "rules": (
            "Square podcast artwork, must be recognizable at 55x55px. "
            "DO NOT render text (title added separately). "
            "Bold icon or illustration, high contrast, minimal detail. "
            "Brand colors, distinctive silhouette."
        ),
    },
}


def _slugify(text: str, max_len: int = 40) -> str:
    """Prompt → slug fichier."""
    slug = re.sub(r'[^\w\s-]', '', text.lower().strip())
    return re.sub(r'[\s-]+', '_', slug)[:max_len].rstrip('_') or "image"


def _parse_size(size: str) -> tuple[int, int]:
    """Parse '1024x1024' → (1024, 1024)."""
    parts = size.lower().split("x")
    if len(parts) == 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 1024, 1024


# ── Service ────────────────────────────────────────────────────────────────

class ImageGenService:
    """Service singleton de génération d'images multi-provider."""

    _instance: Optional["ImageGenService"] = None

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._http_loop_id: int = 0

    @classmethod
    def get_instance(cls) -> "ImageGenService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (tests)."""
        cls._instance = None

    async def _get_client(self) -> httpx.AsyncClient:
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = 0
        if (
            self._client is None
            or self._client.is_closed
            or (current_loop_id and current_loop_id != self._http_loop_id)
        ):
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = httpx.AsyncClient(timeout=_HTTPX_TIMEOUT)
            self._http_loop_id = current_loop_id
        return self._client

    def _has_api_key(self, provider: str) -> bool:
        env_var = _PROVIDER_API_KEY.get(provider, "")
        return bool(os.getenv(env_var, ""))

    def _get_api_key(self, provider: str) -> str:
        env_var = _PROVIDER_API_KEY.get(provider, "")
        key = os.getenv(env_var, "")
        if not key:
            raise ImageGenError(f"Clé API manquante: {env_var} (provider {provider})")
        return key

    def _get_model_provider(self, model: str) -> str:
        provider = _MODEL_PROVIDER.get(model)
        if not provider:
            raise ImageGenError(f"Modèle inconnu: {model}")
        return provider

    # ── Main entry points ─────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "auto",
        size: str = "",
        quality: str = "",
        style: str = "",
        template: str = "",
    ) -> ImageResult:
        """Génère une image depuis un prompt texte."""
        if not prompt or not prompt.strip():
            raise ImageGenError("Le prompt est vide.")

        # Defaults depuis env vars si non spécifié
        if not size:
            size = os.getenv("LUMENA_IMAGE_DEFAULT_SIZE", "1024x1024")
        if not quality:
            quality = os.getenv("LUMENA_IMAGE_DEFAULT_QUALITY", "hd")

        # Apply template prefix if specified
        final_prompt = prompt
        if template and template in _PROMPT_TEMPLATES:
            final_prompt = _PROMPT_TEMPLATES[template] + prompt

        brain_model = os.getenv("LUMENA_BRAIN_IMAGE_GEN", "auto")
        # Priorité: modèle préféré config > modèle demandé par le LLM > auto
        if brain_model != "auto":
            effective_model = brain_model
        elif model != "auto":
            effective_model = model
        else:
            effective_model = "auto"

        if effective_model == "auto":
            return await self._generate_auto(final_prompt, size=size, quality=quality, style=style)

        # Vérifier que le provider a une clé API avant de dispatcher
        try:
            provider = self._get_model_provider(effective_model)
        except ImageGenError:
            logger.warning("Modèle préféré inconnu: {} — fallback auto", effective_model)
            return await self._generate_auto(final_prompt, size=size, quality=quality, style=style)

        if not self._has_api_key(provider):
            logger.warning("Clé API manquante pour {} ({}), fallback auto", effective_model, provider)
            return await self._generate_auto(final_prompt, size=size, quality=quality, style=style)

        try:
            return await self._dispatch_generate(
                final_prompt, provider=provider, model=effective_model,
                size=size, quality=quality, style=style,
            )
        except Exception as e:
            logger.warning("Échec {} ({}): {} — fallback auto", effective_model, provider, _safe_error_summary(e))
            return await self._generate_auto(final_prompt, size=size, quality=quality, style=style)

    async def _generate_auto(
        self, prompt: str, *, size: str, quality: str, style: str,
    ) -> ImageResult:
        """Fallback auto: essaie chaque provider dans l'ordre."""
        last_error = None
        for model_name in _PROVIDER_FALLBACK_ORDER:
            provider = _MODEL_PROVIDER.get(model_name, "")
            if not self._has_api_key(provider):
                continue
            try:
                return await self._dispatch_generate(
                    prompt, provider=provider, model=model_name,
                    size=size, quality=quality, style=style,
                )
            except Exception as e:
                last_error = _safe_error_summary(e)
                logger.warning("ImageGen {} failed: {}, trying next...", model_name, last_error)
                continue
        raise ImageGenError(
            f"Aucun provider image disponible. Configurez au moins une clé API. "
            f"Dernière erreur: {last_error}"
        )

    async def _dispatch_generate(
        self, prompt: str, *, provider: str, model: str,
        size: str, quality: str, style: str,
    ) -> ImageResult:
        """Dispatch vers le bon provider."""
        t0 = time.monotonic()
        dispatch = {
            "gemini": self._generate_gemini,
            "openai": self._generate_openai,
            "flux": self._generate_flux,
            "stability": self._generate_stability,
            "imagen": self._generate_imagen,
            "ideogram": self._generate_ideogram,
            "recraft": self._generate_recraft,
            "replicate": self._generate_replicate,
            "huggingface": self._generate_huggingface,
            "xai": self._generate_xai,
            "minimax": self._generate_minimax,
            "zai": self._generate_zai,
        }
        fn = dispatch.get(provider)
        if fn is None:
            raise ImageGenError(f"Provider non supporté: {provider}")
        data, fmt, w, h, cost, seed = await fn(prompt, model=model, size=size, quality=quality, style=style)
        elapsed = int((time.monotonic() - t0) * 1000)
        return ImageResult(
            data=data, format=fmt, width=w, height=h,
            provider=provider, model=model, cost_estimate=cost,
            generation_time_ms=elapsed, prompt_used=prompt, seed=seed,
        )

    # ── Edit / Upscale / Remove BG ───────────────────────────────────────

    async def edit(
        self,
        image_path: str,
        prompt: str,
        *,
        mode: str = "inpaint",
        mask_prompt: str = "",
        model: str = "auto",
    ) -> ImageResult:
        """Édite une image existante (inpaint/outpaint/erase/search-replace)."""
        p = Path(image_path)
        if not p.exists():
            raise ImageGenError(f"Image introuvable: {image_path}")
        image_bytes = p.read_bytes()

        # Prefer Stability for edit operations
        if self._has_api_key("stability"):
            return await self._edit_stability(image_bytes, prompt, mode=mode, mask_prompt=mask_prompt)

        # Fallback: Gemini/OpenAI can edit via prompt
        if self._has_api_key("gemini"):
            combined = f"Edit this image: {prompt}"
            t0 = time.monotonic()
            data, fmt, w, h, cost, seed = await self._generate_gemini(
                combined, model="gemini-3.1-flash-image", size="1024x1024",
                quality="hd", style="", reference_image=image_bytes,
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            return ImageResult(
                data=data, format=fmt, width=w, height=h,
                provider="gemini", model="gemini-3.1-flash-image",
                cost_estimate=cost, generation_time_ms=elapsed,
                prompt_used=combined, seed=seed,
            )

        raise ImageGenError("Aucun provider d'édition disponible (STABILITY_API_KEY ou GOOGLE_API_KEY requis)")

    async def upscale(
        self,
        image_path: str,
        *,
        scale: int = 4,
        mode: str = "fast",
    ) -> ImageResult:
        """Upscale une image (fast/conservative/creative)."""
        p = Path(image_path)
        if not p.exists():
            raise ImageGenError(f"Image introuvable: {image_path}")
        image_bytes = p.read_bytes()

        if not self._has_api_key("stability"):
            raise ImageGenError("STABILITY_API_KEY requis pour l'upscale")

        endpoint_map = {
            "fast": "upscale-fast",
            "conservative": "upscale-conservative",
            "creative": "upscale-creative",
        }
        ep_key = endpoint_map.get(mode, "upscale-fast")
        endpoint = _STABILITY_EDIT_ENDPOINTS[ep_key]
        key = self._get_api_key("stability")
        client = await self._get_client()

        t0 = time.monotonic()
        files = {"image": ("image.png", image_bytes, "image/png")}
        data_fields: Dict[str, str] = {"output_format": "png"}
        if mode == "creative":
            data_fields["prompt"] = "enhance and upscale"

        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            files=files,
            data=data_fields,
        )
        resp.raise_for_status()
        elapsed = int((time.monotonic() - t0) * 1000)

        return ImageResult(
            data=resp.content, format="png", width=0, height=0,
            provider="stability", model=f"stability-{mode}-upscale",
            cost_estimate=0.01 if mode == "fast" else 0.25,
            generation_time_ms=elapsed, prompt_used=f"upscale {mode}",
        )

    async def remove_background(self, image_path: str) -> ImageResult:
        """Supprime le fond d'une image (Stability AI)."""
        p = Path(image_path)
        if not p.exists():
            raise ImageGenError(f"Image introuvable: {image_path}")
        image_bytes = p.read_bytes()

        if not self._has_api_key("stability"):
            raise ImageGenError("STABILITY_API_KEY requis pour remove_background")

        key = self._get_api_key("stability")
        client = await self._get_client()
        endpoint = _STABILITY_EDIT_ENDPOINTS["remove-background"]

        t0 = time.monotonic()
        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            files={"image": ("image.png", image_bytes, "image/png")},
            data={"output_format": "png"},
        )
        resp.raise_for_status()
        elapsed = int((time.monotonic() - t0) * 1000)

        return ImageResult(
            data=resp.content, format="png", width=0, height=0,
            provider="stability", model="stability-remove-background",
            cost_estimate=0.02, generation_time_ms=elapsed,
            prompt_used="remove background",
        )

    async def replace_background(
        self, image_path: str, background_prompt: str,
    ) -> ImageResult:
        """Remplace le fond + relight automatique (Stability AI)."""
        p = Path(image_path)
        if not p.exists():
            raise ImageGenError(f"Image introuvable: {image_path}")
        image_bytes = p.read_bytes()

        if not self._has_api_key("stability"):
            raise ImageGenError("STABILITY_API_KEY requis pour replace_background")

        key = self._get_api_key("stability")
        client = await self._get_client()
        endpoint = _STABILITY_EDIT_ENDPOINTS["replace-background"]

        t0 = time.monotonic()
        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            files={"image": ("image.png", image_bytes, "image/png")},
            data={"background_prompt": background_prompt, "output_format": "png"},
        )
        resp.raise_for_status()
        elapsed = int((time.monotonic() - t0) * 1000)

        return ImageResult(
            data=resp.content, format="png", width=0, height=0,
            provider="stability", model="stability-replace-background",
            cost_estimate=0.08, generation_time_ms=elapsed,
            prompt_used=background_prompt,
        )

    async def sketch_to_image(
        self, image_path: str, prompt: str, *, style: str = "",
    ) -> ImageResult:
        """Transforme un croquis en image (Stability AI)."""
        p = Path(image_path)
        if not p.exists():
            raise ImageGenError(f"Image introuvable: {image_path}")
        image_bytes = p.read_bytes()

        if not self._has_api_key("stability"):
            raise ImageGenError("STABILITY_API_KEY requis pour sketch_to_image")

        key = self._get_api_key("stability")
        client = await self._get_client()
        endpoint = _STABILITY_EDIT_ENDPOINTS["sketch"]

        t0 = time.monotonic()
        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            files={"image": ("sketch.png", image_bytes, "image/png")},
            data={"prompt": prompt, "output_format": "png"},
        )
        resp.raise_for_status()
        elapsed = int((time.monotonic() - t0) * 1000)

        return ImageResult(
            data=resp.content, format="png", width=0, height=0,
            provider="stability", model="stability-sketch",
            cost_estimate=0.03, generation_time_ms=elapsed,
            prompt_used=prompt,
        )

    # ── Sauvegarde workspace (P0 critique) ────────────────────────────────

    def save_to_workspace(self, result: ImageResult, slug: str) -> Path:
        """Sauvegarde l'image + métadonnées dans workspace/images/YYYY-MM-DD/"""
        today = datetime.now().strftime("%Y-%m-%d")
        day_dir = GENERATED_IMAGES_DIR / today
        day_dir.mkdir(parents=True, exist_ok=True)

        uid = uuid.uuid4().hex[:8]
        filename = f"{slug}_{uid}.{result.format}"
        filepath = day_dir / filename
        filepath.write_bytes(result.data)

        # Métadonnées associées
        try:
            from src.utils.persistence import atomic_write_json
            meta = {
                "filename": filename,
                "path": str(filepath),
                "prompt": result.prompt_used,
                "provider": result.provider,
                "model": result.model,
                "size": f"{result.width}x{result.height}",
                "format": result.format,
                "cost_usd": result.cost_estimate,
                "generation_time_ms": result.generation_time_ms,
                "seed": result.seed,
                "created_at": datetime.now().isoformat(),
            }
            atomic_write_json(filepath.with_suffix(".meta.json"), meta)
        except Exception as exc:
            logger.debug(f"image meta write skipped: {exc}")

        return filepath

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Retourne la liste des modèles avec métadonnées complètes."""
        models = []
        for model_name, provider in _MODEL_PROVIDER.items():
            available = self._has_api_key(provider)
            info = _MODEL_CATALOG.get(model_name)
            entry: Dict[str, Any] = {
                "name": model_name,
                "provider": provider,
                "available": available,
                "free": info.free if info else provider in ("gemini", "huggingface"),
            }
            if info:
                entry.update({
                    "display_name": info.name,
                    "quality": info.quality,
                    "speed": info.speed,
                    "cost_per_image": info.cost_per_image,
                    "max_resolution": info.max_resolution,
                    "styles": info.styles,
                    "strengths": info.strengths,
                    "weaknesses": info.weaknesses,
                    "capabilities": info.capabilities,
                    "best_for": info.best_for,
                })
            models.append(entry)
        return models

    # ── Provider implementations ──────────────────────────────────────────

    async def _generate_gemini(
        self,
        prompt: str,
        *,
        model: str = "gemini-3.1-flash-image",
        size: str = "1024x1024",
        quality: str = "hd",
        style: str = "",
        reference_image: Optional[bytes] = None,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Google Gemini — generateContent with responseModalities IMAGE."""
        key = self._get_api_key("gemini")
        # Map short names to API model IDs
        model_id_map = {
            "gemini-3.1-flash-image": "gemini-3.1-flash-image",
            "gemini-3-pro-image": "gemini-3-pro-image",
            "gemini-2.5-flash-image": "gemini-2.5-flash-image",
        }
        api_model = model_id_map.get(model, "gemini-3.1-flash-image")
        url = f"https://generativelanguage.googleapis.com/v1/models/{api_model}:generateContent"

        parts: List[Dict[str, Any]] = []
        if reference_image:
            parts.append({
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(reference_image).decode(),
                }
            })
        parts.append({"text": prompt})

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
            },
        }

        client = await self._get_client()
        resp = await client.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract image from response
        candidates = data.get("candidates", [])
        for candidate in candidates:
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData")
                if inline and inline.get("mimeType", "").startswith("image/"):
                    img_bytes = base64.b64decode(inline["data"])
                    mime = inline["mimeType"]
                    fmt = mime.split("/")[-1]
                    if fmt == "jpeg":
                        fmt = "jpg"
                    w, h = _parse_size(size)
                    return img_bytes, fmt, w, h, 0.0, None

        raise ImageGenError(f"Gemini n'a pas retourné d'image (modèle: {model})")

    async def _generate_openai(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """OpenAI Images API."""
        key = self._get_api_key("openai")
        model_id_map = {
            "gpt-image-2": "gpt-image-2",
            "gpt-image-1.5": "gpt-image-1.5",
            "gpt-image-1-mini": "gpt-image-1-mini",
        }
        model_id = model_id_map.get(model, "gpt-image-1.5")

        # OpenAI gpt-image-1 accepte: low/medium/high/auto
        _quality_map = {"hd": "high", "standard": "medium", "sd": "low"}
        openai_quality = _quality_map.get(quality, quality) if quality else "high"

        body: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": openai_quality,
        }

        client = await self._get_client()
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("data", [])
        if not items:
            raise ImageGenError("OpenAI n'a pas retourné d'image")

        item = items[0]
        if "b64_json" in item:
            img_bytes = base64.b64decode(item["b64_json"])
        elif "url" in item:
            img_resp = await client.get(item["url"])
            img_resp.raise_for_status()
            img_bytes = img_resp.content
        else:
            raise ImageGenError("OpenAI: ni b64_json ni url dans la réponse")

        w, h = _parse_size(size)
        cost = 0.12 if model == "gpt-image-2" else 0.08 if model == "gpt-image-1.5" else 0.02
        return img_bytes, "png", w, h, cost, None

    async def _generate_flux(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """BFL FLUX API — submit task + poll result."""
        key = self._get_api_key("flux")
        api_path = _FLUX_API_PATHS.get(model, "/v1/flux-pro-1.1")
        w, h = _parse_size(size)

        body: Dict[str, Any] = {"prompt": prompt, "width": w, "height": h}

        client = await self._get_client()
        # Submit task
        resp = await client.post(
            f"https://api.bfl.ml{api_path}",
            headers={"x-key": key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        task_id = resp.json().get("id")
        if not task_id:
            raise ImageGenError("FLUX: pas de task_id dans la réponse")

        # Poll for result (max 120s)
        for _ in range(60):
            await asyncio.sleep(2)
            poll_resp = await client.get(
                f"https://api.bfl.ml/v1/get_result?id={task_id}",
                headers={"x-key": key},
            )
            poll_resp.raise_for_status()
            result = poll_resp.json()
            status = result.get("status", "")
            if status == "Ready":
                sample_url = result.get("result", {}).get("sample", "")
                if not sample_url:
                    raise ImageGenError("FLUX: résultat Ready mais pas de sample URL")
                img_resp = await client.get(sample_url)
                img_resp.raise_for_status()
                seed = result.get("result", {}).get("seed")
                cost = 0.03 if "pro" in model else 0.014 if "klein" in model else 0.07
                return img_resp.content, "png", w, h, cost, seed
            if status in ("Error", "Failed"):
                raise ImageGenError(f"FLUX task failed: {result}")

        raise ImageGenError("FLUX: timeout après 120s de polling")

    async def _generate_stability(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Stability AI REST API."""
        key = self._get_api_key("stability")
        endpoint = _STABILITY_GEN_ENDPOINTS.get(model, "/v2beta/stable-image/generate/core")

        form_data: Dict[str, str] = {"prompt": prompt, "output_format": "png"}
        if "sd3.5" in model and model != "stable-image-ultra" and model != "stable-image-core":
            form_data["model"] = model

        client = await self._get_client()
        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            data=form_data,
        )
        resp.raise_for_status()

        w, h = _parse_size(size)
        cost_map = {"stable-image-ultra": 0.08, "stable-image-core": 0.03}
        cost = cost_map.get(model, 0.04)
        seed_val = int(resp.headers.get("x-seed", "0")) or None
        return resp.content, "png", w, h, cost, seed_val

    async def _generate_imagen(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Google Imagen 4 API."""
        key = self._get_api_key("imagen")
        model_id_map = {
            "imagen-4-ultra": "imagen-4.0-ultra-generate-001",
            "imagen-4": "imagen-4.0-generate-001",
            "imagen-4-fast": "imagen-4.0-fast-generate-001",
        }
        api_model = model_id_map.get(model, "imagen-4.0-generate-001")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{api_model}:predict"
        )
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1},
        }
        client = await self._get_client()
        resp = await client.post(
            url, json=body,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        predictions = resp.json().get("predictions", [])
        if not predictions:
            raise ImageGenError(f"Imagen n'a pas retourné d'image (modèle: {model})")
        b64 = predictions[0].get("bytesBase64Encoded", "")
        if not b64:
            raise ImageGenError("Imagen: pas de bytesBase64Encoded")
        img_bytes = base64.b64decode(b64)
        w, h = _parse_size(size)
        return img_bytes, "png", w, h, 0.04, None

    async def _generate_ideogram(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Ideogram V3 API."""
        key = self._get_api_key("ideogram")
        model_map = {
            "ideogram-v4-quality": "V_4",
            "ideogram-v4": "V_4",
            "ideogram-v4-turbo": "V_4_TURBO",
            "ideogram-v3-quality": "V_3",
            "ideogram-v3-balanced": "V_3",
            "ideogram-v3-turbo": "V_3_TURBO",
        }
        body = {
            "image_request": {
                "prompt": prompt,
                "model": model_map.get(model, "V_3"),
                "magic_prompt_option": "AUTO",
            }
        }
        rendering = "QUALITY" if "quality" in model else "BALANCED" if "balanced" in model or model == "ideogram-v4" else "SPEED"
        body["image_request"]["rendering_speed"] = rendering

        client = await self._get_client()
        resp = await client.post(
            "https://api.ideogram.ai/generate",
            headers={"Api-Key": key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("data", [])
        if not images:
            raise ImageGenError("Ideogram n'a pas retourné d'image")
        img_url = images[0].get("url", "")
        if not img_url:
            raise ImageGenError("Ideogram: pas d'URL dans la réponse")
        img_resp = await client.get(img_url)
        img_resp.raise_for_status()
        w, h = _parse_size(size)
        cost = 0.08 if "quality" in model else 0.05 if "balanced" in model or model == "ideogram-v4" else 0.03
        seed = images[0].get("seed")
        return img_resp.content, "png", w, h, cost, seed

    async def _generate_recraft(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Recraft V4 API (y compris SVG natif)."""
        key = self._get_api_key("recraft")
        is_svg = "svg" in model

        body: Dict[str, Any] = {
            "prompt": prompt,
            "model": "recraftv4" if not is_svg else "recraftv4",
            "response_format": "url",
        }
        if is_svg:
            body["style"] = "vector_illustration"
            body["substyle"] = "flat_2"

        client = await self._get_client()
        resp = await client.post(
            "https://external.api.recraft.ai/v1/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("data", [])
        if not images:
            raise ImageGenError("Recraft n'a pas retourné d'image")
        img_url = images[0].get("url", "")
        if not img_url:
            raise ImageGenError("Recraft: pas d'URL dans la réponse")
        img_resp = await client.get(img_url)
        img_resp.raise_for_status()

        fmt = "svg" if is_svg else "png"
        w, h = (0, 0) if is_svg else _parse_size(size)
        return img_resp.content, fmt, w, h, 0.04, None

    async def _generate_replicate(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Replicate API (seedream, wan, qwen, hunyuan, etc.)."""
        key = self._get_api_key("replicate")
        model_id = _REPLICATE_VERSIONS.get(model, model)

        body = {
            "version": model_id,
            "input": {"prompt": prompt},
        }

        client = await self._get_client()
        # Create prediction
        resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        prediction = resp.json()
        poll_url = prediction.get("urls", {}).get("get", "")
        if not poll_url:
            raise ImageGenError("Replicate: pas de poll URL")

        # Poll for completion (max 120s)
        for _ in range(60):
            await asyncio.sleep(2)
            poll_resp = await client.get(
                poll_url,
                headers={"Authorization": f"Token {key}"},
            )
            poll_resp.raise_for_status()
            result = poll_resp.json()
            status = result.get("status", "")
            if status == "succeeded":
                output = result.get("output")
                if isinstance(output, list) and output:
                    img_url = output[0]
                elif isinstance(output, str):
                    img_url = output
                else:
                    raise ImageGenError("Replicate: output inattendu")
                img_resp = await client.get(img_url)
                img_resp.raise_for_status()
                w, h = _parse_size(size)
                return img_resp.content, "png", w, h, 0.03, None
            if status == "failed":
                raise ImageGenError(f"Replicate prediction failed: {result.get('error')}")

        raise ImageGenError("Replicate: timeout après 120s de polling")

    async def _generate_huggingface(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """HuggingFace Inference API (gratuit, lent)."""
        key = self._get_api_key("huggingface")
        hf_model = "stabilityai/stable-diffusion-xl-base-1.0"

        client = await self._get_client()
        resp = await client.post(
            f"https://api-inference.huggingface.co/models/{hf_model}",
            headers={"Authorization": f"Bearer {key}"},
            json={"inputs": prompt},
        )
        resp.raise_for_status()

        w, h = _parse_size(size)
        return resp.content, "png", w, h, 0.0, None

    async def _generate_xai(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """xAI Grok Imagine Image API."""
        key = self._get_api_key("xai")

        client = await self._get_client()
        resp = await client.post(
            "https://api.x.ai/v1/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "prompt": prompt, "n": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise ImageGenError("xAI n'a pas retourné d'image")
        b64 = items[0].get("b64_json", "")
        if b64:
            img_bytes = base64.b64decode(b64)
        elif items[0].get("url"):
            img_resp = await client.get(items[0]["url"])
            img_resp.raise_for_status()
            img_bytes = img_resp.content
        else:
            raise ImageGenError("xAI: ni b64 ni url")
        w, h = _parse_size(size)
        return img_bytes, "png", w, h, 0.05, None

    async def _generate_minimax(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """MiniMax image-01 API."""
        key = self._get_api_key("minimax")

        client = await self._get_client()
        resp = await client.post(
            "https://api.minimax.chat/v1/image/generation",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "image-01", "prompt": prompt},
        )
        resp.raise_for_status()
        data = resp.json()
        b64 = data.get("data", {}).get("image", "")
        if not b64:
            raise ImageGenError("MiniMax: pas de données image")
        img_bytes = base64.b64decode(b64)
        w, h = _parse_size(size)
        return img_bytes, "png", w, h, 0.03, None

    async def _generate_zai(
        self, prompt: str, *, model: str, size: str, quality: str, style: str,
    ) -> tuple[bytes, str, int, int, float, Optional[int]]:
        """Z.AI Image API for the documented CogView-4 and GLM-Image models."""
        key = self._get_api_key("zai")
        api_model = {
            "cogview-4": "cogview-4-250304",
            "glm-image": "glm-image",
        }.get(model)
        if not api_model:
            raise ImageGenError(f"Z.AI image model unsupported: {model}")

        zai_quality = "hd" if model == "glm-image" else "standard"
        if quality in ("hd", "standard"):
            zai_quality = quality

        body: Dict[str, Any] = {
            "model": api_model,
            "prompt": prompt,
            "size": size,
            "quality": zai_quality,
        }
        base_url = os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4").rstrip("/")
        client = await self._get_client()
        resp = await client.post(
            f"{base_url}/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        images = resp.json().get("data", [])
        if not images or not isinstance(images[0], dict):
            raise ImageGenError("Z.AI did not return an image")
        image_url = images[0].get("url")
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise ImageGenError("Z.AI returned an invalid image URL")

        image_response = await client.get(image_url)
        image_response.raise_for_status()
        content_type = image_response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise ImageGenError("Z.AI image download returned a non-image response")
        fmt = content_type.split("/", 1)[1]
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in _IMAGE_EXTS:
            raise ImageGenError(f"Z.AI returned an unsupported image format: {fmt}")

        w, h = _parse_size(size)
        cost = 0.01 if model == "cogview-4" else 0.015
        return image_response.content, fmt, w, h, cost, None

    # ── Edit with Stability ───────────────────────────────────────────────

    async def _edit_stability(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mode: str = "inpaint",
        mask_prompt: str = "",
    ) -> ImageResult:
        """Édition via Stability AI REST API."""
        key = self._get_api_key("stability")
        endpoint = _STABILITY_EDIT_ENDPOINTS.get(mode, _STABILITY_EDIT_ENDPOINTS["inpaint"])

        files: Dict[str, Any] = {"image": ("image.png", image_bytes, "image/png")}
        data_fields: Dict[str, str] = {"output_format": "png"}

        if mode == "search-replace":
            data_fields["prompt"] = prompt
            data_fields["search_prompt"] = mask_prompt or prompt
        elif mode == "erase":
            data_fields["prompt"] = prompt
        elif mode in ("inpaint", "outpaint"):
            data_fields["prompt"] = prompt
        else:
            data_fields["prompt"] = prompt

        client = await self._get_client()
        t0 = time.monotonic()
        resp = await client.post(
            f"https://api.stability.ai{endpoint}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
            files=files,
            data=data_fields,
        )
        resp.raise_for_status()
        elapsed = int((time.monotonic() - t0) * 1000)

        return ImageResult(
            data=resp.content, format="png", width=0, height=0,
            provider="stability", model=f"stability-{mode}",
            cost_estimate=0.05, generation_time_ms=elapsed,
            prompt_used=prompt,
        )

    # ── Composition multi-images (Minia-style) ─────────────────────────────

    async def compose(
        self,
        assets: List[ComposeAsset],
        prompt: str,
        *,
        platform: str = "",
        model: str = "auto",
        size: str = "",
    ) -> ImageResult:
        """Compose une image à partir de plusieurs assets avec rôles.

        Utilise l'API Gemini multimodale : chaque asset est envoyé comme
        ``inlineData`` précédé d'un texte de contexte décrivant son rôle,
        exactement comme le fait Minia via ``callImageGenerationWithImages()``.

        Args:
            assets: Liste de ComposeAsset (path, role, description).
            prompt: Directive de composition globale.
            platform: Clé de _PLATFORM_PROMPTS (ex: "youtube_thumbnail").
            model: Modèle Gemini (auto = gemini-3.1-flash-image).
            size: Taille de sortie (auto-détectée depuis platform si vide).
        """
        if not assets:
            raise ImageGenError("Au moins un asset est requis pour la composition.")
        if not prompt or not prompt.strip():
            raise ImageGenError("Le prompt de composition est vide.")

        # Charger les images depuis les paths
        loaded: List[tuple[ComposeAsset, bytes]] = []
        for asset in assets:
            p = Path(asset.path)
            if not p.is_file():
                raise ImageGenError(f"Asset introuvable: {asset.path}")
            loaded.append((asset, p.read_bytes()))

        # Appliquer les règles de la plateforme
        platform_rules = ""
        if platform and platform in _PLATFORM_PROMPTS:
            pinfo = _PLATFORM_PROMPTS[platform]
            platform_rules = pinfo["rules"]
            if not size:
                size = pinfo["dimensions"]

        if not size:
            size = "1024x1024"

        # Construire les parts Gemini (texte contexte + image pour chaque asset)
        parts: List[Dict[str, Any]] = []
        for asset, img_bytes in loaded:
            role_desc = asset.description or asset.role
            parts.append({"text": f"[{asset.role.upper()}]: {role_desc}"})

            # Détecter le MIME type
            mime = "image/png"
            ext = Path(asset.path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif ext == ".webp":
                mime = "image/webp"

            parts.append({
                "inlineData": {
                    "mimeType": mime,
                    "data": base64.b64encode(img_bytes).decode(),
                }
            })

        # Prompt final avec règles plateforme
        final_prompt = prompt
        if platform_rules:
            final_prompt = f"{platform_rules}\n\n{prompt}"
        parts.append({"text": final_prompt})

        # Appel Gemini multimodal
        key = self._get_api_key("gemini")
        model_map = {
            "gemini-3.1-flash-image": "gemini-3.1-flash-image",
            "gemini-3-pro-image": "gemini-3-pro-image",
            "gemini-2.5-flash-image": "gemini-2.5-flash-image",
        }
        effective_model = model if model != "auto" else "gemini-3.1-flash-image"
        api_model = model_map.get(effective_model, "gemini-3.1-flash-image")
        url = f"https://generativelanguage.googleapis.com/v1/models/{api_model}:generateContent"

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
            },
        }

        client = await self._get_client()
        t0 = time.monotonic()
        resp = await client.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed = int((time.monotonic() - t0) * 1000)

        # Extraire l'image de la réponse
        candidates = data.get("candidates", [])
        for candidate in candidates:
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData")
                if inline and inline.get("mimeType", "").startswith("image/"):
                    img_bytes = base64.b64decode(inline["data"])
                    mime_type = inline["mimeType"]
                    fmt = mime_type.split("/")[-1]
                    if fmt == "jpeg":
                        fmt = "jpg"
                    w, h = _parse_size(size)
                    return ImageResult(
                        data=img_bytes, format=fmt, width=w, height=h,
                        provider="gemini", model=effective_model,
                        cost_estimate=0.0, generation_time_ms=elapsed,
                        prompt_used=prompt,
                    )

        raise ImageGenError(
            f"Gemini n'a pas retourné d'image composée ({len(assets)} assets, modèle: {effective_model})"
        )

    # ── Pipeline miniature pro (2 étapes) ─────────────────────────────────

    async def generate_thumbnail_pro(
        self,
        topic: str,
        *,
        platform: str = "youtube_thumbnail",
        assets: Optional[List[ComposeAsset]] = None,
        model: str = "auto",
        style: str = "",
    ) -> tuple[ImageResult, ThumbnailPlan]:
        """Pipeline 2 étapes inspiré Minia : plan LLM → génération image.

        Étape 1: Le LLM analyse le sujet et produit un ThumbnailPlan JSON
                 (headlines virales, prompt visuel, palette, composition, émotion).
        Étape 2: Le prompt visuel structuré est envoyé au générateur d'images
                 (avec assets si fournis via compose()).

        Returns:
            Tuple (ImageResult, ThumbnailPlan).
        """
        # ── Étape 1 : Planification via LLM ──
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.get_instance()

        platform_info = _PLATFORM_PROMPTS.get(platform, _PLATFORM_PROMPTS["youtube_thumbnail"])

        plan_system = (
            "Tu es un expert en création de miniatures/thumbnails virales pour les réseaux sociaux. "
            "Analyse le sujet donné et produis un plan structuré en JSON.\n\n"
            f"PLATEFORME: {platform}\n"
            f"DIMENSIONS: {platform_info['dimensions']}\n"
            f"RÈGLES: {platform_info['rules']}\n\n"
            "Retourne UNIQUEMENT un JSON valide avec cette structure exacte:\n"
            "{\n"
            '  "headlines": ["Titre 1 viral", "Titre 2 alternatif", "Titre 3 alternatif"],\n'
            '  "visual_prompt": "Description détaillée EN ANGLAIS de l\'image à générer...",\n'
            '  "colors": ["#FF0000", "#FFFFFF", "#000000"],\n'
            '  "composition_notes": "Notes sur la composition (placement sujet, espaces texte...)",\n'
            '  "emotion": "surprised|excited|curious|shocked|happy|determined|angry"\n'
            "}\n\n"
            "IMPORTANT: Le visual_prompt doit être optimisé pour un modèle de génération d'images. "
            "Il ne doit JAMAIS contenir de texte à afficher dans l'image."
        )

        plan_response = await llm.chat(
            messages=[
                {"role": "system", "content": plan_system},
                {"role": "user", "content": f"Sujet: {topic}\nStyle souhaité: {style or 'viral, impactant'}"},
            ],
            temperature=0.7,
            max_tokens=500,
        )

        # Parser le JSON du plan
        plan = self._parse_thumbnail_plan(plan_response)

        # ── Étape 2 : Génération image ──
        visual_prompt = plan.visual_prompt
        size = platform_info["dimensions"]

        if assets:
            result = await self.compose(
                assets, visual_prompt, platform=platform, model=model, size=size,
            )
        else:
            result = await self.generate(
                visual_prompt, model=model, size=size, quality="hd", style=style,
            )

        return result, plan

    def _parse_thumbnail_plan(self, raw: str) -> ThumbnailPlan:
        """Parse la réponse LLM en ThumbnailPlan, avec fallback robuste."""
        import json as _json

        # Extraire le JSON (le LLM peut wrapper dans ```json ... ```)
        cleaned = raw.strip()
        if "```" in cleaned:
            match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)

        try:
            data = _json.loads(cleaned)
        except _json.JSONDecodeError:
            # Fallback : extraire le premier {...} trouvé
            match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
            if match:
                try:
                    data = _json.loads(match.group(0))
                except _json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        return ThumbnailPlan(
            headlines=data.get("headlines", ["Thumbnail"]),
            visual_prompt=data.get("visual_prompt", "Professional thumbnail, high quality"),
            colors=data.get("colors", ["#FF0000", "#FFFFFF"]),
            composition_notes=data.get("composition_notes", ""),
            emotion=data.get("emotion", "excited"),
        )

    # ── Génération de titres viraux ───────────────────────────────────────

    async def generate_headlines(
        self,
        topic: str,
        *,
        platform: str = "youtube",
        count: int = 5,
        style: str = "",
    ) -> List[str]:
        """Génère des titres/headlines viraux via LLM.

        Args:
            topic: Sujet du contenu.
            platform: Plateforme cible (youtube, tiktok, instagram, linkedin, twitter).
            count: Nombre de titres à générer (max 10).
            style: Style souhaité (clickbait, informatif, question, etc.).
        """
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.get_instance()

        count = min(max(count, 1), 10)

        platform_tips = {
            "youtube": "Titres YouTube: max 60 chars, curiosity gap, chiffres, émotions fortes",
            "tiktok": "Titres TikTok: courts (< 40 chars), trend hooks, emojis autorisés",
            "instagram": "Captions IG: storytelling, questions, call-to-action",
            "linkedin": "Titres LinkedIn: professionnels, insights, données chiffrées",
            "twitter": "Tweets: max 280 chars, hooks percutants, thread-worthy",
        }
        tip = platform_tips.get(platform, platform_tips["youtube"])

        system = (
            f"Tu es un expert en copywriting viral pour {platform}.\n"
            f"RÈGLE: {tip}\n\n"
            f"Génère exactement {count} titres/headlines pour le sujet donné.\n"
            "Retourne UNIQUEMENT un JSON array de strings, sans explication.\n"
            'Exemple: ["Titre 1", "Titre 2", "Titre 3"]'
        )

        response = await llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Sujet: {topic}\nStyle: {style or 'viral et accrocheur'}"},
            ],
            temperature=0.8,
            max_tokens=300,
        )

        # Parser le JSON array
        cleaned = response.strip()
        if "```" in cleaned:
            match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)

        try:
            import json as _json
            headlines = _json.loads(cleaned)
            if isinstance(headlines, list):
                return [str(h) for h in headlines[:count]]
        except Exception:
            pass

        # Fallback: extraire le premier [...] trouvé
        match = re.search(r'\[.*?\]', cleaned, re.DOTALL)
        if match:
            try:
                import json as _json
                headlines = _json.loads(match.group(0))
                if isinstance(headlines, list):
                    return [str(h) for h in headlines[:count]]
            except Exception:
                pass

        # Dernier fallback: split par lignes
        lines = [l.strip().strip('"').strip("- ") for l in response.strip().splitlines() if l.strip()]
        return lines[:count] if lines else [f"Headline pour: {topic}"]

    # ── Prompt enrichment (P6) ────────────────────────────────────────────

    async def enrich_prompt(self, prompt: str, *, style: str = "") -> str:
        """Enrichit un prompt simple en prompt détaillé via LLM texte."""
        try:
            from src.llm.multi_provider import MultiProviderLLM
            llm = MultiProviderLLM.get_instance()
            system = (
                "Tu es un expert en prompts pour la génération d'images. "
                "Transforme le prompt utilisateur en un prompt détaillé et structuré, "
                "en anglais, optimisé pour les modèles de génération d'images. "
                "Ajoute des détails sur le style, l'éclairage, la composition, les couleurs. "
                "Retourne UNIQUEMENT le prompt amélioré, sans explication."
            )
            enriched = await llm.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Prompt: {prompt}\nStyle souhaité: {style or 'professionnel'}"},
                ],
                temperature=0.7,
                max_tokens=300,
            )
            return enriched.strip() if enriched else prompt
        except Exception as e:
            logger.debug(f"Prompt enrichment failed, using original: {e}")
            return prompt


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
