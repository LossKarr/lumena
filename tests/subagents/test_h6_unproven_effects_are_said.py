"""H6 — l'effet non réalisé est DIT dans le bilan, pas seulement en métadonnée.

Run `veille_python_313` (2026-08-13). Le contrat engageait deux effets :
`w_recherche` (3 sources) et `w_redaction` (synthèse enregistrée en mémoire).
`w_redaction` a échoué. Le bilan livré présentait les 3 sources… et ne disait
**nulle part** que la synthèse n'avait pas été faite.

L'état était pourtant fidèle — `completion_proven=False`,
`terminal_reason_code=completed_effects_unproven` — et la liste des porteurs en
défaut était calculée puis journalisée. Elle n'était simplement jamais livrée.

C'est le motif de toute cette session, une dernière fois : le fait existe, il est
calculé, il est même affiché dans les logs, et il est jeté avant d'atteindre la
seule personne qui en a besoin. L'utilisateur lit le texte, pas les métadonnées.
"""
from __future__ import annotations

from src.subagents.runner import annotate_unproven_effects


def test_the_missing_effect_is_named():
    out = annotate_unproven_effects("Voici les 3 sources.", ["w_redaction"])
    assert "w_redaction" in out


def test_the_original_report_is_preserved():
    """Additif, comme les bannières du truth-lock : on ne réécrit jamais le fond."""
    out = annotate_unproven_effects("Voici les 3 sources.", ["w_redaction"])
    assert out.endswith("Voici les 3 sources.")


def test_the_warning_comes_first():
    out = annotate_unproven_effects("Voici les 3 sources.", ["w_redaction"])
    assert out.startswith("⚠️")


def test_it_warns_that_the_text_may_look_complete():
    """Le piège du run : le bilan avait l'air d'un succès."""
    out = annotate_unproven_effects("Tout est prêt !", ["w_x"])
    assert "ne couvre PAS" in out


def test_several_owners_are_all_named():
    out = annotate_unproven_effects("Rapport.", ["w_mail", "w_slack"])
    assert "w_mail" in out and "w_slack" in out


def test_a_clean_mission_is_untouched():
    """Le risque est d'inquiéter sur une mission parfaitement remplie."""
    assert annotate_unproven_effects("Tout est fait.", []) == "Tout est fait."
    assert annotate_unproven_effects("Tout est fait.", None) == "Tout est fait."


def test_blank_owners_are_ignored():
    assert annotate_unproven_effects("Rapport.", ["", "   ", None]) == "Rapport."


def test_it_is_idempotent_across_a_recovery():
    """Une mission reprise ne doit pas empiler deux bannières identiques."""
    once = annotate_unproven_effects("Rapport.", ["w_1"])
    assert annotate_unproven_effects(once, ["w_1"]) == once


def test_an_empty_report_still_carries_the_warning():
    out = annotate_unproven_effects("", ["w_1"])
    assert "w_1" in out


def test_garbage_never_raises():
    assert annotate_unproven_effects(None, None) == ""
    assert annotate_unproven_effects(None, ["w_1"]).startswith("⚠️")
