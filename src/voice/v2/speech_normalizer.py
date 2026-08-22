"""SpeechNormalizer — ne jamais LIRE l'imprononçable (V2.2, règle inverse).

But : transformer un texte (réponse Lumena) en quelque chose de parlable, en
*supprimant* ce qui rendrait la voix ridicule si lu littéralement :
chemins, hash, secrets, URLs, JSON, SQL, code, logs, tableaux, stack traces,
markdown brut, identifiants longs, diffs.

À la place, Lumena dit « c'est affiché à l'écran ». Les chiffres, dates et
décisions normales sont conservés. Pur Python, déterministe, aucun I/O.

NB : c'est l'inverse du SpeechPlanner (quoi résumer) — ici on retire seulement
l'imprononçable. Le SpeechPlanner viendra plus tard.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Set

# Phrases de substitution (voix Lumena).
_SAY = {
    "code": "Le code est affiché à l'écran.",
    "path": "Le chemin exact est affiché à l'écran.",
    "url": "Le lien est affiché à l'écran.",
    "hash": "Le hash est affiché à l'écran.",
    "json": "Les données sont affichées à l'écran.",
    "sql": "La requête est affichée à l'écran.",
    "table": "Le tableau est affiché à l'écran.",
    "trace": "L'erreur détaillée est affichée à l'écran.",
    "secret": "(valeur masquée)",
    "ident": "L'identifiant est affiché à l'écran.",
}

_CODE_EXT = (
    "php|py|js|ts|tsx|jsx|json|sql|log|md|txt|yaml|yml|env|sh|bat|ps1|csv|"
    "html|css|xml|ini|toml|java|go|rs|c|cpp|h"
)

_RE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_RE_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_RE_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s]+")
_RE_FILE = re.compile(r"\b[\w./\\-]+\.(?:" + _CODE_EXT + r")\b", re.IGNORECASE)
_RE_SECRET = re.compile(
    r"(?i)\b[\w-]*(?:password|passwd|pwd|secret|secret_key|client_secret|api[_-]?key|"
    r"token|access_token|webhook_secret)[\w-]*\s*[:=]\s*\S+"
)
_RE_HASH = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_RE_LONG_IDENT = re.compile(r"\b[A-Za-z0-9_]{24,}\b")
_RE_TRACE = re.compile(r"(?im)^\s*traceback|^\s*at\s+\w+.*line\s+\d+|File \".*\", line \d+")
_RE_SQL_LINE = re.compile(r"(?im)^\s*(select|insert|update|delete|create|drop|alter|truncate)\b.*$")
_RE_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)


@dataclass
class SpeechText:
    spoken: str
    suppressed: Set[str] = field(default_factory=set)

    def __str__(self) -> str:  # pragma: no cover - confort
        return self.spoken


def _mark(suppressed: Set[str], kind: str) -> str:
    suppressed.add(kind)
    return _SAY[kind]


def _collapse_tables(text: str, suppressed: Set[str]) -> str:
    """Remplace les blocs de tableau markdown (lignes avec >=2 '|') par une phrase."""
    out: List[str] = []
    in_table = False
    for line in text.splitlines():
        if line.count("|") >= 2:
            if not in_table:
                out.append(_mark(suppressed, "table"))
                in_table = True
            # lignes de tableau suivantes : ignorées
        else:
            in_table = False
            out.append(line)
    return "\n".join(out)


def normalize_for_speech(text: str) -> SpeechText:
    """Retourne le texte parlable + l'ensemble des catégories supprimées."""
    if not text:
        return SpeechText("", set())
    suppressed: Set[str] = set()
    s = text

    # 1) Blocs de code ``` ... ``` (avant tout le reste).
    s = _RE_FENCE.sub(lambda m: _mark(suppressed, "code"), s)
    # 2) Stack traces / SQL (niveau ligne).
    s = _RE_TRACE.sub(lambda m: _mark(suppressed, "trace"), s)
    s = _RE_SQL_LINE.sub(lambda m: _mark(suppressed, "sql"), s)
    # 3) Tableaux markdown.
    s = _collapse_tables(s, suppressed)
    # 4) Secrets (clé=valeur) — masqués, jamais prononcés.
    s = _RE_SECRET.sub(lambda m: _mark(suppressed, "secret"), s)
    # 5) URLs, chemins Windows, fichiers de code.
    s = _RE_URL.sub(lambda m: _mark(suppressed, "url"), s)
    s = _RE_WIN_PATH.sub(lambda m: _mark(suppressed, "path"), s)
    s = _RE_FILE.sub(lambda m: _mark(suppressed, "path"), s)
    # 6) Hash longs puis identifiants longs (l'ordre évite de couper un hash en deux).
    s = _RE_HASH.sub(lambda m: _mark(suppressed, "hash"), s)
    s = _RE_LONG_IDENT.sub(lambda m: _mark(suppressed, "ident"), s)

    # Nettoyage : espaces multiples, phrases de substitution dupliquées consécutives.
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return SpeechText(spoken=s, suppressed=suppressed)


def prepare_for_tts(text: str) -> str:
    """Return the canonical French text actually sent to a TTS engine.

    This projection never changes the displayed answer. It removes formatting
    that Piper pronounces literally and recomposes combining accents before
    phonemization.
    """
    s = normalize_for_speech(text or "").spoken
    s = unicodedata.normalize("NFC", s)
    for source, target in (
        ("’", "'"), ("‘", "'"), ("“", ""), ("”", ""),
        ('"', ""), ("«", ""), ("»", ""), ("…", "..."),
    ):
        s = s.replace(source, target)
    s = re.sub(r"(?m)^\s*[-*•]\s+", "", s)
    s = re.sub(r"[*_`#>]+", " ", s)
    s = re.sub(r"\s+[-–—]\s+", ", ", s)
    s = s.replace("—", ", ").replace("–", ", ")
    s = _RE_EMOJI.sub("", s)
    s = re.sub(r"\s*/\s*", ", ", s)
    s = re.sub(r"\n{2,}", ". ", s)
    s = re.sub(r"\n", ". ", s)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    s = re.sub(r"\.\s*,", ", ", s)
    s = re.sub(r"([?!:;])\s*\.", r"\1", s)
    s = re.sub(r"\.{4,}", "...", s)
    s = re.sub(r"(?:,\s*){2,}", ", ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return re.sub(r"^[\s,]+", "", s).strip()
