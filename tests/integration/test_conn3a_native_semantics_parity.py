"""Compare every loaded native tool, not just a few IDE examples."""

from copy import deepcopy

from src.reasoning.plan_evidence import get_tool_capabilities, tool_capabilities_are_known_readonly
from src.reasoning.tool_categories import get_semantic_category
from src.reasoning.tool_registry import ToolRegistry
from src.reasoning.tool_semantics import LegacyToolSemantics, resolve_tool_semantics
from src.runtime.execution_ledger import MUTATION_TOOLS


def test_whole_native_registry_preserves_legacy_facts_without_rewiring(tmp_path):
    registry = ToolRegistry(lumena_root=tmp_path)
    assert not registry._failed_modules
    assert len(registry.tools) >= 500
    before = {name: (id(item["handler"]), item["description"], deepcopy(item["parameters"]))
              for name, item in registry.tools.items()}
    for name in registry.tools:
        module = registry._tool_modules.get(name, "")
        semantic = get_semantic_category(module)
        result = resolve_tool_semantics(name, module_category=module, semantic_category=semantic)
        assert isinstance(result, LegacyToolSemantics)
        assert result.proof_capabilities == get_tool_capabilities(name, module, semantic), name
        assert result.known_readonly == tool_capabilities_are_known_readonly(name, module, semantic), name
        assert result.ledger_mutation == (name in MUTATION_TOOLS), name
    after = {name: (id(item["handler"]), item["description"], item["parameters"])
             for name, item in registry.tools.items()}
    assert after == before
