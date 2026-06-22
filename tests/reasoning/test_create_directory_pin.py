"""P3 — create_directory suit le projet épinglé (mission) comme write_file.

Régression du piège « dossier vide » (log B 05:20:50) : create_directory créait
à la racine du workspace pendant que write_file épinglait dans un sous-dossier.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.reasoning.handlers.files import create_directory_handler
from src.tools.file_guardrails import WorkspaceFileGuardrails


@pytest.fixture(autouse=True)
def _clean_pin():
    WorkspaceFileGuardrails._pinned_project = None
    yield
    WorkspaceFileGuardrails._pinned_project = None


def _ctx(tmp_path):
    # file_guardrails=None → _assert_write_boundary est silencieux (mode test léger)
    return SimpleNamespace(runtime_root=tmp_path, file_guardrails=None)


def test_create_directory_honors_pin(tmp_path, monkeypatch):
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path / "workspace")

    WorkspaceFileGuardrails.pin_project("mission-ta-1")
    asyncio.run(create_directory_handler(_ctx(tmp_path), "mini-horloge"))

    today = datetime.now().strftime("%Y-%m-%d")
    expected = tmp_path / "workspace" / today / "projet-mission-ta-1" / "mini-horloge"
    assert expected.is_dir()
    # et PAS à la racine (l'ancien piège)
    assert not (tmp_path / "mini-horloge").exists()


def test_create_directory_without_pin_unchanged(tmp_path):
    # Hors mission → comportement normal (sous runtime_root).
    asyncio.run(create_directory_handler(_ctx(tmp_path), "mon-dossier"))
    assert (tmp_path / "mon-dossier").is_dir()
