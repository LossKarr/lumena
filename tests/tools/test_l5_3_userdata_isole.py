"""Lot L5-3 - une instance peut recevoir son propre `userData`.

**PREREQUIS de la voie A, pas une suite** (reordonne le 16 septembre apres mesure).

--- Pourquoi ce lot, et pourquoi dans cet ordre ---

`electron/main.ts` l.679-680 prend `app.requestSingleInstanceLock()` et fait
`app.quit()` sinon. Ce verrou est **lie au repertoire `userData`** : tant que deux
instances le partagent, la seconde meurt au demarrage. **Aucune IDE de mission ne
peut donc exister avant que ce lot soit fait** - c'est pourquoi L5-3 precede
L5-3b (donner une instance a une mission) et L5-4 (canari).

Mesure du 16 septembre : **aucun `app.setPath` dans `electron/`** et **aucun
`--user-data-dir`** dans les deux depots.

--- Pourquoi par l'ENVIRONNEMENT et non par la ligne de commande ---

`_command` (`ide_launcher.py` l.264-267) rend `installation.command` BRUT en mode
`source` (l.265-266) : le workspace n'y est pas ajoute, il passe deja par
`LUMENA_IDE_WORKSPACE` dans l'environnement (l.186-187). Et **deux tests figent la
commande par egalite stricte de tuple** : `test_conn1b_ide_launcher.py` l.121
(`(exe, "--workspace=...")`) et l.405 (`("npm.cmd", "run", "start", "--")`).

Passer le repertoire par l'**environnement** est donc a la fois symetrique de
l'existant et **sans casse** : les deux gels restent intacts. C'est la raison du
choix, pas un detail d'implementation.

Le versant Electron (`app.setPath('userData', ...)` avant le verrou) est couvert par
`ide/tests/electron/l53UserDataIsolation.test.ts`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.tools.test_conn1b_ide_launcher import (
    _Discovery,
    _Probe,
    _Starter,
    _installation,
    _readiness,
)


def _launcher(tmp_path: Path, starter: _Starter, workspace: Path):
    from src.tools.ide_launcher import IDELauncherService

    return IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(
            _readiness(),
            _readiness(connected=True, handshake=True, workspace=workspace),
        ),
        process_starter=starter,
        sleep=lambda _delay: asyncio.sleep(0),
        timeout=1,
    )


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_repertoire_de_donnees_est_transmis_a_l_instance_lancee(tmp_path):
    """Sans lui, Electron refuse la seconde instance (verrou lie au userData)."""
    workspace = tmp_path / "projet"
    workspace.mkdir()
    profil = tmp_path / "profil-mission"
    starter = _Starter()

    result = await _launcher(tmp_path, starter, workspace).ensure_ready(
        workspace, user_data_dir=profil,
    )

    assert result.available is True
    _command, _cwd, environment = starter.calls[0]
    assert environment["LUMENA_IDE_USER_DATA"] == str(profil.resolve())


@pytest.mark.asyncio
async def test_la_commande_de_lancement_ne_change_pas(tmp_path):
    """Le repertoire passe par l'ENVIRONNEMENT : les deux gels d'egalite stricte
    sur la commande (`test_conn1b_ide_launcher` l.121 et l.405) restent intacts."""
    workspace = tmp_path / "projet"
    workspace.mkdir()
    starter = _Starter()

    await _launcher(tmp_path, starter, workspace).ensure_ready(
        workspace, user_data_dir=tmp_path / "profil-mission",
    )

    command, _cwd, _environment = starter.calls[0]
    assert "--user-data-dir" not in " ".join(command)
    assert command == (str(_installation(tmp_path / "ide").executable),
                       f"--workspace={workspace.resolve()}")


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
async def test_sans_repertoire_dedie_l_environnement_ne_gagne_aucune_cle(tmp_path):
    """Non-regression : le comportement par defaut est strictement celui d'avant."""
    workspace = tmp_path / "projet"
    workspace.mkdir()
    starter = _Starter()

    await _launcher(tmp_path, starter, workspace).ensure_ready(workspace)

    _command, _cwd, environment = starter.calls[0]
    assert "LUMENA_IDE_USER_DATA" not in environment
    assert environment["LUMENA_IDE_WORKSPACE"] == str(workspace.resolve())


@pytest.mark.asyncio
async def test_le_workspace_reste_transmis_avec_un_repertoire_dedie(tmp_path):
    """Les deux informations coexistent : profil isole ET dossier de travail."""
    workspace = tmp_path / "projet"
    workspace.mkdir()
    starter = _Starter()

    await _launcher(tmp_path, starter, workspace).ensure_ready(
        workspace, user_data_dir=tmp_path / "profil-mission",
    )

    _command, _cwd, environment = starter.calls[0]
    assert environment["LUMENA_IDE_WORKSPACE"] == str(workspace.resolve())
