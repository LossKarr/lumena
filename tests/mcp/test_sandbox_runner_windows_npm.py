"""Phase I-7 — Verrouille _resolve_cli_binary pour Windows npm.

Bug d'origine : sur Windows, `subprocess.run(["npm", "install", ...])`
sans `shell=True` cherche `npm.exe` et lève `FileNotFoundError` parce
que npm est installé comme `npm.cmd` ou `npm.ps1`. Conséquence :
runner_install_failed → install pipeline cassé sur 100% des Windows.

Fix : `_resolve_cli_binary("npm")` cherche explicitement `.cmd`/`.bat`/`.exe`
sur Windows avant le fallback `shutil.which`. Les `.ps1` sont volontairement
exclus (subprocess ne peut pas les exécuter sans powershell.exe).
"""
from __future__ import annotations

import shutil
import sys
from unittest.mock import patch

import pytest

from src.mcp.sandbox_runner import _resolve_cli_binary


class TestResolveCliBinaryWindows:

    def test_windows_prefers_cmd_over_ps1(self):
        """Sur Windows, .cmd doit être préféré à .ps1 (subprocess-exécutable)."""
        if sys.platform != "win32":
            pytest.skip("Windows-only behavior")

        def fake_which(name):
            # Simule un environnement avec .ps1 ET .cmd disponibles
            if name == "npm.cmd":
                return r"C:\Program Files\nodejs\npm.cmd"
            if name == "npm.bat":
                return None
            if name == "npm.exe":
                return None
            if name == "npm":
                return r"C:\Program Files\nodejs\npm.ps1"
            return None

        with patch.object(shutil, "which", side_effect=fake_which):
            resolved = _resolve_cli_binary("npm")
        assert resolved.endswith(".cmd"), (
            f"Sur Windows, attendu .cmd (subprocess-exécutable), "
            f"vu {resolved!r} (.ps1 casse subprocess sans shell)"
        )

    def test_windows_falls_back_to_exe_if_no_cmd(self):
        if sys.platform != "win32":
            pytest.skip("Windows-only behavior")

        def fake_which(name):
            if name in ("npm.cmd", "npm.bat"):
                return None
            if name == "npm.exe":
                return r"C:\tools\npm.exe"
            if name == "npm":
                return r"C:\tools\npm.exe"
            return None

        with patch.object(shutil, "which", side_effect=fake_which):
            resolved = _resolve_cli_binary("npm")
        assert resolved.endswith(".exe")

    def test_returns_name_unchanged_if_nothing_found(self):
        """Fallback : si rien trouvé, retourne le nom brut (subprocess lèvera FileNotFoundError)."""
        with patch.object(shutil, "which", return_value=None):
            resolved = _resolve_cli_binary("nonexistent_binary_xyz")
        assert resolved == "nonexistent_binary_xyz"

    def test_unix_uses_plain_which(self):
        """Sur Unix, juste shutil.which sans préférences d'extension."""
        if sys.platform == "win32":
            pytest.skip("Unix-only behavior")
        with patch.object(shutil, "which", return_value="/usr/bin/npm"):
            assert _resolve_cli_binary("npm") == "/usr/bin/npm"

    def test_real_npm_resolution_on_current_system(self):
        """Sanity check : sur le système courant, npm doit résoudre vers un truc exécutable."""
        resolved = _resolve_cli_binary("npm")
        if resolved == "npm":
            pytest.skip("npm not installed on this test system")
        # Si résolu, ne doit jamais être un .ps1 sur Windows
        if sys.platform == "win32":
            assert not resolved.lower().endswith(".ps1"), (
                f"Phase I-7 : .ps1 ne peut pas être lancé par subprocess sans "
                f"powershell.exe -File. Résolution actuelle = {resolved!r}"
            )
