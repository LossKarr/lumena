"""H1 — le lead attend réellement ses workers ; `deadline_ts` est une DATE, pas un nombre.

Run fondateur SuiviDepenses (2026-08-12, mission `task_e4766b66…`) :

    23:41:28  mission créée, échéance 30 min → 00:11
    23:42:20  delegate_and_wait(timeout=600) sur 3 workers
    23:52:22  EXPIRATION à 600 s pile — alors qu'il restait ~19 min de budget
    23:52:28  « Les 3 workers sont bloqués. Je reprends le périmètre moi-même. »
    23:53:12  le lead édite app.py … pendant que le CodeAgent de w_backend l'écrit
    23:55:03  w_frontend termine  ← il travaillait bel et bien
    00:02:53  w_tests termine, alors que le parent est déjà « terminé »

Cause : le verrou 2.6.4, censé étendre l'attente jusqu'à l'échéance, faisait

    float(deadline_ts) - time.time() - 120.0

sur une chaîne ISO (`'2026-08-13T00:11:00'`). `ValueError`, avalée par son propre
`except` → le relèvement n'a **jamais** tiré depuis son écriture. Le commentaire du
verrou décrit pourtant mot pour mot le dégât qu'il était censé empêcher :
« le lead a publié pendant que w_frontend/w_tests mutaient les fichiers ».

Le même `float()` existait sur le garde de publication : là, l'except rendait le
garde PLUS strict (refus permanent tant qu'un worker tourne) — faux aussi, mais
sans dommage.
"""
from __future__ import annotations

from datetime import datetime

from src.subagents.mission_budget import seconds_until_deadline


# ── Le helper : une DATE, jamais un nombre ───────────────────────────────────

_RUN_NOW = datetime(2026, 8, 12, 23, 42, 20)      # instant réel du delegate_and_wait
_RUN_DEADLINE = "2026-08-13T00:11:00"             # échéance réelle de la mission


def test_the_exact_run_that_failed():
    """Le cas qui a produit la course : ~1720 s restantes, pas une ValueError."""
    left = seconds_until_deadline(_RUN_DEADLINE, now=_RUN_NOW)
    assert left is not None, "l'ancien float() levait ici, et le lead n'attendait plus"
    assert 1700 < left < 1740


def test_the_lead_would_have_waited_instead_of_racing():
    """Avec la marge d'intégration de 120 s, l'attente devient ~1600 s — le lead
    aurait tenu jusqu'à 00:09 au lieu d'abandonner à 23:52."""
    timeout = seconds_until_deadline(_RUN_DEADLINE, now=_RUN_NOW) - 120.0
    assert timeout > 600.0, "le plancher de 600 s ne doit plus l'emporter"
    assert 1550 < timeout < 1650


def test_past_deadline_is_negative():
    assert seconds_until_deadline("2026-08-12T23:00:00", now=_RUN_NOW) < 0


def test_unreadable_values_return_none_not_zero():
    """`None` ≠ « échéance atteinte » : confondre les deux ferait couper une
    mission qui a du budget, ou publier pendant que des workers écrivent."""
    for bad in ("pas une date", "", None, [], {}):
        assert seconds_until_deadline(bad) is None


def test_numeric_epoch_is_not_mistaken_for_a_date():
    """`deadline_ts` est TOUJOURS ISO (posé par `normalize_deadline`). Un nombre
    est une anomalie : on rend None plutôt que d'inventer une échéance."""
    assert seconds_until_deadline(1234567890) is None
    assert seconds_until_deadline("1234567890") is None


def test_timezone_aware_deadline_does_not_explode():
    """Le budget mission a déjà été tué une fois par un mélange aware/naïf
    (`can't subtract offset-naive and offset-aware`). `_parse_iso` normalise."""
    left = seconds_until_deadline("2026-08-13T00:11:00+00:00", now=_RUN_NOW)
    assert left is not None
    assert isinstance(left, float)


def test_now_is_injectable_so_tests_are_deterministic():
    a = seconds_until_deadline(_RUN_DEADLINE, now=_RUN_NOW)
    b = seconds_until_deadline(_RUN_DEADLINE, now=_RUN_NOW)
    assert a == b
