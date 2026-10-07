"""Contrat vidéo canonique et compilateur déterministe pour Remotion.

Le modèle décrit l'intention. Ce module borne et normalise cette description,
puis peut produire un projet visuel sûr sans dépendre de sa capacité à écrire du
TSX. Le mode expert reste disponible dans le handler Remotion.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple


_COMPONENT_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_SAFE_MODES = {"auto", "safe", "expert"}


@dataclass(frozen=True)
class VideoSceneSpec:
    """Une scène normalisée, bornée et indépendante du fournisseur LLM."""

    id: str
    component_name: str
    duration_frames: int
    text_title: str = ""
    text_subtitle: str = ""
    background_value: str = ""
    animation_in: str = "fadeIn"
    animation_out: str = "fadeOut"
    elements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoSpec:
    """Représentation canonique utilisée avant toute génération TSX."""

    schema_version: int
    title: str
    total_frames: int
    fps: int
    width: int
    height: int
    font_family: str
    palette: Mapping[str, str]
    scenes: Tuple[VideoSceneSpec, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Retourne une forme sérialisable pour manifeste et diagnostic."""
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "total_frames": self.total_frames,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "font_family": self.font_family,
            "palette": dict(self.palette),
            "scenes": [
                {
                    "id": scene.id,
                    "component_name": scene.component_name,
                    "duration_frames": scene.duration_frames,
                    "text_title": scene.text_title,
                    "text_subtitle": scene.text_subtitle,
                    "background_value": scene.background_value,
                    "animation_in": scene.animation_in,
                    "animation_out": scene.animation_out,
                    "elements": list(scene.elements),
                }
                for scene in self.scenes
            ],
        }


def choose_generation_mode(requested: str, model_family: str) -> str:
    """Choisit le rail de génération sans dépendre d'une liste de modèles."""
    mode = (requested or "auto").strip().lower()
    if mode not in _SAFE_MODES:
        raise ValueError("creation_mode doit valoir auto, safe ou expert")
    if mode != "auto":
        return mode
    return "safe" if model_family == "small" else "expert"


def normalize_video_spec(
    payload: Mapping[str, Any],
    *,
    total_frames: int,
    fps: int,
    width: int,
    height: int,
) -> VideoSpec:
    """Valide et normalise un plan LLM en ``VideoSpec`` strict.

    Les durées sont ajustées de façon déterministe au nombre total de frames. Les
    champs libres sont tronqués et injectés ensuite comme littéraux JSON, jamais
    comme source TypeScript.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("Le plan vidéo doit être un objet JSON")
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("Le plan vidéo doit contenir au moins une scène")
    if len(raw_scenes) > 24:
        raise ValueError("Le plan vidéo dépasse la limite de 24 scènes")
    if total_frames < len(raw_scenes):
        raise ValueError("La durée est trop courte pour le nombre de scènes")

    parsed: List[Dict[str, Any]] = []
    used_names: set[str] = set()
    for index, raw in enumerate(raw_scenes):
        if not isinstance(raw, Mapping):
            raise ValueError(f"La scène {index + 1} doit être un objet")
        scene_id = _slug(str(raw.get("id") or f"scene-{index + 1}"), 48)
        component = str(raw.get("component_name") or _pascal(scene_id) + "Scene")
        if not _COMPONENT_RE.fullmatch(component):
            component = _pascal(scene_id) + "Scene"
        base_component = component
        suffix = 2
        while component in used_names:
            component = f"{base_component}{suffix}"
            suffix += 1
        used_names.add(component)
        try:
            duration = max(1, int(raw.get("duration_frames", 1)))
        except (TypeError, ValueError):
            duration = 1
        parsed.append({
            "id": scene_id,
            "component_name": component,
            "duration_frames": duration,
            "text_title": _text(raw.get("text_title"), 180),
            "text_subtitle": _text(raw.get("text_subtitle"), 320),
            "background_value": _text(raw.get("background_value"), 240),
            "animation_in": _choice(raw.get("animation_in"), {"fadeIn", "slideLeft", "slideRight", "slideUp", "scaleUp", "typewriter"}, "fadeIn"),
            "animation_out": _choice(raw.get("animation_out"), {"fadeOut", "slideLeft", "slideRight", "scaleDown"}, "fadeOut"),
            "elements": tuple(_text(value, 32) for value in (raw.get("elements") or [])[:12]),
        })

    durations = _fit_durations([scene["duration_frames"] for scene in parsed], total_frames)
    scenes = tuple(
        VideoSceneSpec(**{**scene, "duration_frames": durations[index]})
        for index, scene in enumerate(parsed)
    )
    raw_palette = payload.get("palette") if isinstance(payload.get("palette"), Mapping) else {}
    defaults = {
        "primary": "#ff9f43",
        "secondary": "#7c3aed",
        "text": "#ffffff",
        "bg": "#07111f",
        "accent": "#22d3ee",
    }
    palette = {
        key: str(raw_palette.get(key)) if _COLOR_RE.fullmatch(str(raw_palette.get(key, ""))) else value
        for key, value in defaults.items()
    }
    font = re.sub(r"[^A-Za-z0-9 ,'-]", "", str(payload.get("font_family") or "Inter"))[:80] or "Inter"
    return VideoSpec(
        schema_version=1,
        title=_text(payload.get("title"), 160) or "Vidéo Lumena",
        total_frames=total_frames,
        fps=fps,
        width=width,
        height=height,
        font_family=font,
        palette=palette,
        scenes=scenes,
    )


def compile_safe_video(spec: VideoSpec) -> Dict[str, str]:
    """Compile un ``VideoSpec`` en composants Remotion déterministes."""
    files: Dict[str, str] = {}
    for index, scene in enumerate(spec.scenes):
        files[f"src/scenes/{scene.component_name}.tsx"] = _compile_scene(spec, scene, index)

    imports = "\n".join(
        f"import {scene.component_name} from './scenes/{scene.component_name}';"
        for scene in spec.scenes
    )
    offset = 0
    sequences: List[str] = []
    for scene in spec.scenes:
        sequences.append(
            f"      <Sequence from={{{offset}}} durationInFrames={{{scene.duration_frames}}}>"
            f"<{scene.component_name} /></Sequence>"
        )
        offset += scene.duration_frames
    files["src/Video.tsx"] = (
        "import {Sequence} from 'remotion';\n"
        f"{imports}\n\n"
        "export default function Video() {\n"
        "  return (\n    <>\n"
        + "\n".join(sequences)
        + "\n    </>\n  );\n}\n"
    )
    return files


def _compile_scene(spec: VideoSpec, scene: VideoSceneSpec, index: int) -> str:
    primary = spec.palette["primary"]
    secondary = spec.palette["secondary"]
    text_color = spec.palette["text"]
    background = scene.background_value if "gradient(" in scene.background_value else (
        f"linear-gradient({120 + (index % 4) * 25}deg, {spec.palette['bg']} 0%, "
        f"{secondary} 62%, {primary} 140%)"
    )
    align = ("flex-start", "center", "flex-end")[index % 3]
    title = json.dumps(scene.text_title, ensure_ascii=False)
    subtitle = json.dumps(scene.text_subtitle, ensure_ascii=False)
    font = json.dumps(spec.font_family, ensure_ascii=False)
    bg = json.dumps(background, ensure_ascii=False)
    return f"""import {{AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig}} from 'remotion';

export default function {scene.component_name}() {{
  const frame = useCurrentFrame();
  const {{fps, width}} = useVideoConfig();
  const enter = spring({{frame, fps, config: {{damping: 18, stiffness: 110}}}});
  const opacity = interpolate(frame, [0, 12, {max(13, scene.duration_frames - 14)}, {max(14, scene.duration_frames - 1)}], [0, 1, 1, 0], {{extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}});
  const y = interpolate(enter, [0, 1], [56, 0]);
  return (
    <AbsoluteFill style={{{{background: {bg}, color: '{text_color}', fontFamily: {font}, overflow: 'hidden'}}}}>
      <div style={{{{position: 'absolute', inset: 0, background: 'radial-gradient(circle at 82% 18%, rgba(255,255,255,.18), transparent 34%)'}}}} />
      <div style={{{{position: 'absolute', width: width * 0.36, height: width * 0.36, borderRadius: '50%', right: -width * 0.12, bottom: -width * 0.18, border: '2px solid rgba(255,255,255,.22)'}}}} />
      <div style={{{{display: 'flex', flex: 1, flexDirection: 'column', justifyContent: 'center', alignItems: '{align}', padding: '8%', opacity, transform: `translateY(${{y}}px)`, textAlign: '{'center' if align == 'center' else 'left'}', zIndex: 1}}}}>
        <div style={{{{fontSize: Math.max(54, width * 0.055), lineHeight: 1.02, fontWeight: 800, maxWidth: '82%', letterSpacing: '-0.04em'}}}}>{{{title}}}</div>
        {scene.text_subtitle and f"<div style={{{{fontSize: Math.max(25, width * 0.021), lineHeight: 1.35, marginTop: 30, maxWidth: '68%', opacity: .84}}}}>{{{subtitle}}}</div>" or ""}
        <div style={{{{width: 110, height: 7, borderRadius: 99, marginTop: 38, backgroundColor: '{spec.palette['accent']}'}}}} />
      </div>
    </AbsoluteFill>
  );
}}
"""


def _fit_durations(values: List[int], total: int) -> List[int]:
    current = sum(values)
    if current <= 0:
        values = [1] * len(values)
        current = len(values)
    fitted = [max(1, round(value * total / current)) for value in values]
    delta = total - sum(fitted)
    index = len(fitted) - 1
    while delta != 0:
        if delta > 0:
            fitted[index] += 1
            delta -= 1
        elif fitted[index] > 1:
            fitted[index] -= 1
            delta += 1
        index = (index - 1) % len(fitted)
    return fitted


def _slug(value: str, limit: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "scene")[:limit]


def _pascal(value: str) -> str:
    result = "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", value) if part)
    if not result or result[0].isdigit():
        result = "Scene" + result
    return result[:56]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or "")
    return candidate if candidate in allowed else default

