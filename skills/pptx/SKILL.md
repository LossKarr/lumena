---
name: pptx
description: "À utiliser pour créer, lire ou éditer des présentations PowerPoint (.pptx) : decks, pitchs, diaporamas. RÈGLE LUMENA PRIORITAIRE : Lumena possède des OUTILS NATIFS (create_pptx, edit_pptx, read_document). Utilise TOUJOURS ces outils. N'écris JAMAIS de script Python (python-pptx) pour produire ou modifier un .pptx tant qu'un outil natif couvre le besoin. Ne pas utiliser pour PDF, Word, tableurs ou tâches de code."
keywords: [presentation, slides, deck, pptx, powerpoint, diapositive, diapo, pitch deck, diaporama, slide deck, creer presentation, modifier slides, presenter, conference]
license: Lumena - usage interne
---

# PPTX — Utilise les outils natifs Lumena, pas du code

⛔ **NE CODE PAS de script Python (python-pptx) pour produire ou modifier un .pptx.**
Lumena a des outils natifs qui le font en un appel. Le code n'est qu'un dernier recours.

## Table de routage : tâche → outil natif Lumena

| Tu veux… | Utilise l'outil | Au lieu de coder |
|---|---|---|
| **Créer une présentation / deck** | `create_pptx` | ~~python-pptx~~ |
| Créer depuis un **modèle** | `create_from_template` (+ `list_templates`) | — |
| **Éditer** un .pptx existant (slides, texte, notes) | `edit_pptx` | ~~python-pptx~~ |
| **Lire / extraire** le contenu des slides | `read_document` puis `document_summary` | ~~python-pptx~~ |
| Ajouter un **graphique** | `generate_chart` puis l'insérer | — |
| **Convertir** vers PDF | `convert_document` | — |

## Règle d'usage

1. Trouve la tâche dans la table et appelle l'outil natif.
2. Ne passe au code python-pptx QUE si la tâche n'est couverte par aucun outil natif.
