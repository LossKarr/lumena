
import asyncio
import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger

class PiperProvider:
    """
    Provider TTS utilisant Piper (ONNX) pour une synthèse vocale 100% locale.
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.root_dir = data_dir or Path(__file__).parent.parent.parent.parent
        self.models_dir = self.root_dir / "models" / "piper"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        
        # Configuration par défaut
        self.model_name = "fr_FR-siwis-low" # Voix française légère et rapide
        self.model_path = self.models_dir / f"{self.model_name}.onnx"
        self.config_path = self.models_dir / f"{self.model_name}.onnx.json"
        
        # Détection de l'exécutable Piper
        self.piper_exe = self._find_piper_exe()
        
    def _find_piper_exe(self) -> Optional[str]:
        """Cherche l'exécutable piper dans le système ou le venv."""
        # 1. Chercher dans le PATH
        exe = shutil.which("piper")
        if exe:
            return exe
            
        # 2. Chercher dans le venv (Windows)
        venv_exe = self.root_dir / "venv" / "Scripts" / "piper.exe"
        if venv_exe.exists():
            return str(venv_exe)
            
        # 3. Chercher dans le venv (Linux/Mac)
        venv_exe_nix = self.root_dir / "venv" / "bin" / "piper"
        if venv_exe_nix.exists():
            return str(venv_exe_nix)
            
        return None

    def is_available(self) -> bool:
        """Vérifie si Piper et le modèle sont prêts."""
        return self.piper_exe is not None and self.model_path.exists()

    async def generate(self, text: str, output_path: Path) -> bool:
        """Génère un fichier audio à partir du texte."""
        if not self.is_available():
            logger.warning(f"Piper non disponible ou modèle manquant ({self.model_path})")
            return False
            
        try:
            # Commande Piper: echo "text" | piper --model model.onnx --output_file out.wav
            # On utilise une approche asynchrone pour ne pas bloquer
            process = await asyncio.create_subprocess_exec(
                self.piper_exe,
                "--model", str(self.model_path),
                "--output_file", str(output_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate(input=text.encode('utf-8'))
            
            if process.returncode == 0:
                return True
            else:
                logger.error(f"Erreur Piper (code {process.returncode}): {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Exception lors de la génération Piper: {e}")
            return False

    def get_info(self) -> Dict[str, Any]:
        """Retourne les informations sur le provider."""
        return {
            "name": "Piper",
            "available": self.is_available(),
            "model": self.model_name,
            "exe_found": self.piper_exe is not None,
            "model_found": self.model_path.exists()
        }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
