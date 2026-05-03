"""
🧪 Test de garde ReAct loop (Phase 5.2)

Vérifie que la boucle ReAct a des protections appropriées.
"""

import pytest


class TestReActLoopGuards:
    """Tests de protection de la boucle ReAct."""
    
    def test_react_loop_has_max_iterations(self):
        """Vérifie que la boucle ReAct a une limite d'itérations."""
        from src.reasoning.react import ReActLoop
        
        loop = ReActLoop()
        
        # Vérifier qu'il y a une limite
        assert hasattr(loop, 'max_iterations'), "max_iterations non défini"
        assert loop.max_iterations > 0, "max_iterations doit être > 0"
        assert loop.max_iterations <= 100, "max_iterations trop élevé (risque de boucle)"
    
    def test_react_loop_has_timeout(self):
        """Vérifie que la boucle ReAct a un timeout."""
        from src.reasoning.react import ReActLoop
        
        loop = ReActLoop()
        
        # Vérifier qu'il y a un timeout
        assert hasattr(loop, 'timeout_seconds'), "timeout_seconds non défini"
        assert loop.timeout_seconds > 0, "timeout doit être > 0"
        assert loop.timeout_seconds <= 3600, "timeout trop long (1h max)"
    
    def test_react_loop_default_values(self):
        """Vérifie les valeurs par défaut de la boucle ReAct."""
        import os
        from unittest.mock import patch
        from src.reasoning.react import ReActLoop
        
        # Forcer la suppression des env vars pour tester la valeur par défaut du code
        env_clean = {k: v for k, v in os.environ.items()
                     if "LUMENA_MAX_REACT" not in k and "LUMENA_REACT_TIMEOUT" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            loop = ReActLoop()
            # Valeurs attendues (augmentées Phase 1 débridage : 35 depuis session 19/03)
            assert loop.max_iterations == 35, f"max_iterations devrait être 35, got {loop.max_iterations}"
            assert loop.timeout_seconds == 900, f"timeout devrait être 900s, got {loop.timeout_seconds}"

    def test_local_code_fix_detection_requires_anchor_or_file(self):
        from src.reasoning.react import ReActLoop

        assert ReActLoop._looks_like_local_code_fix(
            "corrige le bug de la touche entrée dans main.js",
            has_project_anchor=False,
            inferred_intent="code_edit",
        ) is True
        assert ReActLoop._looks_like_local_code_fix(
            "corrige le bug de la touche entrée",
            has_project_anchor=False,
            inferred_intent="question",
        ) is False

    def test_local_code_fix_detection_rejects_broad_rewrite(self):
        from src.reasoning.react import ReActLoop

        assert ReActLoop._looks_like_local_code_fix(
            "réécris toute l'architecture du projet et fusionne tout",
            has_project_anchor=True,
            inferred_intent="code_edit",
        ) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
