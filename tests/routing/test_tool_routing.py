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
