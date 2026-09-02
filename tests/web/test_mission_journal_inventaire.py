"""Journal de mission — lot D : SAVOIR CE QU'ON GARDE, ET LE DIRE.

Les lots A, B et C gravent, relisent et affichent. Il manquait la derniere
piece pour que « traçable » soit vrai de bout en bout : un systeme qui archive
sans jamais montrer son empreinte, et sans pouvoir se nettoyer, n'est pas fini.

═══════════════════════════════════════════════════════════════════════════════
  LE DEFAUT VISIBLE
═══════════════════════════════════════════════════════════════════════════════

Devant la liste des missions terminees — 188 sur 199 dans le corpus reel —
RIEN ne distinguait celles qui gardent une trace de celles qui sont muettes.
Il fallait deplier chacune pour le decouvrir. La liste porte desormais le poids
du journal de chaque mission, et le repli l'annonce.

═══════════════════════════════════════════════════════════════════════════════
  LE COUT, MESURE AVANT DE CHOISIR
═══════════════════════════════════════════════════════════════════════════════

    UN balayage de repertoire, 5 000 fichiers avec leurs tailles ....  8,7 ms
    670 `exists()` un par un  ..................................... 17,9 ms

Le balayage unique est deux fois plus rapide ET il tient l'echelle. La route
annote donc toute la liste a partir d'un seul `inventaire()`, au lieu
d'interroger le disque mission par mission.

═══════════════════════════════════════════════════════════════════════════════
  CE QU'ON NE PURGE PAS
═══════════════════════════════════════════════════════════════════════════════

Pas de purge par DATE. « Une mission close reste rouvrable pour toujours » est
le but du module ; borner par l'age reviendrait a decider a la place de
l'utilisateur que son passe ne compte plus.

On ne supprime QUE les journaux ORPHELINS — ceux dont la tache n'existe plus.
Ceux-la ne peuvent plus etre rouverts depuis le panneau : il n'y a plus de
mission a deplier. C'est du poids mort, et c'est tout.

Et sans liste de reference, `purge()` ne supprime RIEN : ne rien faire est le
bon defaut quand on ne sait pas.
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
_CSS = _ROOT / "web" / "static" / "css" / "mission-panel.css"


@pytest.fixture(autouse=True)
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_racine", lambda: tmp_path / "missions")
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "1")
    qmod.reset_for_tests()
    manager_mod._manager = None
    yield tmp_path / "missions"
    qmod.reset_for_tests()
    manager_mod._manager = None


def _core(monkeypatch, tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    monkeypatch.setattr(deps, "lumena", core, raising=False)
    return manager_mod.get_mission_manager(core), orch


def _grave(tid, n=1, taille=100):
    for i in range(n):
        J.grave({"task_id": tid, "seq": i, "ts": "2026-09-01T12:00:00Z",
                 "stage": "codeagent_iteration", "thought": "x" * taille})


# ══════════════════════════════════════════════════════════════════════════
#  1. L'INVENTAIRE
# ══════════════════════════════════════════════════════════════════════════


def test_l_inventaire_compte_les_missions_pas_les_fichiers():
    """Un journal en deux morceaux reste UN journal."""
    _grave("task_a")
    (J._racine() / "task_a.1.jsonl").write_text("{}\n" * 20, encoding="utf-8")
    _grave("task_b")
    inv = J.inventaire()
    assert inv["files"] == 2
    assert set(inv["entries"]) == {"task_a", "task_b"}


def test_les_morceaux_d_une_meme_mission_sont_ADDITIONNES():
    _grave("task_a")
    seul = J.inventaire()["entries"]["task_a"]
    (J._racine() / "task_a.1.jsonl").write_text("z" * 500, encoding="utf-8")
    assert J.inventaire()["entries"]["task_a"] == seul + 500


def test_le_total_est_la_somme():
    _grave("task_a", n=3)
    _grave("task_b", n=2)
    inv = J.inventaire()
    assert inv["bytes"] == sum(inv["entries"].values())


def test_un_dossier_ABSENT_rend_un_inventaire_vide_pas_une_erreur():
    inv = J.inventaire()
    assert inv == {"entries": {}, "files": 0, "bytes": 0}


def test_les_fichiers_ETRANGERS_sont_ignores():
    """Le dossier peut contenir autre chose ; on ne compte que des journaux."""
    J._racine().mkdir(parents=True, exist_ok=True)
    (J._racine() / "note.txt").write_text("bonjour", encoding="utf-8")
    (J._racine() / "sous-dossier").mkdir()
    _grave("task_a")
    assert J.inventaire()["files"] == 1


# ══════════════════════════════════════════════════════════════════════════
#  2. LA PURGE — orphelins SEULEMENT
# ══════════════════════════════════════════════════════════════════════════


def test_sans_liste_de_reference_on_ne_supprime_RIEN():
    """Ne rien faire est le bon defaut quand on ne sait pas."""
    _grave("task_a")
    _grave("task_b")
    r = J.purge(None)
    assert r["removed"] == 0 and r["kept"] == 2
    assert J.inventaire()["files"] == 2


def test_seuls_les_ORPHELINS_partent():
    _grave("task_vivante")
    _grave("task_morte")
    r = J.purge(["task_vivante"])
    assert r["removed"] == 1
    assert set(J.inventaire()["entries"]) == {"task_vivante"}


def test_la_purge_emporte_AUSSI_le_fichier_tourne():
    """Sinon la moitie d'un journal orphelin resterait sur le disque."""
    _grave("task_morte")
    (J._racine() / "task_morte.1.jsonl").write_text("{}\n", encoding="utf-8")
    J.purge([])
    assert list(J._racine().glob("task_morte*")) == []


def test_la_purge_DIT_ce_qu_elle_a_libere():
    _grave("task_morte", n=5, taille=200)
    avant = J.inventaire()["bytes"]
    r = J.purge([])
    assert r["removed"] == 1 and r["bytes"] == avant
    assert J.inventaire()["bytes"] == 0


def test_une_purge_sur_un_dossier_vide_ne_casse_rien():
    assert J.purge(["task_x"]) == {"removed": 0, "bytes": 0, "kept": 0}


def test_AUCUNE_purge_par_date():
    """« Une mission close reste rouvrable pour toujours » : borner par l'age
    reviendrait a decider a la place de l'utilisateur que son passe ne compte
    plus. Le garde est sur la SIGNATURE, pour que l'intention reste explicite."""
    import inspect
    # Le CODE, pas la prose : le docstring explique justement qu'on ne purge
    # pas par age, donc le mot y figure. Piege de la sous-chaine — septieme
    # fois sur ce chantier.
    src = inspect.getsource(J.purge)
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    for mot in ("days", "age", "mtime", "st_mtime", "older", "before"):
        assert mot not in src, f"la purge regarde l'age : {mot}"


# ══════════════════════════════════════════════════════════════════════════
#  3. LA LISTE ANNOTEE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_chaque_mission_porte_le_POIDS_de_son_journal(monkeypatch, tmp_path):
    """Sans cela, rien ne distingue une mission finie qui a garde une trace
    d'une mission finie muette."""
    mgr, _ = _core(monkeypatch, tmp_path)
    mid = mgr.create_mission("objectif", metadata={"depth": 1})
    _grave(mid, n=4, taille=150)

    r = await EP.list_missions()
    ligne = [m for m in r["missions"] if m["task_id"] == mid][0]
    assert ligne["journal_bytes"] > 0


@pytest.mark.asyncio
async def test_une_mission_SANS_journal_porte_zero_pas_rien(monkeypatch, tmp_path):
    """Le champ doit exister meme a zero : le panneau teste sa valeur, pas sa
    presence."""
    mgr, _ = _core(monkeypatch, tmp_path)
    mgr.create_mission("muette", metadata={"depth": 1})
    r = await EP.list_missions()
    assert r["missions"][0]["journal_bytes"] == 0


@pytest.mark.asyncio
async def test_la_liste_DIT_son_empreinte_totale(monkeypatch, tmp_path):
    """Un systeme qui archive sans jamais montrer ce qu'il garde n'est pas
    fini."""
    mgr, _ = _core(monkeypatch, tmp_path)
    mgr.create_mission("a", metadata={"depth": 1})
    _grave("task_libre", n=3)
    r = await EP.list_missions()
    assert r["journal"]["files"] >= 1 and r["journal"]["bytes"] > 0


@pytest.mark.asyncio
async def test_l_annotation_ne_fait_QU_UN_balayage(monkeypatch, tmp_path):
    """Mesure : 8,7 ms pour 5 000 fichiers en un balayage, contre 17,9 ms pour
    670 `exists()`. Le garde compte les appels reels."""
    mgr, _ = _core(monkeypatch, tmp_path)
    for i in range(5):
        mgr.create_mission("m%d" % i, metadata={"depth": 1})

    appels = {"n": 0}
    vrai = J.inventaire

    def compte():
        appels["n"] += 1
        return vrai()

    monkeypatch.setattr(J, "inventaire", compte)
    await EP.list_missions()
    assert appels["n"] == 1, f"{appels['n']} balayages pour une seule liste"


@pytest.mark.asyncio
async def test_un_journal_ILLISIBLE_ne_fait_pas_tomber_la_liste(monkeypatch, tmp_path):
    """Le journal est un CONFORT, la liste des missions est le produit.

    La premiere version de ce test CONSTATAIT que l'echec remontait et faisait
    tomber tout l'ecran — j'ai documente une fragilite au lieu de la corriger,
    ce qui contredisait le principe applique partout ailleurs. La route annote
    desormais a zero et le panneau montre les missions sans leur marqueur."""
    mgr, _ = _core(monkeypatch, tmp_path)
    mgr.create_mission("a", metadata={"depth": 1})
    monkeypatch.setattr(J, "inventaire", lambda: (_ for _ in ()).throw(OSError("disque")))
    r = await EP.list_missions()
    assert r["success"] and r["count"] == 1
    assert r["missions"][0]["journal_bytes"] == 0
    assert r["journal"] == {"files": 0, "bytes": 0}


# ══════════════════════════════════════════════════════════════════════════
#  4. LE MARQUEUR A L'ECRAN
# ══════════════════════════════════════════════════════════════════════════

_node = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _rendu(missions, prefs="null"):
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "process.stdout.write(globalThis.missionRenderView('workshop', %s, %s));"
        % (json.dumps(missions), prefs)
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _m(**kw):
    b = {"id": "m1", "objective": "o", "aggregate": "done", "closed": True,
         "terminal": True, "children": [], "deadlineLabel": "", "workers": {},
         "archiveBytes": 0, "timeline": None, "budget": None, "trail": [],
         "proofs": [], "delivered": {"published": [], "artifacts": []}}
    b.update(kw)
    return b


def _w():
    return {"id": "w1", "objective": "[Worker api] x", "state": "done",
            "perimeter": [], "thought": "", "iteration": 0, "maxIter": 0,
            "lastTool": "", "queueRank": 0, "waitedMs": 0, "proofs": [], "trail": []}


@_node
@pytest.mark.parametrize("octets,attendu", [
    (400, "400 o"), (12452, "12 Ko"), (2_600_000, "2,5 Mo"),
])
def test_le_poids_se_lit_comme_un_humain_le_lit(octets, attendu):
    """« 12 452 » ne dit rien ; « 12 Ko » se lit d'un coup."""
    assert attendu in _rendu([_m(archiveBytes=octets, children=[_w()])])


@_node
def test_une_mission_repliee_ANNONCE_son_archive():
    h = _rendu([_m(archiveBytes=8000, children=[_w()])])
    assert "mp-arch" in h and "journal ·" in h


@_node
def test_une_mission_SANS_archive_ne_dit_rien():
    """Un « journal · 0 o » serait du bruit."""
    assert "mp-arch" not in _rendu([_m(archiveBytes=0, children=[_w()])])


@_node
def test_une_mission_SOLO_avec_archive_le_dit_quand_meme():
    """65 racines sur 199 n'ont aucun worker : sans ce cas, leur seule
    information disponible une fois repliees serait tue."""
    h = _rendu([_m(archiveBytes=5000, children=[])])
    assert "mp-arch" in h and "journal ·" in h


@_node
def test_une_mission_solo_SANS_archive_n_a_pas_de_ligne_vide():
    h = _rendu([_m(archiveBytes=0, children=[])])
    assert "mp-fold-sum" not in h


@_node
def test_le_marqueur_reste_DISCRET():
    """Il repond a « y a-t-il quelque chose a voir ici ? », pas a
    « regarde-moi »."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-arch")
    corps = css[i:css.index("}", i)]
    assert "var(--muted)" in corps
    assert "--accent" not in corps and "--ok" not in corps


@_node
def test_le_modele_lit_l_annotation_de_la_route():
    """Le nom du champ est un CONTRAT entre la route et le modele : s'il
    change d'un cote, le marqueur disparait en silence."""
    arbre = [{"mission": {"task_id": "m", "state": "done", "journal_bytes": 4096,
                          "metadata": {"objective": "o"}}, "children": []}]
    script = (
        "require(%s);" % json.dumps(str(_JS / "mission_model.js"))
        + "process.stdout.write(JSON.stringify("
        + "globalThis.buildMissionModel(%s, [], 0)[0].archiveBytes));" % json.dumps(arbre)
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == 4096


def test_la_route_et_le_modele_parlent_du_MEME_champ():
    """La preuve par les deux sources, pas par un rendu qui pourrait passer
    pour une autre raison."""
    route = (_ROOT / "web" / "routes" / "missions.py").read_text(encoding="utf-8")
    modele = (_JS / "mission_model.js").read_text(encoding="utf-8")
    assert '"journal_bytes"' in route
    assert "journal_bytes" in modele
