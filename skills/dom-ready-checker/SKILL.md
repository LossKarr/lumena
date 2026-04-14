---
name: dom-ready-checker
description: Skill dom-ready-checker
---

# DOM Ready Checker

Un skill pour détecter et résoudre les erreurs courantes de timing DOM en JavaScript, notamment lorsque `getElementById()` ou `querySelector()` retourne `null` parce que le script s'exécute avant que le DOM ne soit complètement chargé.

## Quand l'utiliser

1. **Quand un projet web ne fonctionne pas au chargement** : les boutons ne répondent pas, les canvas ne s'affichent pas, et la console montre `Uncaught TypeError: Cannot read properties of null` ou `getElementById(...) is null`.
2. **Quand tu modifies un fichier HTML existant** et que tu ajoutes de nouveaux éléments avec des IDs, et que tu veux t'assurer que le JavaScript les trouvera correctement.

## Instructions

### Étape 1 : Vérifier l'erreur console
- Ouvre la page dans le navigateur (Chrome/Edge)
- Ouvre les DevTools (F12) → onglet Console
- Cherche les erreurs mentionnant `null`, `getElementById`, `querySelector`, ou `getContext`

### Étape 2 : Analyser l'ordre de chargement
- Ouvre le fichier HTML et vérifie où est placé la balise `<script>`
- **Problème courant** : le `<script>` est dans `<head>` ou en haut de `<body>` avant les éléments qu'il cible

### Étape 3 : Appliquer la correction
**Solution A (recommandée) :** Déplacer le `<script>` à la fin du `<body>`
```html
<!-- AVANT (dans <head>) -->
<head>
  <script src="game.js"></script>
</head>

<!-- APRÈS (fin de <body>) -->
<body>
  <!-- tout le contenu HTML -->
  <canvas id="gameCanvas"></canvas>
  
  <script src="game.js"></script>
</body>
```

**Solution B :** Utiliser `DOMContentLoaded`
```javascript
// Dans game.js
document.addEventListener('DOMContentLoaded', function() {
  // Tout ton code qui accède au DOM ici
  const canvas = document.getElementById('gameCanvas');
  const ctx = canvas.getContext('2d');
  // ...
});
```

**Solution C :** Vérifier l'existence de l'élément avant utilisation
```javascript
const canvas = document.getElementById('gameCanvas');
if (!canvas) {
  console.error('Canvas non trouvé ! Vérifie que l\'élément existe et que le script est bien placé.');
  return;
}
const ctx = canvas.getContext('2d');
```

### Étape 4 : Tester la correction
- Recharge la page (Ctrl+F5)
- Vérifie que l'erreur a disparu de la console
- Teste la fonctionnalité (boutons, canvas, etc.)

## Exemples

### Input (erreur typique)
**Console :** `game.js:1 Uncaught TypeError: Cannot read properties of null (reading 'getContext')`
**HTML :**
```html
<head>
  <script src="game.js"></script>
</head>
<body>
  <canvas id="gameCanvas"></canvas>
</body>
```
**game.js :**
```javascript
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d'); // ERREUR ICI
```

### Output (après correction)
**HTML corrigé :**
```html
<head>
  <!-- plus de script ici -->
</head>
<body>
  <canvas id="gameCanvas"></canvas>
  
  <script src="game.js"></script>
</body>
```
**Résultat :** Plus d'erreur dans la console, le jeu fonctionne correctement.

### Cas réel (du souvenir 2026-04-04)
**Problème :** `game.js` cherchait un `<canvas>` avec `getElementById()` mais l'élément était `null` car le script s'exécutait trop tôt.
**Solution appliquée :** Déplacer la balise `<script>` à la fin du `<body>` et ajouter une vérification `if (!canvas) return;`
**Résultat :** Le jeu Snake 3D a fonctionné immédiatement après.

---
**Tags :** javascript, dom, timing, error-handling, frontend, debug
**Créé le :** 2026-04-06
**Basé sur :** Erreurs récurrentes identifiées dans memory_search (souvenirs 2026-04-04)
