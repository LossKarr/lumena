"""
🧪 Test de validation CRON (Phase 5.2)

Vérifie que les expressions CRON sont validées.
"""

import pytest


class TestCronValidation:
    """Tests de validation des expressions CRON."""
    
    def test_validate_cron_expr_valid(self):
        """Vérifie que les expressions valides passent."""
        from src.autonomy.scheduler import validate_cron_expr
        
        # Expressions valides
        assert validate_cron_expr("*/5 * * * *") is True
        assert validate_cron_expr("0 9 * * 1-5") is True
        assert validate_cron_expr("0 0 1 * *") is True
        assert validate_cron_expr("30 8 * * *") is True
    
    def test_validate_cron_expr_invalid(self):
        """Vérifie que les expressions invalides échouent."""
        from src.autonomy.scheduler import validate_cron_expr
        
        # Expressions invalides (with_error=True pour récupérer le message)
        is_valid, error = validate_cron_expr("invalid", with_error=True)
        assert is_valid is False
        assert error is not None
        
        is_valid, _ = validate_cron_expr("", with_error=True)
        assert is_valid is False
        
        is_valid, _ = validate_cron_expr("* * *", with_error=True)
        assert is_valid is False
    
    def test_validate_cron_expr_edge_cases(self):
        """Vérifie les cas limites."""
        from src.autonomy.scheduler import validate_cron_expr
        
        # Cas limites
        assert validate_cron_expr("0 0 * * *") is True  # Minuit
        assert validate_cron_expr("59 23 * * *") is True  # 23:59
        assert validate_cron_expr("* * * * *") is True  # Toutes les minutes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
