"""RF-3 — matrice byte-for-byte du prompt ReAct, et fermeture de dependances.

Lot RF-3 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md` :
`_build_react_prompt` (589 lignes) quitte `react.py` pour
`src/prompts/react_prompt.py`.

Ce fichier est ecrit AVANT l'extraction et le baseline est capture sur le code
d'origine. Il repond a la seule question qui compte : **le prompt produit
est-il exactement le meme ?**

--- Pourquoi une normalisation avant le hachage ---

Le prompt contient deux elements volatils, mesures : la date du jour
(`## Date actuelle: ...`) et des horodatages venant du magasin de reflexions.
Hacher le texte brut donnerait un test qui casse tous les jours pour rien. On
neutralise donc DATE, HORODATAGE et HEURE — et uniquement cela. Tout le reste
est compare a l'octet pres.

--- Pourquoi ce test est FAIL-CLOSED ---

Aucun `try/except` n'entoure la construction du prompt. Au premier essai de
RF-3, un `except Exception` dans le harnais de mesure avait transforme un
`NameError` en texte capture : les 20 comparaisons etaient marquees en ecart,
mais l'affichage ne montrait que le cote « avant » et j'ai lu « texte
identique ». Une exception doit tuer le scenario, tout de suite et bruyamment.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import re
import types
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "prompts" / "react_prompt.py"
INIT_PROMPTS = RACINE / "src" / "prompts" / "__init__.py"

# ══════════════════════════════════════════════════════════════════════════
#  Normalisation du contenu volatil
# ══════════════════════════════════════════════════════════════════════════

_MOIS = ("January|February|March|April|May|June|July|August|September|October|"
         "November|December|janvier|fevrier|février|mars|avril|mai|juin|juillet|"
         "aout|août|septembre|octobre|novembre|decembre|décembre")
_JOURS = ("Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|lundi|mardi|"
          "mercredi|jeudi|vendredi|samedi|dimanche")

_VOLATILE = (
    (re.compile(rf"(?:{_JOURS})\s+\d{{1,2}}\s+(?:{_MOIS})\s+\d{{4}}", re.I), "<DATE>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?"), "<HORODATAGE>"),
    (re.compile(r"\d{2}:\d{2}:\d{2}"), "<HEURE>"),
)


def normaliser(texte: str) -> str:
    for motif, jeton in _VOLATILE:
        texte = motif.sub(jeton, texte)
    return texte


def empreinte(texte: str) -> tuple[int, str]:
    n = normaliser(texte)
    return len(n), hashlib.sha256(n.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
#  Baseline — capture sur le code AVANT extraction (2026-08-27)
# ══════════════════════════════════════════════════════════════════════════
#
# Si une de ces empreintes bouge, le prompt a change. C'est soit une
# regression du refactor, soit une evolution VOLONTAIRE du prompt — et dans ce
# second cas la mise a jour du baseline doit etre explicite et justifiee, pas
# subie.
#
# REFERENCE RECAPTUREE le 2026-08-27 (lot RF-5b). Motif : les empreintes
# d'origine dependaient de trois magasins persistants que la suite de tests
# ecrit elle-meme, et elles ont derive de -63 caracteres sans qu'aucun lot n'en
# soit la cause — verifie par l'experience : la matrice echouait AUSSI sur le
# `react.py` d'avant RF-5b. Les trois sources sont desormais epinglees (voir
# `construire`), donc cette reference ne peut plus deriver.
#
# RECAPTUREE A NOUVEAU le 2026-08-29. Motif : les 20 comparaisons echouaient sur
# GitHub Actions, du MEME ecart de -3146 caracteres partout, alors qu'elles
# passaient en local. `loaded: 588 handlers` en CI contre 596 ici : le catalogue
# d'outils depend des dependances installees sur la machine.
#
# Le baseline n'etait pas faux, il etait NON PORTABLE — un defaut du test, pas
# du code. Le catalogue est desormais epingle (`CATALOGUE_FIGE`), donc les
# empreintes sont beaucoup plus courtes : elles mesurent le GABARIT du prompt,
# qui est ce que RF-3 a deplace, et plus l'inventaire d'outils, qui ne l'est pas.
#
# `test_l_empreinte_ne_depend_PAS_du_nombre_d_outils` verrouille cette propriete.

BASELINE: dict[str, dict] = {
    "01_chat_simple__gatesOFF": {"taille": 9467, "sha": "c663fc51d78a", "appels": 1},
    "01_chat_simple__gatesON": {"taille": 9467, "sha": "c663fc51d78a", "appels": 1},
    "02_agent_outils__gatesOFF": {"taille": 8963, "sha": "7073058964cd", "appels": 1},
    "02_agent_outils__gatesON": {"taille": 8963, "sha": "7073058964cd", "appels": 1},
    "03_modele_faible__gatesOFF": {"taille": 9637, "sha": "d6136391dc76", "appels": 0},
    "03_modele_faible__gatesON": {"taille": 9637, "sha": "d6136391dc76", "appels": 0},
    "04_avec_skills__gatesOFF": {"taille": 10262, "sha": "ea1197749fed", "appels": 1},
    "04_avec_skills__gatesON": {"taille": 10262, "sha": "ea1197749fed", "appels": 1},
    "05_runtime_ide__gatesOFF": {"taille": 9329, "sha": "38e6b927e05d", "appels": 1},
    "05_runtime_ide__gatesON": {"taille": 9329, "sha": "38e6b927e05d", "appels": 1},
    "06_runtime_web__gatesOFF": {"taille": 8782, "sha": "cd1bd28707c1", "appels": 1},
    "06_runtime_web__gatesON": {"taille": 8782, "sha": "cd1bd28707c1", "appels": 1},
    "07_tache_id__gatesOFF": {"taille": 8970, "sha": "444fe9e06593", "appels": 1},
    "07_tache_id__gatesON": {"taille": 8970, "sha": "444fe9e06593", "appels": 1},
    "08_contexte_long__gatesOFF": {"taille": 13000, "sha": "ad4c896bb4bf", "appels": 1},
    "08_contexte_long__gatesON": {"taille": 13000, "sha": "ad4c896bb4bf", "appels": 1},
    "09_provider_api__gatesOFF": {"taille": 8969, "sha": "666b9ed0eb58", "appels": 1},
    "09_provider_api__gatesON": {"taille": 9766, "sha": "c1685040ff10", "appels": 1},
    "10_abonnement_codex__gatesOFF": {"taille": 9315, "sha": "2be2bd8dd704", "appels": 1},
    "10_abonnement_codex__gatesON": {"taille": 10074, "sha": "dc8dccba6c86", "appels": 1},
}


# ══════════════════════════════════════════════════════════════════════════
#  Les 10 scenarios
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def registre():
    from src.reasoning.tool_registry import ToolRegistry

    return ToolRegistry()


def _rt(**kw):
    return types.SimpleNamespace(
        channel=kw.get("channel", "web"),
        workspace=kw.get("workspace"),
        active_file=kw.get("active_file"),
        open_files=kw.get("open_files", []),
    )


def scenarios(registre) -> dict[str, tuple[dict, str]]:
    """Dix etats couvrant les branches du prompt.

    Les deux derniers ont un MODELE ACTIF. Sans lui, `_active_model_id` reste
    vide, le bloc de hints provider ne se declenche jamais, et le drapeau
    `REACT_QUALITY_GATES` n'a aucun effet mesurable : la moitie de la matrice
    serait des doublons. Mesure avec eux : +797 o et +759 o entre ON et OFF.
    """
    return {
        "01_chat_simple": (dict(conversation_context="Bonjour."),
                           "Bonjour, qui es-tu ?"),
        "02_agent_outils": (dict(tools=registre, conversation_context="Lis a.py."),
                            "Lis le fichier a.py et resume-le."),
        "03_modele_faible": (dict(tools=registre, is_weak_model=True,
                                  conversation_context="Salut."), "Salut."),
        "04_avec_skills": (dict(tools=registre, active_skills_context="pdf, docx",
                                conversation_context="Fais un PDF."),
                           "Cree-moi un PDF de synthese."),
        "05_runtime_ide": (dict(tools=registre,
                                runtime_ctx=_rt(channel="ide", workspace="C:/x",
                                                active_file="a.py",
                                                open_files=["a.py", "b.py"])),
                           "Corrige le bug dans a.py."),
        "06_runtime_web": (dict(tools=registre, runtime_ctx=_rt(channel="web")),
                           "Ouvre le site et verifie la page contact."),
        "07_tache_id": (dict(tools=registre, task_id="task-42",
                             conversation_context="Poursuis la mission."),
                        "Continue la mission en cours."),
        "08_contexte_long": (dict(tools=registre, conversation_context="x" * 4000,
                                  active_skills_context="stripe"),
                             "Fais un site vitrine complet avec animations."),
        "09_provider_api": (dict(tools=registre,
                                 llm_meta_getter=lambda: {"model_used": "deepseek-chat"},
                                 conversation_context="Analyse ce module."),
                            "Analyse le module de paiement."),
        "10_abonnement_codex": (dict(tools=registre,
                                     llm_meta_getter=lambda: {"model_used": "gpt-5.6-codex"},
                                     conversation_context="Analyse ce module."),
                                "Analyse le module de paiement."),
    }


# ── Les trois sources VOLATILES du prompt, epinglees ─────────────────────────
#
# INCIDENT du 2026-08-27, lot RF-5b : les 20 comparaisons sont devenues rouges
# (-63 caracteres, identique sur les 20) SANS qu'aucun lot n'en soit la cause.
# Verifie par l'experience : la matrice echoue AUSSI sur le `react.py` d'avant
# RF-5b. Le coupable etait cette preuve elle-meme.
#
# Le prompt lit TROIS magasins persistants que la suite de tests ecrit :
#
#   ## Erreurs recentes      -> src.autonomy.ops_handlers._load_state()
#   ## Reseau Lumena         -> src.runtime.peer_awareness
#   ## Memoire & identite    -> ChromaDB, via _build_identity_context
#
# La section « Limites connues » de RF-3 annoncait ce risque pour le magasin de
# reflexions. Il etait plus large : trois sources, pas une.
#
# Correctif : on EPINGLE les entrees au lieu d'exclure du texte. Rien ne sort du
# hachage — les 122 000 caracteres restent compares a l'octet pres, outils,
# regles et structure compris. Seules les trois sources deviennent
# deterministes. La paresse ChromaDB reste prouvee separement par
# `test_modele_faible_ne_declenche_aucune_recherche_memoire` et
# `test_un_cache_identite_deja_rempli_n_est_pas_recalcule`, qui n'utilisent pas
# `construire`.

IDENTITE_FIGEE = "Identite epinglee pour la matrice RF-3."
ETAT_OPS_FIGE = {"_idempotence_registry": {}}
RESEAU_FIGE = ""

# ── 4e source volatile, trouvee le 2026-08-29 sur GitHub Actions ────────────
#
# Les 20 comparaisons echouaient de -3146 caracteres, le MEME ecart partout.
# Cause : `loaded: 588 handlers` en CI contre 596 en local. Les handlers se
# chargent CONDITIONNELLEMENT selon les dependances presentes (graceful
# degradation), donc `get_tools_description()` rend un catalogue de taille
# VARIABLE selon la machine.
#
# Une empreinte absolue ne pouvait donc passer que sur UNE machine. Le baseline
# n'etait pas faux : il etait NON PORTABLE.
#
# On epingle le catalogue comme on epingle deja l'identite et les trois magasins.
# Ce que RF-3 a deplace, c'est le GABARIT du prompt — pas l'inventaire d'outils.
# Le gabarit reste compare a l'octet pres ; l'inventaire cesse de faire deriver.
#
# Les cinq marqueurs ci-dessous sont ceux que le gabarit teste (`in tools_desc`,
# react_prompt.py:145,193,196,204,215) : les garder ACTIVE les branches
# navigateur et MCP, qui resteraient sinon jamais parcourues par la matrice.
CATALOGUE_FIGE = """- read_file: lit un fichier
- write_file: ecrit un fichier
- browser_navigate: ouvre une page
- request_mcp_capability: demande une capacite MCP
- run_mcp_autonomy: execute une tache MCP
- resume_mcp_task: reprend une tache MCP
- request_mcp_ticket: ouvre un ticket MCP
"""


def construire(kw: dict, requete: str) -> tuple[str, int]:
    """Construit le prompt et compte les appels a `_build_identity_context`.

    FAIL-CLOSED : aucune exception n'est rattrapee. Si la construction echoue,
    le test echoue — il ne capture pas le message d'erreur comme s'il etait un
    prompt.

    Les trois sources volatiles sont epinglees (voir ci-dessus) : sans cela la
    matrice derive des qu'un autre test ecrit dans un de ces magasins.
    """
    from src.autonomy import ops_handlers
    from src.reasoning.react import ReActLoop
    from src.runtime import peer_awareness

    _vrai_ops = ops_handlers._load_state
    _vrai_reseau = peer_awareness.build_peer_awareness_context
    ops_handlers._load_state = lambda *a, **k: dict(ETAT_OPS_FIGE)
    peer_awareness.build_peer_awareness_context = lambda *a, **k: RESEAU_FIGE
    try:
        return _construire_sans_derive(kw, requete)
    finally:
        ops_handlers._load_state = _vrai_ops
        peer_awareness.build_peer_awareness_context = _vrai_reseau


def _construire_sans_derive(kw: dict, requete: str) -> tuple[str, int]:
    from src.reasoning.react import ReActLoop

    boucle = ReActLoop(**kw)
    appels = {"n": 0}
    vrai = boucle._build_identity_context

    def espion(q, _v=vrai, _c=appels):
        _c["n"] += 1
        _v(q)                      # le vrai chemin est bien parcouru
        return IDENTITE_FIGEE      # mais son CONTENU ne fait pas deriver le hash

    boucle._build_identity_context = espion
    # Le catalogue depend de la machine (588 handlers en CI, 596 en local) : on
    # l'epingle pour que l'empreinte mesure le GABARIT, jamais l'inventaire.
    boucle.tools.get_tools_description = lambda *a, **k: CATALOGUE_FIGE
    texte = boucle._build_react_prompt(requete)
    assert isinstance(texte, str) and texte, "le prompt est vide"
    return texte, appels["n"]


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 20 comparaisons
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("gates", [True, False], ids=["gatesON", "gatesOFF"])
@pytest.mark.parametrize("nom", sorted(scenarios(None)))
def test_le_prompt_est_identique_au_baseline(nom, gates, registre, monkeypatch):
    import src.config.codeagent_flags as flags

    monkeypatch.setattr(flags, "REACT_QUALITY_GATES", gates, raising=False)
    kw, requete = scenarios(registre)[nom]
    texte, appels = construire(kw, requete)
    taille, sha = empreinte(texte)

    cle = f"{nom}__{'gatesON' if gates else 'gatesOFF'}"
    attendu = BASELINE[cle]
    assert taille == attendu["taille"], (
        f"{cle} : longueur normalisee {taille} au lieu de {attendu['taille']} "
        f"({taille - attendu['taille']:+d} caracteres)"
    )
    assert sha[:12] == attendu["sha"], (
        f"{cle} : le prompt a change (sha {sha[:12]} au lieu de {attendu['sha']})"
    )
    assert appels == attendu["appels"], (
        f"{cle} : {appels} appel(s) a _build_identity_context au lieu de "
        f"{attendu['appels']} — la paresse a change"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. Les quatre verifications explicites exigees
# ══════════════════════════════════════════════════════════════════════════


def test_modele_faible_ne_declenche_aucune_recherche_memoire(registre, monkeypatch):
    """`_build_identity_context` fait une recherche ChromaDB (~350 ms mesurees
    en production). Sur modele faible elle n'a JAMAIS lieu. Rendre le calcul
    eager la ferait apparaitre — changement de comportement ET de cout."""
    kw, requete = scenarios(registre)["03_modele_faible"]
    _, appels = construire(kw, requete)
    assert appels == 0, f"{appels} recherche(s) memoire sur modele faible"


def test_un_cache_identite_deja_rempli_n_est_pas_recalcule(registre):
    """Le cache est la raison d'etre de la paresse : une seule recherche par
    run, pas une par construction de prompt."""
    from src.reasoning.react import ReActLoop

    kw, requete = scenarios(registre)["02_agent_outils"]
    boucle = ReActLoop(**kw)
    boucle._identity_ctx_cache = "IDENTITE DEJA EN CACHE"

    appels = {"n": 0}
    vrai = boucle._build_identity_context

    def espion(q, _v=vrai, _c=appels):
        _c["n"] += 1
        _v(q)                      # le vrai chemin est bien parcouru
        return IDENTITE_FIGEE      # mais son CONTENU ne fait pas deriver le hash

    boucle._build_identity_context = espion
    texte = boucle._build_react_prompt(requete)

    assert appels["n"] == 0, "le cache plein a quand meme declenche un recalcul"
    assert "IDENTITE DEJA EN CACHE" in texte, "le cache n'a pas ete utilise"


@pytest.mark.parametrize(
    "nom,ecart_minimal",
    [("09_provider_api", 400), ("10_abonnement_codex", 400)],
)
def test_le_drapeau_qualite_change_le_prompt_sur_un_modele_actif(
    nom, ecart_minimal, registre, monkeypatch
):
    """Sans modele actif, `REACT_QUALITY_GATES` n'a aucun effet et la moitie de
    la matrice serait des doublons. Ces deux scenarios prouvent que le bloc de
    hints provider est bien exerce."""
    import src.config.codeagent_flags as flags

    kw, requete = scenarios(registre)[nom]

    monkeypatch.setattr(flags, "REACT_QUALITY_GATES", True, raising=False)
    avec, _ = construire(kw, requete)
    monkeypatch.setattr(flags, "REACT_QUALITY_GATES", False, raising=False)
    sans, _ = construire(kw, requete)

    assert avec != sans, f"{nom} : le drapeau n'a aucun effet"
    assert len(avec) - len(sans) >= ecart_minimal, (
        f"{nom} : ecart de {len(avec) - len(sans)} o, attendu >= {ecart_minimal}"
    )


def test_le_prompt_est_deterministe(registre):
    """Deux constructions identiques doivent donner le meme octet. Sinon le
    baseline ne vaut rien."""
    kw, requete = scenarios(registre)["02_agent_outils"]
    a, _ = construire(kw, requete)
    b, _ = construire(kw, requete)
    assert normaliser(a) == normaliser(b), "le prompt n'est pas deterministe"


# ══════════════════════════════════════════════════════════════════════════
#  3. Fermeture de dependances — le defaut du premier essai
# ══════════════════════════════════════════════════════════════════════════


def _globaux_non_resolus(chemin: Path, nom_fonction: str) -> list[str]:
    """Noms lus par la fonction et introuvables dans la portee du module.

    Couvre les quatre familles : fonctions, constantes, imports, ET les noms
    lies par la signature. Au premier essai de RF-3, neuf symboles manquaient,
    dont `query` — le parametre de la methode, supprime sans etre rebinde.
    """
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    portee_module = {n.name for n in arbre.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for n in arbre.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            portee_module |= {a.asname or a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.Assign):
            portee_module |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            portee_module.add(n.target.id)

    fn = next((n for n in ast.walk(arbre)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == nom_fonction), None)
    assert fn is not None, f"{nom_fonction} introuvable dans {chemin.name}"

    locaux: set[str] = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            locaux.add(x.id)
        elif isinstance(x, ast.arg):
            locaux.add(x.arg)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            locaux |= {a.asname or a.name.split(".")[0] for a in x.names}
        elif isinstance(x, ast.ExceptHandler) and x.name:
            locaux.add(x.name)
        elif isinstance(x, ast.comprehension):
            for y in ast.walk(x.target):
                if isinstance(y, ast.Name):
                    locaux.add(y.id)

    lus = {x.id for x in ast.walk(fn)
           if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
    return sorted(lus - locaux - portee_module - set(dir(builtins)))


@pytest.mark.skipif(not NOUVEAU.exists(), reason="RF-3 pas encore applique")
def test_le_module_extrait_n_a_aucun_nom_global_non_resolu():
    manquants = _globaux_non_resolus(NOUVEAU, "construire_prompt_react")
    assert manquants == [], (
        f"noms introuvables dans le module extrait : {manquants} — "
        "la fermeture de dependances est incomplete"
    )


@pytest.mark.skipif(not NOUVEAU.exists(), reason="RF-3 pas encore applique")
def test_le_module_extrait_ne_reference_jamais_self():
    """Ni `self.X`, ni `getattr(self, ...)`, ni `Classe.methode(self, ...)`.

    Le premier essai avait laisse `ReActLoop._document_route_for_run(self, query)` :
    un `self` passe en ARGUMENT NU, invisible a un balayage de `self.X`."""
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [f"l.{x.lineno}" for x in ast.walk(arbre)
              if isinstance(x, ast.Name) and x.id == "self"]
    assert fautes == [], f"references a `self` dans le module extrait : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  4. Contraintes d'architecture (invariants 20 a 22)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not NOUVEAU.exists(), reason="RF-3 pas encore applique")
def test_aucun_import_prompts_vers_reasoning():
    """Invariant 21 : une SEULE inversion admise, et ce n'est pas celle-la.
    `OS_NAME` et `_build_model_specific_hints` viennent de `react_config.py` ;
    ils passent par l'entree, pas par un import."""
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom):
            cible = ("." * n.level) + (n.module or "")
            if "reasoning" in cible:
                fautes.append(ast.unparse(n))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if "reasoning" in a.name:
                    fautes.append(ast.unparse(n))
    assert fautes == [], f"import prompts -> reasoning : {fautes}"


def test_le_paquet_prompts_garde_sa_surface_historique():
    """Invariant 22 : importer `src.prompts` ne doit charger ni le runtime
    ReAct ni `src.reasoning`."""
    source = INIT_PROMPTS.read_text(encoding="utf-8")
    assert "react_prompt" not in source, (
        "react_prompt est expose par src/prompts/__init__.py — "
        "tout import de src.prompts chargerait alors le runtime ReAct"
    )
    arbre = ast.parse(source)
    exportes = set()
    for n in arbre.body:
        if isinstance(n, ast.ImportFrom):
            exportes |= {a.asname or a.name for a in n.names}
    assert exportes == {"PromptBuilder", "PromptSection", "get_prompt_builder"}, (
        f"surface du paquet modifiee : {sorted(exportes)}"
    )


@pytest.mark.skipif(not NOUVEAU.exists(), reason="RF-3 pas encore applique")
def test_le_builder_generique_n_est_pas_branche():
    """Invariant 20 : RF-3 deplace l'assemblage existant, il ne le remplace pas
    par le `PromptBuilder` generique ni par ses textes."""
    # On verifie l'USAGE, pas la mention : l'en-tete du module explique
    # justement que ces textes ne sont PAS reutilises, et une recherche de
    # sous-chaine naive echouerait sur sa propre documentation.
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    interdits = {"get_prompt_builder", "DEFAULT_IDENTITY", "DEFAULT_RULES", "PromptBuilder"}
    fautes = sorted({
        x.id for x in ast.walk(arbre)
        if isinstance(x, ast.Name) and x.id in interdits
    } | {
        x.attr for x in ast.walk(arbre)
        if isinstance(x, ast.Attribute) and x.attr in interdits
    })
    assert fautes == [], f"le builder generique est branche : {fautes}"

    importes = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom) and "builder" in (n.module or ""):
            importes |= {a.name for a in n.names}
    assert importes == set(), f"import depuis builder.py : {sorted(importes)}"


@pytest.mark.skipif(not NOUVEAU.exists(), reason="RF-3 pas encore applique")
def test_react_conserve_une_coquille_qui_porte_la_mutation():
    """La SEULE mutation du lot — l'ecriture du cache d'identite — reste chez
    `ReActLoop`. C'est l'exigence du plan, et c'est ce qui permet de garder la
    paresse sans passer `self` au nouveau module."""
    source = REACT.read_text(encoding="utf-8")
    assert "def _build_react_prompt" in source, "la coquille a disparu de react.py"
    assert "_identity_ctx_cache" in source, "l'ecriture du cache a quitte react.py"
    assert "construire_prompt_react" in source, "react.py n'appelle pas le module extrait"


# ══════════════════════════════════════════════════════════════════════════
#  5. Preuves COMPORTEMENTALES adossees au repointage R2
# ══════════════════════════════════════════════════════════════════════════
#
# Cinq tests du depot affirmaient qu'une chaine se trouve dans le TEXTE SOURCE
# de `react.py`. Les six chaines concernees sont parties avec le corps de
# `_build_react_prompt` vers `src/prompts/react_prompt.py` : elles y sont
# toutes presentes, aucune n'a ete perdue.
#
# La section 6 du plan de refactor n'autorise a repointer leur fichier cible
# qu'a une condition : fournir d'abord une preuve COMPORTEMENTALE equivalente
# et une preuve d'integration du nouveau module. Les tests ci-dessous sont
# cette condition. Ils ne cherchent aucune chaine dans aucun fichier : ils
# CONSTRUISENT le prompt en passant par `ReActLoop._build_react_prompt`, donc
# par la coquille de `react.py` — ce qui en fait aussi la preuve d'integration
# demandee. Ils survivraient a un prochain deplacement du code.
#
# Tests repointes, dans l'ordre :
#   tests/security/test_production_hardening.py
#     - TestS2ReactWarnings::test_sandbox_except_logs_warning
#     - TestS2ReactWarnings::test_agent_memory_except_logs_warning
#   tests/websites/test_cursor_ide_local_integration.py
#     - test_react_prompt_adds_cursor_ide_rule_for_project_requests
#   tests/websites/test_video_remotion.py
#     - TestReactVideoContext::test_react_prompt_contains_video_marker
#   tests/documents/test_document_studio_routing.py
#     - test_react_prompt_declares_studio_mandatory_before_legacy_paths


@pytest.fixture()
def warnings_loguru():
    """Capture les `logger.warning` emis pendant la construction du prompt."""
    from loguru import logger

    captures: list[str] = []
    handle = logger.add(captures.append, level="WARNING", format="{message}")
    try:
        yield captures
    finally:
        logger.remove(handle)


def test_comportement_la_section_ide_apparait_quand_le_workspace_est_connu(registre):
    """Remplace la lecture de `ide_runtime_context` dans le source de react.py.

    Le nom de la variable locale n'interesse personne : ce qui compte est que
    le workspace IDE arrive DANS le prompt quand il est connu, et seulement
    alors.
    """
    sans, _ = construire(dict(tools=registre), "cree un projet web fullstack")
    assert "## CONTEXTE IDE (SOURCE DE VERITE):" not in sans

    registre_ide = type(registre)()
    registre_ide.ide_context = {
        "workspace_path": "C:/travail/projet-rf3",
        "active_file_path": "main.py",
        "open_files": ["main.py", "utils.py"],
    }
    avec, _ = construire(dict(tools=registre_ide), "cree un projet web fullstack")
    assert "## CONTEXTE IDE (SOURCE DE VERITE):" in avec
    assert "C:/travail/projet-rf3" in avec
    assert "main.py" in avec
    assert "utils.py" in avec

    # La regression que le test d'origine gardait : plus d'injection statique
    # cursor-ide-local pour toute demande de projet.
    assert "PRIORITE IDE LOCAL (cursor-ide-local)" not in sans
    assert "ACTION: cursor_ide_local" not in sans


def test_comportement_le_canal_ide_ajoute_le_mode_developpement(registre):
    """Second declencheur de la meme section : `runtime_ctx.channel == "ide"`."""
    ctx = types.SimpleNamespace(channel="ide", workspace=None,
                                active_file=None, open_files=[])
    avec, _ = construire(dict(tools=registre, runtime_ctx=ctx), "Corrige le bug.")
    assert "## CANAL IDE" in avec

    ctx_web = types.SimpleNamespace(channel="web", workspace=None,
                                    active_file=None, open_files=[])
    sans, _ = construire(dict(tools=registre, runtime_ctx=ctx_web), "Corrige le bug.")
    assert "## CANAL IDE" not in sans


def test_comportement_la_section_video_arrive_dans_le_prompt(registre):
    """Remplace la lecture de `video_context` dans le source de react.py.

    Le test d'origine cherchait le nom de la variable ; celui-ci verifie que
    la section video est reellement injectee, et qu'elle nomme l'outil et les
    templates que le moteur declare.
    """
    from src.tools.remotion_engine import VIDEO_TEMPLATES

    texte, _ = construire(dict(tools=registre), "Fais-moi une video de presentation.")
    assert "## GENERATION VIDEO (Remotion)" in texte
    assert "generate_video" in texte
    # Les trois noms que le prompt et le moteur partagent a l'identique.
    for modele in ("presentation", "social_short", "explainer"):
        assert modele in VIDEO_TEMPLATES, f"template disparu du moteur : {modele}"
        assert modele in texte, f"template absent du prompt : {modele}"

    # CONSTAT, pas correctif : le prompt annonce « square (1:1) » alors que la
    # cle du moteur est `square_social`. Cette derive est ANTERIEURE a RF-3 —
    # le lot deplace le texte sans le toucher. Elle est consignee ici pour ne
    # pas etre reperdue, et fera l'objet d'une decision separee.
    assert "square" in texte
    assert "square_social" in VIDEO_TEMPLATES
    assert len(VIDEO_TEMPLATES) >= 4


def test_comportement_studio_est_declare_obligatoire_avant_les_chemins_legacy(registre):
    """Remplace la lecture des deux chaines Studio dans le source de react.py.

    L'affirmation du test d'origine est dans son nom : Studio doit etre declare
    AVANT les chemins legacy. Une recherche de sous-chaine ne verifiait pas cet
    ordre ; ici on le mesure.
    """
    texte, _ = construire(dict(tools=registre),
                          "Cree-moi une facture pour le client Dupont.")

    i_studio = texte.find("`generate_studio_document` \u2192 OBLIGATOIRE")
    i_interdit = texte.find("N'utilise PAS create_pdf, Python ou CodeAgent")
    i_legacy = texte.find("`create_pdf`   \u2192 rapport libre")

    assert i_studio > 0, "la regle Studio n'atteint pas le prompt"
    assert i_interdit > 0, "l'interdiction des chemins legacy n'atteint pas le prompt"
    assert i_legacy > 0, "le chemin legacy create_pdf a disparu du prompt"
    assert i_studio < i_interdit < i_legacy, (
        "Studio n'est plus declare avant les chemins legacy : "
        f"studio={i_studio} interdit={i_interdit} legacy={i_legacy}"
    )


def test_comportement_un_echec_du_contexte_sandbox_logue_un_warning(
    registre, warnings_loguru, monkeypatch
):
    """Remplace `test_sandbox_except_logs_warning`.

    Le test d'origine cherchait le message dans le source, ce qui prouvait sa
    presence mais jamais son emission. Ici on fait echouer la lecture du mode
    sandbox et on verifie les deux choses qui comptent : le warning part, et
    le prompt est quand meme produit.
    """
    from src.utils import docker_sandbox

    def _explose():
        raise RuntimeError("docker injoignable (simule RF-3)")

    monkeypatch.setattr(docker_sandbox, "get_sandbox_mode", _explose)

    texte, _ = construire(dict(tools=registre), "Liste les processus Windows.")
    assert texte, "l'echec sandbox a emporte tout le prompt"
    assert "## SANDBOX DOCKER" not in texte

    emis = [m for m in warnings_loguru if "Sandbox context injection failed" in m]
    assert emis, f"aucun warning sandbox emis ; captures = {warnings_loguru}"
    assert "docker injoignable (simule RF-3)" in emis[0], (
        "le warning ne porte pas la cause reelle"
    )


def test_comportement_un_echec_de_la_memoire_agent_logue_un_warning(
    registre, warnings_loguru
):
    """Remplace `test_agent_memory_except_logs_warning`.

    L'identite est calculee paresseusement derriere la coquille de `react.py` ;
    on la fait echouer et on verifie que le warning part, que le prompt survit,
    et que la section memoire est simplement absente.
    """
    from src.reasoning.react import ReActLoop

    boucle = ReActLoop(tools=registre, is_weak_model=False)

    def _explose(_query):
        raise RuntimeError("ChromaDB indisponible (simule RF-3)")

    boucle._build_identity_context = _explose

    texte = boucle._build_react_prompt("Analyse le module de paiement.")
    assert texte, "l'echec memoire a emporte tout le prompt"
    assert "## Memoire & identite:" not in texte

    emis = [m for m in warnings_loguru if "Agent memory inject failed" in m]
    assert emis, f"aucun warning memoire emis ; captures = {warnings_loguru}"
    assert "ChromaDB indisponible (simule RF-3)" in emis[0], (
        "le warning ne porte pas la cause reelle"
    )


def test_comportement_le_modele_faible_ne_declenche_jamais_la_memoire(registre):
    """Corollaire du precedent : sur modele faible, la branche memoire n'est
    meme pas tentee — donc aucun warning possible, et aucune recherche."""
    from src.reasoning.react import ReActLoop

    boucle = ReActLoop(tools=registre, is_weak_model=True)

    def _explose(_query):  # pragma: no cover — doit rester inatteignable
        raise AssertionError("la memoire a ete consultee sur un modele faible")

    boucle._build_identity_context = _explose
    texte = boucle._build_react_prompt("Salut.")
    assert texte
    assert "## Memoire & identite:" not in texte


def test_l_epinglage_des_sources_volatiles_tient(registre):
    """Ferme l'incident du 2026-08-27.

    Les trois sources volatiles sont epinglees dans `construire`. Ce test
    verifie que l'epinglage RESISTE : on modifie reellement les magasins
    persistants, et l'empreinte ne doit pas bouger d'un octet.

    Sans lui, la matrice redeviendrait silencieusement fragile a la premiere
    ecriture d'un autre test dans un de ces magasins.
    """
    from src.autonomy import ops_handlers
    from src.runtime import peer_awareness

    kw, requete = scenarios(registre)["01_chat_simple"]
    _, sha_avant = empreinte(construire(kw, requete)[0])

    vrai_ops = ops_handlers._load_state
    vrai_reseau = peer_awareness.build_peer_awareness_context
    ops_handlers._load_state = lambda *a, **k: {
        "_idempotence_registry": {
            "bonjour_test:1": {"status": "FAILURE", "error": "echec fabrique",
                               "ts": "2026-08-27T12:00"},
        }
    }
    peer_awareness.build_peer_awareness_context = lambda *a, **k: (
        "\n## Reseau Lumena :\n- pair fabrique\n"
    )
    try:
        _, sha_apres = empreinte(construire(kw, requete)[0])
    finally:
        ops_handlers._load_state = vrai_ops
        peer_awareness.build_peer_awareness_context = vrai_reseau

    assert sha_avant == sha_apres, (
        "l'epinglage ne tient plus : le contenu des magasins persistants "
        "atteint de nouveau l'empreinte"
    )


def test_le_contenu_de_l_identite_ne_fait_pas_deriver_l_empreinte(registre):
    """L'identite reelle vient de ChromaDB. Le vrai chemin est bien parcouru —
    le compteur d'appels le prouve — mais son CONTENU est remplace par une
    valeur figee, sinon chaque ecriture en memoire casserait les 20 empreintes.
    """
    kw, requete = scenarios(registre)["01_chat_simple"]
    texte, appels = construire(kw, requete)
    assert appels == 1, "le vrai chemin d'identite n'est plus parcouru"
    assert IDENTITE_FIGEE in texte, "l'identite epinglee n'atteint pas le prompt"


# ══════════════════════════════════════════════════════════════════════════
#  PORTABILITE — l'empreinte ne doit dependre d'AUCUNE machine
# ══════════════════════════════════════════════════════════════════════════
#
# Ajoute le 2026-08-29 apres l'echec CI. Sans ce test, la matrice pouvait
# redevenir non portable au premier oubli d'epinglage, et l'echec ne serait
# apparu qu'apres un push — jamais en local.


def test_l_empreinte_ne_depend_PAS_du_nombre_d_outils(monkeypatch):
    """588 handlers en CI, 596 en local : l'empreinte doit etre la MEME.

    On construit le meme scenario avec deux registres dont les catalogues n'ont
    rien a voir. Si les empreintes divergent, c'est que l'inventaire d'outils
    fuit a nouveau dans la reference — et le test redevient intestable ailleurs.
    """
    import src.config.codeagent_flags as flags
    from src.reasoning.tool_registry import ToolRegistry

    monkeypatch.setattr(flags, "REACT_QUALITY_GATES", True, raising=False)

    complet = ToolRegistry()
    ampute = ToolRegistry()
    ampute.get_tools_description = lambda *a, **k: "- un_seul_outil: rien\n"

    kw_a, req = scenarios(complet)["02_agent_outils"]
    kw_b, _ = scenarios(ampute)["02_agent_outils"]

    a, _ = construire(kw_a, req)
    b, _ = construire(kw_b, req)

    assert empreinte(a) == empreinte(b), (
        "l'empreinte depend encore du catalogue d'outils : la matrice ne "
        "passera pas sur une machine ou toutes les dependances optionnelles ne "
        "sont pas installees (cas GitHub Actions du 2026-08-29)."
    )


def test_le_catalogue_epingle_active_bien_les_branches_conditionnelles():
    """`CATALOGUE_FIGE` doit porter les cinq marqueurs testes par le gabarit.

    Sans eux, les sections navigateur et MCP du prompt ne seraient JAMAIS
    parcourues par la matrice : on figerait un prompt ampute sans le savoir.
    """
    for marqueur in ("browser_", "request_mcp_capability", "run_mcp_autonomy",
                     "resume_mcp_task", "request_mcp_ticket"):
        assert marqueur in CATALOGUE_FIGE, marqueur

    gabarit = NOUVEAU.read_text(encoding="utf-8")
    attendus = set(re.findall(r'"([^"]+)" in tools_desc', gabarit))
    manquants = sorted(m for m in attendus if m not in CATALOGUE_FIGE)
    assert not manquants, (
        f"le gabarit teste des marqueurs absents du catalogue epingle : "
        f"{manquants} — ces branches ne sont plus couvertes par la matrice."
    )
