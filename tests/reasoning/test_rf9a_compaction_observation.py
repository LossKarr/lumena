"""RF-9a — première feuille de `_run_internal` : l'ingestion d'observation.

--- Pourquoi celle-ci, et pourquoi maintenant ---

Le §15 autorise RF-9 après signature de RF-1 à RF-8, et impose d'« extraire
seulement des feuilles cohérentes », chacune rendant « une décision typée ».
Il nomme six feuilles ; **« ingestion d'observation » en fait partie**.

L'audit AST de `_run_internal` a trouvé **91 blocs purs** (aucun `continue`,
`break`, `return`, `await`, aucun effet, aucune mutation de `self`). Le plus
gros groupe cohérent est la compaction des observations volumineuses,
l. 8737-8838 — 102 lignes, **11 lectures / 11 écritures**.

--- Le critère du §15 n'est PAS le nombre de lignes ---

    « Après RF-1 à RF-8, une cible raisonnable est 8 500 à 10 000 lignes »

`react.py` est à **9 868** : la cible est déjà atteinte. Le §15 poursuit :

    « Une réduction supplémentaire n'est acceptée que si elle diminue
      réellement l'état partagé. »

**RF-9 se juge donc en état partagé.** Mesure d'entrée : 672 variables locales,
44 de portée lexicale > 1 000 lignes, 25 > 3 000 lignes.

⚠️ Précision de vocabulaire (revue externe, retenue) : ces chiffres sont des
PORTÉES LEXICALES entre première écriture et dernière lecture, pas une analyse
de vivacité sur le graphe de flot. Excellent indicateur de confusion de
nommage ; pas une preuve que la valeur reste active sur tous les chemins.

--- La décision extraite ---

    (tool_name, contenu, surface_chat) -> Optional[str]

`None` = pas de compaction. Sinon, le corps compacté. La reconstruction du
`ReActStep` et le `logger.debug` restent dans la boucle : ce sont des effets.

--- Ce que RF-9a NE fait PAS ---

Ni la boucle, ni ses 48 `continue`, ni ses 33 retours, ni les 77 `try`, ni les
48 imports locaux, ni le streaming. Le §15 l'interdit explicitement, et l'audit
de cette nuit a montré pourquoi : les deux trous de vérité (FIX-1, FIX-2)
vivaient dans cette fonction, invisibles.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
MODULE = RACINE / "src" / "reasoning" / "observation_synthesis.py"


def _run_internal():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return next(n for n in cls.body if getattr(n, "name", "") == "_run_internal")


# ══════════════════════════════════════════════════════════════════════════
#  1. La decision existe, elle est PURE et TYPEE
# ══════════════════════════════════════════════════════════════════════════


def test_la_decision_existe():
    from src.reasoning.observation_synthesis import compact_observation_body

    sig = inspect.signature(compact_observation_body)
    assert list(sig.parameters)[:3] == ["tool_name", "content", "is_chat_surface"]


def test_la_decision_est_PURE():
    """§15 : « chaque feuille retourne une decision typee ». Aucun effet, sinon
    ce n'est pas une feuille — c'est un morceau de boucle deplace."""
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fn = next(n for n in arbre.body
              if isinstance(n, ast.FunctionDef) and n.name == "compact_observation_body")
    interdits = []
    for x in ast.walk(fn):
        if isinstance(x, (ast.Await, ast.Global, ast.Nonlocal)):
            interdits.append(type(x).__name__)
        if isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store):
            interdits.append("mutation ." + x.attr)
        if isinstance(x, ast.Call) and getattr(x.func, "attr", "") in (
                "debug", "info", "warning", "error"):
            interdits.append("journalisation")
    assert not interdits, f"la feuille n'est pas pure : {sorted(set(interdits))}"


def test_le_module_n_importe_JAMAIS_react():
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fautifs = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("react"):
            fautifs.append(n.module)
        if isinstance(n, ast.Import):
            fautifs += [x.name for x in n.names if x.name.endswith("react")]
    assert not fautifs, f"le module importe react.py : {fautifs}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Les seuils adaptatifs — chaque branche a sa raison datee
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("outil,seuil", [
    ("delegate_and_wait", 20000),   # livrables des workers INTACTS
    ("read_file", 8000),            # B0.3 : contenu fichier precieux
    ("read_files_batch", 8000),     # B0.3 : etait ABSENT de la liste
    ("write_mission_contract", 8000),  # C0.1 : porte les allowed_files
    ("run_command", 3000),          # rapports
    ("outil_inconnu", 3000),        # defaut
])
def test_le_seuil_par_outil_est_conserve(outil, seuil):
    from src.reasoning.observation_synthesis import observation_compact_limit

    assert observation_compact_limit(outil, is_chat_surface=False) == seuil


def test_le_seuil_navigateur_depend_de_la_surface_chat():
    """Fix A : sur une surface de chat, ne pas tronquer la conversation."""
    from src.reasoning.observation_synthesis import observation_compact_limit

    assert observation_compact_limit("browser_get_content", is_chat_surface=True) == 4000
    assert observation_compact_limit("browser_get_content", is_chat_surface=False) == 1800


def test_la_liste_des_outils_de_lecture_est_PARTAGEE():
    """LOT Z12 — la liste etait ecrite EN DUR a deux endroits et les deux
    copies avaient diverge : un outil protege jusqu'a 8 000 chars se
    retrouvait reduit a 800 des qu'il les depassait."""
    from src.reasoning.observation_synthesis import observation_compact_limit
    from src.reasoning.react import _OBS_FILE_READ_TOOLS

    for outil in _OBS_FILE_READ_TOOLS:
        assert observation_compact_limit(outil, is_chat_surface=False) == 8000, (
            f"{outil} est protege au seuil mais pas a la strategie — les deux "
            f"listes ont re-diverge (Z12)"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. Le comportement de compaction
# ══════════════════════════════════════════════════════════════════════════


def _long(n: int) -> str:
    return "A" * n


def test_sous_le_seuil_rien_n_est_compacte():
    from src.reasoning.observation_synthesis import compact_observation_body

    assert compact_observation_body("run_command", _long(100), False) is None


def test_au_dessus_du_seuil_le_corps_est_compacte():
    from src.reasoning.observation_synthesis import compact_observation_body

    corps = compact_observation_body("run_command", _long(9000), False)
    assert corps is not None
    assert len(corps) < 9000


@pytest.mark.parametrize("outil", [
    "delegate_task", "create_project", "generate_website",
    "write_website_files", "website_build",
])
def test_les_delegations_gardent_debut_ET_fin(outil):
    """« garder debut (statut) + fin (conclusion) »."""
    from src.reasoning.observation_synthesis import compact_observation_body

    contenu = "DEBUT" + _long(5000) + "CONCLUSION"
    corps = compact_observation_body(outil, contenu, False)
    assert corps is not None
    assert "DEBUT" in corps and "CONCLUSION" in corps


@pytest.mark.parametrize("outil", ["run_command", "execute_code", "dev_run_fix"])
def test_les_commandes_gardent_debut_ET_fin(outil):
    from src.reasoning.observation_synthesis import compact_observation_body

    contenu = "ENV_HEAD" + _long(5000) + "ERREUR_FINALE"
    corps = compact_observation_body(outil, contenu, False)
    assert corps is not None
    assert "ENV_HEAD" in corps and "ERREUR_FINALE" in corps


def test_les_lectures_fichier_gardent_3000_chars_du_DEBUT():
    """« Pas d'ancre ici : le contenu brut est deja preserve integralement »."""
    from src.reasoning.observation_synthesis import compact_observation_body

    contenu = _long(12000)
    corps = compact_observation_body("read_file", contenu, False)
    assert corps is not None
    assert corps.startswith(_long(3000))
    assert "chars omis" in corps


def test_le_defaut_garde_debut_et_fin_avec_ancre():
    from src.reasoning.observation_synthesis import compact_observation_body

    contenu = "TETE" + _long(5000) + "QUEUE"
    corps = compact_observation_body("un_outil_quelconque", contenu, False)
    assert corps is not None
    assert "TETE" in corps and "QUEUE" in corps
    assert "compact" in corps.lower()


def test_un_contenu_vide_ne_compacte_rien():
    from src.reasoning.observation_synthesis import compact_observation_body

    assert compact_observation_body("run_command", "", False) is None


# ══════════════════════════════════════════════════════════════════════════
#  4. La boucle garde ses effets — §15
# ══════════════════════════════════════════════════════════════════════════


def test_la_reconstruction_du_step_reste_dans_la_boucle():
    """Le `ReActStep(...)` recompose et le `logger.debug` sont des EFFETS :
    ils restent dans `_run_internal`."""
    # On vise le SITE D'APPEL (`_os_compact_observation_body(`), pas la ligne
    # d'import qui porte le meme nom plus haut dans le fichier.
    src = REACT.read_text(encoding="utf-8")
    i = src.rindex("compact_observation_body(")   # le SITE D'APPEL, pas l'import
    bloc = src[i:i + 1400]
    assert "ReActStep(" in bloc, "la reconstruction du step a quitte la boucle"
    assert "Observation compact" in bloc, "le journal de compaction a quitte la boucle"


def test_le_squelette_de_la_boucle_est_INTACT():
    """§15 — interdictions explicites. Ces nombres sont ceux mesures a
    l'ouverture de RF-9 ; ils ne doivent pas bouger sur un lot de feuille."""
    ri = _run_internal()
    conts = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Continue))
    rets = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Return))
    trys = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Try))
    imps = sum(1 for x in ast.walk(ri)
               if isinstance(x, (ast.Import, ast.ImportFrom)))
    assert conts == 48, f"les `continue` ont bouge : {conts} au lieu de 48"
    assert rets == 33, f"les retours ont bouge : {rets} au lieu de 33"
    assert trys == 77, f"les `try` ont bouge : {trys} au lieu de 77"
    # 48 -> 47 : la feuille a emporte SON PROPRE import local
    # (`compact_batch_observation`, utilise uniquement par la compaction des
    # documents). L'invariant 15 presume les imports locaux intentionnels ; il
    # n'interdit pas a une feuille de partir avec le sien.
    assert imps == 47, (
        f"les imports locaux ont bouge : {imps} au lieu de 47 — invariant 15, "
        f"ils sont presumes intentionnels"
    )


def test_l_etat_partage_a_DIMINUE():
    """LE critere du §15 : « une reduction supplementaire n'est acceptee que si
    elle diminue reellement l'etat partage ».

    Mesure d'ouverture de RF-9 : 672 locales. La feuille en consomme 11 et en
    produit 11 ; toutes celles qui ne servaient QU'a elle doivent disparaitre
    de la boucle.
    """
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    assert len(locales) < 672, (
        f"l'etat partage n'a pas diminue : {len(locales)} locales (etait 672). "
        f"Un lot RF-9 qui ne reduit pas l'etat partage ne remplit pas son "
        f"critere d'acceptation."
    )
    # les variables propres a la compaction ne doivent plus exister dans la boucle
    for mort in ("_c_head", "_c_tail", "_OBS_COMPACT_LIMIT", "_anchor",
                 "_browser_compacted", "_is_chat_surface_compact"):
        assert mort not in locales, (
            f"`{mort}` vit encore dans la boucle : la feuille n'a pas emporte "
            f"son etat"
        )
