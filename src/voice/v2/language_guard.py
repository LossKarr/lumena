"""Lightweight post-generation language guard for spoken answers."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


_MARKERS = {
    "fr": {
        "bonjour", "merci", "avec", "pour", "dans", "mais", "donc", "cela",
        "cette", "votre", "voici", "alors", "maintenant", "peux", "vais", "fait",
        "est", "sont", "une", "des", "les", "que", "pas",
    },
    "en": {
        "hello", "thanks", "thank", "with", "for", "but", "this", "that", "your",
        "here", "now", "can", "will", "done", "is", "are", "the", "and", "not",
    },
    "es": {
        "hola", "gracias", "con", "para", "pero", "esto", "esta", "este", "ahora",
        "puedo", "voy", "hecho", "es", "son", "una", "los", "las", "que", "no",
    },
}


def _plain_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(text or "").lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.findall(r"[a-z]+", normalized)


@dataclass(frozen=True)
class LanguageCheck:
    expected: str
    detected: Optional[str]
    confidence: float
    compliant: bool
    reason: str


def detect_text_language(text: str) -> tuple[Optional[str], float]:
    raw = str(text or "")
    words = _plain_words(raw)
    if not words:
        return None, 0.0
    scores = {language: sum(word in markers for word in words)
              for language, markers in _MARKERS.items()}
    if "¿" in raw or "¡" in raw or "ñ" in raw.lower():
        scores["es"] += 2
    if re.search(r"[àâçéèêëîïôùûüÿœ]", raw.lower()):
        scores["fr"] += 1
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ordered[0]
    second_score = ordered[1][1]
    if best_score < 2 or best_score == second_score:
        return None, 0.0
    confidence = min(1.0, (best_score - second_score + best_score) / max(4.0, best_score * 2.0))
    return best, round(confidence, 3)


def check_response_language(
    text: str, expected: str, *, minimum_confidence: float = 0.60
) -> LanguageCheck:
    expected_code = str(expected or "fr").lower().split("-")[0]
    detected, confidence = detect_text_language(text)
    if detected is None or confidence < minimum_confidence:
        return LanguageCheck(
            expected_code, detected, confidence, True, "insufficient_evidence"
        )
    return LanguageCheck(
        expected_code, detected, confidence, detected == expected_code,
        "matches" if detected == expected_code else "language_mismatch",
    )
