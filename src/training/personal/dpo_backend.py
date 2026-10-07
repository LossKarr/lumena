"""Optional preference training backed by TRL DPOTrainer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class DPOJobConfig:
    base_model_id: str
    output_dir: str
    learning_rate: float = 5e-7
    epochs: int = 1
    batch_size: int = 1
    gradient_accumulation: int = 8
    beta: float = 0.1
    seed: int = 42


def load_preference_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"dpo_dataset_invalid_json:{number}") from exc
        if not all(isinstance(row.get(key), list) and row[key] for key in ("prompt", "chosen", "rejected")):
            raise ValueError(f"dpo_dataset_invalid_pair:{number}")
        if row["chosen"] == row["rejected"]:
            raise ValueError(f"dpo_dataset_identical_pair:{number}")
        rows.append(row)
    if not rows:
        raise ValueError("dpo_dataset_empty")
    return rows


def run_dpo_training(
    config: DPOJobConfig,
    dataset_path: Path,
    *,
    trainer_factory: Callable[[DPOJobConfig, list[dict[str, Any]]], Any] | None = None,
) -> dict[str, Any]:
    """Train on real prompt/chosen/rejected rows; never synthesize a rejection."""
    rows = load_preference_rows(dataset_path)
    output = Path(config.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if trainer_factory is not None:
        trainer = trainer_factory(config, rows)
    else:
        try:
            from datasets import Dataset
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from trl import DPOConfig, DPOTrainer
        except ImportError as exc:
            raise RuntimeError("dpo_dependencies_missing") from exc
        model = AutoModelForCausalLM.from_pretrained(config.base_model_id)
        tokenizer = AutoTokenizer.from_pretrained(config.base_model_id)
        args = DPOConfig(
            output_dir=str(output),
            learning_rate=config.learning_rate,
            num_train_epochs=config.epochs,
            per_device_train_batch_size=config.batch_size,
            gradient_accumulation_steps=config.gradient_accumulation,
            beta=config.beta,
            seed=config.seed,
            data_seed=config.seed,
            save_strategy="epoch",
            logging_steps=1,
        )
        trainer = DPOTrainer(model=model, args=args, train_dataset=Dataset.from_list(rows), processing_class=tokenizer)
    result = trainer.train()
    trainer.save_model(str(output))
    return {"output_dir": str(output), "pair_count": len(rows), "train_result": str(result)}
