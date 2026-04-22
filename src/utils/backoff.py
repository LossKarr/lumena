"""
⏰ LUMENA - Backoff Exponentiel (Phase 4.1)

Utilitaire pour le retry avec backoff exponentiel.
Utilisé pour les appels réseau, API, etc.
"""

import asyncio
import random
import time
from typing import TypeVar, Callable, Optional, Any
from functools import wraps
from loguru import logger

T = TypeVar('T')


class BackoffConfig:
    """Configuration du backoff."""
    
    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        max_retries: int = 5,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: tuple = (Exception,)
    ):
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_exceptions = retryable_exceptions


# Configuration par défaut
DEFAULT_CONFIG = BackoffConfig()


def calculate_delay(
    attempt: int,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True
) -> float:
    """
    Calcule le délai pour une tentative donnée.
    
    Args:
        attempt: Numéro de tentative (0-indexed)
        initial_delay: Délai initial en secondes
        max_delay: Délai maximum en secondes
        exponential_base: Base exponentielle (default 2.0)
        jitter: Ajouter un bruit aléatoire
        
    Returns:
        Délai en secondes
    """
    delay = initial_delay * (exponential_base ** attempt)
    delay = min(delay, max_delay)
    
    if jitter:
        # Jitter de ±25%
        delay = delay * (0.75 + random.random() * 0.5)
    
    return delay


async def retry_async(
    func: Callable[..., T],
    *args,
    config: Optional[BackoffConfig] = None,
    **kwargs
) -> T:
    """
    Exécute une fonction async avec retry et backoff exponentiel.
    
    Args:
        func: Fonction async à exécuter
        *args: Arguments positionnels
        config: Configuration du backoff
        **kwargs: Arguments nommés
        
    Returns:
        Résultat de la fonction
        
    Raises:
        La dernière exception si toutes les tentatives échouent
    """
    config = config or DEFAULT_CONFIG
    last_exception = None
    
    for attempt in range(config.max_retries):
        try:
            return await func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e
            
            if attempt < config.max_retries - 1:
                delay = calculate_delay(
                    attempt,
                    config.initial_delay,
                    config.max_delay,
                    config.exponential_base,
                    config.jitter
                )
                logger.warning(
                    f"Retry {attempt + 1}/{config.max_retries} après {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Échec après {config.max_retries} tentatives: {e}")
    
    raise last_exception


def retry_sync(
    func: Callable[..., T],
    *args,
    config: Optional[BackoffConfig] = None,
    **kwargs
) -> T:
    """
    Exécute une fonction sync avec retry et backoff exponentiel.
    """
    config = config or DEFAULT_CONFIG
    last_exception = None
    
    for attempt in range(config.max_retries):
        try:
            return func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e
            
            if attempt < config.max_retries - 1:
                delay = calculate_delay(
                    attempt,
                    config.initial_delay,
                    config.max_delay,
                    config.exponential_base,
                    config.jitter
                )
                logger.warning(
                    f"Retry {attempt + 1}/{config.max_retries} après {delay:.2f}s: {e}"
                )
                time.sleep(delay)
            else:
                logger.error(f"Échec après {config.max_retries} tentatives: {e}")
    
    raise last_exception


def with_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,)
):
    """
    Décorateur pour ajouter retry avec backoff à une fonction async.
    
    Usage:
        @with_retry(max_retries=3)
        async def fetch_data():
            ...
    """
    config = BackoffConfig(
        initial_delay=initial_delay,
        max_delay=max_delay,
        max_retries=max_retries,
        retryable_exceptions=retryable_exceptions
    )
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_async(func, *args, config=config, **kwargs)
        return wrapper
    
    return decorator


# Configurations pré-définies pour cas courants
API_CONFIG = BackoffConfig(
    initial_delay=1.0,
    max_delay=30.0,
    max_retries=3,
    retryable_exceptions=(ConnectionError, TimeoutError)
)

LLM_CONFIG = BackoffConfig(
    initial_delay=2.0,
    max_delay=60.0,
    max_retries=5,
    retryable_exceptions=(ConnectionError, TimeoutError, Exception)
)

DATABASE_CONFIG = BackoffConfig(
    initial_delay=0.5,
    max_delay=10.0,
    max_retries=3,
)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
