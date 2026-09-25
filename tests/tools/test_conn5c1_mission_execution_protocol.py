"""CONN-5C-1 - variante `execution` du perimetre de mission, cote Lumena.

Un perimetre porte exactement UNE des deux formes : `target` (ecriture, 5B-2) ou
`execution` (tache ou test, 5C-1). Toujours reserve au protocole 4.
"""
from __future__ import annotations

import pytest

from src.tools.ide_command_protocol import command_frame
from src.tools.ide_protocol import ProtocolError, negotiate
from tests.tools.test_conn2b_ide_protocol import SESSION, hello

BASE = {"task_id": "task_worker", "role": "worker", "allowed_files": []}
TACHE = {**BASE, "execution": {"kind": "task", "id": "package:build", "command_sha256": "a" * 64}}
TEST = {**BASE, "execution": {"kind": "test", "id": "pytest:test_app.py", "command_sha256": None}}


def _session(maximum=4):
    message = hello()
    message["protocol"] = {"min": 3, "max": maximum}
    return negotiate(message, SESSION)[0]


def _frame(session, scope, action="task_run", params=None):
    return command_frame(session, action, params or {"taskId": "package:build"}, request_id="request-1",
                         operation_id="ab" * 16, sequence=1, expires_at=1_900_000_000_000, mission_scope=scope)


@pytest.mark.parametrize("scope", [TACHE, TEST])
def test_execution_transmise_telle_quelle_en_version_4(scope):
    frame = _frame(_session(), scope)
    assert frame["mission_scope"] == scope
    assert frame["mission_scope"] is not scope
    assert frame["mission_scope"]["execution"] is not scope["execution"]


def test_execution_refusee_en_version_3():
    with pytest.raises(ProtocolError, match="^mission_scope_unsupported$"):
        _frame(_session(3), TACHE)


@pytest.mark.parametrize("scope", [
    {**TACHE, "target": "app.py"},
    dict(BASE),
    {**BASE, "execution": {"kind": "shell", "id": "x", "command_sha256": None}},
    {**BASE, "execution": {"kind": "task", "id": "package:build", "command_sha256": None}},
    {**BASE, "execution": {"kind": "test", "id": "pytest:a", "command_sha256": "a" * 64}},
    {**BASE, "execution": {"kind": "task", "id": "package:build", "command_sha256": "A" * 64}},
    {**BASE, "execution": {"kind": "task", "id": "", "command_sha256": "a" * 64}},
    {**BASE, "execution": {"kind": "task", "id": "x" * 513, "command_sha256": "a" * 64}},
    {**BASE, "execution": {"kind": "task", "id": "package:build", "command_sha256": "a" * 64, "extra": 1}},
    {**BASE, "execution": ["task", "package:build"]},
])
def test_execution_mal_formee_refusee(scope):
    with pytest.raises(ProtocolError, match="^command_invalid$"):
        _frame(_session(), scope)
