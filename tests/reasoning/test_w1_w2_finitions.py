"""W1 + W2 — finitions P2P.

W1 : submit_peer_task (et co.) coche une tâche « envoyer la mission » (DELIVERY)
     → plus de FINAL bloqué ×3 par le PlanGuard.
W2 : la note « en cours » donne le VRAI dossier de réception (pas « <pair> »).
"""
from __future__ import annotations

import pytest

from src.reasoning.plan_evidence import has_sufficient_proof, get_tool_capabilities, ProofCapability


# ── W1 ────────────────────────────────────────────────────────────────────────

def test_submit_peer_task_has_message_send_cap():
    caps = get_tool_capabilities("submit_peer_task")
    assert ProofCapability.MESSAGE_SEND in caps


@pytest.mark.parametrize("tool", [
    "submit_peer_task", "peer_team_request", "run_peer_task_sync",
    "orchestrate_peer_request", "delegate_to_peer",
])
def test_peer_tools_prove_delivery_task(tool):
    # Tâche « envoyer la mission » (kind DELIVERY) + accusé « ✅ Mission lancée »
    obs = "✅ Mission bien lancée chez Lumena (réf. ta-d2b58c39ad30)."
    assert has_sufficient_proof(tool, obs, task_desc="Envoyer la mission à l'autre Lumena") is True


def test_non_send_tool_still_fails_delivery():
    # Garde-fou : un outil de lecture ne prouve toujours PAS un envoi.
    assert has_sufficient_proof("list_directory", "📂 contenu",
                                task_desc="Envoyer la mission à l'autre Lumena") is False


# ── W2 ────────────────────────────────────────────────────────────────────────

def test_running_note_uses_real_peer_folder(monkeypatch, tmp_path):
    from src.runtime import peer_mission_tracker as mt
    mt.reset_for_tests(tmp_path / "missions.json")
    mt.register_outbound_mission(
        task_id="w2", peer_id="p", peer_name="Lumena-B", host="h", port=1,
        objective="créer un site", channel="web",
    )
    from web.routes.chat import _inject_mission_reminders
    out = _inject_mission_reminders("alors ?")
    assert "recu-de-lumena-b" in out      # vrai nom
    assert "<pair>" not in out            # plus de placeholder


# ── W1 (complément) — porte de CONTENU du PlanGuard ───────────────────────────

from src.reasoning.plan_evidence import is_peer_delegation_success


def test_peer_delegation_success_recognized():
    # Cas exact du log A 03:33:35 : l'accusé doit prouver la délégation.
    obs = "✅ Mission bien lancée chez Lumena (réf. ta-c7eddb84004c). Ça va prendre un peu de temps."
    assert is_peer_delegation_success("submit_peer_task", obs) is True


def test_peer_delegation_sync_success_recognized():
    obs = "Le CodeAgent a terminé avec succès. Rapport: ✅ Projet créé."
    assert is_peer_delegation_success("peer_team_request", obs) is True


def test_peer_delegation_failure_not_success():
    # fail-closed : un 429 / timeout / inconnu ne coche PAS la tâche
    assert is_peer_delegation_success("submit_peer_task", "'Lumena' a retourné HTTP 429.") is False
    assert is_peer_delegation_success("submit_peer_task", "Pair 'x' inconnu.") is False


def test_non_peer_tool_ignored():
    assert is_peer_delegation_success("write_file", "✅ Mission bien lancée réf. ta-1") is False
