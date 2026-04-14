#!/usr/bin/env python3
"""
Website Generator — Génère un site web complet depuis un template + description.

Usage:
    python scripts/generate_site.py <project-name> --type <site-type> [--fullstack] [--output <dir>]

Examples:
    python scripts/generate_site.py mon-restaurant --type restaurant
    python scripts/generate_site.py saas-fitness --type saas --fullstack
    python scripts/generate_site.py portfolio-design --type portfolio --output ./mes-sites

Ce script:
1. Copie le template approprié (spa-frontend ou fullstack)
2. Personnalise les fichiers avec le nom du projet
3. Crée la structure de dossiers complète
4. Affiche un résumé avec les prochaines étapes
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


SKILL_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = SKILL_DIR / "templates"

SITE_TYPES = {
    "vitrine": {
        "title": "Site Vitrine",
        "pages": ["accueil", "services", "realisations", "equipe", "contact"],
        "description": "Site vitrine professionnel avec services, réalisations et contact",
    },
    "saas": {
        "title": "Application SaaS",
        "pages": ["accueil", "features", "pricing", "login", "register", "admin", "admin-users", "admin-settings", "contact"],
        "description": "Application SaaS avec tarifs, authentification et dashboard admin",
        "fullstack_recommended": True,
    },
    "ecommerce": {
        "title": "Site E-commerce",
        "pages": ["accueil", "catalogue", "produit", "panier", "checkout", "login", "admin", "contact"],
        "description": "Boutique en ligne avec catalogue, panier et dashboard admin",
        "fullstack_recommended": True,
    },
    "portfolio": {
        "title": "Portfolio Créatif",
        "pages": ["accueil", "projets", "about", "competences", "contact"],
        "description": "Portfolio avec projets, compétences et formulaire de contact",
    },
    "restaurant": {
        "title": "Site Restaurant",
        "pages": ["accueil", "menu", "reservation", "galerie", "about", "contact"],
        "description": "Site restaurant avec menu, réservation et galerie photos",
    },
    "blog": {
        "title": "Blog",
        "pages": ["accueil", "articles", "article", "categories", "about", "contact", "admin"],
        "description": "Blog avec articles, catégories et interface d'administration",
        "fullstack_recommended": True,
    },
    "dashboard": {
        "title": "Dashboard Analytique",
        "pages": ["login", "dashboard", "users", "analytics", "settings", "logs"],
        "description": "Dashboard avec KPIs, graphiques, gestion utilisateurs et logs",
        "fullstack_recommended": True,
    },
    "landing": {
        "title": "Landing Page",
        "pages": ["accueil"],
        "description": "Landing page longue avec hero, features, social proof et CTA",
    },
}


def generate_nav_html(pages: list, project_title: str) -> str:
    """Génère le HTML de navigation pour les pages données."""
    links = []
    for page in pages:
        label = page.replace("-", " ").replace("_", " ").title()
        if page.startswith("admin"):
            continue  # Admin n'apparaît pas dans la nav publique
        links.append(f'        <a href="#" data-page="page-{page}" onclick="navigateTo(\'page-{page}\'); return false;">{label}</a>')
    
    return f"""  <header id="main-header">
    <div class="container header-inner">
      <a href="#" class="logo" onclick="navigateTo('page-accueil'); return false;">{project_title}</a>
      <nav class="main-nav">
{chr(10).join(links)}
      </nav>
      <button class="hamburger" onclick="toggleMobileMenu()" aria-label="Menu">
        <i class="fas fa-bars"></i>
      </button>
    </div>
  </header>"""


def generate_sections_html(pages: list) -> str:
    """Génère les sections SPA vides pour chaque page."""
    sections = []
    for i, page in enumerate(pages):
        active = ' class="active"' if i == 0 else ""
        display = "" if i == 0 else ' style="display:none;"'
        label = page.replace("-", " ").replace("_", " ").title()
        sections.append(f"""  <section id="page-{page}"{active}{display}>
    <div class="container">
      <h2 class="section-title fade-in">{label}</h2>
      <!-- TODO: Contenu de la page {label} -->
    </div>
  </section>""")
    
    return "\n\n".join(sections)


def create_project(project_name: str, site_type: str, fullstack: bool, output_dir: Path):
    """Crée un nouveau projet web à partir du template."""
    
    type_info = SITE_TYPES.get(site_type, SITE_TYPES["vitrine"])
    project_title = project_name.replace("-", " ").replace("_", " ").title()
    project_dir = output_dir / project_name
    
    if project_dir.exists():
        print(f"⚠️  Le dossier '{project_dir}' existe déjà.")
        response = input("Écraser ? (o/N) : ").strip().lower()
        if response != "o":
            print("Annulé.")
            return
        shutil.rmtree(project_dir)
    
    # Choisir le template
    template_name = "fullstack" if fullstack else "spa-frontend"
    template_dir = TEMPLATES_DIR / template_name
    
    if template_dir.exists():
        shutil.copytree(template_dir, project_dir)
        print(f"📦 Template '{template_name}' copié")
    else:
        project_dir.mkdir(parents=True)
        print(f"📁 Dossier créé (pas de template trouvé)")
    
    # Créer les sous-dossiers nécessaires
    if fullstack:
        for d in ["public", "public/css", "public/js", "public/assets", "api", "api/endpoints", "config", "database"]:
            (project_dir / d).mkdir(parents=True, exist_ok=True)
    else:
        for d in ["css", "js", "assets"]:
            (project_dir / d).mkdir(parents=True, exist_ok=True)
    
    # Personnaliser le HTML principal
    html_dir = project_dir / "public" if fullstack else project_dir
    index_path = html_dir / "index.html"
    
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        content = content.replace("{{PROJECT_TITLE}}", project_title)
        content = content.replace("{{PROJECT_NAME}}", project_name)
        content = content.replace("{{YEAR}}", "2026")
        index_path.write_text(content, encoding="utf-8")
    
    # Générer un README
    pages_list = "\n".join(f"  - page-{p}" for p in type_info["pages"])
    readme = f"""# {project_title}

> {type_info['description']}

## Informations
- **Type** : {type_info['title']}
- **Architecture** : {'Fullstack (Frontend + Backend + API)' if fullstack else 'Frontend SPA'}
- **Généré le** : {datetime.now().strftime('%d/%m/%Y à %H:%M')}
- **Générateur** : Lumena Website Generator v2.0

## Pages
{pages_list}

## Lancement

### Frontend
```bash
cd {'public' if fullstack else '.'}
python -m http.server 8080
# Ouvrir http://localhost:8080
```
"""
    if fullstack:
        readme += """
### Backend
```bash
# PHP
php -S localhost:8001 -t api/

# Ou Python
cd api && python app.py
```

### Base de données
```bash
mysql -u root < database/schema.sql
```
"""
    readme += f"""
## Structure
```
{project_name}/
{'├── public/' if fullstack else ''}
{'│   ' if fullstack else ''}├── index.html
{'│   ' if fullstack else ''}├── css/styles.css
{'│   ' if fullstack else ''}└── js/app.js
{'├── api/' if fullstack else ''}
{'├── config/' if fullstack else ''}
{'├── database/' if fullstack else ''}
└── README.md
```

---
*Généré par Lumena 🌟*
"""
    (project_dir / "README.md").write_text(readme, encoding="utf-8")
    
    # Résumé
    file_count = sum(1 for _ in project_dir.rglob("*") if _.is_file())
    print(f"""
✅ Projet "{project_name}" créé avec succès !

📂 Dossier : {project_dir}
📊 Type : {type_info['title']}
🏗️  Architecture : {'Fullstack' if fullstack else 'Frontend SPA'}
📄 Pages : {', '.join(type_info['pages'])}
📁 {file_count} fichiers

💡 Prochaines étapes :
  1. Demande à Lumena de générer le contenu : "génère le site {project_name}"
  2. Preview : python -m http.server 8080
  3. Export : python scripts/export_zip.py {project_name}
""")


def main():
    parser = argparse.ArgumentParser(
        description="Génère un nouveau projet de site web",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Types disponibles : {', '.join(SITE_TYPES.keys())}"
    )
    parser.add_argument("project_name", help="Nom du projet (ex: mon-restaurant)")
    parser.add_argument("--type", "-t", default="vitrine", choices=SITE_TYPES.keys(),
                        help="Type de site (défaut: vitrine)")
    parser.add_argument("--fullstack", "-f", action="store_true",
                        help="Inclure backend + API + BDD")
    parser.add_argument("--output", "-o", default="",
                        help="Dossier de sortie (défaut: workspace/)")
    
    args = parser.parse_args()
    
    if args.output:
        output_dir = Path(args.output)
    else:
        lumena_root = Path(__file__).parent.parent.parent
        output_dir = lumena_root / "workspace"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Auto-fullstack pour certains types
    type_info = SITE_TYPES.get(args.type, {})
    if type_info.get("fullstack_recommended") and not args.fullstack:
        print(f"💡 Le type '{args.type}' est recommandé en fullstack. Ajoutez --fullstack pour inclure le backend.")
    
    create_project(args.project_name, args.type, args.fullstack, output_dir)


if __name__ == "__main__":
    main()
