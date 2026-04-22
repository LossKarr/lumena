"""
🌟 LUMENA - Système de Hooks Event-Driven

Permet d'exécuter des actions automatiques en réponse à des événements.
Inspiré des hooks Git et des event handlers de frameworks modernes.
"""

from typing import Dict, Any, List, Optional, Callable, Awaitable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from pathlib import Path
import asyncio
import json
import os
from loguru import logger


class HookEvent(Enum):
    """Types d'événements pouvant déclencher des hooks."""
    # Événements de conversation
    CONVERSATION_START = "conversation_start"
    CONVERSATION_END = "conversation_end"
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    
    # Événements de fichiers
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    FILE_DELETED = "file_deleted"
    
    # Événements de code
    CODE_ERROR = "code_error"
    TEST_FAILED = "test_failed"
    TEST_PASSED = "test_passed"
    
    # Événements système
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    MEMORY_SAVED = "memory_saved"
    
    # Événements personnalisés
    CUSTOM = "custom"


@dataclass
class HookContext:
    """Contexte passé aux hooks lors de leur exécution."""
    event: HookEvent
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class Hook:
    """Définition d'un hook."""
    name: str
    event: HookEvent
    handler: Callable[[HookContext], Awaitable[Any]]
    priority: int = 5  # 1-10, 10 = haute priorité
    enabled: bool = True
    description: str = ""
    
    async def execute(self, context: HookContext) -> Any:
        """Exécute le hook."""
        if not self.enabled:
            return None
        
        try:
            return await self.handler(context)
        except Exception as e:
            logger.error(f"Erreur hook {self.name}: {e}")
            return None


class HookSystem:
    """
    🪝 Système de Hooks Event-Driven
    
    Permet d'enregistrer des handlers qui s'exécutent automatiquement
    en réponse à des événements du système.
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        self.hooks: Dict[HookEvent, List[Hook]] = {event: [] for event in HookEvent}
        self.config_path = config_path
        self._execution_log: List[Dict[str, Any]] = []
        
        # Charger la config si elle existe
        if config_path and config_path.exists():
            self._load_config()
        
        logger.info("🪝 HookSystem initialisé")
    
    def register(
        self,
        event: HookEvent,
        handler: Callable[[HookContext], Awaitable[Any]],
        name: Optional[str] = None,
        priority: int = 5,
        description: str = ""
    ) -> Hook:
        """
        Enregistre un nouveau hook.
        
        Args:
            event: L'événement déclencheur
            handler: Fonction async à appeler
            name: Nom du hook (auto-généré si absent)
            priority: Priorité (1-10)
            description: Description du hook
            
        Returns:
            Le hook créé
        """
        hook_name = name or f"hook_{event.value}_{len(self.hooks[event])}"
        
        # Vérifier si un hook avec ce nom existe déjà (prévenir doublons)
        for existing in self.hooks[event]:
            if existing.name == hook_name:
                logger.debug(f"🪝 Hook {hook_name} déjà enregistré, ignoré")
                return existing
        
        hook = Hook(
            name=hook_name,
            event=event,
            handler=handler,
            priority=priority,
            description=description
        )
        
        # Insérer selon la priorité (haute priorité = début)
        hooks_list = self.hooks[event]
        inserted = False
        for i, existing in enumerate(hooks_list):
            if hook.priority > existing.priority:
                hooks_list.insert(i, hook)
                inserted = True
                break
        if not inserted:
            hooks_list.append(hook)
        
        logger.debug(f"🪝 Hook enregistré: {hook_name} -> {event.value}")
        return hook
    
    def unregister(self, name: str) -> bool:
        """Supprime un hook par son nom."""
        for event, hooks in self.hooks.items():
            for hook in hooks:
                if hook.name == name:
                    hooks.remove(hook)
                    logger.debug(f"🪝 Hook supprimé: {name}")
                    return True
        return False
    
    async def trigger(self, event: HookEvent, data: Dict[str, Any] = None, source: str = "system") -> List[Any]:
        """
        Déclenche tous les hooks pour un événement.
        
        Args:
            event: L'événement à déclencher
            data: Données associées
            source: Source de l'événement
            
        Returns:
            Liste des résultats des hooks
        """
        context = HookContext(
            event=event,
            data=data or {},
            source=source
        )
        
        hooks = self.hooks.get(event, [])
        if not hooks:
            return []
        
        logger.debug(f"🪝 Trigger: {event.value} ({len(hooks)} hooks)")
        
        results = []
        for hook in hooks:
            if hook.enabled:
                result = await hook.execute(context)
                results.append(result)
                
                # Logger l'exécution (cap à 200 entrées)
                self._execution_log.append({
                    "hook": hook.name,
                    "event": event.value,
                    "timestamp": datetime.now().isoformat(),
                    "success": result is not None
                })
                if len(self._execution_log) > 200:
                    self._execution_log = self._execution_log[-200:]
        
        return results
    
    def enable(self, name: str) -> bool:
        """Active un hook."""
        for hooks in self.hooks.values():
            for hook in hooks:
                if hook.name == name:
                    hook.enabled = True
                    return True
        return False
    
    def disable(self, name: str) -> bool:
        """Désactive un hook."""
        for hooks in self.hooks.values():
            for hook in hooks:
                if hook.name == name:
                    hook.enabled = False
                    return True
        return False
    
    def get_hooks(self, event: Optional[HookEvent] = None) -> List[Hook]:
        """Retourne les hooks (tous ou pour un événement)."""
        if event:
            return self.hooks.get(event, [])
        return [hook for hooks in self.hooks.values() for hook in hooks]
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du système."""
        total_hooks = sum(len(hooks) for hooks in self.hooks.values())
        enabled = sum(1 for hooks in self.hooks.values() for h in hooks if h.enabled)
        
        return {
            "total_hooks": total_hooks,
            "enabled_hooks": enabled,
            "disabled_hooks": total_hooks - enabled,
            "recent_executions": len(self._execution_log),
            "events_with_hooks": sum(1 for hooks in self.hooks.values() if hooks)
        }
    
    def _load_config(self):
        """Charge la configuration des hooks."""
        try:
            if self.config_path and self.config_path.exists():
                config = json.loads(self.config_path.read_text(encoding='utf-8'))
                # Les hooks de config sont juste des références,
                # les handlers réels doivent être enregistrés par le code
                logger.debug(f"Config hooks chargée: {len(config.get('hooks', []))} entrées")
        except Exception as e:
            logger.warning(f"Erreur chargement config hooks: {e}")


# Singleton
_hook_system: Optional[HookSystem] = None


def get_hook_system(config_path: Optional[Path] = None) -> HookSystem:
    """Retourne l'instance globale du système de hooks."""
    global _hook_system
    if _hook_system is None:
        _hook_system = HookSystem(config_path)
    return _hook_system


# === HOOKS PRÉDÉFINIS ===

async def log_message_hook(ctx: HookContext) -> None:
    """Hook pour logger les messages."""
    message = ctx.get("message", "")
    role = ctx.get("role", "unknown")
    logger.info(f"📨 [{role}] {message[:100]}...")


async def auto_save_memory_hook(ctx: HookContext) -> None:
    """Hook pour sauvegarder automatiquement en mémoire."""
    message = ctx.get("message", "")
    if len(message) > 50:  # Seulement les messages significatifs
        logger.debug("💾 Auto-save memory triggered")


async def startup_hook(ctx: HookContext) -> None:
    """Hook de démarrage."""
    logger.info("🚀 Lumena startup hook triggered")


async def shutdown_hook(ctx: HookContext) -> None:
    """Hook d'arrêt."""
    logger.info("👋 Lumena shutdown hook triggered")


def register_default_hooks(hook_system: HookSystem):
    """Enregistre les hooks par défaut."""
    hook_system.register(
        HookEvent.STARTUP,
        startup_hook,
        name="default_startup",
        priority=10,
        description="Log le démarrage"
    )
    
    hook_system.register(
        HookEvent.SHUTDOWN,
        shutdown_hook,
        name="default_shutdown",
        priority=10,
        description="Log l'arrêt"
    )
    
    hook_system.register(
        HookEvent.MESSAGE_RECEIVED,
        log_message_hook,
        name="log_received",
        priority=1,
        description="Log les messages reçus"
    )


if __name__ == "__main__":
    import asyncio
    
    async def test():
        hs = HookSystem()
        register_default_hooks(hs)
        
        print(f"Stats: {hs.get_stats()}")
        
        # Tester le trigger
        await hs.trigger(HookEvent.STARTUP)
        await hs.trigger(HookEvent.MESSAGE_RECEIVED, {"message": "Test message", "role": "user"})
        
        print("Hooks OK!")
    
    asyncio.run(test())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
