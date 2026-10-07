from __future__ import annotations

import json

from src.training.personal.dpo_backend import DPOJobConfig, run_dpo_training


class _Trainer:
    def __init__(self):
        self.saved = ""

    def train(self):
        return "trained"

    def save_model(self, path):
        self.saved = path


def test_dpo_backend_passes_the_real_rejected_answer_to_trainer(tmp_path) -> None:
    path = tmp_path / "train.jsonl"
    row = {
        "prompt": [{"role": "user", "content": "question"}],
        "chosen": [{"role": "assistant", "content": "proven answer"}],
        "rejected": [{"role": "assistant", "content": "failed answer"}],
        "tools": [{"name": "read_file"}],
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    observed = {}
    trainer = _Trainer()

    def factory(config, rows):
        observed["rows"] = rows
        return trainer

    result = run_dpo_training(DPOJobConfig("base", str(tmp_path / "output")), path, trainer_factory=factory)
    assert observed["rows"][0]["rejected"][0]["content"] == "failed answer"
    assert observed["rows"][0]["tools"] == [{"name": "read_file"}]
    assert result["pair_count"] == 1
    assert trainer.saved.endswith("output")
