"""Local Voice V3 privacy lifecycle: bounded, explicit data deletion."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid
from typing import Any, Dict


class VoicePrivacyError(RuntimeError):
    pass


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def purge_voice_data(root: str | Path, *, data_dir: str | Path) -> Dict[str, Any]:
    """Atomically detach then delete the dedicated voice-data tree.

    The caller supplies both paths so tests and packaged installations use the
    same boundary. Symlinks are counted but never followed. External profile
    paths are deliberately outside this operation.
    """
    requested_root = Path(root).absolute()
    data_path = Path(data_dir).resolve(strict=False)
    if requested_root.is_symlink():
        raise VoicePrivacyError("le dossier vocal ne peut pas être un lien symbolique")
    root_path = requested_root.resolve(strict=False)
    if root_path.name != "voice" or not _is_within(root_path, data_path):
        raise VoicePrivacyError("suppression limitée au dossier data/voice")
    if not root_path.exists():
        return {"deleted": True, "files": 0, "bytes": 0, "scope": "data/voice"}

    files = 0
    size = 0
    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        for name in list(dirnames):
            item = current_path / name
            if item.is_symlink():
                files += 1
                dirnames.remove(name)
        for name in filenames:
            item = current_path / name
            files += 1
            try:
                size += item.lstat().st_size
            except OSError:
                pass

    tombstone = data_path / f".voice-delete-{uuid.uuid4().hex}"
    os.replace(root_path, tombstone)
    try:
        shutil.rmtree(tombstone)
    except Exception as exc:
        try:
            if not root_path.exists() and tombstone.exists():
                os.replace(tombstone, root_path)
        except Exception:
            pass
        raise VoicePrivacyError("suppression vocale incomplète; restauration tentée") from exc
    return {"deleted": True, "files": files, "bytes": size, "scope": "data/voice"}

