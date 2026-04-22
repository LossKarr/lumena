"""
🌟 LUMENA - Process Manager

Gestion des processus en arrière-plan pour les commandes longues.
"""

from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import asyncio
import shlex
import subprocess
import sys
import uuid
import os
from pathlib import Path
from loguru import logger

_IS_WINDOWS = sys.platform == "win32"


class ProcessStatus(Enum):
    """État d'un processus."""
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass
class ProcessInfo:
    """Informations sur un processus."""
    id: str
    command: str
    status: ProcessStatus
    started_at: datetime
    ended_at: Optional[datetime] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)


class ProcessManager:
    """
    Gestionnaire de processus en arrière-plan.
    
    Permet de :
    - Lancer des commandes en background
    - Suivre leur statut
    - Envoyer de l'input
    - Les terminer
    """
    
    def __init__(self, work_dir: Optional[Path] = None, max_processes: int = 10):
        self.processes: Dict[str, ProcessInfo] = {}
        self.work_dir = work_dir or Path.cwd()
        self.max_processes = max_processes
        self._cleanup_lock = asyncio.Lock()
    
    async def run_background(
        self,
        command: str,
        wait_ms_before_async: int = 5000,
        timeout_s: int = 60
    ) -> Tuple[str, Optional[str]]:
        """
        Lance une commande, attend un peu puis passe en background si elle dure.
        
        Args:
            command: La commande à exécuter
            wait_ms_before_async: Temps d'attente avant de passer en background (ms)
            timeout_s: Timeout total pour les commandes synchrones
        
        Returns:
            Tuple[output, process_id ou None si terminée]
        """
        # Cleanup vieux processus
        await self._cleanup_finished()
        
        if len(self.processes) >= self.max_processes:
            return "❌ Trop de processus en cours. Utilisez process_list pour voir.", None
        
        process_id = str(uuid.uuid4())[:8]

        # Securite: validation whitelist des commandes
        from ..utils.command_sanitizer import sanitize_chained_command
        allowed, reason = sanitize_chained_command(command)
        if not allowed:
            return f"⛔ {reason}", None

        try:
            # Lance le processus
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                cwd=str(self.work_dir),
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
            )
            if _IS_WINDOWS:
                popen_kwargs["shell"] = True
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                cmd = command
            else:
                popen_kwargs["shell"] = False
                cmd = shlex.split(command)
            process = subprocess.Popen(cmd, **popen_kwargs)
            
            info = ProcessInfo(
                id=process_id,
                command=command,
                status=ProcessStatus.RUNNING,
                started_at=datetime.now(),
                process=process
            )
            self.processes[process_id] = info
            
            # Attend un moment pour voir si ça finit vite
            wait_seconds = wait_ms_before_async / 1000.0
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None, lambda: process.communicate(timeout=wait_seconds)
                    ),
                    timeout=wait_seconds + 1
                )
                
                # Terminé dans le délai
                info.stdout = stdout or ""
                info.stderr = stderr or ""
                info.exit_code = process.returncode
                info.ended_at = datetime.now()
                info.status = ProcessStatus.DONE if process.returncode == 0 else ProcessStatus.FAILED
                
                output = info.stdout
                if info.stderr:
                    output += f"\n[STDERR] {info.stderr}"
                
                # Supprimer de la liste (terminé)
                del self.processes[process_id]
                
                return self._truncate_output(output), None
                
            except (asyncio.TimeoutError, subprocess.TimeoutExpired):
                # Passe en background
                logger.info(f"⏳ Processus {process_id} passe en arrière-plan: {command[:50]}...")
                return f"⏳ Commande lancée en arrière-plan.\n📌 ID: {process_id}\n💻 Commande: {command[:100]}\n\nUtilisez `process_status` avec l'ID pour voir le résultat.", process_id
                
        except Exception as e:
            logger.error(f"❌ Erreur lancement processus: {e}")
            return f"❌ Erreur: {e}", None
    
    async def get_status(self, process_id: str) -> str:
        """
        Récupère le statut d'un processus.
        
        Args:
            process_id: ID du processus
            
        Returns:
            Description du statut
        """
        if process_id not in self.processes:
            return f"❌ Processus '{process_id}' non trouvé"
        
        info = self.processes[process_id]
        
        # Vérifie si terminé
        if info.process and info.status == ProcessStatus.RUNNING:
            poll = info.process.poll()
            if poll is not None:
                # Terminé
                try:
                    info.stdout, info.stderr = info.process.communicate(timeout=1)
                except (subprocess.TimeoutExpired, OSError):
                    pass  # processus toujours en cours, on continue
                info.exit_code = poll
                info.ended_at = datetime.now()
                info.status = ProcessStatus.DONE if poll == 0 else ProcessStatus.FAILED
        
        lines = [
            f"📊 Statut du processus {process_id}",
            f"   Commande: {info.command[:80]}",
            f"   Statut: {info.status.value}",
            f"   Démarré: {info.started_at.strftime('%H:%M:%S')}",
        ]
        
        if info.ended_at:
            lines.append(f"   Terminé: {info.ended_at.strftime('%H:%M:%S')}")
            lines.append(f"   Code retour: {info.exit_code}")
        
        if info.stdout:
            lines.append(f"\n📤 Sortie:\n{self._truncate_output(info.stdout)}")
        
        if info.stderr:
            lines.append(f"\n⚠️ Erreurs:\n{self._truncate_output(info.stderr)}")
        
        return "\n".join(lines)
    
    async def send_input(self, process_id: str, input_text: str) -> str:
        """
        Envoie de l'input à un processus.
        
        Args:
            process_id: ID du processus
            input_text: Texte à envoyer
            
        Returns:
            Confirmation ou erreur
        """
        if process_id not in self.processes:
            return f"❌ Processus '{process_id}' non trouvé"
        
        info = self.processes[process_id]
        
        if info.status != ProcessStatus.RUNNING:
            return f"❌ Processus '{process_id}' n'est plus en cours"
        
        if not info.process or not info.process.stdin:
            return f"❌ Impossible d'envoyer de l'input à ce processus"
        
        try:
            info.process.stdin.write(input_text + "\n")
            info.process.stdin.flush()
            return f"✅ Input envoyé au processus {process_id}"
        except Exception as e:
            return f"❌ Erreur envoi input: {e}"
    
    async def terminate(self, process_id: str) -> str:
        """
        Termine un processus.
        
        Args:
            process_id: ID du processus
            
        Returns:
            Confirmation
        """
        if process_id not in self.processes:
            return f"❌ Processus '{process_id}' non trouvé"
        
        info = self.processes[process_id]
        
        if info.status != ProcessStatus.RUNNING:
            return f"ℹ️ Processus '{process_id}' déjà terminé"
        
        try:
            if info.process:
                info.process.terminate()
                # Attendre un peu puis kill si nécessaire
                await asyncio.sleep(0.5)
                if info.process.poll() is None:
                    info.process.kill()
            
            info.status = ProcessStatus.TERMINATED
            info.ended_at = datetime.now()
            
            return f"✅ Processus {process_id} terminé"
        except Exception as e:
            return f"❌ Erreur terminaison: {e}"
    
    async def list_processes(self) -> str:
        """
        Liste tous les processus actifs.
        
        Returns:
            Liste formatée
        """
        await self._cleanup_finished()
        
        if not self.processes:
            return "📋 Aucun processus en cours"
        
        lines = [f"📋 Processus ({len(self.processes)}):", ""]
        
        for pid, info in self.processes.items():
            status_emoji = {
                ProcessStatus.RUNNING: "🔄",
                ProcessStatus.DONE: "✅",
                ProcessStatus.FAILED: "❌",
                ProcessStatus.TERMINATED: "🛑"
            }.get(info.status, "❓")
            
            lines.append(f"{status_emoji} [{pid}] {info.command[:60]}...")
        
        return "\n".join(lines)
    
    async def _cleanup_finished(self) -> None:
        """Nettoie les processus terminés depuis longtemps."""
        async with self._cleanup_lock:
            now = datetime.now()
            to_remove = []
            
            for pid, info in self.processes.items():
                # Garde les processus terminés pendant 5 minutes
                if info.status != ProcessStatus.RUNNING:
                    if info.ended_at and (now - info.ended_at).seconds > 300:
                        to_remove.append(pid)
            
            for pid in to_remove:
                del self.processes[pid]
    
    def _truncate_output(self, output: str, max_chars: int = 5000) -> str:
        """Tronque la sortie si trop longue."""
        if len(output) > max_chars:
            return output[:max_chars] + "\n\n[... sortie tronquée ...]"
        return output


# Instance globale
_process_manager: Optional[ProcessManager] = None


def get_process_manager(work_dir: Optional[Path] = None) -> ProcessManager:
    """Retourne l'instance globale du ProcessManager."""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager(work_dir)
    return _process_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
