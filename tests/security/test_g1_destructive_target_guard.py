"""LOT G1 — une commande destructive ne peut plus viser un fichier du dépôt.

Incident fondateur (2026-08-12, mission T1 de la campagne) : pour contourner un
conflit de configuration pytest, un CodeAgent a exécuté

    del C:\\Users\\charl\\Desktop\\lumena\\pytest.ini     → exit 0

supprimant un fichier du dépôt de l'utilisateur. `Rename-Item` sur la même cible
avait été bloqué (verbe PowerShell interdit) ; `pyproject.toml` n'a survécu que
parce que l'agent n'a tenté sur lui qu'un renommage. **Pur hasard de séquence.**

Cause : le sanitizer juge la DANGEROSITÉ d'une commande (`rm -rf`, `del /s /f /q`
sont bloqués) mais jamais la PROPRIÉTÉ de sa cible. `del rapport.md` dans le
dossier de mission est légitime ; `del <dépôt>/pytest.ini` ne l'est pas. Même
commande, même verdict — jusqu'ici.

G1 ajoute la dimension manquante. Il est **additif** (rien n'est retiré à
l'allowlist ni aux patterns bloqués), **mission-only** et **conservateur** : tout
doute laisse passer, car sur-bloquer casserait les missions.
"""
from __future__ import annotations

import types

from src.utils.command_sanitizer import destructive_command_target_violation as violation
from src.utils.paths import ROOT_DIR

REPO = ROOT_DIR.as_posix()
MISSION = REPO + "/workspace/missions/task_abc"
BS = chr(92)  # antislash, pour écrire des chemins Windows sans échappement


def _v(cmd: str) -> str:
    return violation(cmd, mission_root=MISSION, repo_root=REPO)


# ── Ce qui DOIT être bloqué ──────────────────────────────────────────────────

def test_the_real_incident_is_blocked():
    """La commande exacte qui a supprimé pytest.ini."""
    cmd = "del C:" + BS + "Users" + BS + "charl" + BS + "Desktop" + BS + "lumena" + BS + "pytest.ini"
    assert _v(cmd).endswith("pytest.ini")


def test_forward_slashes_are_equivalent():
    assert _v("del C:/Users/charl/Desktop/lumena/pytest.ini")


def test_powershell_remove_item_on_repo_file():
    assert _v("Remove-Item -Path C:/Users/charl/Desktop/lumena/pyproject.toml")


def test_relative_escape_out_of_the_mission():
    """`..` qui remonte du dossier de mission jusqu'au dépôt."""
    assert _v("erase ../../../pytest.ini")


def test_moving_a_repo_file_counts_as_destroying_it():
    """Déplacer `core.py` équivaut à le supprimer de sa place."""
    assert _v("move C:/Users/charl/Desktop/lumena/core.py old.py")


def test_rename_of_repo_file_is_blocked():
    assert _v("ren C:/Users/charl/Desktop/lumena/pytest.ini pytest.ini.bak")


def test_repo_source_tree_is_protected():
    assert _v("rm C:/Users/charl/Desktop/lumena/src/reasoning/react.py")


def test_chained_command_is_inspected_too():
    """Le verbe destructif peut être en seconde position d'un chaînage."""
    assert _v("echo hello && del C:/Users/charl/Desktop/lumena/pytest.ini")


def test_every_shell_flavour_is_covered():
    """Le point de l'incident : changer de shell ne doit plus contourner."""
    target = "C:/Users/charl/Desktop/lumena/pytest.ini"
    for verb in ("del", "erase", "rm", "rmdir", "rd", "Remove-Item", "unlink"):
        assert _v(f"{verb} {target}"), f"{verb} devrait être bloqué"


# ── G1.b — l'enrobage ne doit plus contourner le garde ───────────────────────
#
# La v1 n'inspectait que le PREMIER mot de chaque sous-commande. Cinq
# contournements triviaux passaient — trouvés en testant mon propre garde contre
# lui-même, pas par un test qui l'aurait supposé bon.

def test_python_interpreter_cannot_smuggle_a_deletion():
    assert _v("python -c \"import os; os.remove('" + REPO + "/pytest.ini')\"")


def test_python_shutil_rmtree_on_repo_source():
    assert _v("python -c \"import shutil; shutil.rmtree('" + REPO + "/src')\"")


def test_node_interpreter_cannot_smuggle_a_deletion():
    assert _v("node -e \"require('fs').unlinkSync('" + REPO + "/pytest.ini')\"")


def test_powershell_command_wrapper_is_covered():
    assert _v('powershell -Command "del ' + REPO + '/pytest.ini"')


def test_cmd_c_wrapper_is_covered():
    assert _v("cmd /c del " + REPO + "/pytest.ini")


def test_overwrite_by_redirection_is_destruction_too():
    """Écraser un fichier du dépôt le détruit aussi sûrement que `del`."""
    assert _v("echo config vide > " + REPO + "/pytest.ini")
    assert _v("echo x >> " + REPO + "/pytest.ini")


# ── Ce qui NE DOIT JAMAIS être bloqué ────────────────────────────────────────

def test_mission_own_files_stay_deletable():
    """Un worker doit pouvoir nettoyer ses propres fichiers."""
    assert _v("del rapport.md") == ""
    assert _v("del data/inscrits.json") == ""
    assert _v("rm -f build/output.tmp") == ""


def test_mission_tests_directory_is_not_the_repo_one():
    """Piège : `tests/` en relatif désigne les tests DE LA MISSION, pas ceux du
    dépôt. Les confondre bloquerait tous les workers de test."""
    assert _v("rm tests/test_app.py") == ""


def test_non_destructive_commands_are_never_touched():
    """Même en visant le dépôt : lire, tester, inspecter reste libre."""
    assert _v("python -m pytest C:/Users/charl/Desktop/lumena/tests") == ""
    assert _v("git status") == ""
    assert _v("node --check static/script.js") == ""
    assert _v("type C:/Users/charl/Desktop/lumena/pytest.ini") == ""


def test_paths_outside_the_repo_are_not_our_business():
    """Les autres gardes (allowlist, BLOCKED_PATTERNS) s'en chargent ; G1 ne
    protège que le dépôt."""
    assert _v("del C:/Windows/Temp/scratch.txt") == ""


def test_option_flags_are_not_mistaken_for_paths():
    assert _v("Remove-Item -Recurse -Force build/") == ""


def test_redirection_inside_the_mission_is_fine():
    """Un worker écrit ses propres logs : la redirection n'est pas suspecte en soi."""
    assert _v("python app.py > output.log") == ""
    assert _v("pytest -q > rapport/tests.txt") == ""


# ── Inertie : hors mission, aucun effet ──────────────────────────────────────

def test_inert_without_mission_root():
    """Chat, CodeAgent direct, autonomie : comportement strictement inchangé."""
    assert violation("del C:/Users/charl/Desktop/lumena/pytest.ini",
                     mission_root=None, repo_root=REPO) == ""


def test_inert_without_repo_root():
    assert violation("del C:/Users/charl/Desktop/lumena/pytest.ini",
                     mission_root=MISSION, repo_root=None) == ""


def test_empty_and_garbage_inputs_never_raise():
    assert violation("", mission_root=MISSION, repo_root=REPO) == ""
    assert violation(None, mission_root=MISSION, repo_root=REPO) == ""
    assert violation("del", mission_root=MISSION, repo_root=REPO) == ""
    assert violation("   ", mission_root=MISSION, repo_root=REPO) == ""


# ── Le branchement dans run_command_handler ──────────────────────────────────

def _ctx(*, mission: bool, sub: str = "workspace/missions/task_abc", guardrails=True):
    from src.utils.paths import ROOT_DIR

    fg = None
    if guardrails:
        fg = types.SimpleNamespace(_workspace_root=lambda: ROOT_DIR)
    return types.SimpleNamespace(
        is_mission_run=mission,
        mission_workspace_subdir=lambda: sub,
        file_guardrails=fg,
    )


def _repo_file_cmd() -> str:
    """Commande visant un fichier du dépôt depuis le dossier de mission."""
    return "del ../../../pytest.ini"


def test_handler_guard_fires_in_mission():
    from src.reasoning.handlers.system import _mission_destructive_target_violation as g
    assert g(_ctx(mission=True), _repo_file_cmd())


def test_handler_guard_is_silent_outside_missions():
    from src.reasoning.handlers.system import _mission_destructive_target_violation as g
    assert g(_ctx(mission=False), _repo_file_cmd()) == ""


def test_mission_without_workspace_is_still_protected():
    """G1.c — trouvé par le TEST RÉEL, pas par un test unitaire.

    `mission_workspace` n'est attribué que par `write_mission_contract` /
    `delegate_and_wait`. Une mission SIMPLE n'en a aucun — c'est le cas le plus
    courant, et il était sans protection : la sentinelle du test réel a bien été
    supprimée. Sans dossier de mission, le périmètre autorisé devient le
    workspace global ; le dépôt reste protégé.
    """
    from src.reasoning.handlers.system import _mission_destructive_target_violation as g
    cmd = 'del "' + REPO.replace("/", BS) + BS + 'pytest.ini"'
    assert g(_ctx(mission=True, sub=""), cmd), "mission sans workspace = trou béant"
    assert g(_ctx(mission=True, guardrails=False), cmd)


def test_workspace_files_remain_deletable_without_mission_dir():
    """Contrepartie : sans dossier de mission, Lumena doit garder la main sur le
    workspace, sinon on casse toutes les missions simples."""
    from src.reasoning.handlers.system import _mission_destructive_target_violation as g
    from src.utils.paths import WORKSPACE_DIR
    cmd = 'del "' + str(WORKSPACE_DIR).replace(BS, "/") + '/2026-08-12/note.md"'
    assert g(_ctx(mission=True, sub=""), cmd) == ""


def test_handler_guard_never_raises_on_broken_context():
    from src.reasoning.handlers.system import _mission_destructive_target_violation as g

    class _Explodes:
        @property
        def is_mission_run(self):
            raise RuntimeError("contexte cassé")

    assert g(_Explodes(), "del x") == ""
