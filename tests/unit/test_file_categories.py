"""Tests unitaires pour file_categories.requires_codeagent / categorize."""
import pytest
from src.reasoning.file_categories import requires_codeagent, categorize


@pytest.mark.parametrize("path,expected", [
    # Code → CodeAgent
    ("src/app.py", True),
    ("index.js", True),
    ("module.ts", True),
    ("style.css", True),
    ("page.html", True),
    ("component.vue", True),
    ("main.rs", True),
    ("script.sh", True),
    ("main.go", True),
    # Config → CodeAgent
    ("package.json", True),
    ("pyproject.toml", True),
    ("config.yaml", True),
    ("Dockerfile", True),
    ("Makefile", True),
    (".gitignore", True),
    (".env", True),
    # Docs → ReAct
    ("README.md", False),
    ("docs/guide.rst", False),
    ("notes.txt", False),
    # Binaires → ReAct
    ("CV.pdf", False),
    ("photo.png", False),
    ("image.jpg", False),
    ("video.mp4", False),
    ("song.mp3", False),
    ("archive.zip", False),
    # Assets → ReAct
    ("logo.svg", False),
    ("icon.ico", False),
    ("font.woff2", False),
    # Inconnu → False (prudent)
    ("weird.xyz123", False),
])
def test_requires_codeagent(path, expected):
    assert requires_codeagent(path) is expected


@pytest.mark.parametrize("path,expected", [
    ("app.py", "code"),
    ("config.json", "config"),
    ("README.md", "doc"),
    ("image.png", "binary"),
    ("logo.svg", "asset"),
    ("unknown.xyz123", "unknown"),
    ("Dockerfile", "config"),
    (".env", "config"),
])
def test_categorize(path, expected):
    assert categorize(path) == expected


def test_path_with_directories():
    assert requires_codeagent("/home/user/project/src/main.py") is True
    assert requires_codeagent("C:\\Users\\me\\docs\\guide.pdf") is False


def test_case_insensitive():
    assert requires_codeagent("MAIN.PY") is True
    assert requires_codeagent("README.MD") is False
