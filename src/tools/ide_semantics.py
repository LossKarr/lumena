"""Audited IDE policy owned by Lumena, not downloaded from an IDE peer.

Catalogue validation is not permission to execute. Parameter-dependent effects
require a local call decision; possible proofs still need observed results.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _contracts() -> dict:
    return json.loads(Path(__file__).with_name("ide_tool_policy.json").read_text(encoding="utf-8"))


def ide_contract(command_id: str) -> dict:
    """An independent copy; callers cannot change the local minimum policy."""
    if type(command_id) is not str or command_id not in _contracts():
        raise ValueError("ide_command_not_audited")
    return deepcopy(_contracts()[command_id])


def audited_ide_commands() -> tuple[str, ...]:
    return tuple(sorted(_contracts()))


def _same_shape(local: Any, announced: Any) -> bool:
    # Descend only through the bounded LOCAL schema, not arbitrary peer trees.
    if type(local) is not type(announced):
        return False
    if type(local) is dict:
        return local.keys() == announced.keys() and all(_same_shape(v, announced[k]) for k, v in local.items())
    if type(local) is list:
        return len(local) == len(announced) and all(_same_shape(a, b) for a, b in zip(local, announced))
    return local == announced


def local_ide_semantics(command_id: str, *, instance_id: str, revision: str, availability):
    # Lazy: the existing reasoning package initializer loads the engine. The
    # wire codec can be imported without introducing that dependency at import.
    from ..reasoning.tool_semantics import (
        Confirmation, Idempotency, MissionPolicy, ModelExposure, ProofCapability,
        ProviderKind, Risk, ToolEffect, ToolSemantics,
    )

    raw = ide_contract(command_id)["semantics"]
    return ToolSemantics(
        tool_name="ide__" + command_id, provider_kind=ProviderKind.IDE,
        provider_instance_id=instance_id, catalog_revision=revision, availability=availability,
        model_exposure=ModelExposure(raw["model_exposure"]), effect=ToolEffect(raw["effect"]),
        proof_capabilities=frozenset(ProofCapability(p) for p in raw["proof_capabilities"]),
        risk_floor=Risk(raw["risk_floor"]), confirmation=Confirmation(raw["confirmation"]),
        mission_policy=MissionPolicy(raw["mission_policy"]), idempotency=Idempotency(raw["idempotency"]),
        sensitive_fields=frozenset(raw["sensitive_fields"]),
    )


def validate_ide_contract(command: dict) -> None:
    from ..reasoning.tool_semantics import Availability, validate_semantic_announcement

    audited = ide_contract(command["id"])
    if not _same_shape(audited["input_schema"], command.get("input_schema")):
        raise ValueError("ide_input_schema_mismatch")
    schema = audited["input_schema"]
    parameters = command["parameters"]
    if (set(parameters) != set(schema["properties"])
            or any(spec["type"] != schema["properties"][key]["type"]
                   or (spec.get("required", False) is True) != (key in schema["required"])
                   for key, spec in parameters.items())):
        raise ValueError("ide_parameters_mismatch")
    local = local_ide_semantics(command["id"], instance_id="catalogue-validation",
                                revision="catalogue-validation", availability=Availability.UNAVAILABLE)
    validate_semantic_announcement(local, command.get("semantics"))


@dataclass(frozen=True, slots=True)
class AuditedRetrySource:
    """Supplied by the local operation journal adapter, NEVER by model params."""
    operation_id: str
    instance_id: str
    catalog_revision: str
    workspace_id: str
    action: str
    retryable: bool


def resolve_ide_call(descriptor, params: dict, *, workspace_id: str = "",
                     retry_source: AuditedRetrySource | None = None):
    from ..reasoning.tool_semantics import (
        MissionPolicy, ProofCapability, SemanticsError, ToolEffect, ToolSemantics, validate_semantic_announcement,
    )

    if type(descriptor) is not ToolSemantics or not descriptor.tool_name.startswith("ide__"):
        raise SemanticsError("local_ide_descriptor_required")
    name = descriptor.tool_name[5:]
    raw = {key: getattr(descriptor, key) for key in ide_contract(name)["semantics"]}
    raw = {key: sorted(item.value if hasattr(item, "value") else item for item in value)
           if type(value) is frozenset else value.value for key, value in raw.items()}
    local = local_ide_semantics(name, instance_id=descriptor.provider_instance_id,
                                revision=descriptor.catalog_revision, availability=descriptor.availability)
    descriptor = validate_semantic_announcement(local, raw)
    if descriptor.effect is not ToolEffect.PARAMETER_DEPENDENT:
        return descriptor
    schema = ide_contract(name)["input_schema"]
    if type(params) is not dict or not set(schema["required"]) <= params.keys() <= schema["properties"].keys():
        raise SemanticsError("ide_call_parameters_invalid")
    for key, value in params.items():
        expected = {"string": str, "boolean": bool, "object": dict}[schema["properties"][key]["type"]]
        if type(value) is not expected:
            raise SemanticsError("ide_call_parameters_invalid")
    effect, proofs, mission = None, frozenset(), descriptor.mission_policy
    if name == "editor_find_replace":
        effect = ToolEffect.BUFFER_MUTATION if "replace" in params else ToolEffect.READ_ONLY
        if effect is ToolEffect.READ_ONLY:
            proofs = frozenset({ProofCapability.GENERIC_READONLY})
            if mission is not MissionPolicy.FORBIDDEN:
                mission = MissionPolicy.READONLY
    elif name == "git_sync":
        if params["operation"] not in {"fetch", "pull", "push"}:
            raise SemanticsError("ide_call_parameters_invalid")
        effect = ToolEffect.DEPLOY_MUTATION if params["operation"] == "push" else ToolEffect.GIT_LOCAL_MUTATION
        proofs = frozenset({ProofCapability.DEPLOY_MUTATION if params["operation"] == "push"
                            else ProofCapability.GENERIC_MUTATION})
    elif name == "terminal_run":
        # Sending characters to a PTY never supplies command completion evidence.
        effect = ToolEffect.PROCESS_CONTROL
    elif name == "operation_retry":
        source = retry_source
        if (type(source) is not AuditedRetrySource or source.retryable is not True
                or not workspace_id or source.workspace_id != workspace_id
                or source.operation_id != params["operationId"]
                or source.instance_id != descriptor.provider_instance_id
                or source.catalog_revision != descriptor.catalog_revision
                or source.action not in {"task_run", "test_run"}):
            raise SemanticsError("ide_retry_source_unproven")
        key = "taskId" if source.action == "task_run" else "itemId"
        replay = params["parameters"]
        if set(replay) != {key} or type(replay[key]) is not str or not replay[key]:
            raise SemanticsError("ide_call_parameters_invalid")
        effect = ToolEffect.PROCESS_LAUNCH if source.action == "task_run" else ToolEffect.TEST_EXECUTION
        proofs = frozenset({ProofCapability.PROCESS_LAUNCH if source.action == "task_run"
                            else ProofCapability.TEST_EXECUTION})
    if effect is None:
        raise SemanticsError("ide_effect_unresolved")
    return replace(descriptor, effect=effect, proof_capabilities=proofs, mission_policy=mission)
