"""
remotion_prompts.py — Prompts LLM pour la génération de projets vidéo Remotion.

Séparé du moteur (remotion_engine.py) pour faciliter l'itération sur les prompts
sans toucher à la logique de rendu.

Améliorations P2:
  - Adaptation au modèle (small/medium/large)
  - Few-shot examples pour petits modèles
  - Prompt de correction d'erreur itératif
  - Injection dynamique de la mémoire vidéo
  - Prompt de simplification forcée (fallback)
"""

from __future__ import annotations
from typing import Dict, List, Optional


# ── Prompt Phase 1 : Planification des scènes ──────────────────────

VIDEO_PLAN_SYSTEM = """Tu es un réalisateur vidéo expert et motion designer.
Tu crées des vidéos professionnelles avec le framework Remotion (React).
Tu réponds UNIQUEMENT en JSON valide, sans commentaires, sans markdown."""

VIDEO_PLAN_PROMPT = """Planifie cette vidéo.

**Description**: {description}
**Template**: {template_name} ({duration_sec}s, {width}×{height}, {fps}fps)
**Scènes suggérées**: {suggested_scenes}{assets_section}

Retourne ce JSON EXACT:
{{
  "title": "Titre court de la vidéo",
  "scenes": [
    {{
      "id": "intro",
      "component_name": "IntroScene",
      "duration_frames": 90,
      "text_title": "Texte principal affiché",
      "text_subtitle": "Sous-titre optionnel",
      "background_type": "gradient",
      "background_value": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
      "image_url": "",
      "animation_in": "fadeIn",
      "animation_out": "fadeOut",
      "elements": ["title", "subtitle", "logo"]
    }}
  ],
  "palette": {{
    "primary": "#667eea",
    "secondary": "#764ba2",
    "text": "#ffffff",
    "bg": "#0f0f1a",
    "accent": "#f59f4a"
  }},
  "font_family": "Inter",
  "total_frames": {total_frames}
}}

CONTRAINTES:
- La somme des duration_frames de toutes les scènes DOIT = {total_frames}
- Chaque scène DOIT avoir un id unique et un component_name en PascalCase
- background_type: "gradient" | "solid" | "image"
- Si background_type = "image" ET aucun asset fourni: UTILISER "gradient" à la place — les URLs externes (Unsplash, etc.) sont bloquées pendant le rendu
- Si des assets sont fournis (section ASSETS CI-DESSUS), utilise image_url = "public/<nom_fichier>" pour les utiliser
- animation_in: "fadeIn" | "slideLeft" | "slideRight" | "slideUp" | "scaleUp" | "typewriter"
- animation_out: "fadeOut" | "slideLeft" | "slideRight" | "scaleDown"
- {fps} fps × {duration_sec}s = {total_frames} frames total
- INTERDIT: mettre "logo" dans elements si aucun asset logo n'est fourni dans ASSETS CI-DESSUS
- Si aucun asset fourni, elements ne peut contenir QUE: "title", "subtitle", "text", "badge"
"""

# ── Prompt Phase 2 : Génération composant scène ────────────────────

SCENE_COMPONENT_SYSTEM = """Tu es un expert Remotion et React.
Tu génères des composants TSX Remotion valides et professionnels.
Tu réponds UNIQUEMENT avec le code du composant, sans markdown, sans explication."""

SCENE_COMPONENT_PROMPT = """Génère le composant React/Remotion pour cette scène.

**Scène**: {scene_json}
**Palette**: {palette_json}
**Font**: {font_family}
**Dimensions**: {width}×{height} @ {fps}fps{assets_section}

IMPORTS DISPONIBLES (utilise UNIQUEMENT ceux-ci):
```tsx
import {{ useCurrentFrame, useVideoConfig, interpolate, spring, Sequence, AbsoluteFill, Img }} from 'remotion';
{static_file_import_hint}
```

CONTRAINTES STRICTES:
1. Export default du composant: `export default function {component_name}()`
2. Animations via interpolate() et spring() UNIQUEMENT (PAS de CSS @keyframes)
3. {image_constraint}
4. Texte: via constantes dans le composant, pas de props externes
5. Responsive: utiliser les dimensions de useVideoConfig(), pas de valeurs hardcodées
6. Fond: CSS background/backgroundColor/backgroundImage inline
7. Le composant DOIT occuper tout l'espace (<AbsoluteFill>)
8. PAS de dépendances npm supplémentaires
9. Typage TypeScript valide
10. {static_file_constraint}

EXEMPLE D'ANIMATION:
```tsx
const frame = useCurrentFrame();
const opacity = interpolate(frame, [0, 30], [0, 1], {{ extrapolateRight: 'clamp' }});
const scale = spring({{ frame, fps: {fps}, from: 0.8, to: 1 }});
```
"""

# ── Prompt Phase 3 : Video.tsx (séquenceur principal) ──────────────

VIDEO_SEQUENCER_PROMPT = """Génère le composant Video.tsx qui séquence toutes les scènes.

**Scènes dans l'ordre**:
{scenes_list}

**Total frames**: {total_frames}

Génère un composant qui utilise <Sequence> pour enchaîner les scènes:

```tsx
import {{ Sequence }} from 'remotion';
// import de chaque scène

export default function Video() {{
  return (
    <>
      <Sequence from={{0}} durationInFrames={{90}}>
        <IntroScene />
      </Sequence>
      // ... etc
    </>
  );
}}
```

CONTRAINTES:
- Chaque <Sequence> a un `from` = somme des duration_frames précédentes
- Chaque <Sequence> a un `durationInFrames` = duration_frames de la scène
- Import chaque scène depuis './scenes/{{ComponentName}}'
- Export default du composant Video
"""

# ── Templates fixes (Root.tsx, index.ts) ───────────────────────────

ROOT_TSX_TEMPLATE = """import {{ Composition }} from 'remotion';
import Video from './Video';

export const RemotionRoot: React.FC = () => {{
  return (
    <Composition
      id="{composition_id}"
      component={{Video}}
      durationInFrames={{{total_frames}}}
      fps={{{fps}}}
      width={{{width}}}
      height={{{height}}}
    />
  );
}};
"""

INDEX_TS_TEMPLATE = """import {{ registerRoot }} from 'remotion';
import {{ RemotionRoot }} from './Root';

registerRoot(RemotionRoot);
"""

# ══════════════════════════════════════════════════════════════════════════════
# P2 — ADAPTATION AU MODÈLE + FEW-SHOT + CORRECTION ITÉRATIVE
# ══════════════════════════════════════════════════════════════════════════════

# ── Few-shot example pour petits modèles ──────────────────────────────────────

FEW_SHOT_SCENE_EXAMPLE = """// EXEMPLE COMPLET D'UN COMPOSANT SCÈNE REMOTION FONCTIONNEL:
import {{ useCurrentFrame, useVideoConfig, interpolate, spring, AbsoluteFill }} from 'remotion';

export default function ExampleScene() {{
  const frame = useCurrentFrame();
  const {{ fps }} = useVideoConfig();
  
  const opacity = interpolate(frame, [0, 20], [0, 1], {{ extrapolateRight: 'clamp' }});
  const titleY = spring({{ frame, fps, from: -50, to: 0, config: {{ damping: 12 }} }});
  
  return (
    <AbsoluteFill style={{ {{ background: 'linear-gradient(135deg, #667eea, #764ba2)' }} }}>
      <div style={{{{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        opacity,
      }}}}>
        <h1 style={{{{
          fontSize: 72,
          color: '#fff',
          fontFamily: 'Inter',
          transform: `translateY(${{titleY}}px)`,
          textAlign: 'center',
        }}}}>Titre Principal</h1>
      </div>
    </AbsoluteFill>
  );
}}
"""

# ── Few-shot exemples spécialisés (injectés selon type de scène) ──────────────

FEW_SHOT_CTA_SCENE = """// EXEMPLE : Scène CTA avec bouton bounce + texte fadeIn
import {{ useCurrentFrame, useVideoConfig, interpolate, spring, AbsoluteFill }} from 'remotion';

export default function CtaScene() {{
  const frame = useCurrentFrame();
  const {{ fps }} = useVideoConfig();

  const opacity = interpolate(frame, [0, 20], [0, 1], {{ extrapolateRight: 'clamp' }});
  const btnScale = spring({{ frame: frame - 20, fps, from: 0, to: 1, config: {{ damping: 8, stiffness: 120 }} }});
  const titleY = interpolate(frame, [0, 25], [30, 0], {{ extrapolateRight: 'clamp' }});

  return (
    <AbsoluteFill style={{{{ background: '#0f0f1a', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 32 }}}}>
      <h2 style={{{{ fontSize: 56, color: '#fff', opacity, transform: `translateY(${{titleY}}px)`,
        textAlign: 'center', fontFamily: 'Inter', margin: 0 }}}}>
        Prêt à commencer ?
      </h2>
      <div style={{{{ transform: `scale(${{btnScale}})`, background: '#667eea', borderRadius: 12,
        padding: '16px 48px', color: '#fff', fontSize: 28, fontFamily: 'Inter', fontWeight: 700 }}}}>
        Démarrer maintenant
      </div>
    </AbsoluteFill>
  );
}}
"""

FEW_SHOT_SUBTITLE_SCENE = """// EXEMPLE : Scène avec titre + sous-titre en stagger (animations décalées)
import {{ useCurrentFrame, useVideoConfig, interpolate, spring, AbsoluteFill }} from 'remotion';

export default function SubtitleScene() {{
  const frame = useCurrentFrame();
  const {{ fps }} = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], {{ extrapolateRight: 'clamp' }});
  const titleY = spring({{ frame, fps, from: -40, to: 0, config: {{ damping: 14 }} }});
  const subtitleOpacity = interpolate(frame, [15, 35], [0, 1], {{ extrapolateRight: 'clamp' }});
  const subtitleY = spring({{ frame: frame - 15, fps, from: 20, to: 0, config: {{ damping: 14 }} }});

  return (
    <AbsoluteFill style={{{{ background: 'linear-gradient(180deg, #1a1a2e, #16213e)',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}}}>
      <h1 style={{{{ fontSize: 72, color: '#fff', margin: 0, opacity: titleOpacity,
        transform: `translateY(${{titleY}}px)`, fontFamily: 'Inter', fontWeight: 800,
        textAlign: 'center' }}}}>
        Titre Principal
      </h1>
      <p style={{{{ fontSize: 36, color: 'rgba(255,255,255,0.75)', marginTop: 24,
        opacity: subtitleOpacity, transform: `translateY(${{subtitleY}}px)`,
        fontFamily: 'Inter', textAlign: 'center' }}}}>
        Sous-titre descriptif et accrocheur
      </p>
    </AbsoluteFill>
  );
}}
"""

FEW_SHOT_COUNTER_SCENE = """// EXEMPLE : Scène avec compteur animé (chiffre qui monte)
import {{ useCurrentFrame, useVideoConfig, interpolate, AbsoluteFill }} from 'remotion';

export default function CounterScene() {{
  const frame = useCurrentFrame();
  const {{ durationInFrames }} = useVideoConfig();

  const progress = interpolate(frame, [0, durationInFrames * 0.8], [0, 1], {{ extrapolateRight: 'clamp' }});
  const targetValue = 98;
  const currentValue = Math.floor(progress * targetValue);
  const opacity = interpolate(frame, [0, 15], [0, 1], {{ extrapolateRight: 'clamp' }});
  const scale = interpolate(frame, [0, 20], [0.8, 1], {{ extrapolateRight: 'clamp' }});

  return (
    <AbsoluteFill style={{{{ background: '#0a0a0f', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', opacity }}}}>
      <div style={{{{ fontSize: 140, fontWeight: 900, color: '#667eea', fontFamily: 'Inter',
        transform: `scale(${{scale}})`, lineHeight: 1 }}}}>
        ${{currentValue}}%
      </div>
      <p style={{{{ fontSize: 32, color: 'rgba(255,255,255,0.7)', fontFamily: 'Inter',
        marginTop: 16, textAlign: 'center' }}}}>
        Taux de satisfaction
      </p>
    </AbsoluteFill>
  );
}}
"""


def get_few_shot_for_scene(scene_elements: list, model_family: str) -> str:
    """Retourne le(s) exemple(s) few-shot les plus pertinents selon le type de scène.

    Logique de sélection :
    - Si la scène contient 'cta' ou 'button' → exemple CTA
    - Si la scène contient 'subtitle' → exemple subtitle+stagger
    - Si la scène contient 'counter' ou 'metric' ou 'number' → exemple counter
    - Sinon → exemple basique (titre centré)

    Tous les modèles reçoivent au moins 1 exemple (pas juste small).
    Les modèles medium/large reçoivent 1 exemple ciblé.
    Les petits modèles reçoivent l'exemple + le modèle simplifié.
    """
    elements_lower = [str(e).lower() for e in (scene_elements or [])]

    # Sélection selon le contenu de la scène
    if any(e in elements_lower for e in ("cta", "button", "action")):
        targeted = FEW_SHOT_CTA_SCENE
    elif any(e in elements_lower for e in ("subtitle", "sub", "description")):
        targeted = FEW_SHOT_SUBTITLE_SCENE
    elif any(e in elements_lower for e in ("counter", "metric", "number", "stat")):
        targeted = FEW_SHOT_COUNTER_SCENE
    else:
        # Par défaut : exemple titre centré (le plus générique)
        targeted = FEW_SHOT_SCENE_EXAMPLE

    return targeted


# ── Contraintes adaptées par taille de modèle ─────────────────────────────────

MODEL_CONSTRAINTS: Dict[str, Dict[str, str]] = {
    "small": {
        "max_scenes": "3",
        "animation_policy": "UNIQUEMENT interpolate() — PAS de spring() (trop complexe)",
        "complexity": "SIMPLICITÉ MAXIMALE: 1 animation par élément, max 4 éléments par scène",
        "style_policy": "Utiliser UNIQUEMENT des styles inline simples (backgroundColor, color, fontSize)",
        "extra_constraint": "COPIE la structure de l'exemple ci-dessus, modifie uniquement le contenu",
    },
    "medium": {
        "max_scenes": "5",
        "animation_policy": "interpolate() et spring() — max 3 animations par scène",
        "complexity": "Composants modérés: 4-6 éléments animés par scène",
        "style_policy": "Styles inline avec gradients et ombres autorisés",
        "extra_constraint": "",
    },
    "large": {
        "max_scenes": "8",
        "animation_policy": "Toutes animations Remotion autorisées (interpolate, spring, Sequence imbriqué)",
        "complexity": "Composants riches autorisés: effets parallaxe, SVG inline, animations chaînées",
        "style_policy": "Styles avancés autorisés incluant backdrop-filter, clip-path, etc.",
        "extra_constraint": "",
    },
}


def get_model_constraints(model_family: str) -> Dict[str, str]:
    """Retourne les contraintes de génération adaptées à la famille de modèle."""
    return MODEL_CONSTRAINTS.get(model_family, MODEL_CONSTRAINTS["medium"])


# ── Prompt de correction d'erreur (boucle self-repair) ────────────────────────

SCENE_FIX_PROMPT = """Le composant TSX suivant contient des erreurs. Corrige-le.

**Code actuel (AVEC ERREURS)**:
```tsx
{broken_code}
```

**Erreurs détectées**:
{error_description}

**Contraintes de correction**:
1. Corrige UNIQUEMENT les erreurs listées ci-dessus
2. Garde la même structure et le même design
3. Retourne le composant COMPLET corrigé
4. AUCUN commentaire, AUCUNE explication — uniquement le code TSX
5. Assure-toi que les imports sont corrects
6. Le composant doit compiler sans erreur TypeScript
"""

# ── Prompt de simplification forcée (fallback pour petits modèles) ────────────

SCENE_SIMPLIFY_PROMPT = """Génère un composant Remotion TSX MINIMAL pour cette scène.

**Scène**: {scene_json}
**Palette**: {palette_json}
**Dimensions**: {width}×{height} @ {fps}fps

RESTRICTIONS ABSOLUES (ne viole AUCUNE de ces règles):
1. MAX 40 lignes de code
2. UNIQUEMENT interpolate() pour l'animation (PAS spring, PAS CSS animations)
3. UNIQUEMENT: AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate
4. UN SEUL élément animé (le titre)
5. Fond: UN gradient CSS simple
6. Export default obligatoire

MODÈLE EXACT À SUIVRE (modifie le contenu uniquement):
```tsx
import {{ useCurrentFrame, useVideoConfig, interpolate, AbsoluteFill }} from 'remotion';

export default function {component_name}() {{
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 20], [0, 1], {{ extrapolateRight: 'clamp' }});
  return (
    <AbsoluteFill style={{{{ background: '{bg_gradient}' }}}}>
      <div style={{{{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', opacity }}}}>
        <h1 style={{{{ fontSize: 64, color: '{text_color}', fontFamily: 'Inter', textAlign: 'center' }}}}>TITRE ICI</h1>
      </div>
    </AbsoluteFill>
  );
}}
```
"""

# ── Prompt de correction de rendu (après échec Docker) ────────────────────────

RENDER_FIX_SYSTEM = """Tu es un expert Remotion/React chargé de corriger un projet vidéo dont le rendu échoue.
Tu identifies la cause racine et proposes une correction précise et minimale.
Tu réponds UNIQUEMENT avec le code corrigé, sans markdown, sans explication."""

RENDER_FIX_PROMPT = """Le rendu de ce projet Remotion échoue. Corrige le fichier fautif.

**Erreur de rendu**:
{render_error}

**Fichier problématique** ({filename}):
```tsx
{file_content}
```

**Contexte du projet**:
- Scènes: {scene_names}
- Total frames: {total_frames}
- Dimensions: {width}×{height} @ {fps}fps

CORRIGE le fichier ci-dessus. Retourne le code COMPLET et CORRIGÉ du fichier.
Aucun commentaire, aucune explication. UNIQUEMENT le code TSX corrigé.

RÈGLES CRITIQUES:
- Si le fichier est Video.tsx: CONSERVER les imports depuis './scenes/' — NE JAMAIS les remplacer par des stubs inline
- NE JAMAIS utiliser <Img> avec des URLs externes (Unsplash, etc.) — remplacer par un fond CSS gradient
- Si l'erreur est liée à une image externe: supprimer le <Img> et mettre un backgroundColor ou gradient en CSS
"""


# ── Builder de prompts dynamiques ─────────────────────────────────────────────

def build_scene_prompt(
    scene_json: str,
    palette_json: str,
    font_family: str,
    width: int,
    height: int,
    fps: int,
    component_name: str,
    model_family: str = "medium",
    has_assets: bool = False,
    memory_block: str = "",
    scene_elements: list = None,
) -> str:
    """Construit le prompt de génération de scène adapté au modèle.

    Injecte des few-shot examples pour TOUS les modèles (pas juste small) :
    - L'exemple est sélectionné selon le type d'éléments de la scène (CTA, subtitle, counter)
    - Small : few-shot ciblé + contraintes simplifiées
    - Medium/Large : few-shot ciblé adapté au niveau du modèle
    Pour tous : injecte la mémoire vidéo (leçons + succès passés).
    """
    constraints = get_model_constraints(model_family)

    # Import hints
    static_file_import = "import { staticFile } from 'remotion';" if has_assets else ""
    static_constraint = (
        "staticFile() AUTORISÉ pour les fichiers dans public/"
        if has_assets else
        "INTERDIT d'utiliser staticFile() — aucun asset local disponible"
    )
    image_constraint = (
        "Images: utiliser staticFile('nom_fichier') pour les assets locaux"
        if has_assets else
        "Images: INTERDIT d'utiliser <Img> ou des URLs externes (Unsplash, etc.) — les images externes sont bloquées pendant le rendu. Utiliser UNIQUEMENT des fonds CSS (background, gradient, backgroundColor)"
    )

    # Base prompt
    prompt = SCENE_COMPONENT_PROMPT.format(
        scene_json=scene_json,
        palette_json=palette_json,
        font_family=font_family,
        width=width,
        height=height,
        fps=fps,
        component_name=component_name,
        static_file_import_hint=static_file_import,
        image_constraint=image_constraint,
        static_file_constraint=static_constraint,
        assets_section="",
    )

    # Ajout contraintes modèle
    model_block = f"""\n\nCONTRAINTES MODÈLE ({model_family.upper()}):
- Animation: {constraints['animation_policy']}
- Complexité: {constraints['complexity']}
- Style: {constraints['style_policy']}"""
    if constraints["extra_constraint"]:
        model_block += f"\n- {constraints['extra_constraint']}"
    prompt += model_block

    # Few-shot pour TOUS les modèles — sélection intelligente selon le type de scène
    # Extrait les éléments depuis scene_json si non fourni explicitement
    _elements = scene_elements
    if _elements is None and scene_json:
        try:
            import json as _json
            _scene_data = _json.loads(scene_json)
            _elements = _scene_data.get("elements", [])
        except Exception:
            _elements = []
    few_shot = get_few_shot_for_scene(_elements or [], model_family)
    prompt += f"\n\n// EXEMPLE DE RÉFÉRENCE (adapte le contenu, conserve la structure):\n{few_shot}"

    # Mémoire vidéo (leçons + succès)
    if memory_block:
        prompt += f"\n\n{memory_block}"

    return prompt


def build_plan_prompt(
    description: str,
    template_name: str,
    duration_sec: int,
    width: int,
    height: int,
    fps: int,
    suggested_scenes: str,
    total_frames: int,
    model_family: str = "medium",
    assets_section: str = "",
    memory_block: str = "",
) -> str:
    """Construit le prompt de planification adapté au modèle."""
    constraints = get_model_constraints(model_family)
    
    prompt = VIDEO_PLAN_PROMPT.format(
        description=description,
        template_name=template_name,
        duration_sec=duration_sec,
        width=width,
        height=height,
        fps=fps,
        suggested_scenes=suggested_scenes,
        total_frames=total_frames,
        assets_section=assets_section,
    )
    
    # Contrainte nombre de scènes
    prompt += f"\n- MAXIMUM {constraints['max_scenes']} scènes pour ce modèle"
    
    if model_family == "small":
        prompt += "\n- IMPORTANT: garde les scènes SIMPLES (1-2 éléments par scène max)"
        prompt += "\n- N'utilise QUE les animations: fadeIn, fadeOut, slideUp"
    
    if memory_block:
        prompt += f"\n\n{memory_block}"
    
    return prompt


def build_fix_prompt(
    broken_code: str,
    error_description: str,
) -> str:
    """Prompt pour corriger un composant TSX défaillant."""
    return SCENE_FIX_PROMPT.format(
        broken_code=broken_code,
        error_description=error_description,
    )


def build_simplify_prompt(
    scene_json: str,
    palette_json: str,
    width: int,
    height: int,
    fps: int,
    component_name: str,
    bg_gradient: str = "linear-gradient(135deg, #667eea, #764ba2)",
    text_color: str = "#ffffff",
) -> str:
    """Prompt de simplification forcée — dernier recours."""
    return SCENE_SIMPLIFY_PROMPT.format(
        scene_json=scene_json,
        palette_json=palette_json,
        width=width,
        height=height,
        fps=fps,
        component_name=component_name,
        bg_gradient=bg_gradient,
        text_color=text_color,
    )


def build_render_fix_prompt(
    render_error: str,
    filename: str,
    file_content: str,
    scene_names: List[str],
    total_frames: int,
    width: int,
    height: int,
    fps: int,
) -> str:
    """Prompt pour corriger un fichier après échec de rendu."""
    return RENDER_FIX_PROMPT.format(
        render_error=render_error,
        filename=filename,
        file_content=file_content,
        scene_names=", ".join(scene_names),
        total_frames=total_frames,
        width=width,
        height=height,
        fps=fps,
    )


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
