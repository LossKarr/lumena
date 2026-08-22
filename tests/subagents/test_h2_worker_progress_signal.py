"""H2 — un worker qui code n'est plus déclaré « bloqué », et le conseil ne crée
plus la course qu'il veut éviter.

Correction d'un faux positif introduit par moi (F3.b, 2026-08-12) et payé au run
SuiviDepenses le soir même :

    23:52:22  [delegate_and_wait] workers non terminaux : 3 sans progres, 0 en file
    23:52:28  « Les 3 workers sont bloqués. Je reprends le périmètre moi-même. »
    23:53:12  le lead édite app.py … pendant que le CodeAgent de w_backend l'écrit
    23:55:03  w_frontend TERMINE  ← il n'a jamais été bloqué

Deux fautes distinctes, corrigées séparément :

1. **Mauvais signal.** `updated_at` ne bouge qu'aux TRANSITIONS d'état. Un worker
   dont le CodeAgent tourne 719 s n'écrit rien dans sa tâche — mais il écrit des
   fichiers. D'où le signal disque.

2. **Mauvais conseil.** Le message disait « reprends leur périmètre toi-même ».
   Un garde-fou qui conseille mal produit exactement le dégât qu'il surveille.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

from src.reasoning.handlers.missions import (
    _mission_workspace_idle_s,
    classify_pending_workers,
)

_NOW = datetime(2026, 8, 12, 23, 52, 22, tzinfo=timezone.utc)  # instant réel du run


def _rec(task_id, state, *, idle_s):
    return {
        "task_id": task_id,
        "state": state,
        "updated_at": (_NOW - timedelta(seconds=idle_s)).isoformat(),
    }


def _now():
    return _NOW.isoformat()


# ── Le signal disque empêche l'accusation ────────────────────────────────────

def test_the_exact_false_positive_of_the_run():
    """Trois workers `checkpointed` sans transition depuis 600 s, mais le dossier
    de mission a bougé il y a 8 s : ils codent. Aucun ne doit être accusé."""
    records = [_rec(f"w{i}", "checkpointed", idle_s=600) for i in range(3)]
    out = classify_pending_workers(records, _now(), 300.0, workspace_idle_s=8.0)
    assert [p["kind"] for p in out] == ["working"] * 3


def test_without_the_signal_the_old_behaviour_is_preserved():
    """Rétrocompatibilité : sans `workspace_idle_s`, la v1 s'applique."""
    records = [_rec("w1", "checkpointed", idle_s=600)]
    out = classify_pending_workers(records, _now(), 300.0)
    assert out[0]["kind"] == "stalled"


def test_a_truly_silent_workspace_still_reports_stalled():
    """Le garde ne devient pas aveugle : disque figé ET tâche figée = bloqué."""
    records = [_rec("w1", "checkpointed", idle_s=600)]
    out = classify_pending_workers(records, _now(), 300.0, workspace_idle_s=1200.0)
    assert out[0]["kind"] == "stalled"
    assert out[0]["idle_s"] == 600


def test_queued_stays_queued_even_when_others_work():
    """Un worker jamais démarré reste `queued` : le disque bouge grâce aux AUTRES."""
    records = [_rec("w1", "queued", idle_s=600)]
    out = classify_pending_workers(records, _now(), 300.0, workspace_idle_s=5.0)
    assert out[0]["kind"] == "queued"


def test_signal_at_the_threshold_boundary():
    records = [_rec("w1", "running", idle_s=900)]
    under = classify_pending_workers(records, _now(), 300.0, workspace_idle_s=299.0)
    over = classify_pending_workers(records, _now(), 300.0, workspace_idle_s=300.0)
    assert under[0]["kind"] == "working"
    assert over[0]["kind"] == "stalled"


def test_terminal_workers_are_still_ignored():
    records = [_rec("w1", "done", idle_s=900), _rec("w2", "failed", idle_s=900)]
    assert classify_pending_workers(records, _now(), 300.0, workspace_idle_s=5.0) == []


# ── La lecture du disque ─────────────────────────────────────────────────────

def test_idle_reader_returns_none_without_workspace():
    ctx = types.SimpleNamespace(file_guardrails=None)
    assert _mission_workspace_idle_s(ctx, "") is None


def test_idle_reader_returns_none_when_dir_missing(tmp_path):
    ctx = types.SimpleNamespace(
        file_guardrails=types.SimpleNamespace(_workspace_root=lambda: tmp_path)
    )
    assert _mission_workspace_idle_s(ctx, "missions/inexistante") is None


def test_idle_reader_sees_a_fresh_file(tmp_path):
    mission = tmp_path / "missions" / "task_x"
    mission.mkdir(parents=True)
    (mission / "app.py").write_text("print('hello')", encoding="utf-8")
    ctx = types.SimpleNamespace(
        file_guardrails=types.SimpleNamespace(_workspace_root=lambda: tmp_path)
    )
    idle = _mission_workspace_idle_s(ctx, "missions/task_x")
    assert idle is not None and idle < 60


def test_idle_reader_ignores_cache_dirs(tmp_path):
    """Un `__pycache__` fraîchement écrit ne prouve aucun travail de worker."""
    mission = tmp_path / "missions" / "task_y"
    (mission / "__pycache__").mkdir(parents=True)
    (mission / "__pycache__" / "x.pyc").write_bytes(b"cache")
    ctx = types.SimpleNamespace(
        file_guardrails=types.SimpleNamespace(_workspace_root=lambda: tmp_path)
    )
    assert _mission_workspace_idle_s(ctx, "missions/task_y") is None


def test_idle_reader_never_raises_on_broken_context():
    class _Boom:
        @property
        def file_guardrails(self):
            raise RuntimeError("contexte cassé")

    assert _mission_workspace_idle_s(_Boom(), "missions/x") is None


# ── Le conseil ne pousse plus à la course ────────────────────────────────────

def test_advice_forbids_taking_over_a_live_worker():
    """Le texte est le garde-fou : c'est lui qui a envoyé le lead dans le mur."""
    import inspect
    from src.reasoning.handlers import missions as M

    src = inspect.getsource(M.delegate_and_wait_handler)
    assert "NE REPRENDS PAS leurs fichiers" in src
    assert "cancel_mission" in src, "il faut une sortie explicite : annuler d'abord"
    assert "reprends leur périmètre toi-même, ou conclus" not in src, (
        "l'ancien conseil qui a causé la course ne doit plus exister"
    )
