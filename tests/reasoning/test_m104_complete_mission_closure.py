"""M104 - complete mission closure and operational proof invariants."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.documents import export_library_document_handler
from src.reasoning.handlers.system import run_command_handler
from src.reasoning.plan_progress import (
    final_fulfills_task,
    final_requires_operational_proof,
)
from src.subagents.mission_budget import mission_budget_finalize


@pytest.mark.parametrize(
    "description",
    [
        "Servir l'application et vérifier via browser_verify_local_project",
        "Valider le formulaire dans le navigateur",
        "Exécuter pytest puis confirmer les résultats",
        "Publier tous les livrables dans le workspace",
        "Générer le PDF via generate_studio_document",
    ],
)
def test_final_never_replaces_operational_proof(description):
    assert final_requires_operational_proof(description)
    assert not final_fulfills_task(description)


def test_final_still_fulfills_a_pure_user_report():
    assert not final_requires_operational_proof("Présenter le rapport à l'utilisateur")
    assert final_fulfills_task("Présenter le rapport à l'utilisateur")


@pytest.fixture
def handler_ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)


@pytest.mark.asyncio
async def test_background_server_that_exits_is_a_failure_and_not_a_preview(handler_ctx, tmp_path):
    class FakeManager:
        async def run_background(self, command, wait_ms_before_async, timeout_s):
            return "", None

    with (
        patch("src.tools.process_manager.get_process_manager", return_value=FakeManager()),
        patch("src.utils.local_preview.register_preview") as register_preview,
    ):
        result = await run_command_handler(
            handler_ctx,
            command="python app.py",
            cwd=str(tmp_path),
            background=True,
        )

    assert not result.success
    assert result.status_code == "background_server_exited"
    assert "browser_verify_local_project" in result.output
    assert "aucun processus" in result.output.lower()
    register_preview.assert_not_called()


@pytest.mark.asyncio
async def test_quick_non_server_background_command_reports_no_live_process(handler_ctx, tmp_path):
    class FakeManager:
        async def run_background(self, command, wait_ms_before_async, timeout_s):
            return "", None

    with patch("src.tools.process_manager.get_process_manager", return_value=FakeManager()):
        result = await run_command_handler(
            handler_ctx,
            command='python -c "print(1)"',
            cwd=str(tmp_path),
            background=True,
        )

    assert result.success
    assert "aucun processus" in result.output.lower()
    assert "terminée immédiatement" in result.output


@pytest.mark.asyncio
async def test_mission_document_export_guides_to_single_publication(tmp_path):
    record = SimpleNamespace(id="doc-1")

    class FakeLibrary:
        def resolve_reference(self, reference, allow_search=True):
            return record

    class FakeDelivery:
        def export_local(self, document_id, filename):
            return {"success": True, "proof": str(tmp_path / "report.pdf")}

    studio = SimpleNamespace(library=FakeLibrary(), delivery=FakeDelivery())
    ctx = SimpleNamespace(is_mission_run=True)
    with patch("src.documents.studio.get_document_studio", return_value=studio):
        result = await export_library_document_handler(
            ctx, "doc-1", "workspace/project/report.pdf"
        )

    payload = json.loads(result.output)
    assert result.success
    assert "publish_mission_workspace" in payload["mission_next_step"]
    assert "Ne répète pas export_library_document" in payload["mission_next_step"]


def test_deadline_steer_demands_complete_delivery_or_explicit_failure():
    decision = mission_budget_finalize({"has_deadline": True, "remaining_s": 0})
    assert decision and decision[0] == "finalize"
    text = decision[1]
    assert "COMPLET" in text
    assert "échec explicite" in text
    assert "livraison incomplète comme un succès" in text


def test_react_operational_guard_cannot_relax_or_deadline_bypass():
    """M104 : ni `_mission_relax` ni `_deadline_finalized` ne doivent permettre de
    conclure alors qu'une tâche à PREUVE opérationnelle reste ouverte.

    LOT Z6 (2026-08-15) — ce test vérifiait la chaîne littérale
    ``or bool(_operational_tasks_remaining)``. Le run « Écluse » a montré que ce
    court-circuit n'avait aucune borne : ``retry 18/3``, 18 refus de FINAL sur un
    plafond annoncé de 3, puis mort par épuisement à l'itération 66 — ni final, ni
    tâches faites, alors que la mission avait bel et bien sa preuve (valeur lue au
    DOM après un clic). Z6 lui a donné un plafond FINI.

    Le test vérifie désormais l'INTENTION qu'il porte dans son nom, et non une
    formulation : le terme opérationnel participe toujours au blocage, et les deux
    voies de contournement visées par M104 restent fermées. Ce qui protège contre
    « une livraison incomplète présentée comme un succès » n'est de toute façon pas
    ce compteur — ce sont les verrous de vérité (publication, tests verts, constat
    mesuré), qui eux exigent des PREUVES et non de la patience.
    """
    source = (Path(__file__).parents[2] / "src" / "reasoning" / "react.py").read_text(
        encoding="utf-8"
    )
    assert "and not _operational_tasks_remaining" in source

    # Le terme opérationnel bloque toujours le FINAL prématuré…
    site = source.rindex("[PLAN GUARD] FINAL premature bloque")
    condition = source[site - 3000 : site]
    assert "bool(_operational_tasks_remaining)" in condition
    # …mais désormais sous un plafond, et non plus sans limite.
    assert "_PLAN_GUARD_MAX_RETRIES_OPERATIONAL" in condition

    # Cœur de M104 : les deux contournements nommés restent interdits.
    assert "and not _mission_relax" in condition
    assert "and not _deadline_finalized" in condition
