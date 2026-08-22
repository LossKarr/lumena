"""LOT M3 — l'accusé de lancement dit ce qu'elle a retenu.

Demande utilisateur (2026-08-14, capture à l'appui) : « change ce message de
réponse de Lumena la laisser libre et surtout dir se que et a fait et donner au
sous agent ». Le message était figé au caractère près depuis le régime A.

Deux contraintes tenues :
  • déterministe — repasser par le LLM rouvrirait le THOUGHT leaké en réponse
    finale (3 occurrences dans le run du 14/08) ;
  • honnête — à l'instant de `create_mission`, le contrat n'est pas posé et
    AUCUN worker n'existe : le message ne peut pas dire « j'ai donné X à Y ».
"""
from __future__ import annotations

from src.subagents.mission_ack import build_mission_ack, summarize_objective

_OBJ_CAVEAVIN = (
    "Construire CaveÀVin, un SaaS local de gestion de cave à vin multi-utilisateur "
    "en Flask + SQLite. Fonctionnalités : inscription/connexion (mots de passe "
    "hachés), chaque utilisateur ne voit que ses propres bouteilles."
)


# ── ce que l'utilisateur a demandé ──────────────────────────────────────────

def test_the_ack_says_what_was_understood():
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_f4bfbd50", "120 minutes")
    assert "CaveÀVin" in ack
    assert "retenu" in ack.lower()


def test_the_ack_states_the_deadline():
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_f4bfbd50", "120 minutes")
    assert "120 minutes" in ack


def test_the_ack_explains_the_method_for_a_multi_worker_mission():
    """« ce qu'elle donne aux sous-agents » : le périmètre par worker."""
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_x", "2h", multi_worker=True)
    assert "contrat" in ack.lower()
    assert "sous-agent" in ack.lower()
    assert "périmètre" in ack.lower()


def test_the_ack_keeps_the_mission_id():
    """Le suivi doit rester possible — l'id était la seule info de l'ancien
    message, ne pas la perdre."""
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_f4bfbd507a72", "")
    assert "task_f4bfbd507a72" in ack


def test_the_wording_is_not_frozen():
    """« la laisser libre » : la tournure varie d'une mission à l'autre."""
    openings = {
        build_mission_ack("obj", f"task_{i:03d}", "").splitlines()[0]
        for i in range(40)
    }
    assert len(openings) >= 3, openings


def test_the_variation_is_stable_for_a_given_mission():
    """Déterministe : deux affichages de la MÊME mission sont identiques —
    sinon le message changerait sous les yeux de l'utilisateur."""
    a = build_mission_ack(_OBJ_CAVEAVIN, "task_stable", "1h")
    b = build_mission_ack(_OBJ_CAVEAVIN, "task_stable", "1h")
    assert a == b


# ── l'honnêteté : rien sur des workers qui n'existent pas encore ────────────

def test_the_ack_never_claims_workers_already_got_something():
    """À `create_mission`, le contrat n'est pas posé. Un passé composé ici
    serait une fabrication — le défaut que tout ce chantier combat."""
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_x", "2h", multi_worker=True).lower()
    for fabrication in (
        "j'ai confié", "j'ai délégué", "j'ai posé le contrat",
        "j'ai donné", "workers lancés", "sous-agents lancés",
    ):
        assert fabrication not in ack, fabrication


def test_the_ack_never_claims_a_result():
    ack = build_mission_ack(_OBJ_CAVEAVIN, "task_x", "2h").lower()
    for claim in ("terminé", "livré", "tests verts", "publié", "vérifié au navigateur"):
        assert claim not in ack, claim


# ── robustesse : l'accusé ne doit JAMAIS casser un lancement ────────────────

def test_no_objective_still_produces_a_usable_ack():
    ack = build_mission_ack("", "task_x", "")
    assert "task_x" in ack and ack.strip()


def test_garbage_never_raises():
    for obj in (None, "", 42, "x" * 5000):
        for mid in (None, "", "task_1"):
            out = build_mission_ack(obj, mid, None)
            assert isinstance(out, str) and out.strip()


# ── le résumé : couper, jamais inventer ─────────────────────────────────────

def test_the_summary_keeps_the_first_sentence():
    """Sur un objectif réel (toujours long), on garde la première phrase."""
    objectif = (
        "Construis un SaaS de gestion de cave à vin en Flask. "
        "Au moins 10 tests dont un test d'isolation."
    )
    assert summarize_objective(objectif) == (
        "Construis un SaaS de gestion de cave à vin en Flask"
    )


def test_a_short_two_sentence_objective_is_kept_whole():
    """Une première phrase très courte n'est PAS coupée : le seuil protège des
    faux séparateurs (« M. Dupont », « v1. »), et garder 33 caractères entiers
    informe mieux l'utilisateur que d'en garder 17."""
    assert summarize_objective("Construis un site. Puis teste-le.") == (
        "Construis un site. Puis teste-le."
    )


def test_the_summary_is_bounded():
    out = summarize_objective("mot " * 400)
    assert len(out) <= 181 and out.endswith("…")


def test_a_short_objective_is_untouched():
    assert summarize_objective("Range le dossier") == "Range le dossier"


def test_the_summary_collapses_whitespace():
    assert summarize_objective("Construis\n\n  un   site") == "Construis un site"


def test_empty_summary():
    assert summarize_objective("") == ""
    assert summarize_objective(None) == ""


# ── le branchement : un accusé jamais appelé n'existe pas ───────────────────

def test_react_wires_the_builder():
    import inspect

    from src.reasoning import react

    src = inspect.getsource(react)
    assert "build_mission_ack" in src


def test_the_legacy_ack_remains_as_a_fallback():
    """Une guidance ne doit jamais empêcher de lancer une mission : si le
    constructeur échoue, l'ancien message part quand même."""
    import inspect

    from src.reasoning import react

    src = inspect.getsource(react)
    block = src.split("build_mission_ack")[-1][:1200]
    assert "except Exception" in block
    assert "La mission tourne en arrière-plan" in block
