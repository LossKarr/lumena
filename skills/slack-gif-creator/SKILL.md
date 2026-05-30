---
name: slack-gif-creator
description: "Création de GIFs ANIMÉS optimisés pour Slack (contraintes de taille, validation, concepts d'animation). RÈGLE LUMENA PRIORITAIRE : ce skill est réservé au GIF animé (pas d'outil natif équivalent). Pour une image FIXE / logo / avatar → utiliser les outils image natifs (generate_image, generate_logo), pas ce skill."
keywords: [gif, gif anime, slack gif, animation gif, gif optimise, creer gif, animated gif, gif slack, faire un gif, generer gif, gif pour slack]
license: Lumena - usage interne
---

# Slack GIF Creator (GIF animé uniquement)

Ce skill couvre la création de **GIFs animés** pour Slack — il n'existe **pas** d'outil
natif de GIF animé, donc c'est sa niche légitime (contraintes de taille Slack, frames,
boucle, validation).

## Quand utiliser quoi

| Besoin | Quoi utiliser |
|---|---|
| **GIF animé** pour Slack (frames, boucle) | **Ce skill** |
| Image **fixe** (illustration, scène) | `generate_image` (skill `image`) |
| **Logo / avatar** fixe | `generate_logo` |
| **SVG / icône** | `generate_svg` |
| Retoucher / détourer une frame source | `edit_image` / `remove_background` |

## Règles
1. **GIF animé** → ce skill (code de génération de frames justifié ici, faute d'outil natif).
2. Toute image **fixe** → outils image natifs, pas ce skill.
3. Respecter les contraintes Slack (taille/poids) documentées dans le dossier.
