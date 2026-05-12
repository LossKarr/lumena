"""Tests Phase -1 — Legacy inventory (dry-run, attribution, stabilité manifeste)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.legacy_inventory import (
    LEGACY_OWNER,
    run_inventory,
    run_and_write,
    write_inventory,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_tree(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    workspace_dir = tmp_path / "workspace"
    # quelques fichiers legacy
    (data_dir / "memory").mkdir(parents=True)
    (data_dir / "memory" / "facts.json").write_text('{"foo": 1}', encoding="utf-8")
    (data_dir / "tg_contexts").mkdir(parents=True)
    (data_dir / "tg_contexts" / "12345.json").write_text("[]", encoding="utf-8")
    (data_dir / "journal.json").write_text("[]", encoding="utf-8")
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "2026-01-01").mkdir()
    (workspace_dir / "2026-01-01" / "note.md").write_text("hello", encoding="utf-8")
    return data_dir, workspace_dir


# ── test 1 : dry-run ne modifie aucun fichier ─────────────────────────────────


def test_dry_run_does_not_modify_files(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "migration"

    before_files = {
        str(p): p.read_bytes()
        for p in data_dir.rglob("*")
        if p.is_file()
    }

    inventory = run_inventory(data_dir, workspace_dir, dry_run=True)
    write_inventory(inventory, migration_dir)

    after_files = {
        str(p): p.read_bytes()
        for p in data_dir.rglob("*")
        if p.is_file()
    }

    assert before_files == after_files, "dry-run a modifié des fichiers existants"


# ── test 2 : tout l'existant est assigné à local:owner ────────────────────────


def test_all_existing_entries_assigned_to_local_owner(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)

    inventory = run_inventory(data_dir, workspace_dir, dry_run=True)

    existing = [e for e in inventory["entries"] if e["exists"]]
    assert existing, "aucune entrée existante détectée"
    for entry in existing:
        assert entry["owner_user_id"] == LEGACY_OWNER, (
            f"{entry['path']} n'est pas assigné à {LEGACY_OWNER}"
        )


# ── test 3 : le manifeste ne contient que les entrées existantes ───────────────


def test_manifest_contains_only_existing_entries(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "migration"

    inventory = run_inventory(data_dir, workspace_dir, dry_run=True)
    paths = write_inventory(inventory, migration_dir)

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    existing_paths = {e["path"] for e in inventory["entries"] if e["exists"]}
    manifest_paths = {a["path"] for a in manifest["assignments"]}

    assert manifest_paths == existing_paths


# ── test 4 : le manifeste est stable (idempotent) ─────────────────────────────


def test_manifest_is_stable_across_runs(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "migration"

    inv1 = run_inventory(data_dir, workspace_dir, dry_run=True)
    p1 = write_inventory(inv1, migration_dir)
    manifest1 = json.loads(p1["manifest"].read_text(encoding="utf-8"))

    inv2 = run_inventory(data_dir, workspace_dir, dry_run=True)
    p2 = write_inventory(inv2, migration_dir)
    manifest2 = json.loads(p2["manifest"].read_text(encoding="utf-8"))

    assert manifest1["assignments"] == manifest2["assignments"]
    assert manifest1["legacy_owner"] == manifest2["legacy_owner"]


# ── test 5 : les artefacts sont créés dans migration_dir ──────────────────────


def test_write_inventory_creates_all_artifacts(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "migration"

    inventory = run_inventory(data_dir, workspace_dir, dry_run=True)
    written = write_inventory(inventory, migration_dir)

    assert written["inventory"].exists()
    assert written["manifest"].exists()
    assert written["report"].exists()


# ── test 6 : migration_applied=False dans le manifeste ────────────────────────


def test_manifest_migration_not_applied(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "migration"

    inventory = run_inventory(data_dir, workspace_dir, dry_run=True)
    paths = write_inventory(inventory, migration_dir)

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["migration_applied"] is False


# ── test 7 : run_and_write fonctionne avec des chemins custom ─────────────────


def test_run_and_write_custom_paths(tmp_path):
    data_dir, workspace_dir = _make_tree(tmp_path)
    migration_dir = tmp_path / "mig_out"

    result = run_and_write(
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        migration_dir=migration_dir,
        dry_run=True,
    )

    assert "_written_to" in result
    assert Path(result["_written_to"]["manifest"]).exists()
    assert result["legacy_owner"] == LEGACY_OWNER
