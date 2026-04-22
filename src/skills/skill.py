"""
🎯 LUMENA - Définition des Skills

Structures de données pour représenter un skill.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import re


class SkillTrigger:
    """
    Représente un déclencheur de skill.
    
    Peut être:
    - Un pattern exact
    - Un regex
    - Des mots-clés
    """
    
    def __init__(self, pattern: str, is_regex: bool = False):
        self.pattern = pattern
        self.is_regex = is_regex
        self._compiled_regex = None
        
        if is_regex:
            try:
                self._compiled_regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                pass  # regex invalide, skill sans pattern
    
    def matches(self, text: str) -> bool:
        """Vérifie si le texte correspond au trigger."""
        if self.is_regex and self._compiled_regex:
            return bool(self._compiled_regex.search(text))
        
        # Correspondance simple (mots-clés)
        pattern_lower = self.pattern.lower()
        text_lower = text.lower()
        
        # Gérer les placeholders [Ville], [X], etc.
        pattern_clean = re.sub(r'\[.*?\]', '.*', pattern_lower)
        if re.search(pattern_clean, text_lower):
            return True
        
        # Correspondance exacte
        return pattern_lower in text_lower
    
    def extract_variables(self, text: str) -> Dict[str, str]:
        """Extrait les variables du pattern (ex: [Ville])."""
        variables = {}
        
        # Trouver les placeholders
        placeholders = re.findall(r'\[(\w+)\]', self.pattern)
        
        if placeholders:
            # Créer un regex pour capturer les valeurs
            pattern_regex = self.pattern
            for i, ph in enumerate(placeholders):
                pattern_regex = pattern_regex.replace(f'[{ph}]', f'(.+)')
            
            try:
                match = re.search(pattern_regex, text, re.IGNORECASE)
                if match:
                    for i, ph in enumerate(placeholders):
                        variables[ph.lower()] = match.group(i + 1).strip()
            except re.error:
                pass  # regex matching échoué
        
        return variables


@dataclass
class SkillDefinition:
    """
    Définition complète d'un skill.
    
    Chargé depuis un fichier Markdown avec le format:
    
    # Skill Name
    
    ## Description
    ...
    
    ## Déclencheurs
    - pattern1
    - pattern2
    
    ## Instructions
    1. Step 1
    2. Step 2
    
    ## Exemple
    ...
    """
    
    name: str
    description: str = ""
    triggers: List[SkillTrigger] = field(default_factory=list)
    instructions: str = ""
    example: str = ""
    
    # Métadonnées
    source_file: str = ""
    priority: int = 0  # Plus haut = plus prioritaire
    enabled: bool = True
    
    # Dépendances
    required_tools: List[str] = field(default_factory=list)
    
    def matches(self, text: str) -> bool:
        """Vérifie si le skill correspond au texte."""
        if not self.enabled:
            return False
        return any(trigger.matches(text) for trigger in self.triggers)
    
    def extract_variables(self, text: str) -> Dict[str, str]:
        """Extrait les variables du premier trigger qui match."""
        for trigger in self.triggers:
            if trigger.matches(text):
                return trigger.extract_variables(text)
        return {}
    
    def get_prompt_section(self) -> str:
        """Génère la section de prompt pour ce skill."""
        lines = [
            f"### 🎯 Skill: {self.name}",
            "",
            f"**Description**: {self.description}",
            "",
            "**Instructions**:",
            self.instructions
        ]
        
        if self.example:
            lines.extend([
                "",
                "**Exemple**:",
                self.example
            ])
        
        return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
