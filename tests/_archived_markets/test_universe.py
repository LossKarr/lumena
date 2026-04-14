"""
Tests pour le TradingUniverse configurable.
"""

import pytest
import os
import tempfile


class TestTradingUniverse:
    """Tests pour le gestionnaire d'univers d'actifs."""
    
    def test_import(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert u is not None
    
    def test_default_has_48_assets(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert len(u) == 48
    
    def test_all_active_by_default(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert len(u.get_active_symbols()) == 48
    
    def test_contains_key_tickers(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        for sym in ["NVDA", "AAPL", "MSFT", "LLY", "JPM", "XOM", "SPY"]:
            assert sym in u, f"{sym} devrait être dans l'univers"
    
    def test_no_intc(self):
        """Intel a été retiré de l'univers par défaut."""
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert "INTC" not in u
    
    def test_sectors_present(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        sectors = u.get_active_sectors()
        assert len(sectors) >= 6  # Au moins 6 secteurs
    
    def test_get_symbols_by_sector(self):
        from src.markets.universe import TradingUniverse, Sector
        u = TradingUniverse()
        pharma = u.get_symbols_by_sector(Sector.PHARMA_BIOTECH)
        assert len(pharma) == 10
        assert "LLY" in pharma
        assert "MRK" in pharma
    
    def test_ai_tech_sector(self):
        from src.markets.universe import TradingUniverse, Sector
        u = TradingUniverse()
        tech = u.get_symbols_by_sector(Sector.AI_TECH)
        assert len(tech) == 20
        assert "NVDA" in tech
        assert "PLTR" in tech
    
    def test_disable_symbol(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert u.disable_symbol("TSLA")
        assert "TSLA" not in u.get_active_symbols()
        assert "TSLA" in u.get_all_symbols()
    
    def test_enable_symbol(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        u.disable_symbol("TSLA")
        u.enable_symbol("TSLA")
        assert "TSLA" in u.get_active_symbols()
    
    def test_toggle_symbol(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        new_state = u.toggle_symbol("AAPL")
        assert new_state is False
        new_state2 = u.toggle_symbol("AAPL")
        assert new_state2 is True
    
    def test_disable_sector(self):
        from src.markets.universe import TradingUniverse, Sector
        u = TradingUniverse()
        count = u.disable_sector(Sector.ENERGY)
        assert count == 5
        energy = u.get_symbols_by_sector(Sector.ENERGY)
        assert len(energy) == 0
    
    def test_enable_sector(self):
        from src.markets.universe import TradingUniverse, Sector
        u = TradingUniverse()
        u.disable_sector(Sector.ENERGY)
        count = u.enable_sector(Sector.ENERGY)
        assert count == 5
    
    def test_add_asset(self):
        from src.markets.universe import TradingUniverse, Asset, Sector
        u = TradingUniverse()
        new_asset = Asset("BABA", "Alibaba", Sector.AI_TECH, notes="E-commerce Chine")
        assert u.add_asset(new_asset)
        assert "BABA" in u
        assert len(u) == 49
    
    def test_add_duplicate_fails(self):
        from src.markets.universe import TradingUniverse, Asset, Sector
        u = TradingUniverse()
        dup = Asset("NVDA", "Nvidia Duplicate", Sector.AI_TECH)
        assert u.add_asset(dup) is False
    
    def test_remove_asset(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert u.remove_asset("COIN")
        assert "COIN" not in u
        assert len(u) == 47
    
    def test_remove_nonexistent(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        assert u.remove_asset("FAKE") is False
    
    def test_get_asset(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        nvda = u.get_asset("NVDA")
        assert nvda is not None
        assert nvda.name == "Nvidia"
        assert "GPU" in nvda.notes or "IA" in nvda.notes
    
    def test_sector_summary(self):
        from src.markets.universe import TradingUniverse
        u = TradingUniverse()
        summary = u.get_sector_summary()
        assert len(summary) >= 6
        for sector, info in summary.items():
            assert "active" in info
            assert "total" in info
            assert "symbols" in info
    
    def test_persistence_save_load(self):
        from src.markets.universe import TradingUniverse
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "universe.json")
            
            # Créer et modifier
            u1 = TradingUniverse(persistence_path=path)
            u1.disable_symbol("TSLA")
            u1.disable_symbol("META")
            
            # Recharger
            u2 = TradingUniverse(persistence_path=path)
            assert "TSLA" not in u2.get_active_symbols()
            assert "META" not in u2.get_active_symbols()
            assert len(u2.get_active_symbols()) == 46
    
    def test_reset_to_default(self):
        from src.markets.universe import TradingUniverse
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "u.json")
            u = TradingUniverse(persistence_path=path)
            u.disable_symbol("NVDA")
            u.remove_asset("SPY")
            count = u.reset_to_default()
            assert count == 48
            assert "NVDA" in u.get_active_symbols()
            assert "SPY" in u
    
    def test_to_api_response(self):
        from src.markets.universe import TradingUniverse
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "u.json")
            u = TradingUniverse(persistence_path=path)
            resp = u.to_api_response()
            assert "total" in resp
            assert "active" in resp
            assert "sectors" in resp
            assert "assets" in resp
            assert resp["total"] == 48
            assert resp["active"] == 48
    
    def test_repr(self):
        from src.markets.universe import TradingUniverse
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "u.json")
            u = TradingUniverse(persistence_path=path)
            r = repr(u)
            assert "48/48" in r
            assert "secteurs" in r
    
    def test_asset_to_dict_from_dict(self):
        from src.markets.universe import Asset, Sector
        a = Asset("TEST", "Test Corp", Sector.AI_TECH, notes="test")
        d = a.to_dict()
        assert d["symbol"] == "TEST"
        a2 = Asset.from_dict(d)
        assert a2.symbol == a.symbol
        assert a2.name == a.name
    
    def test_crypto_sector(self):
        from src.markets.universe import TradingUniverse, Sector
        u = TradingUniverse()
        crypto = u.get_symbols_by_sector(Sector.CRYPTO)
        assert "COIN" in crypto
        assert "MSTR" in crypto
