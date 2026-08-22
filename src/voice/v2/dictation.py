"""Règles pures de dictée du compositeur web."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable


_SEND_COMMANDS = frozenset({"envoyer", "envoyez"})
_TRAILING_COMMAND_RE = re.compile(
    r"(?is)\s*(?:envoyer|envoyez)\s*[.!?;,\u2026]*\s*$"
)
_STRONG_SENTENCE_RE = re.compile(
    r"(?is)^(?P<body>.+[.!?;])\s*(?P<command>envoyer|envoyez)"
    r"\s*[.!?;,\u2026]*\s*$"
)


@dataclass(frozen=True)
class DictationDecision:
    text: str
    should_send: bool
    command: str = ""
    boundary: str = ""


def _normalized_unit(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"^[\s.!?;,:'\"«»\u2026]+|[\s.!?;,:'\"«»\u2026]+$", "", text)
    return re.sub(r"\s+", " ", text)


def _segment_texts(segments: Iterable[Any] | None) -> list[str]:
    values: list[str] = []
    for segment in segments or ():
        if isinstance(segment, dict):
            value = segment.get("text", "")
        else:
            value = getattr(segment, "text", segment)
        value = str(value or "").strip()
        if value:
            values.append(value)
    return values


def extract_dictation_send_command(
    text: str, segments: Iterable[Any] | None = None
) -> DictationDecision:
    """Retire uniquement une commande finale isolée et prouvée.

    Une simple terminaison lexicale ne suffit jamais. Il faut soit un dernier
    segment Whisper composé uniquement de la commande, soit une dernière phrase
    séparée du corps par une ponctuation forte.
    """
    source = str(text or "").strip()
    segment_texts = _segment_texts(segments)
    if segment_texts:
        final_unit = _normalized_unit(segment_texts[-1])
        if final_unit in _SEND_COMMANDS:
            cleaned = _TRAILING_COMMAND_RE.sub("", source).rstrip()
            return DictationDecision(
                text=cleaned, should_send=True,
                command=final_unit, boundary="segment",
            )

    match = _STRONG_SENTENCE_RE.match(source)
    if match and _normalized_unit(match.group("command")) in _SEND_COMMANDS:
        return DictationDecision(
            text=match.group("body").strip(), should_send=True,
            command=_normalized_unit(match.group("command")), boundary="sentence",
        )

    # Une dictée qui ne contient que la commande peut envoyer un texte déjà
    # présent dans le compositeur ; le frontend vérifie que ce texte existe.
    whole = _normalized_unit(source)
    if whole in _SEND_COMMANDS:
        return DictationDecision(
            text="", should_send=True, command=whole, boundary="whole",
        )
    return DictationDecision(text=source, should_send=False)
