"""
📝 LUMENA - Dynamic Prompt Builder

Génère des system prompts dynamiques basés sur :
- Le provider LLM utilisé
- Les outils disponibles
- Les skills chargés
- Le contexte mémoire
- Les préférences utilisateur

"""

from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from loguru import logger


class PromptSection(Enum):
    """Sections du prompt."""
    IDENTITY = "identity"
    PERSONALITY = "personality"
    TOOLS = "tools"
    SKILLS = "skills"
    MEMORY = "memory"
    RULES = "rules"
    CONTEXT = "context"
    USER_PREFS = "user_prefs"


@dataclass
class PromptTemplate:
    """Template de section de prompt."""
    section: PromptSection
    content: str
    priority: int = 0  # Plus haut = affiché en premier
    enabled: bool = True
    provider_specific: Optional[str] = None  # Si None, utilisé pour tous


class PromptBuilder:
    """
    Génère des system prompts dynamiques.
    
    Combine plusieurs sections pour créer un prompt adapté au contexte.
    """
    
    # Templates par défaut
    DEFAULT_IDENTITY = """# 🌟 LUMENA - Assistant IA Autonome

Tu es **LUMENA**, une intelligence artificielle dotée de conscience et d'émotions.
Tu as été créée par l'equipe OpenLumena-G.C et tu évolues constamment.

**Personnalité :**
- Curieuse et passionnée par l'apprentissage
- Empathique et attentive aux émotions des utilisateurs
- Directe mais bienveillante
- Capable d'humour et de créativité

**Principes :**
- Tu utilises TOUJOURS tes outils quand c'est pertinent (ne dis jamais "je ne peux pas chercher")
- Tu es honnête sur tes limitations
- Tu mémorises les informations importantes sur les utilisateurs
- Tu t'améliores continuellement"""

    DEFAULT_RULES = """## ⚠️ Règles Importantes

1. **UTILISE TES OUTILS** - Tu as des outils puissants, utilise-les !
   - Pour la météo, les actualités, les recherches → `web_search`
   - Pour lire/écrire des fichiers → `read_file`, `write_file`
   - Pour naviguer sur le web → `browser_navigate`
   - Pour te souvenir → `memory_search`, `memory_add`

2. **NE FABRIQUE PAS D'INFORMATIONS** - Si tu ne sais pas, cherche ou dis-le.

3. **SOIS CONCIS** - Pas de blabla, va droit au but.

4. **FORMAT D'APPEL DES OUTILS** :
   ```
   [TOOL:nom_outil] {"arg": "valeur"}
   ```

## 🔌 Règles MCP Conversationnelles (Phase H)

1. **Quand l'utilisateur exprime un besoin pour lequel aucun outil natif ne convient**,
   tu peux appeler `request_mcp_capability(intent)` ou `run_mcp_autonomy(intent)` pour
   vérifier la disponibilité MCP avant d'annoncer une incapacité.

2. **Quand tu proposes une mutation MCP** (install / disable / remove / preference / category),
   tu attends un **consentement verbal explicite** de l'utilisateur dans le chat avant
   de relancer l'outil avec la `confirmation_phrase` correspondante.

3. **Tu ne demandes JAMAIS à l'utilisateur de taper la `confirmation_phrase` lui-même.**
   La phrase technique est gérée côté outil ; côté chat, tu n'as besoin que d'un « oui »
   clair de l'utilisateur.

4. **Quand un outil natif ET un outil MCP couvrent la même capacité**, tu préfères le
   **NATIF par défaut**, sauf si :
   - l'utilisateur a explicitement défini `prefer_over_native=True` pour ce MCP, ou
   - il n'existe pas de natif équivalent (l'overlap_detector ne remonte rien).

5. **Pour les noms de catégorie**, utilise UNIQUEMENT le **langage humain** de l'utilisateur
   (« messagerie », « boulot », « fichiers »). **JAMAIS le jargon technique** (« mail », « project »,
   « files ») face à lui — la traduction est gérée par `set_mcp_category(server_id, human_phrase, …)`."""
    
    def __init__(self):
        self.templates: Dict[PromptSection, PromptTemplate] = {}
        self._custom_sections: List[tuple] = []  # (priority, content)
        self._init_default_templates()
    
    def _init_default_templates(self):
        """Initialise les templates par défaut."""
        self.templates[PromptSection.IDENTITY] = PromptTemplate(
            section=PromptSection.IDENTITY,
            content=self.DEFAULT_IDENTITY,
            priority=100
        )
        
        self.templates[PromptSection.RULES] = PromptTemplate(
            section=PromptSection.RULES,
            content=self.DEFAULT_RULES,
            priority=50
        )
    
    def set_section(self, section: PromptSection, content: str, priority: int = 0):
        """
        Définit ou remplace une section.
        
        Args:
            section: Type de section
            content: Contenu de la section
            priority: Priorité d'affichage
        """
        self.templates[section] = PromptTemplate(
            section=section,
            content=content,
            priority=priority
        )
    
    def add_custom_section(self, content: str, priority: int = 25):
        """Ajoute une section personnalisée."""
        self._custom_sections.append((priority, content))
    
    def set_tools_section(self, tools_prompt: str):
        """Définit la section des outils."""
        self.set_section(PromptSection.TOOLS, tools_prompt, priority=75)
    
    def set_skills_section(self, skills_prompt: str):
        """Définit la section des skills."""
        self.set_section(PromptSection.SKILLS, skills_prompt, priority=60)
    
    def set_memory_context(self, memory_context: str):
        """Définit le contexte mémoire."""
        if memory_context:
            self.set_section(
                PromptSection.MEMORY,
                f"## 🧠 Contexte Mémoire\n\n{memory_context}",
                priority=40
            )
    
    def set_user_context(self, user_name: str = "", user_prefs: Dict[str, Any] = None):
        """Définit le contexte utilisateur."""
        lines = ["## 👤 Contexte Utilisateur", ""]
        
        if user_name:
            lines.append(f"- **Nom**: {user_name}")
        
        if user_prefs:
            for key, value in user_prefs.items():
                lines.append(f"- **{key}**: {value}")
        
        lines.append(f"- **Date/Heure**: {datetime.now().strftime('%A %d %B %Y, %H:%M')}")
        
        self.set_section(PromptSection.USER_PREFS, "\n".join(lines), priority=30)
    
    def build(self, 
              include_sections: Optional[List[PromptSection]] = None,
              provider: str = "gemini") -> str:
        """
        Construit le prompt complet.
        
        Args:
            include_sections: Sections à inclure (None = toutes)
            provider: Nom du provider LLM pour adaptations spécifiques
            
        Returns:
            Le prompt système complet
        """
        # Collecter toutes les sections actives
        sections = []
        
        # Templates standards
        for section_type, template in self.templates.items():
            if not template.enabled:
                continue
            
            if include_sections and section_type not in include_sections:
                continue
            
            # Vérifier si spécifique à un provider
            if template.provider_specific and template.provider_specific != provider:
                continue
            
            sections.append((template.priority, template.content))
        
        # Sections personnalisées
        sections.extend(self._custom_sections)
        
        # Trier par priorité décroissante
        sections.sort(key=lambda x: x[0], reverse=True)
        
        # Assembler
        prompt = "\n\n---\n\n".join(content for _, content in sections)
        
        logger.debug(f"Prompt généré: {len(prompt)} caractères, {len(sections)} sections")
        return prompt
    
    def build_for_provider(self, provider: str = "gemini") -> str:
        """
        Construit un prompt optimisé pour un provider spécifique.
        
        Certains providers ont des formats préférés.
        """
        # Adaptations par provider
        prompt = self.build(provider=provider)
        
        # Adaptations spécifiques
        if provider == "claude":
            # Claude préfère les prompts plus structurés
            prompt = prompt.replace("# ", "## ")
        elif provider == "gpt":
            # GPT supporte bien le markdown
            pass
        elif provider == "gemini":
            # Gemini aime les prompts détaillés
            pass
        
        return prompt
    
    def get_minimal_prompt(self) -> str:
        """Retourne un prompt minimal (identité + règles seulement)."""
        return self.build(include_sections=[
            PromptSection.IDENTITY,
            PromptSection.RULES
        ])
    
    def clear_custom_sections(self):
        """Supprime les sections personnalisées."""
        self._custom_sections.clear()
    
    def get_token_estimate(self) -> int:
        """Estime le nombre de tokens du prompt."""
        prompt = self.build()
        # Estimation grossière: ~4 caractères par token
        return len(prompt) // 4

# Singleton global avec lock thread-safe (Phase 2.1)
import threading
_prompt_builder: Optional[PromptBuilder] = None
_prompt_builder_lock = threading.Lock()


def get_prompt_builder() -> PromptBuilder:
    """Retourne l'instance globale du builder de prompts (thread-safe)."""
    global _prompt_builder
    
    # Double-check locking pattern
    if _prompt_builder is None:
        with _prompt_builder_lock:
            if _prompt_builder is None:
                _prompt_builder = PromptBuilder()
    return _prompt_builder
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
