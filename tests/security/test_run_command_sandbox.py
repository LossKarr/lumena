"""
Tests pour le sandbox Docker + cd/d extraction + git guard regex.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import re


class TestGitGuardCdD:
    """Vérifie que le git guard Cas 2 gère cd /d."""

    def test_git_guard_regex_with_cd_d(self):
        """Le regex Cas 2 doit matcher cd /d 'dir' && git push."""
        pattern = r'cd\s+(?:/d\s+)?("(?:[^"]+)"|\'(?:[^\']+)\'|[^\s&|;]+)\s*&&\s*git\s+(\S+)'
        # Avec /d
        m = re.match(pattern, 'cd /d "C:\\project" && git push', re.IGNORECASE)
        assert m is not None
        assert m.group(1) == '"C:\\project"'
        assert m.group(2) == "push"

    def test_git_guard_regex_without_cd_d(self):
        """Le regex Cas 2 doit toujours matcher cd simple (sans /d)."""
        pattern = r'cd\s+(?:/d\s+)?("(?:[^"]+)"|\'(?:[^\']+)\'|[^\s&|;]+)\s*&&\s*git\s+(\S+)'
        m = re.match(pattern, 'cd "project" && git commit', re.IGNORECASE)
        assert m is not None
        assert m.group(2) == "commit"

    def test_git_guard_regex_bare_dir(self):
        """Le regex Cas 2 matche cd dir (sans quotes)."""
        pattern = r'cd\s+(?:/d\s+)?("(?:[^"]+)"|\'(?:[^\']+)\'|[^\s&|;]+)\s*&&\s*git\s+(\S+)'
        m = re.match(pattern, 'cd myproject && git status', re.IGNORECASE)
        assert m is not None
        assert m.group(1) == "myproject"


class TestCdDExtraction:
    """Vérifie que cd /d est extrait correctement."""

    def test_cd_d_extraction_regex(self):
        """Le regex d'extraction cd /d fonctionne."""
        pattern = r'^cd\s+/d\s+"([^"]+)"\s*(?:&&|;)\s*(.*)'
        m = re.match(pattern, 'cd /d "C:\\Projects\\website" && sed -n "1,5p" f', re.IGNORECASE | re.DOTALL)
        assert m is not None
        assert m.group(1) == "C:\\Projects\\website"
        assert m.group(2) == 'sed -n "1,5p" f'

    def test_cd_d_extraction_with_semicolon(self):
        """Le regex gère ; comme séparateur."""
        pattern = r'^cd\s+/d\s+"([^"]+)"\s*(?:&&|;)\s*(.*)'
        m = re.match(pattern, 'cd /d "C:\\proj"; grep -n "x" f.html', re.IGNORECASE | re.DOTALL)
        assert m is not None
        assert m.group(2) == 'grep -n "x" f.html'


class TestSandboxWorkdir:
    """Vérifie que should_use_sandbox gère les commandes cd /d."""

    def test_should_use_sandbox_findstr_local(self):
        """findstr est dans _LOCAL_COMMANDS → pas de sandbox."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("findstr /n \"x\" file.txt") is False

    def test_should_use_sandbox_powershell_cmdlet(self):
        """Commande PS cmdlet → pas de sandbox."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("Get-Content file.txt") is False

    def test_should_use_sandbox_sed(self):
        """sed → sandbox (pas dans _LOCAL_COMMANDS)."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("sed -n '1,5p' file.txt") is True

    def test_should_use_sandbox_grep(self):
        """grep → sandbox."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("grep -n 'x' file.html") is True

    def test_should_use_sandbox_cd_d_sed(self):
        """cd /d + sed → sandbox (sed n'est pas local)."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox('cd /d "C:\\proj" && sed -n "1,5p" f') is True

    def test_should_use_sandbox_localhost_curl_stays_local(self):
        """curl localhost doit verifier l'hote, pas le container Docker."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("curl -s http://localhost:3000/health") is False

    def test_should_use_sandbox_loopback_ip_stays_local(self):
        """127.0.0.1 reste local meme avec curl."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("curl http://127.0.0.1:8080") is False

    def test_should_use_sandbox_external_curl_stays_sandboxed(self):
        """Un vrai endpoint externe continue de passer par Docker."""
        from src.utils.docker_sandbox import should_use_sandbox
        with patch("src.utils.docker_sandbox.get_sandbox_mode", return_value="auto"):
            assert should_use_sandbox("curl -I https://example.com") is True
