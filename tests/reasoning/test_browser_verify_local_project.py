from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.website import (
    browser_verify_local_project_handler,
    get_website_handler_defs,
)


class _FakeVerifyResult:
    def __init__(self, passed: bool):
        self.passed = passed

    def to_report(self, max_chars: int = 5000):
        return "runtime ok" if self.passed else "console_error: boom"


def test_browser_verify_local_project_registered():
    names = {item.name for item in get_website_handler_defs()}
    assert "browser_verify_local_project" in names


@pytest.mark.asyncio
async def test_browser_verify_local_project_handler_success(tmp_path: Path, monkeypatch):
    project = tmp_path / "site"
    project.mkdir()
    (project / "index.html").write_text("<!doctype html><main>OK</main>", encoding="utf-8")

    async def fake_verify(*_args, **_kwargs):
        return _FakeVerifyResult(True)

    monkeypatch.setattr(
        "src.tools.web_project_runtime_verifier.verify_web_project_runtime",
        fake_verify,
    )

    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
    result = await browser_verify_local_project_handler(ctx, project_path=str(project))

    assert result.success is True
    assert result.status_code == "runtime_ok"
    assert "runtime ok" in result.output


@pytest.mark.asyncio
async def test_browser_verify_local_project_handler_failure(tmp_path: Path, monkeypatch):
    project = tmp_path / "site"
    project.mkdir()
    (project / "index.html").write_text("<!doctype html><main>OK</main>", encoding="utf-8")

    async def fake_verify(*_args, **_kwargs):
        return _FakeVerifyResult(False)

    monkeypatch.setattr(
        "src.tools.web_project_runtime_verifier.verify_web_project_runtime",
        fake_verify,
    )

    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
    result = await browser_verify_local_project_handler(ctx, project_path=str(project))

    assert result.success is False
    assert result.status_code == "runtime_failed"
    assert "console_error" in result.output
