"""Panel Missions — lot 11 : DEUX ETATS REELS PORTAIENT UN FAUX NOM.

Trouve en ouvrant la page dans un vrai Chromium, apres que tous les tests
textuels soient au vert. C'est la lecon N du projet, appliquee au panneau : les
runs reels trouvent ce que les tests verts ne voient pas.

--- Ce qui a ete mesure ---

Etats reellement persistes, corpus `task_orchestrator_state.json`, 665 taches :

    done           597
    cancelled       47      <- affiche « échec »
    failed          14
    checkpointed     7      <- affiche « travaille »

`workerState` repliait `cancelled` dans `TERMINAL` puis rendait « tout ce qui
n'est pas `done` » en `failed`. Le deuxieme etat le plus frequent du corpus
etait donc peint en rouge et nomme echec.

`checkpointed` n'etait dans aucune liste : il tombait dans le cas par defaut,
`running`. Une tache interrompue au redemarrage — qui n'est PAS rejouee
automatiquement et attend une revue humaine — s'affichait comme un worker en
train de travailler. C'est exactement le defaut que ce panneau existe pour
fermer, et il etait dans le panneau.

--- Le choix des couleurs, et pourquoi ---

    cancelled -> --muted    elle RECULE : c'est une fin, pas une faute
    stalled   -> --warn     elle APPELLE : elle attend une decision humaine

Une annulation n'appelle pas l'oeil. Une interruption, si.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"
_CSS = _ROOT / "web" / "static" / "css" / "mission-panel.css"

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
        + "const W=globalThis.missionWorkerState, A=globalThis.missionAggregateState,"
        + " V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _etat(st, live="null"):
    return _node("return W({state:%s}, %s);" % (json.dumps(st), live))


# ══════════════════════════════════════════════════════════════════════════
#  1. LES DEUX MENSONGES
# ══════════════════════════════════════════════════════════════════════════


def test_une_tache_ANNULEE_n_est_pas_un_echec():
    """47 taches du corpus — le deuxieme etat le plus frequent."""
    assert _etat("cancelled") == "cancelled"


def test_une_tache_INTERROMPUE_ne_travaille_pas():
    """7 taches du corpus. Elle n'est pas rejouee automatiquement : elle attend
    une revue. La montrer active, c'est le defaut que ce panneau doit fermer."""
    assert _etat("checkpointed") == "stalled"


@pytest.mark.parametrize("st,attendu", [
    ("done", "done"), ("failed", "failed"), ("running", "running"),
    ("cancelled", "cancelled"), ("checkpointed", "stalled"),
])
def test_chaque_etat_du_corpus_a_sa_traduction(st, attendu):
    assert _etat(st) == attendu


def test_un_etat_INCONNU_reste_actif_plutot_que_de_disparaitre():
    """Une version future du runtime ne doit pas vider l'ecran."""
    assert _etat("cosmique") == "running"


def test_une_tache_interrompue_n_est_pas_maquillee_en_ATTENTE():
    """`stalled` prime sur la deduction du flux SSE : si le disque dit
    interrompue, une trace d'attente ne doit pas la repeindre en « en file »."""
    assert _etat("checkpointed", "{waitingSince: 1}") == "stalled"


def test_un_checkpoint_RUNTIME_ACTIF_est_affiche_EN_COURS():
    assert _node("return W({state:'checkpointed',runtime_active:true}, null);") == "running"


def test_un_checkpoint_runtime_actif_en_file_reste_EN_ATTENTE():
    assert _node(
        "return W({state:'checkpointed',runtime_active:true}, {waitingSince:1});"
    ) == "waiting"


def test_un_backend_ancien_peut_prouver_la_reprise_par_une_trace_recente():
    assert _node(
        "return W({state:'checkpointed'}, {lastAt:'2026-09-01T02:34:00Z'}, "
        "Date.parse('2026-09-01T02:35:00Z'));"
    ) == "running"


def test_une_trace_ancienne_ne_ressuscite_pas_un_checkpoint():
    assert _node(
        "return W({state:'checkpointed'}, {lastAt:'2026-09-01T02:00:00Z'}, "
        "Date.parse('2026-09-01T02:35:00Z'));"
    ) == "stalled"


# ══════════════════════════════════════════════════════════════════════════
#  2. CE QUE LA MISSION EN DEDUIT
# ══════════════════════════════════════════════════════════════════════════


def _agg(etats, remaining="null"):
    return _node("return A(%s, %s);"
                 % (json.dumps([{"state": e} for e in etats]), remaining))


def test_un_worker_INTERROMPU_arrete_la_mission():
    """Il attend une decision humaine : la mission ne peut pas l'ignorer."""
    assert _agg(["running", "running", "stalled"]) == "stalled"


def test_l_ordre_de_gravite_est_STABLE():
    """failed > stalled > clos > late > waiting > running.

    `late` est descendu sous les etats deduits des workers (lot 12) : le
    retard a deja son canal, le compte a rebours rouge de l'en-tete. La
    pastille sert a montrer ce qui n'a pas d'autre porte-voix."""
    assert _agg(["stalled", "failed"]) == "failed"
    assert _agg(["failed", "stalled"]) == "failed", "l'ordre du tableau ne doit rien changer"
    assert _agg(["stalled", "running"], "-1") == "stalled"
    assert _agg(["running", "running"], "-1") == "late"


def test_une_mission_entierement_ANNULEE_le_dit():
    assert _agg(["cancelled", "cancelled"]) == "cancelled"


def test_annulee_et_terminee_melangees_restent_ANNULEES():
    """Sinon une mission arretee en cours de route passerait pour accomplie."""
    assert _agg(["done", "cancelled"]) == "cancelled"


def test_une_annulation_n_empeche_pas_les_autres_d_avancer():
    assert _agg(["cancelled", "running"]) == "running"


def test_tout_termine_sans_annulation_reste_TERMINEE():
    assert _agg(["done", "done"]) == "done"


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUE L'ECRAN AFFICHE
# ══════════════════════════════════════════════════════════════════════════


def _w(state, **kw):
    b = {"id": "w1", "objective": "[Worker api] x", "state": state, "perimeter": [],
         "thought": "", "iteration": 0, "maxIter": 0, "lastTool": "", "queueRank": 0,
         "waitedMs": 0, "proofs": []}
    b.update(kw)
    return b


def _rendu(agg, kids):
    return _node("return V('workshop', %s, null);" % json.dumps(
        [{"id": "m", "objective": "o", "aggregate": agg, "children": kids}]))


def test_l_ecran_dit_ANNULEE_et_jamais_echec():
    h = _rendu("running", [_w("cancelled")])
    assert "annulée" in h and 'data-state="cancelled"' in h
    assert "échec" not in h


def test_l_ecran_dit_INTERROMPUE_et_jamais_travaille():
    """On isole la carte DU WORKER : l'Atelier ajoute toujours une carte
    « Lead » qui, elle, travaille legitimement."""
    h = _rendu("running", [_w("stalled")])
    i = h.index('data-state="stalled"')
    carte = h[i:h.index("</article>", i)]
    assert "interrompue" in carte
    assert "travaille" not in carte


def test_une_tache_interrompue_EXPLIQUE_pourquoi_elle_est_muette():
    h = _rendu("running", [_w("stalled")])
    assert "interrompue au redémarrage" in h and "attend une revue" in h


def test_la_synthese_compte_l_interruption_comme_une_DIFFICULTE():
    h = _node("return globalThis.missionSynthese(%s);" % json.dumps([
        {"aggregate": "stalled", "children": []},
        {"aggregate": "running", "children": []}]))
    assert 'data-agg="stalled"' in h and "attend une revue" in h


def test_une_mission_ANNULEE_n_est_PAS_une_difficulte():
    """Elle est close. Elle n'a pas a tirer l'oeil du bandeau de synthese."""
    h = _node("return globalThis.missionSynthese(%s);" % json.dumps([
        {"aggregate": "cancelled", "children": []},
        {"aggregate": "running", "children": []}]))
    assert "aucune mission en difficulté" in h


# ══════════════════════════════════════════════════════════════════════════
#  4. LE VIDE NE PESE PLUS AUTANT QUE LE PLEIN
# ══════════════════════════════════════════════════════════════════════════


def test_une_pensee_absente_tient_sur_UNE_ligne():
    """Vu au navigateur : cinq cartes sur onze etaient des placeholders aussi
    hauts qu'un vrai raisonnement, et l'ecran disait surtout du vide."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-thought-empty {")
    corps = css[i:css.index("}", i)]
    assert "white-space: nowrap" in corps
    assert "flex: 0 0 auto" in corps, "le bloc vide s'etirait pour remplir la carte"


def test_le_bloc_vide_n_a_plus_l_etiquette_PENSEE():
    """Annoncer « pensée » au-dessus d'une absence de pensee est un contresens."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-thought-empty::before")
    assert "content: none" in css[i:css.index("}", i)]


@pytest.mark.parametrize("etat,mot", [
    ("waiting", "en attente du CodeAgent"), ("stalled", "interrompue"),
    ("cancelled", "annulée"), ("done", "terminée"),
    ("running", "aucun raisonnement transmis"),
])
def test_chaque_silence_dit_SA_raison(etat, mot):
    assert mot in _rendu("running", [_w(etat)])


# ══════════════════════════════════════════════════════════════════════════
#  5. L'HABILLAGE
# ══════════════════════════════════════════════════════════════════════════


def test_annulee_RECULE_et_interrompue_APPELLE():
    """Une annulation n'appelle pas l'oeil ; une interruption, si."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index('.mp-post[data-state="cancelled"],')
    assert "--et: var(--muted)" in css[i:css.index("}", i)]
    j = css.index('.mp-post[data-state="stalled"],')
    assert "--et: var(--warn)" in css[j:css.index("}", j)]


# ══════════════════════════════════════════════════════════════════════════
#  6. LA MESURE QUI A DECLENCHE CE LOT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_TOUS_les_etats_du_corpus_ont_une_traduction():
    """Le vrai garde : si le runtime invente un etat demain, ce test le voit
    avant que l'ecran ne le peigne en « travaille »."""
    taches = json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
    etats = sorted({t["state"] for t in taches if isinstance(t, dict) and t.get("state")})
    connus = {"done", "cancelled", "failed", "checkpointed", "running", "waiting",
              "queued", "pending"}
    inconnus = [e for e in etats if e not in connus]
    assert not inconnus, (
        f"etats persistes sans traduction : {inconnus} — ils s'afficheront "
        f"« travaille », ce qui est le defaut que le lot 11 a ferme"
    )


_FIXTURE = Path(__file__).parents[2] / "tests" / "fixtures" / "mission-panel-corpus-2026-09-23.json"


def test_les_deux_etats_corriges_existent_dans_le_corpus_de_PREUVE():
    """Preuve que ce lot corrige un defaut reel et pas une hypothese.

    REG-2 (24/09/2026) : cette preuve lisait l'etat VIVANT. Elle est tombee des que
    le corpus a change - `cancelled` et `checkpointed` avaient disparu - en annoncant
    que « le correctif n'aurait pas d'objet », alors qu'aucun code n'avait bouge.

    Le corpus d'origine n'est pas recuperable : l'incident ORCH-1 a laisse
    `task_orchestrator_state.CORROMPU-2026-09-23T2139.json`, 12 270 771 octets
    d'octets nuls. La preuve est donc figee dans une fixture, qui porte chacun des
    etats que la mesure du 23/09 avait exhibes.
    """
    taches = [t for t in json.loads(_FIXTURE.read_text(encoding="utf-8"))["tasks"]
              if isinstance(t, dict)]
    n_cancel = sum(1 for t in taches if t.get("state") == "cancelled")
    n_check = sum(1 for t in taches if t.get("state") == "checkpointed")
    assert n_cancel > 0, "le corpus de preuve ne porte plus d'annulation : fixture a refaire"
    assert n_check > 0, "le corpus de preuve ne porte plus d'interruption : fixture a refaire"


@pytest.mark.skipif(not _corpus_exploitable(), reason="corpus absent ou vide sur cette machine")
def test_les_etats_du_corpus_reel_sont_tous_traduits_meme_les_rares():
    """Le CONSTAT sur le vivant : tout etat persiste doit avoir une traduction.

    Celui-la vaut pour n'importe quel corpus, meme vide de `cancelled` : il verifie
    un invariant, pas la presence d'un cas. C'est ce qui le rend stable.
    """
    taches = [t for t in json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
              if isinstance(t, dict)]
    connus = {"done", "cancelled", "failed", "checkpointed", "running", "waiting",
              "queued", "pending"}
    inconnus = sorted({t.get("state") for t in taches if t.get("state")} - connus)
    assert not inconnus, f"etats persistes sans traduction : {inconnus}"
