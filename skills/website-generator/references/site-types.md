# Types de sites — Référence Lumena

> Ce document est chargé quand Lumena doit recommander la bonne architecture pour un projet. Il contient les spécifications par type de site : pages recommandées, fonctionnalités, stack technique, et structure de fichiers.

---

## 1. Site Vitrine (vitrine)

**Pour qui :** Entreprises, indépendants, commerces locaux.

### Pages recommandées
| Page           | Contenu                                      |
|----------------|----------------------------------------------|
| Accueil        | Hero, services/avantages, témoignages, CTA   |
| À propos       | Histoire, équipe, valeurs                    |
| Services       | Cartes détaillées par service                |
| Contact        | Formulaire + coordonnées + carte Google Maps |

### Fonctionnalités
- Formulaire de contact (email ou webhook)
- Animations fade-in au scroll
- Section témoignages avec carousel simple
- CTA omniprésent (bouton de contact fixe ou dans le footer)

### Stack
- **Template** : `spa-frontend`
- **Backend** : Aucun (formulaire via webhook ou email PHP simple)
- **Pages estimées** : 4-6

### Structure
```
project/
├── index.html
├── css/styles.css
├── js/app.js
└── assets/
    └── images/
```

---

## 2. SaaS / Application Web (saas)

**Pour qui :** Startups, entreprise proposant un service en ligne.

### Pages recommandées
| Page            | Contenu                                            |
|-----------------|----------------------------------------------------|
| Landing         | Hero avec démo/vidéo, features, pricing, FAQ, CTA  |
| Pricing         | Tableaux comparatifs 3 plans (Free/Pro/Enterprise)  |
| Login           | Formulaire auth avec « forgot password »            |
| Dashboard       | KPIs, graphiques, activité récente                  |
| Settings        | Profil, notifications, billing                      |

### Fonctionnalités
- Authentification JWT avec rôles (user/admin)
- Dashboard avec KPI cards
- Gestion du profil utilisateur
- Système de notifications (toast)
- Responsive admin layout avec sidebar

### Stack
- **Template** : `fullstack`
- **Backend** : PHP REST API + MySQL
- **Pages estimées** : 8-12

### Structure
```
project/
├── public/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
├── api/
│   ├── index.php
│   ├── config.php
│   └── controllers/
├── database/
│   └── schema.sql
└── uploads/
```

---

## 3. E-commerce (ecommerce)

**Pour qui :** Boutiques en ligne, artisans, dropshipping.

### Pages recommandées
| Page            | Contenu                                      |
|-----------------|----------------------------------------------|
| Accueil         | Hero promo, produits vedettes, catégories     |
| Catalogue       | Grille produits avec filtres/tri             |
| Fiche produit   | Galerie, description, prix, ajout panier     |
| Panier          | Récapitulatif, quantités, sous-total         |
| Checkout        | Étapes : adresse → paiement → confirmation   |
| Compte client   | Commandes, adresses, profil                  |
| Admin produits  | CRUD produits, stock, catégories             |

### Fonctionnalités
- Panier persistant (localStorage)
- Filtres dynamiques (prix, catégorie, tri)
- Galerie d'images produit (lightbox)
- Calcul automatique des sous-totaux
- Gestion de stock (admin)
- Intégration paiement (Stripe Checkout recommandé)

### Stack
- **Template** : `fullstack`
- **Backend** : PHP REST API + MySQL
- **Pages estimées** : 10-20
- **Tables DB** : products, categories, orders, order_items, users, addresses

### Tables clés
```sql
CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE,
    description TEXT,
    price DECIMAL(10,2) NOT NULL,
    stock INT DEFAULT 0,
    category_id INT,
    image_url VARCHAR(500),
    status ENUM('active','draft','archived') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    status ENUM('pending','paid','shipped','delivered','cancelled') DEFAULT 'pending',
    total DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Portfolio (portfolio)

**Pour qui :** Créatifs, développeurs, designers, photographes.

### Pages recommandées
| Page        | Contenu                                    |
|-------------|---------------------------------------------|
| Accueil     | Hero avec titre fort, aperçu projets        |
| Projets     | Grille filtrable par catégorie              |
| Projet (détail) | Galerie, description, technologies, lien |
| À propos    | Bio, compétences, parcours, CV download     |
| Contact     | Formulaire + liens sociaux                  |

### Fonctionnalités
- Grille de projets avec filtrage par tag (JS)
- Lightbox pour les images
- Animations d'entrée élégantes
- Bouton « télécharger mon CV »
- Liens vers GitHub/LinkedIn/Dribbble

### Stack
- **Template** : `spa-frontend`
- **Backend** : Aucun
- **Pages estimées** : 4-5

---

## 5. Restaurant / Établissement (restaurant)

**Pour qui :** Restaurants, cafés, bars, hôtels.

### Pages recommandées
| Page        | Contenu                                       |
|-------------|------------------------------------------------|
| Accueil     | Hero avec photo ambiance, horaires, réservation |
| Menu        | Catégories (entrées/plats/desserts), prix       |
| Réservation | Formulaire date/heure/personnes                |
| Galerie     | Photos du lieu et des plats                    |
| Contact     | Adresse, carte, téléphone, réseaux sociaux     |

### Fonctionnalités
- Menu avec catégories et prix
- Formulaire de réservation (date picker)
- Galerie photo avec lightbox
- Google Maps embed
- Horaires d'ouverture avec jours/heures
- Lien direct vers UberEats/Deliveroo si applicable

### Stack
- **Template** : `spa-frontend`
- **Backend** : Optionnel (réservations par email ou webhook)
- **Pages estimées** : 4-6

### Section menu (HTML pattern)
```html
<div class="menu-category">
  <h3>🥗 Entrées</h3>
  <div class="menu-item">
    <div class="menu-item-header">
      <span class="menu-item-name">Salade César</span>
      <span class="menu-item-price">12,50 €</span>
    </div>
    <p class="menu-item-desc">Romaine, parmesan, croûtons, sauce César maison</p>
  </div>
</div>
```

---

## 6. Blog (blog)

**Pour qui :** Créateurs de contenu, entreprises (content marketing).

### Pages recommandées
| Page          | Contenu                                 |
|---------------|-----------------------------------------|
| Accueil       | Derniers articles, catégories, newsletter |
| Liste articles | Grille avec pagination, filtres        |
| Article       | Contenu, auteur, date, articles liés    |
| Catégorie     | Articles filtrés par catégorie          |
| À propos      | Bio de l'auteur/équipe                  |

### Fonctionnalités
- Articles avec métadonnées (auteur, date, catégorie, temps de lecture)
- Pagination ou infinite scroll
- Catégories et tags
- Table des matières auto-générée
- Formulaire newsletter
- Partage social (liens directs, pas de SDK)

### Stack
- **Template** : `fullstack` (si dynamique) ou `spa-frontend` (si statique)
- **Backend** : PHP + MySQL pour articles dynamiques
- **Pages estimées** : 5+ (sans compter les articles)

### Table articles
```sql
CREATE TABLE articles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE,
    content LONGTEXT,
    excerpt TEXT,
    author_id INT,
    category_id INT,
    status ENUM('draft','published','archived') DEFAULT 'draft',
    featured_image VARCHAR(500),
    read_time INT DEFAULT 5,
    published_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 7. Dashboard / Admin (dashboard)

**Pour qui :** Outils internes, backoffice, monitoring.

### Pages recommandées
| Page        | Contenu                                      |
|-------------|----------------------------------------------|
| Dashboard   | KPIs, graphiques, activité récente           |
| Utilisateurs| Table CRUD avec recherche/filtres            |
| Paramètres  | Configuration générale, thème, sécurité      |
| Logs        | Journal d'activité paginé                    |
| Profil      | Modifier son profil, changer mot de passe    |

### Fonctionnalités
- Layout sidebar + main content
- KPI cards avec indicateurs de variation (+/-%)
- Tables de données avec recherche, tri, pagination
- Toggles et formulaires de configuration
- Rôles et permissions (admin/editor/viewer)
- Notifications toast
- Export CSV

### Stack
- **Template** : `fullstack`
- **Backend** : PHP REST API + MySQL
- **Pages estimées** : 6-10

---

## 8. Landing Page (landing)

**Pour qui :** Campagnes marketing, lancement produit, collecte emails.

### Sections recommandées (single-page)
| Section     | Contenu                                 |
|-------------|------------------------------------------|
| Hero        | Titre accrocheur, sous-titre, CTA unique |
| Problème    | Le pain point du client cible            |
| Solution    | Votre produit/service comme solution     |
| Features    | 3-4 avantages clés avec icônes           |
| Social proof| Témoignages, logos clients, stats         |
| CTA final   | Formulaire inscription ou bouton achat   |

### Fonctionnalités
- **Un seul CTA clair** (pas de menu complexe)
- Formulaire d'inscription email
- Compteur (early birds, places restantes)
- Micro-animations de scroll
- Responsive parfait (le trafic vient souvent de pubs mobile)

### Stack
- **Template** : `spa-frontend`
- **Backend** : Aucun (formulaire via webhook)
- **Pages estimées** : 1 (single-page)

---

## 9. Matrice de décision rapide

| Besoin           | Template      | Backend | DB  | Auth | Pages |
|------------------|---------------|---------|-----|------|-------|
| Présenter        | spa-frontend  | Non     | Non | Non  | 4-6   |
| Vendre           | fullstack     | Oui     | Oui | Oui  | 10-20 |
| Publier          | fullstack     | Oui     | Oui | Opt  | 5+    |
| Montrer (portfolio) | spa-frontend | Non  | Non | Non  | 4-5   |
| Gérer (admin)    | fullstack     | Oui     | Oui | Oui  | 6-10  |
| Convertir (landing) | spa-frontend | Non  | Non | Non  | 1     |
| Réserver         | spa-frontend  | Opt     | Opt | Non  | 4-6   |
| Dashboard SaaS   | fullstack     | Oui     | Oui | Oui  | 8-12  |

---

## 10. Recommandations transversales

1. **Toujours commencer par le SPA** — même les fullstack ont un frontend SPA
2. **Mobile-first** — plus de 60% du trafic est mobile
3. **Accessibilité** — nav au clavier, contraste, aria-labels
4. **Performance** — pas de jQuery, pas de frameworks lourds, vanilla JS
5. **SEO** — meta title/description, Open Graph, sitemap.xml pour les sites publics
6. **Sécurité** — HTTPS, CSP headers, SQL paramétré, XSS prevention
