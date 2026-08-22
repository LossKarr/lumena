"""H7 — pour une mission d'EFFETS, le rapport des workers EST le livrable.

TEST RÉEL n°2 (mission `uv`, 2026-08-13). La mission a parfaitement réussi :
2 workers coordonnés, mémo réellement écrit en mémoire
(`Mémoire ajoutée: mem_20260813_034116_199447` au log du store). Le bilan livré
était pourtant :

    **Délégation**
    Délégation : 2/2 terminée(s) :

Ce deux-points annonçait une liste qui n'arrivait jamais. Le bilan de secours ne
gardait que `observation.splitlines()[0]` — l'en-tête — et jetait tout ce qui
suit : **les rapports des workers**, que l'observation elle-même présente comme
« Ce sont les LIVRABLES des workers ».

Pour une mission de CODE, le livrable est sur le disque et un bilan maigre passe.
Pour une mission d'EFFETS (H4), ce rapport est **le seul endroit** où le livrable
existe — le mémo n'apparaissait nulle part.

Le motif de toute la session, une dernière fois : le fait était présent dans
l'observation, et tronqué avant d'atteindre l'utilisateur.
"""
from __future__ import annotations

from src.reasoning.react import _synthesize_mission_response_from_evidence as _summary

_OBS = """Délégation : 2/2 terminée(s) :

[w_recherche] 2 sources : docs.astral.sh/uv et realpython.com
[w_redaction] Mémo enregistré en mémoire (memory_add, importance=0.6) :
uv est un gestionnaire de paquets Python ultra-rapide écrit en Rust par Astral.

➡️ Ce sont les LIVRABLES des workers. Fusionne-les DIRECTEMENT à partir d'ici \
(la version longue de chacun est conservée via mission_result(<id>))."""


def _out(obs=_OBS):
    return _summary([("delegate_and_wait", obs, True)]) or ""


def test_the_deliverable_reaches_the_report():
    """Le cas du run : le mémo était le livrable et disparaissait."""
    assert "ultra-rapide écrit en Rust" in _out()


def test_every_worker_is_represented():
    out = _out()
    assert "w_recherche" in out and "w_redaction" in out


def test_the_header_is_still_there():
    assert "2/2 terminée(s)" in _out()


def test_internal_steering_is_stripped():
    """« Fusionne-les DIRECTEMENT » s'adresse au LEAD pendant le run ; livré à
    l'utilisateur, c'est du bruit incompréhensible."""
    out = _out()
    assert "Fusionne-les" not in out
    assert "mission_result(" not in out


def test_the_partial_warning_is_stripped_too():
    obs = (
        "Délégation : 1/2 terminée(s) ⏳ (délai dépassé) :\n\n"
        "[w_a] fait\n\n"
        "⛔ RÉSULTAT PARTIEL — des workers travaillent ENCORE sur les fichiers."
    )
    out = _out(obs)
    assert "[w_a] fait" in out
    assert "RÉSULTAT PARTIEL" not in out
    assert "délai dépassé" in out, "l'en-tête, lui, reste une information utile"


def test_a_long_report_is_capped():
    obs = "Délégation : 1/1 terminée(s) :\n\n" + ("x" * 5000)
    assert len(_out(obs)) < 3000


def test_the_fallback_still_says_what_it_is():
    assert "Bilan de secours" in _out()


def test_no_evidence_no_report():
    assert _summary([]) is None


def test_a_failed_delegation_is_not_summarised():
    """Seules les observations RÉUSSIES nourrissent le bilan (invariant du
    fallback : rien de non prouvé n'est déclaré terminé)."""
    assert _summary([("delegate_and_wait", _OBS, False)]) is None


def test_other_tools_are_unaffected():
    out = _summary([("run_command", "collected 5 items\n5 passed in 0.4s", True)]) or ""
    assert "5 passed" in out
