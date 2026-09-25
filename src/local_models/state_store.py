"""Versioned durable state for model activation and role assignments."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json
from src.utils.paths import DATA_DIR

from .contracts import ModelReference, utc_now

_VERSION = 1


def _default_state() -> dict[str, Any]:
    return {"version": _VERSION, "models": {}, "assignments": {}, "updated_at": utc_now()}


class LocalModelStateStore:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.getenv("LUMENA_LOCAL_MODEL_STATE_FILE", "").strip()
        self.path = (
            Path(configured) if configured and path is None else (path or DATA_DIR / "local_models" / "state.json")
        )
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=5)

    @staticmethod
    def key(reference: ModelReference) -> str:
        return f"{reference.source.value}:{reference.canonical}"

    def load(self) -> dict[str, Any]:
        with self._thread_lock, self._file_lock:
            data = safe_read_json(self.path, default={})
            if type(data) is not dict or data.get("version") != _VERSION:
                return _default_state()
            if type(data.get("models")) is not dict or type(data.get("assignments")) is not dict:
                return _default_state()
            return deepcopy(data)

    def _mutate(self, operation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._thread_lock, self._file_lock:
            data = safe_read_json(self.path, default={})
            if type(data) is not dict or data.get("version") != _VERSION:
                data = _default_state()
            data.setdefault("models", {})
            data.setdefault("assignments", {})
            operation(data)
            data["version"] = _VERSION
            data["updated_at"] = utc_now()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
            return deepcopy(data)

    def set_enabled(self, reference: ModelReference, enabled: bool, *, verified: bool | None = None) -> dict[str, Any]:
        key = self.key(reference)

        def update(data):
            previous = data["models"].get(key, {})
            data["models"][key] = {
                **previous,
                "source": reference.source.value,
                "canonical": reference.canonical,
                "pull_reference": reference.pull_reference,
                "enabled": bool(enabled),
                "updated_at": utc_now(),
            }
            if verified is not None:
                data["models"][key]["verified"] = bool(verified)

        return self._mutate(update)["models"][key]

    def record_installed(
        self, reference: ModelReference, *, digest: str = "", size_bytes: int | None = None
    ) -> dict[str, Any]:
        key = self.key(reference)

        def update(data):
            previous = data["models"].get(key, {})
            data["models"][key] = {
                **previous,
                "source": reference.source.value,
                "canonical": reference.canonical,
                "pull_reference": reference.pull_reference,
                "installed": True,
                "digest": digest[:256],
                "size_bytes": size_bytes,
                "updated_at": utc_now(),
            }

        return self._mutate(update)["models"][key]

    def record_absent(self, reference: ModelReference) -> dict[str, Any]:
        key = self.key(reference)

        def update(data):
            previous = data["models"].get(key, {})
            data["models"][key] = {**previous, "installed": False, "enabled": False, "updated_at": utc_now()}
            data["assignments"] = {role: value for role, value in data["assignments"].items() if value != key}

        return self._mutate(update)["models"][key]

    def assign(self, role: str, reference: ModelReference) -> None:
        if role not in {"primary", "code", "vision", "web"}:
            raise ValueError("local_model_role_invalid")
        key = self.key(reference)
        self._mutate(lambda data: data["assignments"].__setitem__(role, key))

    def entry(self, reference: ModelReference) -> dict[str, Any]:
        return self.load()["models"].get(self.key(reference), {})

    def is_enabled(self, reference: ModelReference, *, legacy_default: bool = True) -> bool:
        entry = self.entry(reference)
        return bool(entry.get("enabled", legacy_default))
