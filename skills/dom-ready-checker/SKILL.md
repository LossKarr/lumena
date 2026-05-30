---
name: dom-ready-checker
description: "Guide de debug des erreurs de timing DOM en JavaScript (getElementById/querySelector qui renvoie null, script exécuté avant chargement, 'Cannot read properties of null'). Corriger via DOMContentLoaded / placement du script ; vérifier avec les outils browser_* natifs."
keywords: [dom, getElementById, querySelector, null, timing, DOMContentLoaded, chargement dom, script timing, cannot read null, dom error, dom not ready, js timing]
---

# Debug timing DOM (JavaScript)

Guide court — cause fréquente : le script s'exécute **avant** que le DOM existe.

## Symptômes
- `getElementById` / `querySelector` renvoie `null`.
- `Cannot read properties of null (reading '...')`.

## Corrections
- Envelopper dans `document.addEventListener('DOMContentLoaded', () => { ... })`.
- **Ou** placer le `<script>` en fin de `<body>`, ou ajouter `defer` sur la balise.
- Vérifier que l'élément ciblé existe bien (id/sélecteur corrects).

## Vérification
- Reproduire/valider avec les outils natifs : `browser_navigate` → `browser_dialog_log` (erreurs JS) → `browser_screenshot`.
- Pour corriger le fichier source : édition ciblée ou CodeAgent (selon l'ampleur).
