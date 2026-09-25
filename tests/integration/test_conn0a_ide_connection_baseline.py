"""CONN-0A: baseline contracts for the Lumena <-> Lumena IDE connection.

These tests intentionally freeze the pre-v3 surface. A connection lot may
change a value only together with its dedicated behavioral proof and the
canonical connection plan.
"""

from __future__ import annotations

import asyncio
import ast
from collections import Counter
from pathlib import Path

from src.reasoning.handlers import ide as ide_handlers


ROOT = Path(__file__).resolve().parents[2]
REACT_PATH = ROOT / "src" / "reasoning" / "react.py"
BRIDGE_PATH = ROOT / "src" / "tools" / "ide_bridge.py"

# Physical lines as returned by str.splitlines(). PowerShell's
# Measure-Object -Line ignores blank lines and must not be used for this guard.
REACT_LINE_BUDGET = 9_761
EXPECTED_HANDLER_COUNT = 33
# CONN-2C separates owner-loop execution, proven with real foreign-loop sockets.
EXPECTED_BRIDGE_ASYNC_METHODS = 41


def _async_methods(path: Path, class_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                child.name
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef)
            ]
    raise AssertionError(f"class {class_name!r} not found in {path}")


def test_react_keeps_the_conn0a_line_budget_and_does_not_own_protocol_v3():
    source = REACT_PATH.read_text(encoding="utf-8")

    assert len(source.splitlines()) <= REACT_LINE_BUDGET
    forbidden_connection_owners = (
        "IDE_CONTROL_PROTOCOL_VERSION",
        "ide_connected",
        "catalog_revision",
        "IDEExternalToolProvider",
        "IDECapabilityService",
        "IDELauncherService",
        "ide__",
    )
    found = [name for name in forbidden_connection_owners if name in source]
    assert found == [], f"IDE connection logic leaked into react.py: {found}"


def test_bridge_keeps_legacy_surface_and_explicit_pairing_lifecycle():
    methods = _async_methods(BRIDGE_PATH, "IDEBridge")

    assert len(methods) == EXPECTED_BRIDGE_ASYNC_METHODS
    assert len(methods) == len(set(methods))
    assert {
        "start_server", "send_command", "read_file", "write_file", "terminal_run",
        "rotate_pairing", "revoke_pairing",
    } <= set(methods)


def test_static_ide_facades_are_unique_and_keep_the_baseline_distribution():
    definitions = ide_handlers.get_ide_handler_defs()
    names = [definition.name for definition in definitions]

    assert len(definitions) == EXPECTED_HANDLER_COUNT
    assert len(names) == len(set(names))
    assert all(name.startswith("ide_") for name in names)
    assert Counter(definition.category for definition in definitions) == {"ide": 33}
    assert {
        "ide_status",
        "ide_read_file",
        "ide_write_file",
        "ide_terminal",
        "ide_launch",
        "ide_get_state",
    } <= set(names)


class _TraceBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def read_file(self, path: str):
        self.calls.append(("read_file", (path,)))
        return {"success": True, "content": "alpha\nbeta"}

    async def write_file(self, path: str, content: str):
        self.calls.append(("write_file", (path, content)))
        return {"success": True, "path": path, "written": len(content)}

    async def terminal_run(self, command: str):
        self.calls.append(("terminal_run", (command,)))
        return {"success": True, "operation_id": "baseline-op", "status": "succeeded"}


def test_v2_read_write_and_terminal_traces_are_captured(monkeypatch):
    bridge = _TraceBridge()
    monkeypatch.setattr(ide_handlers, "_get_bridge", lambda: bridge)

    read = asyncio.run(ide_handlers._handle_ide_read_file(None, path="workspace/a.txt"))
    write = asyncio.run(
        ide_handlers._handle_ide_write_file(None, path="workspace/a.txt", content="updated")
    )
    terminal = asyncio.run(ide_handlers._handle_ide_terminal(None, command="pytest -q"))

    assert bridge.calls == [
        ("read_file", ("workspace/a.txt",)),
        ("write_file", ("workspace/a.txt", "updated")),
        ("terminal_run", ("pytest -q",)),
    ]
    assert (read.success, read.output) == (True, "```\nalpha\nbeta\n```")
    assert (write.success, write.output) == (
        True,
        "Fichier ecrit et ouvert: workspace/a.txt (7 chars)",
    )
    assert (terminal.success, terminal.output) == (
        True,
        "Commande envoyee au terminal IDE: pytest -q",
    )
    # This is the baseline gap CONN-4A must close: structured operation proof
    # is currently discarded by all three compatibility facades.
    assert not hasattr(write, "proof")
    assert not hasattr(terminal, "operation_id")
