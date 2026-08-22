"""Brique 4 — tests du module artefacts (manifeste, empaquetage, réception sandboxée)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from src.runtime import peer_artifacts as pa


def _mkfile(p: Path, content: bytes = b"hello"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# ── Capture des fichiers produits (historique ReAct) ─────────────────────────

class _Action:
    def __init__(self, tool_name, tool_args):
        self.tool_name = tool_name
        self.tool_args = tool_args

class _Obs:
    def __init__(self, content):
        self.content = content

class _Step:
    def __init__(self, tool_name, tool_args=None, obs=""):
        self.action = _Action(tool_name, tool_args or {})
        self.observation = _Obs(obs)


def test_extract_created_files_whitelist_and_existence(tmp_path):
    produced = _mkfile(tmp_path / "site" / "index.html", b"<html>")
    read_only = _mkfile(tmp_path / "source.txt", b"data")
    history = [
        _Step("write_file", {"path": str(produced)}),          # créé → gardé
        _Step("read_file", {"path": str(read_only)}),          # lecture → ignoré
        _Step("list_directory", {"path": str(tmp_path)}),       # ignoré
        _Step("create_pdf", {"filename": "absent.pdf"}),        # n'existe pas → ignoré
    ]
    files = pa.extract_created_files(history, base_dir=tmp_path)
    assert files == [str(produced.resolve())]


def test_extract_created_files_from_observation(tmp_path):
    produced = _mkfile(tmp_path / "out" / "rapport.pdf", b"%PDF")
    history = [
        _Step("create_pdf", {"filename": "rapport.pdf"},
              obs=f'✅ PDF créé : {produced}'),
    ]
    files = pa.extract_created_files(history, base_dir=tmp_path)
    assert str(produced.resolve()) in files


def test_extract_created_files_from_document_studio_and_csv(tmp_path):
    pdf = _mkfile(tmp_path / "documents" / "aquawatch.pdf", b"%PDF")
    csv = _mkfile(tmp_path / "exports" / "aquawatch.csv", b"date,litres\n")
    history = [
        _Step("generate_studio_document", {}, obs=f"Document genere: {pdf}"),
        _Step("create_csv", {"output_path": str(csv)}),
    ]

    assert set(pa.extract_created_files(history, base_dir=tmp_path)) == {
        str(pdf.resolve()),
        str(csv.resolve()),
    }


def test_persist_created_files_merges_task_metadata(tmp_path):
    first = _mkfile(tmp_path / "documents" / "first.pdf", b"%PDF-1")
    second = _mkfile(tmp_path / "documents" / "second.pdf", b"%PDF-2")

    class _Orchestrator:
        def __init__(self):
            self.metadata = {"artifacts": [str(first.resolve())]}
            self.updates = []

        def get_task(self, task_id):
            return {"metadata": dict(self.metadata)} if task_id == "task_1" else None

        def set_task_metadata(self, task_id, **values):
            self.metadata.update(values)
            self.updates.append((task_id, values))

    orch = _Orchestrator()
    added = pa.persist_created_files(
        orch,
        "task_1",
        [_Step("generate_studio_document", {}, obs=f"Document genere: {second}")],
        base_dir=tmp_path,
    )

    assert added == [str(second.resolve())]
    assert orch.metadata["artifacts"] == [str(first.resolve()), str(second.resolve())]
    assert len(orch.updates) == 1


def test_extract_ignores_files_outside_workspace(tmp_path):
    outside = _mkfile(tmp_path.parent / "elsewhere.txt", b"x")
    try:
        history = [_Step("write_file", {"path": str(outside)})]
        files = pa.extract_created_files(history, base_dir=tmp_path)
        assert files == []   # hors workspace → exclu
    finally:
        outside.unlink(missing_ok=True)


# ── Capture par snapshot disque (indépendant des outils) ─────────────────────

def test_snapshot_diff_detects_new_files(tmp_path):
    _mkfile(tmp_path / "ancien.txt", b"old")
    before = pa.snapshot_workspace(tmp_path)
    # Simule une mission qui crée 2 fichiers via N'IMPORTE quel outil
    # (parallel_tools, CodeAgent…) — on ne regarde que le disque.
    _mkfile(tmp_path / "site" / "index.html", b"<html>")
    _mkfile(tmp_path / "site" / "style.css", b"body{}")
    after = pa.snapshot_workspace(tmp_path)
    produced = pa.diff_workspace(before, after)
    assert str((tmp_path / "site" / "index.html").resolve()) in produced
    assert str((tmp_path / "site" / "style.css").resolve()) in produced
    assert str((tmp_path / "ancien.txt").resolve()) not in produced  # pré-existant → ignoré


def test_snapshot_diff_detects_modified_file(tmp_path):
    f = _mkfile(tmp_path / "a.txt", b"v1")
    before = pa.snapshot_workspace(tmp_path)
    f.write_bytes(b"v2-plus-long")  # taille change
    after = pa.snapshot_workspace(tmp_path)
    assert str(f.resolve()) in pa.diff_workspace(before, after)


def test_snapshot_excludes_inbound(tmp_path):
    # Les artefacts REÇUS d'autres pairs ne doivent pas être re-capturés.
    before = pa.snapshot_workspace(tmp_path)
    recu = _mkfile(tmp_path / "inbound" / "peerX" / "task1" / "recu.txt", b"data")
    _mkfile(tmp_path / "livrable.txt", b"out")
    produced = pa.diff_workspace(before, pa.snapshot_workspace(tmp_path))
    assert str((tmp_path / "livrable.txt").resolve()) in produced
    assert str(recu.resolve()) not in produced  # artefact reçu → jamais re-capturé


def test_snapshot_empty_when_no_change(tmp_path):
    _mkfile(tmp_path / "x.txt", b"x")
    before = pa.snapshot_workspace(tmp_path)
    assert pa.diff_workspace(before, pa.snapshot_workspace(tmp_path)) == []


# ── Manifeste ────────────────────────────────────────────────────────────────

def test_build_manifest(tmp_path):
    f1 = _mkfile(tmp_path / "site" / "index.html", b"<html>")
    f2 = _mkfile(tmp_path / "site" / "style.css", b"body{}")
    man = pa.build_manifest([str(f1), str(f2), "inexistant.txt"], base_dir=tmp_path)
    assert len(man) == 2
    names = {m["filename"] for m in man}
    assert names == {"index.html", "style.css"}
    m0 = man[0]
    assert m0["sha256"] and m0["size"] > 0 and m0["mime"]
    assert m0["rel_path"].startswith("site/")  # arborescence préservée


def test_build_manifest_dedup(tmp_path):
    f1 = _mkfile(tmp_path / "a.txt")
    man = pa.build_manifest([str(f1), str(f1)], base_dir=tmp_path)
    assert len(man) == 1


def test_public_manifest_strips_abs(tmp_path):
    f1 = _mkfile(tmp_path / "a.txt")
    man = pa.build_manifest([str(f1)], base_dir=tmp_path)
    pub = pa.public_manifest(man)
    assert "_abs" not in pub[0]
    assert "_abs" in man[0]


# ── Empaquetage ──────────────────────────────────────────────────────────────

def test_prepare_bundle_single_is_raw(tmp_path):
    f1 = _mkfile(tmp_path / "rapport.pdf", b"%PDF")
    man = pa.build_manifest([str(f1)], base_dir=tmp_path)
    bundle = pa.prepare_bundle(man, task_id="t1", out_dir=tmp_path / "out")
    assert bundle["kind"] == "raw"
    assert bundle["filename"] == "rapport.pdf"
    assert bundle["count"] == 1


def test_prepare_bundle_multi_is_zip(tmp_path):
    f1 = _mkfile(tmp_path / "site" / "index.html", b"<html>")
    f2 = _mkfile(tmp_path / "site" / "app.js", b"console.log(1)")
    man = pa.build_manifest([str(f1), str(f2)], base_dir=tmp_path)
    bundle = pa.prepare_bundle(man, task_id="t1", out_dir=tmp_path / "out")
    assert bundle["kind"] == "zip"
    assert bundle["count"] == 2
    # Le zip contient bien les deux, arborescence préservée.
    with zipfile.ZipFile(bundle["path"]) as zf:
        names = set(zf.namelist())
    assert "site/index.html" in names and "site/app.js" in names


def test_prepare_bundle_empty(tmp_path):
    assert pa.prepare_bundle([], task_id="t", out_dir=tmp_path) is None


# ── Réception : ZIP → workspace ──────────────────────────────────────────────

def _make_zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_receive_zip_places_files(tmp_path):
    content = _make_zip({"site/index.html": b"<html>", "site/style.css": b"body{}"})
    dest = tmp_path / "inbound" / "peerB" / "t1"
    out = pa.receive_artifact(content, kind="zip", filename="t1.zip",
                              expected_sha256=pa.sha256_bytes(content), dest_dir=dest)
    assert out["ok"] and out["count"] == 2
    assert (dest / "site" / "index.html").read_bytes() == b"<html>"


def test_receive_zip_rejects_zip_slip(tmp_path):
    content = _make_zip({"../evil.txt": b"x"})
    out = pa.receive_artifact(content, kind="zip", filename="z.zip",
                              expected_sha256="", dest_dir=tmp_path / "in")
    assert not out["ok"] and "unsafe_member" in out["error"]


def test_receive_zip_allows_any_type_by_default(tmp_path):
    # Par défaut : n'importe quel type passe (on n'exécute jamais).
    content = _make_zip({"data.exe": b"MZ", "doc.pdf": b"%PDF"})
    out = pa.receive_artifact(content, kind="zip", filename="z.zip",
                              expected_sha256="", dest_dir=tmp_path / "in")
    assert out["ok"] and out["count"] == 2


def test_receive_zip_blocks_exec_when_option_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_ARTIFACT_BLOCK_EXEC", "1")
    content = _make_zip({"malware.exe": b"MZ"})
    out = pa.receive_artifact(content, kind="zip", filename="z.zip",
                              expected_sha256="", dest_dir=tmp_path / "in")
    assert not out["ok"]


def test_receive_rejects_hash_mismatch(tmp_path):
    content = _make_zip({"a.txt": b"x"})
    out = pa.receive_artifact(content, kind="zip", filename="z.zip",
                              expected_sha256="deadbeef", dest_dir=tmp_path / "in")
    assert not out["ok"] and out["error"] == "hash_mismatch"


def test_receive_bad_zip(tmp_path):
    out = pa.receive_artifact(b"not a zip", kind="zip", filename="z.zip",
                              expected_sha256="", dest_dir=tmp_path / "in")
    assert not out["ok"] and out["error"] == "bad_zip"


# ── Réception : fichier brut ─────────────────────────────────────────────────

def test_receive_raw_file(tmp_path):
    content = b"%PDF-1.4 ..."
    dest = tmp_path / "inbound" / "peerB" / "t2"
    out = pa.receive_artifact(content, kind="raw", filename="rapport.pdf",
                              expected_sha256=pa.sha256_bytes(content), dest_dir=dest)
    assert out["ok"] and out["count"] == 1
    assert (dest / "rapport.pdf").read_bytes() == content


def test_receive_raw_any_type_by_default(tmp_path):
    # Tout type accepté par défaut (zip anti-slip + hash + taille restent actifs).
    dest = tmp_path / "in"
    out = pa.receive_artifact(b"MZ", kind="raw", filename="outil.exe",
                              expected_sha256="", dest_dir=dest)
    assert out["ok"] and (dest / "outil.exe").exists()


def test_receive_raw_blocks_exec_when_option_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_ARTIFACT_BLOCK_EXEC", "1")
    out = pa.receive_artifact(b"MZ", kind="raw", filename="virus.exe",
                              expected_sha256="", dest_dir=tmp_path / "in")
    assert not out["ok"] and out["error"] == "blocked_extension"


def test_receive_raw_strips_path(tmp_path):
    # Un nom avec chemin → on ne garde que le basename (pas d'évasion).
    content = b"data"
    dest = tmp_path / "in"
    out = pa.receive_artifact(content, kind="raw", filename="../../etc/passwd",
                              expected_sha256="", dest_dir=dest)
    assert out["ok"]
    assert (dest / "passwd").exists()


# ── Emplacement workspace ────────────────────────────────────────────────────

def test_inbound_dir_under_workspace():
    from src.utils.paths import WORKSPACE_DIR
    d = pa.inbound_dir_for("peer-B 123", "ta-xyz")
    assert str(d).startswith(str(WORKSPACE_DIR))
    assert "inbound" in str(d)
