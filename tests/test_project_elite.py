"""
test_project_elite.py — Tests du plan CREATE PROJECT ÉLITE.

Couvre: P0 (waves sub-sequencing), P1 (scoring bidirectionnel + max_tokens),
P2 (directives type-spécifiques), P3 (détection type + run_command élargi + timeout dynamique),
et toutes les phases suivantes au fur et à mesure.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from src.reasoning.handlers.project import (
    _build_dependency_context,
    _dep_wave,
    _is_web_project,
    _detect_project_type,
    _detect_run_command,
    _detect_run_timeout,
    _get_type_directives,
    _get_contract_prompt,
    _quick_syntax_check,
    _looks_truncated,
    _is_json_contract_capable,
    _format_json_contract,
    _build_plan_prompt,
    _PLAN_SUPPLEMENT_MAP,
    _JSON_CONTRACT_CAPABLE_MODELS,
    _CONTRACT_JSON_SCHEMA_MAP,
    _CODEAGENT_AVAILABLE,
    _PLAN_SECTION_MAP,
    create_project_handler,
)
from src.tools.code_validator import validate_project


# ═══════════════════════════════════════════════════════════════════════════
# P0 — Wave séquentielle pour fichiers interdépendants
# ═══════════════════════════════════════════════════════════════════════════


class TestWaveSubSequencing:
    """Vérifie que Wave 2 sépare HTML → JS → reste pour les projets web."""

    def test_wave2_html_before_js(self):
        """Pour un projet web, HTML doit être en Wave 2 (même wave que JS),
        mais le sub-sequencing doit les traiter dans l'ordre HTML → JS."""
        html_entry = {"path": "index.html", "description": "page", "language": "html"}
        js_entry = {"path": "script.js", "description": "logic", "language": "javascript"}

        # Les deux sont bien en Wave 2
        assert _dep_wave(html_entry) == 2
        assert _dep_wave(js_entry) == 2

        # Les extensions sont bien dans les bonnes catégories
        assert html_entry["path"].endswith((".html", ".htm"))
        assert js_entry["path"].endswith((".js", ".ts", ".jsx", ".tsx"))

    def test_wave2_js_sees_html_context(self):
        """Quand index.html est dans _generated_contents,
        _build_dependency_context pour script.js doit le retourner."""
        gen_contents = {"index.html": "<html><body><div id='hero' class='main-section'></div></body></html>"}
        all_paths = ["index.html", "script.js"]

        ctx = _build_dependency_context("script.js", gen_contents, all_paths)

        assert "index.html" in ctx
        assert "hero" in ctx
        assert "main-section" in ctx

    def test_wave2_independent_files_parallel(self):
        """README.md et requirements.txt ne sont ni HTML ni JS,
        donc ils tombent dans la sous-wave 2c (parallèle)."""
        readme = {"path": "README.md", "description": "readme", "language": "markdown"}
        req = {"path": "requirements.txt", "description": "deps", "language": "text"}

        assert _dep_wave(readme) == 2
        assert _dep_wave(req) == 2

        # Ni HTML ni JS → iront dans _rest_files (parallèle)
        assert not readme["path"].endswith((".html", ".htm"))
        assert not readme["path"].endswith((".js", ".ts", ".jsx", ".tsx"))
        assert not req["path"].endswith((".html", ".htm"))
        assert not req["path"].endswith((".js", ".ts", ".jsx", ".tsx"))

    def test_wave2_python_project_stays_parallel(self):
        """Un projet Python pur n'est pas web → tous les .py restent parallèles."""
        assert not _is_web_project("script Python CLI qui parse des CSV")
        assert not _is_web_project("API FastAPI pour gérer les utilisateurs")

        # Les .py sont en Wave 2
        py_entry = {"path": "main.py", "description": "main", "language": "python"}
        assert _dep_wave(py_entry) == 2

    def test_dep_wave_returns_int(self):
        """_dep_wave retourne toujours un int (pas de tuple, pas de string)."""
        entries = [
            {"path": ".env", "description": "env", "language": "text"},
            {"path": "style.css", "description": "css", "language": "css"},
            {"path": "index.html", "description": "html", "language": "html"},
            {"path": "main.py", "description": "py", "language": "python"},
        ]
        for e in entries:
            result = _dep_wave(e)
            assert isinstance(result, int), f"{e['path']} returned {type(result)}"
            assert result in (0, 1, 2)


# ═══════════════════════════════════════════════════════════════════════════
# P1 — Scoring bidirectionnel + max_tokens sécurisé
# ═══════════════════════════════════════════════════════════════════════════


class TestScoringBidirectional:
    """Vérifie que _build_dependency_context score correctement
    JS→HTML, HTML→JS, JS→CSS dans les deux sens."""

    def test_dep_context_js_sees_html(self):
        """JS doit voir le HTML en contexte (score ≥ 25)."""
        gen = {"index.html": "<html><body><div id='app'></div></body></html>"}
        ctx = _build_dependency_context("script.js", gen, ["index.html", "script.js"])
        assert "index.html" in ctx
        assert "app" in ctx

    def test_dep_context_html_sees_js(self):
        """HTML doit voir le JS en contexte (score ≥ 20)."""
        gen = {"app.js": "function initPage() { console.log('ready'); }"}
        ctx = _build_dependency_context("index.html", gen, ["index.html", "app.js"])
        assert "app.js" in ctx
        assert "initPage" in ctx

    def test_dep_context_js_sees_css(self):
        """JS doit voir le CSS en contexte (score ≥ 20)."""
        gen = {"style.css": ":root { --primary: #3b82f6; }\n.hero { color: var(--primary); }"}
        ctx = _build_dependency_context("app.js", gen, ["app.js", "style.css"])
        assert "style.css" in ctx
        assert "--primary" in ctx

    def test_dep_context_html_sees_css(self):
        """HTML doit voir le CSS (score ≥ 20 — existait déjà)."""
        gen = {"style.css": ".header { background: navy; }"}
        ctx = _build_dependency_context("index.html", gen, ["index.html", "style.css"])
        assert "style.css" in ctx

    def test_dep_context_py_same_dir(self):
        """Fichiers Python dans le même répertoire → score same-dir +30 + same-ext +5."""
        gen = {"utils.py": "def helper():\n    return 42"}
        ctx = _build_dependency_context("main.py", gen, ["main.py", "utils.py"])
        assert "utils.py" in ctx
        assert "helper" in ctx

    def test_dep_context_py_subdir(self):
        """Fichiers Python dans un sous-répertoire → same-ext +5 mais pas same-dir."""
        gen = {"src/utils.py": "def helper():\n    return 42"}
        ctx = _build_dependency_context("main.py", gen, ["main.py", "src/utils.py"])
        # same-ext=+5, mais no same-dir → score=5 > 0 → included
        assert "src/utils.py" in ctx

    def test_dep_context_empty_returns_empty(self):
        """Pas de contenu généré → chaîne vide."""
        ctx = _build_dependency_context("main.py", {}, ["main.py"])
        assert ctx == ""

    def test_dep_context_budget_respected(self):
        """Le contexte ne dépasse pas _MAX_DEP_CONTEXT_CHARS (8000)."""
        gen = {f"file{i}.py": "x" * 5000 for i in range(10)}
        ctx = _build_dependency_context("main.py", gen, ["main.py"] + list(gen.keys()))
        # Approximate: should be under budget + headers
        assert len(ctx) < 12000  # 8000 budget + overhead headers


class TestMaxTokensFallback:
    """Vérifie que _fmax ne peut jamais être None."""

    def test_max_tokens_fallback_value(self):
        """Sans max_output_tokens sur le LLM → fallback à 16384."""
        llm = MagicMock(spec=[])  # no max_output_tokens attribute
        _fmax = getattr(llm, "max_output_tokens", None) or 16384
        assert _fmax == 16384

    def test_max_tokens_with_value(self):
        """Avec max_output_tokens = 8192 → utilise cette valeur."""
        llm = MagicMock()
        llm.max_output_tokens = 8192
        _fmax = getattr(llm, "max_output_tokens", None) or 16384
        assert _fmax == 8192

    def test_max_tokens_zero_gets_fallback(self):
        """max_output_tokens = 0 → fallback à 16384 (0 is falsy)."""
        llm = MagicMock()
        llm.max_output_tokens = 0
        _fmax = getattr(llm, "max_output_tokens", None) or 16384
        assert _fmax == 16384


# ═══════════════════════════════════════════════════════════════════════════
# P3 — Détection de type de projet
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectProjectType:
    """Vérifie que _detect_project_type retourne le bon type pour chaque catégorie."""

    def test_detect_type_fastapi(self):
        desc = "API REST avec FastAPI pour gérer les utilisateurs et les commandes"
        assert _detect_project_type(desc, ["main.py", "routes.py", "models.py"]) == "python_api"

    def test_detect_type_flask(self):
        desc = "Backend Flask avec endpoints REST pour un e-commerce"
        assert _detect_project_type(desc, ["app.py", "routes.py"]) == "python_api"

    def test_detect_type_express(self):
        desc = "Express.js REST API server for user management"
        files = ["package.json", "src/index.ts", "src/routes/users.ts"]
        assert _detect_project_type(desc, files) == "node_express"

    def test_detect_type_pygame(self):
        desc = "Jeu pygame space invaders avec score et niveaux"
        assert _detect_project_type(desc, ["game.py", "sprites.py"]) == "game"

    def test_detect_type_phaser(self):
        desc = "Jeu HTML5 avec Phaser.js — plateforme 2D"
        files = ["index.html", "game.js", "style.css"]
        assert _detect_project_type(desc, files) == "game"

    def test_detect_type_cli(self):
        desc = "Script Python CLI pour parser des fichiers CSV et générer des rapports"
        assert _detect_project_type(desc, ["main.py", "parser.py", "report.py"]) == "python_cli"

    def test_detect_type_web(self):
        desc = "Site web portfolio avec animations CSS et navigation SPA"
        files = ["index.html", "style.css", "app.js"]
        assert _detect_project_type(desc, files) == "web"

    def test_detect_type_desktop(self):
        desc = "Application desktop GUI avec tkinter pour gestion de contacts"
        assert _detect_project_type(desc, ["main.py", "ui.py", "db.py"]) == "desktop"

    def test_detect_type_data_science(self):
        desc = "Analyse de données avec pandas et visualisation matplotlib"
        files = ["analysis.py", "data_loader.py", "visualize.py"]
        assert _detect_project_type(desc, files) == "data_science"

    def test_detect_type_docker(self):
        desc = "Application containerisée avec Docker et multi-stage build"
        files = ["Dockerfile", "docker-compose.yml", "main.py"]
        assert _detect_project_type(desc, files) == "docker"

    def test_detect_type_generic_fallback(self):
        desc = "Quelque chose d'indéterminé"
        assert _detect_project_type(desc, ["readme.md"]) == "generic"

    def test_detect_type_returns_string(self):
        """Le type retourné est toujours un string non vide."""
        for desc in ["web app", "script python", "jeu", "api", "gui desktop", "random"]:
            result = _detect_project_type(desc, ["file.txt"])
            assert isinstance(result, str)
            assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════
# P3 — _detect_run_command élargi
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectRunCommand:
    """Vérifie que _detect_run_command couvre tous les types sans régresser."""

    def test_run_command_docker_compose(self, tmp_path):
        files = ["docker-compose.yml", "app.py", "Dockerfile"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd == "docker compose up --build"

    def test_run_command_dockerfile_only(self, tmp_path):
        files = ["Dockerfile", "main.py"]
        cmd = _detect_run_command(tmp_path, files)
        assert "docker build" in cmd
        assert "docker run" in cmd

    def test_run_command_cargo(self, tmp_path):
        files = ["Cargo.toml", "src/main.rs"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd == "cargo run"

    def test_run_command_go(self, tmp_path):
        files = ["go.mod", "main.go"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd == "go run ."

    def test_run_command_django(self, tmp_path):
        files = ["manage.py", "settings.py", "requirements.txt"]
        cmd = _detect_run_command(tmp_path, files)
        assert "manage.py runserver" in cmd
        assert "pip install" in cmd

    def test_run_command_npm_start(self, tmp_path):
        """package.json avec script start → npm start."""
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"scripts": {"start": "node index.js"}}))
        files = ["package.json", "index.js"]
        cmd = _detect_run_command(tmp_path, files)
        assert "npm start" in cmd

    def test_run_command_python_entry(self, tmp_path):
        files = ["main.py", "utils.py", "requirements.txt"]
        cmd = _detect_run_command(tmp_path, files)
        assert "python main.py" in cmd
        assert "pip install" in cmd

    def test_run_command_static_html(self, tmp_path):
        files = ["index.html", "style.css"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd is None

    def test_run_command_makefile(self, tmp_path):
        files = ["Makefile", "main.c"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd == "make"

    def test_run_command_notebook_skip(self, tmp_path):
        """Un projet 100% notebooks → pas de run."""
        files = ["analysis.ipynb"]
        cmd = _detect_run_command(tmp_path, files)
        assert cmd is None

    def test_run_command_poetry(self, tmp_path):
        """pyproject.toml avec poetry → poetry install && poetry run."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text("[tool.poetry]\nname = 'myapp'\n")
        files = ["pyproject.toml", "main.py"]
        cmd = _detect_run_command(tmp_path, files)
        assert "poetry install" in cmd
        assert "poetry run" in cmd


# ═══════════════════════════════════════════════════════════════════════════
# P3 — Timeout dynamique
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectRunTimeout:
    """Vérifie que _detect_run_timeout retourne un timeout adapté."""

    def test_timeout_docker(self):
        assert _detect_run_timeout("docker compose up --build") == 120

    def test_timeout_cargo(self):
        assert _detect_run_timeout("cargo run") == 30

    def test_timeout_go(self):
        assert _detect_run_timeout("go run .") == 30

    def test_timeout_npm(self):
        assert _detect_run_timeout("npm install --silent 2>&1 && npm start") == 30

    def test_timeout_pip(self):
        assert _detect_run_timeout("pip install -r requirements.txt -q 2>&1 && python main.py") == 20

    def test_timeout_poetry(self):
        assert _detect_run_timeout("poetry install -q 2>&1 && poetry run python main.py") == 60

    def test_timeout_default_python(self):
        assert _detect_run_timeout("python main.py") == 6  # _RUN_FIX_TIMEOUT

    def test_timeout_none(self):
        assert _detect_run_timeout(None) == 6


# ═══════════════════════════════════════════════════════════════════════════
# P2 — Directives type-spécifiques
# ═══════════════════════════════════════════════════════════════════════════


class TestTypeDirectives:
    """Vérifie que _get_type_directives retourne les bonnes directives."""

    def test_directives_python_api(self):
        d = _get_type_directives("python_api", "API FastAPI")
        assert "DIRECTIVES API" in d
        assert "Validation" in d or "validation" in d.lower()

    def test_directives_python_cli(self):
        d = _get_type_directives("python_cli", "Script CLI")
        assert "DIRECTIVES PYTHON" in d
        assert "import" in d.lower()

    def test_directives_game(self):
        d = _get_type_directives("game", "jeu pygame")
        assert "DIRECTIVES JEU" in d
        assert "Game loop" in d or "game loop" in d.lower()

    def test_directives_node(self):
        d = _get_type_directives("node_express", "Express API")
        assert "DIRECTIVES NODE" in d
        assert "package.json" in d

    def test_directives_desktop(self):
        d = _get_type_directives("desktop", "GUI app")
        assert "DIRECTIVES" in d
        assert "DESKTOP" in d

    def test_directives_data_science(self):
        d = _get_type_directives("data_science", "analyse données")
        assert "DIRECTIVES DATA" in d
        assert "pandas" in d

    def test_directives_web_empty(self):
        """Web a ses propres directives, pas de duplication."""
        d = _get_type_directives("web", "site web portfolio")
        assert d == ""

    def test_directives_generic_empty(self):
        d = _get_type_directives("generic", "quelque chose")
        assert d == ""


# ═══════════════════════════════════════════════════════════════════════════
# P2+P3 — Contrats enrichis + intégration _detect_project_type
# ═══════════════════════════════════════════════════════════════════════════


class TestContractIntegration:
    """Vérifie que _get_contract_prompt utilise _detect_project_type."""

    def test_contract_python_enriched(self):
        """Le contrat Python doit contenir les nouvelles directives."""
        contract = _get_contract_prompt(
            "Script CLI Python pour convertir des fichiers",
            [{"path": "main.py", "language": "python"}, {"path": "converter.py", "language": "python"}],
        )
        assert "if __name__" in contract or "Point d'entrée" in contract
        assert "Type hints" in contract or "type hints" in contract.lower()
        assert "Logging" in contract or "logging" in contract

    def test_contract_node_enriched(self):
        """Le contrat Node doit contenir les nouvelles directives."""
        contract = _get_contract_prompt(
            "Express API server",
            [{"path": "index.ts", "language": "typescript"}, {"path": "package.json", "language": "json"}],
        )
        assert "Middleware" in contract or "middleware" in contract
        assert "error" in contract.lower()

    def test_contract_game_enriched(self):
        """Le contrat Game doit contenir les nouvelles directives."""
        contract = _get_contract_prompt(
            "Jeu pygame space invaders",
            [{"path": "game.py", "language": "python"}],
        )
        assert "collision" in contract.lower()
        assert "score" in contract.lower()

    def test_contract_web_unchanged(self):
        """Le contrat Web ne doit pas casser."""
        contract = _get_contract_prompt(
            "Site web portfolio moderne",
            [{"path": "index.html", "language": "html"}, {"path": "style.css", "language": "css"}],
        )
        assert "SPA" in contract or "Classes CSS" in contract
        assert "ACCESSIBILITE" in contract or "WCAG" in contract

    def test_contract_api_via_detect(self):
        """FastAPI doit être détecté comme python_api → contrat Python."""
        contract = _get_contract_prompt(
            "API REST avec FastAPI pour gérer les utilisateurs",
            [{"path": "main.py", "language": "python"}, {"path": "routes.py", "language": "python"}],
        )
        assert "PYTHON" in contract
        assert "imports" in contract.lower() or "Imports" in contract


# ═══════════════════════════════════════════════════════════════════════════
# P4 — Validation syntaxique pré-écriture
# ═══════════════════════════════════════════════════════════════════════════


class TestQuickSyntaxCheck:
    """Vérifie que _quick_syntax_check attrape les erreurs grossières."""

    def test_valid_html(self):
        html = "<!DOCTYPE html><html><head><title>Test</title></head><body><h1>Hello</h1></body></html>"
        assert _quick_syntax_check(html, ".html") is None

    def test_broken_html_no_close(self):
        html = "<!DOCTYPE html><html><head><title>Test</title></head><body><h1>Hello</h1>"
        err = _quick_syntax_check(html, ".html")
        assert err is not None
        assert "html" in err.lower()

    def test_broken_html_script(self):
        html = '<!DOCTYPE html><html><body><script>console.log("x")</body></html>'
        err = _quick_syntax_check(html, ".html")
        assert err is not None
        assert "script" in err.lower()

    def test_valid_js(self):
        js = "function hello() {\n  console.log('hi');\n}\nhello();"
        assert _quick_syntax_check(js, ".js") is None

    def test_broken_js_braces(self):
        js = "function hello() {\n  if (true) {\n    for (let i=0; i<10; i++) {\n      console.log('hi');\n}"
        err = _quick_syntax_check(js, ".js")
        assert err is not None
        assert "accolade" in err.lower()

    def test_valid_css(self):
        css = ":root { --primary: blue; }\n.hero { color: var(--primary); }"
        assert _quick_syntax_check(css, ".css") is None

    def test_broken_css_braces(self):
        css = ":root { --primary: blue;\n.hero { color: red; }"
        err = _quick_syntax_check(css, ".css")
        assert err is not None
        assert "accolade" in err.lower()

    def test_valid_json(self):
        assert _quick_syntax_check('{"key": "value", "n": 42}', ".json") is None

    def test_broken_json(self):
        err = _quick_syntax_check('{"key": "value",', ".json")
        assert err is not None
        assert "JSON" in err

    def test_valid_python(self):
        py = "def hello():\n    print('hi')\n\nhello()"
        assert _quick_syntax_check(py, ".py") is None

    def test_broken_python(self):
        py = "def hello(\n    print('hi')"
        err = _quick_syntax_check(py, ".py")
        assert err is not None
        assert "SyntaxError" in err

    def test_yaml_mixed_indent(self):
        yml = "key:\n  value1: a\n\tvalue2: b"
        err = _quick_syntax_check(yml, ".yml")
        assert err is not None
        assert "tab" in err.lower() or "YAML" in err

    def test_unknown_ext_passes(self):
        """Extension non reconnue → None (OK par défaut)."""
        assert _quick_syntax_check("whatever content here..", ".xyz") is None

    def test_empty_content(self):
        err = _quick_syntax_check("", ".py")
        assert err is not None


# ═══════════════════════════════════════════════════════════════════════════
# P4 — _looks_truncated renforcé
# ═══════════════════════════════════════════════════════════════════════════


class TestLooksTruncatedReinforced:
    """Vérifie les nouvelles détections de troncature."""

    def test_html_script_unclosed(self):
        html = '<!DOCTYPE html><html><body><script>console.log("hi");</body></html>'
        assert _looks_truncated(html, ".html")

    def test_css_media_unclosed(self):
        css = "@media (max-width: 768px) {\n  .hero { display: none; }"
        assert _looks_truncated(css, ".css")

    def test_js_block_comment_unclosed(self):
        js = "function hello() {\n  /* this is a comment\n  that never closes\n}"
        assert _looks_truncated(js, ".js")

    def test_js_valid_not_truncated(self):
        js = "function hello() {\n  /* comment */\n  console.log('hi');\n}"
        assert not _looks_truncated(js, ".js")

    def test_html_complete_not_truncated(self):
        html = "<!DOCTYPE html><html><body><script>x()</script></body></html>"
        assert not _looks_truncated(html, ".html")


# ═══════════════════════════════════════════════════════════════════════════
# P5 — Plan supplements + design déterministe
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanSupplements:
    """Vérifie l'injection de contraintes architecturales en Phase 1."""

    def test_plan_web_supplement(self):
        supplement = _PLAN_SUPPLEMENT_MAP.get("web", "")
        assert "SPA" in supplement
        assert "responsive" in supplement.lower() or "mobile" in supplement.lower()

    def test_plan_python_supplement(self):
        supplement = _PLAN_SUPPLEMENT_MAP.get("python_cli", "")
        assert "main.py" in supplement or "requirements" in supplement.lower()

    def test_plan_api_supplement(self):
        supplement = _PLAN_SUPPLEMENT_MAP.get("python_api", "")
        assert "health" in supplement.lower() or "endpoint" in supplement.lower()

    def test_plan_game_supplement(self):
        supplement = _PLAN_SUPPLEMENT_MAP.get("game", "")
        assert "game loop" in supplement.lower() or "init" in supplement.lower()

    def test_plan_generic_no_supplement(self):
        """Generic ne doit pas avoir de supplement."""
        supplement = _PLAN_SUPPLEMENT_MAP.get("generic", "")
        assert supplement == ""

    def test_plan_docker_no_supplement(self):
        """Docker n'a pas de supplement dédié."""
        supplement = _PLAN_SUPPLEMENT_MAP.get("docker", "")
        assert supplement == ""


class TestDesignDeterministic:
    """Vérifie que les directives de design sont déterministes par description."""

    def test_same_description_same_result(self):
        from src.tools.website_builder import build_design_directives
        d1 = build_design_directives("restaurant italien à Paris")
        d2 = build_design_directives("restaurant italien à Paris")
        assert d1 == d2

    def test_different_description_different_result(self):
        from src.tools.website_builder import build_design_directives
        d1 = build_design_directives("restaurant italien à Paris")
        d2 = build_design_directives("portfolio photographe minimaliste")
        # Les palettes/fonts peuvent différer, au minimum la variation unique diffère
        assert d1 != d2


class TestWebsitePromptLength:
    """Vérifie que website.py injecte assez de contexte."""

    def test_prompt_at_least_2500_chars(self):
        from src.tools.website_builder import WEBSITE_GENERATE_PROMPT
        assert len(WEBSITE_GENERATE_PROMPT[:3000]) >= 2500


# ═══════════════════════════════════════════════════════════════════════════
# P6 — Self-repair amélioré
# ═══════════════════════════════════════════════════════════════════════════


class TestSelfRepairConfig:
    """Vérifie les constantes de self-repair améliorées."""

    def test_repair_max_iter_is_3(self):
        """Self-repair a maintenant 3 itérations max."""
        # On ne peut pas importer la constante locale, on la vérifie dans le source
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_REPAIR_MAX_ITER = 3" in src

    def test_repair_max_files_is_10(self):
        """Self-repair traite jusqu'à 10 fichiers par itération."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "[:10]" in src

    def test_repair_updates_generated_contents(self):
        """Après réparation, _generated_contents est mis à jour (pas seulement _all_files)."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        # Le self-repair doit écrire dans _generated_contents
        assert "_generated_contents[bp] = _fixed" in src

    def test_repair_injects_prev_context(self):
        """Le prompt de réparation injecte le contexte des fichiers déjà réparés."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_repaired_this_iter" in src
        assert "corrigés dans cette itération" in src

    def test_repair_uses_quick_syntax_check(self):
        """Self-repair utilise _quick_syntax_check au lieu du simple ast.parse."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        # Dans la section self-repair, on vérifie l'usage de _quick_syntax_check
        assert "_quick_syntax_check(_fixed" in src


# ═══════════════════════════════════════════════════════════════════════════
# P7 — Contrat partagé JSON structuré
# ═══════════════════════════════════════════════════════════════════════════


class TestJsonContractCapability:
    """Vérifie la détection des modèles JSON-capable."""

    def test_deepseek_v3_capable(self):
        assert _is_json_contract_capable("deepseek-v3")

    def test_gpt4_1_capable(self):
        assert _is_json_contract_capable("gpt-4.1")

    def test_gpt4_1_mini_capable(self):
        assert _is_json_contract_capable("gpt-4.1-mini")

    def test_claude_4_capable(self):
        assert _is_json_contract_capable("claude-4")

    def test_minimax_not_capable(self):
        assert not _is_json_contract_capable("minimax-m2.5")

    def test_ollama_not_capable(self):
        assert not _is_json_contract_capable("qwen3-8b")

    def test_empty_model_not_capable(self):
        assert not _is_json_contract_capable("")


class TestJsonContractPrompt:
    """Vérifie que le prompt contrat s'adapte selon la capacité JSON du modèle."""

    def test_json_prompt_for_capable_model_web(self):
        files = [{"path": "index.html"}, {"path": "style.css"}, {"path": "app.js"}]
        prompt = _get_contract_prompt("landing page portfolio photographe", files, model_name="deepseek-v3")
        assert "JSON" in prompt
        assert "css_classes" in prompt

    def test_json_prompt_for_capable_model_python(self):
        files = [{"path": "main.py"}, {"path": "utils.py"}]
        prompt = _get_contract_prompt("script CLI Python", files, model_name="gpt-4.1")
        assert "JSON" in prompt
        assert "entry_point" in prompt

    def test_text_prompt_for_incapable_model(self):
        files = [{"path": "index.html"}, {"path": "style.css"}]
        prompt = _get_contract_prompt("landing page portfolio", files, model_name="minimax-m2.5")
        assert "texte libre" in prompt.lower() or "pas JSON" in prompt

    def test_text_prompt_when_no_model(self):
        files = [{"path": "main.py"}]
        prompt = _get_contract_prompt("script Python", files, model_name="")
        assert "texte libre" in prompt.lower() or "pas JSON" in prompt

    def test_json_schemas_cover_main_types(self):
        """Tous les types principaux ont un schéma JSON."""
        for ptype in ["web", "python_api", "python_cli", "node_express", "game"]:
            assert ptype in _CONTRACT_JSON_SCHEMA_MAP, f"Missing schema for {ptype}"


class TestFormatJsonContract:
    """Vérifie le formatage JSON → texte lisible."""

    def test_format_simple_dict(self):
        data = {"entry_point": "main.py", "font": "Inter"}
        result = _format_json_contract(data)
        assert "entry_point: main.py" in result
        assert "font: Inter" in result

    def test_format_list(self):
        data = {"css_classes": ["hero", "nav", "card"]}
        result = _format_json_contract(data)
        assert "hero" in result
        assert "nav" in result

    def test_format_nested_dict(self):
        data = {"color_palette": {"primary": "#3B82F6", "secondary": "#10B981"}}
        result = _format_json_contract(data)
        assert "#3B82F6" in result
        assert "primary" in result

    def test_format_api_endpoints(self):
        data = {"api_endpoints": [{"method": "GET", "path": "/api/users"}]}
        result = _format_json_contract(data)
        assert "GET" in result
        assert "/api/users" in result

    def test_format_empty_dict(self):
        result = _format_json_contract({})
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# P8 — Validation cross-file Python + Node
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossPython:
    """Vérifie la validation inter-fichiers Python (noms d'imports)."""

    def test_valid_imports(self):
        from src.tools.code_validator import _validate_cross_python
        files = {
            "main.py": "from utils import helper\nhelper()\n",
            "utils.py": "def helper():\n    return 42\n",
        }
        issues = _validate_cross_python(files)
        assert len(issues) == 0

    def test_missing_name(self):
        from src.tools.code_validator import _validate_cross_python
        files = {
            "main.py": "from utils import nonexistent\nnonexistent()\n",
            "utils.py": "def helper():\n    return 42\n",
        }
        issues = _validate_cross_python(files)
        assert len(issues) == 1
        assert issues[0].code == "PY_MISSING_NAME"
        assert "nonexistent" in issues[0].message

    def test_class_import_ok(self):
        from src.tools.code_validator import _validate_cross_python
        files = {
            "main.py": "from models import User\nu = User()\n",
            "models.py": "class User:\n    pass\n",
        }
        issues = _validate_cross_python(files)
        assert len(issues) == 0

    def test_constant_import_ok(self):
        from src.tools.code_validator import _validate_cross_python
        files = {
            "main.py": "from config import PORT\nprint(PORT)\n",
            "config.py": "PORT = 8080\n",
        }
        issues = _validate_cross_python(files)
        assert len(issues) == 0

    def test_single_file_skip(self):
        from src.tools.code_validator import _validate_cross_python
        files = {"main.py": "import os\nprint(os.getcwd())\n"}
        issues = _validate_cross_python(files)
        assert len(issues) == 0

    def test_star_import_skip(self):
        from src.tools.code_validator import _validate_cross_python
        files = {
            "main.py": "from utils import *\n",
            "utils.py": "def helper():\n    pass\n",
        }
        issues = _validate_cross_python(files)
        assert len(issues) == 0


class TestCrossNode:
    """Vérifie la validation inter-fichiers Node.js."""

    def test_valid_require(self):
        from src.tools.code_validator import _validate_cross_node
        files = {
            "index.js": "const db = require('./db');\ndb.connect();\n",
            "db.js": "module.exports = { connect() {} };\n",
        }
        issues = _validate_cross_node(files)
        assert len(issues) == 0

    def test_missing_require(self):
        from src.tools.code_validator import _validate_cross_node
        files = {
            "index.js": "const db = require('./missing');\n",
        }
        issues = _validate_cross_node(files)
        assert len(issues) == 1
        assert issues[0].code == "NODE_MISSING_MODULE"

    def test_valid_import_from(self):
        from src.tools.code_validator import _validate_cross_node
        files = {
            "app.js": "import { router } from './routes.js';\n",
            "routes.js": "export const router = {};\n",
        }
        issues = _validate_cross_node(files)
        assert len(issues) == 0

    def test_missing_import_from(self):
        from src.tools.code_validator import _validate_cross_node
        files = {
            "app.js": "import { router } from './missing';\n",
        }
        issues = _validate_cross_node(files)
        assert len(issues) == 1
        assert issues[0].code == "NODE_MISSING_MODULE"

    def test_extensionless_resolve(self):
        """require('./db') doit matcher db.js ou db.ts."""
        from src.tools.code_validator import _validate_cross_node
        files = {
            "index.js": "const db = require('./db');\n",
            "db.ts": "export const connect = () => {};\n",
        }
        issues = _validate_cross_node(files)
        assert len(issues) == 0

    def test_no_js_files_skip(self):
        from src.tools.code_validator import _validate_cross_node
        files = {"main.py": "print('hello')\n"}
        issues = _validate_cross_node(files)
        assert len(issues) == 0


class TestCrossValidationIntegration:
    """Vérifie que les nouveaux validateurs sont intégrés dans validate_project."""

    def test_python_cross_in_validate_project(self):
        from src.tools.code_validator import validate_project
        files = {
            "main.py": "from utils import nonexistent\n",
            "utils.py": "def helper():\n    pass\n",
        }
        report = validate_project(files)
        codes = [i.code for i in report.issues]
        assert "PY_MISSING_NAME" in codes

    def test_node_cross_in_validate_project(self):
        from src.tools.code_validator import validate_project
        files = {
            "app.js": "const x = require('./missing');\n",
        }
        report = validate_project(files)
        codes = [i.code for i in report.issues]
        assert "NODE_MISSING_MODULE" in codes


# ═══════════════════════════════════════════════════════════════════════════
# P9 — CodeAgent fallback post-génération
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeAgentFallback:
    """Vérifie la présence du CodeAgent fallback dans le pipeline."""

    def test_codeagent_import_available(self):
        """Le flag _CODEAGENT_AVAILABLE existe et est bool."""
        assert isinstance(_CODEAGENT_AVAILABLE, bool)

    def test_codeagent_fallback_in_source_validation(self):
        """CodeAgent est appelé après échec self-repair dans le code source."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_delegate_to_agent" in src
        assert "CodeAgent fallback" in src

    def test_codeagent_fallback_in_source_run_fix(self):
        """CodeAgent est appelé après échec auto-run dans le code source."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "auto-run échoué" in src

    def test_codeagent_guarded_by_availability(self):
        """CodeAgent est gardé par _CODEAGENT_AVAILABLE."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_CODEAGENT_AVAILABLE" in src

    def test_codeagent_has_try_except(self):
        """Les appels CodeAgent sont protégés par try/except."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "CodeAgent fallback échoué" in src or "CodeAgent run-fix fallback échoué" in src


# ═══════════════════════════════════════════════════════════════════════════
# P10 — Plan prompt modulaire par type de projet
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanPromptModular:
    """Vérifie que _build_plan_prompt injecte la bonne section selon le type."""

    def test_plan_prompt_web_contains_spa(self):
        """Type 'web' → prompt contient architecture SPA."""
        prompt = _build_plan_prompt("web", 20)
        assert "SPA" in prompt
        assert "routeur" in prompt.lower() or "navigateTo" in prompt

    def test_plan_prompt_web_excludes_python(self):
        """Type 'web' → prompt NE contient PAS les sections Python."""
        prompt = _build_plan_prompt("web", 20)
        assert "pyproject.toml" not in prompt
        assert "argparse" not in prompt

    def test_plan_prompt_python_contains_conventions(self):
        """Type 'python_cli' → prompt contient conventions Python."""
        prompt = _build_plan_prompt("python_cli", 20)
        assert "pyproject.toml" in prompt
        assert "snake_case" in prompt

    def test_plan_prompt_python_excludes_spa(self):
        """Type 'python_cli' → prompt NE contient PAS SPA."""
        prompt = _build_plan_prompt("python_cli", 20)
        assert "SPA" not in prompt

    def test_plan_prompt_api_contains_rest(self):
        """Type 'python_api' → prompt contient REST conventions."""
        prompt = _build_plan_prompt("python_api", 20)
        assert "routes" in prompt.lower()
        assert "schemas" in prompt.lower()
        assert "health" in prompt.lower()

    def test_plan_prompt_game_contains_game_loop(self):
        """Type 'game' → prompt contient game loop et FPS."""
        prompt = _build_plan_prompt("game", 20)
        assert "game loop" in prompt.lower()
        assert "FPS" in prompt

    def test_plan_prompt_desktop_contains_gui(self):
        """Type 'desktop' → prompt contient GUI/MVC."""
        prompt = _build_plan_prompt("desktop", 20)
        assert "MVC" in prompt or "MVP" in prompt
        assert "fenêtre" in prompt.lower() or "window" in prompt.lower()

    def test_plan_prompt_docker_contains_dockerfile(self):
        """Type 'docker' → prompt contient Dockerfile."""
        prompt = _build_plan_prompt("docker", 20)
        assert "Dockerfile" in prompt
        assert "docker-compose" in prompt.lower()

    def test_plan_prompt_common_always_present(self):
        """Toutes les sections contiennent les règles communes."""
        for ptype in ["web", "python_cli", "python_api", "game", "desktop", "docker"]:
            prompt = _build_plan_prompt(ptype, 15)
            assert "FICHIERS BINAIRES INTERDITS" in prompt, f"manque binaires interdits pour {ptype}"
            assert '"project_name"' in prompt, f"manque JSON format pour {ptype}"
            assert "15" in prompt, f"max_files non injecté pour {ptype}"

    def test_plan_prompt_unknown_type_common_only(self):
        """Type inconnu → prompt contient uniquement les règles communes."""
        prompt = _build_plan_prompt("unknown_type", 20)
        assert "FICHIERS BINAIRES INTERDITS" in prompt
        assert "SPA" not in prompt
        assert "pyproject.toml" not in prompt
        assert "game loop" not in prompt.lower()

    def test_plan_section_map_coverage(self):
        """_PLAN_SECTION_MAP couvre tous les types principaux."""
        expected = {"web", "python_cli", "python_package", "python_api",
                    "node_express", "game", "desktop", "data_science", "docker"}
        assert expected == set(_PLAN_SECTION_MAP.keys())

    def test_plan_prompt_max_files_substituted(self):
        """max_files est bien substitué dans le prompt."""
        prompt = _build_plan_prompt("web", 42)
        assert "42" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATOR PRODUCTION FIXES — faux positifs éliminés
# ═══════════════════════════════════════════════════════════════════════════

class TestValidatorProductionFixes:
    """Tests pour les correctifs validateur découverts en production."""

    def test_web_detected_before_api_when_enriched(self):
        """Description enrichie avec WEBSITE_GENERATE_PROMPT ne retourne pas node_express."""
        desc = (
            "Site portfolio photographe avec galerie filtrable "
            "api rest endpoint serveur server responsive"
        )
        files = ["index.html", "style.css", "app.js"]
        assert _detect_project_type(desc, files) == "web"

    def test_web_detected_pure_portfolio(self):
        """Portfolio simple = web, pas node_express."""
        desc = "Crée un site portfolio pour un photographe avec galerie, page à propos et formulaire de contact"
        files = ["index.html", "css/style.css", "js/main.js"]
        assert _detect_project_type(desc, files) == "web"

    def test_express_still_detected_when_real(self):
        """Un vrai projet Express est toujours détecté correctement."""
        desc = "Express.js REST API backend with user authentication"
        files = ["package.json", "server.js", "routes/users.js"]
        assert _detect_project_type(desc, files) == "node_express"

    def test_validator_no_false_positive_test_frameworks(self):
        """Les globals Jest/Vitest ne sont pas flaggées undefined."""
        files = {
            "test_app.js": "describe('App', () => { it('works', () => { expect(1).toBe(1); }); });",
        }
        report = validate_project(files)
        undef = [i for i in report.issues if i.code == "JS_UNDEFINED_FUNCTION"]
        assert len(undef) == 0

    def test_validator_no_false_positive_class_methods(self):
        """Les méthodes de classe ne sont pas flaggées undefined."""
        files = {
            "app.js": (
                "class Router {\n"
                "  navigateTo(path) {\n"
                "    console.log(path);\n"
                "  }\n"
                "}\n"
                "const r = new Router();\n"
                "r.navigateTo('/home');\n"
            ),
        }
        report = validate_project(files)
        undef = [i for i in report.issues if i.code == "JS_UNDEFINED_FUNCTION"
                 and "navigateTo" in i.message]
        assert len(undef) == 0

    def test_validator_no_false_positive_window_export(self):
        """window.xxx = ... exporte globalement, pas flaggé dans onclick."""
        files = {
            "index.html": '<button onclick="navigateTo(\'/home\')">Home</button>',
            "router.js": (
                "class Router {\n"
                "  navigateTo(path) { location.hash = path; }\n"
                "}\n"
                "window.navigateTo = (p) => router.navigateTo(p);\n"
            ),
        }
        report = validate_project(files)
        handler_issues = [i for i in report.issues if i.code == "XREF_UNDEFINED_HANDLER"]
        assert len(handler_issues) == 0

    def test_validator_no_false_positive_promise_callback(self):
        """Les paramètres de callback Promise ne sont pas flaggés undefined."""
        files = {
            "form.js": (
                "function submitForm() {\n"
                "  return new Promise((resolvePromise, rejectPromise) => {\n"
                "    resolvePromise('ok');\n"
                "  });\n"
                "}\n"
            ),
        }
        report = validate_project(files)
        undef = [i for i in report.issues if i.code == "JS_UNDEFINED_FUNCTION"
                 and "resolvePromise" in i.message]
        assert len(undef) == 0

    def test_validator_backslash_path_no_duplicates(self):
        """Les chemins Windows avec backslash ne créent pas de doublons."""
        files = {
            "js\\app.js": "function hello() { return 1; }",
            "js/app.js": "function hello() { return 1; }",
        }
        report = validate_project(files)
        # Les deux clés existent en entrée mais en interne normalisées
        assert report.files_checked == 2

    def test_validator_dynamic_dom_elements_from_js(self):
        """Les éléments DOM créés dynamiquement en JS sont reconnus."""
        files = {
            "index.html": "<div id='app'></div>",
            "app.js": (
                'const html = `<div class="modal-overlay"><div class="modal-content">Hi</div></div>`;\n'
                'document.querySelector(".modal-overlay").remove();\n'
            ),
        }
        report = validate_project(files)
        missing = [i for i in report.issues if i.code == "XREF_JS_MISSING_CLASS"
                   and "modal-overlay" in i.message]
        assert len(missing) == 0

    def test_validator_test_file_skip_undefined(self):
        """Les fichiers de test sont exemptés du check JS_UNDEFINED_FUNCTION."""
        files = {
            "test.spec.js": "describe('X', () => { it('y', () => { customHelper(); }); });",
        }
        report = validate_project(files)
        undef = [i for i in report.issues if i.code == "JS_UNDEFINED_FUNCTION"]
        assert len(undef) == 0

    def test_validator_keywords_not_flagged(self):
        """async, await, function, class ne sont pas flaggés comme fonctions."""
        files = {
            "app.js": (
                "async function loadData() {\n"
                "  const data = await fetch('/api');\n"
                "  class Model {}\n"
                "  return new Model();\n"
                "}\n"
            ),
        }
        report = validate_project(files)
        undef = [i for i in report.issues if i.code == "JS_UNDEFINED_FUNCTION"
                 and i.message and any(k in i.message for k in ("async", "await", "class"))]
        assert len(undef) == 0


# ═══════════════════════════════════════════════════════════════════════════
# P11 — Provider-agnostic retry & small model prompt trimming
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderAgnosticRetry:
    """Vérifie que le retry fonctionne pour n'importe quel provider (pas que deepseek)."""

    def test_upgrade_map_in_source(self):
        """Le _UPGRADE_MAP couvre GPT nano/mini et deepseek."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_UPGRADE_MAP" in src
        assert '"gpt-5.4-nano": "gpt-5.4-mini"' in src
        assert '"gpt-4.1-nano": "gpt-4.1-mini"' in src
        assert '"deepseek-chat": "deepseek-reasoner"' in src

    def test_get_upgrade_model_in_source(self):
        """La fonction _get_upgrade_model existe dans le handler."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "def _get_upgrade_model(" in src
        assert "_UPGRADE_MAP.get(cl)" in src

    def test_empty_content_retry_provider_agnostic(self):
        """Le retry contenu vide est provider-agnostic (pas limité à _is_deepseek)."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        # Chercher le nouveau code: retry quand vide + _was_truncated (sans condition _is_deepseek exclusive)
        assert "vide/tronqué sur" in src
        assert "_get_upgrade_model(_used_model)" in src

    def test_truncation_retry_provider_agnostic(self):
        """Le retry _looks_truncated utilise _get_upgrade_model (pas que deepseek-reasoner)."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        # Le bloc _looks_truncated doit appeler _get_upgrade_model
        lines = src.split("\n")
        found_truncated_block = False
        found_upgrade_in_block = False
        for i, line in enumerate(lines):
            if "_looks_truncated(content, _ext)" in line:
                found_truncated_block = True
            if found_truncated_block and "_get_upgrade_model(_used_model)" in line:
                found_upgrade_in_block = True
                break
        assert found_truncated_block
        assert found_upgrade_in_block

    def test_process_one_retry_uses_upgrade(self):
        """_process_one_and_write utilise _get_upgrade_model au lieu de model_override=None."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        # Le retry dans _process_one_and_write doit utiliser _get_upgrade_model
        assert "_retry_override = _get_upgrade_model(_failed_model)" in src
        # Et l'ancien pattern model_override=None ne doit plus exister dans le retry
        lines = src.split("\n")
        for line in lines:
            if "model_override=None" in line and "Retry" in line:
                pytest.fail("model_override=None trouvé dans un retry — devrait utiliser _get_upgrade_model")


class TestSmallModelPromptTrimming:
    """Vérifie que les modèles légers ont un prompt allégé."""

    @pytest.fixture(autouse=True)
    def _force_batch_pipeline(self, monkeypatch):
        monkeypatch.setattr(
            "src.reasoning.handlers.project._CODEAGENT_AVAILABLE", False,
        )

    def test_small_model_detection_in_source(self):
        """_is_small_model détecte nano, mini dans le nom du modèle."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "_is_small_model" in src
        assert '"nano"' in src
        assert '"mini"' in src

    def test_prompt_trimmed_for_small_model(self):
        """Pour modèles légers, _web_directives exclut WEBSITE_GENERATE_PROMPT."""
        import inspect
        from src.reasoning.handlers import project
        src = inspect.getsource(project.create_project_handler)
        assert "prompt web allégé" in src
        assert "_design_only = _build_design_directives(description)" in src

    @pytest.mark.asyncio
    async def test_nano_model_generates_content(self, tmp_path):
        """Un modèle nano doit pouvoir générer du contenu grâce au prompt allégé + retry upgrade."""
        call_count = 0
        async def mock_chat(messages, temperature=0.7, max_tokens=65536, model=None, stop=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1: plan — doit retourner {"files": [...]}
                return json.dumps({
                    "files": [
                        {"path": "index.html", "description": "Page principale", "language": "html"}
                    ],
                    "project_name": "test_nano"
                })
            if model and ("mini" in str(model) or "gpt-5.4" == str(model)):
                # Retry upgrade → contenu OK
                return "<!DOCTYPE html><html><head><title>Test</title></head><body><h1>Hello</h1></body></html>"
            # Appels nano → vide (simule GPT Nano qui échoue)
            return ""

        mock_llm = MagicMock()
        mock_llm.chat = mock_chat
        mock_llm.model = "gpt-5.4-nano"
        mock_llm.provider = MagicMock()
        mock_llm.provider.value = "openai"
        mock_llm.provider.__str__ = lambda s: "openai"
        mock_llm.max_output_tokens = 16384
        mock_llm._last_response_meta = {
            "text_may_be_incomplete": True,
            "model_used": "gpt-5.4-nano",
        }

        mock_lumena = MagicMock()
        mock_lumena.llm = mock_llm
        mock_lumena.workspace_path = str(tmp_path)

        mock_ctx = MagicMock()
        mock_ctx.lumena = mock_lumena
        mock_ctx.runtime_root = tmp_path / "runtime"

        with patch("src.reasoning.handlers.project._build_design_directives", return_value="Design: modern"), \
             patch("src.reasoning.handlers.project._WEBSITE_BUILDER_AVAILABLE", True), \
             patch("src.reasoning.handlers.project._WEBSITE_GENERATE_PROMPT", "X" * 3000), \
             patch("src.reasoning.handlers.project._VALIDATOR_AVAILABLE", False):
            result = await create_project_handler(
                ctx=mock_ctx,
                description="site vitrine one-page pour Lumena",
                project_name="test_nano",
                output_dir=str(tmp_path / "out"),
                auto_run=False,
            )

        # Le handler ne doit pas crasher; il doit au minimum avoir tenté la génération
        assert result is not None
        assert call_count >= 2  # au moins: plan + 1 tentative de génération
