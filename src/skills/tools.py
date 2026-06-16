"""
🎯 LUMENA - Skill Tools

Outils pour créer, lister et gérer les skills depuis Lumena.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple
from loguru import logger

from src.utils.paths import ROOT_DIR
from .loader import get_skill_loader, reload_skills, Skill  # reload_skills kept for other callers
from .validation import (
    SKILL_NAME_RE,
    is_generic_description,
    parse_frontmatter,
    validate_skill_dir,
)

# Dossier de création par défaut (injectable en test via patch de _SKILLS_DIR).
_SKILLS_DIR = ROOT_DIR / "skills"

# Template minimal de script (création avec with_script=True), sans subprocess.
_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Script du skill: {name}"""
import sys


def main():
    print("Skill {name} execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


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


def _normalize_skill_name(name: str) -> str:
    """Normalise un nom de skill en lowercase-with-hyphens."""
    norm = re.sub(r"[^a-z0-9-]+", "-", str(name).lower().replace("_", "-"))
    return norm.strip("-")


def _derive_description_from_body(body: str) -> str:
    """Extrait une description du corps : 1er titre Markdown ou 1re ligne non vide."""
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        stripped = stripped.lstrip("#").strip()
        if stripped:
            return stripped[:200]
    return ""


def _strip_frontmatter(content: str) -> str:
    """Retourne le corps d'un SKILL.md, frontmatter retiré."""
    if not content.startswith("---"):
        return content.strip()
    lines = content.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:]).strip()
    return content.strip()


def _build_skill_md(
    name: str, *, content: str = "", description: str = "", fallback_description: str = ""
) -> Tuple[Optional[str], str, str]:
    """
    Construit le texte final d'un SKILL.md avec frontmatter garanti
    (name + description NON générique → S3 trigger garanti).

    Returns:
        (md_text | None, resolved_description, error_message)
    """
    fm_desc = ""
    if content and content.strip().startswith("---"):
        ok, data = parse_frontmatter(content)
        if ok and isinstance(data, dict):
            fm_desc = str(data.get("description") or "")
        body = _strip_frontmatter(content)
    else:
        body = (content or "").strip()

    # Priorité : description explicite > frontmatter > dérivée du corps >
    # description existante (repli sur update d'un skill déjà valide, F5).
    resolved = ""
    for candidate in (description, fm_desc, _derive_description_from_body(body), fallback_description):
        if candidate and not is_generic_description(candidate, name):
            resolved = candidate.strip()
            break

    if not resolved:
        return None, "", (
            "Description manquante ou générique : elle ne déclenchera jamais le "
            "skill. Fournis une description claire (ce que fait le skill + quand "
            "l'utiliser)."
        )

    display = " ".join(w.capitalize() for w in name.split("-") if w)
    body = body or f"# {display}\n\n{resolved}"
    safe_desc = resolved.replace('"', "'")
    md = f'---\nname: {name}\ndescription: "{safe_desc}"\n---\n\n{body}\n'
    return md, resolved, ""


def create_skill(
    name: str,
    description: str = "",
    with_script: bool = True,
    content: str = "",
    skills_dir=None,
    *,
    validate: bool = True,
    register: bool = True,
    allow_scripts: bool = True,
) -> str:
    """
    Crée un nouveau skill — implémentation UNIFIÉE et disciplinée.

    Chemin unique pour le ReAct interactif (via `content`) ET l'autonomie
    (via `description` + `with_script`). Garantit :
      - S1: une seule implémentation (plus de drift handler/tools).
      - S2: porte de validation — un skill invalide est rejeté (et supprimé).
      - S3: trigger garanti — refus si description générique/vide.
      - P0: garde "guides purs" — `allow_scripts=False` interdit tout script
            exécutable (les chemins autonomes l'imposent → sûreté par
            construction, voir design "boucle skill autonome").

    Args:
        name: Nom du skill (normalisé en lowercase-with-hyphens).
        description: Description courte (mécanisme de déclenchement).
        with_script: Génère un script template scripts/<name>.py.
        content: Contenu SKILL.md complet (chemin interactif). Si fourni,
                 le frontmatter est complété/garanti automatiquement.
        skills_dir: Dossier cible (défaut: _SKILLS_DIR). Injectable en test.
        validate: Passe la porte de validation (S2).
        register: Enregistre le skill dans le loader runtime si applicable.
        allow_scripts: Si False, AUCUN script n'est créé même si with_script=True
                       (garde "guides purs" pour les skills auto-créés).

    Returns:
        Message commençant par "✅" (succès) ou "❌" (échec).
    """
    # P0 — garde "guides purs" : un skill auto-créé ne contient jamais de code.
    effective_with_script = bool(with_script) and bool(allow_scripts)

    target_dir = Path(skills_dir) if skills_dir is not None else _SKILLS_DIR

    normalized = _normalize_skill_name(name)
    if not normalized or not SKILL_NAME_RE.match(normalized):
        return f"❌ Nom de skill invalide: {name}"

    skill_dir = target_dir / normalized
    if skill_dir.exists():
        return f"❌ Le skill '{normalized}' existe deja."

    md, resolved_desc, err = _build_skill_md(
        normalized, content=content, description=description
    )
    if md is None:
        return f"❌ {err}"

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        (skill_dir / "SKILL.md").write_text(md, encoding="utf-8")

        if effective_with_script:
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            (scripts_dir / f"{normalized}.py").write_text(
                _SCRIPT_TEMPLATE.format(name=normalized), encoding="utf-8"
            )

        # S2 — porte de validation : rejette (et nettoie) tout skill invalide.
        if validate:
            ok, vmsg = validate_skill_dir(skill_dir)
            if not ok:
                shutil.rmtree(skill_dir, ignore_errors=True)
                return f"❌ Skill rejete par la validation: {vmsg}"

        # Enregistrement runtime — uniquement si le skill vit sous un dossier
        # connu du loader (évite la pollution du singleton en test).
        if register:
            try:
                loader = get_skill_loader()
                bases = [str(b.resolve()) for b in loader.base_dirs]
                if any(str(skill_dir.resolve()).startswith(b) for b in bases):
                    loader.register_single(skill_dir)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("register_single échec pour {}: {}", normalized, e)

        return (
            f"✅ Skill '{normalized}' cree avec succes!\n"
            f"📁 {skill_dir / 'SKILL.md'}\n"
            f"📝 Description: {resolved_desc}"
        )
    except Exception as e:
        shutil.rmtree(skill_dir, ignore_errors=True)
        return f"❌ Erreur creation skill: {e}"


def update_skill(
    name: str,
    content: str,
    skills_dir=None,
    *,
    validate: bool = True,
    register: bool = True,
) -> str:
    """
    Met à jour le SKILL.md d'un skill existant — RE-VALIDÉ (P1).

    Réécrit uniquement SKILL.md (les ressources scripts/references/assets sont
    préservées). Si le nouveau contenu est invalide (frontmatter cassé,
    description morte…), l'ancien SKILL.md est restauré et la mise à jour est
    refusée. Symétrie avec create_skill : même discipline de validation.

    Returns:
        Message commençant par "✅" (succès) ou "❌" (échec).
    """
    target_dir = Path(skills_dir) if skills_dir is not None else _SKILLS_DIR
    normalized = _normalize_skill_name(name)
    if not normalized or not SKILL_NAME_RE.match(normalized):
        return f"❌ Nom de skill invalide: {name}"

    skill_dir = target_dir / normalized
    skill_md = skill_dir / "SKILL.md"
    if not skill_dir.exists() or not skill_md.exists():
        return f"❌ Le skill '{normalized}' n'existe pas."

    previous = skill_md.read_text(encoding="utf-8")

    # F5: si le nouveau contenu n'embarque pas de description exploitable, on
    # réutilise celle du skill existant (déjà validée) plutôt que de refuser.
    existing_desc = ""
    _ok_fm, _data_fm = parse_frontmatter(previous)
    if _ok_fm and isinstance(_data_fm, dict):
        existing_desc = str(_data_fm.get("description") or "")

    md, resolved_desc, err = _build_skill_md(
        normalized, content=content, fallback_description=existing_desc
    )
    if md is None:
        return f"❌ {err}"
    try:
        skill_md.write_text(md, encoding="utf-8")
        if validate:
            ok, vmsg = validate_skill_dir(skill_dir)
            if not ok:
                skill_md.write_text(previous, encoding="utf-8")  # rollback
                return f"❌ Mise à jour rejetée par la validation: {vmsg}"

        if register:
            try:
                loader = get_skill_loader()
                bases = [str(b.resolve()) for b in loader.base_dirs]
                if any(str(skill_dir.resolve()).startswith(b) for b in bases):
                    loader.register_single(skill_dir)
            except Exception as e:  # pragma: no cover - best effort
                logger.warning("register_single (update) échec pour {}: {}", normalized, e)

        return (
            f"✅ Skill '{normalized}' mis a jour!\n"
            f"📝 Description: {resolved_desc}"
        )
    except Exception as e:
        try:
            skill_md.write_text(previous, encoding="utf-8")  # rollback best effort
        except Exception:
            pass
        return f"❌ Erreur mise a jour skill: {e}"


def delete_skill(name: str, skills_dir=None) -> str:
    """
    Supprime un skill (P1) — branche l'uninstall existant du loader.

    Returns:
        Message commençant par "✅" (succès) ou "❌" (échec).
    """
    normalized = _normalize_skill_name(name)
    if not normalized:
        return f"❌ Nom de skill invalide: {name}"

    target_dir = Path(skills_dir) if skills_dir is not None else _SKILLS_DIR
    skill_dir = target_dir / normalized

    removed = False
    if skill_dir.exists():
        shutil.rmtree(skill_dir, ignore_errors=True)
        removed = not skill_dir.exists()
    else:
        # Pas sur disque ici → tenter l'uninstall via le loader runtime.
        try:
            removed = get_skill_loader().uninstall_skill(normalized)
        except Exception as e:  # pragma: no cover - best effort
            return f"❌ Erreur suppression skill: {e}"

    if not removed:
        return f"❌ Skill '{normalized}' introuvable."

    # Désenregistrer du loader runtime (si présent).
    try:
        get_skill_loader().skills.pop(normalized, None)
    except Exception:  # pragma: no cover - best effort
        pass

    return f"✅ Skill '{normalized}' supprime."


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


# NOTE: l'ancien dict `SKILL_TOOLS` a été supprimé (drift) — il n'était consommé
# par aucun chemin runtime (le routage des outils passe par les HandlerDef de
# reasoning/handlers/skills.py). Seul son test le référençait.
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
