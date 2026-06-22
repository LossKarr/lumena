"""Lot 2 — réception LISIBLE des artefacts (fini les chemins à codes/UUID).

Vérifie : strip du routage interne (`<date>/projet-…/`), dossier `recu-de-<pair>`,
et enregistrement dans le project_registry (retrouvable par find_project).
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from src.runtime import peer_artifacts as pa


# ── strip du préfixe de routage ───────────────────────────────────────────────

def test_strip_routing_prefix_date_et_projet():
    assert pa._strip_routing_prefix(
        "2026-06-18/projet-mission-ta-123/wok-nomade/index.html"
    ) == "wok-nomade/index.html"


def test_strip_routing_prefix_backslashes():
    assert pa._strip_routing_prefix(
        "2026-06-18\\projet-x\\memo\\notes.md"
    ) == "memo/notes.md"


def test_strip_routing_prefix_sans_prefixe():
    assert pa._strip_routing_prefix("wok-nomade/index.html") == "wok-nomade/index.html"


def test_strip_routing_prefix_fichier_seul():
    assert pa._strip_routing_prefix("rapport.md") == "rapport.md"


# ── dossier de réception lisible ──────────────────────────────────────────────

def test_reception_dir_lisible(monkeypatch, tmp_path):
    monkeypatch.setattr(pa, "WORKSPACE_DIR", tmp_path, raising=False)
    # patch via le module paths (reception_dir_for importe WORKSPACE_DIR localement)
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path)
    d = pa.reception_dir_for("Lumena-B")
    assert d.name == "recu-de-lumena-b"
    assert "inbound" not in str(d)
    # aucun code/UUID dans le chemin
    assert "ta-" not in d.name and "76fdb352" not in d.name


# ── réception bout-en-bout : zip avec routage interne → arborescence propre ────

def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_receive_strips_prefix_into_readable_tree(tmp_path):
    content = _zip_bytes({
        "2026-06-18/projet-mission-ta-abc/wok-nomade/index.html": "<html>",
        "2026-06-18/projet-mission-ta-abc/wok-nomade/css/style.css": "body{}",
        "2026-06-18/projet-mission-ta-abc/wok-nomade/js/script.js": "//js",
    })
    sha = pa.sha256_bytes(content)
    dest = tmp_path / "recu-de-lumena-b"
    out = pa.receive_artifact(content, kind="zip", filename="b.zip", expected_sha256=sha, dest_dir=dest)
    assert out["ok"] and out["count"] == 3
    assert (dest / "wok-nomade" / "index.html").is_file()
    assert (dest / "wok-nomade" / "css" / "style.css").is_file()
    assert (dest / "wok-nomade" / "js" / "script.js").is_file()
    # le préfixe daté/projet a bien disparu
    assert not (dest / "2026-06-18").exists()


# ── enregistrement dans le project_registry ───────────────────────────────────

def test_register_received_projects(tmp_path, monkeypatch):
    import src.utils.project_registry as reg
    monkeypatch.setattr(reg, "_REGISTRY_PATH", tmp_path / "registry.json")
    from src.runtime.peer_mission_tracker import _register_received_projects

    dest = tmp_path / "recu-de-lumena-b"
    f1 = dest / "wok-nomade" / "index.html"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("<html>", encoding="utf-8")

    _register_received_projects(dest, [str(f1)])
    # retrouvable par find_project (« reprends le projet wok-nomade »)
    found = reg.find_project("wok-nomade")
    assert found is not None and found.name == "wok-nomade"
