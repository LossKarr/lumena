"""Phase 11A — Team Mode conversationnel.

Objectif : quand l'utilisateur parle naturellement d'une autre Lumena, le chat
doit voir les outils peer et ne plus contourner le protocole avec HTTP/browser/curl.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.tool_registry import ToolRegistry


def _make_registry() -> ToolRegistry:
    reg = object.__new__(ToolRegistry)
    reg.tools = {}
    reg._tool_modules = {}
    reg._allowed_tools = None
    reg._caller_set_allowed = False
    reg._tools_desc_cache = None
    reg.ide_context = {}

    categories = {
        "system": ["final_answer", "ask_user", "run_command"],
        "memory": ["memory_search"],
        "web": ["web_search", "http_request", "web_fetch"],
        "browser": ["browser_navigate"],
        "network": ["ping_host"],
        "agents": [
            "delegate_agent",
        ],
        "peers": [
            "peer_team_request",
            "delegate_to_peer",
            "orchestrate_peer_request",
            "run_peer_task_sync",
            "query_peer_knowledge",
        ],
    }
    for category, names in categories.items():
        for name in names:
            reg.tools[name] = {
                "name": name,
                "description": f"test {name}",
                "parameters": {},
                "handler": lambda **_: None,
            }
            reg._tool_modules[name] = category
    return reg


def _write_peer_registry(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "peer_registry.json").write_text(
        json.dumps({
            "peer-salon": {
                "instance_id": "peer-salon",
                "instance_name": "Lumena Salon",
                "host": "192.168.1.100",
                "port": 8081,
                "trust": "trusted",
                "peer_token_outbound": "raw-token-not-rendered",
                "peer_token_hash": "hash",
                "allowed_scopes": ["chat", "task.delegate"],
            }
        }),
        encoding="utf-8",
    )
    return data_dir


class TestPeerTeamContextFilter:
    def test_demande_lui_chat_intent_keeps_peer_tools_visible(self):
        reg = _make_registry()
        reg.apply_context_filter(
            "demande lui de lancer une recherche web sur wikipedia",
            intent="chat",
        )

        assert reg._allowed_tools is not None
        assert "peer_team_request" in reg._allowed_tools
        assert "orchestrate_peer_request" in reg._allowed_tools
        assert "delegate_to_peer" in reg._allowed_tools
        assert "run_peer_task_sync" in reg._allowed_tools
        assert "delegate_agent" not in reg._allowed_tools
        assert "http_request" in reg._allowed_tools  # visible, mais guardé si cible Lumena connue

    def test_plain_chat_still_stays_light(self):
        reg = _make_registry()
        reg.apply_context_filter("salut ça va", intent="chat")

        assert reg._allowed_tools is not None
        assert "peer_team_request" not in reg._allowed_tools
        assert "orchestrate_peer_request" not in reg._allowed_tools
        assert "delegate_to_peer" not in reg._allowed_tools


class TestPeerRawNetworkGuard:
    def test_http_request_to_known_peer_is_refused(self, tmp_path, monkeypatch):
        from src.utils import paths as paths_mod

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        monkeypatch.setattr(paths_mod, "DATA_DIR", _write_peer_registry(tmp_path))
        reg = _make_registry()

        obs = reg._peer_raw_network_refusal(
            "http_request",
            {"url": "http://192.168.1.100:8081/api/chat"},
        )

        assert obs is not None
        assert obs.success is False
        assert "orchestrate_peer_request" in obs.content
        assert "peer-salon" in obs.content
        assert "raw-token-not-rendered" not in obs.content

    def test_browser_to_known_peer_is_refused(self, tmp_path, monkeypatch):
        from src.utils import paths as paths_mod

        monkeypatch.setenv("LUMENA_PEER_AWARENESS", "1")
        monkeypatch.setattr(paths_mod, "DATA_DIR", _write_peer_registry(tmp_path))
        reg = _make_registry()

        obs = reg._peer_raw_network_refusal(
            "browser_navigate",
            {"url": "http://192.168.1.100:8081/"},
        )

        assert obs is not None
        assert obs.success is False
        assert "protocole inter-instance" in obs.content

    def test_test_netconnection_is_not_blocked(self, tmp_path, monkeypatch):
        from src.utils import paths as paths_mod

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        monkeypatch.setattr(paths_mod, "DATA_DIR", _write_peer_registry(tmp_path))
        reg = _make_registry()

        obs = reg._peer_raw_network_refusal(
            "run_command",
            {"command": "Test-NetConnection -ComputerName 192.168.1.100 -Port 8081"},
        )

        assert obs is None

    def test_curl_to_known_peer_is_refused(self, tmp_path, monkeypatch):
        from src.utils import paths as paths_mod

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        monkeypatch.setattr(paths_mod, "DATA_DIR", _write_peer_registry(tmp_path))
        reg = _make_registry()

        obs = reg._peer_raw_network_refusal(
            "run_command",
            {"command": 'curl http://192.168.1.100:8081/api/chat'},
        )

        assert obs is not None
        assert "run_peer_task_sync" in obs.content


class TestPeerAgentContract:
    def test_peer_agent_tools_do_not_require_workspace(self):
        reg = _make_registry()

        obs = reg._category_contract_check(
            "peer_team_request",
            {"prompt": "demande à l'autre Lumena de vérifier"},
            REACT,
        )

        assert obs is None

    def test_regular_delegate_agent_still_requires_workspace(self):
        reg = _make_registry()

        obs = reg._category_contract_check(
            "delegate_agent",
            {"description": "travaille sur un projet sans workspace explicite"},
            REACT,
        )

        assert obs is not None
        assert "workspace_path requis" in obs.content


class TestPeerAwarenessInstructions:
    def test_context_contains_natural_delegation_rules(self, tmp_path, monkeypatch):
        import src.runtime.peer_awareness as awareness

        monkeypatch.setenv("LUMENA_PEER_AWARENESS", "1")
        monkeypatch.setattr(awareness, "_PEER_REGISTRY_FILE", _write_peer_registry(tmp_path) / "peer_registry.json")

        ctx = awareness.build_peer_awareness_context()

        assert "Réseau Lumena" in ctx
        assert "orchestrate_peer_request" in ctx
        assert "http_request" in ctx
        assert "chef d'équipe" in ctx
        assert "raw-token-not-rendered" not in ctx
