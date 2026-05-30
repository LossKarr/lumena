---
name: image
description: "À utiliser pour générer ou éditer des images : illustration, photo, logo, icône, SVG, miniature/thumbnail, détourage, fond. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les 13 outils image natifs (generate_image, edit_image, generate_logo, generate_svg…). N'écris JAMAIS de code canvas/PIL/SVG à la main tant qu'un outil natif couvre le besoin."
keywords: [image, illustration, photo, dessin, logo, icone, icon, svg, vectoriel, thumbnail, miniature, vignette, detourage, fond, background, generer image, creer image, retoucher, upscale, ia image]
license: Lumena - usage interne
---

# Images — Utilise les outils natifs `image`, pas du code

⛔ **NE CODE PAS** de canvas HTML, PIL/Pillow, ou de SVG à la main pour produire une
image. Lumena a 13 outils image natifs. Le code n'est justifié que si l'utilisateur
demande explicitement une génération programmatique (ex : data-viz custom).

## Table de routage : besoin → outil natif

| Tu veux… | Outil |
|---|---|
| **Générer une image** (illustration, photo, scène) | `generate_image` |
| **Éditer / retoucher** une image existante | `edit_image` |
| **Composer / assembler** plusieurs images | `compose_image` |
| **Logo** | `generate_logo` |
| **SVG / vectoriel / icône** | `generate_svg` |
| **Miniature / thumbnail** | `generate_thumbnail` · qualité max : `generate_thumbnail_pro` |
| **Détourer** (enlever le fond) | `remove_background` |
| **Remplacer le fond** | `replace_background` |
| **Croquis → image** | `sketch_to_image` |
| **Agrandir / améliorer la résolution** | `upscale_image` |
| **Texte d'accroche pour visuel** | `generate_headlines` |
| **Voir les modèles disponibles** | `list_image_models` |

## Règles
1. Choisis l'outil dans la table et appelle-le directement.
2. Pour une image **dans un site** → ce skill produit l'asset, puis `edit_website`/`generate_website` l'intègre.
3. Code (canvas/PIL/matplotlib) **uniquement** sur demande explicite de génération programmatique.
4. Vérifie le modèle dispo (`list_image_models`) si une génération échoue.
