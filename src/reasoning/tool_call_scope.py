"""CONN-5D-2 - registre qui execute l'outil en cours.

CodeAgent execute ses outils par le singleton `get_tool_system()`, lie au registre
du chat ; les registres de mission ne lui sont jamais lies (`registry_factory`).
Delegue par un worker, CodeAgent perdait donc le registre - et le perimetre - de la
mission qui l'avait lance.

`ToolRegistry.execute` expose ici son registre pendant l'execution d'un outil. Les
taches asyncio creees pendant cette execution (`delegate_task` lance CodeAgent par
`asyncio.create_task`) heritent d'une copie du contexte : CodeAgent retrouve le
registre de la mission. Seuls les outils IDE s'en servent (voir `tool_system`).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_CURRENT_TOOL_REGISTRY: ContextVar[Optional[Any]] = ContextVar("lumena_current_tool_registry", default=None)


def current_tool_registry() -> Optional[Any]:
    """Registre qui execute l'outil en cours dans ce contexte, sinon `None`."""
    return _CURRENT_TOOL_REGISTRY.get()


@contextmanager
def tool_registry_scope(registry: Any) -> Iterator[Any]:
    """Expose `registry` pour la duree du bloc, puis restaure le precedent."""
    token = _CURRENT_TOOL_REGISTRY.set(registry)
    try:
        yield registry
    finally:
        _CURRENT_TOOL_REGISTRY.reset(token)
