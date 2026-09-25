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


def test_catalog_provider_wins_over_ambiguous_model_name(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_PROMPTS", "true")
    prompt = _load_provider_prompt("gemma-4-31b-it")
    assert "GEMINI" in prompt
    assert "local/Ollama" not in prompt


def test_current_provider_adapters_are_routed_from_catalog(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_PROMPTS", "true")
    assert "OPENAI WORKFLOW" in _load_provider_prompt("gpt-6-sol")
    assert "XAI" in _load_provider_prompt("grok-4.7")
    assert "DEEPSEEK V4" in _load_provider_prompt("deepseek-flash")


def test_current_models_have_exact_stable_profiles():
    for name in ("gpt-6-sol", "gpt-6-luna", "claude-opus-5.5", "grok-4.7"):
        profile = get_model_profile(name)
        assert profile.parser_severity == "strict"
        assert profile.tool_call_quality == "excellent"
        assert profile.sub_agent_stability == "stable"


def test_mistral_and_local_sizes_do_not_inherit_unrelated_profiles():
    assert get_model_profile("mistral-large").tool_call_quality == "excellent"
    small = get_model_profile("qwen-custom:3b")
    large = get_model_profile("qwen-custom:32b")
    assert small.sub_agent_iter_cap < large.sub_agent_iter_cap
    assert small.tool_call_quality == "poor"
    assert large.tool_call_quality == "moderate"


def test_every_selectable_catalog_model_gets_its_provider_adapter():
    from src.llm.providers import AVAILABLE_MODELS

    markers = {
        "ollama": "local/Ollama",
        "openai": "OPENAI WORKFLOW",
        "anthropic": "professionnel",
        "google": "GEMINI",
        "moonshot": "Kimi/Moonshot",
        "deepseek": "DEEPSEEK V4",
        "xai": "XAI",
        "nvidia": "NVIDIA NIM",
        "minimax": "MiniMax",
        "zai": "Z.AI",
        "mistral": "MISTRAL",
    }
    for name, config in AVAILABLE_MODELS.items():
        if not config.is_selectable():
            continue
        prompt = _load_provider_prompt(name)
        assert markers[config.provider.value].lower() in prompt.lower(), name
        assert "CONFIANCE ABSOLUE" not in prompt, name
        assert "solution doit être PARFAITE" not in prompt, name
