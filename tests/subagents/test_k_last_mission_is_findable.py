"""LOT K — « c'est quoi la dernière mission ? » doit avoir une réponse EXACTE.

Run du 2026-08-13. MemoNest vient de se terminer (20:24). L'utilisateur demande
la dernière mission ; Lumena répond **EcoPilot** — une mission du 3 août. Son
raisonnement, mot pour mot :

    « EcoPilot qui semble être la plus récente avec une description complète »

Elle DEVINE, parce qu'on ne lui a jamais donné de quoi savoir. Quatre manques
cumulés dans `list_missions_handler`, tous mesurés sur l'état réel :

1. aucun TRI          → l'ordre était celui du stockage ;
2. aucune DATE        → même en voyant toute la liste, rien ne dit laquelle est
                        la dernière — `created_at` était pourtant stocké ;
3. aucun FILTRE       → 371 tâches de worker listées comme des missions ;
4. aucune LIMITE      → 16 545 caractères, que la compaction d'observation
                        réduisait à 831 : la mission la plus récente était
                        littéralement JETÉE avant d'atteindre le modèle.

C'est le motif racine de tout ce chantier : **le fait existait, était stocké,
puis jeté avant la décision.** Ce lot ne calcule rien de neuf — il cesse de
jeter ce qu'on avait déjà.
"""
from __future__ import annotations

from src.reasoning.handlers.missions import (
    _is_worker_task,
    _mission_created_ts,
    _mission_when_text,
    select_missions_for_listing,
)


def _m(task_id, created_at=None, objective="", parent=None, state="done"):
    meta = {"objective": objective}
    if parent:
        meta["parent_id"] = parent
    return {"task_id": task_id, "created_at": created_at, "state": state, "metadata": meta}


# ── le cas réel : MemoNest doit sortir en tête, pas EcoPilot ─────────────────

def test_the_most_recent_mission_comes_first():
    """LE test du lot : rejoue exactement la question posée au run."""
    items = [
        _m("eco", "2026-08-03T18:00:00+00:00", "Construis EcoPilot"),
        _m("memo", "2026-08-13T20:24:00+00:00", "Construis MemoNest"),
        _m("note", "2026-08-13T18:08:00+00:00", "Construis NoteFlow"),
    ]
    rows, _ = select_missions_for_listing(items)
    assert rows[0]["task_id"] == "memo"
    assert [r["task_id"] for r in rows] == ["memo", "note", "eco"]


def test_a_worker_is_never_listed_as_a_mission():
    """371 tâches de worker noyaient les vraies missions."""
    items = [
        _m("mission", "2026-08-13T10:00:00+00:00", "Construis MemoNest"),
        _m("w1", "2026-08-13T11:00:00+00:00", "[Worker w_auth] CONTRAT", parent="mission"),
        _m("w2", "2026-08-13T12:00:00+00:00", "[Worker w_notes] CONTRAT", parent="mission"),
    ]
    rows, _ = select_missions_for_listing(items)
    assert [r["task_id"] for r in rows] == ["mission"]


def test_a_worker_is_recognised_by_its_parent():
    assert _is_worker_task({"metadata": {"parent_id": "task_x"}}) is True
    assert _is_worker_task({"metadata": {"objective": "Construis MemoNest"}}) is False
    assert _is_worker_task({}) is False
    assert _is_worker_task(None) is False


# ── la liste doit SURVIVRE à la compaction d'observation ────────────────────

def test_the_list_is_capped_and_says_how_many_are_hidden():
    """16 545 → 831 caractères : on coupe nous-mêmes, par le bon bout."""
    items = [_m(f"t{i}", f"2026-08-13T10:{i:02d}:00+00:00") for i in range(40)]
    rows, hidden = select_missions_for_listing(items, limit=15)
    assert len(rows) == 15
    assert hidden == 25
    assert rows[0]["task_id"] == "t39"  # la plus récente survit TOUJOURS


def test_a_short_list_hides_nothing():
    items = [_m("a", "2026-08-01T10:00:00+00:00"), _m("b", "2026-08-02T10:00:00+00:00")]
    rows, hidden = select_missions_for_listing(items, limit=15)
    assert len(rows) == 2 and hidden == 0


def test_the_cap_can_be_lifted():
    items = [_m(f"t{i}", f"2026-08-{i + 1:02d}T10:00:00+00:00") for i in range(20)]
    assert len(select_missions_for_listing(items, limit=0)[0]) == 20


# ── la date : ce qui permet de RÉPONDRE au lieu de deviner ──────────────────

def test_the_date_is_readable():
    """Format court `JJ/MM HH:MM`. Indépendant du fuseau de la machine : on
    vérifie la FORME et la conversion, pas une heure en dur."""
    import re

    txt = _mission_when_text(_m("x", "2026-08-13T18:24:00+00:00"))
    assert re.fullmatch(r"\d{2}/\d{2} \d{2}:\d{2}", txt), txt


def test_the_stored_utc_is_shown_in_local_time():
    """L'utilisateur pense en heure LOCALE : il a lancé MemoNest « à 20h24 »,
    alors que le disque porte `18:24:00+00:00`. Afficher l'UTC brut rendrait la
    réponse fausse à ses yeux. On compare à la conversion standard de Python,
    donc le test vaut sous n'importe quel fuseau."""
    from datetime import datetime, timezone

    raw = "2026-08-13T18:24:00+00:00"
    expected = datetime.fromisoformat(raw).astimezone().strftime("%d/%m %H:%M")
    assert _mission_when_text(_m("x", raw)) == expected
    assert _mission_created_ts(_m("x", raw)) == datetime(
        2026, 8, 13, 18, 24, tzinfo=timezone.utc
    ).timestamp()


def test_an_unknown_date_stays_silent_rather_than_lying():
    assert _mission_when_text(_m("x", None)) == ""
    assert _mission_when_text(_m("x", "pas une date")) == ""


def test_a_missing_date_never_breaks_the_sort():
    """Une tâche sans date part en fin de liste — elle ne fait pas échouer le tri."""
    items = [
        _m("sans_date", None, "vieille tâche"),
        _m("recente", "2026-08-13T20:00:00+00:00", "MemoNest"),
    ]
    rows, _ = select_missions_for_listing(items)
    assert rows[0]["task_id"] == "recente"


def test_naive_and_aware_timestamps_are_comparable():
    """Le piège du lot 5.7.4 : comparer un datetime naïf à un datetime aware
    lève TypeError. Les deux formes coexistent dans l'état réel."""
    naive = _mission_created_ts(_m("a", "2026-08-13T20:00:00"))
    aware = _mission_created_ts(_m("b", "2026-08-13T21:00:00+00:00"))
    assert naive > 0 and aware > 0
    items = [_m("a", "2026-08-13T20:00:00"), _m("b", "2026-08-13T21:00:00+00:00")]
    assert len(select_missions_for_listing(items)[0]) == 2  # ne lève pas


def test_the_z_suffix_is_understood():
    assert _mission_created_ts(_m("x", "2026-08-13T20:00:00Z")) > 0


# ── robustesse : un helper d'affichage ne doit JAMAIS casser la réponse ──────

def test_garbage_never_raises():
    assert select_missions_for_listing(None) == ([], 0)
    assert select_missions_for_listing([]) == ([], 0)
    assert select_missions_for_listing([None, "texte", 42])[0] == []
    assert _mission_created_ts(None) == 0.0
    assert _mission_created_ts({"created_at": 12345}) == 0.0


def test_a_mission_without_metadata_is_kept():
    """Pas de metadata ⇒ pas de parent ⇒ ce n'est pas un worker : on la garde."""
    rows, _ = select_missions_for_listing([{"task_id": "x", "created_at": None}])
    assert [r["task_id"] for r in rows] == ["x"]


def test_the_handler_uses_the_selection():
    """Le tri/filtre doit vivre DANS le handler, pas dans un appelant."""
    import inspect

    from src.reasoning.handlers.missions import list_missions_handler

    src = inspect.getsource(list_missions_handler)
    assert "select_missions_for_listing" in src
    assert "_mission_when_text" in src
