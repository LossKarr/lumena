"""Guided base-model migration and expert from-scratch preflights."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BaseCandidate:
    model_id: str
    parameters_billion: float
    min_ram_gb: float
    min_vram_gb: float
    estimated_disk_gb: float
    label: str


class BaseMigrationAdvisor:
    """Uses a supplied, versioned candidate list; never invents remote availability."""

    def recommend(self, hardware: dict[str, Any], candidates: list[BaseCandidate]) -> dict[str, Any]:
        ram = float(hardware.get("ram_gb", 0) or 0)
        vram = float(hardware.get("vram_gb", 0) or 0)
        disk = float(hardware.get("free_disk_gb", 0) or 0)
        compatible = [item for item in candidates if ram >= item.min_ram_gb and vram >= item.min_vram_gb and disk >= item.estimated_disk_gb + 10]
        compatible.sort(key=lambda item: (item.parameters_billion, -item.estimated_disk_gb), reverse=True)
        choices = compatible[:3]
        return {
            "compatible": bool(choices),
            "recommended": asdict(choices[0]) if choices else None,
            "alternatives": [asdict(item) for item in choices[1:]],
            "reason_codes": ["hardware_compatible"] if choices else ["no_compatible_base_candidate"],
            "old_version_preserved": True,
            "activation_automatic": False,
        }


@dataclass(frozen=True, slots=True)
class FromScratchRequirements:
    min_training_tokens: int = 100_000_000
    min_ram_gb: float = 64
    min_vram_gb: float = 24
    min_free_disk_gb: float = 500


def from_scratch_preflight(*, hardware: dict[str, Any], licensed_tokens: int, tokenizer_versioned: bool, requirements: FromScratchRequirements | None = None) -> dict[str, Any]:
    required = requirements or FromScratchRequirements()
    blockers = []
    if licensed_tokens < required.min_training_tokens:
        blockers.append("licensed_corpus_too_small")
    if float(hardware.get("ram_gb", 0) or 0) < required.min_ram_gb:
        blockers.append("ram_insufficient")
    if float(hardware.get("vram_gb", 0) or 0) < required.min_vram_gb:
        blockers.append("vram_insufficient")
    if float(hardware.get("free_disk_gb", 0) or 0) < required.min_free_disk_gb:
        blockers.append("disk_insufficient")
    if not tokenizer_versioned:
        blockers.append("tokenizer_not_versioned")
    return {"allowed": not blockers, "blockers": blockers, "requirements": asdict(required), "mode": "expert_from_scratch"}
