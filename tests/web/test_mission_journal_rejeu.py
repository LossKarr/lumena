"""Journal de mission — lot B : ROUVRIR une mission finie.

Le lot A grave le raisonnement sur le disque. Celui-ci le rend consultable.

═══════════════════════════════════════════════════════════════════════════════
  LE PARI DE CONCEPTION, ET POURQUOI IL TIENT
═══════════════════════════════════════════════════════════════════════════════

Aucune vue nouvelle n'a ete ecrite. Le modele du panneau prend deja `events` en
parametre ; on lui donne les evenements ARCHIVES au lieu du tampon SSE, et les
memes cartes, la meme pensee, le meme battement se rejouent depuis le disque.
C'est le decoupage modele/vues qui rend ce lot presque gratuit — la seule
raison pour laquelle il tient en trois fonctions.

Deux decisions qui ne vont pas de soi :

**Le chargement se fait au DEPLIAGE, pas au rendu.** 188 des 199 missions du
corpus sont terminales ; les charger a l'affichage ferait 188 requetes pour
rien. Deplier une mission close, c'est precisement dire « je veux regarder
celle-ci ».

**L'endpoint agrege le lead ET ses workers.** Chaque tache a son propre
`task_id`, donc son propre fichier. Ne servir que celui de la mission
montrerait le lead seul, avec des workers muets — ce qui serait pire que rien,
parce que ca ressemblerait a une mission qui a travaille seule.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.telemetry import mission_journal as J
from web.routes import deps
from web.routes import missions as EP

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"
_PANELS = _JS / "panels.js"


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    # Opt-in EXPLICITE : le conftest coupe le journal pour toute la suite
    # afin qu'aucun test n'ecrive dans les donnees reelles. Ici on le
    # rallume, et sa racine est deja detournee vers `tmp_path`.
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "1")
    monkeypatch.setattr(J, "_racine", lambda: tmp_path / "missions")
    qmod.reset_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    manager_mod._manager = None


def _core(monkeypatch, tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    monkeypatch.setattr(deps, "lumena", core, raising=False)
    return manager_mod.get_mission_manager(core), orch


def _grave(tid, **kw):
    b = {"task_id": tid, "seq": 1, "ts": "2026-09-01T12:00:00Z",
         "stage": "codeagent_iteration"}
    b.update(kw)
    assert J.grave(b) is True


# ══════════════════════════════════════════════════════════════════════════
#  1. L'ENDPOINT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_le_journal_d_une_mission_se_sert(monkeypatch, tmp_path):
    _core(monkeypatch, tmp_path)
    _grave("task_m", thought="J’intègre d’abord, je sers ensuite.")
    r = await EP.get_mission_journal("task_m")
    assert r["success"] and r["count"] == 1
    assert r["events"][0]["thought"] == "J’intègre d’abord, je sers ensuite."


@pytest.mark.asyncio
async def test_le_task_id_est_REMIS_car_le_modele_indexe_par_lui(monkeypatch, tmp_path):
    """Il n'est pas grave dans le fichier : il EST le nom du fichier."""
    _core(monkeypatch, tmp_path)
    _grave("task_m", thought="x")
    brut = J.chemin_journal("task_m").read_text(encoding="utf-8")
    assert "task_id" not in brut, "le task_id ne doit pas etre duplique a chaque ligne"
    r = await EP.get_mission_journal("task_m")
    assert r["events"][0]["task_id"] == "task_m"


@pytest.mark.asyncio
async def test_l_endpoint_agrege_le_LEAD_ET_ses_workers(monkeypatch, tmp_path):
    """Servir le lead seul montrerait des workers muets — pire que rien, parce
    que ca ressemblerait a une mission qui a travaille seule."""
    mgr, orch = _core(monkeypatch, tmp_path)
    mid = mgr.create_mission("objectif", metadata={"depth": 1})
    wid = orch.start_task(conversation_id="conv", channel="web",
                          message_preview="[Worker api] contrat",
                          metadata={"parent_id": mid}).task_id
    _grave(mid, thought="LEAD", ts="2026-09-01T12:00:00Z")
    _grave(wid, thought="WORKER", ts="2026-09-01T12:00:05Z")

    r = await EP.get_mission_journal(mid)
    assert set(r["tasks"]) == {mid, wid}
    assert [e["thought"] for e in r["events"]] == ["LEAD", "WORKER"]


@pytest.mark.asyncio
async def test_l_ordre_est_CHRONOLOGIQUE_entre_taches(monkeypatch, tmp_path):
    """`seq` repart de zero au redemarrage du serveur ; `ts` est un horodatage
    UTC ISO, triable comme une chaine et stable a travers les redemarrages."""
    mgr, orch = _core(monkeypatch, tmp_path)
    mid = mgr.create_mission("o", metadata={"depth": 1})
    wid = orch.start_task(conversation_id="conv", channel="web",
                          message_preview="[Worker w] contrat",
                          metadata={"parent_id": mid}).task_id
    _grave(wid, thought="EN PREMIER", ts="2026-09-01T10:00:00Z", seq=999)
    _grave(mid, thought="EN SECOND", ts="2026-09-01T11:00:00Z", seq=1)

    r = await EP.get_mission_journal(mid)
    assert [e["thought"] for e in r["events"]] == ["EN PREMIER", "EN SECOND"]


@pytest.mark.asyncio
async def test_le_journal_SURVIT_a_sa_tache(monkeypatch, tmp_path):
    """C'est precisement l'interet : l'endpoint ne verifie pas que la mission
    existe encore."""
    _core(monkeypatch, tmp_path)
    _grave("task_disparue", thought="ce qui reste")
    r = await EP.get_mission_journal("task_disparue")
    assert r["count"] == 1


@pytest.mark.asyncio
async def test_une_mission_SANS_journal_rend_une_liste_vide(monkeypatch, tmp_path):
    _core(monkeypatch, tmp_path)
    r = await EP.get_mission_journal("task_neuve")
    assert r["success"] and r["count"] == 0 and r["exists"] is False


@pytest.mark.asyncio
async def test_un_identifiant_DOUTEUX_ne_sert_rien(monkeypatch, tmp_path):
    """La porte du lot A tient aussi cote reseau."""
    _core(monkeypatch, tmp_path)
    for mauvais in ("../../etc/passwd", "a/b", "..\\x"):
        r = await EP.get_mission_journal(mauvais)
        assert r["count"] == 0 and r["exists"] is False


@pytest.mark.asyncio
async def test_la_borne_est_APPLIQUEE_et_garde_la_fin(monkeypatch, tmp_path):
    _core(monkeypatch, tmp_path)
    for i in range(50):
        _grave("task_m", seq=i, ts="2026-09-01T12:%02d:00Z" % i, thought="etape %d" % i)
    r = await EP.get_mission_journal("task_m", limit=5)
    assert r["count"] == 5
    assert r["events"][-1]["thought"] == "etape 49"


@pytest.mark.asyncio
async def test_la_borne_est_PLAFONNEE_meme_si_on_demande_l_infini(monkeypatch, tmp_path):
    _core(monkeypatch, tmp_path)
    _grave("task_m", thought="x")
    for demande in (0, -5, 10 ** 9):
        r = await EP.get_mission_journal("task_m", limit=demande)
        assert r["success"]


@pytest.mark.asyncio
async def test_un_orchestrateur_absent_ne_fait_pas_TOMBER_le_journal(monkeypatch, tmp_path):
    """Sans les enfants, le lead vaut mieux que rien."""
    monkeypatch.setattr(deps, "lumena", None, raising=False)
    _grave("task_m", thought="seul")
    r = await EP.get_mission_journal("task_m")
    assert r["count"] == 1 and r["events"][0]["thought"] == "seul"


# ══════════════════════════════════════════════════════════════════════════
#  2. LE CHASSIS
# ══════════════════════════════════════════════════════════════════════════

_node = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _js(corps):
    script = (
        "const P=require(%s);" % json.dumps(str(_JS / "mission_panel.js"))
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@_node
def test_le_DIRECT_gagne_contre_l_archive():
    """Le modele garde la derniere valeur vue : l'archive doit donc passer en
    PREMIER. Une mission qui repart apres un rechargement affiche ce qu'elle
    fait maintenant, pas ce qu'elle faisait avant."""
    out = _js("""
      P.pousserEvenement({task_id:'m', stage:'live', thought:'MAINTENANT'});
      P.poserJournal('m', [{task_id:'m', stage:'archive', thought:'AVANT'}]);
      return P.evenements().map(function (e) { return e.stage; });
    """)
    assert out == ["archive", "live"]


@_node
def test_un_journal_charge_se_SAIT_charge():
    """Sans ce drapeau, deplier deux fois relancerait la requete."""
    assert _js("P.poserJournal('m', []); return [P.journalCharge('m'), P.journalCharge('z')];") \
        == [True, False]


@_node
def test_un_journal_VIDE_compte_comme_charge():
    """C'est le verrou anti-boucle : un echec reseau ne doit pas rejouer la
    requete a chaque rendu."""
    assert _js("P.poserJournal('m', []); return P.journalCharge('m');") is True


@_node
def test_les_journaux_ne_se_MELANGENT_pas():
    out = _js("""
      P.poserJournal('a', [{task_id:'a', thought:'A'}]);
      P.poserJournal('b', [{task_id:'b', thought:'B'}]);
      return P.evenements().map(function (e) { return e.thought; }).sort();
    """)
    assert out == ["A", "B"]


@_node
def test_on_peut_TOUT_oublier():
    assert _js("P.poserJournal('m',[{a:1}]); P.oublierJournaux(); return P.evenements().length;") == 0


# ══════════════════════════════════════════════════════════════════════════
#  3. LE CABLAGE
# ══════════════════════════════════════════════════════════════════════════


def _code_panels() -> str:
    src = re.sub(r"/\*.*?\*/", "", _PANELS.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def test_le_journal_se_charge_au_DEPLIAGE():
    c = _code_panels()
    i = c.index("cible.dataset.mpFold")
    bloc = c[i:i + 900]
    assert "_chargerJournalMission" in bloc


def test_on_ne_charge_QUE_les_missions_terminales():
    """Une mission vivante a son flux SSE : aller chercher son archive serait
    du travail pour rien."""
    c = _code_panels()
    i = c.index("_chargerJournalMission(id)")
    garde = c[max(0, i - 400):i]
    assert "data-terminal" in garde
    assert "replieMaintenant" in garde, "on ne charge qu'en DEPLIANT, pas en repliant"


def test_on_ne_charge_QU_UNE_fois():
    c = _code_panels()
    i = c.index("_chargerJournalMission(id)")
    assert "journalCharge(id)" in c[max(0, i - 400):i]


def test_le_verrou_est_pose_AVANT_la_requete():
    """Sinon deux clics rapides lancent deux requetes."""
    c = _code_panels()
    i = c.index("async function _chargerJournalMission")
    bloc = c[i:i + 900]
    assert bloc.index("poserJournal(missionId, [])") < bloc.index("fetch(")


def test_un_echec_reseau_ne_bloque_PAS_le_depliage():
    c = _code_panels()
    i = c.index("async function _chargerJournalMission")
    bloc = c[i:i + 1200]
    assert "try {" in bloc and "catch" in bloc
    assert "if (!r.ok) return;" in bloc


def test_le_rendu_n_est_relance_QUE_si_le_journal_dit_quelque_chose():
    """Re-peindre pour zero evenement ferait clignoter l'ecran pour rien."""
    c = _code_panels()
    i = c.index("async function _chargerJournalMission")
    bloc = c[i:i + 1200]
    j = bloc.index("_renderMissionsFromCache()")
    assert "d.events.length" in bloc[max(0, j - 300):j]


def test_l_identifiant_est_ECHAPPE_dans_l_URL():
    c = _code_panels()
    i = c.index("api/missions/")
    j = c.index("/journal", i)
    assert "encodeURIComponent" in c[i:j]


# ══════════════════════════════════════════════════════════════════════════
#  4. BOUT EN BOUT — la seule verification qui compte
# ══════════════════════════════════════════════════════════════════════════


@_node
def test_une_mission_FINIE_retrouve_sa_pensee(tmp_path):
    """Le pari du lot : aucune vue nouvelle. Le modele prend les evenements
    archives comme il prenait ceux du direct, et la carte se rejoue."""
    arbre = [{"mission": {"task_id": "m", "state": "done",
                          "metadata": {"objective": "Construis le site"}},
              "children": [{"mission": {"task_id": "w1", "state": "done",
                                        "metadata": {"objective": "[Worker api] x"}},
                            "children": []}]}]
    archive = [
        {"task_id": "m", "stage": "codeagent_iteration", "iteration": 9, "max_iter": 20,
         "thought": "J’intègre d’abord, je sers ensuite."},
        {"task_id": "w1", "stage": "codeagent_iteration", "iteration": 4, "max_iter": 12,
         "thought": "Le POST /contact répond 500."},
    ]
    script = (
        "require(%s);require(%s);const P=require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in
            ("mission_model.js", "mission_views.js", "mission_panel.js"))
        + "P.poserJournal('m', %s);" % json.dumps(archive)
        + "const m = globalThis.buildMissionModel(%s, P.evenements(), 0);" % json.dumps(arbre)
        + "process.stdout.write(globalThis.missionRenderView('workshop', m,"
        + " {folded:{m:false}}));"
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    html = r.stdout
    assert "J’intègre d’abord" in html, "la pensee du lead ne se rejoue pas"
    assert "Le POST /contact répond 500." in html, "celle du worker non plus"
    assert "aucun raisonnement transmis" not in html


@pytest.mark.asyncio
async def test_la_chaine_ENTIERE_du_disque_a_la_carte(monkeypatch, tmp_path):
    """Du bus de traces jusqu'au HTML, sans raccourci : on grave par le vrai
    chemin, on relit par le vrai endpoint, on rend par le vrai modele."""
    if shutil.which("node") is None:
        pytest.skip("node indisponible")
    from src.telemetry.trace_bus import publish_trace

    mgr, _orch = _core(monkeypatch, tmp_path)
    mid = mgr.create_mission("Refondre le site", metadata={"depth": 1})
    publish_trace(stage="codeagent_iteration", task_id=mid, tool_name="serve_website",
                  thought="Un site qui n’a jamais été ouvert n’est pas vérifié.",
                  iteration=9, max_iter=20)

    servi = await EP.get_mission_journal(mid)
    assert servi["count"] == 1, "le bus n'a pas grave l'evenement"

    arbre = [{"mission": {"task_id": mid, "state": "done",
                          "metadata": {"objective": "Refondre le site"}},
              "children": []}]
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const m = globalThis.buildMissionModel(%s, %s, 0);"
        % (json.dumps(arbre), json.dumps(servi["events"]))
        + "process.stdout.write(globalThis.missionRenderView('workshop', m, %s));"
        % json.dumps({"folded": {mid: False}})
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    assert "jamais été ouvert" in r.stdout
