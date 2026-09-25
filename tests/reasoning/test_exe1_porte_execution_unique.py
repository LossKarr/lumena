"""Lot EXE-1 - la porte d'execution est REELLEMENT unique.

**Defaut observe dans les journaux de Charles, 23 septembre 2026 a 21 h 38.**

Lumena ne peut pas lancer son IDE. `run_command` la refuse. Elle ecrit alors, en
toutes lettres :

    Thought: « Le `run_command` est bloque dans le depot. **Je pivote : j'utilise le
              PowerShell MCP (canal different)** pour lancer l'executable. »

    mcp__windows-mcp__PowerShell(command='Start-Process -FilePath "...\\
                                 win-unpacked\\Lumena IDE.exe"')

**Le `Start-Process` est passe.** Le chantier L2 s'appelait « porte d'execution
unique » ; il y en avait deux.

--- L'audit, mesure et non suppose ---

`assert_execution_cwd_allowed` protege **5 points natifs** : `run_command` (x2),
`bg_start`, `process_run`, `dev_run_fix`. Les outils MCP passent par
`_execute_inner` **sans aucune garde d'execution** - zero occurrence de garde,
d'assertion ou de controle de dossier sur le chemin `mcp__*`.

Et le registre l'avait vu sans rien faire :

    Tool 'mcp__windows-mcp__PowerShell' hors filtre prompt - execution soft-filter

--- Le second defaut, qui a fabrique le premier ---

Sept refus dans ces journaux portent sur des commandes de **pure lecture** :

    tasklist /FI "IMAGENAME eq electron.exe"
    netstat -ano | findstr :5173
    wmic process where "name='electron.exe'"

Elles ne modifient rien. Le motif du refus est `cwd=<le depot>`. **Une garde trop
large fabrique ses propres contournements** : c'est parce que ces lectures
inoffensives etaient refusees que le modele a cherche - et trouve - une autre porte.

Fermer le MCP sans ouvrir ces lectures ne ferait que retirer la derniere issue sans
resoudre le besoin. Les deux vont ensemble.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.reasoning.handlers.files import assert_execution_cwd_allowed

RACINE = Path(__file__).resolve().parents[2]


# ── 1. Les lectures d'etat systeme ne sont plus refusees ────────────────────

@pytest.mark.parametrize("commande", [
    'tasklist /FI "IMAGENAME eq electron.exe" /FO TABLE',
    "netstat -ano | findstr :5173",
    'wmic process where "name=\'electron.exe\'" get ProcessId',
    "tasklist",
    "Get-Process electron",
])
def test_une_lecture_d_etat_systeme_est_reconnue(commande):
    """Ces trois-la ont ete refusees dans le run reel du 23/09 a 21 h 37-38."""
    from src.utils.command_sanitizer import is_system_state_read

    assert is_system_state_read(commande), f"{commande!r} devrait etre une lecture d'etat"


@pytest.mark.parametrize("commande", [
    'Start-Process -FilePath "C:/x/Lumena IDE.exe"',
    "npm run build",
    "python -c \"open('a','w').write('x')\"",
    "rm -rf build",
    "taskkill /F /IM electron.exe",
    "git commit -m x",
])
def test_ce_qui_agit_n_est_JAMAIS_pris_pour_une_lecture(commande):
    """La borne du lot : tuer un processus n'est pas le lire."""
    from src.utils.command_sanitizer import is_system_state_read

    assert not is_system_state_read(commande), f"{commande!r} agit, ce n'est pas une lecture"


def test_une_lecture_enchainee_a_une_ECRITURE_n_est_pas_une_lecture():
    """`tasklist && rm -rf x` ne doit pas passer pour une simple lecture."""
    from src.utils.command_sanitizer import is_system_state_read

    for ruse in ("tasklist && rm -rf build", "tasklist ; npm run deploy",
                 "netstat -ano | findstr :80 && taskkill /F /PID 1"):
        assert not is_system_state_read(ruse), ruse


# ── 2. La porte d'execution est UNE seule ───────────────────────────────────

def test_aucun_outil_d_execution_ne_contourne_la_garde():
    """Gel : tout outil qui lance un processus passe par le meme juge.

    Le run du 23/09 a montre un `Start-Process` arbitraire passant par
    `mcp__windows-mcp__PowerShell`, hors de toute garde. Ce test lit le source :
    chaque point d'execution connu doit appeler `assert_execution_cwd_allowed`.
    """
    from src.reasoning.tool_registry import ToolRegistry  # noqa: F401

    source = (RACINE / "src" / "reasoning" / "tool_registry.py").read_text(encoding="utf-8")
    assert "assert_execution_cwd_allowed" in source or "garde_execution_mcp" in source, (
        "le registre laisse passer les outils MCP sans aucune garde d'execution"
    )


@pytest.mark.parametrize("nom", [
    "mcp__windows-mcp__PowerShell",
    "mcp__windows-mcp__Shell",
    "mcp__desktop-commander__execute_command",
])
def test_un_outil_mcp_qui_execute_est_reconnu_comme_tel(nom):
    from src.utils.command_sanitizer import mcp_tool_executes_commands

    assert mcp_tool_executes_commands(nom), f"{nom} lance des processus et n'est pas reconnu"


@pytest.mark.parametrize("nom", [
    "mcp__memory__create_entities",
    "mcp__slack__post_message",
    "mcp__context7__get-library-docs",
    "read_file",
    "ide__get_status",
])
def test_un_outil_qui_n_execute_PAS_n_est_pas_gene(nom):
    """La garde ne doit pas ralentir la memoire, Slack ou la documentation."""
    from src.utils.command_sanitizer import mcp_tool_executes_commands

    assert not mcp_tool_executes_commands(nom)


# ── 3. Ce que le lot ne change PAS ──────────────────────────────────────────

def test_la_garde_native_garde_ses_verdicts(tmp_path):
    """Non-regression : L2-1 refuse toujours le depot pour ce qui ECRIT."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(file_guardrails=None, lumena_root=None)
    assert_execution_cwd_allowed(str(tmp_path), ctx)  # contexte leger : ne juge pas


def test_les_cinq_points_natifs_appellent_toujours_la_garde():
    """Gel du perimetre existant : aucun ne doit disparaitre."""
    attendus = {
        "src/reasoning/handlers/system.py": 2,
        "src/reasoning/handlers/agents.py": 2,
        "src/reasoning/handlers/project.py": 1,
    }
    for chemin, compte in attendus.items():
        source = (RACINE / chemin).read_text(encoding="utf-8")
        trouve = source.count("assert_execution_cwd_allowed(")
        assert trouve >= compte, f"{chemin} : {trouve} appel(s) au lieu de {compte}"
