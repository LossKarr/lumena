"""
Tests Phase 4 — Audit structurel & métriques fiables.

Valide que :
  - _classify_tool_outcome retourne le bon outcome structuré
  - audit_log.log_action enregistre outcome + success dérivé (sans heuristique textuelle)
  - record_task_metrics rejette silencieusement les task_id vides (orphelins)
"""
import json
import tempfile
from pathlib import Path
import pytest

from src.agents.sub_agent import _classify_tool_outcome
from src.agents.audit_log import SubAgentAuditLog


# ─────────────────────────────────────────────────────────────────────────────
# _classify_tool_outcome — classification structurée
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyToolOutcome:
    def test_success_empty_result(self):
        assert _classify_tool_outcome("") == "success"

    def test_success_normal_output(self):
        assert _classify_tool_outcome("fichier créé avec succès") == "success"

    def test_tool_not_found_fr(self):
        assert _classify_tool_outcome("outil 'run_command' non trouvé") == "tool_not_found"

    def test_tool_not_found_en(self):
        assert _classify_tool_outcome("tool 'write_file' not found") == "tool_not_found"

    def test_tool_not_found_introuvable(self):
        assert _classify_tool_outcome("outil introuvable dans le registre") == "tool_not_found"

    def test_tool_not_found_unknown_tool(self):
        assert _classify_tool_outcome("unknown tool requested") == "tool_not_found"

    def test_tool_not_found_aucun_outil(self):
        assert _classify_tool_outcome("aucun outil enregistré sous ce nom") == "tool_not_found"

    def test_policy_denied_permission(self):
        assert _classify_tool_outcome("permission denied: /etc/shadow") == "policy_denied"

    def test_policy_denied_access(self):
        assert _classify_tool_outcome("access denied by policy") == "policy_denied"

    def test_policy_denied_forbidden(self):
        assert _classify_tool_outcome("forbidden: workspace boundary") == "policy_denied"

    def test_exception_nameerror(self):
        assert _classify_tool_outcome("nameerror: name 'x' is not defined") == "exception"

    def test_exception_attributeerror(self):
        assert _classify_tool_outcome("attributeerror: object has no attribute 'run'") == "exception"

    def test_exception_keyerror(self):
        assert _classify_tool_outcome("keyerror: 'missing_key'") == "exception"

    def test_exception_typeerror(self):
        assert _classify_tool_outcome("typeerror: expected str, got int") == "exception"

    def test_priority_not_found_over_exception(self):
        # "not found" a priorité sur "nameerror" (rare mais possible)
        assert _classify_tool_outcome("nameerror: outil not found") == "tool_not_found"

    def test_priority_not_found_over_policy(self):
        assert _classify_tool_outcome("access denied: outil non trouvé") == "tool_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# SubAgentAuditLog.log_action — outcome structurel
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogOutcome:
    def _make_audit(self, tmp_path: Path) -> SubAgentAuditLog:
        return SubAgentAuditLog(data_dir=tmp_path)

    def _read_entries(self, tmp_path: Path) -> list[dict]:
        audit_dir = tmp_path / "ops" / "subagent_audit"
        entries = []
        for f in audit_dir.glob("audit_*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        return entries

    def test_success_outcome_sets_success_true(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "write_file", {}, task_id="t1", outcome="success")
        entries = self._read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "success"
        assert entries[0]["success"] is True

    def test_tool_not_found_sets_success_false(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "run_command", {}, task_id="t2", outcome="tool_not_found")
        entries = self._read_entries(tmp_path)
        assert entries[0]["outcome"] == "tool_not_found"
        assert entries[0]["success"] is False

    def test_timeout_outcome(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "http_probe", {}, task_id="t3", outcome="timeout")
        entries = self._read_entries(tmp_path)
        assert entries[0]["outcome"] == "timeout"
        assert entries[0]["success"] is False

    def test_policy_denied_outcome(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "write_file", {}, task_id="t4", outcome="policy_denied")
        entries = self._read_entries(tmp_path)
        assert entries[0]["outcome"] == "policy_denied"
        assert entries[0]["success"] is False

    def test_exception_outcome(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "run_command", {}, outcome="exception", result_summary="AttributeError")
        entries = self._read_entries(tmp_path)
        assert entries[0]["outcome"] == "exception"
        assert entries[0]["success"] is False

    def test_empty_outcome_success_false(self, tmp_path):
        audit = self._make_audit(tmp_path)
        audit.log_action("agent1", "some_tool", {}, outcome="")
        entries = self._read_entries(tmp_path)
        assert entries[0]["outcome"] == ""
        assert entries[0]["success"] is False

    def test_result_summary_truncated(self, tmp_path):
        audit = self._make_audit(tmp_path)
        long_result = "x" * 500
        audit.log_action("agent1", "read_file", {}, outcome="success", result_summary=long_result)
        entries = self._read_entries(tmp_path)
        assert len(entries[0]["result"]) == 200

    def test_all_recognized_outcomes_recorded(self, tmp_path):
        audit = self._make_audit(tmp_path)
        for outcome in SubAgentAuditLog.OUTCOMES:
            audit.log_action("a", "t", {}, outcome=outcome)
        entries = self._read_entries(tmp_path)
        recorded = {e["outcome"] for e in entries}
        assert SubAgentAuditLog.OUTCOMES == recorded


# ─────────────────────────────────────────────────────────────────────────────
# record_task_metrics — garde task_id vide
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsSyntheticGuard:
    """Teste le rejet silencieux des entrées sans task_id réel."""

    def _call_record(self, task_id: str, metrics_file: Path, monkeypatch) -> None:
        from src.utils import metrics as m_mod
        import src.utils.metrics as m

        # Patcher CODING_METRICS = True et LOGS_DIR → tmp dir
        monkeypatch.setattr("src.config.codeagent_flags.CODING_METRICS", True, raising=False)

        # Forcer LOGS_DIR vers un répertoire temporaire
        parent = metrics_file.parent.parent
        import src.utils.paths as paths_mod
        monkeypatch.setattr(paths_mod, "LOGS_DIR", parent)

        m.record_task_metrics(
            task_id=task_id,
            model_name="test-model",
            attempt=1,
            iterations=3,
            success=False,
            status_code="error",
            duration_s=1.0,
        )

    def test_empty_task_id_skipped(self, tmp_path, monkeypatch):
        metrics_dir = tmp_path / "codeagent"
        metrics_dir.mkdir()
        metrics_file = metrics_dir / "metrics.jsonl"

        self._call_record("", metrics_file, monkeypatch)
        # Rien ne doit être écrit
        assert not metrics_file.exists() or metrics_file.read_text().strip() == ""

    def test_whitespace_task_id_skipped(self, tmp_path, monkeypatch):
        metrics_dir = tmp_path / "codeagent"
        metrics_dir.mkdir()
        metrics_file = metrics_dir / "metrics.jsonl"

        self._call_record("   ", metrics_file, monkeypatch)
        assert not metrics_file.exists() or metrics_file.read_text().strip() == ""

    def test_real_task_id_written(self, tmp_path, monkeypatch):
        metrics_dir = tmp_path / "codeagent"
        metrics_dir.mkdir()
        metrics_file = metrics_dir / "metrics.jsonl"

        self._call_record("task_42_194147", metrics_file, monkeypatch)
        assert metrics_file.exists()
        entry = json.loads(metrics_file.read_text().strip())
        assert entry["task_id"] == "task_42_194147"
        assert entry["success"] is False

    def test_dlg_task_id_written(self, tmp_path, monkeypatch):
        metrics_dir = tmp_path / "codeagent"
        metrics_dir.mkdir()
        metrics_file = metrics_dir / "metrics.jsonl"

        self._call_record("dlg_7_093012", metrics_file, monkeypatch)
        assert metrics_file.exists()
        entry = json.loads(metrics_file.read_text().strip())
        assert entry["task_id"] == "dlg_7_093012"
