from __future__ import annotations

from types import SimpleNamespace
import sys

from src.training.pipeline import ProgressCallback


def test_trainer_callback_stops_and_saves_on_pause(monkeypatch) -> None:
    class TrainerCallback:
        pass

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(TrainerCallback=TrainerCallback))
    callback = ProgressCallback(control_signal=lambda: "pause")
    trainer_callback = callback.get_trainer_callback()
    control = SimpleNamespace(should_save=False, should_training_stop=False)
    state = SimpleNamespace(global_step=12, max_steps=100)
    returned = trainer_callback.on_step_end(None, state, control)
    assert returned.should_save is True
    assert returned.should_training_stop is True
    assert callback.interruption == "pause"
