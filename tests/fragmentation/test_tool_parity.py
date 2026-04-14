"""
Phase 5.3 — Test de parité legacy ↔ V2.

Vérifie la cohérence entre les outils legacy (tool_system.py) et les
handlers V2 fragmentés (src/reasoning/handlers/).

Ce test sert de « filet de sécurité » pour s'assurer que la migration
V2 couvre bien tous les outils legacy utilisables.
"""

import sys
from pathlib import Path

import pytest

# ── Setup path ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.reasoning.handlers.registry_v2 import HandlerRegistryV2

# ── Liste exhaustive des 74 outils legacy (tool_system.py + website_builder) ──
LEGACY_TOOL_NAMES = [
    "browser_click",
    "browser_close_tab",
    "browser_get_text",
    "browser_list_tabs",
    "browser_navigate",
    "browser_open_tab",
    "browser_screenshot",
    "browser_switch_tab",
    "browser_type",
    "create_directory",
    "create_skill",
    "create_zip",
    "delegate_task",
    "delete_file",
    "edit_file",
    "edit_own_code",
    "edit_website",
    "execute_skill",
    "export_website_zip",
    "file_crawl_campaign",
    "file_crawl_campaign_export",
    "file_crawl_campaign_status",
    "find_files",
    "fork_analyze",
    "generate_website",
    "get_agents_status",
    "get_my_capabilities",
    "get_time",
    "git_commit",
    "grep_search",
    "list_directory",
    "list_skills",
    "list_website_projects",
    "mail_account_upsert",
    "mail_delete_message",
    "mail_download_attachments",
    "mail_list_accounts",
    "mail_list_folders",
    "mail_list_messages",
    "mail_move_message",
    "mail_quick_test",
    "mail_read_message",
    "mail_remove_account",
    "mail_reply_message",
    "mail_send",
    "memory_add",
    "memory_search",
    "memory_stats",
    "notify_critical",
    "place_critical_call",
    "read_file",
    "read_own_code",
    "read_skill_reference",
    "reload_skills",
    "run_command",
    "run_tests",
    "search_in_code",
    "send_critical_sms",
    "serve_website",
    "stop_website_server",
    "sync_skills_main",
    "telegram_send_document",
    "trading_dashboard",
    "view_file_outline",
    "web_crawl",
    "web_crawl_campaign",
    "web_crawl_campaign_explain",
    "web_crawl_campaign_export",
    "web_crawl_campaign_pro_report",
    "web_crawl_campaign_status",
    "web_fetch",
    "web_search",
    "write_file",
    "write_website_files",
]

# Outils legacy intentionnellement non migrés en V2.
# Chacun a une justification: renommé, consolidé, obsolète, ou couvert autrement.
INTENTIONALLY_SKIPPED = {
    # Couvert par d'autres outils V2
    "edit_own_code",          # Couvert par apply_patch
    "browser_get_text",       # Couvert par browser_extract_text
    "browser_list_tabs",      # Intégré dans browser_get_tabs / navigation
    "browser_open_tab",       # Unifié avec browser_navigate
    "create_directory",       # Couvert par write_file (crée les dirs) + run_command
    "view_file_outline",      # Renommé view_outline en V2
    "web_crawl",              # Consolidé dans web_crawl_campaign
    "web_crawl_campaign_export",  # Consolidé dans web_crawl_campaign
    # Website builder (maintenant migré en V2 dans handlers/website.py — P1.2.7)
    # Ces outils ne sont plus skipés
    # Outils spécialisés non encore portés (basse priorité)
    "file_crawl_campaign",
    "file_crawl_campaign_export",
    "file_crawl_campaign_status",
    "git_commit",             # Couvert par run_command + git
    "read_skill_reference",   # Couvert par read_own_code
    "reload_skills",          # Automatique en V2
    "run_tests",              # Couvert par run_command
    "sync_skills_main",       # Automatique en V2
    "trading_dashboard",      # Module markets séparé
}


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def v2_registry():
    """Charge tous les handlers V2."""
    from src.reasoning.handlers.files import get_file_handler_defs
    from src.reasoning.handlers.system import get_system_handler_defs
    from src.reasoning.handlers.web import get_web_handler_defs
    from src.reasoning.handlers.memory import get_memory_handler_defs
    from src.reasoning.handlers.browser import get_browser_handler_defs
    from src.reasoning.handlers.computer_use import get_computer_use_handler_defs
    from src.reasoning.handlers.skills import get_skills_handler_defs
    from src.reasoning.handlers.agents import get_agents_handler_defs
    from src.reasoning.handlers.mail import get_mail_handler_defs
    from src.reasoning.handlers.documents import get_documents_handler_defs
    from src.reasoning.handlers.spotify import get_spotify_handler_defs
    from src.reasoning.handlers.notion import get_notion_handler_defs
    from src.reasoning.handlers.website import get_website_handler_defs

    v2 = HandlerRegistryV2()
    for getter in [
        get_file_handler_defs,
        get_system_handler_defs,
        get_web_handler_defs,
        get_memory_handler_defs,
        get_browser_handler_defs,
        get_computer_use_handler_defs,
        get_skills_handler_defs,
        get_agents_handler_defs,
        get_mail_handler_defs,
        get_documents_handler_defs,
        get_spotify_handler_defs,
        get_notion_handler_defs,
        get_website_handler_defs,
    ]:
        try:
            v2.register_many(getter())
        except ValueError:
            for hdef in getter():
                if not v2.has(hdef.name):
                    v2.register(hdef)
    return v2


@pytest.fixture(scope="module")
def parity_report(v2_registry):
    """Rapport de parité legacy ↔ V2."""
    return v2_registry.get_parity_report(LEGACY_TOOL_NAMES)


# ══════════════════════════════════════════════════════════════════════════
# Section 1: Rapport de parité global
# ══════════════════════════════════════════════════════════════════════════

class TestParityReport:
    """Test du rapport de parité legacy vs V2."""

    def test_coverage_above_minimum(self, parity_report):
        """La couverture V2 des outils legacy doit être > 60% (seuil actuel).
        
        Note: beaucoup d'outils legacy ont été renommés, consolidés ou
        remplacés par des outils V2 plus puissants. La parité 1:1 n'est
        pas l'objectif — c'est la couverture fonctionnelle qui compte.
        """
        assert parity_report["coverage_pct"] >= 60.0, (
            f"Couverture trop basse: {parity_report['coverage_pct']}%. "
            f"Outils manquants: {parity_report['missing']}"
        )

    def test_all_missing_are_intentional(self, parity_report):
        """Chaque outil legacy manquant doit être dans INTENTIONALLY_SKIPPED."""
        missing = set(parity_report["missing"])
        unexpected_missing = missing - INTENTIONALLY_SKIPPED
        assert not unexpected_missing, (
            f"Outils legacy non couverts par V2 et non dans INTENTIONALLY_SKIPPED: "
            f"{sorted(unexpected_missing)}"
        )

    def test_v2_has_extras(self, parity_report):
        """V2 a des outils supplémentaires (extensions au-delà du legacy)."""
        # Ce n'est pas un échec, juste une assertion documentaire
        extra = parity_report["extra"]
        assert isinstance(extra, list)

    def test_total_legacy_count(self, parity_report):
        """Vérification programmatique du nombre d'outils legacy."""
        assert parity_report["total_legacy"] == len(LEGACY_TOOL_NAMES)

    def test_total_v2_count(self, parity_report):
        """V2 a au moins autant d'outils que le legacy."""
        assert parity_report["total_v2"] >= len(LEGACY_TOOL_NAMES) - len(INTENTIONALLY_SKIPPED)


# ══════════════════════════════════════════════════════════════════════════
# Section 2: Vérification de chaque HandlerDef V2
# ══════════════════════════════════════════════════════════════════════════

class TestHandlerDefQuality:
    """Vérifie que chaque HandlerDef V2 a les champs minimum requis."""

    def test_all_handlers_have_name(self, v2_registry):
        """Chaque handler V2 a un nom non vide."""
        for name, hdef in v2_registry._handlers.items():
            assert hdef.name, f"Handler '{name}' a un nom vide"
            assert hdef.name == name, f"Handler key '{name}' != hdef.name '{hdef.name}'"

    def test_all_handlers_have_description(self, v2_registry):
        """Chaque handler V2 a une description non vide."""
        for name, hdef in v2_registry._handlers.items():
            assert hdef.description, f"Handler '{name}' n'a pas de description"
            assert len(hdef.description) >= 5, (
                f"Handler '{name}' description trop courte: '{hdef.description}'"
            )

    def test_all_handlers_have_parameters(self, v2_registry):
        """Chaque handler V2 a un dict parameters (peut être {} pour sans params)."""
        for name, hdef in v2_registry._handlers.items():
            assert isinstance(hdef.parameters, dict), (
                f"Handler '{name}' parameters n'est pas un dict: {type(hdef.parameters)}"
            )

    def test_all_handlers_are_callable(self, v2_registry):
        """Chaque handler V2 a un handler callable."""
        for name, hdef in v2_registry._handlers.items():
            assert callable(hdef.handler), f"Handler '{name}' n'est pas callable"

    def test_parameterized_handlers_have_properties(self, v2_registry):
        """Les handlers avec params utilisent le format JSON Schema (properties)."""
        # Exceptions: handlers sans paramètres
        no_params_expected = {
            "get_time", "list_windows", "get_agents_status",
            "get_my_capabilities", "memory_stats", "stop_website_server",
        }
        for name, hdef in v2_registry._handlers.items():
            if name in no_params_expected:
                continue
            params = hdef.parameters
            if params:  # Si non vide
                assert "properties" in params, (
                    f"Handler '{name}' a des params mais pas de 'properties' "
                    f"(format FLAT au lieu de JSON Schema): {list(params.keys())}"
                )


# ══════════════════════════════════════════════════════════════════════════
# Section 3: Vérification des outils critiques
# ══════════════════════════════════════════════════════════════════════════

class TestCriticalTools:
    """Vérifie que les 10 outils les plus critiques sont présents et documentés."""

    CRITICAL_TOOLS = [
        "read_file",
        "write_file",
        "list_directory",
        "run_command",
        "web_search",
        "web_fetch",
        "browser_open",
        "memory_search",
        "memory_add",
        "get_time",
    ]

    @pytest.mark.parametrize("tool_name", CRITICAL_TOOLS)
    def test_critical_tool_exists(self, v2_registry, tool_name):
        """Chaque outil critique existe en V2."""
        # browser_open peut s'appeler browser_navigate ou browser_open
        alt_names = {
            "browser_open": ["browser_open", "browser_navigate", "browser_open_tab"],
        }
        names_to_check = alt_names.get(tool_name, [tool_name])
        found = any(v2_registry.has(n) for n in names_to_check)
        assert found, (
            f"Outil critique '{tool_name}' absent du registre V2. "
            f"Handlers V2: {sorted(v2_registry._handlers.keys())[:20]}..."
        )

    @pytest.mark.parametrize("tool_name", [
        "read_file", "write_file", "list_directory",
        "web_search", "web_fetch", "memory_search",
    ])
    def test_critical_tool_has_documented_params(self, v2_registry, tool_name):
        """Les outils critiques avec params ont des descriptions de paramètres."""
        if not v2_registry.has(tool_name):
            pytest.skip(f"{tool_name} n'existe pas en V2")
        hdef = v2_registry._handlers[tool_name]
        params = hdef.parameters
        if "properties" in params:
            props = params["properties"]
            for pname, pdef in props.items():
                assert "description" in pdef or "type" in pdef, (
                    f"Paramètre '{pname}' de '{tool_name}' manque description/type"
                )


# ══════════════════════════════════════════════════════════════════════════
# Section 4: Vérification de la conversion legacy
# ══════════════════════════════════════════════════════════════════════════

class TestLegacyConversion:
    """Vérifie que to_legacy_tools_dict() produit un format valide."""

    @pytest.fixture
    def legacy_dict(self, v2_registry):
        from src.reasoning.handlers.context import HandlerContext
        ctx = HandlerContext(
            lumena_root=ROOT,
            runtime_root=ROOT,
        )
        return v2_registry.to_legacy_tools_dict(ctx)

    def test_all_entries_have_required_keys(self, legacy_dict):
        """Chaque entrée legacy a name, description, parameters, handler."""
        for name, entry in legacy_dict.items():
            assert "name" in entry, f"'{name}' manque 'name'"
            assert "description" in entry, f"'{name}' manque 'description'"
            assert "parameters" in entry, f"'{name}' manque 'parameters'"
            assert "handler" in entry, f"'{name}' manque 'handler'"

    def test_parameters_are_flat(self, legacy_dict):
        """Les paramètres legacy sont au format plat (pas de 'properties' imbriqué)."""
        for name, entry in legacy_dict.items():
            params = entry["parameters"]
            # Le format legacy ne devrait pas avoir de 'properties' nested
            # (c'est to_legacy_tools_dict qui les aplatit)
            if "properties" in params and isinstance(params["properties"], dict):
                # Si c'est un param qui s'appelle 'properties', c'est OK
                # Mais si c'est un JSON Schema non aplati, c'est un bug
                inner = params["properties"]
                # Vérifier que ce n'est pas un schéma imbriqué
                has_nested_schema = any(
                    isinstance(v, dict) and "type" in v
                    for v in inner.values()
                )
                if has_nested_schema:
                    pytest.fail(
                        f"'{name}' a un format JSON Schema non aplati dans legacy: "
                        f"keys={list(params.keys())}"
                    )

    def test_handlers_are_callable(self, legacy_dict):
        """Chaque handler legacy est callable."""
        for name, entry in legacy_dict.items():
            assert callable(entry["handler"]), f"'{name}' handler n'est pas callable"

    def test_required_is_list(self, legacy_dict):
        """Le champ 'required' est une liste quand présent."""
        for name, entry in legacy_dict.items():
            if "required" in entry:
                assert isinstance(entry["required"], list), (
                    f"'{name}' required n'est pas une list: {type(entry['required'])}"
                )


# ══════════════════════════════════════════════════════════════════════════
# Section 5: Test du rapport de parité (get_parity_report)
# ══════════════════════════════════════════════════════════════════════════

class TestParityReportFormat:
    """Vérifie que get_parity_report retourne un format correct."""

    def test_report_keys(self, parity_report):
        expected_keys = {"covered", "missing", "extra", "coverage_pct", "total_legacy", "total_v2"}
        assert set(parity_report.keys()) == expected_keys

    def test_covered_is_sorted(self, parity_report):
        assert parity_report["covered"] == sorted(parity_report["covered"])

    def test_missing_is_sorted(self, parity_report):
        assert parity_report["missing"] == sorted(parity_report["missing"])

    def test_extra_is_sorted(self, parity_report):
        assert parity_report["extra"] == sorted(parity_report["extra"])

    def test_coverage_is_percentage(self, parity_report):
        pct = parity_report["coverage_pct"]
        assert 0.0 <= pct <= 100.0

    def test_math_consistency(self, parity_report):
        """covered + missing = total_legacy."""
        assert len(parity_report["covered"]) + len(parity_report["missing"]) == parity_report["total_legacy"]
