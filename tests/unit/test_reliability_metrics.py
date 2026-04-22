"""Tests singleton ReliabilityMetrics."""
from src.utils.reliability_metrics import ReliabilityMetrics, get_metrics


def test_singleton():
    a = get_metrics()
    b = get_metrics()
    assert a is b


def test_record_routing():
    m = ReliabilityMetrics()
    m.record_routing(intent="CODE_WRITE", source="llm", confidence=0.9)
    m.record_routing(intent="CHAT", source="regex", confidence=0.4)
    snap = m.snapshot()
    assert snap["routing"]["total"] == 2
    assert snap["routing"]["by_intent"]["CODE_WRITE"] == 1
    assert snap["routing"]["by_source"]["regex"] == 1
    assert 0.6 < snap["routing"]["avg_confidence"] < 0.75


def test_record_policy_refuse():
    m = ReliabilityMetrics()
    m.record_policy_refuse(tool="write_file", path="/x/app.py", project="demo")
    snap = m.snapshot()
    assert snap["policy"]["refuse_count"] == 1
    assert snap["policy"]["refuse_by_tool"]["write_file"] == 1


def test_record_tool_result():
    m = ReliabilityMetrics()
    m.record_tool_result(tool="read_file", success=True)
    m.record_tool_result(tool="run_command", success=False)
    snap = m.snapshot()
    assert snap["tools"]["success_count"] == 1
    assert snap["tools"]["errors_by_tool"]["run_command"] == 1


def test_recent_events_cap():
    m = ReliabilityMetrics()
    for i in range(300):
        m.record_routing(intent="CHAT", source="llm", confidence=0.5)
    snap = m.snapshot()
    # recent_events exposés limités à 50
    assert len(snap["recent_events"]) == 50


def test_snapshot_includes_keys():
    m = ReliabilityMetrics()
    snap = m.snapshot()
    for key in ("uptime_seconds", "routing", "policy", "stickiness", "tools", "recent_events"):
        assert key in snap
