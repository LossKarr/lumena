"""Tests P2 — Progressive tool-output pruning in compaction."""
from __future__ import annotations

import pytest


def _make_msgs(n_obs: int, obs_chars: int = 10000) -> list[dict]:
    """Construit une conversation avec n_obs observations + interleave assistant."""
    msgs = [
        {"role": "system", "content": "you are codeagent"},
        {"role": "user", "content": "fix bug in foo.py"},
    ]
    for i in range(n_obs):
        msgs.append({"role": "assistant", "content": f'{{"action": "read_file", "path": "f{i}.py"}}'})
        msgs.append({"role": "user", "content": f"Résultat de l'action:\n{'X' * obs_chars}"})
    return msgs


# ── Détection des observations ────────────────────────────────────────────


def test_looks_like_observation_detects_fr_prefix():
    from src.tools.compaction import _looks_like_observation
    assert _looks_like_observation({"role": "user", "content": "Résultat de l'action:\n..."})


def test_looks_like_observation_detects_en_prefix():
    from src.tools.compaction import _looks_like_observation
    assert _looks_like_observation({"role": "user", "content": "OBSERVATION: file read"})
    assert _looks_like_observation({"role": "user", "content": "Tool output: OK"})


def test_looks_like_observation_rejects_assistant():
    from src.tools.compaction import _looks_like_observation
    assert not _looks_like_observation({"role": "assistant", "content": "Résultat de l'action:"})


def test_looks_like_observation_rejects_plain_user():
    from src.tools.compaction import _looks_like_observation
    assert not _looks_like_observation({"role": "user", "content": "What does foo do?"})


# ── prune_large_observations ───────────────────────────────────────────────


def test_prune_keeps_recent_obs_intact():
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=6, obs_chars=10000)
    out, count = prune_large_observations(msgs, max_obs_chars=3000, keep_recent=3)
    # 3 plus récentes observations doivent être intactes
    assert count == 3  # 6 - 3 = 3 prunées
    # Derniers 3 obs (indices -1, -3, -5 des messages originaux) intacts
    assert len(out[-1]["content"]) == 10000 + len("Résultat de l'action:\n")


def test_prune_truncates_old_obs():
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=5, obs_chars=8000)
    out, count = prune_large_observations(msgs, max_obs_chars=2000, keep_recent=2)
    assert count == 3
    # La première obs (plus ancienne) doit être tronquée
    pruned_content = out[3]["content"]  # Index 3 = premier obs (system, user, asst, obs)
    assert "chars pruned" in pruned_content
    assert len(pruned_content) < 8000


def test_prune_preserves_head_and_tail():
    from src.tools.compaction import prune_large_observations
    marker_head = "HEAD_MARKER_UNIQUE"
    marker_tail = "TAIL_MARKER_UNIQUE"
    content = f"Résultat de l'action:\n{marker_head}" + ("Y" * 20000) + marker_tail
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": '{"action": "read_file"}'},
        {"role": "user", "content": content},
        {"role": "assistant", "content": '{"action": "done"}'},
        {"role": "user", "content": "Résultat de l'action:\ndone"},
        {"role": "assistant", "content": '{"action": "done"}'},
        {"role": "user", "content": "Résultat de l'action:\nok"},
        {"role": "assistant", "content": '{"action": "done"}'},
        {"role": "user", "content": "Résultat de l'action:\nfinal"},
    ]
    out, count = prune_large_observations(
        msgs, max_obs_chars=500, keep_recent=3, head_chars=100, tail_chars=50
    )
    assert count == 1
    pruned = out[3]["content"]
    assert marker_head in pruned
    assert marker_tail in pruned


def test_prune_noop_when_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_COMPACTION_PRUNE", "0")
    import importlib, src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=10, obs_chars=10000)
    out, count = prune_large_observations(msgs, max_obs_chars=1000, keep_recent=1)
    assert count == 0
    assert out == list(msgs)  # identique
    monkeypatch.delenv("LUMENA_COMPACTION_PRUNE", raising=False)
    importlib.reload(flags_mod)


def test_prune_noop_when_all_obs_small():
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=10, obs_chars=500)
    out, count = prune_large_observations(msgs, max_obs_chars=3000, keep_recent=3)
    assert count == 0


def test_prune_noop_when_too_few_obs():
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=2, obs_chars=10000)
    out, count = prune_large_observations(msgs, max_obs_chars=1000, keep_recent=3)
    assert count == 0  # 2 obs <= 3 keep_recent → skip


def test_prune_reduces_total_tokens():
    from src.tools.compaction import prune_large_observations, estimate_messages_tokens
    msgs = _make_msgs(n_obs=10, obs_chars=8000)
    before = estimate_messages_tokens(msgs)
    out, count = prune_large_observations(msgs, max_obs_chars=2000, keep_recent=2)
    after = estimate_messages_tokens(out)
    assert after < before
    # Pruning must be significant
    assert (before - after) > before * 0.3


def test_prune_returns_new_list():
    """Ne mute pas l'original."""
    from src.tools.compaction import prune_large_observations
    msgs = _make_msgs(n_obs=5, obs_chars=10000)
    original_content = msgs[3]["content"]
    out, count = prune_large_observations(msgs, max_obs_chars=1000, keep_recent=1)
    # L'original est intact
    assert msgs[3]["content"] == original_content


# ── Intégration sub_agent._maybe_compact ───────────────────────────────────


def test_maybe_compact_imports_prune_large_observations():
    """Le hook P2 est bien wire dans _maybe_compact."""
    src = open("src/agents/sub_agent.py", encoding="utf-8").read()
    assert "from src.tools.compaction import prune_large_observations" in src
    assert "P2 Plan Suprême" in src


# ── tiktoken déjà présent ──────────────────────────────────────────────────


def test_tiktoken_is_active_when_available():
    from src.tools.compaction import _TIKTOKEN_AVAILABLE, _count_str_tokens
    # tiktoken devrait être dispo dans l'env lumena
    if _TIKTOKEN_AVAILABLE:
        # Si actif, le compte doit être précis (pas le fallback //4)
        n = _count_str_tokens("hello world")
        assert 1 <= n <= 5  # tiktoken cl100k_base → 2 tokens typiquement
