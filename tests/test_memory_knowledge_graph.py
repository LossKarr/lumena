"""Tests unitaires pour src/memory/knowledge_graph.py"""
import pytest
import tempfile
from pathlib import Path

from src.memory.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraph(graph_path=tmp_path / "test_kg.json")


class TestKnowledgeGraphAddEntity:
    def test_add_entity_returns_id(self, kg):
        nid = kg.add_entity("Alice", "person")
        assert isinstance(nid, str)
        assert len(nid) > 0

    def test_same_entity_returns_same_id(self, kg):
        id1 = kg.add_entity("Bob Dupont", "person")
        id2 = kg.add_entity("Bob Dupont", "person")
        assert id1 == id2

    def test_case_insensitive_dedup(self, kg):
        id1 = kg.add_entity("alice", "person")
        id2 = kg.add_entity("Alice", "person")
        assert id1 == id2

    def test_entity_stored(self, kg):
        kg.add_entity("Acme SAS", "company", source="contract.pdf")
        assert "Acme SAS".lower() in kg._index


class TestKnowledgeGraphAddTriple:
    def test_add_triple_creates_edge(self, kg):
        kg.add_triple("Alice", "travaille_pour", "Acme SAS")
        assert len(kg._edges) == 1

    def test_edge_has_correct_relation(self, kg):
        kg.add_triple("Alice", "likes", "Python")
        edge = kg._edges[0]
        assert edge["relation"] == "likes"

    def test_edge_source_stored(self, kg):
        kg.add_triple("X", "knows", "Y", source="doc.txt")
        assert kg._edges[0]["source"] == "doc.txt"


class TestKnowledgeGraphSearch:
    def test_search_returns_results(self, kg):
        kg.add_entity("Dupont Jean", "person")
        results = kg.search("Dupont")
        assert len(results) > 0

    def test_search_no_match(self, kg):
        results = kg.search("xyznotexist")
        assert results == []

    def test_search_multiple_entities(self, kg):
        kg.add_entity("Python Tech", "technology")
        kg.add_entity("Python Alice", "person")
        results = kg.search("Python")
        assert len(results) >= 1


class TestKnowledgeGraphPersistence:
    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "kg.json"
        kg1 = KnowledgeGraph(graph_path=path)
        kg1.add_entity("Test Entity", "thing")
        kg1._save()

        kg2 = KnowledgeGraph(graph_path=path)
        assert "test entity" in kg2._index

    def test_empty_on_missing_file(self, tmp_path):
        kg = KnowledgeGraph(graph_path=tmp_path / "nonexistent.json")
        assert len(kg._nodes) == 0
        assert len(kg._edges) == 0


class TestKnowledgeGraphStats:
    def test_stats_structure(self, kg):
        kg.add_triple("A", "rel", "B")
        stats = kg.get_stats()
        assert "nodes" in stats
        assert "edges" in stats
        assert stats["edges"] == 1
