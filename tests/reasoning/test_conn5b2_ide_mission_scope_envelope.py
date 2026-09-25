"""CONN-5B-2 - le provider transmet le perimetre de mission, et seulement en version 4.

En version 3, une ecriture de mission est refusee `ide_mission_scope_unsupported`
juste avant l'envoi : APRES les gardes natifs, pour qu'une ecriture interdite reste
refusee pour sa vraie raison et non pour « IDE trop ancienne ».
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.reasoning.caller_context import REACT
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.reasoning.test_conn5b1_ide_mission_write import _ide_qui_ecrit
from tests.tools.test_conn3c_ide_capabilities import service as service


def _protocole(service, version):
    service.bridge._negotiated = replace(service.bridge._negotiated, protocol=version)


def _ide_enregistreuse(service, version):
    """Faux IDE ecrivant comme Electron, qui enregistre le perimetre recu."""
    appels = _ide_qui_ecrit(service)
    ecrire = service.bridge.send_command
    perimetres = []

    async def send_command(action, params, timeout=30.0, *, resume_transport=None, expected_snapshot=None,
                           mission_scope=None):
        perimetres.append(mission_scope)
        return await ecrire(action, params, timeout, resume_transport=resume_transport,
                            expected_snapshot=expected_snapshot, mission_scope=mission_scope)

    service.bridge.send_command = send_command
    _protocole(service, version)
    return appels, perimetres


@pytest.mark.asyncio
async def test_ecriture_de_mission_refusee_face_a_une_ide_en_version_3(registry, service, owner):
    _, root = _mission(registry, allowed=("app.py",))
    _ide_ouverte_sur(service, root)
    appels, perimetres = _ide_enregistreuse(service, 3)
    observation = await registry.execute("ide__write_file", {"path": "app.py", "content": "x"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_unsupported"
    assert appels == [] and perimetres == []
    assert not (root / "app.py").exists()


@pytest.mark.asyncio
async def test_worker_transmet_son_perimetre_exact_en_version_4(registry, service, owner):
    _, root = _mission(registry, allowed=("app.py",))
    _ide_ouverte_sur(service, root)
    appels, perimetres = _ide_enregistreuse(service, 4)
    observation = await registry.execute("ide__write_file", {"path": "app.py", "content": "x"}, caller=REACT)
    assert observation.success is True and observation.execution_evidence is not None
    assert perimetres == [{"task_id": "task_worker", "role": "worker", "allowed_files": ["app.py"], "target": "app.py"}]


@pytest.mark.asyncio
async def test_lead_transmet_une_liste_vide(registry, service, owner):
    _, root = _mission(registry, task_id="task_lead", parent=None, allowed=None)
    _ide_ouverte_sur(service, root)
    _, perimetres = _ide_enregistreuse(service, 4)
    observation = await registry.execute("ide__write_file", {"path": "notes.md", "content": "x"}, caller=REACT)
    assert observation.success is True
    assert perimetres == [{"task_id": "task_lead", "role": "lead", "allowed_files": [], "target": "notes.md"}]


@pytest.mark.asyncio
async def test_sous_dossier_transmis_en_chemin_relatif_posix(registry, service, owner):
    # Pas `src/` : resolve_write_target envoie `src/...` a la RACINE de Lumena (son vrai
    # dossier de code), hors du dossier de mission ; l'IDE le refuse a juste titre.
    _, root = _mission(registry, allowed=("lib/app.py",))
    _ide_ouverte_sur(service, root)
    _, perimetres = _ide_enregistreuse(service, 4)
    observation = await registry.execute("ide__write_file", {"path": "lib/app.py", "content": "x"}, caller=REACT)
    assert observation.success is True
    assert perimetres[0]["target"] == "lib/app.py"


@pytest.mark.asyncio
async def test_un_garde_natif_passe_avant_la_version(registry, service, owner):
    """Ecriture interdite ET IDE en version 3 : le refus dit la vraie raison."""
    _, root = _mission(registry, allowed=("app.py",))
    _ide_ouverte_sur(service, root)
    _ide_enregistreuse(service, 3)
    observation = await registry.execute("ide__write_file", {"path": "autre.py", "content": "x"}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_write_refused")


@pytest.mark.asyncio
async def test_lecture_de_mission_sans_perimetre_dans_l_enveloppe(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    _protocole(service, 3)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.success is True
    assert send.await_args.kwargs.get("mission_scope") is None


@pytest.mark.asyncio
async def test_hors_mission_aucun_perimetre(registry, service, owner):
    _protocole(service, 4)
    send = _envoi(service, connected=True)
    observation = await registry.execute("ide__get_status", {})
    assert observation.success is True
    assert send.await_args.kwargs.get("mission_scope") is None
