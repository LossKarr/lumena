"""Data preparation for local fine-tuning: load, filter, convert, split."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loading & filtering
# ---------------------------------------------------------------------------

def load_lumena_pool(
    pool_dir: Path,
    validated_dir: Path,
    *,
    min_conversations: int = 50,
    min_user_chars: int = 15,
    min_assistant_chars: int = 50,
) -> list[dict[str, Any]]:
    """Load and deduplicate conversations from pool + validated dirs.

    Filters out:
    - quality_flag == "negative_feedback"
    - user message < min_user_chars OR assistant reply < min_assistant_chars
    - duplicate conversations (by content_hash or computed SHA-256)
    """
    raw: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    dirs = []
    if pool_dir.exists():
        dirs.append(pool_dir)
    if validated_dir.exists():
        dirs.append(validated_dir)

    for d in dirs:
        for f in sorted(d.glob("*.jsonl")):
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    meta = entry.get("metadata", {})

                    # Filter: negative feedback
                    if meta.get("quality_flag") == "negative_feedback":
                        continue

                    convs = entry.get("conversations", [])
                    if not convs:
                        continue

                    # Filter: short messages
                    user_msgs = [m for m in convs if m.get("role") == "user"]
                    asst_msgs = [m for m in convs if m.get("role") == "assistant"]
                    if user_msgs and len(user_msgs[-1].get("content", "")) < min_user_chars:
                        continue
                    if asst_msgs and len(asst_msgs[-1].get("content", "")) < min_assistant_chars:
                        continue

                    # Dedup by content_hash
                    h = meta.get("content_hash")
                    if not h:
                        h = hashlib.sha256(json.dumps(convs, sort_keys=True).encode()).hexdigest()[:16]
                    if h in seen_hashes:
                        continue
                    seen_hashes.add(h)

                    raw.append(entry)
            except Exception as exc:
                _log.warning("Failed reading %s: %s", f, exc)

    if len(raw) < min_conversations:
        raise ValueError(
            f"Insufficient data: {len(raw)} conversations found, "
            f"minimum {min_conversations} required."
        )

    _log.info("Loaded %d conversations from pool + validated", len(raw))
    return raw


# ---------------------------------------------------------------------------
# Conversion to TRL format
# ---------------------------------------------------------------------------

def convert_to_trl_format(
    raw: list[dict[str, Any]],
    *,
    system_prompt: str = "",
) -> list[dict[str, Any]]:
    """Convert Lumena format (conversations key) to TRL format (messages key).

    Optionally injects a system prompt at the start of each conversation.
    Filters out conversations that don't end with an assistant message.
    """
    result = []
    for entry in raw:
        convs = entry.get("conversations", [])
        if not convs:
            continue

        # Must end with assistant turn
        if convs[-1].get("role") != "assistant":
            continue

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend({"role": m["role"], "content": m["content"]} for m in convs)

        result.append({"messages": messages})

    return result


# ---------------------------------------------------------------------------
# Dataset splitting
# ---------------------------------------------------------------------------

def split_dataset(
    data: list[dict[str, Any]],
    eval_ratio: float = 0.1,
    seed: int = 42,
) -> tuple[Any, Any]:
    """Convert list to HF Datasets and split train/eval.

    Returns (train_dataset, eval_dataset).
    """
    from datasets import Dataset

    if len(data) < 10:
        raise ValueError(f"Dataset too small: {len(data)} examples (minimum 10)")

    ds = Dataset.from_list(data)
    split = ds.train_test_split(test_size=eval_ratio, seed=seed)
    return split["train"], split["test"]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_dataset_stats(data: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute stats on the dataset."""
    total = len(data)
    if total == 0:
        return {"total": 0, "avg_turns": 0, "avg_tokens_estimate": 0, "quality_breakdown": {}}

    turns = []
    tokens_est = []
    quality: dict[str, int] = {}

    for entry in data:
        convs = entry.get("conversations", entry.get("messages", []))
        turns.append(len(convs))
        text_len = sum(len(m.get("content", "")) for m in convs)
        tokens_est.append(text_len // 4)  # rough estimate
        flag = entry.get("metadata", {}).get("quality_flag", "ok")
        quality[flag] = quality.get(flag, 0) + 1

    return {
        "total": total,
        "avg_turns": round(sum(turns) / total, 1),
        "avg_tokens_estimate": round(sum(tokens_est) / total),
        "quality_breakdown": quality,
    }


# ---------------------------------------------------------------------------
# Export merged JSONL
# ---------------------------------------------------------------------------

def export_merged_jsonl(
    pool_dir: Path,
    validated_dir: Path,
    out_path: Path,
    *,
    system_prompt: str = "",
) -> int:
    """Load, convert, and write merged TRL-format JSONL. Returns count."""
    raw = load_lumena_pool(pool_dir, validated_dir)
    trl = convert_to_trl_format(raw, system_prompt=system_prompt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for entry in trl:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _log.info("Exported %d conversations to %s", len(trl), out_path)
    return len(trl)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
