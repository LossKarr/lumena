"""Tests — Phase 3 : classify_intent() déterministe."""
import pytest

from src.core_services.intent_classifier import classify_intent, RequestMode


class TestChatMode:
    """Messages qui doivent être classifiés CHAT."""

    def test_greeting_bonjour(self):
        assert classify_intent("Bonjour!") == RequestMode.CHAT

    def test_greeting_salut(self):
        assert classify_intent("Salut Lumena") == RequestMode.CHAT

    def test_greeting_merci(self):
        assert classify_intent("Merci!") == RequestMode.CHAT

    def test_greeting_bonsoir(self):
        assert classify_intent("Bonsoir") == RequestMode.CHAT

    def test_opinion_penses(self):
        assert classify_intent("Tu penses quoi de l'IA?") == RequestMode.CHAT

    def test_meta_qui_es_tu(self):
        assert classify_intent("Qui es-tu?") == RequestMode.CHAT

    def test_meta_capacites(self):
        assert classify_intent("C'est quoi tes capacités?") == RequestMode.CHAT

    def test_short_no_action(self):
        assert classify_intent("C'est beau") == RequestMode.CHAT

    def test_empty_returns_chat(self):
        assert classify_intent("") == RequestMode.CHAT

    def test_none_like_empty(self):
        assert classify_intent("   ") == RequestMode.CHAT


class TestProjectMode:
    """Messages qui doivent être classifiés PROJECT."""

    def test_cree_un_site(self):
        assert classify_intent("Crée un site web moderne pour une startup") == RequestMode.PROJECT

    def test_genere_une_app(self):
        assert classify_intent("Génère une app React avec TypeScript") == RequestMode.PROJECT

    def test_build_portfolio(self):
        assert classify_intent("Build me a portfolio website with dark theme") == RequestMode.PROJECT

    def test_landing_page(self):
        assert classify_intent("Crée une landing page pour mon produit SaaS") == RequestMode.PROJECT

    def test_dashboard(self):
        assert classify_intent("Développe un dashboard admin avec des graphiques") == RequestMode.PROJECT


class TestToolDirectMode:
    """Messages qui doivent être classifiés TOOL_DIRECT."""

    def test_envoie_mail(self):
        assert classify_intent("Envoie un mail à john@example.com") == RequestMode.TOOL_DIRECT

    def test_ouvre_fichier(self):
        assert classify_intent("Ouvre le fichier config.json") == RequestMode.TOOL_DIRECT

    def test_joue_spotify(self):
        assert classify_intent("Joue de la musique sur Spotify") == RequestMode.TOOL_DIRECT

    def test_screenshot(self):
        assert classify_intent("Prends une screenshot") == RequestMode.TOOL_DIRECT

    def test_quelle_heure(self):
        assert classify_intent("Quelle heure est-il?") == RequestMode.TOOL_DIRECT

    def test_ouvre_chrome(self):
        assert classify_intent("Ouvre chrome sur google.com") == RequestMode.TOOL_DIRECT


class TestReactMode:
    """Messages qui doivent être classifiés REACT."""

    def test_recherche_web(self):
        result = classify_intent("Recherche les dernières nouvelles sur l'IA et résume-les")
        assert result == RequestMode.REACT

    def test_multi_step(self):
        result = classify_intent("Cherche des infos sur Python puis ouvre la doc")
        assert result == RequestMode.REACT

    def test_code_complex(self):
        result = classify_intent("Écris un script Python pour scraper des données puis envoie par mail")
        assert result == RequestMode.REACT

    @pytest.mark.parametrize(
        "query",
        [
            "Génère-moi un bon de commande test",
            "Crée un devis professionnel",
            "Prépare une attestation de travail",
            "Rédige un contrat de prestation",
        ],
    )
    def test_document_studio_artifacts_are_not_chat(self, query):
        assert classify_intent(query) == RequestMode.REACT


class TestEdgeCases:
    """Cas limites."""

    def test_court_sans_action_est_chat(self):
        assert classify_intent("Quel temps fait-il à Paris?") == RequestMode.CHAT

    def test_projet_long_description(self):
        q = "Crée un site web complet pour mon restaurant avec menu, réservations et contact"
        assert classify_intent(q) == RequestMode.PROJECT
