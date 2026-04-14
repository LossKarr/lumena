"""Tests unitaires pour src/context/repo_map.py"""
import pytest
from pathlib import Path

from src.context.repo_map import RepoMap, RepoStats


class TestRepoStats:
    def test_default_values(self):
        stats = RepoStats()
        assert stats.total_files == 0
        assert stats.total_symbols == 0
        assert isinstance(stats.languages, dict)
        assert isinstance(stats.largest_files, list)

    def test_custom_values(self):
        stats = RepoStats(
            total_files=10,
            total_symbols=50,
            languages={"Python": 10},
            largest_files=[("main.py", 500)]
        )
        assert stats.total_files == 10
        assert stats.languages["Python"] == 10


class TestRepoMap:
    @pytest.fixture
    def repo_map(self, tmp_path):
        # Create a small test project structure
        (tmp_path / "main.py").write_text("def main(): pass\n\nclass App:\n    pass\n")
        (tmp_path / "utils.py").write_text("import os\n\ndef helper(): return 42\n")
        (tmp_path / "README.md").write_text("# Test project\n")
        return RepoMap(project_path=tmp_path, max_files=10, max_symbols_per_file=5)

    def test_instantiation(self, repo_map):
        assert repo_map is not None

    def test_project_path(self, repo_map, tmp_path):
        assert repo_map.project_path == tmp_path

    def test_max_files(self, repo_map):
        assert repo_map.max_files == 10

    def test_build_runs(self, repo_map):
        repo_map.build()  # Should not raise

    def test_get_stats_after_build(self, repo_map):
        repo_map.build()
        stats = repo_map.get_stats()
        assert isinstance(stats, RepoStats)
        assert stats.total_files >= 0

    def test_get_compact_map(self, repo_map):
        repo_map.build()
        result = repo_map.get_compact_map()
        assert isinstance(result, str)

    def test_get_top_files(self, repo_map):
        repo_map.build()
        top = repo_map.get_top_files(n=3)
        assert isinstance(top, list)

    def test_search_symbol(self, repo_map):
        repo_map.build()
        results = repo_map.search_symbol("main")
        assert isinstance(results, list)

    def test_get_file_context(self, repo_map, tmp_path):
        repo_map.build()
        ctx = repo_map.get_file_context("main.py")
        assert isinstance(ctx, str)

    def test_refresh(self, repo_map):
        repo_map.build()
        repo_map.refresh()  # Should not raise

    def test_empty_dir(self, tmp_path):
        empty_sub = tmp_path / "empty"
        empty_sub.mkdir()
        rm = RepoMap(project_path=empty_sub)
        rm.build()
        stats = rm.get_stats()
        assert stats.total_files == 0
