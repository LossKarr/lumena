"""CONN-5C-2 - commande validee en mission (`ide__command_run`).

Decision du 15 septembre 2026 : pas de terminal interactif. Une commande par appel,
dans le dossier de mission, jugee par le sanitizer, G1 et le refus de l'interactif
(lot PROCESS-M), empreinte recalculee par Electron, execution par le gestionnaire
de taches de 5C-1 donc preuve verifiee. `terminal_run` est interdit en mission par
le contrat lui-meme.
"""
from __future__ import annotations

import sys

import pytest

from src.reasoning import ide_mission_execution as module_execution
from src.reasoning.caller_context import REACT
from src.reasoning.handlers.system import _mission_destructive_target_violation
from src.reasoning.ide_mission_execution import command_digest, mission_command_shell
from src.tools.ide_semantics import ide_contract
from src.utils.command_sanitizer import sanitize_chained_command
from src.utils.interactive_commands import interactive_launch
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi
from tests.reasoning.test_conn5c1_ide_mission_execution import (
    ROOT, FauxIDE, _prete, cache_d_effets_neuf as cache_d_effets_neuf,
)
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry
from tests.tools.test_conn3c_ide_capabilities import service as service


class FauxIDECommande(FauxIDE):
    """Faux Electron : `command_run` devient la tache synthetique du shell de la plateforme."""

    async def send_command(self, action, params, timeout=30.0, *, resume_transport=None, expected_snapshot=None,
                           mission_scope=None):
        if action != "command_run":
            return await super().send_command(action, params, timeout, resume_transport=resume_transport,
                                              expected_snapshot=expected_snapshot, mission_scope=mission_scope)
        self.actions.append(action)
        if mission_scope is not None:
            self.perimetres.append(mission_scope)
        executable, args = mission_command_shell(params["command"])
        self.taches = [{"id": "command", "executable": executable, "args": args}]
        return self._lancer("task_run", {"taskId": "command"})


def _attendu(commande):
    executable, args = mission_command_shell(commande)
    return command_digest(source="command", task_id="command", executable=executable, args=args,
                          cwd_relative=".", script=None)


# ══════════════════════════════════════════════════════════════════════════
#  1. Contrat
# ══════════════════════════════════════════════════════════════════════════


def test_contrat_de_command_run():
    contrat = ide_contract("command_run")
    assert contrat["semantics"] == {
        "model_exposure": "contextual", "effect": "PROCESS_LAUNCH", "proof_capabilities": ["process_launch"],
        "risk_floor": "system", "confirmation": "policy", "mission_policy": "scoped",
        "idempotency": "non_replayable", "sensitive_fields": ["command"],
    }
    assert contrat["input_schema"]["required"] == ["command"]
    assert contrat["input_schema"]["additionalProperties"] is False


def test_terminal_run_interdit_en_mission_par_le_contrat():
    assert ide_contract("terminal_run")["semantics"]["mission_policy"] == "forbidden"


@pytest.mark.parametrize("plateforme,executable,args", [
    ("win32", "cmd.exe", ["/d", "/s", "/c", '"python -m pytest -q"']),
    ("linux", "/bin/sh", ["-c", "python -m pytest -q"]),
    ("darwin", "/bin/sh", ["-c", "python -m pytest -q"]),
])
def test_shell_de_la_plateforme(plateforme, executable, args):
    assert mission_command_shell("python -m pytest -q", plateforme) == (executable, args)


# ══════════════════════════════════════════════════════════════════════════
#  2. Commande admise
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_commande_admise_empreinte_preuve_et_cache_libere(registry, service, owner, cache_d_effets_neuf):
    commande = "python -m pytest -q"
    assert sanitize_chained_command(commande)[0] is True and interactive_launch(commande) == ""
    root = _prete(registry, service)
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": commande}, caller=REACT)
    assert observation.success is True, observation.content
    assert ide.actions[0] == "command_run" and "operation_get" in ide.actions
    assert "task_list" not in ide.actions
    assert ide.perimetres == [{"task_id": "task_worker", "role": "worker", "allowed_files": ["README.md"],
                               "execution": {"kind": "command", "id": "command",
                                             "command_sha256": _attendu(commande)}}]
    assert observation.execution_evidence is not None and observation.execution_evidence.exit_code == 0
    assert cache_d_effets_neuf.snapshot()[1] is False


@pytest.mark.asyncio
async def test_commande_en_echec_pas_de_succes(registry, service, owner):
    root = _prete(registry, service)
    FauxIDECommande(service, root, statut="failed", code=2)
    observation = await registry.execute("ide__command_run", {"command": "python -m pytest"}, caller=REACT)
    assert observation.success is False
    assert observation.execution_evidence is not None and observation.execution_evidence.exit_code == 2


@pytest.mark.asyncio
async def test_sans_fin_observee_annulation(registry, service, owner, monkeypatch):
    monkeypatch.setattr(module_execution, "_wait_seconds", lambda _ctx: 0.2)
    monkeypatch.setattr(module_execution, "POLL_INTERVAL_S", 0.05)
    root = _prete(registry, service)
    ide = FauxIDECommande(service, root, jamais_fini=True)
    observation = await registry.execute("ide__command_run", {"command": "python -m pytest"}, caller=REACT)
    assert "ide_mission_execution_timeout" in observation.content
    assert ide.annulations == [{"operationId": "transport:" + ide.lance["operation"], "confirmed": True}]


# ══════════════════════════════════════════════════════════════════════════
#  3. Refus, avec les verdicts natifs verifies d'abord
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_refus_du_sanitizer_natif(registry, service, owner):
    commande = "rimraf ../src"
    assert sanitize_chained_command(commande)[0] is False
    root = _prete(registry, service)
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": commande}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_command_refused - ")
    assert ide.actions == []


@pytest.mark.asyncio
async def test_refus_de_g1_native(registry, service, owner):
    commande = f"del {ROOT / 'pytest.ini'}"
    root = _prete(registry, service)
    assert sanitize_chained_command(commande)[0] is True
    assert _mission_destructive_target_violation(registry._v2_context, commande) != ""
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": commande}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_command_refused - ")
    assert ide.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("commande", ["cmd", "python", "powershell -NoExit -Command dir", "echo a && node"])
async def test_refus_du_lancement_interactif(registry, service, owner, commande):
    assert interactive_launch(commande) != ""
    root = _prete(registry, service)
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": commande}, caller=REACT)
    assert observation.content == "IDE: ide_mission_command_interactive"
    assert ide.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("commande", ["", "   ", "echo a\nrimraf ../src", "echo a\rdir", "echo \x00", "x" * 8193])
async def test_commande_invalide(registry, service, owner, commande):
    root = _prete(registry, service)
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": commande}, caller=REACT)
    assert observation.content == "IDE: ide_mission_command_invalid"
    assert ide.actions == []


@pytest.mark.asyncio
async def test_version_3_refusee_apres_les_gardes(registry, service, owner):
    root = _prete(registry, service, version=3)
    ide = FauxIDECommande(service, root)
    observation = await registry.execute("ide__command_run", {"command": "python -m pytest"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_unsupported"
    refus = await registry.execute("ide__command_run", {"command": "cmd"}, caller=REACT)
    assert refus.content == "IDE: ide_mission_command_interactive"
    assert ide.actions == []


@pytest.mark.asyncio
async def test_terminal_run_refuse_en_mission(registry, service, owner):
    _prete(registry, service)
    send = _envoi(service)
    observation = await registry.execute("ide__terminal_run", {"command": "dir"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_policy_forbidden"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_command_run_ferme(registry, service, owner):
    send = _envoi(service)
    observation = await registry.execute("ide__command_run", {"command": "python -m pytest"})
    assert observation.content == "IDE: ide_host_authorization_not_connected"
    send.assert_not_awaited()


def test_plateforme_courante_par_defaut():
    assert mission_command_shell("x") == mission_command_shell("x", sys.platform)
