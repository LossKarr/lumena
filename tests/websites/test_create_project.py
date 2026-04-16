"""
test_create_project.py - Tests du handler create_project.

Teste la planification, la génération batch, l'écriture disque,
les validations de sécurité et les cas d'erreur.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.project import (
    create_project_handler,
    get_project_handler_defs,
    _extract_json,
    _sanitize_path,
    _strip_code_fences,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_plan_manager(tmp_path):
    """Redirige plan_manager._PLANS_DIR vers un tmp isolé pour éviter
    toute collision/pollution du vrai data/plans/."""
    plans_tmp = tmp_path / "plans"
    plans_tmp.mkdir()
    with patch("src.tools.plan_manager._PLANS_DIR", plans_tmp), \
         patch("src.tools.plan_manager._ARCHIVES_DIR", plans_tmp / "archives"):
        yield


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def mock_llm():
    """Crée un mock LLM qui retourne des réponses configurables."""
    llm = AsyncMock()
    llm.model_name = "test-model"
    return llm


@pytest.fixture
def ctx_with_llm(ctx, mock_llm):
    """Context avec un LLM mocké."""
    lumena = MagicMock()
    lumena.llm = mock_llm
    ctx.lumena = lumena
    return ctx


# ─── Tests utilitaires ─────────────────────────────────────────────────────

class TestExtractJson:
    def test_direct_json(self):
        result = _extract_json('{"files": []}')
        assert result == {"files": []}

    def test_json_in_code_block(self):
        text = '```json\n{"files": [{"path": "a.txt"}]}\n```'
        result = _extract_json(text)
        assert result["files"][0]["path"] == "a.txt"

    def test_json_with_surrounding_text(self):
        text = 'Voici le plan:\n{"project_name": "test", "files": []}\nBonne chance!'
        result = _extract_json(text)
        assert result["project_name"] == "test"

    def test_invalid_json(self):
        assert _extract_json("pas du json du tout") is None

    def test_empty_string(self):
        assert _extract_json("") is None

    def test_nested_braces(self):
        text = '{"a": {"b": 1}, "c": [{"d": 2}]}'
        result = _extract_json(text)
        assert result["a"]["b"] == 1


class TestSanitizePath:
    def test_valid_path(self):
        assert _sanitize_path("src/index.html") == "src/index.html"

    def test_traversal_blocked(self):
        assert _sanitize_path("../../etc/passwd") is None

    def test_absolute_blocked(self):
        assert _sanitize_path("/usr/bin/evil") is None

    def test_drive_letter_blocked(self):
        assert _sanitize_path("C:\\Users\\file.txt") is None

    def test_backslash_conversion(self):
        assert _sanitize_path("src\\styles\\main.css") == "src/styles/main.css"

    def test_too_deep_blocked(self):
        deep = "/".join(["a"] * 12)
        assert _sanitize_path(deep) is None

    def test_gitignore_allowed(self):
        assert _sanitize_path(".gitignore") == ".gitignore"

    def test_hidden_dir_blocked(self):
        assert _sanitize_path(".secret/data.txt") is None


class TestStripCodeFences:
    def test_with_fences(self):
        text = "```html\n<h1>Hello</h1>\n```"
        assert _strip_code_fences(text) == "<h1>Hello</h1>"

    def test_without_fences(self):
        text = "<h1>Hello</h1>"
        assert _strip_code_fences(text) == "<h1>Hello</h1>"

    def test_only_opening_fence(self):
        text = "```python\nprint('hello')"
        assert _strip_code_fences(text) == text.strip()


# ─── Tests du handler principal ────────────────────────────────────────────

class TestCreateProjectHandler:
    @pytest.fixture(autouse=True)
    def _force_batch_pipeline(self, monkeypatch):
        """Force le pipeline batch (désactive CodeAgent) pour tester le batch."""
        monkeypatch.setattr(
            "src.reasoning.handlers.project._CODEAGENT_AVAILABLE", False,
        )

    @pytest.mark.asyncio
    async def test_no_llm_fails(self, ctx):
        """Sans LLM, retourne une erreur."""
        r = await create_project_handler(ctx, description="un site web")
        assert not r.success
        assert "LLM non disponible" in r.output

    @pytest.mark.asyncio
    async def test_invalid_plan_fails(self, ctx_with_llm, mock_llm):
        """Si le LLM retourne un plan invalide."""
        mock_llm.chat.return_value = "Je ne sais pas quoi faire"
        r = await create_project_handler(ctx_with_llm, description="un site web")
        assert not r.success
        assert "Plan invalide" in r.output

    @pytest.mark.asyncio
    async def test_empty_plan_fails(self, ctx_with_llm, mock_llm):
        """Plan avec liste de fichiers vide."""
        mock_llm.chat.return_value = json.dumps({"project_name": "test", "files": []})
        r = await create_project_handler(ctx_with_llm, description="un site web")
        assert not r.success
        assert "aucun fichier" in r.output.lower()

    @pytest.mark.asyncio
    async def test_successful_project_creation(self, ctx_with_llm, mock_llm, tmp_path):
        """Cas nominal : crée un projet avec 3 fichiers."""
        plan = json.dumps({
            "project_name": "mon-site",
            "files": [
                {"path": "index.html", "description": "Page principale", "language": "html"},
                {"path": "styles/main.css", "description": "Styles", "language": "css"},
                {"path": "scripts/app.js", "description": "Script principal", "language": "javascript"},
            ],
        })

        call_count = 0

        async def mock_chat(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return plan
            # Génération de contenu selon le fichier
            user_msg = messages[-1]["content"]
            if "index.html" in user_msg:
                return "<html><head><title>Mon Site</title></head><body><h1>Hello</h1></body></html>"
            elif "main.css" in user_msg:
                return "body { margin: 0; font-family: sans-serif; }"
            elif "app.js" in user_msg:
                return 'console.log("Hello World");'
            return "// contenu"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(ctx_with_llm, description="un site web avec 3 fichiers", output_dir=".")
        assert r.success
        assert "3/3" in r.output
        assert "mon-site" in r.output

        # Vérifier que les fichiers existent
        workspace = ctx_with_llm.runtime_root
        assert (workspace / "mon-site" / "index.html").exists()
        assert (workspace / "mon-site" / "styles" / "main.css").exists()
        assert (workspace / "mon-site" / "scripts" / "app.js").exists()

        # Vérifier le contenu
        html = (workspace / "mon-site" / "index.html").read_text(encoding="utf-8")
        assert "<h1>Hello</h1>" in html

    @pytest.mark.asyncio
    async def test_custom_project_name(self, ctx_with_llm, mock_llm):
        """Le project_name passé en paramètre prend la priorité."""
        plan = json.dumps({
            "project_name": "llm-name",
            "files": [{"path": "readme.md", "description": "Readme", "language": "markdown"}],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "# Mon Projet"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(
            ctx_with_llm,
            description="un projet",
            project_name="custom-name",
            output_dir=".",
        )
        assert r.success
        assert (ctx_with_llm.runtime_root / "custom-name" / "readme.md").exists()

    @pytest.mark.asyncio
    async def test_custom_output_dir(self, ctx_with_llm, mock_llm, tmp_path):
        """output_dir personnalisé."""
        out_dir = tmp_path / "my_output"
        plan = json.dumps({
            "project_name": "test",
            "files": [{"path": "file.txt", "description": "Test", "language": "text"}],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "Hello World"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(
            ctx_with_llm,
            description="test",
            output_dir=str(out_dir),
        )
        assert r.success
        assert (out_dir / "test" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_output_dir_workspace_prefix_no_double_workspace(self, ctx_with_llm, mock_llm):
        """output_dir='workspace/...' ne doit jamais créer workspace/workspace/..."""
        plan = json.dumps({
            "project_name": "test",
            "files": [{"path": "file.txt", "description": "Test", "language": "text"}],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "Hello World"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(
            ctx_with_llm,
            description="test",
            output_dir="workspace/2026-03-14",
        )
        assert r.success
        assert (ctx_with_llm.runtime_root / "2026-03-14" / "test" / "file.txt").exists()
        assert not (ctx_with_llm.runtime_root / "workspace" / "2026-03-14" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_output_dir_relative_stays_in_runtime_root(self, ctx_with_llm, mock_llm):
        """output_dir relatif doit rester dans runtime_root, pas basculer sur lumena_root."""
        plan = json.dumps({
            "project_name": "test",
            "files": [{"path": "file.txt", "description": "Test", "language": "text"}],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "Hello World"

        mock_llm.chat.side_effect = mock_chat

        rel_out = "2026-03-14/logo_masterpiece"
        r = await create_project_handler(
            ctx_with_llm,
            description="test",
            output_dir=rel_out,
        )
        assert r.success
        assert (ctx_with_llm.runtime_root / rel_out / "test" / "file.txt").exists()
        assert not (ctx_with_llm.lumena_root / rel_out / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_traversal_paths_filtered(self, ctx_with_llm, mock_llm):
        """Les chemins avec .. sont filtrés du plan."""
        plan = json.dumps({
            "project_name": "evil",
            "files": [
                {"path": "../../etc/passwd", "description": "Exploit", "language": "text"},
                {"path": "safe.txt", "description": "OK", "language": "text"},
            ],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "Safe content"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(ctx_with_llm, description="test")
        assert r.success
        assert "1/1" in r.output  # Seul safe.txt

    @pytest.mark.asyncio
    async def test_llm_error_on_file_generation(self, ctx_with_llm, mock_llm):
        """Erreur LLM pendant la génération d'un fichier."""
        plan = json.dumps({
            "project_name": "partial",
            "files": [
                {"path": "ok.txt", "description": "OK", "language": "text"},
                {"path": "fail.txt", "description": "Fail", "language": "text"},
            ],
        })

        call_count = 0

        async def mock_chat(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return plan
            if call_count == 2:
                return "Content OK"
            raise ConnectionError("LLM down")

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(ctx_with_llm, description="test")
        # Au moins un fichier a réussi
        assert "1/2" in r.output or "erreur" in r.output.lower()

    @pytest.mark.asyncio
    async def test_max_files_limit(self, ctx_with_llm, mock_llm):
        """Le plan est tronqué si plus de MAX_FILES fichiers."""
        files = [
            {"path": f"file_{i}.txt", "description": f"File {i}", "language": "text"}
            for i in range(150)
        ]
        plan = json.dumps({"project_name": "huge", "files": files})

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return "content"

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(ctx_with_llm, description="projet énorme")
        assert r.success
        assert "150/150" in r.output  # Limité à _MAX_FILES (200)

    @pytest.mark.asyncio
    async def test_code_fences_stripped(self, ctx_with_llm, mock_llm):
        """Les triple backticks sont retirés du contenu généré."""
        plan = json.dumps({
            "project_name": "fenced",
            "files": [{"path": "main.py", "description": "Script", "language": "python"}],
        })

        async def mock_chat(messages, **kwargs):
            if "architecte" in messages[0]["content"]:
                return plan
            return '```python\nprint("hello")\n```'

        mock_llm.chat.side_effect = mock_chat

        r = await create_project_handler(ctx_with_llm, description="test", output_dir=".")
        assert r.success
        content = (ctx_with_llm.runtime_root / "fenced" / "main.py").read_text()
        assert content == 'print("hello")'
        assert "```" not in content


# ─── Tests registration ───────────────────────────────────────────────────

class TestProjectHandlerDefs:
    def test_defs_valid(self):
        defs = get_project_handler_defs()
        assert len(defs) >= 7
        d = next(x for x in defs if x.name == "create_project")
        assert d.category == "project"
        assert d.handler is create_project_handler
        assert "description" in d.parameters["required"]

    def test_defs_have_all_params(self):
        defs = get_project_handler_defs()
        d = next(x for x in defs if x.name == "create_project")
        props = d.parameters["properties"]
        assert "description" in props
        assert "project_name" in props
        assert "output_dir" in props
