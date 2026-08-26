"""LOT Z37 — la porte de verite s'ouvrait sur un AttributeError.

Preuve en production (run « SaaS complet », 2026-08-25 04:34) :

    [CodeAgent] soft done gate fail-open:
        type object 'StatusCode' has no attribute 'FAILURE'

`StatusCode` declare SUCCESS / NEEDS_INPUT / PARTIAL / ERROR / TIMEOUT /
AMBIGUOUS. Il n'a JAMAIS eu de `FAILURE`. Trois sites l'appelaient quand meme
— tous les trois sur le chemin du REFUS, jamais sur celui du succes. Donc :

  * quand la porte laissait passer, elle marchait ;
  * quand la porte voulait BLOQUER, elle levait AttributeError, le
    `except Exception` la rattrapait, loguait en DEBUG, et OUVRAIT.

Le run a conclu « projet livre » sur un `exit:0` de PowerShell qui n'avait
valide aucun fichier PHP. La porte censee l'attraper etait desarmee par une
faute de frappe d'enumeration, invisible tant que rien n'echouait.

Motif de la session, applique a mes propres gardes : le fait existe, il est
calcule, il est meme LOGUE — puis jete avant la decision.

Ce test ne verifie pas trois lignes. Il verifie que tout attribut `StatusCode.X`
ecrit dans sub_agent.py existe reellement, pour que la classe entiere du defaut
ne puisse pas revenir.
"""

import ast
from pathlib import Path

import pytest

from src.agents.sub_agent import StatusCode

_SOURCE = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"


def _statuscode_attributes_referenced() -> set[str]:
    arbre = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    return {
        noeud.attr
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Attribute)
        and isinstance(noeud.value, ast.Name)
        and noeud.value.id == "StatusCode"
    }


def test_tout_statuscode_reference_existe_vraiment():
    """LE lot. Un code de statut invente fait planter la ligne qui le rend."""
    inconnus = sorted(
        nom for nom in _statuscode_attributes_referenced() if not hasattr(StatusCode, nom)
    )
    assert inconnus == [], (
        f"StatusCode.{{{','.join(inconnus)}}} n'existe pas — la ligne qui le rend "
        "levera AttributeError, et si elle est dans un try/except elle ouvrira "
        "la porte au lieu de la fermer."
    )


def test_failure_est_bien_le_nom_qui_manquait():
    """Garde-fou de regression nomme : c'est CE nom qui a desarme la porte."""
    assert not hasattr(StatusCode, "FAILURE")
    assert StatusCode.ERROR == "error"


def test_la_source_ne_contient_plus_aucun_statuscode_failure():
    assert "StatusCode.FAILURE" not in _SOURCE.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
#  Les trois sites reparES rendent bien un ECHEC utilisable
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("code", ["success", "error", "partial", "timeout"])
def test_les_codes_rendus_sont_des_chaines_stables(code):
    """Le ledger et l'UI comparent ces valeurs a des chaines : elles ne
    doivent pas devenir des Enum au detour d'un refactor."""
    valeurs = {
        v for k, v in vars(StatusCode).items() if not k.startswith("_") and isinstance(v, str)
    }
    assert code in valeurs
