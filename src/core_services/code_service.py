"""
CodeService — Analyse, explication et debugging de code.

Migré depuis LumenaCore (4 méthodes, dépendance self.llm uniquement).
"""

import re
from typing import Any, Dict

from loguru import logger

from .base_service import BaseService


class CodeService(BaseService):
    """Analyse, explication et debug de code."""

    def search_code(self, query: str, n_results: int = 5) -> str:
        """Recherche sémantique dans le codebase."""
        code_index = self.ctx.code_index
        if code_index is None:
            return ""
        try:
            return code_index.get_context_for_query(query, max_tokens=1500)
        except Exception as e:
            logger.warning(f"Erreur recherche code: {e}")
            return ""

    def analyze_code(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Analyse un snippet de code."""
        analysis: Dict[str, Any] = {
            "language": language,
            "lines": len(code.split('\n')),
            "characters": len(code),
            "functions": [],
            "classes": [],
            "imports": [],
            "issues": []
        }
        if language == "python":
            analysis["functions"] = re.findall(r'def\s+(\w+)\s*\(', code)
            analysis["classes"] = re.findall(r'class\s+(\w+)\s*[\(:]', code)
            imports = re.findall(r'^(?:from\s+\S+\s+)?import\s+(.+)$', code, re.MULTILINE)
            analysis["imports"] = [i.strip() for i in imports]
            if len(code.split('\n')) > 500:
                analysis["issues"].append("⚠️ Fichier long (>500 lignes)")
            if "except:" in code:
                analysis["issues"].append("⚠️ Exception sans type spécifique")
            if "import *" in code:
                analysis["issues"].append("⚠️ Import wildcard (*)")
        return analysis

    async def explain_code(self, code: str, language: str = "python") -> str:
        """Explique un snippet de code en français."""
        prompt = f"""Explique le code {language} suivant de façon claire et pédagogique en français:

```{language}
{code}
```

Structure ton explication:
1. Résumé de ce que fait le code
2. Explication ligne par ligne des parties importantes
3. Conseils d'amélioration si pertinent"""

        explanation = await self.llm.chat([
            {"role": "system", "content": "Tu es un expert en programmation qui explique le code de façon claire."},
            {"role": "user", "content": prompt}
        ])
        return explanation

    async def debug_code(self, code: str, error: str = "", language: str = "python") -> str:
        """Aide à débugger un snippet de code."""
        prompt = f"""Aide-moi à débugger ce code {language}:

```{language}
{code}
```

{"Erreur rencontrée:" + chr(10) + error if error else ""}

Analyse le code et:
1. Identifie les problèmes potentiels
2. Explique la cause probable
3. Propose une correction avec le code corrigé"""

        debug_help = await self.llm.chat([
            {"role": "system", "content": "Tu es un expert en debugging qui aide à corriger le code."},
            {"role": "user", "content": prompt}
        ])
        return debug_help
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
