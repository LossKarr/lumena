"""Compatibility shim for tests that previously imported ``web.server`` directly.

After the v2 refactoring, globals that were in the monolithic ``web/server.py``
are now spread across submodules:

* ``web.routes.deps``   → lumena, telegram_channel, _TASK_ORCHESTRATOR, …
* ``web.routes.chat``   → chat(), chat_stream(), STREAM_EVENT_V2_ENABLED, …
* ``web.routes.system`` → get_status(), TASK_ORCHESTRATOR_V1_ENABLED, …
* ``web.routes.tasks``  → get_session()
* ``web.routes.schemas``→ ChatRequest, ChatResponse, …

This module exposes a single ``server_module`` object that:
- Forwards attribute *reads* to the correct submodule.
- Forwards ``monkeypatch.setattr(server_module, name, value)`` writes to the
  module that *actually uses* the patched variable, so running code sees the
  patch.

Usage in test files::

    from tests._server_compat import server_module
"""

from __future__ import annotations

import web.routes.deps as _deps_module
import web.routes.chat as _chat_module
import web.routes.system as _system_module
import web.routes.tasks as _tasks_module
import web.routes.lifespan as _lifespan_module
from web.routes.schemas import (
    ChatRequest as _ChatRequest,
    ChatResponse as _ChatResponse,
    TaskStartRequest as _TaskStartRequest,
)
from fastapi import HTTPException as _HTTPException

# Ensure AUTONOMY_ON_WEB_ENABLED exists in deps (it normally lives in lifespan)
if not hasattr(_deps_module, "AUTONOMY_ON_WEB_ENABLED"):
    _deps_module.AUTONOMY_ON_WEB_ENABLED = getattr(_lifespan_module, "AUTONOMY_ON_WEB_ENABLED", False)

# ---------------------------------------------------------------------------
# Attribute routing tables
# ---------------------------------------------------------------------------

# Variables that live in web.routes.deps (monkeypatched there)
_DEPS_VARS: frozenset[str] = frozenset({
    "lumena",
    "telegram_channel",
    "whatsapp_channel",
    "_TASK_ORCHESTRATOR",
    "TELEMETRY_AVAILABLE",
    "AUTONOMY_DAEMON_AVAILABLE",
    "AUTONOMY_ON_WEB_ENABLED",
    "_AUTONOMY_DAEMON",
    "_AUTONOMY_LAST_ERROR",
    # Locks / caches used by benchmark runner
    "_CONVERSATION_CACHE_LOCK",
    "_CONVERSATION_CACHE",
    "_SESSION_STATE_LOCK",
    "_SESSION_STATE",
})

# Variables that live directly in system.py
_SYSTEM_LOCAL_VARS: frozenset[str] = frozenset({
    "_SLO_MONITOR",
})

# Variables imported as module-level names in chat.py — must be patched there
_CHAT_LOCAL_VARS: frozenset[str] = frozenset({
    "STREAM_EVENT_V2_ENABLED",
    "OMNICHANNEL_ENVELOPE_V1_ENABLED",
    "_safe_file_edits_for_trace",
    "RUNTIME_CONTEXT_V2_ENABLED",
    "WORKSPACE_POLICY_V2_ENABLED",
})

# Variables that must be patched in BOTH chat.py AND system.py
# (system._task_orchestrator_enabled checks TASK_ORCHESTRATOR_V1_ENABLED, and
#  tasks.py imports that helper from system.py)
_MULTI_MODULE_VARS: frozenset[str] = frozenset({
    "TASK_ORCHESTRATOR_V1_ENABLED",
})


class _ServerCompat:
    """Minimal compatibility proxy — see module docstring."""

    # Static type/function aliases used by tests via ``server_module.X``
    ChatRequest = _ChatRequest
    ChatResponse = _ChatResponse
    HTTPException = _HTTPException
    TaskStartRequest = _TaskStartRequest

    # Route handlers — chat
    chat = staticmethod(_chat_module.chat)
    chat_stream = staticmethod(_chat_module.chat_stream)

    # Route handlers — system
    get_status = staticmethod(_system_module.get_status)
    get_trace_recent = staticmethod(_system_module.get_trace_recent)
    trace_stream = staticmethod(_system_module.trace_stream)
    _get_task_meta = staticmethod(_system_module._get_task_meta)

    # Route handlers — tasks
    get_session = staticmethod(_tasks_module.get_session)
    start_task = staticmethod(_tasks_module.start_task)
    cancel_task = staticmethod(_tasks_module.cancel_task)
    resume_task = staticmethod(_tasks_module.resume_task)
    get_task = staticmethod(_tasks_module.get_task)

    def __getattr__(self, name: str):
        if name in _DEPS_VARS:
            return getattr(_deps_module, name)
        if name in _CHAT_LOCAL_VARS or name in _MULTI_MODULE_VARS:
            return getattr(_chat_module, name)
        if name in _SYSTEM_LOCAL_VARS:
            return getattr(_system_module, name)
        raise AttributeError(f"_ServerCompat has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        if name in _DEPS_VARS:
            setattr(_deps_module, name, value)
        elif name in _CHAT_LOCAL_VARS:
            setattr(_chat_module, name, value)
        elif name in _MULTI_MODULE_VARS:
            # Patch in both chat.py and system.py so all callers see the change
            setattr(_chat_module, name, value)
            setattr(_system_module, name, value)
        elif name in _SYSTEM_LOCAL_VARS:
            setattr(_system_module, name, value)
        else:
            object.__setattr__(self, name, value)


# Singleton proxy used by test files
server_module = _ServerCompat()
