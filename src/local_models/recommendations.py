"""Deterministic and explainable local model ranking."""

from __future__ import annotations

from typing import Any


def recommend_catalog_models(
    models: list[dict[str, Any]], hardware: dict[str, Any], *, intent: str = "general", limit: int = 5
) -> list[dict[str, Any]]:
    intent = intent.casefold().strip()[:64] or "general"
    vram = int(hardware.get("gpu", {}).get("vram_free_bytes", 0) or 0)
    ram = int(hardware.get("ram_available_bytes", 0) or 0)
    disk = int(hardware.get("disk_free_bytes", 0) or 0)
    ranked = []
    for model in models[:500]:
        size = model.get("size_bytes") if type(model.get("size_bytes")) is int else None
        capabilities = {str(item).casefold() for item in model.get("capabilities", [])}
        category = str(model.get("category") or "").casefold()
        score = 50
        reasons = []
        unknowns = []
        if intent in capabilities or intent in category or (intent == "general" and "text" in capabilities):
            score += 25
            reasons.append("capability_matches_intent")
        elif intent not in {"general", "text"}:
            score -= 20
            reasons.append("capability_not_confirmed")
        if size is None:
            unknowns.append("download_size")
            score -= 5
        else:
            if size > disk:
                score -= 100
                reasons.append("insufficient_disk")
            elif vram and size <= int(vram * 0.9):
                score += 20
                reasons.append("fits_free_vram")
            elif size <= int(ram * 0.7):
                score += 5
                reasons.append("fits_available_ram_cpu_possible")
            else:
                score -= 30
                reasons.append("memory_pressure_likely")
        if model.get("gated"):
            score -= 15
            reasons.append("gated_repository")
        if model.get("license") is None:
            unknowns.append("license")
        if model.get("installed"):
            score += 10
            reasons.append("already_installed")
        ranked.append(
            {
                "model": model,
                "score": score,
                "reasons": reasons,
                "unknowns": unknowns,
                "confidence": "high"
                if size is not None and model.get("license")
                else "medium"
                if size is not None
                else "low",
            }
        )
    ranked.sort(key=lambda item: (-item["score"], str(item["model"].get("display_name", "")).casefold()))
    return ranked[: max(1, min(limit, 20))]
