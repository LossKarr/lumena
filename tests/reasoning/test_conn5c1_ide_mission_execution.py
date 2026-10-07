"""CONN-5C-1 - execution validee en mission par l'IDE (taches et tests).

Invariants :

1. Une tache de mission est jugee par les MEMES gardes que `run_command` natif
   (`sanitize_chained_command` puis G1 `_mission_destructive_target_violation`),
   sur la ligne de commande ET sur le corps du script `package.json` - le vrai code
   lance par `npm run`. Les tests de refus exigent d'abord le verdict natif.
2. Ce qui a ete valide est ce qui part : empreinte de la commande dans
   `mission_scope.execution`, que Electron recalcule avant le lancement.
3. Une execution admise ne compte que par une preuve VERIFIEE, obtenue par le
   suivi interne `operation_get` du provider ; sans fin observee, pas de preuve et
   le processus lance est annule.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest

from src.reasoning import external_effect_cache as module_effets
from src.reasoning import ide_mission_execution as module_execution
from src.reasoning.external_effect_cache import ExternalEffectCache
from src.reasoning.caller_context import REACT
from src.reasoning.handlers.system import _mission_destructive_target_violation
from src.reasoning.ide_mission_execution import canonical_command, command_digest
from src.tools.ide_protocol import TRANSPORT_VERSION
from src.utils.command_sanitizer import sanitize_chained_command
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.reasoning.test_conn5b2_ide_mission_scope_envelope import _protocole
from tests.tools.test_conn3c_ide_capabilities import service as service

ROOT = Path(__file__).resolve().parents[2]
VECTEURS = ROOT / "tests" / "fixtures" / "conn5c1CommandDigestVectors.json"


@pytest.fixture(autouse=True)
def cache_d_effets_neuf(monkeypatch):
    """Le cache d'effets externes est global au processus : un lancement jamais
    termine (delai) le suspend volontairement ; il ne doit pas deborder sur les
    autres fichiers de tests (mesure : 2 echecs de `test_destructive_write_guard`)."""
    cache = ExternalEffectCache()
    monkeypatch.setattr(module_effets, "_effects", cache)
    return cache


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


SCRIPT_ADMIS = "node scripts/build.mjs"


def _tache_package(root, nom="build", script=SCRIPT_ADMIS, **champs):
    (root / "package.json").write_text(json.dumps({"scripts": {nom: script}}), encoding="utf-8")
    tache = {"id": f"package:{nom}", "label": nom, "group": "build", "source": "package",
             "commandPreview": f"npm run {nom}", "executable": "cmd.exe",
             "args": ["/d", "/s", "/c", f"call npm run {nom}"], "cwd": str(root),
             "dependsOn": [], "problemMatchers": []}
    return {**tache, **champs}


def _tache_lumena(root, executable, args, **champs):
    tache = {"id": "lumena:outil", "label": "outil", "group": "other", "source": "lumena",
             "commandPreview": executable, "executable": executable, "args": list(args), "cwd": str(root),
             "dependsOn": [], "problemMatchers": []}
    return {**tache, **champs}


def _element_test(root, nom="test_app.py", **champs):
    chemin = root / nom
    chemin.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    element = {"id": f"pytest:{nom}", "adapter": "pytest", "kind": "file", "label": nom,
               "path": str(chemin), "line": 1, "parentId": None, "selector": nom}
    return {**element, **champs}


class FauxIDE:
    """Faux Electron : liste, lance, puis repond au suivi `operation_get` avec la
    preuve `operationExecutionEvidence` (processus, et rapport de tests)."""

    def __init__(self, service, root, *, taches=(), elements=(), jamais_fini=False, autres_args=False,
                 statut="passed", code=0):
        self.service, self.root = service, root
        self.taches, self.elements = list(taches), list(elements)
        self.jamais_fini, self.autres_args, self.statut, self.code = jamais_fini, autres_args, statut, code
        self.actions, self.perimetres, self.annulations = [], [], []
        self.lance = None
        service.bridge.send_command = self.send_command

    def _transport(self, operation):
        session = self.service.bridge._negotiated
        return {"transport_version": TRANSPORT_VERSION, "session_id": session.session_id,
                "instance_id": session.instance_id, "catalogue_revision": session.catalogue_hash,
                "request_id": "request-5c1", "operation_id": operation, "sequence": 1,
                "workspace_id": session.workspace_id}

    async def send_command(self, action, params, timeout=30.0, *, resume_transport=None, expected_snapshot=None,
                           mission_scope=None):
        self.actions.append(action)
        if mission_scope is not None:
            self.perimetres.append(mission_scope)
        if action == "task_list":
            return {"success": True, "tasks": [dict(t) for t in self.taches], "proof": {"detected": len(self.taches)}}
        if action == "test_list":
            return {"success": True, "adapters": [], "items": [dict(e) for e in self.elements], "truncated": False}
        if action in ("task_run", "test_run"):
            return self._lancer(action, params)
        if action == "operation_get":
            return self._suivi(params)
        if action == "operation_cancel":
            self.annulations.append(dict(params))
            return {"success": True}
        raise AssertionError(f"action inattendue {action}")

    def _lancer(self, action, params):
        debut = _maintenant()
        if action == "task_run":
            tache = next(t for t in self.taches if t["id"] == params["taskId"])
            executable, args, cible = tache["executable"], list(tache["args"]), None
        else:
            element = next(e for e in self.elements if e["id"] == params["itemId"])
            executable, args, cible = "python.exe", ["-m", "pytest", element["selector"], "-q"], element["id"]
        operation, run_id = uuid.uuid4().hex, uuid.uuid4().hex
        self.lance = {"action": action, "operation": operation, "run_id": run_id, "debut": _maintenant(),
                      "executable": executable, "args": ["--autre"] if self.autres_args else args}
        run = {"id": run_id, "status": "running", **({"targetId": cible} if cible else {})}
        return {"success": True, "run": run, "proof": {"accepted": True, "started": True, "pid": 1},
                "execution": {"schema_version": 1, "status": "running", "started_at": debut, "completed_at": None},
                "_transport": self._transport(operation)}

    def _suivi(self, params):
        lance = self.lance
        assert params == {"operationId": "transport:" + lance["operation"]}
        debut = _maintenant()
        fini = not self.jamais_fini
        fin = _maintenant() if fini else None
        processus = {"run_id": lance["run_id"], "status": self.statut if fini else "running",
                     "started_at": lance["debut"], "completed_at": fin, "exit_code": self.code if fini else None,
                     "exit_observed": fini, "cancellation_requested": False, "cwd": str(self.root),
                     "executable": lance["executable"], "args": lance["args"],
                     "output_sha256": "b" * 64, "output_bytes": 0}
        test = lance["action"] == "test_run"
        preuve = {"schema_version": 1, "kind": "test_execution" if test else "process",
                  "operation_id": lance["operation"], "process": processus}
        if test and fini:
            preuve["tests"] = {"run_id": lance["run_id"], "status": self.statut, "completed_at": fin,
                               "counts": {"passed": 3, "failed": 0, "skipped": 0, "total": 3},
                               "report_sha256": "c" * 64, "report_valid": True}
        return {"success": True, "operation": {"transportOperationId": lance["operation"], "action": lance["action"]},
                "proof": preuve,
                "execution": {"schema_version": 1, "status": "succeeded", "started_at": debut,
                              "completed_at": _maintenant()},
                "_transport": self._transport(uuid.uuid4().hex)}


def _prete(registry, service, version=4, **mission):
    _, root = _mission(registry, **mission)
    _ide_ouverte_sur(service, root)
    _protocole(service, version)
    return root


# ══════════════════════════════════════════════════════════════════════════
#  1. Empreinte partagee avec Electron
# ══════════════════════════════════════════════════════════════════════════


def test_empreinte_identique_aux_vecteurs_partages():
    vecteurs = json.loads(VECTEURS.read_text(encoding="utf-8"))["vectors"]
    assert len(vecteurs) >= 4
    for v in vecteurs:
        champs = dict(source=v["source"], task_id=v["id"], executable=v["executable"], args=v["args"],
                      cwd_relative=v["cwd_relative"], script=v["script"])
        assert canonical_command(**champs) == v["canonical"], v["nom"]
        assert command_digest(**champs) == v["sha256"], v["nom"]


def test_un_seul_champ_change_l_empreinte():
    base = dict(source="package", task_id="package:build", executable="cmd.exe", args=["a"],
                cwd_relative=".", script="vite build")
    reference = command_digest(**base)
    for cle, valeur in [("source", "lumena"), ("task_id", "package:test"), ("executable", "npm"),
                        ("args", ["b"]), ("cwd_relative", "lib"), ("script", "vite build ")]:
        assert command_digest(**{**base, cle: valeur}) != reference, cle


# ══════════════════════════════════════════════════════════════════════════
#  2. Execution admise : empreinte envoyee, preuve verifiee
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tache_de_mission_part_avec_son_empreinte_et_rend_une_preuve(registry, service, owner):
    # `vite` n'est pas dans la liste blanche native : run_command le refuserait aussi.
    assert sanitize_chained_command(SCRIPT_ADMIS)[0] is True
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_package(root)])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.success is True, observation.content
    assert ide.actions[:2] == ["task_list", "task_run"] and "operation_get" in ide.actions
    attendu = command_digest(source="package", task_id="package:build", executable="cmd.exe",
                             args=["/d", "/s", "/c", "call npm run build"], cwd_relative=".", script=SCRIPT_ADMIS)
    assert ide.perimetres == [{"task_id": "task_worker", "role": "worker", "allowed_files": ["README.md"],
                               "execution": {"kind": "task", "id": "package:build", "command_sha256": attendu}}]
    preuve = observation.execution_evidence
    assert preuve is not None and preuve.success is True and preuve.exit_code == 0
    contenu = json.loads(observation.content)
    assert contenu["completion"]["proof"]["process"]["exit_code"] == 0
    assert contenu["launch"]["run"]["status"] == "running"


@pytest.mark.asyncio
async def test_test_de_mission_rend_une_preuve_de_tests_verts(registry, service, owner):
    root = _prete(registry, service)
    element = _element_test(root)
    ide = FauxIDE(service, root, elements=[element])
    observation = await registry.execute("ide__test_run", {"itemId": element["id"]}, caller=REACT)
    assert observation.success is True, observation.content
    assert ide.perimetres[0]["execution"] == {"kind": "test", "id": element["id"], "command_sha256": None}
    assert observation.execution_evidence is not None and observation.execution_evidence.green_tests is True


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"statut": "failed", "code": 1}, {"autres_args": True}])
async def test_fin_observee_libere_le_cache_d_effets(registry, service, owner, cache_d_effets_neuf, options):
    """Defaut trouve par la regression complete : sans la preuve, le ticket de
    lancement restait actif pour toujours - cache de lecture natif suspendu pour
    tout le processus, puis `external_effect_capacity_reached` apres 256 lancements."""
    root = _prete(registry, service)
    FauxIDE(service, root, taches=[_tache_package(root)], **options)
    await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert cache_d_effets_neuf.snapshot()[1] is False


@pytest.mark.asyncio
async def test_sans_fin_observee_la_suspension_du_cache_reste(registry, service, owner, cache_d_effets_neuf,
                                                              monkeypatch):
    monkeypatch.setattr(module_execution, "_wait_seconds", lambda _ctx: 0.2)
    monkeypatch.setattr(module_execution, "POLL_INTERVAL_S", 0.05)
    root = _prete(registry, service)
    FauxIDE(service, root, taches=[_tache_package(root)], jamais_fini=True)
    await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert cache_d_effets_neuf.snapshot()[1] is True


@pytest.mark.asyncio
async def test_processus_en_echec_pas_de_succes(registry, service, owner):
    root = _prete(registry, service)
    FauxIDE(service, root, taches=[_tache_package(root)], statut="failed", code=1)
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.success is False
    assert observation.execution_evidence is not None and observation.execution_evidence.success is False


@pytest.mark.asyncio
async def test_processus_lance_different_du_valide_aucune_preuve(registry, service, owner):
    root = _prete(registry, service)
    FauxIDE(service, root, taches=[_tache_package(root)], autres_args=True)
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.execution_evidence is None
    assert observation.success is False


@pytest.mark.asyncio
async def test_sans_fin_observee_aucune_preuve_et_processus_annule(registry, service, owner, monkeypatch):
    # Le delai natif a un minimum de 30 s (`ide_command_timeout_sec`) : on raccourcit l'attente elle-meme.
    monkeypatch.setattr(module_execution, "_wait_seconds", lambda _ctx: 0.2)
    monkeypatch.setattr(module_execution, "POLL_INTERVAL_S", 0.05)
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_package(root)], jamais_fini=True)
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.success is False
    assert observation.execution_evidence is None
    assert "ide_mission_execution_timeout" in observation.content
    assert ide.annulations == [{"operationId": "transport:" + ide.lance["operation"], "confirmed": True}]


# ══════════════════════════════════════════════════════════════════════════
#  3. Refus : memes verdicts que run_command natif
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_script_refuse_par_le_sanitizer_natif(registry, service, owner):
    script = "rimraf ../src"
    assert sanitize_chained_command(script)[0] is False
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_package(root, script=script)])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_command_refused - ")
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
async def test_script_refuse_par_g1_natif_meme_quand_le_sanitizer_laisse_passer(registry, service, owner):
    """Cas mesure le 15/09 : `del` sur un fichier du depot passe le sanitizer."""
    script = f"del {ROOT / 'src' / 'app.py'}"
    root = _prete(registry, service)
    assert sanitize_chained_command(script)[0] is True
    assert _mission_destructive_target_violation(registry._v2_context, script) != ""
    ide = FauxIDE(service, root, taches=[_tache_package(root, script=script)])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_command_refused - ")
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
async def test_ligne_de_commande_refusee_par_le_sanitizer_natif(registry, service, owner):
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_lumena(root, "rimraf", ["../src"])])
    observation = await registry.execute("ide__task_run", {"taskId": "lumena:outil"}, caller=REACT)
    assert observation.content.startswith("IDE: ide_mission_command_refused - ")
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
@pytest.mark.parametrize("champs,code", [
    ({"source": "extension"}, "ide_mission_task_source_forbidden"),
    ({"dependsOn": ["package:lint"]}, "ide_mission_task_pipeline_unsupported"),
])
async def test_tache_hors_perimetre_d_execution(registry, service, owner, champs, code):
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_package(root, **champs)])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.content == f"IDE: {code}"
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
async def test_tache_hors_du_dossier_de_mission(registry, service, owner):
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[_tache_lumena(root, "node", ["x.mjs"], cwd=str(root.parent))])
    observation = await registry.execute("ide__task_run", {"taskId": "lumena:outil"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_task_outside"
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
async def test_tache_introuvable(registry, service, owner):
    root = _prete(registry, service)
    ide = FauxIDE(service, root, taches=[])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_task_not_found"
    assert "task_run" not in ide.actions


@pytest.mark.asyncio
async def test_test_hors_du_dossier_de_mission(registry, service, owner):
    root = _prete(registry, service)
    dehors = _element_test(root.parent, nom="test_dehors.py")
    ide = FauxIDE(service, root, elements=[dehors])
    observation = await registry.execute("ide__test_run", {"itemId": dehors["id"]}, caller=REACT)
    assert observation.content == "IDE: ide_mission_test_outside"
    assert "test_run" not in ide.actions


@pytest.mark.asyncio
async def test_adaptateur_de_test_non_admis(registry, service, owner):
    root = _prete(registry, service)
    element = _element_test(root, adapter="mocha")
    ide = FauxIDE(service, root, elements=[element])
    observation = await registry.execute("ide__test_run", {"itemId": element["id"]}, caller=REACT)
    assert observation.content == "IDE: ide_mission_test_adapter_forbidden"
    assert "test_run" not in ide.actions


@pytest.mark.asyncio
async def test_ide_en_version_3_refusee_apres_les_gardes_natifs(registry, service, owner):
    root = _prete(registry, service, version=3)
    ide = FauxIDE(service, root, taches=[_tache_package(root)])
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_unsupported"
    assert "task_run" not in ide.actions
    FauxIDE(service, root, taches=[_tache_package(root, script="rimraf ../src")])
    refus = await registry.execute("ide__task_run", {"taskId": "package:build"}, caller=REACT)
    assert refus.content.startswith("IDE: ide_mission_command_refused - ")


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce qui reste ferme, et le hors-mission inchange
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("nom", ["ide__task_list", "ide__test_list"])
async def test_listes_de_taches_et_de_tests_lisibles_en_mission(registry, service, owner, nom):
    root = _prete(registry, service)
    FauxIDE(service, root, taches=[_tache_package(root)], elements=[_element_test(root)])
    observation = await registry.execute(nom, {}, caller=REACT)
    assert observation.success is True, observation.content


@pytest.mark.asyncio
@pytest.mark.parametrize("nom", ["ide__task_runs", "ide__test_runs"])
async def test_historique_global_des_executions_reste_ferme(registry, service, owner, nom):
    root = _prete(registry, service)
    send = _envoi(service)
    observation = await registry.execute(nom, {}, caller=REACT)
    assert observation.content == "IDE: ide_mission_read_unconfined"
    send.assert_not_awaited()
    assert root.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("nom,params", [
    ("ide__task_watch", {"taskId": "package:build"}),
    ("ide__task_cancel", {"runId": "run-1"}),
])
async def test_autres_processus_toujours_fermes_en_mission(registry, service, owner, nom, params):
    _prete(registry, service)
    send = _envoi(service)
    observation = await registry.execute(nom, params, caller=REACT)
    assert observation.content == "IDE: ide_mission_mutation_not_connected"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_task_run_reste_ferme(registry, service, owner):
    send = _envoi(service)
    observation = await registry.execute("ide__task_run", {"taskId": "package:build"})
    assert observation.content == "IDE: ide_host_authorization_not_connected"
    send.assert_not_awaited()
