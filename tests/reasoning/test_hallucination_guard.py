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

    # ── Rapport d'une mission LOCALE (faux positif corrigé) ──────────────────────
    def test_rapport_mission_avec_mission_result_passe(self):
        # Lumena lit mission_result (état done) → « c'est fini » est prouvé par cette lecture
        assert self._q("Oui c'est fini ! La mission est terminée", {"mission_result"}) is None

    def test_rapport_mission_avec_mission_status_passe(self):
        assert self._q("c'est fait, la mission a terminé", {"mission_status"}) is None

    def test_cest_fait_lecture_non_mission_reste_bloque(self):
        # garde-fou : une lecture NON-mission (read_file) ne relâche PAS le claim vague
        assert self._q("c'est fait", {"read_file"}) is not None

    def test_claim_precis_avec_mission_result_reste_strict(self):
        # la relaxation ne touche QUE les familles vagues : un claim précis reste strict
        assert self._q("j'ai envoyé le mail", {"mission_result"}) is not None

    # ── Lancement de mission : « j'ai créé une mission » prouvé par create_mission ──
    def test_claim_creation_mission_avec_create_mission_passe(self):
        assert self._q("j'ai créé une mission en arrière-plan", {"create_mission"}) is None

    def test_claim_creation_mission_avec_delegate_passe(self):
        assert self._q("j'ai créé 3 sous-missions", {"delegate_and_wait"}) is None

    def test_claim_creation_sans_outil_reste_bloque(self):
        # garde-fou : « j'ai créé » sans aucun outil reste bloqué
        assert self._q("j'ai créé une mission", set()) is not None

    def test_logo_applique_et_rendu_verifie_ne_simule_pas_une_generation_image(self):
        assert self._q(
            "Logo actif appliqué. Le rendu est vérifié.",
            {"generate_studio_documents"},
        ) is None

    def test_logo_genere_sans_outil_image_reste_bloque(self):
        assert self._q(
            "Logo généré avec succès.",
            {"generate_studio_documents"},
        ) is not None


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


class TestPeerDelegationProof:
    """P2P — « envoyer/confier une mission à un pair » est une preuve valide.

    Régression du faux positif vu en runtime (log A 08:40:00) : l'agent dit
    « j'ai envoyé la mission à l'autre Lumena » après submit_peer_task /
    peer_team_request → ne doit PAS déclencher de retry d'hallucination.
    """

    def _q(self, text, tools):
        from src.reasoning.hallucination_guard import hallucination_retry_query
        q, _ = hallucination_retry_query(text, "orig", set(tools), 0)
        return q

    def test_peer_in_any_send(self):
        from src.reasoning.hallucination_guard import _HC_TOOLS_ANY_SEND, _HC_TOOLS_PEER
        assert _HC_TOOLS_PEER <= _HC_TOOLS_ANY_SEND

    def test_envoye_mission_avec_submit_peer_task_ok(self):
        assert self._q("J'ai envoyé la mission à l'autre Lumena", {"submit_peer_task"}) is None

    def test_envoye_mission_avec_peer_team_request_ok(self):
        assert self._q("J'ai bien envoyé la mission à l'autre Lumena", {"peer_team_request"}) is None

    def test_confie_mission_a_un_pair_avec_outil_peer_ok(self):
        assert self._q("J'ai confié la mission à l'autre Lumena", {"peer_team_request"}) is None

    def test_recall_confie_mission_sans_outil_courant_pas_de_faux_positif(self):
        # Régression runtime (log A 11:17:23) : au tour « alors ? », l'agent SE
        # SOUVIENT d'avoir confié la mission (tour précédent) mais n'utilise que
        # des outils de lecture ce tour-ci. NE DOIT PAS déclencher de retry
        # (sinon il se renie et re-délègue). Pas de pattern dédié « confié ».
        assert self._q("J'ai confié la mission à l'autre Lumena", {"list_directory"}) is None
        assert self._q("la mission que j'ai confiée à l'autre Lumena", set()) is None

    def test_delegation_locale_codeagent_pas_de_faux_positif(self):
        # « délégué … au CodeAgent » (local) ne doit pas exiger d'outil peer.
        assert self._q("J'ai délégué la tâche au CodeAgent", {"delegate_task"}) is None

    # ── Lot 3 : tour « alors ?/vérifie » — rappel d'une mission async ──────────
    def test_recall_cest_fait_mission_pair_pas_de_faux_positif(self):
        # Régression log A 02:35:25 : « c'est fait » (mission déléguée) + outils de
        # LECTURE seulement → ne doit PAS déclencher de retry (travail fait en async).
        txt = "C'est fait : la mission déléguée à l'autre Lumena est terminée."
        assert self._q(txt, {"list_directory"}) is None

    def test_recall_la_tache_confiee_au_pair_terminee(self):
        txt = "La tâche que j'ai confiée au pair est terminée, les fichiers sont là."
        assert self._q(txt, {"list_directory", "find_files"}) is None

    def test_cest_fait_hors_contexte_pair_reste_strict(self):
        # « c'est fait » SANS contexte pair + aucune action → reste une hallucination.
        assert self._q("Voilà, c'est fait !", set()) is not None
