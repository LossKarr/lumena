"""LOT 2.10 (2026-07-08) — le périmètre d'écriture d'une mission ne fuit JAMAIS
d'une tâche à la suivante sur le SubAgent singleton persistant.

Cause racine (run MesRecettes, mission n°3) : le CodeAgent, réutilisé de mission en
mission, gardait `_allowed_files = {'cli.py'}` posé par le worker `w_cli` de TempConv.
La mission suivante (fanout_tasks, SANS périmètre) héritait de ce vieux périmètre → les
3 CodeAgents ne pouvaient plus écrire les `.html` → 50 itérations de contournements.

Invariant visé (général, PAS le cas MesRecettes) : l'état de mission est strictement
task-scoped — toute contrainte posée par une tâche meurt avec elle.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.agents.sub_agent import SubAgent, AgentType, _write_within_perimeter


def _agent() -> SubAgent:
    return SubAgent(agent_type=AgentType.CODE, name="CodeAgent")


# ═══════════ 2.10 — reset task-scoped de l'état de mission ═══════════════════════


class TestPerimeterResetHelper:
    def test_reset_clears_armed_perimeter(self):
        """Un périmètre armé (tâche mission N) est effacé par le reset (avant tâche N+1)."""
        a = _agent()
        a._allowed_files = frozenset({"cli.py"})
        a._task_workspace_root = Path(tempfile.mkdtemp())
        a._reset_task_scoped_state()
        assert a._allowed_files is None
        assert a._task_workspace_root is None

    def test_reset_idempotent_on_clean_agent(self):
        """Reset sur agent neuf (hors mission) : reste None, zéro effet de bord."""
        a = _agent()
        a._reset_task_scoped_state()
        assert a._allowed_files is None
        assert a._task_workspace_root is None


class TestNoLeakAcrossTasks:
    """Le cœur du bug : tâche 1 bornée à ['cli.py'], tâche 2 SANS périmètre doit
    pouvoir écrire n'importe quel fichier (le vieux périmètre ne s'applique plus)."""

    def test_perimeter_of_task1_does_not_bind_task2(self):
        a = _agent()

        # Tâche 1 (worker de mission w_cli) : périmètre ['cli.py'].
        a._allowed_files = frozenset({"cli.py"})
        assert _write_within_perimeter("index.html", a._allowed_files) is False
        assert _write_within_perimeter("cli.py", a._allowed_files) is True

        # Frontière de tâche : le reset (appelé en tête d'execute) purge tout.
        a._reset_task_scoped_state()

        # Tâche 2 (fanout, aucun allowed_files fourni) : écriture .html LIBRE.
        assert _write_within_perimeter("index.html", a._allowed_files) is True
        assert _write_within_perimeter("recette1.html", a._allowed_files) is True
        assert _write_within_perimeter("recette2.html", a._allowed_files) is True


class TestIntraMissionStillBounded:
    """Non-régression : DANS une mission (périmètre fourni), le garde borne toujours."""

    def test_armed_perimeter_still_refuses_out_of_scope(self):
        a = _agent()
        a._allowed_files = frozenset({"convert.py"})
        # fichier d'un autre worker → refusé
        assert _write_within_perimeter("cli.py", a._allowed_files) is False
        # fichier assigné → autorisé
        assert _write_within_perimeter("convert.py", a._allowed_files) is True


class TestExecuteCallsResetAnchor:
    """Garde-fou de câblage : execute() appelle bien le reset en tête de tâche
    (le comportement bout-en-bout passe par le runtime des runs)."""

    def test_execute_invokes_reset(self):
        src = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")
        assert "_reset_task_scoped_state" in src
        # appelé dans execute (pas seulement défini)
        assert src.count("_reset_task_scoped_state") >= 2
