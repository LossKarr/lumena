"""RF-6b — les trois gates mission qui MUTENT `self`.

RF-6a a extrait les 15 lectrices pures. Restent les trois methodes que
l'invariant 5 interdit de deplacer telles quelles :

| methode | lignes | attributs mutes |
|---|---:|---|
| `_nudge_unpublished_writes` | 28 | `_z24_nudged`, `_pending_loop_guidance` |
| `_mission_overwrite_gate` | 48 | `_overwrite_gate_shots` |
| `_chat_mission_intent_gate` | 49 | `_chat_mission_gate_shots` |

--- La coupe ---

Les trois ont exactement la meme forme :

    <gardes>                 -> None
    if tirs >= 1             -> None
    <decision metier>        -> None si aucune raison
    tirs += 1                            <-- MUTATION
    logger.warning(...)
    return Observation(...)              <-- ou affectation du guidance

La DECISION sort (elle ne lit qu'un instantane), la MUTATION reste.

--- Pourquoi la fonction extraite rend un CONTENU, pas une `Observation` ---

Invariant 16 : l'ordre observable ne change pas. Dans l'original, la mutation
precede le log, qui precede la construction de l'`Observation`. Si la fonction
extraite construisait l'`Observation`, celle-ci naitrait AVANT la mutation —
et son horodatage avec. Elle rend donc le contenu ; `react.py` mute, journalise,
puis construit, dans l'ordre d'origine.

--- Ce que RF-6b NE fait PAS ---

Il ne deplace aucune mutation, ne change aucun seuil de tir, ne touche pas au
vocabulaire des messages. Un lot d'extraction n'ajoute aucun comportement
(invariant 11).
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
MODULE = RACINE / "src" / "reasoning" / "mission_runtime.py"
REACT = RACINE / "src" / "reasoning" / "react.py"

GATES = {
    "_nudge_unpublished_writes": ("_z24_nudged", "_pending_loop_guidance"),
    "_mission_overwrite_gate": ("_overwrite_gate_shots",),
    "_chat_mission_intent_gate": ("_chat_mission_gate_shots",),
}


def _methodes():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return {n.name: n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _mutations(noeud) -> set:
    return {
        x.attr for x in ast.walk(noeud)
        if isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store)
        and isinstance(x.value, ast.Name) and x.value.id == "self"
    }


# ══════════════════════════════════════════════════════════════════════════
#  1. Invariant 5 — les mutations RESTENT dans ReActLoop
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,attrs", sorted(GATES.items()))
def test_la_mutation_reste_dans_ReActLoop(nom, attrs):
    """LE garde du lot. Sortir la mutation ferait diverger deux etats : celui
    que le module croit voir et celui que la boucle porte reellement."""
    mute = _mutations(_methodes()[nom])
    for a in attrs:
        assert a in mute, (
            f"{nom} ne mute plus `self.{a}` — la mutation a quitte ReActLoop, "
            f"contre l'invariant 5"
        )


def test_le_module_ne_mute_AUCUN_etat():
    """`mission_runtime.py` decide sur un instantane. Une seule affectation sur
    l'objet d'etat suffirait a en faire un second proprietaire."""
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fautives = []
    for n in arbre.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for x in ast.walk(n):
            if (isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store)
                    and isinstance(x.value, ast.Name) and x.value.id == "etat"):
                fautives.append((n.name, x.attr))
    assert not fautives, f"le module mute l'etat : {fautives}"


# ══════════════════════════════════════════════════════════════════════════
#  2. La decision est sortie
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(GATES))
def test_la_decision_est_deleguee_au_module(nom):
    src = REACT.read_text(encoding="utf-8").splitlines(keepends=True)
    m = _methodes()[nom]
    corps = "".join(src[m.lineno - 1:m.end_lineno])
    assert "_mr_" in corps, (
        f"{nom} n'appelle pas `mission_runtime.py` — la decision n'est pas sortie"
    )


@pytest.mark.parametrize("nom", sorted(GATES))
def test_la_coquille_reste_courte(nom):
    """Elle ne garde que la mutation, le log et la construction du retour.

    On compte les INSTRUCTIONS, pas les lignes : les docstrings de ces gates
    portent la raison datee de leur lot (Z24, P2b, O2) et doivent rester —
    c'est la meme regle qu'en RF-6a.
    """
    m = _methodes()[nom]
    corps = [n for n in m.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    assert len(corps) <= 8, (
        f"{nom} garde {len(corps)} instructions apres la docstring — la "
        f"decision n'est pas entierement sortie"
    )


def test_l_Observation_est_construite_dans_react(monkeypatch):
    """Invariant 16 — l'ordre observable ne change pas.

    Si la fonction extraite construisait l'`Observation`, celle-ci naitrait
    AVANT la mutation, et son horodatage avec. Le module ne doit donc pas
    l'importer.
    """
    texte = MODULE.read_text(encoding="utf-8")
    arbre = ast.parse(texte)
    noms = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom) and n.module and "react_config" in n.module:
            noms |= {a.asname or a.name for a in n.names}
    # `Observation` est deja importe pour RF-6a (`_worker_codeagent_first_gate`),
    # ce qui est legitime : cette methode-la ne mute rien. On verifie donc les
    # trois gates de RF-6b une par une.
    for nom in GATES:
        fn = [n for n in arbre.body
              if isinstance(n, ast.FunctionDef) and nom in n.name]
        for f in fn:
            construit = [
                x for x in ast.walk(f)
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                and x.func.id in noms
            ]
            assert not construit, (
                f"{f.name} construit une Observation : elle naitrait AVANT la "
                f"mutation, contre l'invariant 16"
            )


# ══════════════════════════════════════════════════════════════════════════
#  3. Comportement — un tir, puis l'outil passe
# ══════════════════════════════════════════════════════════════════════════


def _etat_chat(requete: str):
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = ""
    o.task_orchestrator = None
    o._original_query = requete
    return o


def test_le_gate_chat_tire_UNE_fois_puis_laisse_passer():
    """« Redirection, pas blocage » — la docstring d'origine (lot O2). Un
    deuxieme tir transformerait une redirection en mur."""
    o = _etat_chat(
        "Lance ca comme une mission autonome en arriere-plan, echeance 90 minutes"
    )
    premier = o._chat_mission_intent_gate("read_file")
    assert premier is not None, "le gate ne tire pas du tout"
    assert getattr(o, "_chat_mission_gate_shots", 0) == 1
    second = o._chat_mission_intent_gate("read_file")
    assert second is None, "le gate tire deux fois — ce n'est plus une redirection"


def test_le_gate_chat_est_inerte_sur_create_mission():
    o = _etat_chat("Lance une mission avec echeance 90 minutes")
    assert o._chat_mission_intent_gate("create_mission") is None


def test_le_gate_chat_est_inerte_sans_echeance():
    """Garde-fou utilisateur (lot O) : le signal est une ECHEANCE CHIFFREE,
    jamais le mot « mission » seul."""
    o = _etat_chat("parle-moi de la mission Apollo")
    assert o._chat_mission_intent_gate("read_file") is None


def test_le_nudge_z24_tire_UNE_fois():
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o._mission_unpublished_writes = lambda: ["README.md"]

    o._nudge_unpublished_writes()
    assert getattr(o, "_z24_nudged", False) is True
    guidance = getattr(o, "_pending_loop_guidance", "")
    assert "README.md" in guidance
    assert "publish_mission_workspace" in guidance

    o._pending_loop_guidance = ""
    o._nudge_unpublished_writes()
    assert o._pending_loop_guidance == "", "le nudge a tire deux fois"


def test_le_nudge_est_inerte_sans_ecriture_orpheline():
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o._mission_unpublished_writes = lambda: []
    o._nudge_unpublished_writes()
    assert getattr(o, "_z24_nudged", False) is False
    # `_pending_loop_guidance` vaut `None` par defaut de classe, pas `""` :
    # on teste donc l'ABSENCE de guidance, pas une valeur precise.
    assert not getattr(o, "_pending_loop_guidance", None)


def test_un_etat_casse_ne_declenche_aucun_nudge():
    """Invariant 6 : une exception ne devient ni une autorisation ni un tir."""
    from src.reasoning.react import ReActLoop

    def _casse():
        raise RuntimeError("boom")

    o = object.__new__(ReActLoop)
    o._mission_unpublished_writes = _casse
    o._nudge_unpublished_writes()  # ne doit pas lever
    assert getattr(o, "_z24_nudged", False) is False


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(GATES))
def test_les_signatures_sont_identiques(nom):
    from src.reasoning.react import ReActLoop

    attendu = {
        "_nudge_unpublished_writes": ["self"],
        "_mission_overwrite_gate": ["self", "tool_name", "tool_args"],
        "_chat_mission_intent_gate": ["self", "tool_name"],
    }
    sig = inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == attendu[nom]


def test_les_quinze_coquilles_de_RF6a_sont_intactes():
    """La porte de passage du plan : un lot ne defait pas le precedent."""
    from src.reasoning.react import ReActLoop

    for nom in ("_mission_completion_evidence", "_mission_lead_delivered",
                "_is_worker_run", "_mission_allowed_files_meta"):
        assert hasattr(ReActLoop, nom)
    assert isinstance(ReActLoop.__dict__["_is_mission_run"], property)


def test_run_internal_n_est_toujours_pas_touche():
    """RF-9 n'est pas ouvert, et ne le sera pas dans cette serie."""
    taille = (lambda m: m.end_lineno - m.lineno)(_methodes()["_run_internal"])
    assert taille > 5000, f"_run_internal fait {taille} lignes"
