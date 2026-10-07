"""Contrôles déterministes des artefacts vidéo produits par Remotion."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass(frozen=True)
class QualityIssue:
    """Un défaut ou une limite observée pendant le contrôle final."""

    severity: str
    code: str
    message: str


@dataclass
class VideoQualityReport:
    """Rapport sérialisable séparant rendu technique et qualité vérifiée."""

    passed: bool = True
    probed: bool = False
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: str = ""
    has_audio: bool = False
    sampled_frames: int = 0
    issues: List[QualityIssue] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str) -> None:
        """Ajoute un constat et invalide le rapport pour une erreur."""
        self.issues.append(QualityIssue(severity=severity, code=code, message=message))
        if severity == "error":
            self.passed = False

    def to_dict(self) -> Dict[str, Any]:
        """Retourne un dictionnaire stable pour le manifeste de preuve."""
        return {
            "passed": self.passed,
            "probed": self.probed,
            "duration_sec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "has_audio": self.has_audio,
            "sampled_frames": self.sampled_frames,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def summary(self) -> str:
        """Résumé court destiné au chat et aux journaux."""
        errors = sum(issue.severity == "error" for issue in self.issues)
        warnings = sum(issue.severity == "warning" for issue in self.issues)
        state = "validé" if self.passed else "refusé"
        return f"{state}: {errors} erreur(s), {warnings} avertissement(s), {self.sampled_frames} image(s) contrôlée(s)"


async def inspect_rendered_video(
    video_path: Path,
    *,
    project_dir: Path,
    expected_duration_sec: float,
    expected_width: int,
    expected_height: int,
) -> VideoQualityReport:
    """Contrôle le conteneur, les dimensions, la durée et des images échantillons."""
    report = VideoQualityReport()
    project = project_dir.resolve()
    candidate = video_path.resolve()
    try:
        candidate.relative_to(project)
    except ValueError:
        report.add("error", "OUTPUT_OUTSIDE_PROJECT", "La vidéo rendue se trouve hors du projet autorisé.")
        return report
    if not candidate.exists() or not candidate.is_file():
        report.add("error", "OUTPUT_MISSING", "Le fichier vidéo annoncé est introuvable.")
        return report
    if candidate.suffix.lower() not in {".mp4", ".webm", ".gif"}:
        report.add("error", "OUTPUT_EXTENSION", "Le format de sortie n'est pas autorisé.")
    if candidate.stat().st_size < 4096:
        report.add("error", "OUTPUT_TOO_SMALL", "Le fichier vidéo est anormalement petit.")

    ffprobe = shutil.which("ffprobe")
    payload: Optional[Dict[str, Any]] = None
    if ffprobe:
        try:
            payload = await _probe(ffprobe, candidate)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            report.add("error", "FFPROBE_FAILED", f"Le conteneur vidéo est illisible: {exc}")
            await _write_report(project, report)
            return report
    else:
        try:
            from .remotion_engine import probe_video_in_docker

            payload = await probe_video_in_docker(project, candidate)
        except Exception as exc:
            logger.debug("[video-quality] docker ffprobe unavailable: {}", exc)
            report.add(
                "warning",
                "FFPROBE_UNAVAILABLE",
                "FFprobe indisponible sur l'hôte et dans le runtime vidéo : contrôle du conteneur limité.",
            )

    if payload is not None:
        try:
            _apply_probe(report, payload, expected_duration_sec, expected_width, expected_height)
        except (RuntimeError, ValueError, TypeError) as exc:
            report.add("error", "FFPROBE_FAILED", f"Métadonnées vidéo invalides: {exc}")
            await _write_report(project, report)
            return report

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and report.duration_sec and report.duration_sec > 0:
        try:
            report.sampled_frames = await _sample_and_check_frames(
                ffmpeg,
                candidate,
                report.duration_sec,
                report,
            )
        except Exception as exc:
            logger.debug("[video-quality] frame sampling failed: {}", exc)
            report.add("warning", "FRAME_SAMPLING_FAILED", "L'échantillonnage visuel n'a pas pu être terminé.")
    else:
        proof_frames = sorted((project / "quality").glob("frame-*.png"))
        if proof_frames:
            report.sampled_frames = _analyze_frames(proof_frames, report)
        else:
            report.add("warning", "FRAME_PROOFS_MISSING", "Aucune image de preuve Remotion n'est disponible.")

    await _write_report(project, report)
    return report


async def _probe(ffprobe: str, video_path: Path) -> Dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise RuntimeError("timeout ffprobe") from exc
    if proc.returncode:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[:500])
    return json.loads(stdout.decode("utf-8", errors="replace"))


def _apply_probe(
    report: VideoQualityReport,
    payload: Dict[str, Any],
    expected_duration_sec: float,
    expected_width: int,
    expected_height: int,
) -> None:
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        report.add("error", "VIDEO_STREAM_MISSING", "Aucune piste vidéo n'est présente.")
        return
    report.probed = True
    report.width = int(video.get("width") or 0)
    report.height = int(video.get("height") or 0)
    report.codec = str(video.get("codec_name") or "")
    report.has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration_raw = (payload.get("format") or {}).get("duration") or video.get("duration")
    report.duration_sec = float(duration_raw) if duration_raw is not None else None
    if report.width != expected_width or report.height != expected_height:
        report.add(
            "error",
            "DIMENSIONS_MISMATCH",
            f"Dimensions {report.width}×{report.height}, attendu {expected_width}×{expected_height}.",
        )
    if report.duration_sec is None:
        report.add("error", "DURATION_MISSING", "La durée du média n'est pas mesurable.")
    elif abs(report.duration_sec - expected_duration_sec) > max(0.5, expected_duration_sec * 0.03):
        report.add(
            "error",
            "DURATION_MISMATCH",
            f"Durée {report.duration_sec:.2f}s, attendu {expected_duration_sec:.2f}s.",
        )


async def _sample_and_check_frames(
    ffmpeg: str,
    video_path: Path,
    duration_sec: float,
    report: VideoQualityReport,
) -> int:
    with tempfile.TemporaryDirectory(prefix="lumena_video_qa_") as temp_dir:
        output = Path(temp_dir) / "frame-%02d.png"
        interval = max(0.25, duration_sec / 9.0)
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-v", "error",
            "-i", str(video_path),
            "-vf", f"fps=1/{interval},scale=320:-1",
            "-frames:v", "9",
            str(output),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise RuntimeError("timeout ffmpeg") from exc
        if proc.returncode:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[:500])
        frames = sorted(Path(temp_dir).glob("frame-*.png"))
        return _analyze_frames(frames, report)


def _analyze_frames(frames: List[Path], report: VideoQualityReport) -> int:
    """Mesure les preuves visuelles rendues sans conserver de pixels en mémoire."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        report.add("warning", "PIL_UNAVAILABLE", "Pillow absent : analyse des images désactivée.")
        return 0
    black_frames = 0
    uniform_frames = 0
    for frame in frames:
        with Image.open(frame) as image:
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            mean = float(stat.mean[0])
            deviation = float(stat.stddev[0])
            if mean < 5.0 and deviation < 4.0:
                black_frames += 1
            elif deviation < 1.5:
                uniform_frames += 1
    if frames and black_frames / len(frames) >= 0.34:
        report.add("error", "BLACK_FRAMES", "Une part importante des images échantillonnées est noire.")
    elif black_frames:
        report.add("warning", "BLACK_FRAME", "Au moins une image échantillonnée est presque noire.")
    if frames and uniform_frames / len(frames) >= 0.5:
        report.add("warning", "UNIFORM_FRAMES", "La vidéo contient de nombreuses images visuellement uniformes.")
    return len(frames)


async def _write_report(project_dir: Path, report: VideoQualityReport) -> None:
    path = project_dir / "quality-report.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

