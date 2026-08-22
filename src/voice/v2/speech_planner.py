"""Deterministic planning of canonical Lumena text for speech.

The planner never calls an LLM and never changes the display answer. It only
builds the short, pronounceable projection consumed by Voice V2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Set

from .speech_normalizer import normalize_for_speech


_INTERNAL_RE = re.compile(
    r"(?im)^\s*(?:THOUGHT|ACTION|ACTION_INPUT|OBSERVATION|PLAN)\s*:"
)
_CLAIM_RE = re.compile(
    r"(?i)\b(?:"
    r"tests?\s+(?:sont\s+)?(?:verts?|reussis?|passes?)|pytest\s+\d+\s*/\s*\d+|"
    r"publie(?:e|s|es)?|deplo(?:ye|yee|yes|yees)|"
    r"navigateur\s+(?:verifie|valide|teste)|(?:verifie|valide|teste)\s+(?:au|dans le)\s+navigateur|"
    r"(?:mail|message|fichier|paiement|commande)\s+(?:envoye|supprime|cree|effectue)|"
    r"(?:j'ai|je l'ai)\s+(?:envoye|supprime|publie|deplo(?:ye|yee)|achete|paye)"
    r")\b"
)
_NEGATED_CLAIM_RE = re.compile(
    r"(?i)\b(?:non|pas|sans|impossible|echec|echoue|non verifie|non execute)\b"
)
_MARKDOWN_RE = re.compile(r"[*_`#>]+")
_BULLET_RE = re.compile(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+")
_SPACE_RE = re.compile(r"[ \t]{2,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS = ("m.", "mme.", "dr.", "ex.", "etc.", "p.ex.")


@dataclass(frozen=True)
class SpeechPlan:
    spoken: str
    suppressed: Set[str] = field(default_factory=set)
    canonical_verified: bool = False


def _sentences(text: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_RE.split(text or "") if part.strip()]


def _has_unverified_sensitive_claim(sentence: str) -> bool:
    return bool(_CLAIM_RE.search(sentence) and not _NEGATED_CLAIM_RE.search(sentence))


def _clean_plain_text(text: str) -> str:
    s = _MARKDOWN_RE.sub(" ", text or "")
    s = _BULLET_RE.sub("", s)
    s = re.sub(r"\n{2,}", ". ", s)
    s = re.sub(r"\n", " ", s)
    s = _SPACE_RE.sub(" ", s)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    return s.strip(" ,")


def plan_speech(
    canonical_text: str,
    *,
    canonical_verified: bool = False,
    max_chars: int = 360,
    max_sentences: int = 3,
) -> SpeechPlan:
    """Return a safe spoken projection without changing ``canonical_text``."""
    if not (canonical_text or "").strip():
        return SpeechPlan("", set(), canonical_verified)

    suppressed: Set[str] = set()
    if _INTERNAL_RE.search(canonical_text):
        suppressed.add("internal_reasoning")
        lines = [line for line in canonical_text.splitlines() if not _INTERNAL_RE.match(line)]
        canonical_text = "\n".join(lines)

    normalized = normalize_for_speech(canonical_text)
    suppressed.update(normalized.suppressed)
    plain = _clean_plain_text(normalized.spoken)

    kept: List[str] = []
    for sentence in _sentences(plain):
        if not canonical_verified and _has_unverified_sensitive_claim(sentence):
            suppressed.add("unverified_claim")
            continue
        kept.append(sentence)
        if len(kept) >= max(1, int(max_sentences)):
            break

    spoken = " ".join(kept).strip()
    if not spoken and suppressed:
        spoken = "Le resultat detaille est affiche a l'ecran."
    if len(spoken) > max_chars:
        cut = spoken[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")
        spoken = (cut or spoken[:max_chars]).rstrip(". ") + "."
    return SpeechPlan(spoken, suppressed, canonical_verified)


class SentenceCommitter:
    """Commit only complete, pronounceable sentences from safe incremental text."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> List[str]:
        self._buffer += chunk or ""
        committed: List[str] = []
        while True:
            match = re.search(r"[.!?](?:\s+|$)", self._buffer)
            if not match:
                break
            end = match.end()
            candidate = self._buffer[:end].strip()
            if candidate.lower().endswith(_ABBREVIATIONS):
                next_match = re.search(r"[.!?](?:\s+|$)", self._buffer[end:])
                if not next_match:
                    break
                end += next_match.end()
                candidate = self._buffer[:end].strip()
            self._buffer = self._buffer[end:]
            if candidate and not _INTERNAL_RE.search(candidate):
                planned = plan_speech(candidate, canonical_verified=True, max_sentences=1)
                if planned.spoken:
                    committed.append(planned.spoken)
        return committed

    def flush(self) -> List[str]:
        candidate = self._buffer.strip()
        self._buffer = ""
        if not candidate or _INTERNAL_RE.search(candidate):
            return []
        planned = plan_speech(candidate, canonical_verified=True, max_sentences=1)
        return [planned.spoken] if planned.spoken else []
