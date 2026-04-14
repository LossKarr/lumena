"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Market Watchdog
========================
"""

from datetime import datetime, timedelta

import pytest

from src.markets.health.status import HealthLevel, HealthStatus, MarketHealthStatus
from src.markets.health.watchdog import MarketWatchdog


class TestHealthStatus:
    """Tests pour HealthStatus."""
    
    def test_create_healthy(self):
        """Création d'un statut healthy."""
        status = HealthStatus(
            component="test",
            level=HealthLevel.HEALTHY,
            message="All good",
        )
        
        assert status.is_healthy
        assert not status.is_critical
    
    def test_create_unhealthy(self):
        """Création d'un statut unhealthy."""
        status = HealthStatus(
            component="test",
            level=HealthLevel.UNHEALTHY,
            message="Problem",
        )
        
        assert not status.is_healthy
        assert status.is_critical
    
    def test_to_dict(self):
        """Conversion en dictionnaire."""
        status = HealthStatus(
            component="feed",
            level=HealthLevel.DEGRADED,
            message="Slow",
            details={"latency": 100},
        )
        
        d = status.to_dict()
        
        assert d["component"] == "feed"
        assert d["level"] == "degraded"
        assert d["details"]["latency"] == 100


class TestMarketHealthStatus:
    """Tests pour MarketHealthStatus."""
    
    def test_overall_level_empty(self):
        """Niveau global sur statut vide."""
        status = MarketHealthStatus()
        
        assert status.overall_level == HealthLevel.UNKNOWN
    
    def test_overall_level_all_healthy(self):
        """Niveau global si tous healthy."""
        status = MarketHealthStatus()
        status.add_status(HealthStatus("a", HealthLevel.HEALTHY))
        status.add_status(HealthStatus("b", HealthLevel.HEALTHY))
        
        assert status.overall_level == HealthLevel.HEALTHY
        assert status.is_operational
    
    def test_overall_level_one_unhealthy(self):
        """Niveau global si un unhealthy."""
        status = MarketHealthStatus()
        status.add_status(HealthStatus("a", HealthLevel.HEALTHY))
        status.add_status(HealthStatus("b", HealthLevel.UNHEALTHY))
        
        assert status.overall_level == HealthLevel.UNHEALTHY
        assert not status.is_operational
    
    def test_overall_level_one_degraded(self):
        """Niveau global si un degraded."""
        status = MarketHealthStatus()
        status.add_status(HealthStatus("a", HealthLevel.HEALTHY))
        status.add_status(HealthStatus("b", HealthLevel.DEGRADED))
        
        assert status.overall_level == HealthLevel.DEGRADED
        assert status.is_operational
    
    def test_add_status_replaces(self):
        """add_status remplace le composant existant."""
        status = MarketHealthStatus()
        status.add_status(HealthStatus("a", HealthLevel.HEALTHY))
        status.add_status(HealthStatus("a", HealthLevel.UNHEALTHY))
        
        assert len(status.components) == 1
        assert status.components[0].level == HealthLevel.UNHEALTHY
    
    def test_get_component(self):
        """Récupère un composant par nom."""
        status = MarketHealthStatus()
        status.add_status(HealthStatus("feed", HealthLevel.HEALTHY))
        status.add_status(HealthStatus("grok", HealthLevel.DEGRADED))
        
        feed = status.get_component("feed")
        grok = status.get_component("grok")
        unknown = status.get_component("unknown")
        
        assert feed is not None
        assert feed.level == HealthLevel.HEALTHY
        assert grok.level == HealthLevel.DEGRADED
        assert unknown is None
    
    def test_increment_error(self):
        """Incrémente les erreurs par catégorie."""
        status = MarketHealthStatus()
        
        status.increment_error("network")
        status.increment_error("network")
        status.increment_error("timeout")
        
        assert status.errors_by_category["network"] == 2
        assert status.errors_by_category["timeout"] == 1
    
    def test_to_dict(self):
        """Conversion en dictionnaire."""
        status = MarketHealthStatus()
        status.cycle_count = 100
        status.uptime_seconds = 3600.0
        status.add_status(HealthStatus("test", HealthLevel.HEALTHY))
        
        d = status.to_dict()
        
        assert d["overall_level"] == "healthy"
        assert d["cycle_count"] == 100
        assert len(d["components"]) == 1


class TestMarketWatchdog:
    """Tests pour MarketWatchdog."""
    
    @pytest.fixture
    def watchdog(self):
        """Crée un watchdog pour les tests."""
        return MarketWatchdog()
    
    def test_default_thresholds(self, watchdog):
        """Vérifie les seuils par défaut."""
        assert watchdog.STALE_FEED_THRESHOLD_S == 60.0
        assert watchdog.MAX_QUEUE_SIZE == 10000
        assert watchdog.MAX_ERROR_RATE == 0.5
    
    def test_error_rate_empty(self, watchdog):
        """Taux d'erreurs sur historique vide."""
        assert watchdog.get_error_rate() == 0.0
    
    def test_error_rate_tracking(self, watchdog):
        """Suivi du taux d'erreurs."""
        # 3 succès, 2 erreurs = 40% d'erreurs
        watchdog.record_cycle_result(success=True)
        watchdog.record_cycle_result(success=True)
        watchdog.record_cycle_result(success=True)
        watchdog.record_cycle_result(success=False)
        watchdog.record_cycle_result(success=False)
        
        assert watchdog.get_error_rate() == pytest.approx(0.4, rel=0.01)
    
    @pytest.mark.asyncio
    async def test_check_feed_no_callback(self, watchdog):
        """Check feed sans callback configuré."""
        statuses = await watchdog.check_all()
        
        feed_status = next(s for s in statuses if s.component == "feed")
        assert feed_status.level == HealthLevel.UNKNOWN
    
    @pytest.mark.asyncio
    async def test_check_feed_no_tick(self, watchdog):
        """Check feed sans tick reçu."""
        watchdog.set_feed_callback(lambda: None)
        
        statuses = await watchdog.check_all()
        
        feed_status = next(s for s in statuses if s.component == "feed")
        assert feed_status.level == HealthLevel.DEGRADED
    
    @pytest.mark.asyncio
    async def test_check_feed_fresh(self, watchdog):
        """Check feed avec tick récent."""
        recent = datetime.now() - timedelta(seconds=10)
        watchdog.set_feed_callback(lambda: recent)
        
        statuses = await watchdog.check_all()
        
        feed_status = next(s for s in statuses if s.component == "feed")
        assert feed_status.level == HealthLevel.HEALTHY
    
    @pytest.mark.asyncio
    async def test_check_feed_stale(self, watchdog):
        """Check feed avec tick stale (> 60s)."""
        old = datetime.now() - timedelta(seconds=120)
        watchdog.set_feed_callback(lambda: old)
        
        statuses = await watchdog.check_all()
        
        feed_status = next(s for s in statuses if s.component == "feed")
        assert feed_status.level == HealthLevel.UNHEALTHY
        assert "stale" in feed_status.message.lower()
    
    @pytest.mark.asyncio
    async def test_check_queue_overflow(self, watchdog):
        """Check queue avec overflow."""
        watchdog.set_queue_sizes_callback(lambda: {"bars": 15000})  # > 10000
        
        statuses = await watchdog.check_all()
        
        queue_status = next(s for s in statuses if s.component == "queues")
        assert queue_status.level == HealthLevel.UNHEALTHY
        assert "overflow" in queue_status.message.lower()
    
    @pytest.mark.asyncio
    async def test_check_queue_ok(self, watchdog):
        """Check queue normale."""
        watchdog.set_queue_sizes_callback(lambda: {"bars": 100, "ticks": 500})
        
        statuses = await watchdog.check_all()
        
        queue_status = next(s for s in statuses if s.component == "queues")
        assert queue_status.level == HealthLevel.HEALTHY
    
    @pytest.mark.asyncio
    async def test_check_ibkr_connected(self, watchdog):
        """Check IBKR connecté."""
        watchdog.set_ibkr_connected_callback(lambda: True)
        
        statuses = await watchdog.check_all()
        
        ibkr_status = next(s for s in statuses if s.component == "ibkr")
        assert ibkr_status.level == HealthLevel.HEALTHY
    
    @pytest.mark.asyncio
    async def test_check_ibkr_disconnected(self, watchdog):
        """Check IBKR déconnecté."""
        watchdog.set_ibkr_connected_callback(lambda: False)
        
        statuses = await watchdog.check_all()
        
        ibkr_status = next(s for s in statuses if s.component == "ibkr")
        assert ibkr_status.level == HealthLevel.UNHEALTHY
    
    @pytest.mark.asyncio
    async def test_check_error_rate_high(self, watchdog):
        """Check taux d'erreurs élevé."""
        # 60% d'erreurs (6 erreurs sur 10)
        for _ in range(4):
            watchdog.record_cycle_result(success=True)
        for _ in range(6):
            watchdog.record_cycle_result(success=False)
        
        statuses = await watchdog.check_all()
        
        error_status = next(s for s in statuses if s.component == "errors")
        assert error_status.level == HealthLevel.UNHEALTHY
    
    def test_get_metrics(self, watchdog):
        """Test get_metrics."""
        watchdog.record_cycle_result(success=True)
        metrics = watchdog.get_metrics()
        
        assert "error_rate" in metrics
        assert "thresholds" in metrics
        assert metrics["thresholds"]["stale_feed_s"] == 60.0
