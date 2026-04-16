"""Tests pour le filtrage contextuel des outils (Phase 1)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import ToolRegistry


def _make_registry() -> ToolRegistry:
    """Crée un ToolRegistry minimal avec des outils de test dans plusieurs catégories."""
    reg = object.__new__(ToolRegistry)
    reg.tools = {}
    reg._tool_modules = {}
    reg._allowed_tools = None
    reg._caller_set_allowed = False
    reg._tools_desc_cache = None

    # Simuler des outils dans différentes catégories
    _categories = {
        "system": ["final_answer", "ask_user", "get_time", "run_command"],
        "files": ["read_file", "write_file", "list_directory"],
        "web": ["web_search", "fetch_url"],
        "memory": ["memory_search", "memory_store"],
        "browser": ["open_chrome", "navigate_to", "click_element"],
        "computer_use": ["mouse_click", "keyboard_type", "screenshot"],
        "mail": ["send_mail", "read_inbox"],
        "spotify": ["play_song", "search_music"],
        "notion": ["create_notion_page", "query_notion_db"],
        "project": ["create_project", "edit_project"],
        "website": ["generate_website", "edit_website"],
        "git": ["git_commit", "git_push", "git_status"],
        "github": ["create_pr", "list_issues"],
        "autonomy": ["plan_create", "plan_update", "schedule_task"],
        "security": ["osint_scan", "security_audit"],
        "discord": ["send_discord_msg", "manage_discord_role"],
        "network": ["ping_host", "scan_ports"],
        "ide": ["ide_open_file", "ide_navigate"],
        "codebase": ["search_code", "analyze_deps"],
        "lsp": ["lsp_symbols", "lsp_hover"],
        "skills": ["install_skill", "list_skills"],
        "agents": ["delegate_agent", "list_agents"],
        "documents": ["ocr_image", "extract_pdf"],
        "custom": ["custom_handler_1"],
    }

    for cat, tool_names in _categories.items():
        for name in tool_names:
            reg.tools[name] = {
                "name": name,
                "description": f"Test tool {name}",
                "parameters": {},
            }
            reg._tool_modules[name] = cat

    return reg


# ── Tests de matching ──────────────────────────────────────────────────


def test_mail_query_filters_to_mail_tools():
    """Requête mail → seulement outils mail, files, system + always-include."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail à jean@test.com")
    assert reg._allowed_tools is not None
    assert "send_mail" in reg._allowed_tools
    assert "read_inbox" in reg._allowed_tools
    assert "read_file" in reg._allowed_tools  # files inclus
    assert "final_answer" in reg._allowed_tools  # always
    # Pas de browser ni spotify
    assert "open_chrome" not in reg._allowed_tools
    assert "play_song" not in reg._allowed_tools


def test_browser_query_filters_correctly():
    """Requête browser → outils browser, web, system."""
    reg = _make_registry()
    reg.apply_context_filter("ouvre chrome et va sur google")
    assert "open_chrome" in reg._allowed_tools
    assert "web_search" in reg._allowed_tools
    assert "navigate_to" in reg._allowed_tools
    assert "send_mail" not in reg._allowed_tools
    assert "play_song" not in reg._allowed_tools


def test_project_query_filters_correctly():
    """Requête projet → outils project, website, files, system."""
    reg = _make_registry()
    reg.apply_context_filter("crée un site web portfolio avec react")
    assert "create_project" in reg._allowed_tools
    assert "generate_website" in reg._allowed_tools
    assert "write_file" in reg._allowed_tools
    assert "send_mail" not in reg._allowed_tools


def test_git_query_filters_correctly():
    """Requête git → outils git, files, system."""
    reg = _make_registry()
    reg.apply_context_filter("fais un commit et push sur la branche main")
    assert "git_commit" in reg._allowed_tools
    assert "git_push" in reg._allowed_tools
    assert "read_file" in reg._allowed_tools
    assert "play_song" not in reg._allowed_tools


def test_security_query_filters_correctly():
    """Requête sécurité → outils security, network, system."""
    reg = _make_registry()
    reg.apply_context_filter("lance un audit de sécurité sur le domaine")
    assert "osint_scan" in reg._allowed_tools
    assert "security_audit" in reg._allowed_tools
    assert "ping_host" in reg._allowed_tools  # network inclus
    assert "play_song" not in reg._allowed_tools


def test_spotify_query_filters_correctly():
    """Requête musique → outils spotify, system."""
    reg = _make_registry()
    reg.apply_context_filter("joue de la musique de Mozart")
    assert "play_song" in reg._allowed_tools
    assert "search_music" in reg._allowed_tools
    assert "send_mail" not in reg._allowed_tools
    assert "open_chrome" not in reg._allowed_tools


def test_memory_query_filters_correctly():
    """Requête mémoire → outils memory, system."""
    reg = _make_registry()
    reg.apply_context_filter("rappelle-toi de ma préférence pour le thé")
    assert "memory_search" in reg._allowed_tools
    assert "memory_store" in reg._allowed_tools


# ── Tests fallback ──────────────────────────────────────────────────────


def test_fallback_when_no_rule_matches():
    """Si aucune règle ne matche → fallback (files, system, web, memory)."""
    reg = _make_registry()
    reg.apply_context_filter("bonjour comment ça va")
    assert reg._allowed_tools is not None
    # Fallback categories
    assert "read_file" in reg._allowed_tools
    assert "web_search" in reg._allowed_tools
    assert "memory_search" in reg._allowed_tools
    assert "final_answer" in reg._allowed_tools
    # Pas de catégories spécialisées
    assert "play_song" not in reg._allowed_tools
    assert "open_chrome" not in reg._allowed_tools
    assert "send_mail" not in reg._allowed_tools


def test_empty_query_does_not_filter():
    """Requête vide → pas de filtre, tous les outils dispo."""
    reg = _make_registry()
    reg.apply_context_filter("")
    assert reg._allowed_tools is None  # Pas de filtre appliqué


# ── Tests always-include ────────────────────────────────────────────────


def test_final_answer_always_present():
    """final_answer doit toujours être présent peu importe le filtre."""
    queries = [
        "envoie un mail",
        "ouvre chrome",
        "crée un projet",
        "joue de la musique",
        "bonjour",
    ]
    for q in queries:
        reg = _make_registry()
        reg.apply_context_filter(q)
        assert "final_answer" in reg._allowed_tools, f"final_answer absent pour: {q}"
        assert "ask_user" in reg._allowed_tools, f"ask_user absent pour: {q}"


def test_plan_tools_always_present():
    """Les outils plan_* doivent toujours être présents."""
    reg = _make_registry()
    reg.apply_context_filter("ouvre spotify")
    assert "plan_create" in reg._allowed_tools
    assert "plan_update" in reg._allowed_tools


# ── Tests cache invalidation ───────────────────────────────────────────


def test_cache_invalidated_on_filter_change():
    """Le cache de description doit être invalidé quand le filtre change."""
    reg = _make_registry()
    # Premier appel → cache rempli
    desc1 = reg.get_tools_description()
    assert reg._tools_desc_cache is not None

    # Appliquer un filtre → cache invalidé
    reg.apply_context_filter("envoie un mail")
    assert reg._tools_desc_cache is None

    # Nouveau cache
    desc2 = reg.get_tools_description()
    assert len(desc2) < len(desc1)  # Moins d'outils → description plus courte


def test_cache_not_invalidated_if_same_filter():
    """Si le filtre produit les mêmes outils, le cache n'est pas invalidé."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    desc1 = reg.get_tools_description()
    old_cache = reg._tools_desc_cache

    # Re-appliquer le même filtre
    reg.apply_context_filter("envoie un mail")
    assert reg._tools_desc_cache is old_cache  # Même objet


# ── Tests clear_context_filter ──────────────────────────────────────────


def test_clear_context_filter_restores_all_tools():
    """clear_context_filter() doit restaurer tous les outils."""
    reg = _make_registry()
    total = len(reg.tools)
    desc_all = reg.get_tools_description()

    reg.apply_context_filter("envoie un mail")
    filtered_count = len(reg._allowed_tools)
    assert filtered_count < total

    reg.clear_context_filter()
    assert reg._allowed_tools is None
    desc_restored = reg.get_tools_description()
    assert desc_restored == desc_all


# ── Tests combinaisons ──────────────────────────────────────────────────


def test_multi_context_query_unions_categories():
    """Requête qui touche plusieurs domaines → union des catégories."""
    reg = _make_registry()
    reg.apply_context_filter("cherche sur le web et envoie le résultat par mail")
    assert "web_search" in reg._allowed_tools
    assert "send_mail" in reg._allowed_tools
    assert "read_file" in reg._allowed_tools  # files via mail rule


def test_tool_count_reduction():
    """Vérifier que le filtrage réduit significativement le nombre d'outils."""
    reg = _make_registry()
    total = len(reg.tools)

    reg.apply_context_filter("envoie un mail à jean")
    filtered = len(reg._allowed_tools)

    # Le filtrage doit réduire d'au moins 40%
    assert filtered < total * 0.6, (
        f"Filtrage insuffisant: {filtered}/{total} outils ({filtered/total:.0%})"
    )


def test_no_tool_modules_does_not_crash():
    """Si _tool_modules n'est pas peuplé, apply_context_filter ne crash pas."""
    reg = object.__new__(ToolRegistry)
    reg.tools = {"test": {"name": "test", "description": "x", "parameters": {}}}
    reg._tool_modules = {}
    reg._allowed_tools = None
    reg._caller_set_allowed = False
    reg._tools_desc_cache = None
    # Ne doit pas crasher
    reg.apply_context_filter("envoie un mail")
    # Pas de modules → pas de filtre
    assert reg._allowed_tools is None or len(reg._allowed_tools) == 0


def test_ide_query_includes_codebase_and_lsp():
    """Requête IDE → ide, lsp, codebase, files, system."""
    reg = _make_registry()
    reg.apply_context_filter("ouvre le fichier dans l'éditeur vscode")
    assert "ide_open_file" in reg._allowed_tools
    assert "lsp_symbols" in reg._allowed_tools
    assert "search_code" in reg._allowed_tools
    assert "read_file" in reg._allowed_tools
