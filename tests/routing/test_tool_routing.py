"""
Tests de routing des outils — filet de sécurité pour les keyword lists.

Vérifie que les requêtes utilisateur courantes sont correctement acheminées
vers les outils attendus via _TOOL_COMPLETION_HINTS (react_config.py)
et les PACK keywords (tool_registry.py).
"""

import pytest
import unicodedata


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Minuscule + décompose accents pour matching stem."""
    return unicodedata.normalize("NFD", text.lower())


def _match_hints(query: str, hints: dict[str, list[str]]) -> list[str]:
    """Retourne les outils dont au moins un keyword matche (stem) dans la query."""
    q = _normalize(query)
    matched = []
    for tool_name, keywords in hints.items():
        for kw in keywords:
            if _normalize(kw) in q:
                matched.append(tool_name)
                break
    return matched


def _match_packs(query: str, packs: list[tuple[set[str], set[str]]]) -> list[str]:
    """Retourne les catégories de packs dont au moins un keyword matche."""
    q = _normalize(query)
    matched_categories = []
    for keywords_set, categories in packs:
        for kw in keywords_set:
            if _normalize(kw) in q:
                matched_categories.extend(categories)
                break
    return list(set(matched_categories))


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tool_hints() -> dict[str, list[str]]:
    """Charge _TOOL_COMPLETION_HINTS depuis react_config."""
    from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
    return _TOOL_COMPLETION_HINTS


@pytest.fixture(scope="module")
def tool_packs() -> list[tuple[set[str], set[str]]]:
    """Charge les PACK keyword sets depuis tool_registry."""
    from src.reasoning.tool_registry import ToolRegistry
    # Les packs sont définis dans _CONTEXT_RULES (liste de tuples (keywords, categories))
    rules = getattr(ToolRegistry, "_CONTEXT_RULES", None)
    if rules is None:
        # Fallback: tenter l'import direct
        try:
            from src.reasoning.tool_registry import _CONTEXT_RULES
            rules = _CONTEXT_RULES
        except ImportError:
            pytest.skip("_CONTEXT_RULES non trouvé dans tool_registry")
    return rules


# ══════════════════════════════════════════════════════════════════════════════
# Tests HINTS : _TOOL_COMPLETION_HINTS (react_config.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestDelegateTaskHints:
    """Vérifie que delegate_task est suggéré pour les requêtes de code."""

    @pytest.mark.parametrize("query", [
        "code moi un flappy bird",
        "code-moi un site portfolio",
        "crée moi une application de todo",
        "développe une API REST",
        "programme un jeu snake en Python",
        "debug mon script Python",
        "corrige le bug dans mon projet",
        "refactor le fichier main.py",
        "répare l'erreur dans le serveur",
        "résou le problème de connexion API",
        "améliore le design du site",
        "modifie le header du portfolio",
        "ajoute une page contact au website",
        "mets a jour le site portfolio",
        "crée un bot Discord",
        "fais moi un game en JavaScript",
    ])
    def test_delegate_task_suggested(self, query, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert "delegate_task" in matched, (
            f"delegate_task devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )

    @pytest.mark.parametrize("query,not_expected", [
        ("crée moi une vidéo de présentation", "delegate_task"),
        ("génère une image de chat", "delegate_task"),
        ("envoie un mail à Jean", "delegate_task"),
        ("lis le fichier readme", "delegate_task"),
        ("quelle heure est-il", "delegate_task"),
    ])
    def test_delegate_task_not_suggested(self, query, not_expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert not_expected not in matched, (
            f"'{not_expected}' ne devrait PAS être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )


class TestVideoImageHints:
    """Vérifie que les outils media ne collisionnent pas avec delegate_task."""

    @pytest.mark.parametrize("query,expected", [
        ("crée moi une vidéo de présentation", "generate_video"),
        ("génère un clip TikTok", "generate_video"),
        ("fais une animation motion", "generate_video"),
    ])
    def test_video_suggested(self, query, expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert expected in matched, (
            f"'{expected}' devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )

    @pytest.mark.parametrize("query,expected", [
        ("génère une image de chat", "generate_image"),
        ("crée une illustration pour mon article", "generate_image"),
        ("fais moi un visuel pour Instagram", "generate_image"),
    ])
    def test_image_suggested(self, query, expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert expected in matched, (
            f"'{expected}' devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )


class TestMailHints:
    """Vérifie que les mails sont bien routés."""

    @pytest.mark.parametrize("query,expected", [
        ("envoie un mail à Jean", "send_email"),
        ("envoie un email de confirmation", "send_email"),
    ])
    def test_mail_suggested(self, query, expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert expected in matched, (
            f"'{expected}' devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )


class TestBrowserHints:
    """Vérifie que les requêtes de navigation déclenchent le browser."""

    @pytest.mark.parametrize("query,expected", [
        ("ouvre google.com et navigue vers les recettes", "browser_navigate"),
        ("ouvre le site de la SNCF", "browser_navigate"),
        ("navigue sur amazon.fr", "browser_navigate"),
    ])
    def test_browser_suggested(self, query, expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert expected in matched, (
            f"'{expected}' devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )


class TestStripeHints:
    """Vérifie que les requêtes Stripe sont correctement routées."""

    @pytest.mark.parametrize("query,expected", [
        ("crée un produit stripe à 14 euros", "stripe_create_product"),
        ("liste les factures stripe", "stripe_list_invoices"),
        ("crée un lien de paiement stripe", "stripe_create_payment_link"),
    ])
    def test_stripe_suggested(self, query, expected, tool_hints):
        matched = _match_hints(query, tool_hints)
        assert expected in matched, (
            f"'{expected}' devrait être suggéré pour: '{query}'\n"
            f"Outils matchés: {matched}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tests PACKS : keyword sets dans tool_registry.py
# ══════════════════════════════════════════════════════════════════════════════

class TestPackRouting:
    """Vérifie que les PACK keywords chargent les bonnes catégories d'outils."""

    @pytest.mark.parametrize("query,expected_cat", [
        ("crée un site web portfolio", "agents"),
        ("code moi un jeu flappy bird", "agents"),
        ("debug mon application React", "agents"),
        ("développe une API REST", "agents"),
        ("programme un snake en Python", "agents"),
        ("refactor le code source", "agents"),
    ])
    def test_code_queries_load_agents_pack(self, query, expected_cat, tool_packs):
        cats = _match_packs(query, tool_packs)
        assert expected_cat in cats, (
            f"Pack '{expected_cat}' devrait être chargé pour: '{query}'\n"
            f"Catégories matchées: {cats}"
        )

    @pytest.mark.parametrize("query,expected_cat", [
        ("git commit -m 'fix'", "git"),
        ("crée une branche feature", "git"),
        ("push sur le repo", "git"),
    ])
    def test_git_queries_load_git_pack(self, query, expected_cat, tool_packs):
        cats = _match_packs(query, tool_packs)
        assert expected_cat in cats, (
            f"Pack '{expected_cat}' devrait être chargé pour: '{query}'\n"
            f"Catégories matchées: {cats}"
        )

    @pytest.mark.parametrize("query", [
        "installe un MCP pour github",
        "trouve un outil externe pour analyser github",
        "il me manque un outil externe",
    ])
    def test_mcp_install_queries_load_mcp_category(self, query, tool_packs):
        """Phase D : install/missing-tool queries pointent vers la categorie
        unifiee "mcp" (etait "mcp_loop_integration")."""
        cats = _match_packs(query, tool_packs)
        assert "mcp" in cats

    @pytest.mark.parametrize("query", [
        "utilise le MCP actif github",
        "utiliser le mcp active pour chercher",
        "use mcp tool github search",
    ])
    def test_active_mcp_queries_load_real_mcp_category(self, query, tool_packs):
        cats = _match_packs(query, tool_packs)
        assert "mcp" in cats


# ══════════════════════════════════════════════════════════════════════════════
# Tests de NON-COLLISION : pas de faux positifs critiques
# ══════════════════════════════════════════════════════════════════════════════

class TestNoCollisions:
    """Vérifie qu'il n'y a pas de collisions critiques entre outils."""

    def test_generate_video_not_delegate(self, tool_hints):
        """'vidéo' ne doit pas trigger delegate_task."""
        matched = _match_hints("crée une vidéo de présentation", tool_hints)
        assert "generate_video" in matched
        assert "delegate_task" not in matched

    def test_generate_image_not_delegate(self, tool_hints):
        """'image' ne doit pas trigger delegate_task."""
        matched = _match_hints("génère une image de paysage", tool_hints)
        assert "generate_image" in matched
        assert "delegate_task" not in matched

    def test_send_email_not_delegate(self, tool_hints):
        """'envoie mail' ne doit pas trigger delegate_task."""
        matched = _match_hints("envoie un mail à Pierre", tool_hints)
        assert "send_email" in matched
        assert "delegate_task" not in matched

    def test_delegate_task_key_not_duplicated(self, tool_hints):
        """delegate_task ne doit apparaître qu'une seule fois dans les hints."""
        keys = list(tool_hints.keys())
        count = keys.count("delegate_task")
        assert count == 1, f"delegate_task apparaît {count} fois dans _TOOL_COMPLETION_HINTS"


# ══════════════════════════════════════════════════════════════════════════════
# Tests CATÉGORIE BDD IONOS (fix routage 3E) : apply_context_filter réel
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def _registry():
    from src.reasoning.tool_registry import ToolRegistry
    return ToolRegistry()


def _allowed_for(reg, query):
    reg.clear_context_filter()
    reg.apply_context_filter(query, intent="react")
    return reg._allowed_tools or set()


def test_bdd_du_site_inclut_ionos(_registry):
    """'la bdd de ton site web' (sans 'ionos' littéral) inclut la catégorie ionos."""
    allowed = _allowed_for(_registry, "lumena vois tu la bdd de ton site web ?")
    assert "ionos_db_list_tables" in allowed


def test_list_tables_ionos_inclut_ionos(_registry):
    allowed = _allowed_for(_registry, "liste les tables de openlumena.com sur ionos")
    assert "ionos_db_list_tables" in allowed
    assert "ionos_db_select" in allowed


def test_creer_table_bdd_expose_ionos_create(_registry):
    """'rajoute une table test à la bdd' expose l'outil ionos de création (pas seulement fichiers/shell)."""
    allowed = _allowed_for(_registry, "rajoute une table test à la bdd")
    assert "ionos_db_create_sandbox_table" in allowed
    assert "ionos_db_set_sandbox_config" in allowed


def test_cree_une_table_test_expose_ionos(_registry):
    allowed = _allowed_for(_registry, "crée une table test")
    assert "ionos_db_create_sandbox_table" in allowed


def test_table_dans_bdd_openlumena_expose_ionos(_registry):
    allowed = _allowed_for(_registry, "crée une table dans la bdd openlumena")
    assert "ionos_db_create_sandbox_table" in allowed
    assert "ionos_db_set_sandbox_config" in allowed


def test_opelumena_typo_table_bdd_expose_ionos(_registry):
    allowed = _allowed_for(_registry, "rajoute une table test a la bdd sur ionos du site opelumena")
    assert "ionos_db_create_sandbox_table" in allowed
    assert "ionos_db_set_sandbox_config" in allowed


def test_ionos_hard_context_is_multi_site_not_openlumena_hardcoded():
    from src.reasoning.tool_registry import _looks_like_ionos_config_access, _looks_like_ionos_db_intent

    sites = ("client-a.fr", "formation-pro.com")
    assert _looks_like_ionos_db_intent(
        "rajoute une table test a la bdd du site client-a.fr",
        configured_sites=sites,
    )
    assert _looks_like_ionos_db_intent(
        "liste les tables de formationpro",
        configured_sites=sites,
    )
    assert not _looks_like_ionos_db_intent(
        "sans rien publier hors workspace cree un site complet avec backend bdd multi page",
        configured_sites=sites,
    )
    assert not _looks_like_ionos_db_intent(
        "cree un site complet avec BDD locale. ne touche pas a IONOS, ne touche pas aux BDD IONOS",
        configured_sites=sites,
    )
    assert _looks_like_ionos_config_access(
        r"C:\Users\user\Desktop\lumena\workspace\formation-pro\api\config.local.php",
        configured_sites=sites,
    )
    assert not _looks_like_ionos_config_access(
        r"C:\Users\user\Desktop\lumena\workspace\site-formation\api\config.local.php",
        configured_sites=sites,
    )


def test_data_category_groups_datagouv_sirene_geo_workbench(_registry):
    """Phase 1 : datagouv/sirene/geo/data_* sont dans la MÊME catégorie runtime `data`."""
    tm = getattr(_registry, "_tool_modules", {}) or {}
    for tool in ("datagouv_search", "datagouv_get_dataset", "datagouv_download_resource",
                 "sirene_search_company", "sirene_get_by_siret",
                 "geo_search_address", "geo_reverse", "geo_commune_info",
                 "data_profile_file", "data_aggregate", "data_filter_rows",
                 "data_unique_values", "data_join", "data_export"):
        assert tm.get(tool) == "data", f"{tool} devrait être catégorie 'data', a {tm.get(tool)}"


def test_data_contract_autonomy_no_workspace():
    """Phase 1 : contrat data = autonomy_allowed=True, requires_workspace=False."""
    from src.reasoning import tool_categories as tc
    ct = tc.get_category_contract("data")
    assert ct is not None and ct.name == "data"
    assert ct.autonomy_allowed is True
    assert ct.requires_workspace is False


def test_lsp_contract_autonomous_with_workspace():
    """Phase 3 : lsp (lecture code) devient autonome ET exige un workspace."""
    from src.reasoning import tool_categories as tc
    ct = tc.get_category_contract("lsp")
    assert ct is not None and ct.name == "lsp"
    assert ct.autonomy_allowed is True
    assert ct.requires_workspace is True


def test_skills_contract_not_autonomous():
    """Phase 3 : skills (edit_own_code/rollback) protégé → NON-autonome."""
    from src.reasoning import tool_categories as tc
    ct = tc.get_category_contract("skills")
    assert ct is not None and ct.name == "skills"
    assert ct.autonomy_allowed is False


def test_data_pack_loads_data_category(_registry):
    """Phase 1 : requêtes data.gouv / SIRET / SIREN / code INSEE chargent la catégorie data."""
    for q in [
        "recherche le dataset accidents sur data.gouv",
        "trouve l'entreprise avec le siret 12345678900012",
        "donne-moi le siren de cette société",
        "quel est le code insee de Nantes",
        "télécharge ce jeu de données open data",
    ]:
        allowed = _allowed_for(_registry, q)
        assert "datagouv_search" in allowed, f"data non chargé pour: {q}"
        # le pipeline complet (récupération + analyse) doit être visible ensemble
        assert "data_aggregate" in allowed, f"data_aggregate non chargé pour: {q}"


def test_data_tools_visible_via_data_pack(_registry):
    """Phase 1 : les 4 familles d'outils data sont visibles quand le PACK DATA matche."""
    allowed = _allowed_for(_registry, "cherche un dataset insee et analyse le fichier")
    for tool in ("datagouv_search", "sirene_search_company", "geo_search_address", "data_profile_file"):
        assert tool in allowed, f"{tool} devrait être visible via PACK DATA"


def test_simple_web_search_does_not_load_data(_registry):
    """Phase 1 (anti-bruit) : une recherche web simple ne charge PAS la catégorie data."""
    for q in ["cherche des infos sur le chat", "c'est quoi la photosynthèse",
              "actualités du jour"]:
        allowed = _allowed_for(_registry, q)
        assert "datagouv_search" not in allowed, f"data chargé à tort pour: {q}"


def test_ionos_db_intent_no_false_positive_on_web_edit():
    """Régression : éditer le SITE web d'un site IONOS configuré ne doit PAS
    être routé vers le bridge BDD.

    Avant le fix : « table » matchait « tableau »/« comptable » en sous-chaîne,
    et le simple nom du site configuré armait le hard-block — verrouillant
    fichier/shell/CodeAgent dès qu'on éditait le site openlumena.
    """
    from src.reasoning.tool_registry import _looks_like_ionos_db_intent

    sites = ("openlumena.com",)

    # Faux positifs corrigés (édition web / dev → PAS BDD)
    for q in [
        "cree un tableau de bord sur openlumena",
        "ajoute un tableau recapitulatif sur la page openlumena",
        "corrige le bug comptable sur openlumena",
        "modifie la table des prix dans index.html de openlumena",
        "change le fichier style.css de openlumena",
        "deploie openlumena",
    ]:
        assert not _looks_like_ionos_db_intent(q, configured_sites=sites), q

    # Vraies intentions BDD préservées
    for q in [
        "ajoute une table mysql sur openlumena",
        "montre la base de donnees ionos",
        "cree une nouvelle table dans la bdd openlumena",
        # terme BDD fort + fichier nommé → reste une intention BDD
        "mets le resultat de la table mysql dans rapport.html sur openlumena",
    ]:
        assert _looks_like_ionos_db_intent(q, configured_sites=sites), q


@pytest.mark.asyncio
async def test_ionos_bdd_context_hard_blocks_file_shell_and_agents(_registry):
    from src.reasoning.caller_context import REACT

    _registry.clear_context_filter()
    _registry.apply_context_filter(
        "rajoute une table test a la bdd sur ionos du site opelumena",
        intent="react",
    )

    blocked_calls = [
        ("find_files", {"pattern": "config*", "path": r"C:\Users\user\Desktop\lumena\workspace\openlumena"}),
        ("read_file", {"path": r"C:\Users\user\Desktop\lumena\workspace\openlumena\api\config.php"}),
        ("run_command", {"command": "node create_table.js"}),
        ("delegate_task", {
            "agent_type": "code",
            "description": "Créer un script PHP pour ajouter une table test à la BDD IONOS openlumena.",
            "project_path": r"C:\Users\user\Desktop\lumena\workspace\openlumena",
        }),
    ]
    for name, args in blocked_calls:
        obs = await _registry.execute(name, args, caller=REACT)
        assert not obs.success, name
        assert "ionos_db_" in obs.content.lower(), name
        assert "bridge" in obs.content.lower(), name


@pytest.mark.asyncio
async def test_ionos_config_path_hard_blocks_without_context_query(_registry, monkeypatch):
    from src.reasoning.caller_context import REACT

    monkeypatch.setenv("LUMENA_IONOS_DEFAULT_SITE", "openlumena.com")
    _registry.clear_context_filter()
    config_path = (
        _registry.lumena_root / "workspace" / "openlumena" / "api" / "config.local.php"
    )
    obs = await _registry.execute(
        "read_file",
        {"path": str(config_path)},
        caller=REACT,
    )

    assert not obs.success
    assert "ionos_db_" in obs.content.lower()
    assert "bridge" in obs.content.lower()


def test_ionos_readonly_tools_are_known_readonly():
    """Les outils BDD IONOS read-only n'exigent pas de mutation (ledger guard exonéré)."""
    from src.reasoning.plan_evidence import tool_capabilities_are_known_readonly
    for tool in ("ionos_db_get_config", "ionos_test_site_database", "ionos_db_bridge_status",
                 "ionos_db_list_tables", "ionos_db_select", "ionos_db_list_snapshots",
                 "ionos_db_list_pending_actions", "ionos_db_get_write_config"):
        assert tool_capabilities_are_known_readonly(tool, "ionos", "") is True, tool
    # mutations/proposals NE sont PAS read-only
    for tool in ("ionos_db_create_sandbox_table", "ionos_db_propose_write", "deploy_to_ionos"):
        assert tool_capabilities_are_known_readonly(tool, "ionos", "") is False, tool


def test_ionos_readonly_bridge_tool_not_hardblocked(_registry):
    """En contexte BDD IONOS, un outil bridge read-only n'est PAS hard-block."""
    from src.reasoning.caller_context import REACT
    _registry.clear_context_filter()
    _registry.apply_context_filter("rajoute une table test a la bdd sur ionos du site opelumena", intent="react")
    # un outil ionos_db_* passe la policy (refusal None)
    assert _registry._ionos_db_context_refusal("ionos_db_get_config", {}, REACT) is None
    assert _registry._ionos_db_context_refusal("ionos_test_site_database", {}, REACT) is None
    # final_answer toujours autorisé
    assert _registry._ionos_db_context_refusal("final_answer", {}, REACT) is None
    # mais un outil fichier reste bloqué
    assert _registry._ionos_db_context_refusal("read_file", {"path": "config.php"}, REACT) is not None


def test_ionos_hardblock_escalates_on_second_attempt(_registry):
    """Le 2e hard-block du même intent escalade le message (anti-boucle de contournement)."""
    from src.reasoning.caller_context import REACT
    _registry.clear_context_filter()
    _registry.apply_context_filter("lis config.local.php pour la bdd ionos openlumena", intent="react")
    first = _registry._ionos_db_context_refusal("read_file", {"path": "config.local.php"}, REACT)
    second = _registry._ionos_db_context_refusal("run_command", {"command": "powershell cat config.local.php"}, REACT)
    assert first is not None and second is not None
    assert "final_answer" in second.content.lower()
    # marqueur d'escalade présent au 2e blocage, absent au 1er
    assert "2e tentative" in second.content.lower() and "arr" in second.content.lower()
    assert "2e tentative" not in first.content.lower()
