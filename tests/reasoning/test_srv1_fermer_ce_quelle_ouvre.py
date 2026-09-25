r"""Lot SRV-1 - Lumena peut fermer les serveurs qu'elle ouvre.

--- Ce que le run du 25/09 a 02 h 29 a mesure ---

Charles : « ferme le ». Trois tentatives, trois murs, en 5 secondes :

    02:29:58  Get-NetTCPConnection ... | ForEach-Object { Stop-Process -Id $_ -Force }
              -> Commande bloquee (pattern dangereux)
    02:30:01  taskkill /F /PID 32952
              -> Commande bloquee (pattern dangereux)
    02:30:03  « `taskkill` declenche le filtre "pattern dangereux". Je change de
              strategie : j'utilise PowerShell `Stop-Process` »
              -> bloque aussi

Puis elle abandonne. **Elle ne peut pas fermer ce qu'elle vient d'ouvrir.**

--- Le diagnostic : le sanitizer n'y est pour RIEN ---

`taskkill /f` et `Stop-Process -Force` sont dans `BLOCKED_PATTERNS`, et ils doivent y
rester : ce sont des commandes qui tuent des processus arbitraires de la machine de
Charles.

**Les outils legitimes EXISTENT** : `process_kill` (module `agents`) et
`stop_website_server` (module `website`). Mesure sur les formulations reelles :

    ferme le                            -> process_kill NON, stop_website_server NON
    ferme les serveurs que tu as ouvert -> NON, NON
    arrete le serveur sur le port 8085  -> NON, NON
    tue le processus du serveur         -> NON, NON

**0 sur 3.** Le filtre contextuel n'expose aucune des deux portes. Elle improvise donc
avec `run_command`, et se fait bloquer - a juste titre. La porte legitime etait
invisible, elle est passee par la fenetre, et la fenetre est verrouillee.

C'est la TROISIEME fois du 24-25/09 que ce motif frappe, apres `lumena_ide` (IDE-7) et
`peer_team_request` (« la deuxieme lumena »). Sur le corpus reel des 15 demandes de
Charles, **9 fois l'outil necessaire n'etait pas dans son prompt**.

--- Le second volet, et pourquoi il compte autant ---

Le refus disait seulement « pattern dangereux detecte ». Il ne nommait aucune voie.

Le patron inverse a ete PROUVE EN RUNTIME le meme soir : a 23:17:28 `ide__navigate` est
refuse, le refus nomme `lumena_ide(ensure_workspace)`, et elle reussit **4 secondes plus
tard**. A 20:07, le meme refus sans porte nommee lui avait coute dix minutes.

Un refus muet fabrique la recherche du contournement suivant. C'est litteralement ce
qu'elle a fait ici, en trois essais.
"""
from __future__ import annotations

import pytest

from src.core_services.intent_classifier import classify_intent
from src.reasoning.react import ToolRegistry
from src.utils.command_sanitizer import sanitize_command


@pytest.fixture(scope="module")
def registre():
    return ToolRegistry()


def _permis(registre, demande, intent=None):
    if intent is None:
        resultat = classify_intent(demande, None)
        intent = resultat.value if hasattr(resultat, "value") else str(resultat)
    registre._allowed_tools = None
    registre.apply_context_filter(demande, intent=intent)
    return registre._allowed_tools, intent


# -- 1. Les deux portes sont ouvertes sur les formulations reelles ------------

DEMANDES = [
    "ferme les serveurs que tu as ouvert",
    "arrete le serveur sur le port 8085",
    "tue le processus du serveur",
    "coupe le serveur de preview",
    "ferme le serveur web",
    "arrete les processus que tu as lances",
    "stoppe le serveur",
]


@pytest.mark.parametrize("demande", DEMANDES)
def test_une_demande_d_arret_expose_les_deux_outils(registre, demande):
    """Mesure avant le lot : 0 sur 3. Et avec l'intent que le classifier CALCULE -
    la lecon d'IDE-7 bis, ou j'avais choisi les intents qui passaient."""
    permis, intent = _permis(registre, demande)
    if permis is None:
        return  # aucun filtre applique : tout est visible
    assert "process_kill" in permis, f"process_kill absent (intent={intent}) : {demande!r}"
    assert "stop_website_server" in permis, (
        f"stop_website_server absent (intent={intent}) : {demande!r}"
    )


def test_l_intent_chat_n_efface_pas_les_outils_d_arret(registre):
    """Les 4 formulations mesurees tombaient toutes en `chat`, l'intent qui reduit la
    boite a outils a memory+system."""
    permis, _ = _permis(registre, "ferme les serveurs que tu as ouvert", intent="chat")
    assert permis is not None
    assert {"process_kill", "stop_website_server"} <= permis


def test_de_quoi_VOIR_avant_de_tuer(registre):
    """Tuer sans regarder est le geste qu'on ne veut pas. `bg_list` vit dans la meme
    categorie qu'un des deux outils : la demande doit donner les deux."""
    permis, _ = _permis(registre, "ferme les serveurs que tu as ouvert")
    assert permis is None or "bg_list" in permis


# -- 2. Le refus nomme la porte ouverte --------------------------------------

@pytest.mark.parametrize("commande", [
    "taskkill /F /PID 32952",                                  # verbatim 02:30:01
    'Get-Process node | Stop-Process -Force',
])
def test_le_refus_nomme_l_outil_a_utiliser(commande):
    """Patron prouve en runtime a 23:17 : un refus qui nomme la voie ouverte est suivi
    en 4 secondes. Un refus muet coute dix minutes et trois contournements."""
    ok, message = sanitize_command(commande)
    assert ok is False, f"cette commande doit rester bloquee : {commande!r}"
    assert "process_kill" in message, message
    assert "stop_website_server" in message, message


def test_les_autres_refus_ne_sont_PAS_pollues():
    """Le conseil ne vaut que pour l'arret de processus. Le proposer sur `format C:`
    serait absurde et diluerait le message."""
    ok, message = sanitize_command("format c:")
    assert ok is False
    assert "process_kill" not in message, message


# -- 3. Ce que le lot ne doit PAS affaiblir ----------------------------------

@pytest.mark.parametrize("commande", [
    "taskkill /F /PID 32952",
    "taskkill /f /im node.exe",
    'Get-Process node | Stop-Process -Force',
    "Stop-Process -Id 4242 -Force",
])
def test_les_commandes_qui_tuent_restent_bloquees(commande):
    """Le sanitizer protege la machine de Charles : ces commandes visent des processus
    arbitraires. On ne les ouvre pas pour du confort - on offre la voie encadree."""
    ok, _ = sanitize_command(commande)
    assert ok is False, f"AFFAIBLI : {commande!r} passe desormais"


@pytest.mark.parametrize("commande", ["format c:", "shutdown /s", "reg delete HKLM\\Foo"])
def test_les_autres_patterns_dangereux_restent_bloques(commande):
    ok, _ = sanitize_command(commande)
    assert ok is False, f"AFFAIBLI : {commande!r}"


# -- 4. Le garde contre l'elargissement -------------------------------------

@pytest.mark.parametrize("demande", [
    "ecris-moi un poeme sur la pluie",
    "resume-moi cet article",
    "comment tu vas aujourd'hui",
])
def test_une_demande_sans_rapport_n_ouvre_pas_ces_categories(registre, demande):
    """Elargir un filtre est facile ; ne plus filtrer serait le vrai echec."""
    permis, _ = _permis(registre, demande)
    assert permis is not None, f"aucun filtre pour : {demande!r}"
    assert "process_kill" not in permis, f"ouvert a tort pour : {demande!r}"


def test_le_mot_ferme_seul_ne_suffit_pas(registre):
    """« ferme la porte », « ferme le document » ne parlent pas de processus. Le lot
    doit exiger le CONTEXTE serveur/processus, pas le seul verbe."""
    permis, _ = _permis(registre, "ferme la fenetre du navigateur")
    assert permis is not None
    assert "process_kill" not in permis, (
        "le verbe seul ouvre la categorie : le declencheur est trop large"
    )


def test_LIMITE_ASSUMEE_ferme_le_ne_dit_pas_QUOI():
    """« ferme le » est le message VERBATIM de Charles a 02 h 29, et il ne contient
    aucune cible : ni « serveur », ni « processus », ni « port ». Le « le » renvoie au
    tour precedent.

    Un filtre par mots-cles ne peut pas le resoudre - meme limite que « ouvre ton
    worskace dedans » cote IDE. On la fige au lieu de pretendre l'avoir traitee : le
    declencheur exige une CIBLE, sinon « ferme la fenetre » et « ferme le document »
    ouvriraient la categorie des processus.

    Ce que le lot apporte quand meme sur ce cas : le refus du sanitizer nomme desormais
    `process_kill`, donc l'improvisation par `run_command` mene a la bonne porte au lieu
    d'un mur muet.
    """
    from src.reasoning.tool_registry import _CIBLES_PROCESSUS_RE, _VERBES_D_ARRET_RE

    assert _VERBES_D_ARRET_RE.search("ferme le")
    assert not _CIBLES_PROCESSUS_RE.search("ferme le")
