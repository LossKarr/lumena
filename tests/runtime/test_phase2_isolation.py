"""Tests Phase 2 — Isolation workspace et ChromaDB par user_id."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.runtime.user_profile import (
    _safe_user_id,
    get_user_data_dir,
    get_user_memory_dir,
    get_user_workspace_dir,
    get_user_conversations_dir,
    is_local_owner,
    MULTI_USER_ENABLED,
)
from src.utils.paths import MULTI_USER_ENABLED as PATHS_MULTI_USER_ENABLED


# ── safe_user_id ──────────────────────────────────────────────────────────────

def test_safe_user_id_local_owner():
    assert _safe_user_id("local:owner") == "local__owner"


def test_safe_user_id_telegram():
    assert _safe_user_id("telegram:42") == "telegram__42"


def test_safe_user_id_discord_nested():
    assert _safe_user_id("discord:guild1:user99") == "discord__guild1__user99"


def test_safe_user_id_none_falls_back_to_local_owner():
    assert _safe_user_id(None) == "local__owner"


def test_safe_user_id_empty_falls_back_to_local_owner():
    assert _safe_user_id("") == "local__owner"


# ── is_local_owner ────────────────────────────────────────────────────────────

def test_is_local_owner_true():
    assert is_local_owner("local:owner") is True


def test_is_local_owner_false_for_telegram():
    assert is_local_owner("telegram:42") is False


def test_is_local_owner_false_for_none():
    assert is_local_owner(None) is False


# ── paths.py re-export ────────────────────────────────────────────────────────

def test_paths_multi_user_flag_matches_user_profile():
    assert PATHS_MULTI_USER_ENABLED == MULTI_USER_ENABLED


# ── Isolation workspace (MULTI_USER_ENABLED=1) ────────────────────────────────

def test_workspace_isolation_between_users(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        ws_owner = get_user_workspace_dir("local:owner", data_dir=tmp_path, create=True)
        ws_guest = get_user_workspace_dir("telegram:42", data_dir=tmp_path, create=True)
        assert ws_owner != ws_guest


def test_workspace_owner_path_contains_safe_id(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        ws = get_user_workspace_dir("local:owner", data_dir=tmp_path, create=True)
        assert "local__owner" in str(ws)


def test_workspace_telegram_path_contains_safe_id(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        ws = get_user_workspace_dir("telegram:99", data_dir=tmp_path, create=True)
        assert "telegram__99" in str(ws)


def test_workspace_three_users_all_distinct(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        paths = {
            get_user_workspace_dir(uid, data_dir=tmp_path, create=True)
            for uid in ("local:owner", "telegram:1", "discord:g:u2")
        }
        assert len(paths) == 3


def test_workspace_legacy_mode_returns_shared_dir(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", False):
        from src.utils.paths import WORKSPACE_DIR
        ws_owner = get_user_workspace_dir("local:owner", data_dir=tmp_path, create=False)
        ws_guest = get_user_workspace_dir("telegram:42", data_dir=tmp_path, create=False)
        # En mode single-user, les deux renvoient le même chemin legacy
        assert ws_owner == ws_guest


# ── Isolation mémoire ─────────────────────────────────────────────────────────

def test_memory_isolation_between_users(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        mem_owner = get_user_memory_dir("local:owner", data_dir=tmp_path, create=True)
        mem_guest = get_user_memory_dir("telegram:42", data_dir=tmp_path, create=True)
        assert mem_owner != mem_guest


def test_memory_legacy_mode_returns_shared_dir(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", False):
        from src.utils.paths import MEMORY_DIR
        mem = get_user_memory_dir("telegram:42", data_dir=tmp_path, create=False)
        assert mem == MEMORY_DIR


# ── Isolation conversations ───────────────────────────────────────────────────

def test_conversations_isolation_between_users(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        c_owner = get_user_conversations_dir("local:owner", data_dir=tmp_path, create=True)
        c_guest = get_user_conversations_dir("telegram:42", data_dir=tmp_path, create=True)
        assert c_owner != c_guest


# ── resolve_workspace_for_user ────────────────────────────────────────────────

def test_resolve_workspace_for_user_multi_mode(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        from src.runtime.workspace_policy import resolve_workspace_for_user
        res_owner = resolve_workspace_for_user("local:owner", data_dir=tmp_path)
        res_guest = resolve_workspace_for_user("telegram:42", data_dir=tmp_path)
        assert res_owner.resolved_workspace != res_guest.resolved_workspace


def test_resolve_workspace_for_user_contains_safe_id(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True):
        from src.runtime.workspace_policy import resolve_workspace_for_user
        res = resolve_workspace_for_user("telegram:99", data_dir=tmp_path)
        assert "telegram__99" in res.resolved_workspace


def test_resolve_workspace_for_user_single_mode_returns_shared(tmp_path):
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", False):
        from src.runtime.workspace_policy import resolve_workspace_for_user
        res_a = resolve_workspace_for_user("local:owner", data_dir=tmp_path)
        res_b = resolve_workspace_for_user("telegram:42", data_dir=tmp_path)
        # En mode single-user, même base workspace
        assert Path(res_a.resolved_workspace).parent == Path(res_b.resolved_workspace).parent


# ── ChromaDB user_id isolation ────────────────────────────────────────────────

def test_chromadb_store_carries_user_id(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma", user_id="telegram:42")
    assert store.user_id == "telegram:42"


def test_chromadb_store_default_user_id(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma")
    assert store.user_id == "local:owner"


def test_chromadb_build_where_user_id_only(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma", user_id="telegram:42")
    where = store._build_where()
    assert where == {"user_id": "telegram:42"}


def test_chromadb_build_where_with_memory_type(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma", user_id="local:owner")
    where = store._build_where(memory_type="episodic")
    assert "$and" in where
    assert {"user_id": "local:owner"} in where["$and"]
    assert {"type": "episodic"} in where["$and"]


def test_chromadb_build_where_with_min_importance(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma", user_id="local:owner")
    where = store._build_where(min_importance=0.5)
    assert "$and" in where
    assert {"importance": {"$gte": 0.5}} in where["$and"]


def test_chromadb_add_injects_user_id(tmp_path):
    pytest.importorskip("chromadb")
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma", user_id="telegram:42")
    if not store.collection:
        pytest.skip("ChromaDB non disponible")
    mem_id = store.add("test content for telegram user")
    assert mem_id is not None
    result = store.collection.get(ids=[mem_id], include=["metadatas"])
    meta = result["metadatas"][0]
    assert meta["user_id"] == "telegram:42"


def test_chromadb_two_users_no_cross_contamination(tmp_path):
    pytest.importorskip("chromadb")
    from src.memory.chromadb_store import ChromaMemoryStore

    store_alice = ChromaMemoryStore(tmp_path / "chroma", user_id="local:owner")
    store_bob = ChromaMemoryStore(tmp_path / "chroma", user_id="telegram:42")

    if not store_alice.collection:
        pytest.skip("ChromaDB non disponible")

    store_alice.add("alice secret memory", memory_type="episodic")
    store_bob.add("bob secret memory", memory_type="episodic")

    results_alice = store_alice.search("secret memory", hybrid=False)
    results_bob = store_bob.search("secret memory", hybrid=False)

    alice_contents = {m.content for m in results_alice}
    bob_contents = {m.content for m in results_bob}

    assert "alice secret memory" in alice_contents
    assert "bob secret memory" not in alice_contents

    assert "bob secret memory" in bob_contents
    assert "alice secret memory" not in bob_contents


def test_lumena_memory_carries_user_id(tmp_path):
    pytest.importorskip("chromadb")
    from src.memory.chromadb_store import LumenaMemory
    mem = LumenaMemory(data_dir=tmp_path / "mem", user_id="telegram:99")
    assert mem.user_id == "telegram:99"
    assert mem.vector_store.user_id == "telegram:99"


def test_chromadb_store_alias_carries_user_id(tmp_path):
    pytest.importorskip("chromadb")
    from src.memory.chromadb_store import ChromaDBStore
    store = ChromaDBStore(data_dir=tmp_path / "chroma", user_id="discord:g1:u2")
    assert store.user_id == "discord:g1:u2"


# ── _matches_where helper ─────────────────────────────────────────────────────

def test_matches_where_simple_equality(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma")
    assert store._matches_where({"user_id": "local:owner"}, {"user_id": "local:owner"})
    assert not store._matches_where({"user_id": "telegram:42"}, {"user_id": "local:owner"})


def test_matches_where_and_structure(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma")
    where = {"$and": [{"user_id": "local:owner"}, {"type": "episodic"}]}
    assert store._matches_where({"user_id": "local:owner", "type": "episodic"}, where)
    assert not store._matches_where({"user_id": "telegram:42", "type": "episodic"}, where)
    assert not store._matches_where({"user_id": "local:owner", "type": "semantic"}, where)


def test_matches_where_gte_operator(tmp_path):
    from src.memory.chromadb_store import ChromaMemoryStore
    store = ChromaMemoryStore(tmp_path / "chroma")
    where = {"$and": [{"user_id": "local:owner"}, {"importance": {"$gte": 0.5}}]}
    assert store._matches_where({"user_id": "local:owner", "importance": 0.8}, where)
    assert not store._matches_where({"user_id": "local:owner", "importance": 0.2}, where)
    assert not store._matches_where({"user_id": "telegram:42", "importance": 0.8}, where)


# ── BM25 anti-fuite inter-utilisateurs ───────────────────────────────────────

def test_bm25_search_no_cross_contamination(tmp_path):
    """La recherche hybride BM25 ne doit pas retourner les souvenirs d'un autre user."""
    pytest.importorskip("chromadb")
    from src.memory.chromadb_store import ChromaMemoryStore

    store_alice = ChromaMemoryStore(tmp_path / "chroma", user_id="local:owner")
    store_bob = ChromaMemoryStore(tmp_path / "chroma", user_id="telegram:42")

    if not store_alice.collection:
        pytest.skip("ChromaDB non disponible")

    store_alice.add("alice private note about vacation", memory_type="episodic")
    store_bob.add("bob private note about vacation", memory_type="episodic")

    # Recherche hybride (BM25 + vectorielle)
    results_alice = store_alice.search("vacation", hybrid=True)
    results_bob = store_bob.search("vacation", hybrid=True)

    alice_contents = {m.content for m in results_alice}
    bob_contents = {m.content for m in results_bob}

    assert "alice private note about vacation" in alice_contents
    assert "bob private note about vacation" not in alice_contents

    assert "bob private note about vacation" in bob_contents
    assert "alice private note about vacation" not in bob_contents


# ── Intégration _apply_workspace_policy ───────────────────────────────────────

def test_apply_workspace_policy_isolates_users_in_multi_mode(tmp_path):
    """local:owner et telegram:42 doivent obtenir des workspaces différents."""
    from unittest.mock import patch, MagicMock
    import os

    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True), \
         patch("src.utils.paths.DATA_DIR", tmp_path):

        from src.runtime.workspace_policy import resolve_workspace_for_user
        res_owner = resolve_workspace_for_user("local:owner", data_dir=tmp_path)
        res_tg = resolve_workspace_for_user("telegram:42", data_dir=tmp_path)

    assert res_owner.resolved_workspace != res_tg.resolved_workspace
    assert "local__owner" in res_owner.resolved_workspace
    assert "telegram__42" in res_tg.resolved_workspace


def test_apply_workspace_policy_same_in_single_mode(tmp_path):
    """En mode single-user, les deux users tombent sur le même chemin de base."""
    with patch("src.runtime.user_profile.MULTI_USER_ENABLED", False):
        from src.runtime.workspace_policy import resolve_workspace_for_user
        res_a = resolve_workspace_for_user("local:owner", data_dir=tmp_path)
        res_b = resolve_workspace_for_user("telegram:42", data_dir=tmp_path)
    assert Path(res_a.resolved_workspace).parent == Path(res_b.resolved_workspace).parent


# ── get_user_memory sur LumenaCore ────────────────────────────────────────────

def test_lumena_core_get_user_memory_no_crash(tmp_path):
    """get_user_memory ne doit pas lever d'exception même sans ChromaDB."""
    try:
        from src.core import LumenaCore
    except Exception:
        pytest.skip("LumenaCore non importable sans dépendances complètes")

    core = LumenaCore.__new__(LumenaCore)
    core.data_dir = tmp_path
    core._user_memory_cache = {}
    core.memory = None

    # Sans mémoire disponible, doit retourner None proprement
    result = core.get_user_memory("telegram:42")
    assert result is None


# ── Test direct _apply_workspace_policy() ────────────────────────────────────

def test_apply_workspace_policy_direct_isolates_users(tmp_path):
    """Appel direct à _apply_workspace_policy() : local:owner et telegram:42 doivent
    produire deux workspace_path différents en mode LUMENA_MULTI_USER=1."""
    from unittest.mock import patch, MagicMock
    from web.routes.schemas import ChatRequest
    from web.routes.chat import _apply_workspace_policy

    req_owner = ChatRequest(message="bonjour", user_id="local:owner")
    req_tg = ChatRequest(message="bonjour", user_id="telegram:42")

    # Simuler runtime disponible + multi-user ON
    from src.runtime.workspace_policy import resolve_workspace_for_user as _rswu
    import web.routes.deps as _deps

    _orig_ru = getattr(_deps, "resolve_workspace_for_user", None)
    _orig_ra = getattr(_deps, "RUNTIME_AVAILABLE", False)

    try:
        _deps.resolve_workspace_for_user = _rswu
        _deps.RUNTIME_AVAILABLE = True
        _deps.lumena = None

        with patch("src.runtime.user_profile.MULTI_USER_ENABLED", True), \
             patch("src.utils.paths.DATA_DIR", tmp_path), \
             patch("web.routes.chat.WORKSPACE_POLICY_V2_ENABLED", True):

            result_owner = _apply_workspace_policy(req_owner, "web", {})
            result_tg = _apply_workspace_policy(req_tg, "web", {})
    finally:
        _deps.resolve_workspace_for_user = _orig_ru
        _deps.RUNTIME_AVAILABLE = _orig_ra

    ws_owner = result_owner["workspace_path"]
    ws_tg = result_tg["workspace_path"]

    assert ws_owner != ws_tg, (
        f"Les deux users ne doivent pas avoir le même workspace :\n"
        f"  owner → {ws_owner}\n"
        f"  telegram:42 → {ws_tg}"
    )
    assert "local__owner" in ws_owner
    assert "telegram__42" in ws_tg


def test_apply_workspace_policy_direct_single_mode_same_base(tmp_path):
    """En mode single-user, les deux users partagent la même base workspace."""
    from web.routes.schemas import ChatRequest
    from web.routes.chat import _apply_workspace_policy
    import web.routes.deps as _deps
    from src.runtime.workspace_policy import resolve_workspace_for_user as _rswu

    req_owner = ChatRequest(message="test", user_id="local:owner")
    req_tg = ChatRequest(message="test", user_id="telegram:42")

    _orig_ru = getattr(_deps, "resolve_workspace_for_user", None)
    _orig_ra = getattr(_deps, "RUNTIME_AVAILABLE", False)

    try:
        _deps.resolve_workspace_for_user = _rswu
        _deps.RUNTIME_AVAILABLE = True
        _deps.lumena = None

        with patch("src.runtime.user_profile.MULTI_USER_ENABLED", False), \
             patch("web.routes.chat.WORKSPACE_POLICY_V2_ENABLED", True):

            result_owner = _apply_workspace_policy(req_owner, "web", {})
            result_tg = _apply_workspace_policy(req_tg, "web", {})
    finally:
        _deps.resolve_workspace_for_user = _orig_ru
        _deps.RUNTIME_AVAILABLE = _orig_ra

    from pathlib import Path
    assert (
        Path(result_owner["workspace_path"]).parent
        == Path(result_tg["workspace_path"]).parent
    )
