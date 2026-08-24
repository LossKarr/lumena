"""
Lumena v1.0.0 — Fine-Tuning QLoRA avec Unsloth
================================================
Fine-tune Qwen3-8B sur les données de Lumena.

Usage:
    python 2_train.py
    python 2_train.py --config config.yaml
    python 2_train.py --resume output/lumena-lora/checkpoint-50

Prérequis:
    pip install -r requirements.txt
    GPU CUDA avec 10Go+ VRAM
    Les données générées par 1_prepare_data.py

Durée estimée : 2-4h sur RTX 3060 12Go
"""

import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# Changer vers le répertoire du script pour que les chemins relatifs fonctionnent
os.chdir(os.path.dirname(os.path.abspath(__file__)))
# Fix Unsloth cross entropy : forcer 1Go de target pour le fused CE loss
os.environ["UNSLOTH_CE_LOSS_TARGET_GB"] = "1.0"
# Désactiver la compilation Triton/Inductor (problème d'espace disque ou permissions)
os.environ["TORCHDYNAMO_DISABLE"] = "1"
os.environ["TORCH_COMPILE_DISABLE"] = "1"
import argparse
import json
import yaml
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────
# CONFIGURATION PAR DÉFAUT
# (remplacée par config.yaml si présent)
# ─────────────────────────────────────────
DEFAULT_CONFIG = {
    "base_model": {
        "name": "Qwen/Qwen3-8B",
        "max_seq_length": 4096,
        "load_in_4bit": True,
        "dtype": "bfloat16",
    },
    "lora": {
        "r": 32,
        "lora_alpha": 64,
        "lora_dropout": 0.05,
        "bias": "none",
        "use_gradient_checkpointing": "unsloth",
        "use_rslora": False,
        "use_dora": False,
        "target_modules": [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj"
        ],
    },
    "training": {
        "output_dir": "./output/lumena-lora",
        "num_train_epochs": 3,
        "per_device_train_batch_size": 2,
        "gradient_accumulation_steps": 4,
        "warmup_ratio": 0.1,
        "learning_rate": 2e-4,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "fp16": False,
        "bf16": True,
        "logging_steps": 10,
        "save_steps": 50,
        "save_total_limit": 3,
        "dataloader_num_workers": 0,
        "seed": 42,
        "optim": "adamw_8bit",
        "packing": True,
        "max_grad_norm": 1.0,
    },
    "data": {
        "train_files": [
            "data/lumena_personality.jsonl",
            "data/lumena_tool_use.jsonl",
            "data/lumena_conversations.jsonl",
            "data/lumena_reasoning.jsonl",
        ],
        "format": "sharegpt",
        "test_size": 0.05,
    }
}


def load_config(config_path: str = None) -> dict:
    """Charge la configuration depuis config.yaml ou utilise les défauts."""
    config = DEFAULT_CONFIG.copy()
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
        # Merge récursif
        for section, values in user_config.items():
            if isinstance(values, dict) and section in config:
                config[section].update(values)
            else:
                config[section] = values
        print(f"Configuration chargée depuis : {config_path}")
    return config


def check_gpu():
    """Vérifie que le GPU est disponible et affiche les infos."""
    try:
        import torch
        if not torch.cuda.is_available():
            print("ERREUR : Aucun GPU CUDA détecté. Le fine-tuning requiert un GPU.")
            print("Si vous avez bien une GPU NVIDIA, assurez-vous que CUDA est installé.")
            sys.exit(1)

        gpu_name = torch.cuda.get_device_name(0)
        vram_total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"GPU : {gpu_name}")
        print(f"VRAM : {vram_total:.1f} Go")

        if vram_total < 10:
            print("AVERTISSEMENT : Moins de 10Go VRAM — risque d'OOM. Réduisez max_seq_length à 2048.")

        return True
    except ImportError:
        print("ERREUR : PyTorch n'est pas installé. pip install torch")
        sys.exit(1)


def load_datasets(data_config: dict):
    """Charge et fusionne tous les fichiers JSONL."""
    from datasets import Dataset
    import json

    all_examples = []
    for filepath in data_config["train_files"]:
        path = Path(filepath)
        if not path.exists():
            print(f"  AVERTISSEMENT : {filepath} introuvable, ignoré.")
            continue
        with open(path, "r", encoding="utf-8") as f:
            examples = [json.loads(line) for line in f if line.strip()]
        print(f"  ✓ {filepath} : {len(examples)} exemples")
        all_examples.extend(examples)

    if not all_examples:
        print("ERREUR : Aucune donnée trouvée. Lancez d'abord : python 1_prepare_data.py")
        sys.exit(1)

    print(f"  Total : {len(all_examples)} exemples")
    return Dataset.from_list(all_examples)


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning Lumena v1.0.0 avec QLoRA + Unsloth")
    parser.add_argument("--config", type=str, default="config.yaml", help="Fichier de configuration YAML")
    parser.add_argument("--resume", type=str, help="Reprendre depuis un checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Test sans entraînement réel")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA v1.0.0 — Fine-Tuning QLoRA")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Vérifier GPU
    print("\n[1/6] Vérification du GPU...")
    check_gpu()

    # Charger config
    print("\n[2/6] Chargement de la configuration...")
    config = load_config(args.config)
    bc = config["base_model"]
    lc = config["lora"]
    tc = config["training"]
    dc = config["data"]

    print(f"  Modèle de base : {bc['name']}")
    print(f"  LoRA rank : {lc['r']}, alpha : {lc['lora_alpha']}")
    print(f"  Epochs : {tc['num_train_epochs']}, LR : {tc['learning_rate']}")
    print(f"  Max seq length : {bc['max_seq_length']}")

    if args.dry_run:
        print("\nDRY RUN — Arrêt avant chargement du modèle")
        return

    # Charger le modèle avec Unsloth
    print(f"\n[3/6] Chargement de {bc['name']} avec Unsloth...")
    print("  (Première exécution : téléchargement ~16Go depuis HuggingFace...)")

    try:
        from unsloth import FastLanguageModel
        import torch

        dtype = torch.bfloat16 if bc["dtype"] == "bfloat16" else torch.float16

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=bc["name"],
            max_seq_length=bc["max_seq_length"],
            dtype=dtype,
            load_in_4bit=bc["load_in_4bit"],
        )
        print("  ✓ Modèle chargé")
    except ImportError as ie:
        import traceback
        print(f"ERREUR ImportError : {ie}")
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"ERREUR lors du chargement du modèle : {e}")
        traceback.print_exc()
        sys.exit(1)

    # Appliquer LoRA
    print("\n[4/6] Configuration LoRA...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=lc["r"],
        target_modules=lc["target_modules"],
        lora_alpha=lc["lora_alpha"],
        lora_dropout=lc["lora_dropout"],
        bias=lc["bias"],
        use_gradient_checkpointing=lc["use_gradient_checkpointing"],
        random_state=tc["seed"],
        use_rslora=lc["use_rslora"],
        use_dora=lc["use_dora"],
    )

    # Afficher les paramètres entraînables
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  ✓ Paramètres entraînables : {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    # Charger les données
    print("\n[5/6] Chargement des données...")
    dataset = load_datasets(dc)

    # Formater pour ShareGPT/Unsloth
    from trl import SFTTrainer
    from transformers import TrainingArguments, DataCollatorForSeq2Seq

    # Template de conversation pour Qwen3
    # Qwen3 utilise le format ChatML : <|im_start|>role\ncontent<|im_end|>
    def format_chat(example):
        """Convertit le format ShareGPT en texte formaté ChatML."""
        conversations = example.get("conversations", [])
        text = ""
        for turn in conversations:
            role = turn["from"]
            content = turn["value"]
            if role == "system":
                text += f"<|im_start|>system\n{content}<|im_end|>\n"
            elif role == "human":
                text += f"<|im_start|>user\n{content}<|im_end|>\n"
            elif role == "gpt":
                text += f"<|im_start|>assistant\n{content}<|im_end|>\n"
        return {"text": text}

    # Appliquer le formatage
    dataset = dataset.map(format_chat, remove_columns=dataset.column_names)

    # Split train/validation
    test_size = dc.get("test_size", 0.05)
    if len(dataset) > 20:
        split = dataset.train_test_split(test_size=test_size, seed=tc["seed"])
        train_dataset = split["train"]
        eval_dataset = split["test"]
        print(f"  Train : {len(train_dataset)}, Validation : {len(eval_dataset)}")
    else:
        train_dataset = dataset
        eval_dataset = None
        print(f"  Train : {len(train_dataset)} (trop peu pour validation)")

    # Configuration de l'entraînement
    print("\n[6/6] Lancement de l'entraînement...")
    output_dir = Path(tc["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=tc["num_train_epochs"],
        per_device_train_batch_size=tc["per_device_train_batch_size"],
        gradient_accumulation_steps=tc["gradient_accumulation_steps"],
        warmup_ratio=tc["warmup_ratio"],
        learning_rate=tc["learning_rate"],
        lr_scheduler_type=tc["lr_scheduler_type"],
        weight_decay=tc["weight_decay"],
        fp16=tc["fp16"],
        bf16=tc["bf16"],
        logging_steps=tc["logging_steps"],
        save_steps=tc["save_steps"],
        save_total_limit=tc["save_total_limit"],
        dataloader_num_workers=tc["dataloader_num_workers"],
        seed=tc["seed"],
        optim=tc["optim"],
        max_grad_norm=tc["max_grad_norm"],
        report_to="none",  # Désactiver wandb/tensorboard par défaut
        eval_strategy="epoch" if eval_dataset else "no",
        save_strategy="epoch" if eval_dataset else "steps",
        load_best_model_at_end=True if eval_dataset else False,
    )

    # Trainer Unsloth optimisé
    from unsloth import is_bfloat16_supported

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        dataset_text_field="text",
        max_seq_length=bc["max_seq_length"],
        dataset_num_proc=2,
        packing=tc["packing"],
        args=training_args,
    )

    # Reprendre depuis checkpoint si demandé
    resume_from = args.resume if args.resume else None
    if resume_from:
        print(f"  Reprise depuis : {resume_from}")

    # Afficher un résumé avant de lancer
    print("\n" + "─" * 40)
    print("RÉSUMÉ AVANT ENTRAÎNEMENT")
    print(f"  Modèle : {bc['name']}")
    print(f"  LoRA r={lc['r']}, alpha={lc['lora_alpha']}")
    print(f"  Epochs : {tc['num_train_epochs']}")
    print(f"  Batch effectif : {tc['per_device_train_batch_size'] * tc['gradient_accumulation_steps']}")
    print(f"  Exemples : {len(train_dataset)}")
    steps_per_epoch = len(train_dataset) // (tc["per_device_train_batch_size"] * tc["gradient_accumulation_steps"])
    total_steps = steps_per_epoch * tc["num_train_epochs"]
    print(f"  Steps estimés : ~{total_steps}")
    print(f"  Sortie : {output_dir.absolute()}")
    print("─" * 40)
    print("\nEntraînement en cours... (Ctrl+C pour arrêter proprement)")

    # Lancer l'entraînement
    trainer_stats = trainer.train(resume_from_checkpoint=resume_from)

    # Sauvegarder le modèle final
    print("\n✓ Entraînement terminé !")
    print(f"  Perte finale : {trainer_stats.training_loss:.4f}")
    print(f"  Durée : {trainer_stats.metrics['train_runtime']:.0f}s")

    final_dir = output_dir / "final"
    model.save_pretrained(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))
    print(f"  Modèle sauvegardé : {final_dir.absolute()}")

    # Sauvegarder les stats d'entraînement
    stats_file = output_dir / "training_stats.json"
    with open(stats_file, "w") as f:
        json.dump({
            "base_model": bc["name"],
            "lora_r": lc["r"],
            "lora_alpha": lc["lora_alpha"],
            "epochs": tc["num_train_epochs"],
            "final_loss": trainer_stats.training_loss,
            "train_runtime": trainer_stats.metrics["train_runtime"],
            "train_samples": len(train_dataset),
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)

    print("\n" + "=" * 60)
    print("ENTRAÎNEMENT TERMINÉ")
    print(f"Prochaine étape : python 3_export_gguf.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
