"""Tests Phase I-2 — `_mcp_shell_guard.detect_mcp_shell_install` (pur).

Couvre la détection conservatrice :
  - Bloque les installs MCP via shell (npm/pip/uvx/npx/yarn/pnpm)
  - Laisse passer les installs de packages normaux (react, requests...)
  - Cas dégénérés : input vide, non-string, sans verb, sans package
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers._mcp_shell_guard import (
    MCPShellInstallDetection,
    detect_mcp_shell_install,
    list_install_verbs,
    list_mcp_package_patterns,
)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Détection positive (commande bloquée)
# ══════════════════════════════════════════════════════════════════════════════


class TestNpmInstallOfficiel:
    @pytest.mark.parametrize("cmd", [
        "npm install -g @modelcontextprotocol/server-slack",
        "npm i @modelcontextprotocol/server-memory",
        "npm install --global @modelcontextprotocol/server-filesystem",
        "sudo npm install -g @modelcontextprotocol/server-gmail",
    ])
    def test_npm_install_official_anthropic_blocked(self, cmd):
        r = detect_mcp_shell_install(cmd)
        assert r is not None
        assert r.detected_package.startswith("@modelcontextprotocol/")
        assert r.suggested_target.startswith("npm:@modelcontextprotocol/")


class TestNpxBlocked:
    @pytest.mark.parametrize("cmd", [
        "npx @modelcontextprotocol/server-puppeteer",
        "npx -y @modelcontextprotocol/server-memory",
        "npx mcp-server-time",
    ])
    def test_npx_blocked(self, cmd):
        r = detect_mcp_shell_install(cmd)
        assert r is not None


class TestPipInstallBlocked:
    @pytest.mark.parametrize("cmd,expected_pkg", [
        ("pip install mcp-server-fetch", "mcp-server-fetch"),
        ("pip3 install mcp-server-time", "mcp-server-time"),
        ("python -m pip install mcp-server-sqlite", "mcp-server-sqlite"),
    ])
    def test_pip_blocks(self, cmd, expected_pkg):
        r = detect_mcp_shell_install(cmd)
        assert r is not None
        assert r.detected_package == expected_pkg
        assert r.suggested_target == f"pypi:{expected_pkg}"


class TestUvxBlocked:
    @pytest.mark.parametrize("cmd", [
        "uvx mcp-server-time",
        "uvx mcp-server-fetch",
        "uv pip install mcp-server-sentry",
    ])
    def test_uvx_uv_pip(self, cmd):
        r = detect_mcp_shell_install(cmd)
        assert r is not None


class TestYarnPnpmBlocked:
    @pytest.mark.parametrize("cmd", [
        "yarn add @foo/mcp-stuff",
        "pnpm install mcp-something-cool",
        "pnpm add @scope/mcp-helper",
    ])
    def test_yarn_pnpm(self, cmd):
        r = detect_mcp_shell_install(cmd)
        assert r is not None


class TestCommunautairesBlocked:
    @pytest.mark.parametrize("cmd,pkg_substr", [
        ("npm install -g @tacticlaunch/mcp-linear", "@tacticlaunch/mcp-linear"),
        ("npm install -g @suekou/mcp-notion-server", "@suekou/mcp-notion-server"),
        ("pip install mcp-server-sentry", "mcp-server-sentry"),
    ])
    def test_community_packages(self, cmd, pkg_substr):
        r = detect_mcp_shell_install(cmd)
        assert r is not None
        assert pkg_substr in r.detected_package


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Faux positifs (NE doit PAS bloquer)
# ══════════════════════════════════════════════════════════════════════════════


class TestPasMCPNeBloquentPas:
    @pytest.mark.parametrize("cmd", [
        "npm install react",
        "npm install -g typescript",
        "pip install requests",
        "pip install numpy pandas",
        "yarn add lodash",
        "pnpm install vue",
        "npx create-react-app my-app",
        "uvx ruff check src/",
        "uvx black .",
    ])
    def test_dev_packages_pass(self, cmd):
        assert detect_mcp_shell_install(cmd) is None

    @pytest.mark.parametrize("cmd", [
        "npm install",
        "pip install",
        "ls -la",
        "git status",
        "echo hello",
        "python script.py",
        "rm -rf temp/",
    ])
    def test_pas_install_verb_ou_pas_pkg(self, cmd):
        assert detect_mcp_shell_install(cmd) is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Entrées dégénérées (jamais crash)
# ══════════════════════════════════════════════════════════════════════════════


class TestDegenerate:
    @pytest.mark.parametrize("bad", ["", "   ", None, 42, {}, [], object()])
    def test_invalid_returns_none(self, bad):
        assert detect_mcp_shell_install(bad) is None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Suggested target (suggestion correcte pour add_mcp)
# ══════════════════════════════════════════════════════════════════════════════


class TestSuggestedTarget:
    @pytest.mark.parametrize("cmd,expected", [
        ("npm install -g @modelcontextprotocol/server-slack",
         "npm:@modelcontextprotocol/server-slack"),
        ("npx @modelcontextprotocol/server-memory",
         "npm:@modelcontextprotocol/server-memory"),
        ("pip install mcp-server-fetch", "pypi:mcp-server-fetch"),
        ("uvx mcp-server-time", "pypi:mcp-server-time"),
        ("pnpm add @foo/mcp-tool", "npm:@foo/mcp-tool"),
        ("uv pip install mcp-server-sentry", "pypi:mcp-server-sentry"),
    ])
    def test_target(self, cmd, expected):
        r = detect_mcp_shell_install(cmd)
        assert r is not None
        assert r.suggested_target == expected


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Contrat dataclass
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectionDataclass:
    def test_is_frozen(self):
        d = MCPShellInstallDetection(
            detected_tool="npm install", detected_package="x",
            suggested_target="npm:x",
        )
        with pytest.raises(Exception):
            d.detected_tool = "modif"  # type: ignore[misc]

    def test_lists_non_empty(self):
        assert len(list_install_verbs()) >= 5
        assert len(list_mcp_package_patterns()) >= 3


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Intégration run_command_handler (bout-en-bout)
# ══════════════════════════════════════════════════════════════════════════════


class TestRunCommandHandlerIntegration:
    """Vérifie que le guard est BIEN câblé dans run_command_handler."""

    def test_handler_source_contains_guard_import(self):
        """Le handler doit importer detect_mcp_shell_install."""
        import inspect
        from src.reasoning.handlers import system as system_mod
        src = inspect.getsource(system_mod)
        assert "_mcp_shell_guard" in src
        assert "detect_mcp_shell_install" in src

    def test_handler_block_returns_user_message(self):
        """Quand le guard détecte un install MCP, le handler retourne un
        message bloquant qui mentionne add_mcp."""
        import asyncio
        from src.reasoning.handlers.context import HandlerContext
        from src.reasoning.handlers.system import run_command_handler

        ctx = HandlerContext()
        result = asyncio.run(
            run_command_handler(
                ctx,
                command="npm install -g @modelcontextprotocol/server-slack",
            )
        )
        text = result.output if hasattr(result, "output") else str(result)
        assert "Installation MCP via shell interdite" in text
        assert "add_mcp" in text
