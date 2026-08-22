"""Fragmentation — format/parité du module handlers/missions (Lot 3)."""
from __future__ import annotations

import inspect

from src.reasoning.handlers.missions import get_missions_handler_defs


def test_mission_handler_defs():
    defs = get_missions_handler_defs()
    names = {d.name for d in defs}
    assert names == {
        "create_mission", "list_missions", "mission_status",
        "mission_result", "cancel_mission",
        "delegate_and_wait",       # Lot 5.2 — délégation lead→workers
        "write_mission_contract",  # LOT 2.2 — contrat machine + stubs avant délégation
        "publish_mission_workspace",  # A2 — publication déterministe du livrable
    }


def test_handler_defs_well_formed():
    for d in get_missions_handler_defs():
        assert d.category == "missions"
        assert d.source_module == "handlers.missions"
        assert callable(d.handler)
        assert inspect.iscoroutinefunction(d.handler)  # handlers async
        assert isinstance(d.parameters, dict) and "properties" in d.parameters
        assert isinstance(d.description, str) and d.description


def test_no_codeagent_link():
    # garde-fou : le module missions n'importe PAS le CodeAgent
    import src.reasoning.handlers.missions as m
    for line in inspect.getsource(m).splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            assert "sub_agent" not in s and "delegate_to_agent" not in s
