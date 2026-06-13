from pathlib import Path

from src.tools.test_runner_detector import detect_test_runner


def test_detects_pnpm_test(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"packageManager":"pnpm@9.0.0"}',
        encoding="utf-8",
    )
    info = detect_test_runner(tmp_path)
    assert info.runner == "npm_test"
    assert info.command == "pnpm test"


def test_uses_typecheck_when_test_placeholder(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"echo \\"no tests\\"","typecheck":"tsc --noEmit"}}',
        encoding="utf-8",
    )
    info = detect_test_runner(tmp_path)
    assert info.runner == "npm_test"
    assert info.command == "npm run typecheck"

