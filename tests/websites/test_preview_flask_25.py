"""2.5 (runs MotDuJour/Converto 2026-07-06) — la preview officielle sert FLASK.

`start_preview_server` était un serveur STATIQUE : une app à /api/* chargeait sa
page mais tous les fetch échouaient → la jambe navigateur d'une mission Flask
était structurellement impossible (le run final §5 MiniQuiz en dépend).

2.5 : détection `app.py` + create_app/Flask → l'app Flask elle-même est lancée
(process isolé, use_reloader=False, aucun pipe tenu) ; readiness RÉELLE (le port
doit écouter avant « success ») ; port jamais < 8081 (8080 = réservé Lumena).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.website_builder import (
    _flask_entry,
    start_preview_server,
    stop_preview_server,
)
from src.utils.local_preview import is_preview_allowed

_FLASK_APP = '''\
from flask import Flask, jsonify


def create_app():
    app = Flask(__name__)

    @app.route("/api/ping")
    def ping():
        return jsonify({"pong": True})

    return app
'''


@pytest.fixture(autouse=True)
def _cleanup_server():
    yield
    stop_preview_server()


# ── détection pure ──────────────────────────────────────────────────────────────

def test_flask_entry_detected(tmp_path):
    (tmp_path / "app.py").write_text(_FLASK_APP, encoding="utf-8")
    assert _flask_entry(tmp_path) is True


def test_non_flask_app_py_not_detected(tmp_path):
    (tmp_path / "app.py").write_text("print('pas un serveur')\n", encoding="utf-8")
    assert _flask_entry(tmp_path) is False


def test_no_app_py_not_detected(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert _flask_entry(tmp_path) is False


# ── Flask bout-en-bout ──────────────────────────────────────────────────────────

@pytest.mark.timeout(60)
def test_flask_app_served_for_real(tmp_path):
    """Le critère du run §5 : les routes /api/* TOURNENT via la voie officielle."""
    (tmp_path / "app.py").write_text(_FLASK_APP, encoding="utf-8")
    res = start_preview_server(tmp_path, 8081)
    assert res["success"] is True, res
    assert res["mode"] == "flask"
    assert res["port"] >= 8081
    with urllib.request.urlopen(f"http://127.0.0.1:{res['port']}/api/ping", timeout=10) as r:
        assert r.status == 200
        assert json.loads(r.read())["pong"] is True
    # P1 : le port est bien au registre SSRF (browser_navigate passerait)
    assert is_preview_allowed("127.0.0.1", res["port"]) is True
    stop_preview_server()
    assert is_preview_allowed("127.0.0.1", res["port"]) is False


@pytest.mark.timeout(60)
def test_static_dir_still_served_statically(tmp_path):
    """Non-régression : sans app.py, comportement statique intact."""
    (tmp_path / "index.html").write_text("<html><body>STATIC_OK</body></html>",
                                         encoding="utf-8")
    res = start_preview_server(tmp_path, 8081)
    assert res["success"] is True, res
    assert res["mode"] == "static"
    with urllib.request.urlopen(f"http://127.0.0.1:{res['port']}/index.html", timeout=10) as r:
        assert b"STATIC_OK" in r.read()


@pytest.mark.timeout(60)
def test_broken_flask_app_fails_honestly(tmp_path):
    """Une app.py cassée ne donne plus un « success » aveugle sur un port mort."""
    (tmp_path / "app.py").write_text("from flask import Flask\ndef create_app(:\n",
                                     encoding="utf-8")
    res = start_preview_server(tmp_path, 8081)
    assert res["success"] is False
    assert "démarré" in res["error"]


def test_port_never_below_8081(tmp_path):
    """8080 = port réservé Lumena : un appel legacy port=8080 remonte à 8081+."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    res = start_preview_server(tmp_path, 8080)
    assert res["success"] is True, res
    assert res["port"] >= 8081
