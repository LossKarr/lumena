from __future__ import annotations

from pathlib import Path

from web.routes.config import _CONFIG_SCHEMA


ROOT = Path(__file__).resolve().parents[2]


def test_codex_preferences_are_non_secret_and_safe_by_default():
    entries = {
        item["key"]: item
        for item in _CONFIG_SCHEMA
        if item["key"].startswith("LUMENA_CODEX_")
        or item["key"] == "LUMENA_OPENAI_ACCESS_MODE"
    }
    assert set(entries) == {
        "LUMENA_OPENAI_ACCESS_MODE",
        "LUMENA_CODEX_CLI_PATH",
        "LUMENA_CODEX_DEFAULT_MODEL",
        "LUMENA_CODEX_SURFACES",
        "LUMENA_CODEX_API_FALLBACK",
        "LUMENA_CODEX_API_RESCUE",
    }
    assert entries["LUMENA_OPENAI_ACCESS_MODE"]["default"] == "api"
    assert entries["LUMENA_CODEX_API_FALLBACK"]["default"] == "never"
    assert entries["LUMENA_CODEX_API_RESCUE"]["default"] == "1"
    assert entries["LUMENA_CODEX_SURFACES"]["options"] == [
        "codeagent", "codeagent,chat", "codeagent,agent", "codeagent,missions",
        "codeagent,chat,agent", "codeagent,chat,missions",
        "codeagent,agent,missions", "codeagent,chat,agent,missions",
    ]
    assert all(item["type"] != "secret" for item in entries.values())
    assert all(item["group"] == "Acces OpenAI" for item in entries.values())


def test_configuration_mounts_dedicated_codex_card():
    panels = (ROOT / "web/static/js/panels.js").read_text(encoding="utf-8")
    assert "mountCodexSubscriptionCard" in panels
    assert "{name:'Acces OpenAI'" in panels
    assert "name==='Acces OpenAI'" in panels


def test_codex_card_is_dynamic_and_never_requests_api_credentials():
    script = (ROOT / "web/static/js/codex-subscription.js").read_text(
        encoding="utf-8"
    )
    assert "/account/status" in script
    assert "/login/start" in script
    assert "/models" in script
    assert "data-cfg=\"LUMENA_CODEX_DEFAULT_MODEL\"" in script
    assert "data-codex-surface=\"chat\"" in script
    assert "data-codex-surface=\"agent\"" in script
    assert "data-codex-surface=\"missions\"" in script
    assert "['codeagent','chat','agent','missions']" in script
    assert "/collaboration/threads" in script
    assert "data-codex-share-mode" in script
    assert "_syncEnhancedSelect(select)" in script
    assert "Confier une revue" in script
    assert "approve_memory:false" in script
    assert "Aucun fallback API payant implicite" in script
    assert "Secours API vers abonnement Codex" in script
    assert 'data-cfg="LUMENA_CODEX_API_RESCUE"' in script
    assert "CodeAgent" in script
    assert "<strong>Agent</strong>" in script
    assert "<strong>Missions</strong>" in script
    lowered = script.lower()
    assert "openai_api_key" not in lowered
    assert "auth.json" not in lowered
    assert "available_models" not in lowered


def test_codex_card_assets_are_loaded_and_responsive():
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/static/css/codex-subscription.css").read_text(
        encoding="utf-8"
    )
    assert "/static/css/codex-subscription.css?v=2" in index
    assert "/static/js/main.js?v=52" in index
    assert "@media(max-width:820px)" in css
    assert "@media(max-width:560px)" in css
    assert ".codex-access-segment" in css
    assert ".codex-thread-list" in css


def test_global_picker_keeps_api_catalog_and_adds_namespaced_codex_space():
    startup = (ROOT / "web/static/js/startup.js").read_text(encoding="utf-8")
    main = (ROOT / "web/static/js/main.js").read_text(encoding="utf-8")
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/static/css/components.css").read_text(encoding="utf-8")

    assert "fetch(`${API_BASE}/api/models`" in startup
    assert "/api/codex-subscription/models" in startup
    assert "`codex:${model.model_id}`" in startup
    assert "provider:'codex'" in startup
    assert "Abonnement ChatGPT" in startup
    assert "switchCatalogModel" in startup
    assert "/api/codex-subscription/model/select" in startup
    assert "/api/model/switch" in startup
    assert "switchCatalogModel" in main
    assert "setModelSource" in main
    assert "./startup.js?v=2" in main
    assert 'id="model-picker-source"' in index
    assert 'data-source="api"' in index
    assert 'data-source="codex"' in index
    assert ".mpicker-source-switch" in css


def test_global_picker_source_control_switches_the_engine_not_only_the_filter():
    startup = (ROOT / "web/static/js/startup.js").read_text(encoding="utf-8")
    source_switch = startup.split(
        "export async function setModelSource", 1
    )[1].split("export function filterModelSearch", 1)[0]

    assert "_preferredSelectionForSource(nextSource)" in source_switch
    assert "await _selectCatalogModel(" in source_switch
    assert "closePicker:false" in source_switch
    assert "announce:false" in source_switch
    assert "_mpAccessMode===expectedMode" in source_switch
    assert "_mpSourceSwitching" in source_switch


def test_global_picker_codex_failure_is_best_effort_and_api_remains_primary():
    startup = (ROOT / "web/static/js/startup.js").read_text(encoding="utf-8")
    codex_loader = startup.split(
        "async function _loadCodexPickerModels", 1
    )[1].split("export async function loadModels", 1)[0]
    api_loader = startup.split("export async function loadModels", 1)[1]

    assert "if(!response.ok)return apiModels" in codex_loader
    assert "return apiModels" in codex_loader
    assert "const apiModels=Array.isArray(data.models)?data.models:[]" in api_loader
    assert "allModels=await _loadCodexPickerModels(h,apiModels)" in api_loader
