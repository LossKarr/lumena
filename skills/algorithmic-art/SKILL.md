---
name: algorithmic-art
description: "Art génératif CODÉ et interactif en p5.js (flow fields, particules, systèmes algorithmiques, exploration de paramètres seedés). RÈGLE LUMENA PRIORITAIRE : ce skill est réservé à l'art interactif p5.js. Pour une simple image → generate_image ; pour un SVG/logo → generate_svg/generate_logo. Créer des œuvres originales (jamais copier un artiste existant)."
keywords: [art generatif, generative art, p5js, p5, code art, flow field, particules, particles, algorithmique, dessin code, art algorithme, seeded, randomness, interactive, visualization]
license: Lumena - usage interne
---

# Art algorithmique (p5.js interactif)

Ce skill couvre **uniquement** l'art génératif **codé et interactif** : p5.js, flow
fields, systèmes de particules, exploration de paramètres avec graine (seed). C'est sa
niche — il n'entre pas en collision avec les outils image natifs.

## Quand utiliser quoi

| Besoin | Quoi utiliser |
|---|---|
| **Œuvre interactive p5.js** (paramètres, seed, animation contrôlable) | **Ce skill** (template `templates/` du dossier) |
| Une simple **image / illustration** | `generate_image` (skill `image`) |
| Un **SVG / logo / icône** | `generate_svg` / `generate_logo` |
| Une image à **retoucher** | `edit_image` |

## Réalisation (p5.js interactif)
- Partir du template fourni dans `templates/` (viewer HTML + générateur) — ne pas repartir de zéro.
- Produire un artefact HTML autonome (p5.js depuis CDN) avec contrôles de paramètres.
- Œuvre **originale** uniquement.

## Règle
Si la demande est « une image » sans interactivité ni intention génial-algorithmique
explicite → **router vers `generate_image`/`generate_svg`**, pas vers p5.js.
