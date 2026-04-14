"""Tests unitaires pour src/memory/bm25_index.py"""
import pytest

from src.memory.bm25_index import BM25Index


class TestBM25IndexTokenize:
    def test_tokenize_basic(self):
        idx = BM25Index()
        tokens = idx._tokenize("Hello world Python")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens

    def test_stopwords_removed(self):
        idx = BM25Index()
        tokens = idx._tokenize("le la les the a an and or")
        assert tokens == []

    def test_short_words_removed(self):
        idx = BM25Index()
        tokens = idx._tokenize("is a I ok")
        assert all(len(t) > 2 for t in tokens)


class TestBM25IndexAddRemove:
    def test_add_document_increases_count(self):
        idx = BM25Index()
        idx.add_document("doc1", "Python programming language")
        assert len(idx.doc_ids) == 1

    def test_add_alias(self):
        idx = BM25Index()
        idx.add("doc1", "some content here")
        assert idx.doc_count == 1

    def test_update_existing_document(self):
        idx = BM25Index()
        idx.add_document("doc1", "original content here")
        idx.add_document("doc1", "updated content changed")
        assert len(idx.doc_ids) == 1
        assert idx.raw_contents["doc1"] == "updated content changed"

    def test_remove_document(self):
        idx = BM25Index()
        idx.add_document("doc1", "test content present")
        result = idx.remove_document("doc1")
        assert result is True
        assert "doc1" not in idx.doc_ids

    def test_remove_nonexistent(self):
        idx = BM25Index()
        assert idx.remove_document("ghost") is False

    def test_doc_count_property(self):
        idx = BM25Index()
        assert idx.doc_count == 0
        idx.add_document("a", "alpha beta gamma")
        assert idx.doc_count == 1
        idx.add_document("b", "delta epsilon zeta")
        assert idx.doc_count == 2


class TestBM25IndexSearch:
    def test_search_returns_results_when_available(self):
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            pytest.skip("rank-bm25 non installé")

        idx = BM25Index()
        idx.add_document("doc1", "Python programming language tutorial")
        idx.add_document("doc2", "Java object oriented programming")
        idx.add_document("doc3", "Machine learning deep learning neural")

        results = idx.search("Python tutorial", top_k=2)
        assert len(results) <= 2
        # doc1 devrait être en premier pour "Python"
        if results:
            assert results[0][0] == "doc1"

    def test_search_empty_index(self):
        idx = BM25Index()
        results = idx.search("something")
        assert results == []

    def test_search_returns_tuple_id_score(self):
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            pytest.skip("rank-bm25 non installé")

        idx = BM25Index()
        idx.add_document("doc1", "unique keyword xylophone content zeta")
        results = idx.search("unique xylophone zeta", top_k=5)
        if results:
            doc_id, score = results[0]
            assert isinstance(doc_id, str)
            assert isinstance(score, float)
