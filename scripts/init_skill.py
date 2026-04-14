#!/usr/bin/env python3
"""
🎯 LUMENA - Skill Creator (init_skill.py)

Crée un nouveau skill avec la structure appropriée.

Usage:
    python scripts/init_skill.py <skill-name> [--path skills/] [--resources scripts,references,assets]

Exemple:
    python scripts/init_skill.py weather --path skills/
    python scripts/init_skill.py github-cli --path skills/ --resources scripts,references
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime


# Template SKILL.md de base
SKILL_TEMPLATE = '''---
name: {skill_name}
description: "{description}"
---

# {display_name}

{instructions}

## Usage

Décrivez ici comment utiliser ce skill.

## Exemples

```
Exemple d'utilisation...
```
'''

# Template SKILL.md avec script
SKILL_WITH_SCRIPT_TEMPLATE = '''---
name: {skill_name}
description: "{description}"
---

# {display_name}

{instructions}

## Usage

Ce skill utilise un script Python pour exécuter des actions.

### Script disponible

- `scripts/{skill_name}.py` - Script principal

### Exécution

```bash
python scripts/{skill_name}.py [arguments]
```

## Exemples

```
Exemple d'utilisation...
```
'''

# Template script Python
SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""
🎯 Script pour le skill: {skill_name}

Usage:
    python {skill_name}.py [arguments]
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="{display_name}")
    # Ajoutez vos arguments ici
    # parser.add_argument("--option", help="Description")
    
    args = parser.parse_args()
    
    # Votre logique ici
    print(f"Skill {skill_name} exécuté avec succès!")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# Template README pour references
REFERENCE_TEMPLATE = '''# Références pour {display_name}

## Documentation

Ajoutez ici la documentation de référence pour ce skill.

## Ressources

- [Lien 1](url)
- [Lien 2](url)

## Notes

Notes additionnelles...
'''


def normalize_skill_name(name: str) -> str:
    """Normalise le nom du skill (lowercase, hyphens)."""
    return name.lower().replace(" ", "-").replace("_", "-")


def to_display_name(skill_name: str) -> str:
    """Convertit le nom du skill en nom d'affichage."""
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def create_skill(
    skill_name: str,
    output_path: Path,
    resources: list = None,
    description: str = None
):
    """Crée la structure du skill."""
    
    skill_name = normalize_skill_name(skill_name)
    display_name = to_display_name(skill_name)
    skill_dir = output_path / skill_name
    
    # Vérifier si le skill existe déjà
    if skill_dir.exists():
        print(f"❌ Erreur: Le skill '{skill_name}' existe déjà dans {output_path}")
        return False
    
    # Créer le dossier principal
    skill_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Créé: {skill_dir}")
    
    # Déterminer le template à utiliser
    has_scripts = resources and "scripts" in resources
    
    # Créer SKILL.md
    template = SKILL_WITH_SCRIPT_TEMPLATE if has_scripts else SKILL_TEMPLATE
    skill_md_content = template.format(
        skill_name=skill_name,
        display_name=display_name,
        description=description or f"Skill pour {display_name}",
        instructions=f"Ce skill permet d'utiliser les fonctionnalités de {display_name}."
    )
    
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(skill_md_content, encoding="utf-8")
    print(f"📄 Créé: {skill_md_path}")
    
    # Créer les dossiers de ressources demandés
    if resources:
        for resource in resources:
            resource_dir = skill_dir / resource
            resource_dir.mkdir(exist_ok=True)
            print(f"📁 Créé: {resource_dir}")
            
            # Ajouter des fichiers templates selon le type
            if resource == "scripts":
                script_path = resource_dir / f"{skill_name}.py"
                script_content = SCRIPT_TEMPLATE.format(
                    skill_name=skill_name,
                    display_name=display_name
                )
                script_path.write_text(script_content, encoding="utf-8")
                print(f"🐍 Créé: {script_path}")
                
            elif resource == "references":
                ref_path = resource_dir / "README.md"
                ref_content = REFERENCE_TEMPLATE.format(display_name=display_name)
                ref_path.write_text(ref_content, encoding="utf-8")
                print(f"📖 Créé: {ref_path}")
                
            elif resource == "assets":
                # Créer un fichier .gitkeep pour garder le dossier
                gitkeep = resource_dir / ".gitkeep"
                gitkeep.touch()
    
    print(f"\n✅ Skill '{skill_name}' créé avec succès!")
    print(f"📍 Emplacement: {skill_dir.absolute()}")
    print(f"\n💡 Prochaines étapes:")
    print(f"   1. Éditez {skill_md_path} pour personnaliser le skill")
    if has_scripts:
        print(f"   2. Implémentez le script dans scripts/{skill_name}.py")
    print(f"   3. Testez le skill avec Lumena")
    print(f"   4. Packagez avec: python scripts/package_skill.py {skill_dir}")
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="🎯 LUMENA Skill Creator - Crée un nouveau skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python scripts/init_skill.py weather
  python scripts/init_skill.py github-cli --path skills/ --resources scripts,references
  python scripts/init_skill.py my-tool --resources scripts,assets --description "Mon outil personnalisé"
        """
    )
    
    parser.add_argument(
        "skill_name",
        help="Nom du skill (sera normalisé en lowercase-with-hyphens)"
    )
    
    parser.add_argument(
        "--path",
        default="skills",
        help="Chemin où créer le skill (défaut: skills/)"
    )
    
    parser.add_argument(
        "--resources",
        default="",
        help="Ressources à créer: scripts,references,assets (séparées par virgules)"
    )
    
    parser.add_argument(
        "--description",
        default="",
        help="Description courte du skill"
    )
    
    args = parser.parse_args()
    
    # Parser les ressources
    resources = [r.strip() for r in args.resources.split(",") if r.strip()]
    valid_resources = {"scripts", "references", "assets"}
    for r in resources:
        if r not in valid_resources:
            print(f"❌ Ressource invalide: '{r}'. Valides: {valid_resources}")
            return 1
    
    # Créer le skill
    output_path = Path(args.path)
    success = create_skill(
        skill_name=args.skill_name,
        output_path=output_path,
        resources=resources if resources else None,
        description=args.description if args.description else None
    )
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
