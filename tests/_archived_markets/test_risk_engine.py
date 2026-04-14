"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Risk Engine
====================
"""

import tempfile
from pathlib import Path

import pytest

from src.markets.risk.engine import (
    RiskEngine,
    ProposedOrder,
    PortfolioState,
    ValidationResult,
    RejectionReason,
)
from src.markets.risk.audit import AuditLog
from src.markets.risk.kill_switch import KillSwitch


@pytest.fixture
def temp_dir():
    """Crée un répertoire temporaire pour les tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audit_log(temp_dir):
    """Crée un audit log temporaire."""
    return AuditLog(temp_dir / "audit.jsonl")


@pytest.fixture
def kill_switch(temp_dir):
    """Crée un kill switch temporaire."""
    return KillSwitch(kill_file=str(temp_dir / "KILL_SWITCH"))


@pytest.fixture
def risk_engine(audit_log, kill_switch):
    """Crée un risk engine avec dépendances temporaires."""
    return RiskEngine(audit_log=audit_log, kill_switch=kill_switch)


@pytest.fixture
def portfolio():
    """Portfolio par défaut pour les tests."""
    return PortfolioState(
        total_capital=100000.0,
        current_exposure=0.0,
        daily_pnl=0.0,
        daily_orders_count=0,
    )


class TestProposedOrder:
    """Tests pour ProposedOrder."""
    
    def test_position_value(self):
        """Calcul de la valeur de position."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=100,
            entry_price=150.0,
            stop_loss=145.0,
        )
        
        assert order.position_value == 15000.0
    
    def test_risk_amount(self):
        """Calcul du risque."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=100,
            entry_price=150.0,
            stop_loss=145.0,  # 5$ de risque par action
        )
        
        assert order.risk_amount == 500.0  # 100 * 5$
    
    def test_risk_amount_no_stop(self):
        """Risque infini sans stop."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=100,
            entry_price=150.0,
            stop_loss=None,
        )
        
        assert order.risk_amount == float('inf')
    
    def test_stop_loss_pct(self):
        """Calcul du stop loss en %."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=100,
            entry_price=100.0,
            stop_loss=95.0,  # 5% de distance
        )
        
        assert order.stop_loss_pct == pytest.approx(5.0, rel=0.01)


class TestValidationResult:
    """Tests pour ValidationResult."""
    
    def test_approved(self):
        """Création d'un résultat approuvé."""
        result = ValidationResult.approved()
        
        assert result.is_valid
        assert result.rejection_reason is None
        assert result.warnings == []
    
    def test_approved_with_warnings(self):
        """Résultat approuvé avec avertissements."""
        result = ValidationResult.approved(warnings=["Attention X"])
        
        assert result.is_valid
        assert len(result.warnings) == 1
    
    def test_rejected(self):
        """Création d'un résultat rejeté."""
        result = ValidationResult.rejected(
            RejectionReason.NO_STOP_LOSS,
            "Stop loss obligatoire"
        )
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.NO_STOP_LOSS


class TestRiskEngineLimits:
    """Tests des limites hard-coded."""
    
    def test_limits_are_hardcoded(self, risk_engine):
        """Vérifie les limites hard-coded."""
        assert risk_engine.MAX_LOSS_PER_DAY_PCT == 2.5
        assert risk_engine.MAX_SINGLE_POSITION_PCT == 5.0
        assert risk_engine.MAX_TOTAL_EXPOSURE_PCT == 30.0
        assert risk_engine.MAX_ORDERS_PER_DAY == 60
        assert risk_engine.MANDATORY_STOP_LOSS is True
    
    def test_get_limits(self, risk_engine):
        """Test get_limits."""
        limits = risk_engine.get_limits()
        
        assert "max_loss_per_day_pct" in limits
        assert "mandatory_stop_loss" in limits
        assert limits["max_loss_per_day_pct"] == 2.5


class TestRiskEngineStopLoss:
    """Tests du stop loss obligatoire."""
    
    def test_order_without_stop_rejected(self, risk_engine, portfolio):
        """Ordre sans stop loss → REJETÉ."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=150.0,
            stop_loss=None,  # PAS DE STOP
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.NO_STOP_LOSS
    
    def test_order_with_stop_approved(self, risk_engine, portfolio):
        """Ordre avec stop loss → approuvé."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=150.0,
            stop_loss=147.0,  # Stop à 2%
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert result.is_valid
    
    def test_stop_too_far_rejected(self, risk_engine, portfolio):
        """Stop loss trop loin → REJETÉ."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=100.0,
            stop_loss=90.0,  # Stop à 10% > max 5%
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.STOP_TOO_FAR


class TestRiskEnginePositionSize:
    """Tests de la taille de position."""
    
    def test_position_too_large_rejected(self, risk_engine, portfolio):
        """Position trop grande → REJETÉ."""
        # Max = 5% de 100k = 5000$
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=100,  # 100 * 100$ = 10000$ > 5000$
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.POSITION_TOO_LARGE
    
    def test_position_at_limit_approved(self, risk_engine, portfolio):
        """Position à la limite → approuvé."""
        # Max = 5% de 100k = 5000$
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=50,  # 50 * 100$ = 5000$ = limite
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert result.is_valid


class TestRiskEngineExposure:
    """Tests de l'exposition totale."""
    
    def test_max_exposure_exceeded_rejected(self, risk_engine):
        """Exposition max dépassée → REJETÉ."""
        # Max = 30% de 100k = 30000$
        portfolio = PortfolioState(
            total_capital=100000.0,
            current_exposure=29500.0,  # Déjà 29.5k
            daily_pnl=0.0,
            daily_orders_count=0,
        )
        
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=20,  # +2000$ → 31500$ > 30000$
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.MAX_EXPOSURE_EXCEEDED


class TestRiskEngineDailyLoss:
    """Tests de la perte journalière."""
    
    def test_max_daily_loss_activates_kill_switch(self, risk_engine, kill_switch):
        """Perte journalière max → kill switch activé."""
        # Max loss = 2.5% de 100k = 2500$
        portfolio = PortfolioState(
            total_capital=100000.0,
            current_exposure=0.0,
            daily_pnl=-2600.0,  # Perte de 2600$ > 2500$
            daily_orders_count=0,
        )
        
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.MAX_DAILY_LOSS_EXCEEDED
        
        # Kill switch activé automatiquement
        assert kill_switch.is_active


class TestRiskEngineDailyOrders:
    """Tests du nombre d'ordres journalier."""
    
    def test_max_daily_orders_exceeded(self, risk_engine):
        """Nombre max d'ordres atteint → REJETÉ."""
        portfolio = PortfolioState(
            total_capital=100000.0,
            current_exposure=0.0,
            daily_pnl=0.0,
            daily_orders_count=60,  # Max atteint
        )
        
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.MAX_DAILY_ORDERS_EXCEEDED


class TestRiskEngineKillSwitch:
    """Tests du kill switch."""
    
    def test_kill_switch_blocks_all(self, risk_engine, kill_switch, portfolio):
        """Kill switch actif → tout bloqué."""
        # Activer le kill switch
        kill_switch.activate("Test")
        
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            entry_price=100.0,
            stop_loss=98.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.KILL_SWITCH_ACTIVE
    
    def test_is_kill_switch_active(self, risk_engine, kill_switch):
        """Test is_kill_switch_active."""
        assert not risk_engine.is_kill_switch_active()
        
        kill_switch.activate("Test")
        
        assert risk_engine.is_kill_switch_active()


class TestRiskEngineValidOrder:
    """Tests d'un ordre valide complet."""
    
    def test_valid_order_approved(self, risk_engine, portfolio):
        """Ordre valide complet → approuvé."""
        order = ProposedOrder(
            symbol="AAPL",
            side="BUY",
            quantity=30,  # 3000$ < 5000$ max
            entry_price=100.0,
            stop_loss=98.0,  # 2% < 5% max
            take_profit=110.0,
        )
        
        result = risk_engine.validate_order(order, portfolio)
        
        assert result.is_valid
        assert result.rejection_reason is None
    
    def test_valid_order_logged(self, risk_engine, portfolio, audit_log):
        """Ordre approuvé est loggé."""
        order = ProposedOrder(
            symbol="MSFT",
            side="BUY",
            quantity=10,
            entry_price=300.0,
            stop_loss=294.0,
        )
        
        risk_engine.validate_order(order, portfolio)
        
        entries = audit_log.replay()
        assert len(entries) > 0
        assert any(e.get("result") == "approved" for e in entries)


class TestRiskEnginePortfolioValidation:
    """Tests de validation du portefeuille."""
    
    def test_validate_portfolio_ok(self, risk_engine):
        """Portefeuille OK."""
        portfolio = PortfolioState(
            total_capital=100000.0,
            current_exposure=10000.0,
            daily_pnl=-500.0,
            daily_orders_count=10,
        )
        
        result = risk_engine.validate_portfolio_state(portfolio)
        
        assert result.is_valid
    
    def test_check_daily_limits(self, risk_engine):
        """Test check_daily_limits."""
        portfolio = PortfolioState(
            total_capital=100000.0,
            current_exposure=10000.0,
            daily_pnl=-500.0,
            daily_orders_count=10,
        )
        
        assert risk_engine.check_daily_limits(portfolio)
