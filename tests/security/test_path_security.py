"""Tests P0.2 — Path traversal & blacklist guardrails."""

import pytest
from pathlib import Path

from src.tools.file_guardrails import (
    PathSecurityError,
    check_path_boundary,
    check_read_blacklist,
    check_write_blacklist,
    check_delete_allowed,
)


@pytest.fixture
def roots(tmp_path):
    lumena = tmp_path / "lumena"
    workspace = lumena / "workspace"
    lumena.mkdir()
    workspace.mkdir()
    # Create a few dummy files/dirs for resolved paths
    (lumena / ".env").touch()
    (lumena / "data").mkdir()
    (lumena / "data" / "mail").mkdir()
    (lumena / "data" / "journal.json").touch()
    (lumena / "data" / "browser_profiles").mkdir()
    (lumena / "data" / "browser_profiles" / "chrome.json").touch()
    (lumena / "data" / "mail" / "inbox.json").touch()
    (lumena / "models").mkdir()
    (lumena / "models" / "model.bin").touch()
    (lumena / "backups").mkdir()
    (lumena / "backups" / "snap.backup").touch()
    (lumena / "src").mkdir()
    (lumena / "src" / "core.py").touch()
    (workspace / "projet").mkdir()
    (workspace / "projet" / "index.html").touch()
    return lumena, workspace


# ===== check_path_boundary =====

class TestPathBoundary:
    def test_inside_lumena(self, roots):
        lumena, workspace = roots
        check_path_boundary((lumena / "src" / "core.py").resolve(), lumena, workspace)

    def test_inside_workspace(self, roots):
        lumena, workspace = roots
        check_path_boundary((workspace / "projet" / "index.html").resolve(), lumena, workspace)

    def test_outside_both_raises(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError, match="hors des limites"):
            check_path_boundary(Path("C:/Windows/System32/cmd.exe").resolve(), lumena, workspace)

    def test_parent_traversal_raises(self, roots):
        lumena, workspace = roots
        # Trying to escape via ../..
        evil = (lumena / ".." / ".." / "etc" / "passwd").resolve()
        if evil != lumena.resolve() and not str(evil).startswith(str(lumena.resolve())):
            with pytest.raises(PathSecurityError, match="hors des limites"):
                check_path_boundary(evil, lumena, workspace)

    def test_root_itself_ok(self, roots):
        lumena, workspace = roots
        check_path_boundary(lumena.resolve(), lumena, workspace)
        check_path_boundary(workspace.resolve(), lumena, workspace)


# ===== check_read_blacklist =====

class TestReadBlacklist:
    def test_env_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="secrets"):
            check_read_blacklist((lumena / ".env").resolve(), lumena)

    def test_mail_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="privée"):
            check_read_blacklist((lumena / "data" / "mail" / "inbox.json").resolve(), lumena)

    def test_browser_profiles_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="privée"):
            check_read_blacklist((lumena / "data" / "browser_profiles" / "chrome.json").resolve(), lumena)

    def test_src_allowed(self, roots):
        lumena, _ = roots
        # Should NOT raise
        check_read_blacklist((lumena / "src" / "core.py").resolve(), lumena)

    def test_data_journal_allowed(self, roots):
        lumena, _ = roots
        # data/journal.json is NOT in read blacklist (only mail and browser_profiles dirs)
        check_read_blacklist((lumena / "data" / "journal.json").resolve(), lumena)

    def test_outside_lumena_passes(self, roots):
        lumena, _ = roots
        # Outside lumena_root, the function just returns (boundary check handles it)
        check_read_blacklist(Path("C:/tmp/random.txt").resolve(), lumena)


# ===== check_write_blacklist =====

class TestWriteBlacklist:
    def test_env_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="configuration protégé"):
            check_write_blacklist((lumena / ".env").resolve(), lumena)

    def test_data_dir_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="zone protégée"):
            check_write_blacklist((lumena / "data" / "journal.json").resolve(), lumena)

    def test_data_mail_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="zone protégée"):
            check_write_blacklist((lumena / "data" / "mail" / "inbox.json").resolve(), lumena)

    def test_models_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="zone protégée"):
            check_write_blacklist((lumena / "models" / "model.bin").resolve(), lumena)

    def test_backups_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match="zone protégée"):
            check_write_blacklist((lumena / "backups" / "snap.backup").resolve(), lumena)

    def test_backup_suffix_blocked(self, roots):
        lumena, _ = roots
        with pytest.raises(PathSecurityError, match=".backup"):
            check_write_blacklist((lumena / "src" / "thing.backup").resolve(), lumena)

    def test_src_allowed(self, roots):
        lumena, _ = roots
        check_write_blacklist((lumena / "src" / "core.py").resolve(), lumena)

    def test_workspace_file_allowed(self, roots):
        lumena, workspace = roots
        check_write_blacklist((workspace / "projet" / "index.html").resolve(), lumena)


# ===== check_delete_allowed =====

class TestDeleteAllowed:
    def test_workspace_file_ok(self, roots):
        lumena, workspace = roots
        check_delete_allowed((workspace / "projet" / "index.html").resolve(), lumena, workspace)

    def test_src_blocked(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError, match="workspace"):
            check_delete_allowed((lumena / "src" / "core.py").resolve(), lumena, workspace)

    def test_data_blocked(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError, match="workspace"):
            check_delete_allowed((lumena / "data" / "journal.json").resolve(), lumena, workspace)

    def test_env_blocked(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError, match="workspace"):
            check_delete_allowed((lumena / ".env").resolve(), lumena, workspace)

    def test_outside_project_blocked(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError, match="workspace"):
            check_delete_allowed(Path("C:/tmp/evil.txt").resolve(), lumena, workspace)


# ===== Integration: resolve_user_path boundary =====

class TestResolveUserPathBoundary:
    """Verify that resolve_user_path raises on traversal attempts."""

    def test_traversal_dotdot_raises(self, roots):
        lumena, workspace = roots
        # Direct boundary check simulating what resolve_user_path does
        evil = (lumena / ".." / ".." / "Windows" / "System32" / "cmd.exe").resolve()
        if not str(evil).startswith(str(lumena.resolve())):
            with pytest.raises(PathSecurityError):
                check_path_boundary(evil, lumena, workspace)

    def test_absolute_outside_raises(self, roots):
        lumena, workspace = roots
        with pytest.raises(PathSecurityError):
            check_path_boundary(Path("/etc/shadow").resolve(), lumena, workspace)
