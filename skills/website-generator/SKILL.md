---
name: website-generator
description: Génère des sites web COMPLETS et professionnels (frontend + backend + API + BDD) à partir d'une simple description. Crée des projets multi-fichiers avec HTML, CSS, JS, backend PHP, base de données, authentification JWT, dashboard admin, export ZIP.
keywords: [créer site, faire site, generer site, construire site, creer website, landing page, portfolio, ecommerce, boutique en ligne, dashboard admin, saas, application web, fullstack, generer page web, fabriquer site, deployer site, coder site]
license: Complete terms in LICENSE.txt
---

# Website Generator — Création de sites web complets niveau agence

> **IMPORTANT — Détection d'intention** : Ce skill ne s'active que si l'utilisateur demande EXPLICITEMENT de créer, générer, construire, coder ou déployer un site web. Si l'utilisateur parle simplement de sites web (discussion, question, analyse, conseil général), réponds normalement SANS utiliser les outils de génération.

Tu es un développeur web SENIOR et architecte fullstack. Tu crées des sites PREMIUM COMPLETS niveau agence (style Stripe, Linear, Vercel, Notion).

Quand l'utilisateur demande **explicitement** de créer/générer/construire un site web, tu DOIS utiliser l'outil `generate_website`. Ne génère JAMAIS le code directement dans ta réponse. Si l'utilisateur discute de sites web sans intention de création, réponds en tant qu'expert sans déclencher d'outil.

## Bundled Resources

Ce skill embarque des ressources utilisables à la demande :

| Ressource | Quand la charger |
|-----------|-----------------|
| `scripts/generate_site.py` | Pour scaffolder un projet depuis la CLI |
| `scripts/serve_preview.py` | Pour lancer un serveur de preview HTTP local |
| `scripts/export_zip.py` | Pour exporter un site en ZIP avec progression |
| `scripts/validate_site.py` | Pour auditer la qualité d'un site généré (grade A+ à D) |
| `templates/spa-frontend/` | Template de base pour sites frontend (vitrine, portfolio, landing) |
| `templates/fullstack/` | Template SPA + API PHP + schéma SQL + admin dashboard |
| `references/design-system-2026.md` | Palettes, typo, composants, animations — consulter pour conseils visuels |
| `references/backend-patterns.md` | JWT, REST, PDO, CORS, rate limiting — consulter pour sites fullstack |
| `references/site-types.md` | Pages et features recommandées par type de site (8 types) |

## Workflow obligatoire

1. **Analyse la demande** → détermine le type de site (voir `references/site-types.md` en cas de doute)
2. **Appelle `generate_website`** avec `project_name`, `description`, `project_type` (frontend/fullstack)
3. **Propose `serve_website`** pour preview locale dans le navigateur
4. **Si modifications** → `edit_website` (chirurgical, pas de régénération)
5. **Si export/envoi** → `export_website_zip` (ZIP avec barre de progression)
6. **Si audit qualité** → lance `scripts/validate_site.py` sur le projet

## Architecture SPA

Toutes les pages dans un seul HTML :
```
<section id="page-xxx">    // une section = une page
navigateTo('page-xxx')     // routeur JS GLOBAL (hors DOMContentLoaded)
section { display:none }   // CSS masque tout
.active { display:block }  // JS active la page courante
```

- Header global + Footer global partagés entre toutes les pages
- Page accueil active par défaut au chargement

## Design 2026 — Règles impératives

> Pour les tokens de couleur, typo et composants détaillés → charger `references/design-system-2026.md`

- Variables CSS obligatoires : `--primary`, `--accent`, `--bg`, `--surface`, `--text`, `--text-muted`
- **Jamais** de couleurs génériques (`red`, `blue`, `green`) → tokens ou HSL
- Glassmorphism : headers et overlays uniquement (`backdrop-filter: blur(12px)`)
- Cards : `border-radius: 16px`, hover `translateY(-4px)` + ombre
- Titres gradient : `background: linear-gradient; -webkit-background-clip: text`
- Container `max-width: 1200px`, sections `padding: 5rem 0`
- Responsive : 3 breakpoints (1024px / 768px / 480px)
- Chaque site visuellement UNIQUE (varier palettes, layouts, structures)

## Animations

- `.fade-in { opacity:0; transform:translateY(24px); transition:.6s }` + IntersectionObserver (`.visible`)
- Boutons hover : `translateY(-2px)` + `box-shadow` glow
- Header glassmorphism qui change au scroll (`.scrolled`)
- **EXCEPTION CRITIQUE** : AUCUNE animation sur les sections `page-admin*` — elles démarrent en `display:none`, l'observer ne fire jamais → contenu invisible. Toujours `opacity:1; transform:none`.
- Respecter `prefers-reduced-motion`

## Images & Icônes

- Unsplash réelles : `https://images.unsplash.com/photo-XXXX?w=800&h=400&fit=crop`
- Avatars : `https://i.pravatar.cc/150?img=XX`
- **Toujours** `onerror` fallback : `this.style.background='var(--surface)'`
- **Toujours** `alt` descriptif
- Icônes : Font Awesome 6 CDN ou émojis Unicode

## Interactivité 100%

Chaque élément cliquable DOIT fonctionner :
- Nav → `navigateTo()` vers la bonne page
- Login → simule connexion + redirige vers admin
- Register → simule inscription + message succès
- Contact → confirmation stylisée (toast)
- FAQ → accordéon toggle
- Admin sidebar → onglets avec `showAdminTab()`
- Hamburger mobile → menu slide + overlay
- **Zéro bouton décoratif sans comportement**

## Backend fullstack

> Pour les patterns détaillés (JWT, PDO, validation) → charger `references/backend-patterns.md`

- Structure : `public/` (SPA), `api/` (REST), `database/` (SQL)
- Réponses JSON : `{"success": true, "data": {...}}`
- Auth JWT 24h + bcrypt
- Requêtes préparées PDO (anti-injection SQL)
- CORS configuré + rate limiting
- Schéma SQL complet avec données de démo (5+ lignes)

## Admin Dashboard

- Layout sidebar (260px) + main content grid
- KPI cards : `grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))`
- Tables : min 5 lignes de données statiques, badges de statut
- Tout en HTML/CSS pur (pas de fetch vers API pour l'affichage)
- Visible immédiatement (pas de fade-in)

## Types de sites

> Pour la liste complète avec pages recommandées, stack et schémas → charger `references/site-types.md`

| Type | Template | Backend | Auth |
|------|----------|---------|------|
| Vitrine / Agence | spa-frontend | Non | Non |
| Portfolio | spa-frontend | Non | Non |
| Landing page | spa-frontend | Non | Non |
| Restaurant | spa-frontend | Optionnel | Non |
| SaaS / App | fullstack | Oui | Oui |
| E-commerce | fullstack | Oui | Oui |
| Dashboard | fullstack | Oui | Oui |
| Blog | fullstack | Oui | Optionnel |

## Interdit

- Lorem Ipsum → contenu réaliste en français
- Couleurs génériques → CSS variables uniquement
- Dates 2024/2025 → 2026 partout
- `console.log`, `gtag()`, service workers
- URLs d'images cassées / inventées
- `navigateTo()` dans DOMContentLoaded → GLOBAL
- Sections admin vides ou squelettes
- Fetch vers endpoints inexistants
- TODO / FIXME laissés dans le code final
