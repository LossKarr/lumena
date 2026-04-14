from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.skills.sync import sync_skills_main


def _write_skill(skill_dir: Path, name: str, description: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "keywords: [test]\n"
            "---\n\n"
            f"# {name}\n\n"
            "Instructions.\n"
        ),
        encoding="utf-8",
    )


def test_sync_imports_16_skills_and_resync_stays_non_destructive(tmp_path: Path):
    source = tmp_path / "skills-main" / "skills"
    destination = tmp_path / "lumena" / "skills"
    manifest = tmp_path / "lumena" / "data" / "skills_sync_manifest.json"

    names = [f"skill-{i:02d}" for i in range(1, 17)]
    for name in names:
        _write_skill(source / name, name, f"Description {name}")

    first = sync_skills_main(source_path=source, destination_path=destination, manifest_path=manifest)
    assert first["errors_count"] == 0
    assert first["updated_count"] == 16
    assert len([p for p in destination.iterdir() if p.is_dir()]) == 16

    second = sync_skills_main(source_path=source, destination_path=destination, manifest_path=manifest)
    assert second["errors_count"] == 0
    assert len([p for p in destination.iterdir() if p.is_dir()]) == 16
    assert manifest.exists()

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_path"] == str(source.resolve())
    assert payload["destination_path"] == str(destination.resolve())
    assert sorted(payload["skills"]) == sorted(names)


def test_sync_collision_preserves_local_skill_without_marker(tmp_path: Path):
    source = tmp_path / "skills-main" / "skills"
    destination = tmp_path / "lumena" / "skills"
    manifest = tmp_path / "lumena" / "data" / "skills_sync_manifest.json"

    _write_skill(source / "pdf", "pdf", "PDF upstream")
    _write_skill(source / "xlsx", "xlsx", "XLSX upstream")

    local_pdf_dir = destination / "pdf"
    local_pdf_dir.mkdir(parents=True, exist_ok=True)
    local_payload = "---\nname: pdf\ndescription: local\n---\n\nLOCAL\n"
    (local_pdf_dir / "SKILL.md").write_text(local_payload, encoding="utf-8")

    result = sync_skills_main(source_path=source, destination_path=destination, manifest_path=manifest)
    assert "pdf" in result["conflicts"]
    assert "pdf" in result["skipped"]
    assert "xlsx" in result["updated"]

    current_pdf = (local_pdf_dir / "SKILL.md").read_text(encoding="utf-8")
    assert current_pdf == local_payload
