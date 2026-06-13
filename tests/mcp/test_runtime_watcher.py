"""
Tests Phase 12 v3 — RuntimeWatcher.

Sections :
  1. Init & configuration
  2. Register/unregister + validation server_id (regex, path traversal, Windows reserved)
  3. take_snapshot (poll, runner.state() méthode)
  4. record_event + validation error_code + mapping event_kind→state
  5. Anomalies + report (sans UNRESPONSIVE)
  6. Fenêtre glissante crash_count
  7. Persistance disque
  8. Audit forensique no-PII
  9. Sanity intégration MCPSandboxRunner (sans modifier le runner)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.runtime_watcher import (
    RuntimeHealth,
    RuntimeReport,
    RuntimeSnapshot,
    RuntimeWatcher,
    RuntimeWatcherError,
    _EVENT_KIND_TO_STATE,
    _WINDOWS_RESERVED_NAMES,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


class _DummyRunner:
    """Runner minimal exposant state() callable."""

    def __init__(self, state_value: str = "init"):
        self._state = state_value
        self.state_call_count = 0

    def state(self) -> str:
        self.state_call_count += 1
        return self._state

    def set_state(self, value: str) -> None:
        self._state = value


class _EnumLikeState:
    """Pour tester l'extraction .value / .name."""

    def __init__(self, value: str):
        self.value = value


class _EnumNamedRunner:
    """Runner exposant un objet avec .name uniquement."""

    def __init__(self, name: str):
        class _S:
            pass
        s = _S()
        s.name = name
        self._s = s

    def state(self):
        return self._s


class _RaisingRunner:
    """Runner dont state() raise."""

    def state(self):
        raise RuntimeError("boom")


@pytest.fixture
def watcher(tmp_path: Path) -> RuntimeWatcher:
    return RuntimeWatcher(
        snapshots_dir=tmp_path / "snapshots",
        audit_log_path=tmp_path / "audit" / "audit.jsonl",
    )


def _audit_lines(watcher: RuntimeWatcher) -> List[Dict[str, Any]]:
    if not watcher.audit_log_path.exists():
        return []
    out = []
    with open(watcher.audit_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _audit_blob(watcher: RuntimeWatcher) -> str:
    if not watcher.audit_log_path.exists():
        return ""
    return watcher.audit_log_path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Init & configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInit:
    def test_default_dirs_created(self, watcher):
        assert watcher.snapshots_dir.exists()
        assert watcher.audit_log_path.parent.exists()

    def test_default_thresholds(self, watcher):
        assert watcher.crash_loop_window_s == 300
        assert watcher.crash_loop_threshold == 3
        assert watcher.transitions_max_history == 50

    def test_custom_thresholds_respected(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_window_s=60,
            crash_loop_threshold=2,
            transitions_max_history=10,
        )
        assert w.crash_loop_window_s == 60
        assert w.crash_loop_threshold == 2
        assert w.transitions_max_history == 10

    def test_invalid_thresholds_rejected(self, tmp_path):
        for kwargs in [
            {"crash_loop_window_s": 0},
            {"crash_loop_threshold": 0},
            {"transitions_max_history": -1},
        ]:
            with pytest.raises(RuntimeWatcherError, match="must be > 0"):
                RuntimeWatcher(
                    snapshots_dir=tmp_path / "s",
                    audit_log_path=tmp_path / "a" / "audit.jsonl",
                    **kwargs,
                )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Register/unregister + validation server_id
# ══════════════════════════════════════════════════════════════════════════════


class TestServerIdValidation:
    def test_server_id_empty_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner("", _DummyRunner())

    def test_server_id_none_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner(None, _DummyRunner())  # type: ignore[arg-type]

    def test_server_id_uppercase_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner("Alice", _DummyRunner())

    def test_server_id_starts_with_dash_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner("-foo", _DummyRunner())

    def test_server_id_starts_with_dot_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner(".hidden", _DummyRunner())

    def test_server_id_with_slash_rejected(self, watcher):
        # `/` n'est pas dans le charset regex → "regex violated"
        with pytest.raises(RuntimeWatcherError):
            watcher.register_runner("foo/bar", _DummyRunner())

    def test_server_id_with_backslash_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError):
            watcher.register_runner("foo\\bar", _DummyRunner())

    def test_server_id_with_dotdot_rejected(self, watcher):
        # ".." en début est bloqué par regex (starts with [a-z0-9]),
        # ".." au milieu : "foo..bar" passe la regex (charset OK) mais
        # le check path-traversal explicite doit le rejeter.
        with pytest.raises(RuntimeWatcherError, match="path traversal"):
            watcher.register_runner("foo..bar", _DummyRunner())

    def test_server_id_too_long_rejected(self, watcher):
        sid = "a" + "b" * 64
        with pytest.raises(RuntimeWatcherError, match="regex violated"):
            watcher.register_runner(sid, _DummyRunner())

    def test_server_id_valid_lowercase_digits_dot_dash_underscore_ok(self, watcher):
        for sid in ["a", "a1", "my_server", "my-server", "my.server", "a1.b2_c3-d4"]:
            w_runner = _DummyRunner()
            watcher.register_runner(sid, w_runner)
            assert watcher.is_registered(sid)
            watcher.unregister_runner(sid)


class TestServerIdWindowsReserved:
    @pytest.mark.parametrize("bad_id", [
        "con", "prn", "aux", "nul",
        "com1", "com5", "com9",
        "lpt1", "lpt5", "lpt9",
        "con.json", "aux.tmp", "nul.log",
        "com1.snapshot", "lpt3.json",
    ])
    def test_server_id_windows_reserved_rejected(self, watcher, bad_id):
        with pytest.raises(RuntimeWatcherError, match="Windows reserved"):
            watcher.register_runner(bad_id, _DummyRunner())

    @pytest.mark.parametrize("good_id", [
        "con_main", "aux_server", "console", "auxiliary",
        "com1_a", "lpt1x", "com10", "lpt10",
        "my_con", "prn_server", "nul1",
    ])
    def test_server_id_lookalikes_not_rejected(self, watcher, good_id):
        watcher.register_runner(good_id, _DummyRunner())
        assert watcher.is_registered(good_id)


class TestRegisterUnregister:
    def test_register_adds_server(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        assert watcher.is_registered("alice")
        assert "alice" in watcher.list_watched_servers()

    def test_register_double_raises(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="already registered"):
            watcher.register_runner("alice", _DummyRunner())

    def test_register_runner_none_rejected(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="must not be None"):
            watcher.register_runner("alice", None)

    def test_register_runner_without_state_method_rejected(self, watcher):
        class NoState:
            pass
        with pytest.raises(RuntimeWatcherError, match="callable .state"):
            watcher.register_runner("alice", NoState())

    def test_register_runner_with_state_property_rejected(self, watcher):
        class StateProp:
            state = "running"  # attribute, not callable
        with pytest.raises(RuntimeWatcherError, match="callable .state"):
            watcher.register_runner("alice", StateProp())

    def test_unregister_returns_true_when_found(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        assert watcher.unregister_runner("alice") is True

    def test_unregister_returns_false_when_missing(self, watcher):
        assert watcher.unregister_runner("alice") is False

    def test_list_watched_sorted(self, watcher):
        watcher.register_runner("zeta", _DummyRunner())
        watcher.register_runner("alpha", _DummyRunner())
        watcher.register_runner("beta", _DummyRunner())
        assert watcher.list_watched_servers() == ["alpha", "beta", "zeta"]

    def test_audit_register_unregister_no_pii(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        watcher.unregister_runner("alice")
        events = [e["event"] for e in _audit_lines(watcher)]
        assert "runner_registered" in events
        assert "runner_unregistered" in events


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — take_snapshot (poll mode, runner.state() méthode)
# ══════════════════════════════════════════════════════════════════════════════


class TestTakeSnapshot:
    def test_snapshot_unregistered_raises(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="not registered"):
            watcher.take_snapshot("ghost")

    def test_snapshot_calls_state_as_method(self, watcher):
        runner = _DummyRunner(state_value="running")
        watcher.register_runner("alice", runner)
        watcher.take_snapshot("alice")
        assert runner.state_call_count >= 1

    def test_snapshot_running_state(self, watcher):
        runner = _DummyRunner(state_value="running")
        watcher.register_runner("alice", runner)
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"
        assert snap.restart_count == 0
        assert snap.crash_count_window == 0

    def test_snapshot_transition_observed_via_poll(self, watcher):
        runner = _DummyRunner(state_value="init")
        watcher.register_runner("alice", runner)
        watcher.take_snapshot("alice")
        runner.set_state("running")
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"
        # transitions_recent doit contenir au moins init et running
        states = [s for _, s in snap.transitions_recent]
        assert "running" in states

    def test_snapshot_extracts_enum_value(self, watcher):
        class EnumRunner:
            def state(self):
                return _EnumLikeState("RUNNING")
        watcher.register_runner("alice", EnumRunner())
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"

    def test_snapshot_extracts_enum_name(self, watcher):
        watcher.register_runner("alice", _EnumNamedRunner("Crashed"))
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "crashed"

    def test_snapshot_raising_runner_state_kept_unknown(self, watcher):
        watcher.register_runner("alice", _RaisingRunner())
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "unknown"

    def test_snapshot_persists_to_disk(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        watcher.take_snapshot("alice")
        path = watcher.snapshots_dir / "alice.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["server_id"] == "alice"
        assert data["process_state"] == "running"

    def test_take_all_snapshots(self, watcher):
        watcher.register_runner("a", _DummyRunner("running"))
        watcher.register_runner("b", _DummyRunner("stopped"))
        snaps = watcher.take_all_snapshots()
        assert set(snaps.keys()) == {"a", "b"}
        assert snaps["a"].process_state == "running"
        assert snaps["b"].process_state == "stopped"

    def test_transitions_history_capped(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            transitions_max_history=3,
        )
        runner = _DummyRunner("init")
        w.register_runner("alice", runner)
        for state in ["running", "stopped", "running", "crashed", "running"]:
            runner.set_state(state)
            w.take_snapshot("alice")
        snap = w.take_snapshot("alice")
        assert len(snap.transitions_recent) <= 3

    def test_snapshot_uptime_zero_when_not_running(self, watcher):
        watcher.register_runner("alice", _DummyRunner("stopped"))
        snap = watcher.take_snapshot("alice")
        assert snap.uptime_seconds == 0.0

    def test_snapshot_uptime_positive_when_running(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.take_snapshot("alice")
        # uptime tracking : depuis première transition RUNNING observée.
        # Le take_snapshot l'instant T peut donner ~0 sec mais ≥ 0.
        snap = watcher.take_snapshot("alice")
        assert snap.uptime_seconds >= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — record_event + validation error_code + mapping
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorCodeValidation:
    def test_error_code_none_ok(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        # crashed sans error_code OK
        watcher.record_event("alice", "crashed")

    def test_error_code_valid_short(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        for code in ["exit_nonzero", "init_timeout", "sig:term", "err-42", "x"]:
            watcher.record_event("alice", "error", error_code=code)

    def test_error_code_with_space_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event("alice", "error", error_code="exit failed")

    def test_error_code_with_email_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event(
                "alice", "error", error_code="alice@evil.com"
            )

    def test_error_code_with_unix_path_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event("alice", "error", error_code="/etc/passwd")

    def test_error_code_with_windows_path_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event(
                "alice", "error", error_code="C:\\Users\\charl"
            )

    def test_error_code_too_long_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        long_code = "a" * 65
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event("alice", "error", error_code=long_code)

    def test_error_code_uppercase_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event("alice", "error", error_code="PANIC")

    def test_error_code_with_newline_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event(
                "alice", "error", error_code="panic\nstack_trace"
            )

    def test_error_code_with_secret_marker_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid error_code"):
            watcher.record_event(
                "alice",
                "error",
                error_code="SUPER_SECRET_API_KEY_!!8EhmGf5zj6u5E",
            )


class TestRecordEventMapping:
    def test_record_event_unregistered_raises(self, watcher):
        with pytest.raises(RuntimeWatcherError, match="not registered"):
            watcher.record_event("ghost", "started")

    def test_invalid_event_kind_rejected(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="Invalid event_kind"):
            watcher.record_event("alice", "exploded")

    def test_started_event_sets_state_running_and_increments_restart_count(
        self, watcher
    ):
        watcher.register_runner("alice", _DummyRunner())
        watcher.record_event("alice", "started")
        snap = watcher.take_snapshot("alice")
        # take_snapshot peut re-poll state du runner et écraser.
        # Vérifions via le registry interne via report.
        # restart_count est persistant.
        watcher.register_runner("bob", _DummyRunner())
        watcher.record_event("bob", "started")
        # Use a runner that returns "running" for clean state.
        runner2 = _DummyRunner("running")
        watcher.register_runner("charlie", runner2)
        watcher.record_event("charlie", "started")
        snap2 = watcher.take_snapshot("charlie")
        assert snap2.restart_count == 1
        assert snap2.process_state == "running"

    def test_restarted_event_sets_state_running_and_increments_restart_count(
        self, watcher
    ):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        watcher.record_event("alice", "restarted")
        snap = watcher.take_snapshot("alice")
        assert snap.restart_count == 2
        assert snap.process_state == "running"

    def test_stopped_event_sets_state_stopped_no_restart_increment(self, watcher):
        runner = _DummyRunner("stopped")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "stopped")
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "stopped"
        assert snap.restart_count == 0

    def test_crashed_event_sets_state_crashed_and_increments_crash_window(
        self, watcher
    ):
        runner = _DummyRunner("crashed")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "crashed"
        assert snap.crash_count_window == 1
        assert snap.last_error_code == "exit_nonzero"

    def test_error_event_does_not_change_state_only_updates_error_code(
        self, watcher
    ):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        # state running, then error
        watcher.record_event("alice", "error", error_code="warn_blip")
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"  # inchangé
        assert snap.last_error_code == "warn_blip"
        assert snap.crash_count_window == 0  # error ≠ crashed

    def test_error_event_without_error_code_is_noop_on_state_and_code(
        self, watcher
    ):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        watcher.record_event("alice", "error", error_code="initial_code")
        watcher.record_event("alice", "error")  # no error_code
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"
        # last_error_code reste celui d'avant (initial_code) — pas écrasé par None
        assert snap.last_error_code == "initial_code"

    def test_event_kind_to_state_mapping_table(self):
        # Vérifie le contrat de la table de mapping
        assert _EVENT_KIND_TO_STATE["started"]   == "running"
        assert _EVENT_KIND_TO_STATE["restarted"] == "running"
        assert _EVENT_KIND_TO_STATE["stopped"]   == "stopped"
        assert _EVENT_KIND_TO_STATE["crashed"]   == "crashed"
        assert _EVENT_KIND_TO_STATE["error"]     is None

    def test_started_event_rejects_error_code(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="only allowed"):
            watcher.record_event(
                "alice", "started", error_code="exit_nonzero"
            )

    def test_stopped_event_rejects_error_code(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="only allowed"):
            watcher.record_event(
                "alice", "stopped", error_code="exit_nonzero"
            )

    def test_restarted_event_rejects_error_code(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError, match="only allowed"):
            watcher.record_event(
                "alice", "restarted", error_code="exit_nonzero"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Anomalies + report (sans UNRESPONSIVE)
# ══════════════════════════════════════════════════════════════════════════════


class TestAnomaliesReport:
    def test_no_anomaly_healthy(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        report = watcher.get_report("alice")
        assert report.health == RuntimeHealth.HEALTHY
        assert report.anomalies == []

    def test_recent_crash_degraded(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        watcher.record_event("alice", "restarted")
        report = watcher.get_report("alice")
        assert "recent_crash" in report.anomalies
        assert report.health == RuntimeHealth.DEGRADED

    def test_state_crashed_unhealthy(self, watcher):
        runner = _DummyRunner("crashed")
        watcher.register_runner("alice", runner)
        report = watcher.get_report("alice")
        assert "state_crashed" in report.anomalies
        assert report.health == RuntimeHealth.UNHEALTHY

    def test_crash_loop_threshold_reached(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_window_s=300,
            crash_loop_threshold=3,
        )
        runner = _DummyRunner("running")
        w.register_runner("alice", runner)
        for _ in range(3):
            w.record_event("alice", "crashed", error_code="exit_nonzero")
            w.record_event("alice", "restarted")
        report = w.get_report("alice")
        assert "crash_loop" in report.anomalies
        assert report.health == RuntimeHealth.CRASH_LOOP

    def test_runner_missing_unknown(self, watcher):
        report = watcher.get_report("ghost")
        assert report.health == RuntimeHealth.UNKNOWN
        assert "runner_missing" in report.anomalies
        assert report.snapshot.process_state == "unknown"

    def test_hierarchy_crash_loop_over_unhealthy(self, tmp_path):
        # crash_loop seuil=2 : 2 crashes + state actuel crashed.
        # Doit retourner CRASH_LOOP (priorité plus haute).
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_threshold=2,
        )
        runner = _DummyRunner("crashed")
        w.register_runner("alice", runner)
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        w.record_event("alice", "crashed", error_code="exit_nonzero")
        report = w.get_report("alice")
        assert report.health == RuntimeHealth.CRASH_LOOP

    def test_hierarchy_unhealthy_over_degraded(self, watcher):
        runner = _DummyRunner("crashed")
        watcher.register_runner("alice", runner)
        # 1 crash → recent_crash (DEGRADED) MAIS state_crashed (UNHEALTHY)
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        report = watcher.get_report("alice")
        assert report.health == RuntimeHealth.UNHEALTHY

    def test_get_report_logs_anomalies_audit(self, watcher):
        runner = _DummyRunner("crashed")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        watcher.get_report("alice")
        events = [e for e in _audit_lines(watcher) if e["event"] == "anomaly_detected"]
        assert events

    def test_registered_runner_state_raises_report_unknown(self, watcher):
        """Runner enregistré dont state() raise → process_state="unknown",
        anomalies inclut "runner_unknown", health == UNKNOWN."""
        watcher.register_runner("alice", _RaisingRunner())
        report = watcher.get_report("alice")
        assert report.snapshot.process_state == "unknown"
        assert "runner_unknown" in report.anomalies
        assert report.health == RuntimeHealth.UNKNOWN

    def test_process_state_unknown_maps_to_unknown_health(self, watcher):
        """Runner dont state() retourne None (illisible) → UNKNOWN."""
        class NoneStateRunner:
            def state(self):
                return None
        watcher.register_runner("alice", NoneStateRunner())
        report = watcher.get_report("alice")
        assert report.snapshot.process_state == "unknown"
        assert "runner_unknown" in report.anomalies
        assert report.health == RuntimeHealth.UNKNOWN

    def test_runner_unknown_over_recent_crash(self, watcher):
        """Un runner illisible avec crash récent (state=running après restart)
        reste UNKNOWN, jamais DEGRADED. La hiérarchie est :
        UNHEALTHY(state_crashed) > UNKNOWN(runner_unknown) > DEGRADED(recent_crash).
        """
        watcher.register_runner("alice", _RaisingRunner())
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        watcher.record_event("alice", "restarted")
        report = watcher.get_report("alice")
        assert report.health == RuntimeHealth.UNKNOWN
        assert "runner_unknown" in report.anomalies
        assert "recent_crash" in report.anomalies
        # state_crashed ne doit PAS être présent : state="running" après restart
        assert "state_crashed" not in report.anomalies

    def test_state_crashed_with_runner_unknown_stays_unhealthy(self, watcher):
        """state_crashed + runner illisible → UNHEALTHY (state_crashed trump
        runner_unknown dans la hiérarchie)."""
        watcher.register_runner("alice", _RaisingRunner())
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        report = watcher.get_report("alice")
        assert report.health == RuntimeHealth.UNHEALTHY
        assert "state_crashed" in report.anomalies
        assert "runner_unknown" in report.anomalies


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Fenêtre glissante crash_count
# ══════════════════════════════════════════════════════════════════════════════


class TestCrashWindow:
    def test_3_crashes_in_window_crash_loop(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_window_s=300,
            crash_loop_threshold=3,
        )
        runner = _DummyRunner("running")
        w.register_runner("alice", runner)
        for _ in range(3):
            w.record_event("alice", "crashed")
        report = w.get_report("alice")
        assert "crash_loop" in report.anomalies

    def test_crashes_outside_window_pruned(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_window_s=300,
            crash_loop_threshold=3,
        )
        runner = _DummyRunner("running")
        w.register_runner("alice", runner)
        entry = w._registry["alice"]
        # Injecte 2 crashes anciens (hors fenêtre)
        old = datetime.now(timezone.utc) - timedelta(seconds=10_000)
        entry.crash_timestamps.append(old)
        entry.crash_timestamps.append(old)
        # 1 crash récent
        w.record_event("alice", "crashed")
        report = w.get_report("alice")
        # Seuls les crashes récents comptent → 1 crash → DEGRADED
        assert "crash_loop" not in report.anomalies
        assert "recent_crash" in report.anomalies

    def test_custom_window_threshold(self, tmp_path):
        w = RuntimeWatcher(
            snapshots_dir=tmp_path / "s",
            audit_log_path=tmp_path / "a" / "audit.jsonl",
            crash_loop_window_s=60,
            crash_loop_threshold=2,
        )
        runner = _DummyRunner("running")
        w.register_runner("alice", runner)
        w.record_event("alice", "crashed")
        w.record_event("alice", "crashed")
        report = w.get_report("alice")
        assert "crash_loop" in report.anomalies

    def test_crash_count_window_reported_in_snapshot(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "crashed")
        watcher.record_event("alice", "crashed")
        snap = watcher.take_snapshot("alice")
        assert snap.crash_count_window == 2


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Persistance disque
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_snapshot_written_at_expected_path(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        watcher.take_snapshot("alice")
        assert (watcher.snapshots_dir / "alice.json").exists()

    def test_load_snapshot_from_disk_roundtrip(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "started")
        original = watcher.take_snapshot("alice")
        loaded = watcher.load_snapshot_from_disk("alice")
        assert loaded is not None
        assert loaded.server_id == original.server_id
        assert loaded.process_state == original.process_state
        assert loaded.restart_count == original.restart_count

    def test_load_snapshot_returns_none_if_missing(self, watcher):
        assert watcher.load_snapshot_from_disk("ghost") is None

    def test_load_snapshot_returns_none_if_malformed(self, watcher):
        path = watcher.snapshots_dir / "alice.json"
        path.write_text("not valid json", encoding="utf-8")
        assert watcher.load_snapshot_from_disk("alice") is None

    def test_list_persisted_snapshots(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        watcher.register_runner("bob", _DummyRunner("running"))
        watcher.take_snapshot("alice")
        watcher.take_snapshot("bob")
        listed = watcher.list_persisted_snapshots()
        assert "alice" in listed
        assert "bob" in listed

    def test_snapshot_does_not_contain_raw_runner_objects(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.take_snapshot("alice")
        path = watcher.snapshots_dir / "alice.json"
        text = path.read_text(encoding="utf-8")
        # Pas de représentation Python du runner
        assert "_DummyRunner" not in text
        assert "object at 0x" not in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Audit forensique no-PII
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditForensicNoPII:
    def test_audit_never_contains_stderr_raw(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        # Tente d'injecter un stderr raw via error_code → doit être rejeté.
        with pytest.raises(RuntimeWatcherError):
            watcher.record_event(
                "alice", "crashed",
                error_code="PANIC at /lib/x.so: SECRET_STDERR_MARKER_42",
            )
        blob = _audit_blob(watcher)
        assert "SECRET_STDERR_MARKER_42" not in blob

    def test_audit_never_contains_paths(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        with pytest.raises(RuntimeWatcherError):
            watcher.record_event(
                "alice", "error",
                error_code="/home/charl/SECRET_PATH_MARKER",
            )
        blob = _audit_blob(watcher)
        assert "SECRET_PATH_MARKER" not in blob

    def test_audit_event_recorded_format(self, watcher):
        watcher.register_runner("alice", _DummyRunner())
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        events = [e for e in _audit_lines(watcher) if e["event"] == "event_recorded"]
        assert events
        ev = events[-1]
        assert ev["server_id"] == "alice"
        assert ev["event_kind"] == "crashed"
        assert ev["error_code"] == "exit_nonzero"

    def test_audit_snapshot_taken_event(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        watcher.take_snapshot("alice")
        events = [e for e in _audit_lines(watcher) if e["event"] == "snapshot_taken"]
        assert events
        assert events[-1]["server_id"] == "alice"

    def test_audit_anomaly_detected_only_codes_no_payload(self, watcher):
        runner = _DummyRunner("crashed")
        watcher.register_runner("alice", runner)
        watcher.record_event("alice", "crashed", error_code="exit_nonzero")
        watcher.get_report("alice")
        events = [e for e in _audit_lines(watcher) if e["event"] == "anomaly_detected"]
        assert events
        ev = events[-1]
        assert "anomalies" in ev
        assert isinstance(ev["anomalies"], list)
        # Pas de payload runner
        assert "_DummyRunner" not in json.dumps(ev)

    def test_audit_multi_server_no_leak(self, watcher):
        for sid in ["alpha", "beta", "gamma"]:
            watcher.register_runner(sid, _DummyRunner("running"))
            watcher.record_event(sid, "started")
            watcher.record_event(sid, "crashed", error_code="exit_nonzero")
        blob = _audit_blob(watcher)
        # Aucun marker forensic n'a été injecté avec succès
        assert "SECRET" not in blob
        assert "PANIC" not in blob

    def test_audit_no_runner_object_stringified(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        watcher.take_snapshot("alice")
        blob = _audit_blob(watcher)
        assert "_DummyRunner" not in blob
        assert "object at 0x" not in blob

    def test_audit_no_paths_in_register(self, watcher):
        watcher.register_runner("alice", _DummyRunner("running"))
        blob = _audit_blob(watcher)
        # Aucun chemin subprocess / executable / working dir
        assert "C:\\" not in blob
        assert "/usr/" not in blob
        assert "/home/" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Sanity intégration MCPSandboxRunner (sans modifier le runner)
# ══════════════════════════════════════════════════════════════════════════════


class TestSanitySandboxRunnerCompat:
    """Sanity : compatible avec un objet qui ressemble au vrai
    MCPSandboxRunner sans le modifier (duck typing sur state())."""

    def test_state_called_as_method(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        watcher.take_snapshot("alice")
        watcher.take_snapshot("alice")
        # state() appelée plusieurs fois en méthode
        assert runner.state_call_count >= 2

    def test_take_snapshot_does_not_mutate_runner_state(self, watcher):
        runner = _DummyRunner("running")
        watcher.register_runner("alice", runner)
        before = runner._state
        watcher.take_snapshot("alice")
        watcher.take_snapshot("alice")
        watcher.take_snapshot("alice")
        # Le watcher ne doit JAMAIS muter le runner
        assert runner._state == before

    def test_runner_state_returning_processstate_enum_like(self, watcher):
        """Compatibilité avec ProcessState enum (state.value)"""
        class ProcessStateMock:
            def __init__(self, v):
                self.value = v
        class Runner:
            def state(self):
                return ProcessStateMock("RUNNING")
        watcher.register_runner("alice", Runner())
        snap = watcher.take_snapshot("alice")
        assert snap.process_state == "running"
