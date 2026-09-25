"""Immutable external catalogues, separate from native handlers and MCP policy.

Schema/exposure validation does not authorize execution. A prepared call still
needs the host's role, trust, mission, confirmation and lease checks.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
import json
import re
from threading import RLock
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .tool_semantics import Availability, ModelExposure, ToolEffect, ToolSemantics


class ExternalToolError(ValueError):
    """Fixed codes only; do not expose parameter values through validation errors."""


def _json_copy(value: Any, *, limit: int) -> tuple[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        size = len(encoded.encode("utf-8"))
        copied = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ExternalToolError("external_payload_invalid") from None
    if size > limit:
        raise ExternalToolError("external_payload_too_large")
    return encoded, copied


@lru_cache(maxsize=512)
def _validator(schema_json: str) -> Draft202012Validator:
    try:
        schema = json.loads(schema_json)
        if (type(schema) is not dict or schema.get("type") != "object"
                or schema.get("additionalProperties") is not False):
            raise ExternalToolError("external_schema_must_be_closed")
        pending = [schema]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if any(key in item for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                    raise ExternalToolError("external_schema_reference_forbidden")
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except ExternalToolError:
        raise
    except (ValueError, TypeError, RecursionError, SchemaError):
        raise ExternalToolError("external_schema_invalid") from None


CallResolver = Callable[[ToolSemantics, dict], ToolSemantics]


@dataclass(frozen=True, slots=True)
class ExternalToolSpec:
    semantics: ToolSemantics
    description: str
    schema_json: str
    # LOCAL code only. Conditional templates cannot authorize their unresolved effect.
    call_resolver: CallResolver | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.semantics) is not ToolSemantics:
            raise ExternalToolError("external_semantics_required")
        if type(self.description) is not str or len(self.description) > 8192:
            raise ExternalToolError("external_description_invalid")
        if type(self.schema_json) is not str:
            raise ExternalToolError("external_schema_invalid")
        try:
            schema_size = len(self.schema_json.encode("utf-8"))
        except UnicodeError:
            raise ExternalToolError("external_schema_invalid") from None
        if schema_size > 65536:
            raise ExternalToolError("external_schema_invalid")
        _validator(self.schema_json)
        if self.call_resolver is not None and (
            not callable(self.call_resolver) or self.semantics.effect is not ToolEffect.PARAMETER_DEPENDENT
        ):
            raise ExternalToolError("external_resolver_invalid")

    @property
    def name(self) -> str:
        return self.semantics.tool_name

    @property
    def model_candidate(self) -> bool:
        descriptor = self.semantics
        conditional = (descriptor.effect is ToolEffect.PARAMETER_DEPENDENT and self.call_resolver is not None
                       and descriptor.availability is Availability.READY
                       and descriptor.model_exposure in {ModelExposure.DIRECT, ModelExposure.CONTEXTUAL})
        return descriptor.model_exposable or conditional

    def api_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": json.loads(self.schema_json)}}

    def prepare(self, parameters: dict) -> PreparedExternalCall:
        if not self.model_candidate:
            raise ExternalToolError("external_tool_not_exposed")
        if type(parameters) is not dict:
            raise ExternalToolError("external_parameters_invalid")
        encoded, snapshot = _json_copy(parameters, limit=1_000_000)
        if not _validator(self.schema_json).is_valid(snapshot):
            raise ExternalToolError("external_parameters_invalid")
        descriptor = self.semantics
        if self.call_resolver is not None:
            descriptor = self.call_resolver(descriptor, snapshot)
            # Resolution may inspect a copy, never rewrite the command to execute.
            if _json_copy(snapshot, limit=1_000_000)[0] != encoded:
                raise ExternalToolError("external_resolver_mutated_parameters")
        if (type(descriptor) is not ToolSemantics or not descriptor.model_exposable
                or any(getattr(descriptor, key) != getattr(self.semantics, key) for key in (
                    "tool_name", "provider_kind", "provider_instance_id", "catalog_revision", "availability",
                    "model_exposure", "risk_floor", "confirmation", "idempotency", "sensitive_fields"))):
            raise ExternalToolError("external_call_unresolved")
        if list(type(descriptor.mission_policy)).index(descriptor.mission_policy) > list(
            type(self.semantics.mission_policy)
        ).index(self.semantics.mission_policy):
            raise ExternalToolError("external_resolver_expanded_mission_policy")
        return PreparedExternalCall(self, descriptor, encoded)


@dataclass(frozen=True, slots=True)
class PreparedExternalCall:
    spec: ExternalToolSpec
    semantics: ToolSemantics
    parameters_json: str = field(repr=False)

    @property
    def parameters(self) -> dict:
        return json.loads(self.parameters_json)

    def audit_summary(self) -> dict:
        # Even paths/non-sensitive strings can contain secrets. Record keys only.
        return {"tool": self.spec.name, "effect": self.semantics.effect.value,
                "parameter_keys": sorted(self.parameters),
                "instance_id": self.semantics.provider_instance_id,
                "catalog_revision": self.semantics.catalog_revision}


@dataclass(frozen=True, slots=True)
class ExternalProviderSnapshot:
    provider_id: str
    state: str
    tools: tuple[ExternalToolSpec, ...]
    binding: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.provider_id) is not str or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", self.provider_id):
            raise ExternalToolError("external_provider_invalid")
        if self.state not in {"unavailable", "launch_only", "ready", "degraded"}:
            raise ExternalToolError("external_provider_state_invalid")
        if type(self.tools) is not tuple or any(type(tool) is not ExternalToolSpec for tool in self.tools):
            raise ExternalToolError("external_tools_invalid")
        if self.state in {"unavailable", "degraded"} and self.tools:
            raise ExternalToolError("external_unavailable_tools")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ExternalToolError("external_tool_collision")
        if any(not tool.model_candidate for tool in self.tools):
            raise ExternalToolError("external_tool_not_exposed")


@dataclass(frozen=True, slots=True)
class ExternalToolCatalog:
    providers: tuple[ExternalProviderSnapshot, ...]

    def __post_init__(self) -> None:
        if type(self.providers) is not tuple or any(type(p) is not ExternalProviderSnapshot for p in self.providers):
            raise ExternalToolError("external_providers_invalid")
        ids = [p.provider_id for p in self.providers]
        names = [t.name for p in self.providers for t in p.tools]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ExternalToolError("external_tool_collision")

    def schemas(self) -> list[dict]:
        return [tool.api_schema() for provider in self.providers for tool in provider.tools]

    def resolve(self, name: str) -> tuple[ExternalProviderSnapshot, ExternalToolSpec]:
        for provider in self.providers:
            for tool in provider.tools:
                if tool.name == name:
                    return provider, tool
        raise ExternalToolError("external_tool_not_in_snapshot")


class ExternalToolProvider(Protocol):
    provider_id: str

    def capture(self) -> ExternalProviderSnapshot: ...


class ExternalToolProviderRegistry:
    """Boot-owned providers. Capturing never modifies an existing run catalogue."""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalToolProvider] = {}
        self._lock = RLock()

    def register(self, provider: ExternalToolProvider) -> None:
        with self._lock:
            if provider.provider_id in self._providers:
                raise ExternalToolError("external_provider_collision")
            self._providers[provider.provider_id] = provider

    def capture(self, *, reserved_names: frozenset[str] = frozenset()) -> ExternalToolCatalog:
        with self._lock:
            providers = tuple(self._providers.items())
        snapshots = []
        for name, provider in providers:
            snapshot = provider.capture()
            if type(snapshot) is not ExternalProviderSnapshot or snapshot.provider_id != name:
                raise ExternalToolError("external_provider_identity_changed")
            if any(tool.name in reserved_names for tool in snapshot.tools):
                raise ExternalToolError("external_native_collision")
            snapshots.append(snapshot)
        return ExternalToolCatalog(tuple(snapshots))


_CURRENT_CATALOG: ContextVar[ExternalToolCatalog | None] = ContextVar("lumena_external_tool_catalog", default=None)


def current_external_catalog() -> ExternalToolCatalog | None:
    return _CURRENT_CATALOG.get()


@contextmanager
def bind_external_catalog(catalog: ExternalToolCatalog) -> Iterator[ExternalToolCatalog]:
    """Scope only external providers; native/MCP read-through remains unchanged."""
    if type(catalog) is not ExternalToolCatalog:
        raise ExternalToolError("external_catalog_required")
    token = _CURRENT_CATALOG.set(catalog)
    try:
        yield catalog
    finally:
        _CURRENT_CATALOG.reset(token)
