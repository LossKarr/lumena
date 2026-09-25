"""External-only catalogues and strict call preparation, without MCP emulation."""
import asyncio
from contextvars import copy_context
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace

import pytest

from src.reasoning.external_tool_registry import (
    ExternalProviderSnapshot, ExternalToolCatalog, ExternalToolError,
    ExternalToolProviderRegistry, ExternalToolSpec, bind_external_catalog, current_external_catalog,
)
from src.reasoning.tool_semantics import Availability, ModelExposure, ToolEffect
from src.tools.ide_semantics import ide_contract, local_ide_semantics, resolve_ide_call


def spec(name="get_status", *, resolver=None):
    return ExternalToolSpec(
        local_ide_semantics(name, instance_id="instance", revision="revision", availability=Availability.READY),
        "Local test description", json.dumps(ide_contract(name)["input_schema"]), resolver)


def provider(name="lumena.ide", commands=None):
    snapshot = ExternalProviderSnapshot(name, "ready", tuple(commands or [spec()]))
    return SimpleNamespace(provider_id=name, capture=lambda: snapshot)


def test_catalogues_are_immutable_and_schema_exports_are_independent():
    registry = ExternalToolProviderRegistry()
    registry.register(provider())
    catalog = registry.capture()
    assert len(catalog.schemas()) == 1
    exported = catalog.schemas()
    exported[0]["function"]["parameters"]["properties"]["attack"] = {"type": "string"}
    exported.clear()
    assert catalog.schemas()[0]["function"]["parameters"]["properties"] == {}
    with pytest.raises(FrozenInstanceError):
        catalog.providers = ()
    with pytest.raises(ExternalToolError, match="external_tool_not_in_snapshot"):
        catalog.resolve("get_status")
    assert catalog.resolve("ide__get_status")[1].name == "ide__get_status"


def test_native_collision_rejects_whole_capture_not_a_partial_catalogue():
    registry = ExternalToolProviderRegistry()
    registry.register(provider(commands=[spec(), spec("get_state")]))
    with pytest.raises(ExternalToolError, match="external_native_collision"):
        registry.capture(reserved_names=frozenset({"ide__get_state"}))
    assert len(registry.capture().schemas()) == 2


def test_provider_and_cross_provider_tool_collisions_are_explicit():
    registry = ExternalToolProviderRegistry()
    registry.register(provider())
    with pytest.raises(ExternalToolError, match="external_provider_collision"):
        registry.register(provider())
    registry.register(provider("other"))
    with pytest.raises(ExternalToolError, match="external_tool_collision"):
        registry.capture()


def test_provider_cannot_change_identity_after_registration():
    registry = ExternalToolProviderRegistry()
    peer = provider()
    registry.register(peer)
    peer.capture = lambda: ExternalProviderSnapshot("other", "unavailable", ())
    with pytest.raises(ExternalToolError, match="external_provider_identity_changed"):
        registry.capture()


@pytest.mark.parametrize("state", ["unavailable", "degraded"])
def test_unavailable_provider_cannot_offer_any_tool(state):
    with pytest.raises(ExternalToolError, match="external_unavailable_tools"):
        ExternalProviderSnapshot("lumena.ide", state, (spec(),))


@pytest.mark.parametrize("exposure", [ModelExposure.NEVER, ModelExposure.INTERNAL])
def test_never_internal_are_not_exposable_even_with_local_parameter_resolver(exposure):
    entry = spec("editor_find_replace", resolver=resolve_ide_call)
    entry = replace(entry, semantics=replace(entry.semantics, model_exposure=exposure))
    assert not entry.model_candidate
    with pytest.raises(ExternalToolError, match="external_tool_not_exposed"):
        ExternalProviderSnapshot("lumena.ide", "ready", (entry,))


@pytest.mark.parametrize("availability", [Availability.DEGRADED, Availability.UNAVAILABLE])
def test_stale_or_degraded_semantics_do_not_become_model_candidates(availability):
    entry = spec("editor_find_replace", resolver=resolve_ide_call)
    assert not replace(entry, semantics=replace(entry.semantics, availability=availability)).model_candidate


def test_conditional_template_requires_explicit_local_resolver_and_resolved_call():
    plain = spec("editor_find_replace")
    assert not plain.model_candidate and not plain.semantics.model_exposable
    entry = replace(plain, call_resolver=resolve_ide_call)
    assert entry.model_candidate and not entry.semantics.model_exposable
    readonly = entry.prepare({"find": "hello"})
    mutation = entry.prepare({"find": "hello", "replace": ""})
    assert readonly.semantics.effect is ToolEffect.READ_ONLY
    assert mutation.semantics.effect is ToolEffect.BUFFER_MUTATION
    assert mutation.parameters == {"find": "hello", "replace": ""}


@pytest.mark.parametrize("parameters", [None, [], "{}", {"input": {}}, {"unknown": "secret"}])
def test_strict_parameters_have_no_wrappers_coercion_or_unknown_key_removal(parameters):
    with pytest.raises(ExternalToolError, match="external_parameters_invalid") as error:
        spec().prepare(parameters)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("parameters", [
    {"line": True}, {"line": "2"}, {"line": None}, {"line": float("nan")}, {"line": float("inf")},
])
def test_number_schema_does_not_accept_bool_strings_null_or_nonfinite(parameters):
    with pytest.raises(ExternalToolError):
        spec("editor_cursor_goto").prepare(parameters)


def test_valid_numbers_and_empty_content_are_preserved_and_caller_mutation_isolated():
    assert spec("editor_cursor_goto").prepare({"line": 2}).parameters == {"line": 2}
    args = {"path": "project/a.py", "content": ""}
    prepared = spec("write_file").prepare(args)
    args["content"] = "secret"
    prepared.parameters["content"] = "changed"
    assert prepared.parameters == {"path": "project/a.py", "content": ""}
    audit = prepared.audit_summary()
    assert audit["parameter_keys"] == ["content", "path"]
    assert "project/a.py" not in json.dumps(audit)
    assert "secret" not in repr(prepared)


@pytest.mark.parametrize("parameters", [
    {"operation": "rm", "confirmed": True},
    {"operation": "push", "confirmed": "true"},
    {"operation": "push", "confirmed": True, "args": "secret"},
])
def test_conditional_git_schema_rejects_ambiguous_operations(parameters):
    with pytest.raises(ExternalToolError):
        spec("git_sync", resolver=resolve_ide_call).prepare(parameters)


def test_resolved_git_push_keeps_confirmation_requirement_instead_of_granting_consent():
    entry = spec("git_sync", resolver=resolve_ide_call)
    call = entry.prepare({"operation": "push", "confirmed": True})
    assert call.semantics.effect is ToolEffect.DEPLOY_MUTATION
    assert call.semantics.confirmation is entry.semantics.confirmation


@pytest.mark.parametrize("corrupt", ["unresolved", "identity", "params", "risk", "mission"])
def test_buggy_local_resolver_cannot_rebind_or_rewrite_a_call(corrupt):
    def resolve(descriptor, args):
        resolved = resolve_ide_call(descriptor, args)
        if corrupt == "unresolved":
            return descriptor
        if corrupt == "identity":
            return replace(resolved, catalog_revision="other")
        if corrupt == "risk":
            from src.reasoning.tool_semantics import Risk
            return replace(resolved, risk_floor=Risk.READ)
        if corrupt == "mission":
            from src.reasoning.tool_semantics import MissionPolicy
            return replace(resolved, mission_policy=MissionPolicy.ALLOWED)
        args["replace"] = "secret"
        return resolved
    with pytest.raises(ExternalToolError):
        spec("editor_find_replace", resolver=resolve).prepare({"find": "x"})


@pytest.mark.parametrize("schema", [
    {"type": "object", "additionalProperties": True},
    {"type": "object", "additionalProperties": False, "properties": {"x": {"$ref": "https://invalid/secret"}}},
    {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "not-a-type"}}},
])
def test_schema_errors_do_not_echo_content_or_load_remote_references(schema):
    with pytest.raises(ExternalToolError) as error:
        replace(spec(), schema_json=json.dumps(schema))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("schema", ["{invalid", "\ud800", " " * 65537], ids=["json", "unicode", "oversize"])
def test_schema_serialization_errors_are_fixed_codes(schema):
    with pytest.raises(ExternalToolError, match="external_schema_invalid"):
        replace(spec(), schema_json=schema)


@pytest.mark.parametrize("value", ["\ud800", "s" * 1_000_001], ids=["unicode", "oversize"])
def test_payload_encoding_and_size_are_bounded_without_echoing_values(value):
    with pytest.raises(ExternalToolError, match="external_payload_(invalid|too_large)"):
        spec("write_file").prepare({"path": "a.txt", "content": value})


def test_run_scope_is_restored_on_error_without_changing_native_or_mcp_state():
    registry = ExternalToolProviderRegistry()
    registry.register(provider())
    before = registry.capture()
    after = ExternalToolCatalog(())
    assert current_external_catalog() is None
    with bind_external_catalog(before):
        with pytest.raises(RuntimeError):
            with bind_external_catalog(after):
                assert current_external_catalog() is after
                raise RuntimeError("test")
        assert current_external_catalog() is before
    assert current_external_catalog() is None


@pytest.mark.asyncio
async def test_concurrent_run_scopes_and_explicit_foreign_thread_copy_do_not_leak():
    registry = ExternalToolProviderRegistry()
    peer = provider()
    registry.register(peer)
    ready = registry.capture()
    peer.capture = lambda: ExternalProviderSnapshot("lumena.ide", "unavailable", ())
    absent = registry.capture()

    async def inspect(catalog):
        with bind_external_catalog(catalog):
            copied = copy_context()
            await asyncio.sleep(0)
            assert current_external_catalog() is catalog
            assert await asyncio.to_thread(copied.run, current_external_catalog) is catalog
            return catalog.schemas()

    a, b = await asyncio.gather(inspect(ready), inspect(absent))
    assert len(a) == 1 and b == []
    assert ready.schemas() == a and current_external_catalog() is None
