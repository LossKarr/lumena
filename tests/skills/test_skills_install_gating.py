from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.skills.loader import SkillLoader


def _write_skill_archive(archive_path: Path, files: dict[str, str]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for name, content in files.items():
            zipf.writestr(name, content)


def test_install_skill_accepts_safe_archive(tmp_path: Path):
    archive = tmp_path / "safe.skill"
    _write_skill_archive(
        archive,
        {
            "safe-skill/SKILL.md": (
                "---\n"
                "name: safe-skill\n"
                "description: Safe test skill\n"
                "---\n\n"
                "Instructions\n"
            ),
            "safe-skill/scripts/safe-skill.py": "print('ok')\n",
        },
    )

    loader = SkillLoader(base_dirs=[tmp_path / "skills", tmp_path / "installed"])
    installed = loader.install_skill(archive, target_dir=tmp_path / "installed")

    assert installed is not None
    assert installed.name == "safe-skill"
    assert (tmp_path / "installed" / "safe-skill" / "SKILL.md").exists()


def test_install_skill_rejects_path_traversal(tmp_path: Path):
    archive = tmp_path / "evil-path.skill"
    _write_skill_archive(
        archive,
        {
            "../escape/SKILL.md": "---\nname: escape\n---\n",
        },
    )

    target = tmp_path / "installed"
    loader = SkillLoader(base_dirs=[tmp_path / "skills", target])
    installed = loader.install_skill(archive, target_dir=target)

    assert installed is None
    assert loader.last_install_error
    assert not (target / "escape" / "SKILL.md").exists()


def test_install_skill_rejects_blocked_extension(tmp_path: Path):
    archive = tmp_path / "evil-ext.skill"
    _write_skill_archive(
        archive,
        {
            "evil-skill/SKILL.md": (
                "---\n"
                "name: evil-skill\n"
                "description: Should be rejected\n"
                "---\n\n"
                "Instructions\n"
            ),
            "evil-skill/scripts/run.bat": "echo hacked\n",
        },
    )

    target = tmp_path / "installed"
    loader = SkillLoader(base_dirs=[tmp_path / "skills", target])
    installed = loader.install_skill(archive, target_dir=target)

    assert installed is None
    assert "interdit" in loader.last_install_error.lower()
    assert not (target / "evil-skill").exists()
