"""LOT O1 — « relance » + « échéance » n'est PAS une relance d'impayé.

Run HuffPack v2 (2026-08-14). Message envoyé par l'utilisateur :

    Mission avec ÉCHÉANCE 90 minutes.
    […]
    - RELANCE pytest toi-même, publie, et ne conclus « livré » que si […]

Deux mots ordinaires — l'un technique (« relance pytest »), l'autre étant
précisément celui qui annonce une mission (« échéance ») — ont suffi à classer
la demande en `relance_impaye` avec une confiance de **1.0**.

La conséquence n'était pas cosmétique. Le rail Document Studio activé a refusé
`run_command` **trois fois** :

    03:59:13  [DOCUMENT STUDIO GATE] run_command refuse; types sans tentative
              Studio=['relance_impaye']

Pour exécuter la moindre commande, il fallait d'abord produire une lettre de
recouvrement. Douze itérations de lecture stérile, aucune écriture, et à
l'itération 9 la tentation de contourner : « Utilisons mcp__windows-mcp__
PowerShell au lieu de run_command » — le même réflexe que le CodeAgent du LOT N2
face à un garde incompréhensible.

La cause tient en une ligne : le commentaire du code affirmait « a professional
payment-reminder request necessarily contains "facture" », et la condition ne
testait jamais « facture ». Ce lot fait dire au code ce que son auteur avait
écrit.

Mesuré sur les messages réels : 1 seul des 16 est classé document (une lettre
officielle, légitime) ; les 3 vraies relances contiennent toutes « facture »,
les 2 usages techniques non.
"""
from __future__ import annotations

import pytest

from src.documents.document_intent import _resolve_kind, normalize_document_query


def _kind(query: str):
    return getattr(_resolve_kind(normalize_document_query(query)), "kind", None)


# ── LE cas du lot ───────────────────────────────────────────────────────────

def test_the_exact_message_that_paralysed_the_session():
    query = (
        "Mission avec échéance 90 minutes. Le codec HuffPack existe déjà. "
        "Méthode : relance pytest toi-même, publie, et ne conclus livré que si "
        "les 12 tests d'origine sont verts."
    )
    assert _kind(query) != "relance_impaye"


def test_relaunching_a_server_before_a_certificate_deadline():
    assert _kind("relance le serveur après l'échéance du certificat") != "relance_impaye"


def test_relaunching_a_build_while_talking_about_a_payment():
    assert _kind(
        "relance le build puis vérifie le paiement du plan cloud"
    ) != "relance_impaye"


def test_a_mission_deadline_alone_is_not_a_document():
    assert _kind("Mission avec échéance 120 minutes. Construis un SaaS.") != "relance_impaye"


# ── NON-RÉGRESSION : les vraies relances restent reconnues ──────────────────

@pytest.mark.parametrize(
    "query",
    [
        "relance la facture impayée du client Dupont",
        "fais une relance pour le paiement en retard de la facture 42",
        "relance de paiement pour facture échue",
        "relance l'impayé de M. Dupont",
        "relance de créance pour le débiteur Martin",
        "relance de recouvrement niveau 2",
        "relance des impayés du trimestre",
    ],
)
def test_a_real_payment_reminder_is_still_detected(query):
    assert _kind(query) == "relance_impaye"


def test_the_unambiguous_words_stand_alone():
    """« impayé », « créance », « débiteur », « recouvrement » ne veulent rien
    dire d'autre : ils n'ont pas besoin du mot « facture »."""
    for word in ("impayé", "créance", "débiteur", "recouvrement"):
        assert _kind(f"relance {word}") == "relance_impaye", word


def test_a_reminder_phrased_without_any_debt_word_is_a_known_limit():
    """LIMITE ASSUMÉE, documentée plutôt que devinée.

    « prépare une relance : le montant du règlement est en retard » est
    sémantiquement une relance d'impayé, et n'est PAS détectée : aucun mot
    non ambigu, et ni « paiement » ni « échéance ».

    Je n'élargis pas la règle pour ce cas, parce que je ne l'ai jamais observé :
    les 3 vraies relances mesurées contiennent toutes « facture ». Élargir sur
    une phrase imaginée reproduirait le défaut d'origine — une inférence prise
    pour un fait.

    L'asymétrie des coûts tranche : ne pas détecter coûte une reformulation à
    l'utilisateur ; détecter à tort PARALYSE la session (`run_command` refusé).
    """
    assert _kind(
        "prépare une relance : le montant du règlement est en retard"
    ) != "relance_impaye"


def test_the_polysemic_words_need_a_debt_object():
    """« paiement » et « échéance » servent partout — seuls, ils ne suffisent
    plus ; accompagnés d'un objet de créance, ils suffisent."""
    assert _kind("relance le paiement") != "relance_impaye"
    assert _kind("relance l'échéance") != "relance_impaye"
    assert _kind("relance le paiement de la facture") == "relance_impaye"
    assert _kind("relance l'échéance du solde") == "relance_impaye"


# ── robustesse ──────────────────────────────────────────────────────────────

def test_the_word_relance_alone_is_never_enough():
    for query in ("relance", "relance le test", "relance la page", "relance ça"):
        assert _kind(query) != "relance_impaye", query


def test_a_debt_word_without_relance_is_not_this_kind():
    """Sans « relance », ce n'est pas une RELANCE (ce peut être une facture)."""
    assert _kind("crée une facture impayée") != "relance_impaye"


def test_empty_and_garbage_never_raise():
    for query in ("", "   ", "?!@#", "a" * 3000):
        assert isinstance(_kind(query), (str, type(None)))


# ── le garde-fou : la règle dit ce que son commentaire promet ──────────────

def test_the_rule_now_matches_its_own_comment():
    """Le commentaire annonçait « necessarily contains facture » alors que la
    condition ne le testait pas. Ce test empêche l'écart de revenir."""
    import inspect

    from src.documents import document_intent

    src = inspect.getsource(document_intent._resolve_kind)
    head = src.split("relance facture impayee")[0]
    assert "factur" in head, "le contexte de créance doit figurer dans la condition"
    assert "echeance" in head
