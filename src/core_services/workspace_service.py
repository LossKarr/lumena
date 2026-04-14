"""
WorkspaceService — Gestion du workspace fichiers / dossiers.

Migré depuis LumenaCore (5 méthodes, zéro couplage interne).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .base_service import BaseService


class WorkspaceService(BaseService):
    """Gestion du workspace (fichiers, dossiers, projets)."""

    def get_workspace_path(self) -> Path:
        """Retourne le chemin du workspace pour aujourd'hui."""
        from ..utils.paths import WORKSPACE_DIR
        today = datetime.now().strftime("%Y-%m-%d")
        workspace = WORKSPACE_DIR / today
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def create_project_folder(self, project_name: str) -> Path:
        """Crée un dossier projet dans le workspace."""
        workspace = self.get_workspace_path()
        safe_name = re.sub(r'[^\w\-]', '_', project_name.lower())
        project_path = workspace / f"projet-{safe_name}"
        project_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"📁 Dossier projet créé: {project_path}")
        return project_path

    async def create_file(self, filename: str, content: str, project_name: Optional[str] = None) -> Path:
        """Crée un fichier dans le workspace."""
        if project_name:
            folder = self.create_project_folder(project_name)
        else:
            folder = self.get_workspace_path()
        filepath = folder / filename
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"📝 Fichier créé: {filepath}")
        return filepath

    async def read_file(self, filepath: str) -> str:
        """Lit le contenu d'un fichier."""
        try:
            path = Path(filepath)
            if not path.exists():
                workspace = self.get_workspace_path()
                path = workspace / filepath
            if path.exists():
                content = path.read_text(encoding="utf-8")
                logger.info(f"📖 Fichier lu: {path} ({len(content)} chars)")
                return content
            else:
                logger.warning(f"⚠️ Fichier non trouvé: {filepath}")
                return f"Erreur: fichier non trouvé: {filepath}"
        except Exception as e:
            logger.error(f"❌ Erreur lecture fichier: {e}")
            return f"Erreur: {e}"

    def list_workspace_files(self, pattern: str = "*") -> List[Dict[str, Any]]:
        """Liste les fichiers dans le workspace."""
        workspace = self.get_workspace_path()
        files = []
        for path in workspace.rglob(pattern):
            if path.is_file():
                files.append({
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                })
        return files
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
