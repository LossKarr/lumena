"""Symmetric adapter between installed Ollama models and Lumena's runtime catalog."""

from __future__ import annotations

from .contracts import ModelReference
from .state_store import LocalModelStateStore


def lumena_model_key(reference: ModelReference) -> str:
    from src.llm.providers import _ollama_key

    return _ollama_key(reference.pull_reference)


def register_local_model(
    reference: ModelReference, state_store: LocalModelStateStore, *, verified: bool = False
) -> str:
    from src.llm.providers import register_ollama_models

    register_ollama_models([reference.pull_reference])
    state_store.set_enabled(reference, True, verified=verified)
    return lumena_model_key(reference)


def unregister_local_model(reference: ModelReference, state_store: LocalModelStateStore) -> bool:
    """Disable one dynamic Ollama entry without touching its disk files."""
    from src.llm.providers import AVAILABLE_MODELS, LOCAL_VALIDATED_MODELS, MODEL_SKILLS, ProviderType

    key = lumena_model_key(reference)
    config = AVAILABLE_MODELS.get(key)
    removed = False
    if config is not None and config.provider == ProviderType.OLLAMA and config.model_id == reference.pull_reference:
        AVAILABLE_MODELS.pop(key, None)
        MODEL_SKILLS.pop(key, None)
        for values in LOCAL_VALIDATED_MODELS.values():
            while key in values:
                values.remove(key)
        removed = True
    state_store.set_enabled(reference, False)
    return removed


def reconcile_registry(installed: list[ModelReference], state_store: LocalModelStateStore) -> dict[str, int]:
    """Apply durable disabled choices while preserving legacy enabled installs."""
    installed_keys = {state_store.key(reference) for reference in installed}
    state = state_store.load()
    enabled = disabled = absent = 0
    for reference in installed:
        entry = state["models"].get(state_store.key(reference), {})
        if entry.get("enabled", True):
            register_local_model(reference, state_store, verified=bool(entry.get("verified", False)))
            enabled += 1
        else:
            unregister_local_model(reference, state_store)
            disabled += 1
    for key, entry in state["models"].items():
        if entry.get("installed") and key not in installed_keys:
            try:
                from .identifiers import parse_model_reference

                reference = parse_model_reference(entry["canonical"], entry["source"])
                state_store.record_absent(reference)
                absent += 1
            except (KeyError, ValueError):
                continue
    return {"enabled": enabled, "disabled": disabled, "absent": absent}
