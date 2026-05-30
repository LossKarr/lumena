---
name: canvas-design
description: "Guide de DIRECTION ARTISTIQUE pour visuels statiques (poster, affiche, illustration, design). RÈGLE LUMENA PRIORITAIRE : ce skill conseille la composition, mais la PRODUCTION passe TOUJOURS par les outils natifs (image bitmap → generate_image/edit_image/compose_image ; SVG → generate_svg ; PDF/poster → create_pdf/html_to_pdf). Ne pas dessiner à la main en code (canvas/PIL) si un outil natif couvre le besoin."
keywords: [poster, affiche, visuel, visuelle, design graphique, illustration, png, image, artwork, dessin, art visuel, creation graphique, static design, piece artistique]
license: Lumena - usage interne
---

# Canvas / Design visuel — Conçois, puis route vers les outils natifs

Ce skill aide à **penser** un visuel (composition, hiérarchie, couleurs, typographie),
puis à le **produire via les outils natifs Lumena**. Ne génère pas d'image en codant un
canvas/PIL à la main.

## Étape 1 — Direction artistique (texte)
- But, audience, format, ton.
- Composition, focal point, palette (cf skill `brand-guidelines` pour la charte Lumena).
- Créer une œuvre **originale** — jamais copier le travail d'un artiste existant.

## Étape 2 — Router la production vers l'outil natif

| Sortie voulue | Outil natif |
|---|---|
| Image bitmap (poster, illustration, scène) | `generate_image` |
| Retoucher / varier une image | `edit_image` |
| Assembler plusieurs visuels | `compose_image` |
| Logo | `generate_logo` |
| SVG / vectoriel / icône | `generate_svg` |
| Détourer / changer le fond | `remove_background` / `replace_background` |
| Affiche / poster en **PDF** | `create_pdf` ou `html_to_pdf` |
| Agrandir la résolution | `upscale_image` |

## Règles
1. **Conseil d'abord, production via outil natif ensuite.**
2. **Ne pas** produire `.png`/`.pdf` à la main en code tant qu'un outil natif convient.
3. Si l'utilisateur veut seulement une direction/critique design → répondre en texte.
