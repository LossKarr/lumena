"""CONN-5B-2 - negociation 3/4 et perimetre de mission dans l'enveloppe, cote Lumena.

Le hello n'a aucune liste de capacites et l'enveloppe est fermee : le seul levier
propre est la version. Lumena choisit la plus haute version commune ; en version 3
rien ne change a l'octet pres, et `mission_scope` n'existe qu'en version 4.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.tools.ide_command_protocol import command_frame, encode_frame
from src.tools.ide_protocol import ProtocolError, negotiate
from tests.tools.test_conn2b_ide_protocol import SESSION, hello

SCOPE = {"task_id": "task_worker", "role": "worker", "allowed_files": ["app.py"], "target": "app.py"}


def _session(protocol_range=None):
    message = hello()
    if protocol_range is not None:
        message["protocol"] = protocol_range
    return negotiate(message, SESSION)


def _frame(session, **options):
    return command_frame(session, "write_file", {"path": "app.py", "content": "x"}, request_id="request-1",
                         operation_id="ab" * 16, sequence=1, expires_at=1_900_000_000_000, **options)


@pytest.mark.parametrize("plage,choisie", [
    ({"min": 3, "max": 3}, 3), ({"min": 3, "max": 4}, 4), ({"min": 4, "max": 4}, 4),
    ({"min": 2, "max": 4}, 4), ({"min": 3, "max": 9}, 4),
])
def test_lumena_choisit_la_plus_haute_version_commune(plage, choisie):
    session, ack = _session(plage)
    assert session.protocol == choisie
    assert ack["protocol"] == choisie


@pytest.mark.parametrize("plage", [{"min": 5, "max": 6}, {"min": 1, "max": 2}])
def test_aucune_version_commune(plage):
    with pytest.raises(ProtocolError, match="^protocol_incompatible$"):
        _session(plage)


@pytest.mark.parametrize("plage", [{"min": 3, "max": 3}, {"min": 3, "max": 4}])
def test_commande_ordinaire_identique_en_version_3_et_4(plage):
    session, _ = _session(plage)
    frame = _frame(session)
    assert "mission_scope" not in frame
    reference = _frame(_session({"min": 3, "max": 3})[0])
    assert frame == reference


def test_mission_scope_refuse_en_version_3():
    session, _ = _session({"min": 3, "max": 3})
    with pytest.raises(ProtocolError, match="^mission_scope_unsupported$"):
        _frame(session, mission_scope=SCOPE)


def test_mission_scope_transmis_tel_quel_en_version_4():
    session, _ = _session({"min": 3, "max": 4})
    scope = dict(SCOPE, allowed_files=list(SCOPE["allowed_files"]))
    frame = _frame(session, mission_scope=scope)
    assert frame["mission_scope"] == SCOPE
    scope["allowed_files"].append("autre.py")
    assert frame["mission_scope"] == SCOPE, "la trame doit porter une copie, pas une reference"
    encode_frame(frame)


@pytest.mark.parametrize("scope", [
    dict(SCOPE, extra=True),
    dict(SCOPE, role="admin"),
    dict(SCOPE, task_id="task avec espace"),
    dict(SCOPE, allowed_files="app.py"),
    dict(SCOPE, target="C:/Windows/app.py"),
    dict(SCOPE, target="/abs/app.py"),
    dict(SCOPE, target="../evasion.py"),
    dict(SCOPE, target="dossier\\app.py"),
    dict(SCOPE, target="app\x00.py"),
    dict(SCOPE, allowed_files=["../evasion.py"]),
    dict(SCOPE, allowed_files=[f"f{i}.py" for i in range(257)]),
    {k: v for k, v in SCOPE.items() if k != "target"},
])
def test_perimetre_mal_forme_refuse(scope):
    session, _ = _session({"min": 3, "max": 4})
    with pytest.raises(ProtocolError, match="^command_invalid$"):
        _frame(session, mission_scope=scope)


def test_la_version_negociee_est_portee_par_la_session():
    session, _ = _session({"min": 3, "max": 4})
    assert replace(session, protocol=3).protocol == 3
