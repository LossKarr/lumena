"""Best-effort Ollama catalogue search with an explicit curated fallback.

Ollama documents no stable exhaustive public library API. The HTML adapter is
therefore isolated, bounded and disableable. Direct validated identifiers stay
installable even when the public index is unavailable or changes shape.
"""

from __future__ import annotations

from dataclasses import replace
import html
import os
import re
from typing import Any

import httpx

from .contracts import CatalogModel
from .identifiers import parse_model_reference


def _size_bytes(label: object) -> int | None:
    if type(label) is not str:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(GB|MB)", label, re.I)
    if not match:
        return None
    amount = float(match.group(1))
    multiplier = 1024**3 if match.group(2).upper() == "GB" else 1024**2
    return int(amount * multiplier)


class OllamaLibrarySource:
    name = "ollama_public_search"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        public_search: bool | None = None,
    ) -> None:
        self._transport = transport
        self.public_search = (
            public_search
            if public_search is not None
            else os.getenv("LUMENA_LOCAL_MODELS_OLLAMA_LIBRARY", "1").lower() in {"1", "true", "yes", "on"}
        )

    async def _public_models(self, query: str) -> list[CatalogModel]:
        if not self.public_search:
            return []
        timeout = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            response = await client.get("https://ollama.com/search", params={"q": query[:160]})
        if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
            raise RuntimeError("ollama_library_unavailable")

        names: list[str] = []
        for raw in re.findall(r'href=["\']/library/([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})["\']', response.text):
            name = html.unescape(raw)
            if name not in names:
                names.append(name)
        return [
            CatalogModel(
                reference=parse_model_reference(name, "ollama"),
                display_name=name,
                provenance=("ollama.com/search",),
                confidence="official-index",
                partial=True,
            )
            for name in names
        ]

    async def search(
        self, query: str = "", *, limit: int = 30, offset: int = 0
    ) -> tuple[list[CatalogModel], dict[str, Any]]:
        from src.llm.providers import MODEL_CATALOG_REVISION, OLLAMA_CATALOG

        needle = query.strip().casefold()[:160]
        public_available = False
        public_error = None
        try:
            public = await self._public_models(needle)
            public_available = self.public_search
        except (httpx.HTTPError, RuntimeError):
            public = []
            public_error = "ollama_library_unavailable"

        indexed: dict[str, CatalogModel] = {item.reference.canonical.casefold(): item for item in public}
        for entry in OLLAMA_CATALOG:
            haystack = " ".join(str(entry.get(key, "")) for key in ("id", "desc", "category", "params")).casefold()
            if needle and needle not in haystack:
                continue
            ref = parse_model_reference(entry["id"], "ollama")
            category = str(entry.get("category") or "llm")
            capabilities = (
                ("embedding",)
                if category == "embedding"
                else (("vision", "text") if category == "vision" else (category, "text"))
            )
            curated = CatalogModel(
                reference=ref,
                display_name=entry["id"],
                description=str(entry.get("desc") or ""),
                category=category,
                size_bytes=_size_bytes(entry.get("size")),
                capabilities=capabilities,
                provenance=(f"lumena-curated:{MODEL_CATALOG_REVISION}",),
                confidence="curated",
                partial=True,
            )
            previous = indexed.get(ref.canonical.casefold())
            if previous:
                curated = replace(
                    curated,
                    provenance=tuple(sorted(set(curated.provenance + previous.provenance))),
                )
            indexed[ref.canonical.casefold()] = curated

        items = list(indexed.values())
        start = max(0, offset)
        page = items[start : start + max(1, min(limit, 100))]
        return page, {
            "source": self.name,
            "partial": True,
            "total": len(items),
            "coverage": "official_public_search_plus_curated_plus_direct_identifier",
            "public_search_available": public_available,
            "public_search_error": public_error,
        }
