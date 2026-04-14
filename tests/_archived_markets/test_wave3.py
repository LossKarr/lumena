"""
Tests pour les modules Wave 3.

Modules testés:
- Walk-Forward Backtesting Engine (Phase 2)
- Market Simulator (Phase 2)
- Sentiment Analyzer (Phase 3)
- Headline Collector (Phase 3)
- Strategy Switcher (Phase 4)
"""

import pytest
import os
import tempfile


# ============================================================================
# TESTS: BacktestEngine (Phase 2)
# ============================================================================

class TestBacktestEngine:
    """Tests pour le moteur walk-forward."""
    
    def test_import(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine()
        assert engine is not None
    
    def test_empty_trades(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine()
        result = engine.walk_forward(trades=[], total_bars=100)
        assert result.window_count == 0
        assert result.is_robust is False
    
    def test_create_windows(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine(train_ratio=0.7)
        windows = engine._create_windows(total_bars=1000, num_windows=5)
        assert len(windows) == 5
        # Vérifier que train < test pour chaque fenêtre
        for train_start, train_end, test_start, test_end in windows:
            assert train_start < train_end
            assert train_end == test_start
            assert test_start < test_end
    
    def test_windows_cover_full_range(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine()
        windows = engine._create_windows(total_bars=100, num_windows=5)
        # Les fenêtres couvrent 0 à 100
        assert windows[0][0] == 0
        assert windows[-1][3] <= 100
    
    def test_compute_metrics_winners(self):
        from src.markets.backtesting.engine import BacktestEngine, BacktestTrade
        engine = BacktestEngine()
        trades = [
            BacktestTrade("AAPL", "long", 100, 105, 0, 10),
            BacktestTrade("AAPL", "long", 100, 103, 10, 20),
        ]
        metrics = engine._compute_metrics(trades)
        assert metrics["win_rate"] == 1.0
        assert metrics["avg_pnl"] > 0
    
    def test_compute_metrics_losers(self):
        from src.markets.backtesting.engine import BacktestEngine, BacktestTrade
        engine = BacktestEngine()
        trades = [
            BacktestTrade("AAPL", "long", 100, 95, 0, 10),
            BacktestTrade("AAPL", "long", 100, 97, 10, 20),
        ]
        metrics = engine._compute_metrics(trades)
        assert metrics["win_rate"] == 0.0
        assert metrics["avg_pnl"] < 0
    
    def test_walk_forward_with_trades(self):
        from src.markets.backtesting.engine import BacktestEngine, BacktestTrade
        engine = BacktestEngine()
        
        # Créer des trades distribués sur 500 barres
        trades = []
        for i in range(50):
            bar = i * 10
            pnl = 2.0 if i % 3 != 0 else -1.0  # ~67% WR
            exit_p = 100 * (1 + pnl / 100)
            trades.append(BacktestTrade(
                symbol="SIM", direction="long",
                entry_price=100, exit_price=exit_p,
                entry_bar=bar, exit_bar=bar + 5,
            ))
        
        result = engine.walk_forward(trades, total_bars=500, num_windows=5)
        assert result.window_count == 5
        assert result.total_test_trades > 0
        assert result.robustness_score >= 0
    
    def test_backtest_trade_pnl(self):
        from src.markets.backtesting.engine import BacktestTrade
        
        # Long gagnant
        t = BacktestTrade("AAPL", "long", 100, 110, 0, 10)
        assert t.pnl_pct == 10.0
        assert t.is_winner is True
        
        # Short gagnant
        t2 = BacktestTrade("AAPL", "short", 100, 90, 0, 10)
        assert t2.pnl_pct == 10.0
        assert t2.is_winner is True
        
        # Long perdant
        t3 = BacktestTrade("AAPL", "long", 100, 95, 0, 10)
        assert t3.pnl_pct == -5.0
        assert t3.is_winner is False
    
    def test_max_drawdown(self):
        from src.markets.backtesting.engine import BacktestEngine, BacktestTrade
        engine = BacktestEngine()
        # 3 pertes consécutives
        trades = [
            BacktestTrade("SIM", "long", 100, 95, 0, 5),
            BacktestTrade("SIM", "long", 100, 95, 5, 10),
            BacktestTrade("SIM", "long", 100, 95, 10, 15),
        ]
        dd = engine._max_drawdown(trades)
        assert dd > 0
    
    def test_robustness_score_range(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine()
        score = engine._compute_robustness_score(
            efficiency=0.8, test_wr=0.65, test_pnl=0.5,
            max_dd=3.0, total_trades=30
        )
        assert 0 <= score <= 100
    
    def test_to_dict(self):
        from src.markets.backtesting.engine import BacktestEngine
        engine = BacktestEngine()
        result = engine.walk_forward(trades=[], total_bars=100)
        d = result.to_dict()
        assert "robustness_score" in d
        assert "is_robust" in d
        assert "avg_efficiency" in d


# ============================================================================
# TESTS: MarketSimulator (Phase 2)
# ============================================================================

class TestMarketSimulator:
    """Tests pour le simulateur de marché."""
    
    def test_import(self):
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        assert sim is not None
    
    def test_generate_bars(self):
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        bars = sim.generate_bars(num_bars=100, seed=42)
        assert len(bars) == 100
        assert bars[0].open > 0
        assert bars[0].high >= bars[0].low
    
    def test_bars_reproducible_with_seed(self):
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        bars1 = sim.generate_bars(num_bars=50, seed=123)
        bars2 = sim.generate_bars(num_bars=50, seed=123)
        assert bars1[0].close == bars2[0].close
        assert bars1[10].close == bars2[10].close
    
    def test_bar_properties(self):
        from src.markets.backtesting.simulator import SimulatedBar
        bar = SimulatedBar(index=0, open=100, high=105, low=98, close=103)
        assert bar.change_pct == 3.0
        assert bar.range_pct == pytest.approx(7.0)
        assert bar.body_pct == 3.0
    
    def test_simulate_momentum(self):
        from src.markets.backtesting.simulator import MarketSimulator, SimulationConfig
        sim = MarketSimulator()
        bars = sim.generate_bars(num_bars=200, seed=42, trend=0.05, volatility=1.0)
        config = SimulationConfig(initial_capital=100000)
        result = sim.simulate_momentum(bars, config=config, lookback=5)
        assert result.total_trades > 0
        assert 0 <= result.win_rate <= 1.0
    
    def test_simulation_result_to_dict(self):
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        bars = sim.generate_bars(num_bars=100, seed=42)
        result = sim.simulate_momentum(bars)
        d = result.to_dict()
        assert "win_rate" in d
        assert "sharpe_ratio" in d
        assert "max_drawdown_pct" in d
    
    def test_no_trades_on_short_data(self):
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        bars = sim.generate_bars(num_bars=3, seed=42)
        result = sim.simulate_momentum(bars, lookback=5)
        # Pas assez de barres pour le lookback
        assert result.total_trades == 0
    
    def test_trend_affects_direction(self):
        """Un fort drift positif devrait produire plus de longs gagnants."""
        from src.markets.backtesting.simulator import MarketSimulator
        sim = MarketSimulator()
        bars = sim.generate_bars(num_bars=500, seed=42, trend=0.10, volatility=0.5)
        result = sim.simulate_momentum(bars)
        # Avec trend positif fort et faible volatilité, on devrait gagner
        if result.total_trades > 5:
            assert result.total_pnl_pct > -50  # Pas de perte catastrophique


# ============================================================================
# TESTS: SentimentAnalyzer (Phase 3)
# ============================================================================

class TestSentimentAnalyzer:
    """Tests pour l'analyseur de sentiment."""
    
    def test_import(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        assert analyzer is not None
    
    def test_bullish_headline(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("AAPL beats earnings expectations with record revenue")
        assert score.score > 0
        assert score.label == "BULLISH"
        assert len(score.bullish_matches) > 0
    
    def test_bearish_headline(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("Stock crashes on fraud investigation, massive selloff")
        assert score.score < 0
        assert score.label == "BEARISH"
        assert len(score.bearish_matches) > 0
    
    def test_neutral_headline(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("Company announces quarterly meeting next week")
        assert score.label == "NEUTRAL"
    
    def test_empty_text(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("")
        assert score.score == 0.0
        assert score.label == "NEUTRAL"
    
    def test_negation(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        positive = analyzer.analyze("Stock rallied significantly")
        negated = analyzer.analyze("Stock not rallied")
        # Le score négaté devrait être inférieur au positif
        assert negated.score < positive.score
    
    def test_aggregate(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        agg = analyzer.aggregate([
            "AAPL beats earnings with record revenue growth",
            "Tech stocks surge on strong demand",
            "Minor concerns about rate hike",
        ])
        # Globalement positif (2 bullish, 1 légèrement bearish)
        assert agg.num_sources == 3
        assert agg.avg_score > -1.0  # Pas complètement bearish
    
    def test_aggregate_empty(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        agg = analyzer.aggregate([])
        assert agg.num_sources == 0
        assert agg.label == "NEUTRAL"
    
    def test_to_dict(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("Stock rallied")
        d = score.to_dict()
        assert "score" in d
        assert "label" in d
        assert "magnitude" in d
    
    def test_get_prompt_context(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        ctx = analyzer.get_prompt_context(
            ["AAPL beats expectations", "Revenue growth strong"],
            symbol="AAPL"
        )
        assert "Sentiment" in ctx
        assert "AAPL" in ctx
    
    def test_get_prompt_context_empty(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        ctx = analyzer.get_prompt_context([])
        assert "pas de données" in ctx
    
    def test_intensifier(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        normal = analyzer.analyze("Stock rallied")
        intense = analyzer.analyze("Stock extremely rallied")
        # L'intensificateur devrait augmenter le score
        assert intense.score >= normal.score
    
    def test_multiword_match(self):
        from src.markets.sentiment.analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        score = analyzer.analyze("Company earnings beat estimates this quarter")
        assert len(score.bullish_matches) > 0


# ============================================================================
# TESTS: HeadlineCollector (Phase 3)
# ============================================================================

class TestHeadlineCollector:
    """Tests pour le collecteur de headlines."""
    
    def test_import(self):
        from src.markets.sentiment.headlines import HeadlineCollector
        collector = HeadlineCollector()
        assert collector is not None
    
    def test_no_headlines_without_api(self):
        from src.markets.sentiment.headlines import HeadlineCollector
        collector = HeadlineCollector()
        headlines = collector.get_headlines("AAPL")
        assert headlines == []
    
    def test_inject_and_retrieve(self):
        from src.markets.sentiment.headlines import HeadlineCollector, Headline
        collector = HeadlineCollector()
        collector.inject_headlines("AAPL", [
            Headline(symbol="AAPL", text="AAPL beats earnings"),
            Headline(symbol="AAPL", text="AAPL revenue up 20%"),
        ])
        headlines = collector.get_headlines("AAPL")
        assert len(headlines) == 2
    
    def test_max_count(self):
        from src.markets.sentiment.headlines import HeadlineCollector, Headline
        collector = HeadlineCollector()
        collector.inject_headlines("AAPL", [
            Headline(symbol="AAPL", text=f"News {i}") for i in range(10)
        ])
        headlines = collector.get_headlines("AAPL", max_count=3)
        assert len(headlines) == 3
    
    def test_clear_cache(self):
        from src.markets.sentiment.headlines import HeadlineCollector, Headline
        collector = HeadlineCollector()
        collector.inject_headlines("AAPL", [
            Headline(symbol="AAPL", text="Test")
        ])
        collector.clear_cache()
        assert collector.get_headlines("AAPL") == []


# ============================================================================
# TESTS: StrategySwitcher (Phase 4)
# ============================================================================

class TestStrategySwitcher:
    """Tests pour le strategy switcher adaptatif."""
    
    def test_import(self):
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        assert switcher is not None
    
    def test_new_strategy_can_trade(self):
        """Pas assez de données → laisser passer."""
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        decision = switcher.get_decision("AGGRESSIVE", "trending_bull")
        assert decision.can_trade is True
        assert decision.weight == 1.0
    
    def test_consecutive_losses_disable(self):
        """5 pertes consécutives → désactiver."""
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        for i in range(6):
            switcher.record_outcome("AGGRESSIVE", "volatile", pnl_pct=-1.0)
        
        decision = switcher.get_decision("AGGRESSIVE", "volatile")
        assert decision.can_trade is False
        assert "Désactivée" in decision.reason
    
    def test_high_win_rate_boost(self):
        """WR > 60% → boost de poids."""
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        # 8 victoires, 2 défaites = 80% WR
        for i in range(8):
            switcher.record_outcome("NEUTRAL", "trending_bull", pnl_pct=1.5)
        for i in range(2):
            switcher.record_outcome("NEUTRAL", "trending_bull", pnl_pct=-0.5)
        
        decision = switcher.get_decision("NEUTRAL", "trending_bull")
        assert decision.can_trade is True
        assert decision.weight >= 1.5
    
    def test_low_win_rate_penalty(self):
        """WR < 40% → pénalité de poids."""
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        # 2 victoires, 5 défaites = 29% WR (pas consécutives)
        for i in range(7):
            pnl = 1.0 if i < 2 else -0.5
            switcher.record_outcome("NEWS", "ranging", pnl_pct=pnl)
        
        decision = switcher.get_decision("NEWS", "ranging")
        if decision.can_trade:
            assert decision.weight <= 1.0
    
    def test_get_active_strategies(self):
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        active = switcher.get_active_strategies("trending_bull")
        assert len(active) > 0
        # Toutes les stratégies devraient être actives au départ
        assert len(active) == 4
    
    def test_get_performance_matrix(self):
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        matrix = switcher.get_performance_matrix()
        assert "NEUTRAL" in matrix
        assert "trending_bull" in matrix["NEUTRAL"]
    
    def test_persistence_save_load(self):
        from src.markets.strategy.switcher import StrategySwitcher
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "switcher_state.json")
            
            # Enregistrer des résultats
            s1 = StrategySwitcher(persistence_path=path)
            for i in range(5):
                s1.record_outcome("AGGRESSIVE", "volatile", pnl_pct=-1.0)
            
            # Charger
            s2 = StrategySwitcher(persistence_path=path)
            perf = s2._get_performance("AGGRESSIVE", "volatile")
            assert perf.losses == 5
    
    def test_cool_down_reactivation(self):
        """Après cool-down, la stratégie devrait se réactiver."""
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        switcher.COOL_DOWN_TRADES = 3  # Réduire pour le test
        
        # Désactiver
        for i in range(6):
            switcher.record_outcome("AGGRESSIVE", "volatile", pnl_pct=-1.0)
        
        decision = switcher.get_decision("AGGRESSIVE", "volatile")
        assert decision.can_trade is False
        
        # Simuler des trades globaux dans le régime (cool-down)
        switcher._global_trades_by_regime["volatile"] = 5
        
        decision2 = switcher.get_decision("AGGRESSIVE", "volatile")
        assert decision2.can_trade is True  # Réactivée !
    
    def test_decision_to_dict(self):
        from src.markets.strategy.switcher import StrategySwitcher
        switcher = StrategySwitcher()
        decision = switcher.get_decision("NEUTRAL", "trending_bull")
        d = decision.to_dict()
        assert "strategy" in d
        assert "regime" in d
        assert "can_trade" in d
        assert "weight" in d
    
    def test_performance_to_dict(self):
        from src.markets.strategy.switcher import StrategyPerformance
        perf = StrategyPerformance(wins=10, losses=5)
        d = perf.to_dict()
        assert d["win_rate"] == round(10/15, 3)
        assert d["total_trades"] == 15
