import threading

import pytest

from src.reasoning.react import ReActLoop, _REACT_CANCEL_EVENTS


class _Tools:
    def __init__(self):
        self.calls = []

    async def execute(self, name, args, *, caller):
        self.calls.append((name, args, caller))
        return "ok"


@pytest.mark.asyncio
async def test_cancelled_stream_cannot_start_a_new_tool_side_effect():
    loop = ReActLoop.__new__(ReActLoop)
    loop.tools = _Tools()
    event = threading.Event()
    event.set()
    thread_id = threading.get_ident()
    _REACT_CANCEL_EVENTS[thread_id] = event
    try:
        with pytest.raises(SystemExit, match="user_cancelled_react"):
            await loop._execute_tool_with_cancel_guard(
                "open_file", {"path": "document.pdf"}, caller=object(),
            )
        assert loop.tools.calls == []
    finally:
        _REACT_CANCEL_EVENTS.pop(thread_id, None)


@pytest.mark.asyncio
async def test_active_stream_preserves_normal_tool_execution():
    loop = ReActLoop.__new__(ReActLoop)
    loop.tools = _Tools()
    event = threading.Event()
    thread_id = threading.get_ident()
    _REACT_CANCEL_EVENTS[thread_id] = event
    caller = object()
    try:
        result = await loop._execute_tool_with_cancel_guard(
            "open_file", {"path": "document.pdf"}, caller=caller,
        )
        assert result == "ok"
        assert loop.tools.calls == [
            ("open_file", {"path": "document.pdf"}, caller),
        ]
    finally:
        _REACT_CANCEL_EVENTS.pop(thread_id, None)
