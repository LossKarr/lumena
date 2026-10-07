from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.training.personal.sft_backend import execute_sft_job
from src.training.pipeline import TrainingInterrupted


def _dataset(root: Path) -> Path:
    path = root / "owners" / "owner" / "datasets" / "manifest"
    path.mkdir(parents=True)
    row = {"messages": [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]}
    (path / "train.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (path / "eval.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _config(dataset_dir: Path) -> dict:
    return {
        "backend": "canonical_sft_v1",
        "dataset_dir": str(dataset_dir),
        "finetune": {"base_model_hf_id": "base/revision", "output_name": "lumena-model-1.0.0", "seed": 7},
    }


def test_backend_consumes_manifest_dataset_and_persists_evidence(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    dataset_dir = _dataset(tmp_path)
    observed = {}

    def trainer(config, train, evaluation, progress_cb):
        observed.update(config=config, train=train, evaluation=evaluation)
        progress_cb._emit({"phase": "training", "step": 1, "pct_done": 50, "private": "blocked"})
        output = Path(config.output_dir)
        output.mkdir(parents=True)
        (output / "adapter_config.json").write_text("{}", encoding="utf-8")
        return str(output)

    result = execute_sft_job(run_dir, _config(dataset_dir), control_signal=lambda: None, trainer=trainer, dataset_factory=lambda rows: rows)
    assert result.state == "completed"
    assert observed["config"].seed == 7
    assert len(observed["train"]) == len(observed["evaluation"]) == 1
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["latest"]["pct_done"] == 50
    assert "private" not in progress["latest"]


def test_backend_persists_checkpoint_on_pause(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    dataset_dir = _dataset(tmp_path)

    def trainer(*args, **kwargs):
        checkpoint = run_dir / "artifacts" / "adapter" / "checkpoint-4"
        checkpoint.mkdir(parents=True)
        raise TrainingInterrupted("pause", str(checkpoint))

    result = execute_sft_job(run_dir, _config(dataset_dir), control_signal=lambda: "pause", trainer=trainer, dataset_factory=lambda rows: rows)
    assert result.state == "pause"
    assert result.checkpoint_path.endswith("checkpoint-4")


def test_backend_rejects_unknown_fields_and_external_dataset(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    external = tmp_path.parent / "external-dataset"
    external.mkdir(exist_ok=True)
    with pytest.raises(PermissionError, match="outside"):
        execute_sft_job(run_dir, _config(external), control_signal=lambda: None)

    dataset_dir = _dataset(tmp_path)
    config = _config(dataset_dir)
    config["finetune"]["command"] = "arbitrary"
    with pytest.raises(ValueError, match="unknown_fields"):
        execute_sft_job(run_dir, config, control_signal=lambda: None, dataset_factory=lambda rows: rows)
