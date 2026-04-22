"""
🌐 LUMENA - Website Builder Tools

Outils pour générer des sites web complets (frontend + backend + API),
les prévisualiser, les modifier et les exporter en ZIP.

Inspiré de WebCraft Builder Pro mais intégré nativement dans Lumena.
"""

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger
from src.prompts.tools.website_prompts import (
    WEBSITE_GENERATE_PROMPT,
    WEBSITE_EDIT_PROMPT,
)

try:
    from .code_validator import validate_project as _validate_project
    _VALIDATOR_AVAILABLE = True
except ImportError:
    _VALIDATOR_AVAILABLE = False

try:
    from .ui_ux_knowledge import get_design_for_project as _get_pro_design
    _PRO_DESIGN_AVAILABLE = True
except ImportError:
    _PRO_DESIGN_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
# DESIGN ENGINE — Sélection automatique de palette/thème/font
# ═══════════════════════════════════════════════════════════════

PALETTES = [
    {
        "name": "Ocean Depths",
        "primary": "#0ea5e9", "secondary": "#06b6d4", "accent": "#6366f1",
        "gradient": "linear-gradient(135deg, #0ea5e9, #6366f1)",
        "theme": "dark",
        "keywords": ["tech", "saas", "ia", "ai", "startup", "digital", "logiciel", "software", "plateforme", "app"],
    },
    {
        "name": "Sunset Glow",
        "primary": "#f59e0b", "secondary": "#f97316", "accent": "#ec4899",
        "gradient": "linear-gradient(135deg, #f59e0b, #ec4899)",
        "theme": "dark",
        "keywords": ["restaurant", "cuisine", "food", "café", "bar", "boulangerie", "traiteur", "pizza"],
    },
    {
        "name": "Forest Zen",
        "primary": "#10b981", "secondary": "#059669", "accent": "#14b8a6",
        "gradient": "linear-gradient(135deg, #10b981, #14b8a6)",
        "theme": "dark",
        "keywords": ["nature", "bio", "écolo", "vert", "jardin", "yoga", "bien-être", "santé", "plante"],
    },
    {
        "name": "Royal Purple",
        "primary": "#8b5cf6", "secondary": "#a855f7", "accent": "#d946ef",
        "gradient": "linear-gradient(135deg, #8b5cf6, #d946ef)",
        "theme": "dark",
        "keywords": ["luxe", "premium", "mode", "fashion", "beauté", "cosmétique", "bijoux", "joaillerie"],
    },
    {
        "name": "Neon Cyber",
        "primary": "#00d9ff", "secondary": "#a855f7", "accent": "#10b981",
        "gradient": "linear-gradient(135deg, #00d9ff, #a855f7)",
        "theme": "dark",
        "keywords": ["gaming", "jeu", "esport", "crypto", "nft", "web3", "metaverse", "musique", "dj"],
    },
    {
        "name": "Coral Reef",
        "primary": "#e11d48", "secondary": "#f43f5e", "accent": "#fb7185",
        "gradient": "linear-gradient(135deg, #e11d48, #fb7185)",
        "theme": "dark",
        "keywords": ["voyage", "tourisme", "hôtel", "plage", "vacances", "agence de voyage"],
    },
    {
        "name": "Midnight Pro",
        "primary": "#64748b", "secondary": "#475569", "accent": "#0ea5e9",
        "gradient": "linear-gradient(135deg, #64748b, #0ea5e9)",
        "theme": "dark",
        "keywords": ["finance", "banque", "immobilier", "assurance", "comptable", "avocat", "cabinet"],
    },
    {
        "name": "Sakura Dream",
        "primary": "#ec4899", "secondary": "#f472b6", "accent": "#db2777",
        "gradient": "linear-gradient(135deg, #ec4899, #f472b6)",
        "theme": "light",
        "keywords": ["mariage", "événement", "fleuriste", "photographe", "wedding", "spa", "coiffure", "esthétique"],
    },
    {
        "name": "Slate Charcoal",
        "primary": "#334155", "secondary": "#475569", "accent": "#0ea5e9",
        "gradient": "linear-gradient(135deg, #334155, #0ea5e9)",
        "theme": "dark",
        "keywords": ["industrie", "construction", "btp", "auto", "mécanique", "garage", "architecture"],
    },
    {
        "name": "Emerald Night",
        "primary": "#059669", "secondary": "#10b981", "accent": "#34d399",
        "gradient": "linear-gradient(135deg, #059669, #34d399)",
        "theme": "dark",
        "keywords": ["portfolio", "freelance", "cv", "développeur", "designer", "créatif", "agence"],
    },
    {
        "name": "Electric Indigo",
        "primary": "#4f46e5", "secondary": "#6366f1", "accent": "#818cf8",
        "gradient": "linear-gradient(135deg, #4f46e5, #818cf8)",
        "theme": "dark",
        "keywords": ["éducation", "école", "formation", "cours", "e-learning", "université"],
    },
    {
        "name": "Golden Luxury",
        "primary": "#f59e0b", "secondary": "#d97706", "accent": "#fbbf24",
        "gradient": "linear-gradient(135deg, #f59e0b, #fbbf24)",
        "theme": "dark",
        "keywords": ["haut de gamme", "prestige", "or", "gold", "montre", "champagne"],
    },
    {
        "name": "Warm Terracotta",
        "primary": "#b45309", "secondary": "#d97706", "accent": "#f59e0b",
        "gradient": "linear-gradient(135deg, #b45309, #f59e0b)",
        "theme": "light",
        "keywords": ["artisan", "bois", "déco", "intérieur", "meuble", "rénovation", "céramique"],
    },
    {
        "name": "Nordic Teal",
        "primary": "#0d9488", "secondary": "#14b8a6", "accent": "#2dd4bf",
        "gradient": "linear-gradient(135deg, #0d9488, #2dd4bf)",
        "theme": "dark",
        "keywords": ["médical", "docteur", "clinique", "pharmacie", "dentiste", "hôpital"],
    },
    {
        "name": "Crimson Passion",
        "primary": "#ef4444", "secondary": "#dc2626", "accent": "#f97316",
        "gradient": "linear-gradient(135deg, #ef4444, #f97316)",
        "theme": "dark",
        "keywords": ["sport", "fitness", "gym", "musculation", "running", "martial"],
    },
    {
        "name": "Copper Rose",
        "primary": "#c2410c", "secondary": "#ea580c", "accent": "#f97316",
        "gradient": "linear-gradient(135deg, #c2410c, #f97316)",
        "theme": "dark",
        "keywords": ["brasserie", "vin", "cave", "whisky", "barbier", "vintage"],
    },
]

FONTS = [
    {"name": "Inter", "url": "Inter:wght@300;400;500;600;700;800;900"},
    {"name": "Poppins", "url": "Poppins:wght@300;400;500;600;700;800"},
    {"name": "DM Sans", "url": "DM+Sans:wght@300;400;500;600;700"},
    {"name": "Outfit", "url": "Outfit:wght@300;400;500;600;700"},
    {"name": "Sora", "url": "Sora:wght@300;400;500;600;700"},
    {"name": "Plus Jakarta Sans", "url": "Plus+Jakarta+Sans:wght@300;400;500;600;700;800"},
    {"name": "Manrope", "url": "Manrope:wght@300;400;500;600;700;800"},
    {"name": "Space Grotesk", "url": "Space+Grotesk:wght@300;400;500;600;700"},
    {"name": "Montserrat", "url": "Montserrat:wght@300;400;500;600;700;800;900"},
    {"name": "Raleway", "url": "Raleway:wght@300;400;500;600;700;800"},
]

HERO_LAYOUTS = ["split-left", "centered", "fullscreen-bg", "asymmetric", "split-right", "stacked-layers"]

DESIGN_STYLES = [
    ("glassmorphism", "- backdrop-filter: blur(20px) sur les cards\n- Background semi-transparent rgba\n- Bordures subtiles 1px solid rgba(255,255,255,0.1)\n- Effets de profondeur et flou"),
    ("neumorphism", "- Ombres douces inset + outset (relief 3D soft)\n- box-shadow: 8px 8px 16px #d1d1d1, -8px -8px 16px #ffffff\n- Fond clair uni, contraste léger"),
    ("minimalist", "- Maximum d'espace négatif (padding: 8rem+ vertical)\n- Typographie très grande et bold pour titres\n- 2 couleurs max + neutres\n- Élégance par la simplicité"),
    ("gradient-heavy", "- Gradients sur TOUT: texte, fond, boutons, bordures\n- Couleurs vibrantes et saturées\n- Effets de glow et halo lumineux"),
    ("dark-luxury", "- Fond ultra-sombre (#050505 à #0a0a0a)\n- Accents dorés/cuivrés subtils\n- Typographie fine et espacée (letter-spacing:0.1em)\n- Bordures ultra-subtiles"),
    ("editorial", "- Layout magazine avec colonnes asymétriques\n- Grandes images pleine largeur\n- Espacement vertical très généreux\n- Lignes de séparation fines et élégantes"),
    ("brutalist", "- Bordures épaisses noires (3-4px solid)\n- Typographie MASSIVE bold uppercase\n- AUCUN border-radius\n- Couleurs brutes et contrastées"),
    ("corporate-clean", "- Design professionnel sobre\n- Fond blanc ou gris clair\n- Cards avec ombres légères\n- Sans-serif lisible, pas d'effets fantaisistes"),
    ("organic", "- Border-radius: 24px+ sur tout\n- Formes blob/arrondies, courbes naturelles\n- Couleurs chaudes et douces\n- Aspect accueillant"),
    ("playful", "- Couleurs vives et saturées, fun\n- Formes géométriques décoratives\n- Micro-animations bounce\n- Asymétrie intentionnelle et joyeuse"),
]

BACKGROUND_TYPES = [
    "floating-orbs", "gradient-mesh", "grain-texture", "grid-lines",
    "geometric-shapes", "clean-gradient", "noise-overlay", "dot-grid",
]

COMPONENT_PATTERNS = {
    "testimonials": ["carousel", "grid-3col", "cards-stacked", "single-featured"],
    "pricing": ["3-columns", "horizontal-cards", "toggle-monthly-yearly"],
    "features": ["icon-grid", "alternating-rows", "bento-grid", "tabs-content"],
    "faq": ["accordion", "tabs", "two-columns"],
}


def _hex_to_rgb(hex_color: str) -> str:
    """Convertit #hex en 'r, g, b'."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"
    return "99, 102, 241"


def select_palette(user_message: str) -> Dict[str, Any]:
    """Sélectionne la palette la plus adaptée au message, ou aléatoire si aucun mot-clé."""
    msg_lower = user_message.lower()
    best_score = 0
    candidates = []

    for palette in PALETTES:
        score = sum(1 for kw in palette["keywords"] if kw in msg_lower)
        if score > best_score:
            best_score = score
            candidates = [palette]
        elif score == best_score and score > 0:
            candidates.append(palette)

    if candidates:
        return random.choice(candidates)
    return random.choice(PALETTES)


def select_font() -> Dict[str, str]:
    """Sélectionne une font aléatoire."""
    return random.choice(FONTS)


def _get_bg_instructions(bg_type: str, rgb: str) -> str:
    """Instructions CSS pour le type de fond."""
    bgs = {
        "floating-orbs": f"3 orbes floues (filter:blur(80px)) avec animation floatOrb 20s, position fixed z-index:-1, tailles 350-500px, opacité 0.1-0.15, couleurs de la palette",
        "gradient-mesh": f"Fond avec gradient mesh: plusieurs radial-gradient superposés — radial-gradient(at 20% 30%, rgba({rgb},0.15) 0%, transparent 50%)..., statique, subtil",
        "grain-texture": "Fond uni sombre + overlay grain via ::after sur body, opacité très basse (0.03-0.05), effet premium photographique",
        "grid-lines": f"Grille de lignes fines en fond: background-image: linear-gradient(rgba({rgb},0.05) 1px, transparent 1px), linear-gradient(90deg, rgba({rgb},0.05) 1px, transparent 1px); background-size: 60px 60px",
        "geometric-shapes": "4-6 formes SVG en position absolute (cercles, hexagones), opacité 0.03-0.08, rotation lente animation CSS",
        "clean-gradient": "UN SEUL gradient linéaire doux (135deg) sur body, du fond principal vers teinte légèrement différente, sobre et élégant",
        "noise-overlay": "Fond uni + overlay noise SVG via ::before sur body, mix-blend-mode: overlay, opacité très basse, texture premium",
        "dot-grid": f"Pattern de points: background: radial-gradient(circle, rgba({rgb},0.08) 1px, transparent 1px); background-size: 24px 24px",
    }
    return bgs.get(bg_type, bgs["clean-gradient"])


def _get_hero_instructions(layout: str) -> str:
    """Instructions pour le layout hero."""
    layouts = {
        "split-left": "2 colonnes: texte GAUCHE (60%), image/visuel DROITE (40%), CTA buttons sous le texte",
        "split-right": "2 colonnes: image GAUCHE (40%), texte DROITE (60%), layout inversé",
        "centered": "Tout centré: titre, sous-titre, CTA, max-width:800px pour le texte, boutons côte à côte",
        "fullscreen-bg": "Image de fond 100vh avec overlay gradient sombre, texte centré en blanc, CTA prominent avec glow",
        "asymmetric": "Layout non-conventionnel asymétrique, titre très grand décalé, éléments hors grille, effet artistique",
        "stacked-layers": "Couches empilées: badge/pill → titre gradient → description → CTA → stats/logos, espacement propre",
    }
    return layouts.get(layout, layouts["centered"])


def build_design_directives(user_message: str) -> str:
    """Construit les directives de design complètes à injecter dans le prompt.
    
    Utilise ui_ux_knowledge (palettes WCAG-compliant, 57 font pairings, règles UX pro)
    si disponible, sinon le système legacy.
    Déterministe par description (seeded par hash MD5).
    """
    # Seed le random global pour que _get_pro_design() soit déterministe aussi
    _seed = hashlib.md5(user_message.encode()).hexdigest()
    _prev_state = random.getstate()
    random.seed(_seed)
    try:
        if _PRO_DESIGN_AVAILABLE:
            return _build_pro_design_directives(user_message)
        return _build_legacy_design_directives(user_message)
    finally:
        random.setstate(_prev_state)


def _build_pro_design_directives(user_message: str) -> str:
    """Directives de design pro — palettes WCAG + fonts contextuels + UX rules."""
    design = _get_pro_design(user_message)
    palette = design["palette"]
    font = design["font"]
    css_root = design["css_root"]
    font_import = design["font_import"]
    rgb = design["primary_rgb"]

    rng = random.Random(hashlib.md5(user_message.encode()).hexdigest())
    hero_layout = rng.choice(HERO_LAYOUTS)
    design_style = design["style"]
    bg_type = rng.choice(BACKGROUND_TYPES)

    testimonial_pattern = rng.choice(COMPONENT_PATTERNS["testimonials"])
    pricing_pattern = rng.choice(COMPONENT_PATTERNS["pricing"])
    features_pattern = rng.choice(COMPONENT_PATTERNS["features"])
    faq_pattern = rng.choice(COMPONENT_PATTERNS["faq"])

    return f"""
═══ DIRECTIVES DE DESIGN PRO (WCAG 2.1 AA — OBLIGATOIRE) ═══
Applique EXACTEMENT ces choix. Ne substitue PAS par tes valeurs par défaut.
Palette validée WCAG 2.1 AA pour l'accessibilité.

🎨 PALETTE: "{palette['name']}" (type: {palette['product_type']})
Thème: {"DARK" if design['is_dark'] else "LIGHT"} — Note: {palette['notes']}
```css
{css_root}
```
⚠️ Copie ce bloc :root EXACTEMENT. Utilise UNIQUEMENT var(--xxx), JAMAIS de couleurs en dur.

📝 TYPOGRAPHIE: "{font['name']}" ({font['heading']} / {font['body']})
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
{_font_link_from_import(font_import)}
```
```css
body {{ font-family: '{font['body']}', system-ui, sans-serif; line-height:1.7; font-size:16px; }}
h1,h2,h3,h4,h5,h6 {{ font-family: '{font['heading']}', system-ui, sans-serif; font-weight:700; line-height:1.2; letter-spacing:-0.02em; }}
```

🎭 STYLE VISUEL: {design_style['name'].upper()}
CSS signatures:
```css
/* {design_style['name']} */
.card {{ {design_style['css_rules']} }}
```

🖼️ HERO LAYOUT: "{hero_layout}"
{_get_hero_instructions(hero_layout)}

🌌 FOND DE PAGE: "{bg_type}"
{_get_bg_instructions(bg_type, rgb)}

♿ ACCESSIBILITÉ (WCAG 2.1 AA — NON NÉGOCIABLE):
- :focus {{ outline:2px solid var(--accent); outline-offset:2px; }}
- Toutes les images ont un alt descriptif
- Labels sur tous les champs de formulaire
- Zones tactiles min 44×44px (boutons, nav, icônes)
- touch-action:manipulation sur les boutons
- @media (prefers-reduced-motion: reduce) {{ * {{ animation-duration:0.01ms !important; transition-duration:0.01ms !important; }} }}

📦 PATTERNS COMPOSANTS:
- Témoignages: {testimonial_pattern}
- Pricing: {pricing_pattern}
- Features: {features_pattern}
- FAQ: {faq_pattern}

Variation unique: {rng.randint(1000, 9999)}
"""


def _font_link_from_import(css_import: str) -> str:
    """Extrait l'URL depuis @import url('...') pour en faire un <link>."""
    match = re.search(r"url\('([^']+)'\)", css_import)
    if match:
        return f'<link href="{match.group(1)}" rel="stylesheet">'
    return '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">'


def _build_legacy_design_directives(user_message: str) -> str:
    """Système legacy de directives de design (fallback si ui_ux_knowledge absent)."""
    palette = select_palette(user_message)
    font = select_font()
    rng = random.Random(hashlib.md5(user_message.encode()).hexdigest())
    hero_layout = rng.choice(HERO_LAYOUTS)
    design_style_name, design_style_desc = rng.choice(DESIGN_STYLES)
    bg_type = rng.choice(BACKGROUND_TYPES)
    rgb = _hex_to_rgb(palette["primary"])

    testimonial_pattern = rng.choice(COMPONENT_PATTERNS["testimonials"])
    pricing_pattern = rng.choice(COMPONENT_PATTERNS["pricing"])
    features_pattern = rng.choice(COMPONENT_PATTERNS["features"])
    faq_pattern = rng.choice(COMPONENT_PATTERNS["faq"])

    is_dark = palette["theme"] == "dark"

    return f"""
═══ DIRECTIVES DE DESIGN UNIQUES (OBLIGATOIRE) ═══
Applique EXACTEMENT ces choix. Ne substitue PAS par tes valeurs par défaut.

🎨 PALETTE: "{palette['name']}"
```css
:root {{
    --primary: {palette['primary']};
    --secondary: {palette['secondary']};
    --accent: {palette['accent']};
    --gradient: {palette['gradient']};
    --primary-rgb: {rgb};
    --bg-dark: {'#0a0a0f' if is_dark else '#ffffff'};
    --bg-card: {'#1a1a24' if is_dark else '#f8f9fa'};
    --text-primary: {'#ffffff' if is_dark else '#0f172a'};
    --text-secondary: {'#94a3b8' if is_dark else '#64748b'};
    --glass-bg: rgba(255, 255, 255, {'0.05' if is_dark else '0.8'});
    --glass-border: rgba(255, 255, 255, {'0.1' if is_dark else '0.2'});
    --glow: rgba({rgb}, 0.4);
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1);
    --shadow-xl: 0 20px 25px -5px rgba(0,0,0,0.1);
    --radius: 16px;
    --radius-sm: 8px;
    --radius-lg: 24px;
    --radius-full: 9999px;
    --transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}}
```
Copie-colle ce bloc :root dans ton CSS. Utilise UNIQUEMENT var(--xxx), JAMAIS de couleurs en dur.

📝 TYPOGRAPHIE: "{font['name']}"
```html
<link href="https://fonts.googleapis.com/css2?family={font['url']}&display=swap" rel="stylesheet">
```
body {{ font-family: '{font['name']}', system-ui, sans-serif; }}

🎭 STYLE VISUEL: {design_style_name.upper()}
{design_style_desc}

🖼️ HERO LAYOUT: "{hero_layout}"
{_get_hero_instructions(hero_layout)}

🌌 FOND DE PAGE: "{bg_type}"
{_get_bg_instructions(bg_type, rgb)}

📦 PATTERNS COMPOSANTS:
- Témoignages: {testimonial_pattern}
- Pricing: {pricing_pattern}
- Features: {features_pattern}
- FAQ: {faq_pattern}

Variation unique: {rng.randint(1000, 9999)}
"""


# ═══════════════════════════════════════════════════════════════
# WEBSITE SYSTEM PROMPT — Le méga-prompt pour la génération
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# RESPONSE PARSER — Extraction robuste des fichiers
# ═══════════════════════════════════════════════════════════════

def parse_website_response(raw_response: str) -> Optional[Dict[str, Any]]:
    """
    Parse la réponse du LLM pour en extraire les fichiers du site.
    
    Supporte:
    - JSON brut
    - JSON dans un bloc ```json ... ```
    - JSON malformé (réparation basique)
    """
    if not raw_response or not raw_response.strip():
        return None

    text = raw_response.strip()

    # 1. Essayer JSON brut directement
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "files" in data:
            return data
    except json.JSONDecodeError:
        pass  # essayer extraction suivante

    # 2. Extraire du bloc ```json ... ```
    json_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_block_match:
        try:
            data = json.loads(json_block_match.group(1).strip())
            if isinstance(data, dict) and "files" in data:
                return data
        except json.JSONDecodeError:
            pass  # essayer extraction suivante

    # 3. Trouver le premier { ... } valide contenant "files"
    brace_depth = 0
    start_idx = None
    for i, char in enumerate(text):
        if char == '{':
            if brace_depth == 0:
                start_idx = i
            brace_depth += 1
        elif char == '}':
            brace_depth -= 1
            if brace_depth == 0 and start_idx is not None:
                candidate = text[start_idx:i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict) and "files" in data:
                        return data
                except json.JSONDecodeError:
                    pass  # essayer le candidat suivant
                start_idx = None

    # 4. Fallback: essayer de parser des blocs ```html/css/js ```
    files = {}
    # Chercher ```html:path ou ```css:path ou ```javascript:path
    block_pattern = re.compile(
        r'```(?:html|css|javascript|js|php|sql|python|py|md|htaccess)(?::([^\n]+))?\s*\n(.*?)```',
        re.DOTALL
    )
    for match in block_pattern.finditer(text):
        path = match.group(1)
        content = match.group(2).strip()
        
        if not path:
            # Deviner le chemin depuis le type
            lang = match.group(0).split('```')[1].split(':')[0].split('\n')[0].strip()
            if lang in ('html',):
                path = 'index.html'
            elif lang in ('css',):
                path = 'css/styles.css'
            elif lang in ('javascript', 'js'):
                path = 'js/app.js'
            elif lang in ('php',):
                path = f'api/endpoint.php'
            elif lang in ('sql',):
                path = 'sql/schema.sql'
            else:
                path = f'file.{lang}'
        
        files[path] = content

    if files:
        return {
            "project_name": "generated-site",
            "project_type": "frontend",
            "files": files,
            "summary": "Site extrait depuis blocs de code",
        }

    logger.warning("Impossible de parser la réponse du LLM pour le site web")
    return None


# ═══════════════════════════════════════════════════════════════
# FILE WRITER — Écriture des fichiers sur disque
# ═══════════════════════════════════════════════════════════════

def write_project_to_disk(
    project_data: Dict[str, Any],
    output_dir: Path,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Écrit tous les fichiers du projet sur disque.
    
    Returns:
        {"success": bool, "files_written": int, "output_dir": str, "errors": [...]}
    """
    files = project_data.get("files", {})
    if not files:
        return {"success": False, "files_written": 0, "output_dir": str(output_dir), "errors": ["Aucun fichier à écrire"]}

    output_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    written = 0
    total = len(files)

    for i, (file_path, content) in enumerate(files.items()):
        try:
            # Nettoyer le chemin
            clean_path = file_path.lstrip("/").lstrip("\\")
            target = output_dir / clean_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written += 1
            
            if progress_callback:
                progress_callback(clean_path, i + 1, total)
                
        except Exception as e:
            errors.append(f"{file_path}: {e}")
            logger.error(f"Erreur écriture {file_path}: {e}")

    # ── Validation inter-fichiers post-écriture ──
    validation_report = None
    if _VALIDATOR_AVAILABLE and written > 0:
        try:
            # Normaliser les clés (retirer / ou \ en préfixe) pour cohérence
            clean_files = {
                k.lstrip("/").lstrip("\\"): v for k, v in files.items()
            }
            validation_report = _validate_project(clean_files, project_dir=output_dir)
            if not validation_report.is_clean:
                logger.warning(
                    "[write_project] 🔍 Validation: {}",
                    validation_report.summary(),
                )
                for issue in validation_report.errors[:5]:
                    errors.append(f"VALIDATION {issue}")
            else:
                logger.info("[write_project] 🔍 {}", validation_report.summary())
        except Exception as exc:
            logger.debug("[write_project] Validation skip: {}", exc)

    return {
        "success": written > 0,
        "files_written": written,
        "total_files": total,
        "output_dir": str(output_dir),
        "errors": errors,
        "validation_report": validation_report,
    }


# ═══════════════════════════════════════════════════════════════
# ZIP EXPORTER — Export avec progression
# ═══════════════════════════════════════════════════════════════

def export_to_zip(
    source_dir: Path,
    zip_path: Optional[Path] = None,
    progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
) -> Dict[str, Any]:
    """
    Exporte un dossier projet en ZIP avec progression.
    
    Args:
        source_dir: Dossier source du projet
        zip_path: Chemin du ZIP (par défaut: source_dir.zip)
        progress_callback: callback(file_name, current, total, percent)
    
    Returns:
        {"success": bool, "zip_path": str, "size_bytes": int, "files_count": int}
    """
    if not source_dir.exists():
        return {"success": False, "zip_path": "", "error": f"Dossier introuvable: {source_dir}"}

    if zip_path is None:
        zip_path = source_dir.parent / f"{source_dir.name}.zip"

    # Collecter tous les fichiers
    all_files = []
    for root, dirs, files in os.walk(source_dir):
        # Ignorer les dossiers cachés et __pycache__
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules']
        for f in files:
            if not f.startswith('.'):
                full_path = Path(root) / f
                rel_path = full_path.relative_to(source_dir)
                all_files.append((full_path, str(rel_path)))

    if not all_files:
        return {"success": False, "zip_path": str(zip_path), "error": "Aucun fichier à zipper"}

    total = len(all_files)
    
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, (full_path, rel_path) in enumerate(all_files):
                zf.write(full_path, rel_path)
                percent = ((i + 1) / total) * 100
                
                if progress_callback:
                    progress_callback(rel_path, i + 1, total, percent)

        size = zip_path.stat().st_size
        return {
            "success": True,
            "zip_path": str(zip_path),
            "size_bytes": size,
            "size_human": _format_size(size),
            "files_count": total,
        }
    except Exception as e:
        return {"success": False, "zip_path": str(zip_path), "error": str(e)}


def _format_size(size_bytes: int) -> str:
    """Formate une taille en bytes en format lisible."""
    for unit in ['o', 'Ko', 'Mo', 'Go']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} To"


# ═══════════════════════════════════════════════════════════════
# PREVIEW SERVER — Serveur HTTP local
# ═══════════════════════════════════════════════════════════════

_preview_process: Optional[subprocess.Popen] = None
_preview_port: int = 0


def start_preview_server(directory: Path, port: int = 8080) -> Dict[str, Any]:
    """Lance un serveur HTTP local pour prévisualiser le site."""
    global _preview_process, _preview_port

    # Arrêter le serveur précédent si actif
    stop_preview_server()

    if not directory.exists():
        return {"success": False, "error": f"Dossier introuvable: {directory}"}

    # Trouver un port libre
    import socket
    for p in range(port, port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', p))
                port = p
                break
        except OSError:
            continue

    try:
        _preview_process = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port)],
            cwd=str(directory),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _preview_port = port

        return {
            "success": True,
            "url": f"http://localhost:{port}",
            "port": port,
            "directory": str(directory),
            "pid": _preview_process.pid,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def stop_preview_server() -> Dict[str, Any]:
    """Arrête le serveur de preview."""
    global _preview_process, _preview_port

    if _preview_process is not None:
        try:
            _preview_process.terminate()
            _preview_process.wait(timeout=5)
        except Exception:
            try:
                _preview_process.kill()
            except Exception:
                pass  # process cleanup best-effort
        _preview_process = None
        old_port = _preview_port
        _preview_port = 0
        return {"success": True, "message": f"Serveur arrêté (port {old_port})"}
    
    return {"success": True, "message": "Aucun serveur actif"}


# ═══════════════════════════════════════════════════════════════
# TOOL HANDLERS — Fonctions appelées par le LumenaToolSystem
# ═══════════════════════════════════════════════════════════════

async def generate_website_handler(
    description: str,
    project_name: str = "",
    project_type: str = "frontend",
    output_dir: str = "",
) -> str:
    """
    Génère un site web complet à partir d'une description.
    
    Cet outil:
    1. Construit un prompt enrichi avec directives de design
    2. Le retourne pour que le LLM génère le code
    3. Parse la réponse et écrit les fichiers sur disque
    
    Comme le tool handler ne peut pas appeler le LLM directement,
    on retourne les instructions pour que le LLM génère le code.
    """
    if not description:
        return "❌ Erreur: paramètre 'description' requis (ex: 'site vitrine pour un restaurant italien')"

    # Générer le nom du projet
    if not project_name:
        project_name = re.sub(r'[^a-z0-9]+', '-', description.lower()[:40]).strip('-') or "website"

    # Résoudre le dossier de sortie
    if not output_dir:
        # Utiliser le workspace Lumena par défaut
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        out = workspace / project_name
    else:
        out = Path(output_dir) / project_name

    # Construire les directives de design
    design = build_design_directives(description)

    # Détecter si fullstack
    fullstack_keywords = ["backend", "api", "base de données", "bdd", "mysql", "php", "node",
                          "authentification", "login", "admin", "dashboard", "crud", "fullstack",
                          "complet", "e-commerce", "ecommerce", "boutique en ligne", "paiement"]
    is_fullstack = project_type == "fullstack" or any(kw in description.lower() for kw in fullstack_keywords)
    actual_type = "fullstack" if is_fullstack else "frontend"

    # Retourner les instructions pour le LLM
    return f"""🌐 GÉNÉRATION DE SITE WEB INITIÉE

📂 Projet: {project_name}
📁 Dossier: {out}
🔧 Type: {actual_type}

{design}

DEMANDE UTILISATEUR:
{description}

⚠️ MAINTENANT, tu DOIS générer le code du site en utilisant EXACTEMENT ce format JSON:

{{
  "project_name": "{project_name}",
  "project_type": "{actual_type}",
  "files": {{
    "index.html": "<!-- TOUT le HTML ici -->",
    "css/styles.css": "/* TOUT le CSS ici */",
    "js/app.js": "// TOUT le JS ici"
    {', "api/index.php": "<?php // API", "sql/schema.sql": "-- SQL"' if is_fullstack else ''}
  }},
  "summary": "Description du site"
}}

Après avoir généré le JSON, appelle l'outil `write_website_files` avec le JSON pour écrire les fichiers sur disque.

{WEBSITE_GENERATE_PROMPT}
"""


async def write_website_files_handler(
    json_data: str,
    output_dir: str = "",
    project_name: str = "",
) -> str:
    """
    Écrit les fichiers d'un site web sur disque à partir du JSON généré.
    """
    # Parser le JSON
    try:
        if isinstance(json_data, str):
            data = parse_website_response(json_data)
        else:
            data = json_data
    except Exception as e:
        return f"❌ Erreur parsing JSON: {e}"

    if not data or not data.get("files"):
        return "❌ Aucun fichier trouvé dans le JSON. Vérifie le format."

    # Résoudre le dossier
    pname = project_name or data.get("project_name", "website")
    if not output_dir:
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        out = workspace / pname
    else:
        out = Path(output_dir)

    # Écrire les fichiers
    progress_lines = []

    def on_progress(filename, current, total):
        bar_len = 20
        filled = int(bar_len * current / total)
        bar = '█' * filled + '░' * (bar_len - filled)
        pct = int(100 * current / total)
        progress_lines.append(f"  [{bar}] {pct}% - {filename}")

    result = write_project_to_disk(data, out, progress_callback=on_progress)

    if result["success"]:
        files_list = "\n".join(f"  📄 {f}" for f in data["files"].keys())
        progress_text = "\n".join(progress_lines[-5:])  # Dernières 5 lignes de progression
        
        summary = data.get("summary", "Site web généré")
        
        return f"""✅ Site web généré avec succès !

📂 Projet: {pname}
📁 Dossier: {out}
📊 {result['files_written']}/{result['total_files']} fichiers écrits

Fichiers créés:
{files_list}

Progression:
{progress_text}

📝 {summary}

💡 Commandes disponibles:
- "lance le serveur" → pour prévisualiser le site
- "modifie le site" → pour faire des changements
- "exporte en zip" → pour créer un ZIP du projet
- "envoie le zip" → pour envoyer le fichier"""
    else:
        errors_text = "\n".join(f"  ❌ {e}" for e in result.get("errors", []))
        return f"❌ Erreur lors de l'écriture:\n{errors_text}"


async def serve_website_handler(
    project_name: str = "",
    port: int = 8080,
    directory: str = "",
) -> str:
    """Lance un serveur de preview pour un site généré."""
    if directory:
        target = Path(directory)
    elif project_name:
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        # Chercher dans les sous-dossiers datés (workspace/YYYY-MM-DD/project_name)
        target = workspace / project_name
        if not target.exists() and workspace.exists():
            for date_dir in sorted(workspace.iterdir(), reverse=True):
                if date_dir.is_dir() and (date_dir / project_name).is_dir():
                    target = date_dir / project_name
                    break
    else:
        # Trouver le dernier projet
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        if workspace.exists():
            projects = sorted(workspace.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)
            dirs = [p for p in projects if p.is_dir()]
            if dirs:
                target = dirs[0]
            else:
                return "❌ Aucun projet trouvé dans le workspace"
        else:
            return "❌ Workspace introuvable"

    if not target.exists():
        return f"❌ Dossier introuvable: {target}"

    result = start_preview_server(target, port)
    
    if result["success"]:
        return f"""✅ Serveur de preview lancé !

🌐 URL: {result['url']}
📂 Dossier: {result['directory']}
🔌 Port: {result['port']}
🆔 PID: {result['pid']}

Ouvre ton navigateur sur {result['url']} pour voir le site.

Pour arrêter: "stop le serveur" """
    else:
        return f"❌ Erreur: {result.get('error', 'inconnue')}"


async def stop_server_handler() -> str:
    """Arrête le serveur de preview."""
    result = stop_preview_server()
    return f"✅ {result['message']}"


async def edit_website_handler(
    modifications: str,
    project_name: str = "",
    directory: str = "",
) -> str:
    """
    Prépare une modification chirurgicale d'un site existant.
    Retourne les instructions pour que le LLM modifie les fichiers.
    """
    if not modifications:
        return "❌ Erreur: paramètre 'modifications' requis (ex: 'ajoute une section témoignages')"

    # Trouver le projet
    if directory:
        target = Path(directory)
    elif project_name:
        from ..utils.paths import WORKSPACE_DIR
        target = WORKSPACE_DIR / project_name
    else:
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        if workspace.exists():
            projects = sorted(workspace.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)
            dirs = [p for p in projects if p.is_dir()]
            if dirs:
                target = dirs[0]
                project_name = target.name
            else:
                return "❌ Aucun projet trouvé"
        else:
            return "❌ Workspace introuvable"

    if not target.exists():
        return f"❌ Projet introuvable: {target}"

    # Charger les fichiers existants
    existing_files = {}
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules']
        for f in files:
            full = Path(root) / f
            rel = str(full.relative_to(target)).replace('\\', '/')
            try:
                content = full.read_text(encoding='utf-8', errors='replace')
                existing_files[rel] = content
            except Exception:
                pass  # fichier binaire ou illisible

    # Construire le contexte des fichiers existants (limiter la taille)
    context_parts = []
    total_chars = 0
    max_chars = 100000
    
    for path, content in existing_files.items():
        if total_chars + len(content) > max_chars:
            context_parts.append(f"\n--- {path} (TRONQUÉ) ---\n{content[:2000]}...\n")
            break
        context_parts.append(f"\n--- {path} ---\n{content}\n")
        total_chars += len(content)

    files_context = "\n".join(context_parts)

    return f"""🔧 MODE ÉDITION — Projet: {project_name}

MODIFICATIONS DEMANDÉES:
{modifications}

{WEBSITE_EDIT_PROMPT}

{files_context}

⚠️ Retourne UNIQUEMENT le JSON avec les fichiers COMPLETS modifiés.
Ensuite appelle `write_website_files` avec le JSON et output_dir="{target}" pour sauvegarder.
"""


async def export_website_zip_handler(
    project_name: str = "",
    directory: str = "",
    output_path: str = "",
) -> str:
    """
    Exporte un projet en ZIP avec progression.
    """
    # Trouver le projet
    if directory:
        target = Path(directory)
    elif project_name:
        from ..utils.paths import WORKSPACE_DIR
        target = WORKSPACE_DIR / project_name
    else:
        from ..utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        if workspace.exists():
            projects = sorted(workspace.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)
            dirs = [p for p in projects if p.is_dir()]
            if dirs:
                target = dirs[0]
                project_name = target.name
            else:
                return "❌ Aucun projet trouvé dans le workspace"
        else:
            return "❌ Workspace introuvable"

    if not target.exists():
        return f"❌ Projet introuvable: {target}"

    # Chemin du ZIP
    if output_path:
        zip_path = Path(output_path)
    else:
        zip_path = target.parent / f"{target.name}.zip"

    # Progress tracking
    progress_lines = []
    start_time = time.time()

    def on_progress(filename, current, total, percent):
        bar_len = 30
        filled = int(bar_len * percent / 100)
        bar = '█' * filled + '░' * (bar_len - filled)
        elapsed = time.time() - start_time
        progress_lines.append(f"  [{bar}] {percent:.0f}% ({current}/{total}) - {filename}")

    result = export_to_zip(target, zip_path, progress_callback=on_progress)

    if result["success"]:
        elapsed = time.time() - start_time
        # Montrer les dernières étapes de progression
        progress_display = "\n".join(progress_lines[-8:]) if progress_lines else "  Aucune progression"
        
        return f"""✅ ZIP exporté avec succès !

📦 Fichier: {result['zip_path']}
📊 Taille: {result['size_human']}
📁 Fichiers: {result['files_count']}
⏱️ Durée: {elapsed:.1f}s

Progression:
{progress_display}

💡 Le ZIP est prêt. Tu peux demander "envoie le zip" pour l'envoyer."""
    else:
        return f"❌ Erreur export ZIP: {result.get('error', 'inconnue')}"


async def list_website_projects_handler() -> str:
    """Liste les projets web dans le workspace."""
    from ..utils.paths import WORKSPACE_DIR
    workspace = WORKSPACE_DIR

    if not workspace.exists():
        return "📁 Aucun projet (workspace vide)"

    projects = []
    for entry in sorted(workspace.iterdir()):
        if entry.is_dir() and not entry.name.startswith('.'):
            # Compter les fichiers
            file_count = sum(1 for _ in entry.rglob('*') if _.is_file())
            # Taille totale
            total_size = sum(f.stat().st_size for f in entry.rglob('*') if f.is_file())
            # Date de modification
            mtime = datetime.fromtimestamp(entry.stat().st_mtime).strftime('%d/%m/%Y %H:%M')
            
            # Détecter le type
            has_html = any(entry.rglob('*.html'))
            has_php = any(entry.rglob('*.php'))
            has_sql = any(entry.rglob('*.sql'))
            ptype = "fullstack" if (has_php or has_sql) else "frontend" if has_html else "autre"
            
            projects.append(f"  📂 {entry.name} — {ptype} | {file_count} fichiers | {_format_size(total_size)} | {mtime}")

    if not projects:
        return "📁 Aucun projet dans le workspace"

    return f"""📁 Projets dans le workspace ({len(projects)}):

{chr(10).join(projects)}

💡 Commandes: "ouvre [nom]", "modifie [nom]", "exporte [nom] en zip" """


# ═══════════════════════════════════════════════════════════════
# REGISTRATION — Fonction pour enregistrer les outils
# ═══════════════════════════════════════════════════════════════

def register_website_tools(tool_system) -> int:
    """
    Enregistre les outils website dans le LumenaToolSystem.
    
    Returns:
        Nombre d'outils enregistrés
    """
    tools = [
        {
            "name": "generate_website",
            "description": (
                "Génère un site web complet (frontend + backend + API) à partir d'une description. "
                "Crée la structure du projet, les fichiers HTML/CSS/JS, et optionnellement le backend PHP/Node + SQL. "
                "Utilise pour: créer un site, faire une landing page, construire une app web, etc."
            ),
            "parameters": {
                "description": {"type": "string", "description": "Description du site à générer (ex: 'site vitrine pour restaurant italien haut de gamme')", "required": True},
                "project_name": {"type": "string", "description": "Nom du projet (ex: 'restaurant-bella')", "default": ""},
                "project_type": {"type": "string", "description": "Type: 'frontend' ou 'fullstack'", "default": "frontend"},
                "output_dir": {"type": "string", "description": "Dossier de sortie (optionnel)", "default": ""},
            },
            "handler": generate_website_handler,
            "category": "website",
        },
        {
            "name": "write_website_files",
            "description": (
                "Écrit les fichiers d'un site web sur disque à partir du JSON généré par le LLM. "
                "Appeler après generate_website quand le code JSON est prêt."
            ),
            "parameters": {
                "json_data": {"type": "string", "description": "Le JSON contenant les fichiers du site ({files: {path: content}})", "required": True},
                "output_dir": {"type": "string", "description": "Dossier de sortie (optionnel)", "default": ""},
                "project_name": {"type": "string", "description": "Nom du projet (optionnel)", "default": ""},
            },
            "handler": write_website_files_handler,
            "category": "website",
        },
        {
            "name": "serve_website",
            "description": (
                "Lance un serveur HTTP local pour prévisualiser un site web généré. "
                "Ouvre le site dans le navigateur."
            ),
            "parameters": {
                "project_name": {"type": "string", "description": "Nom du projet à servir", "default": ""},
                "port": {"type": "integer", "description": "Port HTTP (défaut: 8080)", "default": 8080},
                "directory": {"type": "string", "description": "Dossier à servir (alternatif)", "default": ""},
            },
            "handler": serve_website_handler,
            "category": "website",
        },
        {
            "name": "stop_website_server",
            "description": "Arrête le serveur de preview du site web.",
            "parameters": {},
            "handler": stop_server_handler,
            "category": "website",
        },
        {
            "name": "edit_website",
            "description": (
                "Modifie chirurgicalement un site web existant. "
                "Charge les fichiers du projet et prépare les instructions de modification. "
                "Utilise pour: ajouter une page, modifier le style, corriger un bug, etc."
            ),
            "parameters": {
                "modifications": {"type": "string", "description": "Description des modifications (ex: 'ajoute une section témoignages')", "required": True},
                "project_name": {"type": "string", "description": "Nom du projet à modifier", "default": ""},
                "directory": {"type": "string", "description": "Dossier du projet (alternatif)", "default": ""},
            },
            "handler": edit_website_handler,
            "category": "website",
        },
        {
            "name": "export_website_zip",
            "description": (
                "Exporte un projet web en fichier ZIP avec progression en temps réel. "
                "Affiche la barre de progression et les statistiques du ZIP. "
                "Le ZIP peut ensuite être envoyé."
            ),
            "parameters": {
                "project_name": {"type": "string", "description": "Nom du projet à exporter", "default": ""},
                "directory": {"type": "string", "description": "Dossier du projet (alternatif)", "default": ""},
                "output_path": {"type": "string", "description": "Chemin de sortie du ZIP (optionnel)", "default": ""},
            },
            "handler": export_website_zip_handler,
            "category": "website",
        },
        {
            "name": "list_website_projects",
            "description": "Liste tous les projets web dans le workspace Lumena avec leurs stats.",
            "parameters": {},
            "handler": list_website_projects_handler,
            "category": "website",
        },
    ]

    count = 0
    for tool_def in tools:
        try:
            tool_system._register_tool(
                name=tool_def["name"],
                description=tool_def["description"],
                parameters=tool_def["parameters"],
                handler=tool_def["handler"],
                category=tool_def.get("category", "website"),
                requires_confirmation=tool_def.get("requires_confirmation", False),
            )
            count += 1
        except Exception as e:
            logger.error(f"Erreur enregistrement outil {tool_def['name']}: {e}")

    if count > 0:
        logger.info(f"🌐 {count} outils website enregistrés")
    
    return count
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
