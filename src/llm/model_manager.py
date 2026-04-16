"""
🔄 LUMENA - Model Manager avec Failover

Gère la rotation et le failover automatique entre modèles LLM.

Fonctionnalités:
- Failover automatique si un modèle échoue
- Gestion des quotas/rate limits
- Rotation intelligente
- Health checks
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import asyncio
from loguru import logger


class ModelStatus(Enum):
    """État d'un modèle."""
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class ModelHealth:
    """Informations de santé d'un modèle."""
    model_name: str
    provider: str
    status: ModelStatus = ModelStatus.AVAILABLE
    last_success: Optional[datetime] = None
    last_error: Optional[datetime] = None
    error_count: int = 0
    rate_limit_until: Optional[datetime] = None
    request_count: int = 0
    
    def is_available(self) -> bool:
        """Vérifie si le modèle est disponible."""
        if self.status == ModelStatus.DISABLED:
            return False
        
        if self.status == ModelStatus.RATE_LIMITED:
            if self.rate_limit_until and datetime.now() < self.rate_limit_until:
                return False
            # Rate limit expiré
            self.status = ModelStatus.AVAILABLE
        
        # Trop d'erreurs récentes ?
        if self.error_count >= 3:
            # Attendre 5 minutes avant de réessayer
            if self.last_error and datetime.now() - self.last_error < timedelta(minutes=5):
                return False
            # Reset après 5 minutes
            self.error_count = 0
        
        return True
    
    def record_success(self):
        """Enregistre un succès."""
        self.last_success = datetime.now()
        self.error_count = 0
        self.request_count += 1
        self.status = ModelStatus.AVAILABLE
    
    def record_error(self, error_msg: str = ""):
        """Enregistre une erreur."""
        self.last_error = datetime.now()
        self.error_count += 1
        
        # Détecter rate limiting
        if "rate" in error_msg.lower() or "quota" in error_msg.lower() or "429" in error_msg:
            self.status = ModelStatus.RATE_LIMITED
            self.rate_limit_until = datetime.now() + timedelta(minutes=1)
        elif self.error_count >= 3:
            self.status = ModelStatus.ERROR


class ModelManager:
    """
    Gestionnaire de modèles avec failover automatique.
    
    Permet de:
    - Basculer automatiquement vers un modèle de backup en cas d'erreur
    - Gérer les quotas et rate limits
    - Monitorer la santé des modèles
    """
    
    # Ordre de préférence par défaut (du plus préféré au moins)
    DEFAULT_FALLBACK_CHAIN = [
        "gemini-2.5-flash",
        "gpt-4o-mini",
        "claude-3-5-haiku",
        "qwen3-8b"  # Ollama local comme dernier recours
    ]
    
    def __init__(self, primary_model: str = "gemini-2.5-flash"):
        """
        Initialise le manager.
        
        Args:
            primary_model: Modèle principal à utiliser
        """
        self.primary_model = primary_model
        self.current_model = primary_model
        self.model_health: Dict[str, ModelHealth] = {}
        self.fallback_chain = self.DEFAULT_FALLBACK_CHAIN.copy()
        
        # S'assurer que le modèle principal est en premier
        if primary_model in self.fallback_chain:
            self.fallback_chain.remove(primary_model)
        self.fallback_chain.insert(0, primary_model)
        
        # Initialiser la santé des modèles
        for model in self.fallback_chain:
            provider = self._detect_provider(model)
            self.model_health[model] = ModelHealth(model_name=model, provider=provider)
    
    def _detect_provider(self, model_name: str) -> str:
        """Détecte le provider d'un modèle."""
        model_lower = model_name.lower()
        
        if "gemini" in model_lower:
            return "google"
        elif "gpt" in model_lower or "o1" in model_lower:
            return "openai"
        elif "claude" in model_lower:
            return "anthropic"
        elif "moonshot" in model_lower or "kimi" in model_lower:
            return "moonshot"
        else:
            return "ollama"  # Par défaut, Ollama local
    
    def get_next_available_model(self) -> Optional[str]:
        """
        Retourne le prochain modèle disponible dans la chaîne de fallback.
        
        Returns:
            Nom du modèle ou None si aucun disponible
        """
        for model in self.fallback_chain:
            health = self.model_health.get(model)
            if health and health.is_available():
                return model
        return None
    
    def record_success(self, model: str):
        """Enregistre un succès pour un modèle."""
        if model in self.model_health:
            self.model_health[model].record_success()
            self.current_model = model
            logger.debug(f"✅ Succès {model}")
    
    def record_error(self, model: str, error_msg: str = ""):
        """
        Enregistre une erreur et retourne le modèle de fallback.
        
        Returns:
            Nom du modèle de fallback ou None
        """
        if model in self.model_health:
            self.model_health[model].record_error(error_msg)
            logger.warning(f"⚠️ Erreur {model}: {error_msg[:50]}")
        
        # Trouver un fallback
        for backup_model in self.fallback_chain:
            if backup_model != model:
                health = self.model_health.get(backup_model)
                if health and health.is_available():
                    logger.info(f"🔄 Failover: {model} → {backup_model}")
                    self.current_model = backup_model
                    return backup_model
        
        return None
    
    def get_current_model(self) -> str:
        """Retourne le modèle actuellement sélectionné."""
        return self.current_model
    
    def set_primary_model(self, model: str):
        """Change le modèle principal."""
        self.primary_model = model
        self.current_model = model
        
        if model not in self.model_health:
            provider = self._detect_provider(model)
            self.model_health[model] = ModelHealth(model_name=model, provider=provider)
        
        # Mettre en premier dans la chaîne
        if model in self.fallback_chain:
            self.fallback_chain.remove(model)
        self.fallback_chain.insert(0, model)
    
    def add_to_fallback_chain(self, model: str, position: int = -1):
        """Ajoute un modèle à la chaîne de fallback."""
        if model not in self.fallback_chain:
            if position == -1:
                self.fallback_chain.append(model)
            else:
                self.fallback_chain.insert(position, model)
        
        if model not in self.model_health:
            provider = self._detect_provider(model)
            self.model_health[model] = ModelHealth(model_name=model, provider=provider)
    
    def disable_model(self, model: str):
        """Désactive un modèle."""
        if model in self.model_health:
            self.model_health[model].status = ModelStatus.DISABLED
            logger.info(f"🚫 Modèle désactivé: {model}")
    
    def enable_model(self, model: str):
        """Réactive un modèle."""
        if model in self.model_health:
            self.model_health[model].status = ModelStatus.AVAILABLE
            self.model_health[model].error_count = 0
            logger.info(f"✅ Modèle réactivé: {model}")
    
    def get_health_report(self) -> Dict[str, Any]:
        """Retourne un rapport de santé de tous les modèles."""
        report = {
            "current_model": self.current_model,
            "primary_model": self.primary_model,
            "models": {}
        }
        
        for model, health in self.model_health.items():
            report["models"][model] = {
                "status": health.status.value,
                "provider": health.provider,
                "available": health.is_available(),
                "request_count": health.request_count,
                "error_count": health.error_count,
                "last_success": health.last_success.isoformat() if health.last_success else None,
                "last_error": health.last_error.isoformat() if health.last_error else None
            }
        
        return report
    
    def reset_all(self):
        """Reset la santé de tous les modèles."""
        for health in self.model_health.values():
            health.status = ModelStatus.AVAILABLE
            health.error_count = 0
            health.rate_limit_until = None


# Singleton global
_model_manager: Optional[ModelManager] = None


def get_model_manager(primary_model: str = "gemini-2.5-flash") -> ModelManager:
    """Retourne l'instance globale du gestionnaire de modèles."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager(primary_model)
    return _model_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
