"""Lot 5.6.2 — read-through EXECUTE des MCP actifs depuis un registre de mission.

Le forward se fait AVANT le lease local (sinon double lease MCP). Tests STOP de la revue :
- forward une seule fois (boot.execute appelé 1×) ;
- pas de double lease (le lease MCP n'est pris qu'une fois, côté boot) ;
- désactivation entre discovery et execute → fail propre (jamais d'exécution fantôme) ;
- catalog removed/quarantined → pas d'exécution ;
- alias court `server__tool` → forward vers `mcp__server__tool` ;
- exécutable même après un filtre de contexte restrictif.
"""
from types import SimpleNamespace

import pytest

import src.subagents.resource_lease as rl
from src.reasoning.tool_registry import ToolRegistry
from src.mcp.policy import MCPPolicy


class _Res:
    def __init__(self, s="ok"):
        self.s = s

    def to_legacy_str(self) -> str:
        return self.s


def _handler_def(name, calls):
    async def _h(ctx, **kw):
        calls.append((name, dict(kw)))
        return _Res(f"done:{name}")
    return SimpleNamespace(
        name=name, description=f"d {name}", category="mcp",
        parameters={"properties": {"city": {"type": "string"}}, "required": ["city"]},
        handler=_h,
    )


class _FakeCatalog:
    def __init__(self, active=True):
        self.active = active

    def is_callable(self, server_id):
        return self.active


def _boot_with(calls, name="mcp__weather__now", sid="weather"):
    boot = ToolRegistry(lumena=None)
    boot.register_dynamic_handler(
        _handler_def(name, calls),
        policy=MCPPolicy.READ_ONLY,
        provenance={"source_kind": "mcp", "server_id": sid},
    )
    return boot


def _mission(boot, catalog):
    m = ToolRegistry(lumena=None)
    m.attach_mcp_readthrough(boot, catalog=catalog)
    return m


@pytest.mark.asyncio
async def test_execute_forwards_once_and_runs():
    calls = []
    boot = _boot_with(calls)
    mission = _mission(boot, _FakeCatalog(True))
    n = {"c": 0}
    _orig = boot.execute

    async def _spy(*a, **k):
        n["c"] += 1
        return await _orig(*a, **k)
    boot.execute = _spy

    obs = await mission.execute("mcp__weather__now", {"city": "Paris"})
    assert obs.success is True
    assert "done:mcp__weather__now" in obs.content
    assert n["c"] == 1
    assert calls == [("mcp__weather__now", {"city": "Paris"})]
    # jamais matérialisé dans le registre mission
    assert "mcp__weather__now" not in mission.tools
    assert mission._dynamic_handlers == {}


@pytest.mark.asyncio
async def test_no_double_lease(monkeypatch):
    holds = {"n": 0}

    class _CM:
        async def __aenter__(self):
            holds["n"] += 1
            return self

        async def __aexit__(self, *a):
            return False

    class _Lease:
        def hold(self, key, timeout=None):
            return _CM()

    monkeypatch.setattr(rl, "get_resource_lease", lambda: _Lease())
    calls = []
    boot = _boot_with(calls)
    mission = _mission(boot, _FakeCatalog(True))
    obs = await mission.execute("mcp__weather__now", {"city": "Paris"})
    assert obs.success is True
    # lease MCP pris UNE seule fois (côté boot) ; la mission n'en prend aucun (forward avant).
    assert holds["n"] == 1


@pytest.mark.asyncio
async def test_deactivation_between_discovery_and_execute():
    calls = []
    boot = _boot_with(calls)
    mission = _mission(boot, _FakeCatalog(True))
    assert "mcp__weather__now" in {s["function"]["name"] for s in mission.get_tools_schema()}
    # désactivation = unregister côté boot
    boot.unregister_dynamic_handler("mcp__weather__now")
    obs = await mission.execute("mcp__weather__now", {"city": "Paris"})
    assert obs.success is False         # fail propre
    assert calls == []                  # jamais exécuté (pas de fantôme)


@pytest.mark.asyncio
async def test_quarantined_not_executed():
    calls = []
    boot = _boot_with(calls)
    mission = _mission(boot, _FakeCatalog(active=False))  # quarantined/removed
    obs = await mission.execute("mcp__weather__now", {"city": "Paris"})
    assert obs.success is False
    assert calls == []


@pytest.mark.asyncio
async def test_short_alias_forwards():
    calls = []
    boot = _boot_with(calls, name="mcp__weather__now", sid="weather")
    mission = _mission(boot, _FakeCatalog(True))
    obs = await mission.execute("weather__now", {"city": "Paris"})  # alias court
    assert obs.success is True
    assert calls == [("mcp__weather__now", {"city": "Paris"})]


@pytest.mark.asyncio
async def test_executable_after_restrictive_context_filter():
    calls = []
    boot = _boot_with(calls)
    mission = _mission(boot, _FakeCatalog(True))
    mission.apply_context_filter("dis bonjour", intent="chat")  # filtre restrictif
    obs = await mission.execute("mcp__weather__now", {"city": "Paris"})
    assert obs.success is True
    assert calls == [("mcp__weather__now", {"city": "Paris"})]
