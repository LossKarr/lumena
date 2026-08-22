"""LOT H (run BiblioFlux 2026-07-04) — le lead ne meurt plus avant d'intégrer.

Deux défauts empilés, tous deux prouvés au runtime :

H.1 : `normalize_deadline('2026-07-05T12:00:00')` renvoyait None — le séparateur ISO
      'T' n'était pas parsé (`_parse_run_at` n'essaie que des formats à ESPACE). Le
      chat émet pourtant le format machine avec 'T' → `deadline_ts` absent → l'uplift
      budget (runner B0.1) ne tirait jamais → lead plafonné à 600 s.

H.2 : INVARIANT INVERSÉ — `delegate_and_wait` attend ses workers jusqu'à 1200 s alors
      que le lead était plafonné à 600 s → il mourait TOUJOURS en pleine délégation,
      avant d'intégrer. Plancher top-lead 1800 s (1200 délégation + 600 intégration).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.subagents.mission_budget import normalize_deadline, mission_budget
from src.subagents.runner import _effective_lead_timeout


# ── H.1 : ISO-8601 avec 'T' (cause racine BiblioFlux) ─────────────────────────────

_NOW = datetime(2026, 7, 4, 12, 0, 0)


def test_iso_with_T_separator_now_parses():
    """Le cas EXACT BiblioFlux : '2026-07-05T12:00:00' doit produire un deadline_ts."""
    dts = normalize_deadline("2026-07-05T12:00:00", now=_NOW)
    assert dts == "2026-07-05T12:00:00"
    rec = {"created_at": _NOW.isoformat(), "metadata": {"deadline_ts": dts}}
    b = mission_budget(rec, now=_NOW)
    assert b["has_deadline"] is True
    assert b["remaining_s"] == 86400.0  # +24 h, budget réel exploitable par l'uplift


def test_iso_with_T_no_seconds():
    assert normalize_deadline("2026-07-05T12:00", now=_NOW) == "2026-07-05T12:00:00"


def test_iso_with_space_still_works():
    # Non-régression : la variante à espace (déjà gérée par _parse_run_at) reste OK.
    assert normalize_deadline("2026-07-05 12:00:00", now=_NOW) == "2026-07-05T12:00:00"


def test_iso_tz_aware_normalized_to_local_naive():
    """Une échéance tz-aware ('+00:00', 'Z') est ramenée en naïf local (cf. _parse_iso)."""
    for raw in ("2026-07-05T10:00:00+00:00", "2026-07-05T10:00:00Z"):
        dts = normalize_deadline(raw, now=_NOW)
        assert dts is not None
        # comparable au `now` naïf sans lever (le bug 5.7.3 d'origine)
        b = mission_budget({"created_at": _NOW.isoformat(),
                            "metadata": {"deadline_ts": dts}}, now=_NOW)
        assert b["remaining_s"] is not None


def test_iso_date_only():
    assert normalize_deadline("2026-07-05", now=_NOW) == "2026-07-05T00:00:00"


def test_natural_language_still_works():
    # Non-régression : le fast-path ISO ne doit rien avaler du langage naturel.
    assert normalize_deadline("demain 12h", now=_NOW) == "2026-07-05T12:00:00"
    assert normalize_deadline("18:00", now=_NOW) == "2026-07-04T18:00:00"
    assert normalize_deadline("dans 2h", now=_NOW) == "2026-07-04T14:00:00"


def test_unrecognized_and_empty_return_none():
    assert normalize_deadline("un jour peut-être", now=_NOW) is None
    assert normalize_deadline("", now=_NOW) is None
    assert normalize_deadline(None, now=_NOW) is None


# ── H.2 : plancher d'exécution du top-lead ────────────────────────────────────────

class _FakeOrch:
    def __init__(self, task):
        self._task = task

    def get_task(self, _mid):
        return self._task


class _FakeCore:
    def __init__(self, task):
        self.task_orchestrator = _FakeOrch(task)


def _core(depth=1, deadline_ts=None):
    md = {"depth": depth}
    if deadline_ts:
        md["deadline_ts"] = deadline_ts
    return _FakeCore({"created_at": datetime.now().isoformat(), "metadata": md})


def test_top_lead_floor_lifts_600_to_1800(monkeypatch):
    """Sans échéance, un top-lead (délégation possible) passe de 600 s → 1800 s."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")  # délégation activée
    assert _effective_lead_timeout(_core(depth=1), "m", 600.0) == 1800.0


def test_top_lead_floor_is_monotone(monkeypatch):
    """Le plancher ne RACCOURCIT jamais un budget déjà supérieur."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    assert _effective_lead_timeout(_core(depth=1), "m", 2400.0) == 2400.0


def test_sub_worker_unchanged(monkeypatch):
    """Un sous-worker (depth 2) n'est PAS un top-lead → budget inchangé."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    assert _effective_lead_timeout(_core(depth=2), "w", 600.0) == 600.0


def test_no_delegation_no_floor(monkeypatch):
    """Délégation désactivée (max_depth=1) → pas de plancher (le lead ne délègue pas)."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "1")
    assert _effective_lead_timeout(_core(depth=1), "m", 600.0) == 600.0


def test_far_deadline_uplift_dominates_floor(monkeypatch):
    """Échéance lointaine → uplift au plafond 3600 s, qui domine le plancher 1800 s."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    far = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
    assert _effective_lead_timeout(_core(depth=1, deadline_ts=far), "m", 600.0) == 3600.0


def test_near_deadline_floor_still_protects(monkeypatch):
    """Échéance proche (uplift faible) : le plancher top-lead garde le lead en vie
    assez longtemps pour survivre à sa délégation (cœur du bug BiblioFlux)."""
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    near = (datetime.now() + timedelta(seconds=100)).isoformat(timespec="seconds")
    # uplift ≈ max(600, 100+120)=600 → plancher relève à 1800
    assert _effective_lead_timeout(_core(depth=1, deadline_ts=near), "m", 600.0) == 1800.0
