from src.mcp.network_sources import (
    MCPDirectorySearchSource,
    NpmRegistrySearchSource,
    PyPIProjectLookupSource,
)


def test_npm_registry_source_disabled_returns_empty():
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return {"objects": []}

    src = NpmRegistrySearchSource(network_enabled=False, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []
    assert calls == []


def test_npm_registry_source_maps_mcp_package_safely():
    def fetch(url, timeout):
        assert "airtable" in url
        assert "mcp" in url
        assert timeout == 4.0
        return {
            "objects": [
                {
                    "package": {
                        "name": "@scope/airtable-mcp",
                        "version": "1.2.3",
                        "description": "Model Context Protocol server for Airtable",
                        "keywords": ["mcp", "airtable", "stdio"],
                        "date": "2026-01-02T00:00:00.000Z",
                        "license": "MIT",
                        "links": {"repository": "https://example.invalid/repo"},
                    },
                    "score": {"detail": {"popularity": 0.8}},
                }
            ]
        }

    src = NpmRegistrySearchSource(network_enabled=True, fetch_json=fetch)
    out = src.search({"airtable"}, limit=10)

    assert len(out) == 1
    item = out[0]
    assert item["source"] == "npm_registry"
    assert item["package_name"] == "@scope/airtable-mcp"
    assert item["package_spec"] == "npm:@scope/airtable-mcp"
    assert item["version"] == "1.2.3"
    assert item["package_transport"] == "npm"
    assert item["mcp_transport_hint"] == "stdio"
    assert item["has_repo"] is True
    assert item["has_license"] is True
    assert item["license_id"] == "MIT"
    assert item["downloads_count"] > 0


def test_npm_registry_source_filters_non_mcp_packages():
    def fetch(url, timeout):
        return {
            "objects": [
                {
                    "package": {
                        "name": "airtable",
                        "version": "1.0.0",
                        "description": "Airtable SDK",
                        "keywords": ["airtable"],
                    }
                }
            ]
        }

    src = NpmRegistrySearchSource(network_enabled=True, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []


def test_npm_registry_source_network_failure_degrades_to_empty():
    def fetch(url, timeout):
        raise OSError("network down")

    src = NpmRegistrySearchSource(network_enabled=True, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []


def test_pypi_lookup_source_disabled_returns_empty():
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return {}

    src = PyPIProjectLookupSource(network_enabled=False, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []
    assert calls == []


def test_pypi_lookup_source_maps_mcp_project_safely():
    def fetch(url, timeout):
        assert "airtable" in url
        assert timeout == 4.0
        return {
            "info": {
                "name": "mcp-airtable",
                "version": "0.4.0",
                "summary": "Model Context Protocol server for Airtable",
                "description": "Longer MCP server description",
                "keywords": "mcp airtable stdio",
                "license": "MIT",
                "project_urls": {"Repository": "https://example.invalid/repo"},
            },
            "releases": {
                "0.4.0": [
                    {"upload_time_iso_8601": "2026-02-03T00:00:00.000Z"}
                ]
            },
        }

    src = PyPIProjectLookupSource(network_enabled=True, fetch_json=fetch)
    out = src.search({"airtable"}, limit=10)

    assert len(out) == 1
    item = out[0]
    assert item["source"] == "pypi_project_lookup"
    assert item["package_name"] == "mcp-airtable"
    assert item["package_spec"] == "pypi:mcp-airtable"
    assert item["version"] == "0.4.0"
    assert item["package_transport"] == "pypi"
    assert item["mcp_transport_hint"] == "stdio"
    assert item["has_repo"] is True
    assert item["has_license"] is True
    assert item["license_id"] == "MIT"


def test_pypi_lookup_source_filters_non_mcp_project():
    def fetch(url, timeout):
        return {
            "info": {
                "name": "airtable-sdk",
                "version": "1.0.0",
                "summary": "Airtable SDK",
                "description": "A client library",
                "keywords": "airtable sdk",
            },
            "releases": {},
        }

    src = PyPIProjectLookupSource(network_enabled=True, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []


def test_pypi_lookup_source_network_failure_degrades_to_empty():
    def fetch(url, timeout):
        raise OSError("network down")

    src = PyPIProjectLookupSource(network_enabled=True, fetch_json=fetch)

    assert src.search({"airtable"}, limit=10) == []


def test_directory_source_disabled_returns_empty():
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return "Model Context Protocol npx -y @scope/airtable-mcp"

    src = MCPDirectorySearchSource(
        name="smithery_directory",
        url_templates=("https://example.invalid/search?q={query}",),
        network_enabled=False,
        fetch_text=fetch,
    )

    assert src.search({"airtable"}, limit=10) == []
    assert calls == []


def test_directory_source_extracts_npm_install_snippet_safely():
    def fetch(url, timeout):
        assert "airtable" in url
        assert "mcp" in url
        assert timeout == 5.0
        return """
        Model Context Protocol server for Airtable.
        Install: npx -y @modelcontextprotocol/server-airtable
        Repository: https://github.com/example/server-airtable
        License MIT
        SECRET_DIRECTORY_MARKER_SHOULD_NOT_PROPAGATE
        """

    src = MCPDirectorySearchSource(
        name="smithery_directory",
        url_templates=("https://example.invalid/search?q={query}",),
        network_enabled=True,
        fetch_text=fetch,
    )
    out = src.search({"airtable"}, limit=10)

    assert len(out) == 1
    item = out[0]
    assert item["source"] == "smithery_directory"
    assert item["package_name"] == "@modelcontextprotocol/server-airtable"
    assert item["package_spec"] == "npm:@modelcontextprotocol/server-airtable"
    assert item["package_transport"] == "npm"
    assert item["mcp_transport_hint"] == "stdio"
    assert item["has_repo"] is True
    assert item["has_license"] is True
    assert item["downloads_count"] == 50_000
    assert "SECRET_DIRECTORY_MARKER" not in str(item)


def test_directory_source_extracts_pypi_install_snippet_safely():
    def fetch(url, timeout):
        return """
        MCP / Model Context Protocol package for weather forecasts.
        pip install mcp-weather
        """

    src = MCPDirectorySearchSource(
        name="pulsemcp_directory",
        url_templates=("https://example.invalid/servers?q={query}",),
        network_enabled=True,
        fetch_text=fetch,
    )
    out = src.search({"weather"}, limit=10)

    assert len(out) == 1
    assert out[0]["package_name"] == "mcp-weather"
    assert out[0]["package_spec"] == "pypi:mcp-weather"
    assert out[0]["package_transport"] == "pypi"


def test_directory_source_rejects_pages_without_mcp_signal():
    def fetch(url, timeout):
        return "Install: npx -y @scope/airtable-tool"

    src = MCPDirectorySearchSource(
        name="github_web_search",
        url_templates=("https://example.invalid/search?q={query}",),
        network_enabled=True,
        fetch_text=fetch,
    )

    assert src.search({"airtable"}, limit=10) == []


def test_directory_source_rejects_versioned_or_unmatched_packages():
    def fetch(url, timeout):
        return """
        Model Context Protocol examples.
        npx -y airtable-mcp@1.2.3
        npx -y @scope/calendar-mcp
        """

    src = MCPDirectorySearchSource(
        name="github_web_search",
        url_templates=("https://example.invalid/search?q={query}",),
        network_enabled=True,
        fetch_text=fetch,
    )

    assert src.search({"airtable"}, limit=10) == []
