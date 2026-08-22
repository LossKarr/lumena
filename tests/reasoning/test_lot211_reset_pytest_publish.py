"""LOT 2.11 — tests dédiés A / B / E (run des 5 missions, 2026-07-08).

A : le rapport de fin (`_session_memory['edits_done']`) et les erreurs vues sont
    task-scoped — `_reset_task_scoped_state()` les remet à zéro (fini le « 📝
    Fichiers modifiés » d'une mission qui fuit dans la suivante).
B : un `pytest` nu à la RACINE Lumena en mission est refusé (collecte 16 000+
    tests → timeout garanti) ; cibler un dossier/fichier précis passe.
E : « publié dans workspace/X » sans dossier X sur disque = fausse publication
    → truth-lock rétrograde (immunise contre un `has_published` périmé).
"""

from __future__ import annotations

from pathlib import Path

from src.reasoning.final_guards import (
    apply_mission_truth_lock,
    published_target_missing_on_disk,
)
from src.reasoning.handlers.system import _is_broad_pytest_at_lumena_root


# ─────────────────────────────── LOT 2.11.A ────────────────────────────────
class _FakeCodeAgent:
    """Mime la surface touchée par `_reset_task_scoped_state` : les attributs
    task-scoped + le `_session_memory` (défini seulement sur CodeAgent)."""

    def __init__(self):
        self._allowed_files = {"cli.py"}
        self._task_workspace_root = "missions/tempconv"
        self._session_memory = {
            "files_read": ["a.py"],
            "edits_done": ["app.py: write_file"],
            "errors_seen": ["boom"],
        }


def _bound_reset(obj):
    """Rejoue la logique exacte de SubAgent._reset_task_scoped_state sur `obj`
    sans instancier tout l'agent (dépendances lourdes)."""
    from src.agents.sub_agent import SubAgent

    SubAgent._reset_task_scoped_state(obj)


def test_A_reset_clears_edits_and_errors_report():
    agent = _FakeCodeAgent()
    _bound_reset(agent)
    assert agent._allowed_files is None
    assert agent._task_workspace_root is None
    assert agent._session_memory["edits_done"] == []
    assert agent._session_memory["errors_seen"] == []


def test_A_reset_survives_without_session_memory():
    """SubAgent de base n'a PAS `_session_memory` → le reset ne doit pas crasher."""

    class _BareSub:
        def __init__(self):
            self._allowed_files = {"x.py"}
            self._task_workspace_root = "missions/x"

    bare = _BareSub()
    _bound_reset(bare)  # ne doit pas lever
    assert bare._allowed_files is None
    assert bare._task_workspace_root is None


# ─────────────────────────────── LOT 2.11.B ────────────────────────────────
def test_B_bare_pytest_at_root_is_refused(tmp_path):
    root = tmp_path
    assert _is_broad_pytest_at_lumena_root("python -m pytest -q", str(root), str(root)) is True
    assert _is_broad_pytest_at_lumena_root("pytest", str(root), str(root)) is True


def test_B_targeted_file_passes(tmp_path):
    root = tmp_path
    assert _is_broad_pytest_at_lumena_root(
        "pytest tests/test_app.py", str(root), str(root)
    ) is False
    assert _is_broad_pytest_at_lumena_root(
        "pytest tests/test_app.py::TestX", str(root), str(root)
    ) is False


def test_B_subdir_cwd_passes(tmp_path):
    root = tmp_path
    sub = tmp_path / "workspace" / "projet"
    sub.mkdir(parents=True)
    # cwd = dossier livrable (≠ racine) → collecte bornée → autorisé.
    assert _is_broad_pytest_at_lumena_root("pytest", str(sub), str(root)) is False


def test_B_non_pytest_command_ignored(tmp_path):
    root = tmp_path
    assert _is_broad_pytest_at_lumena_root("python app.py", str(root), str(root)) is False
    assert _is_broad_pytest_at_lumena_root("ls -la", str(root), str(root)) is False


# ─────────────────────────────── LOT 2.11.E ────────────────────────────────
def test_E_missing_target_is_detected(tmp_path):
    text = "✅ Livrable publié dans workspace/statsnotes/ avec succès."
    assert published_target_missing_on_disk(text, tmp_path) is True


def test_E_existing_nonempty_target_passes(tmp_path):
    d = tmp_path / "workspace" / "statsnotes"
    d.mkdir(parents=True)
    (d / "app.py").write_text("print('hi')", encoding="utf-8")
    text = "✅ Livrable publié dans workspace/statsnotes/ avec succès."
    assert published_target_missing_on_disk(text, tmp_path) is False


def test_E_empty_target_dir_is_still_missing(tmp_path):
    (tmp_path / "workspace" / "statsnotes").mkdir(parents=True)
    text = "Publié dans workspace/statsnotes/."
    assert published_target_missing_on_disk(text, tmp_path) is True


def test_E_date_named_workspace_is_ignored(tmp_path):
    # workspace/2026-07-08 = horodatage, pas une cible de livrable.
    text = "sauvegarde workspace/2026-07-08 effectuée"
    assert published_target_missing_on_disk(text, tmp_path) is False


def test_E_no_workspace_path_no_flag(tmp_path):
    assert published_target_missing_on_disk("Tout est publié.", tmp_path) is False


def test_E_negation_not_flagged(tmp_path):
    # Aveu honnête « pas encore publié » → jamais rétrogradé.
    text = "Le livrable workspace/statsnotes n'est pas encore publié."
    assert published_target_missing_on_disk(text, tmp_path) is False


def test_E_truth_lock_adds_banner_when_target_missing(tmp_path):
    final_text = "Mission terminée : livrable publié dans workspace/statsnotes/."
    guarded, _meta = apply_mission_truth_lock(
        final_text,
        has_green_test=True,
        has_browser_proof=True,
        has_published=True,  # flag PÉRIMÉ — le disque doit primer
        project_root=tmp_path,
    )
    assert "Non publié" in guarded


def test_E_truth_lock_silent_when_target_present(tmp_path):
    d = tmp_path / "workspace" / "statsnotes"
    d.mkdir(parents=True)
    (d / "app.py").write_text("x=1", encoding="utf-8")
    final_text = "Mission terminée : livrable publié dans workspace/statsnotes/."
    guarded, _meta = apply_mission_truth_lock(
        final_text,
        has_green_test=True,
        has_browser_proof=True,
        has_published=True,
        project_root=tmp_path,
    )
    assert "Non publié" not in guarded


def test_E_project_root_none_is_inert(tmp_path):
    """Appelants existants (project_root non passé) → comportement inchangé."""
    final_text = "livrable publié dans workspace/statsnotes/."
    guarded, _meta = apply_mission_truth_lock(
        final_text,
        has_green_test=True,
        has_browser_proof=True,
        has_published=True,  # flag présent → pas d'overclaim par le chemin has_published
    )
    assert "Non publié" not in guarded
