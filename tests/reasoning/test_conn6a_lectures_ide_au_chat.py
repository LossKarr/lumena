"""Lot CONN-6a - les lectures de l'IDE arrivent au chat, et rien d'autre.

Mesure du 23 septembre 2026 : sur les **135 commandes auditees**, **41 sont en
lecture pure sans confirmation** (`READ_ONLY` + `Confirmation.NEVER`). Hors mission,
la garde n'en laisse passer que **2**, par une liste EN DUR de deux noms :

    elif (name not in {"ide__get_status", "ide__get_state"}
            or prepared.semantics.effect is not ToolEffect.READ_ONLY
            or prepared.semantics.confirmation is not Confirmation.NEVER):
        raise ExternalToolError("ide_host_authorization_not_connected")

**39 lectures sont donc refusees sans rien proteger.** Le commentaire de cette garde
l'assume : « CONN-4 must authorize scoped targets, leases and host confirmations
before opening other effects » - c'est un fail-closed pose EN ATTENDANT CONN-6. Pour
un effet, le raisonnement tient. Pour une lecture pure sans confirmation, il n'y a
**aucun effet a autoriser**.

--- L'incoherence qui fonde le lot ---

`read_file` NATIF lit librement sur tout le PC depuis L1d-3 (hors zones secretes),
tandis que `ide__read_file` est refuse - alors qu'il est BORNE au workspace actif
par `resolveWorkspacePath` (`workspaceSecurity.ts` l.58). La garde IDE est donc plus
stricte que la garde native, pour une action strictement plus restreinte.

Ce que cela coute en usage : « qu'est-ce que j'ai a l'ecran ? », « quels problemes
dans ce fichier ? », « montre-moi le diff », « les tests ont donne quoi ? ». Et
surtout `editor_get_content`, seule facon de voir un **buffer non sauvegarde**,
qu'aucun outil natif ne sait lire.

--- Gel modifie, et pourquoi ---

`test_conn5a_ide_mission_gate.py::test_hors_mission_lecture_de_fichier_reste_fermee`
figeait « CONN-5A n'ouvre RIEN au chat ». C'etait juste POUR CONN-5A, dont ce
n'etait pas l'objet. CONN-6 est precisement le lot qui ouvre - et il n'ouvre que la
lecture. Les trois autres gels du meme refus (`write_file`, `task_run`,
`command_run`) restent VERTS et deviennent la preuve du perimetre.
"""
from __future__ import annotations

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.tool_semantics import Availability, Confirmation, ToolEffect
from src.tools.ide_semantics import audited_ide_commands, local_ide_semantics
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401

REFUS = "IDE: ide_host_authorization_not_connected"


def _lectures_pures() -> set:
    """Les commandes qui n'ont aucun effet et ne demandent aucune confirmation."""
    noms = set()
    for nom in audited_ide_commands():
        semantique = local_ide_semantics(
            nom, instance_id="0" * 32, revision="0" * 64, availability=Availability.READY)
        if (semantique.effect is ToolEffect.READ_ONLY
                and semantique.confirmation is Confirmation.NEVER):
            noms.add(nom)
    return noms


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_chat_peut_lire_un_fichier_de_l_ide(registry, service, owner):
    """Aujourd'hui refuse, alors que `read_file` natif lit tout le PC."""
    envoi = _envoi(service, content="# Notes")

    observation = await registry._ide_tools.execute(
        "ide__read_file", {"path": "notes.txt"}, caller=REACT)

    assert observation.success is True, observation.content
    envoi.assert_awaited_once()


@pytest.mark.asyncio
async def test_le_chat_voit_le_buffer_non_sauvegarde(registry, service, owner):
    """`editor_get_content` : la seule facon de voir ce que Charles a A L'ECRAN.

    Aucun outil natif ne sait lire un buffer non sauvegarde - c'est la valeur
    propre de la voie IDE, et elle etait entierement inaccessible.
    """
    envoi = _envoi(service, content="VALEUR = 1  # pas encore enregistre")

    observation = await registry._ide_tools.execute(
        "ide__editor_get_content", {}, caller=REACT)

    assert observation.success is True, observation.content
    envoi.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outil", [
    "ide__git_status", "ide__git_diff", "ide__problems_list",
    "ide__test_runs", "ide__terminal_get_output", "ide__list_files",
])
async def test_les_lectures_d_etat_vivant_passent(registry, service, owner, outil):
    """Diff, problemes, tests, terminal : l'etat que seul l'IDE connait."""
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, {}, caller=REACT)

    assert observation.content != REFUS, f"{outil} reste refuse"
    envoi.assert_awaited_once()


# ── 2. Le perimetre, qui ne s'elargit PAS ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outil,params", [
    ("ide__write_file", {"path": "notes.txt", "content": "x"}),
    ("ide__task_run", {"taskId": "package:build"}),
    ("ide__command_run", {"command": "python -m pytest"}),
    ("ide__terminal_run", {"command": "echo bonjour"}),
    ("ide__sidebar_delete", {"path": "notes.txt"}),
])
async def test_rien_qui_produise_un_effet_ne_passe(registry, service, owner, outil, params):
    """Le lot ouvre la LECTURE, jamais l'effet. Trois gels de CONN-5 le figent deja."""
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, params, caller=REACT)

    assert observation.content == REFUS, observation.content
    envoi.assert_not_awaited()


@pytest.mark.asyncio
async def test_une_lecture_a_confirmation_reste_fermee(registry, service, owner):
    """La regle exige les DEUX conditions : lecture pure ET confirmation jamais.

    Si aucune commande auditee n'est `READ_ONLY` avec confirmation, ce test le dit
    au lieu de passer silencieusement pour une mauvaise raison.
    """
    candidates = [
        nom for nom in audited_ide_commands()
        if local_ide_semantics(nom, instance_id="0" * 32, revision="0" * 64,
                               availability=Availability.READY).effect is ToolEffect.READ_ONLY
        and nom not in _lectures_pures()
    ]
    if not candidates:
        pytest.skip("aucune lecture a confirmation dans le catalogue audite")
    envoi = _envoi(service)
    observation = await registry._ide_tools.execute(f"ide__{candidates[0]}", {}, caller=REACT)
    assert observation.content == REFUS
    envoi.assert_not_awaited()


# ── 3. La regle est SEMANTIQUE, pas une liste ───────────────────────────────

def test_la_garde_ne_nomme_plus_aucune_commande():
    """Une liste en dur redevient obsolete des qu'une commande est ajoutee.

    C'est exactement ce qui s'est produit : la liste de deux noms datait de CONN-4 et
    n'a jamais suivi les 41 lectures pures que le catalogue expose aujourd'hui.
    """
    from pathlib import Path

    source = Path("src/reasoning/ide_tool_runtime.py").read_text(encoding="utf-8")
    debut = source.index("ide_host_authorization_not_connected")
    garde = source[max(0, debut - 900):debut]
    assert "ide__get_status" not in garde and "ide__get_state" not in garde, (
        "la garde nomme encore des commandes au lieu de juger leur semantique"
    )


def test_le_catalogue_compte_bien_41_lectures_pures():
    """Chiffre de l'audit du 23 septembre, fige pour que sa derive se voie."""
    assert len(_lectures_pures()) == 41
