from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import zipfile


_BUILDER_PATH = Path(__file__).resolve().parents[2] / "tools" / "release" / "build_qwen_release_payload.py"
_SPEC = importlib.util.spec_from_file_location("lumena_qwen_payload_builder", _BUILDER_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_qwen_payload = _MODULE.build_qwen_payload


def test_qwen_payload_is_split_and_keeps_model_tokenizer_separate(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    sox = tmp_path / "sox"
    model = tmp_path / "model"
    (venv / "Scripts").mkdir(parents=True)
    sox.mkdir()
    (model / "speech_tokenizer").mkdir(parents=True)
    (venv / "Scripts/python.exe").write_bytes(b"p" * 8)
    (venv / "runtime.bin").write_bytes(b"r" * 20)
    (sox / "sox.exe").write_bytes(b"s")
    (model / "config.json").write_bytes(b"{}")
    (model / "model.safetensors").write_bytes(b"m")
    (model / "speech_tokenizer/config.json").write_bytes(b"{}")
    (model / "speech_tokenizer/model.safetensors").write_bytes(b"t")
    voice = tmp_path / "voice.voicepack.zip"
    lock = tmp_path / "lock.txt"
    sbom = tmp_path / "sbom.json"
    licenses = tmp_path / "licenses.json"
    for path in (voice, lock, sbom, licenses):
        path.write_bytes(b"x")
    monkeypatch.setattr(_MODULE, "MAX_RUNTIME_SHARD_BYTES", 24)
    output = tmp_path / "output"
    descriptor = build_qwen_payload(
        venv_dir=venv, sox_dir=sox, model_dir=model, voice_pack=voice,
        lock_file=lock, sbom_file=sbom, license_file=licenses,
        output_dir=output, release_version="1.2.3", runtime_version="runtime-1",
    )
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    ids = [item["id"] for item in payload["components"]]
    assert len([item for item in ids if item.startswith("qwen-runtime-shard-")]) >= 2
    assert ids[-3:] == ["qwen-model", "qwen-tokenizer", "lumena-voice"]
    model_zip = output / "lumena-qwen-model-v1.2.3.zip"
    tokenizer_zip = output / "lumena-qwen-tokenizer-v1.2.3.zip"
    with zipfile.ZipFile(model_zip) as package:
        assert "model.safetensors" in package.namelist()
        assert not any(name.startswith("speech_tokenizer/") for name in package.namelist())
    with zipfile.ZipFile(tokenizer_zip) as package:
        assert "model.safetensors" in package.namelist()
