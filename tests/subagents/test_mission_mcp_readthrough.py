"""Lot 5.6.1 — read-through DISCOVERY des MCP actifs dans un registre de mission.

Le registre de mission reste ISOLÉ (jamais de copie de `_dynamic_handlers`) ; il LIT
les MCP actifs vivants du registre boot. Tests STOP de la revue :
- mission VOIT un MCP actif dans schema + description ;
- mission ne COPIE PAS `_dynamic_handlers` ;
- catalog quarantined/removed → INVISIBLE ;
- désactivation (unregister côté boot) → disparaît ;
- read-through visible dans `_allowed_tools` après filtre de contexte.
"""
from types import SimpleNamespace

from src.reasoning.tool_registry import ToolRegistry
from src.mcp.policy import MCPPolicy


class _Res:
    def to_legacy_str(self) -> str:
        return "ok"


def _handler_def(name: str):
    async def _h(ctx, **kw):
        return _Res()
    return SimpleNamespace(
        name=name,
        description=f"MCP tool {name}",
        category="mcp",
        parameters={"properties": {"city": {"type": "string"}}, "required": ["city"]},
        handler=_h,
    )


class _FakeCatalog:
    """is_callable(sid) → True ssi le serveur est ACTIVE (simulé)."""
    def __init__(self, active: bool = True):
        self.active = active

    def is_callable(self, server_id):  # noqa: D401
        return self.active


def _boot_with_mcp(name="weather_now", server_id="weather"):
    boot = ToolRegistry(lumena=None)
    boot.register_dynamic_handler(
        _handler_def(name),
        policy=MCPPolicy.READ_ONLY,
        provenance={"source_kind": "mcp", "server_id": server_id},
    )
    return boot


def _schema_names(reg) -> set:
    return {s["function"]["name"] for s in reg.get_tools_schema()}


def test_mission_sees_active_mcp_via_readthrough():
    boot = _boot_with_mcp()
    mission = ToolRegistry(lumena=None)
    mission.attach_mcp_readthrough(boot, catalog=_FakeCatalog(True))
    assert "weather_now" in _schema_names(mission)
    assert "weather_now" in mission.get_tools_description()


def test_mission_does_not_copy_dynamic_handlers():
    boot = _boot_with_mcp()
    mission = ToolRegistry(lumena=None)
    mission.attach_mcp_readthrough(boot, catalog=_FakeCatalog(True))
    # Vu en découverte, MAIS jamais matérialisé dans le registre mission (isolation).
    assert "weather_now" in _schema_names(mission)
    assert mission._dynamic_handlers == {}
    assert mission.is_dynamic_handler("weather_now") is False
    assert "weather_now" not in mission.tools


def test_quarantined_or_removed_mcp_invisible():
    boot = _boot_with_mcp()
    mission = ToolRegistry(lumena=None)
    # catalog dit NON-callable (quarantined/removed) → read-through doit l'ignorer.
    mission.attach_mcp_readthrough(boot, catalog=_FakeCatalog(active=False))
    assert "weather_now" not in _schema_names(mission)
    assert "weather_now" not in mission.get_tools_description()


def test_deactivation_makes_mcp_disappear():
    boot = _boot_with_mcp()
    mission = ToolRegistry(lumena=None)
    mission.attach_mcp_readthrough(boot, catalog=_FakeCatalog(True))
    assert "weather_now" in _schema_names(mission)
    # Désactivation = unregister côté boot → la mission ne le voit plus (read-through vivant).
    boot.unregister_dynamic_handler("weather_now")
    assert "weather_now" not in _schema_names(mission)


def test_readthrough_visible_in_allowed_tools_after_context_filter():
    boot = _boot_with_mcp()
    mission = ToolRegistry(lumena=None)
    mission.attach_mcp_readthrough(boot, catalog=_FakeCatalog(True))
    mission.apply_context_filter("dis bonjour", intent="chat")
    # Même un filtre restrictif (chat) ne doit pas masquer un MCP read-through actif,
    # sinon execute() (5.6.2) le refuserait.
    assert mission._allowed_tools is None or "weather_now" in mission._allowed_tools
    assert "weather_now" in _schema_names(mission)


def test_no_readthrough_without_attach():
    # Sans attach → un registre normal ne voit aucun MCP read-through.
    mission = ToolRegistry(lumena=None)
    assert list(mission._mcp_readthrough_items()) == []
