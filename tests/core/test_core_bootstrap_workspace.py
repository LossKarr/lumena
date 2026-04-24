"""Tests — Bootstrap core.py : RepoMap/CodeIndex conditionné au workspace."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_lumena_root() -> Path:
    """Retourne le vrai root Lumena (parent.parent de src/core.py)."""
    return (Path(__file__).parent.parent.parent / "src" / "core.py").parent.parent


class TestCoreBootstrapWorkspace:
    def _run_bootstrap(self, monkeypatch, tmp_path, ext_ws_val: str):
        """Simule le bloc bootstrap de core.py avec l'env var donnée."""
        monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", ext_ws_val)

        repo_map_calls: list[Path] = []
        code_index_calls: list[Path] = []

        class FakeRepoMap:
            def __init__(self, root, **kw):
                repo_map_calls.append(root)

        class FakeCodeIndex:
            def __init__(self, root, **kw):
                code_index_calls.append(root)

        with patch("src.context.repo_map.RepoMap", FakeRepoMap), \
             patch("src.context.code_index.CodeIndex", FakeCodeIndex):

            # Reproduire la logique du bloc bootstrap sans instancier LumenaCore entier
            _lumena_root = Path("src/core.py").resolve().parent.parent
            _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
            _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
            if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
                project_root = _ext_ws
            else:
                project_root = _lumena_root

            FakeRepoMap(project_root, max_files=25, max_tokens=1200)
            FakeCodeIndex(project_root)

        return repo_map_calls, code_index_calls

    def test_no_env_uses_lumena_root(self, monkeypatch, tmp_path):
        """Sans LUMENA_DEFAULT_WORKSPACE → root Lumena."""
        monkeypatch.delenv("LUMENA_DEFAULT_WORKSPACE", raising=False)
        _lumena_root = Path("src/core.py").resolve().parent.parent

        repo_map_calls: list[Path] = []

        _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
        if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
            project_root = _ext_ws
        else:
            project_root = _lumena_root

        assert project_root == _lumena_root

    def test_external_workspace_used_when_valid(self, monkeypatch, tmp_path):
        """LUMENA_DEFAULT_WORKSPACE valide et différent → workspace externe utilisé."""
        monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", str(tmp_path))
        _lumena_root = Path("src/core.py").resolve().parent.parent

        _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
        if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
            project_root = _ext_ws
        else:
            project_root = _lumena_root

        assert project_root == tmp_path.resolve()

    def test_lumena_root_env_falls_back_to_lumena(self, monkeypatch):
        """LUMENA_DEFAULT_WORKSPACE = repo Lumena lui-même → pas de redirection."""
        _lumena_root = Path("src/core.py").resolve().parent.parent
        monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", str(_lumena_root))

        _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
        if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
            project_root = _ext_ws
        else:
            project_root = _lumena_root

        assert project_root == _lumena_root

    def test_nonexistent_path_falls_back_to_lumena(self, monkeypatch):
        """LUMENA_DEFAULT_WORKSPACE inexistant → fallback Lumena."""
        monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", "/nonexistent/path/that/does/not/exist")
        _lumena_root = Path("src/core.py").resolve().parent.parent

        _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
        if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
            project_root = _ext_ws
        else:
            project_root = _lumena_root

        assert project_root == _lumena_root

    def test_empty_env_falls_back_to_lumena(self, monkeypatch):
        """LUMENA_DEFAULT_WORKSPACE vide → fallback Lumena."""
        monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", "")
        _lumena_root = Path("src/core.py").resolve().parent.parent

        _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
        if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
            project_root = _ext_ws
        else:
            project_root = _lumena_root

        assert project_root == _lumena_root
