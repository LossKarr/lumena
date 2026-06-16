"""
Validation unique des skills Lumena (source de vérité).

Ce module centralise TOUTES les règles de validation d'un skill afin
d'éliminer le drift historique : auparavant la création (handlers/tools)
n'appliquait AUCUNE validation, tandis que `scripts/validate_skill.py`
portait sa propre copie des règles. Désormais :

- `validate_skill_md_text()` valide le contenu d'un SKILL.md (frontmatter,
  nom, description non-générique).
- `validate_skill_dir()` valide un dossier de skill complet (SKILL.md +
  structure + compilation des scripts).
- `is_generic_description()` matérialise la règle "trigger garanti" (S3) :
  une description vide ou type "Skill <nom>" ne déclenchera jamais le skill,
  donc on la refuse à la création.

`scripts/validate_skill.py` importe ce module (plus de copie divergente).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Dict, Optional, Tuple

# Champs frontmatter requis
REQUIRED_FIELDS = ("name", "description")
# Sous-dossiers de ressources autorisés dans un skill
ALLOWED_RESOURCES = {"scripts", "references", "assets"}
# Regex de nom de skill : lowercase + chiffres + tirets
SKILL_NAME_RE = re.compile(r"^[a-z0-9-]+$")

# Mots "de remplissage" ignorés pour juger si une description est générique.
_DESC_FILLER = {
    "skill", "skills", "pour", "competence", "competences",
    "de", "des", "du", "la", "le", "les", "un", "une",
    "for", "the", "a", "an",
}


def _normalize_text(value: str) -> str:
    """ASCII, minuscules, espaces normalisés (aligné sur loader._normalize_text)."""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_frontmatter(content: str) -> Tuple[bool, object]:
    """
    Parse le frontmatter YAML simple d'un SKILL.md.

    Returns:
        (True, data: dict) si OK, sinon (False, message: str).
    """
    if not content.startswith("---"):
        return False, "Le fichier doit commencer par '---' (frontmatter YAML)"

    lines = content.split("\n")
    end_index = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_index = i
            break

    if end_index == -1:
        return False, "Frontmatter YAML non fermé (manque le '---' de fin)"

    data: Dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" in line and not line.strip().startswith("-"):
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return True, data


def is_generic_description(description: str, name: str = "") -> bool:
    """
    Vrai si la description est vide, un placeholder TODO, ou générique
    (ex: "Skill weather", "Skill pour Weather", "compétence").

    Règle "trigger garanti" (S3) : la description EST le mécanisme de
    déclenchement. Une description générique = skill chargé mais mort.
    """
    d = _normalize_text(description)
    if not d:
        return True
    if d.startswith("[todo"):
        return True
    name_tokens = set(_normalize_text(name).replace("-", " ").split())
    meaningful = [
        tok
        for tok in d.replace("-", " ").replace("_", " ").split()
        if tok not in _DESC_FILLER and tok not in name_tokens
    ]
    return len(meaningful) == 0


def validate_skill_md_text(
    content: str, expected_name: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Valide le contenu textuel d'un SKILL.md.

    - frontmatter présent et fermé
    - `name` présent, conforme à SKILL_NAME_RE
    - `description` présente, remplie, NON générique (trigger garanti)
    - si `expected_name` fourni : le nom doit correspondre
    """
    ok, result = parse_frontmatter(content)
    if not ok:
        return False, f"Frontmatter invalide: {result}"
    data = result  # type: ignore[assignment]

    for field in REQUIRED_FIELDS:
        if field not in data:
            return False, f"Champ requis manquant: '{field}'"
        if not data[field] or str(data[field]).startswith("[TODO"):
            return False, f"Champ '{field}' non rempli (placeholder TODO)"

    skill_name = data.get("name", "")
    if not SKILL_NAME_RE.match(skill_name):
        return False, (
            f"Nom de skill invalide: '{skill_name}' "
            "(lowercase, chiffres et tirets uniquement)"
        )

    if expected_name is not None and skill_name != expected_name:
        return False, (
            f"Le nom '{skill_name}' ne correspond pas au dossier '{expected_name}'"
        )

    if is_generic_description(data.get("description", ""), skill_name):
        return False, (
            "Description générique ou vide : elle ne déclenchera jamais le skill. "
            "Décris ce que fait le skill ET quand l'utiliser."
        )

    return True, f"SKILL.md valide ('{skill_name}')"


def validate_skill_dir(skill_path) -> Tuple[bool, str]:
    """
    Valide un dossier de skill complet : SKILL.md + structure + scripts.
    """
    skill_path = Path(skill_path).resolve()

    if not skill_path.exists():
        return False, f"Dossier non trouvé: {skill_path}"
    if not skill_path.is_dir():
        return False, f"Le chemin n'est pas un dossier: {skill_path}"

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md manquant"

    content = skill_md.read_text(encoding="utf-8")
    ok, message = validate_skill_md_text(content, expected_name=skill_path.name)
    if not ok:
        return False, message

    # Sous-dossiers : seuls scripts/references/assets (et cachés) autorisés
    for item in skill_path.iterdir():
        if item.is_dir() and item.name not in ALLOWED_RESOURCES and not item.name.startswith("."):
            return False, (
                f"Dossier non reconnu: '{item.name}' (autorisés: {sorted(ALLOWED_RESOURCES)})"
            )

    # Les scripts Python doivent compiler
    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for script in scripts_dir.glob("*.py"):
            try:
                compile(script.read_text(encoding="utf-8"), str(script), "exec")
            except SyntaxError as e:
                return False, f"Erreur de syntaxe dans {script.name}: {e}"

    return True, f"Skill '{skill_path.name}' valide"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
