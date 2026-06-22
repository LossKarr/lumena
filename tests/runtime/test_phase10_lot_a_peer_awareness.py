"""Lot A Phase 10 — Tests Peer Awareness (révisé).

Couvre :
- get_peer_awareness_snapshot : flag, filtre trust/token, booleans has_inbound_token_hash/can_call_peer, aucun secret
- build_peer_awareness_context : vide si pas de peer, logique scopes/token par pair, taille max
- Injection dans _build_react_prompt : bloc présent/absent selon flag et état des peers
- .env.example contient LUMENA_PEER_AWARENESS

Cas de régression demandés :
- trusted + hash seulement (no outbound) -> snapshot présent, non délégable dans contexte
- trusted + outbound + allowed_scopes=["chat"] -> délégation chat visible
- trusted + outbound + allowed_scopes=[] -> connecté mais aucun scope utilisable
- trusted + outbound + allowed_scopes=["knowledge.query"] -> knowledge query visible, PAS délégation chat
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_registry(tmp_path: Path, peers: dict) -> Path:
    f = tmp_path / "peer_registry.json"
    f.write_text(json.dumps(peers, ensure_ascii=False), encoding="utf-8")
    return f


# Peer complet : hash inbound + outbound token + scope chat
TRUSTED_FULL = {
    "instance_id": "peer-aaa",
    "instance_name": "Lumena Salon",
    "host": "192.168.1.100",
    "port": 8081,
    "capabilities": ["chat", "browser"],
    "trust": "trusted",
    "peer_token_hash": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    "peer_token_outbound": "SECRET_OUTBOUND_DO_NOT_EXPOSE",
    "allowed_scopes": ["chat"],
    "last_seen": datetime.now(timezone.utc).isoformat(),
}

# Peer avec hash inbound seulement (pas de token sortant)
TRUSTED_HASH_ONLY = {
    **TRUSTED_FULL,
    "instance_id": "peer-hash",
    "instance_name": "Lumena Hash Only",
    "peer_token_outbound": "",   # on peut recevoir de lui mais pas l'appeler
}

# Peer avec outbound seulement (pas de hash inbound — cas rare mais possible)
TRUSTED_OUTBOUND_ONLY = {
    **TRUSTED_FULL,
    "instance_id": "peer-out",
    "instance_name": "Lumena Outbound Only",
    "peer_token_hash": "",
}

# Peer trusted sans aucun token
TRUSTED_NO_TOKEN = {
    **TRUSTED_FULL,
    "instance_id": "peer-notok",
    "peer_token_hash": "",
    "peer_token_outbound": "",
}

# Pairs non trusted
UNKNOWN_PEER = {**TRUSTED_FULL, "instance_id": "peer-unk", "trust": "unknown"}
BLOCKED_PEER = {**TRUSTED_FULL, "instance_id": "peer-blk", "trust": "blocked"}


def _patch(monkeypatch, tmp_path, flag, peers):
    reg = _make_registry(tmp_path, peers)
    import src.runtime.peer_awareness as pa
    monkeypatch.setattr(pa, "_PEER_REGISTRY_FILE", reg)
    monkeypatch.setenv("LUMENA_PEER_AWARENESS", flag)
    return pa


# ─────────────────────────────────────────────────────────────────────────────
# get_peer_awareness_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPeerAwarenessSnapshot:

    def test_flag_off_returns_disabled(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "0", {"p": TRUSTED_FULL})
        snap = pa.get_peer_awareness_snapshot()
        assert snap["enabled"] is False
        assert snap["peers"] == []

    def test_flag_on_empty_registry(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {})
        snap = pa.get_peer_awareness_snapshot()
        assert snap["enabled"] is True
        assert snap["peers"] == []

    def test_unknown_excluded(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": UNKNOWN_PEER})
        assert pa.get_peer_awareness_snapshot()["peers"] == []

    def test_blocked_excluded(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": BLOCKED_PEER})
        assert pa.get_peer_awareness_snapshot()["peers"] == []

    def test_trusted_no_token_excluded(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_NO_TOKEN})
        assert pa.get_peer_awareness_snapshot()["peers"] == []

    def test_trusted_full_included(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        peers = pa.get_peer_awareness_snapshot()["peers"]
        assert len(peers) == 1
        p = peers[0]
        assert p["instance_id"] == "peer-aaa"
        assert p["has_inbound_token_hash"] is True
        assert p["can_call_peer"] is True

    def test_trusted_hash_only_included_not_callable(self, tmp_path, monkeypatch):
        """Hash inbound seulement : inclus dans snapshot mais can_call_peer=False."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_HASH_ONLY})
        peers = pa.get_peer_awareness_snapshot()["peers"]
        assert len(peers) == 1
        p = peers[0]
        assert p["has_inbound_token_hash"] is True
        assert p["can_call_peer"] is False

    def test_trusted_outbound_only_included_callable(self, tmp_path, monkeypatch):
        """Outbound seulement : inclus dans snapshot avec can_call_peer=True."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_OUTBOUND_ONLY})
        peers = pa.get_peer_awareness_snapshot()["peers"]
        assert len(peers) == 1
        assert peers[0]["can_call_peer"] is True
        assert peers[0]["has_inbound_token_hash"] is False

    def test_no_raw_token_in_snapshot(self, tmp_path, monkeypatch):
        """Aucun token brut ne doit apparaître dans le snapshot."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        p = pa.get_peer_awareness_snapshot()["peers"][0]
        assert "peer_token_outbound" not in p
        assert "peer_token_hash" not in p
        assert "SECRET_OUTBOUND_DO_NOT_EXPOSE" not in str(p)
        assert "abcdef1234567890" not in str(p)

    def test_snapshot_exposes_correct_booleans(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        p = pa.get_peer_awareness_snapshot()["peers"][0]
        assert "has_inbound_token_hash" in p
        assert "can_call_peer" in p
        # L'ancien champ ambigu ne doit plus exister
        assert "has_peer_token" not in p

    def test_snapshot_contains_allowed_scopes(self, tmp_path, monkeypatch):
        peer = {**TRUSTED_FULL, "allowed_scopes": ["chat", "task.delegate"]}
        pa = _patch(monkeypatch, tmp_path, "1", {"p": peer})
        p = pa.get_peer_awareness_snapshot()["peers"][0]
        assert sorted(p["allowed_scopes"]) == ["chat", "task.delegate"]

    def test_mixed_peers_correct_filter(self, tmp_path, monkeypatch):
        peers = {
            "a": TRUSTED_FULL,           # inclus (full)
            "b": TRUSTED_HASH_ONLY,      # inclus (hash only)
            "c": TRUSTED_NO_TOKEN,       # exclu (no token)
            "d": UNKNOWN_PEER,           # exclu (unknown)
            "e": BLOCKED_PEER,           # exclu (blocked)
        }
        pa = _patch(monkeypatch, tmp_path, "1", peers)
        snap = pa.get_peer_awareness_snapshot()
        ids = {p["instance_id"] for p in snap["peers"]}
        assert ids == {"peer-aaa", "peer-hash"}


# ─────────────────────────────────────────────────────────────────────────────
# build_peer_awareness_context
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPeerAwarenessContext:

    def test_flag_off_empty(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "0", {"p": TRUSTED_FULL})
        assert pa.build_peer_awareness_context() == ""

    def test_no_peer_empty(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": UNKNOWN_PEER})
        assert pa.build_peer_awareness_context() == ""

    # ── Cas token hash only (pas de token sortant) ────────────────────────────

    def test_hash_only_not_presented_as_delegable(self, tmp_path, monkeypatch):
        """trusted + hash seulement -> pas présenté comme délégable."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_HASH_ONLY})
        ctx = pa.build_peer_awareness_context()
        assert ctx != ""
        assert "Lumena Hash Only" in ctx
        # Doit mentionner le problème de token sortant
        assert "token sortant manquant" in ctx or "rejumelage" in ctx
        # Ne doit PAS dire que la délégation est disponible
        assert "Délégation inter-instance disponible" not in ctx

    # ── Cas outbound token + scopes ───────────────────────────────────────────

    def test_outbound_chat_scope_delegation_visible(self, tmp_path, monkeypatch):
        """trusted + outbound + allowed_scopes=["chat"] -> délégation chat disponible."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        ctx = pa.build_peer_awareness_context()
        assert "chat" in ctx
        assert "Délégation inter-instance disponible" in ctx

    def test_mission_uses_submit_peer_task(self, tmp_path, monkeypatch):
        """Bug 1 : la consigne doit orienter une mission produisant des fichiers
        vers submit_peer_task (async + artefacts), pas peer_team_request."""
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        ctx = pa.build_peer_awareness_context()
        assert "submit_peer_task" in ctx
        assert "recu-de-" in ctx
        # ET on conserve peer_team_request pour les Q/R rapides
        assert "peer_team_request" in ctx

    def test_outbound_no_scope_not_delegable(self, tmp_path, monkeypatch):
        """trusted + outbound + allowed_scopes=[] -> connecté mais aucun scope utilisable."""
        peer = {**TRUSTED_FULL, "allowed_scopes": []}
        pa = _patch(monkeypatch, tmp_path, "1", {"p": peer})
        ctx = pa.build_peer_awareness_context()
        assert ctx != ""
        assert "aucun scope utilisable" in ctx
        # Le bloc footer de délégation ne doit pas apparaître
        assert "Délégation inter-instance disponible" not in ctx

    def test_outbound_knowledge_scope_not_presenting_chat_delegation(self, tmp_path, monkeypatch):
        """trusted + outbound + allowed_scopes=["knowledge.query"] -> knowledge visible, pas chat."""
        peer = {**TRUSTED_FULL, "allowed_scopes": ["knowledge.query"]}
        pa = _patch(monkeypatch, tmp_path, "1", {"p": peer})
        ctx = pa.build_peer_awareness_context()
        assert "knowledge.query" in ctx
        # Le bloc dit bien "scopes disponibles : knowledge.query"
        assert "Délégation inter-instance disponible" in ctx
        # "chat" ne doit PAS apparaître dans la liste des scopes disponibles
        # (il peut apparaître dans les capacités, mais pas après "scopes disponibles :")
        for line in ctx.splitlines():
            if "scopes disponibles" in line:
                scopes_part = line.split("scopes disponibles :")[-1]
                assert "knowledge.query" in scopes_part
                assert "chat" not in scopes_part

    def test_no_raw_token_in_context(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        ctx = pa.build_peer_awareness_context()
        assert "SECRET_OUTBOUND_DO_NOT_EXPOSE" not in ctx
        assert "peer_token_outbound" not in ctx
        assert "peer_token_hash" not in ctx
        assert "abcdef1234567890" not in ctx

    def test_context_under_1000_chars(self, tmp_path, monkeypatch):
        peers = {}
        for i in range(10):
            p = {**TRUSTED_FULL, "instance_id": f"peer-{i:03}", "instance_name": f"Lumena {i}"}
            peers[p["instance_id"]] = p
        pa = _patch(monkeypatch, tmp_path, "1", peers)
        assert len(pa.build_peer_awareness_context()) <= 1000

    def test_context_header_present(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        ctx = pa.build_peer_awareness_context()
        assert "Réseau Lumena" in ctx

    def test_context_mentions_host_port(self, tmp_path, monkeypatch):
        pa = _patch(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        ctx = pa.build_peer_awareness_context()
        assert "192.168.1.100" in ctx
        assert "8081" in ctx

    def test_multiple_peers_different_states(self, tmp_path, monkeypatch):
        """Un peer callable + un peer hash-only -> deux lignes distinctes."""
        peers = {"a": TRUSTED_FULL, "b": TRUSTED_HASH_ONLY}
        pa = _patch(monkeypatch, tmp_path, "1", peers)
        ctx = pa.build_peer_awareness_context()
        assert "Lumena Salon" in ctx
        assert "Lumena Hash Only" in ctx
        assert "token sortant manquant" in ctx
        # Délégation disponible parce qu'au moins un peer est callable
        assert "Délégation inter-instance disponible" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# _fmt_last_seen
# ─────────────────────────────────────────────────────────────────────────────

class TestFmtLastSeen:

    def test_empty_returns_empty(self):
        from src.runtime.peer_awareness import _fmt_last_seen
        assert _fmt_last_seen("") == ""

    def test_invalid_returns_empty(self):
        from src.runtime.peer_awareness import _fmt_last_seen
        assert _fmt_last_seen("not-a-date") == ""

    def test_recent_returns_minutes_or_instant(self):
        from src.runtime.peer_awareness import _fmt_last_seen
        result = _fmt_last_seen(datetime.now(timezone.utc).isoformat())
        assert "min" in result or "instant" in result

    def test_old_returns_hours(self):
        from datetime import timedelta
        from src.runtime.peer_awareness import _fmt_last_seen
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        assert "h" in _fmt_last_seen(old)


# ─────────────────────────────────────────────────────────────────────────────
# Injection dans _build_react_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestReactPromptInjection:

    def _make_loop(self, monkeypatch, tmp_path, flag, peers):
        reg = _make_registry(tmp_path, peers)
        import src.runtime.peer_awareness as pa
        monkeypatch.setattr(pa, "_PEER_REGISTRY_FILE", reg)
        monkeypatch.setenv("LUMENA_PEER_AWARENESS", flag)

        from src.reasoning.react import ReActLoop
        tools_mock = MagicMock()
        tools_mock.get_tools_description.return_value = "aucun outil"
        tools_mock.ide_context = {}
        tools_mock._get_mail_hub.side_effect = Exception("no hub")
        tools_mock.lumena = None

        loop = ReActLoop.__new__(ReActLoop)
        loop.tools = tools_mock
        loop.llm_chat = MagicMock()
        loop.is_weak_model = False
        loop.conversation_context = ""
        loop.active_skills_context = ""
        loop.runtime_ctx = None
        loop._identity_ctx_cache = ""
        loop._task_plan = ""
        loop.history = []
        loop._established_facts = {}
        loop._recent_tool_failures = []
        loop.llm_meta_getter = lambda: {}
        loop._last_llm_meta = {}
        return loop

    def test_flag_on_peer_block_in_prompt(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        prompt = loop._build_react_prompt("bonjour")
        assert "Réseau Lumena" in prompt
        assert "Lumena Salon" in prompt

    def test_flag_off_no_block(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "0", {"p": TRUSTED_FULL})
        assert "Réseau Lumena" not in loop._build_react_prompt("bonjour")

    def test_no_peer_no_block(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "1", {})
        assert "Réseau Lumena" not in loop._build_react_prompt("bonjour")

    def test_unknown_peer_no_block(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "1", {"p": UNKNOWN_PEER})
        assert "Réseau Lumena" not in loop._build_react_prompt("bonjour")

    def test_no_raw_token_in_prompt(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        prompt = loop._build_react_prompt("bonjour")
        assert "SECRET_OUTBOUND_DO_NOT_EXPOSE" not in prompt
        assert "peer_token_outbound" not in prompt
        assert "abcdef1234567890" not in prompt

    def test_hash_only_peer_no_delegation_in_prompt(self, tmp_path, monkeypatch):
        """Hash-only peer -> bloc présent mais ne dit pas que la délégation est dispo."""
        loop = self._make_loop(monkeypatch, tmp_path, "1", {"p": TRUSTED_HASH_ONLY})
        prompt = loop._build_react_prompt("bonjour")
        assert "Réseau Lumena" in prompt
        assert "Délégation inter-instance disponible" not in prompt

    def test_network_question_context_has_peer(self, tmp_path, monkeypatch):
        loop = self._make_loop(monkeypatch, tmp_path, "1", {"p": TRUSTED_FULL})
        prompt = loop._build_react_prompt("tu as d'autres Lumena disponibles ?")
        assert "Réseau Lumena" in prompt
        assert "192.168.1.100" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# .env.example
# ─────────────────────────────────────────────────────────────────────────────

class TestEnvExample:

    def test_env_example_contains_peer_awareness(self):
        env_path = Path(__file__).resolve().parents[2] / ".env.example"
        assert env_path.exists(), ".env.example introuvable"
        content = env_path.read_text(encoding="utf-8")
        assert "LUMENA_PEER_AWARENESS" in content

    def test_peer_awareness_default_is_zero(self):
        env_path = Path(__file__).resolve().parents[2] / ".env.example"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("LUMENA_PEER_AWARENESS="):
                assert line.strip() == "LUMENA_PEER_AWARENESS=0"
                return
        pytest.fail("LUMENA_PEER_AWARENESS non trouvé dans .env.example")
