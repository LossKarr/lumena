"""
secrets_resolver_service.py — Phase I-4 : recherche multi-sources d'un secret.

Doctrine : Lumena ne doit JAMAIS demander à l'utilisateur une clé qu'elle a
déjà accessible. Ce service cherche un secret existant dans, par ordre de
priorité :

  1. MCPCredentialsService autres MCPs (ex: SLACK_BOT_TOKEN déjà setté
     pour un autre Slack workspace)
  2. SecretsService scope global Lumena (clés héritées du setup)
  3. os.environ (clés .env legacy)
  4. (optionnel) Mémoire long-terme ChromaDB (recherche sémantique)
  5. None → l'orchestrateur demandera à l'utilisateur

Chaque source retourne une `FoundSecret` avec un score de confidence pour
permettre à l'orchestrateur de décider s'il auto-applique ou demande
confirmation utilisateur.

Doctrine sécurité : la VALEUR n'est JAMAIS loggée. Les logs mentionnent
seulement le nom de la clé et la source.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

from src.mcp.credentials_service import MCPCredentialsService
from src.services.secrets_service import SecretsService


# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoundSecret:
    """Résultat d'une recherche : valeur + provenance + confidence."""
    value: str
    source: str   # "credentials:<server_id>" | "global" | "env" | "memory"
    confidence: float                       # 0.0 → 1.0
    requires_user_confirmation: bool        # True si confidence < seuil


# Callable injectable pour la mémoire ChromaDB. Retourne (value, score) ou None.
# La signature reste neutre : on n'impose pas un type ChromaDB ici.
MemoryLookup = Callable[[str, Tuple[str, ...]], Optional[Tuple[str, float]]]


# Seuil sous lequel on exige une confirmation user (subtilité : mémoire =
# float, env/credentials = exact match donc confidence 1.0).
_USER_CONFIRMATION_THRESHOLD = 0.85


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────


class MCPSecretsResolverService:
    """Cherche une valeur de secret à travers toutes les sources connues."""

    GLOBAL_SCOPE = "lumena_global"

    def __init__(
        self,
        *,
        credentials_service: MCPCredentialsService,
        secrets_service: SecretsService,
        memory_lookup: Optional[MemoryLookup] = None,
    ):
        if not isinstance(credentials_service, MCPCredentialsService):
            raise TypeError("credentials_service must be MCPCredentialsService")
        if not isinstance(secrets_service, SecretsService):
            raise TypeError("secrets_service must be SecretsService")
        if memory_lookup is not None and not callable(memory_lookup):
            raise TypeError("memory_lookup must be callable or None")
        self._creds = credentials_service
        self._secrets = secrets_service
        self._memory_lookup = memory_lookup

    # ── API principale ───────────────────────────────────────────────────

    def find_existing_value(
        self,
        canonical_name: str,
        *,
        aliases: Sequence[str] = (),
        exclude_server_id: Optional[str] = None,
    ) -> Optional[FoundSecret]:
        """Cherche une valeur pour `canonical_name` dans les 4 sources.

        Args:
            canonical_name: nom canonique recherché (ex: "SLACK_BOT_TOKEN").
            aliases: noms équivalents historiques (ex: "SLACK_TOKEN").
            exclude_server_id: si fourni, on EXCLUT ce server_id des sources
                (utile pour éviter de retomber sur soi-même).

        Returns:
            FoundSecret ou None si aucune source n'a la valeur.
        """
        if not isinstance(canonical_name, str) or not canonical_name:
            return None
        candidates = self._normalize_candidates(canonical_name, aliases)

        # 1) Autres MCPs
        r = self._find_in_other_credentials(candidates, exclude_server_id)
        if r is not None:
            return r

        # 2) Scope global Lumena
        r = self._find_in_global_secrets(candidates)
        if r is not None:
            return r

        # 3) os.environ
        r = self._find_in_env(candidates)
        if r is not None:
            return r

        # 4) Mémoire long-terme (optionnel)
        if self._memory_lookup is not None:
            try:
                hit = self._memory_lookup(canonical_name, tuple(candidates))
            except Exception:  # noqa: BLE001
                hit = None
            if isinstance(hit, tuple) and len(hit) >= 2:
                value, score = hit[0], float(hit[1])
                if isinstance(value, str) and value:
                    return FoundSecret(
                        value=value,
                        source="memory",
                        confidence=max(0.0, min(1.0, score)),
                        requires_user_confirmation=score < _USER_CONFIRMATION_THRESHOLD,
                    )

        return None

    def find_for_keys(
        self,
        keys: Sequence[str],
        *,
        exclude_server_id: Optional[str] = None,
    ) -> dict:
        """Batch lookup : {key: FoundSecret|None}."""
        out = {}
        for k in keys or ():
            out[k] = self.find_existing_value(
                k, exclude_server_id=exclude_server_id,
            )
        return out

    # ── Sources individuelles ───────────────────────────────────────────

    def _find_in_other_credentials(
        self,
        candidates: List[str],
        exclude_server_id: Optional[str],
    ) -> Optional[FoundSecret]:
        """Cherche dans tous les MCPs qui ont déjà des credentials posés."""
        all_scopes = self._secrets.list_scopes()
        prefix = f"{self._creds.SCOPE_PREFIX}."
        for scope in all_scopes:
            if not scope.startswith(prefix):
                continue
            server_id = scope[len(prefix):]
            if not server_id or server_id == exclude_server_id:
                continue
            existing = set(self._secrets.list_keys(scope))
            for name in candidates:
                if name in existing:
                    v = self._secrets.get(scope, name)
                    if isinstance(v, str) and v:
                        return FoundSecret(
                            value=v,
                            source=f"credentials:{server_id}",
                            confidence=1.0,
                            requires_user_confirmation=False,
                        )
        return None

    def _find_in_global_secrets(
        self, candidates: List[str],
    ) -> Optional[FoundSecret]:
        existing = set(self._secrets.list_keys(self.GLOBAL_SCOPE))
        for name in candidates:
            if name in existing:
                v = self._secrets.get(self.GLOBAL_SCOPE, name)
                if isinstance(v, str) and v:
                    return FoundSecret(
                        value=v,
                        source="global",
                        confidence=0.95,
                        requires_user_confirmation=False,
                    )
        return None

    def _find_in_env(self, candidates: List[str]) -> Optional[FoundSecret]:
        for name in candidates:
            v = os.environ.get(name)
            if isinstance(v, str) and v:
                return FoundSecret(
                    value=v,
                    source="env",
                    confidence=0.9,
                    requires_user_confirmation=False,
                )
        return None

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_candidates(
        canonical: str, aliases: Sequence[str],
    ) -> List[str]:
        """Liste ordonnée des noms à essayer : canonical + aliases + variantes."""
        seen = set()
        out: List[str] = []
        for n in (canonical, *aliases):
            if isinstance(n, str) and n and n not in seen:
                out.append(n)
                seen.add(n)
        # Variantes courantes
        for n in (canonical, *aliases):
            if not isinstance(n, str) or not n:
                continue
            up = n.upper()
            if up not in seen:
                out.append(up)
                seen.add(up)
        return out


__all__ = [
    "MCPSecretsResolverService",
    "FoundSecret",
    "MemoryLookup",
]
