"""A4 — le refus « pair en lecture seule » remonte à l'émetteur (pas de faux succès).

Régression du trou runtime (log A 03:27) : B refusait proprement (chat), mais A
voyait « completed »/« en cours » → croyait à un succès, partait en vrille.
Désormais : refus A4 + 0 artefact → statut `refused` terminal + message honnête.
"""
from __future__ import annotations

from src.reasoning.tool_registry import A4_CHAT_REFUSAL_MARKER
from src.runtime import peer_mission_tracker as mt


def test_marker_is_in_refusal_message():
    # le message de refus A4 contient bien le marqueur (DRY message↔détecteur)
    from src.reasoning.tool_registry import ToolRegistry
    import asyncio
    r = ToolRegistry(lumena=None, lumena_root=None)
    r._allowed_tools = {"web_search"}
    r._allowed_tools_hard = True
    obs = asyncio.get_event_loop().run_until_complete(
        r.execute("write_file", {"path": "x", "content": "y"})
    )
    assert A4_CHAT_REFUSAL_MARKER in obs.content


def test_refused_is_terminal():
    assert "refused" in mt._TERMINAL


def test_completion_text_refused_is_honest_and_actionable():
    txt = mt._build_completion_text({
        "peer_name": "Lumena-B",
        "objective": "créer note.txt",
        "status": "refused",
    })
    # POURQUOI : lecture seule
    assert "lecture seule" in txt
    # COMMENT débloquer : panneau Pairs → niveau mission
    assert "mission" in txt.lower()
    assert "panneau" in txt.lower() or "pairs" in txt.lower()
    # QUI : action humaine — l'IA ne peut pas le faire elle-même (sécurité)
    assert "moi-même" in txt.lower() or "ne peux pas" in txt.lower()
    # surtout PAS un faux succès
    assert "✅" not in txt
    assert "terminé la mission" not in txt


def test_completion_text_completed_still_ok():
    txt = mt._build_completion_text({
        "peer_name": "Lumena-B",
        "objective": "créer note.txt",
        "status": "completed",
        "result": "fait",
    })
    assert "✅" in txt
