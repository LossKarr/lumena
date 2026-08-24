from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return text, parsed


def test_each_main_push_triggers_a_release_but_bot_bumps_do_not_recurse() -> None:
    text, _ = _workflow()
    header = text.split("permissions:", 1)[0]
    assert re.search(r"(?m)^\s+push:\s*$", header)
    assert "branches: [main, master]" in header
    assert "github-actions[bot]" in text
    assert "[skip ci]" in text


def test_release_is_visible_immediately_then_assets_are_certified_downstream() -> None:
    _, workflow = _workflow()
    jobs = workflow["jobs"]
    assert jobs["release"]["needs"] == "prepare"
    assert set(jobs["publish"]["needs"]) == {"prepare", "release", "lint", "test", "package"}
    assert set(jobs["package"]["needs"]) == {"prepare", "lint", "test"}
    assert jobs["publish"]["environment"] == "production"


def test_failed_unpublished_bump_is_reused_instead_of_skipping_a_version() -> None:
    text, _ = _workflow()
    assert "refs/tags/v{current}" in text
    assert 'new = f"{major}.{minor}.{patch + 1}" if tag_exists else current' in text


def test_full_windows_regression_allows_cold_lsp_startup() -> None:
    text, _ = _workflow()
    assert "python -m pytest tests/ --timeout=60 --tb=short -q" in text
    assert "python -m pytest tests/ --timeout=15" not in text


def test_lint_gate_covers_updater_without_being_blocked_by_unrelated_legacy_debt() -> None:
    text, _ = _workflow()
    assert "Lint certified updater surface" in text
    assert "src/runtime/update_service.py" in text
    assert "scripts/lumena_updater.py" in text
    assert "ruff check src/ web/ scripts/ tests/" not in text


def test_release_uploads_every_required_asset_and_attests_the_update() -> None:
    text, _ = _workflow()
    for name in (
        "lumena-update-windows-x64.zip",
        "lumena-update-windows-x64.zip.sha256",
        "update-manifest.json",
        "release-certification.json",
    ):
        assert name in text
    assert "attest-build-provenance@" in text


def test_every_external_action_is_pinned_to_a_commit_sha() -> None:
    text, _ = _workflow()
    uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", text)
    assert uses
    for value in uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", value), value


def test_private_installer_stays_fully_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"(?m)^installer/$", ignore)
    assert "!installer/" not in ignore


def test_release_workflow_never_builds_or_reads_the_private_installer() -> None:
    text, _ = _workflow()
    lowered = text.lower()
    assert "installer/" not in lowered
    assert "lumena_setup.iss" not in lowered
    assert "iscc" not in lowered
    assert ".exe" not in lowered
