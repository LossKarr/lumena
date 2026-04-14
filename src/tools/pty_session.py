"""
LUMENA - PTY Session (Phase 5: Performance)

Terminal interactif pour REPLs (python, node, etc).
Support Windows (winpty) et Unix (pty).
"""

import os
import sys
import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
import subprocess
import threading
import queue

logger = logging.getLogger("lumena.pty_session")

# Detection du systeme
IS_WINDOWS = sys.platform == "win32"


@dataclass
class PtySession:
    """
    Session PTY pour terminal interactif.
    
    Permet d'interagir avec des REPLs comme python, node, etc.
    """
    session_id: str
    command: str
    cwd: str = "."
    env: Dict[str, str] = field(default_factory=dict)
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _output_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _active: bool = False
    created_at: datetime = field(default_factory=datetime.now)


class PtyManager:
    """
    Gestionnaire de sessions PTY.
    
    Fonctionnalites:
    - Creation de sessions interactives
    - Envoi de commandes
    - Lecture de la sortie
    - Gestion multi-sessions
    """
    
    def __init__(self):
        self.sessions: Dict[str, PtySession] = {}
        self._counter = 0
    
    def create_session(
        self,
        command: str = None,
        cwd: str = ".",
        env: Dict[str, str] = None
    ) -> Tuple[str, str]:
        """
        Cree une nouvelle session PTY.
        
        Args:
            command: Commande a lancer (ex: "python", "node")
            cwd: Repertoire de travail
            env: Variables d'environnement additionnelles
            
        Returns:
            Tuple (session_id, message initial)
        """
        self._counter += 1
        session_id = f"pty_{self._counter}"
        
        # Commande par defaut
        if command is None:
            command = "cmd.exe" if IS_WINDOWS else "/bin/bash"
        
        # Environnement
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        
        # Securite: validation whitelist des commandes
        from ..utils.command_sanitizer import sanitize_chained_command
        allowed, reason = sanitize_chained_command(command)
        if not allowed:
            logger.warning(f"Commande PTY bloquee: {command[:60]} - {reason}")
            return "", f"⛔ {reason}"

        try:
            # Creer le processus
            if IS_WINDOWS:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=full_env,
                    bufsize=0,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                import shlex
                process = subprocess.Popen(
                    shlex.split(command),
                    shell=False,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=full_env,
                    bufsize=0
                )
            
            session = PtySession(
                session_id=session_id,
                command=command,
                cwd=cwd,
                env=env or {},
                _process=process,
                _active=True
            )
            
            # Demarrer le thread de lecture
            reader = threading.Thread(
                target=self._reader_loop,
                args=(session,),
                daemon=True
            )
            session._reader_thread = reader
            reader.start()
            
            self.sessions[session_id] = session
            
            return session_id, f"Session {session_id} creee avec '{command}'"
            
        except Exception as e:
            logger.error(f"Erreur creation PTY: {e}")
            return "", f"Erreur: {e}"
    
    def _reader_loop(self, session: PtySession):
        """Thread de lecture de la sortie."""
        try:
            while session._active and session._process:
                if session._process.poll() is not None:
                    # Processus termine
                    session._active = False
                    session._output_queue.put("[Processus termine]")
                    break
                
                try:
                    # Lire un caractere a la fois pour reactivite
                    data = session._process.stdout.read(1024)
                    if data:
                        try:
                            text = data.decode('utf-8', errors='replace')
                        except UnicodeDecodeError:
                            text = data.decode('latin-1', errors='replace')
                        session._output_queue.put(text)
                except (IOError, OSError):
                    break
        except Exception as e:
            session._output_queue.put(f"[Erreur lecture: {e}]")
    
    def send_input(
        self,
        session_id: str,
        text: str,
        add_newline: bool = True
    ) -> Tuple[bool, str]:
        """
        Envoie du texte a une session.
        
        Args:
            session_id: ID de la session
            text: Texte a envoyer
            add_newline: Ajouter un retour ligne
            
        Returns:
            Tuple (success, message)
        """
        session = self.sessions.get(session_id)
        if not session:
            return False, f"Session {session_id} non trouvee"
        
        if not session._active:
            return False, "Session terminee"
        
        try:
            if add_newline and not text.endswith('\n'):
                text += '\n'
            
            session._process.stdin.write(text.encode('utf-8'))
            session._process.stdin.flush()
            
            return True, f"Envoye: {text.strip()}"
        except Exception as e:
            return False, f"Erreur envoi: {e}"
    
    def read_output(
        self,
        session_id: str,
        timeout: float = 0.5
    ) -> Tuple[bool, str]:
        """
        Lit la sortie d'une session.
        
        Args:
            session_id: ID de la session
            timeout: Temps d'attente max en secondes
            
        Returns:
            Tuple (has_more, output)
        """
        session = self.sessions.get(session_id)
        if not session:
            return False, f"Session {session_id} non trouvee"
        
        output_parts = []
        deadline = datetime.now().timestamp() + timeout
        
        while datetime.now().timestamp() < deadline:
            try:
                text = session._output_queue.get_nowait()
                output_parts.append(text)
            except queue.Empty:
                if output_parts:
                    break
                # Attendre un peu
                import time
                time.sleep(0.05)
        
        output = "".join(output_parts)
        has_more = not session._output_queue.empty()
        
        return has_more, output
    
    def close_session(self, session_id: str) -> Tuple[bool, str]:
        """
        Ferme une session.
        
        Args:
            session_id: ID de la session
            
        Returns:
            Tuple (success, message)
        """
        session = self.sessions.get(session_id)
        if not session:
            return False, f"Session {session_id} non trouvee"
        
        try:
            session._active = False
            
            if session._process:
                session._process.terminate()
                try:
                    session._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    session._process.kill()
            
            del self.sessions[session_id]
            return True, f"Session {session_id} fermee"
            
        except Exception as e:
            return False, f"Erreur fermeture: {e}"
    
    def list_sessions(self) -> Dict[str, Dict[str, Any]]:
        """Liste toutes les sessions actives."""
        return {
            sid: {
                "command": s.command,
                "cwd": s.cwd,
                "active": s._active,
                "created_at": s.created_at.isoformat()
            }
            for sid, s in self.sessions.items()
        }


# Singleton
_pty_manager: Optional[PtyManager] = None


def get_pty_manager() -> PtyManager:
    """Retourne l'instance du gestionnaire PTY."""
    global _pty_manager
    if _pty_manager is None:
        _pty_manager = PtyManager()
    return _pty_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
