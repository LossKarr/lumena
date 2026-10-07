from pathlib import Path

import pytest

from src.training.personal.export_service import PersonalModelExporter


def test_export_hashes_imports_and_canaries(tmp_path) -> None:
    merged = tmp_path / "versions" / "1.0.0" / "merged"
    merged.mkdir(parents=True)
    (merged / "config.json").write_text("{}", encoding="utf-8")

    def convert(_source, output):
        Path(output).write_bytes(b"f16")
        return output

    def quantize(_source, output, _quant):
        Path(output).write_bytes(b"gguf-personal-model")
        return output

    registered = []
    exporter = PersonalModelExporter(tmp_path, converter=convert, quantizer=quantize, importer=lambda name, path: True, canary=lambda name: name == "lumena-model-1.0.0", registrar=lambda *args, **kwargs: registered.append((args, kwargs)))
    proof = exporter.export(model_name="lumena-model-1.0.0", merged_model_dir=merged)
    assert proof.ollama_imported is True
    assert proof.canary_verified is True
    assert proof.registered is True
    assert registered
    assert len(proof.gguf_sha256) == 64
    assert Path(proof.modelfile_path).is_file()


def test_export_rejects_source_outside_root(tmp_path) -> None:
    external = tmp_path.parent / "foreign-model"
    external.mkdir(exist_ok=True)
    with pytest.raises(PermissionError):
        PersonalModelExporter(tmp_path).export(model_name="lumena-model-1.0.0", merged_model_dir=external)
