"""Guards read-only vs mutation (2026-05-21).

Corrige les faux positifs DISCORD ACTION GUARD et LEDGER GUARD sur des missions
read-only, en réutilisant l'architecture existante (ProofCapability +
ToolRegistry + ExecutionLedger). Aucune nouvelle taxonomie.

Principe anti-hallucination : un outil/catégorie INCONNU n'est jamais considéré
read-only (le fallback GENERIC_READONLY de get_tool_capabilities est trop
permissif pour bypasser un guard).
"""

from __future__ import annotations

from src.reasoning.plan_evidence import tool_capabilities_are_known_readonly
from src.reasoning.tool_categories import get_semantic_category
from src.reasoning.react import (
    ReActLoop,
    discord_requires_send,
    mission_expects_mutation,
    claim_text_is_negated,
    claim_match_is_negated,
)


# ─── Stub ToolRegistry : reproduit fidèlement la résolution réelle ───────────
# get_tool_semantic_category = get_semantic_category(module_category), comme le
# vrai ToolRegistry (tool_registry.py:1419).
class _StubTools:
    _MODS = {
        "memory_stats": "memory",
        "memory_search": "memory",
        "list_skills": "skills",
        "get_lumena_config": "system",
        "discord_send": "discord",
        "parallel_tools": "system",
    }

    def get_tool_module_category(self, name: str) -> str:
        return self._MODS.get(name, "")

    def get_tool_semantic_category(self, name: str) -> str:
        mod = self.get_tool_module_category(name)
        return get_semantic_category(mod) if mod else ""


def _loop() -> ReActLoop:
    loop = ReActLoop(llm_chat_func=None)
    loop.tools = _StubTools()
    return loop


# ─── Helper pur : tool_capabilities_are_known_readonly ───────────────────────


def test_pure_discord_send_not_readonly():
    """discord_send (communication → MESSAGE_SEND) n'est jamais read-only."""
    assert tool_capabilities_are_known_readonly(
        "discord_send", "discord", get_semantic_category("discord")
    ) is False


def test_pure_known_readonly_via_real_category():
    """memory_stats / list_skills / get_lumena_config = read-only via catégorie réelle."""
    assert tool_capabilities_are_known_readonly(
        "memory_stats", "memory", get_semantic_category("memory")
    ) is True
    # list_skills / get_lumena_config : module "system" (défaut PROCESS_LAUNCH)
    # mais override read-only explicite — l'override prime.
    assert tool_capabilities_are_known_readonly(
        "list_skills", "skills", get_semantic_category("skills")
    ) is True
    assert tool_capabilities_are_known_readonly(
        "get_lumena_config", "system", get_semantic_category("system")
    ) is True


def test_pure_unknown_tool_not_bypassable():
    """Outil inconnu (aucune catégorie) → non bypassable (False)."""
    assert tool_capabilities_are_known_readonly("outil_inconnu_xyz", "", "") is False
    # Catégorie sémantique non vide mais absente du mapping → toujours False.
    assert tool_capabilities_are_known_readonly("outil_inconnu_xyz", "", "categorie_bidon") is False


# ─── Helper guard-safe : _tool_is_safe_readonly ──────────────────────────────


def test_guardsafe_discord_send_not_readonly():
    loop = _loop()
    assert loop._tool_is_safe_readonly("discord_send") is False


def test_guardsafe_known_readonly():
    loop = _loop()
    assert loop._tool_is_safe_readonly("memory_stats") is True
    assert loop._tool_is_safe_readonly("list_skills") is True
    assert loop._tool_is_safe_readonly("get_lumena_config") is True


def test_guardsafe_unknown_not_readonly():
    loop = _loop()
    assert loop._tool_is_safe_readonly("outil_inconnu_xyz") is False
    assert loop._tool_is_safe_readonly("") is False


def test_guardsafe_parallel_tools_not_business_readonly():
    """parallel_tools (agrégateur) ne doit pas être traité comme outil métier
    read-only : le verdict s'évalue sur ses sous-outils réels, pas sur lui."""
    loop = _loop()
    assert loop._tool_is_safe_readonly("parallel_tools") is False


# ─── Discord guard : discord_requires_send ───────────────────────────────────


def test_discord_control_no_send_no_guard():
    """« contrôle les canaux Discord sans envoyer » → pas de Discord guard."""
    assert discord_requires_send("contrôle les canaux Discord sans envoyer") is False
    assert discord_requires_send("liste les canaux discord et leur statut") is False
    assert discord_requires_send("fais un rapport sur l'activité discord") is False


def test_discord_positive_send_keeps_guard():
    """« poste un message sur Discord » → guard maintenu (demande positive)."""
    assert discord_requires_send("poste un message sur Discord") is True
    assert discord_requires_send("envoie un récap sur discord") is True
    assert discord_requires_send("anime le discord ce soir") is True


def test_discord_negation_disarms_guard():
    assert discord_requires_send("ne poste pas sur discord, juste vérifie") is False
    assert discord_requires_send("envoie un mail à l'équipe") is False  # pas de "discord"


def test_discord_negation_apostrophe_variants():
    """La négation doit être détectée quelle que soit l'apostrophe/casse."""
    # Apostrophe droite (ASCII).
    assert discord_requires_send("N'envoie rien sur Discord") is False
    # Apostrophe typographique (U+2019).
    assert discord_requires_send("N’envoie rien sur Discord") is False
    # Apostrophe + espace parasite.
    assert discord_requires_send("n' envoie rien sur le discord") is False


def test_discord_negation_rien_aucun_rule():
    """Règle générale négation + verbe + rien/aucun (prompt UI prod)."""
    assert discord_requires_send("Ne poste aucun message sur Discord") is False
    assert discord_requires_send("Ne poste rien sur Discord") is False
    assert discord_requires_send("ne publie rien sur discord") is False
    # Cas positif conservé.
    assert discord_requires_send("poste un message sur Discord") is True


def test_mission_negation_rien_aucun_rule():
    """Les mêmes négations désarment aussi la mutation attendue."""
    assert mission_expects_mutation("Ne poste aucun message sur Discord") is False
    assert mission_expects_mutation("Ne poste rien sur Discord") is False
    assert mission_expects_mutation("ne publie rien sur discord") is False


# ─── Ledger guard : mission_expects_mutation ─────────────────────────────────


def test_mutation_expected_positive_verbs():
    assert mission_expects_mutation("crée le fichier rapport.txt") is True
    assert mission_expects_mutation("envoie le message à l'équipe") is True
    assert mission_expects_mutation("modifie la config") is True
    assert mission_expects_mutation("supprime le doublon") is True


def test_mutation_not_expected_readonly_report():
    """Rapport read-only → aucune mutation attendue."""
    assert mission_expects_mutation("fais un rapport mémoire") is False
    assert mission_expects_mutation("vérifie le statut des providers") is False
    assert mission_expects_mutation("liste mes skills disponibles") is False


def test_mutation_negation_is_readonly():
    """Une négation explicite désarme la mutation attendue, même kind DELIVERY."""
    assert mission_expects_mutation("contrôle les canaux Discord sans envoyer") is False


def test_mutation_webapp_api_script_not_mutation():
    """WEB_APP / API / SCRIPT seuls ne déclenchent pas mutation attendue."""
    assert mission_expects_mutation("vérifie que l'API répond sur le endpoint") is False
    assert mission_expects_mutation("regarde si le serveur web tourne sur le port 8000") is False


def test_document_read_only_not_mutation():
    """DOCUMENT en lecture/analyse/vérification reste read-only."""
    assert mission_expects_mutation("ouvre le fichier rapport et résume son contenu") is False
    assert mission_expects_mutation("vérifie le fichier rapport") is False
    assert mission_expects_mutation("fais un rapport pdf sans créer de fichier") is False
    # Sans accents : la normalisation doit aussi désarmer la négation.
    assert mission_expects_mutation("fais un rapport pdf sans creer de fichier") is False


def test_document_creation_is_mutation():
    """DOCUMENT avec demande de création/export/écriture = mutation attendue."""
    assert mission_expects_mutation("crée un fichier rapport PDF") is True
    assert mission_expects_mutation("génère un document DOCX") is True
    assert mission_expects_mutation("exporte le rapport en XLSX") is True


# ─── HALLUCINATION GUARD : claim négatif vs positif ──────────────────────────


def test_hallucination_negative_claims_not_blocked():
    """Claims NÉGATIFS → ignorés par le hallucination guard."""
    for phrase in (
        "aucun message envoyé",
        "0 message envoyé",
        "pas de message envoyé",
        "rien envoyé",
        "je n'ai rien envoyé",
        "je n’ai rien envoyé",  # apostrophe typographique
        "aucun tweet posté",
        "aucun fichier créé",
        "aucun fichier modifié",
        "aucun document généré",
        "aucun déploiement effectué",
    ):
        assert claim_text_is_negated(phrase) is True, phrase


def test_hallucination_positive_claims_still_blocked():
    """Claims POSITIFS → restent des affirmations à prouver (non niés)."""
    for phrase in (
        "j'ai envoyé le message",
        "le message a été envoyé",
        "j'ai posté le tweet",
        "le tweet est posté",
        "j'ai créé le fichier",
        "le fichier a été créé",
        "j'ai généré le document",
        "j'ai déployé le site",
    ):
        assert claim_text_is_negated(phrase) is False, phrase


def test_hallucination_negation_not_loose():
    """Présence de rien/pas/aucun SANS lien syntaxique au claim → non nié."""
    # "aucun problème" mais "le tweet posté" → claim positif, pas une négation.
    assert claim_text_is_negated("aucun problème, le tweet posté avec succès") is False
    assert claim_text_is_negated("pas de souci, le fichier créé est prêt") is False
    assert claim_text_is_negated("rien à signaler, le message envoyé est parti") is False


def test_hallucination_negation_window_in_context():
    """La négation est détectée dans la proposition contenant le claim."""
    text = "voici le bilan : aucun message envoyé sur discord, tout est read-only."
    idx = text.index("message")
    assert claim_match_is_negated(text, idx, idx + len("message envoyé")) is True
    # Claim positif dans une phrase sans négation proche.
    text2 = "rapport final : j'ai créé le fichier rapport.pdf avec succès."
    idx2 = text2.index("créé")
    assert claim_match_is_negated(text2, idx2 - 6, idx2 + 4) is False
