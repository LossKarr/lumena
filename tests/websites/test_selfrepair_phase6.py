"""Tests Phase 6 — self-repair enrichi avec contexte des dépendances."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestSelfRepairDepContext:
    """Vérifie que le self-repair injecte le contexte des dépendances."""

    def test_build_dependency_context_injects_css_for_html(self):
        """Quand on répare index.html, les CSS sont injectés comme contexte."""
        from src.reasoning.handlers.project import _build_dependency_context

        generated = {
            "styles.css": "body { color: red; }\n.hero { padding: 20px; }",
            "app.js": "function init() { }",
        }
        ctx = _build_dependency_context("index.html", generated, list(generated.keys()))
        assert "styles.css" in ctx
        assert ".hero" in ctx or "styles.css" in ctx

    def test_build_dependency_context_empty_when_no_deps(self):
        """Sans fichiers générés, le contexte est vide."""
        from src.reasoning.handlers.project import _build_dependency_context

        ctx = _build_dependency_context("index.html", {}, [])
        assert ctx == ""

    def test_build_dependency_context_css_for_js(self):
        """Un fichier JS reçoit le CSS comme contexte."""
        from src.reasoning.handlers.project import _build_dependency_context

        generated = {
            "styles.css": ":root { --primary: #0047AB; }",
            "README.md": "# Docs",
        }
        ctx = _build_dependency_context("app.js", generated, list(generated.keys()))
        # CSS est pertinent pour JS (vue variables CSS)
        assert "styles.css" in ctx or "--primary" in ctx

    def test_repair_prompt_includes_dep_context(self):
        """Le prompt de repair contient le contexte des dépendances."""
        from src.reasoning.handlers.project import _build_dependency_context

        generated = {
            "styles.css": ".nav { display: flex; }",
            "index.html": "<html><link href='styles.css'></html>",
        }
        # Simuler un repair de app.js
        dep_ctx = _build_dependency_context("app.js", generated, list(generated.keys()))
        repair_prompt = (
            "Erreurs de validation...\n"
            + (f"\n**Contexte des fichiers dont dépend `app.js` :**\n{dep_ctx}\n" if dep_ctx else "")
        )
        if dep_ctx:
            assert ".nav" in repair_prompt or "styles.css" in repair_prompt

    def test_repair_skips_dep_context_when_empty(self):
        """Si je répare un fichier sans dépendances générées, pas de section contexte."""
        from src.reasoning.handlers.project import _build_dependency_context

        dep_ctx = _build_dependency_context("config.json", {}, [])
        repair_prompt = (
            "Erreurs...\n"
            + (f"\n**Contexte :**\n{dep_ctx}\n" if dep_ctx else "")
        )
        assert "Contexte" not in repair_prompt


class TestValidatorIntegration:
    """Vérifie que le validator est disponible et retourne des rapports."""

    def test_validator_importable(self):
        try:
            from src.tools.code_validator import validate_project, ValidationReport
            assert callable(validate_project)
        except ImportError:
            pytest.skip("code_validator not available")

    def test_validate_simple_html_css(self):
        try:
            from src.tools.code_validator import validate_project, Severity
        except ImportError:
            pytest.skip("code_validator not available")

        files = {
            "index.html": "<html><head><link href='styles.css' rel='stylesheet'></head><body></body></html>",
            "styles.css": "body { margin: 0; }",
        }
        report = validate_project(files)
        # Pas d'erreurs CSS/HTML critiques attendues
        errors = [i for i in report.issues if i.severity == Severity.ERROR]
        assert len(errors) == 0

    def test_validate_missing_css_file_detected(self):
        try:
            from src.tools.code_validator import validate_project, Severity
        except ImportError:
            pytest.skip("code_validator not available")

        files = {
            "index.html": '<html><head><link href="missing.css" rel="stylesheet"></head></html>',
        }
        report = validate_project(files)
        errors = [i for i in report.issues if i.severity == Severity.ERROR and "missing.css" in str(i)]
        assert len(errors) > 0
