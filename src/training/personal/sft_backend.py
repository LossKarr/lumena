"""Canonical, injectable SFT backend for personal-model jobs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from src.utils.persistence import atomic_write_json

from ..pipeline import FinetuneConfig, ProgressCallback, TrainingInterrupted, run_finetuning


@dataclass(frozen=True, slots=True)
class SFTJobResult:
    state: str
    adapter_path: str = ""
    checkpoint_path: str = ""
    metrics_path: str = ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"training_dataset_missing:{path.name}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"training_dataset_invalid_json:{path.name}:{number}") from exc
        messages = value.get("messages") if isinstance(value, dict) else None
        if not isinstance(messages, list) or not messages or messages[-1].get("role") != "assistant":
            raise ValueError(f"training_dataset_invalid_messages:{path.name}:{number}")
        rows.append({"messages": messages})
    if not rows:
        raise ValueError(f"training_dataset_empty:{path.name}")
    return rows


def _to_dataset(rows: list[dict[str, Any]]):
    try:
        from datasets import Dataset
    except ImportError as exc:
        raise RuntimeError("training_dependency_missing:datasets") from exc
    return Dataset.from_list(rows)


def execute_sft_job(
    run_dir: Path,
    config_payload: dict[str, Any],
    *,
    control_signal: Callable[[], str | None],
    trainer: Callable[..., str] = run_finetuning,
    dataset_factory: Callable[[list[dict[str, Any]]], Any] = _to_dataset,
) -> SFTJobResult:
    """Validate inputs, run SFT, and persist bounded progress evidence."""
    if config_payload.get("backend") != "canonical_sft_v1":
        raise ValueError("training_backend_not_allowed")
    dataset_dir = Path(str(config_payload.get("dataset_dir", ""))).resolve()
    expected_root = Path(run_dir).resolve().parents[1]
    if dataset_dir != expected_root and expected_root not in dataset_dir.parents:
        raise PermissionError("training_dataset_outside_personal_root")
    train_rows = _read_jsonl(dataset_dir / "train.jsonl")
    eval_path = dataset_dir / "eval.jsonl"
    eval_rows = _read_jsonl(eval_path) if eval_path.is_file() and eval_path.stat().st_size else []
    output_dir = Path(run_dir).resolve() / "artifacts" / "adapter"
    progress_path = Path(run_dir).resolve() / "progress.json"

    allowed = {
        "base_model_hf_id", "output_name", "lora_r", "lora_alpha", "lora_dropout",
        "learning_rate", "num_epochs", "batch_size", "grad_accumulation",
        "max_seq_length", "load_in_4bit", "use_unsloth", "system_prompt", "hf_token",
        "resume_from_checkpoint", "seed", "lora_target_modules",
    }
    raw = dict(config_payload.get("finetune") or {})
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"training_config_unknown_fields:{','.join(unknown)}")
    raw["output_dir"] = str(output_dir)
    if isinstance(raw.get("lora_target_modules"), list):
        raw["lora_target_modules"] = tuple(raw["lora_target_modules"])
    config = FinetuneConfig(**raw)

    events: list[dict[str, Any]] = []

    def on_progress(event: dict[str, Any]) -> None:
        bounded = {key: event[key] for key in ("phase", "step", "max_steps", "loss", "learning_rate", "epoch", "pct_done", "message") if key in event}
        events.append(bounded)
        atomic_write_json(progress_path, {"schema_version": 1, "latest": bounded, "event_count": len(events)})

    callback = ProgressCallback(on_progress=on_progress, control_signal=control_signal)
    try:
        adapter_path = trainer(
            config,
            dataset_factory(train_rows),
            dataset_factory(eval_rows) if eval_rows else None,
            progress_cb=callback,
        )
    except TrainingInterrupted as exc:
        atomic_write_json(progress_path, {"schema_version": 1, "latest": {"phase": exc.reason}, "event_count": len(events), "checkpoint_path": exc.checkpoint_path})
        return SFTJobResult(state=exc.reason, checkpoint_path=exc.checkpoint_path, metrics_path=str(progress_path))
    result = SFTJobResult(state="completed", adapter_path=str(adapter_path), metrics_path=str(progress_path))
    atomic_write_json(Path(run_dir) / "result.json", {"schema_version": 1, **asdict(result)})
    return result
