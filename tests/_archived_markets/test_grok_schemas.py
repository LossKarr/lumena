"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Grok Schemas
=====================
"""

from datetime import datetime

import pytest

from src.markets.grok.schemas import (
    SignalDirection,
    SignalStrength,
    SymbolSignal,
    MarketAnalysis,
    GrokScanRequest,
    GrokScanResponse,
    GrokAnalysisRequest,
    GrokAnalysisResponse,
    create_empty_scan_response,
    create_empty_analysis_response,
)


class TestSymbolSignal:
    """Tests pour SymbolSignal."""
    
    def test_create_basic(self):
        """Création basique."""
        signal = SymbolSignal(
            symbol="AAPL",
            direction=SignalDirection.LONG,
        )
        
        assert signal.symbol == "AAPL"
        assert signal.direction == SignalDirection.LONG
        assert signal.strength == SignalStrength.MODERATE  # Default
        assert signal.confidence == 0.5  # Default
    
    def test_confidence_out_of_range_raises(self):
        """Confiance hors limites lève une erreur Pydantic."""
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            SymbolSignal(
                symbol="AAPL",
                direction=SignalDirection.LONG,
                confidence=1.5,  # Trop élevé
            )
    
    def test_full_signal(self):
        """Signal complet avec tous les champs."""
        signal = SymbolSignal(
            symbol="MSFT",
            direction=SignalDirection.SHORT,
            strength=SignalStrength.STRONG,
            confidence=0.85,
            reason="RSI oversold with volume spike",
        )
        
        assert signal.symbol == "MSFT"
        assert signal.direction == SignalDirection.SHORT
        assert signal.strength == SignalStrength.STRONG
        assert signal.confidence == 0.85
        assert "RSI" in signal.reason


class TestMarketAnalysis:
    """Tests pour MarketAnalysis."""
    
    def test_create_minimal(self):
        """Création minimale avec signal obligatoire."""
        analysis = MarketAnalysis(
            symbol="AAPL",
            signal=SymbolSignal(
                symbol="AAPL",
                direction=SignalDirection.LONG,
            ),
        )
        
        assert analysis.symbol == "AAPL"
        assert analysis.signal.direction == SignalDirection.LONG
        assert analysis.key_levels == {}
        assert analysis.risk_factors == []
    
    def test_full_analysis(self):
        """Analyse complète."""
        analysis = MarketAnalysis(
            symbol="GOOGL",
            market_context="Tech sector bullish",
            sector_analysis="FAANG performing well",
            technical_summary="Breakout above resistance",
            key_levels={"support": 140.0, "resistance": 150.0},
            signal=SymbolSignal(
                symbol="GOOGL",
                direction=SignalDirection.LONG,
                confidence=0.8,
            ),
            risk_factors=["Earnings next week", "High IV"],
            suggested_stop_loss=138.0,
            suggested_take_profit=155.0,
            suggested_timeframe="swing",
        )
        
        assert analysis.key_levels["support"] == 140.0
        assert len(analysis.risk_factors) == 2
        assert analysis.suggested_timeframe == "swing"


class TestGrokScanRequest:
    """Tests pour GrokScanRequest."""
    
    def test_create_basic(self):
        """Création basique."""
        request = GrokScanRequest(
            symbols=["AAPL", "MSFT", "GOOGL"],
        )
        
        assert len(request.symbols) == 3
        assert request.market_conditions == "normal"
    
    def test_symbols_limited_to_20(self):
        """Les symboles sont limités à 20."""
        symbols = [f"SYM{i}" for i in range(30)]
        request = GrokScanRequest(symbols=symbols)
        
        assert len(request.symbols) == 20
    
    def test_with_features(self):
        """Requête avec features."""
        request = GrokScanRequest(
            symbols=["AAPL"],
            features={
                "AAPL": {"rsi_14": 75.0, "volume_rel": 2.5},
            },
            market_conditions="volatile",
        )
        
        assert request.features["AAPL"]["rsi_14"] == 75.0
        assert request.market_conditions == "volatile"


class TestGrokScanResponse:
    """Tests pour GrokScanResponse."""
    
    def test_create_empty(self):
        """Création vide."""
        response = GrokScanResponse()
        
        assert response.signals == []
        assert response.market_sentiment == "neutral"
    
    def test_create_with_signals(self):
        """Création avec signaux."""
        signals = [
            SymbolSignal(symbol="AAPL", direction=SignalDirection.LONG, confidence=0.8),
            SymbolSignal(symbol="MSFT", direction=SignalDirection.NEUTRAL, confidence=0.5),
            SymbolSignal(symbol="GOOGL", direction=SignalDirection.SHORT, confidence=0.7),
        ]
        
        response = GrokScanResponse(
            signals=signals,
            market_sentiment="bullish",
            model_used="grok-4-1-fast-non-reasoning",
            tokens_used=500,
        )
        
        assert len(response.signals) == 3
        assert response.model_used == "grok-4-1-fast-non-reasoning"
    
    def test_get_actionable_signals(self):
        """Filtre les signaux actionnables."""
        signals = [
            SymbolSignal(symbol="AAPL", direction=SignalDirection.LONG, confidence=0.8),
            SymbolSignal(symbol="MSFT", direction=SignalDirection.NEUTRAL, confidence=0.9),  # Neutral
            SymbolSignal(symbol="GOOGL", direction=SignalDirection.SHORT, confidence=0.5),  # Trop faible
        ]
        response = GrokScanResponse(signals=signals)
        
        actionable = response.get_actionable_signals(min_confidence=0.6)
        
        assert len(actionable) == 1
        assert actionable[0].symbol == "AAPL"
    
    def test_get_top_signals(self):
        """Retourne les top N signaux."""
        signals = [
            SymbolSignal(symbol="AAPL", direction=SignalDirection.LONG, confidence=0.7),
            SymbolSignal(symbol="MSFT", direction=SignalDirection.SHORT, confidence=0.9),
            SymbolSignal(symbol="GOOGL", direction=SignalDirection.LONG, confidence=0.8),
            SymbolSignal(symbol="AMZN", direction=SignalDirection.NEUTRAL, confidence=0.95),  # Ignoré car neutral
        ]
        response = GrokScanResponse(signals=signals)
        
        top = response.get_top_signals(n=2)
        
        assert len(top) == 2
        assert top[0].symbol == "MSFT"  # Confiance 0.9
        assert top[1].symbol == "GOOGL"  # Confiance 0.8


class TestGrokAnalysisRequest:
    """Tests pour GrokAnalysisRequest."""
    
    def test_create(self):
        """Création basique."""
        signal = SymbolSignal(
            symbol="AAPL",
            direction=SignalDirection.LONG,
            confidence=0.8,
        )
        
        request = GrokAnalysisRequest(
            symbol="AAPL",
            signal=signal,
            features={"rsi_14": 65.0, "atr_pct": 2.5},
        )
        
        assert request.symbol == "AAPL"
        assert request.signal.direction == SignalDirection.LONG


class TestGrokAnalysisResponse:
    """Tests pour GrokAnalysisResponse."""
    
    def test_create(self):
        """Création basique."""
        analysis = MarketAnalysis(
            symbol="AAPL",
            signal=SymbolSignal(
                symbol="AAPL",
                direction=SignalDirection.LONG,
            ),
        )
        
        response = GrokAnalysisResponse(analysis=analysis)
        
        assert response.analysis.symbol == "AAPL"
        assert response.recommendation == "hold"
    
    def test_is_actionable(self):
        """Vérifie is_actionable."""
        analysis = MarketAnalysis(
            symbol="AAPL",
            signal=SymbolSignal(symbol="AAPL", direction=SignalDirection.LONG),
        )
        
        # Non actionable (confiance trop basse)
        response1 = GrokAnalysisResponse(
            analysis=analysis,
            recommendation="buy",
            action_confidence=0.5,
        )
        assert not response1.is_actionable
        
        # Actionable
        response2 = GrokAnalysisResponse(
            analysis=analysis,
            recommendation="buy",
            action_confidence=0.8,
        )
        assert response2.is_actionable
        
        # Non actionable (hold)
        response3 = GrokAnalysisResponse(
            analysis=analysis,
            recommendation="hold",
            action_confidence=0.9,
        )
        assert not response3.is_actionable


class TestHelperFunctions:
    """Tests pour les fonctions helper."""
    
    def test_create_empty_scan_response(self):
        """Crée réponse scan vide."""
        response = create_empty_scan_response()
        
        assert response.signals == []
        assert response.market_sentiment == "unknown"
        assert response.model_used == "none"
    
    def test_create_empty_analysis_response(self):
        """Crée réponse analyse vide."""
        response = create_empty_analysis_response("AAPL")
        
        assert response.analysis.symbol == "AAPL"
        assert response.analysis.signal.direction == SignalDirection.NEUTRAL
        assert response.recommendation == "hold"
        assert response.action_confidence == 0.0


class TestEnums:
    """Tests pour les enums."""
    
    def test_signal_direction_values(self):
        """Valeurs de SignalDirection."""
        assert SignalDirection.LONG.value == "long"
        assert SignalDirection.SHORT.value == "short"
        assert SignalDirection.NEUTRAL.value == "neutral"
    
    def test_signal_strength_values(self):
        """Valeurs de SignalStrength."""
        assert SignalStrength.STRONG.value == "strong"
        assert SignalStrength.MODERATE.value == "moderate"
        assert SignalStrength.WEAK.value == "weak"
