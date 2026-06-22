"""La note web d'une mission REFUSÉE remonte le message riche (pourquoi/comment),
pas juste le mot brut « refused » (régression log A 05:01 : A devinait la raison).
"""
from __future__ import annotations

from unittest.mock import patch

from web.routes.chat import _inject_mission_reminders


def _refused_mission():
    return {
        "task_id": "ta-x", "channel": "web", "status": "refused",
        "peer_name": "Lumena-B", "objective": "créer note.txt", "result": "",
    }


def test_refused_reminder_is_rich_and_actionable():
    with patch("src.runtime.peer_mission_tracker.pending_web_reminders", return_value=[_refused_mission()]), \
         patch("src.runtime.peer_mission_tracker.ack_web_reminders"), \
         patch("src.runtime.peer_mission_tracker.list_pending", return_value=[]):
        out = _inject_mission_reminders("salut")
    low = out.lower()
    # POURQUOI + COMMENT + QUI
    assert "lecture seule" in low
    assert "mission" in low and ("panneau" in low or "pairs" in low)
    assert "moi-même" in low or "ne peux pas" in low
    # PAS le mot brut tout seul
    assert "(lumena-b) : refused." not in low
    # le message utilisateur d'origine est préservé
    assert "salut" in out


def test_completed_reminder_unchanged():
    m = {
        "task_id": "ta-y", "channel": "web", "status": "completed",
        "peer_name": "Lumena-B", "objective": "créer note.txt",
        "result": "fait", "artifacts_dir": "recu-de-lumena-b",
    }
    with patch("src.runtime.peer_mission_tracker.pending_web_reminders", return_value=[m]), \
         patch("src.runtime.peer_mission_tracker.ack_web_reminders"), \
         patch("src.runtime.peer_mission_tracker.list_pending", return_value=[]):
        out = _inject_mission_reminders("salut")
    assert "TERMINÉ" in out  # format completed inchangé
    assert "recu-de-lumena-b" in out
