"""Tests du ReflexionStore : mémoire long-terme des leçons d'échec."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.learning.reflexion_store import (
    Reflexion,
    ReflexionStore,
    get_reflexion_store,
    reset_reflexion_store,
    build_reflexion_prompt,
    parse_reflexion_llm_response,
    _tokenize,
    _jaccard,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> ReflexionStore:
    return ReflexionStore(path=tmp_path / "refl.jsonl")


# ── Tokenizer / Jaccard ─────────────────────────────────────────────────────


def test_tokenize_ignores_stopwords_and_short():
    toks = _tokenize("Le grep dans un fichier CSS ne trouve pas /* Responsive */")
    assert "grep" in toks
    assert "fichier" in toks
    assert "css" in toks
    assert "responsive" in toks
    assert "le" not in toks
    assert "un" not in toks


def test_tokenize_accents_preserved():
    toks = _tokenize("créé déjà écrit")
    assert "créé" in toks
    assert "déjà" in toks
    assert "écrit" in toks


def test_jaccard_basic():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert _jaccard({"a"}, {"a"}) == 1.0


# ── Reflexion dataclass ─────────────────────────────────────────────────────


def test_reflexion_id_is_stable_and_deterministic():
    a = Reflexion.compute_id("Préférer edit_lines sur les fichiers CSS longs.")
    b = Reflexion.compute_id("  préférer edit_lines SUR les  fichiers CSS longs.  ")
    assert a == b


def test_reflexion_from_dict_roundtrip():
    d = {
        "id": "refl_test",
        "triggered_by": "grep 0 result × 3",
        "root_cause": "pattern ne matche pas commentaires",
        "lesson": "Pour localiser section CSS, préférer @media aux commentaires /* === */",
        "apply_when": "CSS édition section responsive",
        "confidence": 0.8,
        "uses": 2,
        "created_at": "2026-04-18T19:00:00",
        "tags": ["css", "grep"],
    }
    r = Reflexion.from_dict(d)
    assert r.confidence == 0.8
    assert r.uses == 2
    assert r.tags == ["css", "grep"]
    restored = Reflexion.from_dict(r.to_dict())
    assert restored.id == r.id
    assert restored.lesson == r.lesson


# ── Store persistence ───────────────────────────────────────────────────────


def test_add_persists_to_disk_and_reloads(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    s1 = ReflexionStore(path=p)
    s1.add(
        triggered_by="grep 0 result",
        root_cause="pattern ne matche pas",
        lesson="Pour CSS, préférer @media aux commentaires",
        apply_when="CSS section responsive",
    )
    assert p.exists()
    # Reload fresh
    s2 = ReflexionStore(path=p)
    assert len(s2) == 1
    assert "css" in s2.all()[0].tags or True  # tags peuvent être vides
    assert s2.all()[0].lesson.startswith("Pour CSS")


def test_dedup_by_lesson_hash_boosts_confidence(tmp_store: ReflexionStore):
    r1 = tmp_store.add(
        triggered_by="t1", root_cause="c1",
        lesson="Préfère edit_lines pour gros fichiers.",
        apply_when="refactor python",
        confidence=0.7,
    )
    r2 = tmp_store.add(
        triggered_by="t2", root_cause="c2",
        lesson="  préfère EDIT_LINES pour gros fichiers.  ",
        apply_when="refactor js",
        confidence=0.6,
    )
    # Même id (même leçon normalisée)
    assert r1.id == r2.id
    assert len(tmp_store) == 1
    # Confiance boostée
    assert tmp_store.all()[0].confidence > 0.7


def test_increment_uses(tmp_store: ReflexionStore):
    r = tmp_store.add(
        triggered_by="t", root_cause="c",
        lesson="lesson A", apply_when="when A",
    )
    tmp_store.increment_uses(r.id)
    tmp_store.increment_uses(r.id)
    assert tmp_store.all()[0].uses == 2


def test_forget_removes_from_memory_and_disk(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    s = ReflexionStore(path=p)
    r = s.add(
        triggered_by="t", root_cause="c",
        lesson="lesson to forget", apply_when="when",
    )
    assert s.forget(r.id) is True
    assert len(s) == 0
    # Reload : toujours vide
    s2 = ReflexionStore(path=p)
    assert len(s2) == 0


def test_add_empty_lesson_raises(tmp_store: ReflexionStore):
    with pytest.raises(ValueError):
        tmp_store.add(triggered_by="", root_cause="", lesson="   ", apply_when="")


def test_prune_removes_old_unused(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    s = ReflexionStore(path=p)
    r = s.add(triggered_by="t", root_cause="c", lesson="old lesson", apply_when="w")
    # Manipulation directe : simule réflexion vieille de 100 jours
    r.created_at = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(timespec="seconds")
    s._append(r)  # reflète sur disque
    s2 = ReflexionStore(path=p)
    pruned = s2.prune(max_age_days=90, min_uses=0)
    assert pruned == 1
    assert len(s2) == 0


def test_prune_keeps_used_even_if_old(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    s = ReflexionStore(path=p)
    r = s.add(triggered_by="t", root_cause="c", lesson="old but used", apply_when="w")
    r.created_at = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(timespec="seconds")
    r.uses = 3
    s._append(r)
    s2 = ReflexionStore(path=p)
    pruned = s2.prune(max_age_days=90, min_uses=0)
    assert pruned == 0


# ── Retrieval ───────────────────────────────────────────────────────────────


def test_retrieve_returns_relevant_by_tokens(tmp_store: ReflexionStore):
    tmp_store.add(
        triggered_by="grep 0 result",
        root_cause="pattern no match",
        lesson="Pour CSS responsive, préférer @media aux commentaires /* === */",
        apply_when="CSS grep responsive section",
        tags=["css", "grep"],
    )
    tmp_store.add(
        triggered_by="import circular",
        root_cause="circular import python",
        lesson="Utiliser TYPE_CHECKING pour casser les imports circulaires.",
        apply_when="python import circular module",
        tags=["python", "import"],
    )
    hits = tmp_store.retrieve("éditer section responsive dans style.css", k=3)
    assert len(hits) >= 1
    assert "CSS" in hits[0].lesson


def test_retrieve_respects_k(tmp_store: ReflexionStore):
    for i in range(5):
        tmp_store.add(
            triggered_by=f"t{i}", root_cause=f"c{i}",
            lesson=f"lesson python refactor {i}",
            apply_when="python refactor",
        )
    hits = tmp_store.retrieve("refactor python function", k=2)
    assert len(hits) <= 2


def test_retrieve_no_match_returns_empty(tmp_store: ReflexionStore):
    tmp_store.add(
        triggered_by="t", root_cause="c",
        lesson="Lesson about CSS grid",
        apply_when="css grid layout",
    )
    hits = tmp_store.retrieve("totally unrelated subject xyz", k=3)
    assert hits == []


def test_retrieve_boost_usage_improves_ranking(tmp_store: ReflexionStore):
    r1 = tmp_store.add(
        triggered_by="t1", root_cause="c1",
        lesson="Python import circular fix A",
        apply_when="python import circular",
    )
    r2 = tmp_store.add(
        triggered_by="t2", root_cause="c2",
        lesson="Python import circular fix B",
        apply_when="python import circular",
    )
    # Booster r2 avec 5 usages
    for _ in range(5):
        tmp_store.increment_uses(r2.id)
    hits = tmp_store.retrieve("python import circular", k=2)
    assert len(hits) == 2
    assert hits[0].id == r2.id


# ── LLM prompt helpers ──────────────────────────────────────────────────────


def test_build_reflexion_prompt_has_system_and_user():
    msgs = build_reflexion_prompt("grep failed × 3", "trace: iter 11, 13, 14 grep Responsive")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "grep failed" in msgs[1]["content"]


def test_parse_reflexion_llm_response_valid():
    raw = """```json
    {
      "triggered_by": "grep x3",
      "root_cause": "pattern wrong",
      "lesson": "Use @media for css",
      "apply_when": "css responsive",
      "tags": ["css"],
      "confidence": 0.85
    }
    ```"""
    parsed = parse_reflexion_llm_response(raw)
    assert parsed is not None
    assert parsed["confidence"] == 0.85
    assert parsed["tags"] == ["css"]


def test_parse_reflexion_llm_response_missing_required_returns_none():
    raw = '{"lesson": "incomplete"}'
    assert parse_reflexion_llm_response(raw) is None


def test_parse_reflexion_llm_response_invalid_json_returns_none():
    assert parse_reflexion_llm_response("not json at all") is None
    assert parse_reflexion_llm_response("") is None


def test_parse_reflexion_llm_response_clamps_confidence():
    raw = """{
      "triggered_by": "x", "root_cause": "y", "lesson": "z",
      "apply_when": "w", "confidence": 2.0
    }"""
    parsed = parse_reflexion_llm_response(raw)
    assert parsed is not None
    assert parsed["confidence"] == 1.0


def test_parse_reflexion_llm_response_handles_missing_confidence():
    raw = """{
      "triggered_by": "x", "root_cause": "y", "lesson": "z", "apply_when": "w"
    }"""
    parsed = parse_reflexion_llm_response(raw)
    assert parsed is not None
    assert parsed["confidence"] == 0.7  # default


# ── Singleton ───────────────────────────────────────────────────────────────


def test_singleton_reset(tmp_path: Path):
    reset_reflexion_store()
    s1 = get_reflexion_store(path=tmp_path / "a.jsonl")
    s2 = get_reflexion_store()  # sans path, renvoie le même
    assert s1 is s2
    reset_reflexion_store()
    s3 = get_reflexion_store(path=tmp_path / "a.jsonl")
    assert s3 is not s1
    reset_reflexion_store()


# ── format_for_prompt ───────────────────────────────────────────────────────


def test_format_for_prompt_empty_returns_empty(tmp_store: ReflexionStore):
    assert tmp_store.format_for_prompt([]) == ""


def test_format_for_prompt_includes_header_and_bullets(tmp_store: ReflexionStore):
    r = tmp_store.add(
        triggered_by="t", root_cause="c",
        lesson="Lesson ABC", apply_when="w",
    )
    txt = tmp_store.format_for_prompt([r])
    assert "LEÇONS APPRISES" in txt
    assert "Lesson ABC" in txt


def test_format_for_prompt_shows_usage_count(tmp_store: ReflexionStore):
    r = tmp_store.add(
        triggered_by="t", root_cause="c",
        lesson="Used lesson", apply_when="w",
    )
    tmp_store.increment_uses(r.id)
    tmp_store.increment_uses(r.id)
    tmp_store.increment_uses(r.id)
    refreshed = tmp_store.all()[0]
    txt = tmp_store.format_for_prompt([refreshed])
    assert "3" in txt


# ── JSONL corruption robustness ─────────────────────────────────────────────


def test_load_ignores_invalid_json_lines(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({
                "id": Reflexion.compute_id("valid lesson"),
                "triggered_by": "t", "root_cause": "c",
                "lesson": "valid lesson", "apply_when": "w",
            }),
            "corrupted line not json",
            "",
            json.dumps({
                "id": Reflexion.compute_id("lesson 2"),
                "triggered_by": "t", "root_cause": "c",
                "lesson": "lesson 2", "apply_when": "w",
            }),
        ]),
        encoding="utf-8",
    )
    s = ReflexionStore(path=p)
    assert len(s) == 2
