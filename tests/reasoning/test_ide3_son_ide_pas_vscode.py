r"""Lot IDE-3 - SON IDE par defaut, VS Code seulement si on le demande.

--- Ce que le run reel du 24 septembre 2026 a 18 h 24 a mesure ---

Charles : « ouvre une autre instance ide avec ton workspace dedans ».

Premier raisonnement de Lumena, verbatim :

    « Losskarr veut une nouvelle instance de l'IDE (VS Code) »

Elle lance `code --new-window`. Puis, a l'iteration 7, elle voit dans `list_windows` :

    - Lumena IDE
    - Lumena IDE
    - Lumena IDE

et se corrige : « il existe donc un IDE integre a Lumena, ce que l'utilisateur appelle
peut-etre "ton ide" ». Elle interroge `discover_tools`... qui ne remonte pas l'outil.
Alors elle **retourne a VS Code** et conclut « nouvelle instance VS Code ».

Le raisonnement etait bon. C'est le catalogue qui l'a trompee deux fois.

--- Deux causes, mesurees ---

**1. `ide_launch` disparait quand une IDE est connectee.** `capture()` ne l'expose qu'a
l'etat `launch_only`, c'est-a-dire quand AUCUNE session n'existe. C'est une borne de
securite de CONN-7d, figee par un test de CONN-3c (`assert "ide_launch" not in expected`)
— donc deliberee, et non modifiee ici. Mais c'est exactement le moment ou l'on veut une
instance de plus : IDE-2 avait ajoute le parametre a un outil devenu invisible.

**2. `cursor_ide_local` reste toujours visible, mais** il ne savait pas ouvrir une
seconde fenetre, et sa description parlait de « l'IDE local cursor-ide-local » — un nom
technique que rien ne relie a « ton IDE ». Un outil qu'on ne reconnait pas est un outil
qu'on n'essaie pas.

--- La regle demandee ---

**Par defaut SON IDE. VS Code uniquement s'il est nomme.**
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers import computer_use as CU


class _LanceurEspion:
    def __init__(self):
        self.appels = []

    async def observe(self):
        from src.tools.ide_launcher import IDEReadiness
        return IDEReadiness(transport_connected=True, handshake_received=True,
                            authenticated=True, workspace=None)

    async def ensure_ready(self, workspace=None, *, dedicated=False, **kwargs):
        self.appels.append({"workspace": str(workspace) if workspace else None,
                            "dedicated": dedicated})
        from src.tools.ide_launcher import IDELaunchResult
        return IDELaunchResult(state="transport_ready", process_started=True,
                              transport_connected=True, handshake_received=True,
                              reused=not dedicated)


@pytest.fixture
def espion(monkeypatch, tmp_path):
    lanceur = _LanceurEspion()
    monkeypatch.setattr(CU, "_get_cursor_ide_launcher", lambda: lanceur)
    monkeypatch.setattr(CU, "_prepare_cursor_workspace_path",
                        lambda ctx, **kw: tmp_path / "ws")
    return lanceur


def _outil():
    return [d for d in CU.get_computer_use_handler_defs() if d.name == "lumena_ide"][0]


# ── 1. L'action existe et demande une instance dediee ───────────────────────

@pytest.mark.asyncio
async def test_new_instance_ouvre_une_fenetre_de_plus(espion):
    """Le coeur du lot : la demande de Charles a enfin un outil qui y repond."""
    resultat = await CU.lumena_ide(None, action="new_instance")
    assert resultat.success, resultat.error
    assert espion.appels and espion.appels[0]["dedicated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("synonyme", ["nouvelle_instance", "new_window", "autre_instance"])
async def test_les_mots_reellement_employes_sont_acceptes(espion, synonyme):
    """« une autre instance », « nouvelle fenetre » : les formes que Charles utilise."""
    resultat = await CU.lumena_ide(None, action=synonyme)
    assert resultat.success, resultat.error
    assert espion.appels[0]["dedicated"] is True


@pytest.mark.asyncio
async def test_ensure_open_reste_une_reutilisation(espion):
    """Sans le demander, on ne multiplie pas les fenetres par surprise."""
    await CU.lumena_ide(None, action="ensure_open")
    assert espion.appels[0]["dedicated"] is False


@pytest.mark.asyncio
async def test_une_dediee_ne_se_dit_pas_reutilisee(espion):
    """C'est ce mot, renvoye au run precedent, qui a fait croire a un echec."""
    resultat = await CU.lumena_ide(None, action="new_instance")
    assert "reutilise" not in resultat.output.lower(), resultat.output
    assert "dediee" in resultat.output.lower(), resultat.output


@pytest.mark.asyncio
async def test_une_action_inconnue_nomme_les_actions_valides(espion):
    resultat = await CU.lumena_ide(None, action="téléporte")
    assert not resultat.success
    assert "new_instance" in (resultat.error or ""), resultat.error


# ── 2. La description empeche la confusion avec VS Code ─────────────────────

def test_la_description_nomme_LUMENA_IDE_et_pas_un_nom_technique():
    """« cursor-ide-local » ne dit rien a personne. « ton IDE », si."""
    description = _outil().description
    assert "LUMENA IDE" in description.upper()
    assert "ton" in description.lower() and "ide" in description.lower()


def test_la_description_ecrit_la_distinction_avec_vscode():
    """Le defaut mesure : « une nouvelle instance de l'IDE (VS Code) ».

    Tant que l'outil ne dit pas lui-meme qu'il n'est PAS VS Code, le modele continue
    de traduire « ide » par « VS Code » — c'est le nom qu'il rencontre le plus.
    """
    description = _outil().description.lower()
    assert "vs code" in description, "la distinction n'est pas ecrite"
    assert "confondre" in description or "pas" in description


def test_la_description_porte_les_formulations_de_la_demande():
    """C'est par ces mots que le modele cherchera — et que `discover_tools` indexe."""
    description = _outil().description.lower()
    for formule in ("ton ide", "instance ide", "autre instance"):
        assert formule in description, f"formulation absente : {formule}"


def test_vscode_reste_possible_mais_sur_demande_explicite():
    """Charles a dit : « de base son ide et si demande vscode »."""
    description = _outil().description.lower()
    assert "explicitement" in description, (
        "l'outil doit dire que VS Code reste accessible, mais seulement s'il est nomme"
    )


def test_l_action_new_instance_est_annoncee_au_schema():
    """Un parametre que le catalogue n'annonce pas est un parametre jamais essaye."""
    action = _outil().parameters["properties"]["action"]["description"]
    assert "new_instance" in action
    assert "PLUS" in action or "plus" in action


# ── 3. Une mission garde sa propre voie ────────────────────────────────────

@pytest.mark.asyncio
async def test_une_mission_ne_passe_pas_par_cette_facade(espion):
    """L5-2 : une mission ne touche pas la fenetre de l'utilisateur, et elle a deja
    la voie A pour ouvrir SA propre instance. La facade reste refusee en mission."""
    class _Ctx:
        is_mission_run = True

    resultat = await CU.lumena_ide(_Ctx(), action="new_instance")
    assert not resultat.success
    assert espion.appels == [], "le lanceur a ete sollicite depuis une mission"


# ── 4. IDE-4 — ce que le run de 19 h a mesure ───────────────────────────────
#
# Deux defauts, trouves en regardant Lumena utiliser IDE-3 pour de vrai.

@pytest.mark.asyncio
@pytest.mark.parametrize("invente", ["open_new", "new", "instance", "ensure_new"])
async def test_les_synonymes_que_lumena_a_reellement_composes(espion, invente):
    """Run de 19 h 09 : elle a essaye `open_new`, qui n'existait pas.

    Elle a perdu un appel sur un refus d'action, puis trouve `new_instance` au
    suivant. Un modele compose naturellement ces formes a partir de `open` : refuser
    sur un synonyme n'apprend rien a personne.
    """
    resultat = await CU.lumena_ide(None, action=invente)
    assert resultat.success, resultat.error
    assert espion.appels[0]["dedicated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("invente", ["open_workspace", "workspace"])
async def test_les_synonymes_de_changement_de_dossier(espion, invente):
    resultat = await CU.lumena_ide(None, action=invente)
    assert resultat.success, resultat.error
    assert espion.appels[0]["dedicated"] is False


def test_le_refus_du_chat_nomme_la_voie_qui_FONCTIONNE():
    """Le geste envoyait dans une impasse, mesuree a 19 h 12.

    Charles demande d'ouvrir les fichiers d'un AUTRE dossier dans l'IDE. Il faut donc
    changer de workspace. `ide__navigate` est refuse (modifie l'hote),
    `ide__open_file` refuse hors workspace actif. Lumena a tourne sur ces deux refus
    jusqu'a renoncer — avec pour seul conseil « demande-la depuis une mission, ou
    fais-la toi-meme », alors qu'elle EST dans le chat.

    Un refus doit nommer la porte OUVERTE, pas seulement celle qu'il ferme.
    """
    from src.reasoning.ide_error_guidance import GUIDES_ERREURS_IDE
    _cause, geste = GUIDES_ERREURS_IDE["ide_host_authorization_not_connected"]
    assert "lumena_ide" in geste, geste
    assert "ensure_workspace" in geste, geste
    assert "autorisee au chat" in geste or "autorisée au chat" in geste, geste
