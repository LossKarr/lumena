"""A4 (Phase A, run FitLog) — garde anti-boucle HONNÊTE.

Le run FitLog est mort à 08:12:27 : « ⚠️ Fichier  écrit 7 fois - arrêt de la
boucle » — path VIDE (params manquants → '' substring de tout l'historique),
échecs comptés comme écritures, et un FAUX succès fabriqué (« ✅ créé avec
succès après 7 tentatives ») émis SANS chokepoint.
Verrous : path vide ignoré ; arrêt honnête via chokepoint si le fichier est
réellement au ledger ; échec honnête à ≥6 sinon ; hint anti-troncature sur
« params manquants » ; exit code ≠ 0 visible EN TÊTE de l'observation run_command.
"""
from __future__ import annotations

import inspect

import pytest


# ── 1. structurels : le bloc react.py ne peut plus mentir ───────────────────────

def _react_src() -> str:
    import src.reasoning.react as react_mod
    return inspect.getsource(react_mod)


def test_no_fabricated_success_message():
    src = _react_src()
    assert "créé avec succès après" not in src, (
        "le garde anti-boucle ne doit JAMAIS fabriquer un succès"
    )


def test_empty_path_not_counted():
    src = _react_src()
    # le comptage n'existe que sous `if target_path:` (path vide = pas une écriture)
    i = src.find("stop_on_repeated_write_file")
    assert i > 0
    block = src[max(0, i - 2500):i]
    assert "if target_path:" in block


def test_honest_stop_goes_through_chokepoint():
    src = _react_src()
    i = src.find("stop_on_repeated_write_file")
    block = src[i:i + 1200]
    assert "_stream_and_return_final" in block, (
        "l'arrêt de boucle doit passer par le chokepoint (truth-lock)"
    )


def test_repeated_failures_end_as_failure():
    src = _react_src()
    assert "repeated_write_failures" in src
    i = src.find("repeated_write_failures")
    block = src[i:i + 900]
    assert "_mark_task_failed" in block


def test_ledger_written_basenames_consulted():
    src = _react_src()
    i = src.find("stop_on_repeated_write_file")
    block = src[max(0, i - 2500):i + 500]
    assert "written_basenames" in block, (
        "le garde doit distinguer « écrit au ledger » de « tentatives ratées »"
    )


# ── 2. hint anti-troncature sur params manquants ────────────────────────────────

@pytest.mark.asyncio
async def test_missing_params_hint_truncation_for_write_tools():
    from src.reasoning.tool_registry import ToolRegistry

    reg = ToolRegistry(lumena=None)
    obs = await reg.execute("write_file", {})
    assert not obs.success
    assert "Paramètre(s) requis manquant(s)" in obs.content
    assert "TRONQUÉE" in obs.content
    assert "edit_file" in obs.content  # guidance de découpage


@pytest.mark.asyncio
async def test_missing_params_no_truncation_hint_for_other_tools():
    from src.reasoning.tool_registry import ToolRegistry

    reg = ToolRegistry(lumena=None)
    obs = await reg.execute("read_file", {})
    assert not obs.success
    assert "TRONQUÉE" not in obs.content


# ── 3. exit code visible dans run_command ───────────────────────────────────────

@pytest.mark.asyncio
async def test_run_command_nonzero_exit_marked(tmp_path):
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.system import run_command_handler

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    r = await run_command_handler(ctx, command="exit 4")
    assert r.success  # l'outil a fait son travail (Fix AV)
    # ... mais l'échec de la COMMANDE est dit en clair
    assert "ÉCHEC de la commande" in r.output or "exit code 4" in r.output


@pytest.mark.asyncio
async def test_run_command_zero_exit_unmarked(tmp_path):
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.system import run_command_handler

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    r = await run_command_handler(ctx, command="echo tout_va_bien")
    assert r.success
    assert "tout_va_bien" in r.output
    assert "ÉCHEC de la commande" not in r.output
