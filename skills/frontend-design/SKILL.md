---
name: frontend-design
description: "Guide de DIRECTION UX/design pour des interfaces web distinctives et haut de gamme (évite l'esthétique générique « AI slop »). Aide à choisir parcours, structure, hiérarchie visuelle, composants, interactions. RÈGLE LUMENA PRIORITAIRE : ce skill NE produit PAS le code par défaut — il guide le design, puis route vers les outils natifs (generate_website / edit_website pour le site, outils image pour les visuels, CodeAgent pour du code framework explicitement demandé)."
keywords: [site web, frontend, html css, interface, composant, landing page, dashboard, react component, design web, ui ux, web design, beau site, navbar, ameliorer design, refaire design, styliser]
license: Lumena - usage interne
---

# Frontend design — Guide UX/design (ne code pas par défaut)

Ce skill aide à **concevoir** une interface distinctive et soignée, puis à **router**
vers le bon outil natif pour la réaliser. Il ne doit **pas** écrire de code directement
sauf si l'utilisateur demande explicitement un composant/du code.

## Étape 1 — Penser le design (toujours)

Avant toute réalisation, poser une direction claire :
- **But & audience** : quel problème, pour qui ?
- **Ton** : choisir une direction nette (minimal radical, maximaliste, rétro-futuriste,
  éditorial, brutaliste, luxe, pastel…). Intentionnalité > intensité.
- **Contraintes** : framework imposé ?, performance, accessibilité.
- **Différenciation** : qu'est-ce qui rend l'interface mémorable ?
- Éviter l'« AI slop » : pas de tout-centré, dégradés violets partout, coins arrondis
  uniformes, police Inter par défaut.

## Étape 2 — Router vers le bon outil natif

| L'utilisateur veut… | Route vers |
|---|---|
| **Un site / landing / dashboard** | `generate_website` (cf skill `website-generator`) |
| **Modifier le design d'un site existant** | `edit_website` |
| **Prévisualiser** | `serve_website` + vérif via `browser_*` |
| **Une image / illustration / bannière** | outils `image` (`generate_image`, `compose_image`) |
| **Un logo / SVG / icône** | `generate_logo`, `generate_svg` |
| **Un document stylé** (poster, rapport) | `create_pdf` / `html_to_pdf` (cf skills `pdf`/`documents`) |
| **Du code framework** (React/Vue, composant précis) **explicitement demandé** | `delegate_task` (CodeAgent) |
| **Juste un conseil / une direction design** | répondre directement en texte |

## Règles

1. **Conseil design d'abord**, réalisation ensuite — via l'outil natif adapté.
2. Ne JAMAIS « implémenter le code » par réflexe : si c'est un site → `generate_website` ;
   si c'est un visuel → outils `image` ; si c'est vraiment du code framework demandé → CodeAgent.
3. Ce skill apporte la **qualité visuelle/UX** ; il ne remplace pas `generate_website`
   ni `web-artifacts-builder`, il les oriente.
