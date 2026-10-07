---
name: remotion-skill
description: Créer, modifier, corriger et rendre des vidéos Remotion/React dans Lumena. Use when the user asks for a Remotion video, reel, short, explainer, animated composition, motion graphics, captions, subtitles, transitions, voiceover, charts, maps, audio visualization, FFmpeg trimming, or when debugging an existing Remotion project.
keywords: [remotion, video, reel, short, tiktok, animation, motion graphics, composition, captions, subtitles, transitions, voiceover, audio visualization, charts, maps, render]
---

# Remotion For Lumena

Utiliser ce skill pour travailler sur des projets Remotion dans le workflow Lumena.

## Workflow Lumena

1. Si l'utilisateur veut generer ou modifier une video a partir d'une description, preferer les outils Lumena `generate_video`, `edit_video`, `preview_video`, `retry_video_render`, `list_video_projects`, `get_video_job` et `cancel_video_job`.
2. Laisser `creation_mode=auto` par defaut. Utiliser `safe` pour imposer le compilateur borne. Utiliser `expert` uniquement quand l'utilisateur le demande ou que le modele maitrise Remotion et que sa liberte TSX est utile.
3. Si la demande concerne le pipeline video interne de Lumena, lire d'abord `src/reasoning/handlers/remotion.py`, `src/tools/remotion_engine.py`, `src/tools/remotion_spec.py`, `src/tools/remotion_quality.py` et `src/tools/remotion_prompts.py`.
4. Si la demande cible un projet Remotion existant, faire des edits chirurgicaux et garder les `Composition id` stables. `edit_video` doit ensuite valider et rendre une preuve.
5. Charger uniquement les references necessaires au besoin; ne pas lire tout le dossier `references/` par defaut.

## Guardrails

- Declarer explicitement `fps`, `width`, `height` et `durationInFrames`.
- Preferer les assets locaux fournis par l'utilisateur; si besoin, les placer dans `public/` et utiliser des chemins stables.
- Ne jamais remplacer silencieusement un asset absent par une URL externe.
- Ne jamais contourner la sandbox, les racines autorisees, la validation pre-rendu ou le controle qualite.
- Distinguer un rendu technique termine d'une video validee. Un MP4 present ne suffit pas a prouver la qualite.
- Utiliser `get_video_job` pour observer le travail. N'utiliser `cancel_video_job` qu'apres une demande explicite d'arret.
- Apres une panne Docker, npm ou reseau, utiliser `retry_video_render`: il reprend le projet valide sans demander au modele de reecrire les scenes.
- Ne pas inventer d'API Remotion ou de composants inexistants.
- Ne pas laisser de placeholder, TODO ou imports morts dans le code final.
- Si l'utilisateur veut surtout un rendu video final, ne pas deverser tout le TSX dans la reponse: utiliser le pipeline Lumena.
- Pour une verification rapide, privilegier `npx remotion studio` ou un still render si l'environnement le permet.
- Pour la coupe, la detection de silence ou les operations media hors timeline Remotion, utiliser les patterns FFmpeg.

## References

Lire seulement les fichiers utiles:

- Structure et metadata: `references/compositions.md`, `references/parameters.md`, `references/calculate-metadata.md`
- Animation et timing: `references/animations.md`, `references/timing.md`, `references/sequencing.md`, `references/transitions.md`, `references/text-animations.md`
- Media et assets: `references/assets.md`, `references/images.md`, `references/videos.md`, `references/audio.md`, `references/fonts.md`, `references/gifs.md`
- Captions et sous-titres: `references/subtitles.md`, `references/display-captions.md`, `references/import-srt-captions.md`, `references/transcribe-captions.md`
- Cas avances: `references/charts.md`, `references/maps.md`, `references/lottie.md`, `references/light-leaks.md`, `references/3d.md`, `references/transparent-videos.md`
- Utilitaires media: `references/ffmpeg.md`, `references/silence-detection.md`, `references/can-decode.md`, `references/get-audio-duration.md`, `references/get-video-duration.md`, `references/get-video-dimensions.md`, `references/extract-frames.md`

## Decision Rules

- Si la demande est "fais-moi une video" ou "genere un reel", partir d'abord sur les handlers Remotion de Lumena.
- Si le modele est faible ou inconnu, conserver le mode `auto` ou `safe`; ne pas lui demander du TSX libre pour prouver sa competence.
- Si le modele est expert Remotion, lui laisser construire des compositions avancees en mode `expert`, puis laisser Lumena valider, sandboxer, rendre et controler le resultat.
- Si la demande est "corrige ce projet Remotion" ou "edite ce composant", ouvrir les fichiers du projet et patcher localement.
- Si la demande est conceptuelle seulement, repondre directement et lire les references seulement si elles apportent quelque chose.
- Si l'utilisateur fournit des assets, des timings ou un format, les respecter avant toute heuristique.
