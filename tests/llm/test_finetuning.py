"""Tests for the fine-tuning subsystem (gpu_detect, data_prep, pipeline, export, ollama, API)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import httpx
import pytest

try:
    import datasets as _ds
    HAS_DATASETS = True
except ImportError:
    HAS_DATASETS = False


# ============================================================
# TestGpuDetect (13 tests)
# ============================================================
class TestGpuDetect:
    def test_detect_gpu_safe_returns_dict(self):
        from src.training.gpu_detect import detect_gpu_safe
        result = detect_gpu_safe()
        assert isinstance(result, dict)
        assert "available" in result

    def test_detect_gpu_safe_no_crash_no_torch(self):
        from src.training.gpu_detect import detect_gpu_safe
        with patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
            result = detect_gpu_safe()
        assert result["available"] is False

    def test_recommend_models_2gb(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(2.0)
        ids = [m["ollama_id"] for m in models]
        assert "qwen3:0.6b" in ids
        # No 6GB+ models
        for m in models:
            assert m["vram_ft_min_gb"] <= 2

    def test_recommend_models_6gb(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(6.0)
        ids = [m["ollama_id"] for m in models]
        assert "qwen3:4b" in ids
        assert "gemma3:4b" in ids

    def test_recommend_models_8gb(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(8.0)
        ids = [m["ollama_id"] for m in models]
        assert "mistral:7b" in ids
        assert "deepseek-r1:7b" in ids

    def test_recommend_models_10gb(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(10.0)
        ids = [m["ollama_id"] for m in models]
        assert "qwen3:8b" in ids
        assert "llama3.3:8b" in ids

    def test_recommend_models_24gb(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(24.0)
        ids = [m["ollama_id"] for m in models]
        assert "gemma3:27b" in ids

    def test_recommend_marks_already_installed(self):
        from src.training.gpu_detect import recommend_models
        models = recommend_models(10.0, ollama_installed=["qwen3:8b"])
        for m in models:
            if m["ollama_id"] == "qwen3:8b":
                assert m["already_installed"] is True
                break
        else:
            pytest.fail("qwen3:8b not in recommended models")

    def test_finetune_ok_false_nomic_embed(self):
        from src.training.gpu_detect import FINETUNE_CATALOG
        for entry in FINETUNE_CATALOG:
            if entry["ollama_id"] == "nomic-embed-text":
                assert entry["finetune_ok"] is False
                break
        else:
            pytest.fail("nomic-embed-text not in catalog")

    def test_finetune_ok_false_lumena_v1(self):
        from src.training.gpu_detect import FINETUNE_CATALOG
        for entry in FINETUNE_CATALOG:
            if entry["ollama_id"] == "lumena-v1":
                assert entry["finetune_ok"] is False
                break
        else:
            pytest.fail("lumena-v1 not in catalog")

    def test_finetune_ok_false_qwen3_235b(self):
        from src.training.gpu_detect import FINETUNE_CATALOG
        for entry in FINETUNE_CATALOG:
            if entry["ollama_id"] == "qwen3:235b":
                assert entry["finetune_ok"] is False
                break
        else:
            pytest.fail("qwen3:235b not in catalog")

    def test_check_deps_returns_all_keys(self):
        from src.training.gpu_detect import check_finetuning_deps
        result = check_finetuning_deps()
        assert "all_ok" in result
        assert "missing" in result
        assert "installed" in result
        assert "torch_ok" in result
        assert "cuda_ok" in result

    def test_get_full_status_structure(self):
        from src.training.gpu_detect import get_full_status
        with patch("src.training.gpu_detect.detect_gpu_safe", return_value={"available": False, "vram_gb": 0}), \
             patch("src.training.gpu_detect.detect_ollama_installed_models", return_value=[]):
            result = get_full_status()
        assert "gpu" in result
        assert "deps" in result
        assert "recommended_models" in result
        assert "ready_to_train" in result
        assert "dataset_count" in result


# ============================================================
# TestDataPrep (8 tests)
# ============================================================
class TestDataPrep:
    def _make_entry(self, user: str = "Bonjour Lumena, comment ça va aujourd'hui ?",
                    assistant: str = "Salut ! Je suis Lumena, ton assistante IA. Comment puis-je t'aider aujourd'hui ?",
                    quality_flag: str = "ok", content_hash: str = "abc123"):
        return {
            "conversations": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant}
            ],
            "metadata": {
                "timestamp": "2026-04-08T09:00:00",
                "quality_flag": quality_flag,
                "content_hash": content_hash,
            }
        }

    def test_convert_conversations_to_messages(self):
        from src.training.data_prep import convert_to_trl_format
        raw = [self._make_entry()]
        result = convert_to_trl_format(raw)
        assert len(result) == 1
        assert "messages" in result[0]
        assert result[0]["messages"][0]["role"] == "user"

    def test_filter_negative_quality_flag(self):
        """quality_flag='negative_feedback' must be excluded."""
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool"
            pool.mkdir()
            val = Path(td) / "val"
            val.mkdir()
            f = pool / "2026-04-08.jsonl"
            entries = [
                self._make_entry(content_hash="ok1"),
                self._make_entry(quality_flag="negative_feedback", content_hash="bad1"),
            ]
            # Write enough entries to meet min_conversations
            for i in range(60):
                entries.append(self._make_entry(content_hash=f"extra{i}"))
            f.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

            from src.training.data_prep import load_lumena_pool
            result = load_lumena_pool(pool, val, min_conversations=0)
            hashes = [e["metadata"]["content_hash"] for e in result]
            assert "bad1" not in hashes
            assert "ok1" in hashes

    def test_filter_short_assistant_message(self):
        """assistant reply < 50 chars -> excluded."""
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool"
            pool.mkdir()
            val = Path(td) / "val"
            val.mkdir()
            f = pool / "2026-04-08.jsonl"
            entries = [self._make_entry(assistant="Short", content_hash="short1")]
            for i in range(60):
                entries.append(self._make_entry(content_hash=f"long{i}"))
            f.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

            from src.training.data_prep import load_lumena_pool
            result = load_lumena_pool(pool, val, min_conversations=0)
            hashes = [e["metadata"]["content_hash"] for e in result]
            assert "short1" not in hashes

    def test_deduplicate_identical_conversations(self):
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool"
            pool.mkdir()
            val = Path(td) / "val"
            val.mkdir()
            f = pool / "2026-04-08.jsonl"
            entries = [self._make_entry(content_hash="dup")] * 3
            for i in range(60):
                entries.append(self._make_entry(content_hash=f"unique{i}"))
            f.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

            from src.training.data_prep import load_lumena_pool
            result = load_lumena_pool(pool, val, min_conversations=0)
            dup_count = sum(1 for e in result if e["metadata"]["content_hash"] == "dup")
            assert dup_count == 1

    @pytest.mark.skipif(not HAS_DATASETS, reason="datasets not installed")
    def test_split_dataset_ratio(self):
        from src.training.data_prep import split_dataset
        data = [{"messages": [{"role": "user", "content": f"msg{i}"}, {"role": "assistant", "content": f"reply{i}"}]} for i in range(100)]
        train, test = split_dataset(data, eval_ratio=0.1)
        assert len(train) == 90
        assert len(test) == 10

    @pytest.mark.skipif(not HAS_DATASETS, reason="datasets not installed")
    def test_split_dataset_no_overlap(self):
        from src.training.data_prep import split_dataset
        data = [{"messages": [{"role": "user", "content": f"msg{i}"}, {"role": "assistant", "content": f"reply{i}"}]} for i in range(100)]
        train, test = split_dataset(data, eval_ratio=0.1)
        train_msgs = {json.dumps(r) for r in train}
        test_msgs = {json.dumps(r) for r in test}
        assert len(train_msgs & test_msgs) == 0

    def test_get_dataset_stats_keys(self):
        from src.training.data_prep import get_dataset_stats
        data = [self._make_entry()]
        stats = get_dataset_stats(data)
        assert "total" in stats
        assert "avg_turns" in stats
        assert "avg_tokens_estimate" in stats
        assert stats["total"] == 1

    @pytest.mark.skipif(not HAS_DATASETS, reason="datasets not installed")
    def test_empty_dataset_raises(self):
        from src.training.data_prep import split_dataset
        with pytest.raises(ValueError, match="too small"):
            split_dataset([{"messages": []}] * 5)


# ============================================================
# TestPipelineUnit (5 tests — mocks, sans GPU)
# ============================================================
class TestPipelineUnit:
    def test_config_defaults_valid(self):
        from src.training.pipeline import FinetuneConfig
        cfg = FinetuneConfig(base_model_hf_id="test/model", output_name="test-v1")
        assert cfg.lora_r == 16
        assert cfg.lora_alpha == 32
        assert cfg.num_epochs == 3
        assert cfg.learning_rate == 2e-4

    def test_progress_callback_dict_serializable(self):
        from src.training.pipeline import ProgressCallback
        collected = []
        cb = ProgressCallback(on_progress=collected.append)
        cb._emit({"step": 1, "loss": 0.5, "phase": "training"})
        assert len(collected) == 1
        # Must be JSON-serializable
        json.dumps(collected[0])

    def test_cancel_flag_set(self):
        from src.training.pipeline import cancel_training, is_cancelled, _reset_cancel
        _reset_cancel()
        assert not is_cancelled()
        cancel_training()
        assert is_cancelled()
        _reset_cancel()

    def test_run_raises_without_unsloth(self):
        from src.training.pipeline import FinetuneConfig, run_finetuning
        cfg = FinetuneConfig(base_model_hf_id="test/model", output_name="test-v1")
        with patch.dict("sys.modules", {"unsloth": None}):
            with pytest.raises(ImportError):
                run_finetuning(cfg, dataset_train=[])

    def test_lora_target_modules_list_valid(self):
        from src.training.pipeline import LORA_TARGET_MODULES
        assert isinstance(LORA_TARGET_MODULES, list)
        assert len(LORA_TARGET_MODULES) >= 5
        assert "q_proj" in LORA_TARGET_MODULES


# ============================================================
# TestExportGguf (5 tests)
# ============================================================
class TestExportGguf:
    def test_gguf_size_gb_calculation(self):
        from src.training.export_gguf import get_gguf_size_gb
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.gguf"
            p.write_bytes(b"x" * (1024 * 1024 * 100))  # 100 MB
            size = get_gguf_size_gb(str(p))
            assert 0.09 < size < 0.11

    def test_supported_quant_types(self):
        from src.training.export_gguf import SUPPORTED_QUANT_TYPES
        assert "Q4_K_M" in SUPPORTED_QUANT_TYPES
        assert "Q5_K_M" in SUPPORTED_QUANT_TYPES
        assert "Q8_0" in SUPPORTED_QUANT_TYPES
        assert "Q2_K" in SUPPORTED_QUANT_TYPES
        assert len(SUPPORTED_QUANT_TYPES) == 4

    def test_convert_raises_missing_dir(self):
        from src.training.export_gguf import convert_to_gguf
        with pytest.raises(ValueError, match="does not exist"):
            convert_to_gguf("/nonexistent/path", "/tmp/out.gguf")

    def test_quantize_raises_missing_f16(self):
        from src.training.export_gguf import quantize_gguf
        with pytest.raises(ValueError, match="does not exist"):
            quantize_gguf("/nonexistent/f16.gguf", "/tmp/out.gguf")

    def test_cleanup_removes_files(self):
        from src.training.export_gguf import cleanup_intermediate
        with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as f:
            path = f.name
            f.write(b"test data")
        assert Path(path).exists()
        cleanup_intermediate([path])
        assert not Path(path).exists()


# ============================================================
# TestOllamaImport (7 tests)
# ============================================================
class TestOllamaImport:
    def test_write_modelfile_contains_from(self):
        from src.training.ollama_import import write_modelfile
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "Modelfile")
            write_modelfile("/path/to/model.gguf", "test-model", "", out)
            content = Path(out).read_text()
            assert "FROM /path/to/model.gguf" in content

    def test_write_modelfile_with_system_prompt(self):
        from src.training.ollama_import import write_modelfile
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "Modelfile")
            write_modelfile("/model.gguf", "test", "Tu es Lumena.", out)
            content = Path(out).read_text()
            assert 'SYSTEM "Tu es Lumena."' in content

    def test_write_modelfile_without_system_prompt(self):
        from src.training.ollama_import import write_modelfile
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "Modelfile")
            write_modelfile("/model.gguf", "test", "", out)
            content = Path(out).read_text()
            assert "SYSTEM" not in content

    def test_list_finetuned_empty_if_no_file(self):
        from src.training.ollama_import import list_finetuned_models
        with patch("src.training.ollama_import._registry_path", return_value=Path("/nonexistent/file.json")):
            result = list_finetuned_models()
        assert result == []


    def test_register_writes_json(self):
        from src.training.ollama_import import register_in_lumena
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "finetuned_models.json"

            def fake_save(models):
                reg.write_text(json.dumps(models), encoding="utf-8")

            with patch("src.training.ollama_import._registry_path", return_value=reg), \
                 patch("src.training.ollama_import._save_registry", side_effect=fake_save), \
                 patch("src.llm.providers.register_ollama_models"):
                register_in_lumena("test-model-v1", "qwen3:8b", "pool")
            data = json.loads(reg.read_text(encoding="utf-8"))
            assert len(data) == 1
            assert data[0]["model_name"] == "test-model-v1"

    def test_delete_removes_from_registry(self):
        from src.training.ollama_import import delete_finetuned_model
        existing = [{"model_name": "test-v1"}, {"model_name": "test-v2"}]
        saved = []
        with patch("src.training.ollama_import.list_finetuned_models", return_value=existing), \
             patch("src.training.ollama_import._save_registry", side_effect=lambda m: saved.append(m)), \
             patch("subprocess.run"):
            result = delete_finetuned_model("test-v1")
        assert result is True
        assert len(saved[0]) == 1
        assert saved[0][0]["model_name"] == "test-v2"

    def test_ollama_create_timeout_value(self):
        from src.training.ollama_import import ollama_create
        with tempfile.NamedTemporaryFile(suffix="Modelfile", delete=False, mode="w") as f:
            f.write("FROM test.gguf\n")
            mf_path = f.name
        try:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="success")
                ollama_create("test-model", mf_path)
                _, kwargs = mock_run.call_args
                assert kwargs.get("timeout") == 300
        finally:
            os.unlink(mf_path)


# ============================================================
# TestFinetuningAPI (8 tests)
# ============================================================
def _make_ft_app():
    from fastapi import FastAPI
    from web.routes.finetuning import router
    from web.routes import deps
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


class TestFinetuningAPI:
    @pytest.mark.asyncio
    async def test_status_endpoint_structure(self):
        with patch("src.training.gpu_detect.get_full_status", return_value={
            "gpu": {"available": False, "vram_gb": 0},
            "deps": {"all_ok": False, "missing": ["unsloth"]},
            "ollama_installed": [],
            "recommended_models": [],
            "dataset_available": False,
            "dataset_count": 0,
            "ready_to_train": False,
        }):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/finetuning/status")
        assert r.status_code == 200
        data = r.json()
        assert "gpu" in data
        assert "deps" in data

    @pytest.mark.asyncio
    async def test_models_endpoint_returns_list(self):
        with patch("src.training.gpu_detect.detect_gpu_safe", return_value={"available": False, "vram_gb": 0}), \
             patch("src.training.gpu_detect.recommend_models", return_value=[]), \
             patch("src.training.gpu_detect.detect_ollama_installed_models", return_value=[]):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/finetuning/models")
        assert r.status_code == 200
        assert "models" in r.json()

    @pytest.mark.asyncio
    async def test_start_missing_base_model_422(self):
        app = _make_ft_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/finetuning/start", json={
                "ollama_tag": "test", "output_name": "test-v1"
            })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_start_missing_output_name_422(self):
        app = _make_ft_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/finetuning/start", json={
                "base_model": "test/model", "ollama_tag": "test"
            })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_start_conflict_if_active_job_409(self):
        import web.routes.finetuning as ft_mod
        with patch.object(ft_mod, "_active_job", {"status": "running"}):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/api/finetuning/start", json={
                    "base_model": "unsloth/Qwen3-8B-bnb-4bit",
                    "ollama_tag": "qwen3:8b",
                    "output_name": "test-v1",
                })
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_cancel_no_job_returns_false(self):
        import web.routes.finetuning as ft_mod
        with patch.object(ft_mod, "_active_job", None):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/api/finetuning/cancel")
        assert r.status_code == 200
        assert r.json()["cancelled"] is False

    @pytest.mark.asyncio
    async def test_jobs_endpoint_returns_list(self):
        with patch("src.training.ollama_import.list_finetuned_models", return_value=[]):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/finetuning/jobs")
        assert r.status_code == 200
        assert "models" in r.json()

    @pytest.mark.asyncio
    async def test_delete_unknown_job_404(self):
        with patch("src.training.ollama_import.delete_finetuned_model", return_value=False):
            app = _make_ft_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.delete("/api/finetuning/jobs/nonexistent-model")
        assert r.status_code == 404


# ============================================================
# Guard tests (P9.2)
# ============================================================
class TestFinetuningGuards:
    def test_requirements_finetuning_exists(self):
        """requirements-finetuning.txt must be present alongside src/training/."""
        from src.utils.paths import ROOT_DIR
        assert (ROOT_DIR / "src" / "training" / "__init__.py").exists()
        assert (ROOT_DIR / "requirements-finetuning.txt").exists()

    def test_pipeline_uses_assistant_only_loss(self):
        """Guard: SFTConfig must use assistant_only_loss=True."""
        import inspect
        from src.training.pipeline import run_finetuning
        source = inspect.getsource(run_finetuning)
        assert "assistant_only_loss=True" in source, (
            "assistant_only_loss=True manquant dans SFTConfig — "
            "le modèle entraînerait sur les tokens utilisateur"
        )
