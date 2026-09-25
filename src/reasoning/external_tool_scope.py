"""One lazy external catalogue per run, including streaming and nested agents."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import isasyncgenfunction
from threading import RLock

from .external_tool_registry import ExternalToolCatalog, current_external_catalog


class ExternalToolRun:
    def __init__(self) -> None:
        self._catalogs: dict[object, ExternalToolCatalog] = {}
        self._lock = RLock()

    def capture(self, owner, factory) -> ExternalToolCatalog:
        # Also protects the first capture by concurrent child/agent threads.
        with self._lock:
            if owner not in self._catalogs:
                self._catalogs[owner] = factory()
            return self._catalogs[owner]

    def oublier(self, owner) -> None:
        """LOT L5-4a : jeter une entree que l'appelant vient de rendre obsolete."""
        with self._lock:
            self._catalogs.pop(owner, None)


_RUN: ContextVar[ExternalToolRun | None] = ContextVar("lumena_external_tool_run", default=None)


def run_external_catalog(owner, factory) -> ExternalToolCatalog:
    explicit = current_external_catalog()
    if explicit is not None:
        return explicit
    run = _RUN.get()
    return run.capture(owner, factory) if run is not None else factory()


def oublier_catalogue_externe(owner) -> None:
    """Oublier UNE entree de catalogue du tour en cours.

    LOT L5-4a - defaut trouve par le canari reel, invisible aux 22 750 tests verts
    de L5-3c : le rail interroge le catalogue du dossier de mission AVANT d'ouvrir
    l'instance, donc capture `launch_only` ; apres l'ouverture, il relit ce cache et
    continue de croire qu'aucune IDE n'existe la. Resultat mesure :
    `ide_mission_workspace_mismatch` alors que l'instance etait vivante et distincte.

    Le cache reste indispensable (CONN-3C : un catalogue stable pendant un tour).
    Ce qui est faux, c'est de garder une entree que l'on vient SOI-MEME de rendre
    obsolete. L'oubli est donc cible sur une cle : ni la proprietaire, ni le chat ne
    sont touches. Hors run, c'est un non-evenement.
    """
    run = _RUN.get()
    if run is not None:
        run.oublier(owner)


@contextmanager
def external_tool_run(run: ExternalToolRun | None = None):
    scope = run or _RUN.get() or ExternalToolRun()
    token = _RUN.set(scope)
    try:
        yield scope
    finally:
        _RUN.reset(token)


def scoped_external_tools(function):
    """Preserve nested run scope without leaking ContextVars across stream yields."""
    if isasyncgenfunction(function):
        @wraps(function)
        async def stream(*args, **kwargs):
            scope = _RUN.get() or ExternalToolRun()
            iterator = function(*args, **kwargs)
            try:
                while True:
                    # Tokens are created/reset in the SAME __anext__ context.
                    with external_tool_run(scope):
                        try:
                            item = await anext(iterator)
                        except StopAsyncIteration:
                            return
                    yield item
            finally:
                with external_tool_run(scope):
                    await iterator.aclose()
        return stream

    @wraps(function)
    async def call(*args, **kwargs):
        with external_tool_run():
            return await function(*args, **kwargs)
    return call
