"""Lot CONN-6b - les actions de navigation de l'IDE arrivent au chat.

CONN-6a a ouvert les **41 lectures pures**. Restent **29 commandes
`UI_STATE_ONLY` sans confirmation**, encore refusees hors mission : ouvrir un
fichier, aller a la definition, ouvrir les references, afficher un diff, basculer
un panneau, changer d'onglet.

Elles ne touchent **ni le disque, ni un processus, ni un reglage**. Lumena peut
aujourd'hui te DIRE ou une fonction est definie, mais pas te l'OUVRIR - alors que
c'est le geste naturel d'un editeur.

--- Mesure du 23 septembre 2026, sur les 135 commandes auditees ---

    READ_ONLY            never   ->  41   ouvertes par CONN-6a
    UI_STATE_ONLY        never   ->  29   ce lot
    tout le reste                ->  65   effet reel ou confirmation exigee

--- Ce qui borne le risque, et qui est un FAIT, pas un avis ---

Les **29** commandes sont `mission_policy: forbidden`, **sans exception**. Aucune
mission ne peut donc les employer, quoi que fasse ce lot : le seul appelant possible
est le chat, ou l'utilisateur est devant son ecran et vient de le demander. Un test
fige ce compte : si une seule de ces commandes devenait autorisee en mission, le lot
changerait de nature et doit etre revu.

L'autre borne est deja en place : `authorize_mission_call` refuse par
`ide_mission_tool_forbidden` avant meme d'atteindre le transport.
"""
from __future__ import annotations

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.tool_semantics import Availability, Confirmation, MissionPolicy, ToolEffect
from src.tools.ide_semantics import audited_ide_commands, local_ide_semantics
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401

REFUS = "IDE: ide_host_authorization_not_connected"


@pytest.fixture(autouse=True)
def effets_isoles():
    """Isole le registre d'effets, qui est un singleton de MODULE.

    Mesure du 23 septembre 2026 : ce fichier est le premier a faire REUSSIR une
    commande a effet hors mission. `external_effect_cache.begin()` ouvre alors un
    ticket pour tout effet non `READ_ONLY`, et `complete()` ne le referme que si le
    `ToolExecutionResult` correspond exactement - ce que le `send_command` double de
    ces tests ne produit pas.

    Sans cette isolation, les tickets restent actifs et `observation_cache_epoch`
    rend `None`, ce qui **desactive le cache d'observation de tout le processus** :
    `test_destructive_write_guard::test_p2_cache_*` tombait alors deux fichiers plus
    loin. CONN-6a ne polluait pas, les lectures ne prenant aucun ticket.
    """
    from src.reasoning.external_effect_cache import external_effect_cache

    cache = external_effect_cache()
    with cache._lock:
        avant = dict(cache._active)
    yield
    with cache._lock:
        cache._active.clear()
        cache._active.update(avant)
        cache._epoch += 1


def _semantique(nom):
    return local_ide_semantics(
        nom, instance_id="0" * 32, revision="0" * 64, availability=Availability.READY)


def _navigation() -> set:
    """Les commandes qui ne changent que l'affichage, sans rien demander."""
    return {nom for nom in audited_ide_commands()
            if _semantique(nom).effect is ToolEffect.UI_STATE_ONLY
            and _semantique(nom).confirmation is Confirmation.NEVER}


# ── 1. Le fait qui borne le lot ─────────────────────────────────────────────

def test_aucune_action_de_navigation_n_est_permise_en_mission():
    """La borne du lot, verifiee et non supposee.

    Si une seule de ces commandes cessait d'etre `forbidden`, ouvrir la navigation
    au chat ouvrirait aussi une porte cote mission : le lot changerait de nature.
    """
    fautives = [nom for nom in _navigation()
                if _semantique(nom).mission_policy is not MissionPolicy.FORBIDDEN]
    assert fautives == [], fautives


def test_le_catalogue_compte_bien_29_actions_de_navigation():
    """Chiffre de l'audit du 23 septembre, fige pour que sa derive se voie."""
    assert len(_navigation()) == 29


# ── 2. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outil,params", [
    ("ide__open_file", {"path": "notes.txt"}),
    ("ide__show_diff", {"original": "a = 1\n", "modified": "a = 2\n"}),
    ("ide__problems_open", {"path": "notes.txt", "line": 3}),
    ("ide__editor_go_definition", {}),
    ("ide__navigation_open_references", {}),
    ("ide__toggle_terminal", {}),
    ("ide__editor_switch_tab", {"path": "notes.txt"}),
])
async def test_le_chat_peut_naviguer_dans_l_ide(registry, service, owner, outil, params):
    """« Ouvre-moi ce fichier », « montre-moi ou c'est defini » : refuse aujourd'hui."""
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, params, caller=REACT)

    assert observation.content != REFUS, f"{outil} reste refuse"
    envoi.assert_awaited_once()


# ── 3. Le perimetre, qui ne s'elargit PAS ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outil,params", [
    ("ide__write_file", {"path": "notes.txt", "content": "x"}),
    ("ide__task_run", {"taskId": "package:build"}),
    ("ide__command_run", {"command": "python -m pytest"}),
    ("ide__terminal_run", {"command": "echo bonjour"}),
    ("ide__sidebar_delete", {"path": "notes.txt"}),
    ("ide__sidebar_create_file", {"path": "notes.txt"}),
])
async def test_rien_qui_produise_un_effet_ne_passe(registry, service, owner, outil, params):
    """Les trois gels de CONN-5 figent deja les trois premiers."""
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, params, caller=REACT)

    assert observation.content == REFUS, observation.content
    envoi.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outil,params", [
    ("ide__editor_close_tab", {"path": "notes.txt"}),
    ("ide__terminal_clear", {}),
])
async def test_une_action_d_interface_a_confirmation_reste_fermee(
        registry, service, owner, outil, params):
    """`UI_STATE_ONLY` ne suffit pas : la confirmation doit aussi etre `never`.

    Ces commandes sont `UI_STATE_ONLY` avec `confirmation: policy` - fermer un onglet
    peut perdre un buffer, vider un terminal efface une trace. Ce n'est pas de la
    navigation. (`editor_rename_symbol`, troisieme du meme genre, est ecartee ici :
    son schema refuse mes parametres avant la garde, donc elle ne prouverait rien.)

    Mesure faite en ecrivant ce test : les quatre `assistant_*` a confirmation
    (`new_conversation`, `resume_conversation`, `select_model`, `set_mode`) sont
    `model_exposure: never`. Elles ne sont donc **jamais dans le catalogue du
    modele** et echouent en amont par `external_tool_not_in_snapshot` - un SECOND
    verrou, anterieur a la garde d'autorisation. Les viser ici n'aurait rien prouve
    de la garde elle-meme.
    """
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, params, caller=REACT)

    assert observation.content == REFUS, observation.content
    envoi.assert_not_awaited()


# ── 4. La regle reste SEMANTIQUE ────────────────────────────────────────────

def test_la_garde_ne_nomme_toujours_aucune_commande():
    """Gel de CONN-6a, reconduit : une liste en dur redevient obsolete."""
    from pathlib import Path

    source = Path("src/reasoning/ide_tool_runtime.py").read_text(encoding="utf-8")
    debut = source.index("ide_host_authorization_not_connected")
    garde = source[max(0, debut - 1400):debut]
    assert "ide__open_file" not in garde and "ide__get_status" not in garde, (
        "la garde nomme des commandes au lieu de juger leur semantique"
    )


def test_les_deux_familles_ouvertes_couvrent_exactement_70_commandes():
    """41 lectures (CONN-6a) + 29 navigations (ce lot), sur 135 auditees."""
    lectures = {nom for nom in audited_ide_commands()
                if _semantique(nom).effect is ToolEffect.READ_ONLY
                and _semantique(nom).confirmation is Confirmation.NEVER}
    assert len(lectures) == 41
    assert len(lectures | _navigation()) == 70
    assert not (lectures & _navigation()), "une commande ne peut pas etre des deux familles"
