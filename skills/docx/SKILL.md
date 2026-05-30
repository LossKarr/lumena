---
name: docx
description: "À utiliser pour créer, lire, éditer ou manipuler des documents Word (.docx) : rapports, lettres, mémos, contrats, templates. RÈGLE LUMENA PRIORITAIRE : Lumena possède des OUTILS NATIFS (create_docx, edit_docx, read_document, convert_document). Utilise TOUJOURS ces outils. N'écris JAMAIS de script Python (python-docx) pour produire ou modifier un .docx tant qu'un outil natif couvre le besoin. Ne pas utiliser pour PDF, tableurs ou tâches de code."
keywords: [word, docx, document word, rapport word, lettre word, memo word, template word, fichier word, contrat, courrier, mise en forme, table of contents, sommaire]
license: Lumena - usage interne
---

# DOCX — Utilise les outils natifs Lumena, pas du code

⛔ **NE CODE PAS de script Python (python-docx) pour produire ou modifier un .docx.**
Lumena a des outils natifs qui le font en un appel. Le code n'est qu'un dernier recours.

## Table de routage : tâche → outil natif Lumena

| Tu veux… | Utilise l'outil | Au lieu de coder |
|---|---|---|
| **Créer un document Word** (rapport, lettre, mémo, contrat) | `create_docx` (markdown inline supporté) | ~~python-docx~~ |
| Créer depuis un **modèle** | `create_from_template` (+ `list_templates`) | ~~docxtpl~~ |
| **Éditer** un .docx existant (find/replace, ajouts) | `edit_docx` | ~~python-docx~~ |
| **Lire / extraire** le contenu | `read_document` puis `analyze_document` / `document_summary` | ~~python-docx~~ |
| **Convertir** vers PDF/autre | `convert_document` (ou `html_to_pdf`) | — |
| **Comparer** deux documents | `compare_documents` | — |
| Ajouter un **filigrane** | `add_watermark` | — |

## Règle d'usage

1. Trouve la tâche dans la table et appelle l'outil natif.
2. `create_docx` accepte du **markdown** (titres, gras, listes, tableaux) — pas besoin
   de coder la mise en forme.
3. Ne passe au code python-docx QUE si la tâche n'est couverte par aucun outil natif.
