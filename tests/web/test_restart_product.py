from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from web.routes import system


def _request(host: str):
    return SimpleNamespace(client=SimpleNamespace(host=host))


@pytest.mark.asyncio
async def test_restart_is_localhost_only():
    with pytest.raises(HTTPException) as error:
        await system.restart_server(_request("192.0.2.10"), _auth=True)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_restart_prepares_relauncher_before_shutdown(monkeypatch):
    import src.runtime.restart_service as restart_service

    events = []

    def fake_schedule(**kwargs):
        events.append(("schedule", kwargs["current_pid"]))
        return {"scheduled": True}

    async def fake_shutdown(_request, _auth):
        events.append(("shutdown", bool(_auth)))
        return {"status": "shutting_down"}

    monkeypatch.setattr(restart_service, "schedule_desktop_restart", fake_schedule)
    monkeypatch.setattr(system, "shutdown_server", fake_shutdown)
    result = await system.restart_server(_request("127.0.0.1"), _auth=True)
    assert result["status"] == "shutting_down"
    assert [event[0] for event in events] == ["schedule", "shutdown"]


def test_restart_control_is_distinct_from_shutdown_and_cache_busted():
    html = (system._PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    main = (system._PROJECT_ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert 'id="restart-btn"' in html
    assert 'id="shutdown-btn"' in html
    assert 'data-action="_restartLumena"' in html
    assert "/static/js/main.js?v=71" in html
    assert "/api/restart" in main
