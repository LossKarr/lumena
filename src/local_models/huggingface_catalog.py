"""Read-only Hugging Face Hub search adapter restricted to GGUF models."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .contracts import CatalogModel
from .identifiers import parse_model_reference

_QUANT = re.compile(r"(?:^|[-_.])(IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|F16|BF16)(?:[-_.]|\.gguf$)", re.I)


class HuggingFaceCatalogError(RuntimeError):
    pass


def _license(item: dict[str, Any]) -> str | None:
    card = item.get("cardData") if type(item.get("cardData")) is dict else {}
    value = card.get("license")
    if type(value) is str and len(value) <= 128:
        return value
    tags = item.get("tags") if type(item.get("tags")) is list else []
    for tag in tags:
        if type(tag) is str and tag.startswith("license:"):
            return tag.split(":", 1)[1][:128]
    return None


def _gguf_metadata(item: dict[str, Any]) -> tuple[int | None, tuple[str, ...]]:
    siblings = item.get("siblings") if type(item.get("siblings")) is list else []
    sizes: list[int] = []
    quants: set[str] = set()
    for sibling in siblings[:10000]:
        if type(sibling) is not dict:
            continue
        filename = sibling.get("rfilename")
        if type(filename) is not str or not filename.lower().endswith(".gguf"):
            continue
        if type(sibling.get("size")) is int and sibling["size"] > 0:
            sizes.append(sibling["size"])
        match = _QUANT.search(filename)
        if match:
            quants.add(match.group(1).upper())
    size = sizes[0] if len(sizes) == 1 else None
    return size, tuple(sorted(quants))


class HuggingFaceCatalog:
    endpoint = "https://huggingface.co"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def search(
        self, query: str = "", *, limit: int = 30, offset: int = 0
    ) -> tuple[list[CatalogModel], dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, min(int(offset), 10000))
        params: list[tuple[str, str | int]] = [
            ("filter", "gguf"),
            ("sort", "downloads"),
            ("direction", "-1"),
            ("limit", safe_limit + safe_offset),
            ("full", "true"),
        ]
        if query.strip():
            params.append(("search", query.strip()[:160]))
        token = os.getenv("HF_TOKEN", os.getenv("HUGGING_FACE_HUB_TOKEN", "")).strip()
        headers = {"Accept": "application/json", "User-Agent": "Lumena/local-model-catalog"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(
                base_url=self.endpoint,
                timeout=20.0,
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.get("/api/models", params=params, headers=headers)
            if response.is_redirect or response.status_code >= 400 or len(response.content) > 8 * 1024 * 1024:
                raise HuggingFaceCatalogError("huggingface_catalog_unavailable")
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise HuggingFaceCatalogError("huggingface_catalog_unavailable") from None
        if type(payload) is not list:
            raise HuggingFaceCatalogError("huggingface_catalog_invalid")

        results: list[CatalogModel] = []
        for item in payload[safe_offset : safe_offset + safe_limit]:
            if type(item) is not dict:
                continue
            repo_id = item.get("id") or item.get("modelId")
            if type(repo_id) is not str:
                continue
            tags = tuple(str(tag)[:128] for tag in item.get("tags", [])[:100] if type(tag) is str)
            if not any(tag.casefold() == "gguf" for tag in tags):
                continue
            try:
                ref = parse_model_reference(repo_id, "huggingface")
            except ValueError:
                continue
            size, quantizations = _gguf_metadata(item)
            pipeline = str(item.get("pipeline_tag") or "")[:80]
            capabilities = ["text"]
            if "image" in pipeline or any("vision" in tag.casefold() for tag in tags):
                capabilities.append("vision")
            results.append(
                CatalogModel(
                    reference=ref,
                    display_name=repo_id,
                    description=pipeline,
                    category=pipeline or "text-generation",
                    size_bytes=size,
                    quantization=",".join(quantizations[:20]),
                    license=_license(item),
                    gated=item.get("gated") not in {None, False, "false"},
                    downloads=item.get("downloads") if type(item.get("downloads")) is int else None,
                    capabilities=tuple(capabilities),
                    provenance=("huggingface-api",),
                    confidence="hub_metadata",
                    partial=not bool(quantizations),
                )
            )
        return results, {
            "source": "huggingface-api",
            "partial": len(payload) >= safe_limit + safe_offset,
            "offset": safe_offset,
            "limit": safe_limit,
        }
