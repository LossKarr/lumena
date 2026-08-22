"""A3 (Phase A, run FitLog) — tests contractuels intouchables.

Le lead FitLog, face à des tests contractuels qui ne collaient pas à son code
réinventé, a « adapté les tests à l'implémentation » — le mauvais sens (le contrat
est la vérité). Verrou : quiconque écrit SANS périmètre allowed_files (= le lead)
ne peut plus muter un test déclaré au contract.json — ni dans le dossier mission,
ni sa copie publiée (basename). L'OWNER (w_tests, avec allowed_files) reste libre
de remplir ses stubs.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import (
    delete_file_handler,
    edit_file_handler,
    write_file_handler,
)
from src.tools.file_guardrails import WorkspaceFileGuardrails

_CONTRACT = (
    '{"project": "fitlog", "files": ['
    '{"path": "storage.py", "owner": "w_storage", "api": ["def add() -> int"]},'
    '{"path": "test_storage.py", "owner": "w_tests"},'
    '{"path": "tests/test_stats.py", "owner": "w_tests"}]}'
)


def _mission_dir(tmp_path):
    d = tmp_path / "missions" / "task_a3"
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "contract.json").write_text(_CONTRACT, encoding="utf-8")
    (d / "storage.py").write_text("def add(): return 1\n", encoding="utf-8")
    (d / "test_storage.py").write_text("def test_add(): assert True\n", encoding="utf-8")
    (d / "tests" / "test_stats.py").write_text("def test_s(): assert True\n", encoding="utf-8")
    return d


def _ctx(tmp_path, *, allowed_files=None, in_mission=True):
    kw = {}
    if in_mission:
        kw = dict(is_mission_run=True, runtime_task_id="task_a3",
                  mission_workspace="missions/task_a3")
        if allowed_files:
            kw["mission_allowed_files"] = list(allowed_files)
    return HandlerContext(
        lumena_root=tmp_path, runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path), **kw)


@pytest.mark.asyncio
async def test_lead_cannot_rewrite_contract_test(tmp_path):
    _mission_dir(tmp_path)
    ctx = _ctx(tmp_path)  # lead : en mission, SANS allowed_files
    r = await write_file_handler(ctx, path="test_storage.py",
                                 content="def test_add(): assert False\n",
                                 force_rewrite=True, rewrite_reason="adapter")
    assert "TEST CONTRACTUEL" in r.output
    # le fichier n'a pas bougé
    assert "assert True" in (tmp_path / "missions" / "task_a3" / "test_storage.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lead_cannot_edit_nested_contract_test(tmp_path):
    _mission_dir(tmp_path)
    ctx = _ctx(tmp_path)
    r = await edit_file_handler(ctx, file_path="tests/test_stats.py",
                                old_content="def test_s(): assert True\n",
                                new_content="def test_s(): assert 1 == 2\n")
    assert "TEST CONTRACTUEL" in r.output


@pytest.mark.asyncio
async def test_lead_cannot_delete_contract_test(tmp_path):
    _mission_dir(tmp_path)
    ctx = _ctx(tmp_path)
    r = await delete_file_handler(ctx, path="test_storage.py")
    assert "TEST CONTRACTUEL" in r.output
    assert (tmp_path / "missions" / "task_a3" / "test_storage.py").is_file()


@pytest.mark.asyncio
async def test_lead_can_mutate_code_files(tmp_path):
    """Le bon sens du contrat : le lead corrige le CODE, librement."""
    _mission_dir(tmp_path)
    ctx = _ctx(tmp_path)
    r = await edit_file_handler(ctx, file_path="storage.py",
                                old_content="def add(): return 1\n",
                                new_content="def add(): return 2\n")
    assert r.success, r.output
    assert "TEST CONTRACTUEL" not in r.output


@pytest.mark.asyncio
async def test_owner_worker_fills_its_test_stub(tmp_path):
    """w_tests (allowed_files) remplit SES tests — non concerné par A3."""
    _mission_dir(tmp_path)
    ctx = _ctx(tmp_path, allowed_files=["test_storage.py", "tests/test_stats.py"])
    r = await write_file_handler(ctx, path="test_storage.py",
                                 content="def test_add(): assert 2 == 2\n",
                                 force_rewrite=True, rewrite_reason="remplir le stub")
    assert r.success, r.output


@pytest.mark.asyncio
async def test_published_copy_protected_by_basename(tmp_path):
    """La copie PUBLIÉE d'un test contractuel est protégée aussi (le lead FitLog
    réécrivait les tests dans workspace/fitlog/)."""
    _mission_dir(tmp_path)
    (tmp_path / "fitlog").mkdir()
    (tmp_path / "fitlog" / "test_storage.py").write_text(
        "def test_add(): assert True\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    r = await write_file_handler(ctx, path="fitlog/test_storage.py",
                                 content="def test_add(): assert False\n",
                                 force_rewrite=True, rewrite_reason="adapter")
    assert "TEST CONTRACTUEL" in r.output


@pytest.mark.asyncio
async def test_no_contract_lead_free(tmp_path):
    """Sans contract.json : aucun verrou (missions non-code intactes)."""
    d = tmp_path / "missions" / "task_a3"
    d.mkdir(parents=True)
    (d / "test_libre.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    r = await write_file_handler(ctx, path="test_libre.py",
                                 content="def test_x(): assert 3 == 3\n",
                                 force_rewrite=True, rewrite_reason="maj")
    assert r.success, r.output


@pytest.mark.asyncio
async def test_out_of_mission_free(tmp_path):
    """Hors mission : un fichier test_* quelconque reste libre."""
    (tmp_path / "test_storage.py").write_text("def test_a(): assert True\n", encoding="utf-8")
    ctx = _ctx(tmp_path, in_mission=False)
    r = await write_file_handler(ctx, path="test_storage.py",
                                 content="def test_a(): assert 4 == 4\n",
                                 force_rewrite=True, rewrite_reason="maj")
    assert r.success, r.output
    assert "TEST CONTRACTUEL" not in r.output
