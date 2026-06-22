"""Fix scatter — un projet ÉPINGLÉ regroupe tous les fichiers d'une mission.

Régression runtime (log B 18:04-18:05) : sans pin, `notes.md` → `projet-notes`
et `README.md` → `projet-readme` (un livrable éclaté en 2 dossiers) → l'agent
boucle jusqu'au disjoncteur. Avec pin, tout va dans un seul `projet-…`.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.tools.file_guardrails import WorkspaceFileGuardrails


@pytest.fixture()
def g(tmp_path):
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None
    yield WorkspaceFileGuardrails(lumena_root=tmp_path)
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None


def _project_folder(path, today):
    parts = path.parts
    return parts[parts.index(today) + 1]


def test_sans_pin_les_md_scattent(g):
    today = datetime.now().strftime("%Y-%m-%d")
    p1 = g.get_workspace_path("memo-reseau/notes.md")
    p2 = g.get_workspace_path("memo-reseau/README.md")
    # comportement actuel : 2 dossiers projet différents (le bug)
    assert _project_folder(p1, today) == "projet-notes"
    assert _project_folder(p2, today) == "projet-readme"


def test_avec_pin_tout_dans_un_seul_dossier(g):
    today = datetime.now().strftime("%Y-%m-%d")
    with WorkspaceFileGuardrails.pinned_project("mission-ta-123"):
        p1 = g.get_workspace_path("memo-reseau/notes.md")
        p2 = g.get_workspace_path("memo-reseau/README.md")
        p3 = g.get_workspace_path("index.html")  # même un fichier web suit le pin
    assert _project_folder(p1, today) == "projet-mission-ta-123"
    assert _project_folder(p2, today) == "projet-mission-ta-123"
    assert _project_folder(p3, today) == "projet-mission-ta-123"
    # la structure relative est préservée
    assert p1.name == "notes.md" and p1.parent.name == "memo-reseau"


def test_pin_relache_apres_contexte(g):
    today = datetime.now().strftime("%Y-%m-%d")
    with WorkspaceFileGuardrails.pinned_project("mission-x"):
        pass
    assert WorkspaceFileGuardrails._pinned_project is None
    p = g.get_workspace_path("rapport.md")
    assert _project_folder(p, today) == "projet-rapport"  # dérivation normale revenue


def test_project_name_explicite_prime_sur_le_pin(g):
    today = datetime.now().strftime("%Y-%m-%d")
    with WorkspaceFileGuardrails.pinned_project("mission-x"):
        p = g.get_workspace_path("notes.md", project_name="mon-projet")
    assert _project_folder(p, today) == "projet-mon-projet"


def test_pin_normalise_le_nom(g):
    WorkspaceFileGuardrails.pin_project("Ma Mission_42")
    assert WorkspaceFileGuardrails._pinned_project == "projet-ma-mission-42"
    WorkspaceFileGuardrails.pin_project(None)
    assert WorkspaceFileGuardrails._pinned_project is None
