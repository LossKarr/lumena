"""
🌙 LUMENA - Graceful Degradation (Phase 6.2)

Gère la dégradation gracieuse des dépendances optionnelles.
Permet à Lumena de fonctionner même si certains modules ne sont pas installés.
"""

import os
import sys
import threading
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class ModuleStatus:
    """Statut d'un module optionnel."""
    name: str
    available: bool
    feature: str
    error: Optional[str] = None
    fallback_available: bool = True


@dataclass
class DependencyReport:
    """Rapport de disponibilité des dépendances."""
    modules: List[ModuleStatus] = field(default_factory=list)
    
    @property
    def all_available(self) -> bool:
        """Retourne True si tous les modules sont disponibles."""
        return all(m.available for m in self.modules)
    
    @property
    def missing(self) -> List[str]:
        """Retourne la liste des modules manquants."""
        return [m.name for m in self.modules if not m.available]
    
    @property
    def features_degraded(self) -> List[str]:
        """Retourne les features dégradées."""
        return list(set(m.feature for m in self.modules if not m.available))
    
    def summary(self) -> str:
        """Génère un résumé textuel."""
        available = len([m for m in self.modules if m.available])
        total = len(self.modules)
        
        if self.all_available:
            return f"✅ Toutes les {total} dépendances optionnelles disponibles"
        else:
            missing = len(self.missing)
            return f"⚠️ {missing}/{total} dépendances manquantes: {', '.join(self.features_degraded)}"


class GracefulDegradation:
    """
    Gestionnaire de dégradation gracieuse.
    
    Vérifie les dépendances optionnelles et fournit des fallbacks.
    """
    
    # Mapping des modules vers leurs features
    MODULE_FEATURES = {
        # Memory
        "chromadb": "memory",
        
        # Voice
        "whisper": "voice_stt",
        "openai_whisper": "voice_stt",
        "faster_whisper": "voice_stt",
        "pygame": "voice_tts",
        "sounddevice": "voice_recording",
        "pyaudio": "voice_recording",
        
        # Browser automation
        "playwright": "browser_playwright",
        "selenium": "browser_selenium",
        
        # Computer use
        "pyautogui": "computer_use",
        "mss": "screen_capture",
        "PIL": "image_processing",
        "pillow": "image_processing",
        
        # Channels
        "telegram": "telegram",
        "discord": "discord",
        "whatsapp": "whatsapp",
        
        # Hosting
        "paramiko": "ionos",
        
        # Utilities
        "psutil": "system_monitoring",
        "filelock": "file_locking",
    }
    
    _instance: Optional["GracefulDegradation"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._module_cache: Dict[str, bool] = {}
        self._report: Optional[DependencyReport] = None
    
    def check_module(self, module_name: str) -> bool:
        """
        Vérifie si un module est disponible.
        
        Args:
            module_name: Nom du module à vérifier
            
        Returns:
            True si le module est importable
        """
        if module_name in self._module_cache:
            return self._module_cache[module_name]
        
        try:
            __import__(module_name)
            self._module_cache[module_name] = True
            return True
        except ImportError:
            self._module_cache[module_name] = False
            return False
    
    def check_all(self) -> DependencyReport:
        """
        Vérifie toutes les dépendances optionnelles.
        
        Returns:
            Rapport de disponibilité
        """
        modules = []
        
        for module_name, feature in self.MODULE_FEATURES.items():
            try:
                __import__(module_name)
                modules.append(ModuleStatus(
                    name=module_name,
                    available=True,
                    feature=feature
                ))
            except ImportError as e:
                modules.append(ModuleStatus(
                    name=module_name,
                    available=False,
                    feature=feature,
                    error=str(e)
                ))
        
        self._report = DependencyReport(modules=modules)
        return self._report
    
    def get_report(self) -> DependencyReport:
        """Retourne le dernier rapport (ou en génère un nouveau)."""
        if self._report is None:
            self.check_all()
        return self._report
    
    def is_feature_available(self, feature: str) -> bool:
        """
        Vérifie si une feature est disponible.
        
        Args:
            feature: Nom de la feature (ex: "voice", "browser")
            
        Returns:
            True si AU MOINS UN des modules requis est disponible
        """
        required = [m for m, f in self.MODULE_FEATURES.items() if f == feature]
        if not required: return True
        return any(self.check_module(m) for m in required)
    
    def log_startup_status(self):
        """Log le statut des dépendances au démarrage."""
        report = self.check_all()
        
        # Filtrer le résumé pour ne pas effrayer l'utilisateur si une feature a au moins un module
        actual_degraded = []
        for feature in list(report.features_degraded):
            if not self.is_feature_available(feature):
                actual_degraded.append(feature)
        
        if not actual_degraded:
            logger.info(f"✅ Toutes les features critiques sont disponibles ({len(report.modules)} modules testés)")
        else:
            logger.info(f"⚠️ {len(actual_degraded)} feature(s) dégradée(s): {', '.join(actual_degraded)}")
        
        # Log uniquement les features réellement indisponibles
        for feature in actual_degraded:
            modules = [m.name for m in report.modules 
                      if m.feature == feature]
            logger.warning(f"  ❌ Feature '{feature}' désactivée (manque: {', '.join(modules)})")
    
    def get_fallback_message(self, feature: str) -> str:
        """
        Retourne un message de fallback pour une feature manquante.
        
        Args:
            feature: Nom de la feature
            
        Returns:
            Message explicatif
        """
        messages = {
            "memory": "Mémoire ChromaDB non disponible. Utilisation de la mémoire volatile (non persistante).",
            "voice_stt": "Reconnaissance vocale non disponible. Installez openai-whisper pour l'activer.",
            "voice_tts": "Synthèse vocale non disponible. Installez pygame pour l'activer.",
            "browser_playwright": "Automatisation browser Playwright non disponible.",
            "browser_selenium": "Automatisation browser Selenium non disponible.",
            "computer_use": "Contrôle d'ordinateur non disponible. Installez pyautogui.",
            "telegram": "Canal Telegram désactivé. Installez python-telegram-bot.",
            "discord": "Canal Discord désactivé. Installez discord.py.",
            "whatsapp": "Canal WhatsApp désactivé. Configurez WHATSAPP_ACCESS_TOKEN et WHATSAPP_PHONE_NUMBER_ID.",
            "system_monitoring": "Monitoring système limité. Installez psutil.",
            "file_locking": "Verrouillage fichier désactivé. Installez filelock.",
        }
        return messages.get(feature, f"Feature '{feature}' non disponible.")


# Singleton global
def get_graceful_degradation() -> GracefulDegradation:
    """Retourne l'instance singleton."""
    return GracefulDegradation()


def check_dependencies() -> DependencyReport:
    """Helper pour vérifier toutes les dépendances."""
    return get_graceful_degradation().check_all()


def log_startup_dependencies():
    """Helper pour logger les dépendances au démarrage."""
    get_graceful_degradation().log_startup_status()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
