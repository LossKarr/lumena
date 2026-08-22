from src.voice.v2.speech_planner import SentenceCommitter, plan_speech


def test_planner_keeps_display_text_outside_its_scope_and_shortens_speech():
    raw = "**Termine.** Le rapport complet est dans C:\\tmp\\rapport.json. Details tres longs. Encore."
    plan = plan_speech(raw, canonical_verified=True, max_sentences=2)
    assert raw.startswith("**Termine")
    assert "C:\\tmp" not in plan.spoken
    assert "path" in plan.suppressed
    assert len(plan.spoken.split(". ")) <= 2


def test_planner_never_speaks_internal_react_markers():
    raw = "THOUGHT: je dois agir\nACTION: FINAL\nACTION_INPUT: Voici le resultat utile."
    plan = plan_speech(raw, canonical_verified=True)
    assert "THOUGHT" not in plan.spoken
    assert "ACTION" not in plan.spoken
    assert "internal_reasoning" in plan.suppressed


def test_unverified_sensitive_claim_is_not_spoken():
    plan = plan_speech("Les tests sont verts. Le fichier est pret.", canonical_verified=False)
    assert "tests sont verts" not in plan.spoken.lower()
    assert "unverified_claim" in plan.suppressed


def test_verified_sensitive_claim_may_be_spoken():
    plan = plan_speech("Les tests sont verts.", canonical_verified=True)
    assert "tests sont verts" in plan.spoken.lower()


def test_sentence_committer_waits_for_complete_sentence():
    c = SentenceCommitter()
    assert c.feed("Bonjour, je term") == []
    assert c.feed("ine maintenant. Suite") == ["Bonjour, je termine maintenant."]
    assert c.flush() == ["Suite"]


def test_sentence_committer_rejects_internal_protocol():
    c = SentenceCommitter()
    assert c.feed("THOUGHT: je reflechis. ") == []

