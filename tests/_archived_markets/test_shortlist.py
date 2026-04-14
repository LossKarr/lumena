"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests ShortlistRanker
========================
"""

import pytest

from src.markets.aggregator.shortlist import ShortlistRanker, ScoredSymbol, ScoringWeights


class TestScoredSymbol:
    """Tests pour ScoredSymbol."""
    
    def test_ordering_by_score(self):
        """Tri par score décroissant."""
        s1 = ScoredSymbol(symbol="AAPL", score=0.8)
        s2 = ScoredSymbol(symbol="MSFT", score=0.9)
        s3 = ScoredSymbol(symbol="GOOGL", score=0.7)
        
        sorted_list = sorted([s1, s2, s3])
        
        assert sorted_list[0].symbol == "MSFT"  # Score 0.9
        assert sorted_list[1].symbol == "AAPL"  # Score 0.8
        assert sorted_list[2].symbol == "GOOGL"  # Score 0.7
    
    def test_ordering_tiebreaker_alphabetic(self):
        """Égalité de score → tri alphabétique."""
        s1 = ScoredSymbol(symbol="MSFT", score=0.8)
        s2 = ScoredSymbol(symbol="AAPL", score=0.8)
        s3 = ScoredSymbol(symbol="GOOGL", score=0.8)
        
        sorted_list = sorted([s1, s2, s3])
        
        # Même score → alphabétique
        assert sorted_list[0].symbol == "AAPL"
        assert sorted_list[1].symbol == "GOOGL"
        assert sorted_list[2].symbol == "MSFT"


class TestScoringWeights:
    """Tests pour ScoringWeights."""
    
    def test_default_weights(self):
        """Poids par défaut normalisés à 1."""
        weights = ScoringWeights()
        normalized = weights.to_dict()
        
        total = sum(normalized.values())
        assert total == pytest.approx(1.0, abs=0.01)
    
    def test_custom_weights_normalized(self):
        """Poids personnalisés sont normalisés."""
        weights = ScoringWeights(
            rsi_extremes=1.0,
            volume_spike=1.0,
            momentum=1.0,
            trend_strength=1.0,
            volatility=1.0,
        )
        normalized = weights.to_dict()
        
        total = sum(normalized.values())
        assert total == pytest.approx(1.0, abs=0.01)
        
        # Tous égaux = 0.2 chacun
        for value in normalized.values():
            assert value == pytest.approx(0.2, abs=0.01)


class TestShortlistRankerInit:
    """Tests d'initialisation."""
    
    def test_default_init(self):
        """Test initialisation par défaut."""
        ranker = ShortlistRanker()
        
        assert ranker.max_shortlist == 10
    
    def test_custom_init(self):
        """Test initialisation personnalisée."""
        ranker = ShortlistRanker(max_shortlist=5)
        
        assert ranker.max_shortlist == 5
    
    def test_invalid_max_shortlist(self):
        """max_shortlist < 1 lève erreur."""
        ranker = ShortlistRanker()
        
        with pytest.raises(ValueError):
            ranker.max_shortlist = 0


class TestShortlistRankerRank:
    """Tests du ranking."""
    
    def test_rank_empty(self):
        """Rank sur dict vide = liste vide."""
        ranker = ShortlistRanker()
        
        result = ranker.rank({})
        
        assert result == []
    
    def test_rank_single_symbol(self):
        """Rank avec un seul symbole."""
        ranker = ShortlistRanker()
        
        features = {
            "AAPL": {
                "rsi_14": 75.0,
                "volume_rel": 2.0,
                "momentum": 5.0,
                "trend_strength": 0.5,
                "atr_pct": 2.0,
            }
        }
        
        result = ranker.rank(features)
        
        assert len(result) == 1
        assert result[0].symbol == "AAPL"
        assert result[0].rank == 1
        assert result[0].score > 0
    
    def test_rank_multiple_symbols(self):
        """Rank avec plusieurs symboles."""
        ranker = ShortlistRanker(max_shortlist=5)
        
        features = {
            "AAPL": {
                "rsi_14": 75.0,      # RSI extrême → score élevé
                "volume_rel": 3.0,   # Volume spike
                "momentum": 5.0,
                "trend_strength": 0.8,
                "atr_pct": 2.5,
            },
            "MSFT": {
                "rsi_14": 50.0,      # RSI neutre → score bas
                "volume_rel": 1.0,   # Volume normal
                "momentum": 1.0,
                "trend_strength": 0.2,
                "atr_pct": 1.0,
            },
            "GOOGL": {
                "rsi_14": 25.0,      # RSI oversold → score élevé
                "volume_rel": 2.5,
                "momentum": 3.0,
                "trend_strength": -0.6,
                "atr_pct": 2.0,
            },
        }
        
        result = ranker.rank(features)
        
        assert len(result) == 3
        # AAPL ou GOOGL devrait être en tête (RSI extrêmes)
        assert result[0].rank == 1
        assert result[1].rank == 2
        assert result[2].rank == 3
    
    def test_rank_respects_max_shortlist(self):
        """Le ranking respecte max_shortlist."""
        ranker = ShortlistRanker(max_shortlist=2)
        
        features = {
            "AAPL": {"rsi_14": 80, "volume_rel": 2, "momentum": 5, "trend_strength": 0.5, "atr_pct": 2},
            "MSFT": {"rsi_14": 70, "volume_rel": 1.5, "momentum": 3, "trend_strength": 0.3, "atr_pct": 1.5},
            "GOOGL": {"rsi_14": 60, "volume_rel": 1, "momentum": 1, "trend_strength": 0.1, "atr_pct": 1},
            "AMZN": {"rsi_14": 50, "volume_rel": 0.8, "momentum": 0, "trend_strength": 0, "atr_pct": 0.5},
        }
        
        result = ranker.rank(features)
        
        assert len(result) == 2


class TestShortlistRankerDeterminism:
    """Tests de déterminisme (même input → même output)."""
    
    def test_deterministic_ranking(self):
        """Le ranking est déterministe."""
        ranker = ShortlistRanker(max_shortlist=10)
        
        features = {
            "AAPL": {"rsi_14": 75, "volume_rel": 2.5, "momentum": 4, "trend_strength": 0.6, "atr_pct": 2.2},
            "MSFT": {"rsi_14": 45, "volume_rel": 1.2, "momentum": 1, "trend_strength": 0.2, "atr_pct": 1.1},
            "GOOGL": {"rsi_14": 25, "volume_rel": 2.8, "momentum": -3, "trend_strength": -0.5, "atr_pct": 1.8},
            "AMZN": {"rsi_14": 55, "volume_rel": 1.5, "momentum": 2, "trend_strength": 0.3, "atr_pct": 1.5},
            "TSLA": {"rsi_14": 80, "volume_rel": 3.5, "momentum": 6, "trend_strength": 0.9, "atr_pct": 3.0},
        }
        
        # Exécuter plusieurs fois
        results = [ranker.rank(features) for _ in range(5)]
        
        # Vérifier que tous les résultats sont identiques
        for i in range(1, len(results)):
            assert len(results[0]) == len(results[i])
            for j, ss in enumerate(results[0]):
                assert ss.symbol == results[i][j].symbol
                assert ss.score == results[i][j].score
                assert ss.rank == results[i][j].rank
    
    def test_deterministic_with_ties(self):
        """Déterminisme même avec égalités."""
        ranker = ShortlistRanker()
        
        # Créer des features qui donnent des scores égaux
        features = {
            "ZZZ": {"rsi_14": 75, "volume_rel": 2, "momentum": 3, "trend_strength": 0.5, "atr_pct": 2},
            "AAA": {"rsi_14": 75, "volume_rel": 2, "momentum": 3, "trend_strength": 0.5, "atr_pct": 2},
            "MMM": {"rsi_14": 75, "volume_rel": 2, "momentum": 3, "trend_strength": 0.5, "atr_pct": 2},
        }
        
        results = [ranker.rank(features) for _ in range(5)]
        
        # Même ordre à chaque fois (alphabétique en cas d'égalité)
        for result in results:
            assert result[0].symbol == "AAA"
            assert result[1].symbol == "MMM"
            assert result[2].symbol == "ZZZ"


class TestShortlistRankerExplanation:
    """Tests de l'explication."""
    
    def test_get_explanation(self):
        """Test génération d'explication."""
        ranker = ShortlistRanker()
        
        features = {
            "AAPL": {"rsi_14": 80, "volume_rel": 2.5, "momentum": 5, "trend_strength": 0.7, "atr_pct": 2.0},
        }
        
        result = ranker.rank(features)
        explanation = ranker.get_explanation(result[0])
        
        assert "AAPL" in explanation
        assert "Score" in explanation
        assert "rsi_extremes" in explanation


class TestShortlistRankerScoring:
    """Tests des composantes du scoring."""
    
    def test_rsi_extremes_scoring(self):
        """Score RSI: élevé si < 30 ou > 70."""
        ranker = ShortlistRanker()
        
        # RSI extrême (oversold)
        features_low = {
            "LOW": {"rsi_14": 20, "volume_rel": 1, "momentum": 0, "trend_strength": 0, "atr_pct": 0},
        }
        # RSI extrême (overbought)
        features_high = {
            "HIGH": {"rsi_14": 85, "volume_rel": 1, "momentum": 0, "trend_strength": 0, "atr_pct": 0},
        }
        # RSI neutre
        features_neutral = {
            "NEUTRAL": {"rsi_14": 50, "volume_rel": 1, "momentum": 0, "trend_strength": 0, "atr_pct": 0},
        }
        
        result_low = ranker.rank(features_low)[0]
        result_high = ranker.rank(features_high)[0]
        result_neutral = ranker.rank(features_neutral)[0]
        
        assert result_low.components["rsi_extremes"] > 0
        assert result_high.components["rsi_extremes"] > 0
        assert result_neutral.components["rsi_extremes"] == 0
    
    def test_volume_spike_scoring(self):
        """Score volume: élevé si volume_rel > 1."""
        ranker = ShortlistRanker()
        
        features_spike = {
            "SPIKE": {"rsi_14": 50, "volume_rel": 3.5, "momentum": 0, "trend_strength": 0, "atr_pct": 0},
        }
        features_normal = {
            "NORMAL": {"rsi_14": 50, "volume_rel": 1.0, "momentum": 0, "trend_strength": 0, "atr_pct": 0},
        }
        
        result_spike = ranker.rank(features_spike)[0]
        result_normal = ranker.rank(features_normal)[0]
        
        assert result_spike.components["volume_spike"] > 0
        assert result_normal.components["volume_spike"] == 0
