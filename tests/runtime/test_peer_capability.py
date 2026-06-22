"""Brique 2 — tests du module de niveaux de capacité par pair (pur)."""
from __future__ import annotations

from src.runtime.peer_capability import (
    CHAT_ALLOWED_TOOLS,
    DEFAULT_LEVEL,
    describe_level,
    is_mission_level,
    normalize_level,
    resolve_allowed_tools,
)


# ── Normalisation (fail-closed) ──────────────────────────────────────────────

def test_default_is_chat():
    assert DEFAULT_LEVEL == "chat"
    assert normalize_level(None) == "chat"
    assert normalize_level("") == "chat"


def test_unknown_level_falls_back_to_chat():
    assert normalize_level("admin") == "chat"
    assert normalize_level("root") == "chat"
    assert normalize_level("MISSION ") == "mission"  # trim + lower


def test_is_mission_level():
    assert is_mission_level("mission") is True
    assert is_mission_level("chat") is False
    assert is_mission_level("n'importe quoi") is False


# ── Niveau chat : liste blanche, zéro action ─────────────────────────────────

def test_chat_returns_whitelist():
    tools = resolve_allowed_tools("chat")
    assert tools == set(CHAT_ALLOWED_TOOLS)


def test_chat_has_no_action_tools():
    tools = resolve_allowed_tools("chat")
    for dangerous in ("delegate_task", "write_file", "run_command", "computer_use",
                      "delete_file", "read_file", "list_directory"):
        assert dangerous not in tools


def test_chat_intersects_with_real_tools():
    real = {"memory_search", "web_search", "outil_inexistant"}
    tools = resolve_allowed_tools("chat", real)
    assert "memory_search" in tools and "web_search" in tools
    assert "outil_inexistant" not in tools  # pas dans la liste blanche


# ── Niveau mission : TOUS les outils (None = aucune restriction) ─────────────

def test_mission_is_full_no_restriction():
    # mission → None = l'agent a accès à TOUS ses outils (CodeAgent inclus).
    assert resolve_allowed_tools("mission") is None
    assert resolve_allowed_tools("mission", {"write_file", "delegate_task"}) is None


def test_mission_allows_delegate_task():
    # En mission, B peut coder → delegate_task (CodeAgent) est disponible.
    # None signifie « aucune restriction » côté think_and_act_silent.
    assert resolve_allowed_tools("mission") is None
    assert is_mission_level("mission") is True


# ── Fail-closed : un niveau inconnu n'ouvre jamais plus que chat ─────────────

def test_unknown_level_is_chat_restricted():
    all_tools = {"write_file", "memory_search", "delegate_task"}
    tools = resolve_allowed_tools("superadmin", all_tools)
    assert tools == set(CHAT_ALLOWED_TOOLS) & all_tools
    assert "write_file" not in tools
    assert "delegate_task" not in tools


# ── Libellé ──────────────────────────────────────────────────────────────────

def test_describe_level():
    assert "Mission" in describe_level("mission")
    assert "Chat" in describe_level("chat")
    assert "Chat" in describe_level("inconnu")
