"""Document-oriented web search built on Lumena's existing SearchHub."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_WEB_DOCUMENT_FORMATS = ("pdf", "docx", "xlsx", "pptx", "csv", "odt", "ods", "rtf")


@dataclass(frozen=True)
class DocumentSearchCandidate:
    title: str
    url: str
    description: str
    detected_format: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentWebSearch:
    def __init__(self, search_hub: Any):
        self.search_hub = search_hub

    async def search(
        self,
        query: str,
        *,
        formats: list[str] | tuple[str, ...] | None = None,
        count: int = 12,
    ) -> dict[str, Any]:
        wanted = tuple(
            dict.fromkeys(str(item).lower().lstrip(".") for item in (formats or DEFAULT_WEB_DOCUMENT_FORMATS) if item)
        )
        suffix_query = " OR ".join(f"filetype:{fmt}" for fmt in wanted)
        enriched = f"{str(query).strip()} ({suffix_query})" if suffix_query else str(query).strip()
        raw = await self.search_hub.web_search(enriched, count=max(1, min(int(count), 30)))
        candidates: list[DocumentSearchCandidate] = []
        for item in raw.get("results", []):
            url = str(item.get("url", "")).strip()
            fmt = Path(urlparse(url).path).suffix.lower().lstrip(".")
            text = f"{item.get('title', '')} {item.get('description', '')}".lower()
            if not fmt:
                fmt = next((candidate for candidate in wanted if f".{candidate}" in text), "")
            if wanted and fmt not in wanted:
                continue
            candidates.append(
                DocumentSearchCandidate(
                    title=str(item.get("title", "")).strip(),
                    url=url,
                    description=str(item.get("description", "")).strip(),
                    detected_format=fmt,
                    source=str(raw.get("source", "web")),
                )
            )
        return {
            "query": str(query).strip(),
            "provider": raw.get("source", "unknown"),
            "formats": list(wanted),
            "candidates": [candidate.to_dict() for candidate in candidates],
            "error": raw.get("error", ""),
        }
