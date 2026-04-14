"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests FeatureCalculator
==========================
"""

from datetime import datetime, timedelta

import pytest

from src.markets.ibkr.models import Bar
from src.markets.aggregator.features import FeatureCalculator


def make_bar(close: float, volume: int = 100, high: float = None, low: float = None) -> Bar:
    """Helper pour créer des barres de test."""
    if high is None:
        high = close + 0.5
    if low is None:
        low = close - 0.5
    
    return Bar(
        symbol="TEST",
        timestamp=datetime.now(),
        timeframe="1m",
        open=close - 0.1,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def make_bar_series(closes: list, volumes: list = None) -> list:
    """Crée une série de barres à partir de closes."""
    if volumes is None:
        volumes = [100] * len(closes)
    
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    bars = []
    
    for i, (c, v) in enumerate(zip(closes, volumes)):
        bars.append(Bar(
            symbol="TEST",
            timestamp=base_time + timedelta(minutes=i),
            timeframe="1m",
            open=c - 0.1,
            high=c + 0.5,
            low=c - 0.5,
            close=c,
            volume=v,
        ))
    
    return bars


class TestFeatureCalculatorRSI:
    """Tests pour le calcul du RSI."""
    
    def test_rsi_constant_series_is_50(self):
        """RSI sur série constante = 50.0 (le point clé du plan)."""
        calc = FeatureCalculator()
        
        # 20 barres avec le même prix
        bars = make_bar_series([100.0] * 20)
        
        rsi = calc.calculate_rsi(bars, period=14)
        
        assert rsi == 50.0
    
    def test_rsi_uptrend_high(self):
        """RSI sur tendance haussière > 50."""
        calc = FeatureCalculator()
        
        # Prix qui monte régulièrement
        closes = [100 + i for i in range(20)]
        bars = make_bar_series(closes)
        
        rsi = calc.calculate_rsi(bars, period=14)
        
        assert rsi > 70  # Fort RSI en tendance haussière
    
    def test_rsi_downtrend_low(self):
        """RSI sur tendance baissière < 50."""
        calc = FeatureCalculator()
        
        # Prix qui descend régulièrement
        closes = [120 - i for i in range(20)]
        bars = make_bar_series(closes)
        
        rsi = calc.calculate_rsi(bars, period=14)
        
        assert rsi < 30  # Faible RSI en tendance baissière
    
    def test_rsi_insufficient_data(self):
        """RSI avec données insuffisantes = 50."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100, 101, 102])  # Trop peu
        
        rsi = calc.calculate_rsi(bars, period=14)
        
        assert rsi == 50.0  # Valeur neutre
    
    def test_rsi_bounds(self):
        """RSI reste entre 0 et 100."""
        calc = FeatureCalculator()
        
        # Test avec plein de scénarios
        test_cases = [
            [100 + i * 10 for i in range(20)],  # Forte hausse
            [200 - i * 10 for i in range(20)],  # Forte baisse
            [100, 100, 100, 100, 100],          # Plat
        ]
        
        for closes in test_cases:
            bars = make_bar_series(closes)
            rsi = calc.calculate_rsi(bars, period=14)
            assert 0 <= rsi <= 100


class TestFeatureCalculatorATR:
    """Tests pour le calcul de l'ATR."""
    
    def test_atr_basic(self):
        """Test calcul ATR basique."""
        calc = FeatureCalculator()
        
        # Créer des barres avec True Range connu
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        bars = []
        
        for i in range(15):
            bars.append(Bar(
                symbol="TEST",
                timestamp=base_time + timedelta(minutes=i),
                timeframe="1m",
                open=100.0,
                high=101.0,  # Range = 2.0
                low=99.0,
                close=100.0,
                volume=100,
            ))
        
        atr = calc.calculate_atr(bars, period=14)
        
        # True Range constant = 2.0, donc ATR ≈ 2.0
        assert atr == pytest.approx(2.0, abs=0.1)
    
    def test_atr_empty(self):
        """ATR sur liste vide = 0."""
        calc = FeatureCalculator()
        
        atr = calc.calculate_atr([], period=14)
        
        assert atr == 0.0
    
    def test_atr_single_bar(self):
        """ATR sur une seule barre = 0."""
        calc = FeatureCalculator()
        
        bars = [make_bar(100.0)]
        atr = calc.calculate_atr(bars, period=14)
        
        assert atr == 0.0


class TestFeatureCalculatorVWAP:
    """Tests pour le calcul du VWAP."""
    
    def test_vwap_basic(self):
        """Test calcul VWAP basique."""
        calc = FeatureCalculator()
        
        # Barres avec volumes différents
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        bars = [
            Bar(symbol="TEST", timestamp=base_time, timeframe="1m",
                open=99, high=101, low=99, close=100, volume=1000),
            Bar(symbol="TEST", timestamp=base_time + timedelta(minutes=1), timeframe="1m",
                open=100, high=102, low=100, close=101, volume=2000),
        ]
        
        vwap = calc.calculate_vwap(bars)
        
        # Prix typique barre 1: (101+99+100)/3 = 100
        # Prix typique barre 2: (102+100+101)/3 = 101
        # VWAP = (100*1000 + 101*2000) / 3000 = 302000/3000 = 100.67
        assert vwap == pytest.approx(100.67, abs=0.1)
    
    def test_vwap_zero_volume(self):
        """VWAP avec volume 0 = moyenne simple."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100, 102, 104], volumes=[0, 0, 0])
        
        vwap = calc.calculate_vwap(bars)
        
        # Devrait être la moyenne simple des closes
        assert vwap == pytest.approx(102.0, abs=0.1)


class TestFeatureCalculatorVolumeRelative:
    """Tests pour le volume relatif."""
    
    def test_volume_relative_normal(self):
        """Volume relatif = 1 quand volume normal."""
        calc = FeatureCalculator()
        
        # Volumes constants
        bars = make_bar_series([100] * 25, volumes=[1000] * 25)
        
        vol_rel = calc.calculate_volume_relative(bars, avg_period=20)
        
        assert vol_rel == pytest.approx(1.0, abs=0.1)
    
    def test_volume_relative_spike(self):
        """Volume relatif > 1 quand spike."""
        calc = FeatureCalculator()
        
        # Volumes normaux puis spike
        volumes = [1000] * 20 + [3000]  # Spike 3x
        bars = make_bar_series([100] * 21, volumes=volumes)
        
        vol_rel = calc.calculate_volume_relative(bars, avg_period=20)
        
        assert vol_rel == pytest.approx(3.0, abs=0.1)


class TestFeatureCalculatorMomentum:
    """Tests pour le momentum."""
    
    def test_momentum_flat(self):
        """Momentum = 0 sur série plate."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100] * 15)
        
        momentum = calc.calculate_momentum(bars, period=10)
        
        assert momentum == pytest.approx(0.0, abs=0.1)
    
    def test_momentum_up(self):
        """Momentum > 0 sur hausse."""
        calc = FeatureCalculator()
        
        # Prix qui monte de 100 à 110 en 11 barres (+10%)
        closes = [100 + i for i in range(11)]
        bars = make_bar_series(closes)
        
        momentum = calc.calculate_momentum(bars, period=10)
        
        assert momentum == pytest.approx(10.0, abs=0.5)  # +10%
    
    def test_momentum_down(self):
        """Momentum < 0 sur baisse."""
        calc = FeatureCalculator()
        
        # Prix qui descend de 110 à 100 en 11 barres (-9.1%)
        closes = [110 - i for i in range(11)]
        bars = make_bar_series(closes)
        
        momentum = calc.calculate_momentum(bars, period=10)
        
        assert momentum < 0


class TestFeatureCalculatorTrendStrength:
    """Tests pour la force de tendance."""
    
    def test_trend_strength_flat(self):
        """Tendance plate = force 0."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100] * 25)
        
        trend = calc.calculate_trend_strength(bars, period=20)
        
        assert trend == pytest.approx(0.0, abs=0.1)
    
    def test_trend_strength_up(self):
        """Tendance haussière = force > 0."""
        calc = FeatureCalculator()
        
        closes = [100 + i for i in range(25)]
        bars = make_bar_series(closes)
        
        trend = calc.calculate_trend_strength(bars, period=20)
        
        assert trend > 0
    
    def test_trend_strength_bounded(self):
        """Force de tendance entre -1 et +1."""
        calc = FeatureCalculator()
        
        # Test avec différents scénarios
        test_cases = [
            [100 + i * 5 for i in range(25)],   # Forte hausse
            [200 - i * 5 for i in range(25)],   # Forte baisse
            [100, 100, 100, 100, 100],          # Plat
        ]
        
        for closes in test_cases:
            bars = make_bar_series(closes)
            trend = calc.calculate_trend_strength(bars)
            assert -1 <= trend <= 1


class TestFeatureCalculatorCalculate:
    """Tests pour calculate() complet."""
    
    def test_calculate_returns_all_features(self):
        """calculate() retourne toutes les features."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100 + i * 0.5 for i in range(30)])
        
        features = calc.calculate(bars)
        
        # Vérifier que toutes les clés sont présentes
        expected_keys = [
            "rsi_14", "atr_14", "atr_pct", "vwap",
            "vwap_deviation", "volume_rel", "momentum", "trend_strength"
        ]
        for key in expected_keys:
            assert key in features
    
    def test_calculate_empty_returns_defaults(self):
        """calculate() sur vide retourne valeurs par défaut."""
        calc = FeatureCalculator()
        
        features = calc.calculate([])
        
        assert features["rsi_14"] == 50.0
        assert features["atr_14"] == 0.0
        assert features["volume_rel"] == 1.0
    
    def test_calculate_feature_vector(self):
        """Test calculate_feature_vector."""
        calc = FeatureCalculator()
        
        bars = make_bar_series([100 + i for i in range(20)])
        
        fv = calc.calculate_feature_vector(bars)
        
        assert fv.symbol == "TEST"
        assert fv.rsi_14 > 50  # Tendance haussière
        
        # to_dict doit fonctionner
        d = fv.to_dict()
        assert "rsi_14" in d
