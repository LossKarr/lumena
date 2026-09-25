"""Panel Missions — lot 13 : UNE MISSION SANS WORKER ETAIT INVISIBLE A L'AGREGAT.

Capture d'ecran du panneau reel, envoyee par l'utilisateur : deux missions du
15 aout affichees « ÉCHÉANCE DÉPASSÉE », `-370:13:36`, avec un bouton
« Arrêter ». Les deux sont `state: done` sur le disque, avec ZERO worker.

--- La cause ---

`aggregateState` n'inspectait QUE les enfants. Une mission solo — le lead
travaille seul — n'avait aucun enfant a examiner : la boucle ne concluait rien,
et le premier test qui matchait ensuite etait `late`.

Le fait manquant existait pourtant. `buildModel` calcule `base.state` par
`workerState(m, l)` trois lignes plus haut, le pose sur l'objet rendu, puis
appelle `aggregateState(kids, ...)` sans le lui passer. Calcule, affiche, jete
avant la decision : le motif de tous les lots precedents, et de mon fait.

--- Ce qui a ete mesure, avant d'ecrire une ligne ---

Corpus reel, 665 taches, 199 missions racines :

    racines solo (0 worker)               65
    solo, closes, avec echeance           43      soit 22 % des racines
      dont done                           36
      dont cancelled                       4
      dont failed                          3      <- leur ECHEC etait masque

Les trois derniers sont le vrai degat : un echec repeint en « retard », c'est
un fait remplace par son symptome.

Apres correctif, les 199 racines repassees dans le modele :

    missions closes encore montrees vivantes     0
    decomptes qui tournent sur du terminal       0
    missions qui se replient seules            188 / 199
    echecs laisses DEPLIES pour etre vus        10

--- `terminal` et `closed` sont deux choses ---

La premiere version les confondait, et sept missions echouees egrenaient encore
leur retard. `terminal` = elle ne tournera plus : pas de compte a rebours, pas
de bouton « Arrêter ». `closed` = elle se replie seule — et un echec ne se
replie pas, c'est justement ce qu'il faut regarder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"

from src.utils.paths import DATA_DIR  # noqa: E402

_ETAT = DATA_DIR / "task_orchestrator_state.json"

def _corpus_exploitable() -> bool:
    """Vrai seulement si l'etat des taches contient VRAIMENT quelque chose.

    LOT ORCH-1 (23/09/2026) : `_ETAT.exists()` ne suffit pas. Apres la corruption
    mesuree ce jour-la - 12 270 771 octets de zeros, puis un etat remis a vide -
    le fichier EXISTAIT sans rien contenir, et ces tests echouaient en affirmant
    qu'une fonctionnalite avait disparu. **Un corpus vide ne prouve rien** : il ne
    dit pas que le runtime a cesse de persister, seulement qu'il n'a rien a dire.
    """
    try:
        import json as _json
        return bool(_json.loads(_ETAT.read_text(encoding="utf-8")).get("tasks"))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(corps: str):
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const A=globalThis.missionAggregateState, B=globalThis.buildMissionModel;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _agg(kids, remaining="null", own="undefined"):
    return _node("return A(%s, %s, %s);" % (json.dumps(kids), remaining, own))


def _modele(mission, enfants=None):
    arbre = [{"mission": mission,
              "children": [{"mission": c, "children": []} for c in (enfants or [])]}]
    return _node("return B(%s, [], Date.now())[0];" % json.dumps(arbre))


# ══════════════════════════════════════════════════════════════════════════
#  1. LE DEFAUT EXACT DE LA CAPTURE
# ══════════════════════════════════════════════════════════════════════════


def test_une_mission_SOLO_terminee_n_est_pas_en_retard():
    """Le cas exact : `done`, zero worker, echeance vieille de quinze jours."""
    m = _modele({"task_id": "m", "state": "done",
                 "metadata": {"objective": "o", "deadline_ts": "2026-08-15T19:14:30"}})
    assert m["aggregate"] == "done"
    assert m["deadlineLabel"] == "", "le decompte tourne encore sur un travail fini"


def test_une_mission_SOLO_annulee_le_dit():
    m = _modele({"task_id": "m", "state": "cancelled",
                 "metadata": {"objective": "o", "deadline_ts": "2020-01-01T00:00:00"}})
    assert m["aggregate"] == "cancelled"


def test_un_ECHEC_solo_n_est_pas_repeint_en_retard():
    """Trois missions du corpus etaient dans ce cas : leur echec — le seul
    fait actionnable — disparaissait derriere son symptome."""
    m = _modele({"task_id": "m", "state": "failed",
                 "metadata": {"objective": "o", "deadline_ts": "2020-01-01T00:00:00"}})
    assert m["aggregate"] == "failed"


def test_une_mission_solo_VIVANTE_et_en_retard_reste_en_retard():
    """Le correctif ne doit pas eteindre le signal quand il est vrai."""
    m = _modele({"task_id": "m", "state": "running",
                 "metadata": {"objective": "o", "deadline_ts": "2020-01-01T00:00:00"}})
    assert m["aggregate"] == "late"
    assert m["deadlineLabel"], "une mission qui court garde son compte a rebours"


# ══════════════════════════════════════════════════════════════════════════
#  2. L'ETAT PROPRE PRIME, MAIS N'EFFACE PAS LES WORKERS
# ══════════════════════════════════════════════════════════════════════════


def test_l_etat_propre_prime_sur_la_deduction():
    """Le runtime declare la mission terminee : c'est un fait, pas une
    deduction. Il gagne contre tout ce qu'on pourrait inferer des workers."""
    assert _agg([{"state": "running"}], "-1", '"done"') == "done"


def test_sans_etat_propre_la_deduction_reprend_la_main():
    """Retro-compatibilite : un appelant qui ne passe pas l'etat propre — et
    tous les tests des lots 8 a 12 sont dans ce cas — doit garder l'ancien
    comportement."""
    assert _agg([{"state": "running"}], "-1") == "late"
    assert _agg([{"state": "failed"}]) == "failed"


def test_un_etat_propre_NON_terminal_ne_court_circuite_rien():
    """`running` cote mission ne doit pas masquer un worker en echec."""
    assert _agg([{"state": "failed"}], "null", '"running"') == "failed"


def test_une_mission_declaree_terminee_avec_des_workers_vivants():
    """Cas limite reel : le runtime a clos la mission, une trace de worker
    traine encore. Le disque fait foi."""
    m = _modele({"task_id": "m", "state": "done", "metadata": {"objective": "o"}},
                [{"task_id": "w", "state": "running", "metadata": {}}])
    assert m["aggregate"] == "done"


# ══════════════════════════════════════════════════════════════════════════
#  3. `terminal` ET `closed` NE SONT PAS LA MEME CHOSE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("st", ["done", "cancelled", "failed"])
def test_tout_ce_qui_est_TERMINAL_perd_son_compte_a_rebours(st):
    m = _modele({"task_id": "m", "state": st,
                 "metadata": {"objective": "o", "deadline_ts": "2020-01-01T00:00:00"}})
    assert m["terminal"] is True
    assert m["deadlineLabel"] == ""


def test_un_ECHEC_est_terminal_mais_PAS_clos():
    """Il ne se replie pas : c'est justement ce qu'il faut regarder."""
    m = _modele({"task_id": "m", "state": "failed", "metadata": {"objective": "o"}})
    assert m["terminal"] is True and m["closed"] is False


@pytest.mark.parametrize("st", ["done", "cancelled"])
def test_ce_qui_est_CLOS_est_aussi_terminal(st):
    m = _modele({"task_id": "m", "state": st, "metadata": {"objective": "o"}})
    assert m["closed"] is True and m["terminal"] is True


def test_une_mission_VIVANTE_n_est_ni_l_un_ni_l_autre():
    m = _modele({"task_id": "m", "state": "running", "metadata": {"objective": "o"}})
    assert m["terminal"] is False and m["closed"] is False


# ══════════════════════════════════════════════════════════════════════════
#  4. LE CORPUS REEL — la mesure qui a declenche le lot, et sa preuve
# ══════════════════════════════════════════════════════════════════════════


_FIXTURE = _ROOT / "tests" / "fixtures" / "mission-panel-corpus-2026-09-23.json"


def _racines_depuis(chemin):
    """Le modele applique a UN corpus donne — fige ou vivant.

    REG-2 (24/09/2026) : cette fonction ne lisait que l'etat VIVANT. Deux tests en
    dependaient pour prouver que le correctif portait sur un defaut reel, et ils
    sont tombes des que le corpus a change - sans qu'une seule ligne de code n'ait
    regresse. Or le corpus du 23/09 (199 racines, dont 43 closes affichees comme
    vivantes) a ete DETRUIT par l'incident ORCH-1 : le fichier mis de cote fait
    12 270 771 octets et ne contient que des zeros. La mesure n'est plus rejouable.

    D'ou la separation : la PREUVE du comportement se fait sur un corpus fige
    (`_FIXTURE`), reproductible pour toujours ; le CONSTAT sur le reel reste, mais
    il se skippe en disant pourquoi au lieu d'echouer. Un test qui depend d'un etat
    non maitrise n'est pas un test - meme lecon que le tirage dans un frozenset
    ferme par REG-1 le meme jour.
    """
    ts = [t for t in json.loads(Path(chemin).read_text(encoding="utf-8"))["tasks"]
          if isinstance(t, dict)]
    enf = {}
    for t in ts:
        p = (t.get("metadata") or {}).get("parent_id")
        if p:
            enf.setdefault(p, []).append(t)
    rac = [t for t in ts if not (t.get("metadata") or {}).get("parent_id")]
    arbre = [{"mission": t,
              "children": [{"mission": c, "children": []} for c in enf.get(t["task_id"], [])]}
             for t in rac]
    f = Path(tempfile.mkdtemp()) / "arbre.json"
    f.write_text(json.dumps(arbre), encoding="utf-8")
    out = _node("const a=JSON.parse(require('fs').readFileSync(%s,'utf8'));"
                "return B(a, [], Date.now());" % json.dumps(str(f)))
    return rac, out


def _racines_reelles():
    """Le corpus VIVANT — il change, et c'est normal."""
    return _racines_depuis(_ETAT)


def _racines_figees():
    """Le corpus de PREUVE — il ne change pas, et c'est tout son intérêt."""
    assert _FIXTURE.exists(), f"corpus de preuve absent : {_FIXTURE}"
    return _racines_depuis(_FIXTURE)


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_AUCUNE_mission_close_du_corpus_ne_passe_pour_vivante():
    """La preuve du correctif, sur les 199 missions racines reelles. Avant :
    43 d'entre elles s'affichaient « échéance dépassée » avec un bouton
    « Arrêter »."""
    rac, out = _racines_reelles()
    faux = [rac[i]["task_id"] for i in range(len(out))
            if rac[i]["state"] in ("done", "cancelled", "failed") and not out[i]["terminal"]]
    assert not faux, f"{len(faux)} missions closes montrees comme vivantes : {faux[:3]}"


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_AUCUN_decompte_ne_tourne_sur_du_terminal():
    rac, out = _racines_reelles()
    qui = [rac[i]["task_id"] for i, x in enumerate(out) if x["terminal"] and x["deadlineLabel"]]
    assert not qui, f"{len(qui)} decomptes tournent sur des missions terminees"


def test_les_ECHECS_restent_DEPLIES():
    """Ils sont terminaux, mais ce sont eux qu'il faut voir. PREUVE, corpus fige.

    Version d'origine : elle exigeait un echec dans l'etat VIVANT. Le 24/09 il n'y
    en avait plus un seul, et le test tombait en annoncant une regression qui
    n'existait pas.
    """
    _rac, out = _racines_figees()
    echecs = [x for x in out if x["aggregate"] == "failed"]
    assert echecs, "le corpus de preuve ne porte plus d'echec : la fixture est a refaire"
    assert all(not x["closed"] for x in echecs)


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_les_ECHECS_du_corpus_reel_restent_DEPLIES_si_le_corpus_en_a():
    """Le meme invariant, CONSTATE sur le reel — opportuniste, jamais bloquant.

    S'il n'y a pas d'echec en base, il n'y a rien a constater : on le DIT. Ne pas
    avoir de cas a examiner n'est pas un echec, et confondre les deux est le travers
    que tout ce chantier combat.
    """
    _rac, out = _racines_reelles()
    echecs = [x for x in out if x["aggregate"] == "failed"]
    if not echecs:
        pytest.skip("aucun echec dans le corpus vivant — rien a constater ici")
    assert all(not x["closed"] for x in echecs)


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_le_modele_digere_toutes_les_racines_du_corpus_sans_broncher():
    # Nom d'origine : « les_199_racines ». Ces 199 n'existent plus — le corpus qui
    # les portait a ete detruit par ORCH-1. Un nom de test qui cite un chiffre
    # perime affirme une mesure qu'il ne fait plus.
    rac, out = _racines_reelles()
    assert len(out) == len(rac)
    assert all(x["aggregate"] for x in out), "un agregat vide quelque part"
