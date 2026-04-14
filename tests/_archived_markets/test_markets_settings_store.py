"""
Tests: Settings Store.

Tests pour la persistance atomique des settings du dashboard.
"""

import json
import os
import pytest
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from src.markets.dashboard.models import DashboardSettings
from src.markets.dashboard.settings_store import SettingsStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_settings_path(tmp_path):
    """Crée un chemin temporaire pour les settings."""
    return tmp_path / "state" / "settings.json"


@pytest.fixture
def store(temp_settings_path):
    """Crée un SettingsStore avec chemin temporaire."""
    return SettingsStore(path=temp_settings_path)


# =============================================================================
# TEST: CREATION ET LOAD
# =============================================================================

class TestSettingsStoreBasic:
    """Tests de base du SettingsStore."""
    
    def test_creates_directory_if_missing(self, temp_settings_path):
        """Le store crée le répertoire parent si absent."""
        assert not temp_settings_path.parent.exists()
        
        store = SettingsStore(path=temp_settings_path)
        
        assert temp_settings_path.parent.exists()
    
    def test_load_returns_defaults_if_file_missing(self, store, temp_settings_path):
        """Load retourne les valeurs par défaut si le fichier n'existe pas."""
        assert not temp_settings_path.exists()
        
        settings = store.load()
        
        assert settings.ai_auto_run_enabled is False
        assert settings.ai_auto_run_interval_seconds == 60
        assert settings.parallel_agents == 1
    
    def test_load_creates_file_with_defaults(self, store, temp_settings_path):
        """Load crée le fichier avec les valeurs par défaut."""
        assert not temp_settings_path.exists()
        
        store.load()
        
        assert temp_settings_path.exists()
    
    def test_exists_returns_false_initially(self, store, temp_settings_path):
        """exists() retourne False si le fichier n'existe pas."""
        assert store.exists() is False
    
    def test_exists_returns_true_after_save(self, store):
        """exists() retourne True après un save."""
        store.save(DashboardSettings())
        
        assert store.exists() is True


# =============================================================================
# TEST: SAVE ET LOAD CYCLE
# =============================================================================

class TestSettingsStoreSaveLoad:
    """Tests du cycle save/load."""
    
    def test_save_and_load_preserves_data(self, store):
        """Save puis load préserve les données."""
        original = DashboardSettings(
            ai_auto_run_enabled=True,
            ai_auto_run_interval_seconds=120,
            parallel_agents=3,
            timezone="Europe/Paris",
        )
        
        store.save(original)
        loaded = store.load(use_cache=False)
        
        assert loaded.ai_auto_run_enabled is True
        assert loaded.ai_auto_run_interval_seconds == 120
        assert loaded.parallel_agents == 3
        assert loaded.timezone == "Europe/Paris"
    
    def test_save_updates_timestamp(self, store):
        """Save met à jour updated_at."""
        settings = DashboardSettings()
        original_time = settings.updated_at
        
        # Attendre un peu
        import time
        time.sleep(0.01)
        
        store.save(settings)
        loaded = store.load(use_cache=False)
        
        assert loaded.updated_at > original_time
    
    def test_save_is_atomic(self, store, temp_settings_path):
        """Save est atomique (pas de fichier partiel)."""
        # Sauvegarder des settings valides
        store.save(DashboardSettings(parallel_agents=5))
        
        # Vérifier que le fichier est lisible
        with open(temp_settings_path, "r") as f:
            data = json.load(f)
        
        assert data["parallel_agents"] == 5
    
    def test_corrupted_file_resets_to_defaults(self, store, temp_settings_path):
        """Un fichier corrompu est réinitialisé aux valeurs par défaut."""
        # Créer un fichier corrompu
        temp_settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_settings_path, "w") as f:
            f.write("not valid json {{{")
        
        # Load doit fonctionner et retourner les defaults
        settings = store.load()
        
        assert settings.ai_auto_run_enabled is False
        assert settings.parallel_agents == 1


# =============================================================================
# TEST: UPDATE ET RESET
# =============================================================================

class TestSettingsStoreUpdate:
    """Tests de update et reset."""
    
    def test_update_modifies_specific_fields(self, store):
        """update() modifie seulement les champs spécifiés."""
        store.save(DashboardSettings(
            ai_auto_run_enabled=False,
            parallel_agents=2,
        ))
        
        updated = store.update(ai_auto_run_enabled=True)
        
        assert updated.ai_auto_run_enabled is True
        assert updated.parallel_agents == 2  # Non modifié
    
    def test_update_updates_timestamp(self, store):
        """update() met à jour updated_at."""
        store.save(DashboardSettings())
        original = store.load()
        
        import time
        time.sleep(0.01)
        
        updated = store.update(parallel_agents=5)
        
        assert updated.updated_at > original.updated_at
    
    def test_reset_returns_defaults(self, store):
        """reset() retourne les valeurs par défaut."""
        store.save(DashboardSettings(
            ai_auto_run_enabled=True,
            parallel_agents=10,
        ))
        
        reset = store.reset()
        
        assert reset.ai_auto_run_enabled is False
        assert reset.parallel_agents == 1
    
    def test_reset_overwrites_file(self, store):
        """reset() écrase le fichier avec les defaults."""
        store.save(DashboardSettings(parallel_agents=10))
        store.reset()
        
        loaded = store.load(use_cache=False)
        
        assert loaded.parallel_agents == 1


# =============================================================================
# TEST: CACHE
# =============================================================================

class TestSettingsStoreCache:
    """Tests du cache."""
    
    def test_load_uses_cache_by_default(self, store):
        """load() utilise le cache par défaut."""
        store.save(DashboardSettings(parallel_agents=3))
        
        # Premier load
        first = store.load()
        assert first.parallel_agents == 3
        
        # Modifier le fichier directement
        store._path.write_text('{"parallel_agents": 99}')
        
        # Le cache devrait être utilisé
        second = store.load()
        assert second.parallel_agents == 3  # Pas 99
    
    def test_load_bypasses_cache_when_requested(self, store):
        """load(use_cache=False) ignore le cache."""
        store.save(DashboardSettings(parallel_agents=3))
        store.load()  # Remplir le cache
        
        # Modifier le fichier avec une valeur valide (1-10)
        data = {"parallel_agents": 8, "ai_auto_run_enabled": False}
        store._path.write_text(json.dumps(data))
        
        # Forcer le reload
        reloaded = store.load(use_cache=False)
        assert reloaded.parallel_agents == 8


# =============================================================================
# TEST: CONCURRENCY
# =============================================================================

class TestSettingsStoreConcurrency:
    """Tests de concurrence."""
    
    def test_concurrent_saves_dont_corrupt(self, temp_settings_path):
        """Des saves concurrents ne corrompent pas le fichier."""
        store = SettingsStore(path=temp_settings_path)
        errors = []
        
        def save_with_value(value):
            try:
                for _ in range(10):
                    store.save(DashboardSettings(parallel_agents=value))
            except Exception as e:
                errors.append(e)
        
        # Lancer plusieurs threads
        threads = [
            threading.Thread(target=save_with_value, args=(i,))
            for i in range(1, 6)
        ]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # Aucune erreur
        assert len(errors) == 0
        
        # Le fichier est valide
        loaded = store.load(use_cache=False)
        assert 1 <= loaded.parallel_agents <= 5


# =============================================================================
# TEST: VALIDATION
# =============================================================================

class TestSettingsValidation:
    """Tests de validation des settings."""
    
    def test_interval_minimum_enforced(self):
        """L'intervalle minimum est respecté."""
        with pytest.raises(ValueError):
            DashboardSettings(ai_auto_run_interval_seconds=5)  # Min = 10
    
    def test_parallel_agents_minimum_enforced(self):
        """Le nombre minimum d'agents parallèles est respecté."""
        with pytest.raises(ValueError):
            DashboardSettings(parallel_agents=0)  # Min = 1
    
    def test_operating_hours_format_validated(self):
        """Le format des heures d'opération est validé."""
        # Format valide
        settings = DashboardSettings(operating_hours_start="09:30")
        assert settings.operating_hours_start == "09:30"
        
        # Format invalide
        with pytest.raises(ValueError):
            DashboardSettings(operating_hours_start="9:30")  # Manque le 0
        
        with pytest.raises(ValueError):
            DashboardSettings(operating_hours_start="25:00")  # Heure invalide
    
    def test_with_update_creates_copy(self):
        """with_update() crée une copie modifiée."""
        original = DashboardSettings(parallel_agents=2)
        updated = original.with_update(parallel_agents=5)
        
        assert original.parallel_agents == 2
        assert updated.parallel_agents == 5
