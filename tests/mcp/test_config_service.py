"""Tests Phase I-4 — `config_service.MCPConfigService`."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp.config_service import MCPConfigError, MCPConfigService


@pytest.fixture
def cfg(tmp_path: Path) -> MCPConfigService:
    return MCPConfigService(config_root=tmp_path / "mcp_config")


class TestValidations:
    @pytest.mark.parametrize("sid", ["", "Bad-Cap", "space here"])
    def test_invalid_server_id(self, cfg, sid):
        with pytest.raises(MCPConfigError):
            cfg.has(sid, "X_KEY")

    @pytest.mark.parametrize("k", ["", "lowercase", "X.DOT", "X-DASH"])
    def test_invalid_key(self, cfg, k):
        with pytest.raises(MCPConfigError):
            cfg.set("alice", k, "v")

    def test_non_string_value(self, cfg):
        with pytest.raises(MCPConfigError):
            cfg.set("alice", "FOO_BAR", 42)  # type: ignore[arg-type]


class TestCRUD:
    def test_set_get(self, cfg):
        cfg.set("alice", "ALLOWED_PATHS", '["/tmp"]')
        assert cfg.get("alice", "ALLOWED_PATHS") == '["/tmp"]'

    def test_missing_returns_none(self, cfg):
        assert cfg.get("alice", "OTHER_KEY") is None

    def test_delete(self, cfg):
        cfg.set("alice", "A_KEY", "v")
        assert cfg.delete("alice", "A_KEY") is True
        assert cfg.get("alice", "A_KEY") is None

    def test_delete_absent_false(self, cfg):
        assert cfg.delete("alice", "NEVER") is False

    def test_set_empty_deletes(self, cfg):
        cfg.set("alice", "A_KEY", "v")
        cfg.set("alice", "A_KEY", "")
        assert cfg.has("alice", "A_KEY") is False

    def test_isolation(self, cfg):
        cfg.set("alice", "SHARED_KEY", "A")
        cfg.set("bob", "SHARED_KEY", "B")
        assert cfg.get("alice", "SHARED_KEY") == "A"
        assert cfg.get("bob", "SHARED_KEY") == "B"

    def test_persistence_across_instances(self, tmp_path):
        root = tmp_path / "cfg"
        a = MCPConfigService(config_root=root)
        a.set("alice", "A_KEY", "persistent")
        b = MCPConfigService(config_root=root)
        assert b.get("alice", "A_KEY") == "persistent"


class TestListing:
    def test_list_keys(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        cfg.set("alice", "B_KEY", "2")
        assert cfg.list_keys("alice") == ["A_KEY", "B_KEY"]

    def test_list_items_returns_all(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        cfg.set("alice", "B_KEY", "2")
        assert cfg.list_items("alice") == {"A_KEY": "1", "B_KEY": "2"}

    def test_list_keys_empty(self, cfg):
        assert cfg.list_keys("never") == []


class TestHelpers:
    def test_has_all(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        assert cfg.has_all("alice", ["A_KEY"]) is True
        assert cfg.has_all("alice", ["A_KEY", "B_KEY"]) is False
        assert cfg.has_all("alice", []) is True

    def test_missing_keys(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        assert cfg.missing_keys("alice", ["A_KEY", "B_KEY"]) == ["B_KEY"]

    def test_status_map(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        m = cfg.status_map("alice", ["A_KEY", "B_KEY"])
        assert m == {"A_KEY": "set", "B_KEY": "missing"}


class TestExportForRuntime:
    def test_allowlist_filter(self, cfg):
        cfg.set("alice", "ALLOWED_PATH", "/x")
        cfg.set("alice", "OTHER_PATH", "/y")
        out = cfg.export_for_runtime("alice", ["ALLOWED_PATH"])
        assert out == {"ALLOWED_PATH": "/x"}

    def test_empty_allowlist(self, cfg):
        cfg.set("alice", "A_KEY", "v")
        assert cfg.export_for_runtime("alice", []) == {}


class TestClearAll:
    def test_clear_all(self, cfg):
        cfg.set("alice", "A_KEY", "1")
        cfg.set("alice", "B_KEY", "2")
        assert cfg.clear_all("alice") == 2
        assert cfg.list_keys("alice") == []


class TestRobustness:
    def test_malformed_json_treated_as_empty(self, cfg, tmp_path):
        # Force la corruption d'un fichier
        path = cfg._path("corrupted")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        # Le service doit retourner None / dict vide sans crash
        assert cfg.get("corrupted", "X_KEY") is None
        assert cfg.list_keys("corrupted") == []

    def test_non_dict_root_treated_as_empty(self, cfg):
        path = cfg._path("listed")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('["not a dict"]', encoding="utf-8")
        assert cfg.list_keys("listed") == []
