#!/usr/bin/env python3
"""
🎯 LUMENA - Skill Validator (validate_skill.py)

Valide la structure d'un skill avant packaging.

Usage:
    python scripts/validate_skill.py <path/to/skill>
"""

import sys
import re
from pathlib import Path


REQUIRED_FIELDS = ["name", "description"]
ALLOWED_RESOURCES = {"scripts", "references", "assets"}


def parse_frontmatter(content: str) -> tuple:
    """
    Parse le frontmatter YAML d'un fichier SKILL.md.
    
    Returns:
        (success: bool, data: dict ou error_message: str)
    """
    if not content.startswith("---"):
        return False, "Le fichier doit commencer par '---' (frontmatter YAML)"
    
    # Trouver la fin du frontmatter
    lines = content.split("\n")
    end_index = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_index = i
            break
    
    if end_index == -1:
        return False, "Frontmatter YAML non fermé (manque le '---' de fin)"
    
    # Parser le YAML simple
    frontmatter_lines = lines[1:end_index]
    data = {}
    
    for line in frontmatter_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            data[key] = value
    
    return True, data


def validate_skill(skill_path: Path) -> tuple:
    """
    Valide un skill.
    
    Returns:
        (valid: bool, message: str)
    """
    skill_path = Path(skill_path).resolve()
    
    # Vérifier que le dossier existe
    if not skill_path.exists():
        return False, f"Dossier non trouvé: {skill_path}"
    
    if not skill_path.is_dir():
        return False, f"Le chemin n'est pas un dossier: {skill_path}"
    
    # Vérifier SKILL.md
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md manquant"
    
    # Lire et parser le frontmatter
    content = skill_md.read_text(encoding="utf-8")
    success, result = parse_frontmatter(content)
    
    if not success:
        return False, f"Frontmatter invalide: {result}"
    
    data = result
    
    # Vérifier les champs requis
    for field in REQUIRED_FIELDS:
        if field not in data:
            return False, f"Champ requis manquant: '{field}'"
        if not data[field] or data[field].startswith("[TODO"):
            return False, f"Champ '{field}' non rempli (contient encore TODO)"
    
    # Vérifier le nom du skill
    skill_name = data.get("name", "")
    if not re.match(r"^[a-z0-9-]+$", skill_name):
        return False, f"Nom de skill invalide: '{skill_name}' (doit être lowercase avec hyphens)"
    
    # Vérifier que le nom correspond au dossier
    if skill_name != skill_path.name:
        return False, f"Le nom '{skill_name}' ne correspond pas au dossier '{skill_path.name}'"
    
    # Vérifier les sous-dossiers (optionnels)
    for item in skill_path.iterdir():
        if item.is_dir() and item.name not in ALLOWED_RESOURCES and not item.name.startswith("."):
            return False, f"Dossier non reconnu: '{item.name}' (autorisés: {ALLOWED_RESOURCES})"
    
    # Vérifier que les scripts sont exécutables (si présents)
    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for script in scripts_dir.glob("*.py"):
            # Vérifier la syntaxe Python basique
            try:
                compile(script.read_text(encoding="utf-8"), script, "exec")
            except SyntaxError as e:
                return False, f"Erreur de syntaxe dans {script.name}: {e}"
    
    return True, f"Skill '{skill_name}' valide ✅"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/validate_skill.py <path/to/skill>")
        print("\nExemple:")
        print("  python scripts/validate_skill.py skills/weather")
        sys.exit(1)
    
    skill_path = sys.argv[1]
    
    print(f"🔍 Validation du skill: {skill_path}\n")
    
    valid, message = validate_skill(skill_path)
    
    if valid:
        print(f"✅ {message}")
        sys.exit(0)
    else:
        print(f"❌ {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
