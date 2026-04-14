#!/usr/bin/env python3
"""
🎯 LUMENA - Skill Packager (package_skill.py)

Crée un fichier .skill distribuable à partir d'un dossier skill.

Usage:
    python scripts/package_skill.py <path/to/skill> [output_dir]

Exemple:
    python scripts/package_skill.py skills/weather
    python scripts/package_skill.py skills/weather dist/
"""

import sys
import zipfile
from pathlib import Path

# Import local du validateur
sys.path.insert(0, str(Path(__file__).parent))
from validate_skill import validate_skill


def package_skill(skill_path: str, output_dir: str = None) -> Path:
    """
    Package un skill en fichier .skill (zip).
    
    Args:
        skill_path: Chemin vers le dossier du skill
        output_dir: Dossier de sortie (optionnel, défaut: dossier courant)
    
    Returns:
        Path vers le fichier .skill créé, ou None si erreur
    """
    skill_path = Path(skill_path).resolve()
    
    # Valider le skill d'abord
    print("🔍 Validation du skill...")
    valid, message = validate_skill(skill_path)
    
    if not valid:
        print(f"❌ Validation échouée: {message}")
        print("   Corrigez les erreurs avant de packager.")
        return None
    
    print(f"✅ {message}\n")
    
    # Déterminer le dossier de sortie
    skill_name = skill_path.name
    if output_dir:
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()
    
    skill_filename = output_path / f"{skill_name}.skill"
    
    # Créer le fichier .skill (format zip)
    try:
        print(f"📦 Création de {skill_filename.name}...")
        
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Parcourir tous les fichiers du skill
            file_count = 0
            for file_path in skill_path.rglob("*"):
                if file_path.is_file():
                    # Ignorer les fichiers cachés et __pycache__
                    if any(part.startswith(".") or part == "__pycache__" 
                           for part in file_path.parts):
                        continue
                    
                    # Chemin relatif dans le zip
                    arcname = file_path.relative_to(skill_path.parent)
                    zipf.write(file_path, arcname)
                    print(f"   📄 {arcname}")
                    file_count += 1
        
        print(f"\n✅ Skill packagé avec succès!")
        print(f"   📦 Fichier: {skill_filename}")
        print(f"   📊 {file_count} fichiers inclus")
        print(f"\n💡 Pour installer ce skill:")
        print(f"   1. Copiez {skill_filename.name} dans le dossier skills/")
        print(f"   2. Ou utilisez: /install-skill {skill_filename}")
        
        return skill_filename
        
    except Exception as e:
        print(f"❌ Erreur lors du packaging: {e}")
        return None


def main():
    if len(sys.argv) < 2:
        print("🎯 LUMENA Skill Packager")
        print("\nUsage: python scripts/package_skill.py <path/to/skill> [output_dir]")
        print("\nExemples:")
        print("  python scripts/package_skill.py skills/weather")
        print("  python scripts/package_skill.py skills/weather dist/")
        sys.exit(1)
    
    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(f"📦 Packaging du skill: {skill_path}")
    if output_dir:
        print(f"   Sortie: {output_dir}")
    print()
    
    result = package_skill(skill_path, output_dir)
    
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
