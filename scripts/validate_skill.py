#!/usr/bin/env python3
"""
🎯 LUMENA - Skill Validator (validate_skill.py)

Valide la structure d'un skill avant packaging.

⚠️ Les règles de validation vivent dans src/skills/validation.py (source de
vérité unique, partagée avec la création de skills). Ce script n'est qu'une
façade CLI — il ne duplique plus les règles (fin du drift historique).

Usage:
    python scripts/validate_skill.py <path/to/skill>
"""

import sys
from pathlib import Path

# Permettre l'import de src.* quand lancé depuis n'importe où (CLI / package_skill).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.skills.validation import validate_skill_dir


def validate_skill(skill_path) -> tuple:
    """
    Valide un skill (façade vers le validateur partagé).

    Returns:
        (valid: bool, message: str)
    """
    return validate_skill_dir(Path(skill_path))


def main():
    try:  # consoles Windows cp1252 : éviter UnicodeEncodeError sur les emojis
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(sys.argv) < 2:
        print("Usage: python scripts/validate_skill.py <path/to/skill>")
        print("\nExemple:")
        print("  python scripts/validate_skill.py skills/weather")
        sys.exit(1)

    skill_path = sys.argv[1]
    print(f"🔍 Validation du skill: {skill_path}\n")

    valid, message = validate_skill(skill_path)

    if valid:
        print(f"✅ {message} ✅")
        sys.exit(0)
    else:
        print(f"❌ {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
