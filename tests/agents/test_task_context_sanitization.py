from pathlib import Path

from src.agents.task_context import TaskContext


def test_project_path_trailing_markdown_backtick_is_cleaned(tmp_path: Path):
    project = tmp_path / "starforge-colony" / "site"
    project.mkdir(parents=True)

    ctx = TaskContext.from_delegate_call(
        "modifie le site",
        project_path=str(project) + "\\`",
        runtime_root=tmp_path,
    )

    assert ctx.workspace_path == project
    assert "`" not in str(ctx.workspace_path)


def test_extracted_path_trailing_backtick_is_cleaned(tmp_path: Path):
    project = tmp_path / "site"
    project.mkdir()

    extracted = TaskContext._extract_path_from_texts([
        f"travaille dans `{project}\\``",
    ])

    assert extracted == project
