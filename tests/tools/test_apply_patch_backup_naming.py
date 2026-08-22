"""LOT C (run CoVoit'Éco 2026-07-04) — `.backups` ne casse plus la collecte pytest.

Bug : `apply_patch._backup_file` écrivait `.backups/<name>` EXACT. Pour
`tests/test_bookings.py` → `tests/.backups/test_bookings.py`, que pytest collectait
comme un 2e module `test_bookings` → `import file mismatch` → 0 test collecté (c'est
ce qui a mis les tests CoVoit'Éco à zéro). `_auto_backup_before_write` (write_file),
lui, timestampe déjà — l'incohérence était le bug.

C.1 : `_backup_file` timestampe (`<name>.<YYYYMMDD_HHMMSS>`) → jamais un `test_*.py`.
C.2 : `pytest.ini` exclut `.backups`/`.lumena_backups` de la récursion (défense).
"""
from __future__ import annotations

import configparser
import fnmatch
import subprocess
import sys
from pathlib import Path

from src.tools.apply_patch import _backup_file


# ── C.1 : le backup est timestampé et non collectable ─────────────────────────────

def test_backup_is_timestamped_not_exact_name(tmp_path):
    f = tmp_path / "test_bookings.py"
    f.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    _backup_file(f, f.read_text(encoding="utf-8"))

    backup_dir = tmp_path / ".backups"
    # le nom EXACT (ancien bug) ne doit PAS exister
    assert not (backup_dir / "test_bookings.py").exists()
    # un backup timestampé existe
    backups = list(backup_dir.glob("test_bookings.py.*"))
    assert len(backups) == 1
    assert not backups[0].name.endswith(".py")  # ne finit pas par .py


def test_backup_name_never_matches_pytest_glob(tmp_path):
    """Le nom du backup ne matche jamais `test_*.py` → pytest ne le collecte pas."""
    f = tmp_path / "test_x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _backup_file(f, "x = 1\n")
    name = next((tmp_path / ".backups").glob("test_x.py.*")).name
    assert not fnmatch.fnmatch(name, "test_*.py")
    # témoin : l'ancien nom exact, LUI, aurait matché (donc collision)
    assert fnmatch.fnmatch("test_x.py", "test_*.py")


def test_backup_content_preserved_and_recoverable(tmp_path):
    f = tmp_path / "test_y.py"
    original = "def test_orig():\n    assert 1 == 1\n"
    f.write_text(original, encoding="utf-8")
    _backup_file(f, original)
    recovered = next((tmp_path / ".backups").glob("test_y.py.*")).read_text(encoding="utf-8")
    assert recovered == original


# ── C.1 prouvé de bout en bout : pytest ne collecte plus le backup ────────────────

def test_pytest_collects_only_real_test_even_with_vulnerable_config(tmp_path):
    """Reproduction EXACTE CoVoit'Éco. Même sous un pytest.ini VULNÉRABLE (qui ne
    recurse PAS `.backups`), le backup timestampé (C.1) n'est pas collecté → plus
    d'`import file mismatch`, exactement 1 test collecté. Prouve que C.1 SEUL suffit."""
    proj = tmp_path / "proj"
    tests_dir = proj / "tests"
    tests_dir.mkdir(parents=True)
    sample = tests_dir / "test_sample.py"
    sample.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    # config VULNÉRABLE : norecursedirs vide → pytest recurse dans .backups
    (proj / "pytest.ini").write_text(
        "[pytest]\nnorecursedirs =\npython_files = test_*.py\n", encoding="utf-8")

    # simule un apply_patch sur le test → backup timestampé
    _backup_file(sample, sample.read_text(encoding="utf-8"))
    assert list((tests_dir / ".backups").glob("test_sample.py.*"))

    res = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(tests_dir)],
        cwd=str(proj), capture_output=True, text=True, timeout=120,
    )
    out = res.stdout + res.stderr
    assert "import file mismatch" not in out, out
    assert "test_sample.py::test_ok" in out, out


# ── C.2 : la config repo exclut bien les dossiers de backup ───────────────────────

def test_pytest_ini_excludes_backup_dirs():
    ini = Path(__file__).resolve().parents[2] / "pytest.ini"
    cfg = configparser.ConfigParser()
    cfg.read(ini, encoding="utf-8")
    norec = cfg["pytest"]["norecursedirs"].split()
    assert ".backups" in norec
    assert ".lumena_backups" in norec
