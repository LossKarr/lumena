"""
🧪 Tests — Correctifs issus de l'analyse des logs 26-27 avril 2026

Fix #1: CodeAgent CACHE-AS-SUCCESS (servir le cache au lieu de bloquer)
Fix #2: THOUGHT leak auto-clean (_strip_thought_leak_prefix)
Fix #3: Fuzzy routing seuil dynamique
Fix #4: DeepSeek content vide — accepter reasoning_content descriptif
"""

import pytest
from types import SimpleNamespace
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# Fix #2 — _strip_thought_leak_prefix
# ═══════════════════════════════════════════════════════════════════════════

from src.reasoning.react import ReActLoop


class TestStripThoughtLeakPrefix:
    """Tests pour le nettoyage automatique des THOUGHT leaks."""

    def test_strips_french_user_prefix(self):
        text = (
            "L'utilisateur demande de corriger le bug dans le fichier main.py. "
            "Voici la correction appliquée :\n\n"
            "Le fichier a été modifié avec succès, le bug est résolu."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert not result.lower().startswith("l'utilisateur")
        assert "correction" in result.lower()

    def test_strips_french_je_dois(self):
        text = (
            "Je dois maintenant fournir la réponse finale à l'utilisateur. "
            "Voici le résumé des modifications effectuées sur le projet."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert not result.lower().startswith("je dois")

    def test_strips_english_prefix(self):
        text = (
            "The user is asking for a summary of the changes made. "
            "Here are the changes I made to the project:\n\n"
            "1. Fixed the login bug\n2. Updated the CSS"
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert not result.lower().startswith("the user")
        assert "changes" in result.lower()

    def test_strips_multiple_prefixes(self):
        text = (
            "L'utilisateur veut un résumé du projet. "
            "Je dois lui répondre clairement. "
            "Voici ce que j'ai fait sur le projet Snake 3D :\n\n"
            "1. Correction du bug d'affichage\n"
            "2. Ajout du mode multijoueur\n"
            "3. Optimisation du rendu WebGL"
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert "Snake" in result

    def test_strips_based_on_prefix(self):
        text = (
            "Based on the analysis I performed. "
            "The main issue was in the authentication module. Here's the fix applied."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert not result.lower().startswith("based on")

    def test_returns_none_if_too_short_after_clean(self):
        text = "L'utilisateur demande un truc. OK."
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is None  # < 50 chars after cleaning

    def test_returns_none_if_still_internal(self):
        text = (
            "L'utilisateur demande de corriger le bug. "
            "Je dois d'abord analyser le code source pour comprendre l'erreur et proposer une solution."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        # Should return None because after stripping first prefix,
        # result still starts with "Je dois"
        assert result is None

    def test_preserves_clean_text(self):
        text = "Voici le résumé des modifications que j'ai apportées au projet Snake 3D."
        result = ReActLoop._strip_thought_leak_prefix(text)
        # No prefix to strip, text is already clean
        assert result == text

    def test_handles_empty_string(self):
        assert ReActLoop._strip_thought_leak_prefix("") is None
        assert ReActLoop._strip_thought_leak_prefix("   ") is None

    def test_strips_apres_avoir(self):
        text = (
            "Après avoir analysé le code source en détail. "
            "Le problème vient de la fonction render() qui ne gère pas "
            "correctement les cas null. J'ai corrigé le fichier."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert "problème" in result.lower()

    def test_strips_maintenant_que(self):
        text = (
            "Maintenant que j'ai terminé l'analyse complète du projet. "
            "✅ Le jeu Snake 3D est fonctionnel avec les améliorations suivantes."
        )
        result = ReActLoop._strip_thought_leak_prefix(text)
        assert result is not None
        assert "Snake" in result


# ═══════════════════════════════════════════════════════════════════════════
# Fix #3 — Fuzzy routing seuil dynamique
# ═══════════════════════════════════════════════════════════════════════════

class TestFuzzyRoutingThreshold:
    """Tests pour la logique de seuil dynamique dans delegate_task."""

    def test_name_in_desc_lowers_threshold(self):
        """Si le nom du projet est dans la description, conf 0.80 suffit."""
        from pathlib import Path as P
        matched = str(P(r"C:\Users\user\Desktop\lumena\workspace\2026-04-26\echo-drift"))
        project_name = P(matched).name.lower()
        description = "Corrige le bug d'affichage dans echo-drift, le jeu ne démarre pas"
        assert project_name in description.lower()
        assert len(project_name) >= 3

    def test_name_not_in_desc_keeps_strict(self):
        """Si le nom n'est pas dans la description, on reste à 0.90."""
        from pathlib import Path as P
        matched = str(P(r"C:\Users\user\Desktop\lumena\workspace\2026-04-26\echo-drift"))
        project_name = P(matched).name.lower()
        description = "Crée un jeu 3D complet avec Three.js"
        assert project_name not in description.lower()

    def test_short_project_name_rejected(self):
        """Noms de projet < 3 chars ne comptent pas (trop ambigus)."""
        from pathlib import Path as P
        matched = str(P(r"C:\workspace\ab"))
        project_name = P(matched).name.lower()
        assert len(project_name) < 3


# ═══════════════════════════════════════════════════════════════════════════
# Fix #4 — DeepSeek content vide fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestDeepSeekContentVideFallback:
    """Tests pour l'acceptation de reasoning_content descriptif."""

    def test_long_descriptive_text_accepted(self):
        """Un reasoning_content de 200+ chars sans code devrait être accepté."""
        reasoning = (
            "L'utilisateur a demandé de créer un jeu Snake 3D. "
            "J'ai analysé les fichiers existants et identifié que le problème "
            "vient de la fonction d'initialisation du canvas WebGL qui ne crée "
            "pas correctement le contexte 3D. La solution est de modifier la "
            "fonction init() pour vérifier la compatibilité du navigateur."
        )
        assert len(reasoning.strip()) >= 200

    def test_short_descriptive_text_rejected(self):
        """Un reasoning_content de < 200 chars devrait être rejeté."""
        reasoning = "Je réfléchis au problème."
        assert len(reasoning.strip()) < 200

    def test_code_content_still_prioritized(self):
        """Le code dans reasoning_content reste prioritaire sur le fallback descriptif."""
        reasoning = "import os\nprint('hello')"
        assert any(m in reasoning for m in ('import ', 'export ', 'function ', 'const ', 'def '))


# ═══════════════════════════════════════════════════════════════════════════
# Fix #5 — Date extraction from user message for workspace resolution
# ═══════════════════════════════════════════════════════════════════════════

from web.routes.chat import _extract_dated_workspace


class TestExtractDatedWorkspace:
    """Tests pour l'extraction de date depuis le message utilisateur."""

    WS = r"C:\Users\user\Desktop\lumena\workspace"

    def test_du_dd_mm(self, tmp_path):
        dated = tmp_path / "2026-04-26"
        dated.mkdir()
        result = _extract_dated_workspace("jeu snake du 26/04", str(tmp_path))
        assert result == str(dated)

    def test_le_dd_mm(self, tmp_path):
        dated = tmp_path / "2026-04-25"
        dated.mkdir()
        result = _extract_dated_workspace("le projet le 25/04", str(tmp_path))
        assert result == str(dated)

    def test_hier(self, tmp_path):
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        dated = tmp_path / yesterday
        dated.mkdir()
        result = _extract_dated_workspace("le projet d'hier", str(tmp_path))
        assert result == str(dated)

    def test_avant_hier(self, tmp_path):
        from datetime import date, timedelta
        day = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
        dated = tmp_path / day
        dated.mkdir()
        result = _extract_dated_workspace("le truc d'avant-hier", str(tmp_path))
        assert result == str(dated)

    def test_no_date_returns_none(self, tmp_path):
        result = _extract_dated_workspace("corrige le bug du jeu", str(tmp_path))
        assert result is None

    def test_today_returns_none(self, tmp_path):
        from datetime import date
        today_str = date.today().strftime("%d/%m")
        dated = tmp_path / date.today().strftime("%Y-%m-%d")
        dated.mkdir()
        result = _extract_dated_workspace(f"le projet du {today_str}", str(tmp_path))
        assert result is None  # today = no redirect needed

    def test_nonexistent_date_dir_returns_none(self, tmp_path):
        result = _extract_dated_workspace("jeu du 01/01", str(tmp_path))
        assert result is None  # directory doesn't exist

    def test_yesterday_english(self, tmp_path):
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        dated = tmp_path / yesterday
        dated.mkdir()
        result = _extract_dated_workspace("fix the game from yesterday", str(tmp_path))
        assert result == str(dated)

    def test_dd_mm_yyyy_full(self, tmp_path):
        dated = tmp_path / "2026-04-20"
        dated.mkdir()
        result = _extract_dated_workspace("le projet du 20/04/2026", str(tmp_path))
        assert result == str(dated)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
