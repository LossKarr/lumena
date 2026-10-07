from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.voice.v2.certification import (
    SCENARIOS, VoiceCertificationError, add_machine, finalize_campaign,
    new_campaign, record_scenario, validate_campaign, verify_final_attestation,
)
from src.voice.v2.endurance import run_endurance


def _complete_campaign():
    report = new_campaign(software_revision="fixture")
    add_machine(
        report, machine_id="dev-nvidia", profiles=["development", "nvidia"],
        devices=["integrated_mic", "usb_mic", "external_speaker"],
        os_version="Windows fixture", hardware="NVIDIA fixture",
    )
    add_machine(
        report, machine_id="clean-cpu", profiles=["clean_exe", "cpu"],
        devices=["bluetooth_headset", "integrated_speaker"],
        os_version="Windows fixture", hardware="CPU fixture",
        installer_sha256="a" * 64,
    )
    for scenario in SCENARIOS:
        record_scenario(
            report, scenario_id=scenario,
            machine_id="clean-cpu" if scenario == "H15" else "dev-nvidia",
            result="pass", tester="Human Fixture",
            iterations=20 if scenario in {"H1", "H2", "H3"} else 1,
            duration_s=1200 if scenario == "H13" else 86400 if scenario == "H14" else 0,
            naturalness=4.5, intelligibility=4.5, minimum_phrase_score=3,
            fatigue=2 if scenario == "H13" else None,
        )
    return report


def test_incomplete_campaign_can_never_be_finalized():
    report = new_campaign()
    with pytest.raises(VoiceCertificationError, match="certification refusée"):
        finalize_campaign(report, signer="Tester")
    assert report["status"] == "blocked"
    assert "H14 exige 24 heures réelles" in report["gate_failures"]


def test_complete_human_campaign_is_checksummed_and_tamper_evident():
    report = _complete_campaign()
    assert validate_campaign(report) == []
    finalize_campaign(report, signer="Release Owner")
    assert verify_final_attestation(report) is True
    report["scenarios"]["H1"]["evidence"][0]["notes"] = "tampered"
    assert verify_final_attestation(report) is False
    assert "H1 contient une attestation altérée" in validate_campaign(report)


def test_cpu_only_and_nvidia_profiles_cannot_be_claimed_by_same_machine():
    report = new_campaign()
    add_machine(
        report, machine_id="impossible", profiles=["development", "clean_exe", "cpu", "nvidia"],
        devices=list({"integrated_mic", "usb_mic", "bluetooth_headset", "integrated_speaker", "external_speaker"}),
        os_version="Windows", hardware="mixed", installer_sha256="b" * 64,
    )
    failures = validate_campaign(report)
    assert "les profils cpu-only et nvidia doivent être sur des machines distinctes" in failures


def test_short_endurance_smoke_is_recorded_but_never_certified(monkeypatch, tmp_path):
    class _Clock:
        value = 0.0
        def __call__(self): return self.value
        def sleep(self, seconds): self.value += seconds

    class _Process:
        def __init__(self, pid): self.pid = pid
        def oneshot(self):
            class _Context:
                def __enter__(self): return None
                def __exit__(self, *_args): return False
            return _Context()
        def memory_info(self): return SimpleNamespace(rss=1000)
        def num_threads(self): return 2
        def children(self, recursive=True): return []

    import psutil
    monkeypatch.setattr(psutil, "Process", _Process)
    clock = _Clock()
    target = tmp_path / "soak.json"
    result = run_endurance(
        duration_s=2, interval_s=1, pid=42, base_url="", token="secret",
        output=target, clock=clock, sleep=clock.sleep,
    )
    assert result["summary"]["sample_count"] == 3
    assert result["certifying_24h"] is False
    assert "secret" not in target.read_text(encoding="utf-8")


def test_endurance_cannot_certify_a_legacy_fallback(monkeypatch, tmp_path):
    class _Clock:
        value = 0.0
        def __call__(self): return self.value
        def sleep(self, seconds): self.value += seconds

    class _Process:
        def __init__(self, pid): self.pid = pid
        def oneshot(self):
            class _Context:
                def __enter__(self): return None
                def __exit__(self, *_args): return False
            return _Context()
        def memory_info(self): return SimpleNamespace(rss=1000)
        def num_threads(self): return 2
        def children(self, recursive=True): return []

    import psutil
    import src.voice.v2.endurance as endurance
    monkeypatch.setattr(psutil, "Process", _Process)
    monkeypatch.setattr(endurance, "_api_sample", lambda *_args, **_kwargs: {
        "running": True, "backend": "legacy", "state": "running",
        "restarts": 0, "provider": "legacy", "fallback_used": True,
        "fallback_reason": "v2_failed",
    })
    clock = _Clock()
    result = run_endurance(
        duration_s=86400, interval_s=86400, pid=42,
        base_url="http://127.0.0.1:8000", output=tmp_path / "fallback.json",
        clock=clock, sleep=clock.sleep,
    )
    assert result["actual_duration_s"] == 86400
    assert result["certifying_24h"] is False
