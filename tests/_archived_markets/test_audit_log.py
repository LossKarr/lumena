"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Audit Log
==================
"""

import tempfile
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import pytest

from src.markets.risk.audit import AuditLog
from src.markets.risk.kill_switch import KillSwitch


@pytest.fixture
def temp_dir():
    """Crée un répertoire temporaire."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audit_log(temp_dir):
    """Crée un audit log temporaire."""
    return AuditLog(temp_dir / "audit.jsonl")


class TestAuditLogBasic:
    """Tests basiques de l'audit log."""
    
    def test_log_action(self, audit_log):
        """Log une action."""
        audit_log.log_action(
            action="order_submitted",
            details={"symbol": "AAPL", "qty": 100},
            result="success",
        )
        
        entries = audit_log.replay()
        
        assert len(entries) == 1
        assert entries[0]["action"] == "order_submitted"
        assert entries[0]["result"] == "success"
    
    def test_log_rejection(self, audit_log):
        """Log un rejet."""
        audit_log.log_rejection(
            action="order_validation",
            reason="no_stop_loss",
            details={"symbol": "MSFT"},
        )
        
        entries = audit_log.replay()
        
        assert len(entries) == 1
        assert entries[0]["type"] == "rejection"
        assert entries[0]["reason"] == "no_stop_loss"
    
    def test_log_event(self, audit_log):
        """Log un événement."""
        audit_log.log_event(
            event="session_start",
            details={"version": "1.0"},
        )
        
        entries = audit_log.replay()
        
        assert len(entries) == 1
        assert entries[0]["type"] == "event"
        assert entries[0]["event"] == "session_start"
    
    def test_multiple_entries(self, audit_log):
        """Multiple entrées."""
        audit_log.log_action("action1", {}, "success")
        audit_log.log_action("action2", {}, "error")
        audit_log.log_rejection("action3", "reason", {})
        audit_log.log_event("event1")
        
        entries = audit_log.replay()
        
        assert len(entries) == 4


class TestAuditLogReplay:
    """Tests du replay."""
    
    def test_replay_empty(self, temp_dir):
        """Replay sur fichier inexistant."""
        audit = AuditLog(temp_dir / "nonexistent.jsonl")
        
        entries = audit.replay()
        
        assert entries == []
    
    def test_replay_with_filter_type(self, audit_log):
        """Replay avec filtre par type."""
        audit_log.log_action("action1", {}, "success")
        audit_log.log_rejection("action2", "reason", {})
        audit_log.log_event("event1")
        
        rejections = audit_log.replay(type_filter="rejection")
        
        assert len(rejections) == 1
        assert rejections[0]["type"] == "rejection"
    
    def test_replay_with_filter_action(self, audit_log):
        """Replay avec filtre par action."""
        audit_log.log_action("submit", {}, "success")
        audit_log.log_action("cancel", {}, "success")
        audit_log.log_action("submit", {}, "error")
        
        submits = audit_log.replay(action_filter="submit")
        
        assert len(submits) == 2
    
    def test_replay_with_time_filter(self, audit_log):
        """Replay avec filtre temporel."""
        # Log maintenant
        audit_log.log_action("now", {}, "success")
        
        # Replay depuis demain (devrait être vide)
        tomorrow = datetime.now() + timedelta(days=1)
        entries = audit_log.replay(since=tomorrow)
        
        assert len(entries) == 0
        
        # Replay depuis hier (devrait avoir l'entrée)
        yesterday = datetime.now() - timedelta(days=1)
        entries = audit_log.replay(since=yesterday)
        
        assert len(entries) == 1


class TestAuditLogReplayStress:
    """Tests de stress du replay."""
    
    def test_replay_100_entries(self, audit_log):
        """Replay après 100 écritures."""
        for i in range(100):
            audit_log.log_action(f"action_{i}", {"index": i}, "success")
        
        entries = audit_log.replay()
        
        assert len(entries) == 100
        assert entries[0]["details"]["index"] == 0
        assert entries[99]["details"]["index"] == 99
    
    def test_replay_handles_corrupted_line(self, temp_dir):
        """Replay ignore les lignes corrompues."""
        log_path = temp_dir / "corrupted.jsonl"
        
        # Écrire des entrées valides et une corrompue
        with open(log_path, "w", encoding="utf-8") as f:
            f.write('{"type": "action", "action": "valid1"}\n')
            f.write('NOT VALID JSON\n')
            f.write('{"type": "action", "action": "valid2"}\n')
        
        audit = AuditLog(log_path)
        entries = audit.replay()
        
        # Devrait ignorer la ligne corrompue
        assert len(entries) == 2


class TestAuditLogDailySummary:
    """Tests du résumé journalier."""
    
    def test_get_daily_summary_empty(self, audit_log):
        """Résumé sur log vide."""
        summary = audit_log.get_daily_summary()
        
        assert summary["total_entries"] == 0
        assert summary["actions"]["total"] == 0
    
    def test_get_daily_summary(self, audit_log):
        """Résumé complet."""
        audit_log.log_action("order", {"symbol": "AAPL"}, "success")
        audit_log.log_action("order", {"symbol": "MSFT"}, "success")
        audit_log.log_action("order", {"symbol": "GOOGL"}, "error")
        audit_log.log_rejection("order", "no_stop_loss", {})
        audit_log.log_rejection("order", "no_stop_loss", {})
        audit_log.log_event("session_start")
        
        summary = audit_log.get_daily_summary()
        
        assert summary["total_entries"] == 6
        assert summary["actions"]["total"] == 3
        assert summary["actions"]["success"] == 2
        assert summary["actions"]["error"] == 1
        assert summary["rejections"]["total"] == 2
        assert summary["rejections"]["by_reason"]["no_stop_loss"] == 2
        assert summary["events"]["total"] == 1


class TestAuditLogStats:
    """Tests des statistiques."""
    
    def test_get_stats_empty(self, temp_dir):
        """Stats sur log inexistant."""
        audit = AuditLog(temp_dir / "nonexistent.jsonl")
        stats = audit.get_stats()
        
        assert stats["file_exists"] is False
        assert stats["total_entries"] == 0
    
    def test_get_stats(self, audit_log):
        """Stats après écritures."""
        for i in range(10):
            audit_log.log_action(f"action_{i}", {}, "success")
        
        stats = audit_log.get_stats()
        
        assert stats["file_exists"] is True
        assert stats["total_entries"] == 10
        assert stats["file_size_bytes"] > 0


class TestAuditLogFileLock:
    """Tests du file lock pour accès concurrent."""
    
    def test_concurrent_writes(self, temp_dir):
        """Écritures concurrentes avec file lock."""
        log_path = temp_dir / "concurrent.jsonl"
        audit = AuditLog(log_path)
        
        errors = []
        
        def writer(thread_id):
            try:
                for i in range(10):
                    audit.log_action(
                        f"thread_{thread_id}_action_{i}",
                        {"thread": thread_id, "index": i},
                        "success"
                    )
                    time.sleep(0.001)  # Petit délai pour forcer contention
            except Exception as e:
                errors.append(e)
        
        # Lancer 3 threads qui écrivent en parallèle
        threads = []
        for t_id in range(3):
            t = threading.Thread(target=writer, args=(t_id,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=10)  # Timeout pour éviter blocage
        
        # Pas d'erreurs
        assert len(errors) == 0
        
        # Toutes les entrées sont là (3 threads * 10 actions = 30)
        entries = audit.replay()
        assert len(entries) == 30


class TestAuditLogClear:
    """Tests du clear (pour tests uniquement)."""
    
    def test_clear(self, audit_log):
        """Clear efface le log."""
        audit_log.log_action("test", {}, "success")
        assert len(audit_log.replay()) == 1
        
        audit_log.clear()
        
        assert len(audit_log.replay()) == 0


class TestKillSwitch:
    """Tests du KillSwitch."""
    
    @pytest.fixture
    def kill_switch(self, temp_dir):
        """Crée un kill switch temporaire."""
        return KillSwitch(kill_file=str(temp_dir / "KILL_SWITCH"))
    
    def test_initial_state_inactive(self, kill_switch):
        """Kill switch inactif initialement."""
        assert not kill_switch.is_active
    
    def test_activate(self, kill_switch):
        """Activation du kill switch."""
        kill_switch.activate("Test reason")
        
        assert kill_switch.is_active
        assert kill_switch.get_reason() == "Test reason"
    
    def test_deactivate(self, kill_switch):
        """Désactivation du kill switch."""
        kill_switch.activate("Test")
        assert kill_switch.is_active
        
        kill_switch.deactivate()
        
        assert not kill_switch.is_active
    
    def test_get_activation_time(self, kill_switch):
        """Récupération du timestamp d'activation."""
        before = datetime.now()
        kill_switch.activate("Test")
        after = datetime.now()
        
        activation_time = kill_switch.get_activation_time()
        
        assert activation_time is not None
        assert before <= activation_time <= after
    
    def test_get_status(self, kill_switch):
        """Test get_status."""
        kill_switch.activate("Emergency stop")
        
        status = kill_switch.get_status()
        
        assert status["is_active"] is True
        assert status["reason"] == "Emergency stop"
        assert status["activation_time"] is not None
    
    def test_deactivate_when_inactive(self, kill_switch):
        """Désactiver quand déjà inactif ne crashe pas."""
        assert not kill_switch.is_active
        
        # Ne devrait pas lever d'exception
        kill_switch.deactivate()
        
        assert not kill_switch.is_active
