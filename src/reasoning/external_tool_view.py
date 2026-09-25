"""Context-bound mapping projection; native/MCP entries remain live and mutable."""
from __future__ import annotations

from collections.abc import MutableMapping

from .external_tool_registry import ExternalToolError
from ..utils.external_tool_names import is_ide_tool_name


class ExternalToolView(MutableMapping):
    def __init__(self, base: dict, project) -> None:
        self.base = base
        self._project = project

    def native_items(self):
        return ((name, entry) for name, entry in self.base.items() if not is_ide_tool_name(name))

    def _snapshot(self) -> dict:
        return {**dict(self.native_items()), **self._project()}

    def __getitem__(self, name):
        if not is_ide_tool_name(name) and name in self.base:
            return self.base[name]
        return self._project()[name]

    def __setitem__(self, name, value):
        if is_ide_tool_name(name):
            raise ExternalToolError("external_namespace_reserved")
        self.base[name] = value

    def __delitem__(self, name):
        if is_ide_tool_name(name):
            raise ExternalToolError("external_namespace_reserved")
        del self.base[name]

    def __iter__(self):
        return iter(self._snapshot())

    def __len__(self):
        return len(self._snapshot())

    def items(self):
        return self._snapshot().items()

    def keys(self):
        return self._snapshot().keys()

    def values(self):
        return self._snapshot().values()

    def copy(self):
        return self._snapshot()
