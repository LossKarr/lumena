"""LOT Z22 — une compaction ne jette pas ce qui a été prouvé.

Run « jeu 3D monde ouvert » (2026-08-19). Déroulé exact, au log :

    02:26:51  📦 Livrable publié : … vers `workspace/jeu-3d-monde-ouvert/`
    02:28:13  🚨 Hallucination streak 2 — compaction d'urgence : historique
                 réduit à 3
    02:28:18  list_directory `workspace/documents/jeu-3d`   ← chemin INVENTÉ
    02:28:26  find_files … 02:28:58 grep_search … 02:29:09 grep_batch …
    02:29:26  ENFIN retrouvé

**42 appels d'exploration et 8 minutes** pour retrouver un dossier créé
90 secondes plus tôt. La compaction avait effacé l'observation qui contenait
le chemin.

Le code fautif était une troncature aveugle par la queue :

    self.history = self.history[-3:]

Rien n'est trié, rien n'est préservé. Elle a tiré **5 fois** dans ce run.

Et c'est une BOUCLE FERMÉE : moins de faits → plus d'invention → streak plus
haut → nouvelle compaction. Le garde anti-hallucination nourrissait exactement
l'hallucination qu'il combat. C'est le motif des 40 lots, sauf qu'ici **c'est un
garde qui jette le fait**.

Le remède existait à côté, jamais consulté : `ExecutionLedger.summary()` produit
la ligne perdue, et le ledger — journal horodaté en ajout seul — n'est JAMAIS
tronqué. On le réinjecte en tête de l'historique nettoyé : le bruit meurt, les
faits restent.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.react import ReActLoop
from src.reasoning.react_config import (
    Action,
    ActionType,
    Observation,
    ReActStep,
    Thought,
)
from src.runtime.execution_ledger import ExecutionLedger


# ── Bancs ────────────────────────────────────────────────────────────────────


def _etape(n: int) -> ReActStep:
    """Une étape ordinaire, du bruit d'exploration."""
    return ReActStep(
        thought=Thought(content=f"pensée {n}"),
        action=Action(action_type=ActionType.TOOL_CALL, tool_name="read_file"),
        observation=Observation(content=f"observation {n}"),
    )


def _ledger_du_run() -> ExecutionLedger:
    """Le ledger tel qu'il était à 02:28 : la publication y est inscrite."""
    led = ExecutionLedger()
    led.append(iteration=3, action="write_mission_contract",
               target="open-world-3d-game", success=True)
    led.append(iteration=4, action="delegate_and_wait",
               target="3 workers", success=True)
    led.append(iteration=5, action="publish_mission_workspace",
               target="jeu-3d-monde-ouvert", success=True,
               proof="5 fichier(s) copiés")
    return led


def _agent(ledger=None):
    return SimpleNamespace(execution_ledger=ledger or _ledger_du_run())


def _faits(agent):
    return ReActLoop._ledger_facts_step(agent)


def _compacte(agent, history):
    """Rejoue EXACTEMENT la compaction d'urgence du code."""
    kept = history[-3:]
    faits = _faits(agent)
    return ([faits] + kept) if faits else kept


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_chemin_publie_survit_a_la_compaction():
    """LE lot : après compaction, le dossier publié est encore lisible."""
    agent = _agent()
    histoire = [_etape(i) for i in range(12)]
    compacte = _compacte(agent, histoire)

    texte = "\n".join(e.observation.content for e in compacte if e.observation)
    assert "jeu-3d-monde-ouvert" in texte
    assert "publish_mission_workspace" in texte


def test_sans_z22_le_chemin_etait_perdu():
    """Garde-fou du banc : la troncature nue effaçait bien le fait.
    Si ce test devient vert, mon banc ne reproduit plus le bug."""
    histoire = [_etape(i) for i in range(12)]
    nue = histoire[-3:]
    texte = "\n".join(e.observation.content for e in nue if e.observation)
    assert "jeu-3d-monde-ouvert" not in texte


def test_le_bruit_meurt_quand_meme():
    """On ne remplace pas une compaction par une non-compaction : les
    12 étapes de bruit doivent bien disparaître."""
    agent = _agent()
    histoire = [_etape(i) for i in range(12)]
    compacte = _compacte(agent, histoire)
    assert len(compacte) == 4          # 3 dernières + les faits
    assert not any(e.observation.content == "observation 0" for e in compacte)


def test_les_faits_sont_en_tete():
    """En queue, ils seraient relus après le bruit qui a causé la dérive."""
    agent = _agent()
    compacte = _compacte(agent, [_etape(i) for i in range(12)])
    assert "FAITS ÉTABLIS" in compacte[0].observation.content


def test_letape_dit_de_ne_pas_rechercher():
    """Le run a perdu 8 min à RECHERCHER. La consigne doit être explicite."""
    contenu = _faits(_agent()).observation.content
    assert "ne les cherche pas" in contenu
    assert "ne les redécouvre pas" in contenu


# ── L'étape injectée ne doit pas se faire passer pour une action ─────────────


def test_letape_nest_pas_un_appel_doutil():
    """Comptée comme un tool_call, elle fausserait les budgets et les preuves."""
    etape = _faits(_agent())
    assert etape.action.action_type == ActionType.THINKING
    assert etape.action.tool_name is None


def test_letape_nest_pas_de_provenance_outil():
    """`origin` != 'tool' : exclue des compteurs de panne (contrat du champ)."""
    assert _faits(_agent()).observation.origin == "ledger_facts"
    assert _faits(_agent()).observation.success is True


# ── L'inertie ────────────────────────────────────────────────────────────────


def test_un_ledger_vide_ninjecte_rien():
    """Au tout début d'un run, il n'y a aucun fait : ne pas polluer."""
    assert _faits(_agent(ExecutionLedger())) is None


def test_ledger_vide_la_compaction_reste_celle_davant():
    agent = _agent(ExecutionLedger())
    compacte = _compacte(agent, [_etape(i) for i in range(12)])
    assert len(compacte) == 3


def test_rien_ne_leve_jamais():
    """Un garde-fou ne doit pas tuer la boucle ReAct."""
    assert ReActLoop._ledger_facts_step(SimpleNamespace()) is None
    assert ReActLoop._ledger_facts_step(object()) is None
    casse = SimpleNamespace(
        execution_ledger=SimpleNamespace(summary=lambda: 1 / 0)
    )
    assert ReActLoop._ledger_facts_step(casse) is None


def test_les_echecs_aussi_sont_des_faits():
    """Savoir qu'une action a ÉCHOUÉ évite de la refaire en boucle."""
    led = ExecutionLedger()
    led.append(iteration=9, action="list_directory",
               target="workspace/documents/jeu-3d", success=False)
    contenu = _faits(_agent(led)).observation.content
    assert "list_directory" in contenu


# ── Le branchement ───────────────────────────────────────────────────────────


_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")


def test_la_compaction_appelle_bien_la_reinjection():
    i = _SRC.index("compaction d'urgence: {} étapes supprimées")
    bloc = _SRC[i - 700 : i]
    assert "self._ledger_facts_step()" in bloc
    assert "[_facts] + _kept" in bloc


def test_la_reinjection_precede_laffectation():
    """Calculée après, elle serait perdue comme le reste."""
    i_calc = _SRC.index("_facts = self._ledger_facts_step()")
    i_aff = _SRC.index("self.history = ([_facts] + _kept)")
    assert i_calc < i_aff


def test_la_troncature_nue_na_pas_survecu():
    """L'ancienne ligne `self.history = _kept` seule ne doit plus exister
    au point de compaction — sinon le correctif serait contourné."""
    i = _SRC.index("compaction d'urgence: {} étapes supprimées")
    bloc = _SRC[i - 700 : i]
    assert "self.history = _kept\n" not in bloc


def test_la_raison_du_lot_est_datee_dans_le_code():
    i = _SRC.index("LOT Z22 — rend les faits")
    entete = _SRC[i : i + 1600]
    assert "jeu 3D monde ouvert" in entete
    assert "42 appels" in entete
    assert "jamais tronqué" in entete
