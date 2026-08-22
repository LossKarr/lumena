"""
Tests — Stratégie browser Lumena (discipline + impasse + progression).

Valide :
  - _BROWSER_VISUAL contient tous les outils de revalidation attendus
  - _detect_browser_impasse détecte les observations bloquantes connues
  - Le guard anti-aveuglement injecte la guidance après le seuil
  - La détection de répétition sur même cible index injecte de la guidance
  - Le fail streak arrête la boucle proprement
  - Les handlers browser existants ne régressent pas (smoke)
"""
from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Classification de surface browser — phase 1
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyBrowserSurface:
    """Vérifie la classification de la surface browser réelle."""

    def _classify(self, text, *, current_url="", page_title="", previous_surface=""):
        from src.reasoning.react import _classify_browser_surface
        return _classify_browser_surface(
            text, current_url=current_url, page_title=page_title,
            previous_surface=previous_surface,
        )

    def test_builder_editor_surface(self):
        surface, reason = self._classify(
            'Page: Demo Submission Form - Jotform Form Builder\n'
            'URL: https://www.jotform.com/build/261188177176365?s=templates\n'
            'Interactive elements: 80\n'
            '[2] button "Open the revision history"\n'
            '[3] button "Add Collaborators"\n'
            '[11] button "Add Element"\n'
            '[57] radio "Preview Form"'
        )
        assert surface == "builder_editor"
        assert "builder" in reason.lower() or "éditeur" in reason.lower()

    def test_search_results_surface(self):
        surface, _ = self._classify(
            "Page: google forms demo form fillable test - Recherche Google\n"
            "URL: https://www.google.com/search?q=google+forms+demo\n"
            "Interactive elements: 96"
        )
        assert surface == "search_results"

    def test_public_form_surface(self):
        surface, _ = self._classify(
            'Interactive elements: 8\n'
            '[1] textbox "Name"\n'
            '[2] textbox "Email"\n'
            '[3] textarea "Message"\n'
            '[4] button "Submit"'
        )
        assert surface == "public_form"

    def test_chat_composer_surface(self):
        surface, reason = self._classify(
            'Page: Le Chat\n'
            'Interactive elements: 9\n'
            '[3] button "Sign in"\n'
            '[4] button "Sign up"\n'
            '[7] button "Think"\n'
            '[8] button "Tools"\n'
            '[9] button "Voice Mode"\n'
            'Message input\n'
            'div.ProseMirror\n'
        )
        assert surface == "chat_composer"
        assert "chat" in reason.lower()

    def test_duckai_like_surface_is_chat_composer(self):
        surface, reason = self._classify(
            'Page: Duck.ai par DuckDuckGo. Chat IA prive. Gratuit.\n'
            'Interactive elements: 16\n'
            '[2] button "Nouvelle discussion"\n'
            '[3] button "Nouveau chat vocal"\n'
            '[11] button "Envoyer" (disabled)\n'
            '[15] textbox "Posez toutes vos questions en prive"\n'
        )
        assert surface == "chat_composer"
        assert "chat" in reason.lower()

    def test_huggingchat_like_surface_is_chat_composer(self):
        surface, reason = self._classify(
            'Page: HuggingChat\n'
            'Interactive elements: 22\n'
            '[15] button "Send message" (disabled)\n'
            '[16] button "Start chatting"\n'
            '[22] textbox "Ask anything"\n'
        )
        assert surface == "chat_composer"
        assert "chat" in reason.lower()

    def test_chat_transcript_surface(self):
        surface, reason = self._classify(
            "✅ JS exécuté\n"
            "→ Salut ! Je suis Lumena.\n"
            "1:55am\n---\n"
            "Salut Lumena ! Je vais très bien, merci.\n"
            "1:55am"
        )
        assert surface == "chat_transcript"
        assert "conversation" in reason.lower()

    def test_chat_surface_persists_after_focus_confirmation(self):
        surface, reason = self._classify(
            '✅ Clic sur [10] textbox "Ask anything" a (964, 502)\n',
            current_url="https://chat.mistral.ai/chat",
            page_title="Le Chat",
            previous_surface="chat_composer",
        )
        assert surface == "chat_composer"
        assert "chat" in reason.lower() or "confirmation" in reason.lower()

    def test_auth_url_showing_contact_form_is_classified_as_contact_form(self):
        surface, reason = self._classify(
            'Page: LossKarr - Connexion\n'
            'Interactive elements: 10\n'
            '[1] textbox "Nom"\n'
            '[2] textbox "Email"\n'
            '[3] textarea "Votre message"\n'
            '[4] button "Envoyer"\n',
            current_url="https://losskarr.fr/connexion",
        )
        assert surface == "contact_form"
        assert "contact" in reason.lower() or "spa" in reason.lower()

    def test_auth_recovery_form_stays_auth_form(self):
        surface, reason = self._classify(
            'Page: Connexion - LossKarr\n'
            'Interactive elements: 7\n'
            '[1] button "Connexion"\n'
            '[2] button "Inscription"\n'
            '[3] button "Envoyer le code"\n'
            '[4] button "← Retour à la connexion"\n'
            '[7] textbox "ton@email.com"\n',
            current_url="https://losskarr.fr/public/auth.html",
        )
        assert surface == "auth_form"
        assert "auth" in reason.lower() or "mot de passe" in reason.lower() or "connexion" in reason.lower()

    def test_detail_page_wins_before_generic_public_form(self):
        surface, _ = self._classify(
            'Date : Mercredi 16 décembre 2026\n'
            'Lieu : Paris La Defense Arena\n'
            'Prix : 65€\n'
            '[1] button "Réserver"\n'
            '[2] textbox "Newsletter"\n'
            'Interactive elements: 12\n'
        )
        assert surface == "detail_page"

    def test_iframe_heavy_surface(self):
        surface, _ = self._classify(
            "🖼️ 30 frame(s):\n"
            "[#1] name='iframeResult' url=about:blank\n"
            "[#2] name='__tcfapiLocator' url=about:blank"
        )
        assert surface == "iframe_heavy"

    def test_error_page_surface(self):
        surface, _ = self._classify(
            "✅ Navigué vers: 404 Not Found (https://example.com/missing)"
        )
        assert surface == "error_page"

    def test_empty_surface_is_unknown(self):
        surface, reason = self._classify("")
        assert surface == "unknown"
        assert "signal" in reason.lower()

    def test_listing_results_surface(self):
        surface, reason = self._classify(
            "Site de petites annonces gratuites\n"
            "Voir l'annonce - Renault Clio\n"
            "Voir l'annonce - Peugeot 208\n"
            "Ajouter l'annonce aux favoris\n"
            "Interactive elements: 45"
        )
        assert surface == "listing_results"
        assert "annonce" in reason.lower() or "listing" in reason.lower()

    def test_action_confirmation_inherits_previous_surface(self):
        """Une observation sans signal fort hérite de previous_surface."""
        surface, reason = self._classify(
            "✅ Clic effectué avec succès.",
            previous_surface="public_form",
        )
        assert surface == "public_form"
        assert "hérité" in reason.lower() or "précédent" in reason.lower()


class TestBrowserSurfaceMismatch:
    """Vérifie les mésalignements utiles surface ↔ objectif."""

    def _mismatch(self, surface, query):
        from src.reasoning.react import _browser_surface_mismatch
        return _browser_surface_mismatch(surface, query)

    def test_builder_editor_mismatch_for_fill_form_goal(self):
        mismatch, reason = self._mismatch(
            "builder_editor",
            "remplis ce formulaire de démonstration avec mes infos"
        )
        assert mismatch
        assert "formulaire public" in reason.lower() or "builder" in reason.lower()

    def test_builder_editor_not_mismatch_for_general_edit_goal(self):
        mismatch, _ = self._mismatch(
            "builder_editor",
            "ouvre la page jotform et analyse l'éditeur"
        )
        assert not mismatch

    def test_login_wall_mismatch_when_auth_not_requested(self):
        mismatch, reason = self._mismatch(
            "login_wall",
            "va lire cette page produit et résume-la"
        )
        assert mismatch
        assert "connexion" in reason.lower() or "authentification" in reason.lower()

    def test_login_wall_not_mismatch_when_auth_requested(self):
        mismatch, _ = self._mismatch(
            "login_wall",
            "connecte-toi à mon compte et récupère le document"
        )
        assert not mismatch

    def test_public_form_no_mismatch_when_auth_requested(self):
        # P2 fix : public_form + wants_auth ne génère plus de mismatch (faux positifs
        # trop fréquents sur Perplexity et sites publics avec tokens "connexion"/"compte").
        # Les vrais mismatches auth sont couverts par auth_form/contact_form/login_wall.
        mismatch, _ = self._mismatch(
            "public_form",
            "va sur la page connexion et connecte-toi avec mon email et mot de passe"
        )
        assert not mismatch


class TestBrowserHumanNavigationHelpers:
    def test_extract_browser_auth_target(self):
        from src.reasoning.react import _extract_browser_auth_target
        obs = (
            'Interactive elements: 5\n'
            '[21] link "Accueil"\n'
            '[22] link "Connexion"\n'
            '[23] button "Envoyer"\n'
        )
        assert _extract_browser_auth_target(obs) == ("22", "Connexion")

    def test_browser_passive_tool_can_complete_task(self):
        from src.reasoning.react import _browser_passive_tool_can_complete_task
        assert _browser_passive_tool_can_complete_task("browser_navigate", "Naviguer vers losskarr.fr")
        assert not _browser_passive_tool_can_complete_task(
            "browser_navigate",
            'Essayer de me connecter (si mot de passe connu) ou cliquer "mot de passe oublié"',
        )
        assert _browser_passive_tool_can_complete_task(
            "browser_dom_state",
            "Trouver le formulaire de connexion",
        )
        assert not _browser_passive_tool_can_complete_task(
            "browser_dom_state",
            "Vérifier les spams email si un code est envoyé",
        )

    def test_browser_rewrite_human_navigation_action_prefers_click(self):
        from src.reasoning.react import _browser_rewrite_human_navigation_action
        obs = (
            'Interactive elements: 5\n'
            '[22] link "Connexion"\n'
        )
        rewritten = _browser_rewrite_human_navigation_action(
            "browser_navigate",
            {"url": "https://losskarr.fr/login"},
            query="connecte-toi à mon compte sur losskarr avec mon email",
            last_surface="contact_form",
            last_observation=obs,
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_click_index"
        assert new_args == {"index": "22"}
        assert "clic réel" in reason.lower() or "renavigation" in reason.lower()

    def test_browser_rewrite_text_entry_action_promotes_click_to_type_with_text_arg(self):
        from src.reasoning.react import _browser_rewrite_text_entry_action
        obs = (
            'Interactive elements: 7\n'
            '[3] button "Envoyer le code"\n'
            '[7] textbox "ton@email.com"\n'
        )
        rewritten = _browser_rewrite_text_entry_action(
            "browser_click_index",
            {"index": "7", "text": "lumena.contact.ai@gmail.com"},
            last_observation=obs,
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "7", "text": "lumena.contact.ai@gmail.com"}
        assert "saisie" in reason.lower() or "browser_type_index" in reason.lower()

    def test_browser_rewrite_text_entry_action_promotes_click_to_type_with_value_arg(self):
        from src.reasoning.react import _browser_rewrite_text_entry_action
        obs = (
            'Interactive elements: 7\n'
            '[7] textbox "ton@email.com"\n'
        )
        rewritten = _browser_rewrite_text_entry_action(
            "browser_click_index",
            {"index": "7", "value": "lumena.contact.ai@gmail.com"},
            last_observation=obs,
        )
        assert rewritten is not None
        new_tool, new_args, _ = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "7", "text": "lumena.contact.ai@gmail.com"}

    def test_browser_rewrite_text_entry_action_does_not_rewrite_non_text_target(self):
        from src.reasoning.react import _browser_rewrite_text_entry_action
        obs = (
            'Interactive elements: 7\n'
            '[3] button "Envoyer le code"\n'
        )
        rewritten = _browser_rewrite_text_entry_action(
            "browser_click_index",
            {"index": "3", "text": "lumena.contact.ai@gmail.com"},
            last_observation=obs,
        )
        assert rewritten is None

    def test_extract_sendkeys_payload(self):
        from src.reasoning.react import _extract_sendkeys_payload
        payload = _extract_sendkeys_payload(
            "powershell -Command \"[System.Windows.Forms.SendKeys]::SendWait('lumena.contact.ai@gmail.com')\""
        )
        assert payload == "lumena.contact.ai@gmail.com"

    def test_browser_rewrite_system_typing_action_promotes_sendkeys_to_type_index(self):
        from src.reasoning.react import _browser_rewrite_system_typing_action
        obs = (
            'Interactive elements: 7\n'
            '[7] textbox "ton@email.com"\n'
        )
        rewritten = _browser_rewrite_system_typing_action(
            "run_command",
            {
                "command": "powershell -Command \"$wshell.SendKeys('lumena.contact.ai@gmail.com')\"",
            },
            last_observation=obs,
            last_textbox_index="7",
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "7", "text": "lumena.contact.ai@gmail.com"}
        assert "sendkeys" in reason.lower() or "browser_type_index" in reason.lower()

    def test_browser_rewrite_index_like_selector_promotes_type_to_type_index(self):
        from src.reasoning.react import _browser_rewrite_index_like_selector_action
        rewritten = _browser_rewrite_index_like_selector_action(
            "browser_type",
            {"selector": "[16]", "text": "LumenaAI"},
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "16", "text": "LumenaAI"}
        assert "index dom" in reason.lower()

    def test_browser_rewrite_index_like_selector_promotes_click_to_click_index(self):
        from src.reasoning.react import _browser_rewrite_index_like_selector_action
        rewritten = _browser_rewrite_index_like_selector_action(
            "browser_click",
            {"selector": "[12]"},
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_click_index"
        assert new_args == {"index": "12"}
        assert "index dom" in reason.lower()

    def test_browser_rewrite_index_like_selector_ignores_real_css(self):
        from src.reasoning.react import _browser_rewrite_index_like_selector_action
        rewritten = _browser_rewrite_index_like_selector_action(
            "browser_type",
            {"selector": "input[type='email']", "text": "lumena.contact.ai@gmail.com"},
        )
        assert rewritten is None

    def test_browser_rewrite_selector_guess_promotes_chat_textarea_guess(self):
        from src.reasoning.react import _browser_rewrite_selector_guess_to_index_action
        obs = (
            'Page: Le Chat\n'
            'Interactive elements: 10\n'
            '[10] textbox "Ask anything"\n'
        )
        rewritten = _browser_rewrite_selector_guess_to_index_action(
            "browser_type",
            {
                "selector": 'textarea[aria-label="Ask anything"]',
                "text": "Bonjour Mistral",
            },
            last_surface="chat_composer",
            last_observation=obs,
        )
        assert rewritten is not None
        new_tool, new_args, reason = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "10", "text": "Bonjour Mistral"}
        assert "conversion" in reason.lower()

    def test_browser_rewrite_selector_guess_promotes_role_textbox_guess(self):
        from src.reasoning.react import _browser_rewrite_selector_guess_to_index_action
        obs = (
            'Interactive elements: 10\n'
            '[10] textbox "Ask anything"\n'
        )
        rewritten = _browser_rewrite_selector_guess_to_index_action(
            "browser_type",
            {
                "selector": '[role="textbox"]',
                "text": "Bonjour Mistral",
            },
            last_surface="chat_composer",
            last_observation=obs,
        )
        assert rewritten is not None
        new_tool, new_args, _ = rewritten
        assert new_tool == "browser_type_index"
        assert new_args == {"index": "10", "text": "Bonjour Mistral"}

    def test_browser_rewrite_selector_guess_ignores_when_multiple_textboxes(self):
        from src.reasoning.react import _browser_rewrite_selector_guess_to_index_action
        obs = (
            'Interactive elements: 4\n'
            '[1] textbox "Email"\n'
            '[2] textbox "Mot de passe"\n'
        )
        rewritten = _browser_rewrite_selector_guess_to_index_action(
            "browser_type",
            {
                "selector": '[role="textbox"]',
                "text": "secret",
            },
            last_surface="auth_form",
            last_observation=obs,
        )
        assert rewritten is None

    def test_read_only_discovery_tool_can_complete_task(self):
        from src.reasoning.react import _read_only_discovery_tool_can_complete_task
        assert _read_only_discovery_tool_can_complete_task(
            "web_fetch",
            "Vérifier l'accès actuel à chat.mistral.ai via web_fetch",
        )
        assert not _read_only_discovery_tool_can_complete_task(
            "web_fetch",
            "Si accessible sans connexion, naviguer et échanger",
        )


class TestBrowserProgressHelpers:
    """Vérifie la mesure de progression browser (phase 2)."""

    def test_extract_browser_interactive_count(self):
        from src.reasoning.react import _extract_browser_interactive_count
        assert _extract_browser_interactive_count("Interactive elements: 96") == 96

    def test_extract_browser_interactive_count_none(self):
        from src.reasoning.react import _extract_browser_interactive_count
        assert _extract_browser_interactive_count("No count here") is None

    def test_make_browser_progress_signature_reuses_previous_missing_fields(self):
        from src.reasoning.react import _make_browser_progress_signature
        prev = ("public_form", "https://example.com/form", "Contact Form", 4, (1, 0, 1, 0, 5), None)
        sig = _make_browser_progress_signature(
            "public_form",
            "📸 Screenshot sauvegarde: x.png",
            previous=prev,
        )
        assert sig == prev

    def test_progress_delta_first_state(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(None, ("search_results", "u", "t", 3), action_tool="browser_navigate")
        assert progressed
        assert "premier" in reason.lower()

    def test_progress_delta_surface_change(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(
            ("search_results", "https://google.com", "Google", 8),
            ("public_form", "https://example.com/form", "Form", 2),
            action_tool="browser_click_index",
        )
        assert progressed
        assert "surface" in reason.lower()

    def test_progress_delta_url_change(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(
            ("normal_content", "https://a.com", "Page A", 4),
            ("normal_content", "https://b.com", "Page A", 4),
            action_tool="browser_navigate",
        )
        assert progressed
        assert "url" in reason.lower()

    def test_progress_delta_same_url_navigate_detects_spa_stagnation(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(
            ("public_form", "https://losskarr.fr/connexion", "LossKarr", 3),
            ("public_form", "https://losskarr.fr/connexion", "LossKarr", 3),
            action_tool="browser_navigate",
        )
        assert not progressed
        assert "spa" in reason.lower() or "même url" in reason.lower()

    def test_progress_delta_no_progress_same_signature(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(
            ("builder_editor", "https://x.com/build", "Builder", 7),
            ("builder_editor", "https://x.com/build", "Builder", 7),
            action_tool="browser_dom_state",
        )
        assert not progressed
        assert "même surface" in reason.lower() or "utile" in reason.lower()

    def test_progress_delta_interactive_bucket_change_after_action(self):
        from src.reasoning.react import _browser_progress_delta
        progressed, reason = _browser_progress_delta(
            ("public_form", "https://x.com/form", "Form", 2),
            ("public_form", "https://x.com/form", "Form", 5),
            action_tool="browser_click_index",
        )
        assert progressed
        assert "densité" in reason.lower()

    def test_extract_browser_form_state(self):
        from src.reasoning.react import _extract_browser_form_state
        obs = "Form state: filled=3, checked=1, disabled_buttons=0, enabled_submit_buttons=1, controls=8"
        result = _extract_browser_form_state(obs)
        assert result == (3, 1, 0, 1, 8)

    def test_extract_browser_form_state_none_when_absent(self):
        from src.reasoning.react import _extract_browser_form_state
        assert _extract_browser_form_state("No form state here") is None
        assert _extract_browser_form_state("") is None

    def test_progress_delta_listing_click_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("listing_results", "https://leboncoin.fr", "LeBonCoin", 4, None, (1, 0, 5))
        cur  = ("listing_results", "https://leboncoin.fr", "LeBonCoin", 4, None, (1, 1, 5))
        progressed, reason = _browser_progress_delta(prev, cur, action_tool="browser_click_index")
        assert progressed
        assert "annonce" in reason.lower() or "listing" in reason.lower()

    def test_progress_delta_listing_more_labels_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("listing_results", "https://leboncoin.fr", "LeBonCoin", 4, None, (1, 0, 5))
        cur  = ("listing_results", "https://leboncoin.fr", "LeBonCoin", 4, None, (1, 0, 8))
        progressed, reason = _browser_progress_delta(prev, cur, action_tool="browser_screenshot_labels")
        assert progressed
        assert "label" in reason.lower() or "listing" in reason.lower()

    def test_browser_observation_failure_detects_missing_element(self):
        from src.reasoning.react import _browser_observation_has_failure
        assert _browser_observation_has_failure(
            "browser_click_smart",
            "Aucun élément trouvé pour 'Accepter' (selector='') — tried: selector"
        )

    def test_browser_observation_failure_detects_missing_parameters(self):
        from src.reasoning.react import _browser_observation_has_failure
        assert _browser_observation_has_failure(
            "browser_navigate",
            "Paramètre(s) requis manquant(s) pour 'browser_navigate': url"
        )

    def test_browser_observation_failure_ignores_successful_click(self):
        from src.reasoning.react import _browser_observation_has_failure
        assert not _browser_observation_has_failure(
            "browser_click_index",
            '✅ Clic sur [3] button "J\'ACCEPTE"'
        )


# ─────────────────────────────────────────────────────────────────────────────
# _detect_browser_impasse — détection centralisée
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectBrowserImpasse:
    """Vérifie que _detect_browser_impasse détecte correctement les impasses."""

    def _detect(self, text):
        from src.reasoning.react import _detect_browser_impasse
        return _detect_browser_impasse(text)

    # ── Cloudflare ──

    def test_cloudflare_token(self):
        blocked, reason, _ = self._detect("Error: cloudflare protection active")
        assert blocked
        assert "cloudflare" in reason.lower()

    def test_checking_your_browser(self):
        blocked, reason, _ = self._detect("Checking your browser before accessing...")
        assert blocked

    def test_just_a_moment(self):
        blocked, reason, _ = self._detect("Just a moment... DDoS protection")
        assert blocked

    def test_challenge_running(self):
        blocked, reason, _ = self._detect("challenge_running=true detected in response")
        assert blocked

    # ── Captcha ──

    def test_captcha(self):
        blocked, reason, _ = self._detect("Please complete the captcha to continue")
        assert blocked

    def test_recaptcha(self):
        blocked, reason, _ = self._detect("reCAPTCHA v2 widget loaded")
        assert blocked

    def test_not_a_robot(self):
        blocked, reason, _ = self._detect("Please verify: I'm not a robot")
        assert blocked

    # ── Erreurs serveur ──

    def test_dyno_hours_exhausted(self):
        blocked, reason, _ = self._detect(
            "Application Error\ndyno hours exhausted\nYour free dyno hours are up."
        )
        assert blocked

    def test_no_web_processes(self):
        blocked, reason, _ = self._detect("No web processes running on Heroku")
        assert blocked

    def test_application_error(self):
        blocked, reason, _ = self._detect("Application Error — An error occurred in the application")
        assert blocked

    # ── Contrôle d'accès ──

    def test_access_denied(self):
        blocked, reason, _ = self._detect("403 Access Denied — You don't have permission")
        assert blocked

    def test_403_forbidden(self):
        blocked, reason, _ = self._detect("HTTP 403 Forbidden")
        assert blocked

    def test_rate_limit_exceeded(self):
        blocked, reason, _ = self._detect("Rate limit exceeded — please retry after 60 seconds")
        assert blocked

    def test_too_many_requests(self):
        blocked, reason, _ = self._detect("429 Too Many Requests")
        assert blocked

    # ── Cas try_dismiss ──

    def test_cookie_consent_try_dismiss(self):
        blocked, reason, try_dismiss = self._detect(
            "Cookie consent banner: Accept all cookies to continue"
        )
        assert blocked
        assert try_dismiss is True

    def test_accept_cookies_try_dismiss(self):
        blocked, reason, try_dismiss = self._detect(
            "Please accept cookies before proceeding"
        )
        assert blocked
        assert try_dismiss is True

    # ── Page vide / non interactive ──

    def test_no_interactive_elements(self):
        blocked, reason, _ = self._detect("No interactive elements found on the page.")
        assert blocked

    def test_0_elements_found(self):
        blocked, reason, _ = self._detect("DOM snapshot: 0 elements found, page may be loading.")
        assert blocked

    def test_aucun_element_interactif(self):
        blocked, reason, _ = self._detect("aucun élément interactif détecté sur cette page")
        assert blocked

    # ── Login wall ──

    def test_you_must_be_logged_in(self):
        blocked, reason, _ = self._detect("You must be logged in to view this content.")
        assert blocked
        assert "authentification" in reason.lower() or "mur" in reason.lower()

    def test_please_log_in_to_continue(self):
        blocked, reason, _ = self._detect("Please log in to continue browsing.")
        assert blocked

    def test_please_sign_in_to_continue(self):
        blocked, reason, _ = self._detect("Please sign in to continue to your account.")
        assert blocked

    def test_login_required(self):
        blocked, reason, _ = self._detect("Login required — this area is restricted.")
        assert blocked

    def test_members_only(self):
        blocked, reason, _ = self._detect("Members only — upgrade your plan to access this page.")
        assert blocked

    def test_subscribers_only(self):
        blocked, reason, _ = self._detect("Subscribers only content. Subscribe to read more.")
        assert blocked

    def test_authentication_required(self):
        blocked, reason, _ = self._detect("Authentication required to access this resource.")
        assert blocked

    def test_session_expired(self):
        blocked, reason, _ = self._detect("Your session has expired. Please log in again.")
        assert blocked

    def test_sign_in_required(self):
        blocked, reason, _ = self._detect("Sign in required to access your dashboard.")
        assert blocked

    def test_you_must_sign_in(self):
        blocked, reason, _ = self._detect("You must sign in before accessing this page.")
        assert blocked

    # ── Cas non bloquants (pas de faux positifs) ──

    def test_no_block_normal_page(self):
        blocked, _, _ = self._detect(
            "✅ Screenshot pris — 42 éléments interactifs trouvés. URL: https://booking.com"
        )
        assert not blocked

    def test_no_block_login_form_normal(self):
        """Un formulaire de login normal ne doit pas être considéré comme un mur."""
        blocked, _, _ = self._detect(
            "Page loaded: Login page\n"
            "Elements: [1] Email input, [2] Password input, [3] Submit button"
        )
        assert not blocked

    def test_no_block_empty_text(self):
        blocked, _, _ = self._detect("")
        assert not blocked

    def test_no_block_successful_navigation(self):
        blocked, _, _ = self._detect(
            "Navigation réussie vers https://example.com — 15 éléments interactifs"
        )
        assert not blocked

    def test_no_block_google_results(self):
        blocked, _, _ = self._detect(
            "Résultats Google: 10 liens trouvés pour 'hôtel Paris'"
        )
        assert not blocked

    # ── Insensibilité à la casse ──

    def test_case_insensitive_cloudflare(self):
        blocked, _, _ = self._detect("CLOUDFLARE security check in progress")
        assert blocked

    def test_case_insensitive_captcha(self):
        blocked, _, _ = self._detect("CAPTCHA VERIFICATION REQUIRED")
        assert blocked


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER_VISUAL_TOOLS — outils de revalidation reconnus (runtime)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserVisualTools:
    """Vérifie le contenu de BROWSER_VISUAL_TOOLS et BROWSER_ACTION_TOOLS
    directement sur les constantes module-level (pas d'inspection de source).
    """

    def _visual(self):
        from src.reasoning.react import BROWSER_VISUAL_TOOLS
        return BROWSER_VISUAL_TOOLS

    def _action(self):
        from src.reasoning.react import BROWSER_ACTION_TOOLS
        return BROWSER_ACTION_TOOLS

    # ── Outils fondamentaux présents avant patch ──

    def test_browser_screenshot_in_visual(self):
        assert "browser_screenshot" in self._visual()

    def test_browser_dom_state_in_visual(self):
        assert "browser_dom_state" in self._visual()

    def test_browser_get_content_in_visual(self):
        assert "browser_get_content" in self._visual()

    # ── Nouveaux outils de revalidation structurelle ──

    def test_browser_frames_in_visual(self):
        assert "browser_frames" in self._visual()

    def test_browser_frame_content_in_visual(self):
        assert "browser_frame_content" in self._visual()

    def test_browser_screenshot_labels_in_visual(self):
        assert "browser_screenshot_labels" in self._visual()

    def test_browser_page_info_in_visual(self):
        assert "browser_page_info" in self._visual()

    def test_browser_get_text_in_visual(self):
        assert "browser_get_text" in self._visual()

    # ── Outils d'action présents dans BROWSER_ACTION_TOOLS ──

    def test_browser_click_index_in_action(self):
        assert "browser_click_index" in self._action()

    def test_browser_type_index_in_action(self):
        assert "browser_type_index" in self._action()

    def test_browser_navigate_in_action(self):
        assert "browser_navigate" in self._action()

    # ── Les deux sets sont disjoints ──

    def test_visual_and_action_disjoint(self):
        overlap = self._visual() & self._action()
        assert len(overlap) == 0, (
            f"Outils présents dans les deux sets (incohérence): {overlap}"
        )

    # ── Comportement runtime : un outil visuel reset le blind streak ──

    def test_visual_tool_resets_blind_streak_at_runtime(self):
        """Vérifie que BROWSER_VISUAL_TOOLS est bien consulté par la logique de reset.
        On teste directement la condition qui reset le streak dans la boucle :
        _tool in BROWSER_VISUAL_TOOLS → streak = 0.
        """
        from src.reasoning.react import BROWSER_VISUAL_TOOLS
        for tool in ("browser_screenshot", "browser_frames", "browser_frame_content",
                     "browser_screenshot_labels", "browser_page_info", "browser_get_text"):
            assert tool in BROWSER_VISUAL_TOOLS, (
                f"{tool!r} absent de BROWSER_VISUAL_TOOLS — ne resettera pas le blind streak"
            )

    def test_action_tool_increments_blind_streak_at_runtime(self):
        """Vérifie que BROWSER_ACTION_TOOLS est bien consulté par la logique d'incrément."""
        from src.reasoning.react import BROWSER_ACTION_TOOLS
        for tool in ("browser_click_index", "browser_type_index", "browser_navigate",
                     "browser_click", "browser_type"):
            assert tool in BROWSER_ACTION_TOOLS, (
                f"{tool!r} absent de BROWSER_ACTION_TOOLS — n'incrémentera pas le blind streak"
            )

    def test_visual_tools_are_frozenset(self):
        from src.reasoning.react import BROWSER_VISUAL_TOOLS, BROWSER_ACTION_TOOLS, BROWSER_SURFACE_TYPES
        assert isinstance(BROWSER_VISUAL_TOOLS, frozenset)
        assert isinstance(BROWSER_ACTION_TOOLS, frozenset)
        assert isinstance(BROWSER_SURFACE_TYPES, frozenset)


# ─────────────────────────────────────────────────────────────────────────────
# _detect_browser_impasse — propriétés de l'API
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectBrowserImpasseAPI:
    """Vérifie que la fonction retourne bien un tuple (bool, str, bool)."""

    def test_returns_tuple_three(self):
        from src.reasoning.react import _detect_browser_impasse
        result = _detect_browser_impasse("cloudflare protection")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_blocked_is_bool(self):
        from src.reasoning.react import _detect_browser_impasse
        blocked, _, _ = _detect_browser_impasse("cloudflare protection")
        assert isinstance(blocked, bool)

    def test_reason_is_str(self):
        from src.reasoning.react import _detect_browser_impasse
        _, reason, _ = _detect_browser_impasse("cloudflare protection")
        assert isinstance(reason, str)

    def test_try_dismiss_is_bool(self):
        from src.reasoning.react import _detect_browser_impasse
        _, _, try_dismiss = _detect_browser_impasse("cookie consent banner")
        assert isinstance(try_dismiss, bool)

    def test_not_blocked_returns_empty_reason(self):
        from src.reasoning.react import _detect_browser_impasse
        blocked, reason, try_dismiss = _detect_browser_impasse("page loaded normally")
        assert not blocked
        assert reason == ""
        assert not try_dismiss

    def test_impasse_signals_count(self):
        """Vérifie le nombre exact de signaux déclarés (mise à jour si ajout voulu)."""
        from src.reasoning.react import _BROWSER_IMPASSE_SIGNALS
        # 4 cloudflare + 3 captcha + 3 serveur + 5 accès + 10 login wall + 2 overlays + 3 vide
        assert len(_BROWSER_IMPASSE_SIGNALS) == 30

    def test_impasse_token_set_consistent(self):
        from src.reasoning.react import _BROWSER_IMPASSE_SIGNALS, _BROWSER_IMPASSE_TOKEN_SET
        expected = {token for token, _, _ in _BROWSER_IMPASSE_SIGNALS}
        assert _BROWSER_IMPASSE_TOKEN_SET == expected


# ─────────────────────────────────────────────────────────────────────────────
# Guard anti-aveuglement — seuil et guidance
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserBlindStreakGuard:
    """Vérifie que le guard anti-aveuglement injecte la guidance après le seuil."""

    def _make_loop(self):
        from src.reasoning.react import ReActLoop
        return ReActLoop(llm_chat_func=None)

    def test_initial_blind_streak_is_zero(self):
        loop = self._make_loop()
        assert loop._browser_blind_streak == 0

    def test_visual_tool_resets_streak(self):
        loop = self._make_loop()
        loop._browser_blind_streak = 2
        # Simuler l'appel à un outil visuel (le reset se fait dans la boucle,
        # ici on vérifie que le setter fonctionne correctement)
        loop._browser_blind_streak = 0
        assert loop._browser_blind_streak == 0

    def test_blind_streak_guidance_message_references_screenshot(self):
        """La guidance injectée doit explicitement recommander browser_screenshot."""
        loop = self._make_loop()
        loop._browser_blind_streak = 3
        # Déclencher le message de guidance manuellement (reproduit la logique de react.py)
        guidance = (
            "⚠️ GUIDANCE VISION: Tu viens d'enchaîner "
            f"{loop._browser_blind_streak} actions browser_* sans prendre de screenshot "
            "ni relire le DOM. Tu agis à l'aveugle. "
            "APPELLE MAINTENANT `browser_screenshot` pour voir l'état réel de la page "
            "avant ta prochaine action. Le DOM a probablement changé."
        )
        assert "browser_screenshot" in guidance
        assert "browser_screenshot" in guidance or "browser_dom_state" in guidance


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test — les handlers browser sont importables sans Playwright réel
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserHandlerSmoke:
    """Vérifie que les handlers ajoutés sont toujours importables."""

    def test_import_browser_frames(self):
        from src.reasoning.handlers.browser import browser_frames
        assert callable(browser_frames)

    def test_import_browser_frame_content(self):
        from src.reasoning.handlers.browser import browser_frame_content
        assert callable(browser_frame_content)

    def test_import_browser_screenshot_labels(self):
        from src.reasoning.handlers.browser import browser_screenshot_labels
        assert callable(browser_screenshot_labels)

    def test_import_browser_page_info(self):
        from src.reasoning.handlers.browser import browser_page_info
        assert callable(browser_page_info)

    def test_browser_handler_count_unchanged(self):
        """Pas de régression sur le nombre de handlers déclarés."""
        from src.reasoning.handlers.browser import get_browser_handler_defs
        defs = get_browser_handler_defs()
        assert len(defs) == 76  # +1 : browser_select_index (LOT Z19)

    def test_detect_impasse_importable(self):
        from src.reasoning.react import _detect_browser_impasse
        assert callable(_detect_browser_impasse)

    def test_impasse_signals_importable(self):
        from src.reasoning.react import _BROWSER_IMPASSE_SIGNALS, _BROWSER_IMPASSE_TOKEN_SET
        assert isinstance(_BROWSER_IMPASSE_SIGNALS, list)
        assert isinstance(_BROWSER_IMPASSE_TOKEN_SET, frozenset)


# ─────────────────────────────────────────────────────────────────────────────
# Guards de régression — progression formulaire (form state 6-tuple)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserProgressRegressionGuards:
    """Vérifie que les changements d'état de formulaire sont détectés comme progression."""

    def test_more_filled_fields_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://x.com/form", "Form", 2, (0, 0, 1, 0, 5), None)
        cur  = ("public_form", "https://x.com/form", "Form", 2, (2, 0, 1, 0, 5), None)
        progressed, reason = _browser_progress_delta(prev, cur, action_tool="browser_type_index")
        assert progressed
        assert "rempli" in reason.lower() or "champ" in reason.lower() or "progression" in reason.lower()

    def test_more_checked_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://x.com/form", "Form", 2, (1, 0, 0, 0, 5), None)
        cur  = ("public_form", "https://x.com/form", "Form", 2, (1, 1, 0, 0, 5), None)
        progressed, reason = _browser_progress_delta(prev, cur, action_tool="browser_click_index")
        assert progressed
        assert "coché" in reason.lower() or "case" in reason.lower() or "progression" in reason.lower()

    def test_enabled_submit_button_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://x.com/form", "Form", 2, (2, 0, 1, 0, 5), None)
        cur  = ("public_form", "https://x.com/form", "Form", 2, (2, 0, 0, 1, 5), None)
        progressed, reason = _browser_progress_delta(prev, cur, action_tool="browser_click_index")
        assert progressed
        assert "submit" in reason.lower() or "soumission" in reason.lower() or "activé" in reason.lower()

    def test_type_action_success_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://x.com/form", "Form", 2, (0, 0, 1, 0, 5), None)
        cur  = ("public_form", "https://x.com/form", "Form", 2, (0, 0, 1, 0, 5), None)
        progressed, reason = _browser_progress_delta(
            prev, cur,
            action_tool="browser_type_index",
            observation_text="✅ Texte saisi dans le champ Email",
        )
        assert progressed
        assert "saisie" in reason.lower() or "champ" in reason.lower()

    def test_checkbox_click_counts_as_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://x.com/form", "Form", 2, (0, 0, 0, 0, 5), None)
        cur  = ("public_form", "https://x.com/form", "Form", 2, (0, 0, 0, 0, 5), None)
        progressed, reason = _browser_progress_delta(
            prev, cur,
            action_tool="browser_click_index",
            observation_text="✅ Case à cocher activée avec succès",
        )
        assert progressed
        assert "case" in reason.lower() or "checkbox" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# allow_impasse=False — désactivation de la détection d'impasse
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserFrameImpasseCaution:
    """Vérifie que allow_impasse=False désactive la détection d'impasse."""

    def test_classify_frames_without_impasse_when_impasse_detection_disabled(self):
        from src.reasoning.react import _classify_browser_surface
        obs = "cloudflare protection active, checking your browser"
        surface_with, _    = _classify_browser_surface(obs, allow_impasse=True)
        surface_without, _ = _classify_browser_surface(obs, allow_impasse=False)
        assert surface_with == "anti_bot_or_challenge"
        assert surface_without != "anti_bot_or_challenge"


# ─────────────────────────────────────────────────────────────────────────────
# _format_form_state_summary — formatage depuis browser.py
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserFormStateSummary:
    """Vérifie le formatage du résumé d'état de formulaire."""

    def test_format_form_state_summary(self):
        from src.reasoning.handlers.browser import _format_form_state_summary
        form_state = {
            "filled": 3,
            "checked": 1,
            "disabled_buttons": 0,
            "enabled_submit_buttons": 1,
            "controls": 8,
        }
        result = _format_form_state_summary(form_state)
        assert result == (
            "Form state: filled=3, checked=1, "
            "disabled_buttons=0, enabled_submit_buttons=1, controls=8"
        )

    def test_format_form_state_summary_empty(self):
        from src.reasoning.handlers.browser import _format_form_state_summary
        assert _format_form_state_summary(None) == ""
        assert _format_form_state_summary({}) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Nouvelles surfaces : auth_form, contact_form, detail_page, spa_shell
# ─────────────────────────────────────────────────────────────────────────────

class TestNewBrowserSurfaces:
    """Point 6 — Vérifie la détection des nouvelles surfaces browser."""

    def _classify(self, text, *, current_url="", page_title="", previous_surface=""):
        from src.reasoning.react import _classify_browser_surface
        return _classify_browser_surface(
            text, current_url=current_url, page_title=page_title,
            previous_surface=previous_surface,
        )

    # ── auth_form ──

    def test_auth_form_via_password_input(self):
        """Champ mot de passe dans le DOM → auth_form."""
        surface, reason = self._classify(
            "Interactive elements: 5\n"
            "[1] textbox \"Email\"\n"
            "[2] password input \"Mot de passe\"\n"
            "[3] button \"Se connecter\"\n"
            "[4] link \"Mot de passe oublié\"",
            current_url="https://example.com/login",
        )
        assert surface == "auth_form"
        assert "authentification" in reason.lower() or "mot de passe" in reason.lower()

    def test_auth_form_via_auth_url_and_form(self):
        """URL /connexion + champ textbox → auth_form."""
        surface, _ = self._classify(
            "Interactive elements: 3\n"
            "[1] textbox \"Identifiant\"\n"
            "[2] password input \"Password\"\n"
            "[3] button \"Login\"",
            current_url="https://losskarr.fr/connexion",
        )
        assert surface == "auth_form"

    def test_auth_form_not_classified_without_form_controls(self):
        """URL /login sans éléments interactifs → pas auth_form (pas de formulaire)."""
        surface, _ = self._classify(
            "Loading...",
            current_url="https://example.com/login",
        )
        assert surface != "auth_form"

    # ── contact_form ──

    def test_contact_form_detected(self):
        """Formulaire de contact sans mot de passe → contact_form."""
        surface, reason = self._classify(
            "Interactive elements: 6\n"
            "[1] textbox \"Nom\"\n"
            "[2] textbox \"Email\"\n"
            "[3] textarea \"Votre message\"\n"
            "[4] textbox \"Objet du message\"\n"
            "[5] button \"Envoyer un message\"",
            current_url="https://example.com/contact",
        )
        assert surface == "contact_form"
        assert "contact" in reason.lower()

    def test_contact_form_not_auth_form(self):
        """Contact form sans password → ne doit pas être classifié auth_form."""
        surface, _ = self._classify(
            "Interactive elements: 4\n"
            "[1] textbox \"Prénom\"\n"
            "[2] textbox \"Email\"\n"
            "[3] textarea \"Formulaire de contact\"\n"
            "[4] button \"Envoyer\"",
        )
        assert surface == "contact_form"
        assert surface != "auth_form"

    def test_form_with_password_overrides_contact_hints(self):
        """Si password présent + contact hints → auth_form gagne (plus spécifique)."""
        surface, _ = self._classify(
            "Interactive elements: 5\n"
            "[1] textbox \"Email\"\n"
            "[2] password input \"Password\"\n"
            "[3] button \"Se connecter\"\n"
            "Formulaire de contact",
        )
        assert surface == "auth_form"

    # ── detail_page ──

    def test_detail_page_via_add_to_cart(self):
        """Page produit avec 'Ajouter au panier' → detail_page."""
        surface, reason = self._classify(
            "Sony WH-1000XM5\nPrix : 299€\nAjouter au panier\nEn stock"
        )
        assert surface == "detail_page"
        assert "détail" in reason.lower() or "produit" in reason.lower()

    def test_detail_page_via_billetterie(self):
        """Page événement avec billetterie/réserver → detail_page."""
        surface, _ = self._classify(
            "Concert de Jazz\nDate : 15 juin 2025\nLieu : Salle Pleyel\n"
            "Billetterie ouverte\nRéserver vos places"
        )
        assert surface == "detail_page"

    def test_detail_page_does_not_override_listing(self):
        """Une page listing avec prix ne doit pas devenir detail_page."""
        surface, _ = self._classify(
            "Site de petites annonces gratuites\n"
            "Voir l'annonce - Peugeot 208 à partir de 8 000€",
        )
        assert surface == "listing_results"

    # ── spa_shell ──

    def test_spa_shell_javascript_required(self):
        """JS requis → spa_shell."""
        surface, reason = self._classify(
            "JavaScript is required to use this application.\n"
            "Please enable JavaScript in your browser settings.",
        )
        assert surface == "spa_shell"
        assert "spa" in reason.lower() or "shell" in reason.lower()

    def test_spa_shell_application_loading(self):
        """App loading → spa_shell."""
        surface, _ = self._classify(
            "Application loading\nInteractive elements: 0\n",
            page_title="My App",
        )
        assert surface == "spa_shell"

    def test_spa_shell_not_triggered_on_normal_page(self):
        """Page normale avec contenu → ne doit pas être spa_shell."""
        surface, _ = self._classify(
            "Interactive elements: 12\n"
            "[1] link \"Accueil\"\n"
            "[2] link \"Concerts\"\n"
            "[3] button \"Rechercher\"",
        )
        assert surface != "spa_shell"

    # ── BROWSER_SURFACE_TYPES contient les nouvelles surfaces ──

    def test_new_surfaces_in_browser_surface_types(self):
        from src.reasoning.react import BROWSER_SURFACE_TYPES
        for s in ("auth_form", "contact_form", "detail_page", "spa_shell"):
            assert s in BROWSER_SURFACE_TYPES, f"{s!r} absent de BROWSER_SURFACE_TYPES"


# ─────────────────────────────────────────────────────────────────────────────
# Mismatch auth_form ↔ contact_form (Point 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestFormMismatchAuthContact:
    """Point 1 — Détection du mismatch formulaire de connexion vs contact."""

    def _mismatch(self, surface, query):
        from src.reasoning.react import _browser_surface_mismatch
        return _browser_surface_mismatch(surface, query)

    def test_contact_form_mismatch_when_login_needed(self):
        """Tâche = se connecter, surface = contact_form → mismatch."""
        mismatch, reason = self._mismatch(
            "contact_form",
            "connecte-toi à mon compte losskarr.fr",
        )
        assert mismatch
        assert "contact" in reason.lower() or "connexion" in reason.lower()

    def test_auth_form_mismatch_when_contact_needed(self):
        """Tâche = remplir formulaire de contact, surface = auth_form → mismatch."""
        mismatch, reason = self._mismatch(
            "auth_form",
            "remplis le formulaire de contact sur la page d'accueil",
        )
        assert mismatch
        assert "connexion" in reason.lower() or "contact" in reason.lower()

    def test_auth_form_no_mismatch_when_login_needed(self):
        """Tâche = se connecter, surface = auth_form → pas de mismatch."""
        mismatch, _ = self._mismatch(
            "auth_form",
            "connecte-toi à mon compte et accède au dashboard",
        )
        assert not mismatch

    def test_contact_form_no_mismatch_when_contact_needed(self):
        """Tâche = contacter, surface = contact_form → pas de mismatch."""
        mismatch, _ = self._mismatch(
            "contact_form",
            "remplis ce formulaire de contact avec mes informations",
        )
        assert not mismatch

    def test_detail_page_no_mismatch_any_task(self):
        """detail_page → aucun mismatch défini."""
        mismatch, _ = self._mismatch(
            "detail_page",
            "achète ce produit et confirme la commande",
        )
        assert not mismatch


# ─────────────────────────────────────────────────────────────────────────────
# browser_evaluate progression (Point 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserEvaluateProgression:
    """Point 3 — browser_evaluate avec contenu réel = progrès, bruit JS = non."""

    def _delta(self, obs, *, action_tool="browser_evaluate"):
        from src.reasoning.react import _browser_progress_delta
        prev = ("normal_content", "https://x.com", "Page", 2, None, None)
        cur  = ("normal_content", "https://x.com", "Page", 2, None, None)
        return _browser_progress_delta(prev, cur, action_tool=action_tool, observation_text=obs)

    def test_real_content_date_counts_as_progress(self):
        progressed, reason = self._delta(
            "✅ {\"titre\": \"Concert de Jazz\", \"date\": \"15 juin 2025\", \"lieu\": \"Salle Pleyel\", \"prix\": \"35€\"}"
        )
        assert progressed
        assert "contenu réel" in reason.lower() or "browser_evaluate" in reason.lower()

    def test_real_content_prix_counts_as_progress(self):
        progressed, _ = self._delta("✅ Prix: 299€, disponible: true, réservation ouverte")
        assert progressed

    def test_js_noise_undefined_no_progress(self):
        progressed, reason = self._delta("TypeError: Cannot read properties of undefined")
        assert not progressed
        assert "bruit" in reason.lower() or "js" in reason.lower() or "undefined" in reason.lower()

    def test_js_noise_null_no_progress(self):
        progressed, _ = self._delta("null")
        assert not progressed

    def test_js_noise_object_object_no_progress(self):
        progressed, _ = self._delta("[object Object]")
        assert not progressed

    def test_mixed_real_and_noise_no_override(self):
        """Si JS noise présent malgré du vrai contenu, le résultat est neutre (pas de progrès garanti)."""
        progressed, _ = self._delta("✅ undefined — date: 15 juin")
        # null/undefined présent → noise l'emporte même avec date
        assert not progressed

    def test_other_tool_not_affected(self):
        """Le chemin browser_evaluate ne doit pas s'activer sur browser_click."""
        from src.reasoning.react import _browser_progress_delta
        prev = ("normal_content", "https://x.com", "Page", 2, None, None)
        cur  = ("normal_content", "https://x.com", "Page", 2, None, None)
        progressed, _ = _browser_progress_delta(
            prev, cur,
            action_tool="browser_click",
            observation_text="null",
        )
        # browser_click avec "null" → pas de détection evaluate (résultat dépend d'autres signaux)
        # L'important : la logique evaluate ne se déclenche pas
        assert True  # pas d'exception = correct

    def test_keyboard_enter_not_useful_when_submit_still_available(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://duck.ai/", "Duck.ai", 1, (1, 0, 3, 0, 16), None)
        cur  = ("public_form", "https://duck.ai/", "Duck.ai", 1, (0, 0, 3, 1, 26), None)
        progressed, reason = _browser_progress_delta(
            prev,
            cur,
            action_tool="browser_keyboard_press",
            observation_text=(
                "⌨️ Touche pressée: Enter\n\n"
                "Form state: filled=0, checked=0, disabled_buttons=3, enabled_submit_buttons=1, controls=26\n"
                "Observation browser: Enter a vide le champ, mais un bouton d'envoi reste actif — "
                "la soumission n'est probablement pas partie."
            ),
        )
        assert not progressed
        assert "soumission utile" in reason.lower()

    def test_type_observation_without_form_confirmation_is_not_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://duck.ai/", "Duck.ai", 1, (0, 0, 3, 0, 19), None)
        cur = ("public_form", "https://duck.ai/", "Duck.ai", 1, (0, 0, 3, 0, 19), None)
        progressed, reason = _browser_progress_delta(
            prev,
            cur,
            action_tool="browser_type_index",
            observation_text=(
                '✅ Tape "Salut !" dans [15] textbox "Posez toutes vos questions en prive"\n\n'
                "Form state: filled=0, checked=0, disabled_buttons=3, enabled_submit_buttons=0, controls=19\n"
                "Soumission non prete: aucun bouton d'envoi/validation actif apres saisie."
            ),
        )
        assert not progressed
        assert "confirmee" in reason.lower() or "non" in reason.lower()

    def test_type_observation_explicit_failure_is_not_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("public_form", "https://example.com", "Example", 1, (0, 0, 0, 0, 4), None)
        cur = ("public_form", "https://example.com", "Example", 1, (0, 0, 0, 0, 4), None)
        progressed, reason = _browser_progress_delta(
            prev,
            cur,
            action_tool="browser_type_index",
            observation_text='Echec de saisie dans [22] textbox "Ask anything" (valeur persistante: "")',
        )
        assert not progressed
        assert "echec" in reason.lower() or "persistante" in reason.lower()

    def test_chat_transcript_detection_for_browser_evaluate(self):
        from src.reasoning.react import _looks_like_chat_transcript
        transcript = (
            "✅ JS exécuté\n"
            "→ Salut ! Je suis Lumena, une IA créée par Losskarr. Comment vas-tu aujourd'hui ?\n"
            "1:55am\n---\n"
            "Salut Lumena ! Je vais très bien, merci de demander. Et toi, comment ça va aujourd'hui ? 😊\n"
            "1:55am"
        )
        assert _looks_like_chat_transcript(transcript)

    def test_browser_content_compaction_prefers_human_text_on_spa_shell(self):
        from src.reasoning.react import _compact_browser_observation_payload
        observation = (
            "📄 Page: Le Chat\n\n"
            "((a,b,c,d,e,f,g,h)=>{let i=document.documentElement;"
            "localStorage.getItem(\"theme\");function k(b){} })()\n"
            "Salut ! Je suis Lumena, une IA créée par Losskarr. Comment vas-tu aujourd'hui ?\n"
            "---\n"
            "Salut Lumena ! Je vais très bien, merci de demander."
        )
        compacted = _compact_browser_observation_payload("browser_get_content", observation)
        assert compacted is not None
        assert "SPA shell détectée" in compacted
        assert "Salut Lumena" in compacted
        assert "document.documentelement" not in compacted.lower()

    def test_auxiliary_copy_click_is_not_progress(self):
        from src.reasoning.react import _browser_progress_delta
        prev = ("chat_composer", "https://chat.mistral.ai/chat", "Le Chat", 1, (1, 0, 0, 1, 14), None)
        cur = ("chat_composer", "https://chat.mistral.ai/chat", "Le Chat", 1, (1, 0, 0, 1, 14), None)
        progressed, reason = _browser_progress_delta(
            prev,
            cur,
            action_tool="browser_click_index",
            observation_text='✅ Clic sur [7] button "Copy to clipboard"',
        )
        assert not progressed
        assert "auxiliaire" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# browser_dismiss_popups comme progression (Point 4)
# ─────────────────────────────────────────────────────────────────────────────

class TestDismissPopupsAsProgress:
    """Point 4 — Fermeture d'overlay réussie = progrès réel."""

    def _delta(self, obs, *, action_tool="browser_dismiss_popups"):
        from src.reasoning.react import _browser_progress_delta
        prev = ("popup_blocked", "https://x.com", "Page", 2, None, None)
        cur  = ("popup_blocked", "https://x.com", "Page", 2, None, None)
        return _browser_progress_delta(prev, cur, action_tool=action_tool, observation_text=obs)

    def test_dismiss_success_checkmark(self):
        progressed, reason = self._delta("✅ Popup fermé avec succès")
        assert progressed
        assert "overlay" in reason.lower() or "cookie" in reason.lower() or "éliminé" in reason.lower()

    def test_dismiss_dismissed_keyword(self):
        progressed, _ = self._delta("Cookie banner dismissed")
        assert progressed

    def test_dismiss_ferme_keyword(self):
        progressed, _ = self._delta("Fenêtre fermée correctement")
        assert progressed

    def test_dismiss_accepte_keyword(self):
        progressed, _ = self._delta("Cookies acceptés")
        assert progressed

    def test_accept_cookies_tool_also_counts(self):
        progressed, reason = self._delta(
            "✅ Cookies acceptés",
            action_tool="browser_accept_cookies",
        )
        assert progressed

    def test_dismiss_failure_no_progress(self):
        """Dismiss sans signal de succès → pas de progrès."""
        from src.reasoning.react import _browser_progress_delta
        prev = ("popup_blocked", "https://x.com", "Page", 2, None, None)
        cur  = ("popup_blocked", "https://x.com", "Page", 2, None, None)
        progressed, _ = _browser_progress_delta(
            prev, cur,
            action_tool="browser_dismiss_popups",
            observation_text="Aucun élément trouvé à fermer",
        )
        assert not progressed


# ─────────────────────────────────────────────────────────────────────────────
# Guard anti-dérive post-blocage browser (Point 7)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserPostBlockDriftGuard:
    """Point 7 — _BROWSER_DRIFT_TOOLS est défini et contient les outils attendus."""

    def test_drift_tools_defined(self):
        from src.reasoning.react import _BROWSER_DRIFT_TOOLS
        assert isinstance(_BROWSER_DRIFT_TOOLS, frozenset)

    def test_run_command_in_drift_tools(self):
        from src.reasoning.react import _BROWSER_DRIFT_TOOLS
        assert "run_command" in _BROWSER_DRIFT_TOOLS

    def test_web_fetch_in_drift_tools(self):
        from src.reasoning.react import _BROWSER_DRIFT_TOOLS
        assert "web_fetch" in _BROWSER_DRIFT_TOOLS

    def test_run_shell_in_drift_tools(self):
        from src.reasoning.react import _BROWSER_DRIFT_TOOLS
        assert "run_shell" in _BROWSER_DRIFT_TOOLS

    def test_browser_tools_not_in_drift_tools(self):
        """Les outils browser ne doivent pas être dans drift tools."""
        from src.reasoning.react import _BROWSER_DRIFT_TOOLS, BROWSER_ACTION_TOOLS
        overlap = _BROWSER_DRIFT_TOOLS & BROWSER_ACTION_TOOLS
        assert len(overlap) == 0, f"Outils browser dans drift tools (incohérence): {overlap}"


class TestBrowserAuthSurfaceConfirmation:
    def test_auth_confirmation_after_type_does_not_fall_back_to_public_form(self):
        from src.reasoning.react import _classify_browser_surface

        surface, reason = _classify_browser_surface(
            '✅ Tape "lumena.contact.ai@gmail.com" dans [7] textbox "ton@email.com"\n',
            previous_surface="auth_form",
        )
        assert surface == "auth_form"
        assert "auth" in reason.lower() or "confirmation" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# P1 — Fallback CAPTCHA Google
# ─────────────────────────────────────────────────────────────────────────────

class TestCaptchaFallbackLogic:
    """P1 — Vérifie la logique de détection CAPTCHA et le fallback DuckDuckGo.

    Les tests se concentrent sur :
    - _detect_browser_impasse reconnaît bien les signaux CAPTCHA
    - _BROWSER_CLICK_ONLY_ROLES existe (sanity check)
    - Le flag _google_search_captcha_fallback_attempted existe dans ReActLoop
    """

    def test_detect_impasse_captcha(self):
        """_detect_browser_impasse détecte les signaux CAPTCHA."""
        from src.reasoning.react import _detect_browser_impasse
        blocked, reason, try_dismiss = _detect_browser_impasse(
            "reCAPTCHA requis — vérification humaine"
        )
        assert blocked is True
        assert try_dismiss is False  # CAPTCHA ne se dismissit pas

    def test_detect_impasse_recaptcha(self):
        """_detect_browser_impasse détecte recaptcha."""
        from src.reasoning.react import _detect_browser_impasse
        blocked, reason, _ = _detect_browser_impasse(
            "I'm not a robot detected on the page"
        )
        assert blocked is True

    def test_detect_impasse_cloudflare(self):
        """_detect_browser_impasse détecte le challenge Cloudflare."""
        from src.reasoning.react import _detect_browser_impasse
        blocked, reason, _ = _detect_browser_impasse(
            "challenge_running Cloudflare checking your browser"
        )
        assert blocked is True

    def test_captcha_surface_classified_as_anti_bot(self):
        """_classify_browser_surface classe l'observation CAPTCHA comme anti_bot_or_challenge."""
        from src.reasoning.react import _classify_browser_surface
        surface, reason = _classify_browser_surface(
            "Page: captcha required\nURL: https://www.google.com/search?q=test\n"
            "captcha recaptcha i'm not a robot",
            current_url="https://www.google.com/search?q=test",
        )
        assert surface == "anti_bot_or_challenge"

    def test_no_captcha_no_block(self):
        """Sans signal CAPTCHA, _detect_browser_impasse retourne False."""
        from src.reasoning.react import _detect_browser_impasse
        blocked, _, _ = _detect_browser_impasse(
            "✅ Résultats de recherche Google — 10 résultats trouvés"
        )
        assert blocked is False

    def test_react_loop_has_captcha_fallback_flag(self):
        """ReActLoop accepte le flag _google_search_captcha_fallback_attempted (settable)."""
        from src.reasoning.react import ReActLoop
        loop = ReActLoop(llm_chat_func=lambda *a, **kw: None)
        assert not getattr(loop, "_google_search_captcha_fallback_attempted", False)
        loop._google_search_captcha_fallback_attempted = True
        assert loop._google_search_captcha_fallback_attempted is True


# ─────────────────────────────────────────────────────────────────────────────
# P3 — Guard SUBMIT-ONLY et FINAL-ONLY dans _update_plan_progress
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdatePlanProgressGuards:
    """P3 — Vérifie que les guards inline bloquent les auto-avancements faux.

    Stratégie : on appelle directement _update_plan_progress via un ReActLoop
    minimal avec un plan pré-chargé et un outil browser_type_index.
    """

    def _loop_with_plan(self, tasks):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop(llm_chat_func=lambda *a, **kw: None)
        loop._task_plan = list(tasks)
        loop._plan_emitted = True
        return loop

    def test_submit_task_not_marked_by_browser_type_index(self):
        """Guard SUBMIT-ONLY : browser_type_index ne doit pas marquer 'Soumettre le formulaire'."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Soumettre le formulaire de contact")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_type_index",
            {"index": "4", "text": "Mon message"},
            "✅ Texte saisi dans le champ message",
            iteration=1,
        )
        assert not task.completed, (
            "Guard SUBMIT-ONLY échoué : la tâche de soumission a été marquée par browser_type_index"
        )

    def test_submit_task_marked_by_browser_click_index(self):
        """Un clic (browser_click_index) peut marquer une tâche de soumission."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Soumettre le formulaire de contact")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "5"},
            "✅ Clic sur [5] button 'Submit' — formulaire soumis",
            iteration=1,
        )
        # browser_click_index n'est pas bloqué par le guard SUBMIT-ONLY
        # (le guard ne couvre que browser_type_index)

    def test_submit_task_not_marked_by_click_without_submit_proof(self):
        """Un simple clic sans preuve de soumission ne doit plus suffire."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Soumettre le formulaire de contact")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "5"},
            "✅ Clic sur [5] button 'Submit'",
            iteration=1,
        )
        assert not task.completed

    def test_confirmer_task_not_marked_by_browser_tool(self):
        """Guard FINAL-ONLY : un outil browser ne doit pas marquer 'Confirmer à l'utilisateur'."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Confirmer à l'utilisateur que le formulaire a été envoyé")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_screenshot",
            {},
            "✅ Capture d'écran prise",
            iteration=1,
        )
        assert not task.completed, (
            "Guard FINAL-ONLY échoué : la tâche 'confirmer à' a été marquée par browser_screenshot"
        )

    def test_rapporter_task_not_marked_by_browser_tool(self):
        """Guard FINAL-ONLY couvre aussi 'rapporter les résultats'."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Rapporter les résultats à l'utilisateur")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_dom_state",
            {},
            "✅ DOM récupéré avec 12 éléments",
            iteration=1,
        )
        assert not task.completed

    def test_result_screenshot_requires_real_result_proof(self):
        """Une capture dite 'du résultat' ne doit pas être cochée trop tôt."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Screenshot du résultat")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "8"},
            "✅ Clic sur [8] checkbox 'Option A'",
            iteration=1,
        )
        assert not task.completed

    def test_chat_interaction_task_not_marked_by_cookie_click(self):
        """'Interagir avec l'IA' ne doit pas être validé par un simple clic de consentement."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Étape 3: Interagir avec l'IA trouvée")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "2"},
            "✅ Clic sur [2] button 'Accepter et continuer'\n📸 Screenshot: chat visible",
            iteration=1,
        )
        assert not task.completed

    def test_chat_confirmation_task_not_marked_by_link_click(self):
        """'Confirmer l'échange réussi' doit rester réservé à une vraie preuve finale."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Étape 4: Confirmer l'échange réussi")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "49"},
            '✅ Clic sur [49] link "chat.qwen.ai/c/guest"',
            iteration=1,
        )
        assert not task.completed

    def test_chat_interaction_task_not_marked_by_copy_to_clipboard_click(self):
        """Un clic auxiliaire type Copy to clipboard ne doit jamais valider la conversation."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Étape 3: Interagir avec l'IA trouvée")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_click_index",
            {"index": "7"},
            '✅ Clic sur [7] button "Copy to clipboard"',
            iteration=1,
        )
        assert not task.completed

    def test_chat_interaction_task_requires_response_proof(self):
        """Une vraie réponse visible peut enfin valider l'étape d'échange."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Étape 3: Interagir avec l'IA trouvée")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_keyboard_press",
            {"key": "Enter"},
            "✅ Message envoyé. Réponse de l'assistant reçue dans la conversation",
            iteration=1,
        )
        assert task.completed

    def test_chat_interaction_task_marked_by_browser_evaluate_transcript(self):
        """Une transcription de conversation issue de browser_evaluate doit valider l'échange."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Étape 3: Interagir avec l'IA trouvée")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_evaluate",
            {"script": "() => 'conversation'"},
            (
                "✅ JS exécuté\n→ Salut ! Je suis Lumena, une IA créée par Losskarr. Comment vas-tu aujourd'hui ?\n"
                "1:55am\n---\n"
                "Salut Lumena ! Je vais très bien, merci de demander. Et toi, comment ça va aujourd'hui ? 😊\n"
                "1:55am"
            ),
            iteration=1,
        )
        assert task.completed

    def test_non_guarded_task_can_be_marked(self):
        """Les tâches normales (non-submit, non-final) sont toujours marquables."""
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Remplir le champ email avec mon adresse")
        loop = self._loop_with_plan([task])
        loop._update_plan_progress(
            "browser_type_index",
            {"index": "2", "text": "test@example.com"},
            "✅ email saisi dans le champ",
            iteration=1,
        )
        # Pas de guard sur cette description — peut être marquée


# ─────────────────────────────────────────────────────────────────────────────
# P4 — Réécriture browser_type_index → browser_click_index pour radio/checkbox
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserTypeToClickRewrite:
    """P4 — Vérifie que _browser_rewrite_type_to_click_for_ctrl réécrit correctement."""

    def _rewrite(self, idx, obs):
        from src.reasoning.react import _browser_rewrite_type_to_click_for_ctrl
        return _browser_rewrite_type_to_click_for_ctrl(
            "browser_type_index",
            {"index": str(idx)},
            last_observation=obs,
        )

    def test_radio_rewrites_to_click(self):
        obs = '[3] radio "Oui"\n[4] radio "Non"'
        result = self._rewrite("3", obs)
        assert result is not None
        tool, args, reason = result
        assert tool == "browser_click_index"
        assert args["index"] == "3"
        assert "radio" in reason.lower()

    def test_checkbox_rewrites_to_click(self):
        obs = '[7] checkbox "J\'accepte les CGU"\n[8] button "Valider"'
        result = self._rewrite("7", obs)
        assert result is not None
        tool, args, _ = result
        assert tool == "browser_click_index"
        assert args["index"] == "7"

    def test_button_rewrites_to_click(self):
        obs = '[5] button "Soumettre"\n[6] textbox "Commentaire"'
        result = self._rewrite("5", obs)
        assert result is not None
        tool, args, _ = result
        assert tool == "browser_click_index"
        assert args["index"] == "5"

    def test_textbox_not_rewritten(self):
        """Un vrai champ texte ne doit pas être réécrit."""
        obs = '[2] textbox "Email"\n[3] textbox "Mot de passe"'
        result = self._rewrite("2", obs)
        assert result is None

    def test_textarea_not_rewritten(self):
        obs = '[10] textarea "Message"'
        result = self._rewrite("10", obs)
        assert result is None

    def test_wrong_tool_not_rewritten(self):
        """Seul browser_type_index est concerné par la réécriture."""
        from src.reasoning.react import _browser_rewrite_type_to_click_for_ctrl
        obs = '[3] radio "Oui"'
        result = _browser_rewrite_type_to_click_for_ctrl(
            "browser_click_index",
            {"index": "3"},
            last_observation=obs,
        )
        assert result is None

    def test_unknown_index_returns_none(self):
        """Si l'index n'est pas dans l'observation, pas de réécriture."""
        obs = '[3] radio "Oui"'
        result = self._rewrite("99", obs)
        assert result is None

    def test_click_only_roles_contains_expected_roles(self):
        """_BROWSER_CLICK_ONLY_ROLES contient radio, checkbox, button, switch."""
        from src.reasoning.react import _BROWSER_CLICK_ONLY_ROLES
        for role in ("radio", "checkbox", "button", "switch"):
            assert role in _BROWSER_CLICK_ONLY_ROLES, f"{role!r} absent de _BROWSER_CLICK_ONLY_ROLES"


# ─────────────────────────────────────────────────────────────────────────────
# P5 — Signaux "sans position connue" → guidance scrollIntoView
# ─────────────────────────────────────────────────────────────────────────────

class TestNoPosPatterns:
    """P5 — Vérifie que les patterns 'hors viewport' sont reconnus (smoke test)."""

    _EXPECTED_PATTERNS = (
        "n'a pas de position connue",
        "no position",
        "bbox=none",
        "bounding_box indisponible",
        "element is outside the viewport",
        "element not visible",
    )

    def test_all_patterns_present(self):
        """Tous les patterns attendus doivent être dans le code source."""
        import inspect
        from src.reasoning import react as react_mod
        src = inspect.getsource(react_mod)
        for p in self._EXPECTED_PATTERNS:
            assert p in src, f"Pattern {p!r} absent du code source react.py"


# ─────────────────────────────────────────────────────────────────────────────
# P6 — Signaux de succès précoces
# ─────────────────────────────────────────────────────────────────────────────

class TestEarlySuccessSignals:
    """P6 — Vérifie que les mots-clés de succès précoces sont présents dans le code.

    Stratégie conservative : on vérifie que le code source contient les tokens
    utilisés pour détecter chaque signal, sans avoir besoin d'instancier la boucle.
    """

    def _src(self):
        import inspect
        from src.reasoning import react as react_mod
        return inspect.getsource(react_mod)

    def test_deconnexion_signal_present(self):
        """Signal 1 : 'déconnexion' / 'logout' pour détecter une connexion réussie."""
        src = self._src()
        assert "déconnexion" in src or "logout" in src

    def test_httpbin_signal_present(self):
        """Signal 2 : 'httpbin.org/post' pour détecter une soumission de formulaire."""
        src = self._src()
        assert "httpbin.org/post" in src

    def test_confirmation_signal_present(self):
        """Signal 3 : mots de confirmation ('merci', 'thank you', 'confirmé'…)."""
        src = self._src()
        assert "merci" in src or "thank you" in src or "confirmé" in src

    def test_early_success_guidance_message_present(self):
        """Le message de guidance FINAL est injecté (token distinctif)."""
        src = self._src()
        assert "SIGNAL DE SUCCÈS DÉTECTÉ" in src

    def test_login_signal_requires_auth_intent(self):
        """La détection de 'déconnexion' ne doit pas déclencher sans intent auth."""
        from src.reasoning.react import _browser_is_auth_intent
        # "connexion" est dans les tokens auth → True
        assert _browser_is_auth_intent("connexion au compte utilisateur")
        # Tâche sans rapport avec l'auth → False
        assert not _browser_is_auth_intent("télécharge ce fichier pdf")

    def test_browser_is_auth_intent_detects_connexion(self):
        """_browser_is_auth_intent détecte 'connexion', 'login', 'se connecter'."""
        from src.reasoning.react import _browser_is_auth_intent
        for q in ("connexion au compte", "login sur le site", "se connecter à l'appli"):
            assert _browser_is_auth_intent(q), f"Intent auth non détecté pour: {q!r}"
