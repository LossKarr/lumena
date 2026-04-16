"""Tests pour src/utils/persistence.py (P1.3.1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.persistence import atomic_write_json, atomic_write_text, safe_read_json, _quarantine_file


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    def test_creates_file(self, tmp_path: Path):
        target = tmp_path / "state.json"
        atomic_write_json(target, {"key": "value"})
        assert target.exists()

    def test_content_correct(self, tmp_path: Path):
        target = tmp_path / "state.json"
        data = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        atomic_write_json(target, data)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dir(self, tmp_path: Path):
        target = tmp_path / "subdir" / "deep" / "state.json"
        atomic_write_json(target, {"x": 1})
        assert target.exists()

    def test_no_tmp_file_left_after_success(self, tmp_path: Path):
        target = tmp_path / "state.json"
        atomic_write_json(target, {"x": 1})
        tmp = target.with_suffix(".json.tmp")
        assert not tmp.exists()

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "state.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["v"] == 2

    def test_unicode_preserved(self, tmp_path: Path):
        target = tmp_path / "state.json"
        atomic_write_json(target, {"msg": "éàü中文"}, ensure_ascii=False)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["msg"] == "éàü中文"

    def test_update_catalogue_noop_no_error(self, tmp_path: Path):
        target = tmp_path / "state.json"
        # update_catalogue=True ne doit pas lever d'exception (P1.3.4 no-op)
        atomic_write_json(target, {"x": 1}, update_catalogue=True)
        assert target.exists()

    def test_raises_on_non_serializable(self, tmp_path: Path):
        target = tmp_path / "state.json"
        with pytest.raises((TypeError, ValueError)):
            atomic_write_json(target, {"bad": object()})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# safe_read_json
# ---------------------------------------------------------------------------

class TestSafeReadJson:
    def test_reads_valid_file(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text(json.dumps({"k": 42}), encoding="utf-8")
        result = safe_read_json(target)
        assert result == {"k": 42}

    def test_returns_default_if_absent(self, tmp_path: Path):
        target = tmp_path / "missing.json"
        result = safe_read_json(target, default={"fallback": True})
        assert result == {"fallback": True}

    def test_returns_empty_dict_if_absent_no_default(self, tmp_path: Path):
        target = tmp_path / "missing.json"
        result = safe_read_json(target)
        assert result == {}

    def test_corrupted_json_returns_default(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{invalid json!!!", encoding="utf-8")
        result = safe_read_json(target, default={"safe": True}, quarantine=False)
        assert result == {"safe": True}

    def test_corrupted_json_quarantined(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{broken", encoding="utf-8")
        result = safe_read_json(target, quarantine=True)
        # Fichier corrompu -> defaut retourne
        assert result == {}
        # Quarantaine cree
        quarantine_dir = tmp_path / ".quarantine"
        assert quarantine_dir.exists()
        quarantined_files = list(quarantine_dir.iterdir())
        assert len(quarantined_files) == 1
        assert quarantined_files[0].name.startswith("bad_")

    def test_quarantined_removes_original(self, tmp_path: Path):
        """La quarantaine est un vrai move: l'original doit disparaitre."""
        target = tmp_path / "bad.json"
        target.write_text("{broken", encoding="utf-8")
        safe_read_json(target, quarantine=True)
        assert not target.exists()

    def test_quarantined_stable_second_read(self, tmp_path: Path):
        """Apres quarantaine, un deuxieme appel retourne default sans creer une 2e copie."""
        target = tmp_path / "bad.json"
        target.write_text("{broken", encoding="utf-8")
        safe_read_json(target, quarantine=True)
        # Deuxieme appel: fichier absent -> retour default, pas de 2e copie quarantine
        result2 = safe_read_json(target, default={"second": True}, quarantine=True)
        assert result2 == {"second": True}
        quarantine_dir = tmp_path / ".quarantine"
        assert len(list(quarantine_dir.iterdir())) == 1  # toujours 1 seule copie

    def test_quarantined_file_contains_original_content(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        original = "{{really broken"
        target.write_text(original, encoding="utf-8")
        safe_read_json(target, quarantine=True)
        quarantine_dir = tmp_path / ".quarantine"
        quarantined = list(quarantine_dir.iterdir())[0]
        assert quarantined.read_text(encoding="utf-8") == original

    def test_no_quarantine_when_disabled(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{bad", encoding="utf-8")
        safe_read_json(target, quarantine=False)
        quarantine_dir = tmp_path / ".quarantine"
        assert not quarantine_dir.exists()

    def test_roundtrip_with_atomic_write(self, tmp_path: Path):
        target = tmp_path / "state.json"
        data = {"version": 1, "items": [1, 2, 3]}
        atomic_write_json(target, data)
        result = safe_read_json(target)
        assert result == data


# ---------------------------------------------------------------------------
# _quarantine_file (interne)
# ---------------------------------------------------------------------------

class TestQuarantineFile:
    def test_creates_quarantine_dir(self, tmp_path: Path):
        path = tmp_path / "data.json"
        _quarantine_file(path, '{"test": 1}')
        assert (tmp_path / ".quarantine").is_dir()

    def test_quarantine_file_has_correct_suffix(self, tmp_path: Path):
        path = tmp_path / "ops_state.json"
        _quarantine_file(path, "bad")
        files = list((tmp_path / ".quarantine").iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".json"
        assert files[0].stem.startswith("ops_state_")


# ---------------------------------------------------------------------------
# atomic_write_text  (P1.1.3)
# ---------------------------------------------------------------------------

class TestAtomicWriteText:
    def test_creates_file(self, tmp_path: Path):
        target = tmp_path / "note.md"
        atomic_write_text(target, "# Hello")
        assert target.exists()

    def test_content_correct(self, tmp_path: Path):
        target = tmp_path / "note.md"
        content = "# Titre\n\nContenu avec accents éàü et unicode 中文."
        atomic_write_text(target, content)
        assert target.read_text(encoding="utf-8") == content

    def test_creates_parent_dir(self, tmp_path: Path):
        target = tmp_path / "deep" / "sub" / "note.md"
        atomic_write_text(target, "test")
        assert target.exists()

    def test_no_tmp_file_left_after_success(self, tmp_path: Path):
        target = tmp_path / "note.md"
        atomic_write_text(target, "content")
        # Le fichier .tmp doit avoir été supprimé après rename
        leftovers = [p for p in tmp_path.iterdir() if ".tmp" in p.name]
        assert leftovers == []

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "note.md"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2")
        assert target.read_text(encoding="utf-8") == "v2"

    def test_original_intact_if_tmp_exists(self, tmp_path: Path):
        """Si un .tmp traine (crash précédent), l'original reste lisible."""
        target = tmp_path / "state.md"
        original_content = "# Original intact"
        atomic_write_text(target, original_content)
        # Simuler un .tmp résiduel (crash précédent)
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text("# Contenu corrompu partiel", encoding="utf-8")
        # Une nouvelle écriture doit réussir et overwrite le .tmp
        atomic_write_text(target, "# Nouvelle version")
        assert target.read_text(encoding="utf-8") == "# Nouvelle version"
        assert not tmp.exists()

    def test_empty_string_allowed(self, tmp_path: Path):
        target = tmp_path / "empty.md"
        atomic_write_text(target, "")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == ""

    def test_custom_encoding(self, tmp_path: Path):
        target = tmp_path / "latin.txt"
        atomic_write_text(target, "café", encoding="latin-1")
        assert target.read_text(encoding="latin-1") == "café"
