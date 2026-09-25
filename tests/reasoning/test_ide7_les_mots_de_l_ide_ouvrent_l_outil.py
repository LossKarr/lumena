"""Lot IDE-7 - « ide » et « editeur » donnent acces a l'outil de l'IDE.

--- Ce que le run du 24/09 a 20 h 03 a mesure ---

Charles : « J'ai besoin que tu mouvre ton ide stp ».

Lumena, en NEUF iterations : `list_directory` a la racine, `list_directory ide/`,
`read_file LANCER_IDE.bat`, `run_command npm start` (refuse par le garde du depot),
`open_file` sur le .bat pour contourner, puis trois `tasklist` pour verifier qu'un
processus Electron tourne.

**Elle n'a jamais pense a `lumena_ide`.** Pas par erreur de raisonnement : l'outil
n'etait pas dans sa liste. Au tour suivant elle ne l'a trouve que par `discover_tools`,
de sa propre initiative - « 20 outils trouves et ajoutes ».

Un appel a `lumena_ide(ensure_open)` aurait suffi.

--- La mesure qui fonde le lot ---

Sur 10 formulations reelles passees a `apply_context_filter`, **9 n'exposent pas
`lumena_ide`** ; seule « ferme la fenetre » le fait. Verifie sur les cinq intents
(`react`, `project`, `tool_direct`, `chat`, `None`) : 148 outils permis sur 611, et
`lumena_ide` dans aucun cas.

Cause : le pack COMPUTER de `_CONTEXT_RULES` liste « souris », « clavier », « fenetre »,
« notepad », « paint »... et **aucun mot du champ IDE**. Ni « ide », ni « editeur », ni
« vscode ». La categorie qui contient l'outil ne s'ouvre jamais quand on parle d'IDE.

--- La discipline de ce lot ---

Elargir un filtre est facile et dangereux : tout ouvrir reviendrait a ne plus filtrer,
et le filtre existe pour tenir le contexte d'outils. Ce fichier fige donc les deux
sens : les formulations du champ IDE ouvrent la categorie, **et des demandes sans aucun
rapport continuent de ne pas l'ouvrir**.
"""
from __future__ import annotations

import pytest

from src.reasoning.react import ToolRegistry
from src.reasoning.tool_registry import _MOTS_DE_L_IDE_RE


@pytest.fixture(scope="module")
def registre():
    return ToolRegistry()


def _outils_permis(registre, demande, intent="react"):
    registre._allowed_tools = None
    registre.apply_context_filter(demande, intent=intent)
    return registre._allowed_tools


def _expose(registre, demande, outil="lumena_ide", intent="react"):
    permis = _outils_permis(registre, demande, intent)
    return permis is None or outil in permis


# -- 1. Les formulations reellement employees --------------------------------

@pytest.mark.parametrize("demande", [
    # verbatim du run du 24/09, faute de frappe comprise
    "J'ai besoin que tu mouvre ton ide stp",
    "ouvre ton ide",
    # les 7 messages de la liste de tests de Charles
    "ouvre une autre instance ide avec ton workspace dedans",
    "ouvre mon projet dans ton IDE",
    "dans ton IDE, ouvre calculator.py et montre-moi la ligne 39",
    "lance ton IDE sur workspace/calc_project",
    "tu viens de lancer ton IDE : relis un fichier dedans",
    # autres formes attendues
    "lance ton editeur",
    "ouvre ton editeur de code",
    "prouve moi que tu sais utiliser ton ide",
    "change le workspace de ton ide",
    "ferme ton ide",
])
def test_les_demandes_d_ide_exposent_l_outil_de_l_ide(registre, demande):
    assert _expose(registre, demande), f"lumena_ide absent pour : {demande!r}"


@pytest.mark.parametrize("intent", ["react", "project", "tool_direct", None])
def test_l_intent_ne_change_pas_la_reponse(registre, intent):
    """La mesure montrait NON sur les cinq intents : le defaut n'etait pas un
    accident de classification."""
    assert _expose(registre, "ouvre ton ide", intent=intent)


def test_vscode_nomme_expose_aussi_l_outil(registre):
    """Charles peut demander VS Code (IDE-3). L'outil doit etre visible pour que
    Lumena puisse LIRE dans sa description que VS Code passe par `run_command`."""
    assert _expose(registre, "ouvre VS Code sur mon workspace")


# -- 2. Le garde contre l'elargissement -------------------------------------

@pytest.mark.parametrize("demande", [
    "ecris-moi un poeme sur la pluie",
    "resume-moi cet article",
    "combien font 17 fois 23",
    "traduis cette phrase en anglais",
])
def test_une_demande_sans_rapport_n_ouvre_pas_computer_use(registre, demande):
    """Tout ouvrir reviendrait a ne plus filtrer. Le filtre existe pour tenir le
    contexte d'outils : ce lot l'elargit sur un champ precis, pas partout."""
    permis = _outils_permis(registre, demande)
    assert permis is not None, f"aucun filtre applique pour : {demande!r}"
    assert "lumena_ide" not in permis, f"computer_use ouvert a tort pour : {demande!r}"


def test_le_mot_code_seul_ne_suffit_pas(registre):
    """« code » est partout dans les demandes de developpement. L'ouvrir sur ce seul
    mot ferait entrer computer_use dans presque toutes les demandes techniques."""
    permis = _outils_permis(registre, "corrige ce bug dans le code")
    assert permis is not None
    assert "lumena_ide" not in permis


# -- 3. Ce que le lot ne doit PAS casser ------------------------------------

@pytest.mark.parametrize("demande", [
    "ferme la fenetre",
    "prends un screenshot de l'ecran",
    "clique avec la souris sur le bouton",
    "ouvre notepad",
])
def test_les_demandes_computer_use_historiques_marchent_toujours(registre, demande):
    assert _expose(registre, demande), f"regression sur : {demande!r}"


# -- 4. IDE-10 - le synonyme que Lumena a compose ---------------------------

@pytest.mark.asyncio
async def test_set_workspace_est_accepte(monkeypatch, tmp_path):
    """Run de 20 h 04 48 : elle a essaye `set_workspace`, refuse, et a perdu un appel.

    C'est la cinquieme forme qu'elle compose spontanement apres les quatre d'IDE-4
    (`open_new`, `new`, `instance`, `ensure_new`). Refuser sur un synonyme n'apprend
    rien a personne.
    """
    from src.reasoning.handlers import computer_use as CU
    from src.tools.ide_launcher import IDELaunchResult, IDEReadiness

    appels = []

    class _Lanceur:
        async def observe(self):
            return IDEReadiness(transport_connected=True, handshake_received=True,
                                authenticated=True, workspace=None)

        async def ensure_ready(self, workspace=None, *, dedicated=False, **kw):
            appels.append({"dedicated": dedicated})
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True,
                                   reused=not dedicated)

    monkeypatch.setattr(CU, "_get_cursor_ide_launcher", lambda: _Lanceur())
    monkeypatch.setattr(CU, "_prepare_cursor_workspace_path", lambda ctx, **kw: tmp_path / "ws")

    r = await CU.lumena_ide(None, action="set_workspace")

    assert r.success, r.error
    assert appels and appels[0]["dedicated"] is False, "set_workspace ne doit pas ouvrir une fenetre de plus"


# -- 5. IDE-7 bis - le defaut que mon propre test avait manque ---------------
#
# Run du 24/09 a 23:16, APRES IDE-7. Message : « ouvre ton ide ». Son raisonnement :
#
#     « Je dois d'abord identifier l'outil disponible pour piloter cet IDE »
#     -> discover_tools -> « 6 outils trouves et ajoutes: - lumena_ide: ... »
#     -> « J'ai MAINTENANT acces a `lumena_ide` »
#
# « MAINTENANT » : elle ne l'avait donc PAS. IDE-7 etait vert en test et inoperant
# en production.
#
# CAUSE MESUREE : le classifier range « ouvre ton ide » en `intent="chat"`, et le bloc
# `if intent == "chat"` ECRASE `matched_categories` — y compris la categorie que la
# regle des mots de l'IDE venait d'ajouter juste au-dessus.
#
# POURQUOI MON TEST NE L'A PAS VU : je l'avais parametre sur `react`, `project`,
# `tool_direct` et `None` — **j'avais exclu `chat`**, le seul intent que la production
# produit pour ces demandes. Choisir les cas qui passent n'est pas mesurer.
#
# Le precedent qui donne la forme du correctif existait deja : `peer_team_query`
# traverse ce meme ecrasement, avec pour motif « une demande naturelle doit rendre
# visibles les outils peer, MEME SI le classifier la voit comme un simple chat ».

@pytest.mark.parametrize("demande", [
    "ouvre ton ide",
    "ouvre une autre instance ide avec ton workspace dedans",
    "dans ton IDE, ouvre calculator.py et montre-moi la ligne 39",
    "lance ton editeur",
])
def test_l_intent_chat_n_efface_pas_l_acces_a_l_ide(registre, demande):
    """Le fait qui fonde le lot bis : avec l'intent REEL, pas celui que je choisis."""
    assert _expose(registre, demande, intent="chat"), (
        f"lumena_ide absent en intent=chat pour : {demande!r}"
    )


@pytest.mark.parametrize("demande", [
    "ouvre ton ide",
    "dans ton IDE, ouvre calculator.py et montre-moi la ligne 39",
    "lance ton editeur",
])
def test_avec_l_intent_QUE_LA_PRODUCTION_CALCULE(registre, demande):
    """Ne plus jamais choisir l'intent moi-meme : on demande au classifier.

    C'est la seule forme de ce test qui aurait attrape le defaut de 23:16.
    """
    from src.core_services.intent_classifier import classify_intent

    resultat = classify_intent(demande, None)
    intent = resultat.value if hasattr(resultat, "value") else str(resultat)
    assert _expose(registre, demande, intent=intent), (
        f"lumena_ide absent pour {demande!r} avec l'intent reel {intent!r}"
    )


def test_le_chat_pur_reste_econome(registre):
    """Ce que l'ecrasement `intent == chat` protege : le contexte d'outils. Une vraie
    conversation ne doit pas ouvrir computer_use sous pretexte de ce lot."""
    permis = _outils_permis(registre, "comment tu vas aujourd'hui", intent="chat")
    assert permis is not None
    assert "lumena_ide" not in permis
    assert len(permis) < 80, f"{len(permis)} outils permis pour un simple bonjour"


def test_LIMITE_ASSUMEE_un_filtre_de_mots_ne_lit_pas_le_contexte(registre):
    """« ouvre ton worskace dedans » (message reel de 23 h 17) ne contient AUCUN mot du
    champ IDE : « dedans » renvoie au tour precedent.

    Un filtre par mots-cles ne peut pas le savoir, et pretendre le contraire serait
    inventer une capacite. Ce test fige la limite au lieu de la cacher : la demande
    n'ouvre pas la categorie, et c'est `discover_tools` qui reste le filet.

    Dans le run, ce tour a quand meme reussi — l'outil etait deja dans la liste depuis
    le tour precedent, le filtre n'etant applique qu'une fois par requete.
    """
    assert not _MOTS_DE_L_IDE_RE.search("ouvre ton worskace dedans")


def test_une_demande_d_ide_n_ouvre_pas_tout_le_catalogue(registre):
    """Le garde d'economie, cote IDE cette fois : la branche ajoutee doit rester
    bornee. Sans cela, « ide » deviendrait un passe-partout."""
    permis = _outils_permis(registre, "ouvre ton ide", intent="chat")
    assert permis is not None
    assert "lumena_ide" in permis
    assert len(permis) < 200, f"{len(permis)} outils permis : la branche IDE derape"
