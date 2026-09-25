"""Local floors, call-dependent meaning, and an independently packaged policy."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from src.reasoning.tool_semantics import (
    Availability, Confirmation, MissionPolicy, ModelExposure, Risk, SemanticsError,
    ToolEffect, validate_semantic_announcement,
)
from src.tools.ide_protocol import ProtocolError, canonical_catalogue, negotiate
from src.tools.ide_semantics import (
    AuditedRetrySource, audited_ide_commands, ide_contract, local_ide_semantics, resolve_ide_call,
)

ROOT = Path(__file__).resolve().parents[2]


def descriptor(name):
    return local_ide_semantics(name, instance_id="instance", revision="revision", availability=Availability.READY)


def command(name):
    data = ide_contract(name)
    schema = data["input_schema"]
    return {"id": name, "title": name, "description": "test", "category": "system", "risk": "read",
            "target": "main", "proof": "catalogue", "supported": True, **data,
            "parameters": {key: {"type": value["type"], "description": "parameter",
                                  "required": key in schema["required"]}
                           for key, value in schema["properties"].items()}}


@pytest.mark.parametrize("name", audited_ide_commands())
def test_every_audited_command_has_a_valid_local_floor_and_canonical_schema(name):
    entry = command(name)
    Draft202012Validator.check_schema(entry["input_schema"])
    local = descriptor(name)
    assert validate_semantic_announcement(local, entry["semantics"]) == local
    assert json.loads(canonical_catalogue([entry])) == [entry]
    changed = deepcopy(entry)
    changed["semantics"]["effect"] = "UNKNOWN" if local.effect != ToolEffect.UNKNOWN else "READ_ONLY"
    with pytest.raises(ProtocolError, match="catalogue_policy_invalid"):
        canonical_catalogue([changed])


def test_local_policy_is_independent_of_peer_and_shipped_in_both_repositories():
    # CONN-5C-2 (15/09/2026) : +1 commande auditee, `command_run` (commande validee de mission).
    assert len(audited_ide_commands()) == 135
    data = json.loads((ROOT / "src/tools/ide_tool_policy.json").read_text(encoding="utf-8"))
    ide = ROOT / "ide/electron/ideToolPolicy.json"
    if ide.exists():
        assert json.loads(ide.read_text(encoding="utf-8")) == data
    first = ide_contract("write_file")
    first["semantics"]["effect"] = "READ_ONLY"
    first["input_schema"]["properties"].clear()
    assert ide_contract("write_file")["semantics"]["effect"] == "FILE_WRITE"
    assert "content" in ide_contract("write_file")["input_schema"]["properties"]
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "ide_tool_policy.json" in project["tool"]["setuptools"]["package-data"]["src.tools"]


@pytest.mark.parametrize("field,value", [
    ("risk_floor", "read"), ("model_exposure", "direct"), ("confirmation", "never"),
    ("proof_capabilities", ["file_write", "test_execution"]), ("sensitive_fields", []),
    ("mission_policy", "allowed"), ("idempotency", "idempotent"),
    ("provider_kind", "native"), ("availability", "ready"),
])
def test_peer_cannot_lower_policy_or_supply_local_identity(field, value):
    entry = command("write_file")
    entry["semantics"][field] = value
    with pytest.raises(ProtocolError, match="catalogue_policy_invalid"):
        canonical_catalogue([entry])


def test_restrictive_peer_metadata_is_preserved_not_replaced_by_local_defaults():
    entry = command("write_file")
    entry["semantics"].update(model_exposure="never", mission_policy="forbidden", confirmation="always",
                              proof_capabilities=[], sensitive_fields=["content", "path"])
    assert json.loads(canonical_catalogue([entry]))[0]["semantics"] == entry["semantics"]


@pytest.mark.parametrize("change", [
    lambda c: c.pop("semantics"), lambda c: c.pop("input_schema"),
    lambda c: c.update(id="unreviewed_plugin"),
    lambda c: c["input_schema"].update(additionalProperties=True),
    lambda c: c["input_schema"].update(required=[]),
    lambda c: c["input_schema"]["properties"]["path"].update(type="object"),
    lambda c: c["input_schema"].update(**{"$ref": "https://untrusted.invalid/schema"}),
    lambda c: c["parameters"]["path"].update(required=False),
])
def test_missing_metadata_unknown_commands_and_drifted_schemas_are_rejected(change):
    entry = command("write_file")
    change(entry)
    with pytest.raises(ProtocolError):
        canonical_catalogue([entry])


@pytest.mark.parametrize("name,effect,proofs", [
    ("task_run", "PROCESS_LAUNCH", ["process_launch"]),
    ("editor_rename_symbol", "UI_STATE_ONLY", []),
    ("git_worktree_open", "WORKSPACE_CONTEXT", []),
    ("sidebar_rename", "FILESYSTEM_DESTRUCTIVE", ["generic_mutation"]),
    ("operation_cancel", "PROCESS_CONTROL", []),
    ("editor_insert", "BUFFER_MUTATION", []),
])
def test_audit_corrections_describe_actual_native_action_not_a_hoped_for_result(name, effect, proofs):
    policy = ide_contract(name)["semantics"]
    assert policy["effect"] == effect
    assert policy["proof_capabilities"] == proofs
    if name == "sidebar_rename":
        assert policy["risk_floor"] == "destructive" and policy["confirmation"] == "always"


@pytest.mark.parametrize("params,effect", [
    ({"find": "hello"}, ToolEffect.READ_ONLY),
    ({"find": "hello", "replace": ""}, ToolEffect.BUFFER_MUTATION),
    ({"find": "hello", "replace": "new", "all": False}, ToolEffect.BUFFER_MUTATION),
])
def test_find_replace_presence_not_truthiness_decides_effect(params, effect):
    before = descriptor("editor_find_replace")
    assert not before.model_exposable
    after = resolve_ide_call(before, params)
    assert after.effect is effect
    assert before.effect is ToolEffect.PARAMETER_DEPENDENT
    assert after.provider_instance_id == before.provider_instance_id
    if effect is ToolEffect.BUFFER_MUTATION:
        assert not after.proof_capabilities


@pytest.mark.parametrize("operation,effect", [("fetch", "GIT_LOCAL_MUTATION"), ("pull", "GIT_LOCAL_MUTATION"),
                                             ("push", "DEPLOY_MUTATION")])
def test_git_fetch_is_not_readonly_and_push_is_publication(operation, effect):
    after = resolve_ide_call(descriptor("git_sync"), {"operation": operation, "confirmed": True})
    assert after.effect.value == effect
    assert after.risk_floor is Risk.SYSTEM and after.confirmation is Confirmation.ALWAYS


def test_terminal_ack_never_has_process_or_test_completion_capability():
    for text in ("pytest", "echo hello", "rm -rf unsafe", "start child"):
        after = resolve_ide_call(descriptor("terminal_run"), {"command": text})
        assert after.effect is ToolEffect.PROCESS_CONTROL and not after.proof_capabilities


@pytest.mark.parametrize("name,params", [
    ("editor_find_replace", {"find": "a", "replace": None}), ("editor_find_replace", {"find": "a", "extra": True}),
    ("git_sync", {"operation": "delete", "confirmed": True}), ("git_sync", {"operation": "push"}),
    ("terminal_run", {"command": 3}), ("operation_retry", {"operationId": "op", "parameters": {}, "confirmed": True}),
])
def test_ambiguous_or_unproven_calls_fail_closed(name, params):
    with pytest.raises(SemanticsError):
        resolve_ide_call(descriptor(name), params)


def source(action="task_run"):
    return AuditedRetrySource("op", "instance", "revision", "workspace", action, True)


@pytest.mark.parametrize("action,key,effect", [("task_run", "taskId", "PROCESS_LAUNCH"), ("test_run", "itemId", "TEST_EXECUTION")])
def test_retry_resolves_from_local_bound_journal_not_model_supplied_action(action, key, effect):
    params = {"operationId": "op", "parameters": {key: "task"}, "confirmed": True}
    result = resolve_ide_call(descriptor("operation_retry"), params, workspace_id="workspace", retry_source=source(action))
    assert result.effect.value == effect and not result.model_exposable
    for field, value in [("operation_id", "other"), ("instance_id", "other"), ("catalog_revision", "other"),
                         ("workspace_id", "other"), ("retryable", False), ("action", "terminal_run")]:
        with pytest.raises(SemanticsError):
            resolve_ide_call(descriptor("operation_retry"), params, workspace_id="workspace", retry_source=replace(source(action), **{field: value}))
    with pytest.raises(SemanticsError):
        resolve_ide_call(descriptor("operation_retry"), {**params, "action": action}, workspace_id="workspace", retry_source=source(action))


def test_resolution_does_not_relax_peer_restrictions():
    original = descriptor("git_sync")
    restricted = replace(original, model_exposure=ModelExposure.NEVER, mission_policy=MissionPolicy.FORBIDDEN)
    result = resolve_ide_call(restricted, {"operation": "push", "confirmed": True})
    assert not result.model_exposable and result.mission_policy is MissionPolicy.FORBIDDEN


def test_schema_one_is_incompatible_even_with_valid_new_metadata():
    from tests.tools.test_conn2b_ide_protocol import hello, SESSION
    message = hello([command("get_status")])
    message["catalogue"]["schema"] = 1
    with pytest.raises(ProtocolError, match="catalogue_incompatible"):
        negotiate(message, SESSION)


def test_fresh_root_import_and_negotiation_have_no_cycle_or_peer_dependency(tmp_path):
    script = """
import sys
from src.tools.ide_protocol import canonical_catalogue
assert 'src.reasoning.react' not in sys.modules
from src.tools.ide_semantics import ide_contract
c = {'id':'get_status', 'title':'status', 'description':'', 'category':'system',
     'risk':'read', 'target':'main', 'proof':'catalogue', 'supported':True, 'parameters':{},
     **ide_contract('get_status')}
canonical_catalogue([c])
print('fresh negotiation valid')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fresh negotiation valid"
