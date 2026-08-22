"""Isolated DuckDuckGo search worker.

The ddgs/primp native stack can block the hosting process under concurrent use.
Keeping it in a short-lived subprocess gives SearchHub a real kill boundary.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    count = max(1, min(int(payload.get("count") or 8), 20))

    try:
        from ddgs.http_client import HttpClient

        HttpClient._impersonates = ("chrome", "safari", "firefox", "edge")
    except Exception:
        pass

    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]

    with DDGS() as ddg:
        return list(ddg.text(query, max_results=count, timelimit="y"))


def _read_payload() -> dict[str, Any]:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw or "{}")


def _write_result(result: dict[str, Any]) -> None:
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(encoded)
        stream.flush()
    else:
        sys.stdout.write(encoded.decode("utf-8"))


def main() -> int:
    try:
        payload = _read_payload()
        result = {"ok": True, "results": _search(payload)}
        code = 0
    except Exception as exc:
        result = {"ok": False, "results": [], "error": str(exc)}
        code = 1
    _write_result(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
