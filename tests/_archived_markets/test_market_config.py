"""
Tests pour la configuration Market Sentinel.

Vérifie:
1. Valeurs par défaut correctes
2. Validation des limites
3. Erreurs sur config invalide
4. Chargement depuis variables d'environnement
"""

import os
from unittest.mock import patch

import pytest

from src.markets.config import MarketConfig, load_market_config, _env_flag, _env_int, _env_str


class TestEnvHelpers:
    """Tests pour les fonctions utilitaires d'environnement."""
    
    def test_env_flag_true_values(self):
        """Test _env_flag avec valeurs true."""
        true_values = ["1", "true", "True", "TRUE", "yes", "Yes", "on", "ON", " true "]
        for val in true_values:
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert _env_flag("TEST_FLAG", False) is True, f"'{val}' devrait être True"
    
    def test_env_flag_false_values(self):
        """Test _env_flag avec valeurs false."""
        false_values = ["0", "false", "False", "FALSE", "no", "No", "off", "OFF", ""]
        for val in false_values:
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert _env_flag("TEST_FLAG", True) is False, f"'{val}' devrait être False"
    
    def test_env_flag_default(self):
        """Test _env_flag valeur par défaut."""
        # S'assurer que la variable n'existe pas
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("NONEXISTENT_FLAG", None)
            with patch.dict(os.environ, env, clear=True):
                assert _env_flag("NONEXISTENT_FLAG", False) is False
                assert _env_flag("NONEXISTENT_FLAG", True) is True
    
    def test_env_int_valid(self):
        """Test _env_int avec valeurs valides."""
        with patch.dict(os.environ, {"TEST_INT": "42"}):
            assert _env_int("TEST_INT", 0) == 42
        
        with patch.dict(os.environ, {"TEST_INT": " 123 "}):
            assert _env_int("TEST_INT", 0) == 123
    
    def test_env_int_invalid(self):
        """Test _env_int avec valeurs invalides."""
        with patch.dict(os.environ, {"TEST_INT": "abc"}):
            assert _env_int("TEST_INT", 99) == 99
        
        with patch.dict(os.environ, {"TEST_INT": "12.5"}):
            assert _env_int("TEST_INT", 99) == 99
    
    def test_env_str(self):
        """Test _env_str."""
        with patch.dict(os.environ, {"TEST_STR": "hello"}):
            assert _env_str("TEST_STR", "") == "hello"
        
        with patch.dict(os.environ, {"TEST_STR": " trimmed "}):
            assert _env_str("TEST_STR", "") == "trimmed"


class TestMarketConfigDefaults:
    """Tests des valeurs par défaut de MarketConfig."""
    
    def test_default_values(self):
        """Vérifie les valeurs par défaut."""
        config = MarketConfig()
        
        assert config.mode == "paper"
        assert config.auto_trade is False
        assert config.max_symbols == 20
        assert config.scan_interval == 60
        assert config.max_grok_calls_hour == 30
        assert config.ibkr_host == "127.0.0.1"
        assert config.ibkr_port == 4002
        assert config.ibkr_client_id == 1
        assert config.xai_api_key == ""
    
    def test_is_paper_mode(self):
        """Test propriété is_paper_mode."""
        config = MarketConfig(mode="paper")
        assert config.is_paper_mode is True
        assert config.is_live_mode is False
        
        config = MarketConfig(mode="live")
        assert config.is_paper_mode is False
        assert config.is_live_mode is True
    
    def test_has_grok_key(self):
        """Test propriété has_grok_key."""
        config = MarketConfig(xai_api_key="")
        assert config.has_grok_key is False
        
        config = MarketConfig(xai_api_key="sk-test-123")
        assert config.has_grok_key is True


class TestMarketConfigValidation:
    """Tests de validation de MarketConfig."""
    
    def test_valid_config(self):
        """Vérifie qu'une config valide ne lève pas d'erreur."""
        config = MarketConfig(
            mode="paper",
            max_symbols=10,
            scan_interval=30,
            ibkr_port=4002,
            xai_api_key="test-key",
        )
        assert config.mode == "paper"
    
    def test_invalid_mode(self):
        """Vérifie erreur sur mode invalide."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(mode="invalid_mode")
        
        assert "mode doit être" in str(exc_info.value)
    
    def test_max_symbols_too_low(self):
        """Vérifie erreur sur max_symbols < 1."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(max_symbols=0)
        
        assert "max_symbols doit être >= 1" in str(exc_info.value)
    
    def test_max_symbols_too_high(self):
        """Vérifie erreur sur max_symbols > 100."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(max_symbols=101)
        
        assert "max_symbols doit être <= 100" in str(exc_info.value)
    
    def test_scan_interval_too_low(self):
        """Vérifie erreur sur scan_interval < 10."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(scan_interval=5)
        
        assert "scan_interval doit être >= 10s" in str(exc_info.value)
    
    def test_scan_interval_too_high(self):
        """Vérifie erreur sur scan_interval > 3600."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(scan_interval=4000)
        
        assert "scan_interval doit être <= 3600s" in str(exc_info.value)
    
    def test_invalid_port(self):
        """Vérifie erreur sur port invalide."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(ibkr_port=70000)
        
        assert "ibkr_port doit être entre 1 et 65535" in str(exc_info.value)
    
    def test_negative_client_id(self):
        """Vérifie erreur sur client_id négatif."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(ibkr_client_id=-1)
        
        assert "ibkr_client_id doit être >= 0" in str(exc_info.value)
    
    def test_multiple_errors(self):
        """Vérifie que plusieurs erreurs sont reportées."""
        with pytest.raises(ValueError) as exc_info:
            MarketConfig(mode="bad", max_symbols=0, scan_interval=5)
        
        error_msg = str(exc_info.value)
        assert "mode doit être" in error_msg
        assert "max_symbols" in error_msg
        assert "scan_interval" in error_msg


class TestLoadMarketConfig:
    """Tests pour load_market_config()."""
    
    def test_load_defaults(self):
        """Vérifie le chargement des valeurs par défaut."""
        # Nettoyer les variables d'environnement
        clean_env = {}
        vars_to_clear = [
            "LUMENA_MARKETS_MODE", "LUMENA_MARKETS_AUTO_TRADE",
            "LUMENA_MARKETS_MAX_SYMBOLS", "LUMENA_MARKETS_SCAN_INTERVAL",
            "LUMENA_MARKETS_MAX_GROK_CALLS_HOUR",
            "IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID", "XAI_API_KEY"
        ]
        for var in vars_to_clear:
            clean_env[var] = ""
        
        # Patcher pour retourner les valeurs par défaut
        with patch.dict(os.environ, {}, clear=False):
            for var in vars_to_clear:
                os.environ.pop(var, None)
            
            config = load_market_config()
            
            assert config.mode == "paper"
            assert config.auto_trade is False
            assert config.max_symbols == 20
    
    def test_load_from_env(self):
        """Vérifie le chargement depuis variables d'environnement."""
        env = {
            "LUMENA_MARKETS_MODE": "paper",
            "LUMENA_MARKETS_AUTO_TRADE": "false",
            "LUMENA_MARKETS_MAX_SYMBOLS": "15",
            "LUMENA_MARKETS_SCAN_INTERVAL": "30",
            "LUMENA_MARKETS_MAX_GROK_CALLS_HOUR": "50",
            "IBKR_HOST": "192.168.1.100",
            "IBKR_PORT": "4001",
            "IBKR_CLIENT_ID": "5",
            "XAI_API_KEY": "xai-test-key-123",
        }
        
        with patch.dict(os.environ, env, clear=False):
            config = load_market_config()
            
            assert config.mode == "paper"
            assert config.auto_trade is False
            assert config.max_symbols == 15
            assert config.scan_interval == 30
            assert config.max_grok_calls_hour == 50
            assert config.ibkr_host == "192.168.1.100"
            assert config.ibkr_port == 4001
            assert config.ibkr_client_id == 5
            assert config.xai_api_key == "xai-test-key-123"
