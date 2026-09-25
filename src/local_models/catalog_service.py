"""Federated, cacheable local-model catalogue with explicit provenance."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import time
from typing import Any

from .contracts import CatalogModel
from .huggingface_catalog import HuggingFaceCatalog
from .ollama_library import OllamaLibrarySource


class LocalModelCatalogService:
    def __init__(self, *, ollama_source=None, huggingface_source=None, ttl_seconds: int | None = None) -> None:
        self.ollama_source = ollama_source or OllamaLibrarySource()
        self.huggingface_source = huggingface_source or HuggingFaceCatalog()
        self.ttl_seconds = (
            ttl_seconds if ttl_seconds is not None else max(30, int(os.getenv("LUMENA_LOCAL_MODEL_CATALOG_TTL", "900")))
        )
        self._cache: dict[tuple[str, str, int, int], tuple[float, list[CatalogModel], dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def search(self, query: str = "", *, source: str = "all", limit: int = 30, offset: int = 0) -> dict[str, Any]:
        normalized_source = source.casefold().strip()
        if normalized_source not in {"all", "ollama", "huggingface"}:
            raise ValueError("catalog_source_invalid")
        query = query.strip()[:160]
        limit = max(1, min(int(limit), 100))
        offset = max(0, min(int(offset), 10000))
        key = (normalized_source, query.casefold(), limit, offset)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] <= self.ttl_seconds:
            return {
                "models": [replace(item, stale=False).as_dict() for item in cached[1]],
                "sources": cached[2],
                "cached": True,
            }

        async with self._lock:
            tasks = []
            names = []
            if normalized_source in {"all", "ollama"}:
                names.append("ollama")
                tasks.append(self.ollama_source.search(query, limit=limit, offset=offset))
            if normalized_source in {"all", "huggingface"}:
                names.append("huggingface")
                tasks.append(self.huggingface_source.search(query, limit=limit, offset=offset))
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            merged: dict[tuple[str, str], CatalogModel] = {}
            source_meta: dict[str, Any] = {}
            for name, outcome in zip(names, outcomes):
                if isinstance(outcome, Exception):
                    source_meta[name] = {"available": False, "error_code": str(outcome)[:120]}
                    continue
                items, meta = outcome
                source_meta[name] = {"available": True, **meta}
                for item in items:
                    model_key = (item.reference.source.value, item.reference.canonical.casefold())
                    previous = merged.get(model_key)
                    if previous is None:
                        merged[model_key] = item
                    else:
                        merged[model_key] = replace(
                            previous, provenance=tuple(sorted(set(previous.provenance + item.provenance)))
                        )
            models = list(merged.values())[:limit]
            if not models and cached:
                stale = [replace(item, stale=True) for item in cached[1]]
                return {
                    "models": [item.as_dict() for item in stale],
                    "sources": source_meta,
                    "cached": True,
                    "stale": True,
                }
            self._cache[key] = (now, models, source_meta)
            return {
                "models": [item.as_dict() for item in models],
                "sources": source_meta,
                "cached": False,
                "stale": False,
            }
