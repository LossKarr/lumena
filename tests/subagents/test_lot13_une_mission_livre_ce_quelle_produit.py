"""LOT 13 — UNE MISSION QUI PRODUIT DOIT LIVRER.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ MESURÉ AVANT D'ÉCRIRE UNE LIGNE
═══════════════════════════════════════════════════════════════════════════════

Sur `data/task_orchestrator_state.json` :

    184  missions lead avec ledger
     23  publient
    161  ne publient pas
          89  n'ont RIEN produit (recherche, analyse, effets)  →  normal
          72  ONT PRODUIT et n'ont PAS publié                  →  LE DÉFAUT
              dont 61 terminées `done`, avec succès

**76 % des missions qui produisent quelque chose ne le livrent jamais.**

═══════════════════════════════════════════════════════════════════════════════
  CE N'EST PAS L'OUTIL
═══════════════════════════════════════════════════════════════════════════════

Tentatives tracées au ledger : **24 réussies, 1 échouée**. Quand le lead essaie,
ça marche. Il n'essaie pas — et rien ne le lui demande :

    _LEAD_PREFIX (1 129 car.)  →  parle du contrat, de delegate_and_wait, du
                                  CodeAgent. JAMAIS de publier.
    nudge Z24                  →  ne s'arme que si on écrit APRÈS une publication
                                  (il faut donc avoir DÉJÀ publié)
    garde d'écrasement         →  seulement hors du dossier de mission
    _NOT_PUBLISHED_BANNER      →  APRÈS la fin : un constat servi à l'utilisateur
                                  quand tout est joué

Le motif du dépôt : le fait est calculé, il atteint l'utilisateur, et il arrive
trop tard pour changer quoi que ce soit. Même leçon que `contract.effects`
(3 contrats sur 176) et que le champ `role` du lot 0.

═══════════════════════════════════════════════════════════════════════════════
  LE CORRECTIF — DEUX TEMPS, JAMAIS D'AUTOMATISME
═══════════════════════════════════════════════════════════════════════════════

1. le préambule du lead NOMME l'étape finale ;
2. une porte de clôture bornée à UN tir, exactement comme le BROWSER GATE.

Doctrine Z23 : la porte REDIRIGE, elle ne tue jamais le run. Le lead peut avoir
de bonnes raisons de ne pas publier ; s'il persiste, la bannière dit la vérité.
"""

from __future__ import annotations

import types

import pytest

from src.reasoning import mission_runtime as MR
from src.subagents import runner as R


class _Ledger:
    """Le minimum de l'`ExecutionLedger` que la décision consulte."""

    def __init__(self, publie=False, mutation=False, delegue=False):
        self._publie, self._mutation, self._delegue = publie, mutation, delegue

    def has_published(self):
        return self._publie

    def has_any_mutation(self):
        return self._mutation

    def has_successful_action(self, action):
        return self._delegue if action == "delegate_and_wait" else False


def _etat(publie=False, mutation=False, delegue=False, mission=True, ws="missions/task_x"):
    return types.SimpleNamespace(
        est_run_mission=lambda: mission,
        ledger=lambda: _Ledger(publie, mutation, delegue),
        dossier_mission=lambda: ws,
    )


def _decide(etat, tirs=0):
    return MR.rf6b_decision_publication_manquante(etat, tirs)


# ══════════════════════════════════════════════════════════════════════════
#  1. LA PORTE S'ARME QUAND IL FAUT
# ══════════════════════════════════════════════════════════════════════════


def test_produit_sans_publier_declenche_la_porte():
    """Le cas des 72 missions : des fichiers existent, personne ne les verra."""
    out = _decide(_etat(publie=False, mutation=True))
    assert out is not None
    ws, guidance = out
    assert ws == "missions/task_x"
    assert "publish_mission_workspace" in guidance
    assert "l'utilisateur ne les verra pas" in guidance


def test_une_mission_qui_DELEGUE_tout_est_couverte():
    """`delegate_and_wait` n'est PAS dans MUTATION_TOOLS (vérifié) : un lead qui
    confie tout à des workers n'a aucune mutation propre et aurait échappé au
    garde. C'est justement la mission la plus susceptible d'avoir produit."""
    assert _decide(_etat(publie=False, mutation=False, delegue=True)) is not None


def test_la_guidance_dit_QUOI_faire_et_OU_sont_les_fichiers():
    _, guidance = _decide(_etat(mutation=True, ws="missions/task_abc"))
    assert "missions/task_abc" in guidance          # où ils sont
    assert "publish_mission_workspace" in guidance  # quoi appeler
    assert "PUIS conclus" in guidance               # dans quel ordre


# ══════════════════════════════════════════════════════════════════════════
#  2. ZÉRO BRUIT — AUD-017
# ══════════════════════════════════════════════════════════════════════════


def test_muette_si_deja_publie():
    assert _decide(_etat(publie=True, mutation=True)) is None


def test_muette_si_RIEN_produit():
    """Les 89 missions de recherche, d'analyse ou d'effets purs : il n'y a aucun
    fichier à livrer. Leur crier de publier diluerait tous les autres gardes."""
    assert _decide(_etat(publie=False, mutation=False)) is None


def test_muette_au_CHAT():
    assert _decide(_etat(mutation=True, mission=False)) is None


def test_UN_SEUL_tir():
    """Bornée comme le BROWSER GATE : elle rappelle une fois, puis laisse conclure."""
    assert _decide(_etat(mutation=True), tirs=0) is not None
    assert _decide(_etat(mutation=True), tirs=1) is None
    assert _decide(_etat(mutation=True), tirs=5) is None


def test_un_etat_casse_ne_fait_JAMAIS_echouer_la_cloture():
    """Doctrine Z23 : un garde ne tue pas un run. Si le ledger est indisponible,
    la porte se tait — elle ne lève pas."""
    casse = types.SimpleNamespace(
        est_run_mission=lambda: True,
        ledger=lambda: (_ for _ in ()).throw(RuntimeError("ledger HS")),
        dossier_mission=lambda: "x",
    )
    assert _decide(casse) is None


# ══════════════════════════════════════════════════════════════════════════
#  3. ELLE REDIRIGE, ELLE N'IMPOSE PAS
# ══════════════════════════════════════════════════════════════════════════


def test_le_lead_garde_le_droit_de_NE_PAS_publier():
    """Un brouillon, un livrable incomplet, une mission sans fichier : ne pas
    publier peut être le bon choix. La porte doit le dire, pas l'interdire."""
    _, guidance = _decide(_etat(mutation=True))
    assert "Si tu ne DOIS pas publier" in guidance
    assert "conclus quand même" in guidance
    assert "Relance bornée" in guidance


def test_la_porte_ne_publie_JAMAIS_a_la_place_du_lead():
    """GARDE STRUCTURELLE : publier est une action visible pour l'utilisateur.
    Le code de la décision ne doit contenir aucun appel — seulement du texte."""
    import inspect
    src = inspect.getsource(MR.rf6b_decision_publication_manquante)
    assert "publish_mission_workspace" in src        # elle le NOMME
    for interdit in ("await ", "execute(", "call_tool", "run_tool"):
        assert interdit not in src, f"la décision exécute quelque chose : {interdit}"


# ══════════════════════════════════════════════════════════════════════════
#  4. LA CAUSE PREMIÈRE — le lead doit SAVOIR
# ══════════════════════════════════════════════════════════════════════════


def test_le_preambule_du_lead_NOMME_la_publication():
    """GARDE ANTI-CAPACITÉ-MORTE.

    C'est LA cause des 76 % : `_LEAD_PREFIX` expliquait le contrat, la délégation
    et le CodeAgent, sans un mot sur la publication. L'outil marchait (24/25) et
    personne ne demandait de l'appeler.

    Même leçon que `contract.effects` (3/176) et que le `role` du lot 0."""
    assert "publish_mission_workspace" in R._LEAD_PREFIX
    assert "produire n'est pas livrer" in R._LEAD_PREFIX


def test_le_preambule_dit_aussi_quand_NE_PAS_publier():
    """Sinon on remplace un défaut par un autre : des publications de brouillons."""
    assert "si tu décides de ne pas publier" in R._LEAD_PREFIX.lower()


def test_le_preambule_garde_ses_consignes_HISTORIQUES():
    """Non-régression : le contrat, la délégation et le CodeAgent restent."""
    for ancien in ("delegate_and_wait", "write_mission_contract",
                   "delegate_task", "Mode mission"):
        assert ancien in R._LEAD_PREFIX


# ══════════════════════════════════════════════════════════════════════════
#  5. LA PORTE EST BRANCHÉE — une décision que personne n'appelle ne vaut rien
# ══════════════════════════════════════════════════════════════════════════


def test_la_porte_est_CABLEE_dans_la_cloture():
    """Le motif que ce lot corrige est précisément celui d'un fait calculé qui
    n'atteint pas la décision. Ce test vérifie que je ne l'ai pas reproduit."""
    import pathlib
    src = (pathlib.Path(__file__).parents[2] / "src" / "reasoning"
           / "react.py").read_text(encoding="utf-8")
    assert "_mr_decision_publication_manquante" in src
    assert "PUBLISH GATE" in src
    assert "_publish_gate_shots" in src
    # Bornée à un tir, comme le BROWSER GATE dont elle copie la mécanique.
    i = src.index("_pg_shots = getattr(self, \"_publish_gate_shots\", 0)")
    bloc = src[i:i + 1800]
    assert "_pg_shots < 1" in bloc
    assert "self.history.pop()" in bloc          # on retire le FINAL refusé
    assert "continue" in bloc                    # et on relance la boucle


def test_le_tir_accorde_remet_le_compteur_de_stagnation():
    """PG-1.c — une relance dirigée est une stratégie neuve, pas un run qui piétine.
    Sans cela, le détecteur de non-progression pourrait tuer la mission juste après
    lui avoir demandé d'agir."""
    import pathlib
    src = (pathlib.Path(__file__).parents[2] / "src" / "reasoning"
           / "react.py").read_text(encoding="utf-8")
    i = src.index("[PUBLISH GATE]")
    avant = src[max(0, i - 700):i]
    assert "_iterations_without_progress = 0" in avant
