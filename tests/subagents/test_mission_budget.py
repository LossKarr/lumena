"""Lot 5.7.1+5.7.2 — Budget temporel des missions (logique pure, déterministe).

Verrouille : normalisation d'échéance (relatif/absolu/mots calmes/garbage),
calcul du budget (elapsed/remaining/ratio), et le cadrage ANTI-STRESS du préambule
(aucun mot de pression — c'est la garantie « ne pas stresser l'IA »).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.subagents.mission_budget import (
    normalize_deadline, mission_budget, mission_budget_preamble,
    mission_budget_nudge, mission_budget_finalize, _STRESS_WORDS,
    extract_target_file, deadline_final_exit_allowed, deadline_hard_net_fires,
)


def _b(ratio):
    return {"has_deadline": True, "deadline_ts": "2026-06-29T12:00:00",
            "elapsed_s": 0.0, "remaining_s": 0.0, "ratio_used": ratio}


def _br(remaining):
    return {"has_deadline": True, "deadline_ts": "2026-06-29T12:00:00",
            "elapsed_s": 0.0, "remaining_s": remaining, "ratio_used": 1.0}

# Référence temporelle figée : lundi 2026-06-29 10:00:00
NOW = datetime(2026, 6, 29, 10, 0, 0)


# ── normalize_deadline ───────────────────────────────────────────────────────
def test_relative_dans():
    assert normalize_deadline("dans 2h", now=NOW) == "2026-06-29T12:00:00"
    assert normalize_deadline("dans 30 minutes", now=NOW) == "2026-06-29T10:30:00"


def test_absolute_hhmm_today_or_tomorrow():
    assert normalize_deadline("18:00", now=NOW) == "2026-06-29T18:00:00"
    # heure déjà passée aujourd'hui → demain
    assert normalize_deadline("08:00", now=NOW) == "2026-06-30T08:00:00"


def test_demain():
    assert normalize_deadline("demain à 9h", now=NOW) == "2026-06-30T09:00:00"


def test_calm_keywords():
    assert normalize_deadline("ce soir", now=NOW) == "2026-06-29T20:00:00"
    assert normalize_deadline("ce midi", now=NOW) == "2026-06-29T12:00:00"
    assert normalize_deadline("fin de journée", now=NOW) == "2026-06-29T18:00:00"


def test_bare_duration_without_dans():
    assert normalize_deadline("30min", now=NOW) == "2026-06-29T10:30:00"


def test_demain_a_9h_not_eaten_as_delay():
    # garde-fou : "demain à 9h" NE doit PAS être interprété comme now+9h
    assert normalize_deadline("demain à 9h", now=NOW) == "2026-06-30T09:00:00"


def test_unparseable_returns_none():
    assert normalize_deadline("quand tu peux", now=NOW) is None
    assert normalize_deadline("", now=NOW) is None
    assert normalize_deadline(None, now=NOW) is None


# ── mission_budget ───────────────────────────────────────────────────────────
def _rec(created, deadline_ts=None):
    md = {}
    if deadline_ts:
        md["deadline_ts"] = deadline_ts
    return {"created_at": created, "metadata": md}


def test_budget_with_deadline():
    rec = _rec("2026-06-29T09:30:00", "2026-06-29T11:30:00")  # 2h de budget, 30min écoulées
    b = mission_budget(rec, now=NOW)
    assert b["has_deadline"] is True
    assert b["deadline_ts"] == "2026-06-29T11:30:00"
    assert b["elapsed_s"] == 1800.0           # 30 min
    assert b["remaining_s"] == 5400.0         # 1h30
    assert round(b["ratio_used"], 3) == 0.25  # 30min / 120min


def test_budget_no_deadline():
    b = mission_budget(_rec("2026-06-29T09:30:00"), now=NOW)
    assert b["has_deadline"] is False
    assert b["deadline_ts"] is None
    assert b["remaining_s"] is None
    assert b["elapsed_s"] == 1800.0


def test_budget_overdue_negative_remaining():
    b = mission_budget(_rec("2026-06-29T08:00:00", "2026-06-29T09:00:00"), now=NOW)
    assert b["remaining_s"] == -3600.0        # dépassé d'1h
    assert b["ratio_used"] == 1.0


def test_budget_tz_aware_created_at_does_not_crash():
    # RÉGRESSION : l'orchestrateur écrit created_at en AWARE UTC
    # (datetime.now(timezone.utc).isoformat()), tandis que `now`/deadline_ts sont
    # naïfs locaux. Avant le fix _parse_iso, (now_naïf - created_aware) levait
    # TypeError → tout le budget mission (5.7.3/5.7.4) mourait silencieusement.
    from datetime import timezone
    # created_at AWARE UTC = NOW local exprimé en UTC (offset local appliqué)
    created_aware = NOW.astimezone(timezone.utc).isoformat()
    rec = {"created_at": created_aware,
           "metadata": {"deadline_ts": "2026-06-29T09:00:00"}}  # deadline NAÏVE locale
    b = mission_budget(rec, now=NOW)            # ne doit PAS lever
    assert b["has_deadline"] is True
    assert b["remaining_s"] == -3600.0          # échéance dépassée d'1h (naïf local cohérent)
    assert b["elapsed_s"] is not None and b["elapsed_s"] >= 0.0  # plus de crash sur created_at


# ── mission_budget_preamble (ANTI-STRESS) ────────────────────────────────────
def test_preamble_empty_without_deadline():
    assert mission_budget_preamble(None, now=NOW) == ""
    assert mission_budget_preamble("", now=NOW) == ""
    assert mission_budget_preamble("pas une date", now=NOW) == ""


def test_preamble_contains_deadline_and_quality_framing():
    pre = mission_budget_preamble("2026-06-29T18:00:00", now=NOW)
    assert "18:00" in pre
    assert "aujourd'hui" in pre
    assert "ton rythme" in pre
    assert "qualité" in pre
    assert "toutes les exigences" in pre
    assert "échec explicite" in pre


def test_preamble_is_anti_stress():
    # GARANTIE clé : aucun mot de pression dans le cadrage temporel.
    for dts in ("2026-06-29T18:00:00", "2026-06-29T10:05:00", "2026-06-30T09:00:00"):
        pre = mission_budget_preamble(dts, now=NOW).lower()
        for w in _STRESS_WORDS:
            assert w not in pre, f"mot de stress interdit présent: {w!r}"


def test_preamble_tomorrow_label():
    pre = mission_budget_preamble("2026-06-30T09:00:00", now=NOW)
    assert "demain 09:00" in pre


# ── mission_budget_nudge (auto-gestion one-time, CALME) ──────────────────────
def test_nudge_half_then_low_sequence():
    assert mission_budget_nudge(_b(0.30)) is None              # avant mi-budget
    assert mission_budget_nudge(_b(0.50))[0] == "half"         # mi-budget
    assert mission_budget_nudge(_b(0.60), already=["half"]) is None  # half déjà émis
    assert mission_budget_nudge(_b(0.85), already=["half"])[0] == "low"
    assert mission_budget_nudge(_b(0.95), already=["half", "low"]) is None  # low déjà émis


def test_nudge_jump_past_half_to_low():
    # saut direct >80% → on émet « low » (pas « half » a posteriori)
    assert mission_budget_nudge(_b(0.90))[0] == "low"


def test_nudge_none_without_deadline_or_ratio():
    assert mission_budget_nudge({"has_deadline": False}) is None
    assert mission_budget_nudge({"has_deadline": True, "ratio_used": None}) is None
    assert mission_budget_nudge(None) is None


def test_nudge_is_anti_stress():
    for ratio, already in ((0.50, []), (0.85, ["half"])):
        _, text = mission_budget_nudge(_b(ratio), already=already)
        low = text.lower()
        for w in _STRESS_WORDS:
            assert w not in low, f"mot de stress interdit dans le nudge: {w!r}"
        assert "qualité" in low or "propre" in low  # cadrage qualité présent


def test_temporal_guidance_never_recommends_partial_delivery():
    texts = [
        mission_budget_preamble("2026-06-29T18:00:00", now=NOW),
        mission_budget_nudge(_b(0.50))[1],
        mission_budget_nudge(_b(0.85), already=["half"])[1],
        mission_budget_finalize(_br(0))[1],
    ]
    for text in texts:
        assert "partiel" not in text.lower()


# ── mission_budget_finalize (fin de temps : finaliser AVANT de couper) ───────
def test_finalize_none_before_deadline():
    assert mission_budget_finalize(_br(600)) is None       # 10 min restantes


def test_finalize_at_deadline_and_within_grace():
    a = mission_budget_finalize(_br(0))                     # échéance atteinte
    assert a[0] == "finalize"
    assert "qualité" in a[1].lower()
    assert mission_budget_finalize(_br(-60))[0] == "finalize"  # dans la grâce (120s)


def test_finalize_cancel_only_after_grace():
    assert mission_budget_finalize(_br(-120)) == ("cancel", None)
    assert mission_budget_finalize(_br(-300)) == ("cancel", None)


def test_finalize_grace_configurable():
    assert mission_budget_finalize(_br(-60), grace_s=30)[0] == "cancel"   # au-delà de 30s
    assert mission_budget_finalize(_br(-20), grace_s=30)[0] == "finalize"  # encore dans la grâce


def test_finalize_none_without_deadline_or_remaining():
    assert mission_budget_finalize({"has_deadline": False}) is None
    assert mission_budget_finalize({"has_deadline": True, "remaining_s": None}) is None
    assert mission_budget_finalize(None) is None


def test_finalize_steer_is_anti_stress():
    _, text = mission_budget_finalize(_br(0))
    low = text.lower()
    for w in _STRESS_WORDS:
        assert w not in low, f"mot de stress interdit dans la finalisation: {w!r}"
    assert "qualité" in low and "final" in low


# ── extract_target_file (Lot 5.7.4a — contrat artefact disque) ───────────────
def test_target_file_named_in_objective():
    obj = ("Compare 6 cocktails. Rédige un guide clair dans workspace/cocktails_maison.md. "
           "Si le temps manque, rends un partiel honnête.")
    assert extract_target_file(obj) == "workspace/cocktails_maison.md"


def test_target_file_nested_path_and_other_exts():
    assert extract_target_file("écris le rapport dans workspace/2026-06-30/guide.md") \
        == "workspace/2026-06-30/guide.md"
    assert extract_target_file("génère un PDF nommé rapport_final.pdf") == "rapport_final.pdf"
    assert extract_target_file("dépose le tableau dans data/stats.csv") == "data/stats.csv"


def test_target_file_none_for_text_mission():
    # Mission « texte » : aucun fichier nommé → pas de contrat d'artefact (None).
    assert extract_target_file("compare 6 villes pour un week-end en Europe") is None
    assert extract_target_file("écris une nouvelle de 800 mots sur l'automne") is None
    assert extract_target_file("") is None
    assert extract_target_file(None) is None


# ── deadline_final_exit_allowed (Lot 5.7.4a — sortie propre après partiel livré) ──
def test_exit_blocked_without_finalization():
    # Pas d'échéance finalisée → on NE relâche PAS le PLAN GUARD.
    assert deadline_final_exit_allowed(
        partial_due_to_deadline=False, target_file="workspace/x.md", artifact_written=True) is False


def test_exit_blocked_when_target_not_yet_written():
    # CAS STRICT (correction reviewer) : finalisation déclenchée MAIS fichier cible
    # pas encore écrit → on NE sort PAS (sinon perte du livrable).
    assert deadline_final_exit_allowed(
        partial_due_to_deadline=True, target_file="workspace/x.md", artifact_written=False) is False


def test_exit_stays_blocked_when_only_partial_target_written():
    assert deadline_final_exit_allowed(
        partial_due_to_deadline=True, target_file="workspace/x.md", artifact_written=True) is False


def test_exit_stays_blocked_for_partial_text_mission():
    assert deadline_final_exit_allowed(
        partial_due_to_deadline=True, target_file=None, artifact_written=False) is False


# ── deadline_hard_net_fires (Lot 5.7.4b — filet dur désarmé si artefact livré) ──
def test_net_does_not_fire_before_steer():
    # Finalisation jamais tentée → jamais de cancel (invariant 5.7.4).
    assert deadline_hard_net_fires(
        steered=False, remaining_s=-9999, grace_s=120, artifact_written=False) is False


def test_net_does_not_fire_within_grace():
    # Steered mais encore dans la grâce → on laisse finaliser, pas de cancel.
    assert deadline_hard_net_fires(
        steered=True, remaining_s=-60, grace_s=120, artifact_written=False) is False


def test_net_fires_past_grace_without_artifact():
    # Steered + grâce épuisée + AUCUN artefact → filet dur légitime.
    assert deadline_hard_net_fires(
        steered=True, remaining_s=-120, grace_s=120, artifact_written=False) is True
    assert deadline_hard_net_fires(
        steered=True, remaining_s=-300, grace_s=120, artifact_written=False) is True


def test_net_still_fires_when_only_partial_artifact_written():
    assert deadline_hard_net_fires(
        steered=True, remaining_s=-300, grace_s=120, artifact_written=True) is True


def test_net_none_remaining_does_not_fire():
    assert deadline_hard_net_fires(
        steered=True, remaining_s=None, grace_s=120, artifact_written=False) is False
