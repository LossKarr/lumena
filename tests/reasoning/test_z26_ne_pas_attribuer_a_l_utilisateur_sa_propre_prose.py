"""LOT Z26 — n'attribuer à l'utilisateur que ce qu'il a écrit.

La porte 2.13.B refusait `create_project` direct en disant :

    « ⛔ L'utilisateur a EXPLICITEMENT demandé le protocole contrat + workers »

Elle lisait `_original_query`. Or `_original_query` contient le **préambule de
mission écrit par Lumena elle-même**, où figure littéralement
`write_mission_contract`. Mesuré : le préambule seul suffit à déclencher.

Lumena citait donc sa propre prose et l'attribuait à l'utilisateur. C'est le
contraire exact du critère de ce chantier — « ne jamais affirmer ce qu'on n'a pas
vérifié » vaut aussi pour ce qu'on prête à quelqu'un d'autre.

Second effet, moins visible : la porte tirait là où 2.13.B la voulait INERTE
(« sans exigence explicite, create_project direct reste licite — morpion, pwgen,
sondage l'ont prouvé »), puisque le préambule est présent à CHAQUE mission.

Mesure du corpus AVANT correctif (643 missions archivées) :
  - 166 objectifs UTILISATEUR exigent réellement le protocole ;
  - déclencheur dominant « sous-agents » (120), vraie formulation humaine.
La porte garde donc tout son travail. Z26 corrige **à qui on l'attribue**, pas
ce qu'elle protège.
"""

from types import SimpleNamespace

import pytest

from src.reasoning.react import ReActLoop

_PREAMBULE = (
    "Tu es en MODE MISSION. Pose d'abord un contrat via `write_mission_contract` "
    "(files avec path/owner/exports), puis délègue via `delegate_and_wait`.\n\n"
    "Objectif : Crée un jeu morpion en HTML/JS."
)


def _self(*, routing=None, brut=""):
    o = SimpleNamespace(_original_query=brut)
    if routing is not None:
        o._mission_routing_objective = lambda: routing
    return o


def _exigence(**kw):
    return ReActLoop._contract_protocol_requirement(_self(**kw))


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_preambule_de_lumena_ne_declenche_plus():
    """LE lot : l'objectif réel est un morpion — 2.13.B veut la porte inerte."""
    exige, _ = _exigence(routing="Crée un jeu morpion en HTML/JS.", brut=_PREAMBULE)
    assert exige is False


def test_sans_le_correctif_le_preambule_declenchait():
    """Garde-fou du banc : si ce test devient vert, mon banc ne reproduit plus
    le bug et le test précédent ne prouve plus rien."""
    from src.reasoning.final_guards import objective_requires_contract_protocol
    assert objective_requires_contract_protocol(_PREAMBULE) is True


def test_une_vraie_demande_utilisateur_declenche_toujours():
    """« sous-agents » : le déclencheur dominant du corpus (120 occurrences)."""
    exige, du_user = _exigence(
        routing="Fais ça avec des sous-agents en parallèle.", brut=_PREAMBULE,
    )
    assert exige is True
    assert du_user is True


def test_l_objectif_utilisateur_decide_seul():
    """Retomber sur la requête brute quand l'objectif est connu, ce serait
    réintroduire le préambule par la porte de derrière."""
    exige, _ = _exigence(routing="Crée un site vitrine.", brut="… write_mission_contract …")
    assert exige is False


# ── L'attribution ────────────────────────────────────────────────────────────


def test_sans_objectif_connu_on_n_attribue_rien():
    """Repli sur la requête brute : la porte peut encore tirer, mais elle ne
    prétend PLUS que l'utilisateur l'a demandé."""
    exige, du_user = _exigence(routing=None, brut="Utilise le protocole contrat + workers.")
    assert exige is True
    assert du_user is False


def test_un_objectif_vide_vaut_absence():
    exige, du_user = _exigence(routing="   ", brut="Utilise des sous-agents.")
    assert exige is True
    assert du_user is False


def test_rien_ne_leve_jamais():
    casse = SimpleNamespace(
        _original_query="protocole contrat + workers",
        _mission_routing_objective=lambda: (_ for _ in ()).throw(RuntimeError("x")),
    )
    exige, du_user = ReActLoop._contract_protocol_requirement(casse)
    assert exige is True and du_user is False
    assert ReActLoop._contract_protocol_requirement(SimpleNamespace()) == (False, False)


# ── Le message dit la vérité sur sa source ───────────────────────────────────


class _Ledger:
    def __init__(self, has=False):
        self._has = has

    def has_successful_action(self, _a):
        return self._has


def _porte(*, routing=None, brut=""):
    s = _self(routing=routing, brut=brut)
    s._is_mission_run = True
    s.execution_ledger = _Ledger()
    s.task_id = "t_z26"
    s._is_worker_run = lambda: False
    return ReActLoop._contract_intent_gate(s, "create_project")


def test_demande_utilisateur_l_affirmation_est_licite():
    obs = _porte(routing="Fais-le avec des sous-agents.", brut=_PREAMBULE)
    assert obs is not None
    assert "L'utilisateur a EXPLICITEMENT demandé" in obs.content


def test_sans_demande_utilisateur_l_affirmation_disparait():
    """C'était LE mensonge : citer sa propre prose au nom de l'utilisateur."""
    obs = _porte(routing=None, brut="protocole contrat + workers imposé")
    assert obs is not None
    assert "L'utilisateur a EXPLICITEMENT demandé" not in obs.content
    assert "Cette mission relève du protocole" in obs.content


def test_la_consigne_utile_survit_dans_les_deux_cas():
    """Changer la formulation ne doit pas coûter l'instruction qui sert."""
    for kw in (dict(routing="avec des sous-agents"), dict(brut="contrat + workers")):
        obs = _porte(**kw)
        assert "write_mission_contract" in obs.content
        assert "delegate_and_wait" in obs.content


def test_le_morpion_passe_desormais_sans_redirection():
    """Le cas que 2.13.B voulait explicitement laisser passer."""
    assert _porte(routing="Crée un jeu morpion en HTML/JS.", brut=_PREAMBULE) is None


# ── Le code porte sa raison ──────────────────────────────────────────────────


def test_la_porte_ne_lit_plus_la_requete_brute_en_premier():
    from pathlib import Path
    src = Path("src/reasoning/react.py").read_text(encoding="utf-8")
    i = src.index("def _contract_intent_gate")
    bloc = src[i:i + 2000]
    assert "_contract_protocol_requirement" in bloc
    assert 'objective_requires_contract_protocol(\n            getattr(self, "_original_query"' not in bloc


def test_la_raison_du_lot_est_dans_le_code():
    from pathlib import Path
    src = Path("src/reasoning/react.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z26 — d'où vient l'exigence"):][:1800]
    assert "préambule" in entete
    assert "166" in entete
