---
name: theme-factory
description: "Catalogue de thèmes (couleurs/typographies) à appliquer aux artefacts : slides, docs, sites, PDF. RÈGLE LUMENA PRIORITAIRE : choisir/définir un thème ici, mais l'APPLIQUER via les outils natifs (edit_website, edit_docx, edit_pptx, create_pdf/html_to_pdf, outils image). Ne pas styliser un fichier à la main si un outil natif le fait."
keywords: [theme, style, couleurs, palette, charte, design theme, appliquer theme, look and feel, theme site, theme doc, theme slides, couleur fond, typographie theme, dark theme, light theme]
license: Lumena - usage interne
---

# Theme Factory — Choisis un thème, applique-le via les outils natifs

Ce skill sert à **définir/choisir** un thème (palette + typo + ambiance), puis à
l'**appliquer** avec les outils natifs Lumena. Pour la charte officielle Lumena, voir
le skill `brand-guidelines`.

## Étape 1 — Choisir le thème
- Définir : couleurs (fond, surface, accent, texte), police, arrondis, densité, ambiance.
- Proposer un thème cohérent (clair/sombre, sobre/premium…) ou réutiliser la charte Lumena.

## Étape 2 — Appliquer via l'outil natif (selon le support)

| Support | Outil natif |
|---|---|
| Site web | `edit_website` (ou `generate_website` à la création) |
| Document Word | `edit_docx` |
| Présentation | `edit_pptx` |
| PDF / poster | `create_pdf` ou `html_to_pdf` |
| Image / bannière | outils `image` (`generate_image`, `compose_image`) |

## Règles
1. **Ne pas styliser manuellement** un fichier si l'outil d'édition natif existe.
2. Garder la cohérence avec la charte Lumena (`brand-guidelines`) sauf demande contraire.
