"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Market Sentinel Integration
====================================
"""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.markets.config import MarketConfig
from src.markets.sentinel import MarketSentinel
from src.markets.health.status import HealthLevel
from src.markets.output.report import MarketReport, ReportEntry


class TestMarketSentinelLifecycle:
    """Tests du cycle de vie du Sentinel."""
    
    @pytest.fixture
    def config(self):
        """Configuration de test."""
        return MarketConfig(
            mode="paper",
            auto_trade=False,
            max_symbols=5,
            scan_interval=10,  # Minimum valide
        )
    
    @pytest.fixture
    def sentinel(self, config):
        """Crée un sentinel pour les tests."""
        return MarketSentinel(config)
    
    @pytest.mark.asyncio
    async def test_start_stop(self, sentinel):
        """Test start/stop basique."""
        assert not sentinel.is_running
        
        await sentinel.start()
        assert sentinel.is_running
        
        await sentinel.stop()
        assert not sentinel.is_running
    
    @pytest.mark.asyncio
    async def test_start_idempotent(self, sentinel):
        """Start est idempotent."""
        await sentinel.start()
        await sentinel.start()  # Second appel ignoré
        
        assert sentinel.is_running
        
        await sentinel.stop()
    
    @pytest.mark.asyncio
    async def test_stop_idempotent(self, sentinel):
        """Stop est idempotent."""
        await sentinel.start()
        await sentinel.stop()
        await sentinel.stop()  # Second appel ignoré, pas d'erreur
        
        assert not sentinel.is_running
    
    @pytest.mark.asyncio
    async def test_stop_without_start(self, sentinel):
        """Stop sans start ne crashe pas."""
        await sentinel.stop()  # No-op
        assert not sentinel.is_running
    
    @pytest.mark.asyncio
    async def test_shutdown_clean(self, sentinel):
        """Shutdown propre sans tâche pendante."""
        await sentinel.start()
        
        # Bref délai (pas besoin d'attendre un cycle complet)
        await asyncio.sleep(0.05)
        
        await sentinel.stop()
        
        # Vérifier qu'il n'y a pas de tâche pendante
        assert sentinel._main_task is None


class TestMarketSentinelStatus:
    """Tests du statut du Sentinel."""
    
    @pytest.fixture
    def config(self):
        return MarketConfig(
            mode="paper",
            auto_trade=False,
            max_symbols=10,
            scan_interval=10,  # Minimum valide
        )
    
    @pytest.fixture
    def sentinel(self, config):
        return MarketSentinel(config)
    
    @pytest.mark.asyncio
    async def test_get_status_not_running(self, sentinel):
        """Status quand pas démarré."""
        status = sentinel.get_status()
        
        assert status["running"] is False
        assert status["mode"] == "paper"
        assert status["uptime_seconds"] == 0.0
    
    @pytest.mark.asyncio
    async def test_get_status_running(self, sentinel):
        """Status quand démarré."""
        await sentinel.start()
        await asyncio.sleep(0.05)
        
        status = sentinel.get_status()
        
        assert status["running"] is True
        assert status["uptime_seconds"] > 0
        
        await sentinel.stop()
    
    @pytest.mark.asyncio
    async def test_status_contains_health(self, sentinel):
        """Le status contient les infos de health."""
        status = sentinel.get_status()
        
        assert "health" in status
        assert "components" in status["health"]


class TestMarketSentinelKillSwitch:
    """Tests du kill switch dans le Sentinel."""
    
    @pytest.fixture
    def config(self):
        return MarketConfig(
            mode="paper",
            auto_trade=False,
            scan_interval=10,  # Minimum valide
        )
    
    @pytest.fixture
    def sentinel(self, config):
        return MarketSentinel(config)
    
    @pytest.mark.asyncio
    async def test_kill_switch_inactive_by_default(self, sentinel):
        """Kill switch inactif par défaut."""
        assert not sentinel.kill_switch.is_active
        
        status = sentinel.get_status()
        assert status["kill_switch_active"] is False
    
    @pytest.mark.asyncio
    async def test_kill_switch_shows_in_status(self, sentinel):
        """Kill switch apparaît dans le status."""
        # Activer le kill switch
        sentinel.kill_switch.activate("Test")
        
        status = sentinel.get_status()
        
        assert status["kill_switch_active"] is True
        assert status["kill_switch_reason"] == "Test"
        
        # Cleanup
        sentinel.kill_switch.deactivate()


class TestMarketSentinelWatchdog:
    """Tests du watchdog intégré."""
    
    @pytest.fixture
    def config(self):
        return MarketConfig(
            mode="paper",
            auto_trade=False,
            scan_interval=10,  # Minimum valide
        )
    
    @pytest.fixture
    def sentinel(self, config):
        return MarketSentinel(config)
    
    @pytest.mark.asyncio
    async def test_watchdog_accessible(self, sentinel):
        """Le watchdog est accessible."""
        assert sentinel.watchdog is not None
    
    @pytest.mark.asyncio
    async def test_update_tick_time(self, sentinel):
        """Mise à jour du tick time."""
        now = datetime.now()
        sentinel.update_tick_time(now)
        
        assert sentinel._last_tick_time == now
    
    @pytest.mark.asyncio
    async def test_update_ibkr_connected(self, sentinel):
        """Mise à jour de la connexion IBKR."""
        sentinel.update_ibkr_connected(True)
        assert sentinel._ibkr_connected is True
        
        sentinel.update_ibkr_connected(False)
        assert sentinel._ibkr_connected is False
    
    @pytest.mark.asyncio
    async def test_update_queue_size(self, sentinel):
        """Mise à jour des tailles de queue."""
        sentinel.update_queue_size("bars", 100)
        sentinel.update_queue_size("ticks", 500)
        
        assert sentinel._queue_sizes["bars"] == 100
        assert sentinel._queue_sizes["ticks"] == 500


class TestMarketReport:
    """Tests du MarketReport."""
    
    def test_report_start_end_cycle(self):
        """Test start/end cycle."""
        report = MarketReport()
        
        report.start_cycle(1)
        assert report._cycle_id == 1
        
        report.end_cycle()
        assert report._cycle_end is not None
    
    def test_report_add_entry(self):
        """Test ajout d'entrée."""
        report = MarketReport()
        report.start_cycle(1)
        
        entry = ReportEntry(
            symbol="AAPL",
            direction="long",
            confidence=0.8,
        )
        report.add_entry(entry)
        
        assert len(report.entries) == 1
        assert report.entries[0].symbol == "AAPL"
    
    def test_report_actionable_entries(self):
        """Test entrées actionnables."""
        report = MarketReport()
        report.start_cycle(1)
        
        # Entrée validée
        entry1 = ReportEntry(symbol="AAPL", direction="long", risk_validated=True)
        # Entrée non validée
        entry2 = ReportEntry(symbol="MSFT", direction="short", risk_validated=False)
        # Entrée neutre
        entry3 = ReportEntry(symbol="GOOGL", direction="neutral", risk_validated=True)
        
        report.add_entry(entry1)
        report.add_entry(entry2)
        report.add_entry(entry3)
        
        actionable = report.actionable_entries
        
        assert len(actionable) == 1
        assert actionable[0].symbol == "AAPL"
    
    def test_report_get_summary(self):
        """Test résumé du rapport."""
        report = MarketReport()
        report.start_cycle(42)
        
        report.add_entry(ReportEntry(symbol="AAPL", direction="long"))
        report.add_entry(ReportEntry(symbol="MSFT", direction="short"))
        
        report.end_cycle()
        
        summary = report.get_summary()
        
        assert summary["cycle_id"] == 42
        assert summary["total_symbols"] == 2
        assert summary["directions"]["long"] == 1
        assert summary["directions"]["short"] == 1
    
    def test_report_to_json(self):
        """Test conversion JSON."""
        report = MarketReport()
        report.start_cycle(1)
        report.add_entry(ReportEntry(symbol="AAPL", direction="long"))
        report.end_cycle()
        
        json_str = report.to_json()
        
        assert "AAPL" in json_str
        assert "long" in json_str
    
    def test_report_get_top_signals(self):
        """Test top signals."""
        report = MarketReport()
        report.start_cycle(1)
        
        report.add_entry(ReportEntry(symbol="AAPL", direction="long", confidence=0.7, risk_validated=True))
        report.add_entry(ReportEntry(symbol="MSFT", direction="short", confidence=0.9, risk_validated=True))
        report.add_entry(ReportEntry(symbol="GOOGL", direction="long", confidence=0.8, risk_validated=True))
        
        top = report.get_top_signals(n=2)
        
        assert len(top) == 2
        assert top[0].symbol == "MSFT"  # Highest confidence
        assert top[1].symbol == "GOOGL"


class TestReportEntry:
    """Tests pour ReportEntry."""
    
    def test_create_basic(self):
        """Création basique."""
        entry = ReportEntry(symbol="AAPL")
        
        assert entry.symbol == "AAPL"
        assert entry.direction == "neutral"
        assert entry.confidence == 0.0
    
    def test_to_dict(self):
        """Conversion en dict."""
        entry = ReportEntry(
            symbol="AAPL",
            direction="long",
            confidence=0.85,
            strength="strong",
            features={"rsi": 65.0},
            grok_analysis="Bullish momentum",
        )
        
        d = entry.to_dict()
        
        assert d["symbol"] == "AAPL"
        assert d["signal"]["direction"] == "long"
        assert d["signal"]["confidence"] == 0.85
        assert d["features"]["rsi"] == 65.0
        assert d["grok"]["analysis"] == "Bullish momentum"
