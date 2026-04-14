"""
remotion_prompts.py — Prompts LLM pour la génération de projets vidéo Remotion.

Séparé du moteur (remotion_engine.py) pour faciliter l'itération sur les prompts
sans toucher à la logique de rendu.
"""

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
- Si background_type = "image" ET aucun asset fourni, image_url DOIT être une URL Unsplash valide
- Si des assets sont fournis (section ASSETS CI-DESSUS), utilise image_url = "public/<nom_fichier>" pour les utiliser
- animation_in: "fadeIn" | "slideLeft" | "slideRight" | "slideUp" | "scaleUp" | "typewriter"
- animation_out: "fadeOut" | "slideLeft" | "slideRight" | "scaleDown"
- {fps} fps × {duration_sec}s = {total_frames} frames total
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
import {{ staticFile }} from 'remotion'; // Pour les assets locaux fournis
```

CONTRAINTES STRICTES:
1. Export default du composant: `export default function {component_name}()`
2. Animations via interpolate() et spring() UNIQUEMENT (PAS de CSS @keyframes)
3. Images sans assets: URL Unsplash DIRECTES uniquement
   Images AVEC assets fournis: utilise `<Img src={{staticFile("nom_fichier.ext")}} />` pour les intégrer visuellement
4. Texte: via constantes dans le composant, pas de props externes
5. Responsive: utiliser les dimensions de useVideoConfig(), pas de valeurs hardcodées
6. Fond: CSS background/backgroundColor/backgroundImage inline
7. Le composant DOIT occuper tout l'espace (<AbsoluteFill>)
8. PAS de dépendances npm supplémentaires
9. Typage TypeScript valide

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
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
