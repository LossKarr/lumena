"""Deterministic language intent and session policy for Voice V3.

Detection never changes the durable response language by itself. Only explicit
commands may mutate the session or profile; translation requests are one-shot.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Dict, Literal, Optional

from src.utils.persistence import atomic_write_text


LanguageScope = Literal["profile", "session", "one_shot", "quoted_span"]
LanguageSource = Literal["default", "detected", "explicit", "channel"]

_LANGUAGES: Dict[str, tuple[str, ...]] = {
    "fr": ("francais", "francaise", "french"),
    "en": ("anglais", "english", "ingles"),
    "es": ("espagnol", "espagnole", "espanol", "spanish", "castellano"),
    "de": ("allemand", "allemande", "german", "deutsch"),
    "it": ("italien", "italienne", "italian", "italiano"),
    "pt": ("portugais", "portugaise", "portuguese", "portugues"),
    "nl": ("neerlandais", "dutch", "nederlands"),
    "ar": ("arabe", "arabic"),
    "zh": ("chinois", "chinoise", "chinese", "mandarin"),
    "ja": ("japonais", "japonaise", "japanese"),
    "ko": ("coreen", "coreenne", "korean"),
    "ru": ("russe", "russian"),
    "pl": ("polonais", "polonaise", "polish"),
    "tr": ("turc", "turque", "turkish"),
}
_NAMES = {
    "fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand",
    "it": "italien", "pt": "portugais", "nl": "néerlandais", "ar": "arabe",
    "zh": "chinois", "ja": "japonais", "ko": "coréen", "ru": "russe",
    "pl": "polonais", "tr": "turc",
}


def _plain(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip()


def normalize_language(value: Optional[str], fallback: str = "fr") -> str:
    candidate = _plain(value or "")
    if candidate in _LANGUAGES:
        return candidate
    for code, aliases in _LANGUAGES.items():
        if candidate in aliases:
            return code
    if fallback == "":
        return ""
    return fallback if fallback in _LANGUAGES else "fr"


def _mentioned_language(text: str) -> Optional[str]:
    padded = f" {_plain(text)} "
    hits = []
    for code, aliases in _LANGUAGES.items():
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\b", padded)
            if match:
                hits.append((match.start(), code))
    return max(hits, default=(0, None), key=lambda item: item[0])[1]


@dataclass(frozen=True)
class LanguageState:
    profile_language: str = "fr"
    session_language: str = "fr"
    detected_input_language: Optional[str] = None
    detected_confidence: float = 0.0
    response_language: str = "fr"
    scope: LanguageScope = "session"
    source: LanguageSource = "default"


@dataclass(frozen=True)
class LanguageDecision:
    state: LanguageState
    response_language: str
    scope: LanguageScope
    changed: bool
    persist_profile: bool
    reason: str

    @property
    def language_name(self) -> str:
        return _NAMES.get(self.response_language, self.response_language)

    def prompt_instruction(self) -> str:
        return (
            f"Réponds en {self.language_name}. Garde cette langue pour le texte parlé, "
            "sauf les mots ou citations que l'utilisateur demande explicitement de traduire."
        )


class LanguagePolicy:
    """Own language state for one user/channel pair."""

    def __init__(
        self,
        *,
        default_language: str = "fr",
        user_id: str = "voice:guest",
        channel: str = "voice",
        state_path: Optional[Path] = None,
    ) -> None:
        self._lock = Lock()
        self.user_id = str(user_id or "voice:guest")
        self.channel = str(channel or "voice")
        self.state_path = state_path
        profile = self._load_profile(normalize_language(default_language))
        self._state = LanguageState(
            profile_language=profile, session_language=profile,
            response_language=profile,
        )

    @property
    def state(self) -> LanguageState:
        with self._lock:
            return replace(self._state)

    @property
    def _profile_key(self) -> str:
        return f"{self.channel}:{self.user_id}"

    def _load_profile(self, fallback: str) -> str:
        if self.state_path is None:
            return fallback
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return normalize_language(payload.get("profiles", {}).get(self._profile_key), fallback)
        except (OSError, ValueError, TypeError, AttributeError):
            return fallback

    def _persist(self, language: str) -> None:
        if self.state_path is None:
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except (OSError, ValueError, TypeError):
            payload = {}
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        profiles[self._profile_key] = language
        payload = {"schema": "lumena.voice-language.v1", "profiles": profiles}
        atomic_write_text(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def decide(
        self,
        text: str,
        *,
        detected_language: Optional[str] = None,
        detected_confidence: float = 0.0,
    ) -> LanguageDecision:
        normalized = _plain(text)
        target = _mentioned_language(normalized)
        detected = normalize_language(detected_language, "") if detected_language else None
        confidence = max(0.0, min(1.0, float(detected_confidence or 0.0)))
        with self._lock:
            current = self._state
            if detected not in _LANGUAGES or confidence < 0.50:
                detected = current.detected_input_language
                confidence = current.detected_confidence if detected else 0.0
            observed = replace(
                current,
                detected_input_language=detected,
                detected_confidence=confidence,
            )

            durable = bool(re.search(
                r"\b(toujours|par defaut|langue par defaut|always|por defecto|siempre)\b",
                normalized,
            ))
            session = bool(re.search(
                r"\b(a partir de maintenant|desormais|dorenavant|maintenant|continue(?:r)?|"
                r"from now on|now speak|a partir de ahora|ahora habla)\b",
                normalized,
            ))
            speech_verb = bool(re.search(
                r"\b(parle|parler|reponds|repondre|continue|speak|answer|respond|habla|responde)\b",
                normalized,
            ))
            translation = bool(re.search(
                r"\b(traduis|traduire|traduction|comment (?:dit|dire|traduit)(?:-on)?|"
                r"comment on dit|prononce|translate|how do (?:you|we) say|traduce|como se dice)\b",
                normalized,
            ))
            one_shot = bool(re.search(
                r"\b(cette fois|une fois|juste cette reponse|reponds en|answer in|respond in)\b",
                normalized,
            ))
            reset = bool(re.search(
                r"\b(reviens|retourne|repasse|go back|vuelve)\b", normalized
            )) and target is not None

            if target and translation:
                decision = LanguageDecision(
                    state=replace(observed, response_language=target, scope="one_shot", source="explicit"),
                    response_language=target, scope="one_shot", changed=False,
                    persist_profile=False, reason="explicit_translation",
                )
            elif target and speech_verb and durable:
                observed = replace(
                    observed, profile_language=target, session_language=target,
                    response_language=target, scope="profile", source="explicit",
                )
                self._state = observed
                self._persist(target)
                decision = LanguageDecision(
                    state=observed, response_language=target, scope="profile", changed=True,
                    persist_profile=True, reason="explicit_profile_change",
                )
            elif target and speech_verb and (session or reset):
                observed = replace(
                    observed, session_language=target, response_language=target,
                    scope="session", source="explicit",
                )
                self._state = observed
                decision = LanguageDecision(
                    state=observed, response_language=target, scope="session", changed=True,
                    persist_profile=False, reason="explicit_session_change",
                )
            elif target and speech_verb and one_shot:
                decision = LanguageDecision(
                    state=replace(observed, response_language=target, scope="one_shot", source="explicit"),
                    response_language=target, scope="one_shot", changed=False,
                    persist_profile=False, reason="explicit_one_shot",
                )
            else:
                observed = replace(
                    observed, response_language=observed.session_language,
                    scope="session", source=current.source,
                )
                self._state = observed
                decision = LanguageDecision(
                    state=observed, response_language=observed.session_language,
                    scope="session", changed=False, persist_profile=False,
                    reason="session_policy",
                )
            return decision
