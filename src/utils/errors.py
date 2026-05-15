"""
Systeme d'erreurs categorisees pour Lumena.

Chaque erreur est classifiee par categorie et par capacite de retry,
permettant une gestion intelligente des fallbacks.
"""

from __future__ import annotations

from typing import Optional


class LumenaError(Exception):
    """Erreur de base Lumena avec categorisation."""

    def __init__(
        self,
        message: str,
        category: str = "internal",
        retryable: bool = False,
        original: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.original = original

    def __str__(self) -> str:
        base = super().__str__()
        if self.original:
            return f"{base} (cause: {self.original})"
        return base


class ProviderError(LumenaError):
    """Erreur liee a un provider LLM."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        model: str = "",
        category: str = "network",
        retryable: bool = True,
        original: Optional[Exception] = None,
    ):
        super().__init__(message, category=category, retryable=retryable, original=original)
        self.provider = provider
        self.model = model


class ToolExecutionError(LumenaError):
    """Erreur lors de l'execution d'un outil."""

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        category: str = "internal",
        retryable: bool = False,
        original: Optional[Exception] = None,
    ):
        super().__init__(message, category=category, retryable=retryable, original=original)
        self.tool_name = tool_name


class MemoryError_(LumenaError):
    """Erreur liee au systeme de memoire (nom avec _ pour eviter conflit builtin)."""

    def __init__(
        self,
        message: str,
        category: str = "internal",
        retryable: bool = False,
        original: Optional[Exception] = None,
    ):
        super().__init__(message, category=category, retryable=retryable, original=original)


class ConfigError(LumenaError):
    """Erreur de configuration."""

    def __init__(
        self,
        message: str,
        original: Optional[Exception] = None,
    ):
        super().__init__(message, category="config", retryable=False, original=original)


# Categories d'erreurs supportees
ERROR_CATEGORIES = {
    "auth": "Erreur d'authentification (cle API invalide/expiree)",
    "rate_limit": "Limite de debit atteinte",
    "timeout": "Timeout de la requete",
    "format": "Erreur de format de reponse",
    "network": "Erreur reseau (connexion, DNS, etc.)",
    "config": "Erreur de configuration",
    "internal": "Erreur interne",
}


def classify_error(error: Exception) -> str:
    """Classifie une exception en categorie d'erreur."""
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    # Auth
    if any(k in error_str for k in ("401", "403", "unauthorized", "forbidden", "invalid.*key", "api.key")):
        return "auth"

    # Rate limit
    if any(k in error_str for k in ("429", "rate.limit", "too.many", "quota")):
        return "rate_limit"

    # Timeout
    if any(k in error_str for k in ("timeout", "timed.out", "etimedout")):
        return "timeout"
    if "timeout" in error_type:
        return "timeout"

    # Network
    if any(k in error_str for k in ("connect", "refused", "reset", "broken.pipe", "eof")):
        return "network"
    if any(k in error_type for k in ("connect", "socket", "protocol")):
        return "network"

    # Format
    if any(k in error_type for k in ("json", "decode", "parse", "validation")):
        return "format"

    return "internal"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
