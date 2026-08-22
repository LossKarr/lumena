"""Secure, versioned brand assets used by Document Studio renders."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from PIL import Image, UnidentifiedImageError

from .template_models import TemplateValidationError
from .template_security import atomic_write_json, resolve_within


_ALLOWED_INPUT_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_PIXELS = 20_000_000


@dataclass(frozen=True)
class LogoRecord:
    id: str
    name: str
    filename: str
    mime_type: str
    size: int
    width: int
    height: int
    sha256: str
    created_at: str

    def to_dict(self, *, active: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["active"] = active
        data["content_url"] = f"/api/document-studio/logos/{self.id}/content"
        return data


class BrandAssetStore:
    """Stores sanitized logos and one optional active selection."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.assets_root = self.root / "assets"
        self.index_path = self.root / "index.json"
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def list_logos(self) -> list[dict[str, Any]]:
        with self._lock:
            index = self._read_index()
            active_id = index.get("active_id")
            return [
                LogoRecord(**raw).to_dict(active=raw.get("id") == active_id)
                for raw in index.get("logos", [])
            ]

    def add(self, content: bytes, *, filename: str, name: str = "") -> dict[str, Any]:
        sanitized, width, height = self._sanitize(content)
        digest = sha256(sanitized).hexdigest()
        logo_id = f"logo-{digest[:16]}"
        display_name = self._display_name(name or Path(filename or "logo").stem)
        with self._lock:
            index = self._read_index()
            for raw in index.get("logos", []):
                if raw.get("sha256") == digest:
                    return LogoRecord(**raw).to_dict(active=raw.get("id") == index.get("active_id"))
            path = resolve_within(self.assets_root, f"{logo_id}.png")
            self._atomic_write_bytes(path, sanitized)
            record = LogoRecord(
                id=logo_id,
                name=display_name,
                filename=f"{logo_id}.png",
                mime_type="image/png",
                size=len(sanitized),
                width=width,
                height=height,
                sha256=digest,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            logos = list(index.get("logos", []))
            logos.append(asdict(record))
            active_id = index.get("active_id") or logo_id
            self._write_index(logos, active_id=active_id)
            return record.to_dict(active=record.id == active_id)

    def set_active(self, logo_id: str | None) -> dict[str, Any] | None:
        with self._lock:
            index = self._read_index()
            if logo_id in (None, ""):
                self._write_index(list(index.get("logos", [])), active_id=None)
                return None
            raw = next((item for item in index.get("logos", []) if item.get("id") == logo_id), None)
            if raw is None:
                raise KeyError(str(logo_id))
            self._write_index(list(index.get("logos", [])), active_id=str(logo_id))
            return LogoRecord(**raw).to_dict(active=True)

    def delete(self, logo_id: str) -> None:
        with self._lock:
            index = self._read_index()
            raw = next((item for item in index.get("logos", []) if item.get("id") == logo_id), None)
            if raw is None:
                raise KeyError(logo_id)
            logos = [item for item in index.get("logos", []) if item.get("id") != logo_id]
            active_id = None if index.get("active_id") == logo_id else index.get("active_id")
            self._write_index(logos, active_id=active_id)
            resolve_within(self.assets_root, raw["filename"]).unlink(missing_ok=True)

    def active_record(self) -> LogoRecord | None:
        with self._lock:
            index = self._read_index()
            active_id = index.get("active_id")
            raw = next((item for item in index.get("logos", []) if item.get("id") == active_id), None)
            return LogoRecord(**raw) if raw else None

    def content_path(self, logo_id: str) -> Path:
        with self._lock:
            index = self._read_index()
            raw = next((item for item in index.get("logos", []) if item.get("id") == logo_id), None)
            if raw is None:
                raise KeyError(logo_id)
            path = resolve_within(self.assets_root, raw["filename"])
            if not path.is_file():
                raise KeyError(logo_id)
            return path

    def active_data_uri(self) -> str:
        record = self.active_record()
        if record is None:
            return ""
        return self.data_uri(record.id)

    def data_uri(self, logo_id: str) -> str:
        """Return one stored, sanitized logo without changing the active choice."""
        import base64

        payload = self.content_path(logo_id).read_bytes()
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")

    @staticmethod
    def _sanitize(content: bytes) -> tuple[bytes, int, int]:
        if not content:
            raise TemplateValidationError("logo file is empty")
        if len(content) > _MAX_UPLOAD_BYTES:
            raise TemplateValidationError("logo exceeds the 5 MB limit")
        try:
            with Image.open(BytesIO(content)) as probe:
                if probe.format not in _ALLOWED_INPUT_FORMATS:
                    raise TemplateValidationError("logo must be PNG, JPEG or WebP")
                width, height = probe.size
                if width < 1 or height < 1 or width * height > _MAX_PIXELS:
                    raise TemplateValidationError("logo dimensions are invalid or too large")
                probe.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                normalized = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                output = BytesIO()
                normalized.save(output, "PNG", optimize=True)
        except TemplateValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise TemplateValidationError("logo is not a valid image") from exc
        return output.getvalue(), width, height

    @staticmethod
    def _display_name(value: str) -> str:
        cleaned = " ".join(str(value or "Logo").strip().split())
        return (cleaned or "Logo")[:80]

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {"schema_version": 1, "active_id": None, "logos": []}
        try:
            import json

            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TemplateValidationError("logo index is unreadable") from exc
        if raw.get("schema_version") != 1 or not isinstance(raw.get("logos"), list):
            raise TemplateValidationError("logo index has an unsupported format")
        return raw

    def _write_index(self, logos: list[dict[str, Any]], *, active_id: str | None) -> None:
        if active_id and sum(1 for item in logos if item.get("id") == active_id) != 1:
            raise TemplateValidationError("active logo must reference exactly one stored logo")
        atomic_write_json(
            self.index_path,
            {"schema_version": 1, "active_id": active_id, "logos": logos},
        )

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
