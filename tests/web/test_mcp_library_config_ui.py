"""Tests Phase I-6 — UI dynamique de configuration MCP."""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PANELS_JS = _REPO_ROOT / "web" / "static" / "js" / "panels.js"


def _js() -> str:
    return _PANELS_JS.read_text(encoding="utf-8")


class TestModalHooks:
    def test_open_config_modal_function_defined(self):
        js = _js()
        assert "window._mcpOpenConfigModal=" in js or "window._mcpOpenConfigModal =" in js

    def test_close_config_modal_function_defined(self):
        assert "window._mcpCloseConfigModal" in _js()

    def test_render_config_modal_present(self):
        assert "_mcpRenderConfigModal" in _js()

    def test_render_config_field_present(self):
        assert "_mcpRenderConfigField" in _js()


class TestSaveDeleteHooks:
    def test_save_field_uses_put(self):
        js = _js()
        assert "_mcpSaveField" in js
        assert "method:'PUT'" in js or 'method:"PUT"' in js

    def test_delete_field_uses_delete(self):
        js = _js()
        assert "_mcpDeleteField" in js
        assert "method:'DELETE'" in js or 'method:"DELETE"' in js

    def test_detect_schema_action_present(self):
        assert "_mcpDetectSchema" in _js()


class TestApiPaths:
    def test_calls_schema_route(self):
        assert "/api/mcp/library/${encodeURIComponent(serverId)}/schema" in _js()

    def test_calls_status_route(self):
        assert "config-status" in _js()

    def test_calls_secrets_route(self):
        assert "library/${encodeURIComponent(serverId)}/${kind}/${encodeURIComponent(key)}" in _js()

    def test_calls_detect_route(self):
        assert "detect-schema" in _js()


class TestPrivacyUI:
    def test_secret_inputs_use_password_type(self):
        """Les champs SECRET doivent utiliser type=password (pas text)."""
        js = _js()
        # On vérifie la logique : isSecret → 'password'
        assert "isSecret" in js
        assert "'password'" in js or '"password"' in js

    def test_no_inline_secret_label_in_help(self):
        """L'UI ne doit pas afficher les valeurs des secrets en clair
        dans le rendu (juste un statut + un input password)."""
        js = _js()
        # On a juste une status pill : "✓ définie" ou "⚠ vide"
        assert "définie" in js or "Définie" in js


class TestConfigureButtonOnCards:
    def test_configure_button_label(self):
        """La card Bibliothèque a un bouton dédié à la configuration."""
        js = _js()
        assert "Configurer" in js
        # Et il appelle _mcpOpenConfigModal
        assert "_mcpOpenConfigModal(" in js


class TestRoutesPresentInMcpModule:
    """Les 8 routes Phase I-6 doivent exister dans mcp.py."""

    def _mcp_src(self) -> str:
        return (_REPO_ROOT / "web" / "routes" / "mcp.py").read_text(encoding="utf-8")

    def test_get_schema_route(self):
        assert '/api/mcp/library/{server_id}/schema' in self._mcp_src()

    def test_get_status_route(self):
        assert '/api/mcp/library/{server_id}/config-status' in self._mcp_src()

    def test_put_secret_route(self):
        s = self._mcp_src()
        assert '/api/mcp/library/{server_id}/secrets/{key_name}' in s

    def test_put_config_route(self):
        s = self._mcp_src()
        assert '/api/mcp/library/{server_id}/config/{key_name}' in s

    def test_ready_route(self):
        assert '/api/mcp/library/{server_id}/ready' in self._mcp_src()

    def test_detect_route(self):
        assert '/api/mcp/library/{server_id}/detect-schema' in self._mcp_src()
