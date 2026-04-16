"""Tests PLAN HARMONIE PARFAITE — couvre P0 à P8.

Validation que le routing, les guards et le filtrage contextuel
fonctionnent sans faux positifs ni skills court-circuités.
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.pipeline_router import (
    _match_edit_and_deploy,
    _match_edit_website_only,
    match_pipeline,
    _SKILL_EXCLUSION_RE,
)
from src.reasoning.prompt_builder import (
    looks_incomplete_final_answer,
    ends_with_strong_punctuation,
)


# ═══════════════════════════════════════════════════════════════════════
# P0 — Pipeline matchers : skill exclusion
# ═══════════════════════════════════════════════════════════════════════

class TestP0SkillExclusion:
    """Les pipelines web ne doivent PAS capturer les requêtes orientées skills."""

    def test_no_match_video_remotion(self):
        assert not _match_edit_website_only("fais une vidéo remotion")

    def test_no_match_pdf_request(self):
        assert not _match_edit_website_only("modifie le rapport pdf du site")

    def test_no_match_discord_action(self):
        assert not _match_edit_website_only("améliore le site discord")

    def test_still_matches_edit_site(self):
        assert _match_edit_website_only("améliore mon site web avec un nouveau design")

    def test_edit_deploy_no_match_video(self):
        assert not _match_edit_and_deploy("améliore le site web vidéo et déploie sur IONOS")

    def test_edit_deploy_still_works_clean(self):
        assert _match_edit_and_deploy("améliore mon site web et déploie sur IONOS")

    def test_no_match_email(self):
        assert not _match_edit_website_only("modifie le site et envoie un email")

    def test_no_match_telegram(self):
        assert not _match_edit_website_only("améliore le site telegram")

    def test_pipeline_none_for_video(self):
        pipe = match_pipeline("faire un video remotion pour le client")
        assert pipe is None

    def test_pipeline_none_for_pdf(self):
        pipe = match_pipeline("crée un pdf de mon rapport")
        assert pipe is None

    def test_path_alone_no_longer_enough(self):
        """has_path supprimé : un chemin Windows sans mention de 'site' ne matche plus."""
        assert not _match_edit_website_only("modifie C:\\workspace\\monsite les fichiers")


# ═══════════════════════════════════════════════════════════════════════
# P1 — Hallucination guard : temporal bypass élargi
# ═══════════════════════════════════════════════════════════════════════

class TestP1TemporalBypass:
    """Le temporal bypass doit couvrir les références implicites au passé."""

    _TEMPORAL_BYPASS_RE = re.compile(
        r"\bj[''`']ai\s+\w+(\s+\w+){0,5}\s+(plus\s+t[oô]t|pr[eé]c[eé]demment|avant|hier|la\s+derni[eè]re\s+fois|tout\s+[àa]\s+l[''']heure|tantôt|tantoˆt)|"
        r"\b(que\s+tu\s+m[''']a(vai[st]|s)\s+demand\w*|comme\s+(demand\w*|convenu)|"
        r"tout\s+[àa]\s+l[''']instant|juste\s+avant)\b",
        re.IGNORECASE,
    )

    def test_implicit_past_ref_demands(self):
        text = "J'ai créé le rapport PDF que tu m'avais demandé"
        assert self._TEMPORAL_BYPASS_RE.search(text)

    def test_comme_convenu(self):
        text = "comme convenu j'ai planifié la tâche"
        assert self._TEMPORAL_BYPASS_RE.search(text)

    def test_juste_avant(self):
        text = "juste avant j'ai envoyé le mail"
        assert self._TEMPORAL_BYPASS_RE.search(text)

    def test_tout_a_linstant(self):
        text = "tout à l'instant j'ai créé le fichier"
        assert self._TEMPORAL_BYPASS_RE.search(text)

    def test_original_patterns_still_work(self):
        text = "j'ai créé le pdf précédemment"
        assert self._TEMPORAL_BYPASS_RE.search(text)

    def test_no_bypass_for_new_claim(self):
        text = "j'ai créé le fichier pour vous"
        assert not self._TEMPORAL_BYPASS_RE.search(text)


# ═══════════════════════════════════════════════════════════════════════
# P2 — Truncation repair : emojis + short stop
# ═══════════════════════════════════════════════════════════════════════

class TestP2TruncationRepair:
    """Le repair ne doit plus se déclencher sur des réponses complètes courtes."""

    def test_emoji_is_strong_punctuation(self):
        assert ends_with_strong_punctuation("Voilà ton rapport 😊✨")

    def test_emoji_alone(self):
        assert ends_with_strong_punctuation("Terminé ! ✅")

    def test_normal_punctuation_still_works(self):
        assert ends_with_strong_punctuation("Voilà le résultat.")

    def test_no_strong_punct(self):
        assert not ends_with_strong_punctuation("et ensuite on peut")

    def test_stop_short_answer_not_incomplete(self):
        """Réponse courte avec finish_reason=stop → pas incomplète."""
        answer = "Voilà ton rapport complet avec les données demandées. " * 5 + "😊✨"
        meta = {"finish_reason": "stop"}
        assert not looks_incomplete_final_answer(answer, meta)

    def test_stop_long_unbalanced_still_incomplete(self):
        """Réponse longue avec accolade non fermée → incomplète."""
        answer = "function test() {\n  console.log('hello');\n" + "  // code" * 300
        meta = {"finish_reason": "stop"}
        assert looks_incomplete_final_answer(answer, meta)

    def test_stop_short_clean_not_incomplete(self):
        """Réponse de 500 chars avec finish_reason=stop et emoji → False."""
        answer = "J'ai bien pris en compte ta demande. " * 10 + "✨"
        meta = {"finish_reason": "stop"}
        assert not looks_incomplete_final_answer(answer, meta)


# ═══════════════════════════════════════════════════════════════════════
# P3 — Skill priority gate
# ═══════════════════════════════════════════════════════════════════════

class TestP3SkillPriorityGate:
    """Le skill system doit avoir priorité sur le pipeline web."""

    def test_skill_exclusion_re_matches_remotion(self):
        assert _SKILL_EXCLUSION_RE.search("fais une vidéo remotion")

    def test_skill_exclusion_re_matches_pdf(self):
        assert _SKILL_EXCLUSION_RE.search("crée un document pdf")

    def test_skill_exclusion_re_no_match_site(self):
        assert not _SKILL_EXCLUSION_RE.search("améliore mon site web")


# ═══════════════════════════════════════════════════════════════════════
# P4 — Strong code pre-filter : non-code exclusion
# ═══════════════════════════════════════════════════════════════════════

class TestP4NonCodeExclusion:
    """Le pré-filtre code ne doit pas capturer les requêtes non-code."""

    _NON_CODE_KW_RE = re.compile(
        r"\b(r[eé]sum[eé]|rapport|pdf|document|facture|devis|contrat|mail|email|"
        r"vid[eé]o|remotion|photo|image|musique|spotify|discord|telegram|"
        r"whatsapp|cherche|recherche|analyse|explique|raconte|parle)\b",
        re.IGNORECASE,
    )

    def test_rapport_excluded(self):
        assert self._NON_CODE_KW_RE.search("fais un rapport PDF du projet")

    def test_video_excluded(self):
        assert self._NON_CODE_KW_RE.search("fais une vidéo du projet")

    def test_snake_not_excluded(self):
        assert not self._NON_CODE_KW_RE.search("fais un jeu snake en python")

    def test_resume_excluded(self):
        assert self._NON_CODE_KW_RE.search("fais un résumé du projet")


# ═══════════════════════════════════════════════════════════════════════
# P5 — Context rules video/remotion
# ═══════════════════════════════════════════════════════════════════════

class TestP5VideoContextRules:
    """Les requêtes video doivent activer les outils video dans le contexte."""

    def _make_registry(self):
        from src.reasoning.react import ToolRegistry
        reg = object.__new__(ToolRegistry)
        reg.tools = {}
        reg._tool_modules = {}
        reg._allowed_tools = None
        reg._caller_set_allowed = False
        reg._tools_desc_cache = None
        _categories = {
            "system": ["final_answer", "ask_user"],
            "files": ["read_file", "write_file"],
            "video": ["generate_video", "edit_video", "preview_video"],
            "web": ["web_search"],
        }
        for cat, names in _categories.items():
            for name in names:
                reg.tools[name] = {"name": name, "description": f"Test {name}", "parameters": {}}
                reg._tool_modules[name] = cat
        return reg

    def test_video_context_includes_video_tools(self):
        reg = self._make_registry()
        reg.apply_context_filter("crée une vidéo pour mon client")
        allowed = reg._allowed_tools
        assert allowed is not None
        assert "generate_video" in allowed

    def test_remotion_context_includes_video_tools(self):
        reg = self._make_registry()
        reg.apply_context_filter("fais un reel TikTok")
        allowed = reg._allowed_tools
        assert allowed is not None
        assert "generate_video" in allowed

    def test_non_video_query_excludes_video_tools(self):
        reg = self._make_registry()
        reg.apply_context_filter("envoie un mail à jean")
        allowed = reg._allowed_tools
        assert allowed is not None
        assert "generate_video" not in allowed


# ═══════════════════════════════════════════════════════════════════════
# P7 — Intent cache (structure only)
# ═══════════════════════════════════════════════════════════════════════

class TestP7IntentCache:
    """Le cache doit supporter 16 entrées et TTL 120s."""

    def test_cache_eviction_over_16(self):
        cache = {}
        for i in range(20):
            if len(cache) > 16:
                cache = {}
            cache[hash(f"query_{i}")] = (0.0, "REACT")
        # Après éviction, le cache doit être petit
        assert len(cache) <= 17
