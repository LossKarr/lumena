"""Tests P11 — Polish UX (DESTRUCTIVE_CONFIRM + FRENCH_ERRORS)."""
from __future__ import annotations

import importlib
import pytest


@pytest.fixture
def _reset_flags(monkeypatch):
    # S'assurer que les flags partent d'un état connu à chaque test
    for var in ("LUMENA_DESTRUCTIVE_CONFIRM", "LUMENA_FRENCH_ERRORS"):
        monkeypatch.delenv(var, raising=False)
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    return cf


# ──────────────────────────────────────────────────────────────
# FRENCH_ERRORS
# ──────────────────────────────────────────────────────────────

def test_french_errors_flag_off_noop(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.french_errors as fe
    importlib.reload(fe)
    msg = "FileNotFoundError: No such file or directory: '/tmp/x.txt'"
    assert fe.translate_error(msg) == msg


def test_french_errors_translates_file_not_found(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "true")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.french_errors as fe
    importlib.reload(fe)
    out = fe.translate_error("No such file or directory: '/tmp/x.txt'")
    assert "introuvable" in out.lower()
    assert "/tmp/x.txt" in out


def test_french_errors_translates_permission(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "true")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.french_errors as fe
    importlib.reload(fe)
    out = fe.translate_error("PermissionError: /etc/shadow")
    assert "permission refus" in out.lower()


def test_french_errors_translates_module_not_found(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "true")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.french_errors as fe
    importlib.reload(fe)
    out = fe.translate_error("ModuleNotFoundError: No module named 'requests'")
    assert "introuvable" in out.lower()
    assert "requests" in out


def test_french_errors_empty_string_noop(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "true")
    import src.utils.french_errors as fe
    importlib.reload(fe)
    assert fe.translate_error("") == ""
    assert fe.translate_error(None) is None  # type: ignore[arg-type]


def test_french_errors_unknown_message_preserved(monkeypatch, _reset_flags):
    monkeypatch.setenv("LUMENA_FRENCH_ERRORS", "true")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.french_errors as fe
    importlib.reload(fe)
    msg = "Random unrelated output from tool"
    assert fe.translate_error(msg) == msg


# ──────────────────────────────────────────────────────────────
# DESTRUCTIVE_CONFIRM (unit-level regex check)
# ──────────────────────────────────────────────────────────────

_DESTRUCTIVE_CMDS = [
    "rm -rf /tmp/foo",
    "rm -r /tmp/foo",
    "rmdir /s C:\\temp",
    "Remove-Item C:\\x -Recurse -Force",
    "git push origin main --force",
    "git reset --hard HEAD~5",
    "git clean -fd",
    "DROP TABLE users;",
    "drop database prod",
    "del /s C:\\data\\*",
    "format c:",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
]

_SAFE_CMDS = [
    "python test.py",
    "npm test",
    "pytest tests/",
    "git status",
    "git push origin main",  # sans --force → safe
    "ls -la",
    "rm file.txt",  # sans -rf → safe
    "node --check script.js",
]


@pytest.mark.parametrize("cmd", _DESTRUCTIVE_CMDS)
def test_destructive_pattern_matches(cmd):
    import re
    patterns = (
        r"\brm\s+-rf?\b",
        r"\brmdir\s+/[sq]",
        r"\bRemove-Item\b.*-Recurse.*-Force",
        r"\bgit\s+push\s+.*--force\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-zA-Z]*f",
        r"\bdrop\s+table\b",
        r"\bdrop\s+database\b",
        r"\bdel\s+/[sq]",
        r"\bformat\s+[a-z]:",
        r":>\s*/dev/[a-z]+",
        r"\bdd\s+.*of=/dev/",
        r"\bmkfs\b",
    )
    assert any(re.search(p, cmd, re.IGNORECASE) for p in patterns), (
        f"Commande destructive non détectée : {cmd!r}"
    )


@pytest.mark.parametrize("cmd", _SAFE_CMDS)
def test_safe_commands_not_matched(cmd):
    import re
    patterns = (
        r"\brm\s+-rf?\b",
        r"\brmdir\s+/[sq]",
        r"\bRemove-Item\b.*-Recurse.*-Force",
        r"\bgit\s+push\s+.*--force\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-zA-Z]*f",
        r"\bdrop\s+table\b",
        r"\bdrop\s+database\b",
        r"\bdel\s+/[sq]",
        r"\bformat\s+[a-z]:",
        r":>\s*/dev/[a-z]+",
        r"\bdd\s+.*of=/dev/",
        r"\bmkfs\b",
    )
    assert not any(re.search(p, cmd, re.IGNORECASE) for p in patterns), (
        f"Faux positif sur commande safe : {cmd!r}"
    )


def test_destructive_confirm_flag_default_off(_reset_flags):
    from src.config.codeagent_flags import DESTRUCTIVE_CONFIRM
    assert DESTRUCTIVE_CONFIRM is False


def test_french_errors_flag_default_on(_reset_flags):
    # FRENCH_ERRORS est opt-OUT (default True)
    from src.config.codeagent_flags import FRENCH_ERRORS
    assert FRENCH_ERRORS is True
