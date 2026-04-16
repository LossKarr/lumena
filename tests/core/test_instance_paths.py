"""Tests P1.6e — Instance ID auto-generation + validate_instance_dirs()."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


# ── ensure_instance_id ────────────────────────────────────────────────────


def test_ensure_instance_id_returns_existing(monkeypatch, tmp_path):
    """Si LUMENA_INSTANCE_ID est déjà défini, il est retourné tel quel."""
    from importlib import reload
    import src.utils.paths as paths_mod

    monkeypatch.setenv("LUMENA_INSTANCE_ID", "my-fixed-id")
    result = paths_mod.ensure_instance_id(env_file=tmp_path / ".env")
    assert result == "my-fixed-id"


def test_ensure_instance_id_generates_uuid(monkeypatch, tmp_path):
    """Sans LUMENA_INSTANCE_ID, un UUID-4 valide est généré."""
    from src.utils.paths import ensure_instance_id

    monkeypatch.delenv("LUMENA_INSTANCE_ID", raising=False)
    # Forcer la re-lecture en remettant "default"
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")

    result = ensure_instance_id(env_file=tmp_path / ".env")
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_ensure_instance_id_writes_env(monkeypatch, tmp_path):
    """L'ID généré est écrit dans le fichier .env fourni."""
    from src.utils.paths import ensure_instance_id

    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")
    env_file = tmp_path / ".env"
    env_file.write_text("LUMENA_ADMIN_TOKEN=test\n", encoding="utf-8")

    result = ensure_instance_id(env_file=env_file)
    content = env_file.read_text(encoding="utf-8")
    assert f"LUMENA_INSTANCE_ID={result}" in content


def test_ensure_instance_id_does_not_duplicate(monkeypatch, tmp_path):
    """Si LUMENA_INSTANCE_ID est déjà dans le .env, on ne le duplique pas."""
    from src.utils.paths import ensure_instance_id

    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")
    env_file = tmp_path / ".env"
    env_file.write_text("LUMENA_INSTANCE_ID=existing-id\nOTHER=val\n", encoding="utf-8")

    ensure_instance_id(env_file=env_file)
    content = env_file.read_text(encoding="utf-8")
    assert content.count("LUMENA_INSTANCE_ID") == 1


def test_ensure_instance_id_no_crash_on_missing_env_file(monkeypatch, tmp_path):
    """Pas d'exception si le fichier .env n'existe pas encore."""
    from src.utils.paths import ensure_instance_id

    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")
    env_file = tmp_path / "missing" / ".env"  # dossier inexistant

    # Ne doit pas lever
    result = ensure_instance_id(env_file=env_file)
    assert isinstance(result, str) and len(result) > 0


# ── validate_instance_dirs ────────────────────────────────────────────────


def test_validate_instance_dirs_creates_dirs(monkeypatch, tmp_path):
    """validate_instance_dirs crée les répertoires critiques."""
    from src.utils import paths as paths_mod

    # Override les constantes critiques pour pointer vers tmp_path
    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths_mod, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(paths_mod, "LOGS_DIR", tmp_path / "data" / "logs")
    monkeypatch.setattr(paths_mod, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(paths_mod, "OPS_DIR", tmp_path / "data" / "ops")
    monkeypatch.setattr(paths_mod, "MEMORY_DIR", tmp_path / "data" / "memory")
    monkeypatch.setattr(paths_mod, "JOURNAL_DIR", tmp_path / "data" / "memory" / "journal")
    monkeypatch.setattr(paths_mod, "ALERTS_DIR", tmp_path / "data" / "alerts")
    monkeypatch.setattr(paths_mod, "MAIL_DIR", tmp_path / "data" / "mail")
    monkeypatch.setattr(paths_mod, "RECEIVED_IMAGES_DIR", tmp_path / "data" / "received_images")
    monkeypatch.setattr(paths_mod, "RECEIVED_DOCS_DIR", tmp_path / "data" / "received_documents")
    monkeypatch.setattr(
        paths_mod,
        "_CRITICAL_DIRS",
        (
            tmp_path / "data",
            tmp_path / "workspace",
            tmp_path / "backups",
        ),
    )

    errors = paths_mod.validate_instance_dirs(create=True)
    assert errors == []
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "backups").is_dir()


def test_validate_instance_dirs_returns_errors_when_no_create(tmp_path):
    """Sans create=True, les dirs manquantes sont signalées sans les créer."""
    from src.utils import paths as paths_mod
    import src.utils.paths as pm

    missing = tmp_path / "nonexistent" / "deeply" / "nested"
    orig = pm._CRITICAL_DIRS
    try:
        pm._CRITICAL_DIRS = (missing,)
        errors = pm.validate_instance_dirs(create=False)
        assert len(errors) == 1
        assert str(missing) in errors[0]
    finally:
        pm._CRITICAL_DIRS = orig


def test_validate_instance_dirs_no_exception_on_permission_error(monkeypatch):
    """validate_instance_dirs ne lève pas d'exception même si mkdir échoue."""
    from src.utils import paths as paths_mod

    def _bad_mkdir(self, *a, **kw):
        raise PermissionError("no write")

    import pathlib
    monkeypatch.setattr(pathlib.Path, "mkdir", _bad_mkdir)

    # Ne doit pas lever
    errors = paths_mod.validate_instance_dirs(create=True)
    assert isinstance(errors, list)
