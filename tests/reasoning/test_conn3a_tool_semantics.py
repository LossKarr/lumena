"""Policy floors are local; catalogue metadata cannot grant extra authority."""

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from src.reasoning.plan_evidence import (
    ProofCapability as Proof,
    get_tool_capabilities,
    tool_capabilities_are_known_readonly,
)
from src.reasoning.tool_semantics import (
    Availability, Confirmation, Idempotency, LegacyToolSemantics, MissionPolicy,
    ModelExposure, ProviderKind, Risk, SemanticPolicy, SemanticsError,
    ToolEffect, ToolSemantics, resolve_tool_semantics, validate_semantic_announcement,
)


def write_floor(**overrides):
    values = dict(
        tool_name="ide__write_file", provider_kind=ProviderKind.IDE,
        provider_instance_id="instance-1", catalog_revision="revision-1",
        availability=Availability.READY,
        model_exposure=ModelExposure.CONTEXTUAL, effect=ToolEffect.FILE_WRITE,
        proof_capabilities=frozenset({Proof.FILE_WRITE}), risk_floor=Risk.WRITE,
        confirmation=Confirmation.POLICY, mission_policy=MissionPolicy.SCOPED,
        idempotency=Idempotency.OPERATION_KEY, sensitive_fields=frozenset({"content"}),
    )
    values.update(overrides)
    return ToolSemantics(**values)


def announcement(local, **overrides):
    result = {}
    for item in fields(SemanticPolicy):
        value = getattr(local, item.name)
        result[item.name] = sorted(entry.value if isinstance(entry, Proof) else entry for entry in value) \
            if isinstance(value, frozenset) else value.value
    result.update(overrides)
    return result


def test_equal_metadata_preserves_every_field_and_local_binding():
    local = write_floor()
    result = validate_semantic_announcement(local, announcement(local))
    assert result == local
    assert result.model_exposable
    assert not result.known_readonly
    assert result.proof_capabilities == frozenset({Proof.FILE_WRITE})


def test_stricter_metadata_intersection_retains_local_identity_and_availability():
    local = write_floor(availability=Availability.DEGRADED)
    result = validate_semantic_announcement(local, announcement(local,
        risk_floor="destructive", confirmation="always", model_exposure="internal",
        mission_policy="forbidden", idempotency="non_replayable",
        proof_capabilities=[], sensitive_fields=["content", "credentials"],
    ))
    assert result.tool_name == local.tool_name
    assert result.provider_kind is ProviderKind.IDE
    assert result.provider_instance_id == "instance-1"
    assert result.catalog_revision == "revision-1"
    assert result.availability is Availability.DEGRADED
    assert result.proof_capabilities == frozenset()
    assert result.sensitive_fields == frozenset({"content", "credentials"})
    assert not result.model_exposable


@pytest.mark.parametrize("field,value,error", [
    ("risk_floor", "read", "risk_floor_reduced"),
    ("confirmation", "never", "confirmation_weakened"),
    ("model_exposure", "direct", "model_exposure_expanded"),
    ("mission_policy", "allowed", "mission_policy_expanded"),
    ("idempotency", "idempotent", "idempotency_weakened"),
    ("proof_capabilities", ["file_write", "test_execution"], "proof_capabilities_expanded"),
    ("sensitive_fields", [], "sensitive_fields_removed"),
    ("effect", "FILE_SYSTEM_WRITE", "unknown_effect"),
    ("effect", "PROCESS_COMPLETION", "effect_mismatch"),
    ("risk_floor", "safe", "unknown_risk_floor"),
    ("proof_capabilities", ["invented"], "unknown_proof_capability"),
])
def test_peer_cannot_weaken_local_policy(field, value, error):
    local = write_floor()
    with pytest.raises(SemanticsError, match=error):
        validate_semantic_announcement(local, announcement(local, **{field: value}))


@pytest.mark.parametrize("risk,announced", [("system", "destructive"), ("destructive", "system")])
def test_system_and_destructive_are_not_interchangeable(risk, announced):
    local = write_floor(risk_floor=Risk(risk))
    with pytest.raises(SemanticsError, match="risk_floor_reduced_or_incompatible"):
        validate_semantic_announcement(local, announcement(local, risk_floor=announced))


@pytest.mark.parametrize("field", [item.name for item in fields(SemanticPolicy)])
def test_missing_metadata_never_falls_back_to_readonly(field):
    local = write_floor()
    raw = announcement(local)
    del raw[field]
    with pytest.raises(SemanticsError, match="semantic_fields_mismatch"):
        validate_semantic_announcement(local, raw)


@pytest.mark.parametrize("field", ["availability", "provider_instance_id", "catalog_revision",
                                    "tool_name", "provider_kind", "surprise"])
def test_wire_metadata_cannot_replace_local_liveness_identity_or_revision(field):
    local = write_floor()
    raw = announcement(local, **{field: "forged"})
    with pytest.raises(SemanticsError, match="semantic_fields_mismatch"):
        validate_semantic_announcement(local, raw)


@pytest.mark.parametrize("field,value", [
    ("effect", None), ("risk_floor", True), ("model_exposure", 1),
    ("proof_capabilities", "file_write"), ("proof_capabilities", ["file_write", "file_write"]),
    ("proof_capabilities", [["file_write"]]), ("proof_capabilities", [False]),
    ("sensitive_fields", ["content", "content"]), ("sensitive_fields", ["a\nb"]),
    ("sensitive_fields", ["content"] + [f"field_{i}" for i in range(128)]),
    ("sensitive_fields", ["content", "x" * 129]),
])
def test_malformed_metadata_is_rejected_without_echoing_values(field, value):
    local = write_floor()
    with pytest.raises(SemanticsError, match=f"invalid_{field}"):
        validate_semantic_announcement(local, announcement(local, **{field: value}))


@pytest.mark.parametrize("effect", [ToolEffect.UI_STATE_ONLY, ToolEffect.WORKSPACE_CONTEXT,
    ToolEffect.BUFFER_MUTATION, ToolEffect.SETTINGS_MUTATION,
    ToolEffect.PARAMETER_DEPENDENT, ToolEffect.UNKNOWN])
def test_non_delivery_effects_cannot_claim_disk_or_test_evidence(effect):
    with pytest.raises(SemanticsError, match="effect_cannot_supply_delivery_proof"):
        write_floor(effect=effect)


def test_readonly_effect_cannot_claim_a_file_write():
    with pytest.raises(SemanticsError, match="readonly_cannot_supply_mutation_proof"):
        write_floor(effect=ToolEffect.READ_ONLY)


@pytest.mark.parametrize("effect", [ToolEffect.UNKNOWN, ToolEffect.DESTRUCTIVE_SYSTEM])
def test_unknown_or_global_destructive_effect_cannot_be_exposed(effect):
    with pytest.raises(SemanticsError, match="unsafe_effect_exposure"):
        write_floor(effect=effect, proof_capabilities=frozenset())


@pytest.mark.parametrize("availability", list(Availability))
@pytest.mark.parametrize("exposure", list(ModelExposure))
def test_model_exposure_requires_both_local_ready_state_and_exposable_class(availability, exposure):
    local = write_floor(availability=availability, model_exposure=exposure)
    assert local.model_exposable == (availability is Availability.READY and exposure in {
        ModelExposure.CONTEXTUAL, ModelExposure.DIRECT,
    })


def test_parameter_dependent_effect_requires_runtime_resolution_before_exposure():
    local = write_floor(effect=ToolEffect.PARAMETER_DEPENDENT, proof_capabilities=frozenset())
    assert not local.model_exposable
    assert not local.known_readonly


@pytest.mark.parametrize("overrides", [
    {"risk_floor": "write"}, {"effect": "FILE_WRITE"}, {"proof_capabilities": {Proof.FILE_WRITE}},
    {"sensitive_fields": {"content"}}, {"provider_kind": "ide"}, {"availability": "ready"},
    {"tool_name": "write_file"}, {"provider_instance_id": ""}, {"catalog_revision": ""},
    {"tool_name": "ide__write_file", "provider_kind": ProviderKind.NATIVE},
    {"tool_name": "ide__write_file\n"},
])
def test_invalid_local_descriptor_cannot_be_registered(overrides):
    with pytest.raises(SemanticsError):
        write_floor(**overrides)


def test_descriptor_and_its_collections_are_immutable():
    local = write_floor()
    with pytest.raises(FrozenInstanceError):
        local.risk_floor = Risk.READ
    with pytest.raises(AttributeError):
        local.sensitive_fields.add("other")


def test_registered_descriptor_precedes_native_category_and_is_returned_by_identity():
    local = write_floor()
    result = resolve_tool_semantics(local.tool_name, registered={local.tool_name: local}, module_category="ide")
    assert result is local
    assert result.proof_capabilities != get_tool_capabilities(local.tool_name, "ide", "ide")


@pytest.mark.parametrize("invalid", [None, {}, "readonly", write_floor(tool_name="ide__another")])
def test_invalid_registered_entry_never_falls_back(invalid):
    with pytest.raises(SemanticsError, match="invalid_registered_semantics"):
        resolve_tool_semantics("ide__write_file", registered={"ide__write_file": invalid}, module_category="ide")


def test_unregistered_ide_command_is_not_resolved_through_generic_mutation_category():
    with pytest.raises(SemanticsError, match="unregistered_ide_semantics"):
        resolve_tool_semantics("ide__invented", module_category="ide")


def test_unknown_native_capability_is_not_misclassified_as_known_readonly():
    result = resolve_tool_semantics("new_unknown_tool")
    assert result == LegacyToolSemantics("new_unknown_tool", frozenset({Proof.GENERIC_READONLY}), False, False)
    assert result.effect is ToolEffect.UNKNOWN


@pytest.mark.parametrize("tool,module,semantic", [
    ("read_file", "files", "files"), ("ide_read_file", "ide", "ide"),
    ("ide_write_file", "ide", "ide"), ("mcp__test__read", "mcp", "mcp"),
    ("datagouv_download_resource", "data", "data"), ("read_own_code", "", "unknown"),
    ("run_command", "system", "platform"), ("undefined", "", "unknown"),
])
def test_legacy_native_facades_mcp_and_fallback_keep_exact_decisions(tool, module, semantic):
    from src.runtime.execution_ledger import MUTATION_TOOLS
    result = resolve_tool_semantics(tool, module_category=module, semantic_category=semantic)
    assert result.proof_capabilities == get_tool_capabilities(tool, module, semantic)
    assert result.known_readonly == tool_capabilities_are_known_readonly(tool, module, semantic)
    assert result.ledger_mutation == (tool in MUTATION_TOOLS)


def test_semantic_module_does_not_import_react_or_a_tool_provider():
    path = Path(__file__).resolve().parents[2] / "src/reasoning/tool_semantics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"__future__", "collections.abc", "dataclasses", "enum",
                                   "plan_evidence", "runtime.execution_ledger"}
        elif isinstance(node, ast.Import):
            assert [item.name for item in node.names] == ["re"]
