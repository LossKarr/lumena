"""LOT J — le périmètre d'écriture du CodeAgent devient étanche.

Run `NoteFlow` (2026-08-13, `task_bf091429…`) : SaaS multi-utilisateur, 5 workers.
Le code a RÉUSSI — **12/12 tests verts**, auth hachée, sessions, isolation A/B — et
la mission est morte à la publication :

    Publication refusee : bundle web contractuel incoherent.
    sources_non_declarees: static/style.css, test_run_desktop.py

Deux fichiers parasites, créés par le CodeAgent en dehors de son périmètre, ont fait
refuser la publication. Aucune voie de suppression n'existant (`delete_file` →
policy, `Remove-Item` → verbe interdit, `del` → pattern dangereux, `delegate_task`
→ cassé), le livrable a été perdu.

**Les deux fuites sont d'origine** : `tool_registry.py` et `sub_agent.py` sont
identiques à la ligne près dans la version du 9 août (celle du benchmark 0/4).
Ce lot ne répare pas une régression — il ferme deux trous jamais exercés avant,
parce qu'aucune mission assez complexe n'avait été tentée.

Le choix de conception : **empêcher la création plutôt qu'autoriser la
suppression**. Aucun parasite ⇒ rien à nettoyer ⇒ aucune capacité de destruction
à ouvrir.
"""
from __future__ import annotations

from src.agents.sub_agent import _action_write_paths, _write_within_perimeter


# ── J-b : le bon nom au mauvais endroit n'est pas le bon fichier ─────────────

_TEMPLATES = ["noteflow/templates/base.html", "noteflow/static/style.css"]


def test_the_exact_parasite_of_the_run_is_refused():
    """`static/style.css` passait parce que `style.css` était autorisé sous
    `noteflow/static/`. C'est ce doublon qui a bloqué la publication."""
    assert _write_within_perimeter("static/style.css", _TEMPLATES) is False


def test_the_legitimate_path_still_passes():
    assert _write_within_perimeter("noteflow/static/style.css", _TEMPLATES) is True


def test_a_bare_name_keeps_its_tolerance():
    """Comportement HISTORIQUE à préserver : les workers émettent réellement
    `app.py` pour `noteflow/app.py`. Le casser ferait échouer des missions saines."""
    assert _write_within_perimeter("test_app.py", ["tests/test_app.py"]) is True
    assert _write_within_perimeter("app.py", ["noteflow/app.py"]) is True


def test_an_exact_path_passes():
    assert _write_within_perimeter("tests/test_app.py", ["tests/test_app.py"]) is True
    assert _write_within_perimeter("./tests/test_app.py", ["tests/test_app.py"]) is True


def test_a_foreign_directory_is_refused():
    allowed = ["tests/test_app.py"]
    assert _write_within_perimeter("backend/app.py", allowed) is False
    assert _write_within_perimeter("frontend/index.html", allowed) is False


def test_the_same_name_in_another_directory_is_refused():
    """Le cœur du lot : même basename, dossier différent."""
    assert _write_within_perimeter("backup/test_app.py", ["tests/test_app.py"]) is False
    assert _write_within_perimeter("a/b/style.css", ["static/style.css"]) is False


def test_outside_a_mission_nothing_changes():
    """NON-RÉGRESSION cœur : sans périmètre, le CodeAgent est strictement inchangé."""
    assert _write_within_perimeter("anything/at/all.py", None) is True
    assert _write_within_perimeter("backend/app.py", []) is True


def test_an_absolute_path_is_resolved_then_matched(tmp_path):
    (tmp_path / "tests").mkdir()
    allowed = ["tests/test_app.py"]
    owned = str(tmp_path / "tests" / "test_app.py")
    foreign = str(tmp_path / "backend" / "app.py")
    assert _write_within_perimeter(owned, allowed, workspace_root=tmp_path) is True
    assert _write_within_perimeter(foreign, allowed, workspace_root=tmp_path) is False


# ── J-a : le périmètre lit les chemins des patchs textuels ──────────────────
# `apply_patch` (singulier) ne porte AUCUN chemin en clé : le CodeAgent lit
# `action["patch"]` et le chemin vit dans le texte, sous `*** Add File:`.
# L'extracteur ne lisait que les clés → il rendait [] → le garde, pourtant branché
# sur `apply_patch`, n'avait rien à refuser.

_RUN_PATCH = (
    "*** Begin Patch\n"
    "*** Add File: test_run_desktop.py\n"
    "+def test_post_edit_hook():\n"
    "+    assert True\n"
    "*** End Patch"
)


def test_the_path_of_the_run_patch_is_extracted():
    assert _action_write_paths({"action": "apply_patch", "patch": _RUN_PATCH}) == [
        "test_run_desktop.py"
    ]


def test_that_patch_is_now_refused_by_the_perimeter():
    """Bout en bout : le patch exact qui a créé le fichier fantôme."""
    paths = _action_write_paths({"action": "apply_patch", "patch": _RUN_PATCH})
    allowed = ["noteflow/tests/test_app.py"]
    assert [p for p in paths if not _write_within_perimeter(p, allowed)] == [
        "test_run_desktop.py"
    ]


def test_update_and_delete_markers_are_covered():
    assert _action_write_paths({"patch": "*** Update File: noteflow/app.py"}) == [
        "noteflow/app.py"
    ]
    assert _action_write_paths({"patch": "*** Delete File: vieux.py"}) == ["vieux.py"]


def test_several_files_in_one_patch():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "*** Update File: b/c.py\n"
        "*** End Patch"
    )
    assert _action_write_paths({"patch": patch}) == ["a.py", "b/c.py"]


def test_classic_keys_are_unaffected():
    """NON-RÉGRESSION : les formes déjà couvertes doivent rendre la même chose."""
    assert _action_write_paths({"path": "a.py"}) == ["a.py"]
    assert _action_write_paths({"file": "b.py"}) == ["b.py"]
    assert _action_write_paths({"patches": [{"file": "c.py"}]}) == ["c.py"]


def test_garbage_never_raises():
    assert _action_write_paths(None) == []
    assert _action_write_paths({}) == []
    assert _action_write_paths({"patch": ""}) == []
    assert _action_write_paths({"patch": 123}) == []
    assert _action_write_paths({"patch": "*** Add File:"}) == []


# ── J-c : on ne force jamais un appel invalide ──────────────────────────────
# `run_tests` avec un `test_path` vide échoue toujours, et ce message est un
# marqueur de NON-EXÉCUTION → « Livraison refusée ». Au run, le CodeAgent est mort
# en 0.0s trois fois de suite, supprimant le dernier recours de la mission.

def _resolve(agent, args):
    """Reproduit la résolution posée dans `_call_tool` (point de passage unique)."""
    if not str(args.get("test_path") or "").strip():
        ws = str(getattr(agent, "_task_workspace_root", "") or "").strip()
        if ws:
            return {**args, "test_path": ws}
    return args


class _Agent:
    def __init__(self, ws=None):
        self._task_workspace_root = ws


def test_an_empty_test_path_falls_back_to_the_task_workspace():
    out = _resolve(_Agent("C:/ws/missions/task_x"), {"test_path": ""})
    assert out["test_path"] == "C:/ws/missions/task_x"


def test_without_a_workspace_nothing_is_fabricated():
    """Pas de chemin inventé : l'appelant garde l'erreur claire d'origine."""
    assert _resolve(_Agent(None), {"test_path": ""})["test_path"] == ""


def test_an_explicit_path_is_never_overwritten():
    out = _resolve(_Agent("C:/ws/missions/task_x"), {"test_path": "tests/x.py"})
    assert out["test_path"] == "tests/x.py"


def test_the_call_tool_resolution_is_wired():
    """Le correctif doit vivre au point de passage unique, pas dans un seul site
    d'appel — plusieurs chemins produisent cet appel."""
    import inspect

    from src.agents.sub_agent import CodeAgent

    src = inspect.getsource(CodeAgent._call_tool)
    assert "run_tests" in src and "_task_workspace_root" in src
