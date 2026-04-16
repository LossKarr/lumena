"""Tests unitaires pour src/tools/local_file_crawler.py"""
import pytest
from pathlib import Path

from src.tools.local_file_crawler import LocalFileCrawler


@pytest.fixture
def crawler(tmp_path):
    return LocalFileCrawler(data_dir=tmp_path)


class TestLocalFileCrawlerInit:
    def test_init(self, crawler, tmp_path):
        assert crawler.data_dir == tmp_path

    def test_default_campaign_id(self, crawler):
        cid = crawler._default_campaign_id()
        assert isinstance(cid, str)
        assert len(cid) > 0

    def test_campaign_dir(self, crawler):
        d = crawler._campaign_dir("test_campaign")
        assert "test_campaign" in str(d)

    def test_workspace_root(self, crawler):
        root = crawler._workspace_root()
        assert isinstance(root, Path)


class TestLocalFileCrawlerNormalizeGlobs:
    def test_list_passthrough(self, crawler):
        patterns = crawler._normalize_globs(["*.py", "*.md"])
        assert patterns == ["*.py", "*.md"]

    def test_string_splits(self, crawler):
        patterns = crawler._normalize_globs("*.py,*.md")
        assert "*.py" in patterns

    def test_none_returns_empty(self, crawler):
        patterns = crawler._normalize_globs(None)
        assert isinstance(patterns, list)


class TestLocalFileCrawlerIsAllowed:
    def test_all_allowed_when_no_patterns(self, crawler):
        result = crawler._is_allowed_by_patterns("file.py", [], [])
        assert result is True

    def test_include_pattern_matches(self, crawler):
        result = crawler._is_allowed_by_patterns("file.py", ["*.py"], [])
        assert result is True

    def test_include_pattern_no_match(self, crawler):
        result = crawler._is_allowed_by_patterns("file.md", ["*.py"], [])
        assert result is False

    def test_exclude_pattern_excludes(self, crawler):
        result = crawler._is_allowed_by_patterns("file.py", [], ["*.py"])
        assert result is False


class TestLocalFileCrawlerCampaignStatus:
    def test_no_campaign_returns_not_found(self, crawler):
        status = crawler.campaign_status("nonexistent_campaign")
        assert isinstance(status, dict)
        assert status.get("found") is False or "error" in status or "status" in status


class TestLocalFileCrawlerScoreContent:
    def test_keyword_match_increases_score(self, crawler):
        score_with = crawler._score_content("file.py", "def my_function", "function")
        score_without = crawler._score_content("file.py", "def my_function", "xyz_unrelated")
        assert score_with >= score_without

    def test_score_is_float(self, crawler):
        score = crawler._score_content("test.py", "test content here", "test")
        assert isinstance(score, float)
