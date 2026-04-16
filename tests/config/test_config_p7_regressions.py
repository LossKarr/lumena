from __future__ import annotations

from importlib import reload
from pathlib import Path

from scripts.sync_env_example import render_env_example
from src.llm.providers import AVAILABLE_MODELS, build_models_info


def test_sync_env_example_contains_schema_keys():
    from web.routes.config import _CONFIG_SCHEMA

    rendered = render_env_example()
    for entry in _CONFIG_SCHEMA:
        assert f"{entry['key']}=" in rendered


def test_sync_env_example_matches_file():
    rendered = render_env_example()
    current = Path(".env.example").read_text(encoding="utf-8")
    assert current == rendered


def test_setup_models_sync():
    models_info = build_models_info()
    assert set(models_info) == set(AVAILABLE_MODELS)
    for key, model in AVAILABLE_MODELS.items():
        assert models_info[key]["desc"] == model.description


def test_env_defaults_safe():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    assert schema["LUMENA_HOST"]["default"] == "0.0.0.0"
    assert schema["LUMENA_PORT"]["default"] == "8080"
    assert schema["LUMENA_SANDBOX_MEMORY"]["default"] == "512m"
    assert schema["LUMENA_SANDBOX_MODE"]["default"] in {"auto", "always", "off"}


def test_ports_all_configurable():
    ide_bridge = Path("src/tools/ide_bridge.py").read_text(encoding="utf-8")
    vite_config = Path("web/vite.config.js").read_text(encoding="utf-8")
    web_server = Path("web/server.py").read_text(encoding="utf-8")

    assert 'LUMENA_PORT' in web_server
    assert 'LUMENA_VITE_PORT' in vite_config
    assert 'LUMENA_IDE_WS_PORT' in ide_bridge


def test_cors_dynamic(monkeypatch):
    import web.server as server_mod

    monkeypatch.setenv("LUMENA_PORT", "9090")
    reloaded = reload(server_mod)
    assert "http://localhost:9090" in reloaded._ALLOWED_ORIGINS
    assert "http://127.0.0.1:9091" in reloaded._ALLOWED_ORIGINS


def test_backward_compat_old_vars(monkeypatch):
    import src.llm.multi_provider as multi_provider_mod

    monkeypatch.delenv("LUMENA_OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://legacy-host:11434")
    reload(multi_provider_mod)

    assert multi_provider_mod.MultiProviderLLM._resolve_ollama_host() == "http://legacy-host:11434"


def test_config_no_user_resources():
    from web.routes.config import _CONFIG_SCHEMA

    forbidden = ("data/memory/", "identity.json", ".lumena_rules")
    for entry in _CONFIG_SCHEMA:
        text = " ".join(str(entry.get(key, "")) for key in ("key", "label", "hint", "default"))
        lowered = text.lower().replace("\\", "/")
        for marker in forbidden:
            assert marker not in lowered


def test_instance_isolation(monkeypatch, tmp_path):
    import src.utils.paths as paths_mod

    data_a = tmp_path / "data-a"
    data_b = tmp_path / "data-b"

    monkeypatch.setenv("LUMENA_DATA_DIR", str(data_a))
    mod_a = reload(paths_mod)
    path_a = mod_a.DATA_DIR

    monkeypatch.setenv("LUMENA_DATA_DIR", str(data_b))
    mod_b = reload(paths_mod)
    path_b = mod_b.DATA_DIR

    assert path_a != path_b
    assert str(path_a).endswith("data-a")
    assert str(path_b).endswith("data-b")


def test_paths_centralized():
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        if path.name == "paths.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        normalized = text.replace(" ", "")
        if 'Path(__file__).parent.parent.parent/"data"' in normalized or 'Path(__file__).parent.parent/"data"' in normalized:
            offenders.append(str(path))
    assert offenders == []