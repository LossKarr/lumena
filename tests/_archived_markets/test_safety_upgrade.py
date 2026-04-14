"""
🛡️ Tests Safety Upgrade — Post-analyse DUP806663
===================================================

Vérifie les 5 corrections critiques:
1. Limite par symbole (MAX_CUMUL_PER_SYMBOL_PCT = 10%)
2. Cap absolu par trade (MAX_POSITION_VALUE_ABS = $50K)
3. Rejet SYMBOL_CONCENTRATION_EXCEEDED
4. Kelly position sizer cap absolu
5. PortfolioState symbol_exposure tracking
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
from src.markets.risk.position_sizer import KellyPositionSizer, PositionSizeResult


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_dir():
    """Crée un répertoire temporaire."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def risk_engine(temp_dir):
    """Risk engine avec dépendances temporaires."""
    audit = AuditLog(temp_dir / "audit.jsonl")
    ks = KillSwitch()
    return RiskEngine(audit_log=audit, kill_switch=ks)


@pytest.fixture
def portfolio_1M():
    """Portfolio de 1M$ (comme le compte DUP806663)."""
    return PortfolioState(
        total_capital=1_000_000.0,
        current_exposure=50_000.0,
        daily_pnl=0.0,
        daily_orders_count=0,
        positions={"AAPL": 15000.0},
        symbol_exposure={"AAPL": 15000.0},
    )


@pytest.fixture
def kelly_sizer():
    """Kelly position sizer par défaut."""
    return KellyPositionSizer()


# =============================================================================
# FIX 1 & 3: SYMBOL CONCENTRATION LIMIT
# =============================================================================

class TestSymbolConcentration:
    """Tests pour la limite de concentration par symbole (FIX META 74%)."""

    def test_new_rejection_reason_exists(self):
        """Le nouveau motif de rejet existe."""
        assert hasattr(RejectionReason, "SYMBOL_CONCENTRATION_EXCEEDED")
        assert RejectionReason.SYMBOL_CONCENTRATION_EXCEEDED.value == "symbol_concentration_exceeded"

    def test_symbol_below_limit_approved(self, risk_engine, portfolio_1M):
        """Ordre sous la limite → approuvé."""
        order = ProposedOrder(
            symbol="MSFT",
            side="BUY",
            quantity=100,
            entry_price=400.0,  # $40K < 10% de $1M
            stop_loss=388.0,
        )
        result = risk_engine.validate_order(order, portfolio_1M)
        assert result.is_valid, f"Devrait être approuvé: {result.rejection_message}"

    def test_symbol_at_exact_limit_approved(self, risk_engine):
        """Ordre exactement à la limite → approuvé."""
        portfolio = PortfolioState(
            total_capital=1_000_000.0,
            current_exposure=50_000.0,
            daily_pnl=0.0,
            daily_orders_count=0,
            symbol_exposure={"META": 50_000.0},  # 5% déjà
        )
        order = ProposedOrder(
            symbol="META",
            side="BUY",
            quantity=100,
            entry_price=500.0,  # +$50K = 10% total = pile à la limite
            stop_loss=485.0,
        )
        result = risk_engine.validate_order(order, portfolio)
        assert result.is_valid, f"Pile à la limite devrait passer: {result.rejection_message}"

    def test_symbol_over_limit_rejected(self, risk_engine):
        """Ordre qui dépasse 10% cumulé → REJETÉ (FIX META)."""
        portfolio = PortfolioState(
            total_capital=1_000_000.0,
            current_exposure=90_000.0,
            daily_pnl=0.0,
            daily_orders_count=0,
            symbol_exposure={"META": 90_000.0},  # 9% déjà
        )
        order = ProposedOrder(
            symbol="META",
            side="BUY",
            quantity=50,
            entry_price=500.0,  # +$25K → 11.5% total > 10%
            stop_loss=485.0,
        )
        result = risk_engine.validate_order(order, portfolio)
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.SYMBOL_CONCENTRATION_EXCEEDED

    def test_different_symbol_ok_even_if_one_is_full(self, risk_engine):
        """Un nouveau symbole passe même si un autre est au max."""
        portfolio = PortfolioState(
            total_capital=1_000_000.0,
            current_exposure=100_000.0,
            daily_pnl=0.0,
            daily_orders_count=0,
            symbol_exposure={"META": 100_000.0},  # META = 10% (full)
        )
        order = ProposedOrder(
            symbol="NVDA",  # Nouveau symbole, 0% cumulé
            side="BUY",
            quantity=50,
            entry_price=800.0,  # $40K = 4% < 10%
            stop_loss=776.0,
        )
        result = risk_engine.validate_order(order, portfolio)
        assert result.is_valid, f"NVDA devrait passer: {result.rejection_message}"

    def test_meta_68_trades_scenario_blocked(self, risk_engine):
        """Simule le scénario DUP806663: META acheté 68 fois."""
        # Après 20 achats de $500 = $10K = 10%, le 21ème devrait être bloqué
        # Avec capital de $100K
        portfolio = PortfolioState(
            total_capital=100_000.0,
            current_exposure=10_000.0,
            daily_pnl=0.0,
            daily_orders_count=20,
            symbol_exposure={"META": 10_000.0},  # 10% déjà
        )
        order = ProposedOrder(
            symbol="META",
            side="BUY",
            quantity=1,
            entry_price=500.0,  # +$500 → 10.5% > 10%
            stop_loss=485.0,
        )
        result = risk_engine.validate_order(order, portfolio)
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.SYMBOL_CONCENTRATION_EXCEEDED

    def test_portfolio_state_has_symbol_exposure(self):
        """PortfolioState a le champ symbol_exposure."""
        ps = PortfolioState()
        assert hasattr(ps, "symbol_exposure")
        assert isinstance(ps.symbol_exposure, dict)
        assert len(ps.symbol_exposure) == 0


# =============================================================================
# FIX 2: ABSOLUTE POSITION CAP
# =============================================================================

class TestAbsolutePositionCap:
    """Tests pour le cap absolu par trade ($50K)."""

    def test_cap_constant_exists(self, risk_engine):
        """La constante MAX_POSITION_VALUE_ABS existe."""
        assert hasattr(risk_engine, "MAX_POSITION_VALUE_ABS")
        assert risk_engine.MAX_POSITION_VALUE_ABS == 50_000.0

    def test_order_below_cap_approved(self, risk_engine, portfolio_1M):
        """Ordre sous le cap → approuvé."""
        order = ProposedOrder(
            symbol="NVDA",
            side="BUY",
            quantity=50,
            entry_price=800.0,  # $40K < $50K cap
            stop_loss=776.0,
        )
        result = risk_engine.validate_order(order, portfolio_1M)
        assert result.is_valid

    def test_order_above_cap_rejected(self, risk_engine, portfolio_1M):
        """Ordre au-dessus du cap → REJETÉ."""
        order = ProposedOrder(
            symbol="NVDA",
            side="BUY",
            quantity=100,
            entry_price=600.0,  # $60K > $50K cap
            stop_loss=582.0,
        )
        result = risk_engine.validate_order(order, portfolio_1M)
        assert not result.is_valid
        assert result.rejection_reason == RejectionReason.POSITION_TOO_LARGE

    def test_cumul_limit_in_get_limits(self, risk_engine):
        """get_limits() retourne les nouvelles limites."""
        limits = risk_engine.get_limits()
        assert "max_cumul_per_symbol_pct" in limits
        assert limits["max_cumul_per_symbol_pct"] == 10.0
        assert "max_position_value_abs" in limits
        assert limits["max_position_value_abs"] == 50_000.0


# =============================================================================
# FIX 5: KELLY POSITION SIZER ABSOLUTE CAP
# =============================================================================

class TestKellyAbsoluteCap:
    """Tests pour le cap absolu dans le Kelly position sizer."""

    def test_cap_constant_exists(self, kelly_sizer):
        """La constante MAX_POSITION_VALUE_ABS existe."""
        assert hasattr(kelly_sizer, "MAX_POSITION_VALUE_ABS")
        assert kelly_sizer.MAX_POSITION_VALUE_ABS == 50_000.0

    def test_small_portfolio_not_capped(self, kelly_sizer):
        """Portfolio modeste → position pas capée."""
        result = kelly_sizer.calculate(
            win_rate=0.60,
            avg_win_pct=1.5,
            avg_loss_pct=0.8,
            portfolio_value=100_000.0,
        )
        # 5% max de $100K = $5K, largement sous $50K
        assert result.position_value <= 50_000.0
        assert result.position_value <= 5000.0  # 5% cap de $100K

    def test_large_portfolio_capped_at_50k(self, kelly_sizer):
        """Portfolio très large → position capée à $50K."""
        result = kelly_sizer.calculate(
            win_rate=0.70,
            avg_win_pct=2.0,
            avg_loss_pct=0.5,
            portfolio_value=5_000_000.0,  # $5M portfolio
            consensus_score=1.0,
        )
        # 5% de $5M = $250K, mais cap à $50K
        assert result.position_value <= 50_000.0

    def test_medium_portfolio_within_kelly_range(self, kelly_sizer):
        """Portfolio moyen → Kelly fonctionne normalement."""
        result = kelly_sizer.calculate(
            win_rate=0.55,
            avg_win_pct=1.2,
            avg_loss_pct=1.0,
            portfolio_value=200_000.0,
        )
        # Devrait être dans le range normal, pas capé
        assert result.position_value > 0
        assert result.position_value <= 50_000.0
        assert result.position_pct <= 0.05  # 5% max

    def test_negative_kelly_zero_position(self, kelly_sizer):
        """Kelly négatif → position 0 (edge négatif)."""
        result = kelly_sizer.calculate(
            win_rate=0.30,
            avg_win_pct=0.5,
            avg_loss_pct=1.5,
            portfolio_value=1_000_000.0,
        )
        assert result.position_value == 0.0
        assert "négatif" in result.reason.lower() or "edge" in result.reason.lower()

    def test_result_to_dict(self, kelly_sizer):
        """to_dict() fonctionne correctement."""
        result = kelly_sizer.calculate(
            win_rate=0.60,
            avg_win_pct=1.3,
            avg_loss_pct=0.8,
            portfolio_value=100_000.0,
        )
        d = result.to_dict()
        assert "position_value" in d
        assert "position_pct" in d
        assert "kelly_full" in d
        assert isinstance(d["position_value"], float)


# =============================================================================
# INTEGRATION: Scénario complet DUP806663
# =============================================================================

class TestDUP806663Scenario:
    """Reproduction du scénario DUP806663 pour vérifier que les fix fonctionnent."""

    def test_full_scenario_meta_concentration(self, risk_engine):
        """
        Simule: $1M capital, on essaye d'acheter META en boucle.
        Après 200 actions ($100K = 10%), le risk engine DOIT bloquer.
        """
        capital = 1_000_000.0
        meta_cumul = 0.0
        meta_price = 500.0
        trades_passed = 0
        trades_blocked = 0

        for i in range(250):  # 250 tentatives
            portfolio = PortfolioState(
                total_capital=capital,
                current_exposure=meta_cumul,
                daily_pnl=0.0,
                daily_orders_count=i % 50,  # Reset pour ne pas trigger daily limit
                symbol_exposure={"META": meta_cumul},
            )
            order = ProposedOrder(
                symbol="META",
                side="BUY",
                quantity=1,  # 1 action
                entry_price=meta_price,
                stop_loss=meta_price * 0.97,
            )
            result = risk_engine.validate_order(order, portfolio)

            if result.is_valid:
                meta_cumul += meta_price
                trades_passed += 1
            else:
                trades_blocked += 1

        # $500/trade, limite 10% de $1M ($100K) → 200 trades max
        assert trades_passed == 200, f"Attendu 200 trades passés, obtenu {trades_passed}"
        assert trades_blocked == 50, f"Attendu 50 trades bloqués, obtenu {trades_blocked}"
        # META ne doit JAMAIS dépasser 10% du capital
        assert meta_cumul <= capital * 0.10 + meta_price  # Tolérance d'un trade

    def test_diversified_portfolio_works(self, risk_engine):
        """
        Un portfolio diversifié fonctionne:
        5 symboles × 2% chacun = 10% exposition → OK.
        """
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        portfolio = PortfolioState(
            total_capital=1_000_000.0, 
            current_exposure=50_000.0,
            daily_pnl=0.0,
            daily_orders_count=5,
            symbol_exposure={s: 10_000.0 for s in symbols[:5]},
        )

        # Ajouter un 6ème symbole
        order = ProposedOrder(
            symbol="TSLA",
            side="BUY",
            quantity=50,
            entry_price=300.0,  # $15K = 1.5%
            stop_loss=291.0,
        )
        result = risk_engine.validate_order(order, portfolio)
        assert result.is_valid, f"Portfolio diversifié devrait passer: {result.rejection_message}"
