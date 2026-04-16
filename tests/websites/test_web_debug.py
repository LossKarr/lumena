"""
Tests pour les 3 améliorations de debug web de Lumena :
1. Validation type="module" dans code_validator
2. Outil check_web_project
3. Détecteur de stagnation post-édition (variables react.py)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile
import os


# ═══════════════════════════════════════════════════════════════
# 1. Tests validation type="module" dans code_validator
# ═══════════════════════════════════════════════════════════════

class TestModuleTypeValidation:
    """Vérifie que _validate_cross_file détecte les <script> sans type=module."""

    def _get_validate(self):
        from src.tools.code_validator import _validate_cross_file
        return _validate_cross_file

    def test_script_missing_type_module_detected(self):
        """JS avec import/export mais <script> sans type=module → ERROR."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="js/main.js" defer></script></body></html>',
            "js/main.js": 'import { foo } from "./utils.js";\nconsole.log(foo);',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" in codes

    def test_script_with_type_module_ok(self):
        """JS avec import et <script type=module> → pas d'erreur."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="js/main.js" type="module" defer></script></body></html>',
            "js/main.js": 'import { foo } from "./utils.js";\nconsole.log(foo);',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" not in codes

    def test_script_no_import_no_export_ok(self):
        """JS sans import/export et <script> normal → pas d'erreur."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="js/app.js" defer></script></body></html>',
            "js/app.js": 'document.addEventListener("click", function() { alert("ok"); });',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" not in codes

    def test_script_unnecessary_module_type_warning(self):
        """JS sans import/export mais type=module → WARNING."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="js/app.js" type="module"></script></body></html>',
            "js/app.js": 'console.log("hello");',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_UNNECESSARY_MODULE_TYPE" in codes

    def test_export_default_detected(self):
        """export default doit aussi être détecté comme usage ES6."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="carousel.js"></script></body></html>',
            "carousel.js": 'export default function initCarousel() {}',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" in codes

    def test_export_named_detected(self):
        """export { ... } doit être détecté."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="utils.js"></script></body></html>',
            "utils.js": 'function debounce(fn) { return fn; }\nexport { debounce };',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" in codes

    def test_import_in_comment_not_flagged(self):
        """import dans un commentaire JS ne doit pas compter."""
        validate = self._get_validate()
        files = {
            "index.html": '<html><body><script src="app.js"></script></body></html>',
            "app.js": '// import { foo } from "bar";\nconsole.log("ok");',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        assert "HTML_SCRIPT_MISSING_MODULE_TYPE" not in codes

    def test_multiple_scripts_mixed(self):
        """Plusieurs scripts : certains module, d'autres non — erreur ciblée."""
        validate = self._get_validate()
        files = {
            "index.html": (
                '<html><body>'
                '<script src="js/router.js" type="module" defer></script>'
                '<script src="js/main.js" defer></script>'
                '<script src="js/faq.js" defer></script>'
                '</body></html>'
            ),
            "js/router.js": 'export function navigateTo() {}',
            "js/main.js": 'import { navigateTo } from "./router.js";\nnavigTo();',
            "js/faq.js": 'document.querySelector(".faq").addEventListener("click", function() {});',
        }
        issues = validate(files)
        codes = [i.code for i in issues]
        # main.js doit être flaggé, pas faq.js ni router.js
        module_issues = [i for i in issues if i.code == "HTML_SCRIPT_MISSING_MODULE_TYPE"]
        assert len(module_issues) == 1
        assert "main.js" in module_issues[0].message


# ═══════════════════════════════════════════════════════════════
# 2. Tests outil check_web_project
# ═══════════════════════════════════════════════════════════════

class TestCheckWebProject:
    """Vérifie que check_web_project_handler fonctionne."""

    @pytest.fixture
    def ctx(self, tmp_path):
        ctx = MagicMock()
        ctx.lumena_root = str(tmp_path)
        return ctx

    def _write_project(self, tmp_path, files: dict):
        project = tmp_path / "workspace" / "test-project"
        project.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            fp = project / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
        return project

    @pytest.mark.asyncio
    async def test_basic_clean_project(self, ctx, tmp_path):
        from src.reasoning.handlers.website import check_web_project_handler
        project = self._write_project(tmp_path, {
            "index.html": '<html><body><script src="app.js" defer></script></body></html>',
            "app.js": 'console.log("hello");',
        })
        result = await check_web_project_handler(ctx, project_dir=str(project))
        assert result.success
        assert "check_web_project" in result.output

    @pytest.mark.asyncio
    async def test_detects_missing_module_type(self, ctx, tmp_path):
        from src.reasoning.handlers.website import check_web_project_handler
        project = self._write_project(tmp_path, {
            "index.html": '<html><body><script src="main.js"></script></body></html>',
            "main.js": 'import { foo } from "./utils.js";\nconsole.log(foo);',
        })
        result = await check_web_project_handler(ctx, project_dir=str(project))
        assert result.success
        assert "MISSING_MODULE_TYPE" in result.output or "type=\"module\"" in result.output

    @pytest.mark.asyncio
    async def test_detects_missing_file(self, ctx, tmp_path):
        from src.reasoning.handlers.website import check_web_project_handler
        project = self._write_project(tmp_path, {
            "index.html": '<html><body><script src="missing.js"></script></body></html>',
        })
        result = await check_web_project_handler(ctx, project_dir=str(project))
        assert result.success
        assert "missing.js" in result.output

    @pytest.mark.asyncio
    async def test_no_project_found(self, ctx, tmp_path):
        from src.reasoning.handlers.website import check_web_project_handler
        result = await check_web_project_handler(ctx, project_dir=str(tmp_path / "nope"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_auto_detect_last_project(self, ctx, tmp_path):
        from src.reasoning.handlers.website import check_web_project_handler
        project = self._write_project(tmp_path, {
            "index.html": '<html><body>Hello</body></html>',
        })
        # Sans project_dir, doit trouver le dernier index.html dans workspace/
        with patch("src.utils.paths.WORKSPACE_DIR", tmp_path / "workspace"):
            result = await check_web_project_handler(ctx, project_dir="")
        assert result.success


# ═══════════════════════════════════════════════════════════════
# 3. Tests HandlerDef registration
# ═══════════════════════════════════════════════════════════════

class TestHandlerDefRegistration:
    """Vérifie que check_web_project est bien dans les HandlerDef."""

    def test_check_web_project_in_handler_defs(self):
        from src.reasoning.handlers.website import get_website_handler_defs
        defs = get_website_handler_defs()
        names = [d.name for d in defs]
        assert "check_web_project" in names

    def test_check_web_project_handler_def_has_category(self):
        from src.reasoning.handlers.website import get_website_handler_defs
        defs = get_website_handler_defs()
        cwp = next(d for d in defs if d.name == "check_web_project")
        assert cwp.category == "website"
        assert cwp.handler is not None


# ═══════════════════════════════════════════════════════════════
# 4. Tests variables stagnation post-édition react.py
# ═══════════════════════════════════════════════════════════════

class TestPostEditStagnationVars:
    """Vérifie que les variables de stagnation sont déclarées dans react.py."""

    def test_post_edit_vars_exist_in_source(self):
        """Les variables _post_edit_read_streak et _has_done_edits sont dans react.py."""
        import inspect
        from src.reasoning.react import ReActLoop
        source = inspect.getsource(ReActLoop._run_internal)
        assert "_post_edit_read_streak" in source
        assert "_has_done_edits" in source
        assert "_write_tools" in source
        assert "_read_only_tools" in source

    def test_write_tools_set_defined(self):
        """Les frozensets _write_tools et _read_only_tools contiennent les bons outils."""
        import inspect
        from src.reasoning.react import ReActLoop
        source = inspect.getsource(ReActLoop._run_internal)
        assert "write_file" in source
        assert "check_web_project" in source
        assert "forced_final_post_edit_stagnation" in source
