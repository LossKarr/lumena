
import asyncio
import os
import sys
import subprocess
import shutil
import re
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger

class PiperProvider:
    """
    Provider TTS utilisant Piper (ONNX) pour une synthèse vocale 100% locale.
    """
    
    def __init__(self, data_dir: Optional[Path] = None, model_name: Optional[str] = None):
        self.root_dir = data_dir or Path(__file__).parent.parent.parent.parent
        self.models_dir = self.root_dir / "models" / "piper"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        
        # Configuration par défaut
        self.model_name = self._safe_model_name(
            model_name or os.getenv("LUMENA_PIPER_MODEL", "fr_FR-siwis-low")
        )
        self.model_path = self.models_dir / f"{self.model_name}.onnx"
        self.config_path = self.models_dir / f"{self.model_name}.onnx.json"
        
        # Détection de l'exécutable Piper
        self.piper_exe = self._find_piper_exe()

    @staticmethod
    def _safe_model_name(value: str) -> str:
        name = (value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("nom de modèle Piper invalide")
        return name

    def model_paths(self, model_name: Optional[str] = None) -> tuple[Path, Path]:
        name = self._safe_model_name(model_name or self.model_name)
        return self.models_dir / f"{name}.onnx", self.models_dir / f"{name}.onnx.json"
        
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

    def is_available(self, model_name: Optional[str] = None) -> bool:
        """Vérifie si Piper et le modèle sont prêts."""
        model_path, config_path = self.model_paths(model_name)
        return self.piper_exe is not None and model_path.exists() and config_path.exists()

    async def generate(
        self, text: str, output_path: Path, *, model_name: Optional[str] = None,
    ) -> bool:
        """Génère un fichier audio à partir du texte."""
        selected = self._safe_model_name(model_name or self.model_name)
        model_path, _ = self.model_paths(selected)
        if not self.is_available(selected):
            logger.warning(f"Piper non disponible ou modèle manquant ({model_path})")
            return False

        input_path: Optional[Path] = None
        try:
            # Sous Windows, stdin passe par l'encodage de console et transforme
            # souvent "é" en "Ã©". Piper ouvre explicitement --input_file en
            # UTF-8 : ce chemin préserve donc les accents et apostrophes français.
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_input_path = tempfile.mkstemp(
                prefix="lumena_piper_", suffix=".txt", dir=str(output_path.parent)
            )
            os.close(fd)
            input_path = Path(raw_input_path)
            input_path.write_text(text.rstrip() + "\n", encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                self.piper_exe,
                "--model", str(model_path),
                "--input_file", str(input_path),
                "--output_file", str(output_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            
            if process.returncode == 0:
                return True
            else:
                logger.error(f"Erreur Piper (code {process.returncode}): {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Exception lors de la génération Piper: {e}")
            return False
        finally:
            if input_path is not None:
                input_path.unlink(missing_ok=True)

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
