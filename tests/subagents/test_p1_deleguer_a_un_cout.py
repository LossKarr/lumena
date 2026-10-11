"""LOT P1 — le lead décide son découpage sans savoir ce qu'il coûte.

Garde-fou posé par l'utilisateur (2026-08-14) : « il peut coder seul ou
travailler seul, ça oui — mais si c'est nécessaire, Lumena doit SAVOIR si elle a
besoin ou pas de workers ».

Le fait qui lui manque est mesurable : **déléguer ne parallélise pas le codage**.
`SubAgentOrchestrator` porte UN seul `CodeAgent` dont `execute()` est sérialisé
(LOT 2.12.A). Les workers raisonnent en parallèle, puis font la queue pour coder.

    38 exécutions CodeAgent tracées · moyenne 403 s
    HuffPack v1 : 288 + 273 + 818 = 1 379 s EN FILE = 23 min pour 4 fichiers

Sur les 79 contrats du disque : **40 % créent plus de workers que le livrable n'a
de familles de fichiers**, jusqu'à « 5 fichiers, 5 workers, 1 seule famille ».

Ce lot n'IMPOSE aucun découpage — décider reste le travail du lead, et sur
HuffPack v2 coder seule était le bon choix (un codec et ses tests sont couplés
par le format binaire). Il lui donne le chiffre au moment où il peut encore
regrouper, et pas une minute plus tard.
"""
from __future__ import annotations

import inspect

import pytest

from src.subagents.delegation_cost import (
    CODEAGENT_MEAN_SECONDS,
    delegation_cost_note,
)


# ⚠️ MISE A JOUR DU 2026-10-08 — ces deux tests figeaient un fait que le lot A4
# (pool de CodeAgent, signé ce jour) a rendu FAUX. Ce n'est pas un contournement :
# c'est le monde qui a changé, et la preuve est dans le run réel du 07/10 à 23:29:02,
# où Lumena a lu « 27 min de file d'attente » pour une mission bouclée en 5 minutes.
#
# L'ESPRIT des deux tests est conservé : le lead doit connaître le coût de son
# découpage, et le fait qu'il ignore doit lui être dit. Seul le FAIT change — le
# goulot n'est plus le CodeAgent sérialisé, c'est le créneau LLM.
# Couverture complète du nouveau comportement : tests/subagents/test_a6_les_faits_pour_decider.py


def test_the_estimate_matches_the_real_huffpack_run():
    """Le découpage est toujours chiffré au lead — en vagues, plus en file.

    L'ancienne assertion exigeait 18-25 min de file pour 3 workers. A4 a supprimé
    la file : ce qui reste vrai, c'est que le nombre de workers est annoncé et que
    le coût est chiffré.
    """
    note = delegation_cost_note(3, 4, parallelisme_reel=2)
    assert "3 workers" in note
    assert "token" in note.lower()          # le coût est chiffré
    assert "2" in note                      # le parallélisme réel est dit


def test_it_says_the_codeagent_is_serialised():
    """Le fait que le lead ignore — ce n'est PLUS la sérialisation du CodeAgent.

    Avant A4 : « le CodeAgent est unique et sérialisé, ils coderont chacun leur tour ».
    Depuis A4 : le goulot est le créneau LLM, et c'est LUI qu'il faut nommer pour
    que le lead puisse le desserrer.
    """
    note = delegation_cost_note(3, 5, parallelisme_reel=2, plafond_llm=2, pool=10)
    bas = note.lower()
    assert "chacun leur tour" not in bas, "A4 a supprimé la file du CodeAgent"
    assert "llm" in bas or "créneau" in bas, "le vrai goulot doit être NOMMÉ"


def test_one_worker_per_file_is_called_out():
    """Le cas mesuré à 40 % : autant de workers que de fichiers."""
    note = delegation_cost_note(5, 5)
    assert "un worker par fichier" in note
    assert "même" in note and "owner" in note


def test_a_grouped_contract_gets_no_extra_warning():
    """4 fichiers pour 2 workers : le lead a déjà regroupé, on ne le sermonne pas."""
    note = delegation_cost_note(2, 4)
    assert note
    assert "un worker par fichier" not in note


@pytest.mark.parametrize("owners", [0, 1])
def test_solo_and_single_worker_stay_silent(owners):
    """Sans file d'attente, une note de plus ne ferait que diluer les autres
    avertissements (AUD-017, désaturation des gardes)."""
    assert delegation_cost_note(owners, 4) == ""


@pytest.mark.parametrize("bad", [None, "x", -1, 2.5, [], {}])
def test_garbage_never_raises(bad):
    assert isinstance(delegation_cost_note(bad), str)
    assert isinstance(delegation_cost_note(3, bad), str)


def test_the_note_never_forbids_anything():
    """Décider reste le travail du lead : c'est un constat, pas un ordre."""
    note = delegation_cost_note(5, 5)
    for forbidden in ("⛔", "REFUSÉ", "interdit", "tu dois"):
        assert forbidden not in note, forbidden


def test_it_is_wired_where_the_lead_can_still_change_his_mind():
    """Juste avant « Délègue MAINTENANT » — pas dans le bilan final."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.write_mission_contract_handler)
    assert "delegation_cost_note" in src
    assert src.index("delegation_cost_note") < src.index("Délègue MAINTENANT")


def test_the_note_never_breaks_the_contract():
    """Guidance additive : un contrat doit se poser même si la note échoue."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.write_mission_contract_handler)
    block = src.split("LOT P1")[1].split("Délègue MAINTENANT")[0]
    assert "try:" in block and "except Exception" in block


def test_the_measured_constant_is_documented():
    """Le chiffre vient d'une mesure, pas d'une intuition."""
    from src.subagents import delegation_cost

    doc = inspect.getsource(delegation_cost)
    assert "38 exécutions" in doc
    assert "403" in doc
    assert "1 379" in doc or "1379" in doc
