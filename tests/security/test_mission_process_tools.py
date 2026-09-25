"""Lot natif prealable a CONN-5C-2 - `process_run` / `process_input` en mission.

Mesure du 15 septembre 2026 : le sanitizer admet `cmd`, `powershell`, `pwsh`,
`python -i` et `node`. `process_run("cmd")` lance donc un shell, puis
`process_input` lui envoie n'importe quel texte SANS sanitizer ni G1 : toutes les
gardes de `run_command` sont contournees. `process_run` n'appliquait pas non plus G1.

En mission seulement :
- `process_run` applique G1 comme `run_command` (le sanitizer y est deja) et refuse
  le lancement d'un shell ou d'un interpreteur INTERACTIF ;
- `process_input` est refuse.
Hors mission, rien ne change.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.agents import process_input_handler, process_run_handler
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.system import _mission_destructive_target_violation
from src.utils.command_sanitizer import sanitize_chained_command
from src.utils.interactive_commands import interactive_launch
from src.utils.paths import ROOT_DIR

INTERACTIFS = [
    "cmd", "cmd.exe", "CMD /k", "C:/Windows/System32/cmd.exe", "powershell", "powershell -NoLogo",
    "pwsh -NoExit -Command Get-Date", "python", "python3 -u", "py", "python -i script.py",
    "python -W ignore", "node", "node -i", "node --interactive", "bash", "sh -i",
    "echo a && cmd", "npm test | python",
]
NON_INTERACTIFS = [
    "cmd /c dir", "cmd.exe /d /s /c call npm run build", "powershell -Command Get-Date",
    "pwsh -File build.ps1", "powershell -NoLogo -NonInteractive -Command dir", "python script.py",
    "python -m pytest", 'python -c "print(1)"', "python -u -W ignore script.py", "node build.mjs",
    'node -e "1"', "node -p 1+1", "bash -c ls", "sh build.sh", "npm run build", "pytest", "echo python",
    "git status",
]


@pytest.mark.parametrize("commande", INTERACTIFS)
def test_lancement_interactif_detecte(commande):
    assert interactive_launch(commande) != ""


@pytest.mark.parametrize("commande", NON_INTERACTIFS)
def test_commande_non_interactive_admise(commande):
    assert interactive_launch(commande) == ""


def test_le_sanitizer_admet_bien_les_shells_mesures():
    """Constat qui fonde le lot : sans ce lot, rien n'arrete ces lancements."""
    for commande in ("cmd", "powershell", "pwsh", "python -i", "node"):
        assert sanitize_chained_command(commande)[0] is True, commande


@pytest.fixture
def mission(tmp_path):
    ctx = HandlerContext.for_testing(lumena_root=ROOT_DIR, runtime_root=tmp_path / "workspace")
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task_process"
    ctx.mission_workspace = "missions/task_process"
    return ctx


@pytest.fixture
def hors_mission(tmp_path):
    return HandlerContext.for_testing(lumena_root=ROOT_DIR, runtime_root=tmp_path / "workspace")


def _gestionnaire():
    manager = MagicMock()
    manager.run_background = AsyncMock(return_value=("fini", None))
    manager.send_input = AsyncMock(return_value="Input envoye")
    module = MagicMock()
    module.get_process_manager = MagicMock(return_value=manager)
    return manager, patch.dict(sys.modules, {"src.tools.process_manager": module})


@pytest.mark.asyncio
@pytest.mark.parametrize("commande", ["cmd", "powershell", "python", "node", "echo a && cmd"])
async def test_mission_refuse_le_lancement_interactif(mission, commande):
    manager, modules = _gestionnaire()
    with modules:
        resultat = await process_run_handler(mission, command=commande)
    assert resultat.success is False
    assert "interactif" in resultat.output
    assert "run_command" in resultat.output
    manager.run_background.assert_not_awaited()


@pytest.mark.asyncio
async def test_mission_applique_g1_comme_run_command(mission):
    commande = f"del {ROOT_DIR / 'pytest.ini'}"
    assert sanitize_chained_command(commande)[0] is True
    assert _mission_destructive_target_violation(mission, commande) != ""
    manager, modules = _gestionnaire()
    with modules:
        resultat = await process_run_handler(mission, command=commande)
    assert resultat.success is False
    assert "pytest.ini" in resultat.output
    manager.run_background.assert_not_awaited()


@pytest.mark.asyncio
async def test_mission_commande_ordinaire_inchangee(mission):
    manager, modules = _gestionnaire()
    with modules:
        resultat = await process_run_handler(mission, command="python -m pytest")
    assert resultat.success is True
    manager.run_background.assert_awaited_once()


@pytest.mark.asyncio
async def test_mission_refuse_process_input(mission):
    manager, modules = _gestionnaire()
    with modules:
        resultat = await process_input_handler(mission, process_id="p1", text="del C:/x")
    assert resultat.success is False
    assert "mission" in resultat.output
    manager.send_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_shell_interactif_et_input_inchanges(hors_mission):
    manager, modules = _gestionnaire()
    with modules:
        lancement = await process_run_handler(hors_mission, command="cmd")
        entree = await process_input_handler(hors_mission, process_id="p1", text="dir")
    assert lancement.success is True and entree.success is True
    manager.run_background.assert_awaited_once()
    manager.send_input.assert_awaited_once()
