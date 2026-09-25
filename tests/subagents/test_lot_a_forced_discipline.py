"""LOT A (run PostuloTrack 2026-07-05) — la discipline atteint le worker, quoi que le
lead écrive.

Constat runtime : le lead a RÉÉCRIT les objectifs de delegate_and_wait de zéro (dérive
d'objectifs). La discipline G, le steer de délégation CodeAgent (I.4) et les riders, qui
ne vivaient QUE dans `worker_objectives()`, se sont perdus → le worker a codé à la main,
aucun `delegate_task` → LOT I n'a jamais tiré.

A.1 : bloc réutilisable `worker_discipline_block` + `inject_worker_discipline` (idempotent).
A.2 : force-injection déterministe dans delegate_and_wait_handler (testée indirectement via
      inject_worker_discipline, le helper pur que le handler appelle).
"""
from __future__ import annotations

from src.subagents.mission_contract import (
    worker_discipline_block,
    inject_worker_discipline,
    worker_objectives,
    WORKER_CODING_DISCIPLINE,
)

_DISC = "DISCIPLINE DE CODAGE"
_STEER = "CODE PAR DÉLÉGATION"  # marqueur du _DELEGATE_CODE_STEER


# ── A.1 : le bloc réutilisable ────────────────────────────────────────────────────

def test_block_code_worker_has_discipline_steer_and_rider():
    block = worker_discipline_block(["tests/test_app.py"])
    assert _DISC in block
    assert _STEER in block            # steer de délégation CodeAgent
    assert "🧪 TESTS" in block         # rider tests


def test_block_backend_rider():
    block = worker_discipline_block(["backend/app.py"])
    assert _STEER in block
    assert "🔌 BACKEND" in block


def test_block_non_code_worker_discipline_but_no_steer():
    """LOT 0 (2026-09-02) — l'INTENTION de ce test est conservée, son texte attendu a
    changé.

    Il vérifiait qu'un worker non-code reçoit « une discipline, mais pas le steer ».
    C'est toujours ce qu'on veut. Mais la discipline servie était
    `WORKER_CODING_DISCIPLINE`, qui s'ouvre sur « agent dev, pas un rédacteur » et
    ordonne « après CHAQUE mutation, EXÉCUTE (module/tests concernés) » — à quelqu'un
    dont le livrable est un README, sans module ni test.

    Le test figeait donc le défaut. Il vérifie désormais que le worker reçoit la
    discipline de SON métier, et toujours pas le steer de délégation CodeAgent."""
    block = worker_discipline_block(["README.md"])
    assert "DISCIPLINE RÉDACTIONNELLE" in block   # la discipline de SON métier
    assert _DISC not in block                     # plus celle du codage
    assert _STEER not in block                    # toujours pas de steer (inchangé)


# ── A.2 : injection idempotente (le helper appelé par le handler) ──────────────────

def test_inject_adds_discipline_to_freeform_lead_objective():
    """Le cas EXACT du run : objectif réécrit par le lead, SANS discipline, code."""
    lead_txt = (
        "[Worker w_tests] 📜 CONTRAT DE MISSION : lis d'abord CONTRAT.md. Tu es "
        "responsable des tests. Remplir tests/test_candidatures.py."
    )
    out = inject_worker_discipline(lead_txt, ["tests/test_candidatures.py"])
    assert _DISC in out
    assert _STEER in out              # le steer de délégation atteint enfin le worker
    assert out.startswith(lead_txt)   # additif : le texte du lead est préservé


def test_inject_idempotent_when_already_present():
    """Objectif généré (déjà la discipline) → aucune duplication."""
    generated = worker_objectives({
        "project": "X",
        "files": [{"path": "backend/app.py", "owner": "w_b", "exports": ["def f()"]}],
    })[0]["objective"]
    assert generated.count(_DISC) == 1
    out = inject_worker_discipline(generated, ["backend/app.py"])
    assert out == generated           # inchangé
    assert out.count(_DISC) == 1      # pas de doublon


def test_inject_couvre_TOUS_les_metiers():
    """LOT 0 — ce test attendait exactement le défaut que le lot corrige.

    Il exigeait `inject_worker_discipline(txt, ["README.md"]) == txt` : le filet du
    LOT A ne s'armait QUE pour le code. Un worker de documents, de données ou de
    recherche dont le lead avait réécrit l'objectif repartait donc avec la prose du
    lead et RIEN d'autre — alors que 66 % des 463 workers du corpus ne touchent jamais
    au CodeAgent.

    Le filet couvre désormais tous les métiers, et reste idempotent."""
    txt = "[Worker w_doc] Rédige le README."
    out = inject_worker_discipline(txt, ["README.md"])
    assert out != txt
    assert "DISCIPLINE RÉDACTIONNELLE" in out
    assert out.startswith(txt)                       # l'objectif du lead est préservé
    assert inject_worker_discipline(out, ["README.md"]) == out   # idempotent

    # Sans aucun fichier ni effet : discipline générale, jamais celle du codage.
    vide = inject_worker_discipline(txt, [])
    assert "DISCIPLINE DE TRAVAIL" in vide
    assert _DISC not in vide


# ── Non-régression : worker_objectives inchangé ───────────────────────────────────

def test_worker_objectives_still_carries_discipline_and_steer():
    objs = worker_objectives({
        "project": "PostuloTrack",
        "files": [
            {"path": "backend/app.py", "owner": "w_backend", "exports": ["def create_app()"]},
            {"path": "tests/test_app.py", "owner": "w_tests", "exports": ["def test_x()"]},
        ],
    })
    by_owner = {o["allowed_files"][0]: o["objective"] for o in objs}
    for text in by_owner.values():
        assert _DISC in text
        assert _STEER in text
    assert "🔌 BACKEND" in by_owner["backend/app.py"]
    assert "🧪 TESTS" in by_owner["tests/test_app.py"]
