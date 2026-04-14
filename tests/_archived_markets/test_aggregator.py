"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests TimeframeAggregator
============================
"""

from datetime import datetime, timedelta

import pytest

from src.markets.ibkr.models import Bar
from src.markets.aggregator.timeframe import TimeframeAggregator, TIMEFRAME_MINUTES


def make_bar(symbol: str, timestamp: datetime, close: float, volume: int = 100) -> Bar:
    """Helper pour créer des barres de test."""
    return Bar(
        symbol=symbol,
        timestamp=timestamp,
        timeframe="1m",
        open=close - 0.5,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=volume,
    )


class TestTimeframeAggregatorInit:
    """Tests d'initialisation."""
    
    def test_default_init(self):
        """Test initialisation par défaut."""
        agg = TimeframeAggregator()
        
        assert "1m" in agg._timeframes
        assert "5m" in agg._timeframes
        assert "15m" in agg._timeframes
        assert "1h" in agg._timeframes
    
    def test_custom_timeframes(self):
        """Test avec timeframes personnalisés."""
        agg = TimeframeAggregator(timeframes=["1m", "5m"])
        
        assert agg._timeframes == ["1m", "5m"]
    
    def test_invalid_timeframe_raises(self):
        """Timeframe invalide lève une erreur."""
        with pytest.raises(ValueError) as exc_info:
            TimeframeAggregator(timeframes=["1m", "3m"])  # 3m n'existe pas
        
        assert "Timeframe inconnu" in str(exc_info.value)


class TestTimeframeAggregatorIngest:
    """Tests d'ingestion de barres."""
    
    def test_ingest_single_bar(self):
        """Ingérer une seule barre 1m."""
        agg = TimeframeAggregator(timeframes=["1m", "5m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        bar = make_bar("AAPL", base_time, 150.0)
        completed = agg.ingest_bar(bar)
        
        # 1m devrait être complète immédiatement
        assert completed["1m"] is not None
        assert completed["1m"].symbol == "AAPL"
        assert completed["1m"].close == 150.0
        
        # 5m ne devrait pas être complète
        assert completed["5m"] is None
    
    def test_aggregate_5_bars_to_5m(self):
        """5 barres 1m → 1 barre 5m."""
        agg = TimeframeAggregator(timeframes=["1m", "5m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Insérer 5 barres (10:00 à 10:04)
        for i in range(5):
            bar = make_bar("AAPL", base_time + timedelta(minutes=i), 150.0 + i)
            completed = agg.ingest_bar(bar)
        
        # La 5m ne sera pas encore complète car on n'a pas de barre suivante
        assert completed["5m"] is None
        
        # Insérer une 6ème barre (10:05) pour déclencher la complétion
        bar6 = make_bar("AAPL", base_time + timedelta(minutes=5), 155.0)
        completed = agg.ingest_bar(bar6)
        
        # Maintenant la 5m 10:00-10:05 devrait être complète
        assert completed["5m"] is not None
        assert completed["5m"].open == 149.5  # Premier open (close=150 -> open=149.5)
        assert completed["5m"].close == 154.0  # Dernier close avant la 6ème
    
    def test_ohlcv_aggregation(self):
        """Vérifie le calcul OHLCV correct."""
        agg = TimeframeAggregator(timeframes=["1m", "5m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Créer des barres avec des valeurs spécifiques
        bars_data = [
            (0, 100.0, 102.0, 99.0, 101.0, 1000),   # open, high, low, close, volume
            (1, 101.0, 105.0, 100.0, 104.0, 1500),
            (2, 104.0, 106.0, 103.0, 105.0, 2000),
            (3, 105.0, 105.5, 102.0, 103.0, 1800),
            (4, 103.0, 104.0, 101.0, 102.0, 1200),
        ]
        
        for i, (offset, o, h, l, c, v) in enumerate(bars_data):
            bar = Bar(
                symbol="AAPL",
                timestamp=base_time + timedelta(minutes=offset),
                timeframe="1m",
                open=o, high=h, low=l, close=c, volume=v
            )
            agg.ingest_bar(bar)
        
        # Déclencher complétion avec barre suivante
        trigger_bar = make_bar("AAPL", base_time + timedelta(minutes=5), 102.0)
        completed = agg.ingest_bar(trigger_bar)
        
        bar_5m = completed["5m"]
        assert bar_5m is not None
        
        # Vérifier OHLCV
        assert bar_5m.open == 100.0    # Premier open
        assert bar_5m.high == 106.0    # Max des highs
        assert bar_5m.low == 99.0      # Min des lows
        assert bar_5m.close == 102.0   # Dernier close
        assert bar_5m.volume == 7500   # Somme des volumes

    def test_15m_not_lost_after_5m_cleanup(self):
        """Une 15m doit se compléter même si 5m est aussi active."""
        agg = TimeframeAggregator(timeframes=["1m", "5m", "15m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)

        completed = None
        for i in range(16):  # 10:00 -> 10:15 (barre de trigger incluse)
            bar = make_bar("AAPL", base_time + timedelta(minutes=i), 150.0 + i)
            completed = agg.ingest_bar(bar)

        assert completed is not None
        assert completed["15m"] is not None
        assert completed["15m"].open == 149.5
        assert completed["15m"].close == 164.0


class TestTimeframeAggregatorHistory:
    """Tests de l'historique."""
    
    def test_get_latest(self):
        """Test get_latest."""
        agg = TimeframeAggregator(timeframes=["1m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Pas de barre
        assert agg.get_latest("AAPL", "1m") is None
        
        # Ajouter des barres
        for i in range(5):
            bar = make_bar("AAPL", base_time + timedelta(minutes=i), 150.0 + i)
            agg.ingest_bar(bar)
        
        latest = agg.get_latest("AAPL", "1m")
        assert latest is not None
        assert latest.close == 154.0  # Dernière barre
    
    def test_get_history(self):
        """Test get_history."""
        agg = TimeframeAggregator(timeframes=["1m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Ajouter 10 barres
        for i in range(10):
            bar = make_bar("AAPL", base_time + timedelta(minutes=i), 150.0 + i)
            agg.ingest_bar(bar)
        
        # Récupérer les 5 dernières
        history = agg.get_history("AAPL", "1m", count=5)
        
        assert len(history) == 5
        assert history[0].close == 155.0  # 6ème barre (index 5)
        assert history[-1].close == 159.0  # 10ème barre
    
    def test_history_bounded_by_maxlen(self):
        """Vérifie que l'historique est borné."""
        agg = TimeframeAggregator(timeframes=["1m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Ajouter plus de 500 barres
        for i in range(600):
            bar = make_bar("AAPL", base_time + timedelta(minutes=i), 150.0 + i % 10)
            agg.ingest_bar(bar)
        
        history = agg.get_history("AAPL", "1m", count=1000)
        
        # Devrait être limité à 500
        assert len(history) == 500


class TestTimeframeAggregatorMultiSymbol:
    """Tests multi-symboles."""
    
    def test_independent_symbols(self):
        """Chaque symbole a son propre buffer."""
        agg = TimeframeAggregator(timeframes=["1m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Ajouter des barres pour AAPL et MSFT
        agg.ingest_bar(make_bar("AAPL", base_time, 150.0))
        agg.ingest_bar(make_bar("MSFT", base_time, 380.0))
        
        aapl_latest = agg.get_latest("AAPL", "1m")
        msft_latest = agg.get_latest("MSFT", "1m")
        
        assert aapl_latest.close == 150.0
        assert msft_latest.close == 380.0
    
    def test_get_symbols(self):
        """Test get_symbols."""
        agg = TimeframeAggregator()
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        agg.ingest_bar(make_bar("AAPL", base_time, 150.0))
        agg.ingest_bar(make_bar("MSFT", base_time, 380.0))
        agg.ingest_bar(make_bar("GOOGL", base_time, 140.0))
        
        symbols = agg.get_symbols()
        
        assert set(symbols) == {"AAPL", "MSFT", "GOOGL"}


class TestTimeframeAggregatorStats:
    """Tests des statistiques."""
    
    def test_get_stats(self):
        """Test get_stats."""
        agg = TimeframeAggregator(timeframes=["1m", "5m"])
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        for i in range(10):
            agg.ingest_bar(make_bar("AAPL", base_time + timedelta(minutes=i), 150.0))
        
        stats = agg.get_stats()
        
        assert stats["timeframes"] == ["1m", "5m"]
        assert stats["symbols_count"] == 1
        assert "AAPL" in stats["symbols"]
        assert stats["symbols"]["AAPL"]["history"]["1m"] == 10
    
    def test_clear_all(self):
        """Test clear() sans argument."""
        agg = TimeframeAggregator()
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        agg.ingest_bar(make_bar("AAPL", base_time, 150.0))
        agg.ingest_bar(make_bar("MSFT", base_time, 380.0))
        
        agg.clear()
        
        assert len(agg.get_symbols()) == 0
    
    def test_clear_single_symbol(self):
        """Test clear() avec symbole spécifique."""
        agg = TimeframeAggregator()
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        agg.ingest_bar(make_bar("AAPL", base_time, 150.0))
        agg.ingest_bar(make_bar("MSFT", base_time, 380.0))
        
        agg.clear("AAPL")
        
        symbols = agg.get_symbols()
        assert "MSFT" in symbols
        assert "AAPL" not in symbols
