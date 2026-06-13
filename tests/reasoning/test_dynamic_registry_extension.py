"""
Tests Phase 8 — Dynamic Registry Extension.

Vérifie l'API publique register/unregister/is/list/provenance de
ToolRegistry pour les handlers dynamiques (typiquement MCP).

Garanties testées :
  - Refus collision native / dynamic / legacy
  - Conversion HandlerDef → entrée legacy avec wrapper V2 (ctx)
  - JSON Schema {properties, required} → legacy params/required
  - _sig_cache géré (register/unregister)
  - Cache lazy : _tool_collection = None, pas de rebuild immédiat
  - discover_tools protégé (snapshot native inclut)
  - HandlerRegistryV2 non touché
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.mcp.policy import MCPPolicy
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef
from src.reasoning.tool_registry import DynamicRegistryError, ToolRegistry


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> ToolRegistry:
    """ToolRegistry réel avec tous les handlers V2 chargés."""
    return ToolRegistry()


def _make_handler_def(
    name: str = "mcp__test__echo",
    description: str = "Echo for test",
    parameters: Dict[str, Any] = None,
    category: str = "mcp",
    handler=None,
) -> HandlerDef:
    """Construit un HandlerDef V2 minimal pour tests."""
    if parameters is None:
        parameters = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
    if handler is None:
        async def _default_handler(ctx, **kwargs):
            return HandlerResult.ok(output=f"echo:{kwargs}")
        handler = _default_handler
    return HandlerDef(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
        category=category,
        source_module="mcp.test",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot natif
# ──────────────────────────────────────────────────────────────────────────────


def test_native_handler_names_frozen_at_boot(registry):
    """Le snapshot natif est un frozenset non vide figé au boot."""
    assert isinstance(registry._native_handler_names, frozenset)
    assert len(registry._native_handler_names) > 0


def test_native_snapshot_includes_discover_tools(registry):
    """discover_tools est ajouté en fin de _load_v2_handlers → DOIT être natif."""
    assert "discover_tools" in registry._native_handler_names


def test_native_snapshot_unchanged_by_dynamic_register(registry):
    """Ajout dynamique ne pollue pas le set natif."""
    before = registry._native_handler_names
    hdef = _make_handler_def(name="mcp__test__echo")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    assert registry._native_handler_names == before
    assert "mcp__test__echo" not in registry._native_handler_names
    registry.unregister_dynamic_handler("mcp__test__echo")


# ──────────────────────────────────────────────────────────────────────────────
# Anti-collision
# ──────────────────────────────────────────────────────────────────────────────


def test_register_dynamic_handler_rejects_empty_name(registry):
    hdef = _make_handler_def(name="")
    with pytest.raises(DynamicRegistryError, match="Invalid"):
        registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)


def test_register_dynamic_handler_rejects_non_string_name(registry):
    hdef = MagicMock()
    hdef.name = 12345
    with pytest.raises(DynamicRegistryError, match="Invalid"):
        registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)


def test_register_dynamic_handler_rejects_native_collision(registry):
    """Refus collision avec un handler natif (ex: read_file)."""
    # Trouver n'importe quel handler natif
    native_name = next(iter(registry._native_handler_names))
    hdef = _make_handler_def(name=native_name)
    with pytest.raises(DynamicRegistryError, match="native"):
        registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)


def test_register_dynamic_handler_rejects_double_register(registry):
    """Refus double register du même nom dynamique."""
    hdef = _make_handler_def(name="mcp__test__double")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        with pytest.raises(DynamicRegistryError, match="already registered"):
            registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    finally:
        registry.unregister_dynamic_handler("mcp__test__double")


def test_register_dynamic_rejects_existing_legacy_registered_tool(registry):
    """Si un outil a été ajouté via legacy register() après boot,
    register_dynamic_handler doit le détecter et refuser."""
    # Ajout via legacy register (post-boot, donc pas dans _native_handler_names)
    registry.register(
        name="temp_legacy_tool",
        description="legacy",
        parameters={},
        handler=lambda **kw: "ok",
    )
    assert "temp_legacy_tool" in registry.tools
    assert "temp_legacy_tool" not in registry._native_handler_names

    hdef = _make_handler_def(name="temp_legacy_tool")
    with pytest.raises(DynamicRegistryError, match="already exists"):
        registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)

    # Cleanup
    registry.tools.pop("temp_legacy_tool", None)


# ──────────────────────────────────────────────────────────────────────────────
# Comportement nominal
# ──────────────────────────────────────────────────────────────────────────────


def test_register_dynamic_appears_in_tools_dict(registry):
    hdef = _make_handler_def(name="mcp__test__tool_in_dict")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert "mcp__test__tool_in_dict" in registry.tools
    finally:
        registry.unregister_dynamic_handler("mcp__test__tool_in_dict")


def test_register_dynamic_appears_in_tool_modules(registry):
    hdef = _make_handler_def(name="mcp__test__in_modules", category="mcp")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry._tool_modules.get("mcp__test__in_modules") == "mcp"
    finally:
        registry.unregister_dynamic_handler("mcp__test__in_modules")


def test_register_dynamic_appears_in_list_dynamic_handlers(registry):
    hdef = _make_handler_def(name="mcp__test__listed")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert "mcp__test__listed" in registry.list_dynamic_handlers()
    finally:
        registry.unregister_dynamic_handler("mcp__test__listed")


def test_is_dynamic_handler_discrimination(registry):
    """True pour dynamic, False pour natif."""
    hdef = _make_handler_def(name="mcp__test__discrim")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry.is_dynamic_handler("mcp__test__discrim") is True
        native_name = next(iter(registry._native_handler_names))
        assert registry.is_dynamic_handler(native_name) is False
        assert registry.is_dynamic_handler("nonexistent_xyz") is False
    finally:
        registry.unregister_dynamic_handler("mcp__test__discrim")


def test_provenance_stored_and_retrievable(registry):
    hdef = _make_handler_def(name="mcp__test__prov")
    prov = {"source_kind": "mcp", "server_name": "test", "ts": "2026-06-01"}
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY, provenance=prov)
    try:
        stored = registry.get_dynamic_handler_provenance("mcp__test__prov")
        assert stored == prov
        # Doit être une copie défensive
        stored["server_name"] = "MUTATED"
        assert registry.get_dynamic_handler_provenance(
            "mcp__test__prov"
        )["server_name"] == "test"
    finally:
        registry.unregister_dynamic_handler("mcp__test__prov")


def test_get_dynamic_handler_provenance_unknown_returns_none(registry):
    assert registry.get_dynamic_handler_provenance("nonexistent") is None


# ──────────────────────────────────────────────────────────────────────────────
# Conversion HandlerDef → legacy
# ──────────────────────────────────────────────────────────────────────────────


def test_dynamic_handler_v2_receives_context(registry):
    """Le wrapper appelle handler avec ctx non None (capture self._v2_context)."""
    received = {}

    async def capture_ctx_handler(ctx, **kw):
        received["ctx"] = ctx
        received["kwargs"] = kw
        return HandlerResult.ok(output="ok")

    hdef = _make_handler_def(
        name="mcp__test__ctx",
        handler=capture_ctx_handler,
    )
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        wrapper = registry.tools["mcp__test__ctx"]["handler"]
        # Appel direct du wrapper
        import asyncio
        result = asyncio.run(wrapper(x="hello"))
        assert isinstance(result, str)
        assert received["ctx"] is not None
        assert received["kwargs"] == {"x": "hello"}
    finally:
        registry.unregister_dynamic_handler("mcp__test__ctx")


def test_dynamic_handler_json_schema_converted_to_legacy_required(registry):
    """input_schema {properties, required} → tools[name] avec params=properties + required=[...]."""
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["url"],
    }
    hdef = _make_handler_def(name="mcp__test__schema", parameters=schema)
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        entry = registry.tools["mcp__test__schema"]
        # parameters = properties (pas tout le schema)
        assert entry["parameters"] == {
            "url": {"type": "string"},
            "timeout": {"type": "integer"},
        }
        # required propagé
        assert entry["required"] == ["url"]
    finally:
        registry.unregister_dynamic_handler("mcp__test__schema")


def test_dynamic_handler_no_properties_keeps_params_as_is(registry):
    """Si pas de "properties", parameters reste tel quel."""
    schema = {"x": {"type": "string"}}  # déjà plat
    hdef = _make_handler_def(name="mcp__test__flat", parameters=schema)
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        entry = registry.tools["mcp__test__flat"]
        assert entry["parameters"] == schema
        assert entry["required"] == []
    finally:
        registry.unregister_dynamic_handler("mcp__test__flat")


# ──────────────────────────────────────────────────────────────────────────────
# _sig_cache management
# ──────────────────────────────────────────────────────────────────────────────


def test_dynamic_handler_sig_cache_added_and_removed(registry):
    """register ajoute (True, None) à _sig_cache, unregister le retire."""
    name = "mcp__test__sigcache"
    assert name not in registry._sig_cache  # avant
    hdef = _make_handler_def(name=name)
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    # Après register : (True, None) = var_keyword, pas de filtre
    assert registry._sig_cache.get(name) == (True, None)
    registry.unregister_dynamic_handler(name)
    # Après unregister : retiré
    assert name not in registry._sig_cache


# ──────────────────────────────────────────────────────────────────────────────
# Rollback / unregister
# ──────────────────────────────────────────────────────────────────────────────


def test_unregister_dynamic_returns_true_and_removes(registry):
    hdef = _make_handler_def(name="mcp__test__remove")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    assert "mcp__test__remove" in registry.tools
    assert registry.unregister_dynamic_handler("mcp__test__remove") is True
    assert "mcp__test__remove" not in registry.tools
    assert "mcp__test__remove" not in registry._dynamic_handlers
    assert "mcp__test__remove" not in registry._tool_modules


def test_unregister_unknown_returns_false_no_raise(registry):
    assert registry.unregister_dynamic_handler("nonexistent_xyz") is False


def test_unregister_native_refused_returns_false(registry):
    """Refus silencieux de désenregistrer un natif (protection)."""
    native_name = next(iter(registry._native_handler_names))
    assert registry.unregister_dynamic_handler(native_name) is False
    # Le natif reste dans tools
    assert native_name in registry.tools


def test_unregister_clears_provenance(registry):
    hdef = _make_handler_def(name="mcp__test__clearprov")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY, provenance={"k": "v"})
    assert registry.get_dynamic_handler_provenance("mcp__test__clearprov") == {"k": "v"}
    registry.unregister_dynamic_handler("mcp__test__clearprov")
    assert registry.get_dynamic_handler_provenance("mcp__test__clearprov") is None


def test_unregister_then_register_works(registry):
    """Cycle complet : register → unregister → register doit marcher."""
    name = "mcp__test__cycle"
    hdef = _make_handler_def(name=name)
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    assert registry.unregister_dynamic_handler(name) is True
    # 2e register doit fonctionner (pas d'état résiduel)
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry.is_dynamic_handler(name) is True
    finally:
        registry.unregister_dynamic_handler(name)


def test_unregister_empty_or_invalid_name_returns_false(registry):
    assert registry.unregister_dynamic_handler("") is False
    assert registry.unregister_dynamic_handler(None) is False  # type: ignore


def test_discover_tools_is_native_protected(registry):
    """discover_tools doit être protégé : unregister retourne False."""
    assert "discover_tools" in registry._native_handler_names
    assert registry.unregister_dynamic_handler("discover_tools") is False
    # Et toujours présent dans tools
    assert "discover_tools" in registry.tools


# ──────────────────────────────────────────────────────────────────────────────
# Cache invalidation
# ──────────────────────────────────────────────────────────────────────────────


def test_register_invalidates_description_cache(registry):
    # Force la création du cache
    registry.get_tools_description()
    assert registry._tools_desc_cache is not None

    hdef = _make_handler_def(name="mcp__test__invalidcache")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry._tools_desc_cache is None
    finally:
        registry.unregister_dynamic_handler("mcp__test__invalidcache")


def test_dynamic_registry_invalidates_tool_collection_not_tool_index(registry):
    """register doit setter _tool_collection à None (lazy rebuild), PAS appeler _init_tool_index."""
    # On force _tool_collection à un sentinel détectable
    sentinel = object()
    registry._tool_collection = sentinel
    assert registry._tool_collection is sentinel

    hdef = _make_handler_def(name="mcp__test__lazy")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        # _tool_collection mis à None (pas de rebuild immédiat)
        assert registry._tool_collection is None
    finally:
        registry.unregister_dynamic_handler("mcp__test__lazy")


def test_unregister_invalidates_caches(registry):
    hdef = _make_handler_def(name="mcp__test__inv_unreg")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    # Force cache description rebuild
    registry.get_tools_description()
    assert registry._tools_desc_cache is not None
    registry._tool_collection = "sentinel"

    registry.unregister_dynamic_handler("mcp__test__inv_unreg")
    assert registry._tools_desc_cache is None
    assert registry._tool_collection is None


def test_register_makes_tool_visible_in_get_tools_schema(registry):
    hdef = _make_handler_def(name="mcp__test__visible_schema")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        schema = registry.get_tools_schema()
        names = {e["function"]["name"] for e in schema}
        assert "mcp__test__visible_schema" in names
    finally:
        registry.unregister_dynamic_handler("mcp__test__visible_schema")


def test_register_makes_tool_visible_in_get_tools_description(registry):
    hdef = _make_handler_def(
        name="mcp__test__visible_desc",
        description="VISIBLE_MARKER_42",
    )
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        desc = registry.get_tools_description()
        assert "VISIBLE_MARKER_42" in desc
    finally:
        registry.unregister_dynamic_handler("mcp__test__visible_desc")


# ──────────────────────────────────────────────────────────────────────────────
# Anti-régression : v2_registry et natifs intouchés
# ──────────────────────────────────────────────────────────────────────────────


def test_v2_registry_untouched_after_dynamic_register(registry):
    """HandlerRegistryV2 ne doit JAMAIS être touché par dynamic register.

    `tool_names` est une @property (pas une méthode) sur HandlerRegistryV2.
    """
    before = sorted(registry._v2_registry.tool_names)
    hdef = _make_handler_def(name="mcp__test__v2_untouched")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        after = sorted(registry._v2_registry.tool_names)
        assert before == after
        # Le nouveau nom n'apparaît PAS dans v2_registry
        assert "mcp__test__v2_untouched" not in after
    finally:
        registry.unregister_dynamic_handler("mcp__test__v2_untouched")


def test_native_handler_still_works_after_dynamic_register(registry):
    """Un handler natif (ex: read_file) doit toujours marcher après register dynamique."""
    # On vérifie au moins que sa configuration reste intacte
    if "read_file" not in registry.tools:
        pytest.skip("read_file not in this registry")
    native_entry_before = dict(registry.tools["read_file"])

    hdef = _make_handler_def(name="mcp__test__no_impact")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        native_entry_after = registry.tools["read_file"]
        # Mêmes clés et même handler (id)
        assert (
            native_entry_after["handler"]
            is native_entry_before["handler"]
        )
        assert native_entry_after["description"] == native_entry_before["description"]
    finally:
        registry.unregister_dynamic_handler("mcp__test__no_impact")


def test_get_tools_description_still_contains_native(registry):
    """Description contient toujours les natifs après register dynamique."""
    hdef = _make_handler_def(name="mcp__test__contains_native")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        desc = registry.get_tools_description()
        # discover_tools natif doit y être
        assert "discover_tools" in desc
    finally:
        registry.unregister_dynamic_handler("mcp__test__contains_native")


def test_tool_modules_native_unchanged(registry):
    """_tool_modules pour les natifs reste inchangé après register dynamique."""
    sample_native = next(iter(registry._native_handler_names))
    cat_before = registry._tool_modules.get(sample_native)

    hdef = _make_handler_def(name="mcp__test__modules_check")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry._tool_modules.get(sample_native) == cat_before
    finally:
        registry.unregister_dynamic_handler("mcp__test__modules_check")


def test_dynamic_handlers_dict_empty_at_boot(registry):
    """Aucun handler dynamique au boot."""
    assert registry._dynamic_handlers == {}
    assert registry._dynamic_provenance == {}
    assert registry.list_dynamic_handlers() == []


# ══════════════════════════════════════════════════════════════════════════════
# Section Phase E — cohabitation natifs ↔ MCP via set_mcp_overlap + filtre
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def registry_with_dynamic_mcp(registry):
    """Registry avec 1 handler MCP enregistre et accessible pour les tests."""
    mcp_name = "mcp__gmail__send_message"
    hdef = _make_handler_def(name=mcp_name, description="Send email via gmail MCP")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    yield registry, mcp_name
    if mcp_name in registry._dynamic_handlers:
        registry.unregister_dynamic_handler(mcp_name)


class TestSetMcpOverlapValidation:
    def test_invalid_mcp_name_raises(self, registry):
        with pytest.raises(DynamicRegistryError, match="Invalid mcp_name"):
            registry.set_mcp_overlap("", ["send_email"])

    def test_non_dynamic_mcp_name_raises(self, registry):
        with pytest.raises(DynamicRegistryError, match="registered dynamic handler"):
            registry.set_mcp_overlap("mcp__not__registered", ["send_email"])

    def test_non_bool_prefer_raises(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        with pytest.raises(DynamicRegistryError, match="prefer_over_native"):
            reg.set_mcp_overlap(mcp_name, [], prefer_over_native=1)  # type: ignore[arg-type]

    def test_non_native_names_in_list_are_filtered_out(
        self, registry_with_dynamic_mcp
    ):
        reg, mcp_name = registry_with_dynamic_mcp
        # `not_a_real_native` n'est pas dans _native_handler_names → filtre.
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native, "not_a_real_native", ""])
        assert reg.get_mcp_overlap(mcp_name) == frozenset({sample_native})


class TestSetMcpOverlapStorage:
    def test_overlap_and_prefer_persisted(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native], prefer_over_native=True)
        assert reg.get_mcp_overlap(mcp_name) == frozenset({sample_native})
        assert reg.get_mcp_prefer_over_native(mcp_name) is True

    def test_default_prefer_is_false(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native])
        assert reg.get_mcp_prefer_over_native(mcp_name) is False

    def test_set_overlap_is_idempotent_overwrite(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        natives = sorted(reg._native_handler_names)
        reg.set_mcp_overlap(mcp_name, [natives[0]])
        reg.set_mcp_overlap(mcp_name, [natives[1]], prefer_over_native=True)
        assert reg.get_mcp_overlap(mcp_name) == frozenset({natives[1]})
        assert reg.get_mcp_prefer_over_native(mcp_name) is True

    def test_set_overlap_invalidates_description_cache(
        self, registry_with_dynamic_mcp
    ):
        reg, mcp_name = registry_with_dynamic_mcp
        # Pre-warm cache.
        _ = reg.get_tools_description()
        assert reg._tools_desc_cache is not None
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native])
        assert reg._tools_desc_cache is None


class TestGetToolsDescriptionPhaseEFilter:
    def test_mcp_without_overlap_is_visible(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        reg.set_mcp_overlap(mcp_name, [])  # explicit no overlap
        desc = reg.get_tools_description()
        assert mcp_name in desc

    def test_mcp_with_overlap_prefer_false_is_hidden(
        self, registry_with_dynamic_mcp
    ):
        reg, mcp_name = registry_with_dynamic_mcp
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native], prefer_over_native=False)
        desc = reg.get_tools_description()
        # MCP cache (natif prioritaire par defaut)
        assert mcp_name not in desc
        # Natif toujours visible
        assert sample_native in desc

    def test_mcp_with_overlap_prefer_true_hides_native(
        self, registry_with_dynamic_mcp
    ):
        reg, mcp_name = registry_with_dynamic_mcp
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native], prefer_over_native=True)
        desc = reg.get_tools_description()
        # MCP visible (prefer=True)
        assert mcp_name in desc
        # Natif cache
        assert f"- {sample_native}(" not in desc

    def test_filter_does_not_affect_unrelated_tools(
        self, registry_with_dynamic_mcp
    ):
        reg, mcp_name = registry_with_dynamic_mcp
        natives = sorted(reg._native_handler_names)
        sample_overlap = natives[0]
        sample_other = natives[1]
        reg.set_mcp_overlap(mcp_name, [sample_overlap], prefer_over_native=True)
        desc = reg.get_tools_description()
        # Le natif non en overlap reste visible
        assert sample_other in desc


class TestUnregisterCleanupPhaseE:
    def test_unregister_clears_overlap_maps(self, registry_with_dynamic_mcp):
        reg, mcp_name = registry_with_dynamic_mcp
        sample_native = next(iter(reg._native_handler_names))
        reg.set_mcp_overlap(mcp_name, [sample_native], prefer_over_native=True)
        assert mcp_name in reg._mcp_overlaps
        reg.unregister_dynamic_handler(mcp_name)
        assert mcp_name not in reg._mcp_overlaps
        assert mcp_name not in reg._mcp_prefer_over_native


class TestPhaseEAccessors:
    def test_list_native_handler_names_returns_sorted_list(self, registry):
        names = registry.list_native_handler_names()
        assert isinstance(names, list)
        assert names == sorted(names)
        assert len(names) > 0

    def test_get_tool_description_returns_string(self, registry):
        sample = next(iter(registry._native_handler_names))
        desc = registry.get_tool_description(sample)
        assert isinstance(desc, str)

    def test_get_tool_description_unknown_returns_empty(self, registry):
        assert registry.get_tool_description("nope_not_a_tool") == ""

    def test_get_tool_description_non_string_returns_empty(self, registry):
        assert registry.get_tool_description(42) == ""  # type: ignore[arg-type]
