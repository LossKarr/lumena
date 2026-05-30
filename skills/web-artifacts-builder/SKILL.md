---
name: web-artifacts-builder
description: "À utiliser pour créer ou modifier un site / une app web / un artefact web. RÈGLE LUMENA PRIORITAIRE : Lumena a des outils natifs (generate_website, edit_website, serve_website). Utilise-les TOUJOURS pour produire un site. Ne force PAS une stack React/Tailwind/shadcn/Vite ni de scripts de bundling — réserve le code complexe au CodeAgent, et seulement si l'utilisateur le demande explicitement."
keywords: [react, tailwind, shadcn, composant react, artifact, widget, interface react, component, multi-component, state management, routing, shadcn ui, creer composant, app react]
license: Lumena - usage interne
---

# Artefacts / sites web — Utilise les outils natifs, pas une stack lourde

⛔ **NE FORCE PAS React/Tailwind/shadcn/Vite ni de scripts de bundling** pour produire
un site ou un artefact web. Lumena génère des sites complets nativement, en un appel.
Le code framework n'est justifié QUE si l'utilisateur le demande explicitement.

## Table de routage : besoin → outil natif

| Tu veux… | Utilise | Au lieu de |
|---|---|---|
| **Créer un site / app complet** | `generate_website` (frontend + backend + API si besoin) | ~~init-artifact.sh + React~~ |
| **Modifier** un site existant | `edit_website` | ~~éditer le code à la main~~ |
| **Prévisualiser** localement | `serve_website` (puis `stop_website_server`) | ~~bundle-artifact.sh~~ |
| **Exporter** en ZIP | `export_website_zip` | — |
| **Vérifier** le projet web | `check_web_project` | — |
| Une **image / logo / SVG** dans le site | outils `image` (`generate_image`, `generate_logo`, `generate_svg`) | — |
| **Code framework complexe** (React/state/routing) **explicitement demandé** | `delegate_task` (CodeAgent) | — |

## Règles de décision

1. **Par défaut → `generate_website`** : c'est le bon outil pour « fais-moi un site / une landing / un dashboard ».
2. **Site déjà existant → `edit_website`** (ne régénère pas tout).
3. **Prévisualiser → `serve_website`**, puis `browser_*` pour vérifier (cf skill `webapp-testing`).
4. **React/Tailwind/shadcn UNIQUEMENT** si l'utilisateur le demande nommément → alors `delegate_task` (CodeAgent), pas un script de scaffolding ici.
5. `website-generator` reste le skill principal pour toute création de site — ce skill ne doit pas le contourner.

> Les scripts de scaffolding (`init-artifact.sh`, `bundle-artifact.sh`) restent dans le dossier en dernier recours, mais ne sont PAS le chemin par défaut.
