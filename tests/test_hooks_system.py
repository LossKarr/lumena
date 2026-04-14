"""Tests unitaires pour src/hooks/hook_system.py"""
import asyncio
import pytest

from src.hooks.hook_system import (
    HookEvent,
    HookContext,
    Hook,
    HookSystem,
)


# ─── HookContext ───────────────────────────────────────────────────────────

class TestHookContext:
    def test_get_existing_key(self):
        ctx = HookContext(event=HookEvent.STARTUP, data={"user": "alice"})
        assert ctx.get("user") == "alice"

    def test_get_missing_key_with_default(self):
        ctx = HookContext(event=HookEvent.STARTUP)
        assert ctx.get("missing", "fallback") == "fallback"

    def test_get_missing_key_none(self):
        ctx = HookContext(event=HookEvent.STARTUP)
        assert ctx.get("missing") is None


# ─── Hook.execute ──────────────────────────────────────────────────────────

class TestHookExecute:
    @pytest.mark.asyncio
    async def test_execute_runs_handler(self):
        results = []

        async def my_handler(ctx: HookContext):
            results.append(ctx.event)
            return "ok"

        hook = Hook(name="test", event=HookEvent.STARTUP, handler=my_handler)
        ctx = HookContext(event=HookEvent.STARTUP)
        result = await hook.execute(ctx)
        assert result == "ok"
        assert HookEvent.STARTUP in results

    @pytest.mark.asyncio
    async def test_disabled_hook_returns_none(self):
        async def my_handler(ctx):
            return "should not run"

        hook = Hook(name="disabled", event=HookEvent.STARTUP,
                    handler=my_handler, enabled=False)
        result = await hook.execute(HookContext(event=HookEvent.STARTUP))
        assert result is None

    @pytest.mark.asyncio
    async def test_handler_exception_returns_none(self):
        async def bad_handler(ctx):
            raise RuntimeError("handler error")

        hook = Hook(name="bad", event=HookEvent.STARTUP, handler=bad_handler)
        result = await hook.execute(HookContext(event=HookEvent.STARTUP))
        assert result is None


# ─── HookSystem.register ───────────────────────────────────────────────────

class TestHookSystemRegister:
    def test_register_adds_hook(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hook = hs.register(HookEvent.STARTUP, handler, name="my_hook")
        assert hook.name == "my_hook"
        assert hook in hs.hooks[HookEvent.STARTUP]

    def test_register_auto_name(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hook = hs.register(HookEvent.STARTUP, handler)
        assert "startup" in hook.name

    def test_duplicate_name_not_added_twice(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hs.register(HookEvent.STARTUP, handler, name="unique")
        hs.register(HookEvent.STARTUP, handler, name="unique")
        count = sum(1 for h in hs.hooks[HookEvent.STARTUP] if h.name == "unique")
        assert count == 1

    def test_priority_ordering(self):
        hs = HookSystem()
        async def h1(ctx): pass
        async def h2(ctx): pass
        async def h3(ctx): pass
        hs.register(HookEvent.STARTUP, h1, name="low", priority=1)
        hs.register(HookEvent.STARTUP, h2, name="high", priority=10)
        hs.register(HookEvent.STARTUP, h3, name="mid", priority=5)
        hooks = hs.hooks[HookEvent.STARTUP]
        priorities = [h.priority for h in hooks]
        # Should be sorted descending [10, 5, 1]
        assert priorities == sorted(priorities, reverse=True)


# ─── HookSystem.unregister ─────────────────────────────────────────────────

class TestHookSystemUnregister:
    def test_unregister_existing(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hs.register(HookEvent.STARTUP, handler, name="to_remove")
        result = hs.unregister("to_remove")
        assert result is True
        assert all(h.name != "to_remove" for h in hs.hooks[HookEvent.STARTUP])

    def test_unregister_nonexistent(self):
        hs = HookSystem()
        result = hs.unregister("does_not_exist")
        assert result is False


# ─── HookSystem.trigger ────────────────────────────────────────────────────

class TestHookSystemTrigger:
    @pytest.mark.asyncio
    async def test_trigger_runs_handlers(self):
        hs = HookSystem()
        called = []

        async def handler(ctx: HookContext):
            called.append(ctx.data.get("msg"))
            return "done"

        hs.register(HookEvent.MESSAGE_RECEIVED, handler, name="test")
        results = await hs.trigger(HookEvent.MESSAGE_RECEIVED, data={"msg": "hello"})
        assert "hello" in called
        assert "done" in results

    @pytest.mark.asyncio
    async def test_trigger_no_hooks_returns_empty(self):
        hs = HookSystem()
        results = await hs.trigger(HookEvent.CUSTOM)
        assert results == []

    @pytest.mark.asyncio
    async def test_trigger_only_enabled_hooks(self):
        hs = HookSystem()
        called = []

        async def active(ctx):
            called.append("active")

        async def inactive(ctx):
            called.append("inactive")

        hs.register(HookEvent.STARTUP, active, name="active_hook", priority=10)
        hs.register(HookEvent.STARTUP, inactive, name="inactive_hook", priority=5)
        hs.disable("inactive_hook")
        await hs.trigger(HookEvent.STARTUP)
        assert "active" in called
        assert "inactive" not in called

    @pytest.mark.asyncio
    async def test_execution_log_populated(self):
        hs = HookSystem()
        async def handler(ctx): return "ok"
        hs.register(HookEvent.STARTUP, handler, name="logged")
        await hs.trigger(HookEvent.STARTUP)
        assert len(hs._execution_log) >= 1
        assert hs._execution_log[-1]["hook"] == "logged"


# ─── HookSystem.enable / disable ───────────────────────────────────────────

class TestHookSystemEnableDisable:
    def test_disable_hook(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hs.register(HookEvent.STARTUP, handler, name="toggleable")
        result = hs.disable("toggleable")
        assert result is True
        hook = next(h for h in hs.hooks[HookEvent.STARTUP] if h.name == "toggleable")
        assert hook.enabled is False

    def test_enable_hook(self):
        hs = HookSystem()
        async def handler(ctx): pass
        hs.register(HookEvent.STARTUP, handler, name="toggled")
        hs.disable("toggled")
        result = hs.enable("toggled")
        assert result is True
        hook = next(h for h in hs.hooks[HookEvent.STARTUP] if h.name == "toggled")
        assert hook.enabled is True

    def test_disable_nonexistent(self):
        hs = HookSystem()
        assert hs.disable("ghost") is False

    def test_enable_nonexistent(self):
        hs = HookSystem()
        assert hs.enable("ghost") is False


# ─── HookSystem.get_hooks ──────────────────────────────────────────────────

class TestHookSystemGetHooks:
    def test_get_hooks_for_event(self):
        hs = HookSystem()
        async def h1(ctx): pass
        async def h2(ctx): pass
        hs.register(HookEvent.STARTUP, h1, name="s1")
        hs.register(HookEvent.SHUTDOWN, h2, name="sd1")
        startup_hooks = hs.get_hooks(HookEvent.STARTUP)
        assert len(startup_hooks) == 1
        assert startup_hooks[0].name == "s1"

    def test_get_all_hooks(self):
        hs = HookSystem()
        async def h(ctx): pass
        hs.register(HookEvent.STARTUP, h, name="a")
        hs.register(HookEvent.SHUTDOWN, h, name="b")
        all_hooks = hs.get_hooks()
        names = [h.name for h in all_hooks]
        assert "a" in names
        assert "b" in names


# ─── HookSystem.get_stats ──────────────────────────────────────────────────

class TestHookSystemGetStats:
    def test_stats_empty(self):
        hs = HookSystem()
        stats = hs.get_stats()
        assert stats["total_hooks"] == 0
        assert stats["enabled_hooks"] == 0

    def test_stats_with_hooks(self):
        hs = HookSystem()
        async def h(ctx): pass
        hs.register(HookEvent.STARTUP, h, name="h1")
        hs.register(HookEvent.STARTUP, h, name="h2")
        hs.disable("h2")
        stats = hs.get_stats()
        assert stats["total_hooks"] == 2
        assert stats["enabled_hooks"] == 1
        assert stats["disabled_hooks"] == 1


# ─── HookEvent coverage ────────────────────────────────────────────────────

class TestHookEvents:
    def test_all_events_unique(self):
        values = [e.value for e in HookEvent]
        assert len(values) == len(set(values))

    def test_key_events_present(self):
        names = {e.name for e in HookEvent}
        for expected in ["STARTUP", "SHUTDOWN", "MESSAGE_RECEIVED", "CUSTOM"]:
            assert expected in names
