"""Extraction bornée de sorties JSON structurées produites par les LLM.

Ce module ne publie jamais le raisonnement brut. Il ne retourne qu'un objet
qui respecte explicitement le contrat demandé par l'appelant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


_MAX_STRUCTURED_SOURCE_CHARS = 131_072


@dataclass(frozen=True, slots=True)
class JsonObjectContract:
    """Clés obligatoires et autorisées d'un objet JSON public."""

    required_keys: frozenset[str]
    allowed_keys: frozenset[str]

    def accepts(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        keys = frozenset(str(key) for key in value)
        return self.required_keys <= keys <= self.allowed_keys


class StructuredOutputError(ValueError):
    """Erreur publique et sans contenu fournisseur."""


def _text_candidates(text: str) -> Iterable[dict[str, Any]]:
    """Émet les objets JSON complets trouvés dans une chaîne bornée."""

    value = str(text or "").strip()
    if not value:
        return
    # Le verdict final se trouve normalement à la fin. Borner depuis la fin
    # évite qu'un raisonnement anormalement long provoque un scan quadratique.
    value = value[-_MAX_STRUCTURED_SOURCE_CHARS:]
    if value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()

    try:
        direct = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        direct = None
    if isinstance(direct, dict):
        yield direct

    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(value[index:])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(candidate, dict):
            yield candidate


def _extract_from_text(text: str, contract: JsonObjectContract) -> dict[str, Any] | None:
    matches = [candidate for candidate in _text_candidates(text) if contract.accepts(candidate)]
    return dict(matches[-1]) if matches else None


def extract_structured_json(raw: Any, contract: JsonObjectContract) -> dict[str, Any]:
    """Retourne le dernier objet conforme, sans rendre le raisonnement visible.

    La sortie publique est prioritaire. ``reasoning_content`` n'est consulté
    qu'en dernier recours et seul l'objet contractuel en est extrait.
    """

    if contract.accepts(raw):
        return dict(raw)

    if isinstance(raw, str):
        match = _extract_from_text(raw, contract)
        if match is not None:
            return match
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if parsed is not None and not isinstance(parsed, dict):
                raise StructuredOutputError("structured_response_not_object")
        raise StructuredOutputError("structured_response_invalid_json")

    if isinstance(raw, dict):
        # Priorité absolue aux champs destinés à l'utilisateur.
        for key in ("text", "content", "output_text"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                match = _extract_from_text(value, contract)
                if match is not None:
                    return match
        # Le raisonnement reste privé : seul le sous-objet conforme sort.
        reasoning = raw.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            match = _extract_from_text(reasoning, contract)
            if match is not None:
                return match
        raise StructuredOutputError("structured_response_invalid_json")

    if isinstance(raw, (list, tuple)):
        for item in reversed(raw):
            try:
                return extract_structured_json(item, contract)
            except StructuredOutputError:
                continue
        raise StructuredOutputError("structured_response_invalid_json")

    raise StructuredOutputError("structured_response_not_object")

