"""Read-only official MCP Registry search for the admin panel."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from src.mcp.official_registry import OfficialMCPRegistryClient
from src.utils.paths import DATA_DIR
from web.routes.deps import verify_admin_token


router = APIRouter()
_client = OfficialMCPRegistryClient(
    cache_path=DATA_DIR / "mcp_registry" / "official-v0.1.json"
)


@router.get(
    "/api/mcp/registry/search",
    dependencies=[Depends(verify_admin_token)],
)
async def search_official_mcp_registry(
    q: str = Query(default="", max_length=256),
    limit: int = Query(default=30, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        records = _client.search(q.strip(), limit=limit)
    except Exception:
        return {"available": False, "source": "official_registry", "items": []}
    items: List[Dict[str, Any]] = []
    for record in records:
        package = next((
            p for p in record.packages
            if p.registry_type in {"npm", "pypi"} and p.transport == "stdio"
        ), None)
        remote = next((
            r for r in record.remotes
            if r.transport_type in {"streamable-http", "streamable_http", "sse"}
        ), None)
        if package is None and remote is None:
            continue
        target = (
            f"{package.registry_type}:{package.identifier}"
            if package is not None else remote.url
        )
        items.append({
            "name": record.canonical_name,
            "display_name": record.display_name,
            "description": record.description[:1000],
            "version": (package.version if package else None) or record.version,
            "transport": "stdio" if package is not None else remote.transport_type,
            "target": target,
            "publisher": record.publisher_namespace,
        })
    return {
        "available": True,
        "source": "official_registry",
        "query": q.strip(),
        "items": items,
    }
