"""Versioned state and activation pointers for large Lumena components."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from src.utils.persistence import atomic_write_json, safe_read_json


@dataclass(frozen=True)
class ActiveComponent:
    component_id: str
    version: str
    path: str
    archive_sha256: str
    activated_at: str


class ComponentStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.state_path = self.root / "component-state.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def state(self) -> dict[str, Any]:
        value = safe_read_json(self.state_path, default={})
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            return {"schema_version": 1, "active": {}, "previous": {}}
        active = value.get("active") if isinstance(value.get("active"), Mapping) else {}
        previous = value.get("previous") if isinstance(value.get("previous"), Mapping) else {}
        return {"schema_version": 1, "active": dict(active), "previous": dict(previous)}

    def active(self, component_id: str) -> ActiveComponent | None:
        value = self.state()["active"].get(component_id)
        if not isinstance(value, Mapping):
            return None
        try:
            result = ActiveComponent(
                component_id=component_id, version=str(value["version"]),
                path=str(value["path"]), archive_sha256=str(value["archive_sha256"]),
                activated_at=str(value["activated_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        target = (self.root / Path(result.path)).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None
        return result if target.is_dir() else None

    def activate(self, component: ActiveComponent) -> None:
        state = self.state()
        current = state["active"].get(component.component_id)
        if isinstance(current, Mapping):
            state["previous"][component.component_id] = dict(current)
        state["active"][component.component_id] = asdict(component)
        atomic_write_json(self.state_path, state)

    def rollback(self, component_id: str) -> ActiveComponent:
        state = self.state()
        previous = state["previous"].get(component_id)
        if not isinstance(previous, Mapping):
            raise RuntimeError("no previous component generation")
        current = state["active"].get(component_id)
        if isinstance(current, Mapping):
            state["previous"][component_id] = dict(current)
        state["active"][component_id] = dict(previous)
        atomic_write_json(self.state_path, state)
        result = self.active(component_id)
        if result is None:
            raise RuntimeError("previous component generation is unavailable")
        return result
