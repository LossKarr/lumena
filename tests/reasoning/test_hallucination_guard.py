"""Tests — Guard anti-hallucination dans react.py.

Vérifie que :
- Les constantes _HC_TOOLS_* sont des frozensets non vides et cohérents
- Les patterns génériques pointent sur des familles sémantiques et non _ALL_MUTATIONS
- _HC_TOOLS_ANY_CREATE et _HC_TOOLS_ANY_SEND couvrent les bonnes familles
- Aucun pattern ne référence _LEDGER_MUTATION_TOOLS en entier (>100 outils)
- Les familles critique (mail, discord, github) sont disjointes de read-only
"""

import pytest
from src.reasoning.react import (
    _HC_TOOLS_FILE,
    _HC_TOOLS_DOC,
    _HC_TOOLS_SITE,
    _HC_TOOLS_TASK,
    _HC_TOOLS_MAIL,
    _HC_TOOLS_DISCORD,
    _HC_TOOLS_MESSAGING,
    _HC_TOOLS_SOCIAL,
    _HC_TOOLS_STRIPE,
    _HC_TOOLS_GITHUB,
    _HC_TOOLS_IMAGE,
    _HC_TOOLS_NOTION,
    _HC_TOOLS_ANY_CREATE,
    _HC_TOOLS_ANY_SEND,
)
from src.runtime.execution_ledger import MUTATION_TOOLS as _ALL_MUTATION_TOOLS

_READONLY_TOOLS = frozenset({
    "read_file", "web_search", "search_web", "read_url", "memory_recall",
    "memory_retrieve", "get_context", "list_files", "list_directory",
    "search_memory", "retrieve_memory", "get_weather",
})


class TestHCToolsFamilies:
    def test_all_families_nonempty(self):
        for name, family in [
            ("FILE", _HC_TOOLS_FILE), ("DOC", _HC_TOOLS_DOC), ("SITE", _HC_TOOLS_SITE),
            ("TASK", _HC_TOOLS_TASK), ("MAIL", _HC_TOOLS_MAIL), ("DISCORD", _HC_TOOLS_DISCORD),
            ("MESSAGING", _HC_TOOLS_MESSAGING), ("SOCIAL", _HC_TOOLS_SOCIAL),
            ("STRIPE", _HC_TOOLS_STRIPE), ("GITHUB", _HC_TOOLS_GITHUB),
            ("IMAGE", _HC_TOOLS_IMAGE), ("NOTION", _HC_TOOLS_NOTION),
        ]:
            assert len(family) > 0, f"_HC_TOOLS_{name} est vide"

    def test_all_families_are_frozensets(self):
        for family in [
            _HC_TOOLS_FILE, _HC_TOOLS_DOC, _HC_TOOLS_SITE, _HC_TOOLS_TASK,
            _HC_TOOLS_MAIL, _HC_TOOLS_DISCORD, _HC_TOOLS_MESSAGING, _HC_TOOLS_SOCIAL,
            _HC_TOOLS_STRIPE, _HC_TOOLS_GITHUB, _HC_TOOLS_IMAGE, _HC_TOOLS_NOTION,
            _HC_TOOLS_ANY_CREATE, _HC_TOOLS_ANY_SEND,
        ]:
            assert isinstance(family, frozenset)

    def test_any_create_contains_file_family(self):
        assert _HC_TOOLS_FILE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_doc_family(self):
        assert _HC_TOOLS_DOC <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_site_family(self):
        assert _HC_TOOLS_SITE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_task_family(self):
        assert _HC_TOOLS_TASK <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_stripe_family(self):
        assert _HC_TOOLS_STRIPE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_github_family(self):
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_CREATE

    def test_any_send_contains_mail(self):
        assert _HC_TOOLS_MAIL <= _HC_TOOLS_ANY_SEND

    def test_any_send_contains_messaging(self):
        assert _HC_TOOLS_MESSAGING <= _HC_TOOLS_ANY_SEND

    def test_any_send_contains_social(self):
        assert _HC_TOOLS_SOCIAL <= _HC_TOOLS_ANY_SEND

    def test_families_not_equal_to_all_mutations(self):
        """Les familles sémantiques ne doivent pas couvrir ALL mutations (>100 outils)."""
        assert len(_HC_TOOLS_ANY_CREATE) < len(_ALL_MUTATION_TOOLS), (
            f"_HC_TOOLS_ANY_CREATE ({len(_HC_TOOLS_ANY_CREATE)}) doit être < MUTATION_TOOLS ({len(_ALL_MUTATION_TOOLS)})"
        )

    def test_no_readonly_in_create_family(self):
        assert not (_HC_TOOLS_ANY_CREATE & _READONLY_TOOLS), (
            f"Outils read-only dans ANY_CREATE: {_HC_TOOLS_ANY_CREATE & _READONLY_TOOLS}"
        )

    def test_no_readonly_in_send_family(self):
        assert not (_HC_TOOLS_ANY_SEND & _READONLY_TOOLS), (
            f"Outils read-only dans ANY_SEND: {_HC_TOOLS_ANY_SEND & _READONLY_TOOLS}"
        )

    def test_mail_tools_in_send_not_in_create(self):
        """mail_send est dans ANY_SEND mais pas dans ANY_CREATE."""
        assert "mail_send" in _HC_TOOLS_ANY_SEND
        assert "mail_send" not in _HC_TOOLS_ANY_CREATE

    def test_write_file_in_create_not_in_send(self):
        assert "write_file" in _HC_TOOLS_ANY_CREATE
        assert "write_file" not in _HC_TOOLS_ANY_SEND

    def test_github_tools_in_both(self):
        """GitHub crée ET pousse → dans ANY_CREATE et ANY_SEND."""
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_CREATE
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_SEND

    def test_stripe_not_in_send(self):
        """Stripe est création de ressources, pas envoi de messages."""
        assert not (_HC_TOOLS_STRIPE & _HC_TOOLS_ANY_SEND)

    def test_image_in_create_not_in_send(self):
        assert _HC_TOOLS_IMAGE <= _HC_TOOLS_ANY_CREATE
        assert not (_HC_TOOLS_IMAGE & _HC_TOOLS_ANY_SEND)

    def test_notion_in_create(self):
        assert _HC_TOOLS_NOTION <= _HC_TOOLS_ANY_CREATE

    def test_discord_in_send(self):
        assert _HC_TOOLS_DISCORD <= _HC_TOOLS_ANY_SEND

    def test_key_tools_present(self):
        """Spot-checks pour les outils les plus critiques."""
        assert "create_task" in _HC_TOOLS_TASK
        assert "schedule_task" in _HC_TOOLS_TASK
        assert "discord_send_message" in _HC_TOOLS_DISCORD
        assert "git_commit" in _HC_TOOLS_GITHUB
        assert "generate_image" in _HC_TOOLS_IMAGE
        assert "stripe_create_invoice" in _HC_TOOLS_STRIPE
        assert "telegram_send_message" in _HC_TOOLS_MESSAGING
        assert "twitter_post_tweet" in _HC_TOOLS_SOCIAL


# ── Extension CU / login (familles ajoutées pour l'ancrage-vérité) ──────────

from src.reasoning.react import (
    _HC_TOOLS_TYPE, _HC_TOOLS_OPEN_APP, _HC_TOOLS_CLICK, _HC_TOOLS_LOGIN,
)


class TestHCToolsCUFamilies:
    def test_familles_cu_non_vides_et_frozenset(self):
        for fam in (_HC_TOOLS_TYPE, _HC_TOOLS_OPEN_APP, _HC_TOOLS_CLICK, _HC_TOOLS_LOGIN):
            assert isinstance(fam, frozenset) and len(fam) > 0

    def test_type_couvre_natif_et_browser(self):
        assert "type_text" in _HC_TOOLS_TYPE
        assert "browser_type" in _HC_TOOLS_TYPE

    def test_login_inclut_browser_login_et_la_frappe(self):
        assert "browser_login" in _HC_TOOLS_LOGIN
        assert _HC_TOOLS_TYPE <= _HC_TOOLS_LOGIN

    def test_open_app_natif(self):
        assert "open_app" in _HC_TOOLS_OPEN_APP

    def test_aucun_nom_mcp_code_en_dur(self):
        # Les noms d'outils MCP sont dynamiques → ne doivent PAS être listés.
        for fam in (_HC_TOOLS_TYPE, _HC_TOOLS_OPEN_APP, _HC_TOOLS_CLICK, _HC_TOOLS_LOGIN):
            assert not any(t.startswith("mcp__") for t in fam), "nom MCP codé en dur"


class TestMCPGenericProof:
    """Un outil MCP réussi (installé dynamiquement) compte comme preuve plausible
    pour une action bureau/login, sans avoir à le déclarer."""

    def _loop(self, tmp_path):
        from src.reasoning.react import ReActLoop, ToolRegistry
        return ReActLoop(llm_chat_func=None, tools=ToolRegistry(lumena=None, lumena_root=tmp_path))

    def test_claim_tape_sans_aucun_outil_est_bloque(self, tmp_path):
        loop = self._loop(tmp_path)
        loop._successful_session_tools = set()
        assert loop._action_hallucination_retry_query("j'ai tapé le texte", "orig") is not None

    def test_claim_tape_avec_mcp_reussi_passe(self, tmp_path):
        loop = self._loop(tmp_path)
        loop._successful_session_tools = {"mcp__un-mcp-quelconque__Type"}
        assert loop._action_hallucination_retry_query("j'ai tapé le texte", "orig") is None

    def test_claim_tape_avec_type_text_natif_passe(self, tmp_path):
        loop = self._loop(tmp_path)
        loop._successful_session_tools = {"type_text"}
        assert loop._action_hallucination_retry_query("j'ai tapé le texte", "orig") is None


# ── Temps 2 — familles complètes (carte) + claims vagues + anti-dérive ───────

from src.reasoning.react import _HC_TOOLS_ANY_ACTION, _HC_TOOLS_READONLY, _HC_TOOLS_OPEN_APP
from src.reasoning.hallucination_guard import (
    _HC_TOOLS_MEDIA, _HC_TOOLS_EXEC,
    _HC_TOOLS_IDE, _HC_TOOLS_BROWSER_TECH, _HC_TOOLS_DEPLOY, _HC_TOOLS_DB,
    _HC_TOOLS_DB_PROPOSE, _HC_TOOLS_DB_CONFIG, _HC_TOOLS_NETWORK, _HC_TOOLS_N8N,
    _HC_TOOLS_SKILL, _HC_TOOLS_HTTP, _HC_TOOLS_PEER, _HC_TOOLS_CONFIG,
    _HC_TOOLS_MEMORY, _HC_TOOLS_MAIL_ADMIN, _HC_TOOLS_CU_TASK,
)

_NEW_FAMILIES = [
    _HC_TOOLS_MEDIA, _HC_TOOLS_EXEC, _HC_TOOLS_IDE, _HC_TOOLS_BROWSER_TECH,
    _HC_TOOLS_DEPLOY, _HC_TOOLS_DB, _HC_TOOLS_DB_PROPOSE, _HC_TOOLS_DB_CONFIG,
    _HC_TOOLS_NETWORK, _HC_TOOLS_N8N, _HC_TOOLS_SKILL, _HC_TOOLS_HTTP,
    _HC_TOOLS_PEER, _HC_TOOLS_CONFIG, _HC_TOOLS_MEMORY, _HC_TOOLS_MAIL_ADMIN,
    _HC_TOOLS_CU_TASK,
]


class TestNewFamiliesAndAnyAction:
    def test_nouvelles_familles_non_vides_frozenset(self):
        for fam in _NEW_FAMILIES:
            assert isinstance(fam, frozenset) and len(fam) > 0

    def test_readonly_et_any_action_disjoints(self):
        assert not (_HC_TOOLS_READONLY & _HC_TOOLS_ANY_ACTION)

    def test_nouvelles_familles_incluses_dans_any_action(self):
        for fam in _NEW_FAMILIES:
            assert fam <= _HC_TOOLS_ANY_ACTION, fam

    def test_spotify_classe(self):
        # Fix du faux positif : Spotify est une action (MEDIA + OPEN_APP), pas du vide.
        assert "spotify_play" in _HC_TOOLS_MEDIA
        assert "spotify_play" in _HC_TOOLS_OPEN_APP
        assert "spotify_play" in _HC_TOOLS_ANY_ACTION
        assert "spotify_play" not in _HC_TOOLS_READONLY


class TestVagueClaimsProvenByAnyAction:
    """« c'est fait » / claims d'install = prouvés par TOUTE action réelle ;
    le vide pur reste bloqué (pas de régression de la protection)."""

    def _q(self, text, tools):
        from src.reasoning.hallucination_guard import hallucination_retry_query
        q, _ = hallucination_retry_query(text, "orig", set(tools), 0)
        return q

    def test_cest_fait_avec_spotify_play_passe(self):
        assert self._q("c'est fait", {"spotify_play"}) is None

    def test_cest_fait_sans_aucun_outil_bloque(self):
        assert self._q("c'est fait", set()) is not None

    def test_cest_fait_avec_lecture_seule_bloque(self):
        # une lecture (read_file) n'est PAS une action → claim vague non prouvé
        assert self._q("c'est fait", {"read_file"}) is not None

    def test_deploye_avec_succes_avec_deploy_tool_passe(self):
        assert self._q("Le site a été déployé avec succès", {"deploy_to_ionos"}) is None

    def test_installe_avec_succes_avec_mcp_dynamique_passe(self):
        assert self._q("MCP installé et testé avec succès", {"mcp__weather__forecast"}) is None

    def test_claim_precis_reste_strict(self):
        # « j'ai envoyé le mail » exige toujours un outil MAIL, pas n'importe quelle action
        assert self._q("j'ai envoyé le mail", {"spotify_play"}) is not None


class TestAntiDriftClassification:
    """Garde-fou anti-dérive : TOUT outil natif enregistré doit être classé
    (READONLY ou ANY_ACTION). Les outils MCP dynamiques (mcp__*) sont exclus."""

    def test_tous_les_outils_natifs_sont_classes(self, tmp_path):
        from src.reasoning.react import ToolRegistry
        reg = ToolRegistry(lumena=None, lumena_root=tmp_path)
        registered = set(getattr(reg, "tools", {}) or {})
        native = {t for t in registered if not t.startswith("mcp__")}
        classified = _HC_TOOLS_READONLY | _HC_TOOLS_ANY_ACTION
        unclassified = native - classified
        assert not unclassified, (
            f"{len(unclassified)} outil(s) natif(s) non classé(s) — "
            f"ajoute-les à READONLY ou à une famille d'action : {sorted(unclassified)}"
        )
