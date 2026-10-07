"""État persistant et annulation coopérative des tâches vidéo Remotion."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


JOB_FILENAME = ".lumena-video-job.json"
CANCEL_FILENAME = ".lumena-video-cancel"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VideoJob:
    """État utilisateur minimal d'une génération ou modification vidéo."""

    job_id: str
    project_dir: str
    status: str
    phase: str
    progress: int
    message: str
    created_at: str
    updated_at: str
    output_path: str = ""


class VideoJobTracker:
    """Écrit atomiquement l'état dans le projet, sans base parallèle."""

    def __init__(self, project_dir: Path, job_id: str) -> None:
        self.project_dir = project_dir.resolve()
        now = _now()
        self.job = VideoJob(
            job_id=job_id,
            project_dir=str(self.project_dir),
            status="running",
            phase="initialization",
            progress=0,
            message="Initialisation",
            created_at=now,
            updated_at=now,
        )
        self._write()

    def update(
        self,
        phase: str,
        progress: int,
        message: str,
        *,
        status: str = "running",
        output_path: str = "",
    ) -> None:
        """Publie une progression monotone bornée entre 0 et 100."""
        self.job.phase = phase[:80]
        self.job.progress = max(self.job.progress, min(100, max(0, int(progress))))
        self.job.message = message[:500]
        self.job.status = status
        self.job.updated_at = _now()
        if output_path:
            self.job.output_path = output_path
        self._write()

    def ensure_active(self) -> None:
        """Lève une erreur explicite si une annulation a été demandée."""
        if (self.project_dir / CANCEL_FILENAME).exists():
            self.update("cancelled", self.job.progress, "Annulation demandée", status="cancelled")
            raise RuntimeError("Génération vidéo annulée par l'utilisateur.")

    def _write(self) -> None:
        path = self.project_dir / JOB_FILENAME
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(asdict(self.job), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def read_video_job(project_dir: Path) -> Optional[Dict[str, Any]]:
    """Lit l'état d'un projet sans exécuter son code."""
    path = project_dir.resolve() / JOB_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def request_video_cancel(project_dir: Path) -> Path:
    """Dépose le marqueur coopératif lu par le handler et le moteur."""
    path = project_dir.resolve() / CANCEL_FILENAME
    path.write_text(_now(), encoding="utf-8")
    return path


def clear_video_cancel(project_dir: Path) -> None:
    """Retire une ancienne demande d'annulation avant une reprise explicite."""
    (project_dir.resolve() / CANCEL_FILENAME).unlink(missing_ok=True)

