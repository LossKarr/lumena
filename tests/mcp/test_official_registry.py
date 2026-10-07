from __future__ import annotations

import json

import pytest

from src.mcp.official_registry import (
    OfficialMCPRegistryClient,
    OfficialRegistryError,
    OfficialRegistrySearchSource,
)
from src.mcp.proposal_planner import MCPProposalPlanner, MCPProposalPlannerDeps


def _record(name: str, *, status: str = "active", latest: bool = True):
    return {
        "server": {
            "name": name,
            "title": name.rsplit("/", 1)[-1].title(),
            "description": f"Server {name}",
            "version": "1.2.3",
            "packages": [{
                "registryType": "npm",
                "identifier": "@example/server",
                "version": "1.2.3",
                "transport": "stdio",
            }],
            "remotes": [{"type": "streamable-http", "url": "https://mcp.example.test/v1"}],
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {
            "status": status,
            "isLatest": latest,
            "publishedAt": "2026-01-01T00:00:00Z",
        }},
    }


def test_cursor_pagination_filters_non_active_and_non_latest(tmp_path):
    calls = []

    def fetch(url, headers, timeout):
        calls.append(url)
        if "cursor=" not in url:
            payload = {"servers": [_record("io.one/first"), _record("io.bad/deleted", status="deleted")],
                       "metadata": {"nextCursor": "page-two"}}
        else:
            payload = {"servers": [_record("io.two/second"), _record("io.old/old", latest=False)],
                       "metadata": {"nextCursor": ""}}
        return 200, {"ETag": '"abc"'}, json.dumps(payload).encode()

    client = OfficialMCPRegistryClient(cache_path=tmp_path / "cache.json", fetcher=fetch)
    records = client.search(limit=10)
    assert [record.canonical_name for record in records] == ["io.one/first", "io.two/second"]
    assert len(calls) == 2
    assert "version=latest" in calls[0]
    assert "cursor=page-two" in calls[1]


def test_offline_cache_and_search_are_deterministic(tmp_path):
    payload = {"servers": [_record("io.docs/search"), _record("io.code/editor")], "metadata": {}}
    client = OfficialMCPRegistryClient(
        cache_path=tmp_path / "cache.json",
        fetcher=lambda *_: (200, {}, json.dumps(payload).encode()),
    )
    client.search(limit=10)
    cached = client.search("docs", offline=True, limit=10)
    assert [item.canonical_name for item in cached] == ["io.docs/search"]


def test_etag_304_uses_validated_cache(tmp_path):
    payload = {"servers": [_record("io.cached/server")], "metadata": {}}
    first = OfficialMCPRegistryClient(
        cache_path=tmp_path / "cache.json",
        fetcher=lambda *_: (200, {"ETag": '"v1"'}, json.dumps(payload).encode()),
    )
    first.search()
    seen_headers = {}

    def unchanged(url, headers, timeout):
        seen_headers.update(headers)
        return 304, {}, b""

    second = OfficialMCPRegistryClient(cache_path=tmp_path / "cache.json", fetcher=unchanged)
    assert second.search()[0].canonical_name == "io.cached/server"
    assert seen_headers["If-None-Match"] == '"v1"'


def test_network_failure_uses_cache_but_empty_cache_fails(tmp_path):
    cache = tmp_path / "cache.json"
    failing = OfficialMCPRegistryClient(
        cache_path=cache,
        fetcher=lambda *_: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(TimeoutError):
        failing.search()


def test_payload_size_and_cursor_loop_fail_closed(tmp_path):
    too_large = OfficialMCPRegistryClient(
        cache_path=tmp_path / "large.json",
        max_response_bytes=1024,
        fetcher=lambda *_: (200, {}, b"{" + b"x" * 2048),
    )
    with pytest.raises(OfficialRegistryError, match="too_large"):
        too_large.search()

    payload = json.dumps({"servers": [], "metadata": {"nextCursor": "same"}}).encode()
    loop = OfficialMCPRegistryClient(
        cache_path=tmp_path / "loop.json",
        fetcher=lambda *_: (200, {}, payload),
    )
    with pytest.raises(OfficialRegistryError, match="cursor_loop"):
        loop.search()


def test_phase23_adapter_exposes_package_and_remote_only_records(tmp_path):
    supported = _record("io.docs/search")
    remote_only = _record("io.remote/only")
    remote_only["server"]["packages"] = []
    payload = {"servers": [supported, remote_only], "metadata": {}}
    client = OfficialMCPRegistryClient(
        cache_path=tmp_path / "cache.json",
        fetcher=lambda *_: (200, {}, json.dumps(payload).encode()),
    )
    source = OfficialRegistrySearchSource(client)
    rows = source.search({"docs", "search"}, limit=10)
    assert len(rows) == 2
    assert rows[0]["source"] == "official_registry"
    assert rows[0]["package_spec"] == "npm:@example/server"
    assert rows[0]["version"] == "1.2.3"
    assert rows[1]["package_transport"] == "remote"
    assert rows[1]["package_spec"].startswith("remote:io.remote-only-")
    assert rows[1]["connection_spec"]["transport"] == "streamable_http"
    assert rows[1]["connection_spec"]["remote"]["url"] == "https://mcp.example.test/v1"


def test_remote_only_official_record_reaches_catalog_proposal_with_contract(tmp_path):
    remote_only = _record("io.video/film-tools")
    remote_only["server"]["packages"] = []
    remote_only["server"]["description"] = "video film rendering tools"
    payload = {"servers": [remote_only], "metadata": {}}
    client = OfficialMCPRegistryClient(
        cache_path=tmp_path / "cache.json",
        fetcher=lambda *_: (200, {}, json.dumps(payload).encode()),
    )
    source = OfficialRegistrySearchSource(client)
    plan = MCPProposalPlanner(
        MCPProposalPlannerDeps(sources=(source,))
    ).plan_proposal("render video film", caller_kind="react")
    assert plan.catalog_proposal is not None
    assert plan.catalog_proposal.proposed_package_transport == "remote"
    assert plan.catalog_proposal.proposed_package_spec.startswith("remote:")
    assert (
        plan.catalog_proposal.proposed_connection_spec["transport"]
        == "streamable_http"
    )

