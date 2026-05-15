"""
🚀 LUMENA - Background Task Manager

Permet de lancer des tâches longues en arrière-plan
sans bloquer le loop principal.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import subprocess
import json
from loguru import logger


class TaskStatus(Enum):
    """Statut d'une tâche background."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """Une tâche en arrière-plan."""
    id: str
    name: str
    command: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    output: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    process: Optional[asyncio.subprocess.Process] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "status": self.status.value,
            "output": self.output[-500:] if len(self.output) > 500 else self.output,  # Limite
            "error": self.error[-200:] if len(self.error) > 200 else self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self._get_duration()
        }
    
    def _get_duration(self) -> Optional[float]:
        """Calcule la durée de la tâche."""
        if not self.started_at:
            return None
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


class BackgroundTaskManager:
    """
    Gestionnaire de tâches en arrière-plan.
    
    Permet de:
    - Lancer des commandes shell sans bloquer
    - Suivre leur progression
    - Récupérer leur sortie
    - Les annuler si nécessaire
    """
    
    MAX_TASKS = 10  # Limite de tâches actives
    
    def __init__(self):
        self.tasks: Dict[str, BackgroundTask] = {}
        self._lock = asyncio.Lock()
        logger.info("🚀 BackgroundTaskManager initialisé")
    
    async def start_command(self, name: str, command: str) -> BackgroundTask:
        """
        Lance une commande shell en arrière-plan.
        
        Args:
            name: Nom descriptif de la tâche
            command: Commande shell à exécuter
            
        Returns:
            La tâche créée
        """
        async with self._lock:
            # Nettoyer les anciennes tâches si trop nombreuses
            await self._cleanup_old_tasks()
            
            # Créer la tâche
            task_id = str(uuid.uuid4())[:8]
            task = BackgroundTask(
                id=task_id,
                name=name,
                command=command,
                status=TaskStatus.PENDING
            )
            self.tasks[task_id] = task
        
        # Lancer en background
        asyncio.create_task(self._run_command(task))
        
        logger.info(f"🚀 Tâche {task_id} lancée: {name}")
        return task
    
    async def _run_command(self, task: BackgroundTask):
        """Exécute la commande de manière asynchrone."""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        
        try:
            # Créer le processus
            process = await asyncio.create_subprocess_shell(
                task.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            task.process = process
            
            # Attendre la fin
            stdout, stderr = await process.communicate()
            
            task.output = stdout.decode('utf-8', errors='replace')
            task.error = stderr.decode('utf-8', errors='replace')
            
            if process.returncode == 0:
                task.status = TaskStatus.COMPLETED
                logger.info(f"✅ Tâche {task.id} terminée avec succès")
            else:
                task.status = TaskStatus.FAILED
                logger.warning(f"❌ Tâche {task.id} échouée (code {process.returncode})")
                
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "Tâche annulée"
            logger.info(f"⚠️ Tâche {task.id} annulée")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"❌ Erreur tâche {task.id}: {e}")
        finally:
            task.completed_at = datetime.now()
            task.process = None
    
    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Récupère le statut d'une tâche."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        return task.to_dict()
    
    async def get_all_tasks(self) -> list:
        """Récupère toutes les tâches."""
        return [t.to_dict() for t in self.tasks.values()]
    
    async def cancel_task(self, task_id: str) -> bool:
        """Annule une tâche en cours."""
        task = self.tasks.get(task_id)
        if not task:
            return False
        
        if task.status != TaskStatus.RUNNING:
            return False
        
        if task.process and task.process.returncode is None:
            task.process.terminate()
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            logger.info(f"⚠️ Tâche {task_id} annulée")
            return True
        
        return False
    
    async def get_output(self, task_id: str) -> Optional[str]:
        """Récupère la sortie d'une tâche."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        return task.output
    
    async def _cleanup_old_tasks(self):
        """Nettoie les anciennes tâches terminées."""
        if len(self.tasks) <= self.MAX_TASKS:
            return
        
        # Trier par date de création
        sorted_tasks = sorted(
            self.tasks.values(),
            key=lambda t: t.created_at
        )
        
        # Supprimer les plus anciennes terminées
        for task in sorted_tasks:
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                del self.tasks[task.id]
                if len(self.tasks) <= self.MAX_TASKS:
                    break

# Singleton avec lock thread-safe (Phase 2.1)
import threading
_task_manager: Optional[BackgroundTaskManager] = None
_task_manager_lock = threading.Lock()


def get_task_manager() -> BackgroundTaskManager:
    """Retourne l'instance singleton du TaskManager (thread-safe)."""
    global _task_manager
    
    # Double-check locking pattern
    if _task_manager is None:
        with _task_manager_lock:
            if _task_manager is None:
                _task_manager = BackgroundTaskManager()
    return _task_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
