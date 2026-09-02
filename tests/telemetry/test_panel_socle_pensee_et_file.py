"""Panel Missions — lot 0 : la donnee qui manquait.

Le panneau Missions ne pouvait afficher que des compteurs. A l'ecran :

    llm_request_start
    agent_iteration_done · 17646ms
    read_file ✓

De la telemetrie, pas du sens. Pendant ce temps le fichier de log ecrivait, a
chaque iteration : `[CodeAgent] 💭 <ce que l'agent cherche a faire>`.

--- Pourquoi `_progress_data` ne suffisait pas ---

La cle `thought` y a ete ajoutee, mais ce dictionnaire alimente
`pending_tasks`, que lit `bg_status`. Le panneau, lui, ecoute
`/api/trace/stream`. Sans emission sur le BUS, la pensee reste invisible a
l'ecran quoi qu'on mette dans le payload de progression.

--- La file du CodeAgent ---

Le CodeAgent est un singleton SERIALISE (LOT 2.12.A) : les workers reflechissent
en parallele mais codent chacun leur tour. Rien ne le disait — « en attente » et
« en train de reflechir » etaient indistincts a l'ecran, d'ou l'impression que
la delegation ne sert a rien.

`codeagent_wait_start` / `codeagent_wait_end` bornent l'attente : la vue Ruban
peut dessiner les hachures, et le panneau afficher le rang dans la file.

--- L'allowlist est un garde, pas une formalite ---

`_sanitize_event` JETTE tout champ absent de `TRACE_EVENT_FIELDS`. C'est ce qui
empeche un payload arbitraire de partir vers les navigateurs abonnes au SSE.
La pensee etant du texte libre ecrit par un modele, elle passe par le meme
assainissement borne que `summary` et `error` — jamais par la recopie brute.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.telemetry.trace_bus import TRACE_EVENT_FIELDS, TraceBus

RACINE = pathlib.Path(__file__).resolve().parents[2]
SUB = RACINE / "src" / "agents" / "sub_agent.py"


@pytest.fixture()
def bus():
    b = TraceBus()
    b.enabled = True
    return b


# ══════════════════════════════════════════════════════════════════════════
#  1. Les trois champs traversent le bus
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("champ", ["thought", "iteration", "max_iter"])
def test_le_champ_est_dans_l_allowlist(champ):
    assert champ in TRACE_EVENT_FIELDS


def test_la_pensee_traverse_le_bus(bus):
    ev = bus._sanitize_event({"stage": "codeagent_iteration", "task_id": "t1",
                              "thought": "Je relis parse.py avant d'editer."})
    assert ev["thought"] == "Je relis parse.py avant d'editer."


def test_l_avancement_traverse_le_bus(bus):
    ev = bus._sanitize_event({"stage": "codeagent_iteration",
                              "iteration": 6, "max_iter": 12})
    assert ev["iteration"] == 6 and ev["max_iter"] == 12


# ══════════════════════════════════════════════════════════════════════════
#  2. L'allowlist reste un garde
# ══════════════════════════════════════════════════════════════════════════


def test_un_champ_hors_allowlist_est_TOUJOURS_jete(bus):
    """Non-regression : l'ouverture ne doit pas devenir une passoire."""
    ev = bus._sanitize_event({"stage": "x", "secret": "ne doit pas sortir",
                              "api_key": "sk-123"})
    assert "secret" not in ev and "api_key" not in ev


def test_la_pensee_est_BORNEE(bus):
    """Elle est ecrite par un modele et part vers les navigateurs abonnes."""
    ev = bus._sanitize_event({"stage": "x", "thought": "z" * 5000})
    assert len(ev["thought"]) <= 400


def test_la_pensee_passe_par_l_assainissement_pas_par_la_recopie_brute():
    """Elle doit etre traitee comme `summary` et `error`, pas comme un int."""
    src = (RACINE / "src" / "telemetry" / "trace_bus.py").read_text(encoding="utf-8")
    i = src.index('for key in TRACE_EVENT_FIELDS')
    bloc = src[i:i + 260]
    assert '"thought"' in bloc, (
        "la pensee doit etre exclue de la boucle de recopie brute, sinon elle "
        "sort du bus sans troncature ni assainissement"
    )


def test_une_pensee_absente_ne_casse_rien(bus):
    ev = bus._sanitize_event({"stage": "x"})
    assert ev.get("thought") in ("", None)


# ══════════════════════════════════════════════════════════════════════════
#  3. L'emission par iteration existe reellement
# ══════════════════════════════════════════════════════════════════════════


def _appels_publish_trace() -> dict:
    """{stage: noeud} pour chaque publish_trace de sub_agent.py."""
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    out = {}
    for n in ast.walk(arbre):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "publish_trace"):
            continue
        for kw in n.keywords:
            if kw.arg == "stage" and isinstance(kw.value, ast.Constant):
                out[kw.value.value] = n
    return out


def test_une_trace_est_emise_a_CHAQUE_iteration():
    assert "codeagent_iteration" in _appels_publish_trace(), (
        "sans cette emission la pensee n'atteint jamais le flux SSE que le "
        "panneau ecoute — elle reste dans le fichier de log"
    )


def test_cette_trace_porte_la_pensee_et_l_avancement():
    n = _appels_publish_trace()["codeagent_iteration"]
    args = {kw.arg for kw in n.keywords}
    for attendu in ("thought", "iteration", "max_iter", "task_id"):
        assert attendu in args, f"{attendu} manque a la trace d'iteration"


def test_la_pensee_est_bornee_a_l_emission_aussi():
    """Ceinture et bretelles : bornee ici ET dans le bus."""
    n = _appels_publish_trace()["codeagent_iteration"]
    for kw in n.keywords:
        if kw.arg == "thought":
            assert isinstance(kw.value, ast.Subscript), "pensee non tronquee a l'emission"
            return
    raise AssertionError("argument thought absent")


# ══════════════════════════════════════════════════════════════════════════
#  4. La file du CodeAgent devient mesurable
# ══════════════════════════════════════════════════════════════════════════


def test_l_attente_du_verrou_est_bornee_par_deux_evenements():
    stages = _appels_publish_trace()
    assert "codeagent_wait_start" in stages, "le debut d'attente n'est pas trace"
    assert "codeagent_wait_end" in stages, "la fin d'attente n'est pas tracee"


def test_la_fin_d_attente_porte_sa_DUREE():
    """Sans duree, on sait qu'il a attendu mais pas combien — inutilisable."""
    n = _appels_publish_trace()["codeagent_wait_end"]
    assert "duration_ms" in {kw.arg for kw in n.keywords}


def test_on_ne_trace_l_attente_que_si_le_verrou_est_DEJA_pris():
    """Une tache qui ne fait pas la queue ne doit pas polluer le flux."""
    src = SUB.read_text(encoding="utf-8")
    i = src.index("_en_file = self._exec_lock.locked()")
    assert "if _en_file:" in src[i:i + 400], (
        "l'emission d'attente n'est pas conditionnee au verrou deja pris"
    )


def test_le_verrou_de_serialisation_est_INTACT():
    """LOT 2.12.A : la sûrete ne doit pas avoir bouge sous pretexte d'affichage."""
    src = SUB.read_text(encoding="utf-8")
    assert "async with self._exec_lock:" in src
    assert "return await self._execute_locked(task)" in src


def test_les_emissions_ne_peuvent_PAS_tuer_le_codeagent():
    """Chaque publish_trace du fichier est sous try/except (telemetrie non critique)."""
    src = SUB.read_text(encoding="utf-8")
    arbre = ast.parse(src)
    parents = {}
    for n in ast.walk(arbre):
        for e in ast.iter_child_nodes(n):
            parents[e] = n
    nus = []
    for n in ast.walk(arbre):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "publish_trace"):
            continue
        cur, protege = n, False
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.Try):
                protege = True
                break
        if not protege:
            nus.append(n.lineno)
    assert not nus, f"publish_trace sans try/except, lignes {nus}"
