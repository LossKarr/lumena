"""Tests unitaires pour SuccessStore (P1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.learning.success_store import (
    SuccessPattern,
    SuccessStore,
    build_success_prompt,
    get_success_store,
    parse_success_llm_response,
    reset_success_store,
)


@pytest.fixture
def tmp_store(tmp_path) -> SuccessStore:
    reset_success_store()
    store = SuccessStore(path=tmp_path / "successes.jsonl")
    yield store
    reset_success_store()


class TestSuccessPattern:
    def test_id_stable(self):
        a = SuccessPattern.compute_id("Corriger bug auth")
        b = SuccessPattern.compute_id("  Corriger   bug  auth  ")
        c = SuccessPattern.compute_id("CORRIGER BUG AUTH")
        assert a == b == c
        assert a.startswith("succ_")

    def test_id_differs(self):
        assert SuccessPattern.compute_id("x") != SuccessPattern.compute_id("y")

    def test_to_compact_renders(self):
        p = SuccessPattern(
            id="succ_x", task_type="bugfix", summary="Fix foo",
            approach="Lire avant editer", tools_used=["read_file", "str_replace"],
            uses=2,
        )
        out = p.to_compact()
        assert "bugfix" in out
        assert "Fix foo" in out
        assert "read_file" in out
        assert "2×" in out

    def test_from_dict_defaults(self):
        p = SuccessPattern.from_dict({"summary": "only summary"})
        assert p.id.startswith("succ_")
        assert p.task_type == "other"
        assert p.tools_used == []


class TestSuccessStoreAddRetrieve:
    def test_add_and_retrieve_basic(self, tmp_store):
        tmp_store.add(
            task_type="bugfix",
            summary="Corriger erreur import circulaire Python",
            approach="Extraire la dépendance commune dans un module tiers",
            tools_used=["read_file", "edit_file"],
            apply_when="import circular python",
        )
        hits = tmp_store.retrieve("import circulaire python", k=3)
        assert len(hits) == 1
        assert "import" in hits[0].summary.lower()

    def test_retrieve_empty_query(self, tmp_store):
        tmp_store.add(task_type="x", summary="abc def", approach="ghi")
        assert tmp_store.retrieve("", k=3) == []

    def test_retrieve_no_match(self, tmp_store):
        tmp_store.add(task_type="x", summary="Refactor payment module", approach="split")
        hits = tmp_store.retrieve("astrophysique relativité", k=3, min_score=0.5)
        assert hits == []

    def test_dedup_boosts_confidence(self, tmp_store):
        a = tmp_store.add(task_type="x", summary="Tâche identique", approach="app1", confidence=0.6)
        b = tmp_store.add(task_type="x", summary="Tâche identique", approach="app2", confidence=0.6)
        assert a.id == b.id
        assert b.confidence >= 0.7  # boost +0.1

    def test_dedup_merges_tools(self, tmp_store):
        tmp_store.add(task_type="x", summary="Tâche ABC", approach="a", tools_used=["tool1"])
        p = tmp_store.add(task_type="x", summary="Tâche ABC", approach="a", tools_used=["tool2"])
        assert "tool1" in p.tools_used and "tool2" in p.tools_used

    def test_increment_uses(self, tmp_store):
        p = tmp_store.add(task_type="x", summary="Summary A", approach="approche")
        tmp_store.increment_uses(p.id)
        tmp_store.increment_uses(p.id)
        assert tmp_store._items[p.id].uses == 2

    def test_increment_uses_unknown(self, tmp_store):
        # Ne doit pas lever
        tmp_store.increment_uses("succ_doesnotexist")

    def test_task_type_boost(self, tmp_store):
        tmp_store.add(
            task_type="bugfix",
            summary="fix user login crash",
            approach="corriger null check",
        )
        tmp_store.add(
            task_type="feature",
            summary="add user login feature",
            approach="implem OAuth",
        )
        # Avec task_type="bugfix", le bugfix doit remonter
        hits = tmp_store.retrieve("user login", k=2, task_type="bugfix")
        assert hits[0].task_type == "bugfix"

    def test_requires_summary(self, tmp_store):
        with pytest.raises(ValueError):
            tmp_store.add(task_type="x", summary="", approach="a")


class TestPersistence:
    def test_add_persists(self, tmp_store):
        tmp_store.add(task_type="refactor", summary="Test persistence", approach="split")
        new_store = SuccessStore(path=tmp_store.path)
        assert len(new_store) == 1
        assert list(new_store._items.values())[0].summary == "Test persistence"

    def test_forget_removes(self, tmp_store):
        p = tmp_store.add(task_type="x", summary="À oublier", approach="a")
        assert tmp_store.forget(p.id) is True
        assert len(tmp_store) == 0
        new_store = SuccessStore(path=tmp_store.path)
        assert len(new_store) == 0

    def test_forget_unknown_returns_false(self, tmp_store):
        assert tmp_store.forget("succ_inexistant") is False

    def test_append_only_jsonl_format(self, tmp_store):
        tmp_store.add(task_type="a", summary="S1", approach="ap1")
        tmp_store.add(task_type="b", summary="S2", approach="ap2")
        content = tmp_store.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(content) == 2
        for line in content:
            json.loads(line)  # doit être JSON valide


class TestLLMHelpers:
    def test_build_prompt_shape(self):
        msgs = build_success_prompt(
            task_description="Corriger bug auth",
            tools_used=["read_file", "edit_file"],
            iterations=4,
            outcome_summary="Tests passent",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "Corriger bug auth" in msgs[1]["content"]
        assert "read_file" in msgs[1]["content"]

    def test_parse_valid_response(self):
        raw = """```json
        {
            "task_type": "bugfix",
            "summary": "Fixing auth issues",
            "approach": "Read then edit",
            "apply_when": "auth login",
            "tags": ["auth", "bugfix"],
            "confidence": 0.8
        }
        ```"""
        parsed = parse_success_llm_response(raw)
        assert parsed is not None
        assert parsed["task_type"] == "bugfix"
        assert parsed["confidence"] == 0.8
        assert parsed["tags"] == ["auth", "bugfix"]

    def test_parse_missing_summary(self):
        raw = '{"task_type": "x", "approach": "no summary"}'
        assert parse_success_llm_response(raw) is None

    def test_parse_missing_approach(self):
        raw = '{"summary": "s", "task_type": "x"}'
        assert parse_success_llm_response(raw) is None

    def test_parse_invalid_json(self):
        assert parse_success_llm_response("no json here at all") is None

    def test_parse_empty(self):
        assert parse_success_llm_response("") is None

    def test_parse_normalizes_confidence(self):
        raw = '{"summary": "s", "approach": "a", "confidence": "invalid"}'
        parsed = parse_success_llm_response(raw)
        assert parsed is not None
        assert parsed["confidence"] == 0.7  # fallback


class TestSingleton:
    def test_singleton_returns_same(self, tmp_path):
        reset_success_store()
        s1 = get_success_store(path=tmp_path / "s.jsonl")
        s2 = get_success_store()
        assert s1 is s2
        reset_success_store()

    def test_reset_clears(self, tmp_path):
        reset_success_store()
        s1 = get_success_store(path=tmp_path / "s.jsonl")
        reset_success_store()
        s2 = get_success_store(path=tmp_path / "s.jsonl")
        assert s1 is not s2


class TestFormatForPrompt:
    def test_empty_returns_empty(self, tmp_store):
        assert tmp_store.format_for_prompt([]) == ""

    def test_header_and_items(self, tmp_store):
        p1 = tmp_store.add(task_type="a", summary="S1", approach="ap1")
        p2 = tmp_store.add(task_type="b", summary="S2", approach="ap2")
        out = tmp_store.format_for_prompt([p1, p2])
        assert "🏆" in out
        assert "S1" in out
        assert "S2" in out
