import importlib
import sys


def test_importing_codeagent_pure_module_does_not_load_sub_agent_first():
    sys.modules.pop("src.agents.sub_agent", None)
    importlib.import_module("src.agents.codeagent_todo")
    assert "src.agents.sub_agent" not in sys.modules


def test_importing_model_profile_does_not_load_providers_first():
    sys.modules.pop("src.llm.providers", None)
    importlib.import_module("src.llm.model_profile")
    assert "src.llm.providers" not in sys.modules

