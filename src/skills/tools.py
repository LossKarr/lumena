"""
🎯 LUMENA - Skill Tools

Outils pour créer, lister et gérer les skills depuis Lumena.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from loguru import logger

from .loader import get_skill_loader, reload_skills, Skill  # reload_skills kept for other callers


def list_skills() -> str:
    """
    Liste tous les skills disponibles.
    
    Returns:
        Texte formaté avec la liste des skills
    """
    loader = get_skill_loader()
    
    if not loader.skills:
        return "📋 Aucun skill installé.\n\nPour créer un skill:\n  python scripts/init_skill.py <nom> --path skills"
    
    lines = ["📋 **Skills disponibles:**\n"]
    
    for name, skill in loader.skills.items():
        lines.append(f"### {skill.display_name}")
        lines.append(f"- **Nom**: `{name}`")
        lines.append(f"- **Description**: {skill.description}")
        if skill.scripts:
            lines.append(f"- **Scripts**: {', '.join(s.name for s in skill.scripts)}")
        lines.append("")
    
    return "\n".join(lines)


def get_skill_info(name: str) -> str:
    """
    Obtient les informations détaillées d'un skill.
    
    Args:
        name: Nom du skill
    
    Returns:
        Informations du skill ou message d'erreur
    """
    loader = get_skill_loader()
    skill = loader.get_skill(name)
    
    if not skill:
        return f"❌ Skill '{name}' non trouvé.\n\nSkills disponibles: {', '.join(loader.list_skills())}"
    
    return skill.to_context()


def create_skill(
    name: str,
    description: str = "",
    with_script: bool = True
) -> str:
    """
    Crée un nouveau skill.
    
    Args:
        name: Nom du skill (sera normalisé en lowercase-hyphens)
        description: Description courte du skill
        with_script: Inclure un script Python template
    
    Returns:
        Message de succès ou d'erreur
    """
    # Chemin vers le script init_skill.py
    root = Path(__file__).parent.parent.parent
    init_script = root / "scripts" / "init_skill.py"
    skills_dir = root / "skills"
    
    if not init_script.exists():
        return "❌ Script init_skill.py non trouvé"
    
    # Construire la commande
    cmd = [
        sys.executable,
        str(init_script),
        name,
        "--path", str(skills_dir)
    ]
    
    if with_script:
        cmd.extend(["--resources", "scripts"])
    
    if description:
        cmd.extend(["--description", description])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )
        
        if result.returncode == 0:
            # Enregistrer le nouveau skill sans recharger tout le registre
            normalized = name.lower().replace(" ", "-").replace("_", "-")
            skill_path = skills_dir / normalized
            loader = get_skill_loader()
            loader.register_single(skill_path)
            
            return f"""✅ Skill '{normalized}' créé avec succès!

📁 Emplacement: {skill_path}

📝 Prochaines étapes:
1. Édite `{skill_path}/SKILL.md` pour personnaliser les instructions
2. Modifie `{skill_path}/scripts/{normalized}.py` pour implémenter la logique
3. Teste avec `/skill {normalized}`

🔄 Le skill est déjà chargé et prêt à l'emploi!"""
        else:
            return f"❌ Erreur création skill:\n{result.stderr or result.stdout}"
            
    except subprocess.TimeoutExpired:
        return "❌ Timeout lors de la création du skill"
    except Exception as e:
        return f"❌ Erreur: {e}"


def execute_skill_script(skill_name: str, script_name: str = None, args: str = "") -> str:
    """
    Exécute un script d'un skill.
    
    Args:
        skill_name: Nom du skill
        script_name: Nom du script (optionnel, utilise le principal par défaut)
        args: Arguments à passer au script
    
    Returns:
        Sortie du script ou message d'erreur
    """
    loader = get_skill_loader()
    skill = loader.get_skill(skill_name)
    
    if not skill:
        return f"❌ Skill '{skill_name}' non trouvé"
    
    if not skill.scripts:
        return f"❌ Le skill '{skill_name}' n'a pas de scripts"
    
    # Trouver le script
    if script_name:
        script = skill.get_script(script_name)
        if not script:
            return f"❌ Script '{script_name}' non trouvé dans le skill"
    else:
        # Utiliser le script principal (même nom que le skill)
        script = skill.get_script(skill_name) or skill.scripts[0]
    
    try:
        cmd = [sys.executable, str(script)]
        if args:
            cmd.extend(args.split())
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
            cwd=skill.path,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )
        
        output = result.stdout or result.stderr
        if result.returncode == 0:
            return f"✅ Script exécuté:\n```\n{output}\n```"
        else:
            return f"⚠️ Script terminé avec code {result.returncode}:\n```\n{output}\n```"
            
    except subprocess.TimeoutExpired:
        return "❌ Timeout: le script a pris trop de temps"
    except Exception as e:
        return f"❌ Erreur exécution: {e}"


def install_skill_from_file(skill_file: str) -> str:
    """
    Installe un skill depuis un fichier .skill.
    
    Args:
        skill_file: Chemin vers le fichier .skill
    
    Returns:
        Message de succès ou d'erreur
    """
    loader = get_skill_loader()
    skill = loader.install_skill(Path(skill_file))
    
    if skill:
        return f"✅ Skill '{skill.name}' installé avec succès!\n\n{skill.to_context()}"
    else:
        reason = loader.last_install_error or "Archive invalide ou non autorisée"
        return f"❌ Échec de l'installation du skill\nRaison: {reason}"


# Fonctions exportées pour le tool_system
SKILL_TOOLS = {
    "list_skills": {
        "function": list_skills,
        "description": "Liste tous les skills disponibles",
        "parameters": {}
    },
    "get_skill_info": {
        "function": get_skill_info,
        "description": "Obtient les informations détaillées d'un skill",
        "parameters": {
            "name": {"type": "string", "description": "Nom du skill", "required": True}
        }
    },
    "create_skill": {
        "function": create_skill,
        "description": "Crée un nouveau skill avec structure et templates",
        "parameters": {
            "name": {"type": "string", "description": "Nom du skill", "required": True},
            "description": {"type": "string", "description": "Description du skill"},
            "with_script": {"type": "boolean", "description": "Inclure un script Python"}
        }
    },
    "execute_skill": {
        "function": execute_skill_script,
        "description": "Exécute un script d'un skill",
        "parameters": {
            "skill_name": {"type": "string", "description": "Nom du skill", "required": True},
            "script_name": {"type": "string", "description": "Nom du script (optionnel)"},
            "args": {"type": "string", "description": "Arguments pour le script"}
        }
    },
    "install_skill": {
        "function": install_skill_from_file,
        "description": "Installe un skill depuis un fichier .skill",
        "parameters": {
            "skill_file": {"type": "string", "description": "Chemin vers le fichier .skill", "required": True}
        }
    }
}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
