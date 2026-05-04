"""
Tests pour l'intégration Remotion vidéo dans Lumena.

Couvre:
  - remotion_engine.py (moteur de rendu)
  - remotion_prompts.py (prompts LLM)
  - handlers/remotion.py (handlers V2)
  - tool_registry.py (enregistrement)
  - react_config.py (routage hints)
  - prompt_builder.py (heuristique vidéo)
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# 1. Tests remotion_engine.py — Sélection template
# ═══════════════════════════════════════════════════════════════

class TestSelectTemplate:
    """Vérifie que select_template() choisit le bon template par keywords."""

    def test_presentation_keywords(self):
        from src.tools.remotion_engine import select_template
        name, tpl = select_template("présentation de mon produit SaaS")
        assert name == "presentation"
        assert tpl["width"] == 1920
        assert tpl["height"] == 1080

    def test_social_short_keywords(self):
        from src.tools.remotion_engine import select_template
        name, tpl = select_template("fais un reel TikTok vertical pour mon resto")
        assert name == "social_short"
        assert tpl["width"] == 1080
        assert tpl["height"] == 1920

    def test_explainer_keywords(self):
        from src.tools.remotion_engine import select_template
        name, tpl = select_template("vidéo explicative comment utiliser l'app")
        assert name == "explainer"
        assert tpl["duration_sec"] == 60

    def test_square_social_keywords(self):
        from src.tools.remotion_engine import select_template
        name, tpl = select_template("post carré Instagram pour mon produit")
        assert name == "square_social"
        assert tpl["width"] == 1080
        assert tpl["height"] == 1080

    def test_default_is_presentation(self):
        from src.tools.remotion_engine import select_template
        name, _ = select_template("fais quelque chose de beau")
        assert name == "presentation"

    def test_custom_template_available(self):
        from src.tools.remotion_engine import VIDEO_TEMPLATES
        assert "custom" in VIDEO_TEMPLATES
        assert VIDEO_TEMPLATES["custom"]["scenes"] == []


# ═══════════════════════════════════════════════════════════════
# 2. Tests remotion_engine.py — Scaffold projet
# ═══════════════════════════════════════════════════════════════

class TestScaffoldProject:
    """Vérifie que scaffold_remotion_project() crée les bons fichiers."""

    def test_scaffold_creates_required_files(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        tpl = VIDEO_TEMPLATES["presentation"]
        files = scaffold_remotion_project(tmp_path, tpl, composition_id="Main")
        assert "package.json" in files
        assert "render.mjs" in files
        assert "src/Root.tsx" in files
        assert "src/index.ts" in files
        assert "tsconfig.json" in files

    def test_package_json_has_remotion_deps(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        tpl = VIDEO_TEMPLATES["presentation"]
        files = scaffold_remotion_project(tmp_path, tpl)
        pkg = json.loads(files["package.json"])
        assert "remotion" in pkg["dependencies"]
        assert "@remotion/renderer" in pkg["dependencies"]
        assert "@remotion/bundler" in pkg["dependencies"]
        assert "@remotion/cli" in pkg["dependencies"]

    def test_render_mjs_contains_renderMedia(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        tpl = VIDEO_TEMPLATES["presentation"]
        files = scaffold_remotion_project(tmp_path, tpl)
        render_code = files["render.mjs"]
        assert "renderMedia" in render_code
        assert "bundle" in render_code
        assert "selectComposition" in render_code
        assert "LUMENA_RENDER_COMPLETE" in render_code

    def test_root_tsx_has_composition(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        tpl = VIDEO_TEMPLATES["presentation"]
        files = scaffold_remotion_project(tmp_path, tpl)
        root = files["src/Root.tsx"]
        assert "Composition" in root
        assert "1920" in root
        assert "1080" in root
        assert "30" in root

    def test_index_ts_has_registerRoot(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        tpl = VIDEO_TEMPLATES["presentation"]
        files = scaffold_remotion_project(tmp_path, tpl)
        index = files["src/index.ts"]
        assert "registerRoot" in index

    def test_render_mjs_injects_license_key(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        with patch.dict(os.environ, {"REMOTION_LICENSE_KEY": "test-key-123"}):
            tpl = VIDEO_TEMPLATES["presentation"]
            files = scaffold_remotion_project(tmp_path, tpl)
            assert "test-key-123" in files["render.mjs"] or "licenseKey" in files["render.mjs"]

    def test_render_mjs_no_license_when_empty(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        with patch.dict(os.environ, {"REMOTION_LICENSE_KEY": ""}, clear=False):
            tpl = VIDEO_TEMPLATES["presentation"]
            files = scaffold_remotion_project(tmp_path, tpl)
            assert "licenseKey" not in files["render.mjs"]


# ═══════════════════════════════════════════════════════════════
# 3. Tests remotion_engine.py — Écriture scènes
# ═══════════════════════════════════════════════════════════════

class TestWriteSceneFiles:
    """Vérifie que write_scene_files() écrit correctement les fichiers."""

    def test_writes_scene_files(self, tmp_path):
        from src.tools.remotion_engine import write_scene_files
        scenes = {
            "src/scenes/IntroScene.tsx": "export default function IntroScene() { return <div>Intro</div>; }",
            "src/scenes/CtaScene.tsx": "export default function CtaScene() { return <div>CTA</div>; }",
        }
        write_scene_files(tmp_path, scenes)
        assert (tmp_path / "src/scenes/IntroScene.tsx").exists()
        assert (tmp_path / "src/scenes/CtaScene.tsx").exists()
        assert "IntroScene" in (tmp_path / "src/scenes/IntroScene.tsx").read_text()

    def test_creates_nested_directories(self, tmp_path):
        from src.tools.remotion_engine import write_scene_files
        scenes = {"src/deep/nested/Scene.tsx": "code"}
        write_scene_files(tmp_path, scenes)
        assert (tmp_path / "src/deep/nested/Scene.tsx").exists()


# ═══════════════════════════════════════════════════════════════
# 4. Tests remotion_engine.py — Docker sandbox
# ═══════════════════════════════════════════════════════════════

class TestDockerSandbox:
    """Vérifie la logique Docker sans appeler Docker réellement."""

    @pytest.mark.asyncio
    async def test_run_in_node_sandbox_builds_correct_args(self):
        from src.tools.remotion_engine import _run_in_node_sandbox
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"OK", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            stdout, stderr, code = await _run_in_node_sandbox(
                "npm install",
                workdir=str(Path.cwd()),
                timeout_sec=60,
                network=True,
            )
            call_args = mock_exec.call_args[0]
            assert "docker" in call_args
            assert "run" in call_args
            assert "node:20-slim" in call_args or os.getenv("LUMENA_VIDEO_DOCKER_IMAGE", "node:20-slim") in call_args
            assert code == 0

    @pytest.mark.asyncio
    async def test_render_video_fails_without_docker(self, tmp_path):
        from src.tools.remotion_engine import render_video_in_docker
        with patch("src.tools.remotion_engine.is_docker_available", new_callable=AsyncMock, return_value=False):
            with pytest.raises(RuntimeError, match="Docker non disponible"):
                await render_video_in_docker(tmp_path)

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        from src.tools.remotion_engine import _run_in_node_sandbox
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            import asyncio
            mock_proc = AsyncMock()
            mock_proc.communicate.side_effect = asyncio.TimeoutError()
            mock_proc.kill = MagicMock()
            mock_exec.return_value = mock_proc
            stdout, stderr, code = await _run_in_node_sandbox("slow cmd", "/tmp", timeout_sec=1)
            assert code == -1
            assert "Timeout" in stderr

    @pytest.mark.asyncio
    async def test_gpu_flag_adds_gpus_all(self):
        from src.tools.remotion_engine import _run_in_node_sandbox
        with patch.dict(os.environ, {"LUMENA_VIDEO_GPU": "true"}):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate.return_value = (b"OK", b"")
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc
                await _run_in_node_sandbox("node render.mjs", workdir=str(Path.cwd()), timeout_sec=60)
                call_args = mock_exec.call_args[0]
                assert "--gpus" in call_args
                assert "all" in call_args

    @pytest.mark.asyncio
    async def test_no_gpu_flag_by_default(self):
        from src.tools.remotion_engine import _run_in_node_sandbox
        with patch.dict(os.environ, {"LUMENA_VIDEO_GPU": ""}, clear=False):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate.return_value = (b"OK", b"")
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc
                await _run_in_node_sandbox("node render.mjs", workdir=str(Path.cwd()), timeout_sec=60)
                call_args = mock_exec.call_args[0]
                assert "--gpus" not in call_args


# ═══════════════════════════════════════════════════════════════
# 4b. Tests GPU dans scaffold (render.mjs)
# ═══════════════════════════════════════════════════════════════

class TestGpuScaffold:
    """Vérifie que scaffold_remotion_project injecte les options GPU."""

    def test_gpu_options_in_render_mjs(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        with patch.dict(os.environ, {"LUMENA_VIDEO_GPU": "true"}):
            tpl = VIDEO_TEMPLATES["presentation"]
            files = scaffold_remotion_project(tmp_path, tpl)
            render_code = files["render.mjs"]
            assert "chromiumOptions" in render_code
            assert "gl" in render_code
            assert "egl" in render_code

    def test_no_gpu_options_by_default(self, tmp_path):
        from src.tools.remotion_engine import scaffold_remotion_project, VIDEO_TEMPLATES
        with patch.dict(os.environ, {"LUMENA_VIDEO_GPU": ""}, clear=False):
            tpl = VIDEO_TEMPLATES["presentation"]
            files = scaffold_remotion_project(tmp_path, tpl)
            render_code = files["render.mjs"]
            assert "chromiumOptions" not in render_code


# ═══════════════════════════════════════════════════════════════
# 5. Tests remotion_prompts.py — Contenu prompts
# ═══════════════════════════════════════════════════════════════

class TestRemotionPrompts:
    """Vérifie que les prompts contiennent les éléments critiques."""

    def test_plan_prompt_has_placeholders(self):
        from src.tools.remotion_prompts import VIDEO_PLAN_PROMPT
        assert "{description}" in VIDEO_PLAN_PROMPT
        assert "{template_name}" in VIDEO_PLAN_PROMPT
        assert "{total_frames}" in VIDEO_PLAN_PROMPT
        assert "{fps}" in VIDEO_PLAN_PROMPT
        assert "{width}" in VIDEO_PLAN_PROMPT

    def test_scene_prompt_has_remotion_imports(self):
        from src.tools.remotion_prompts import SCENE_COMPONENT_PROMPT
        assert "useCurrentFrame" in SCENE_COMPONENT_PROMPT
        assert "interpolate" in SCENE_COMPONENT_PROMPT
        assert "spring" in SCENE_COMPONENT_PROMPT
        assert "AbsoluteFill" in SCENE_COMPONENT_PROMPT

    def test_scene_prompt_forbids_local_files(self):
        from src.tools.remotion_prompts import SCENE_COMPONENT_PROMPT
        # Le prompt encadre l'usage des fichiers locaux via des placeholders dynamiques
        # {image_constraint} contiendra "Unsplash" ou "staticFile" selon les assets
        # {static_file_constraint} contiendra "staticFile" ou "INTERDIT"
        assert "{image_constraint}" in SCENE_COMPONENT_PROMPT or "Unsplash" in SCENE_COMPONENT_PROMPT or "staticFile" in SCENE_COMPONENT_PROMPT

    def test_root_tsx_template_has_composition(self):
        from src.tools.remotion_prompts import ROOT_TSX_TEMPLATE
        assert "Composition" in ROOT_TSX_TEMPLATE
        assert "{composition_id}" in ROOT_TSX_TEMPLATE
        assert "{total_frames}" in ROOT_TSX_TEMPLATE

    def test_index_ts_template_has_registerRoot(self):
        from src.tools.remotion_prompts import INDEX_TS_TEMPLATE
        assert "registerRoot" in INDEX_TS_TEMPLATE

    def test_plan_prompt_requires_json(self):
        from src.tools.remotion_prompts import VIDEO_PLAN_SYSTEM
        assert "JSON" in VIDEO_PLAN_SYSTEM


# ═══════════════════════════════════════════════════════════════
# 6. Tests handlers/remotion.py — Handler generate_video
# ═══════════════════════════════════════════════════════════════

class TestGenerateVideoHandler:
    """Vérifie le handler generate_video de bout en bout (LLM mocké)."""

    @pytest.fixture
    def ctx(self, tmp_path):
        ctx = MagicMock()
        ctx.lumena_root = str(tmp_path)
        ctx.lumena = MagicMock()
        ctx.lumena.llm = AsyncMock()
        ctx.lumena.llm.chat = AsyncMock(return_value="Mocked LLM response")
        ctx.lumena.llm.model = "mock-model"
        return ctx

    @pytest.mark.asyncio
    async def test_missing_description_fails(self, ctx):
        from src.reasoning.handlers.remotion import generate_video_handler
        result = await generate_video_handler(ctx, description="")
        assert not result.success
        assert "description" in result.output.lower()

    @pytest.mark.asyncio
    async def test_invalid_template_fails(self, ctx):
        from src.reasoning.handlers.remotion import generate_video_handler
        result = await generate_video_handler(ctx, description="test", template="nonexistent")
        assert not result.success
        assert "nonexistent" in result.output

    @pytest.mark.asyncio
    async def test_full_pipeline_mocked(self, ctx, tmp_path):
        from src.reasoning.handlers.remotion import generate_video_handler
        plan_json = json.dumps({
            "title": "Test Video",
            "scenes": [
                {
                    "id": "intro",
                    "component_name": "IntroScene",
                    "duration_frames": 450,
                    "text_title": "Hello",
                    "text_subtitle": "",
                    "background_type": "gradient",
                    "background_value": "linear-gradient(135deg, #667eea, #764ba2)",
                    "image_url": "",
                    "animation_in": "fadeIn",
                    "animation_out": "fadeOut",
                    "elements": ["title"],
                },
                {
                    "id": "cta",
                    "component_name": "CtaScene",
                    "duration_frames": 450,
                    "text_title": "Buy Now",
                    "text_subtitle": "",
                    "background_type": "solid",
                    "background_value": "#0f0f1a",
                    "image_url": "",
                    "animation_in": "scaleUp",
                    "animation_out": "fadeOut",
                    "elements": ["title"],
                },
            ],
            "palette": {"primary": "#667eea", "secondary": "#764ba2", "text": "#fff", "bg": "#0f0f1a", "accent": "#f59f4a"},
            "font_family": "Inter",
            "total_frames": 900,
        })
        scene_code = "import { useCurrentFrame } from 'remotion';\nexport default function Scene() { const frame = useCurrentFrame(); return <div>{frame}</div>; }"
        video_tsx = "import IntroScene from './scenes/IntroScene';\nexport default function Video() { return <IntroScene />; }"
        ctx.lumena.llm.chat = AsyncMock(side_effect=[plan_json, scene_code, scene_code, video_tsx])
        fake_video = tmp_path / "output.mp4"
        fake_video.write_bytes(b"fake mp4 content")
        with patch("src.reasoning.handlers.remotion.render_video_in_docker", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = (fake_video, "LUMENA_RENDER_COMPLETE:output.mp4")
            with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
                result = await generate_video_handler(
                    ctx, description="vidéo de présentation", duration_sec=30,
                )
        assert result.success
        assert "output.mp4" in result.output or "✅" in result.output

    @pytest.mark.asyncio
    async def test_no_llm_fails_gracefully(self, tmp_path):
        from src.reasoning.handlers.remotion import generate_video_handler
        ctx = MagicMock()
        ctx.lumena = None
        result = await generate_video_handler(ctx, description="test")
        assert not result.success


# ═══════════════════════════════════════════════════════════════
# 7. Tests handlers/remotion.py — Handler edit_video
# ═══════════════════════════════════════════════════════════════

class TestEditVideoHandler:

    @pytest.fixture
    def ctx(self, tmp_path):
        ctx = MagicMock()
        ctx.lumena_root = str(tmp_path)
        ctx.lumena = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_missing_instructions_fails(self, ctx):
        from src.reasoning.handlers.remotion import edit_video_handler
        result = await edit_video_handler(ctx, instructions="")
        assert not result.success
        assert "instructions" in result.output.lower()

    @pytest.mark.asyncio
    async def test_nonexistent_dir_fails(self, ctx, tmp_path):
        from src.reasoning.handlers.remotion import edit_video_handler
        result = await edit_video_handler(
            ctx, project_dir=str(tmp_path / "nope"), instructions="change colors"
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_auto_detect_last_project(self, ctx, tmp_path):
        from src.reasoning.handlers.remotion import edit_video_handler
        proj_dir = tmp_path / "workspace" / "2026-04-10" / "test"
        proj_dir.mkdir(parents=True)
        (proj_dir / "render.mjs").write_text("// render script")
        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path / "workspace"):
            with patch("src.agents.sub_agent.delegate_to_agent", new_callable=AsyncMock) as mock_agent:
                mock_agent.return_value = "Modified successfully"
                result = await edit_video_handler(ctx, instructions="change the title")
        assert result.success


# ═══════════════════════════════════════════════════════════════
# 8. Tests handlers/remotion.py — Handler list_video_projects
# ═══════════════════════════════════════════════════════════════

class TestListVideoProjects:

    @pytest.fixture
    def ctx(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_empty_workspace(self, ctx, tmp_path):
        from src.reasoning.handlers.remotion import list_video_projects_handler
        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
            result = await list_video_projects_handler(ctx)
        assert result.success
        assert "Aucun" in result.output

    @pytest.mark.asyncio
    async def test_finds_remotion_projects(self, ctx, tmp_path):
        from src.reasoning.handlers.remotion import list_video_projects_handler
        proj = tmp_path / "2026-04-10" / "my-video"
        proj.mkdir(parents=True)
        (proj / "render.mjs").write_text("// render")
        (proj / "package.json").write_text(json.dumps({"name": "my-video", "dependencies": {"remotion": "^4.0.0"}}))
        (proj / "output.mp4").write_bytes(b"x" * 1024)
        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path):
            result = await list_video_projects_handler(ctx)
        assert result.success
        assert "my-video" in result.output
        assert "Rendu" in result.output


# ═══════════════════════════════════════════════════════════════
# 9. Tests HandlerDef registration
# ═══════════════════════════════════════════════════════════════

class TestHandlerDefRegistration:
    """Vérifie que les handlers vidéo sont correctement enregistrés."""

    def test_4_handler_defs_returned(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        assert len(defs) == 4

    def test_handler_names(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        names = [d.name for d in defs]
        assert "generate_video" in names
        assert "edit_video" in names
        assert "preview_video" in names
        assert "list_video_projects" in names

    def test_all_have_category_video(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        assert all(d.category == "video" for d in defs)

    def test_all_have_handler_callable(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        assert all(callable(d.handler) for d in defs)

    def test_generate_video_requires_description(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        gen = next(d for d in defs if d.name == "generate_video")
        assert "description" in gen.parameters["required"]

    def test_video_handlers_are_loadable(self):
        from src.reasoning.handlers.remotion import get_video_handler_defs
        defs = get_video_handler_defs()
        assert len(defs) >= 1
        from src.reasoning.handlers.registry_v2 import HandlerDef
        for d in defs:
            assert isinstance(d, HandlerDef)
            assert d.name
            assert d.category == "video"


# ═══════════════════════════════════════════════════════════════
# 10. Tests routage ReAct — _TOOL_COMPLETION_HINTS
# ═══════════════════════════════════════════════════════════════

class TestToolCompletionHints:
    """Vérifie que les hints vidéo sont dans react_config.py."""

    def test_generate_video_in_hints(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        assert "generate_video" in _TOOL_COMPLETION_HINTS
        hints = _TOOL_COMPLETION_HINTS["generate_video"]
        assert "vidéo" in hints or "video" in hints

    def test_edit_video_in_hints(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        assert "edit_video" in _TOOL_COMPLETION_HINTS

    def test_preview_video_in_hints(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        assert "preview_video" in _TOOL_COMPLETION_HINTS

    def test_list_video_projects_in_hints(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        assert "list_video_projects" in _TOOL_COMPLETION_HINTS


# ═══════════════════════════════════════════════════════════════
# 11. Tests heuristique is_video_request (prompt_builder.py)
# ═══════════════════════════════════════════════════════════════

class TestIsVideoRequest:
    """Vérifie la détection d'intent vidéo."""

    def test_explicit_video_creation(self):
        from src.reasoning.prompt_builder import is_video_request
        assert is_video_request("crée une vidéo de présentation")
        assert is_video_request("fais un reel TikTok")
        assert is_video_request("génère une animation de mon produit")
        assert is_video_request("fais une pub vidéo")

    def test_non_video_requests(self):
        from src.reasoning.prompt_builder import is_video_request
        assert not is_video_request("crée un site web")
        assert not is_video_request("cherche des vidéos sur YouTube")
        assert not is_video_request("lis le fichier video.py")
        assert not is_video_request("")

    def test_english_video_requests(self):
        from src.reasoning.prompt_builder import is_video_request
        assert is_video_request("make a short video intro")
        assert is_video_request("build an explainer video")


# ═══════════════════════════════════════════════════════════════
# 12. Tests config schema (web/routes/config.py)
# ═══════════════════════════════════════════════════════════════

class TestConfigSchemaVideo:
    """Vérifie que les entrées config vidéo sont dans _CONFIG_SCHEMA."""

    def test_remotion_license_key_in_schema(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = [e["key"] for e in _CONFIG_SCHEMA]
        assert "REMOTION_LICENSE_KEY" in keys

    def test_video_docker_image_in_schema(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = [e["key"] for e in _CONFIG_SCHEMA]
        assert "LUMENA_VIDEO_DOCKER_IMAGE" in keys

    def test_video_render_timeout_in_schema(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = [e["key"] for e in _CONFIG_SCHEMA]
        assert "LUMENA_VIDEO_RENDER_TIMEOUT" in keys

    def test_remotion_license_is_secret(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "REMOTION_LICENSE_KEY")
        assert entry["type"] == "secret"
        assert entry["group"] == "Vidéo"

    def test_video_gpu_in_schema(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = [e["key"] for e in _CONFIG_SCHEMA]
        assert "LUMENA_VIDEO_GPU" in keys

    def test_video_gpu_is_bool(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_VIDEO_GPU")
        assert entry["type"] == "bool"
        assert entry["group"] == "Vidéo"


# ═══════════════════════════════════════════════════════════════
# 13. Tests utilitaires
# ═══════════════════════════════════════════════════════════════

class TestStripMarkdownFences:
    """Vérifie le nettoyage de code LLM."""

    def test_strips_tsx_fences(self):
        from src.reasoning.handlers.remotion import _strip_markdown_fences
        code = "```tsx\nexport default function A() {}\n```"
        assert _strip_markdown_fences(code) == "export default function A() {}"

    def test_strips_plain_fences(self):
        from src.reasoning.handlers.remotion import _strip_markdown_fences
        code = "```\nsome code\n```"
        assert _strip_markdown_fences(code) == "some code"

    def test_no_fences_unchanged(self):
        from src.reasoning.handlers.remotion import _strip_markdown_fences
        code = "export default function B() {}"
        assert _strip_markdown_fences(code) == code


# ═══════════════════════════════════════════════════════════════
# 14. Tests HANDLER_TIMEOUTS (ops_handlers.py)
# ═══════════════════════════════════════════════════════════════

class TestHandlerTimeouts:
    """Vérifie que generate_video a un timeout dans HANDLER_TIMEOUTS."""

    def test_generate_video_timeout_exists(self):
        from src.autonomy.ops_handlers import HANDLER_TIMEOUTS
        assert "generate_video" in HANDLER_TIMEOUTS
        assert HANDLER_TIMEOUTS["generate_video"] >= 300

    def test_timeout_is_int(self):
        from src.autonomy.ops_handlers import HANDLER_TIMEOUTS
        assert isinstance(HANDLER_TIMEOUTS["generate_video"], int)


# ═══════════════════════════════════════════════════════════════
# 15. Tests panels.js _GROUP_ORDER (vérification statique)
# ═══════════════════════════════════════════════════════════════

class TestGroupOrderUI:
    """Vérifie que le groupe 'Vidéo' est dans _GROUP_ORDER de panels.js."""

    def test_video_group_in_panels_js(self):
        panels_js = Path("web/static/js/panels.js").read_text(encoding="utf-8")
        assert "'Vidéo'" in panels_js or '"Vidéo"' in panels_js, \
            "Groupe 'Vidéo' absent de _GROUP_ORDER dans panels.js"


# ═══════════════════════════════════════════════════════════════
# 16. Tests react.py video_context injection
# ═══════════════════════════════════════════════════════════════

class TestReactVideoContext:
    """Vérifie que la section vidéo est injectable dans le prompt ReAct."""

    def test_video_context_importable(self):
        from src.tools.remotion_engine import VIDEO_TEMPLATES
        assert isinstance(VIDEO_TEMPLATES, dict)
        assert len(VIDEO_TEMPLATES) >= 4

    def test_react_prompt_contains_video_marker(self):
        react_py = Path("src/reasoning/react.py").read_text(encoding="utf-8")
        assert "video_context" in react_py, \
            "Variable video_context absente de react.py _build_react_prompt"


# ═══════════════════════════════════════════════════════════════
# 17. Tests llm.chat() params (signature validation)
# ═══════════════════════════════════════════════════════════════

class TestLlmChatSignature:
    """Vérifie que llm.chat() accepte les params utilisés par generate_video."""

    def test_chat_accepts_temperature_and_max_tokens(self):
        import inspect
        from src.llm.multi_provider import MultiProviderLLM
        sig = inspect.signature(MultiProviderLLM.chat)
        params = set(sig.parameters.keys())
        assert "temperature" in params
        assert "max_tokens" in params
        assert "messages" in params
        assert "stop" in params
