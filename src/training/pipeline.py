"""Fine-tuning pipeline: Unsloth + TRL SFTTrainer orchestration."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


@dataclass
class FinetuneConfig:
    base_model_hf_id: str        # e.g. "unsloth/Qwen3-8B-bnb-4bit"
    output_name: str             # e.g. "lumena-qwen3-8b-v1"
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.0
    learning_rate: float = 2e-4
    num_epochs: int = 3
    batch_size: int = 2
    grad_accumulation: int = 4
    max_seq_length: int = 2048
    load_in_4bit: bool = True
    use_unsloth: bool = True
    system_prompt: str = ""
    hf_token: str = ""


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

class ProgressCallback:
    """Collects training metrics and forwards them to an optional callback."""

    def __init__(self, on_progress: Callable[[dict], None] | None = None):
        self._on_progress = on_progress
        self.logs: list[dict[str, Any]] = []

    def _emit(self, data: dict) -> None:
        self.logs.append(data)
        if self._on_progress:
            try:
                self._on_progress(data)
            except Exception:
                pass

    def get_trainer_callback(self):
        """Return a TrainerCallback subclass wired to this ProgressCallback."""
        try:
            from transformers import TrainerCallback
        except ImportError:
            return None

        parent = self

        class _Cb(TrainerCallback):
            def on_log(self, args, state, control, logs=None, **kw):
                if logs:
                    pct = round(100 * state.global_step / max(1, state.max_steps), 1)
                    parent._emit({
                        "step": state.global_step,
                        "max_steps": state.max_steps,
                        "loss": logs.get("loss"),
                        "learning_rate": logs.get("learning_rate"),
                        "epoch": logs.get("epoch"),
                        "pct_done": pct,
                        "phase": "training",
                    })

            def on_epoch_end(self, args, state, control, **kw):
                parent._emit({"phase": "training", "message": f"Epoch {int(state.epoch)} terminée"})

            def on_train_end(self, args, state, control, **kw):
                parent._emit({"phase": "training", "message": "Entraînement terminé", "pct_done": 100})

        return _Cb()


# ---------------------------------------------------------------------------
# Cancel mechanism
# ---------------------------------------------------------------------------

_cancel_event = threading.Event()


def cancel_training() -> None:
    """Signal the training loop to stop."""
    _cancel_event.set()


def _reset_cancel() -> None:
    _cancel_event.clear()


def is_cancelled() -> bool:
    return _cancel_event.is_set()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_finetuning(
    config: FinetuneConfig,
    dataset_train,
    dataset_eval=None,
    progress_cb: ProgressCallback | None = None,
) -> str:
    """Run the full fine-tuning pipeline. Returns path to the LoRA adapter.

    Raises ImportError if unsloth/trl are not installed.
    """
    _reset_cancel()

    from src.utils.paths import FINETUNED_MODELS_DIR

    output_dir = FINETUNED_MODELS_DIR / f"{config.output_name}_lora"
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb._emit({"phase": "downloading", "message": f"Chargement du modèle {config.base_model_hf_id}..."})

    # Import heavy deps
    try:
        from unsloth import FastLanguageModel
    except ImportError as e:
        raise ImportError(
            "unsloth n'est pas installé. Installez les dépendances fine-tuning : "
            "pip install -r requirements-finetuning.txt"
        ) from e

    try:
        from trl import SFTTrainer, SFTConfig
    except ImportError as e:
        raise ImportError("trl n'est pas installé.") from e

    # Load model
    load_kwargs: dict[str, Any] = {
        "model_name": config.base_model_hf_id,
        "max_seq_length": config.max_seq_length,
        "load_in_4bit": config.load_in_4bit,
    }
    if config.hf_token:
        load_kwargs["token"] = config.hf_token

    model, tokenizer = FastLanguageModel.from_pretrained(**load_kwargs)

    if is_cancelled():
        return str(output_dir)

    if progress_cb:
        progress_cb._emit({"phase": "preparing", "message": "Application LoRA..."})

    # Apply LoRA
    model = FastLanguageModel.get_peft_model(
        model,
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=LORA_TARGET_MODULES,
    )

    if is_cancelled():
        return str(output_dir)

    if progress_cb:
        progress_cb._emit({"phase": "training", "message": "Démarrage de l'entraînement..."})

    # Configure trainer
    sft_config = SFTConfig(
        output_dir=str(output_dir),
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accumulation,
        num_train_epochs=config.num_epochs,
        learning_rate=config.learning_rate,
        max_seq_length=config.max_seq_length,
        assistant_only_loss=True,
        packing=False,
        gradient_checkpointing=True,
        logging_steps=1,
        save_strategy="epoch",
        eval_strategy="epoch" if dataset_eval else "no",
    )

    callbacks = []
    if progress_cb:
        tcb = progress_cb.get_trainer_callback()
        if tcb:
            callbacks.append(tcb)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset_train,
        eval_dataset=dataset_eval,
        args=sft_config,
        callbacks=callbacks,
    )

    # Train
    trainer.train()

    # Save LoRA adapter
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    _log.info("LoRA adapter saved to %s", output_dir)
    return str(output_dir)


# ---------------------------------------------------------------------------
# Merge LoRA into base
# ---------------------------------------------------------------------------

def merge_and_save(lora_path: str, merged_path: str) -> str:
    """Load base model + LoRA adapter, merge, and save as full model."""
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=lora_path,
        max_seq_length=2048,
    )

    Path(merged_path).mkdir(parents=True, exist_ok=True)
    model.save_pretrained_merged(merged_path, tokenizer, save_method="merged_16bit")

    _log.info("Merged model saved to %s", merged_path)
    return merged_path
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
