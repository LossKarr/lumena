---
name: pdf
description: "À utiliser pour toute opération sur des fichiers PDF. RÈGLE LUMENA PRIORITAIRE : Lumena possède des OUTILS NATIFS pour les PDF (create_pdf, html_to_pdf, merge_pdfs, split_pdf, add_watermark, protect_pdf, fill_pdf_form, read_document…). Utilise TOUJOURS ces outils en priorité. N'écris JAMAIS de script Python (reportlab/pypdf) pour créer ou manipuler un PDF tant qu'un outil natif couvre le besoin. Le code Python n'est qu'un dernier recours documenté plus bas."
keywords: [pdf, fichier pdf, extraire pdf, fusionner pdf, creer pdf, formulaire pdf, ocr, watermark, filigrane, splitter pdf, merger pdf, lire pdf, convertir pdf, document pdf]
license: Lumena - usage interne
---

# PDF — Utilise les outils natifs Lumena, pas du code

⛔ **NE CODE PAS de script Python (reportlab, pypdf, pdfplumber) pour produire ou
manipuler un PDF.** Lumena a des outils natifs dédiés qui font ça en un seul appel,
sans écrire ni exécuter de fichier `.py`. Le code Python ci-dessous n'est qu'une
**référence de dernier recours** si — et seulement si — aucun outil natif ne couvre
le besoin précis.

## Table de routage : tâche → outil natif Lumena

| Tu veux… | Utilise l'outil | Au lieu de coder |
|---|---|---|
| **Créer un PDF** (rapport, lettre, note…) | `create_pdf` (markdown inline : titres, gras, listes, tableaux) | ~~reportlab Canvas/Platypus~~ |
| Créer une **facture / devis** PDF | `create_invoice_pdf` | ~~reportlab~~ |
| Convertir un **HTML → PDF** | `html_to_pdf` | ~~weasyprint/wkhtmltopdf~~ |
| Créer depuis un **modèle** | `create_from_template` (+ `list_templates`) | ~~jinja+reportlab~~ |
| **Fusionner** plusieurs PDF | `merge_pdfs` | ~~pypdf PdfWriter~~ |
| **Découper / séparer** un PDF | `split_pdf` | ~~pypdf~~ |
| Ajouter un **filigrane / watermark** | `add_watermark` | ~~pypdf merge_page~~ |
| **Protéger / chiffrer** (mot de passe) | `protect_pdf` | ~~pypdf encrypt~~ |
| **Signer** un document | `sign_document` | — |
| **Annoter** un PDF | `annotate_pdf` | — |
| Lister les **champs de formulaire** | `list_pdf_fields` | ~~pypdf~~ |
| **Remplir un formulaire** PDF | `fill_pdf_form` | ~~pypdf/pdf-lib~~ |
| **Lire / extraire texte & tableaux** | `read_document` puis `analyze_document` / `document_summary` | ~~pdfplumber~~ |
| **OCR** d'un PDF scanné | `read_document` (OCR fallback intégré) | ~~pytesseract+pdf2image~~ |
| **Convertir** un PDF vers un autre format | `convert_document` | — |
| **Comparer** deux documents | `compare_documents` | — |

## Règle d'usage

1. Identifie la tâche dans la table ci-dessus et appelle l'outil natif correspondant.
2. `create_pdf` accepte du **markdown** (titres `#`, **gras**, listes, tableaux `|…|`) —
   inutile de coder une mise en page : passe directement le contenu formaté.
3. Ne passe au code Python QUE si la tâche demandée n'apparaît dans aucune ligne de
   la table (cas très rare). Dans ce cas seulement, consulte `reference.md` /
   `forms.md` et les scripts du dossier `scripts/`.
4. Pour remplir des formulaires complexes, lis d'abord `forms.md`.

## Référence code Python (DERNIER RECOURS uniquement)

> N'utilise cette section que si aucun outil natif ne couvre le besoin. Dans tous les
> autres cas, la table de routage ci-dessus est la bonne réponse.

Voir `reference.md` pour les exemples détaillés pypdf / pdfplumber / reportlab /
poppler / qpdf, et `forms.md` pour le remplissage de formulaires.
