"""CONN-5C-2 - variante `kind: "command"` du perimetre d'execution, cote Lumena."""
from __future__ import annotations

import pytest

from src.tools.ide_command_protocol import command_frame
from src.tools.ide_protocol import ProtocolError, negotiate
from tests.tools.test_conn2b_ide_protocol import SESSION, hello

BASE = {"task_id": "task_worker", "role": "worker", "allowed_files": []}
COMMANDE = {**BASE, "execution": {"kind": "command", "id": "command", "command_sha256": "b" * 64}}


def _session():
    message = hello()
    message["protocol"] = {"min": 3, "max": 4}
    return negotiate(message, SESSION)[0]


def _frame(scope):
    return command_frame(_session(), "command_run", {"command": "python -m pytest"}, request_id="request-1",
                         operation_id="ab" * 16, sequence=1, expires_at=1_900_000_000_000, mission_scope=scope)


def test_commande_transmise_telle_quelle():
    assert _frame(COMMANDE)["mission_scope"] == COMMANDE


@pytest.mark.parametrize("execution", [
    {"kind": "command", "id": "command", "command_sha256": None},
    {"kind": "command", "id": "command", "command_sha256": "B" * 64},
    {"kind": "command", "id": "autre", "command_sha256": "b" * 64},
])
def test_commande_mal_formee_refusee(execution):
    with pytest.raises(ProtocolError, match="^command_invalid$"):
        _frame({**BASE, "execution": execution})
