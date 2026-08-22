"""LOT G (run FidéliBar 2026-07-04) — « le worker sait coder ».

Le worker recevait le CONTRAT (WORKER_CONTRACT_PREAMBLE) mais pas la discipline de
DEV que le CodeAgent a déjà. Symptômes FidéliBar : boucles de read_file, endpoints
inventés (/api/clients vs /api/customers), IDs incohérents (search-btn vs btn-search),
worker de tests qui mocke la logique produit pour verdir un bug.

G.1 : WORKER_CODING_DISCIPLINE (bloc commun, agnostique du provider) injecté dans
      chaque objectif worker par worker_objectives().
G.2 : riders ciblés par rôle (frontend/backend/tests) déduits des extensions des
      fichiers du worker — pas de matrice provider×worker.

Nuance de revue : le rider tests interdit de mocker la LOGIQUE PRODUIT, mais AUTORISE
les mocks réseau/temps/I/O externes (sinon on casserait des tests légitimes).
"""
from __future__ import annotations

import src.subagents.mission_contract as mc


# ── G.1 : le bloc commun apparaît dans CHAQUE objectif worker ─────────────────────

def _objs(files):
    return mc.worker_objectives({"project": "P", "files": files})


def test_common_block_in_every_worker():
    objs = _objs([
        {"path": "customers.py", "owner": "w_back",
         "exports": ["def add(nom: str) -> dict"]},
        {"path": "index.html", "owner": "w_front", "api": ["#app"]},
        {"path": "test_customers.py", "owner": "w_test"},
    ])
    assert len(objs) == 3
    for o in objs:
        t = o["objective"]
        assert "DISCIPLINE DE CODAGE" in t
        assert "3 lectures MAX" in t
        assert "corrige TON code" in t
        assert "mutation RÉELLE" in t


# ── G.2 : riders ciblés par rôle ──────────────────────────────────────────────────

def test_frontend_rider_only_for_web_files():
    objs = _objs([{"path": "app.js", "owner": "wf", "api": ["render()"]}])
    t = objs[0]["objective"]
    assert "FRONTEND" in t
    assert "querySelector" in t
    assert "routes exposées par le backend" in t
    assert "BACKEND" not in t and "TESTS" not in t


def test_backend_rider_for_py_module():
    objs = _objs([{"path": "app.py", "owner": "wb",
                   "exports": ["def create_app() -> Flask"]}])
    t = objs[0]["objective"]
    assert "BACKEND" in t
    assert "API\n" not in t  # sanity : pas de coupure bizarre
    assert "PERSISTE-le" in t
    assert "FRONTEND" not in t


def test_tests_rider_scoped_mock_rule():
    """La nuance de revue : mock produit INTERDIT, mock réseau/temps/I/O AUTORISÉ."""
    objs = _objs([{"path": "test_transactions.py", "owner": "wt"}])
    t = objs[0]["objective"]
    assert "TESTS" in t
    assert "Ne mocke JAMAIS la logique produit" in t
    assert "réseau" in t and "I/O externes" in t  # mocks légitimes autorisés
    assert "REMONTE-le" in t
    assert "FRONTEND" not in t and "BACKEND" not in t


def test_mixed_worker_cumulates_riders():
    """Un worker qui possède backend + frontend cumule les deux riders."""
    objs = _objs([{"path": "app.py", "owner": "w", "exports": ["def create_app() -> Flask"]},
                  {"path": "index.html", "owner": "w", "api": ["#app"]}])
    assert len(objs) == 1
    t = objs[0]["objective"]
    assert "BACKEND" in t and "FRONTEND" in t
    assert "TESTS" not in t


def test_role_rider_helper_direct():
    assert "FRONTEND" in mc._role_rider(["index.html"])
    assert "BACKEND" in mc._role_rider(["customers.py"])
    assert "TESTS" in mc._role_rider(["test_x.py"])
    assert "TESTS" in mc._role_rider(["x_test.py"])
    # test_*.py est un TEST, pas un backend
    r = mc._role_rider(["test_x.py"])
    assert "BACKEND" not in r
    # aucun rôle reconnu → chaîne vide (zéro bruit)
    assert mc._role_rider(["README.md"]) == ""
    assert mc._role_rider([]) == ""


# ── non-régression : le contrat/périmètre existant est intact ─────────────────────

def test_contract_preamble_and_perimeter_preserved():
    objs = _objs([
        {"path": "a.py", "owner": "wa", "exports": ["def f() -> int"]},
        {"path": "b.py", "owner": "wb", "exports": ["def g() -> int"]},
    ])
    ta = next(o["objective"] for o in objs if o["allowed_files"] == ["a.py"])
    # préambule contrat toujours là
    assert "CONTRAT DE MISSION" in ta
    assert "NE modifie JAMAIS une signature" in ta
    # périmètre : allowed_files inchangé + note « NE touche PAS aux autres »
    assert "NE touche PAS aux autres fichiers" in ta and "b.py" in ta


def test_allowed_files_unchanged():
    objs = _objs([{"path": "svc.py", "owner": "w", "exports": ["def h() -> int"]}])
    assert objs[0]["allowed_files"] == ["svc.py"]
