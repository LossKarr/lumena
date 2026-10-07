"""Contrats de finition Remotion : isolation, modes, chemins et qualité."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _valid_plan() -> dict:
    return {
        "title": "Produit",
        "scenes": [
            {
                "id": "intro",
                "component_name": "IntroScene",
                "duration_frames": 12,
                "text_title": "Bonjour",
                "text_subtitle": "Une vidéo vérifiée",
            },
            {
                "id": "cta",
                "component_name": "CtaScene",
                "duration_frames": 8,
                "text_title": "Découvrir",
            },
        ],
    }


class TestVideoSpec:
    def test_normalizes_duration_and_names(self):
        from src.tools.remotion_spec import normalize_video_spec

        plan = _valid_plan()
        plan["scenes"][1]["component_name"] = "../bad"
        spec = normalize_video_spec(plan, total_frames=90, fps=30, width=1920, height=1080)
        assert sum(scene.duration_frames for scene in spec.scenes) == 90
        assert spec.scenes[1].component_name == "CtaScene"

    def test_rejects_too_many_scenes(self):
        from src.tools.remotion_spec import normalize_video_spec

        plan = {"scenes": [{"id": f"s{i}"} for i in range(25)]}
        with pytest.raises(ValueError, match="24 scènes"):
            normalize_video_spec(plan, total_frames=900, fps=30, width=1920, height=1080)

    def test_small_model_auto_uses_safe_mode(self):
        from src.tools.remotion_spec import choose_generation_mode

        assert choose_generation_mode("auto", "small") == "safe"
        assert choose_generation_mode("auto", "large") == "expert"
        assert choose_generation_mode("expert", "small") == "expert"

    def test_safe_compiler_uses_no_external_url(self):
        from src.tools.remotion_spec import compile_safe_video, normalize_video_spec

        spec = normalize_video_spec(_valid_plan(), total_frames=90, fps=30, width=1920, height=1080)
        files = compile_safe_video(spec)
        assert "src/Video.tsx" in files
        assert "Sequence" in files["src/Video.tsx"]
        assert all("http://" not in content and "https://" not in content for content in files.values())
        assert '>{"Bonjour"}</div>' in files["src/scenes/IntroScene.tsx"]
        assert '>"Bonjour"</div>' not in files["src/scenes/IntroScene.tsx"]


class TestRemotionHardening:
    def test_scaffold_pins_every_remotion_package(self, tmp_path):
        from src.tools.remotion_engine import REMOTION_VERSION, VIDEO_TEMPLATES, scaffold_remotion_project

        files = scaffold_remotion_project(tmp_path, VIDEO_TEMPLATES["presentation"])
        package = json.loads(files["package.json"])
        versions = {
            value for name, value in package["dependencies"].items()
            if name == "remotion" or name.startswith("@remotion/")
        }
        assert versions == {REMOTION_VERSION}
        assert "^" not in REMOTION_VERSION

    def test_license_value_never_enters_generated_source(self, tmp_path):
        from src.tools.remotion_engine import VIDEO_TEMPLATES, scaffold_remotion_project

        with patch.dict(os.environ, {"REMOTION_LICENSE_KEY": "super-secret"}):
            files = scaffold_remotion_project(tmp_path, VIDEO_TEMPLATES["presentation"])
        assert "super-secret" not in "\n".join(files.values())
        assert "process.env.REMOTION_LICENSE_KEY" in files["render.mjs"]
        assert "renderStill" in files["render.mjs"]
        assert "quality/frame-" in files["render.mjs"]

    def test_project_path_must_stay_in_workspace(self, tmp_path):
        from src.tools.remotion_engine import resolve_video_project_path

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        with patch("src.utils.paths.WORKSPACE_DIR", workspace):
            with pytest.raises(ValueError, match="workspace"):
                resolve_video_project_path(outside)

    def test_render_output_cannot_escape_project(self, tmp_path):
        from src.tools.remotion_engine import resolve_render_output

        with pytest.raises(RuntimeError, match="hors du projet"):
            resolve_render_output(tmp_path, "../../stolen.mp4")

    def test_asset_outside_roots_is_rejected(self, tmp_path):
        from src.tools.remotion_engine import resolve_asset_paths

        outside = tmp_path / "outside.png"
        outside.write_bytes(b"png")
        workspace = tmp_path / "workspace"
        data = tmp_path / "data"
        received_images = data / "received_images"
        received_docs = data / "received_documents"
        for directory in (workspace, data, received_images, received_docs):
            directory.mkdir(parents=True, exist_ok=True)
        with patch.multiple(
            "src.utils.paths",
            WORKSPACE_DIR=workspace,
            DATA_DIR=data,
            RECEIVED_IMAGES_DIR=received_images,
            RECEIVED_DOCS_DIR=received_docs,
        ):
            assert resolve_asset_paths([str(outside)]) == []

    @pytest.mark.asyncio
    async def test_docker_is_default_even_when_node_exists(self, tmp_path):
        from src.tools.remotion_engine import render_video_in_docker

        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
            with patch("src.tools.remotion_engine.is_docker_available", new_callable=AsyncMock, return_value=True):
                with patch("src.tools.remotion_engine._is_node_available", new_callable=AsyncMock, return_value=True):
                    with patch("src.tools.remotion_engine._ensure_video_runtime_image", new_callable=AsyncMock) as runtime:
                        with patch("src.tools.remotion_engine._render_video_docker", new_callable=AsyncMock) as docker:
                            docker.return_value = (tmp_path / "output.mp4", "ok")
                            await render_video_in_docker(tmp_path)
                            runtime.assert_awaited_once()
                            docker.assert_awaited_once()

    def test_dependency_stamp_tracks_lockfile_and_runtime(self, tmp_path):
        from src.tools.remotion_engine import _dependencies_ready, _write_dependency_stamp

        (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        _write_dependency_stamp(tmp_path, "node:test")
        assert _dependencies_ready(tmp_path, "node:test")
        (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 4}', encoding="utf-8")
        assert not _dependencies_ready(tmp_path, "node:test")

    def test_partial_dependencies_are_removed_inside_project(self, tmp_path):
        from src.tools.remotion_engine import _clean_partial_dependencies

        partial = tmp_path / "node_modules" / "@remotion" / "partial.bin"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        (tmp_path / ".lumena-video-deps.json").write_text("{}", encoding="utf-8")
        _clean_partial_dependencies(tmp_path)
        assert not (tmp_path / "node_modules").exists()
        assert not (tmp_path / ".lumena-video-deps.json").exists()

    def test_recent_install_lock_refuses_concurrent_npm(self, tmp_path):
        from src.tools.remotion_engine import (
            VideoInfrastructureError,
            _dependency_install_lock,
        )

        (tmp_path / ".lumena-video-install.lock").write_text("{}", encoding="utf-8")
        with pytest.raises(VideoInfrastructureError, match="déjà active"):
            with _dependency_install_lock(tmp_path, 900):
                pass

    def test_install_timeout_is_independent_and_bounded(self):
        from src.tools.remotion_engine import _video_install_timeout

        with patch.dict(os.environ, {"LUMENA_VIDEO_INSTALL_TIMEOUT": "1200"}):
            assert _video_install_timeout() == 1200
        with patch.dict(os.environ, {"LUMENA_VIDEO_INSTALL_TIMEOUT": "99999"}):
            assert _video_install_timeout() == 3600

    def test_legacy_node_image_migrates_to_bundled_runtime(self):
        from src.tools.remotion_engine import DEFAULT_VIDEO_DOCKER_IMAGE, _video_docker_image

        with patch.dict(
            os.environ,
            {"LUMENA_VIDEO_DOCKER_IMAGE": "node:20.19.5-bookworm-slim"},
        ):
            assert _video_docker_image() == DEFAULT_VIDEO_DOCKER_IMAGE

    def test_bundled_runtime_contains_official_chrome_dependencies(self):
        dockerfile = Path("src/tools/remotion_runtime/Dockerfile").read_text(encoding="utf-8")
        for package in (
            "libnss3",
            "libdbus-1-3",
            "libatk1.0-0",
            "libgbm-dev",
            "libasound2",
            "libxrandr2",
            "libxkbcommon-dev",
            "libxfixes3",
            "libxcomposite1",
            "libxdamage1",
            "libatk-bridge2.0-0",
            "libpango-1.0-0",
            "libcairo2",
            "libcups2",
        ):
            assert package in dockerfile

    @pytest.mark.asyncio
    async def test_docker_prepares_browser_online_then_renders_offline(self, tmp_path):
        from src.tools.remotion_engine import _render_video_docker

        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()

        async def fake_sandbox(*, command, workdir, timeout_sec, network, **kwargs):
            if "browser" in command:
                assert network is True
                assert command[:2] == [
                    "node",
                    "node_modules/@remotion/cli/remotion-cli.js",
                ]
                return "Has browser", "", 0
            assert command == ["node", "render.mjs"]
            assert network is False
            (tmp_path / "output.mp4").write_bytes(b"video")
            return "LUMENA_RENDER_COMPLETE:output.mp4", "", 0

        with patch("src.tools.remotion_engine._dependencies_ready", return_value=True):
            with patch(
                "src.tools.remotion_engine._run_in_node_sandbox",
                side_effect=fake_sandbox,
            ) as sandbox:
                output, _ = await _render_video_docker(tmp_path, timeout_sec=300)

        assert output == tmp_path / "output.mp4"
        assert sandbox.await_count == 2

    @pytest.mark.asyncio
    async def test_browser_download_failure_is_infrastructure_error(self, tmp_path):
        from src.tools.remotion_engine import VideoInfrastructureError, _render_video_docker

        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        with patch("src.tools.remotion_engine._dependencies_ready", return_value=True):
            with patch(
                "src.tools.remotion_engine._run_in_node_sandbox",
                new_callable=AsyncMock,
                return_value=("", "download unavailable", 1),
            ):
                with pytest.raises(VideoInfrastructureError, match="navigateur Remotion"):
                    await _render_video_docker(tmp_path, timeout_sec=300)

    @pytest.mark.asyncio
    async def test_docker_probe_is_offline_and_bounded_to_project(self, tmp_path):
        from src.tools.remotion_engine import probe_video_in_docker

        workspace = tmp_path / "workspace"
        project = workspace / "video"
        project.mkdir(parents=True)
        video = project / "output.mp4"
        video.write_bytes(b"video")
        payload = {"streams": [], "format": {}}
        with patch("src.utils.paths.WORKSPACE_DIR", workspace):
            with patch(
                "src.tools.remotion_engine._ensure_video_runtime_image",
                new_callable=AsyncMock,
            ):
                with patch(
                    "src.tools.remotion_engine._run_in_node_sandbox",
                    new_callable=AsyncMock,
                    return_value=(json.dumps(payload), "", 0),
                ) as sandbox:
                    assert await probe_video_in_docker(project, video) == payload

        kwargs = sandbox.await_args.kwargs
        assert kwargs["network"] is False
        assert kwargs["timeout_sec"] == 30
        assert kwargs["command"][-1] == "/work/output.mp4"


class TestQualityGate:
    @pytest.mark.asyncio
    async def test_rejects_output_outside_project(self, tmp_path):
        from src.tools.remotion_quality import inspect_rendered_video

        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "output.mp4"
        outside.write_bytes(b"x" * 5000)
        report = await inspect_rendered_video(
            outside,
            project_dir=project,
            expected_duration_sec=30,
            expected_width=1920,
            expected_height=1080,
        )
        assert not report.passed
        assert any(issue.code == "OUTPUT_OUTSIDE_PROJECT" for issue in report.issues)

    @pytest.mark.asyncio
    async def test_rejects_tiny_output_before_probe(self, tmp_path):
        from src.tools.remotion_quality import inspect_rendered_video

        video = tmp_path / "output.mp4"
        video.write_bytes(b"bad")
        report = await inspect_rendered_video(
            video,
            project_dir=tmp_path,
            expected_duration_sec=30,
            expected_width=1920,
            expected_height=1080,
        )
        assert not report.passed
        assert any(issue.code == "OUTPUT_TOO_SMALL" for issue in report.issues)

    @pytest.mark.asyncio
    async def test_uses_remotion_proof_frames_without_ffmpeg(self, tmp_path):
        from PIL import Image
        from src.tools.remotion_quality import inspect_rendered_video

        video = tmp_path / "output.mp4"
        video.write_bytes(b"x" * 5000)
        quality = tmp_path / "quality"
        quality.mkdir()
        for index in range(3):
            Image.new("RGB", (64, 64), color=(0, 0, 0)).save(quality / f"frame-{index}.png")
        with patch("src.tools.remotion_quality.shutil.which", return_value=None):
            report = await inspect_rendered_video(
                video,
                project_dir=tmp_path,
                expected_duration_sec=30,
                expected_width=1920,
                expected_height=1080,
            )
        assert not report.passed
        assert report.sampled_frames == 3
        assert any(issue.code == "BLACK_FRAMES" for issue in report.issues)


class TestGenerationModes:
    @pytest.mark.asyncio
    async def test_safe_mode_compiles_without_scene_llm_calls(self, tmp_path):
        from src.reasoning.handlers.remotion import generate_video_handler

        ctx = MagicMock()
        ctx.lumena = MagicMock()
        ctx.lumena.llm.model = "local-8b"
        ctx.lumena.llm.chat = AsyncMock(return_value=json.dumps(_valid_plan()))
        ctx.lumena.llm.get_last_response_meta.return_value = {
            "model_used": "local-8b",
            "provider_used": "ollama",
        }

        async def fake_render(project_dir, timeout_sec):
            output = Path(project_dir) / "output.mp4"
            output.write_bytes(b"x" * 5000)
            return output, "ok"

        quality = MagicMock(passed=True)
        quality.summary.return_value = "validé"
        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
            with patch("src.reasoning.handlers.remotion.render_video_in_docker", side_effect=fake_render):
                with patch("src.reasoning.handlers.remotion.inspect_rendered_video", new_callable=AsyncMock, return_value=quality):
                    result = await generate_video_handler(
                        ctx,
                        description="Présentation produit",
                        duration_sec=3,
                        creation_mode="safe",
                    )
        assert result.success
        assert ctx.lumena.llm.chat.await_count == 1
        manifests = list(tmp_path.rglob("lumena-video.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["generation"]["mode"] == "safe"
        assert list(manifests[0].parent.glob("src/scenes/*.tsx"))

    @pytest.mark.asyncio
    async def test_infrastructure_failure_never_rewrites_tsx(self, tmp_path):
        from src.reasoning.handlers.remotion import generate_video_handler
        from src.tools.remotion_engine import VideoInfrastructureError

        ctx = MagicMock()
        ctx.lumena = MagicMock()
        ctx.lumena.llm.model = "local-8b"
        ctx.lumena.llm.chat = AsyncMock(return_value=json.dumps(_valid_plan()))
        ctx.lumena.llm.get_last_response_meta.return_value = {"model_used": "local-8b"}

        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
            with patch(
                "src.reasoning.handlers.remotion.render_video_in_docker",
                new_callable=AsyncMock,
                side_effect=VideoInfrastructureError("npm timeout"),
            ):
                with patch(
                    "src.reasoning.handlers.remotion._attempt_render_fix",
                    new_callable=AsyncMock,
                ) as repair:
                    result = await generate_video_handler(
                        ctx,
                        description="Présentation produit",
                        duration_sec=3,
                        creation_mode="safe",
                    )
        assert not result.success
        assert "Infrastructure vidéo indisponible" in result.output
        repair.assert_not_awaited()
        state_files = list(tmp_path.rglob(".lumena-video-job.json"))
        assert len(state_files) == 1
        assert json.loads(state_files[0].read_text(encoding="utf-8"))["status"] == "failed"

    @pytest.mark.asyncio
    async def test_retry_reuses_fresh_output_and_closes_failed_job(self, tmp_path):
        from src.reasoning.handlers.remotion import retry_video_render_handler

        workspace = tmp_path / "workspace"
        project = workspace / "video"
        source = project / "src" / "Video.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export default () => null;", encoding="utf-8")
        (project / "render.mjs").write_text("// render", encoding="utf-8")
        (project / "package-lock.json").write_text("{}", encoding="utf-8")
        (project / "lumena-video.json").write_text(
            json.dumps({
                "video_spec": {
                    "total_frames": 90,
                    "fps": 30,
                    "width": 1920,
                    "height": 1080,
                }
            }),
            encoding="utf-8",
        )
        output = project / "output.mp4"
        output.write_bytes(b"x" * 5000)
        output.touch()
        validation = MagicMock(valid=True)
        quality = MagicMock(passed=True)
        quality.summary.return_value = "validé"

        with patch("src.utils.paths.WORKSPACE_DIR", workspace):
            with patch("src.reasoning.handlers.remotion.validate_project", return_value=validation):
                with patch(
                    "src.reasoning.handlers.remotion.render_video_in_docker",
                    new_callable=AsyncMock,
                ) as render:
                    with patch(
                        "src.reasoning.handlers.remotion.inspect_rendered_video",
                        new_callable=AsyncMock,
                        return_value=quality,
                    ):
                        result = await retry_video_render_handler(
                            MagicMock(),
                            project_dir=str(project),
                        )

        assert result.success
        render.assert_not_awaited()
        state = json.loads((project / ".lumena-video-job.json").read_text(encoding="utf-8"))
        assert state["status"] == "complete"
        assert state["output_path"] == str(output)


class TestVideoMemoryIsolation:
    def test_environment_selects_memory_root(self, tmp_path):
        from src.learning.video_memory import VideoSuccessStore

        with patch.dict(os.environ, {"LUMENA_VIDEO_MEMORY_DIR": str(tmp_path)}):
            store = VideoSuccessStore()
        assert store.path == tmp_path / "video_successes.jsonl"


class TestVideoJobs:
    def test_progress_is_persisted_and_monotone(self, tmp_path):
        from src.tools.remotion_jobs import VideoJobTracker, read_video_job

        tracker = VideoJobTracker(tmp_path, "job-1")
        tracker.update("render", 75, "Rendu")
        tracker.update("late-message", 20, "Ne doit pas reculer")
        state = read_video_job(tmp_path)
        assert state is not None
        assert state["progress"] == 75
        assert state["phase"] == "late-message"

    def test_cancel_marker_stops_tracker(self, tmp_path):
        from src.tools.remotion_jobs import VideoJobTracker, request_video_cancel

        tracker = VideoJobTracker(tmp_path, "job-2")
        request_video_cancel(tmp_path)
        with pytest.raises(RuntimeError, match="annulée"):
            tracker.ensure_active()
