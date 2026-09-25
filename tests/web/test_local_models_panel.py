from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def test_local_models_panel_is_wired_without_inline_catalogue_logic():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    main = (ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    navigation = (ROOT / "web" / "static" / "js" / "navigation.js").read_text(encoding="utf-8")
    assert 'data-panel="local-models"' in html
    assert 'id="panel-local-models"' in html
    assert "/static/css/local-models.css?v=3" in html
    assert "./local-models.js?v=3" in main
    assert "case'local-models'" in navigation


def test_local_models_panel_has_distinct_catalogue_library_and_operations_views():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-lm-view="discover"' in html
    assert 'data-lm-view="installed"' in html
    assert 'data-lm-view="jobs"' in html
    assert 'id="local-model-grid"' in html
    assert 'id="local-model-jobs"' in html
    assert 'id="local-model-delete-dialog"' in html


def test_local_models_ui_uses_authenticated_api_and_escapes_remote_metadata():
    script = (ROOT / "web" / "static" / "js" / "local-models.js").read_text(encoding="utf-8")
    assert "headers.Authorization" in script
    assert "safe(model.display_name)" in script
    assert "safe(model.description" in script
    assert "/api/local-models" in script
    assert "innerHTML=model.description" not in script
    assert "renderStatus(_health)" in script


def test_env_example_generation_ignores_machine_specific_ollama_models(monkeypatch):
    from scripts.sync_env_example import render_env_example
    from src.llm import providers
    from web.routes.config import _CONFIG_SCHEMA

    runtime_name = "runtime-only-test-model"
    monkeypatch.setitem(
        providers.AVAILABLE_MODELS,
        runtime_name,
        SimpleNamespace(display_name="runtime-only:test (Local Ollama)"),
    )
    entry = next(item for item in _CONFIG_SCHEMA if item["key"] == "LUMENA_DEFAULT_MODEL")
    entry["options"].append(runtime_name)
    try:
        assert runtime_name not in render_env_example()
    finally:
        entry["options"].remove(runtime_name)
