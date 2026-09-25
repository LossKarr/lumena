"""Cross-thread cache freshness for external effects, never authorization.

The native cache keeps its existing behavior while no external effect occurs.
Active or uncertain operations suspend caching; only observed completion clears
that suspension. Capacity is bounded and cannot evict an active operation.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid

from .external_tool_registry import ExternalToolError
from .tool_result import ExecutionStatus, ToolExecutionResult
from .tool_semantics import ToolEffect, ToolSemantics


@dataclass(frozen=True, slots=True)
class EffectTicket:
    token: str
    semantics: ToolSemantics
    workspace_id: str | None


class ExternalEffectCache:
    def __init__(self, capacity: int = 256):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError("invalid external effect capacity")
        self._capacity = capacity
        self._lock = threading.RLock()
        self._epoch = 0
        self._active: dict[str, EffectTicket] = {}

    def snapshot(self) -> tuple[int, bool]:
        with self._lock:
            return self._epoch, bool(self._active)

    def begin(self, semantics: ToolSemantics, workspace_id: str | None) -> EffectTicket | None:
        if type(semantics) is not ToolSemantics or semantics.effect in {
            ToolEffect.UNKNOWN,
            ToolEffect.PARAMETER_DEPENDENT,
        }:
            raise ExternalToolError("external_effect_unresolved")
        if semantics.effect is ToolEffect.READ_ONLY:
            return None
        with self._lock:
            if len(self._active) >= self._capacity:
                raise ExternalToolError("external_effect_capacity_reached")
            ticket = EffectTicket(uuid.uuid4().hex, semantics, workspace_id)
            self._active[ticket.token] = ticket
            self._epoch += 1
            return ticket

    def complete(self, ticket: EffectTicket | None, result: ToolExecutionResult | None, *, evidence=None) -> bool:
        if ticket is None:
            return True
        if type(result) is not ToolExecutionResult:
            return False
        semantics = ticket.semantics
        if (
            result.tool_name != semantics.tool_name
            or result.effect is not semantics.effect
            or result.provider_instance_id != semantics.provider_instance_id
            or result.catalog_revision != semantics.catalog_revision
            or result.workspace_id != ticket.workspace_id
        ):
            return False
        if semantics.effect in {
            ToolEffect.PROCESS_LAUNCH,
            ToolEffect.PROCESS_CONTROL,
            ToolEffect.PROCESS_COMPLETION,
            ToolEffect.TEST_EXECUTION,
        }:
            from .execution_evidence import VerifiedExecutionEvidence

            if (
                type(evidence) is not VerifiedExecutionEvidence
                or evidence.operation_id != result.operation_id
                or evidence.tool_name != result.tool_name
                or evidence.effect is not result.effect
                or evidence.provider_instance_id != result.provider_instance_id
                or evidence.catalog_revision != result.catalog_revision
                or evidence.workspace_id != result.workspace_id
            ):
                return False
        elif result.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
            return False
        with self._lock:
            if self._active.get(ticket.token) != ticket:
                return False
            del self._active[ticket.token]
            self._epoch += 1
            return True


    def abandonner(self, ticket: EffectTicket | None) -> bool:
        """LOT CONN-7b — liberer un ticket dont l'operation n'a jamais eu lieu.

        Distinct de `complete()`, et volontairement : `complete()` exige un
        `ToolExecutionResult` conforme, parce qu'il atteste qu'une operation s'est
        TERMINEE. Ici il n'y a pas de resultat a produire - le transport a leve
        avant que quoi que ce soit ne parte.

        Sans cette porte, un tel ticket restait actif a vie et suspendait le cache
        d'observation de tout le processus, en silence.
        """
        if ticket is None:
            return True
        with self._lock:
            if self._active.get(ticket.token) != ticket:
                return False
            del self._active[ticket.token]
            self._epoch += 1
            return True


_effects = ExternalEffectCache()


def external_effect_cache() -> ExternalEffectCache:
    return _effects


def observation_cache_epoch(registry) -> int | None:
    epoch, active = _effects.snapshot()
    if getattr(registry, "_external_cache_epoch", 0) != epoch:
        registry._observation_cache.clear()
        registry._observation_cache_hits.clear()
        registry._external_cache_epoch = epoch
    return None if active else epoch
