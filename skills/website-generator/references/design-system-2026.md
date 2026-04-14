# Design System 2026 — Référence Lumena

> Ce document est chargé par Lumena quand l'utilisateur demande des conseils visuels, un redesign, ou un ajustement esthétique. Il définit les standards de design pour tous les sites générés.

---

## 1. Philosophie

Le design 2026 est **sombre, minimal et immersif**. Chaque interface doit sembler « flottante » au-dessus d'un fond profond. L'objectif : **zéro surcharge visuelle**, chaque pixel a une raison d'être.

Principes fondamentaux :
- **Dark-first** : fond sombre, texte clair, halos colorés subtils
- **Glassmorphism maîtrisé** : backdrop-filter sur les surfaces de navigation et les overlays
- **Motion significative** : animations uniquement pour guider l'attention
- **Typographie comme UI** : hiérarchie forte via taille/grais­se, pas via couleur

---

## 2. Palette de couleurs

### 2.1 Tokens principaux

| Token           | Valeur    | Usage                              |
|-----------------|-----------|-------------------------------------|
| `--primary`     | `#6C5CE7` | Actions principales, CTA, liens     |
| `--primary-light` | `#a29bfe` | Hover, textes actifs              |
| `--primary-dark` | `#4a3db8` | Gradients, ombres de boutons      |
| `--accent`      | `#00cec9` | Highlights, badges, éléments vedettes |
| `--bg`          | `#141422` | Fond de page                       |
| `--surface`     | `#1e1e2e` | Cartes, sidebars, sections         |
| `--surface-card` | `#252538` | Cartes élevées, popups            |
| `--text`        | `#f0f0f5` | Texte principal                    |
| `--text-muted`  | `#a0a0b8` | Labels, sous-titres, placeholders  |

### 2.2 Tokens fonctionnels

| Token        | Valeur    | Usage                  |
|--------------|-----------|------------------------|
| `--success`  | `#00b894` | Validation, succès     |
| `--warning`  | `#fdcb6e` | Avertissements         |
| `--danger`   | `#e17055` | Erreurs, suppression   |

### 2.3 Règles

- **Jamais de blanc pur** (`#fff`) en arrière-plan
- **Jamais de noir pur** (`#000`) en texte
- Les couleurs génériques (`red`, `blue`, `green`) sont interdites — utiliser les tokens
- Les gradients doivent aller de `--primary` vers `--accent` ou `--primary-dark`
- Le rapport de contraste texte/fond doit être ≥ 4.5:1 (WCAG AA)

---

## 3. Typographie

### 3.1 Polices

| Catégorie  | Police                           | Fallback                 |
|------------|----------------------------------|--------------------------|
| Interface  | Inter                            | system-ui, sans-serif    |
| Code       | JetBrains Mono                   | Fira Code, monospace     |

CDN recommandé :
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
```

### 3.2 Échelle typographique

| Élément | Taille           | Graisse | Line-height |
|---------|------------------|---------|-------------|
| h1      | clamp(2.5rem, 5vw, 4rem) | 800 | 1.15    |
| h2      | 2rem             | 700     | 1.3         |
| h3      | 1.2rem           | 600     | 1.4         |
| body    | 1rem (16px)      | 400     | 1.6         |
| small   | 0.85rem          | 400     | 1.5         |
| label   | 0.9rem           | 500     | 1.4         |

### 3.3 Règles

- Maximum **2 polices** par projet
- Toujours `font-display: swap` pour les webfonts
- `clamp()` obligatoire pour les titres principaux (responsive fluide)

---

## 4. Espacement & Layout

### 4.1 Grille

- Container max : `1200px`
- Padding horizontal : `2rem` (desktop), `1rem` (mobile)
- Grid principal : `repeat(auto-fill, minmax(300px, 1fr))` pour les cartes
- Gap standard : `1.5rem`

### 4.2 Espacement vertical

| Contexte          | Valeur   |
|-------------------|----------|
| Entre sections    | `5rem`   |
| Titre → contenu   | `3rem`   |
| Entre éléments    | `1.5rem` |
| Padding carte     | `2rem`   |
| Padding bouton    | `0.75rem 1.5rem` |

### 4.3 Breakpoints

| Nom      | Valeur     | Changements clés                    |
|----------|------------|--------------------------------------|
| Desktop  | > 1024px   | Grille complète, sidebar visible     |
| Tablet   | ≤ 1024px   | Sidebar cachée, grille 2 colonnes    |
| Mobile   | ≤ 768px    | Hamburger, grille 1 colonne          |
| Small    | ≤ 480px    | Padding réduit, sections 3rem        |

---

## 5. Composants

### 5.1 Boutons

```css
.btn {
  padding: .75rem 1.5rem;
  border-radius: 8px;
  font-weight: 600;
  transition: all .3s cubic-bezier(.4,0,.2,1);
}
.btn-primary {
  background: linear-gradient(135deg, var(--primary), var(--primary-dark));
  box-shadow: 0 4px 16px rgba(108,92,231,.3);
}
.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(108,92,231,.45);
}
```

Variantes : `btn-primary`, `btn-secondary`, `btn-danger`, `btn-sm`, `btn-block`.

### 5.2 Cartes

- `background: var(--surface-card)`
- `border: 1px solid rgba(255,255,255,.08)`
- `border-radius: 16px`
- Hover : `translateY(-4px)` + ombre renforcée
- Pas de `box-shadow` au repos (sauf cards élevées)

### 5.3 Glassmorphism

Réservé aux éléments de navigation et overlays :
```css
.glass {
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.08);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
```

**Ne pas utiliser** le glassmorphism sur les cartes de contenu (trop gourmand en GPU si multiplié).

### 5.4 Formulaires

- Input : fond `var(--surface)`, bordure `var(--glass-border)`
- Focus : bordure `var(--primary)` + `box-shadow: 0 0 0 3px rgba(108,92,231,.15)`
- Labels au-dessus des champs, jamais en placeholder-only
- Messages d'erreur en `var(--danger)`, `font-size: 0.8rem`

### 5.5 Tableaux de données (admin)

- Header : fond `var(--surface)`, texte uppercase, letter-spacing `.04em`
- Lignes : bordure top `var(--glass-border)`, hover léger
- Badges inline pour les statuts (`.badge-success`, `.badge-warning`, `.badge-danger`)

---

## 6. Animations & Motion

### 6.1 Timing

| Durée      | Usage                           |
|------------|----------------------------------|
| 150ms      | Micro-interactions (hover, focus)|
| 300ms      | Transitions standard            |
| 600ms      | Entrées de section (fade-in)     |

Easing par défaut : `cubic-bezier(.4, 0, .2, 1)` (Material-style).

### 6.2 Fade-in au scroll

```css
.fade-in {
  opacity: 0;
  transform: translateY(24px);
  transition: opacity .6s ease, transform .6s ease;
}
.fade-in.visible {
  opacity: 1;
  transform: translateY(0);
}
```

Activation via IntersectionObserver avec `threshold: 0.15`.

### 6.3 Règles

- **Pas d'animation au chargement** de l'admin (les panneaux sont visibles immédiatement)
- **Pas de parallaxe** sauf demande explicite
- `prefers-reduced-motion` : désactiver toutes les transitions
- Ne jamais animer `width`, `height` ou `top/left` (utiliser `transform` uniquement)

---

## 7. Assets & Images

### 7.1 Images placeholder

Quand une image est nécessaire mais pas fournie :
```html
<img src="https://picsum.photos/800/400" alt="Description contextuelle"
     onerror="this.style.background='var(--surface)'; this.alt='Image indisponible'">
```

- Toujours un `alt` descriptif (jamais vide sauf images décoratives)
- Toujours un `onerror` fallback
- Format recommandé : WebP avec `<picture>` fallback

### 7.2 Icônes

Utiliser des émojis Unicode ou des SVG inline. Éviter les bibliothèques d'icônes lourdes (Font Awesome) sauf si déjà dans le projet.

---

## 8. Accessibilité (a11y)

- Contraste ≥ 4.5:1 pour le texte, ≥ 3:1 pour les éléments UI
- `focus-visible` sur tous les éléments interactifs
- `aria-label` sur les boutons icon-only
- Navigation au clavier complète (tab order logique)
- `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- `lang` attribut sur `<html>`

---

## 9. Checklist avant livraison

- [ ] Toutes les couleurs utilisent des CSS variables (pas de valeurs en dur)
- [ ] Aucune couleur générique (`red`, `blue`, etc.)
- [ ] Responsive testé à 480px, 768px, 1024px
- [ ] Tous les boutons ont un état hover avec transition
- [ ] IntersectionObserver pour les `.fade-in`
- [ ] `onerror` sur toutes les `<img>`
- [ ] Formulaires avec labels et messages d'erreur
- [ ] Pas de Lorem Ipsum dans la version finale
- [ ] Pas de TODO/FIXME restants
