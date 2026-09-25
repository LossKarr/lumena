r"""Lot FILT-1 - les mots-cles courts ne matchent plus DANS les mots francais.

--- Ce que j'avais propose, et que la mesure a REFUTE ---

J'avais annonce a Charles qu'il fallait **retourner le principe** du bloc
`if intent == "chat"` : conserver les categories qu'une regle de mots-cles a matchees, au
lieu de les ecraser. Argument : cinq exceptions accumulees (peer, autonomy, documents,
IDE, arret de processus) signalent un principe a inverser.

**La mesure dit le contraire.** Les packs de `_CONTEXT_RULES` matchent par SOUS-CHAINE
(`mot in query_lower`), et sur de vraies phrases de conversation :

    je comprends pas ta reponse              -> ['files', 'git', 'github']
    c'est quoi la difference entre les deux  -> ['files', 'git']
    ouvre ton ide                            -> 6 categories, dont browser, lsp, skills
    cherche sur internet les prix du cuivre  -> ['git', 'github', 'stripe', 'web']

Le bloc `intent == "chat"` n'est donc PAS un defaut de conception : c'est le seul rempart
contre ce bruit. L'inverser aurait ouvert `git` + `github` sur presque chaque phrase
francaise contenant « pr ». **Mon lot etait une mauvaise idee, et je l'ai abandonne.**

--- Le vrai defaut, trouve en cherchant la cause ---

Six mots-cles matchent a l'interieur de mots ordinaires. Mesure sur un lexique de mots
courants d'une conversation technique :

    'pr'    -> 32 mots  (apprendre, comprends, prix, propose, premier, probleme...)  git, github
    'diff'  ->  4 mots  (difference, differencier, different, differents)            files, git
    'repo'  ->  2 mots  (reponse, repondre)                                          files, git
    'rt'    ->  2 mots  (important, importante)                                      social, web
    'port'  ->  2 mots  (important, importante)                                      network, security
    'fichier'->  1 mot  (fichiers — pluriel LEGITIME, laisse tel quel)               files, skills

Le mot « important » declenche a lui seul `social`, `web`, `network` ET `security`.

--- Le correctif ---

Ces cinq mots-cles sont testes en **MOT ENTIER**, pas en sous-chaine. Leurs usages
legitimes sont preserves : « fais une PR », « montre le diff », « clone le repo »,
« le port 8085 », « fais un RT ».

Le principe du bloc `chat` n'est PAS touche, et les cinq exceptions restent : ce sont des
regles precises et mesurees, pas de la dette.
"""
from __future__ import annotations

import pytest

from src.core_services.intent_classifier import classify_intent
from src.reasoning.react import ToolRegistry


@pytest.fixture(scope="module")
def registre():
    return ToolRegistry()


def _categories(registre, query):
    """La boucle des packs seule, sans l'ecrasement `chat`."""
    bas = query.lower()
    trouvees = set()
    for mots, cats in registre._CONTEXT_RULES:
        for mot in mots:
            if registre._mot_cle_present(mot, bas):
                trouvees |= cats
                break
    return trouvees


# -- 1. Le bruit mesure disparait -------------------------------------------

@pytest.mark.parametrize("phrase,interdit", [
    ("je comprends pas ta reponse", {"git", "github", "files"}),
    ("c'est quoi la difference entre les deux", {"git", "files"}),
    ("c'est important pour moi", {"social", "web", "network", "security"}),
    ("apprendre a te connaitre", {"git", "github"}),
    ("je te propose autre chose", {"git", "github"}),
    ("quel est le premier probleme", {"git", "github"}),
    ("ta reponse est surprenante", {"git", "github", "files"}),
])
def test_une_phrase_ordinaire_n_ouvre_plus_ces_categories(registre, phrase, interdit):
    trouvees = _categories(registre, phrase)
    fautives = trouvees & interdit
    assert not fautives, f"{phrase!r} ouvre encore {sorted(fautives)}"


def test_important_n_ouvre_plus_QUATRE_categories(registre):
    """Le cas le plus parlant : « important » contient `rt` ET `port`."""
    trouvees = _categories(registre, "c'est tres important")
    assert not trouvees & {"social", "web", "network", "security"}, sorted(trouvees)


# -- 2. Les usages LEGITIMES sont preserves ---------------------------------

@pytest.mark.parametrize("phrase,attendu", [
    ("ouvre une pr sur github", "github"),
    ("montre-moi le diff", "git"),
    ("clone le repo du projet", "git"),
    ("le port 8085 est occupe", "network"),
    ("scanne les ports ouverts", "network"),
])
def test_le_mot_entier_marche_toujours(registre, phrase, attendu):
    assert attendu in _categories(registre, phrase), (
        f"{phrase!r} ne trouve plus {attendu}"
    )


def test_le_pluriel_reste_reconnu(registre):
    """`fichier` -> `fichiers` : le pluriel est un usage legitime, on ne le casse pas."""
    assert "files" in _categories(registre, "liste-moi les fichiers du dossier")


# -- 3. Ce que le lot ne doit PAS changer -----------------------------------

@pytest.mark.parametrize("phrase,attendu", [
    ("ouvre ton ide", "computer_use"),
    ("ferme les serveurs que tu as ouvert", "agents"),
    ("prends un screenshot de l'ecran", "computer_use"),
    ("cree-moi un pdf de synthese", "documents"),
    ("cherche sur internet les prix du cuivre", "web"),
])
def test_les_demandes_reelles_gardent_leur_categorie(registre, phrase, attendu):
    """Les lots IDE-7, SRV-1 et les packs historiques doivent rester intacts.

    Le test emploie l'intent réellement calculé en production. Forcer `chat`
    rendait trois assertions rouges avant FILT-1 : screenshot, document et Web
    sont respectivement `tool_direct`, `react` et `react`.
    """
    registre._allowed_tools = None
    intent = classify_intent(phrase).value
    registre.apply_context_filter(phrase, intent=intent)
    permis = registre._allowed_tools
    assert permis is None or any(
        registre._tool_modules.get(n) == attendu for n in permis
    ), f"{phrase!r} n'expose plus la categorie {attendu}"


def test_le_bloc_chat_reste_un_rempart(registre):
    """Ce que la mesure a sauve : le bloc `intent == chat` n'est PAS inverse."""
    registre._allowed_tools = None
    registre.apply_context_filter("comment tu vas aujourd'hui", intent="chat")
    permis = registre._allowed_tools
    assert permis is not None
    assert len(permis) < 80, f"{len(permis)} outils pour un bonjour : le rempart a saute"


def test_les_expressions_multi_mots_ne_sont_pas_touchees(registre):
    """Seuls les mots-cles COURTS et ambigus passent en mot entier. Une expression
    réellement présente dans `_CONTEXT_RULES` garde son matching historique."""
    phrase = "ouvre une pull request detaillee"
    assert registre._mot_cle_present("pull request", phrase)
    assert "github" in _categories(registre, phrase)
