"""A4 — note de capacité alignée sur le niveau enforced (anti-boucle chat).

Régression de la friction runtime (log B 02:52) : le prompt disait « Scope :
task.delegate » alors que le niveau était `chat` → l'agent retentait write_file
en boucle. La note doit dire CLAIREMENT « lecture seule » en chat.
"""
from __future__ import annotations

from web.routes.peers import _capability_prompt_note


def test_chat_note_says_read_only():
    note = _capability_prompt_note("chat")
    low = note.lower()
    assert "lecture seule" in low
    assert "aucune écriture" in low or "aucune action" in low


def test_mission_note_no_restriction():
    note = _capability_prompt_note("mission")
    assert "MISSION" in note
    assert "lecture seule" not in note.lower()


def test_unknown_level_failclosed_to_chat():
    # niveau inconnu → fail-closed sur chat (lecture seule)
    assert "lecture seule" in _capability_prompt_note("bidon").lower()
    assert "lecture seule" in _capability_prompt_note("").lower()
