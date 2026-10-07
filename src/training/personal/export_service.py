"""Verified export and Ollama import for personal-model versions."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from src.training.export_gguf import convert_to_gguf, quantize_gguf
from src.training.ollama_import import ollama_create, register_in_lumena, write_modelfile
from src.utils.persistence import atomic_write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ExportProof:
    model_name: str
    gguf_path: str
    gguf_sha256: str
    modelfile_path: str
    ollama_imported: bool
    canary_verified: bool
    registered: bool


def ollama_generation_canary(model_name: str) -> bool:
    """Prove that Ollama can load and generate with the imported model."""
    host = (os.getenv("LUMENA_OLLAMA_HOST") or os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    payload = json.dumps({
        "model": model_name,
        "prompt": "Réponds uniquement OK.",
        "stream": False,
        "options": {"num_predict": 8},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{host}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(str(result.get("response") or "").strip()) and not result.get("error")
    except Exception:
        return False


class PersonalModelExporter:
    def __init__(
        self,
        root: Path,
        *,
        converter: Callable[[str, str], str] = convert_to_gguf,
        quantizer: Callable[[str, str, str], str] = quantize_gguf,
        importer: Callable[[str, str], bool] = ollama_create,
        canary: Callable[[str], bool] | None = None,
        registrar: Callable[..., None] = register_in_lumena,
    ) -> None:
        self.root = Path(root).resolve()
        self.converter = converter
        self.quantizer = quantizer
        self.importer = importer
        self.canary = canary or ollama_generation_canary
        self.registrar = registrar

    def export(self, *, model_name: str, merged_model_dir: Path, quant_type: str = "Q4_K_M", system_prompt: str = "") -> ExportProof:
        source = Path(merged_model_dir).resolve()
        if self.root != source and self.root not in source.parents:
            raise PermissionError("export_source_outside_personal_root")
        target = self.root / "exports" / model_name
        target.mkdir(parents=True, exist_ok=True)
        f16 = target / f"{model_name}-f16.gguf"
        final = target / f"{model_name}-{quant_type}.gguf"
        self.converter(str(source), str(f16))
        self.quantizer(str(f16), str(final), quant_type)
        if not final.is_file() or final.stat().st_size <= 0:
            raise RuntimeError("gguf_export_not_proven")
        modelfile = target / "Modelfile"
        write_modelfile(str(final), model_name, system_prompt, str(modelfile))
        imported = bool(self.importer(model_name, str(modelfile)))
        verified = bool(imported and self.canary(model_name))
        registered = False
        if verified:
            self.registrar(
                model_name, "personal-model", "personal-model-dataset",
                quant_type=quant_type, gguf_path=str(final),
            )
            registered = True
        proof = ExportProof(model_name, str(final), sha256_file(final), str(modelfile), imported, verified, registered)
        atomic_write_json(target / "export-proof.json", {"schema_version": 1, **asdict(proof)})
        return proof
