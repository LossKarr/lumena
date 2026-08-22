"""Voix Lumena dans les rails sûrs (repair anti-leak + finalisation déterministe).

La sécurité (chemins déterministes + repairs anti-leak) avait pris le dessus sur la
personnalité → réponses sèches. On réinjecte la voix SANS rouvrir le leak : consigne de
ton COURTE dans le repair (A) + message de clôture déterministe chaleureux (B).
"""
from src.reasoning.react import build_mission_final_message, _LUMENA_TONE_REPAIR


def test_tone_repair_is_short_and_warm():
    t = _LUMENA_TONE_REPAIR
    assert "voix" in t.lower()
    assert "chaleureuse" in t.lower()
    # garde-fou : interdit d'exposer le raisonnement / de repartir en intention
    assert "raisonnement" in t.lower()
    # COURT par design (une consigne bavarde ré-ouvrirait le THOUGHT leak)
    assert len(t) < 400


def test_mission_final_message_warm_and_informative_when_clean():
    note = ("✅ Fichier ecrit: philosophes.md (11379 caracteres)\n"
            "📍 Chemin: C:\\Users\\charl\\Desktop\\lumena\\workspace\\philosophes.md")
    msg = build_mission_final_message(note, "Guide comparé : 6 Philosophes majeurs", malformed=False)
    assert "C'est fait" in msg                                   # accroche humaine
    assert "Guide comparé : 6 Philosophes majeurs" in msg        # titre du livrable injecté
    assert "philosophes.md" in msg                               # note (chemin/taille) conservée
    assert "altéré" not in msg                                   # aucune alerte quand propre


def test_mission_final_message_honest_when_malformed():
    msg = build_mission_final_message("✅ Fichier ecrit: x.md (10 c)", "Titre", malformed=True)
    assert "altéré" in msg.lower() or "jette" in msg.lower()     # honnêteté
    assert "vérifié" not in msg.lower()                          # ne clame PAS « vérifié »


def test_mission_final_message_no_title_still_warm():
    msg = build_mission_final_message("chemin/x.md", "", malformed=False)
    assert "C'est fait" in msg
    assert "chemin/x.md" in msg


# ── P0.2 : gate d'honnêteté du LEAD (cf. run PollApp multi-worker) ─────────────

def test_mission_final_message_tests_present_not_certified_blocks_verified():
    # Des tests EXISTENT (écrits par un worker → hors ledger du lead) mais le lead
    # n'a pas de pytest vert → JAMAIS « vérifié structurellement ».
    msg = build_mission_final_message(
        "✅ Fichier ecrit: app.py", "PollApp", malformed=False,
        tests_expected_not_run=True,
    )
    low = msg.lower()
    assert "non certifiés" in low or "non prouvée" in low     # honnêteté explicite
    assert "vérifié structurellement" not in low              # PAS de fausse certif
    assert "tests verts" not in low                           # ni de faux vert


def test_mission_final_message_green_wins_over_tests_present():
    # Priorité : un pytest VERT réel du lead prime → « tests verts » autorisé.
    msg = build_mission_final_message(
        "note", "T", malformed=False,
        has_green_test=True, tests_expected_not_run=True,
    )
    assert "non certifiés" not in msg.lower()


def test_mission_final_message_not_green_wins_over_tests_present():
    # Priorité : un test qui a tourné NON vert prime sur « présents non lancés ».
    msg = build_mission_final_message(
        "note", "T", malformed=False,
        test_ran_not_green=True, tests_expected_not_run=True,
    )
    assert "non verts" in msg.lower()


def test_mission_final_message_no_tests_still_structural():
    # Non-régression : aucun test du tout → « vérifié structurellement » conservé.
    msg = build_mission_final_message("note", "T", malformed=False)
    assert "structurellement" in msg.lower()
