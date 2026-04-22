"""
🌟 LUMENA - Rules Loader (Règles de Contexte)

Charge les règles projet depuis .lumena_rules ou .lumena/rules.yaml
pour personnaliser le comportement selon le projet.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from pathlib import Path
import yaml
import json
from loguru import logger


@dataclass
class ProjectRule:
    """Une règle de projet."""
    name: str
    description: str
    content: str
    priority: int = 5
    enabled: bool = True


@dataclass
class ProjectRules:
    """Ensemble de règles d'un projet."""
    project_name: str = "Unknown"
    language: str = "python"
    style_guide: str = ""
    conventions: List[str] = field(default_factory=list)
    do_not: List[str] = field(default_factory=list)
    always: List[str] = field(default_factory=list)
    custom_rules: List[ProjectRule] = field(default_factory=list)
    context: str = ""  # Contexte additionnel
    
    def to_prompt(self) -> str:
        """Convertit les règles en instructions pour le prompt."""
        lines = [f"## Project Rules: {self.project_name}"]
        
        if self.style_guide:
            lines.append(f"\n**Style Guide**: {self.style_guide}")
        
        if self.conventions:
            lines.append("\n**Conventions:**")
            for conv in self.conventions[:5]:
                lines.append(f"- {conv}")
        
        if self.always:
            lines.append("\n**Always:**")
            for rule in self.always[:5]:
                lines.append(f"- {rule}")
        
        if self.do_not:
            lines.append("\n**Never:**")
            for rule in self.do_not[:5]:
                lines.append(f"- {rule}")
        
        if self.context:
            lines.append(f"\n**Context**: {self.context[:500]}")
        
        return "\n".join(lines)


class RulesLoader:
    """
    📜 Chargeur de règles projet
    
    Cherche les fichiers de règles dans l'ordre:
    1. .lumena_rules (YAML ou JSON)
    2. .lumena/rules.yaml
    3. .cursor/rules (compatibilité Cursor)
    """
    
    RULE_FILES = [
        ".lumena_rules",
        ".lumena_rules.yaml",
        ".lumena_rules.json",
        ".lumena/rules.yaml",
        ".cursor/rules",
        ".cursorrules",
    ]
    
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.rules: Optional[ProjectRules] = None
        self._rules_file: Optional[Path] = None
        
        # Charger automatiquement
        self._load_rules()
    
    def _load_rules(self):
        """Charge les règles depuis les fichiers."""
        for rule_file in self.RULE_FILES:
            path = self.project_root / rule_file
            if path.exists():
                self._rules_file = path
                try:
                    self.rules = self._parse_rules_file(path)
                    logger.info(f"📜 Règles chargées depuis {rule_file}")
                    return
                except Exception as e:
                    logger.warning(f"Erreur parsing {rule_file}: {e}")
        
        # Pas de fichier trouvé, créer des règles par défaut
        self.rules = ProjectRules(project_name=self.project_root.name)
        logger.debug("📜 Pas de fichier de règles trouvé, règles par défaut")
    
    def _parse_rules_file(self, path: Path) -> ProjectRules:
        """Parse un fichier de règles."""
        content = path.read_text(encoding='utf-8')
        
        # Déterminer le format
        if path.suffix in ['.yaml', '.yml'] or (path.suffix == '' and content.strip().startswith(('project', '#'))):
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError:
                # Fallback: traiter comme texte brut
                return ProjectRules(
                    project_name=self.project_root.name,
                    context=content[:2000]
                )
        elif path.suffix == '.json':
            data = json.loads(content)
        else:
            # Fichier texte brut (comme .cursorrules)
            return ProjectRules(
                project_name=self.project_root.name,
                context=content[:2000]
            )
        
        # Parser le YAML/JSON
        return ProjectRules(
            project_name=data.get('project_name', self.project_root.name),
            language=data.get('language', 'python'),
            style_guide=data.get('style_guide', ''),
            conventions=data.get('conventions', []),
            do_not=data.get('do_not', data.get('never', [])),
            always=data.get('always', []),
            context=data.get('context', ''),
        )
    
    def get_rules(self) -> ProjectRules:
        """Retourne les règles du projet."""
        if self.rules is None:
            self._load_rules()
        return self.rules or ProjectRules()
    
    def get_rules_for_prompt(self) -> str:
        """Retourne les règles formatées pour le prompt."""
        rules = self.get_rules()
        prompt = rules.to_prompt()
        
        if not prompt or prompt == f"## Project Rules: {self.project_root.name}":
            return ""
        
        return prompt
    
    def create_template(self) -> Path:
        """Crée un fichier template .lumena_rules."""
        template_path = self.project_root / ".lumena_rules"
        
        template = """# Lumena Project Rules
# Ce fichier définit les règles de comportement pour Lumena dans ce projet.

project_name: "{project_name}"
language: python

# Guide de style à suivre
style_guide: "PEP 8"

# Conventions du projet
conventions:
  - Utiliser des docstrings Google-style
  - Les noms de variables en snake_case
  - Les classes en PascalCase
  - Imports groupés (stdlib, third-party, local)

# Choses à toujours faire
always:
  - Ajouter des type hints aux fonctions
  - Logger les erreurs avec loguru
  - Gérer les exceptions proprement

# Choses à ne jamais faire
do_not:
  - Utiliser print() au lieu de logger
  - Laisser des TODO sans issue
  - Commit de code non testé

# Contexte additionnel
context: |
  Ce projet est une IA assistante appelée Lumena.
  L'architecture est modulaire avec des sous-systèmes indépendants.
"""
        template = template.format(project_name=self.project_root.name)
        
        template_path.write_text(template, encoding='utf-8')
        logger.info(f"📜 Template créé: {template_path}")
        
        return template_path
    
    def reload(self):
        """Recharge les règles depuis le fichier."""
        self.rules = None
        self._rules_file = None
        self._load_rules()


# Singleton
_rules_loader: Optional[RulesLoader] = None


def get_rules_loader(project_root: Optional[Path] = None) -> RulesLoader:
    """Retourne l'instance globale du loader de règles."""
    global _rules_loader
    if _rules_loader is None:
        if project_root is None:
            raise ValueError("project_root requis pour le premier appel")
        _rules_loader = RulesLoader(project_root)
    return _rules_loader


if __name__ == "__main__":
    project = Path(__file__).parent.parent.parent
    loader = RulesLoader(project)
    
    print(f"Rules file: {loader._rules_file}")
    print(f"\nRules for prompt:\n{loader.get_rules_for_prompt()}")
    
    # Créer un template si pas de règles
    if not loader._rules_file:
        print("\nCréation du template...")
        loader.create_template()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
