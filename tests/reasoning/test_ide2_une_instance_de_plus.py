"""Lot IDE-2 - une FENETRE DE PLUS, demandable depuis le chat.

--- Ce que le run reel du 24 septembre 2026 a mesure ---

Charles : « ouvre une autre instance ide avec ton workspace dedans ».

Lumena remonte toute la chaine, seule et juste :

    main.ts:707  requestSingleInstanceLock()      -> le verrou d'instance unique
    main.ts:128  app.setPath('userData', ...)     -> le verrou est lie au profil
                 LUMENA_IDE_USER_DATA             -> le profil vient de l'environnement
                 LUMENA_IDE_WORKSPACE             -> le dossier aussi
                 release/win-unpacked/...exe      -> l'exe package existe

Puis TRENTE iterations a tenter de lancer un processus a la main : trois refus du
sanitizer (SAN-1), deux refus d'ecriture de `.bat`, un `process_list` qui repond
« aucun processus » avec neuf MCP en marche. Elle a conclu :

    « pas de 2e instance ouverte, je ne vais pas te raconter le contraire »

Elle a eu raison de ne pas mentir. Mais le mecanisme qu'elle cherchait existait
**deja** : `ensure_ready(dedicated=True)`, depuis L5-3b-bis — profil distinct, donc
verrou contourne, et lancement meme si une IDE est connectee. Il servait aux missions
(voie A) et n'etait expose a personne d'autre.

Dixieme occurrence du motif de la journee : le fait existait, et rien ne le rendait
atteignable.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers import ide as H


class _LanceurEspion:
    """Retient ce que le handler lui demande, sans rien lancer."""

    def __init__(self):
        self.appels = []

    async def ensure_ready(self, workspace=None, *, dedicated=False, **kwargs):
        self.appels.append({"workspace": workspace, "dedicated": dedicated})
        from src.tools.ide_launcher import IDELaunchResult
        return IDELaunchResult(state="transport_ready", process_started=not dedicated,
                               transport_connected=True, handshake_received=True,
                               reused=not dedicated)


@pytest.fixture
def espion(monkeypatch):
    lanceur = _LanceurEspion()
    monkeypatch.setattr(H, "_get_launcher", lambda: lanceur)
    return lanceur


# ── 1. Le parametre existe et atteint le lanceur ─────────────────────────────

@pytest.mark.asyncio
async def test_nouvelle_instance_demande_une_instance_dediee(espion):
    """Le coeur du lot : le chat peut enfin demander une fenetre de plus."""
    resultat = await H._handle_ide_launch(None, nouvelle_instance=True)
    assert resultat.success, resultat.error
    assert espion.appels == [{"workspace": None, "dedicated": True}]


@pytest.mark.asyncio
async def test_par_defaut_on_reutilise_la_fenetre_existante(espion):
    """Sans le demander, rien ne change : on ne multiplie pas les fenetres par surprise."""
    await H._handle_ide_launch(None)
    assert espion.appels == [{"workspace": None, "dedicated": False}]


@pytest.mark.asyncio
async def test_le_dossier_accompagne_l_instance_dediee(espion):
    """« une autre instance avec ton workspace dedans » : les deux ensemble."""
    await H._handle_ide_launch(None, workspace="C:/travail/projet", nouvelle_instance=True)
    assert espion.appels == [{"workspace": "C:/travail/projet", "dedicated": True}]


@pytest.mark.asyncio
async def test_le_nom_anglais_reste_accepte(espion):
    """`dedicated` est le nom interne ; l'accepter evite un refus sur un synonyme."""
    await H._handle_ide_launch(None, dedicated=True)
    assert espion.appels[0]["dedicated"] is True


# ── 2. Le message ne ment pas sur ce qui s'est passe ────────────────────────

@pytest.mark.asyncio
async def test_une_instance_dediee_ne_se_dit_pas_reutilisee(espion):
    """`reused` vaut faux pour une dediee : le message doit suivre.

    Dans le run, `cursor_ide_local` repondait « Lumena IDE reutilise » apres avoir
    change le workspace de la fenetre existante — ce qui a fait croire a Lumena
    qu'elle avait echoue, alors qu'elle avait deplace la fenetre de Charles.
    """
    resultat = await H._handle_ide_launch(None, nouvelle_instance=True)
    assert "reutilise" not in resultat.output.lower(), resultat.output
    assert "dediee" in resultat.output.lower(), resultat.output


# ── 3. Le parametre est VISIBLE du modele ───────────────────────────────────

def test_le_catalogue_annonce_le_parametre():
    """Un parametre qu'on ne declare pas est un parametre que le modele n'essaiera pas.

    C'est exactement ce qui s'est passe : `dedicated` existait dans le lanceur, mais
    le schema de `ide_launch` ne mentionnait que `workspace`. Lumena ne pouvait pas
    deviner qu'il suffisait de le demander.
    """
    defs = [d for d in H.get_ide_handler_defs() if d.name == "ide_launch"]
    assert defs, "ide_launch absent du catalogue"
    proprietes = defs[0].parameters.get("properties", {})
    assert "nouvelle_instance" in proprietes, sorted(proprietes)
    assert proprietes["nouvelle_instance"]["type"] == "boolean"
    description = (defs[0].description or "").lower()
    assert "nouvelle_instance" in description, (
        "la description doit nommer le parametre : c'est elle que le modele lit"
    )
