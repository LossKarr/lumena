from pathlib import Path

from src.llm.multi_provider import MultiProviderLLM
from src.llm.providers import get_model_config
from web.routes.config import _CONFIG_SCHEMA


def _schema_entry(key: str) -> dict:
    return next(item for item in _CONFIG_SCHEMA if item["key"] == key)


def test_default_model_is_deepseek_flash(monkeypatch):
    for key in ("LUMENA_DEFAULT_MODEL", "DEFAULT_MODEL", "LUMENA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert MultiProviderLLM._resolve_initial_model_name(None) == "deepseek-flash"
    assert _schema_entry("LUMENA_DEFAULT_MODEL")["default"] == "deepseek-flash"


def test_retired_deepseek_names_migrate_to_selectable_successors(monkeypatch):
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "deepseek-v3")
    assert MultiProviderLLM._resolve_initial_model_name(None) == "deepseek-flash"
    assert MultiProviderLLM._resolve_initial_model_name("deepseek-reasoner") == "deepseek-v4-pro"
    assert get_model_config("deepseek-flash").is_selectable()
    assert get_model_config("deepseek-v4-pro").is_selectable()


def test_legacy_autoswitch_flag_is_off_and_cannot_reactivate_reasoner(monkeypatch):
    assert _schema_entry("LUMENA_CODE_AUTOSWITCH_REASONER")["default"] == "0"
    monkeypatch.setenv("LUMENA_CODE_AUTOSWITCH_REASONER", "1")
    llm = object.__new__(MultiProviderLLM)
    should_switch, reason = llm._is_code_heavy_request(
        [{"role": "user", "content": "corrige cette API Python complexe"}],
        max_tokens=32_000,
    )
    assert should_switch is False
    assert reason is None


def test_code_paths_do_not_inject_retired_deepseek_endpoints():
    root = Path(__file__).resolve().parents[2]
    paths = (
        root / "src" / "agents" / "sub_agent.py",
        root / "src" / "reasoning" / "handlers" / "project.py",
        root / "src" / "reasoning" / "handlers" / "remotion.py",
    )
    forbidden = ('model_name="deepseek-chat"', 'model_name="deepseek-reasoner"', 'model="deepseek-reasoner"')
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{path.name} injecte encore {marker}"


def test_readme_documents_current_default_and_team_neutrality():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert "LUMENA_DEFAULT_MODEL=deepseek-flash" in readme
    assert "maintenu par une seule personne" not in readme
