from __future__ import annotations

import asyncio

from src.autonomy.scheduler import LumenaScheduler
from src.training.personal.legacy_gate import legacy_auto_retrain_enabled


def test_legacy_auto_retrain_is_disabled_by_default() -> None:
    assert legacy_auto_retrain_enabled({}) is False
    assert legacy_auto_retrain_enabled({"LUMENA_LEGACY_AUTO_RETRAIN_ENABLE": "0"}) is False


def test_legacy_auto_retrain_requires_unambiguous_opt_in() -> None:
    assert legacy_auto_retrain_enabled({"LUMENA_LEGACY_AUTO_RETRAIN_ENABLE": "true"}) is True
    assert legacy_auto_retrain_enabled({"LUMENA_LEGACY_AUTO_RETRAIN_ENABLE": "ON"}) is True
    assert legacy_auto_retrain_enabled({"LUMENA_LEGACY_AUTO_RETRAIN_ENABLE": "enabled"}) is False


def test_scheduler_refuses_legacy_retrain_without_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LUMENA_LEGACY_AUTO_RETRAIN_ENABLE", raising=False)
    scheduler = LumenaScheduler(data_dir=tmp_path)

    result = asyncio.run(scheduler.handlers["weekly_auto_improve"]())

    assert result == {
        "success": True,
        "status": "disabled",
        "reason": "legacy_auto_retrain_requires_explicit_opt_in",
    }
