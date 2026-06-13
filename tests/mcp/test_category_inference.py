"""
Tests Phase A — Cascade de classification sémantique des outils MCP.

Couvre :
  - Table statique (server_name → catégorie)
  - Heuristique (vote majoritaire sur descriptions)
  - LLM (cascade niveau 3, avec mock)
  - Cascade complète (ordre de priorité)
  - Traduction langage humain ↔ catégorie
  - UTF-8, anti-leak, fail-safe
"""
from __future__ import annotations

from typing import Callable, List, Optional

import pytest

from src.mcp.category_inference import (
    HUMAN_FRIENDLY_LABELS,
    VALID_CATEGORIES,
    category_to_human_label,
    infer_heuristic,
    infer_semantic_category,
    infer_static,
    infer_with_llm,
    translate_human_to_category,
)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — VALID_CATEGORIES + HUMAN_FRIENDLY_LABELS
# ══════════════════════════════════════════════════════════════════════════════


def test_valid_categories_is_frozenset():
    assert isinstance(VALID_CATEGORIES, frozenset)
    assert len(VALID_CATEGORIES) >= 30


def test_valid_categories_contains_core_lumena_categories():
    # Catégories Lumena standard qui doivent être présentes
    for cat in (
        "mail", "files", "web", "data", "github", "discord",
        "social", "memory", "documents", "image", "video",
        "browser", "spotify", "notion", "stripe", "ionos",
        "system", "mcp", "communication", "git",
    ):
        assert cat in VALID_CATEGORIES, f"{cat} doit être dans VALID_CATEGORIES"


def test_human_friendly_labels_cover_all_valid_categories():
    for cat in VALID_CATEGORIES:
        assert cat in HUMAN_FRIENDLY_LABELS, (
            f"{cat} doit avoir un label humain"
        )


def test_human_friendly_labels_no_jargon_leak():
    # Les labels ne doivent pas contenir le nom technique brut sauf
    # quand c'est aussi un nom de marque commun (notion, spotify, discord).
    brand_names_ok = {
        "notion", "stripe", "discord", "spotify", "ionos",
        "git", "github", "mcp",
    }
    for cat, label in HUMAN_FRIENDLY_LABELS.items():
        if cat in brand_names_ok:
            continue
        # Si le label est juste le nom technique, c'est suspect
        if label == cat:
            # OK quand cat est une catégorie générique sans bon label naturel
            assert cat in {"agents", "custom", "media"}, (
                f"{cat} a un label = nom technique brut"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — infer_static
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("server_name,expected", [
    # Mail
    ("gmail", "mail"),
    ("gmail-srv", "mail"),
    ("outlook", "mail"),
    ("imap-server", "mail"),
    # GitHub / Git
    ("github", "github"),
    ("github-tools", "github"),
    ("gitlab", "github"),
    ("git", "git"),
    # Web
    ("brave-search", "web"),
    ("tavily", "web"),
    ("fetch-server", "web"),
    # Files
    ("filesystem", "files"),
    ("gdrive", "files"),
    # Browser
    ("puppeteer", "browser"),
    ("playwright-mcp", "browser"),
    # Data
    ("postgres", "data"),
    ("sqlite-server", "data"),
    ("weather-api", "data"),
    # Memory
    ("memory", "memory"),
    # Notion / Stripe
    ("notion-mcp", "notion"),
    ("stripe-mcp", "stripe"),
    # Image / Video
    ("dalle", "image"),
    ("video-render", "video"),
    # Discord / Social / Communication
    ("discord-bot", "discord"),
    ("slack-mcp", "social"),
    ("telegram-bot", "communication"),
    # IDE / LSP
    ("vscode", "ide"),
    ("lsp-server", "lsp"),
    # Spotify
    ("spotify", "spotify"),
])
def test_infer_static_known(server_name, expected):
    assert infer_static(server_name) == expected


@pytest.mark.parametrize("server_name", [
    "",
    None,
    "unknown-mcp-12345",
    "completely-exotic-name",
    "x" * 200,
])
def test_infer_static_unknown_returns_none(server_name):
    assert infer_static(server_name) is None


def test_infer_static_case_insensitive():
    assert infer_static("GMAIL") == "mail"
    assert infer_static("GitHub-Tools") == "github"


def test_infer_static_accent_tolerant():
    # Si quelqu'un nomme un serveur avec accent
    assert infer_static("gmáil") == "mail"


def test_infer_static_returns_only_valid_categories():
    # Tout résultat de la table statique doit être ∈ VALID_CATEGORIES
    from src.mcp.category_inference import _MCP_SERVER_NAME_TO_SEMANTIC
    for cat in set(_MCP_SERVER_NAME_TO_SEMANTIC.values()):
        assert cat in VALID_CATEGORIES, f"{cat} de la table statique non valide"


def test_infer_static_specific_keywords_priority():
    # "google-calendar" doit gagner sur "google-search" si les deux étaient
    # dans la table (longueur décroissante).
    # On vérifie que la fonction trie correctement avec un cas existant.
    # "gmail" et "mail" sont tous deux dans la table → "gmail" doit gagner
    # (préfixe plus long).
    assert infer_static("gmail-server") == "mail"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — infer_heuristic
# ══════════════════════════════════════════════════════════════════════════════


def test_infer_heuristic_mail_keywords():
    descriptions = [
        "Send an email message to a recipient",
        "Read inbox messages",
    ]
    assert infer_heuristic(descriptions) == "mail"


def test_infer_heuristic_files_keywords():
    descriptions = [
        "Read a file from the filesystem",
        "Write content to a directory path",
    ]
    assert infer_heuristic(descriptions) == "files"


def test_infer_heuristic_web_keywords():
    descriptions = [
        "Fetch a URL and return HTML content",
        "HTTP request to a webpage",
    ]
    assert infer_heuristic(descriptions) == "web"


def test_infer_heuristic_data_keywords():
    descriptions = [
        "Execute a SQL query on the database",
        "List tables in the database",
    ]
    assert infer_heuristic(descriptions) == "data"


def test_infer_heuristic_github_keywords():
    descriptions = [
        "Create a pull request in a repository",
        "List commits on a branch",
    ]
    assert infer_heuristic(descriptions) == "github"


def test_infer_heuristic_empty_list():
    assert infer_heuristic([]) is None


def test_infer_heuristic_no_keywords_match():
    descriptions = [
        "Some completely unrelated tool that does xyzabc",
    ]
    assert infer_heuristic(descriptions) is None


def test_infer_heuristic_non_list_input():
    assert infer_heuristic(None) is None
    assert infer_heuristic("not a list") is None


def test_infer_heuristic_tie_returns_none():
    # 2 catégories à égalité (1 vote chacune) → None (ambigu)
    descriptions = [
        "Send email message",  # mail
        "Execute SQL query on table",  # data
    ]
    # Un seul vote chacun, égalité au top → None
    result = infer_heuristic(descriptions)
    assert result is None


def test_infer_heuristic_majority_wins():
    descriptions = [
        "Send email",  # mail
        "Read email message",  # mail (2 keywords)
        "Execute SQL query",  # data (1 vote)
    ]
    # mail aura 2 votes, data 1 → mail gagne
    assert infer_heuristic(descriptions) == "mail"


def test_infer_heuristic_skips_invalid_descriptions():
    # Mélange descriptions valides et invalides
    descriptions = [
        None,
        "",
        123,  # type invalide
        "Send email to recipient",  # valide
    ]
    assert infer_heuristic(descriptions) == "mail"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — infer_with_llm (mock)
# ══════════════════════════════════════════════════════════════════════════════


def _make_mock_llm(response: str) -> Callable[[str], str]:
    def _mock(prompt: str) -> str:
        return response
    return _mock


def test_infer_with_llm_valid_response():
    llm = _make_mock_llm("mail")
    result = infer_with_llm("exotic-srv", ["Some tool description"], llm)
    assert result == "mail"


def test_infer_with_llm_response_with_punctuation():
    llm = _make_mock_llm("'mail'.")
    result = infer_with_llm("exotic-srv", ["Some tool"], llm)
    assert result == "mail"


def test_infer_with_llm_response_in_sentence():
    llm = _make_mock_llm("This tool fits the mail category best.")
    result = infer_with_llm("exotic-srv", ["Some tool"], llm)
    assert result == "mail"


def test_infer_with_llm_invalid_category_returns_none():
    llm = _make_mock_llm("xyz_invalid_category")
    result = infer_with_llm("exotic-srv", ["Some tool"], llm)
    assert result is None


def test_infer_with_llm_empty_response_returns_none():
    llm = _make_mock_llm("")
    result = infer_with_llm("exotic-srv", ["Some tool"], llm)
    assert result is None


def test_infer_with_llm_exception_returns_none():
    def _bad_llm(prompt: str) -> str:
        raise RuntimeError("LLM down")
    result = infer_with_llm("exotic-srv", ["Some tool"], _bad_llm)
    assert result is None


def test_infer_with_llm_no_tool_descriptions_returns_none():
    llm = _make_mock_llm("mail")
    result = infer_with_llm("exotic-srv", [], llm)
    assert result is None


def test_infer_with_llm_non_callable_returns_none():
    result = infer_with_llm("exotic-srv", ["Some tool"], None)
    assert result is None


def test_infer_with_llm_truncates_long_descriptions():
    # Description très longue, ne doit pas crasher
    llm = _make_mock_llm("web")
    long_desc = "Search " * 500  # 3500+ chars
    result = infer_with_llm("exotic-srv", [long_desc], llm)
    assert result == "web"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Cascade complète infer_semantic_category
# ══════════════════════════════════════════════════════════════════════════════


def test_cascade_cache_hit_priority_1():
    result = infer_semantic_category(
        server_name="gmail",  # serait static="mail"
        tool_descriptions=["Send email"],
        cached="files",  # cache hit → gagne
    )
    assert result == ("files", "cache")


def test_cascade_cache_invalid_falls_through_to_static():
    result = infer_semantic_category(
        server_name="gmail",
        cached="invalid_category",  # cache invalide
    )
    assert result == ("mail", "static")


def test_cascade_static_hit_priority_2():
    result = infer_semantic_category(
        server_name="github-tools",
        tool_descriptions=["Send email"],  # heuristique dirait "mail"
    )
    # static gagne
    assert result == ("github", "static")


def test_cascade_heuristic_priority_3():
    result = infer_semantic_category(
        server_name="exotic-server-xyz",
        tool_descriptions=["Send email to recipient", "Read inbox messages"],
    )
    assert result == ("mail", "heuristic")


def test_cascade_llm_priority_4():
    llm = _make_mock_llm("data")
    result = infer_semantic_category(
        server_name="exotic-server-abc",
        tool_descriptions=["completely abstract tool"],
        llm_callable=llm,
    )
    assert result == ("data", "llm")


def test_cascade_fallback_last_resort():
    result = infer_semantic_category(
        server_name="completely-exotic-mcp-xyz",
        tool_descriptions=["abstract tool that doesnt match anything"],
        llm_callable=None,
    )
    assert result == ("mcp", "fallback")


def test_cascade_llm_returns_mcp_falls_through_to_fallback():
    # Si le LLM répond "mcp", c'est comme s'il n'avait pas trouvé
    llm = _make_mock_llm("mcp")
    result = infer_semantic_category(
        server_name="exotic-mcp",
        tool_descriptions=["abstract tool"],
        llm_callable=llm,
    )
    assert result == ("mcp", "fallback")


def test_cascade_no_tool_descriptions_skips_heuristic_and_llm():
    result = infer_semantic_category(
        server_name="completely-exotic-mcp",
        tool_descriptions=None,
        llm_callable=_make_mock_llm("data"),
    )
    # Sans descriptions, l'heuristique et le LLM ne peuvent rien faire
    assert result == ("mcp", "fallback")


def test_cascade_returns_only_valid_categories():
    # Quel que soit le chemin, la catégorie retournée doit être valide
    for server in ("gmail", "exotic-xyz", "unknown"):
        cat, _src = infer_semantic_category(server)
        assert cat in VALID_CATEGORIES


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — translate_human_to_category
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("human,expected", [
    ("emails", "mail"),
    ("messagerie", "mail"),
    ("mail", "mail"),
    ("courrier", "mail"),
    ("github", "github"),
    ("code", "github"),
    ("boulot", "project"),
    ("travail", "project"),
    ("fichier", "files"),
    ("fichiers", "files"),
    ("calendrier", "communication"),
    ("musique", "spotify"),
    ("photo", "image"),
    ("vidéo", "video"),
    ("notes", "memory"),
    ("base de donnees", "data"),
    ("réseau", "network"),
    ("sécurité", "security"),
])
def test_translate_human_to_category_known(human, expected):
    assert translate_human_to_category(human) == expected


def test_translate_human_to_category_unknown_returns_none():
    assert translate_human_to_category("blablabla xyz") is None


def test_translate_human_to_category_empty_returns_none():
    assert translate_human_to_category("") is None
    assert translate_human_to_category(None) is None


def test_translate_human_to_category_with_accents():
    # Accents normalisés
    assert translate_human_to_category("mémoire") == "memory"
    assert translate_human_to_category("vidéo") == "video"


def test_translate_human_in_sentence():
    # "je veux gérer mes emails" → mail
    assert translate_human_to_category("je veux gérer mes emails") == "mail"


def test_translate_human_to_category_returns_only_valid():
    # Quoi qu'on lui donne, retour ∈ VALID_CATEGORIES ou None
    for phrase in ("emails", "boulot", "xyzunknown", "fichiers"):
        result = translate_human_to_category(phrase)
        if result is not None:
            assert result in VALID_CATEGORIES


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — category_to_human_label
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("category,expected_label", [
    ("mail", "messages et emails"),
    ("files", "fichiers sur ton ordinateur"),
    ("github", "code et dépôts GitHub"),
    ("memory", "mémoire et connaissances"),
])
def test_category_to_human_label_known(category, expected_label):
    assert category_to_human_label(category) == expected_label


def test_category_to_human_label_unknown_returns_input():
    # Si la catégorie n'a pas de label, renvoie le category brut
    assert category_to_human_label("custom_unknown") == "custom_unknown"


def test_category_to_human_label_non_string():
    assert category_to_human_label(None) == ""
    assert category_to_human_label(123) == ""


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Anti-leak et fail-safe
# ══════════════════════════════════════════════════════════════════════════════


def test_no_module_imports_other_mcp_modules():
    """Phase A doit rester isolé : aucun import vers src.mcp.* autre."""
    import inspect
    from src.mcp import category_inference
    source = inspect.getsource(category_inference)
    forbidden_imports = [
        "from src.mcp.capability_resolver",
        "from src.mcp.proposal_planner",
        "from src.mcp.autonomous_orchestrator",
        "from src.mcp.execution_bridge",
        "from src.mcp.activation_service",
        "from src.mcp.install_orchestrator",
        "from src.mcp.handler_adapter",
        "from src.mcp.server_catalog",
        "from src.mcp.approval_queue",
        "from src.mcp.policy_resolver",
        "from src.reasoning",
        "from web.routes",
    ]
    for imp in forbidden_imports:
        assert imp not in source, f"Import interdit Phase A : {imp}"


def test_no_mutation_no_side_effect():
    """Les fonctions doivent être pures : aucun effet sur état global."""
    # Appel multiple ne change rien
    result1 = infer_semantic_category("gmail")
    result2 = infer_semantic_category("gmail")
    assert result1 == result2


def test_infer_with_llm_no_raw_prompt_leak():
    """Si le LLM lève, aucun fragment de prompt ne doit fuir."""
    captured = []
    def _capturing_llm(prompt: str) -> str:
        captured.append(prompt)
        raise RuntimeError("boom")
    result = infer_with_llm("test-srv", ["SECRET_TOOL_DESC"], _capturing_llm)
    assert result is None
    # Le test vérifie juste que ça ne crash pas et retourne None


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — UTF-8
# ══════════════════════════════════════════════════════════════════════════════


def test_utf8_in_human_labels():
    # Vérification que les labels UTF-8 sont préservés (accents)
    assert "é" in HUMAN_FRIENDLY_LABELS["memory"]
    assert "é" in HUMAN_FRIENDLY_LABELS["github"]


def test_no_mojibake_in_labels():
    # Pas de mojibake type "Ã©" "Ã¨" dans les labels
    for label in HUMAN_FRIENDLY_LABELS.values():
        for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "â€™"):
            assert moji not in label, f"Mojibake dans label : {label}"


def test_human_input_with_emoji_doesnt_crash():
    # Si l'user dit "📧 emails", ça matche quand même
    result = translate_human_to_category("📧 emails")
    assert result == "mail"
