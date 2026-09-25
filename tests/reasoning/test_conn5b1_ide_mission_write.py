"""CONN-5B-1 - ecriture de contenu en mission par l'IDE.

Invariant central : une ecriture IDE en mission est jugee par les MEMES gardes
que `write_file` natif, appeles sur la MEME cible (`resolve_write_target`). Les
tests de refus lancent donc la meme ecriture par les deux voies et exigent le
meme verdict - jamais un verdict ecrit a la main qui pourrait diverger du code
natif.

Deuxieme invariant : une ecriture admise produit une preuve VERIFIEE (relecture
disque, hash, cible attendue) qui rejoint le ledger. Audit du 14/09 :
`verify_execution` n'etait appele que dans les tests et la sonde du canari.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.execution_guards import structured_observation_success
from src.reasoning.execution_observation_runtime import record_tool_observation
from src.reasoning.handlers.files import write_file_handler
from src.runtime.execution_ledger import ExecutionLedger
from src.tools.ide_protocol import TRANSPORT_VERSION
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.tools.test_conn3c_ide_capabilities import service as service

REFUS = "IDE: ide_mission_write_refused"


def _horodatage() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ide_qui_ecrit(service, *, falsifier=False):
    """Faux IDE fidele a Electron : cible confinee au dossier ouvert, ecriture,
    relecture, preuve `verifiedFileWrite` et liaison de transport authentifiee."""
    appels = []

    async def send_command(action, params, timeout=30.0, *, resume_transport=None, expected_snapshot=None,
                           mission_scope=None):
        appels.append((action, dict(params)))
        session = service.bridge._negotiated
        brut = Path(params["path"])
        cible = brut if brut.is_absolute() else Path(session.workspace_path) / brut
        debut = _horodatage()
        avant = hashlib.sha256(cible.read_bytes()).hexdigest() if cible.is_file() else None
        cible.parent.mkdir(parents=True, exist_ok=True)
        # Electron ecrit les octets exacts : pas de conversion de fin de ligne.
        cible.write_bytes(params["content"].encode("utf-8"))
        relu = cible.read_bytes()
        reel = str(cible.resolve())
        preuve = {
            "schema_version": 1, "kind": "file_write", "target": reel,
            "before_sha256": avant,
            "after_sha256": ("0" * 64) if falsifier else hashlib.sha256(relu).hexdigest(),
            "bytes": len(relu), "reread": True, "observed_at": _horodatage(),
        }
        return {
            "success": True, "path": reel, "proof": preuve,
            "execution": {"schema_version": 1, "status": "succeeded",
                          "started_at": debut, "completed_at": _horodatage()},
            "_transport": {
                "transport_version": TRANSPORT_VERSION, "session_id": session.session_id,
                "instance_id": session.instance_id, "catalogue_revision": session.catalogue_hash,
                "request_id": "request-5b1", "operation_id": uuid.uuid4().hex, "sequence": 1,
                "workspace_id": session.workspace_id,
            },
        }

    service.bridge.send_command = send_command
    return appels


def _preparer(registry, service, **mission):
    orch, root = _mission(registry, **mission)
    _ide_ouverte_sur(service, root)
    # CONN-5B-2 : une ecriture de mission exige une IDE negociee en version 4.
    service.bridge._negotiated = replace(service.bridge._negotiated, protocol=4)
    return orch, root


async def _ecrire(registry, chemin, contenu="print('ok')\n"):
    return await registry.execute("ide__write_file", {"path": chemin, "content": contenu}, caller=REACT)


# ══════════════════════════════════════════════════════════════════════════
#  1. ECRITURE ADMISE : creation par le worker proprietaire, preuve verifiee
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_cree_son_fichier_avec_preuve_verifiee(registry, service, owner):
    _, root = _preparer(registry, service, allowed=("app.py",))
    appels = _ide_qui_ecrit(service)
    observation = await _ecrire(registry, "app.py")

    assert observation.success is True
    assert len(appels) == 1 and appels[0] == ("write_file", {"path": "app.py", "content": "print('ok')\n"})
    assert (root / "app.py").read_text(encoding="utf-8") == "print('ok')\n"

    preuve = observation.execution_evidence
    assert preuve is not None and preuve.success
    assert preuve.effect.value == "FILE_WRITE"
    assert Path(preuve.target) == (root / "app.py").resolve()
    assert structured_observation_success(observation, "ide__write_file") is True

    ledger = ExecutionLedger()
    entree = record_tool_observation(ledger, iteration=1, name="ide__write_file",
                                     args={"path": "app.py"}, observation=observation)
    assert entree.evidence is preuve


@pytest.mark.asyncio
async def test_preuve_falsifiee_ne_prouve_rien(registry, service, owner):
    """L'IDE dit « reussi » avec un hash faux : l'observation reste sans preuve."""
    _preparer(registry, service, allowed=("app.py",))
    appels = _ide_qui_ecrit(service, falsifier=True)
    observation = await _ecrire(registry, "app.py")
    # L'ecriture doit etre PARTIE : sinon « pas de preuve » serait vrai trivialement.
    assert len(appels) == 1 and observation.success is True
    assert observation.execution_evidence is None
    assert structured_observation_success(observation, "ide__write_file") is False


@pytest.mark.asyncio
async def test_lease_pris_sur_la_cible_canonique(registry, service, owner, monkeypatch):
    import src.subagents.resource_lease as lease_module

    _, root = _preparer(registry, service, allowed=("app.py",))
    _ide_qui_ecrit(service)
    cles = []

    class Enregistreur:
        @asynccontextmanager
        async def hold(self, key, *, timeout=None):
            cles.append(key)
            yield

    monkeypatch.setattr(lease_module, "get_resource_lease", lambda: Enregistreur())
    observation = await _ecrire(registry, "app.py")
    assert observation.success is True
    assert cles == ["files:" + os.path.normcase(str((root / "app.py").resolve()))]


# ══════════════════════════════════════════════════════════════════════════
#  2. PARITE DES REFUS AVEC WRITE_FILE NATIF
# ══════════════════════════════════════════════════════════════════════════


def _contrat(root: Path, fichiers):
    (root / "contract.json").write_text(json.dumps({"files": fichiers}), encoding="utf-8")


def _scenario(nom, registry):
    """Rend (orchestrateur, dossier, chemin) pour une situation que le natif refuse."""
    if nom == "hors_perimetre_du_worker":
        orch, root = _mission(registry, allowed=("app.py",))
        return orch, root, "autre.py"
    if nom == "fichier_existant":
        orch, root = _mission(registry, allowed=("app.py",))
        (root / "app.py").write_text("ancien\n", encoding="utf-8")
        return orch, root, "app.py"
    if nom == "contract_json_a_la_main":
        orch, root = _mission(registry, task_id="task_lead", parent=None, allowed=None)
        return orch, root, "contract.json"
    if nom == "test_contractuel_A3":
        orch, root = _mission(registry, task_id="task_lead", parent=None, allowed=None)
        _contrat(root, [{"path": "tests/test_app.py", "owner": "w_tests"}])
        return orch, root, "tests/test_app.py"
    if nom == "fichier_d_un_worker_vivant_H3":
        orch, root = _mission(registry, task_id="task_lead", parent=None, allowed=None)
        _contrat(root, [{"path": "app.py", "owner": "w_back"}])
        orch.start_task(conversation_id="conversation", channel="web", message_preview="worker",
                        metadata={"kind": "mission", "parent_id": "task_lead", "delegation_owner": "w_back"},
                        task_id="task_w_back")
        return orch, root, "app.py"
    if nom == "zone_protegee":
        orch, root = _mission(registry, task_id="task_lead", parent=None, allowed=None)
        return orch, root, ".env"
    raise AssertionError(nom)


@pytest.mark.asyncio
@pytest.mark.parametrize("nom,code", [
    ("hors_perimetre_du_worker", REFUS), ("fichier_existant", REFUS), ("contract_json_a_la_main", REFUS),
    ("test_contractuel_A3", REFUS), ("fichier_d_un_worker_vivant_H3", REFUS),
    # `.env` n'entre pas dans le workspace : resolve_write_target le place a la RACINE de
    # Lumena. Le natif le refuse par la liste noire (le vrai .env), l'IDE parce que la
    # cible sort du dossier de mission. Meme verdict, raison propre a chaque voie.
    ("zone_protegee", "IDE: ide_mission_target_outside"),
])
async def test_meme_verdict_que_write_file_natif(registry, service, owner, nom, code):
    _, root, chemin = _scenario(nom, registry)
    _ide_ouverte_sur(service, root)
    appels = _ide_qui_ecrit(service)
    avant = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    natif = await write_file_handler(registry._v2_context, path=chemin, content="nouveau\n")
    assert natif.success is False, f"situation mal construite : le natif accepte ({nom})"
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == avant

    observation = await _ecrire(registry, chemin, "nouveau\n")
    assert observation.success is False
    assert observation.content.startswith(code), observation.content
    assert appels == []
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == avant


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUI RESTE FERME
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cible_hors_du_dossier_de_mission_refusee(registry, service, owner):
    """Plus strict que le lead natif, qui peut ecrire ailleurs dans le workspace."""
    _, root = _preparer(registry, service, task_id="task_lead", parent=None, allowed=None)
    appels = _ide_qui_ecrit(service)
    ailleurs = root.parent / "ailleurs.py"
    observation = await _ecrire(registry, str(ailleurs))
    assert observation.content == "IDE: ide_mission_target_outside"
    assert appels == [] and not ailleurs.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("nom,args", [
    ("ide__sidebar_create_file", {"path": "vide.py"}),
    ("ide__sidebar_create_folder", {"path": "dossier"}),
    ("ide__editor_save", {}),
    ("ide__sidebar_delete", {"path": "app.py"}),
])
async def test_autres_mutations_restent_fermees(registry, service, owner, nom, args):
    _preparer(registry, service, allowed=("app.py", "vide.py", "dossier"))
    send = _envoi(service)
    observation = await registry.execute(nom, args, caller=REACT)
    assert observation.content == "IDE: ide_mission_mutation_not_connected"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_write_file_reste_ferme(registry, service, owner):
    send = _envoi(service)
    observation = await registry._ide_tools.execute(
        "ide__write_file", {"path": "notes.txt", "content": "x"}, caller=REACT,
    )
    assert observation.content == "IDE: ide_host_authorization_not_connected"
    send.assert_not_awaited()
