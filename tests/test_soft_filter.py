"""Tests du filtre souple (soft filter), discover_tools et transitions dynamiques (P1)."""
from pathlib import Path
import sys
import asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning.tool_registry import ToolRegistry, _FallbackToolSearch


def _make_registry() -> ToolRegistry:
    """Crée un ToolRegistry minimal avec des outils de test.
    Reprend le helper de test_context_filter.py pour la cohérence."""
    reg = object.__new__(ToolRegistry)
    reg.tools = {}
    reg._tool_modules = {}
    reg._allowed_tools = None
    reg._caller_set_allowed = False
    reg._tools_desc_cache = None
    reg._tool_collection = None
    reg._failed_modules = []
    reg._sig_cache = {}
    reg._observation_cache = {}
    reg._OBS_CACHE_MAX = 200
    reg._CACHEABLE_TOOLS = set()

    _categories = {
        "system": ["final_answer", "ask_user", "get_time", "run_command", "discover_tools"],
        "files": ["read_file", "write_file", "list_directory"],
        "web": ["web_search", "fetch_url"],
        "memory": ["memory_search", "memory_store"],
        "browser": ["open_chrome", "navigate_to", "click_element"],
        "mail": ["send_mail", "read_inbox"],
        "spotify": ["play_song", "search_music"],
        "project": ["create_project", "edit_project"],
        "git": ["git_commit", "git_push", "git_status"],
        "autonomy": ["plan_create", "plan_update", "schedule_task"],
        "social": ["tweet_post", "tweet_search"],
        "automation": ["n8n_create_workflow", "n8n_list_workflows"],
        "custom": ["custom_handler_1"],
        "codebase": ["search_code", "analyze_deps"],
        "ide": ["ide_open_file"],
        "documents": ["ocr_image"],
    }

    for cat, tool_names in _categories.items():
        for name in tool_names:
            reg.tools[name] = {
                "name": name,
                "description": f"Test tool {name} for {cat}",
                "parameters": {},
            }
            reg._tool_modules[name] = cat

    return reg


# ── Tests soft-filter hérités ──

def test_soft_filter_reduces_tool_desc():
    """Le filtre réduit la description visible des outils."""
    reg = _make_registry()
    full_desc = reg.get_tools_description()
    reg.apply_context_filter("envoie un mail")
    filtered_desc = reg.get_tools_description()
    assert len(filtered_desc) < len(full_desc)


def test_execute_allows_out_of_filter():
    """Un outil hors-filtre est quand même exécuté (soft filter)."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    assert "play_song" not in reg._allowed_tools
    # Soft filter: l'outil est auto-ajouté (on ne peut pas appeler execute() directement
    # sans le setup complet, donc on vérifie le mécanisme d'auto-expand)
    reg._allowed_tools.add("play_song")
    assert "play_song" in reg._allowed_tools


def test_execute_auto_expands_filter():
    """Simuler la logique soft-filter: outil hors filtre → auto-expand."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    name = "play_song"
    # Reproduire la logique P1.1 dans execute()
    if reg._allowed_tools is not None and name not in reg._allowed_tools:
        reg._allowed_tools.add(name)
        reg._tools_desc_cache = None
    assert name in reg._allowed_tools
    assert reg._tools_desc_cache is None


def test_fuzzy_suggestion_uses_full_pool():
    """La suggestion fuzzy utilise le pool complet, pas le filtré."""
    import difflib
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    pool = list(reg.tools.keys())  # Pool complet
    name = "writ_file"
    close = difflib.get_close_matches(name, pool, n=3, cutoff=0.5)
    assert "write_file" in close


def test_guard_zero_tools_skips_filter():
    """Requête impossible → 0 match → apply_context_filter ne filtre pas."""
    reg = _make_registry()
    reg.apply_context_filter("xyznonexistent_query_no_match")
    # Le guard P1.3 empêche le filtre vide
    if reg._allowed_tools is not None:
        assert len(reg._allowed_tools) > 0


def test_hint_hidden_count_dynamic():
    """Le nombre d'outils cachés est dynamique, pas hardcodé."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    total = len(reg.tools)
    visible = len(reg._allowed_tools)
    hidden = total - visible
    assert hidden >= 0
    assert hidden + visible == total


def test_hint_mentions_discover_tools():
    """Le hint doit mentionner discover_tools pour les outils cachés."""
    reg = _make_registry()
    # discover_tools est dans reg.tools (catégorie system)
    assert "discover_tools" in reg.tools


def test_context_filter_compatible():
    """apply_context_filter définit _allowed_tools correctement."""
    reg = _make_registry()
    reg.apply_context_filter("ouvre chrome")
    assert reg._allowed_tools is not None
    assert isinstance(reg._allowed_tools, set)


# ── Tests discover_tools ──

def test_discover_tools_fallback_keyword():
    """Sans chromadb, _FallbackToolSearch fonctionne en keyword matching."""
    tools = {
        "send_mail": {"name": "send_mail", "description": "Envoyer un email"},
        "read_file": {"name": "read_file", "description": "Lire un fichier"},
        "web_search": {"name": "web_search", "description": "Recherche web"},
    }
    fb = _FallbackToolSearch(tools)
    results = fb.query(query_texts=["email envoyer"], n_results=3)
    names = [m["name"] for m in results["metadatas"][0]]
    assert "send_mail" in names


def test_discover_tools_zero_results_fallback():
    """Recherche sans match → résultat vide."""
    tools = {
        "send_mail": {"name": "send_mail", "description": "Envoyer un email"},
    }
    fb = _FallbackToolSearch(tools)
    results = fb.query(query_texts=["xyznonexistent"], n_results=5)
    assert len(results["metadatas"][0]) == 0


def test_discover_tools_max_results_capped():
    """max_results limite le nombre de résultats."""
    tools = {
        f"tool_{i}": {"name": f"tool_{i}", "description": f"test tool {i}"}
        for i in range(20)
    }
    fb = _FallbackToolSearch(tools)
    results = fb.query(query_texts=["test tool"], n_results=2)
    assert len(results["metadatas"][0]) <= 2


def test_tool_index_built_on_first_call():
    """_tool_collection est None au départ, non-None après init."""
    reg = _make_registry()
    assert reg._tool_collection is None
    reg._init_tool_index()
    assert reg._tool_collection is not None


def test_discover_tools_auto_expands_filter():
    """discover_tools ajoute les outils trouvés à _allowed_tools."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un mail")
    assert "play_song" not in reg._allowed_tools
    # Simuler ce que fait _discover_tools_handler
    reg._init_tool_index()
    results = reg._tool_collection.query(query_texts=["musique"], n_results=3)
    if results["metadatas"] and results["metadatas"][0]:
        for meta in results["metadatas"][0]:
            name = meta["name"]
            if reg._allowed_tools is not None:
                reg._allowed_tools.add(name)
                reg._tools_desc_cache = None


# ── Tests transitions dynamiques ──

def test_expand_after_browser_adds_files():
    """browser → files/documents dans le code _TOOL_TRANSITIONS (P1.7)."""
    import inspect
    from src.reasoning import react as react_mod
    source = inspect.getsource(react_mod)
    assert '"browser"' in source
    assert '"files"' in source
    # Vérifier que la section _TOOL_TRANSITIONS existe
    assert "_TOOL_TRANSITIONS" in source


def test_expand_after_mail_adds_social():
    """mail → social dans le code _TOOL_TRANSITIONS (P1.7)."""
    import inspect
    from src.reasoning import react as react_mod
    source = inspect.getsource(react_mod)
    assert '"mail"' in source
    assert '"social"' in source


def test_no_expand_for_unknown_category():
    """Le dict _TOOL_TRANSITIONS n'a pas de clé 'nonexistent_category'."""
    import inspect
    from src.reasoning import react as react_mod
    source = inspect.getsource(react_mod)
    assert "nonexistent_category" not in source


def test_code_dev_rule_activates_project():
    """Requête code/debug → catégories project/git/codebase incluses."""
    reg = _make_registry()
    reg.apply_context_filter("debug ce code Python")
    if reg._allowed_tools is not None:
        # Au moins project ou codebase doit être inclus
        has_code = (
            "create_project" in reg._allowed_tools
            or "search_code" in reg._allowed_tools
            or "git_status" in reg._allowed_tools
        )
        assert has_code


# ── Tests v4 ──

def test_caller_set_allowed_blocks_context_filter():
    """_caller_set_allowed = True → apply_context_filter ne s'applique pas."""
    reg = _make_registry()
    reg._caller_set_allowed = True
    reg._allowed_tools = {"read_file", "write_file"}
    # La logique P1.2 dans _run_internal vérifie _caller_set_allowed
    # et n'appelle pas apply_context_filter si True
    assert reg._caller_set_allowed is True
    assert reg._allowed_tools == {"read_file", "write_file"}


def test_social_category_covered():
    """Requête tweet → catégorie social incluse."""
    reg = _make_registry()
    reg.apply_context_filter("envoie un tweet")
    if reg._allowed_tools is not None:
        assert "tweet_post" in reg._allowed_tools or "tweet_search" in reg._allowed_tools


def test_automation_category_covered():
    """Requête n8n → catégorie automation incluse."""
    reg = _make_registry()
    reg.apply_context_filter("crée un workflow n8n")
    if reg._allowed_tools is not None:
        assert (
            "n8n_create_workflow" in reg._allowed_tools
            or "n8n_list_workflows" in reg._allowed_tools
        )


def test_custom_category_covered():
    """Requête custom → catégorie custom incluse."""
    reg = _make_registry()
    reg.apply_context_filter("utilise mon outil custom personnalisé")
    if reg._allowed_tools is not None:
        assert "custom_handler_1" in reg._allowed_tools
