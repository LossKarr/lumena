"""Provider-neutral tool meaning, before authorization or result verification.

Registered descriptors are local policy, never unvalidated catalogue metadata.
The legacy branch preserves today's capability/ledger decisions without trying
to infer a precise effect from an imprecise category. No consumer is rewired here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from enum import Enum
import re

from .plan_evidence import (
    ProofCapability,
    get_tool_capabilities,
    tool_capabilities_are_known_readonly,
)


class SemanticsError(ValueError):
    """Invalid or insufficiently restrictive semantic metadata."""


class ToolEffect(str, Enum):
    READ_ONLY = "READ_ONLY"
    UI_STATE_ONLY = "UI_STATE_ONLY"
    WORKSPACE_CONTEXT = "WORKSPACE_CONTEXT"
    BUFFER_MUTATION = "BUFFER_MUTATION"
    FILE_WRITE = "FILE_WRITE"
    FILESYSTEM_DESTRUCTIVE = "FILESYSTEM_DESTRUCTIVE"
    PROCESS_LAUNCH = "PROCESS_LAUNCH"
    PROCESS_CONTROL = "PROCESS_CONTROL"
    PROCESS_COMPLETION = "PROCESS_COMPLETION"
    TEST_EXECUTION = "TEST_EXECUTION"
    DEBUG_CONTROL = "DEBUG_CONTROL"
    GIT_LOCAL_MUTATION = "GIT_LOCAL_MUTATION"
    DEPLOY_MUTATION = "DEPLOY_MUTATION"
    SETTINGS_MUTATION = "SETTINGS_MUTATION"
    DESTRUCTIVE_SYSTEM = "DESTRUCTIVE_SYSTEM"
    PARAMETER_DEPENDENT = "PARAMETER_DEPENDENT"
    UNKNOWN = "UNKNOWN"


class ProviderKind(str, Enum):
    NATIVE = "native"
    MCP = "mcp"
    IDE = "ide"


class ModelExposure(str, Enum):
    NEVER = "never"
    INTERNAL = "internal"
    CONTEXTUAL = "contextual"
    DIRECT = "direct"


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    SYSTEM = "system"
    DESTRUCTIVE = "destructive"


class Confirmation(str, Enum):
    NEVER = "never"
    POLICY = "policy"
    ALWAYS = "always"


class MissionPolicy(str, Enum):
    FORBIDDEN = "forbidden"
    READONLY = "readonly"
    SCOPED = "scoped"
    ALLOWED = "allowed"


class Idempotency(str, Enum):
    IDEMPOTENT = "idempotent"
    OPERATION_KEY = "operation_key"
    NON_REPLAYABLE = "non_replayable"


class Availability(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


_POLICY_ENUMS = {
    "model_exposure": ModelExposure, "effect": ToolEffect,
    "risk_floor": Risk, "confirmation": Confirmation,
    "mission_policy": MissionPolicy, "idempotency": Idempotency,
}
_NO_DELIVERY_PROOF = frozenset({
    ToolEffect.UI_STATE_ONLY, ToolEffect.WORKSPACE_CONTEXT,
    ToolEffect.BUFFER_MUTATION, ToolEffect.SETTINGS_MUTATION,
    ToolEffect.PARAMETER_DEPENDENT, ToolEffect.UNKNOWN,
})
_READ_CAPABILITIES = frozenset({ProofCapability.FILE_READ, ProofCapability.GENERIC_READONLY,
                              ProofCapability.HTTP_PROBE, ProofCapability.BROWSER_PROBE})
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,191}\Z")


def _string(value: object, *, limit: int, empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= limit and (empty or bool(value))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


@dataclass(frozen=True, slots=True)
class SemanticPolicy:
    model_exposure: ModelExposure
    effect: ToolEffect
    proof_capabilities: frozenset[ProofCapability]
    risk_floor: Risk
    confirmation: Confirmation
    mission_policy: MissionPolicy
    idempotency: Idempotency
    sensitive_fields: frozenset[str]

    def __post_init__(self) -> None:
        for name, enum_type in _POLICY_ENUMS.items():
            if type(getattr(self, name)) is not enum_type:
                raise SemanticsError(f"invalid_{name}")
        if (type(self.proof_capabilities) is not frozenset
                or any(type(item) is not ProofCapability for item in self.proof_capabilities)):
            raise SemanticsError("invalid_proof_capabilities")
        if (type(self.sensitive_fields) is not frozenset or len(self.sensitive_fields) > 128
                or any(not _string(item, limit=128) for item in self.sensitive_fields)):
            raise SemanticsError("invalid_sensitive_fields")
        if self.effect in _NO_DELIVERY_PROOF and self.proof_capabilities:
            raise SemanticsError("effect_cannot_supply_delivery_proof")
        if self.effect is ToolEffect.READ_ONLY and not self.proof_capabilities <= _READ_CAPABILITIES:
            raise SemanticsError("readonly_cannot_supply_mutation_proof")
        if self.effect in {ToolEffect.UNKNOWN, ToolEffect.DESTRUCTIVE_SYSTEM}:
            if self.model_exposure is not ModelExposure.NEVER or self.mission_policy is not MissionPolicy.FORBIDDEN:
                raise SemanticsError("unsafe_effect_exposure")


@dataclass(frozen=True, slots=True)
class ToolSemantics(SemanticPolicy):
    tool_name: str
    provider_kind: ProviderKind
    provider_instance_id: str
    catalog_revision: str
    availability: Availability

    def __post_init__(self) -> None:
        SemanticPolicy.__post_init__(self)
        if not _string(self.tool_name, limit=192) or not _NAME.fullmatch(self.tool_name):
            raise SemanticsError("invalid_tool_name")
        if type(self.provider_kind) is not ProviderKind or type(self.availability) is not Availability:
            raise SemanticsError("invalid_provider_binding")
        for value in (self.provider_instance_id, self.catalog_revision):
            if not _string(value, limit=256, empty=self.provider_kind is ProviderKind.NATIVE):
                raise SemanticsError("invalid_provider_binding")
        if self.provider_kind is ProviderKind.IDE and not self.tool_name.startswith("ide__"):
            raise SemanticsError("invalid_ide_namespace")
        if self.tool_name.startswith("ide__") and self.provider_kind is not ProviderKind.IDE:
            raise SemanticsError("invalid_ide_provider")

    @property
    def known_readonly(self) -> bool:
        return self.effect is ToolEffect.READ_ONLY

    @property
    def model_exposable(self) -> bool:
        """Candidate only: trust, scope and confirmation still need enforcement."""
        return (self.availability is Availability.READY
                and self.model_exposure in {ModelExposure.DIRECT, ModelExposure.CONTEXTUAL}
                and self.effect not in {ToolEffect.UNKNOWN, ToolEffect.PARAMETER_DEPENDENT})


def _parse_policy(raw: Mapping[str, object]) -> SemanticPolicy:
    names = {item.name for item in fields(SemanticPolicy)}
    if not isinstance(raw, Mapping) or set(raw) != names:
        raise SemanticsError("semantic_fields_mismatch")
    values: dict[str, object] = {}
    for name, enum_type in _POLICY_ENUMS.items():
        if type(raw[name]) is not str:
            raise SemanticsError(f"invalid_{name}")
        try:
            values[name] = enum_type(raw[name])
        except ValueError:
            raise SemanticsError(f"unknown_{name}") from None
    proofs = raw["proof_capabilities"]
    sensitive = raw["sensitive_fields"]
    for name, items, limit in (("proof_capabilities", proofs, len(ProofCapability)),
                               ("sensitive_fields", sensitive, 128)):
        if (type(items) is not list or len(items) > limit
                or any(not _string(item, limit=128) for item in items)
                or len(items) != len(set(items))):
            raise SemanticsError(f"invalid_{name}")
    try:
        values["proof_capabilities"] = frozenset(ProofCapability(item) for item in proofs)
    except ValueError:
        raise SemanticsError("unknown_proof_capability") from None
    values["sensitive_fields"] = frozenset(sensitive)
    return SemanticPolicy(**values)


def _rank(value: Enum) -> int:
    return list(type(value)).index(value)


def validate_semantic_announcement(local: ToolSemantics, raw: Mapping[str, object]) -> ToolSemantics:
    """Intersect a provider announcement with an audited LOCAL minimum policy.

    Identity, liveness and revision are exclusively local inputs. Metadata that
    weakens a floor is rejected, not silently repaired. Possible proofs can be
    reduced, never invented; they remain capabilities, not evidence of an effect.
    """
    if type(local) is not ToolSemantics:
        raise SemanticsError("local_policy_required")
    announced = _parse_policy(raw)
    if announced.effect is not local.effect:
        raise SemanticsError("effect_mismatch")
    # System and destructive are different risk dimensions; do not pretend one
    # can replace the other. Either can strengthen a read/write floor.
    allowed_risks = {
        Risk.READ: frozenset(Risk),
        Risk.WRITE: frozenset({Risk.WRITE, Risk.SYSTEM, Risk.DESTRUCTIVE}),
        Risk.SYSTEM: frozenset({Risk.SYSTEM}),
        Risk.DESTRUCTIVE: frozenset({Risk.DESTRUCTIVE}),
    }
    if announced.risk_floor not in allowed_risks[local.risk_floor]:
        raise SemanticsError("risk_floor_reduced_or_incompatible")
    for name in ("confirmation", "idempotency"):
        if _rank(getattr(announced, name)) < _rank(getattr(local, name)):
            raise SemanticsError(f"{name}_weakened")
    for name in ("model_exposure", "mission_policy"):
        if _rank(getattr(announced, name)) > _rank(getattr(local, name)):
            raise SemanticsError(f"{name}_expanded")
    if not announced.proof_capabilities <= local.proof_capabilities:
        raise SemanticsError("proof_capabilities_expanded")
    if not announced.sensitive_fields >= local.sensitive_fields:
        raise SemanticsError("sensitive_fields_removed")
    return replace(local, **{item.name: getattr(announced, item.name) for item in fields(SemanticPolicy)})


@dataclass(frozen=True, slots=True)
class LegacyToolSemantics:
    """Existing facts only, NOT a new authorization policy for native/MCP tools."""

    tool_name: str
    proof_capabilities: frozenset[ProofCapability]
    known_readonly: bool
    ledger_mutation: bool

    @property
    def effect(self) -> ToolEffect:
        return ToolEffect.READ_ONLY if self.known_readonly else ToolEffect.UNKNOWN


def resolve_tool_semantics(
    tool_name: str,
    *,
    registered: Mapping[str, ToolSemantics] | None = None,
    module_category: str = "",
    semantic_category: str = "",
) -> ToolSemantics | LegacyToolSemantics:
    """Registered local metadata, then exact historical mappings, then unknown.

    An IDE command without a locally registered descriptor never falls through
    to the historical generic IDE category. Legacy ide_* facades remain legacy
    until their separately tested migration. This function executes no tool.
    """
    if not _string(tool_name, limit=192) or not _NAME.fullmatch(tool_name):
        raise SemanticsError("invalid_tool_name")
    if registered is not None and tool_name in registered:
        descriptor = registered[tool_name]
        if type(descriptor) is not ToolSemantics or descriptor.tool_name != tool_name:
            raise SemanticsError("invalid_registered_semantics")
        return descriptor
    if tool_name.startswith("ide__"):
        raise SemanticsError("unregistered_ide_semantics")

    # Keep the exact legacy decisions, including their inconsistencies. CONN-4
    # owns consumer migration; a vague GENERIC_MUTATION is not a precise effect.
    from ..runtime.execution_ledger import MUTATION_TOOLS

    return LegacyToolSemantics(
        tool_name=tool_name,
        proof_capabilities=get_tool_capabilities(tool_name, module_category, semantic_category),
        known_readonly=tool_capabilities_are_known_readonly(tool_name, module_category, semantic_category),
        ledger_mutation=tool_name in MUTATION_TOOLS,
    )
