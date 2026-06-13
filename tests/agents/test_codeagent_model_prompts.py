from src.llm.model_profile import get_model_profile
from src.prompts.agents.sub_agent_prompts import _load_provider_prompt


def test_ollama_family_uses_unstable_profile():
    profile = get_model_profile("ollama/llama3.1:8b")
    assert profile.parser_severity == "forgiving"
    assert profile.sub_agent_iter_cap > 0


def test_qwen_coder_profile_is_less_capped_than_tiny_locals():
    profile = get_model_profile("qwen2.5-coder:32b")
    assert profile.tool_call_quality == "moderate"
    assert profile.sub_agent_iter_cap >= 20


def test_local_prompt_loader_for_llama(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_PROMPTS", "true")
    import importlib
    import src.config.codeagent_flags as flags
    importlib.reload(flags)
    prompt = _load_provider_prompt("llama3.1:8b")
    assert "modele local/Ollama" in prompt
    importlib.reload(flags)


def test_moonshot_prompt_loader_for_real_kimi(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_PROMPTS", "true")
    import importlib
    import src.config.codeagent_flags as flags
    importlib.reload(flags)
    prompt = _load_provider_prompt("moonshotai/kimi-k2.6")
    assert "Kimi/Moonshot" in prompt
    importlib.reload(flags)


def test_nvidia_kimi_uses_nvidia_prompt(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_PROMPTS", "true")
    import importlib
    import src.config.codeagent_flags as flags
    importlib.reload(flags)
    prompt = _load_provider_prompt("nvidia-kimi-k2.6")
    assert "NVIDIA NIM" in prompt
    importlib.reload(flags)
