import json
from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.autonomy import (
    autonomy_activity_summary_handler,
    autonomy_next_best_action_handler,
    get_autonomy_handler_defs,
)
from src.reasoning.tool_registry import ToolRegistry


@pytest.fixture
def autonomy_logs(tmp_path, monkeypatch):
    import src.reasoning.handlers.autonomy as mod
    import src.autonomy.activity_ledger as ledger_mod

    ops_dir = tmp_path / "data" / "ops"
    ops_dir.mkdir(parents=True)
    journal_path = tmp_path / "data" / "journal.json"
    ops_state_path = ops_dir / "ops_state.json"
    metrics_path = ops_dir / "metrics.jsonl"
    ledger_dir = tmp_path / "data" / "autonomy"
    ledger_dir.mkdir(parents=True)
    ledger_path = ledger_dir / "activity_ledger.jsonl"

    entries = [
        {
            "timestamp": "2026-05-09T00:52:42.560091",
            "handler": "memory_hygiene",
            "data": {"success": True, "dedup_count": 1, "purge_count": 0},
        },
        {
            "timestamp": "2026-05-09T01:37:43.650269",
            "handler": "runtime_health",
            "data": {
                "success": False,
                "reason": "Disque critique: 99.7% utilise",
                "alerts": ["Disque critique: 99.7% utilise"],
            },
        },
        {
            "timestamp": "2026-05-08T23:59:00",
            "handler": "provider_probe",
            "data": {"success": True},
        },
    ]
    metrics_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    journal_path.write_text(
        json.dumps(
            [
                {
                    "timestamp": "2026-05-09T01:27:03.918782",
                    "type": "action",
                    "content": "Action: explore_web: jeux video\nResultat: ok",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ledger_events = [
        {
            "timestamp": "2026-05-09T01:40:00",
            "event_type": "action_candidate",
            "action_type": "explore_web",
            "decision": "considered",
            "description": "Explore web",
        },
        {
            "timestamp": "2026-05-09T01:40:01",
            "event_type": "action_blocked",
            "action_type": "explore_web",
            "decision": "blocked",
            "reason": "disk_guard: free disk 1.3 GB below 10.0 GB",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ledger_events) + "\n",
        encoding="utf-8",
    )
    ops_state_path.write_text(
        json.dumps({"incidents_today": ["incident test"], "saved_at": "2026-05-09T02:00:00"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "OPS_DIR", ops_dir)
    monkeypatch.setattr(mod, "JOURNAL_JSON", journal_path)
    monkeypatch.setattr(mod, "OPS_STATE_JSON", ops_state_path)
    monkeypatch.setattr(ledger_mod, "DATA_DIR", tmp_path / "data")
    mod._ACTIVITY_CACHE.clear()
    return tmp_path


@pytest.mark.asyncio
async def test_autonomy_activity_summary_reads_existing_structured_logs(autonomy_logs):
    ctx = HandlerContext.for_testing(lumena_root=autonomy_logs, runtime_root=autonomy_logs)

    result = await autonomy_activity_summary_handler(ctx, date="2026-05-09", limit=10)

    assert result.success
    assert "Rapport factuel autonomie - 2026-05-09" in result.output
    assert "memory_hygiene" in result.output
    assert "runtime_health" in result.output
    assert "provider_probe" not in result.output
    assert "explore_web: jeux video" in result.output
    assert "Disque critique" in result.output
    assert "Decisions autonomes tracees" in result.output
    assert "action_blocked/explore_web" in result.output
    assert "aucune initiative spontanee" in result.output


@pytest.mark.asyncio
async def test_autonomy_activity_summary_rejects_invalid_date(autonomy_logs):
    ctx = HandlerContext.for_testing(lumena_root=autonomy_logs, runtime_root=autonomy_logs)

    result = await autonomy_activity_summary_handler(ctx, date="09/05/2026")

    assert not result.success
    assert "YYYY-MM-DD" in result.output


@pytest.mark.asyncio
async def test_autonomy_next_best_action_prioritizes_disk_pressure(autonomy_logs):
    ctx = HandlerContext.for_testing(lumena_root=autonomy_logs, runtime_root=autonomy_logs)

    result = await autonomy_next_best_action_handler(ctx, date="2026-05-09")

    assert result.success
    assert "Priority: critical" in result.output
    assert "Action: stabilize_disk_and_report" in result.output
    assert "heavy autonomous work" in result.output


def test_autonomy_activity_summary_registered_as_autonomy_tool():
    defs = get_autonomy_handler_defs()
    tool = next(d for d in defs if d.name == "autonomy_activity_summary")

    assert tool.category == "autonomy"
    assert callable(tool.handler)
    assert "metrics.jsonl" in tool.description

    next_tool = next(d for d in defs if d.name == "autonomy_next_best_action")
    assert next_tool.category == "autonomy"
    assert callable(next_tool.handler)


def test_context_filter_keeps_autonomy_summary_visible_for_chat_activity_question():
    reg = object.__new__(ToolRegistry)
    reg.tools = {
        "autonomy_activity_summary": {"name": "autonomy_activity_summary", "description": "", "parameters": {}},
        "autonomy_next_best_action": {"name": "autonomy_next_best_action", "description": "", "parameters": {}},
        "memory_search": {"name": "memory_search", "description": "", "parameters": {}},
        "get_time": {"name": "get_time", "description": "", "parameters": {}},
        "final_answer": {"name": "final_answer", "description": "", "parameters": {}},
    }
    reg._tool_modules = {
        "autonomy_activity_summary": "autonomy",
        "autonomy_next_best_action": "autonomy",
        "memory_search": "memory",
        "get_time": "system",
        "final_answer": "system",
    }
    reg._allowed_tools = None
    reg._caller_set_allowed = False
    reg._tools_desc_cache = None

    reg.apply_context_filter("tu na rien fait de 00h a mtn", intent="chat")

    assert "autonomy_activity_summary" in reg._allowed_tools
    assert "autonomy_next_best_action" in reg._allowed_tools
    assert "memory_search" in reg._allowed_tools
