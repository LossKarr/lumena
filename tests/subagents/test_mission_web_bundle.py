from __future__ import annotations

from src.subagents.mission_web_bundle import validate_contract_web_bundle


def _contract():
    return {
        "project": "runway",
        "files": [
            {"path": "static/index.html", "owner": "frontend", "desc": "UI"},
            {"path": "static/style.css", "owner": "frontend", "desc": "Styles"},
            {"path": "static/app.js", "owner": "frontend", "desc": "Interactions"},
        ],
    }


def test_valid_contract_web_bundle_passes(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><main id="dashboard"></main>'
        '<script src="app.js"></script>', encoding="utf-8",
    )
    (static / "style.css").write_text("#dashboard { display: block; }", encoding="utf-8")
    (static / "app.js").write_text(
        "document.getElementById('dashboard').textContent = 'ok';", encoding="utf-8",
    )

    assert validate_contract_web_bundle(tmp_path, _contract())["errors"] == []


def test_undeclared_duplicate_script_is_rejected(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><script src="script.js"></script>',
        encoding="utf-8",
    )
    (static / "style.css").write_text("body {}", encoding="utf-8")
    (static / "app.js").write_text("console.log('canonical')", encoding="utf-8")
    (static / "script.js").write_text("console.log('drift')", encoding="utf-8")

    errors = validate_contract_web_bundle(tmp_path, _contract())["errors"]
    assert any("sources_non_declarees" in item and "static/script.js" in item for item in errors)
    assert any("asset_non_declare" in item and "static/script.js" in item for item in errors)
    assert any("asset_non_lie" in item and "static/app.js" in item for item in errors)


def test_missing_html_id_referenced_by_javascript_is_rejected(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><main id="dashboard"></main>'
        '<script src="app.js"></script>', encoding="utf-8",
    )
    (static / "style.css").write_text("body {}", encoding="utf-8")
    (static / "app.js").write_text(
        "document.getElementById('scenario-status').textContent = 'ok';", encoding="utf-8",
    )

    errors = validate_contract_web_bundle(tmp_path, _contract())["errors"]
    assert any("ids_html_manquants" in item and "scenario-status" in item for item in errors)


def test_non_web_contract_is_ignored(tmp_path):
    (tmp_path / "report.py").write_text("print('ok')", encoding="utf-8")
    report = validate_contract_web_bundle(tmp_path, {
        "files": [{"path": "report.py", "owner": "worker", "exports": ["def run():"]}],
    })
    assert report == {"errors": [], "warnings": []}
