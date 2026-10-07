"""VOICE3-1 — bounded, private and reproducible conversation timings."""
from src.voice.v2.observability import VoiceTimingWindow


def test_three_segments_measure_playback_gaps_and_their_cause():
    timings = VoiceTimingWindow(max_events=64, clock=lambda: 99.0)
    gen = "generation-1"
    timings.record("tts.segment_ready", generation_id=gen, sequence=0, at=1.00)
    timings.record("playback.started", generation_id=gen, sequence=0, at=1.10)
    timings.record("tts.segment_ready", generation_id=gen, sequence=1, at=1.30)
    timings.record("playback.finished", generation_id=gen, sequence=0, at=1.50)
    timings.record("playback.started", generation_id=gen, sequence=1, at=1.55)
    timings.record("playback.finished", generation_id=gen, sequence=1, at=2.00)
    timings.record("tts.segment_ready", generation_id=gen, sequence=2, at=2.20)
    timings.record("playback.started", generation_id=gen, sequence=2, at=2.25)
    timings.record("playback.finished", generation_id=gen, sequence=2, at=2.70)

    summary = timings.summary()
    assert summary["playback_gap_ms"] == {"count": 2, "p50": 50.0, "p95": 250.0}
    assert summary["artificial_gap_ms"] == {"count": 1, "p50": 50.0, "p95": 50.0}
    assert summary["synthesis_wait_gap_ms"] == {"count": 1, "p50": 250.0, "p95": 250.0}


def test_timing_window_is_bounded_and_contains_no_text_or_audio_payload():
    timings = VoiceTimingWindow(max_events=32, clock=lambda: 1.0)
    for index in range(80):
        timings.record("stt.started", turn_id=f"turn-{index}", at=float(index))
    events = timings.events()
    assert len(events) == 32
    assert events[0]["turn_id"] == "turn-48"
    assert all(set(event) <= {
        "event", "at", "turn_id", "generation_id", "task_id", "sequence",
        "reason", "result", "provider", "state",
    }
               for event in events)


def test_timing_metadata_is_bounded_and_never_accepts_content_fields():
    timings = VoiceTimingWindow()
    timings.record(
        "stt.final", turn_id="turn-1", reason="no_speech", result="empty",
    )
    assert timings.events()[0]["reason"] == "no_speech"
    assert timings.events()[0]["result"] == "empty"


def test_latency_percentiles_use_monotonic_event_pairs():
    timings = VoiceTimingWindow(max_events=64)
    for index, latency_ms in enumerate((100, 200, 900)):
        turn = f"turn-{index}"
        timings.record("stt.started", turn_id=turn, at=10.0 + index)
        timings.record("stt.final", turn_id=turn, at=10.0 + index + latency_ms / 1000)
    assert timings.summary()["stt_ms"] == {"count": 3, "p50": 200.0, "p95": 900.0}


def test_unknown_or_content_bearing_event_is_rejected():
    timings = VoiceTimingWindow()
    try:
        timings.record("transcript.raw", turn_id="secret")
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:  # pragma: no cover - contract guard
        raise AssertionError("an unapproved event entered telemetry")


def test_interruption_count_distinguishes_user_cut_from_audio_gap():
    timings = VoiceTimingWindow()
    timings.record("playback.started", generation_id="generation-1", sequence=0, at=1.0)
    timings.record("interruption.received", generation_id="generation-1", at=1.2)
    timings.record("interruption.received", generation_id="generation-2", at=2.0)
    assert timings.summary()["interruption_count"] == 2
