"""M102 - regressions exposed by the EchoForge real-runtime canary."""

from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.website import _resolve_web_project_dir
from src.tools.playwright_browser import _same_url_requires_reload
from src.tools.web_project_runtime_verifier import (
    _effective_runtime_entry,
    looks_like_web_project,
)
from src.tools.website_builder import _unlinked_stylesheets


def test_empty_verifier_path_prefers_current_mission_root(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    mission = workspace / "missions" / "task_echo"
    (mission / "templates").mkdir(parents=True)
    (mission / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8"
    )
    (mission / "templates" / "index.html").write_text(
        "<!doctype html><main>Echo</main>", encoding="utf-8"
    )
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", workspace)

    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task_echo"
    ctx.mission_workspace = "missions/task_echo"

    assert _resolve_web_project_dir(ctx) == mission


def test_explicit_templates_path_is_promoted_to_flask_project_root(tmp_path: Path):
    project = tmp_path / "echo"
    (project / "templates").mkdir(parents=True)
    (project / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8"
    )
    (project / "templates" / "index.html").write_text("<main>Echo</main>", encoding="utf-8")
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)

    assert _resolve_web_project_dir(ctx, project_path=str(project / "templates")) == project


def test_flask_project_with_template_is_web_and_opens_root(tmp_path: Path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8"
    )
    (tmp_path / "templates" / "index.html").write_text("<main>Echo</main>", encoding="utf-8")

    assert looks_like_web_project(tmp_path) is True
    assert _effective_runtime_entry(tmp_path, "index.html") == ""


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ("http://localhost:8081/", "http://localhost:8081", True),
        ("http://127.0.0.1:8086/", "http://127.0.0.1:8086", True),
        ("https://example.com/", "https://example.com", False),
        ("http://localhost:8081", "http://localhost:8082", False),
    ],
)
def test_same_local_preview_url_reloads_instead_of_using_stale_page(
    current: str, target: str, expected: bool
):
    assert _same_url_requires_reload(current, target) is expected


def test_jinja_static_stylesheet_is_recognized_as_linked(tmp_path: Path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / "templates" / "index.html").write_text(
        "<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">",
        encoding="utf-8",
    )
    (tmp_path / "static" / "style.css").write_text("body{}", encoding="utf-8")

    assert _unlinked_stylesheets(tmp_path) == []
