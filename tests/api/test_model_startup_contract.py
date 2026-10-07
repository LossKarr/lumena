"""Regression coverage for first-run model activation."""

from __future__ import annotations

import asyncio

import pytest


class _ReadOnlyLocalLLM:
    """Local client whose model identifier cannot be assigned."""

    provider = None

    def __init__(self, provider) -> None:
        self.provider = provider

    @property
    def model(self) -> str:
        return "qwen3:8b"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["llava:latest"]


def test_core_stays_uninitialized_when_selected_ollama_model_is_absent(tmp_path, monkeypatch) -> None:
    """A responsive Ollama daemon is insufficient when the selected tag is absent."""
    import src.core as core_module
    from src.core import LumenaCore
    from src.llm.providers import ProviderType

    core = LumenaCore.__new__(LumenaCore)
    core.llm = _ReadOnlyLocalLLM(ProviderType.OLLAMA)
    core.data_dir = tmp_path
    core.agent_final_repair_enabled = True
    core.memory_auto_migrate = False

    monkeypatch.setattr(core_module, "CONFIG_VALIDATOR_AVAILABLE", False)
    monkeypatch.setattr(core_module, "GRACEFUL_DEGRADATION_AVAILABLE", False)

    assert asyncio.run(core.initialize()) is False


def test_models_catalog_does_not_advertise_uninstalled_local_model(monkeypatch) -> None:
    from web.routes import deps
    from web.routes import models as models_route

    monkeypatch.setattr(deps, "lumena", None)
    monkeypatch.setattr(models_route, "_local_model_enabled", lambda _config: False)

    payload = asyncio.run(models_route.get_models())
    qwen = next(model for model in payload["models"] if model["name"] == "qwen3-8b")

    assert qwen["available"] is False


def test_switch_without_web_runtime_recovers_core_singleton(monkeypatch) -> None:
    from web.routes import deps
    from web.routes import models as models_route

    class _CandidateLLM:
        model_name = "deepseek-flash"

        def __init__(self, model_name: str | None = None) -> None:
            if model_name:
                self.model_name = model_name

        def switch_model(self, model_name: str) -> bool:
            self.model_name = model_name
            return True

    class _Core:
        def __init__(self) -> None:
            self.llm = _CandidateLLM()
            self.is_initialized = False

        async def initialize(self) -> bool:
            self.is_initialized = True
            return True

    core = _Core()
    old_core = deps.lumena
    old_setup_only = deps.setup_only_mode
    try:
        monkeypatch.setattr(deps, "lumena", None)
        monkeypatch.setattr(deps, "setup_only_mode", True)
        monkeypatch.setattr("src.core.get_lumena", lambda: core)
        monkeypatch.setattr("src.llm.multi_provider.MultiProviderLLM", _CandidateLLM)

        from src.llm.providers import get_model_config
        config = get_model_config("deepseek-flash")
        assert config is not None
        asyncio.run(models_route._activate_runtime_model("deepseek-flash", config))

        assert deps.lumena is core
        assert core.is_initialized is True
        assert deps.setup_only_mode is False
    finally:
        deps.lumena = old_core
        deps.setup_only_mode = old_setup_only


def test_local_switch_requires_the_exact_installed_tag() -> None:
    from fastapi import HTTPException
    from src.local_models.contracts import InstalledModel
    from src.local_models.identifiers import parse_model_reference
    from src.llm.providers import get_model_config
    from web.routes.models import _ensure_local_model_live

    config = get_model_config("qwen3-8b")
    assert config is not None
    class _OllamaProbe:
        async def health(self):
            return {"available": True}

        async def list_installed(self):
            return [InstalledModel(parse_model_reference("llava:latest"), "digest", 10, None)]

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_ensure_local_model_live(None, config, ollama_client=_OllamaProbe()))

    assert exc_info.value.status_code == 409
    assert "n'est pas installé" in str(exc_info.value.detail)


def test_local_switch_probes_ollama_instead_of_the_active_cloud_provider() -> None:
    from src.local_models.contracts import InstalledModel
    from src.local_models.identifiers import parse_model_reference
    from src.llm.providers import get_model_config
    from web.routes.models import _ensure_local_model_live

    config = get_model_config("qwen3-8b")
    assert config is not None

    class _CloudRuntime:
        async def is_available(self):
            raise AssertionError("the active cloud provider must not be probed")

        async def list_models(self):
            raise AssertionError("the active cloud catalogue must not be read")

    class _OllamaProbe:
        async def health(self):
            return {"available": True}

        async def list_installed(self):
            return [InstalledModel(parse_model_reference("qwen3:8b"), "digest", 10, None)]

    asyncio.run(
        _ensure_local_model_live(
            _CloudRuntime(),
            config,
            ollama_client=_OllamaProbe(),
        )
    )


def test_startup_frontend_handles_non_json_server_errors() -> None:
    from pathlib import Path

    startup = (
        Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "startup.js"
    ).read_text(encoding="utf-8")

    assert "_apiErrorMessage" in startup
    assert "content-type" in startup
    assert "Internal server error" not in startup
    assert "if(!cur||cur.name!==selectedModel)" not in startup
    assert startup.count("await _apiErrorMessage(r") >= 2


def test_setup_frontend_never_enters_chat_with_an_unready_core() -> None:
    from pathlib import Path

    setup = (
        Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "setup.js"
    ).read_text(encoding="utf-8")

    assert "if (data.llm_ready === false)" in setup
    assert "Réessayer le démarrage" in setup
    assert "llm-warn-continue" not in setup
    assert "Continuer quand même" not in setup
    assert "m.startsWith('glm-')" in setup
    assert "MISTRAL_API_KEY" in setup


def test_reselecting_current_model_initializes_setup_only_runtime(monkeypatch) -> None:
    from src.llm.providers import get_model_config
    from web.routes import deps
    from web.routes import models as models_route

    class _CurrentLLM:
        model_name = "qwen3-8b"

    class _Core:
        llm = _CurrentLLM()
        is_initialized = False

    activated: list[str] = []

    async def _activate(model_name, config) -> None:
        activated.append(model_name)
        assert config is get_model_config(model_name)

    monkeypatch.setattr(deps, "lumena", _Core())
    monkeypatch.setattr(models_route, "_activate_runtime_model", _activate)

    response = asyncio.run(
        models_route.switch_model(models_route.ModelSwitchRequest(model_name="qwen3-8b"))
    )

    assert response["success"] is True
    assert activated == ["qwen3-8b"]

