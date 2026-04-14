"""
🌟 LUMENA - Système Heartbeat (Battement de Coeur)

Inspiré de Moltbot : permet à Lumena de tourner en autonomie
en vérifiant périodiquement s'il y a des tâches à faire.

Le fichier HEARTBEAT.md contient les tâches que Lumena doit surveiller.
"""

import asyncio
from pathlib import Path
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Callable, List, Dict, Any
from dataclasses import dataclass, field
from loguru import logger
import json
from ..utils.persistence import atomic_write_json, atomic_write_text


@dataclass
class HeartbeatTask:
    """Une tâche définie dans HEARTBEAT.md"""
    description: str
    interval_minutes: int = 30
    last_run: Optional[datetime] = None
    enabled: bool = True
    action_type: str = "check"  # check, execute, remind


@dataclass  
class HeartbeatConfig:
    """Configuration du heartbeat"""
    enabled: bool = True
    interval_minutes: int = 30  # Utilisé uniquement si scheduled_hours est vide
    heartbeat_file: str = "HEARTBEAT.md"
    tasks: List[HeartbeatTask] = field(default_factory=list)
    scheduled_hours: List[int] = field(default_factory=list)  # Ex: [6, 18] → 2×/jour


class HeartbeatSystem:
    """
    Système de battement de cœur pour l'autonomie de Lumena.
    
    Vérifie périodiquement s'il y a des tâches à faire.
    """
    
    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        on_task_callback: Optional[Callable[[str], Any]] = None,
        on_thinking_callback: Optional[Callable[[str], None]] = None
    ):
        self.workspace_dir = workspace_dir or Path.cwd()
        self.config = HeartbeatConfig()
        self.on_task = on_task_callback
        self.on_thinking = on_thinking_callback
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.last_heartbeat: Optional[datetime] = None
        self.heartbeat_count = 0

        # État persistant : survit aux redémarrages (v1.0.1)
        from src.utils.paths import HEARTBEAT_STATE_JSON
        self._state_file = HEARTBEAT_STATE_JSON
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_state()

        # Charger le fichier HEARTBEAT.md s'il existe
        self._load_heartbeat_file()
    
    def _load_state(self):
        """Charge l'état persistant (last_run timestamps) depuis le disque."""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                raw_last = data.get("last_heartbeat")
                if raw_last:
                    self.last_heartbeat = datetime.fromisoformat(raw_last)
                self.heartbeat_count = data.get("heartbeat_count", 0)
                logger.debug(f"💓 État heartbeat restauré : #{self.heartbeat_count}, dernier: {self.last_heartbeat}")
        except Exception as e:
            logger.debug(f"💓 Pas d'état heartbeat précédent ({e})")

    def _save_state(self):
        """Persiste l'état courant sur le disque (survit aux redémarrages)."""
        try:
            data = {
                "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
                "heartbeat_count": self.heartbeat_count,
                "saved_at": datetime.now().isoformat(),
            }
            atomic_write_json(self._state_file, data)
        except Exception as e:
            logger.debug(f"💓 Erreur sauvegarde état heartbeat: {e}")

    def _load_heartbeat_file(self):
        """Charge les tâches depuis HEARTBEAT.md"""
        heartbeat_path = self.workspace_dir / self.config.heartbeat_file
        
        if not heartbeat_path.exists():
            # Créer un fichier par défaut
            self._create_default_heartbeat_file(heartbeat_path)
            return
        
        try:
            content = heartbeat_path.read_text(encoding='utf-8')
            self._parse_heartbeat_content(content)
            logger.info(f"💓 {len(self.config.tasks)} tâches heartbeat chargées")
        except Exception as e:
            logger.error(f"Erreur lecture HEARTBEAT.md: {e}")
    
    def _create_default_heartbeat_file(self, path: Path):
        """Crée un fichier HEARTBEAT.md par défaut"""
        default_content = """# 💓 HEARTBEAT.md - Tâches Autonomes de Lumena

# Ce fichier définit les tâches que Lumena vérifie périodiquement.
# Format: Une ligne par tâche, préfixée par - ou *

# Exemple de tâches (décommentez pour activer):
# - Vérifie si l'utilisateur a besoin d'aide
# - Regarde s'il y a des fichiers modifiés récemment
# - Check les rappels en attente

# Pour désactiver une tâche, commentez-la avec #

# Configuration:
# interval: 30  # minutes entre chaque check (par défaut: 30)

# Laissez ce fichier vide (ou avec seulement des commentaires) 
# pour désactiver les heartbeats.
"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, default_content)
            logger.info(f"💓 Fichier HEARTBEAT.md créé: {path}")
        except Exception as e:
            logger.error(f"Impossible de créer HEARTBEAT.md: {e}")
    
    def _parse_heartbeat_content(self, content: str):
        """Parse le contenu de HEARTBEAT.md"""
        self.config.tasks.clear()
        
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            
            # Ignorer les lignes vides et commentaires
            if not line or line.startswith('#'):
                # Vérifier si c'est une config
                if line.startswith('# interval:'):
                    try:
                        interval = int(line.split(':')[1].strip())
                        self.config.interval_minutes = interval
                    except (ValueError, IndexError):
                        pass  # config format invalide, on garde la valeur par défaut
                elif line.startswith('# schedule:'):
                    try:
                        raw = line.split(':', 1)[1].strip()
                        hours = [int(h.strip()) for h in raw.split(',') if h.strip().isdigit()]
                        self.config.scheduled_hours = [h for h in hours if 0 <= h <= 23]
                    except Exception as e:
                        logger.debug(f"Parse heartbeat schedule: {e}")
                continue
            
            # Tâche: commence par - ou *
            if line.startswith('-') or line.startswith('*'):
                task_desc = line[1:].strip()
                if task_desc:
                    self.config.tasks.append(HeartbeatTask(
                        description=task_desc,
                        interval_minutes=self.config.interval_minutes
                    ))
    
    def is_effectively_empty(self) -> bool:
        """Vérifie si le heartbeat n'a pas de tâches actives"""
        return len(self.config.tasks) == 0 or not self.config.enabled
    
    async def start(self):
        """Démarre le système heartbeat"""
        if self._running:
            return
        
        if self.is_effectively_empty():
            logger.info("💓 Heartbeat: pas de tâches configurées, système en veille")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        if self.config.scheduled_hours:
            hours_str = ' et '.join(f'{h:02d}h00' for h in sorted(self.config.scheduled_hours))
            logger.info(f"💓 Heartbeat démarré (planifié: {hours_str} — {len(self.config.scheduled_hours)}×/jour)")
        else:
            logger.info(f"💓 Heartbeat démarré (interval: {self.config.interval_minutes}min)")
    
    async def stop(self):
        """Arrête le système heartbeat"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("💓 Heartbeat arrêté")
    
    def _secs_until_next_scheduled(self) -> int:
        """Calcule les secondes jusqu'à la prochaine heure planifiée."""
        now = datetime.now()
        candidates = []
        for h in self.config.scheduled_hours:
            for delta_days in (0, 1):
                dt = datetime.combine(
                    (now + timedelta(days=delta_days)).date(),
                    dt_time(hour=h, minute=0, second=0)
                )
                if dt > now:
                    candidates.append(dt)
        if not candidates:
            # Sécurité : fallback 12h
            return 12 * 3600
        next_dt = min(candidates)
        secs = max(1, int((next_dt - now).total_seconds()))
        logger.info(
            f"💓 Prochain heartbeat planifié: {next_dt.strftime('%d/%m %H:%M')} "
            f"(dans {secs // 3600}h{(secs % 3600) // 60}m)"
        )
        return secs

    async def _heartbeat_loop(self):
        """Boucle principale du heartbeat"""
        while self._running:
            try:
                # Attendre l'intervalle ou la prochaine heure planifiée
                if self.config.scheduled_hours:
                    sleep_secs = self._secs_until_next_scheduled()
                else:
                    sleep_secs = self.config.interval_minutes * 60
                await asyncio.sleep(sleep_secs)
                
                if not self._running:
                    break
                
                # Exécuter le heartbeat
                await self._execute_heartbeat()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur heartbeat: {e}")
                await asyncio.sleep(60)  # Attendre 1 minute avant de réessayer
    
    async def _execute_heartbeat(self):
        """Exécute un heartbeat — consulte les handlers ops avant d'invoquer le LLM."""
        self.last_heartbeat = datetime.now()
        self.heartbeat_count += 1
        self._save_state()  # Persiste immédiatement (survit aux redémarrages)

        logger.info(f"💓 Heartbeat #{self.heartbeat_count} - Vérification des tâches...")
        
        # Recharger le fichier au cas où il a changé
        self._load_heartbeat_file()
        
        if self.is_effectively_empty():
            logger.debug("💓 HEARTBEAT_OK - Aucune tâche")
            return
        
        # ── Phase 1 : vérification programmatique (zéro tokens) ──
        issues: List[str] = []
        try:
            from .ops_handlers import handler_runtime_health
            health = await handler_runtime_health()
            status = health.get("status", "healthy")
            alerts = health.get("alerts", [])
            if status != "healthy" or alerts:
                issues.append(f"Health: {status}")
                issues.extend(alerts)
        except Exception as e:
            logger.debug(f"💓 Ops check skipped: {e}")

        # ── Phase 2 : appeler le LLM uniquement si problèmes détectés ──
        if issues:
            if self.on_thinking:
                self.on_thinking("💓 Lumena traite des alertes détectées...")
            
            tasks_list = "\n".join([f"- {t.description}" for t in self.config.tasks if t.enabled])
            issues_text = "\n".join(f"⚠️ {i}" for i in issues)

            heartbeat_prompt = f"""[HEARTBEAT AUTONOME - ALERTES DÉTECTÉES]
Problèmes détectés automatiquement:
{issues_text}

Tâches configurées:
{tasks_list}

Instructions:
1. Analyse les alertes ci-dessus
2. Si tu peux corriger quelque chose, fais-le (MAX 4 actions au total)
3. Décris brièvement ce que tu as fait

⚠️ RÈGLES ABSOLUES:
- N'utilise JAMAIS run_command dans parallel_tools. run_command doit toujours être appelé seul, séquentiellement.
- N'utilise JAMAIS browser_new_tab ni browser_navigate dans parallel_tools. Ces outils doivent être appelés séquentiellement.
- Limite-toi à 4 appels d'outils maximum. Reste concis.
"""
            if self.on_task:
                try:
                    response = await self.on_task(heartbeat_prompt)
                    if response:
                        logger.info(f"💓 Heartbeat action: {response[:100]}...")
                except Exception as e:
                    logger.error(f"Erreur exécution heartbeat: {e}")
        else:
            logger.debug("💓 HEARTBEAT_OK - Aucun problème détecté")
    
    async def force_heartbeat(self) -> Optional[str]:
        """Force un heartbeat immédiat"""
        if self.on_task:
            await self._execute_heartbeat()
            return "Heartbeat exécuté"
        return None
    
    def add_task(self, description: str) -> bool:
        """Ajoute une tâche au heartbeat"""
        task = HeartbeatTask(description=description)
        self.config.tasks.append(task)
        
        # Sauvegarder dans le fichier
        self._save_heartbeat_file()
        return True
    
    def remove_task(self, description: str) -> bool:
        """Retire une tâche du heartbeat"""
        for task in self.config.tasks:
            if task.description == description:
                self.config.tasks.remove(task)
                self._save_heartbeat_file()
                return True
        return False

    def set_schedule(self, hours: List[int]) -> bool:
        """Modifie les heures planifiées du heartbeat et persiste dans HEARTBEAT.md."""
        self.config.scheduled_hours = sorted(set(h for h in hours if 0 <= h <= 23))
        self._save_heartbeat_file()
        logger.info(f"💓 Schedule heartbeat modifié: {self.config.scheduled_hours}")
        return True
    
    def _save_heartbeat_file(self):
        """Sauvegarde le fichier HEARTBEAT.md"""
        heartbeat_path = self.workspace_dir / self.config.heartbeat_file
        
        lines = [
            "# 💓 HEARTBEAT.md - Tâches Autonomes de Lumena",
            "",
        ]
        if self.config.scheduled_hours:
            lines.append(f"# schedule: {','.join(str(h) for h in sorted(self.config.scheduled_hours))}")
        else:
            lines.append(f"# interval: {self.config.interval_minutes}")
        lines.append("")
        
        for task in self.config.tasks:
            prefix = "- " if task.enabled else "# - "
            lines.append(f"{prefix}{task.description}")
        
        try:
            atomic_write_text(heartbeat_path, '\n'.join(lines))
            logger.info("💓 HEARTBEAT.md sauvegardé")
        except Exception as e:
            logger.error(f"Erreur sauvegarde HEARTBEAT.md: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut du heartbeat"""
        status = {
            "enabled": self.config.enabled and not self.is_effectively_empty(),
            "running": self._running,
            "tasks_count": len(self.config.tasks),
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "heartbeat_count": self.heartbeat_count,
        }
        if self.config.scheduled_hours:
            status["scheduled_hours"] = sorted(self.config.scheduled_hours)
            status["mode"] = "scheduled"
        else:
            status["interval_minutes"] = self.config.interval_minutes
            status["mode"] = "interval"
        return status


# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_heartbeat_instance: Optional[HeartbeatSystem] = None
_heartbeat_lock = threading.Lock()


def get_heartbeat(
    workspace_dir: Optional[Path] = None,
    on_task_callback: Optional[Callable] = None
) -> HeartbeatSystem:
    """Obtient l'instance singleton du heartbeat (thread-safe)"""
    global _heartbeat_instance
    
    # Double-check locking pattern
    if _heartbeat_instance is None:
        with _heartbeat_lock:
            if _heartbeat_instance is None:
                _heartbeat_instance = HeartbeatSystem(
                    workspace_dir=workspace_dir,
                    on_task_callback=on_task_callback
                )
    return _heartbeat_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
