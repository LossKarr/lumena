"""Replay déterministe d'événements JSONL via le MÊME reducer que la prod (V2.3).

Format JSONL (une ligne = un événement) :
    {"t": 0, "type": "vad.speech_started"}
    {"t": 120, "type": "stt.partial", "text": "je veux que tu"}
    {"t": 1600, "type": "stt.final", "text": "ouvre le fichier"}
    {"t": 1700, "type": "endpoint.decision", "state": "turn_complete"}

Toute clé autre que `t`/`type` est mise dans `event.data` (donc `text`, `state`,
`generation_id`, `sequence`… sont accessibles via `event.get(...)`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Union

from .events import VoiceEvent
from .turn_manager import TurnManager


def parse_event_line(obj: dict) -> VoiceEvent:
    t = int(obj.get("t", 0))
    etype = obj["type"]
    data = {k: v for k, v in obj.items() if k not in ("t", "type")}
    return VoiceEvent(type=etype, t=t, data=data)


def parse_events(text: str) -> List[VoiceEvent]:
    """Parse un bloc JSONL (lignes vides et commentaires `#` ignorés)."""
    events: List[VoiceEvent] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        events.append(parse_event_line(json.loads(line)))
    return events


def load_events_jsonl(path: Union[str, Path]) -> List[VoiceEvent]:
    return parse_events(Path(path).read_text(encoding="utf-8"))


def replay_sync(tm: TurnManager, events: Iterable[VoiceEvent]) -> List[VoiceEvent]:
    """Rejoue les événements via `tm.feed` (reducer pur). Retourne la liste rejouée.

    N'exécute AUCUN effet (pas de runtime) : utile pour tester les transitions
    d'état et les commandes émises (`tm.emitted`).
    """
    played: List[VoiceEvent] = []
    for ev in events:
        tm.feed(ev)
        played.append(ev)
    return played
