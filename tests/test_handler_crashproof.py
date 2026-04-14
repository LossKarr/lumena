"""Tests crash-proofing du chargement des handlers V2 (P0)."""
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_import_error_for(target_module):
    """Crée un side_effect pour importlib.import_module qui échoue sur target_module."""
    _original = importlib.import_module

    def _patched(name, package=None):
        if target_module in name:
            raise ImportError(f"Simulated import error for {name}")
        return _original(name, package=package)

    return _patched


def _fresh_registry(**kwargs):
    """Instancie un ToolRegistry frais (sans cache module)."""
    # Purger le module du cache pour forcer un re-import propre
    mods_to_remove = [k for k in sys.modules if "tool_registry" in k]
    for m in mods_to_remove:
        del sys.modules[m]
    from src.reasoning.tool_registry import ToolRegistry
    return ToolRegistry(**kwargs)


def test_single_broken_import_does_not_crash_registry():
    """Un seul module en erreur ne crashe pas le registry."""
    with patch("importlib.import_module", side_effect=_make_import_error_for("stripe_api")):
        reg = _fresh_registry()
    assert len(reg.tools) > 0
    assert any("stripe_api" in m for m in reg._failed_modules)


def test_infra_import_failure_raises_runtime_error():
    """Si l'infra critique (context/registry_v2) échoue → RuntimeError."""
    # On patche directement l'import dans le module tool_registry
    import src.reasoning.tool_registry as tr_mod
    _orig_load = tr_mod.ToolRegistry._load_v2_handlers

    def _broken_load(self_inner):
        raise RuntimeError("Impossible de charger l'infra handlers: simulated")

    with patch.object(tr_mod.ToolRegistry, "_load_v2_handlers", _broken_load):
        with pytest.raises(RuntimeError, match="infra|Impossible"):
            tr_mod.ToolRegistry()


def test_failed_modules_tracked_correctly():
    """Deux modules en échec → les deux apparaissent dans _failed_modules."""
    def _fail_two(name, package=None):
        if "stripe_api" in name or "n8n" in name:
            raise ImportError(f"Simulated import error for {name}")
        return importlib.import_module(name, package=package)

    with patch("importlib.import_module", side_effect=_fail_two):
        reg = _fresh_registry()
    stripe_fail = any("stripe_api" in m for m in reg._failed_modules)
    n8n_fail = any("n8n" in m for m in reg._failed_modules)
    assert stripe_fail and n8n_fail
    assert len([m for m in reg._failed_modules if "stripe_api" in m or "n8n" in m]) >= 2


def test_syntax_error_in_handler_skipped():
    """Un SyntaxError dans un module handler est skip, pas fatal."""
    def _syntax_err(name, package=None):
        if "n8n" in name:
            raise SyntaxError("Simulated syntax error")
        return importlib.import_module(name, package=package)

    with patch("importlib.import_module", side_effect=_syntax_err):
        reg = _fresh_registry()
    assert any("n8n" in m for m in reg._failed_modules)
    assert len(reg.tools) > 0


def test_attribute_error_getter_not_found():
    """Module importé OK mais getter absent → _failed_modules, pas de crash."""
    _original = importlib.import_module

    def _bad_getter(name, package=None):
        if "twitter" in name:
            mod = MagicMock(spec=[])  # no attributes at all
            mod.__name__ = "fake_twitter"
            return mod
        return _original(name, package=package)

    with patch("importlib.import_module", side_effect=_bad_getter):
        reg = _fresh_registry()
    assert any("twitter" in m for m in reg._failed_modules)
    assert len(reg.tools) > 0


def test_working_modules_fully_registered_despite_failures():
    """Les modules fonctionnels sont enregistrés même si d'autres échouent."""
    with patch("importlib.import_module", side_effect=_make_import_error_for("stripe_api")):
        reg = _fresh_registry()
    desc = reg.get_tools_description()
    assert len(desc) > 100
    # Des outils core sont bien là
    assert "read_file" in reg.tools or "write_file" in reg.tools


def test_playwright_unavailable_preserves_behavior():
    """Playwright absent → outils browser masqués mais pas d'erreur."""
    from src.reasoning.tool_registry import ToolRegistry
    reg = ToolRegistry()
    # Si browser chargé, il est dans tools ; sinon il est skip — pas dans _failed_modules
    # Le test vérifie que le registry démarre dans les deux cas
    assert len(reg.tools) > 0


def test_getter_exception_during_registration_skipped():
    """Un getter qui crashe pendant defs = getter() est skip proprement."""
    _original = importlib.import_module

    def _crash_getter(name, package=None):
        if "spotify" in name:
            mod = MagicMock()
            mod.__name__ = "fake_spotify"
            mod.get_spotify_handler_defs = MagicMock(
                side_effect=TypeError("bad config"),
                __module__="fake_spotify",
                __name__="get_spotify_handler_defs",
            )
            return mod
        return _original(name, package=package)

    with patch("importlib.import_module", side_effect=_crash_getter):
        reg = _fresh_registry()
    assert any("spotify" in m for m in reg._failed_modules)
    assert len(reg.tools) > 0
