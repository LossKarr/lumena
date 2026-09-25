"""Hardware and storage inventory used by explainable recommendations."""

from __future__ import annotations

import shutil
from typing import Any

import psutil

from src.training.gpu_detect import detect_gpu_safe
from src.utils.paths import DATA_DIR


def get_hardware_inventory() -> dict[str, Any]:
    gpu = detect_gpu_safe()
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(DATA_DIR.anchor or DATA_DIR)
    return {
        "gpu": {
            "available": bool(gpu.get("available", False)),
            "name": gpu.get("name"),
            "vram_total_bytes": int(float(gpu.get("vram_gb", 0) or 0) * 1024**3),
            "vram_free_bytes": int(float(gpu.get("vram_free_gb", gpu.get("vram_gb", 0)) or 0) * 1024**3),
            "device_count": int(gpu.get("device_count", 1) or 1) if gpu.get("available") else 0,
        },
        "ram_total_bytes": int(memory.total),
        "ram_available_bytes": int(memory.available),
        "disk_free_bytes": int(disk.free),
        "disk_total_bytes": int(disk.total),
    }
