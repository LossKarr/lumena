"""Tests — persistance de LUMENA_INSTANCE_ID (fix doublons de pairs).

Bug corrigé : un `LUMENA_INSTANCE_ID=` VIDE (ou "default") dans .env n'était
jamais remplacé → l'identité changeait à chaque boot → doublons de pairs +
auto-jumelage avec soi-même. ensure_instance_id doit REMPLACER la ligne.
"""
import re

import pytest

from src.utils import paths as paths_mod


def _read_id(env_file):
    text = env_file.read_text(encoding="utf-8")
    m = re.search(r'(?m)^\s*LUMENA_INSTANCE_ID\s*=(.*)$', text)
    return m.group(1).strip() if m else None


def test_empty_id_line_is_replaced(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nLUMENA_INSTANCE_ID=\nBAZ=qux\n", encoding="utf-8")
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")  # env vide → doit générer
    new_id = paths_mod.ensure_instance_id(env_file=env)
    assert new_id and new_id != "default"
    # la ligne vide a été remplacée (pas dupliquée), uuid persisté
    written = _read_id(env)
    assert written == new_id
    text = env.read_text(encoding="utf-8")
    assert text.count("LUMENA_INSTANCE_ID=") == 1   # pas de doublon de ligne
    assert "FOO=bar" in text and "BAZ=qux" in text   # le reste préservé


def test_default_id_is_replaced(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LUMENA_INSTANCE_ID=default\n", encoding="utf-8")
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "default")
    new_id = paths_mod.ensure_instance_id(env_file=env)
    assert new_id != "default"
    assert _read_id(env) == new_id


def test_stable_id_is_preserved(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LUMENA_INSTANCE_ID=abc-stable-123\n", encoding="utf-8")
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "abc-stable-123")
    out = paths_mod.ensure_instance_id(env_file=env)
    assert out == "abc-stable-123"  # déjà stable → inchangé
    assert _read_id(env) == "abc-stable-123"


def test_missing_key_is_appended(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")
    new_id = paths_mod.ensure_instance_id(env_file=env)
    text = env.read_text(encoding="utf-8")
    assert f"LUMENA_INSTANCE_ID={new_id}" in text
    assert "FOO=bar" in text


def test_idempotent_across_calls(tmp_path, monkeypatch):
    """Deux 'boots' successifs → MÊME id (plus de changement à chaque restart)."""
    env = tmp_path / ".env"
    env.write_text("LUMENA_INSTANCE_ID=\n", encoding="utf-8")
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "")
    id1 = paths_mod.ensure_instance_id(env_file=env)
    # simulate next boot: env loaded from the (now fixed) file
    monkeypatch.setenv("LUMENA_INSTANCE_ID", _read_id(env))
    id2 = paths_mod.ensure_instance_id(env_file=env)
    assert id1 == id2  # stable
