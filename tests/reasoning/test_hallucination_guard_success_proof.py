"""Tests comportementaux — Guard anti-hallucination basé sur outils RÉUSSIS.

Vérifie que :
- successful_session_tools ne contient PAS un outil dont l'observation a échoué
- successful_session_tools contient un outil dont l'observation a réussi
- la logique du guard (any(t in known for t in expected)) blocque bien quand
  le seul outil présent est un outil en échec
- cas couverts: mail, telegram/messaging, stripe, fichier/document
"""

import re
import pytest
from src.reasoning.agent_execution_state import AgentExecutionState
from src.reasoning.react import (
    _HC_TOOLS_MAIL,
    _HC_TOOLS_MESSAGING,
    _HC_TOOLS_STRIPE,
    _HC_TOOLS_FILE,
    _HC_TOOLS_DOC,
    _HC_TOOLS_DISCORD,
    _HC_TOOLS_SOCIAL,
    _HC_TOOLS_ANY_CREATE,
    _HC_TOOLS_ANY_SEND,
    _HC_TOOLS_RUNTIME,
    _has_runtime_server_claim_proof,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _guard_would_block(known_tools: set, pattern: str, expected_tools) -> bool:
    """Simule la décision du guard : True = bloqué, False = autorisé."""
    if re.search(pattern, "texte test", re.IGNORECASE):
        return not any(t in known_tools for t in expected_tools)
    return False


def _guard_check(known_tools: set, text: str, patterns) -> bool:
    """Simule le parcours complet de _HALLUCINATION_PATTERNS.
    Retourne True si au moins un pattern déclenche un blocage."""
    for _pattern, _expected in patterns:
        if re.search(_pattern, text, re.IGNORECASE):
            if not any(t in known_tools for t in _expected):
                return True  # bloqué
    return False  # non bloqué


# ─────────────────────────────────────────────────────────────────────────────
# AgentExecutionState — comportement de successful_session_tools
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessfulSessionToolsTracking:
    def test_failed_tool_not_in_successful_set(self):
        """Un outil appelé avec success=False ne doit PAS être dans successful_session_tools."""
        state = AgentExecutionState()
        state.all_session_tools.add("mail_send")
        # NE PAS ajouter à successful_session_tools (simuler observation.success=False)
        assert "mail_send" not in state.successful_session_tools

    def test_successful_tool_in_successful_set(self):
        """Un outil appelé avec success=True doit être dans successful_session_tools."""
        state = AgentExecutionState()
        state.all_session_tools.add("mail_send")
        state.successful_session_tools.add("mail_send")
        assert "mail_send" in state.successful_session_tools

    def test_sets_diverge_on_failure(self):
        """all_session_tools et successful_session_tools divergent quand un outil échoue."""
        state = AgentExecutionState()
        state.all_session_tools.add("stripe_send_invoice")
        # Pas dans successful_session_tools → échec simulé
        assert "stripe_send_invoice" in state.all_session_tools
        assert "stripe_send_invoice" not in state.successful_session_tools

    def test_sets_agree_on_success(self):
        """Les deux sets contiennent l'outil quand il réussit."""
        state = AgentExecutionState()
        state.all_session_tools.add("telegram_send_message")
        state.successful_session_tools.add("telegram_send_message")
        assert "telegram_send_message" in state.all_session_tools
        assert "telegram_send_message" in state.successful_session_tools

    def test_reset_preserves_both_sets(self):
        """reset() ne vide ni all_session_tools ni successful_session_tools."""
        state = AgentExecutionState()
        state.all_session_tools.add("write_file")
        state.successful_session_tools.add("write_file")
        state.reset()
        assert "write_file" in state.all_session_tools
        assert "write_file" in state.successful_session_tools

    def test_snapshot_includes_successful_tools(self):
        """snapshot() expose successful_session_tools pour debug."""
        state = AgentExecutionState()
        state.successful_session_tools.add("create_pdf")
        snap = state.snapshot()
        assert "successful_session_tools" in snap
        assert "create_pdf" in snap["successful_session_tools"]

    def test_snapshot_successful_tools_sorted(self):
        state = AgentExecutionState()
        state.successful_session_tools.update({"zzz_tool", "aaa_tool"})
        snap = state.snapshot()
        assert snap["successful_session_tools"] == sorted(snap["successful_session_tools"])


# ─────────────────────────────────────────────────────────────────────────────
# Guard logic — mail
# ─────────────────────────────────────────────────────────────────────────────

_MAIL_PATTERN = (
    r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b",
    _HC_TOOLS_MAIL,
)
_GENERIC_SEND_PATTERN = (
    r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b",
    _HC_TOOLS_ANY_SEND,
)

class TestGuardMail:
    def test_blocks_when_mail_send_failed(self):
        """mail_send appelé mais en échec → guard bloque."""
        known = set()  # mail_send a échoué → absent de successful_session_tools
        text = "j'ai envoyé l'email avec succès"
        blocked = _guard_check(known, text, [_GENERIC_SEND_PATTERN])
        assert blocked, "Le guard devrait bloquer quand mail_send a échoué"

    def test_allows_when_mail_send_succeeded(self):
        """mail_send réussi → guard laisse passer."""
        known = {"mail_send"}
        text = "j'ai envoyé l'email avec succès"
        blocked = _guard_check(known, text, [_GENERIC_SEND_PATTERN])
        assert not blocked, "Le guard ne devrait pas bloquer quand mail_send a réussi"

    def test_blocks_explicit_mail_pattern_on_failure(self):
        known = set()
        text = "le courriel a été envoyé"
        blocked = _guard_check(known, text, [_MAIL_PATTERN])
        assert blocked

    def test_allows_explicit_mail_pattern_on_success(self):
        known = {"send_email"}
        text = "le courriel a été envoyé"
        blocked = _guard_check(known, text, [_MAIL_PATTERN])
        assert not blocked

    def test_all_session_tools_alone_insufficient(self):
        """all_session_tools contient mail_send (appelé) mais successful ne le contient pas → blocage."""
        all_tools = {"mail_send"}      # l'outil a été appelé
        successful_tools = set()      # mais a échoué
        # Le guard utilise successful_tools, pas all_tools
        text = "j'ai envoyé l'email"
        blocked = _guard_check(successful_tools, text, [_GENERIC_SEND_PATTERN])
        assert blocked, "successful vide → doit bloquer même si all_session contient mail_send"


class TestRuntimeServerClaimProof:
    def test_runtime_claim_requires_runtime_tool(self):
        text = "Le serveur tourne bien en arrière-plan sur localhost:3000"
        assert not _has_runtime_server_claim_proof(text, {"run_command"})
        assert _has_runtime_server_claim_proof(text, {"process_status"})

    def test_runtime_family_contains_process_status(self):
        assert "process_status" in _HC_TOOLS_RUNTIME


# ─────────────────────────────────────────────────────────────────────────────
# Guard logic — telegram / messaging
# ─────────────────────────────────────────────────────────────────────────────

_MESSAGING_PATTERN = (
    r"\b(message|messages).{0,20}(envoyé|envoye|posté|poste|publié|publie)\b",
    _HC_TOOLS_MESSAGING | _HC_TOOLS_MAIL | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL,
)

class TestGuardMessaging:
    def _patterns(self):
        return [
            (
                r"\b(message|messages).{0,20}(envoyé|envoye|posté|poste|publié|publie)\b",
                _HC_TOOLS_MESSAGING | _HC_TOOLS_MAIL | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL,
            ),
            (
                r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b",
                _HC_TOOLS_ANY_SEND,
            ),
        ]

    def test_blocks_telegram_failed(self):
        known = set()  # telegram_send_message a échoué
        text = "le message Telegram a été envoyé"
        blocked = _guard_check(known, text, self._patterns())
        assert blocked

    def test_allows_telegram_succeeded(self):
        known = {"telegram_send_message"}
        text = "le message Telegram a été envoyé"
        blocked = _guard_check(known, text, self._patterns())
        assert not blocked

    def test_blocks_whatsapp_failed(self):
        known = set()
        text = "j'ai envoyé le message WhatsApp"
        blocked = _guard_check(known, text, self._patterns())
        assert blocked

    def test_allows_whatsapp_succeeded(self):
        known = {"send_whatsapp_message"}
        text = "j'ai envoyé le message WhatsApp"
        blocked = _guard_check(known, text, self._patterns())
        assert not blocked


# ─────────────────────────────────────────────────────────────────────────────
# Guard logic — stripe
# ─────────────────────────────────────────────────────────────────────────────

_STRIPE_PATTERN = (
    r"\b(produit|abonnement|facture|paiement|remboursement).{0,20}(créé[e]?|crée[e]?|envoyé[e]?|annulé[e]?)\b",
    _HC_TOOLS_STRIPE,
)

class TestGuardStripe:
    def test_blocks_stripe_invoice_failed(self):
        known = set()  # stripe_create_invoice a échoué
        text = "la facture Stripe a été créée"
        blocked = _guard_check(known, text, [_STRIPE_PATTERN])
        assert blocked

    def test_allows_stripe_invoice_succeeded(self):
        known = {"stripe_create_invoice"}
        text = "la facture Stripe a été créée"
        blocked = _guard_check(known, text, [_STRIPE_PATTERN])
        assert not blocked

    def test_blocks_stripe_product_failed(self):
        known = set()
        text = "le produit a été créé sur Stripe"
        blocked = _guard_check(known, text, [_STRIPE_PATTERN])
        assert blocked

    def test_allows_stripe_product_succeeded(self):
        known = {"stripe_create_product"}
        text = "le produit a été créé sur Stripe"
        blocked = _guard_check(known, text, [_STRIPE_PATTERN])
        assert not blocked

    def test_stripe_failed_tool_in_all_but_not_successful(self):
        """Régression clé : all_session ne suffit pas si l'outil a échoué.

        Si le guard utilisait all_tools, il ne bloquerait pas (outil présent).
        Mais le guard utilise successful_tools, qui est vide → blocage correct.
        """
        all_tools = {"stripe_create_subscription"}   # appelé mais en échec
        successful_tools: set = set()                # vide car l'outil a échoué
        text = "l'abonnement a été créé"

        # Vérification de la régression : all_tools NE BLOQUERAIT PAS (outil présent)
        assert _guard_check(all_tools, text, [_STRIPE_PATTERN]) is False, (
            "all_tools contient l'outil → ne bloquerait pas si le guard l'utilisait (ancienne faille)"
        )
        # Le guard utilise successful_tools → blocage car vide
        assert _guard_check(successful_tools, text, [_STRIPE_PATTERN]) is True, (
            "successful_tools vide → le guard doit bloquer l'affirmation"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Guard logic — fichier / document
# ─────────────────────────────────────────────────────────────────────────────

_CREATE_PATTERN = (
    r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|configuré|configure|programmé|programme|ajouté|ajoute|sauvegardé|sauvegarde)\b",
    _HC_TOOLS_ANY_CREATE,
)

class TestGuardFileDoc:
    def test_blocks_write_file_failed(self):
        known = set()  # write_file a échoué
        text = "j'ai créé le fichier"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert blocked

    def test_allows_write_file_succeeded(self):
        known = {"write_file"}
        text = "j'ai créé le fichier"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert not blocked

    def test_blocks_create_pdf_failed(self):
        known = set()
        text = "j'ai créé le document PDF"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert blocked

    def test_allows_create_pdf_succeeded(self):
        known = {"create_pdf"}
        text = "j'ai créé le document PDF"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert not blocked

    def test_blocks_create_docx_failed(self):
        known = set()
        text = "j'ai enregistré le document Word"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert blocked

    def test_allows_create_docx_succeeded(self):
        known = {"create_docx"}
        text = "j'ai enregistré le document Word"
        blocked = _guard_check(known, text, [_CREATE_PATTERN])
        assert not blocked

    def test_failed_tool_only_in_all_does_not_bypass(self):
        """Régression : seul successful_tools autorise — all_tools ne doit pas suffire."""
        all_tools = {"write_file", "edit_file"}  # tous appelés mais en échec
        successful_tools: set = set()
        text = "j'ai créé le fichier de configuration"
        blocked = _guard_check(successful_tools, text, [_CREATE_PATTERN])
        assert blocked, "Sans outil réussi, le guard doit bloquer même si all contient write_file"

    def test_partial_success_allows(self):
        """Si au moins UN outil de la famille a réussi, le guard passe."""
        # edit_file a échoué mais apply_patch a réussi
        successful_tools = {"apply_patch"}
        text = "j'ai créé le fichier"
        blocked = _guard_check(successful_tools, text, [_CREATE_PATTERN])
        assert not blocked


# ─────────────────────────────────────────────────────────────────────────────
# Régression globale : tous les outils échoués = toujours bloqué
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobalRegressionAllFailed:
    """Scénario pessimiste : tous les outils de la session ont échoué."""

    def _all_patterns(self):
        from src.reasoning.react import _HC_TOOLS_GITHUB
        return [
            (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|configuré|configure|programmé|programme|ajouté|ajoute|sauvegardé|sauvegarde)\b", _HC_TOOLS_ANY_CREATE),
            (r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b", _HC_TOOLS_ANY_SEND),
            (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b", _HC_TOOLS_MAIL),
            (r"\b(produit|abonnement|facture|paiement|remboursement).{0,20}(créé[e]?|crée[e]?|envoyé[e]?|annulé[e]?)\b", _HC_TOOLS_STRIPE),
            (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|commit réussi|commit reussi)\b", _HC_TOOLS_GITHUB),
        ]

    @pytest.mark.parametrize("text", [
        "j'ai envoyé l'email",
        "j'ai créé le fichier",
        "la facture a été créée",
        "le produit a été créé",
        "push réussi sur GitHub",
        "j'ai enregistré la tâche",
    ])
    def test_all_failed_tools_blocks_all_claims(self, text):
        """Aucun outil réussi → toutes les affirmations d'action doivent être bloquées."""
        successful_tools: set = set()
        blocked = _guard_check(successful_tools, text, self._all_patterns())
        assert blocked, f"Devrait être bloqué : '{text}' avec successful_tools vide"
