"""Lot L3-2 - un refus de `run_command` est un ECHEC, pas un succes.

Mesure du 16 septembre 2026 (lecture du ledger, avant tout code) :

    ExecutionLedger._mutation(entry) == entry.success and entry.action in MUTATION_TOOLS

et `run_command` EST dans `MUTATION_TOOLS`. Or ses refus etaient rendus par
`HandlerResult.ok("⛔ ...")`, donc avec `success=True` : **le ledger enregistrait une
mutation REUSSIE la ou la commande avait ete refusee**. Consequences : `has_any_mutation()`
devient vrai sans qu'aucune commande n'ait tourne, ce qui alimente le truth-lock et les
gardes anti-boucle avec un faux positif. C'est le motif C0.2, deja corrige jadis pour
`write_file` (run FrigoZen) - jamais pour `run_command`.

Perimetre EXACT, mesure : **2 endroits** (`system.py:396` refus du juge de commandes,
`system.py:548` refus de dossier - ce dernier ecrit par moi en L2-1). `process_run` et
`bg_start` ne sont PAS des `MUTATION_TOOLS` : leurs refus-en-succes sont cosmetiques et
restent documentes, pas corriges (pas de correction en masse sans degat prouve).

Ce que ce lot ne change pas : le message de refus reste visible pour le modele, et une
commande autorisee reste un succes.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.system import run_command_handler
from src.runtime.execution_ledger import MUTATION_TOOLS
from src.tools.file_guardrails import OutsideAccessGrant, WorkspaceFileGuardrails


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    (ws / "projet").mkdir(parents=True)
    (root / "src").mkdir()
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    return {"root": root, "ws": ws}


def _ctx(m) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["ws"])
    ctx.outside_access_grant = OutsideAccessGrant.none()
    return ctx


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

def test_run_command_est_compte_comme_mutation_par_le_ledger():
    """Caracterisation : c'est CE fait qui rend un refus-en-succes nuisible."""
    assert "run_command" in MUTATION_TOOLS


# ── 2. Un refus doit etre un echec ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_refus_de_dossier_est_un_echec(monde):
    """`system.py:548` - refus introduit par L2-1 (dossier dans le depot)."""
    m = monde
    r = await run_command_handler(_ctx(m), command="echo test", cwd=str(m["root"] / "src"))
    assert r.success is False, "un refus enregistre comme succes pollue le ledger"
    assert "⛔" in str(r.output) or "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_refus_du_juge_de_commandes_est_un_echec(monde):
    """`system.py:396` - refus du sanitizer (commande dangereuse)."""
    m = monde
    r = await run_command_handler(_ctx(m), command="rm -rf /",
                                  cwd=str(m["ws"] / "projet"))
    assert r.success is False
    assert "⛔" in str(r.output) or "bloqu" in str(r.output).lower()


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
async def test_le_motif_du_refus_reste_lisible(monde):
    """Le modele doit toujours savoir POURQUOI : un echec muet serait pire."""
    m = monde
    r = await run_command_handler(_ctx(m), command="echo test", cwd=str(m["root"] / "src"))
    texte = str(r.output) + str(getattr(r, "error", "") or "")
    assert "workspace" in texte.lower(), texte[:200]


@pytest.mark.asyncio
async def test_commande_autorisee_reste_un_succes(monde):
    """Caracterisation : le chemin normal ne bouge pas."""
    m = monde
    r = await run_command_handler(_ctx(m), command="echo bonjour",
                                  cwd=str(m["ws"] / "projet"))
    assert r.success is True
    assert "bonjour" in str(r.output).lower()
