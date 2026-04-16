"""Tests Phase 4 — scheduler DAG par vagues (project.py)."""
import pytest
from src.reasoning.handlers.project import _dep_wave, _dep_sort_key


class TestDepWave:
    """Vérification de la classification en waves."""

    def test_env_wave_0(self):
        assert _dep_wave({"path": ".env"}) == 0

    def test_json_wave_0(self):
        assert _dep_wave({"path": "package.json"}) == 0

    def test_yaml_wave_0(self):
        assert _dep_wave({"path": "docker-compose.yml"}) == 0

    def test_toml_wave_0(self):
        assert _dep_wave({"path": "pyproject.toml"}) == 0

    def test_css_wave_1(self):
        assert _dep_wave({"path": "styles.css"}) == 1

    def test_scss_wave_1(self):
        assert _dep_wave({"path": "main.scss"}) == 1

    def test_sql_wave_1(self):
        assert _dep_wave({"path": "schema.sql"}) == 1

    def test_py_wave_2(self):
        assert _dep_wave({"path": "app.py"}) == 2

    def test_js_wave_2(self):
        assert _dep_wave({"path": "main.js"}) == 2

    def test_ts_wave_2(self):
        assert _dep_wave({"path": "index.ts"}) == 2

    def test_html_wave_2(self):
        assert _dep_wave({"path": "index.html"}) == 2

    def test_md_wave_2(self):
        assert _dep_wave({"path": "README.md"}) == 2

    def test_txt_wave_2(self):
        assert _dep_wave({"path": "notes.txt"}) == 2

    def test_no_extension_wave_2(self):
        assert _dep_wave({"path": "Makefile"}) == 2

    def test_empty_path_wave_2(self):
        assert _dep_wave({"path": ""}) == 2


class TestDepSortKey:
    """Le tri classique reste intact."""

    def test_env_first(self):
        assert _dep_sort_key({"path": ".env"}) < _dep_sort_key({"path": "styles.css"})

    def test_css_before_html(self):
        assert _dep_sort_key({"path": "main.css"}) < _dep_sort_key({"path": "index.html"})

    def test_json_before_py(self):
        assert _dep_sort_key({"path": "config.json"}) < _dep_sort_key({"path": "app.py"})

    def test_html_before_md(self):
        assert _dep_sort_key({"path": "index.html"}) < _dep_sort_key({"path": "README.md"})


class TestWaveOrdering:
    """Les waves garantissent que les dépendances sont générées en premier."""

    def test_wave_0_before_wave_1(self):
        """Configs (wave 0) doivent être générés avant CSS (wave 1)."""
        files = [
            {"path": "styles.css"},      # wave 1
            {"path": "package.json"},    # wave 0
            {"path": "index.html"},      # wave 2
            {"path": ".env"},            # wave 0
        ]
        # Grouper par wave
        from collections import defaultdict
        waves = defaultdict(list)
        for f in files:
            waves[_dep_wave(f)].append(f["path"])
        assert "package.json" in waves[0]
        assert ".env" in waves[0]
        assert "styles.css" in waves[1]
        assert "index.html" in waves[2]

    def test_web_project_wave_order(self):
        """Dans un projet web, CSS (w1) avant HTML (w2) avant JS (w2)."""
        web_files = [
            {"path": "index.html"},
            {"path": "styles.css"},
            {"path": "app.js"},
            {"path": "package.json"},
        ]
        wave_map = {f["path"]: _dep_wave(f) for f in web_files}
        # CSS doit être dans une wave <= HTML
        assert wave_map["styles.css"] <= wave_map["index.html"]
        # config avant tout
        assert wave_map["package.json"] < wave_map["index.html"]
