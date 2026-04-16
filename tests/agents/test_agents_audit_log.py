"""Tests unitaires pour src/agents/audit_log.py"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.agents.audit_log import SubAgentAuditLog


# ─── _sanitize_args ────────────────────────────────────────────────────────

class TestSanitizeArgs:
    def test_short_values_unchanged(self):
        args = {"file": "test.py", "content": "hello"}
        result = SubAgentAuditLog._sanitize_args(args)
        assert result["file"] == "test.py"
        assert result["content"] == "hello"

    def test_long_values_truncated(self):
        args = {"content": "x" * 500}
        result = SubAgentAuditLog._sanitize_args(args)
        assert result["content"].endswith("...")
        assert len(result["content"]) == 303  # 300 + "..."

    def test_empty_args(self):
        result = SubAgentAuditLog._sanitize_args({})
        assert result == {}

    def test_non_string_value(self):
        args = {"count": 42, "flag": True}
        result = SubAgentAuditLog._sanitize_args(args)
        # non-string values are converted via str()
        assert "count" in result


# ─── _extract_target_path ──────────────────────────────────────────────────

class TestExtractTargetPath:
    def test_file_path_key(self):
        args = {"file_path": "/tmp/foo.py"}
        result = SubAgentAuditLog._extract_target_path("write_file", args)
        assert result == "/tmp/foo.py"

    def test_path_key(self):
        args = {"path": "/data/bar.json"}
        result = SubAgentAuditLog._extract_target_path("delete_file", args)
        assert result == "/data/bar.json"

    def test_filepath_key(self):
        args = {"filepath": "src/main.py"}
        result = SubAgentAuditLog._extract_target_path("write_file", args)
        assert result == "src/main.py"

    def test_no_path_returns_none(self):
        args = {"command": "ls -la"}
        result = SubAgentAuditLog._extract_target_path("run_command", args)
        assert result is None

    def test_empty_value_returns_none(self):
        args = {"file_path": "", "path": None}
        result = SubAgentAuditLog._extract_target_path("write_file", args)
        assert result is None


# ─── log_action ────────────────────────────────────────────────────────────

class TestLogAction:
    def test_writes_jsonl_entry(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        audit.log_action(
            agent_name="coder",
            tool_name="write_file",
            arguments={"file_path": "foo.py", "content": "hello"},
            task_id="task-1",
            success=True,
            result_summary="File written"
        )
        log_files = list((tmp_path / "ops" / "subagent_audit").glob("audit_*.jsonl"))
        assert len(log_files) == 1
        lines = log_files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["agent"] == "coder"
        assert entry["tool"] == "write_file"
        assert entry["success"] is True

    def test_result_truncated_to_200(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        long_result = "r" * 500
        audit.log_action(
            agent_name="a", tool_name="t", arguments={},
            result_summary=long_result
        )
        log_file = list((tmp_path / "ops" / "subagent_audit").glob("audit_*.jsonl"))[0]
        entry = json.loads(log_file.read_text().strip())
        assert len(entry["result"]) == 200

    def test_multiple_entries(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        for i in range(3):
            audit.log_action(f"agent{i}", f"tool{i}", {})
        log_file = list((tmp_path / "ops" / "subagent_audit").glob("audit_*.jsonl"))[0]
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_no_exception_on_write_error(self, tmp_path):
        """Should not raise even if directory is unwritable."""
        audit = SubAgentAuditLog(data_dir=tmp_path)
        # Patch the log_file to return a path that can't be written
        with patch.object(audit, "_log_file", return_value=Path("/nonexistent/path/log.jsonl")):
            # Should not raise
            audit.log_action("a", "t", {})


# ─── backup_before_destructive ─────────────────────────────────────────────

class TestBackupBeforeDestructive:
    def test_non_destructive_action_no_backup(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        result = audit.backup_before_destructive("read_file", {"file_path": "foo.py"})
        assert result is None

    def test_destructive_action_creates_backup(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        # Create a real file to back up
        source = tmp_path / "source_file.py"
        source.write_text("original content")
        result = audit.backup_before_destructive(
            "write_file", {"file_path": str(source)}
        )
        assert result is not None
        backup = Path(result)
        assert backup.exists()
        assert backup.read_text() == "original content"

    def test_no_backup_for_nonexistent_file(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        result = audit.backup_before_destructive(
            "write_file", {"file_path": str(tmp_path / "nonexistent.py")}
        )
        assert result is None

    def test_no_path_in_args_no_backup(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        result = audit.backup_before_destructive("write_file", {"command": "ls"})
        assert result is None


# ─── cleanup_old_logs ──────────────────────────────────────────────────────

class TestCleanupOldLogs:
    def test_removes_old_log_files(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        audit_dir = tmp_path / "ops" / "subagent_audit"
        # Create a fake old log file
        old_log = audit_dir / "audit_2020-01-01.jsonl"
        old_log.write_text("old entry")
        import time
        # Set mtime to 40 days ago
        old_time = time.time() - (40 * 86400)
        import os
        os.utime(old_log, (old_time, old_time))
        removed = audit.cleanup_old_logs()
        assert removed == 1
        assert not old_log.exists()

    def test_keeps_recent_logs(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        audit.log_action("a", "t", {})  # Creates a today log
        removed = audit.cleanup_old_logs()
        assert removed == 0

    def test_returns_zero_when_no_logs(self, tmp_path):
        audit = SubAgentAuditLog(data_dir=tmp_path)
        removed = audit.cleanup_old_logs()
        assert removed == 0


# ─── DESTRUCTIVE_ACTIONS constant ──────────────────────────────────────────

class TestDestructiveActions:
    def test_write_file_is_destructive(self):
        assert "write_file" in SubAgentAuditLog.DESTRUCTIVE_ACTIONS

    def test_delete_file_is_destructive(self):
        assert "delete_file" in SubAgentAuditLog.DESTRUCTIVE_ACTIONS

    def test_read_file_not_destructive(self):
        assert "read_file" not in SubAgentAuditLog.DESTRUCTIVE_ACTIONS
