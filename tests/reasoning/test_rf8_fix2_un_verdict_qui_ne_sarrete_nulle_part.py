"""RF-8-FIX-2 — le garde F1.b etait INERTE : son verdict n'atterrissait nulle part.

Trouve en construisant le harnais de RF-8b : `o._run_meta = {...}` ne prenait
pas. `_run_meta` n'est pas un dict — c'est une **property** rendant un
`RunMetaProxy` sur une dataclass TYPEE.

--- Le mecanisme ---

`RunMetaProxy.__setitem__` LEVE sur une cle non declaree :

    def __setitem__(self, key, value):
        if key in RunMeta._FIELDS:
            setattr(self._rm, key, value)
        else:
            raise KeyError(key)

Et `RunMeta._FIELDS` n'en declarait que QUATRE. Or presque toutes les ecritures
`_run_meta[...]` sont dans un `try/except` : **une cle inconnue disparaissait en
silence**.

--- Mesure sur tout le depot, AVANT correctif ---

    cles ECRITES dans _run_meta          10
    cles INCONNUES de RunMeta             6
    ecritures perdues       9 / 24  =  37,5 %

| cle perdue | consommateur |
|---|---|
| `mission_truth_lock_overclaim` | **`runner.py` -> `closure_decision`** |
| `mission_truth_lock_applied` | `agent_service.py` (preuve F1.a) |
| `agent_output_delivered_anyway` | aucun |
| `z28_lead_artifacts` | aucun |
| `thought_leak_case`, `thought_leak_len` | aucun |

Quatre n'ont aucun lecteur : leur perte n'a pas de consequence fonctionnelle.
**Une seule compte**, et voici sa chaine complete :

    1. le truth-lock calcule `overclaim=True`             <- le fait EXISTE
    2. `_note_truth_lock_outcome` l'ecrit -> KeyError -> `except: pass`
    3. `agent_service` teste `if _k in _meta` -> __contains__ -> False
    4. `runner.py` lit `proof.get(...)`       -> toujours False
    5. `closure_decision(overclaim=False)`    -> jamais `_CLOSURE_UNPROVEN_CLAIM`

**Une mission dont le FINAL a ete RETROGRADE pour affirmation non prouvee se
cloture `completed`.**

C'est mot pour mot ce que le lot F1.b (AUD-014) devait empecher — sa propre
docstring le dit : « l'ETAT de la mission restait donc `done/completed` alors
qu'une affirmation venait d'etre retrogradee ».

--- Cause racine ---

La migration de `_run_meta` (dict libre) vers `RunMeta` typee + `RunMetaProxy`.
Le proxy porte son propre avertissement — « A retirer quand tous les
consommateurs seront migres » — et son intention declaree est de « permettre au
code EXISTANT » d'ecrire librement. Lever `KeyError` defait cette intention.

--- Le correctif ---

Declarer les six champs, et poser un garde STRUCTUREL : toute cle ecrite dans
`_run_meta` quelque part dans le depot doit etre declaree. Ce garde transforme
une `KeyError` silencieuse a l'execution en test rouge.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]

#: Les six clés que le code ecrit et que `RunMeta` ne declarait pas.
CLES_PERDUES = {
    "mission_truth_lock_overclaim": "runner.py -> closure_decision",
    "mission_truth_lock_applied": "agent_service.py (preuve F1.a)",
    "agent_output_delivered_anyway": "aucun consommateur (telemetrie)",
    "z28_lead_artifacts": "aucun consommateur (telemetrie)",
    "thought_leak_case": "aucun consommateur (telemetrie)",
    "thought_leak_len": "aucun consommateur (telemetrie)",
}


def _cles_ecrites() -> dict:
    """Toutes les cles litterales ecrites dans `_run_meta[...]` du depot."""
    out = {}
    for base in ("src", "web"):
        for f in (RACINE / base).rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                arbre = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for n in ast.walk(arbre):
                if not (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)):
                    continue
                b = n.value
                if not (isinstance(b, ast.Attribute) and b.attr == "_run_meta"):
                    continue
                if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                    out.setdefault(n.slice.value, []).append(
                        (str(f.relative_to(RACINE)), n.lineno))
    return out


# ══════════════════════════════════════════════════════════════════════════
#  1. LE GARDE STRUCTUREL — une KeyError silencieuse devient un test rouge
# ══════════════════════════════════════════════════════════════════════════


def test_toute_cle_ecrite_dans_run_meta_est_DECLAREE():
    """LE garde du lot.

    `RunMetaProxy.__setitem__` leve sur une cle non declaree, et presque toutes
    les ecritures sont dans un `try/except` : le fait disparait sans bruit. Ce
    test transforme cette perte silencieuse en echec visible.
    """
    from src.reasoning.agent_execution_state import RunMeta

    ecrites = _cles_ecrites()
    assert ecrites, "aucune ecriture `_run_meta[...]` trouvee — le test s'est aveugle"
    inconnues = {k: v for k, v in ecrites.items() if k not in RunMeta._FIELDS}
    assert not inconnues, (
        "ces cles sont ECRITES mais non declarees dans RunMeta._FIELDS : "
        "chaque ecriture leve KeyError et disparait dans un `except`.\n"
        + "\n".join("  %-38s %s" % (k, v[:2]) for k, v in sorted(inconnues.items()))
    )


@pytest.mark.parametrize("cle,consommateur", sorted(CLES_PERDUES.items()))
def test_la_cle_perdue_est_desormais_declaree(cle, consommateur):
    from src.reasoning.agent_execution_state import RunMeta

    assert cle in RunMeta._FIELDS, (
        f"`{cle}` n'est toujours pas declaree — consommateur : {consommateur}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. LA CHAINE — le verdict atteint celui qui decide
# ══════════════════════════════════════════════════════════════════════════


def test_le_verdict_du_truth_lock_est_ENREGISTRE():
    """Etape 2 de la chaine : `_note_truth_lock_outcome` doit ecrire pour de bon."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome({"overclaim": True, "changed": True})

    assert o._run_meta.get("mission_truth_lock_overclaim") is True, (
        "le verdict d'overclaim n'est pas enregistre : la KeyError est avalee"
    )
    assert o._run_meta.get("mission_truth_lock_applied") is True


def test_le_drapeau_est_CUMULATIF():
    """La docstring d'origine l'exige : « un site aval qui ne detecte rien ne
    doit jamais effacer un overclaim vu en amont »."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome({"overclaim": True})
    o._note_truth_lock_outcome({"overclaim": False})

    assert o._run_meta.get("mission_truth_lock_overclaim") is True, (
        "un site aval a EFFACE l'overclaim vu en amont"
    )


def test_un_verdict_sans_overclaim_pose_False_et_pas_absent():
    """La branche `elif ... not in self._run_meta` : sans overclaim, la cle
    existe et vaut False — c'est ce que `agent_service` copie dans la preuve."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome({"changed": True})

    assert o._run_meta.get("mission_truth_lock_overclaim") is False


def test_la_cloture_refuse_completed_sur_un_overclaim():
    """Etape 5 : `closure_decision` doit pouvoir rendre autre chose que
    `completed`. C'est la raison d'etre de toute la chaine."""
    from src.subagents.runner import closure_decision

    code_sain, _ = closure_decision(
        overclaim=False, web_failed=False, web_http_failed=False,
        effects_unproven=False)
    code_menteur, _ = closure_decision(
        overclaim=True, web_failed=False, web_http_failed=False,
        effects_unproven=False)

    assert code_sain == "completed"
    assert code_menteur != "completed", (
        "une mission qui a surestime son travail se cloture quand meme "
        "`completed`"
    )


def test_la_preuve_transporte_le_drapeau():
    """Etape 3 : `agent_service` copie la cle via `if _k in _meta`, ce qui passe
    par `RunMetaProxy.__contains__`."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome({"overclaim": True})
    assert "mission_truth_lock_overclaim" in o._run_meta, (
        "`__contains__` rend False : la cle ne sera jamais copiee dans la preuve"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_les_quatre_champs_historiques_sont_intacts():
    from src.reasoning.agent_execution_state import RunMeta

    for champ in ("agent_output_incomplete", "agent_output_warning",
                  "agent_repair_attempts", "agent_final_finish_reason"):
        assert champ in RunMeta._FIELDS
        assert hasattr(RunMeta(), champ)


def test_une_cle_VRAIMENT_inconnue_leve_toujours():
    """Le proxy ne devient pas permissif : une faute de frappe doit encore se
    voir. C'est la lecon Z37 — une porte qui s'ouvre sur sa propre faute de
    frappe ne protege plus rien."""
    from src.reasoning.agent_execution_state import RunMeta, RunMetaProxy

    proxy = RunMetaProxy(RunMeta())
    with pytest.raises(KeyError):
        proxy["cle_qui_nexiste_pas_du_tout"] = True


def test_to_dict_expose_les_nouveaux_champs():
    """`get_run_meta()` construit la sortie du run a partir de `to_dict`."""
    from src.reasoning.agent_execution_state import RunMeta

    d = RunMeta().to_dict()
    for cle in CLES_PERDUES:
        assert cle in d, f"`{cle}` absente de to_dict()"


def test_les_valeurs_par_defaut_sont_NEUTRES():
    """Un run qui n'a rien constate ne doit pas naitre avec un overclaim."""
    from src.reasoning.agent_execution_state import RunMeta

    rm = RunMeta()
    assert rm.mission_truth_lock_overclaim is False
    assert rm.mission_truth_lock_applied is False
    assert rm.agent_output_delivered_anyway is False
