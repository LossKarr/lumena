"""Stable naming and semantic-version rules for personal models."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
_MODEL_RE = re.compile(
    r"^(?P<prefix>[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?)-model-"
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)


@dataclass(frozen=True, order=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        if min(self.major, self.minor, self.patch) < 0:
            raise ValueError("semver_negative")

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value or "")
        if not match:
            raise ValueError("semver_invalid")
        return cls(*(int(part) for part in match.groups()))

    def bump(self, kind: str) -> "SemanticVersion":
        if kind == "patch":
            return SemanticVersion(self.major, self.minor, self.patch + 1)
        if kind == "minor":
            return SemanticVersion(self.major, self.minor + 1, 0)
        if kind == "major":
            return SemanticVersion(self.major + 1, 0, 0)
        raise ValueError("semver_bump_invalid")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def normalize_prefix(value: str | None) -> str:
    raw = (value or "lumena").strip().lower()
    ascii_value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not _PREFIX_RE.fullmatch(normalized):
        raise ValueError("model_prefix_invalid")
    return normalized


def build_model_name(prefix: str | None = None, version: SemanticVersion | str = "1.0.0") -> str:
    parsed = SemanticVersion.parse(version) if isinstance(version, str) else version
    return f"{normalize_prefix(prefix)}-model-{parsed}"


def parse_model_name(value: str) -> tuple[str, SemanticVersion]:
    match = _MODEL_RE.fullmatch(value or "")
    if not match:
        raise ValueError("personal_model_name_invalid")
    return match.group("prefix"), SemanticVersion(
        int(match.group("major")), int(match.group("minor")), int(match.group("patch"))
    )


def next_model_name(current: str, bump: str = "patch") -> str:
    prefix, version = parse_model_name(current)
    return build_model_name(prefix, version.bump(bump))
