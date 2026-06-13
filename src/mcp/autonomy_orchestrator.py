"""
autonomy_orchestrator.py — Phase I-5 : pilote l'autonomie complète MCP.

Doctrine :
  Lumena décrit un besoin (intent), et l'orchestrateur déroule la séquence :
    1. Discover : intent → ResolvedTarget (target_resolver + KNOWN_MCPS)
    2. Schema cascade : récupère MCPConfigSchema (Niveau 1-4)
    3. Auto-resolve config : pour chaque field, cherche valeur existante
       via SecretsResolverService
    4. Apply : stocke dans CredentialsService / ConfigService
    5. Ready state : check is_ready (tous les fields requis ont une valeur)
    6. Décision : ready → autonomy OK, sinon → pending_questions à l'user

  L'orchestrateur ne FAIT PAS l'install/activate. Il prépare TOUT pour
  que le caller (ReAct integration / routes API) puisse les déclencher.

Doctrine sécurité :
  - Les VALEURS de secrets ne sortent JAMAIS de cette couche.
  - `AutonomyResult` n'expose que les NOMS des questions à poser.
  - Tous les fields SECRET résolus sont stockés via CredentialsService
    immédiatement (jamais retournés au caller).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from src.mcp.config_schema import (
    ConfigField,
    MCPConfigSchema,
    Sensitivity,
    schema_from_dict,
)
from src.mcp.config_service import MCPConfigService
from src.mcp.credentials_service import MCPCredentialsService
from src.mcp.secrets_resolver_service import (
    FoundSecret,
    MCPSecretsResolverService,
)
from src.mcp.target_resolver import ResolvedTarget, resolve_target


# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────


class AutonomyLevel(str, Enum):
    """Combien de pouvoir laisser à Lumena."""
    READ_ONLY = "read_only"   # détection / proposition uniquement
    INSTALL = "install"        # install + config autosolvable
    FULL = "full"              # install + config + activate + first_call


class AutonomyState(str, Enum):
    """Etat final de l'orchestration."""
    READY = "ready"
    NEEDS_USER_INPUT = "needs_user_input"
    NEEDS_INSTALL = "needs_install"      # cible identifiée mais pas installée
    NEEDS_OAUTH = "needs_oauth"          # nécessite un flow OAuth manuel user
    NOT_RESOLVED = "not_resolved"        # intent non résolu en MCP
    FAILED = "failed"


@dataclass(frozen=True)
class PendingQuestion:
    """Une question à poser à l'utilisateur pour un field manquant."""
    field_name: str
    label: str
    description: str
    placeholder: Optional[str]
    obtained_from: Optional[str]
    docs_url: Optional[str]
    is_secret: bool


@dataclass(frozen=True)
class ResolvedField:
    """Trace d'un field auto-résolu (jamais la valeur)."""
    field_name: str
    source: str                          # credentials:<sid> | global | env | memory
    confidence: float
    applied: bool                        # True si stocké dans creds/config


@dataclass(frozen=True)
class AutonomyResult:
    """Sortie de l'orchestrateur. AUCUNE valeur de secret n'apparaît ici."""
    state: AutonomyState
    intent: str
    server_id: Optional[str]              # slug curated ou None
    package_spec: Optional[str]
    display_name: Optional[str]
    config_schema: Optional[MCPConfigSchema]
    pending_questions: Tuple[PendingQuestion, ...] = ()
    resolved_fields: Tuple[ResolvedField, ...] = ()
    next_step_hint: str = ""              # texte explicatif pour le caller
    error_reason: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrateur
# ──────────────────────────────────────────────────────────────────────────────


class MCPAutonomyOrchestrator:
    """Pilote la séquence autonomie : discover → resolve → apply → décide."""

    def __init__(
        self,
        *,
        credentials_service: MCPCredentialsService,
        config_service: MCPConfigService,
        secrets_resolver: MCPSecretsResolverService,
    ):
        if not isinstance(credentials_service, MCPCredentialsService):
            raise TypeError("credentials_service required")
        if not isinstance(config_service, MCPConfigService):
            raise TypeError("config_service required")
        if not isinstance(secrets_resolver, MCPSecretsResolverService):
            raise TypeError("secrets_resolver required")
        self._creds = credentials_service
        self._config = config_service
        self._resolver = secrets_resolver

    # ── API principale ───────────────────────────────────────────────────

    def fulfill_capability(
        self,
        intent: str,
        *,
        level: AutonomyLevel = AutonomyLevel.INSTALL,
    ) -> AutonomyResult:
        """Pilote la séquence autonomie.

        Args:
            intent: chaîne user (ex: "envoie un message dans #general slack").
            level: jusqu'où Lumena peut aller sans demander.

        Returns:
            AutonomyResult avec state + questions éventuelles.
        """
        intent = intent if isinstance(intent, str) else ""
        if not intent.strip():
            return AutonomyResult(
                state=AutonomyState.NOT_RESOLVED,
                intent="",
                server_id=None, package_spec=None, display_name=None,
                config_schema=None,
                error_reason="empty_intent",
                next_step_hint="Aucune intention fournie.",
            )

        # 1) Discover
        target = resolve_target(intent)
        if target.kind in ("unknown", "intent"):
            return AutonomyResult(
                state=AutonomyState.NOT_RESOLVED,
                intent=intent,
                server_id=None,
                package_spec=None,
                display_name=None,
                config_schema=None,
                next_step_hint=(
                    "Aucun MCP curated trouvé. Lance la cascade Niveau 2-4 "
                    "(schema_extractor / user snippet) avec une cible plus précise."
                ),
            )

        # 2) Schema
        schema = _build_schema_from_target(target)

        # Fix U (Phase I-7) : pour un MCP NON-curated résolu (npm:/pypi:
        # explicite, URL GitHub...), le target n'embarque pas de schéma →
        # sans lui, les secrets requis ne sont jamais détectés, le panel
        # reste vide, et l'activation crashera sur credentials manquants.
        # La cascade Niveau 2 (parse README depuis le registre npm/PyPI)
        # existait depuis la Phase I-3 mais n'était branchée que sur la
        # route UI manuelle. On l'appelle ici, gated par le flag réseau.
        if schema is None and target.package_spec:
            import os as _os
            _net_ok = _os.getenv(
                "LUMENA_MCP_NETWORK_SEARCH_ENABLED", "",
            ).strip().lower() in ("1", "true", "yes", "on")
            if _net_ok:
                try:
                    from src.mcp.schema_cascade import detect_schema
                    schema = detect_schema(
                        server_id=(
                            target.slug
                            or _slug_from_package_spec(target.package_spec)
                        ),
                        package_spec=target.package_spec,
                        enable_levels=(2,),
                    )
                except Exception:  # noqa: BLE001
                    # Réseau down / README illisible → on continue sans
                    # schéma (MCP sans secrets fonctionnera quand même).
                    schema = None

        # 3) Auto-resolve config
        resolved, pending = self._autoresolve_fields(
            schema=schema,
            server_id=target.slug or _slug_from_package_spec(target.package_spec),
        )

        # 4) Compute state
        sid = target.slug or _slug_from_package_spec(target.package_spec)
        state, hint = self._decide_state(
            level=level, target=target, schema=schema,
            pending=pending, resolved=resolved,
        )

        return AutonomyResult(
            state=state,
            intent=intent,
            server_id=sid,
            package_spec=target.package_spec,
            display_name=target.display_name,
            config_schema=schema,
            pending_questions=tuple(pending),
            resolved_fields=tuple(resolved),
            next_step_hint=hint,
        )

    # ── Helpers internes ────────────────────────────────────────────────

    def _autoresolve_fields(
        self,
        *,
        schema: Optional[MCPConfigSchema],
        server_id: str,
    ) -> Tuple[List[ResolvedField], List[PendingQuestion]]:
        """Pour chaque field requis, tente de retrouver et stocker la valeur."""
        resolved: List[ResolvedField] = []
        pending: List[PendingQuestion] = []
        if schema is None:
            return resolved, pending
        for f in schema.fields:
            if not f.required:
                continue

            # Déjà set ? → resolved, no question
            if _is_field_already_set(f, server_id, self._creds, self._config):
                resolved.append(ResolvedField(
                    field_name=f.name,
                    source="already_set",
                    confidence=1.0,
                    applied=False,
                ))
                continue

            # Cherche dans les sources existantes
            hit: Optional[FoundSecret] = self._resolver.find_existing_value(
                f.name, exclude_server_id=server_id,
            )
            if hit is not None and not hit.requires_user_confirmation:
                # Auto-applique
                _apply_value(
                    field=f, server_id=server_id, value=hit.value,
                    creds=self._creds, config=self._config,
                )
                resolved.append(ResolvedField(
                    field_name=f.name,
                    source=hit.source,
                    confidence=hit.confidence,
                    applied=True,
                ))
                continue

            # Sinon → question à l'user
            pending.append(_question_from_field(f))
        return resolved, pending

    @staticmethod
    def _decide_state(
        *,
        level: AutonomyLevel,
        target: ResolvedTarget,
        schema: Optional[MCPConfigSchema],
        pending: List[PendingQuestion],
        resolved: List[ResolvedField],
    ) -> Tuple[AutonomyState, str]:
        """Combine le niveau d'autonomie et l'état pour décider du verdict."""
        # OAuth → l'utilisateur doit obligatoirement passer dans le navigateur
        if schema is not None and schema.auth_flows:
            return (
                AutonomyState.NEEDS_OAUTH,
                f"Le MCP {target.display_name or target.slug} nécessite un flow "
                f"OAuth. Demande à l'user d'ouvrir le panel MCP pour s'authentifier.",
            )

        if level == AutonomyLevel.READ_ONLY:
            return (
                AutonomyState.READY if not pending else AutonomyState.NEEDS_USER_INPUT,
                "Mode read-only : MCP identifié, aucune action effectuée.",
            )

        # INSTALL / FULL : tout ce qui est résolu est appliqué
        if pending:
            return (
                AutonomyState.NEEDS_USER_INPUT,
                f"{len(pending)} information(s) à demander à l'utilisateur "
                f"avant d'activer le MCP.",
            )
        # Tout résolu → READY (caller peut maintenant install / activate)
        return (
            AutonomyState.READY,
            f"Tous les champs sont résolus. Le caller peut maintenant "
            f"déclencher install (si pas installé) puis activate.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers privés au module
# ──────────────────────────────────────────────────────────────────────────────


def _build_schema_from_target(target: ResolvedTarget) -> Optional[MCPConfigSchema]:
    """Reconstruit le MCPConfigSchema depuis target.config_schema_dict."""
    if target.config_schema_dict is None:
        return None
    return schema_from_dict(target.config_schema_dict)


def _slug_from_package_spec(package_spec: Optional[str]) -> str:
    """Fallback : si pas de slug curated, dérive un id depuis le package_spec."""
    if not isinstance(package_spec, str) or not package_spec:
        return "unknown"
    # 'npm:@scope/pkg' → 'pkg'
    # 'pypi:my-mcp' → 'my-mcp'
    if package_spec.startswith("npm:"):
        rest = package_spec[4:]
        if "/" in rest:
            rest = rest.split("/")[-1]
        return rest.lower().replace("@", "")[:64] or "unknown"
    if package_spec.startswith("pypi:"):
        return package_spec[5:].lower()[:64] or "unknown"
    return package_spec.lower()[:64] or "unknown"


def _is_field_already_set(
    field: ConfigField,
    server_id: str,
    creds: MCPCredentialsService,
    config: MCPConfigService,
) -> bool:
    """Cas où l'utilisateur a déjà saisi ce champ pour ce server."""
    if field.sensitivity == Sensitivity.SECRET:
        return creds.has(server_id, field.name)
    return config.has(server_id, field.name)


def _apply_value(
    *,
    field: ConfigField,
    server_id: str,
    value: str,
    creds: MCPCredentialsService,
    config: MCPConfigService,
) -> None:
    """Routage : SECRET → creds, SENSITIVE/NORMAL → config."""
    if field.sensitivity == Sensitivity.SECRET:
        creds.set(server_id, field.name, value)
    else:
        config.set(server_id, field.name, value)


def _question_from_field(field: ConfigField) -> PendingQuestion:
    return PendingQuestion(
        field_name=field.name,
        label=field.label,
        description=field.description,
        placeholder=field.placeholder,
        obtained_from=field.obtained_from,
        docs_url=field.docs_url,
        is_secret=(field.sensitivity == Sensitivity.SECRET),
    )


__all__ = [
    "MCPAutonomyOrchestrator",
    "AutonomyLevel",
    "AutonomyState",
    "AutonomyResult",
    "PendingQuestion",
    "ResolvedField",
]
