"""LOT F (run RéservaSalle 2026-07-04) — le contrat porte le sens.

Le run RéservaSalle a échoué AVANT la jambe navigateur : le lead a posé un contrat
dont les exports .py étaient des NOMS NUS (`get_all`, `add`, …). Le générateur de
stubs les a traités comme des variables → `get_all  # SIGNATURE FIGÉE`, un stub
NON-fonctionnel. Les 5 workers, sans rien à remplir, ont chacun réinventé leur API
et OUBLIÉ la logique métier (détection de chevauchement jamais implémentée).

F.1 : `validate_contract` refuse les noms nus (.py non-test) — signatures COMPLÈTES
      avec `def`/`class` ou constante `NOM = valeur` obligatoires ;
F.2 : l'exemple guidant montre `desc` (comportement) + signatures ; le `desc` est
      déjà relayé au worker par `worker_objectives`.
"""
from __future__ import annotations

import src.subagents.mission_contract as mc


# ── F.1 : refus des noms nus, acceptation des vraies signatures ──────────────────

def test_bare_name_exports_rejected():
    """Le cas RéservaSalle figé : exports = noms de fonctions sans `def`."""
    errs = mc.validate_contract({
        "project": "ReservaSalle",
        "files": [{"path": "rooms.py", "owner": "w_rooms",
                   "exports": ["get_all", "add", "delete", "init_defaults"]}],
    })
    assert errs, "un export nom-nu doit être refusé"
    joined = " ".join(errs)
    assert "signatures" in joined
    assert "get_all" in joined  # nomme les coupables


def test_real_signatures_accepted():
    errs = mc.validate_contract({
        "project": "ReservaSalle",
        "files": [{"path": "rooms.py", "owner": "w_rooms",
                   "exports": ["def get_all() -> list[dict]",
                               "def add(nom: str, capacite: int) -> dict",
                               "def delete(room_id: int) -> bool"]}],
    })
    assert errs == []


def test_async_and_class_signatures_accepted():
    errs = mc.validate_contract({
        "project": "P",
        "files": [{"path": "svc.py", "owner": "w",
                   "exports": ["async def fetch(url: str) -> dict", "class Store:"]}],
    })
    assert errs == []


def test_module_constant_accepted():
    """Une constante `NOM = valeur` produit un stub Python valide → acceptée."""
    errs = mc.validate_contract({
        "project": "P",
        "files": [{"path": "cfg.py", "owner": "w",
                   "exports": ["DATA_FILE = 'data/rooms.json'",
                               "def load() -> list[dict]"]}],
    })
    assert errs == []


def test_mixed_good_and_bad_flags_only_bad():
    errs = mc.validate_contract({
        "project": "P",
        "files": [{"path": "m.py", "owner": "w",
                   "exports": ["def ok() -> int", "bad_bare_name"]}],
    })
    assert errs
    joined = " ".join(errs)
    assert "bad_bare_name" in joined
    assert "def ok" not in joined  # la bonne signature n'est pas incriminée


def test_helper_is_real_signature():
    assert mc._is_real_signature("def add(x: int) -> dict")
    assert mc._is_real_signature("async def f()")
    assert mc._is_real_signature("class Store:")
    assert mc._is_real_signature("DATA_FILE = 'x.json'")
    assert not mc._is_real_signature("get_all")
    assert not mc._is_real_signature("add")
    assert not mc._is_real_signature("get_all()")  # un appel n'est pas une signature


# ── portes existantes préservées (non-régression B0/BudgetBuddy) ─────────────────

def test_tests_and_init_still_exempt_from_signatures():
    """Les tests et __init__.py n'ont pas d'API publique → pas d'exigence de sig."""
    errs = mc.validate_contract({
        "project": "P",
        "files": [
            {"path": "m.py", "owner": "w", "exports": ["def f() -> int"]},
            # LOT 2.8 : desc désormais requise (sens) — l'exemption testée ici
            # reste celle des SIGNATURES.
            {"path": "tests/test_m.py", "owner": "wt", "desc": "tests de f()"},
            {"path": "tests/__init__.py", "owner": "wt"},  # __init__ : exempt de tout
        ],
    })
    assert errs == []


def test_empty_py_still_rejected():
    """Cas BudgetBuddy inchangé : un .py métier sans AUCUN export reste refusé."""
    errs = mc.validate_contract({
        "project": "P",
        "files": [{"path": "rooms.py", "owner": "w"}],
    })
    assert errs and "signatures" in errs[0] and "no_public_api" in errs[0]


def test_no_public_api_gate_still_works():
    errs = mc.validate_contract({
        "project": "P",
        "files": [{"path": "internal.py", "owner": "w", "no_public_api": True,
                   "desc": "helpers internes sans API publique"}],
    })
    assert errs == []


def test_non_py_files_not_signature_checked():
    """Un .css/.js/.html porte des ancres libres, pas des signatures Python."""
    errs = mc.validate_contract({
        "project": "P",
        "files": [
            {"path": "app.py", "owner": "w", "exports": ["def create_app() -> Flask"]},
            {"path": "static/style.css", "owner": "wf", "api": ["layout terre cuite"]},
            {"path": "static/app.js", "owner": "wf", "api": ["function render()"]},
        ],
    })
    assert errs == []


# ── F.2 : le comportement (`desc`) voyage jusqu'au worker ────────────────────────

def test_desc_injected_into_worker_objective():
    data = {
        "project": "ReservaSalle",
        "files": [{
            "path": "bookings.py", "owner": "w_bookings",
            "desc": "create() REFUSE si le créneau chevauche une résa existante",
            "exports": ["def create(room_id: int, date: str, debut: str, fin: str) -> dict | None"],
        }],
    }
    assert mc.validate_contract(data) == []
    objs = mc.worker_objectives(data)
    assert len(objs) == 1
    text = objs[0]["objective"]
    assert "REFUSE si le créneau chevauche" in text, (
        "le comportement du contrat doit être transmis au worker")
    assert "bookings.py" in objs[0]["allowed_files"][0]


def test_retry_guide_shows_desc_and_signatures():
    import inspect
    import src.reasoning.handlers.missions as m
    src = inspect.getsource(m)
    i = src.find("_retry_guide = (")
    block = src[i:i + 1600]
    assert '"desc"' in block, "l'exemple doit montrer le champ desc (comportement)"
    assert "signatures COMPLÈTES avec def" in block
    assert "JAMAIS un nom nu" in block
